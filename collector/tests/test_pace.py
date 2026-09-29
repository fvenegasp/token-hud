"""Pace model (pure): synthetic series, deterministic `now`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cuota import pace
from cuota.model import Window

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
RESET_5H = NOW + timedelta(hours=2, minutes=30)  # window is half elapsed -> expected 50 %


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def pt(minutes_ago: float, used: float, resets: datetime | None = RESET_5H) -> dict[str, Any]:
    return {
        "ts": iso(NOW - timedelta(minutes=minutes_ago)),
        "provider": "p",
        "kind": "5h",
        "group": None,
        "used_pct": used,
        "resets_at": iso(resets) if resets else None,
        "status": "ok",
    }


def w5h(used: float, state: str = "active", resets: datetime | None = RESET_5H, kind: str = "5h") -> Window:
    return Window(kind, None, used, resets, state)  # type: ignore[arg-type]


def test_under_pace_alcanza() -> None:
    p = pace.compute_pace(w5h(20.0), [], NOW)
    assert p is not None
    assert p.expected_pct == 50.0 and p.ratio == 0.4
    assert p.verdict == "bajo_ritmo" and p.projected_exhaust_at is None


def test_over_pace_without_projection_two_points() -> None:
    p = pace.compute_pace(w5h(70.0), [pt(30, 60.0)], NOW)  # 2 points only (history + now)
    assert p is not None and p.verdict == "sobre_ritmo" and p.projected_exhaust_at is None


def test_over_pace_within_margin_is_still_bajo() -> None:
    p = pace.compute_pace(w5h(55.0), [], NOW)
    assert p is not None and p.verdict == "bajo_ritmo"


def test_projection_exhausts_before_reset_se_agota() -> None:
    hist = [pt(60, 40.0), pt(30, 55.0)]  # 0.5 %/min -> from 70 at now, 100 in 60 min < 150 min left
    p = pace.compute_pace(w5h(70.0), hist, NOW)
    assert p is not None and p.verdict == "se_agota"
    assert p.projected_exhaust_at == NOW + timedelta(minutes=60)


def test_projection_exhausts_after_reset_is_sobre_ritmo() -> None:
    hist = [pt(60, 58.0), pt(30, 64.0)]  # 0.2 %/min: 70 -> 100 in 150+ min... make it slower
    hist = [pt(60, 62.0), pt(30, 66.0)]  # ~0.133 %/min -> 225 min > 150 min left
    p = pace.compute_pace(w5h(70.0), hist, NOW)
    assert p is not None and p.verdict == "sobre_ritmo"
    assert p.projected_exhaust_at is None  # exhaustion after the reset is not projected


def test_flat_or_decreasing_slope_no_projection() -> None:
    for hist, used in (([pt(60, 70.0), pt(30, 70.0)], 70.0), ([pt(60, 80.0), pt(30, 75.0)], 70.0)):
        p = pace.compute_pace(w5h(used), hist, NOW)
        assert p is not None and p.projected_exhaust_at is None and p.verdict == "sobre_ritmo"


def test_previous_window_points_excluded() -> None:
    old = RESET_5H - timedelta(hours=5)
    hist = [pt(60, 5.0, old), pt(30, 30.0, old)]  # steep, but a different window
    p = pace.compute_pace(w5h(70.0), hist, NOW)
    assert p is not None and p.projected_exhaust_at is None and p.verdict == "sobre_ritmo"


def test_reset_jitter_within_120s_is_same_window() -> None:
    hist = [pt(60, 40.0, RESET_5H + timedelta(seconds=90)), pt(30, 55.0, RESET_5H - timedelta(seconds=90))]
    p = pace.compute_pace(w5h(70.0), hist, NOW)
    assert p is not None and p.verdict == "se_agota"


def test_future_points_ignored() -> None:
    hist = [pt(60, 40.0), pt(-30, 90.0)]  # one point after now is dropped -> only 2 points
    p = pace.compute_pace(w5h(70.0), hist, NOW)
    assert p is not None and p.projected_exhaust_at is None


def test_span_under_10_minutes_no_projection() -> None:
    hist = [pt(9, 60.0), pt(5, 65.0)]  # 3 points but only 9 min span, very steep
    p = pace.compute_pace(w5h(70.0), hist, NOW)
    assert p is not None and p.projected_exhaust_at is None and p.verdict == "sobre_ritmo"


def test_fewer_than_three_points_never_se_agota() -> None:
    p = pace.compute_pace(w5h(90.0), [pt(60, 10.0)], NOW)  # 2 points, span 60 min, steep
    assert p is not None and p.projected_exhaust_at is None and p.verdict == "sobre_ritmo"


def test_exhausted_and_idle_and_no_reset() -> None:
    p = pace.compute_pace(w5h(100.0, "exhausted"), [pt(60, 40.0)], NOW)
    assert p is not None and p.verdict == "agotada" and p.expected_pct is None and p.ratio is None
    assert pace.compute_pace(w5h(0.0, "idle"), [], NOW) == pace.Pace(None, None, None, "sin_datos")
    assert pace.compute_pace(w5h(10.0, resets=None), [], NOW) == pace.Pace(None, None, None, "sin_datos")


def test_mcp_skipped() -> None:
    assert pace.compute_pace(w5h(10.0, kind="mcp"), [], NOW) is None
    state = {"providers": {"p": {"windows": [w5h(10.0, kind="mcp").to_dict()]}}}
    assert pace.pace_for_state(state, [], NOW) == {"p": {}}


def test_ratio_none_when_expected_below_one() -> None:
    resets = NOW + timedelta(hours=5) - timedelta(minutes=1)  # 1 min elapsed = 0.33 %
    p = pace.compute_pace(w5h(10.0, resets=resets), [], NOW)
    assert p is not None and p.ratio is None and p.expected_pct is not None and p.expected_pct < 1
    assert p.verdict == "sobre_ritmo"


def test_elapsed_clamped() -> None:
    far = pace.compute_pace(w5h(0.0, resets=NOW + timedelta(hours=9)), [], NOW)
    assert far is not None and far.expected_pct == 0.0
    past = pace.compute_pace(w5h(10.0, resets=NOW - timedelta(hours=1)), [], NOW)
    assert past is not None and past.expected_pct == 100.0


def test_weekly_duration_and_expected() -> None:
    p = pace.compute_pace(Window("weekly", None, 10.0, NOW + timedelta(days=3, hours=12), "active"), [], NOW)
    assert p is not None and p.expected_pct == 50.0 and p.ratio == 0.2


def test_monthly_is_calendar_month_not_30_days() -> None:
    reset = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)  # window = Sep 1 .. Oct 1 = 30 d (30 in Sep)
    assert pace.window_duration("monthly", reset) == timedelta(days=30)
    reset31 = datetime(2026, 10, 31, 6, 0, tzinfo=UTC)  # Sep 30 (clamped from 31) .. Oct 31 = 31 d
    assert pace.window_duration("monthly", reset31) == timedelta(days=31)
    reset_mar = datetime(2027, 3, 31, 0, 0, tzinfo=UTC)  # Feb 28 .. Mar 31
    assert pace.window_duration("monthly", reset_mar) == timedelta(days=31)
    jan = datetime(2027, 1, 15, 0, 0, tzinfo=UTC)  # Dec 15 .. Jan 15
    assert pace.window_duration("monthly", jan) == timedelta(days=31)
    # 31-day month, halfway through: expected differs from a fixed 30 d
    now = reset31 - timedelta(days=15, hours=12)
    p = pace.compute_pace(Window("monthly", None, 10.0, reset31, "active"), [], now)
    assert p is not None and p.expected_pct is not None
    assert abs(p.expected_pct - 50.0) < 0.01


def test_pace_for_state_keys_groups_and_iso() -> None:
    g = Window("weekly", "Gemini Models", 10.0, NOW + timedelta(days=3, hours=12), "active")
    a = w5h(70.0)
    state = {"providers": {"p": {"windows": [a.to_dict(), g.to_dict()]}, "bad": {"windows": [{"x": 1}]}}}
    lines = [pt(60, 40.0), "{corrupt", pt(30, 55.0), "[1]", 5]
    out = pace.pace_for_state(state, lines, NOW)
    assert set(out["p"]) == {"5h", "weekly:Gemini Models"}
    assert out["p"]["5h"]["verdict"] == "se_agota"
    assert out["p"]["5h"]["projected_exhaust_at"] == iso(NOW + timedelta(minutes=60))
    assert out["p"]["weekly:Gemini Models"]["verdict"] == "bajo_ritmo"
    assert out["bad"] == {}


def test_history_of_other_group_or_kind_not_used() -> None:
    state = {"providers": {"p": {"windows": [w5h(70.0).to_dict()]}}}
    other = [{**pt(60, 40.0), "group": "X"}, {**pt(30, 55.0), "kind": "weekly"}]
    out = pace.pace_for_state(state, other, NOW)
    assert out["p"]["5h"]["verdict"] == "sobre_ritmo"


def test_near_zero_slope_does_not_overflow() -> None:
    hist = [pt(60, 5.0), pt(40, 5.0), pt(20, 5.0)]
    p = pace.compute_pace(w5h(5.0000001), hist, NOW)
    assert p is not None and p.projected_exhaust_at is None and p.verdict == "bajo_ritmo"


def test_huge_slope_and_degenerate_inputs_do_not_raise() -> None:
    p = pace.compute_pace(w5h(100.0), [pt(11, 0.0), pt(5, 50.0)], NOW)
    assert p is not None and p.verdict == "se_agota" and p.projected_exhaust_at is not None
    same_ts = [pt(30, 10.0), pt(30, 20.0), pt(30, 30.0)]  # identical timestamps collapse to one point
    q = pace.compute_pace(w5h(40.0), same_ts, NOW)
    assert q is not None and q.projected_exhaust_at is None


def test_nan_inf_history_values_ignored() -> None:
    hist = [pt(60, float("nan")), pt(40, float("inf")), pt(20, float("-inf"))]
    p = pace.compute_pace(w5h(40.0), hist, NOW)
    assert p is not None and p.projected_exhaust_at is None and p.verdict == "bajo_ritmo"


def test_pace_for_state_isolates_failing_window(monkeypatch: pytest.MonkeyPatch) -> None:
    real = pace.compute_pace

    def boom(w: Window, h: Any, now: datetime) -> Any:
        if w.kind == "weekly":
            raise OverflowError("boom")
        return real(w, h, now)

    monkeypatch.setattr(pace, "compute_pace", boom)
    weekly = Window("weekly", None, 10.0, NOW + timedelta(days=3), "active")
    state = {"providers": {"p": {"windows": [w5h(20.0).to_dict(), weekly.to_dict()]}}}
    out = pace.pace_for_state(state, [], NOW)
    assert out["p"]["weekly"]["verdict"] == "sin_datos" and out["p"]["5h"]["verdict"] == "bajo_ritmo"
