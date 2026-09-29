"""CLI: commands, exit codes, color policy, import hygiene, doctor. Offline; CUOTA_HOME=tmp."""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from cuota import cli, store
from tests.test_render import base_state

FAKE_KEY = "FAKE-SECRET-0123456789"


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CUOTA_HOME", str(tmp_path / "cuota"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("CUOTA_CLAUDE_STATUSLINE_FILE", raising=False)
    return tmp_path


def write(state: dict[str, Any]) -> None:
    store.write_state(state)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# --- missing / corrupt state -------------------------------------------------------------------------


def test_missing_state(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys)
    assert code == 3 and "Sin datos todavía. Ejecute: cuota collect" in err + out
    code, out, _ = run(capsys, "--json")
    assert code == 3 and json.loads(out) == {"error": "sin datos"}
    code, out, _ = run(capsys, "--line")
    assert code == 0 and out.strip() == "cuota: sin datos"


def test_line_never_raises_on_corrupt_state(capsys: pytest.CaptureFixture[str]) -> None:
    store.base_dir()
    path = store.state_path()
    for junk in (
        "{not json",
        "[1]",
        '{"providers": {"claude": 5}}',
        '{"providers": {"claude": {"windows": [1]}}}',
    ):
        path.write_text(junk, encoding="utf-8")
        code, out, _ = run(capsys, "--line")
        assert code == 0 and out.strip()
    path.write_text(json.dumps({"providers": {"claude": {"status": "ok", "windows": [{"state": "active"}]}}}))
    code, out, _ = run(capsys, "--line")
    assert code == 0 and out.strip() == "Cl ?"


