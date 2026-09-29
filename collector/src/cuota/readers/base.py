"""Shared reader plumbing: typed errors, HTTP helper, error-to-reading conversion."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from cuota.model import Error, ErrorCode, ProviderReading, State

Opts = Mapping[str, Any]


class ReaderError(Exception):
    """A failure with a contract error code. The message must never contain secrets or raw bodies."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message


def shape_error(what: str) -> ReaderError:
    return ReaderError("shape", f"unexpected response shape: {what}")


def utcnow() -> datetime:
    return datetime.now(UTC)


def opt(opts: Opts | None, key: str, default: Any) -> Any:
    if opts and opts.get(key) is not None:
        return opts[key]
    return default


def state_for(used_pct: float) -> State:
    return "exhausted" if used_pct >= 100 else "active"


def number(value: Any, what: str) -> float:
    """A real number (bool and NaN rejected); anything else is a shape error."""
    if isinstance(value, bool) or not isinstance(value, int | float) or math.isnan(value):
        raise shape_error(f"{what} is not a number")
    return float(value)


def mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise shape_error(f"{what} is not an object")
    return value


def iso_datetime(value: Any, what: str) -> datetime:
    if not isinstance(value, str):
        raise shape_error(f"{what} is not a timestamp string")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise shape_error(f"{what} is not ISO-8601") from None
    if dt.tzinfo is None:
        raise shape_error(f"{what} has no timezone")
    return dt


def epoch_datetime(value: Any, what: str, *, millis: bool = False) -> datetime:
    seconds = number(value, what) / (1000 if millis else 1)
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        raise shape_error(f"{what} out of range") from None


def to_error(exc: BaseException) -> Error:
    """Map any exception to a typed, sanitized Error."""
    if isinstance(exc, ReaderError):
        return Error(exc.code, exc.message[:200])
    if isinstance(exc, TimeoutError):
        return Error("timeout", "timed out")
    if isinstance(exc, KeyError | TypeError | ValueError | AttributeError | IndexError):
        return Error("shape", f"unexpected response shape: {type(exc).__name__}: {str(exc)[:100]}")
    return Error("unavailable", f"{type(exc).__name__}")


def error_reading(provider: str, source: str, exc: BaseException, now: datetime) -> ProviderReading:
    return ProviderReading(provider, "error", None, source, now, to_error(exc), ())


def guarded_parse(
    provider: str, source: str, fn: Callable[[Any, datetime], ProviderReading], raw: Any, now: datetime
) -> ProviderReading:
    """Run a pure parse function; every exception becomes an error reading."""
    try:
        return fn(raw, now)
    except Exception as exc:
        return error_reading(provider, source, exc, now)


def normalize_past_resets(reading: ProviderReading, now: datetime) -> ProviderReading:
    """Single shared rule: a window whose reset time has passed is a fresh window (0%, active) and
    the provider is downgraded to stale (never upgraded). Error readings pass through untouched."""
    if reading.status == "error":
        return reading
    changed = False
    windows = []
    for w in reading.windows:
        if w.resets_at is not None and w.resets_at <= now:
            w = replace(w, used_pct=0.0, state="active")
            changed = True
        windows.append(w)
    if not changed:
        return reading
    return replace(reading, status="stale", windows=tuple(windows))


def http_get_json(url: str, headers: Mapping[str, str], timeout: float) -> dict[str, Any]:
    """GET and return {"status": int, "body": parsed JSON or None}. The only network call."""
    req = urllib.request.Request(url, headers=dict(headers), method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status, data = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, data = exc.code, exc.read()
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError from None
        raise ReaderError("unavailable", "network error") from None
    try:
        body = json.loads(data)
    except ValueError:
        body = None
    return {"status": status, "body": body}


def check_http_status(status: int) -> None:
    if status in (401, 403):
        raise ReaderError("auth", f"HTTP {status}: credential rejected")
    if status != 200:
        raise ReaderError("http", f"HTTP {status}")
