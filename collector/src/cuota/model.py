"""Contract types (plan section 6). Pure data, no I/O."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, get_args

Kind = Literal["5h", "weekly", "monthly", "mcp"]
State = Literal["active", "exhausted", "idle"]
Status = Literal["ok", "stale", "error"]
ErrorCode = Literal["auth", "http", "timeout", "shape", "missing_source", "missing_credential", "unavailable"]

KINDS: tuple[str, ...] = get_args(Kind)
STATES: tuple[str, ...] = get_args(State)
STATUSES: tuple[str, ...] = get_args(Status)
ERROR_CODES: tuple[str, ...] = get_args(ErrorCode)


def iso_z(dt: datetime) -> str:
    """ISO-8601 UTC with a trailing Z (whole seconds)."""
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_z(value: str) -> datetime:
    """Inverse of iso_z (also accepts any offset-aware ISO-8601)."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return dt


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")


@dataclass(frozen=True)
class Error:
    code: ErrorCode
    message: str

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise ValueError(f"invalid error code: {self.code!r}")
        if not isinstance(self.message, str):
            raise ValueError("error message must be a string")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class Window:
    kind: Kind
    group: str | None
    used_pct: float
    resets_at: datetime | None
    state: State

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"invalid window kind: {self.kind!r}")
        if self.state not in STATES:
            raise ValueError(f"invalid window state: {self.state!r}")
        if self.group is not None and not isinstance(self.group, str):
            raise ValueError("group must be a string or None")
        used = self.used_pct
        if isinstance(used, bool) or not isinstance(used, int | float) or math.isnan(used):
            raise ValueError("used_pct must be a number")
        if not 0 <= used <= 100:
            raise ValueError(f"used_pct out of range 0..100: {used}")
        object.__setattr__(self, "used_pct", float(used))
        if self.resets_at is not None:
            _require_aware(self.resets_at, "resets_at")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Window:
        """Inverse of to_dict (additive helper, used to read state.json back)."""
        resets = d["resets_at"]
        return cls(d["kind"], d["group"], d["used_pct"], parse_iso_z(resets) if resets else None, d["state"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "group": self.group,
            "used_pct": self.used_pct,
            "resets_at": iso_z(self.resets_at) if self.resets_at else None,
            "state": self.state,
        }


@dataclass(frozen=True)
class ProviderReading:
    provider: str
    status: Status
    plan: str | None
    source: str
    fetched_at: datetime
    error: Error | None = None
    windows: tuple[Window, ...] = field(default_factory=tuple)
    note: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"invalid status: {self.status!r}")
        _require_aware(self.fetched_at, "fetched_at")
        object.__setattr__(self, "windows", tuple(self.windows))
        if self.note is not None and not isinstance(self.note, str):
            raise ValueError("note must be a string or None")
        if (self.status == "error") != (self.error is not None):
            raise ValueError("error must be set if and only if status == 'error'")
        if self.status != "error" and not self.windows:
            raise ValueError("a non-error reading needs at least one window")

    @classmethod
    def from_dict(cls, provider: str, d: dict[str, Any]) -> ProviderReading:
        """Inverse of to_dict (additive helper, used to read state.json back). Raises on bad data."""
        err = d.get("error")
        return cls(
            provider,
            d["status"],
            d.get("plan"),
            d["source"],
            parse_iso_z(d["fetched_at"]),
            Error(err["code"], err["message"]) if err else None,
            tuple(Window.from_dict(w) for w in d.get("windows") or ()),
            d.get("note"),
        )

    def to_dict(self) -> dict[str, Any]:
        """The per-provider entry of state.json (the provider name is the key one level up)."""
        return {
            "status": self.status,
            "plan": self.plan,
            "source": self.source,
            "fetched_at": iso_z(self.fetched_at),
            "error": self.error.to_dict() if self.error else None,
            "windows": [w.to_dict() for w in self.windows],
            "note": self.note,
        }
