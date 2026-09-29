from __future__ import annotations

import copy
import json
import os
import subprocess

from cuota.readers import codex
from tests.conftest import by_kind, utc

NOW = utc(2026, 9, 29, 17, 40)


def app_raw(fx, mutate=None):
    body = fx("codex", "app_server_weekly_exhausted")["body"]
    if mutate:
        mutate(body)
    return {"via": "app-server", "body": body}


def roll_raw(fx, name="rollout_token_count_ok"):
    return {"via": "rollout", "event": fx("codex", name)["body"]}


def test_app_server_real_fixture(fx):
    r = codex.parse(app_raw(fx), now=NOW)
    assert (r.status, r.plan, r.source, r.error, r.fetched_at) == ("ok", "plus", "app-server", None, NOW)
    w = by_kind(r)
    assert set(w) == {"5h", "weekly"}
    assert (w["5h"].used_pct, w["5h"].resets_at, w["5h"].state) == (
        0.0,
        utc(2026, 9, 29, 22, 34, 11),
        "active",
    )
    assert (w["weekly"].used_pct, w["weekly"].resets_at, w["weekly"].state) == (
        100.0,
        utc(2026, 10, 3, 20, 23, 1),
        "exhausted",
    )


def test_classification_is_by_duration_not_slot(fx):
    def swap(body):
        snap = body["result"]["rateLimitsByLimitId"]["codex"]
        snap["primary"], snap["secondary"] = snap["secondary"], snap["primary"]

    w = by_kind(codex.parse(app_raw(fx, swap), now=NOW))
    # primary slot now holds the 10080-min window: it must be weekly, not 5h
    assert (w["weekly"].used_pct, w["5h"].used_pct) == (100.0, 0.0)


def test_unknown_duration_dropped_never_guessed(fx):
    def mutate(body):
        body["result"]["rateLimitsByLimitId"]["codex"]["primary"]["windowDurationMins"] = 60

    r = codex.parse(app_raw(fx, mutate), now=NOW)
    assert [w.kind for w in r.windows] == ["weekly"]


def test_only_unknown_durations_is_shape_error(fx):
    def mutate(body):
        snap = body["result"]["rateLimitsByLimitId"]["codex"]
        snap["primary"]["windowDurationMins"] = 60
        snap["secondary"]["windowDurationMins"] = 61

    r = codex.parse(app_raw(fx, mutate), now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "shape", ())


def test_picks_codex_snapshot_by_limit_id_falling_back_to_rate_limits(fx):
    def other_first(body):
        by_id = body["result"]["rateLimitsByLimitId"]
        other = copy.deepcopy(by_id["codex"])
        other["limitId"], other["primary"]["usedPercent"] = "codex_other", 77
        body["result"]["rateLimitsByLimitId"] = {"codex_other": other, "codex": by_id["codex"]}
        body["result"]["rateLimits"]["primary"]["usedPercent"] = 55

    assert by_kind(codex.parse(app_raw(fx, other_first), now=NOW))["5h"].used_pct == 0.0

    def no_by_id(body):
        del body["result"]["rateLimitsByLimitId"]

    assert by_kind(codex.parse(app_raw(fx, no_by_id), now=NOW))["weekly"].used_pct == 100.0


def test_app_server_error_and_unknown_shape(fx):
    r = codex.parse({"via": "app-server", "body": {"id": 2, "error": {"message": "x"}}}, now=NOW)
    assert (r.status, r.error.code) == ("error", "unavailable")
    r = codex.parse({"via": "app-server", "body": {"id": 2, "result": {"foo": 1}}}, now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "shape", ())


def test_rollout_real_fixture_is_stale_with_snake_case(fx):
    r = codex.parse(roll_raw(fx), now=NOW)
    assert (r.status, r.plan, r.source) == ("stale", "plus", "rollout")
    assert r.fetched_at == utc(2026, 9, 28, 13, 28, 17, 238000)
    w = by_kind(r)
    assert (w["5h"].used_pct, w["5h"].resets_at, w["5h"].state) == (
        82.0,
        utc(2026, 9, 28, 16, 17, 43),
        "active",
    )
    assert (w["weekly"].used_pct, w["weekly"].resets_at, w["weekly"].state) == (
        100.0,
        utc(2026, 10, 3, 20, 23, 1),
        "exhausted",
    )


def test_rollout_null_windows_is_error_never_zero(fx):
    r = codex.parse(roll_raw(fx, "rollout_token_count_null"), now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "missing_source", ())


# ---- fetch level ---------------------------------------------------------------------------------


class FakeProc:
    def __init__(self, replies: bytes = b""):
        self.r, self.w = os.pipe()
        os.write(self.w, replies)
        self.stdout = os.fdopen(self.r, "rb", buffering=0)
        self.written: list[bytes] = []
        self.stdin = self
        self.killed = False
        self.waited = False

    # stdin API
    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        self.stdout.close()
        os.close(self.w)


def replies(fx):
    body = fx("codex", "app_server_weekly_exhausted")["body"]
    return (json.dumps({"id": 1, "result": {}}) + "\n" + json.dumps(body) + "\n").encode()


