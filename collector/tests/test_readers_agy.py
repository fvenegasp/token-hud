from __future__ import annotations

import json
import subprocess

import pytest

from cuota.readers import agy
from tests.conftest import by_kind, utc

NOW = utc(2026, 9, 29, 17, 40)


def raw_of(fx, groups=None):
    raw = {"body": fx("agy", "usage_ok")["body"]}
    if groups is not None:
        raw["groups"] = groups
    return raw


def test_real_fixture_gemini_only_and_inverted(fx):
    r = agy.parse(raw_of(fx), now=NOW)
    assert (r.status, r.plan, r.source, r.error) == ("ok", None, "agy-cli", None)
    w = by_kind(r)
    assert set(w) == {"5h", "weekly"}
    assert {x.group for x in r.windows} == {"Gemini Models"}
    assert (w["5h"].used_pct, w["5h"].resets_at, w["5h"].state) == (
        0.0,
        utc(2026, 9, 29, 22, 34, 24),
        "active",
    )
    # remaining 0.8645931482315063 -> used 13.54068517...
    assert w["weekly"].used_pct == pytest.approx(13.540685, abs=1e-5)
    assert (w["weekly"].resets_at, w["weekly"].state) == (utc(2026, 10, 2, 12, 15, 36), "active")


def test_group_filter_excludes_claude_and_gpt(fx):
    r = agy.parse(raw_of(fx), now=NOW)
    assert "Claude and GPT models" not in {w.group for w in r.windows}
    assert len(r.windows) == 2
    r2 = agy.parse(raw_of(fx, groups=["Claude and GPT models"]), now=NOW)
    assert {w.group for w in r2.windows} == {"Claude and GPT models"}


def test_unknown_group_is_shape_error_not_empty_ok(fx):
    r = agy.parse(raw_of(fx, groups=["Nope"]), now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "shape", ())


def test_remaining_zero_is_exhausted(fx):
    raw = raw_of(fx)
    raw["body"]["command"]["data"]["groups"][0]["buckets"][1]["remaining_fraction"] = 0
    w = by_kind(agy.parse(raw, now=NOW))["5h"]
    assert (w.used_pct, w.state) == (100.0, "exhausted")


def test_unknown_bucket_window_dropped(fx):
    raw = raw_of(fx)
    raw["body"]["command"]["data"]["groups"][0]["buckets"].append(
        {"id": "x", "window": "daily", "remaining_fraction": 0.1, "reset_time": "2026-09-30T00:00:00Z"}
    )
    assert len(agy.parse(raw, now=NOW).windows) == 2


@pytest.mark.parametrize("bad", [1.2, -0.1, "0.5", None])
def test_remaining_out_of_range_is_shape_error(fx, bad):
    raw = raw_of(fx)
    raw["body"]["command"]["data"]["groups"][0]["buckets"][0]["remaining_fraction"] = bad
    r = agy.parse(raw, now=NOW)
    assert (r.status, r.error.code) == ("error", "shape")


def test_text_response_is_ignored(fx):
    raw = raw_of(fx)
    raw["body"]["response"] = "Gemini Models\tWeekly Limit Remaining\t1%\t2026-10-02T12:15:36Z\n"
    assert by_kind(agy.parse(raw, now=NOW))["weekly"].used_pct == pytest.approx(13.540685, abs=1e-5)


@pytest.mark.parametrize("body", [{"status": "SUCCESS"}, {"status": "SUCCESS", "command": {"data": {}}}, []])
def test_unknown_shape_is_shape_error(body):
    r = agy.parse({"body": body}, now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "shape", ())


def test_non_success_status_is_error(fx):
    raw = raw_of(fx)
    raw["body"]["status"] = "ERROR"
    assert agy.parse(raw, now=NOW).status == "error"


def fake_run_factory(fx, seen):
    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(fx("agy", "usage_ok")["body"]), stderr=""
        )

    return fake_run


def test_read_runs_command_with_devnull_stdin_and_timeout(fx, monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", fake_run_factory(fx, seen))
    r = agy.read(now=NOW)
    assert r.status == "ok"
    assert seen["cmd"] == ["agy", "-p", "/usage", "--output-format", "json"]
    assert seen["stdin"] == subprocess.DEVNULL and seen["timeout"] == 40.0


def test_read_groups_and_timeout_overridable(fx, monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", fake_run_factory(fx, seen))
    r = agy.read({"groups": ["Claude and GPT models"], "timeout": 7}, now=NOW)
    assert {w.group for w in r.windows} == {"Claude and GPT models"} and seen["timeout"] == 7.0


def test_read_timeout_is_timeout_error(monkeypatch):
    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    r = agy.read({"timeout": 0.1}, now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "timeout", ())


def test_read_binary_missing_and_nonzero_and_garbage(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert agy.read(now=NOW).error.code == "unavailable"

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, "", ""))
    assert agy.read(now=NOW).error.code == "unavailable"

    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "not json", "")
    )
    assert agy.read(now=NOW).error.code == "shape"
