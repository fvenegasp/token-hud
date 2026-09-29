"""Store: atomic state, history rotation, lock, permissions (offline, CUOTA_HOME=tmp)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from cuota import store


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CUOTA_HOME", str(tmp_path / "cuota"))
    return tmp_path / "cuota"


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_base_dir_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CUOTA_HOME")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert store.base_dir() == tmp_path / "xdg" / "cuota"
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert store.base_dir() == tmp_path / "h" / ".cache" / "cuota"


def test_load_state_missing_and_corrupt(home: Path) -> None:
    assert store.load_state() is None
    store.base_dir()
    (home / "state.json").write_text("{not json", encoding="utf-8")
    assert store.load_state() is None
    (home / "state.json").write_text("[1]", encoding="utf-8")
    assert store.load_state() is None


def test_write_state_roundtrip_and_perms(home: Path) -> None:
    state: dict[str, Any] = {"schema_version": 1, "generated_at": "2026-09-29T17:00:00Z", "providers": {}}
    store.write_state(state)
    assert store.load_state() == state
    assert mode(home / "state.json") == 0o600
    assert mode(home) == 0o700


def test_write_failure_keeps_previous_state(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old: dict[str, Any] = {"schema_version": 1, "generated_at": "A", "providers": {"x": {}}}
    store.write_state(old)

    def boom_dump(obj: Any, fh: Any, **kw: Any) -> None:
        fh.write('{"schema_ver')  # partial write, then die
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(json, "dump", boom_dump)
        with pytest.raises(OSError):
            store.write_state({"schema_version": 1, "generated_at": "B", "providers": {}})
    assert store.load_state() == old
    assert [p.name for p in home.iterdir() if p.name.endswith(".tmp")] == []


def test_replace_failure_keeps_previous_state_and_no_tmp(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old: dict[str, Any] = {"schema_version": 1, "generated_at": "A", "providers": {}}
    store.write_state(old)
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
        with pytest.raises(OSError):
            store.write_state({"schema_version": 1, "generated_at": "B", "providers": {}})
    assert store.load_state() == old
    assert [p.name for p in home.iterdir() if p.name.endswith(".tmp")] == []


def test_history_append_and_perms(home: Path) -> None:
    n = store.append_history([{"a": 1}, {"a": 2}])
    assert n == 2
    store.append_history([{"a": 3}])
    lines = (home / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["a"] for x in lines] == [1, 2, 3]
    assert mode(home / "history.jsonl") == 0o600
    assert store.append_history([]) == 0


def test_history_rotation_at_threshold(home: Path) -> None:
    store.append_history([{"gen": 1, "pad": "x" * 100}], max_bytes=50)
    store.append_history([{"gen": 2}], max_bytes=50)  # file > 50 bytes -> rotate, then append
    assert json.loads((home / "history.jsonl.1").read_text(encoding="utf-8"))["gen"] == 1
    current = (home / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["gen"] for x in current] == [2]
    store.append_history([{"gen": 3, "pad": "y" * 100}], max_bytes=50)  # 10 bytes so far: no rotation
    store.append_history([{"gen": 4}], max_bytes=50)  # replaces the old .1
    rotated = (home / "history.jsonl.1").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["gen"] for x in rotated] == [2, 3]
    assert mode(home / "history.jsonl.1") == 0o600


def test_history_no_rotation_below_threshold(home: Path) -> None:
    store.append_history([{"gen": 1}], max_bytes=10_000)
    store.append_history([{"gen": 2}], max_bytes=10_000)
    assert not (home / "history.jsonl.1").exists()


def test_lock_is_exclusive_and_released() -> None:
    with store.collect_lock():
        with pytest.raises(store.AlreadyRunning), store.collect_lock():
            pass
    with store.collect_lock():  # released after exit
        pass
