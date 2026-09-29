from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta, timezone

import pytest

from cuota.model import Error, ProviderReading, Window

T = datetime(2026, 9, 29, 17, 0, 0, tzinfo=UTC)


def w(**kw):
    base = {"kind": "5h", "group": None, "used_pct": 10.0, "resets_at": T, "state": "active"}
    return Window(**{**base, **kw})


@pytest.mark.parametrize("bad", [-0.01, 100.01, 150, float("nan"), True, "5"])
def test_used_pct_out_of_range_is_rejected_not_clamped(bad):
    with pytest.raises(ValueError):
        w(used_pct=bad)


@pytest.mark.parametrize("ok", [0, 0.0, 50, 100, 100.0])
def test_used_pct_bounds_accepted(ok):
    assert w(used_pct=ok).used_pct == float(ok)


def test_enums_validated():
    with pytest.raises(ValueError):
        w(kind="daily")
    with pytest.raises(ValueError):
        w(state="unknown")
    with pytest.raises(ValueError):
        Error("boom", "x")
    with pytest.raises(ValueError):
        ProviderReading("p", "weird", None, "s", T)


def test_naive_datetimes_rejected():
    with pytest.raises(ValueError):
        w(resets_at=datetime(2026, 1, 1))  # noqa: DTZ001
    with pytest.raises(ValueError):
        ProviderReading("p", "error", None, "s", datetime(2026, 1, 1), Error("shape", "x"))  # noqa: DTZ001


def test_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        w().used_pct = 5  # type: ignore[misc]


def test_error_status_requires_error_and_vice_versa():
    with pytest.raises(ValueError):
        ProviderReading("p", "error", None, "s", T)
    with pytest.raises(ValueError):
        ProviderReading("p", "ok", None, "s", T, Error("shape", "x"), (w(),))
    with pytest.raises(ValueError):
        ProviderReading("p", "ok", None, "s", T, None, ())  # never an empty "ok"


def test_to_dict_matches_contract_shape():
    tz = timezone(timedelta(hours=-3))
    reading = ProviderReading(
        "codex",
        "ok",
        "plus",
        "app-server",
        T.astimezone(tz),
        None,
        (
            w(kind="5h", used_pct=0, resets_at=datetime(2026, 9, 29, 21, 54, 49, tzinfo=UTC)),
            w(kind="weekly", group="g", used_pct=100, resets_at=None, state="exhausted"),
        ),
    )
    assert reading.to_dict() == {
        "status": "ok",
        "plan": "plus",
        "source": "app-server",
        "fetched_at": "2026-09-29T17:00:00Z",
        "error": None,
        "note": None,
        "windows": [
            {
                "kind": "5h",
                "group": None,
                "used_pct": 0.0,
                "resets_at": "2026-09-29T21:54:49Z",
                "state": "active",
            },
            {"kind": "weekly", "group": "g", "used_pct": 100.0, "resets_at": None, "state": "exhausted"},
        ],
    }


def test_error_reading_to_dict():
    r = ProviderReading("kimi", "error", None, "api", T, Error("auth", "HTTP 401"))
    assert r.to_dict()["error"] == {"code": "auth", "message": "HTTP 401"}
    assert r.to_dict()["windows"] == []


def test_note_in_to_dict_and_validated():
    r = ProviderReading("p", "stale", None, "s", T, None, (w(),), "why")
    assert r.to_dict()["note"] == "why"
    with pytest.raises(ValueError):
        ProviderReading("p", "stale", None, "s", T, None, (w(),), 5)  # type: ignore[arg-type]
