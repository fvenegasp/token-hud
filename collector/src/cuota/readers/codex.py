"""Codex reader: `codex app-server` JSON-RPC over stdio, rollout JSONL as fallback (plan 4.2)."""

from __future__ import annotations

import json
import logging
import os
import select
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from cuota.model import Kind, ProviderReading, Window
from cuota.readers import base
from cuota.readers.base import Opts, ReaderError

PROVIDER = "codex"
SOURCE_APP = "app-server"
SOURCE_ROLLOUT = "rollout"
CMD = ("codex", "-s", "read-only", "-a", "never", "app-server")
DEFAULT_TIMEOUT = 15.0  # cold start of app-server measured above 5 s
SESSIONS_DIR = "~/.codex/sessions"
MAX_ROLLOUT_FILES = 30
_DURATIONS: dict[int, Kind] = {300: "5h", 10080: "weekly"}
_log = logging.getLogger("cuota.readers.codex")


def _send(proc: Any, message: dict[str, Any]) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    proc.stdin.flush()


def _read_response(proc: Any, buf: bytearray, want_id: int, deadline: float) -> dict[str, Any]:
    """Read newline-delimited JSON from stdout until the response with `want_id`, or time out."""
    fd = proc.stdout.fileno()
    while True:
        while (nl := buf.find(b"\n")) != -1:
            line = bytes(buf[:nl])
            del buf[: nl + 1]
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return msg
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            raise TimeoutError
        chunk = os.read(fd, 65536)
        if not chunk:
            raise ReaderError("unavailable", "codex app-server closed its output")
        buf += chunk


def fetch_app_server(opts: Opts | None = None) -> dict[str, Any]:
    cmd = list(base.opt(opts, "cmd", CMD))
    timeout = float(base.opt(opts, "timeout", DEFAULT_TIMEOUT))
    deadline = time.monotonic() + timeout
    buf = bytearray()  # shared across responses: one read may carry several lines
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
    except OSError:
        raise ReaderError("unavailable", "codex could not be executed") from None
    try:
        _send(
            proc,
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "cuota", "version": "0.1.0"}},
            },
        )
        _read_response(proc, buf, 1, deadline)
        _send(proc, {"method": "initialized"})
        _send(proc, {"id": 2, "method": "account/rateLimits/read"})
        body = _read_response(proc, buf, 2, deadline)
    except BrokenPipeError:
        raise ReaderError("unavailable", "codex app-server closed its input") from None
    finally:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except Exception:  # noqa: S110
            pass
    return {"via": SOURCE_APP, "body": body}


def _populated(rate_limits: Any) -> bool:
    return isinstance(rate_limits, dict) and any(
        isinstance(rate_limits.get(slot), dict) for slot in ("primary", "secondary")
    )


def fetch_rollout(opts: Opts | None = None) -> dict[str, Any]:
    """Newest token_count event whose rate_limits windows are populated."""
    root = Path(str(base.opt(opts, "sessions_dir", SESSIONS_DIR))).expanduser()
    try:
        files = sorted(root.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files = []
    if not files:
        raise ReaderError("missing_source", "no codex rollout files found")
    for path in files[:MAX_ROLLOUT_FILES]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if "token_count" not in line:
                continue
            try:
                event = json.loads(line)
                payload = event["payload"]
            except (ValueError, KeyError, TypeError):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("type") == "token_count"
                and _populated(payload.get("rate_limits"))
            ):
                return {"via": SOURCE_ROLLOUT, "event": event}
    raise ReaderError("missing_source", "no codex rollout event with populated rate limits")


def _note(err: Any, timeout: float) -> str:
    """Short sanitized reason for using the rollout fallback (no keys, no bodies)."""
    reason = f"timeout after {timeout:g}s" if err.code == "timeout" else f"{err.code}: {err.message[:80]}"
    return f"app-server: {reason}; usando respaldo rollout"


def fetch(opts: Opts | None = None) -> dict[str, Any]:
    """App-server first; on any failure fall back to the rollout files."""
    try:
        return fetch_app_server(opts)
    except Exception as exc:
        first = base.to_error(exc)
        try:
            raw = fetch_rollout(opts)
        except Exception as exc2:
            raise ReaderError(
                first.code, f"{first.message}; rollout fallback: {base.to_error(exc2).message}"
            ) from None
        raw["note"] = _note(first, float(base.opt(opts, "timeout", DEFAULT_TIMEOUT)))
        return raw


