# cuota (collector)

`cuota` is the collector and terminal viewer of Token HUD. A short-lived collector queries each provider
for its quota windows (5 h, weekly, monthly) and writes `state.json`; every view (`cuota`, `--json`,
`--line`, `watch`, the menu bar app, the web panel) only reads that file from disk. It never proxies
requests and never counts tokens. Standard library only, Python 3.12+.

See `../docs/ARCHITECTURE.md`, `../docs/STATE_CONTRACT.md` and `../docs/PROVIDERS.md`.

## Install and run

```sh
cd collector
launchd/install.sh install            # installs the tool (uv, editable), writes env and plist, loads the agent
launchd/install.sh status             # launchd state, plist, env, state.json age, last log lines
launchd/install.sh uninstall          # unloads the agent, removes plist and env; keeps data
launchd/install.sh uninstall --purge  # also uninstalls the tool and removes ~/.cache/cuota (asks y/N; --yes skips)
launchd/install.sh --dry-run <cmd>    # prints the actions without running them
cuota doctor                          # offline diagnosis, includes env and LaunchAgent
```

Without launchd you can run `uv tool install --editable .` and call `cuota collect` yourself.

Views: `cuota` (table), `cuota --json`, `cuota --line` (one compact line for a status line), `cuota watch`,
`cuota collect [--provider X]`, `cuota doctor`. `NO_COLOR` is respected. The interface text is in Spanish.

A LaunchAgent (`com.fan.cuota`) runs `cuota collect` every 5 minutes and at load. No KeepAlive.

## Where things live

- State, history and lock: `~/.cache/cuota/` (`state.json`, `history.jsonl`, `collect.lock`). Override the directory with `CUOTA_HOME`.
- Logs: `~/.cache/cuota/collect.log` (rotated) and `~/.cache/cuota/launchd.log` (launchd stdout/stderr).
- `~/.config/cuota/env` (mode 0600): only `PATH` (launchd does not read `~/.zshrc`). It never stores credentials;
  `cuota.secrets` resolves them on every run from the environment or from an `export` line in `~/.zshrc`.
- Exit code 75 means another collector is already running; that is normal.

## Credentials

- `KIMI_API_KEY` (Kimi Code console key) and `Z_AI_API_KEY` (z.ai), from the environment or `export` in `~/.zshrc`.
- Codex and Antigravity (`agy`) use their own CLI logins. Claude needs no credential (see below).
- Nothing is stored or logged.

## Claude source (status line)

The Claude reader takes `rate_limits` from the JSON that Claude Code pipes to its `statusLine` command.
It reads, in order:

1. `$CUOTA_CLAUDE_STATUSLINE_FILE` if set;
2. `~/.cache/cuota/claude-statusline.json`;
3. legacy fallback `~/.claude/daemon/.statusline-last.json` if (2) does not exist.

To produce that file, chain `extras/claude-statusline-tee.sh` into `~/.claude/settings.json`. It copies stdin to the cache
file atomically (temp file + rename, mode 0600) and passes stdin through unchanged:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/absolute/path/to/token-hud/collector/extras/claude-statusline-tee.sh | your-statusline-command"
  }
}
```

The file is refreshed only while a Claude Code session is active; after 15 minutes the provider is reported as `stale`.
`cuota doctor` prints the resolved path and its age.

## Tests

```sh
uv run --group dev pytest
uv run --group dev ruff check .
```

Fixtures in `tests/fixtures/` are scrubbed provider responses. Do not add real credentials or account data.
