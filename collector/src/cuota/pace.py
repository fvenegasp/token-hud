"""Consumption pace (plan section 7). Pure: all inputs are passed in, `now` is injected."""

from __future__ import annotations

import calendar
import json
import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from cuota.model import Window, iso_z, parse_iso_z

Verdict = Literal["agotada", "se_agota", "sobre_ritmo", "bajo_ritmo", "sin_datos"]

LOG = logging.getLogger("cuota.pace")
WINDOW_TOLERANCE_S = 120.0
MIN_POINTS = 3
MIN_SPAN_S = 600.0
OVER_MARGIN_PCT = 5.0
MIN_EXPECTED_PCT = 1.0
FIXED_DURATIONS = {"5h": timedelta(hours=5), "weekly": timedelta(days=7)}


@dataclass(frozen=True)
class Pace:
    expected_pct: float | None
    ratio: float | None
    projected_exhaust_at: datetime | None
    verdict: Verdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_pct": None if self.expected_pct is None else round(self.expected_pct, 1),
            "ratio": None if self.ratio is None else round(self.ratio, 2),
            "projected_exhaust_at": iso_z(self.projected_exhaust_at) if self.projected_exhaust_at else None,
            "verdict": self.verdict,
        }


def _minus_one_month(dt: datetime) -> datetime:
    year, month = (dt.year - 1, 12) if dt.month == 1 else (dt.year, dt.month - 1)
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def window_duration(kind: str, resets_at: datetime) -> timedelta | None:
    if kind in FIXED_DURATIONS:
        return FIXED_DURATIONS[kind]
    if kind == "monthly":
        end = resets_at.astimezone(UTC)
        return end - _minus_one_month(end)
    return None


def _projection(
    window: Window, resets_at: datetime, history: Iterable[Mapping[str, Any]], now: datetime
) -> datetime | None:
    points: dict[float, float] = {}
    for p in history:
        try:
            ts = parse_iso_z(p["ts"])
            reset = p.get("resets_at")
            if not reset or abs((parse_iso_z(reset) - resets_at).total_seconds()) > WINDOW_TOLERANCE_S:
                continue  # a different resets_at is a previous window
            if ts > now:
                continue
            used = float(p["used_pct"])
            if not math.isfinite(used):
                continue
            points[ts.timestamp()] = used
        except (KeyError, TypeError, ValueError):
            continue
    points[now.timestamp()] = window.used_pct
    if len(points) < MIN_POINTS:
        return None
    xs = sorted(points)
    if xs[-1] - xs[0] < MIN_SPAN_S:
        return None
    ys = [points[x] for x in xs]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if not sxx > 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    if not math.isfinite(slope) or slope <= 0:
        return None
    with_slope = mx + (100.0 - my) / slope
    if not math.isfinite(with_slope):
        return None
    t100 = max(with_slope, now.timestamp())
    if t100 >= resets_at.timestamp():
        return None  # exhaustion at/after the reset is irrelevant; never build a datetime from it
    return datetime.fromtimestamp(t100, UTC)


def compute_pace(window: Window, history: Iterable[Mapping[str, Any]], now: datetime) -> Pace | None:
    """None for kinds without pace (mcp)."""
    if window.kind == "mcp":
        return None
    if window.state == "exhausted":
        return Pace(None, None, None, "agotada")
    resets_at = window.resets_at
    if window.state == "idle" or resets_at is None:
        return Pace(None, None, None, "sin_datos")
    duration = window_duration(window.kind, resets_at)
    if duration is None:
        return None
    elapsed = min(1.0, max(0.0, 1 - (resets_at - now) / duration))
    expected = elapsed * 100
    ratio = window.used_pct / expected if expected >= MIN_EXPECTED_PCT else None
    exhaust = _projection(window, resets_at, history, now)
    if exhaust is not None and exhaust < resets_at:
        verdict: Verdict = "se_agota"
    elif window.used_pct > expected + OVER_MARGIN_PCT:
        verdict = "sobre_ritmo"
    else:
        verdict = "bajo_ritmo"
    return Pace(expected, ratio, exhaust, verdict)


def pace_key(w: Window) -> str:
    return f"{w.kind}:{w.group}" if w.group else w.kind


def _as_dict(line: Any) -> Mapping[str, Any] | None:
    if isinstance(line, str):
        try:
            line = json.loads(line)
        except ValueError:
            return None
    return line if isinstance(line, Mapping) else None


def pace_for_state(
    state: Mapping[str, Any], history_lines: Iterable[Any], now: datetime
) -> dict[str, dict[str, dict[str, Any]]]:
    """{provider: {"<kind>[:<group>]": pace dict}}. History lines: JSON strings or dicts."""
    by_series: dict[tuple[Any, Any, Any], list[Mapping[str, Any]]] = {}
    for line in history_lines:
        d = _as_dict(line)
        if d is not None:
            by_series.setdefault((d.get("provider"), d.get("kind"), d.get("group")), []).append(d)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for name, entry in state.get("providers", {}).items():
        paces: dict[str, dict[str, Any]] = {}
        try:
            windows = [Window.from_dict(w) for w in entry.get("windows") or ()]
        except Exception:
            windows = []
        for w in windows:
            try:
                pace = compute_pace(w, by_series.get((name, w.kind, w.group), ()), now)
            except Exception:
                LOG.warning("pace failed provider=%s kind=%s", name, w.kind)
                pace = Pace(None, None, None, "sin_datos")
            if pace is not None:
                paces[pace_key(w)] = pace.to_dict()
        out[name] = paces
    return out