def _windows(snapshot: Any, keys: tuple[str, str, str]) -> list[Window]:
    used_key, dur_key, reset_key = keys
    windows: list[Window] = []
    for slot in ("primary", "secondary"):
        item = snapshot.get(slot)
        if item is None:
            continue
        item = base.mapping(item, slot)
        duration = base.number(item[dur_key], f"{slot}.{dur_key}")
        kind = _DURATIONS.get(int(duration)) if duration == int(duration) else None
        if kind is None:  # classified by duration only, never by slot; unknown durations are dropped
            _log.warning("dropping codex window with unknown duration %s min", duration)
            continue
        used = base.number(item[used_key], f"{slot}.{used_key}")
        resets_at = base.epoch_datetime(item[reset_key], f"{slot}.{reset_key}")
        windows.append(Window(kind, None, used, resets_at, base.state_for(used)))
    return windows


def _parse_app_server(body: Any, now: datetime) -> ProviderReading:
    msg = base.mapping(body, "response")
    if "error" in msg:
        raise ReaderError("unavailable", "codex app-server returned an error")
    result = base.mapping(msg.get("result"), "result")
    by_id = result.get("rateLimitsByLimitId")
    snapshot = by_id.get("codex") if isinstance(by_id, dict) else None
    if snapshot is None:
        snapshot = result.get("rateLimits")
    snapshot = base.mapping(snapshot, "rate limits snapshot")
    windows = _windows(snapshot, ("usedPercent", "windowDurationMins", "resetsAt"))
    if not windows:
        raise base.shape_error("no recognised codex windows")
    plan = snapshot.get("planType")
    return ProviderReading(
        PROVIDER, "ok", plan if isinstance(plan, str) else None, SOURCE_APP, now, None, tuple(windows)
    )


def _parse_rollout(event: Any, now: datetime, note: str | None = None) -> ProviderReading:
    event = base.mapping(event, "event")
    payload = base.mapping(event.get("payload"), "payload")
    limits = payload.get("rate_limits")
    if not _populated(limits):
        raise ReaderError("missing_source", "codex rollout event has no populated rate limits")
    windows = _windows(limits, ("used_percent", "window_minutes", "resets_at"))
    if not windows:
        raise base.shape_error("no recognised codex windows")
    fetched_at = base.iso_datetime(event.get("timestamp"), "timestamp")
    plan = limits.get("plan_type")
    return ProviderReading(
        PROVIDER,
        "stale",
        plan if isinstance(plan, str) else None,
        SOURCE_ROLLOUT,
        fetched_at,
        None,
        tuple(windows),
        note,
    )


def _parse(raw: Any, now: datetime) -> ProviderReading:
    via = raw["via"]
    if via == SOURCE_APP:
        return _parse_app_server(raw["body"], now)
    if via == SOURCE_ROLLOUT:
        return _parse_rollout(raw["event"], now, raw.get("note"))
    raise base.shape_error("unknown codex raw kind")


def parse(raw: Any, *, now: datetime) -> ProviderReading:
    source = raw.get("via", SOURCE_APP) if isinstance(raw, dict) else SOURCE_APP
    return base.guarded_parse(PROVIDER, source, _parse, raw, now)


def read(opts: Opts | None = None, *, now: datetime | None = None) -> ProviderReading:
    now = now or base.utcnow()
    try:
        raw = fetch(opts)
    except Exception as exc:
        return base.error_reading(PROVIDER, SOURCE_APP, exc, now)
    reading = parse(raw, now=now)
    if reading.status == "error" and raw.get("via") == SOURCE_APP:
        # app-server answered but its payload was unusable: try the fallback before giving up
        try:
            raw2 = fetch_rollout(opts)
            raw2["note"] = _note(reading.error, float(base.opt(opts, "timeout", DEFAULT_TIMEOUT)))
            fallback = parse(raw2, now=now)
        except Exception:
            return reading
        if fallback.status != "error":
            return base.normalize_past_resets(fallback, now)
        return reading
    return base.normalize_past_resets(reading, now)
