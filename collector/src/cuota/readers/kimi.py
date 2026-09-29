"""Kimi Code reader: GET /coding/v1/usages with the console API key (plan 4.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from cuota import secrets
from cuota.model import ProviderReading, Window
from cuota.readers import base
from cuota.readers.base import Opts, ReaderError

PROVIDER = "kimi"
SOURCE = "api"
URL = "https://api.kimi.com/coding/v1/usages"
KEY_NAME = "KIMI_API_KEY"
DEFAULT_TIMEOUT = 10.0
# The real 5 h window is limits[].detail (verified 2026-09-29: HTTP 403 "5-hour limit" while
# usages.limit_5h.used_ratio was 0). usages.limit_5h is only a fallback; monthly comes from usages.
_MINUTES = {"TIME_UNIT_MINUTE": 1, "TIME_UNIT_HOUR": 60}
NOTE_5H_FALLBACK = "5 h desde usages.limit_5h (sin limits[])"


def _limits_5h(body: Any) -> Window | None:
    limits = body.get("limits")
    if not isinstance(limits, list):
        return None
    for entry in limits:
        if not isinstance(entry, dict):
            continue
        win = entry.get("window")
        unit = _MINUTES.get(win.get("timeUnit")) if isinstance(win, dict) else None
        duration = win.get("duration") if unit else None
        if isinstance(duration, bool) or not isinstance(duration, int | float) or duration * unit != 300:
            continue
        detail = base.mapping(entry.get("detail"), "limits.detail")
        try:
            used_n, limit_n = float(detail["used"]), float(detail["limit"])
        except (KeyError, TypeError, ValueError):
            raise base.shape_error("limits.detail used/limit not numeric") from None
        if not limit_n > 0 or used_n < 0 or used_n > limit_n:
            raise base.shape_error("limits.detail used/limit out of range")
        used = round(used_n / limit_n * 100, 6)
        resets_at = base.iso_datetime(detail["resetTime"], "limits.detail.resetTime")
        return Window("5h", None, used, resets_at, base.state_for(used))
    return None


def _usages_window(usages: Any, key: str, kind: str) -> Window | None:
    if key not in usages:
        return None
    item = base.mapping(usages[key], key)
    ratio = base.number(item["used_ratio"], f"{key}.used_ratio")
    if not 0 <= ratio <= 1:
        raise base.shape_error(f"{key}.used_ratio out of range")
    used = round(ratio * 100, 6)
    resets_at = base.iso_datetime(item["reset_time"], f"{key}.reset_time")
    return Window(kind, None, used, resets_at, base.state_for(used))  # type: ignore[arg-type]


def fetch(opts: Opts | None = None) -> dict[str, Any]:
    key = secrets.resolve(KEY_NAME, env=base.opt(opts, "env", None), zshrc=base.opt(opts, "zshrc", None))
    if not key:
        raise ReaderError("missing_credential", f"{KEY_NAME} not found in environment or ~/.zshrc")
    timeout = float(base.opt(opts, "timeout", DEFAULT_TIMEOUT))
    return base.http_get_json(str(base.opt(opts, "url", URL)), {"Authorization": f"Bearer {key}"}, timeout)


def _parse(raw: Any, now: datetime) -> ProviderReading:
    base.check_http_status(raw["status"])
    body = base.mapping(raw["body"], "body")
    usages = base.mapping(body.get("usages"), "usages")
    windows: list[Window] = []
    note = None
    five = _limits_5h(body)
    if five is None:
        five = _usages_window(usages, "limit_5h", "5h")
        note = NOTE_5H_FALLBACK if five else None
    if five:
        windows.append(five)
    monthly = _usages_window(usages, "limit_month_total", "monthly")
    if monthly:
        windows.append(monthly)
    if not windows:
        raise base.shape_error("no known windows")
    return ProviderReading(PROVIDER, "ok", None, SOURCE, now, None, tuple(windows), note)


def parse(raw: Any, *, now: datetime) -> ProviderReading:
    return base.guarded_parse(PROVIDER, SOURCE, _parse, raw, now)


def read(opts: Opts | None = None, *, now: datetime | None = None) -> ProviderReading:
    now = now or base.utcnow()
    try:
        raw = fetch(opts)
    except Exception as exc:
        return base.error_reading(PROVIDER, SOURCE, exc, now)
    return base.normalize_past_resets(parse(raw, now=now), now)
