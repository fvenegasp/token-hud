"""z.ai (GLM Coding Plan) reader: GET quota/limit with the raw key, no Bearer (plan 4.4)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from cuota import secrets
from cuota.model import Kind, ProviderReading, Window
from cuota.readers import base
from cuota.readers.base import Opts, ReaderError

PROVIDER = "zai"
SOURCE = "api"
URL = "https://api.z.ai/api/monitor/usage/quota/limit"
KEY_NAME = "Z_AI_API_KEY"
DEFAULT_TIMEOUT = 10.0
_TOKENS_UNITS: dict[tuple[int, int], Kind] = {(3, 5): "5h", (6, 1): "weekly"}
_log = logging.getLogger("cuota.readers.zai")


def fetch(opts: Opts | None = None) -> dict[str, Any]:
    key = secrets.resolve(KEY_NAME, env=base.opt(opts, "env", None), zshrc=base.opt(opts, "zshrc", None))
    if not key:
        raise ReaderError("missing_credential", f"{KEY_NAME} not found in environment or ~/.zshrc")
    timeout = float(base.opt(opts, "timeout", DEFAULT_TIMEOUT))
    return base.http_get_json(str(base.opt(opts, "url", URL)), {"Authorization": key}, timeout)


def _classify(item: dict[str, Any]) -> Kind | None:
    kind = item.get("type")
    if kind == "TIME_LIMIT":
        return "mcp"
    if kind == "TOKENS_LIMIT":
        return _TOKENS_UNITS.get((item.get("unit"), item.get("number")))  # type: ignore[arg-type]
    return None


def _parse(raw: Any, now: datetime) -> ProviderReading:
    base.check_http_status(raw["status"])
    body = base.mapping(raw["body"], "body")
    # HTTP 200 is not success for z.ai: an invalid key also answers 200 with an error body.
    if body.get("success") is not True or body.get("code") != 200:
        code = body.get("code")
        if code in (401, 403):
            raise ReaderError("auth", "z.ai rejected the credential")
        raise ReaderError("http", f"z.ai reported failure (code {code if isinstance(code, int) else '?'})")
    data = base.mapping(body.get("data"), "data")
    limits = data.get("limits")
    if not isinstance(limits, list):
        raise base.shape_error("data.limits is not a list")
    windows: list[Window] = []
    for item in limits:
        item = base.mapping(item, "limit")
        kind = _classify(dict(item))
        if kind is None:
            _log.warning(
                "dropping unknown limit type=%s unit=%s number=%s",
                item.get("type"),
                item.get("unit"),
                item.get("number"),
            )
            continue
        used = base.number(item["percentage"], "percentage")
        reset = item.get("nextResetTime")
        resets_at = base.epoch_datetime(reset, "nextResetTime", millis=True) if reset is not None else None
        if kind == "5h" and resets_at is None:
            state = "idle"  # the 5h window only starts with the first use
        else:
            state = base.state_for(used)
        windows.append(Window(kind, None, used, resets_at, state))  # type: ignore[arg-type]
    if not windows:
        raise base.shape_error("no recognised limits")
    plan = data.get("level")
    return ProviderReading(
        PROVIDER, "ok", plan if isinstance(plan, str) else None, SOURCE, now, None, tuple(windows)
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
