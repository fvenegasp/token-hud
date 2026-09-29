"""`cuota` command line (plan section 8): table, --json, --line, watch, collect, doctor.

Views only read state.json via store.load_state(); they never touch the network. Anything that needs
readers, collect, subprocess or threads is imported lazily inside the command that uses it, so that
`cuota --line` stays fast.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import IO

from cuota import render, store

EXIT_NO_DATA = 3
NO_DATA_MSG = "Sin datos todavía. Ejecute: cuota collect"
LINE_FALLBACK = "cuota: sin datos"
LAUNCH_AGENT = Path("~/Library/LaunchAgents/com.fan.cuota.plist")
ENV_FILE = Path("~/.config/cuota/env")
CREDENTIALS = ("KIMI_API_KEY", "Z_AI_API_KEY")
BINARIES = ("codex", "agy")


def use_color(mode: str, *, isatty: bool, env: Mapping[str, str]) -> bool:
    """always/never are explicit; auto = TTY and NO_COLOR unset or empty."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    if env.get("NO_COLOR"):
        return False
    return isatty


# --- views -----------------------------------------------------------------------------------------


def cmd_table(color: bool, *, now: datetime | None = None, tz: tzinfo | None = None) -> int:
    state = store.load_state()
    if state is None:
        print(NO_DATA_MSG, file=sys.stderr)
        return EXIT_NO_DATA
    print(render.render_table(state, now=now, tz=tz, color=color))
    return 0


def cmd_json() -> int:
    state = store.load_state()
    if state is None:
        print('{"error":"sin datos"}')
        return EXIT_NO_DATA
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


def cmd_line(color: bool) -> int:
    try:
        state = store.load_state()
        text = render.render_line(state, color=color) if state else LINE_FALLBACK
    except Exception:
        text = LINE_FALLBACK
    print(text)
    return 0


def cmd_watch(
    interval: float,
    color: bool,
    *,
    out: IO[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    iterations: int | None = None,
) -> int:
    """Redraw from disk every `interval` seconds. Never collects."""
    out = out or sys.stdout
    count = 0
    try:
        while iterations is None or count < iterations:
            state = store.load_state()
            body = render.render_table(state, color=color) if state else NO_DATA_MSG
            out.write("\033[H\033[2J" + body + "\n")
            out.flush()
            count += 1
            if iterations is not None and count >= iterations:
                break
            sleep(interval)
    except KeyboardInterrupt:
        pass
    return 0


# --- doctor ----------------------------------------------------------------------------------------


def _check(ok: bool, name: str, detail: str) -> tuple[bool, str]:
    return ok, f"{'OK' if ok else 'Revisar':<8}  {name:<22}  {detail}"


def _age(path: Path, now: datetime) -> str:
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return f"hace {render.format_age(now - mtime)}"


def _doctor_path() -> str:
    """PATH from the LaunchAgent env file when present (what launchd uses), else the current PATH."""
    env_file = ENV_FILE.expanduser()
    try:
        for line in env_file.read_text().splitlines():
            if line.startswith("PATH="):
                return line[5:].strip()
    except OSError:
        pass
    return os.environ.get("PATH", "")


def _run_version(binary: str, path: str) -> tuple[bool, str]:
    """Run `<binary> --version` locally (10 s timeout). Returns (ok, first output line or error)."""
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PATH": path},
        )
    except subprocess.TimeoutExpired:
        return False, "--version excedió 10 s"
    except OSError as exc:
        return False, f"--version falló: {exc.strerror or exc}"
    lines = (proc.stdout or proc.stderr).strip().splitlines()
    first = lines[0][:80] if lines else ""
    if proc.returncode != 0:
        return False, f"--version salió con {proc.returncode}: {first}"
    return True, first


