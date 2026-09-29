"""Pure formatting: table, Reinicio, ages, line. Fixed clock and timezone for deterministic output."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from cuota import render

TZ = timezone(timedelta(hours=-3))  # America/Santiago in spring, fixed for determinism
NOW = datetime(2026, 9, 29, 17, 0, 0, tzinfo=UTC)  # 14:00 local


def win(
    kind: str, used: float, resets: str | None, state: str = "active", group: str | None = None
) -> dict[str, Any]:
    return {"kind": kind, "group": group, "used_pct": used, "resets_at": resets, "state": state}


def prov(
    status: str = "ok", plan: str | None = None, windows: list[dict[str, Any]] | None = None, **kw: Any
) -> dict[str, Any]:
    return {
        "status": status,
        "plan": plan,
        "source": kw.pop("source", "test"),
        "fetched_at": kw.pop("fetched_at", "2026-09-29T17:00:00Z"),
        "error": kw.pop("error", None),
        "windows": windows or [],
        "note": kw.pop("note", None),
    }


def base_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": "2026-09-29T17:00:00Z",
        "providers": {
            "claude": prov(
                plan="max",
                windows=[
                    win("5h", 56.0, "2026-09-29T22:14:00Z"),
                    win("weekly", 25.0, "2026-10-03T20:23:00Z"),
                ],
            ),
            "codex": prov(
                plan="plus",
                windows=[
                    win("5h", 0.0, "2026-09-29T21:54:49Z"),
                    win("weekly", 100.0, "2026-10-03T20:23:01Z", "exhausted"),
                ],
            ),
            "kimi": prov(
                windows=[
                    win("5h", 12.0, "2026-09-30T12:15:00Z"),
                    win("monthly", 40.0, "2026-10-01T03:10:00Z"),
                ]
            ),
            "zai": prov(
                plan="lite",
                windows=[
                    win("mcp", 3.0, "2026-10-20T00:00:00Z"),
                    win("weekly", 8.0, "2026-10-02T00:00:00Z"),
                    win("5h", 0.0, None, "idle"),
                ],
            ),
            "agy": prov(
                windows=[win("5h", 30.0, "2026-09-29T21:00:00Z"), win("weekly", 70.0, "2026-10-05T00:00:00Z")]
            ),
        },
    }


def table(state: dict[str, Any], color: bool = False) -> str:
    return render.render_table(state, now=NOW, tz=TZ, color=color)


def rows_of(text: str, needle: str) -> list[str]:
    return [ln for ln in text.splitlines() if needle in ln]


# --- ages / Reinicio ---------------------------------------------------------------------------------


def test_format_age() -> None:
    f = render.format_age
    assert f(timedelta(seconds=30)) == "<1 min"
    assert f(timedelta(minutes=7, seconds=59)) == "7 min"
    assert f(timedelta(hours=2, minutes=5)) == "2 h 5 min"
    assert f(timedelta(days=3, hours=5)) == "3 d"
    assert f(timedelta(seconds=-5)) == "<1 min"


def test_reset_today_tomorrow_other_day_none() -> None:
    def fr(iso: str) -> str:
        return render.format_reset(datetime.fromisoformat(iso), NOW, TZ)

    assert fr("2026-09-29T22:14:00+00:00") == "hoy 19:14 · en 5 h 14 min"
    assert fr("2026-09-30T12:15:00+00:00") == "mañana 09:15 · en 19 h 15 min"
    assert fr("2026-10-03T20:23:00+00:00") == "sáb 03-oct 17:23 · en 4 d 3 h"
    assert render.format_reset(None, NOW, TZ) == "—"


def test_reset_crossing_midnight_uses_local_date() -> None:
    # 02:30Z is 23:30 local of the SAME local day as NOW (14:00 local); 03:30Z is 00:30 local of the next day.
    assert render.format_reset(datetime(2026, 9, 30, 2, 30, tzinfo=UTC), NOW, TZ).startswith(
        "hoy 23:30 · en 9 h 30 min"
    )
    assert render.format_reset(datetime(2026, 9, 30, 3, 30, tzinfo=UTC), NOW, TZ).startswith(
        "mañana 00:30 · en 10 h 30 min"
    )


def test_reset_minutes_only_and_past() -> None:
    assert render.format_reset(NOW + timedelta(minutes=12), NOW, TZ) == "hoy 14:12 · en 12 min"
    assert render.format_reset(NOW - timedelta(minutes=1), NOW, TZ).endswith("ya reinició")


# --- table -------------------------------------------------------------------------------------------


def test_table_full_render() -> None:
    out = table(base_state())
    lines = out.splitlines()
    assert lines[0] == "Cuotas · actualizado hace <1 min"
    assert "Proveedor" in lines[1] and "Reinicio" in lines[1] and "Estado" in lines[1]
    for expected in ("Claude (max)", "Codex (plus)", "Kimi", "GLM (lite)", "agy (Gemini)"):
        assert expected in out
    # provider order
    order = [out.index(n) for n in ("Claude", "Codex", "Kimi", "GLM", "agy")]
    assert order == sorted(order)
    # window order for zai: 5h, weekly, mcp regardless of input order
    zai = out[out.index("GLM") :]
    assert zai.index("5 h") < zai.index("Semanal") < zai.index("MCP (mensual)")
    claude5h = rows_of(out, "Claude (max)")[0]
    assert "5 h" in claude5h and "56%" in claude5h and "44%" in claude5h
    assert "█" * 11 + "░" * 9 in claude5h  # round(56/100*20) = 11
    assert "hoy 19:14 · en 5 h 14 min" in claude5h
    assert "sáb 03-oct 17:23 · en 4 d 3 h" in out
    assert "mañana 09:15 · en 19 h 15 min" in out
    assert "MCP (mensual)" in out and "Mensual" in out
    assert "\033" not in out


def test_table_exhausted_and_idle() -> None:
    out = table(base_state())
    exhausted = rows_of(out, "Semanal")[1]  # codex weekly
    assert "100%" in exhausted and "0%" in exhausted and exhausted.rstrip().endswith("AGOTADA")
    assert "█" * 20 in exhausted
    idle = next(ln for ln in out.splitlines() if "sin iniciar" in ln)
    assert "░" * 20 in idle and "█" not in idle
    assert "—" in idle  # used and reset are dashes
    assert "0%" not in idle


def test_table_stale_suffix() -> None:
    s = base_state()
    s["providers"]["kimi"]["status"] = "stale"
    s["providers"]["kimi"]["fetched_at"] = "2026-09-29T16:40:00Z"
    out = table(s)
    kimi_rows = [ln for ln in out.splitlines() if "(dato de hace 20 min)" in ln]
    assert len(kimi_rows) == 2
    assert "(dato de hace" not in "\n".join(rows_of(out, "Claude"))


def test_table_error_with_carried_windows_dimmed() -> None:
    s = base_state()
    s["providers"]["codex"] = prov(
        "error",
        "plus",
        [win("5h", 20.0, "2026-09-29T21:54:00Z")],
        fetched_at="2026-09-29T15:50:00Z",
        error={"code": "timeout", "message": "sin respuesta"},
        note="mostrando último valor bueno",
    )
    out = table(s)
    assert "ERROR: timeout — sin respuesta" in out
    assert "(último valor bueno, hace 1 h 10 min)" in out
    assert "mostrando último valor bueno" in out
    colored = table(s, color=True)
    carried = next(ln for ln in colored.splitlines() if "último valor bueno, hace" in ln)
    assert carried.startswith(render.DIM)  # dimmed as a whole
    err = next(ln for ln in colored.splitlines() if "ERROR: timeout" in ln)
    assert render.RED in err


def test_table_error_without_windows_never_zero() -> None:
    s = base_state()
    s["providers"] = {
        "kimi": prov("error", None, [], error={"code": "auth", "message": "clave inválida"}, source="unknown")
    }
    out = table(s)
    assert "ERROR: auth — clave inválida" in out
    assert "%" not in out
    assert "░" not in out and "█" not in out
    assert "Semanal" not in out and "5 h" not in out


def test_table_note_dimmed_under_provider() -> None:
    s = base_state()
    s["providers"]["codex"]["note"] = "respaldo por rollout"
    out = table(s)
    assert "respaldo por rollout" in out
    colored = table(s, color=True)
    note_line = next(ln for ln in colored.splitlines() if "respaldo por rollout" in ln)
    assert note_line.startswith(render.DIM)


def test_header_age_and_old_state_warning() -> None:
    s = base_state()
    s["generated_at"] = "2026-09-29T16:45:00Z"
    out = table(s)
    assert out.splitlines()[0] == "Cuotas · actualizado hace 15 min"
    assert "más de 15 min" not in out  # exactly 15 min is not older than 15
    s["generated_at"] = "2026-09-29T16:44:00Z"
    out = table(s)
    assert "actualizado hace 16 min" in out
    assert "El estado tiene más de 15 min. ¿Está corriendo el colector? (cuota doctor)" in out


def test_table_colors_by_threshold() -> None:
    s = base_state()
    out = table(s, color=True)
    green = next(ln for ln in out.splitlines() if "Kimi" in ln)  # 12%
    amber = next(ln for ln in out.splitlines() if "Claude" in ln)  # 56%
    red = next(ln for ln in out.splitlines() if "AGOTADA" in ln)
    assert render.GREEN in green and render.YELLOW in amber and render.RED in red
    s["providers"]["claude"]["windows"][0]["used_pct"] = 81.0
    assert render.RED in next(ln for ln in table(s, color=True).splitlines() if "Claude" in ln)
    idle = next(ln for ln in out.splitlines() if "sin iniciar" in ln)
    assert idle.startswith(render.DIM)


def test_unreadable_provider_does_not_break_table() -> None:
    s = base_state()
    s["providers"]["kimi"] = {"status": "ok"}
    out = table(s)
    assert "ERROR: datos ilegibles" in out and "Claude" in out


# --- line --------------------------------------------------------------------------------------------


def test_line_real_shape() -> None:
    assert render.render_line(base_state()) == "Cl 56%5h · Cx AGOT sem · Ki 40%mes · GL 8%sem · ag 70%sem"


def test_line_picks_most_critical_not_first() -> None:
    s = base_state()
    s["providers"] = {"claude": prov(windows=[win("5h", 10.0, None), win("weekly", 90.0, None)])}
    assert render.render_line(s) == "Cl 90%sem"


def test_line_error_stale_variants() -> None:
    s = base_state()
    s["providers"]["codex"] = prov("error", None, [], error={"code": "auth", "message": "x"})
    s["providers"]["kimi"] = prov(
        "error", None, [win("5h", 60.0, None)], error={"code": "http", "message": "x"}
    )
    s["providers"]["zai"]["status"] = "stale"
    s["providers"]["agy"]["status"] = "stale"
    assert render.render_line(s) == "Cl 56%5h · Cx ! · Ki 60%5h! · GL 8%sem~ · ag 70%sem~"


def test_line_exhausted_with_error_and_idle_only() -> None:
    s = base_state()
    s["providers"] = {
        "codex": prov(
            "error", "plus", [win("weekly", 100.0, None, "exhausted")], error={"code": "http", "message": "x"}
        ),
        "zai": prov(windows=[win("5h", 0.0, None, "idle"), win("mcp", 90.0, None)]),
    }
    assert render.render_line(s) == "Cx AGOT sem! · GL --"


def test_line_ignores_mcp_and_colors_only_when_enabled() -> None:
    s = base_state()
    plain = render.render_line(s)
    assert "\033" not in plain and "mcp" not in plain
    colored = render.render_line(s, color=True)
    assert render.YELLOW + "Cl 56%5h" in colored
    assert render.RED + "Cx AGOT sem" in colored
    assert render.GREEN + "GL 8%sem" in colored


def test_state_isolation() -> None:
    s = base_state()
    snapshot = copy.deepcopy(s)
    table(s)
    render.render_line(s)
    assert s == snapshot


# --- Ritmo (F4) ---------------------------------------------------------------------------------------


def paced(verdict: str, ratio: float | None = None, exhaust: str | None = None) -> dict[str, Any]:
    return {"5h": {"expected_pct": 50.0, "ratio": ratio, "projected_exhaust_at": exhaust, "verdict": verdict}}


def ritmo_state(pace: dict[str, Any] | None, state: str = "active") -> dict[str, Any]:
    p = prov(windows=[win("5h", 60.0, "2026-09-29T22:14:00Z", state)])
    if pace is not None:
        p["pace"] = pace
    return {"schema_version": 1, "generated_at": "2026-09-29T17:00:00Z", "providers": {"claude": p}}


def test_ritmo_header_after_restante() -> None:
    head = table(ritmo_state(None)).splitlines()[1]
    assert head.index("Restante") < head.index("Ritmo") < head.index("Reinicio")


def test_ritmo_texts_per_verdict() -> None:
    soon = paced("se_agota", 1.5, "2026-09-29T20:30:00Z")["5h"]
    assert render.format_ritmo(soon, NOW, TZ)[0] == "se agota hoy 17:30"
    assert "se agota mañana 09:00" in table(ritmo_state(paced("se_agota", 1.5, "2026-09-30T12:00:00Z")))
    assert "1,3× sobre ritmo" in table(ritmo_state(paced("sobre_ritmo", 1.3)))
    assert "0,4×" not in table(ritmo_state(paced("bajo_ritmo", 0.4)))
    assert "alcanza" in table(ritmo_state(paced("bajo_ritmo", 0.4)))
    assert render.format_ritmo(paced("agotada")["5h"], NOW, TZ)[0] == ""
    for pace in (paced("sin_datos"), None, {}):
        row = rows_of(table(ritmo_state(pace)), "Claude")[0]
        assert "—" in row
    ex = table(ritmo_state(paced("agotada"), "exhausted"))
    assert "AGOTADA" in ex and "ritmo" not in ex and "alcanza" not in ex


def test_ritmo_colors() -> None:
    assert render.format_ritmo(paced("se_agota", 1.0, "2026-09-29T20:30:00Z")["5h"], NOW, TZ)[1] == render.RED
    assert render.format_ritmo(paced("sobre_ritmo", 1.3)["5h"], NOW, TZ)[1] == render.YELLOW
    assert render.format_ritmo(paced("bajo_ritmo", 0.4)["5h"], NOW, TZ)[1] == render.GREEN
    out = table(ritmo_state(paced("bajo_ritmo", 0.4)), color=True)
    assert f"{render.GREEN}alcanza" in out


def test_line_arrow_only_for_se_agota() -> None:
    assert render.render_line(ritmo_state(paced("se_agota", 1.5, "2026-09-29T20:30:00Z"))) == "Cl 60%5h↑"
    for v in ("sobre_ritmo", "bajo_ritmo", "sin_datos", "agotada"):
        assert "↑" not in render.render_line(ritmo_state(paced(v, 1.0)))
    assert "↑" not in render.render_line(ritmo_state(None))


def test_line_arrow_follows_most_critical_window() -> None:
    p = prov(windows=[win("5h", 10.0, "2026-09-29T22:14:00Z"), win("weekly", 90.0, "2026-10-03T20:23:00Z")])
    p["pace"] = {
        "5h": {"verdict": "se_agota", "ratio": 1, "projected_exhaust_at": None, "expected_pct": 1},
        "weekly": {"verdict": "bajo_ritmo", "ratio": 1, "projected_exhaust_at": None, "expected_pct": 1},
    }
    s = {"providers": {"claude": p}}
    assert render.render_line(s) == "Cl 90%sem"


def test_bajo_ritmo_never_shows_multiplier() -> None:
    for ratio in (1.6, 0.0, 0.4, None):
        assert render.format_ritmo(paced("bajo_ritmo", ratio)["5h"], NOW, TZ) == ("alcanza", render.GREEN)
