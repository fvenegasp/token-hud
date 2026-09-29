"""Collector (plan sections 5, 9, 10): run readers in parallel under an overall deadline, merge with the
previous state, write state.json atomically, append history, log one line per provider."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Any

from cuota import pace as pace_mod
from cuota import store
from cuota.model import Error, ProviderReading, iso_z, parse_iso_z
from cuota.readers import agy, base, claude, codex, kimi, zai

READERS: dict[str, Any] = {"claude": claude, "codex": codex, "kimi": kimi, "zai": zai, "agy": agy}
DEFAULT_DEADLINE_S = 50.0
HISTORY_WINDOW = timedelta(days=32)
EXIT_TEMPFAIL = 75


def _read_fn(reader: Any) -> Callable[[], ProviderReading]:
    return getattr(reader, "read", reader)


def _run_one(name: str, reader: Any) -> tuple[ProviderReading, float]:
    start = time.monotonic()
    try:
        reading = _read_fn(reader)()
    except Exception as exc:
        reading = base.error_reading(name, "unknown", exc, base.utcnow())
    return reading, time.monotonic() - start


def _merge(new: ProviderReading, previous: Mapping[str, Any] | None, now: datetime) -> ProviderReading:
    """Never upgrades status, never invents windows."""
    if new.status != "error" or not previous:
        return new
    try:
        prev = ProviderReading.from_dict(new.provider, dict(previous))
    except Exception:
        return new
    if not prev.windows:
        return new
    # Apply the shared past-reset rule to the carried windows; the status stays "error" regardless.
    windows = base.normalize_past_resets(replace(prev, status="stale", error=None), now).windows
    return ProviderReading(
        new.provider,
        "error",
        prev.plan,
        prev.source,
        prev.fetched_at,
        new.error,
        windows,
        "mostrando último valor bueno",
    )


def _history_entries(fresh: Mapping[str, ProviderReading], now: datetime) -> list[dict[str, Any]]:
    ts = iso_z(now)
    return [
        {
            "ts": ts,
            "provider": name,
            "kind": w.kind,
            "group": w.group,
            "used_pct": w.used_pct,
            "resets_at": iso_z(w.resets_at) if w.resets_at else None,
            "status": r.status,
        }
        for name, r in fresh.items()
        if r.status in ("ok", "stale")
        for w in r.windows
    ]


def _recent_history(now: datetime) -> list[dict[str, Any]]:
    """Parsed history lines from history.jsonl(.1) newer than 32 days; corrupt lines are skipped."""
    cutoff = now - HISTORY_WINDOW
    path = store.history_path()
    out: list[dict[str, Any]] = []
    for p in (path.with_name(path.name + ".1"), path):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                d = json.loads(line)
                if isinstance(d, dict) and parse_iso_z(d["ts"]) >= cutoff:
                    out.append(d)
            except (ValueError, KeyError, TypeError):
                continue
    return out


def _log_failure() -> None:
    """Full traceback of an unexpected collection failure into collect.log. Never raises."""
    with contextlib.suppress(Exception):
        _log_lines({}, failure=True)


def _log_lines(results: Mapping[str, tuple[ProviderReading, float]], *, failure: bool = False) -> None:
    logger = logging.getLogger("cuota.collect")
    handler = RotatingFileHandler(
        store.log_path(), maxBytes=store.LOG_MAX_BYTES, backupCount=store.LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        if failure:
            logger.exception("collection failed")
        for name, (r, dur) in results.items():
            code = r.error.code if r.error else "-"
            logger.info(
                "provider=%s status=%s source=%s duration=%.2fs error=%s", name, r.status, r.source, dur, code
            )
    finally:
        logger.removeHandler(handler)
        handler.close()
    os.chmod(store.log_path(), 0o600)


def collect(
    providers: Sequence[str] | None = None,
    *,
    now: datetime | None = None,
    readers: Mapping[str, Any] | None = None,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> dict[str, Any]:
    return _collect(providers, now=now, readers=readers, deadline_s=deadline_s)[0]


def _collect(
    providers: Sequence[str] | None,
    *,
    now: datetime | None,
    readers: Mapping[str, Any] | None,
    deadline_s: float,
) -> tuple[dict[str, Any], dict[str, float]]:
    registry = readers if readers is not None else READERS
    names = list(providers) if providers else list(registry)
    unknown = [n for n in names if n not in registry]
    if unknown:
        raise ValueError(f"unknown provider(s): {', '.join(unknown)}")
    with store.collect_lock():
        now = now or base.utcnow()
        prev_state = store.load_state()
        prev_providers: dict[str, Any] = dict(prev_state["providers"]) if prev_state else {}

        executor = ThreadPoolExecutor(max_workers=len(names), thread_name_prefix="cuota-read")
        futures: dict[str, Future[tuple[ProviderReading, float]]] = {
            n: executor.submit(_run_one, n, registry[n]) for n in names
        }
        wait(futures.values(), timeout=deadline_s)
        executor.shutdown(wait=False, cancel_futures=True)

        results: dict[str, tuple[ProviderReading, float]] = {}
        for name, fut in futures.items():
            if fut.done():
                results[name] = fut.result()
            else:
                note = f"sin respuesta antes de {deadline_s:g}s"
                results[name] = (
                    ProviderReading(
                        name, "error", None, "unknown", now, Error("timeout", note[:200]), (), note
                    ),
                    deadline_s,
                )

        providers_out: dict[str, Any] = dict(prev_providers)
        for name, (reading, _dur) in results.items():
            providers_out[name] = _merge(reading, prev_providers.get(name), now).to_dict()
        refreshed = {n: providers_out[n] for n in results}  # providers not run keep their entry untouched
        paces = pace_mod.pace_for_state({"providers": refreshed}, _recent_history(now), now)
        for name in refreshed:
            providers_out[name]["pace"] = paces.get(name, {})
        state = {
            "schema_version": store.SCHEMA_VERSION,
            "generated_at": iso_z(now),
            "providers": providers_out,
        }

        store.write_state(state)
        store.append_history(_history_entries({n: r for n, (r, _) in results.items()}, now))
        _log_lines({n: (ProviderReading.from_dict(n, providers_out[n]), d) for n, (_, d) in results.items()})
        return state, {n: d for n, (_, d) in results.items()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cuota.collect", description="Run one collection pass.")
    parser.add_argument("--provider", action="append", choices=sorted(READERS), help="limit to a provider")
    parser.add_argument("--json", action="store_true", help="print the state as JSON")
    args = parser.parse_args(argv)
    try:
        state, durations = _collect(args.provider, now=None, readers=None, deadline_s=DEFAULT_DEADLINE_S)
    except store.AlreadyRunning:
        print("cuota: another collector is already running", file=sys.stderr)
        return EXIT_TEMPFAIL
    except Exception as exc:
        _log_failure()
        print(f"cuota: collection failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(state, indent=2, ensure_ascii=False))
    else:
        for name, p in state["providers"].items():
            dur = f"{durations[name]:.1f}s" if name in durations else "-"
            print(f"{name}: {p['status']} ({dur})")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    if threading.active_count() > 1:  # a hung reader thread must not keep the process alive
        os._exit(code)
    sys.exit(code)