def run_doctor(*, now: datetime | None = None) -> tuple[list[str], bool]:
    """Offline diagnosis. Returns (lines, all_ok). Never prints secret values."""
    import fcntl
    import shutil

    from cuota import secrets
    from cuota.model import parse_iso_z

    now = now or datetime.now(UTC)
    checks: list[tuple[bool, str]] = []

    state = store.load_state()
    path = store.state_path()
    if state is None:
        checks.append(_check(False, "estado", f"{path} no existe o es ilegible"))
    else:
        version = state.get("schema_version")
        try:
            age = f"hace {render.format_age(now - parse_iso_z(state['generated_at']))}"
        except (KeyError, TypeError, ValueError):
            age = "edad desconocida"
        checks.append(_check(version == store.SCHEMA_VERSION, "estado", f"{age}, schema_version={version}"))
        for name in render._ordered(state["providers"]):
            p = state["providers"][name]
            try:
                fetched = f"hace {render.format_age(now - parse_iso_z(p['fetched_at']))}"
            except (KeyError, TypeError, ValueError):
                fetched = "edad desconocida"
            err = (p.get("error") or {}).get("code")
            detail = f"status={p.get('status')} fuente={p.get('source')} lectura {fetched}"
            if err:
                detail += f" error={err}"
            checks.append(_check(p.get("status") != "error", f"proveedor {name}", detail))

    for var in CREDENTIALS:
        found = secrets.resolve(var) is not None
        checks.append(_check(found, f"credencial {var}", "sí" if found else "no"))

    search_path = _doctor_path()
    for binary in BINARIES:
        where = shutil.which(binary, path=search_path)
        if where is None:
            checks.append(_check(False, f"binario {binary}", "no encontrado"))
            continue
        ok, detail = _run_version(where, search_path)
        checks.append(_check(ok, f"binario {binary}", f"{where} {detail}"))

    from cuota.readers import claude as claude_reader  # lazy: keeps the --line path light

    source = claude_reader.source_path()
    if source.exists():
        checks.append(_check(True, "fuente Claude", f"{source} {_age(source, now)}"))
    else:
        checks.append(
            _check(
                False,
                "fuente Claude",
                f"{source} no existe (la escribe el statusLine de Claude Code: ver "
                f"extras/claude-statusline-tee.sh; ruta configurable con {claude_reader.ENV_VAR})",
            )
        )

    base = store.base_dir()
    bad: list[str] = []
    if base.stat().st_mode & 0o777 != 0o700:
        bad.append(f"{base.name}/ {base.stat().st_mode & 0o777:04o}")
    for fname in ("state.json", "history.jsonl", "collect.log", "collect.lock"):
        f = base / fname
        if f.exists() and f.stat().st_mode & 0o777 != 0o600:
            bad.append(f"{fname} {f.stat().st_mode & 0o777:04o}")
    checks.append(_check(not bad, "permisos", "dir 0700, archivos 0600" if not bad else "; ".join(bad)))

    fd = os.open(base / "collect.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            checks.append(_check(True, "candado", "libre"))
        except BlockingIOError:
            checks.append(_check(True, "candado", "tomado ahora por un colector"))
    finally:
        os.close(fd)

    agent = LAUNCH_AGENT.expanduser()
    installed = "instalado" if agent.exists() else "no instalado (launchd/install.sh install)"
    checks.append(_check(True, "LaunchAgent com.fan.cuota", installed))
    if agent.exists():
        env_file = ENV_FILE.expanduser()
        env_ok = env_file.is_file() and env_file.stat().st_mode & 0o777 == 0o600
        checks.append(
            _check(
                env_ok, "env del LaunchAgent", str(env_file) if env_ok else f"{env_file} falta o no es 0600"
            )
        )
    return [line for _, line in checks], all(ok for ok, _ in checks)


def cmd_doctor() -> int:
    lines, ok = run_doctor()
    print("\n".join(lines))
    return 0 if ok else 1


# --- entry point -----------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cuota", description="Monitor de cuotas de suscripciones de IA.")
    p.add_argument(
        "command",
        nargs="?",
        choices=("watch", "collect", "doctor"),
        help="watch: refresco en vivo; collect: lectura manual; doctor: diagnóstico sin red",
    )
    p.add_argument("--json", action="store_true", help="estado tal cual (JSON)")
    p.add_argument("--line", action="store_true", help="una línea compacta para la status line")
    p.add_argument("--color", choices=("auto", "always", "never"), default=None)
    p.add_argument("--interval", type=float, default=5.0, help="segundos entre refrescos (watch)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "collect":
        from cuota import collect  # lazy: pulls readers, threads and subprocess

        return collect.main(args_list[1:])
    args = _parser().parse_args(args_list)
    if args.command == "collect":  # e.g. `cuota --color never collect`
        from cuota import collect

        return collect.main([])
    mode = args.color or ("never" if args.line else "auto")
    color = use_color(mode, isatty=sys.stdout.isatty(), env=os.environ)
    if args.line:
        return cmd_line(color)
    if args.json:
        return cmd_json()
    if args.command == "doctor":
        return cmd_doctor()
    if args.command == "watch":
        return cmd_watch(max(args.interval, 0.1), color)
    return cmd_table(color)


if __name__ == "__main__":
    sys.exit(main())
