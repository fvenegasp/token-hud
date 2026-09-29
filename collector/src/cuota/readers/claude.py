"""Claude reader: the status-line JSON persisted by Claude Code (plan 4.1)."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from cuota.model import ProviderReading, Window
from cuota.readers import base
from cuota.readers.base import Opts, ReaderError

PROVIDER = "claude"
SOURCE = "statusline"
ENV_VAR = "CUOTA_CLAUDE_STATUSLINE_FILE"
DEFAULT_PATH = "~/.cache/cuota/claude-statusline.json"
LEGACY_PATH = "~/.claude/daemon/.statusline-last.json"
DEFAULT_TIMEOUT = 1.0
STALE_AFTER_S = 15 * 60
_WINDOWS = (("five_hour", "5h"), ("seven_day", "weekly"))


def source_path(opts: Opts | None = None) -> Path:
    """Resolve the status-line file: opts["path"], else $CUOTA_CLAUDE_STATUSLINE_FILE, else the default
    cache file, falling back to the legacy location when the default does not exist."""
    explicit = base.opt(opts, "path", None) or os.environ.get(ENV_VAR)
    if explicit:
        return Path(str(explicit)).expanduser()
    default = Path(DEFAULT_PATH).expanduser()
    legacy = Path(LEGACY_PATH).expanduser()
    return default if default.exists() or not legacy.exists() else legacy


def fetch(opts: Opts | None = None) -> dict[str, Any]:
    """Read the file. Returns {"data": parsed JSON, "mtime": epoch seconds}."""
    path = source_path(opts)
    try:
        mtime = path.stat().st_mtime
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReaderError("missing_source", "status-line file not found") from None
    except OSError:
        raise ReaderError("missing_source", "status-line file unreadable") from None
    except ValueError:
        raise base.shape_error("status-line file is not JSON") from None
    return {"data": data, "mtime": mtime}


def _parse(raw: Any, now: datetime) -> ProviderReading:
    fetched_at = base.epoch_datetime(raw["mtime"], "mtime")
    data = base.mapping(raw["data"], "status-line")
    limits = data.get("rate_limits")
    if not isinstance(limits, dict) or not limits:
        raise ReaderError("missing_source", "status-line file has no rate_limits")
    stale = (now - fetched_at).total_seconds() > STALE_AFTER_S
    windows: list[Window] = []
    for key, kind in _WINDOWS:
        if key not in limits:
            continue
        item = base.mapping(limits[key], key)
        used = base.number(item["used_percentage"], f"{key}.used_percentage")
        resets_at = base.epoch_datetime(item["resets_at"], f"{key}.resets_at")
        windows.append(Window(kind, None, used, resets_at, base.state_for(used)))  # type: ignore[arg-type]
    if not windows:
        raise ReaderError("missing_source", "status-line rate_limits has no known windows")
    return ProviderReading(
        PROVIDER, "stale" if stale else "ok", None, SOURCE, fetched_at, None, tuple(windows)
    )


def parse(raw: Any, *, now: datetime) -> ProviderReading:
    return base.guarded_parse(PROVIDER, SOURCE, _parse, raw, now)


def read(opts: Opts | None = None, *, now: datetime | None = None) -> ProviderReading:
    now = now or base.utcnow()
    try:
        raw = fetch(opts)
    except Exception as exc:
        return base.error_reading(PROVIDER, SOURCE, exc, now)
    return base.normalize_past_resets(parse(raw, now=now), now)