def test_line_falls_back_when_render_explodes(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    write(base_state())

    def boom(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("x")

    monkeypatch.setattr(cli.render, "render_line", boom)
    code, out, _ = run(capsys, "--line")
    assert code == 0 and out.strip() == "cuota: sin datos"


# --- views -------------------------------------------------------------------------------------------


def test_line_and_json_and_table(capsys: pytest.CaptureFixture[str]) -> None:
    state = base_state()
    write(state)
    code, out, _ = run(capsys, "--line")
    assert code == 0 and out.strip() == "Cl 56%5h · Cx AGOT sem · Ki 40%mes · GL 8%sem · ag 70%sem"
    code, out, _ = run(capsys, "--json")
    assert code == 0 and json.loads(out) == state and "\n  " in out
    code, out, _ = run(capsys, "--color", "never")
    assert code == 0 and "Claude (max)" in out and "\033" not in out


def test_watch_redraws_from_disk_without_collecting(capsys: pytest.CaptureFixture[str]) -> None:
    write(base_state())
    buf = io.StringIO()
    sleeps: list[float] = []
    assert cli.cmd_watch(2.0, False, out=buf, sleep=sleeps.append, iterations=3) == 0
    assert sleeps == [2.0, 2.0]
    assert buf.getvalue().count("\033[H\033[2J") == 3 and "Claude" in buf.getvalue()


def test_watch_ctrl_c_exits_zero_silently(capsys: pytest.CaptureFixture[str]) -> None:
    write(base_state())

    def interrupt(_s: float) -> None:
        raise KeyboardInterrupt

    assert cli.cmd_watch(1.0, False, sleep=interrupt) == 0
    assert capsys.readouterr().err == ""


def test_watch_without_state_shows_message() -> None:
    buf = io.StringIO()
    assert cli.cmd_watch(1.0, False, out=buf, sleep=lambda _s: None, iterations=1) == 0
    assert "Sin datos todavía" in buf.getvalue()


def test_collect_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    import cuota.collect as collect_mod

    seen: list[list[str]] = []
    monkeypatch.setattr(collect_mod, "main", lambda argv=None: seen.append(list(argv)) or 75)
    assert cli.main(["collect", "--provider", "codex"]) == 75
    assert seen == [["--provider", "codex"]]


# --- color policy ------------------------------------------------------------------------------------


def test_use_color_policy() -> None:
    assert cli.use_color("auto", isatty=True, env={}) is True
    assert cli.use_color("auto", isatty=False, env={}) is False
    assert cli.use_color("auto", isatty=True, env={"NO_COLOR": "1"}) is False
    assert cli.use_color("auto", isatty=True, env={"NO_COLOR": ""}) is True  # empty NO_COLOR is ignored
    assert cli.use_color("never", isatty=True, env={}) is False
    assert cli.use_color("always", isatty=False, env={}) is True


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_table_color_end_to_end(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    write(base_state())
    _, out, _ = run(capsys, "--color", "always")
    assert "\033[" in out
    _, out, _ = run(capsys, "--color", "never")
    assert "\033[" not in out
    _, out, _ = run(capsys)  # captured stdout is not a TTY
    assert "\033[" not in out
    tty = FakeTTY()
    monkeypatch.setattr(sys, "stdout", tty)
    cli.main([])
    assert "\033[" in tty.getvalue()  # auto + TTY + no NO_COLOR
    monkeypatch.setenv("NO_COLOR", "1")
    tty2 = FakeTTY()
    monkeypatch.setattr(sys, "stdout", tty2)
    cli.main([])
    assert "\033[" not in tty2.getvalue()  # NO_COLOR wins over a TTY


def test_line_color_only_when_asked(capsys: pytest.CaptureFixture[str]) -> None:
    write(base_state())
    assert "\033[" not in run(capsys, "--line")[1]
    assert "\033[" in run(capsys, "--line", "--color", "always")[1]


# --- import hygiene ----------------------------------------------------------------------------------

HYGIENE = """
import sys
from cuota import cli
code = cli.main(["--line"])
heavy = ("cuota.readers", "cuota.collect", "urllib.request", "concurrent.futures", "subprocess", "threading")
bad = [m for m in heavy if m in sys.modules]
print("BAD=" + ",".join(bad))
sys.exit(code)
"""


def test_line_path_imports_nothing_heavy(env: Path) -> None:
    write(base_state())
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", HYGIENE],
        capture_output=True,
        text=True,
        env={**os.environ, "CUOTA_HOME": str(env / "cuota"), "PYTHONPATH": os.pathsep.join(sys.path)},
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[0].startswith("Cl 56%5h")
    assert proc.stdout.splitlines()[-1] == "BAD=", proc.stdout


def test_line_is_fast(env: Path) -> None:
    write(base_state())
    start = time.perf_counter()
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import sys; from cuota import cli; sys.exit(cli.main(['--line']))"],
        capture_output=True,
        text=True,
        env={**os.environ, "CUOTA_HOME": str(env / "cuota"), "PYTHONPATH": os.pathsep.join(sys.path)},
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0
    assert (
        time.perf_counter() - start < 0.5
    )  # generous CI bound; the real target (<100 ms) is measured by hand


# --- doctor ------------------------------------------------------------------------------------------


@pytest.fixture
def healthy(env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = env / "home"
    (home / ".claude" / "daemon").mkdir(parents=True)
    (home / ".claude" / "daemon" / ".statusline-last.json").write_text("{}")
    bindir = env / "bin"
    bindir.mkdir()
    for name in ("codex", "agy"):
        exe = bindir / name
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("KIMI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("Z_AI_API_KEY", FAKE_KEY + "-2")
    state = base_state()
    from datetime import UTC, datetime

    from cuota.model import iso_z

    state["generated_at"] = iso_z(datetime.now(UTC))
    write(state)
    return home


def test_doctor_all_ok(healthy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "doctor")
    assert code == 0, out
    assert "Revisar" not in out
    for token in (
        "estado",
        "proveedor claude",
        "credencial KIMI_API_KEY",
        "binario codex",
        "fuente Claude",
        "permisos",
        "candado",
    ):
        assert token in out
    assert "no instalado" in out
    assert FAKE_KEY not in out
    assert all(ln.startswith("OK") for ln in out.strip().splitlines())


def test_doctor_missing_credential_no_secret_leak(
    healthy: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("Z_AI_API_KEY")
    code, out, _ = run(capsys, "doctor")
    assert code == 1
    line = next(ln for ln in out.splitlines() if "Z_AI_API_KEY" in ln)
    assert line.startswith("Revisar") and line.rstrip().endswith("no")
    assert FAKE_KEY not in out


def test_doctor_bad_permissions(healthy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    os.chmod(store.state_path(), 0o644)
    code, out, _ = run(capsys, "doctor")
    assert code == 1
    line = next(ln for ln in out.splitlines() if "permisos" in ln)
    assert line.startswith("Revisar") and "state.json 0644" in line
    assert FAKE_KEY not in out
    assert stat.S_IMODE(store.base_dir().stat().st_mode) == 0o700


def test_doctor_missing_binary_source_state_and_error_provider(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(env))  # no codex/agy
    s = base_state()
    s["providers"]["kimi"].update(status="error", error={"code": "auth", "message": "x"}, windows=[])
    write(s)
    code, out, _ = run(capsys, "doctor")
    assert code == 1
    assert next(ln for ln in out.splitlines() if "binario codex" in ln).startswith("Revisar")
    assert "no encontrado" in out
    assert next(ln for ln in out.splitlines() if "fuente Claude" in ln).startswith("Revisar")
    assert next(ln for ln in out.splitlines() if "proveedor kimi" in ln).startswith("Revisar")
    assert "error=auth" in out


def test_doctor_without_state(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "doctor")
    assert code == 1
    assert next(ln for ln in out.splitlines() if " estado " in ln).startswith("Revisar")


def test_doctor_reports_held_lock_and_installed_agent(
    healthy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agents = healthy / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.fan.cuota.plist").write_text("x")
    envdir = healthy / ".config" / "cuota"
    envdir.mkdir(parents=True)
    (envdir / "env").write_text(f"PATH={healthy.parent / 'bin'}\n")
    (envdir / "env").chmod(0o600)
    with store.collect_lock():
        code, out, _ = run(capsys, "doctor")
    assert code == 0, out
    assert "env del LaunchAgent" in out
    assert "tomado ahora por un colector" in out and "instalado" in out and "no instalado" not in out


def test_line_and_table_show_pace(capsys: pytest.CaptureFixture[str]) -> None:
    state = base_state()
    state["providers"]["claude"]["pace"] = {
        "5h": {
            "expected_pct": 50.0,
            "ratio": 1.1,
            "verdict": "se_agota",
            "projected_exhaust_at": "2026-09-29T20:00:00Z",
        }
    }
    write(state)
    code, out, _ = run(capsys, "--line")
    assert code == 0 and out.startswith("Cl 56%5h↑ · Cx AGOT sem")
    code, out, _ = run(capsys, "--color", "never")
    assert code == 0 and "Ritmo" in out and "se agota" in out
    code, out, _ = run(capsys, "--json")
    assert json.loads(out)["providers"]["claude"]["pace"]["5h"]["verdict"] == "se_agota"


def test_doctor_flags_installed_agent_without_env(healthy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    agents = healthy / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.fan.cuota.plist").write_text("x")
    code, out, _ = run(capsys, "doctor")
    assert code == 1
    assert next(ln for ln in out.splitlines() if "env del LaunchAgent" in ln).startswith("Revisar")


def test_doctor_binary_version_failure(healthy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exe = healthy.parent / "bin" / "codex"
    exe.write_text("#!/bin/sh\necho 'spawn ENOENT' >&2\nexit 1\n")
    code, out, _ = run(capsys, "doctor")
    assert code == 1
    line = next(ln for ln in out.splitlines() if "binario codex" in ln)
    assert line.startswith("Revisar") and "ENOENT" in line


def test_doctor_binary_uses_env_file_path(healthy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = healthy.parent / "good"
    good.mkdir()
    exe = good / "codex"
    exe.write_text("#!/bin/sh\necho codex-cli 9.9\n")
    exe.chmod(0o755)
    envdir = healthy / ".config" / "cuota"
    envdir.mkdir(parents=True)
    (envdir / "env").write_text(f"PATH={good}:{healthy.parent / 'bin'}\n")
    code, out, _ = run(capsys, "doctor")
    assert code == 0, out
    assert "codex-cli 9.9" in out


def test_doctor_claude_source_default_and_env_and_missing_hint(
    healthy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # legacy file only (from the fixture): still accepted as fallback
    _, out, _ = run(capsys, "doctor")
    assert ".claude/daemon/.statusline-last.json" in out
    # the default cache file wins once it exists
    default = healthy / ".cache" / "cuota" / "claude-statusline.json"
    default.parent.mkdir(parents=True)
    default.write_text("{}")
    _, out, _ = run(capsys, "doctor")
    assert "claude-statusline.json" in out
    # explicit env override that does not exist -> failure with a hint
    monkeypatch.setenv("CUOTA_CLAUDE_STATUSLINE_FILE", str(healthy / "nope.json"))
    code, out, _ = run(capsys, "doctor")
    assert code != 0
    assert "claude-statusline-tee.sh" in out and "CUOTA_CLAUDE_STATUSLINE_FILE" in out
