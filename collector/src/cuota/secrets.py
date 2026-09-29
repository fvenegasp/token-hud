"""Credential resolution: environment first, then the `export NAME=...` line of ~/.zshrc.

Values are never logged or printed by this module.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

_VAR = re.compile(r"^\$(?:\{(?P<a>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<b>[A-Za-z_][A-Za-z0-9_]*))$")


def _clean(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end != -1 else raw[1:]
    return raw.split(" #", 1)[0].strip()


def _from_zshrc(name: str, zshrc: Path) -> str | None:
    try:
        text = zshrc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    pattern = re.compile(rf"^\s*export\s+{re.escape(name)}=(.*)$")
    found: str | None = None
    for line in text.splitlines():
        m = pattern.match(line)
        if m:
            found = _clean(m.group(1))  # last definition wins, like the shell
    return found or None


def resolve(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    zshrc: Path | str | None = None,
) -> str | None:
    """Return the value of NAME or None. Supports one level of `$OTHER` indirection in ~/.zshrc."""
    environ = os.environ if env is None else env
    value = environ.get(name)
    if value:
        return value
    path = Path(zshrc) if zshrc is not None else Path.home() / ".zshrc"
    value = _from_zshrc(name, path)
    if value is None:
        return None
    m = _VAR.match(value)
    if m:
        other = m.group("a") or m.group("b")
        return environ.get(other) or _from_zshrc(other, path) or None
    return value
