"""Collector (offline: fake readers injected, CUOTA_HOME=tmp)."""

from __future__ import annotations

import json
import re
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cuota import collect as collect_mod
from cuota import store
from cuota.model import ERROR_CODES, KINDS, STATES, STATUSES, Error, ProviderReading, Window

NOW = datetime(2026, 9, 29, 17, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CUOTA_HOME", str(tmp_path / "cuota"))
    return tmp_path / "cuota"


def ok(name: str, used: float = 40.0, *, at: datetime = NOW, resets: datetime | None = None) -> Any:
    reading = ProviderReading(
        name, "ok", "plus", "fake", at, None,
        (Window("5h", None, used, resets or at + timedelta(hours=3), "active"),),
    )  # fmt: skip
    return lambda: reading


def err(name: str, code: str = "http") -> Any:
    reading = ProviderReading(name, "error", None, "fake", NOW, Error(code, "boom"), ())  # type: ignore[arg-type]
    return lambda: reading


def hang(seconds: float) -> Any:
    return lambda: time.sleep(seconds)


def history(home: Path) -> list[dict[str, Any]]:
    p = home / "history.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_hang_does_not_block_pass(home: Path) -> None:
    readers = {"a": ok("a"), "b": ok("b"), "slow": hang(3)}
    t0 = time.monotonic()
    state = collect_mod.collect(readers=readers, now=NOW, deadline_s=0.5)
    assert time.monotonic() - t0 < 1.5
    slow = state["providers"]["slow"]
    assert slow["status"] == "error" and slow["error"]["code"] == "timeout"
    assert slow["note"] == "sin respuesta antes de 0.5s" and slow["windows"] == []
    assert state["providers"]["a"]["status"] == "ok" and state["providers"]["b"]["status"] == "ok"
    assert store.load_state() == state


def test_reader_raising_becomes_error(home: Path) -> None:
    def bad() -> Any:
        raise RuntimeError("kaboom")

    state = collect_mod.collect(readers={"a": ok("a"), "bad": bad}, now=NOW)
    assert state["providers"]["bad"]["status"] == "error"
    assert state["providers"]["bad"]["error"]["code"] == "unavailable"
    assert state["providers"]["a"]["status"] == "ok"


def test_merge_keeps_last_good_on_error(home: Path) -> None:
    collect_mod.collect(readers={"a": ok("a", 42.0)}, now=NOW)
    state = collect_mod.collect(readers={"a": err("a", "auth")}, now=LATER)
    p = state["providers"]["a"]
    assert p["status"] == "error" and p["error"]["code"] == "auth"
    assert [w["used_pct"] for w in p["windows"]] == [42.0]
    assert p["fetched_at"] == "2026-09-29T17:00:00Z"
    assert p["note"] == "mostrando último valor bueno"
    assert p["plan"] == "plus"


def test_merge_past_reset_goes_to_zero_but_stays_error(home: Path) -> None:
    collect_mod.collect(readers={"a": ok("a", 90.0, resets=NOW + timedelta(minutes=30))}, now=NOW)
    state = collect_mod.collect(readers={"a": err("a")}, now=LATER)
    p = state["providers"]["a"]
    assert p["status"] == "error"
    assert p["windows"][0]["used_pct"] == 0.0 and p["windows"][0]["state"] == "active"


def test_merge_carried_twice_keeps_original_fetched_at(home: Path) -> None:
    collect_mod.collect(readers={"a": ok("a", 42.0)}, now=NOW)
    collect_mod.collect(readers={"a": err("a")}, now=LATER)
    p = collect_mod.collect(readers={"a": err("a")}, now=LATER + timedelta(hours=1))["providers"]["a"]
    assert p["fetched_at"] == "2026-09-29T17:00:00Z" and p["windows"][0]["used_pct"] == 42.0


def test_no_previous_gives_empty_windows_never_zero(home: Path) -> None:
    p = collect_mod.collect(readers={"a": err("a")}, now=NOW)["providers"]["a"]
    assert p["status"] == "error" and p["windows"] == []


def test_previous_error_without_windows_stays_empty(home: Path) -> None:
    collect_mod.collect(readers={"a": err("a")}, now=NOW)
    p = collect_mod.collect(readers={"a": err("a")}, now=LATER)["providers"]["a"]
    assert p["windows"] == []


def test_new_ok_replaces_previous(home: Path) -> None:
    collect_mod.collect(readers={"a": ok("a", 10.0)}, now=NOW)
    p = collect_mod.collect(readers={"a": ok("a", 20.0, at=LATER)}, now=LATER)["providers"]["a"]
    assert p["status"] == "ok" and p["windows"][0]["used_pct"] == 20.0 and p["note"] is None


def test_subset_leaves_others_untouched(home: Path) -> None:
    readers = {"a": ok("a", 10.0), "kimi": ok("kimi", 10.0)}
    first = collect_mod.collect(readers=readers, now=NOW)
    readers2 = {"a": ok("a", 99.0, at=LATER), "kimi": ok("kimi", 55.0, at=LATER)}
    second = collect_mod.collect(["kimi"], readers=readers2, now=LATER)
    assert second["providers"]["a"] == first["providers"]["a"]
    assert second["providers"]["kimi"]["windows"][0]["used_pct"] == 55.0


def test_unknown_provider_rejected(home: Path) -> None:
    with pytest.raises(ValueError):
        collect_mod.collect(["nope"], readers={"a": ok("a")}, now=NOW)


def test_state_matches_contract(home: Path) -> None:
    readers = {"a": ok("a", 12.5), "b": err("b", "timeout")}
    collect_mod.collect(readers=readers, now=NOW)
    state = json.loads((home / "state.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == 1
    assert ISO_Z.match(state["generated_at"])
    assert set(state) == {"schema_version", "generated_at", "providers"}
    for p in state["providers"].values():
        assert set(p) == {"status", "plan", "source", "fetched_at", "error", "note", "windows", "pace"}
        assert p["status"] in STATUSES and ISO_Z.match(p["fetched_at"])
        assert (p["error"] is None) == (p["status"] != "error")
        if p["error"]:
            assert p["error"]["code"] in ERROR_CODES
        for w in p["windows"]:
            assert set(w) == {"kind", "group", "used_pct", "resets_at", "state"}
            assert w["kind"] in KINDS and w["state"] in STATES and 0 <= w["used_pct"] <= 100
            assert w["resets_at"] is None or ISO_Z.match(w["resets_at"])


def test_history_only_fresh_ok_or_stale(home: Path) -> None:
    collect_mod.collect(readers={"a": ok("a", 42.0), "b": ok("b", 5.0)}, now=NOW)
    assert {(h["provider"], h["status"]) for h in history(home)} == {("a", "ok"), ("b", "ok")}
    n = len(history(home))
    # a fails (carried value), b fresh again, c errors without previous
    collect_mod.collect(readers={"a": err("a"), "b": ok("b", 6.0, at=LATER), "c": err("c")}, now=LATER)
    new = history(home)[n:]
    assert {h["provider"] for h in new} == {"b"}
    assert set(new[0]) == {"ts", "provider", "kind", "group", "used_pct", "resets_at", "status"}
    assert new[0]["ts"] == "2026-09-29T18:00:00Z" and new[0]["used_pct"] == 6.0


def test_history_stale_fresh_reading_is_appended(home: Path) -> None:
    stale = ProviderReading("a", "stale", None, "fake", NOW, None, (Window("5h", None, 0.0, None, "active"),))
    collect_mod.collect(readers={"a": lambda: stale}, now=NOW)
    assert [h["status"] for h in history(home)] == ["stale"]


def test_lock_held_raises_and_main_returns_75(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with store.collect_lock():
        with pytest.raises(store.AlreadyRunning):
            collect_mod.collect(readers={"a": ok("a")}, now=NOW)
        assert collect_mod.main(["--provider", "kimi"]) == 75
    assert "already running" in capsys.readouterr().err


def test_permissions(home: Path) -> None:
    collect_mod.collect(readers={"a": ok("a")}, now=NOW)
    mode = lambda p: stat.S_IMODE(p.stat().st_mode)  # noqa: E731
    assert mode(home) == 0o700
    for name in ("state.json", "history.jsonl", "collect.log"):
        assert mode(home / name) == 0o600


def test_log_line_has_no_secrets(home: Path) -> None:
    collect_mod.collect(readers={"a": ok("a"), "b": err("b", "auth")}, now=NOW)
    text = (home / "collect.log").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) == 2
    assert "provider=b status=error source=fake" in lines[1] and "error=auth" in lines[1]
    assert "boom" not in text


def test_main_exit_codes_and_output(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(collect_mod, "READERS", {"kimi": type("R", (), {"read": staticmethod(err("kimi"))})})
    monkeypatch.setattr(collect_mod, "_read_fn", lambda r: r.read)
    assert collect_mod.main([]) == 0  # provider in error is data, not failure
    assert capsys.readouterr().out.startswith("kimi: error (")
    assert collect_mod.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["providers"]["kimi"]["status"] == "error"


def test_main_unexpected_failure_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(collect_mod, "_collect", boom)
    assert collect_mod.main([]) == 1


def test_registry_names() -> None:
    assert set(collect_mod.READERS) == {"claude", "codex", "kimi", "zai", "agy"}


# --- pace (F4) ----------------------------------------------------------------------------------------


def _hist_line(ts: datetime, used: float, resets: datetime, provider: str = "a") -> str:
    return json.dumps(
        {
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider": provider,
            "kind": "5h",
            "group": None,
            "used_pct": used,
            "resets_at": resets.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "ok",
        }
    )


def test_collect_writes_pace_with_projection_and_skips_corrupt_lines(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    resets = NOW + timedelta(hours=2, minutes=30)
    lines = [
        _hist_line(NOW - timedelta(minutes=60), 40.0, resets),
        "{not json",
        "",
        _hist_line(NOW - timedelta(minutes=30), 55.0, resets),
        _hist_line(NOW - timedelta(days=40), 1.0, resets),  # older than 32 days: ignored
    ]
    (home / "history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (home / "history.jsonl.1").write_text("garbage\n", encoding="utf-8")
    collect_mod.collect(readers={"a": ok("a", 70.0, resets=resets)}, now=NOW)
    state = json.loads((home / "state.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == 1
    pace = state["providers"]["a"]["pace"]["5h"]
    assert pace["verdict"] == "se_agota" and pace["expected_pct"] == 50.0
    assert ISO_Z.match(pace["projected_exhaust_at"])


def test_collect_pace_for_error_provider_with_carried_windows(home: Path) -> None:
    collect_mod.collect(readers={"a": ok("a", 30.0, resets=NOW + timedelta(hours=2, minutes=30))}, now=NOW)
    collect_mod.collect(readers={"a": err("a")}, now=NOW + timedelta(minutes=1))
    entry = json.loads((home / "state.json").read_text(encoding="utf-8"))["providers"]["a"]
    assert entry["status"] == "error" and entry["pace"]["5h"]["verdict"] == "bajo_ritmo"


def test_collect_pace_empty_without_windows(home: Path) -> None:
    collect_mod.collect(readers={"b": err("b")}, now=NOW)
    assert json.loads((home / "state.json").read_text(encoding="utf-8"))["providers"]["b"]["pace"] == {}


def test_pace_exception_does_not_fail_collect(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise OverflowError("boom")

    monkeypatch.setattr("cuota.pace.compute_pace", boom)
    collect_mod.collect(readers={"a": ok("a", 30.0)}, now=NOW)
    entry = json.loads((home / "state.json").read_text(encoding="utf-8"))["providers"]["a"]
    assert entry["status"] == "ok" and entry["pace"]["5h"]["verdict"] == "sin_datos"


def test_main_failure_logs_traceback(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise OverflowError("boom")

    monkeypatch.setattr(collect_mod, "_collect", boom)
    assert collect_mod.main([]) == 1
    assert "OverflowError" in capsys.readouterr().err
    assert "Traceback" in (home / "collect.log").read_text(encoding="utf-8")
