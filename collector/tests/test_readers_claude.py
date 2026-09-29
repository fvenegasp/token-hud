from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from cuota.readers import base, claude
from tests.conftest import by_kind, utc

MTIME = utc(2026, 9, 29, 17, 34, 10)


def raw_from(fx, mtime=MTIME, mutate=None):
    body = fx("claude", "statusline_ok")["body"]
    if mutate:
        mutate(body)
    return {"data": body, "mtime": mtime.timestamp()}


def test_real_fixture_ok(fx):
    r = claude.parse(raw_from(fx), now=utc(2026, 9, 29, 17, 40))
    assert (r.status, r.plan, r.source, r.error) == ("ok", None, "statusline", None)
    assert r.fetched_at == MTIME
    w = by_kind(r)
    assert set(w) == {"5h", "weekly"}
    assert (w["5h"].used_pct, w["5h"].resets_at, w["5h"].state) == (49.0, utc(2026, 9, 29, 18, 0), "active")
    assert (w["weekly"].used_pct, w["weekly"].resets_at, w["weekly"].state) == (
        24.0,
        utc(2026, 10, 5, 11, 0),
        "active",
    )


def test_old_mtime_is_stale_but_keeps_values(fx):
    r = claude.parse(raw_from(fx), now=MTIME + timedelta(minutes=16))
    assert r.status == "stale"
    assert by_kind(r)["5h"].used_pct == 49.0


def test_exactly_15_minutes_is_still_ok(fx):
    assert claude.parse(raw_from(fx), now=MTIME + timedelta(minutes=15)).status == "ok"


def test_past_reset_is_zero_active_and_provider_stale(fx):
    now = utc(2026, 9, 29, 18, 30)
    parsed = claude.parse(raw_from(fx, mtime=now - timedelta(minutes=1)), now=now)
    assert parsed.status == "ok" and by_kind(parsed)["5h"].used_pct == 49.0  # parse alone does not normalize
    r = base.normalize_past_resets(parsed, now)
    assert r.status == "stale"
    w = by_kind(r)
    assert (w["5h"].used_pct, w["5h"].state) == (0.0, "active")
    assert w["weekly"].used_pct == 24.0


def test_exhausted_state(fx):
    r = claude.parse(
        raw_from(fx, mutate=lambda b: b["rate_limits"]["five_hour"].update(used_percentage=100)),
        now=utc(2026, 9, 29, 17, 40),
    )
    assert by_kind(r)["5h"].state == "exhausted"


def test_missing_rate_limits_is_missing_source_error(fx):
    r = claude.parse(raw_from(fx, mutate=lambda b: b.pop("rate_limits")), now=utc(2026, 9, 29, 17, 40))
    assert (r.status, r.error.code, r.windows) == ("error", "missing_source", ())


def test_unknown_shape_is_shape_error_never_zero(fx):
    r = claude.parse(
        raw_from(fx, mutate=lambda b: b["rate_limits"]["five_hour"].pop("used_percentage")),
        now=utc(2026, 9, 29, 17, 40),
    )
    assert (r.status, r.error.code, r.windows) == ("error", "shape", ())


def test_out_of_range_percentage_is_shape_error(fx):
    r = claude.parse(
        raw_from(fx, mutate=lambda b: b["rate_limits"]["five_hour"].update(used_percentage=140)),
        now=utc(2026, 9, 29, 17, 40),
    )
    assert (r.status, r.error.code) == ("error", "shape")


def test_read_from_file_uses_mtime(fx, tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(fx("claude", "statusline_ok")["body"]))
    os.utime(p, (MTIME.timestamp(), MTIME.timestamp()))
    r = claude.read({"path": str(p)}, now=utc(2026, 9, 29, 17, 20) + timedelta(minutes=30))
    assert r.status == "stale" and r.fetched_at == MTIME
    r = claude.read({"path": str(p)}, now=utc(2026, 9, 29, 17, 40))
    assert r.status == "ok"


def test_read_missing_file_and_bad_json(tmp_path):
    now = datetime(2026, 9, 29, 17, 40, tzinfo=UTC)
    assert claude.read({"path": str(tmp_path / "nope.json")}, now=now).error.code == "missing_source"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert claude.read({"path": str(bad)}, now=now).error.code == "shape"


def _write_statusline(p, fx):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(fx("claude", "statusline_ok")["body"]))


def test_source_path_env_default_and_legacy_fallback(fx, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(claude.ENV_VAR, raising=False)
    default = tmp_path / ".cache" / "cuota" / "claude-statusline.json"
    legacy = tmp_path / ".claude" / "daemon" / ".statusline-last.json"
    # nothing exists: point at the default (the path doctor reports)
    assert claude.source_path() == default
    # only the legacy file exists: fallback
    _write_statusline(legacy, fx)
    assert claude.source_path() == legacy
    assert claude.read(now=utc(2026, 9, 29, 17, 40)).status in ("ok", "stale")
    # default exists: it wins
    _write_statusline(default, fx)
    assert claude.source_path() == default
    # env var beats both; explicit opts beat env
    custom = tmp_path / "custom.json"
    monkeypatch.setenv(claude.ENV_VAR, str(custom))
    assert claude.source_path() == custom
    other = tmp_path / "other.json"
    assert claude.source_path({"path": str(other)}) == other
    assert claude.read(now=utc(2026, 9, 29, 17, 40)).error.code == "missing_source"