def test_fetch_speaks_documented_protocol_and_kills_process(fx, monkeypatch):
    proc = FakeProc(replies(fx))
    seen = {}

    def popen(cmd, **kw):
        seen["cmd"] = cmd
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)
    r = codex.read({"sessions_dir": "/nonexistent"}, now=NOW)
    assert r.status == "ok" and r.source == "app-server"
    assert seen["cmd"] == ["codex", "-s", "read-only", "-a", "never", "app-server"]
    msgs = [json.loads(b) for b in proc.written]
    assert [m.get("method") for m in msgs] == ["initialize", "initialized", "account/rateLimits/read"]
    assert msgs[0]["id"] == 1 and msgs[2]["id"] == 2 and "id" not in msgs[1]
    assert proc.killed and proc.waited


def test_app_server_hang_is_timeout_and_process_killed(monkeypatch, tmp_path):
    proc = FakeProc(b"")  # never answers
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: proc)
    r = codex.read({"timeout": 0.2, "sessions_dir": str(tmp_path)}, now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "timeout", ())
    assert proc.killed and proc.waited


def test_app_server_closing_early_is_unavailable_and_killed(monkeypatch, tmp_path):
    proc = FakeProc(b"")
    os.close(proc.w)
    proc.w = os.dup(0)  # keep wait() happy; the write end was closed so reads hit EOF
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: proc)
    r = codex.read({"timeout": 1, "sessions_dir": str(tmp_path)}, now=NOW)
    assert (r.status, r.error.code) == ("error", "unavailable")
    assert proc.killed


def test_binary_missing_falls_back_to_rollout_as_stale(fx, monkeypatch, tmp_path):
    def missing(cmd, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "Popen", missing)
    day = tmp_path / "2026" / "09" / "28"
    day.mkdir(parents=True)
    ok = json.dumps(fx("codex", "rollout_token_count_ok")["body"])
    null = json.dumps(fx("codex", "rollout_token_count_null")["body"])
    (day / "rollout-a.jsonl").write_text(f"{ok}\n{null}\n{null}\n")  # newest events are null
    r = codex.read({"sessions_dir": str(tmp_path)}, now=NOW)
    assert (r.status, r.source) == ("stale", "rollout")
    assert by_kind(r)["5h"].used_pct == 0.0  # reset in the past -> normalized


def test_fallback_with_only_null_events_is_error_not_zero(fx, monkeypatch, tmp_path):
    def missing(cmd, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "Popen", missing)
    null = json.dumps(fx("codex", "rollout_token_count_null")["body"])
    (tmp_path / "rollout-x.jsonl").write_text(f"{null}\n{null}\n")
    r = codex.read({"sessions_dir": str(tmp_path)}, now=NOW)
    assert (r.status, r.windows) == ("error", ())
    assert r.error.code == "unavailable"


def test_no_rollout_files_is_error(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    r = codex.read({"sessions_dir": str(tmp_path)}, now=NOW)
    assert r.status == "error"


# ---- shared past-reset normalization + fallback note ----------------------------------------------


def test_rollout_past_reset_5h_is_zero_and_stale_future_untouched(fx):
    from cuota.readers import base

    raw = roll_raw(fx)
    r = base.normalize_past_resets(codex.parse(raw, now=NOW), NOW)
    w = by_kind(r)
    assert r.status == "stale"
    assert (w["5h"].used_pct, w["5h"].state) == (0.0, "active")  # reset 2026-09-28T16:17Z is past
    assert (w["weekly"].used_pct, w["weekly"].state) == (100.0, "exhausted")  # reset 10-03 is future


def test_normalizer_never_upgrades_or_touches_errors_and_future_only(fx):
    from cuota.readers import base

    r = codex.parse(app_raw(fx), now=NOW)
    assert base.normalize_past_resets(r, NOW) is r  # nothing past: identical, still ok
    err = codex.parse({"via": "app-server", "body": {"id": 2, "result": {}}}, now=NOW)
    assert base.normalize_past_resets(err, NOW) is err


def test_read_fallback_is_normalized_and_carries_note(fx, monkeypatch, tmp_path):
    def missing(cmd, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "Popen", missing)
    (tmp_path / "rollout-a.jsonl").write_text(
        json.dumps(fx("codex", "rollout_token_count_ok")["body"]) + "\n"
    )
    r = codex.read({"sessions_dir": str(tmp_path)}, now=NOW)
    assert (r.status, r.source) == ("stale", "rollout")
    assert by_kind(r)["5h"].used_pct == 0.0
    assert r.note == "app-server: unavailable: codex could not be executed; usando respaldo rollout"
    assert r.to_dict()["note"] == r.note


def test_timeout_fallback_note_mentions_timeout(fx, monkeypatch, tmp_path):
    proc = FakeProc(b"")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: proc)
    (tmp_path / "rollout-a.jsonl").write_text(
        json.dumps(fx("codex", "rollout_token_count_ok")["body"]) + "\n"
    )
    r = codex.read({"timeout": 0.2, "sessions_dir": str(tmp_path)}, now=NOW)
    assert r.note == "app-server: timeout after 0.2s; usando respaldo rollout"


def test_app_server_success_has_no_note_and_default_timeout_is_15():
    assert codex.DEFAULT_TIMEOUT == 15.0
