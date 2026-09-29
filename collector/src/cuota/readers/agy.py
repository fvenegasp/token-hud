"""agy (Antigravity) reader: `agy -p /usage --output-format json`, Gemini group only (plan 4.5)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from typing import Any

from cuota.model import Kind, ProviderReading, Window
from cuota.readers import base
from cuota.readers.base import Opts, ReaderError

PROVIDER = "agy"
SOURCE = "agy-cli"
CMD = ("agy", "-p", "/usage", "--output-format", "json")
DEFAULT_TIMEOUT = 40.0
DEFAULT_GROUPS = ("Gemini Models",)
_KINDS: dict[str, Kind] = {"5h": "5h", "weekly": "weekly"}


def fetch(opts: Opts | None = None) -> dict[str, Any]:
    cmd = list(base.opt(opts, "cmd", CMD))
    timeout = float(base.opt(opts, "timeout", DEFAULT_TIMEOUT))
    try:
        proc = subprocess.run(  # noqa: S603
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError from None
    except OSError:
        raise ReaderError("unavailable", "agy could not be executed") from None
    if proc.returncode != 0:
        raise ReaderError("unavailable", f"agy exited with code {proc.returncode}")
    try:
        body = json.loads(proc.stdout)
    except ValueError:
        raise base.shape_error("agy output is not JSON") from None
    return {"body": body, "groups": list(base.opt(opts, "groups", DEFAULT_GROUPS))}


def _parse(raw: Any, now: datetime) -> ProviderReading:
    body = base.mapping(raw["body"], "body")
    wanted = list(raw.get("groups") or DEFAULT_GROUPS)
    if body.get("status") != "SUCCESS":
        raise ReaderError("unavailable", "agy did not report SUCCESS")
    command = base.mapping(body.get("command"), "command")
    groups = base.mapping(command.get("data"), "command.data").get("groups")
    if not isinstance(groups, list):
        raise base.shape_error("command.data.groups is not a list")
    by_name = {g.get("name"): g for g in groups if isinstance(g, dict)}
    windows: list[Window] = []
    for name in wanted:
        if name not in by_name:
            raise base.shape_error(f"group {name!r} not present")
        buckets = by_name[name].get("buckets")
        if not isinstance(buckets, list):
            raise base.shape_error("buckets is not a list")
        for bucket in buckets:
            bucket = base.mapping(bucket, "bucket")
            kind = _KINDS.get(bucket.get("window"))  # type: ignore[arg-type]
            if kind is None:
                continue
            remaining = base.number(bucket["remaining_fraction"], "remaining_fraction")
            if not 0 <= remaining <= 1:
                raise base.shape_error("remaining_fraction out of range")
            used = round((1 - remaining) * 100, 6)
            resets_at = base.iso_datetime(bucket["reset_time"], "reset_time")
            windows.append(Window(kind, name, used, resets_at, base.state_for(used)))
    if not windows:
        raise base.shape_error("no usable buckets")
    return ProviderReading(PROVIDER, "ok", None, SOURCE, now, None, tuple(windows))


def parse(raw: Any, *, now: datetime) -> ProviderReading:
    return base.guarded_parse(PROVIDER, SOURCE, _parse, raw, now)


def read(opts: Opts | None = None, *, now: datetime | None = None) -> ProviderReading:
    now = now or base.utcnow()
    try:
        raw = fetch(opts)
    except Exception as exc:
        return base.error_reading(PROVIDER, SOURCE, exc, now)
    return base.normalize_past_resets(parse(raw, now=now), now)
