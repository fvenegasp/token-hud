"""On-disk store (plan sections 6 and 9): state.json, history.jsonl, lock and log paths."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
HISTORY_MAX_BYTES = 5 * 1024 * 1024
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 2


class AlreadyRunning(Exception):
    """Another collector holds the lock."""


def base_dir() -> Path:
    """CUOTA_HOME, else $XDG_CACHE_HOME/cuota, else ~/.cache/cuota. Created with mode 0700."""
    if os.environ.get("CUOTA_HOME"):
        path = Path(os.environ["CUOTA_HOME"])
    elif os.environ.get("XDG_CACHE_HOME"):
        path = Path(os.environ["XDG_CACHE_HOME"]) / "cuota"
    else:
        path = Path.home() / ".cache" / "cuota"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def state_path() -> Path:
    return base_dir() / "state.json"


def history_path() -> Path:
    return base_dir() / "history.jsonl"


def log_path() -> Path:
    return base_dir() / "collect.log"


def load_state() -> dict[str, Any] | None:
    """The parsed state, or None when missing or corrupt. Never raises."""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        return None
    return data


def write_state(state: Mapping[str, Any]) -> None:
    """Atomic: tmp file in the same dir, flush + fsync, os.replace. Mode 0600. No stray tmp on failure."""
    path = state_path()
    fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_history(entries: Iterable[Mapping[str, Any]], *, max_bytes: int = HISTORY_MAX_BYTES) -> int:
    """Append one JSON line per entry. Rotates to history.jsonl.1 first when the file exceeds max_bytes."""
    lines = [json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in entries]
    if not lines:
        return 0
    path = history_path()
    try:
        if path.stat().st_size > max_bytes:
            os.replace(path, path.with_name(path.name + ".1"))
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write("".join(lines))
    os.chmod(path, 0o600)
    return len(lines)


@contextmanager
def collect_lock() -> Iterator[None]:
    """Exclusive non-blocking flock on <base>/collect.lock; raises AlreadyRunning if held."""
    fd = os.open(base_dir() / "collect.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning("another collector is running") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
