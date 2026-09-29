"""Pure formatting of the state (plan section 8). No I/O, no network; stdlib and cuota.model only."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from cuota.model import ProviderReading, Window, parse_iso_z

STALE_STATE_MIN = 15
BAR_WIDTH = 20

PROVIDER_ORDER = ("claude", "codex", "kimi", "zai", "agy")
DISPLAY = {"claude": "Claude", "codex": "Codex", "kimi": "Kimi", "zai": "GLM", "agy": "agy (Gemini)"}
SHORT = {"claude": "Cl", "codex": "Cx", "kimi": "Ki", "zai": "GL", "agy": "ag"}
KIND_ORDER = {"5h": 0, "weekly": 1, "monthly": 2, "mcp": 3}
KIND_LABEL = {"5h": "5 h", "weekly": "Semanal", "monthly": "Mensual", "mcp": "MCP (mensual)"}
KIND_ABBR = {"5h": "5h", "weekly": "sem", "monthly": "mes"}
WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"
HEADERS = ("Proveedor", "Ventana", "", "Usado", "Restante", "Ritmo", "Reinicio", "Estado")


def paint(text: str, code: str, enabled: bool) -> str:
    return f"{code}{text}{RESET}" if enabled and text else text


def level_color(used: float, exhausted: bool = False) -> str:
    if exhausted or used > 80:
        return RED
    return YELLOW if used >= 50 else GREEN


def format_age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "<1 min"
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 24 * 60:
        return f"{minutes // 60} h {minutes % 60} min"
    return f"{minutes // (24 * 60)} d"


def _relative(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "en <1 min"
    if minutes < 60:
        return f"en {minutes} min"
    if minutes < 24 * 60:
        return f"en {minutes // 60} h {minutes % 60} min"
    days, rest = divmod(minutes, 24 * 60)
    return f"en {days} d {rest // 60} h"


def _local_day_time(dt: datetime, now: datetime, tz: tzinfo | None) -> str:
    local = dt.astimezone(tz)
    days = (local.date() - now.astimezone(tz).date()).days
    hhmm = local.strftime("%H:%M")
    if days == 0:
        return f"hoy {hhmm}"
    if days == 1:
        return f"mañana {hhmm}"
    return f"{WEEKDAYS[local.weekday()]} {local.day:02d}-{MONTHS[local.month - 1]} {hhmm}"


def format_ritmo(pace: Mapping[str, Any] | None, now: datetime, tz: tzinfo | None = None) -> tuple[str, str]:
    """(text, ansi color) for the Ritmo column. Missing or unusable pace -> em dash."""
    verdict = pace.get("verdict") if isinstance(pace, Mapping) else None
    ratio = pace.get("ratio") if isinstance(pace, Mapping) else None
    ratio_txt = f"{ratio:.1f}".replace(".", ",") + "× " if isinstance(ratio, int | float) else ""
    if verdict == "agotada":
        return "", RED
    if verdict == "se_agota":
        try:
            when = _local_day_time(parse_iso_z(pace["projected_exhaust_at"]), now, tz)  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            return "se agota", RED
        return f"se agota {when}", RED
    if verdict == "sobre_ritmo":
        return f"{ratio_txt}sobre ritmo".strip(), YELLOW
    if verdict == "bajo_ritmo":
        return "alcanza", GREEN
    return "—", DIM


def format_reset(resets_at: datetime | None, now: datetime, tz: tzinfo | None = None) -> str:
    """Local absolute + relative: 'hoy 23:07 · en 5 h 14 min'. None -> em dash."""
    if resets_at is None:
        return "—"
    absolute = _local_day_time(resets_at, now, tz)
    if resets_at <= now:
        return f"{absolute} · ya reinició"
    return f"{absolute} · {_relative(resets_at - now)}"


def bar(used: float, state: str) -> str:
    filled = BAR_WIDTH if state == "exhausted" else 0 if state == "idle" else round(used / 100 * BAR_WIDTH)
    filled = max(0, min(BAR_WIDTH, filled))
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _ordered(names: Any) -> list[str]:
    known = [n for n in PROVIDER_ORDER if n in names]
    return known + [n for n in names if n not in PROVIDER_ORDER]


def _sorted_windows(windows: tuple[Window, ...]) -> list[Window]:
    return sorted(windows, key=lambda w: KIND_ORDER.get(w.kind, 9))  # stable


def _label(name: str, plan: str | None) -> str:
    base = DISPLAY.get(name, name)
    if not plan:
        return base
    if base.endswith(")"):
        return f"{base[:-1]}, {plan})"
    return f"{base} ({plan})"


def _header(state: Mapping[str, Any], now: datetime, color: bool) -> list[str]:
    try:
        age = now - parse_iso_z(state["generated_at"])
    except (KeyError, TypeError, ValueError):
        return [paint("Cuotas · actualización desconocida", BOLD, color)]
    lines = [paint(f"Cuotas · actualizado hace {format_age(age)}", BOLD, color)]
    if age > timedelta(minutes=STALE_STATE_MIN):
        lines.append(
            paint(
                f"El estado tiene más de {STALE_STATE_MIN} min. ¿Está corriendo el colector? (cuota doctor)",
                YELLOW,
                color,
            )
        )
    return lines


def _window_cells(
    w: Window, now: datetime, tz: tzinfo | None, pace: Mapping[str, Any] | None = None
) -> tuple[list[str], bool]:
    idle = w.state == "idle"
    used = "—" if idle else f"{round(w.used_pct)}%"
    remaining = "—" if idle else f"{100 - round(w.used_pct)}%"
    estado = {"exhausted": "AGOTADA", "idle": "sin iniciar"}.get(w.state, "")
    cells = [
        KIND_LABEL.get(w.kind, w.kind) + (f" [{w.group}]" if w.group else ""),
        bar(w.used_pct, w.state),
        used,
        remaining,
        format_ritmo(pace, now, tz)[0],
        format_reset(w.resets_at, now, tz),
        estado,
    ]
    return cells, idle


def render_table(
    state: Mapping[str, Any], *, now: datetime | None = None, tz: tzinfo | None = None, color: bool = False
) -> str:
    now = now or datetime.now(UTC)
    out = _header(state, now, color)
    # rows: ("cells", provider, cells, style) | ("text", provider, text, style)
    rows: list[tuple[str, str, Any, str, str]] = []
    for name in _ordered(state["providers"]):
        raw = state["providers"][name]
        try:
            r = ProviderReading.from_dict(name, raw)
        except Exception:
            rows.append(("text", DISPLAY.get(name, name), "ERROR: datos ilegibles en el estado", RED, ""))
            continue
        label = _label(name, r.plan)
        labels = iter([label])

        def prov(labels: Any = labels) -> str:  # provider label only on its first row
            return next(labels, "")

        age = format_age(now - r.fetched_at)
        if r.status == "error":
            err = r.error
            assert err is not None  # noqa: S101 - guaranteed by ProviderReading
            rows.append(("text", prov(), f"ERROR: {err.code} — {err.message}", RED, ""))
        if r.note:
            rows.append(("text", prov(), r.note, DIM, ""))
        for w in _sorted_windows(r.windows):
            pace_all = raw.get("pace") if isinstance(raw.get("pace"), Mapping) else {}
            pace = pace_all.get(f"{w.kind}:{w.group}" if w.group else w.kind)
            cells, idle = _window_cells(w, now, tz, pace)
            ritmo_color = format_ritmo(pace, now, tz)[1]
            if r.status == "error":
                cells[6] = f"{cells[6]} (último valor bueno, hace {age})".strip()
                style = DIM
            elif idle:
                style = DIM
            else:
                if r.status == "stale":
                    cells[6] = f"{cells[6]} (dato de hace {age})".strip()
                style = level_color(w.used_pct, w.state == "exhausted")
            rows.append(("cells", prov(), cells, style, ritmo_color))

    widths = [len(h) for h in HEADERS]
    for kind, prov_label, payload, _style, _rc in rows:
        widths[0] = max(widths[0], len(prov_label))
        if kind == "cells":
            for i, cell in enumerate(payload):
                widths[i + 1] = max(widths[i + 1], len(cell))

    def fmt(cells: list[str]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    out.append(paint(fmt(list(HEADERS)), DIM, color))
    for kind, prov_label, payload, style, ritmo_color in rows:
        if kind == "text":
            line = f"{prov_label.ljust(widths[0])}  {payload}".rstrip()
            out.append(paint(line, style, color))
            continue
        cells = [prov_label, *payload]
        if color:
            # Color the numeric block; idle/carried rows are dimmed as a whole.
            padded = [c.ljust(widths[i]) for i, c in enumerate(cells)]
            if style == DIM:
                out.append(paint("  ".join(padded).rstrip(), DIM, True))
                continue
            for i in (2, 3, 4):
                padded[i] = paint(padded[i], style, True)
            padded[5] = paint(padded[5], ritmo_color, True)
            if cells[7].startswith("AGOTADA"):
                padded[7] = paint(padded[7], RED, True)
            out.append("  ".join(padded))
        else:
            out.append(fmt(cells))
    return "\n".join(out)


def _pace_verdict(p: Mapping[str, Any], w: Mapping[str, Any]) -> str | None:
    pace = p.get("pace")
    if not isinstance(pace, Mapping):
        return None
    entry = pace.get(f"{w['kind']}:{w['group']}" if w.get("group") else w["kind"])
    return entry.get("verdict") if isinstance(entry, Mapping) else None


def _segment(name: str, p: Mapping[str, Any]) -> tuple[str, str]:
    """(text, ansi color) for one provider."""
    code = SHORT.get(name, name[:2])
    status = p.get("status")
    best: Mapping[str, Any] | None = None
    for w in p.get("windows") or ():
        if w.get("state") not in ("active", "exhausted") or w.get("kind") == "mcp":
            continue
        key = (float(w["used_pct"]), w.get("state") == "exhausted")
        if best is None or key > (float(best["used_pct"]), best.get("state") == "exhausted"):
            best = w
    if best is None:
        return (f"{code} !" if status == "error" else f"{code} --"), (RED if status == "error" else DIM)
    abbr = KIND_ABBR.get(best["kind"], best["kind"])
    exhausted = best.get("state") == "exhausted"
    used = float(best["used_pct"])
    text = f"{code} AGOT {abbr}" if exhausted else f"{code} {round(used)}%{abbr}"
    if best.get("kind") and _pace_verdict(p, best) == "se_agota":
        text += "↑"
    if status == "error":
        text += "!"
    elif status == "stale":
        text += "~"
    return text, level_color(used, exhausted)


def render_line(state: Mapping[str, Any], *, color: bool = False) -> str:
    parts: list[str] = []
    for name in _ordered(state["providers"]):
        try:
            text, code = _segment(name, state["providers"][name])
        except Exception:
            text, code = f"{SHORT.get(name, name[:2])} ?", DIM
        parts.append(paint(text, code, color))
    return " · ".join(parts) if parts else "cuota: sin datos"
