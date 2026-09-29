"""Shared helpers: loader for the real fixtures ({"_meta": ..., "body": ...})."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(provider: str, name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / provider / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def fx() -> Callable[[str, str], dict[str, Any]]:
    """fx("kimi", "usages_ok") -> {"_meta": ..., "body": ...} (fresh deep copy each call)."""
    return lambda provider, name: copy.deepcopy(_load(provider, name))


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # noqa: DTZ001


def by_kind(reading: Any) -> dict[str, Any]:
    return {w.kind: w for w in reading.windows}
