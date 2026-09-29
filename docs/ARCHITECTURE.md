# Architecture

Token HUD shows how much of each subscription quota window (5 h, weekly, monthly) is used. It does not proxy
requests and does not count tokens: it asks each provider for the numbers the provider already computes.

```
          launchd (every 300 s)
                  |
            cuota collect
                  |  runs the 5 readers in parallel, each with its own timeout
     +------+-----+--+------+------+
  claude  codex   kimi    zai    agy        adapters: source -> normalized reading
     +------+-----+--+------+------+
             normalization + status (ok / stale / error / idle)
                  |
      +-----------+------------+
 state.json (atomic)    history.jsonl (append)
      |                        |
      +----------+-------------+
          views (read disk only)
   cuota | cuota --json | cuota --line | cuota watch | menu bar app | web panel
```

## Principles

- **One adapter per provider with a common interface**: `read(opts) -> ProviderReading`. A new provider is a new file
  in `collector/src/cuota/readers/`; nothing else changes.
- **Views never touch the network.** Only the collector talks to providers; every view reads `state.json` from disk.
- **Atomic writes**: `state.json` is written to a temp file and renamed; `history.jsonl` is append-only with rotation.
  Directory mode 0700, files 0600.
- **Per-provider degradation.** If a reading fails, the last good value is kept and marked `stale` with its age visible.
  An `error` never renders as 0 %.
- **One shared reset rule**: any window whose `resets_at` is in the past is reported as 0 % `active` and the provider drops
  to `stale` (`readers/base.normalize_past_resets`).
- **No stored credentials.** Keys are resolved on every run from the environment or `~/.zshrc`; provider CLIs keep their own logins.

## Repository layout

```
collector/
  pyproject.toml            # metadata, entry point `cuota`, dev deps (pytest, ruff)
  src/cuota/
    model.py                # dataclasses of the contract
    secrets.py              # key resolution (environment, then ~/.zshrc), never logged
    readers/{base,claude,codex,kimi,zai,agy}.py
    collect.py              # orchestration, timeouts, merge with the last good state
    store.py                # state.json and history.jsonl
    pace.py                 # pace and projection
    render.py, cli.py       # views
  launchd/                  # LaunchAgent template, run.sh wrapper, idempotent installer
  extras/                   # claude-statusline-tee.sh
  tests/                    # unit tests + scrubbed fixtures
menubar/                    # Swift app: CuotaCore (pure logic) + CuotaBar (AppKit)
panel/                      # single-file web panel
docs/
```

## Collection pass

`cuota collect` takes a file lock (`collect.lock`; exit code 75 if another collector holds it), runs all readers in
parallel, merges each result with the previous state, computes pace, then writes `state.json` and appends to
`history.jsonl`. Logs go to `collect.log` (size-rotated) and never contain credentials or full response bodies.

Per-reader timeouts (measured on one machine; treat as starting points):

| Reader | Typical | Worst observed | Timeout |
|---|---|---|---|
| claude (file) | < 1 ms | < 1 ms | 1 s |
| codex (app-server, cold start included) | 0.9 s warm | 5.3 s cold | 15 s |
| kimi | 0.8 s | 1.0 s | 10 s |
| zai | 1.0 s | 1.05 s | 10 s |
| agy | 6.5 s | 21 s | 40 s |

Readers run in parallel, so the pass takes as long as the slowest one (agy). If a reader times out, its provider
becomes `stale` with the last good value and the pass continues.

## Pace

For each active window with a known `resets_at`:

- `elapsed = 1 - (resets_at - now) / duration`
- `expected_pct = elapsed * 100`
- If `used_pct > expected_pct`, the exhaustion time is projected with the slope of that window's readings in
  `history.jsonl` (simple linear regression, at least 3 points).
- If the projection falls before the reset, the window is flagged.
- With fewer than 3 points, or a history that crosses a reset, nothing is projected: only expected vs. actual is shown.
- The projection is capped before building dates, and pace is computed in isolation per window, so a near-zero slope
  cannot break a pass.

## Presentation

- `cuota`: one row per provider and window: bar, % used, % remaining, reset ("today 23:07 · in 6 h"), pace, state.
  Color by threshold: green < 50, amber 50-80, red > 80 or exhausted. Honors `NO_COLOR`.
- `cuota --json`: the state as is. `cuota --line`: one compact line (the window closest to exhaustion per provider).
  `cuota watch`: live refresh from disk. `cuota doctor`: offline diagnosis (sources, resolved credentials without showing
  them, permissions, last pass per reader).
- Menu bar app and web panel: see `menubar/README.md` and `panel/README.md`. Both tolerate missing, stale or partial
  state and never render an unknown value as 0 %.

## Operation

- LaunchAgent `com.fan.cuota`: `StartInterval` 300, `RunAtLoad`, low priority, no `KeepAlive` (short-lived process).
- launchd does not load `~/.zshrc` and does not inherit the shell `PATH`; the installer writes a minimal `PATH` (only) to
  `~/.config/cuota/env` after picking the first `codex` and `agy` that actually run `--version`.
- The Mac being asleep pauses the schedule; collection resumes on wake. Data ages to `stale` in the meantime.
