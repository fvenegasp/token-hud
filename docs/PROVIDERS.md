# Providers

Where each number comes from, how to authenticate, and the traps found while verifying them (2026-09-29).
All sources were probed without consuming tokens.

**Stability warning.** Only the Claude status line JSON is officially documented. Every other source below is an
**undocumented** endpoint or CLI output that its vendor may change or remove without notice. Expect readers to break
occasionally; when they do, the provider shows `error` or `stale` instead of a wrong number.

| Provider | Windows | Source | Auth | Documented? |
|---|---|---|---|---|
| Claude | 5 h, weekly | status line JSON (`rate_limits`) | none (file) | Yes (status line input) |
| Codex | 5 h, weekly | `codex app-server` JSON-RPC over stdio | the Codex CLI login | No |
| Kimi Code | 5 h, monthly | `GET https://api.kimi.com/coding/v1/usages` | `KIMI_API_KEY` | No |
| GLM (z.ai) | 5 h, weekly, monthly MCP | `GET https://api.z.ai/api/monitor/usage/quota/limit` | `Z_AI_API_KEY` | No |
| Antigravity (Gemini) | 5 h, weekly | `agy -p /usage --output-format json` | the `agy` login | No |

## Claude

- **Source:** the JSON Claude Code pipes to the `statusLine` command. Fields: `rate_limits.five_hour.{used_percentage, resets_at}`
  and `rate_limits.seven_day.{...}`, `resets_at` in epoch seconds.
- **Getting the file:** the reader needs that JSON on disk. Chain `collector/extras/claude-statusline-tee.sh` in
  `~/.claude/settings.json` (see `collector/README.md`). Path: `$CUOTA_CLAUDE_STATUSLINE_FILE`, else
  `~/.cache/cuota/claude-statusline.json`, else the legacy `~/.claude/daemon/.statusline-last.json`.
- **Trap:** the file only updates while a Claude Code session is running. Freshness = file mtime; `stale` after 15 min. A window
  whose `resets_at` has passed is reported as reset (0 %) and `stale`. A missing file is a clear `missing_source` error, not
  silently old data.
- **Not used:** `GET api.anthropic.com/api/oauth/usage`: undocumented, aggressive 429s, and it requires reading the OAuth token
  from the Keychain.

## Codex

- **Source:** `codex -s read-only -a never app-server` over JSON-RPC on stdio: `initialize`, `initialized`, then
  `account/rateLimits/read`. Fields: `rateLimits.{primary,secondary}.{usedPercent, windowDurationMins, resetsAt}`, `planType`,
  `rateLimitReachedType`. The snapshot with `limitId == "codex"` is used.
- **Auth:** none on our side. The official CLI refreshes its own token, so the collector never touches credentials.
- **Classification:** by `windowDurationMins` (300 = 5 h, 10080 = weekly), never by primary/secondary position.
- **Fallback:** the last `token_count.rate_limits` event in `~/.codex/sessions/**/rollout-*.jsonl`, marked `stale` by age.
  It uses snake_case (`used_percent`, `window_minutes`, `resets_at`), unlike app-server's camelCase, and it can carry
  `primary`/`secondary` as `null`. The fallback looks for the latest event with populated windows; if none exists the state is
  `error`, never 0 %.
- **Trap:** the login shell may resolve a broken `codex` (for example a stale package-manager shim). The installer picks the first
  candidate that runs `--version` successfully, and `cuota doctor` runs the binaries too.

## Kimi Code

- **Source:** `GET https://api.kimi.com/coding/v1/usages` with `Authorization: Bearer <KIMI_API_KEY>` (the console key).
  Fields: `usages.limit_5h`, `usages.limit_month_total`, `usages.limit_month_code`, each `{used_ratio 0-1, reset_time ISO}`.
- **Correction (verified):** the real 5 h window is in `limits[].detail` (`used`/`limit`, `resetTime`; a 300 min or 5 h duration is
  accepted), **not** in `usages.limit_5h`. While the API was answering HTTP 403 "You've reached your 5-hour usage limit",
  `limits[].detail` showed 100/100 and `usages.limit_5h.used_ratio` stayed at 0. The reader takes the 5 h window from `limits[]`
  and only falls back to `usages.limit_5h` when there is no entry, adding a `note`. The monthly window still comes from
  `usages.limit_month_total`.
- **Do not use the CLI's OAuth token.** It lasts about 15 minutes and its refresh token rotates: refreshing it from outside
  would log the CLI out.
- Invalid keys return a real HTTP 401.

## GLM (z.ai)

- **Source:** `GET https://api.z.ai/api/monitor/usage/quota/limit` with `Authorization: <Z_AI_API_KEY>` (**no** `Bearer` prefix).
  Fields: `data.limits[]` with `{type, unit, number, percentage, nextResetTime (epoch ms)}` and `data.level` (the plan).
  - `TOKENS_LIMIT` with unit 3, number 5: the 5 h window.
  - `TOKENS_LIMIT` with unit 6, number 1: the weekly window.
  - `TIME_LIMIT`: the monthly MCP quota.
- **Trap, HTTP 200 with an error:** with an invalid key z.ai answers **HTTP 200** with the body
  `{"code":401,"msg":"token expired or incorrect","success":false}`. The reader validates `success == true` and `code == 200` in the
  body and never trusts the HTTP status alone.
- **Idle window:** the 5 h window has no `nextResetTime` when there is no use (it starts on first use). That is state `idle`, not an error.
- **Risk:** the `unit` codes were inferred from third-party fixtures and real responses. An unknown combination is logged and left out;
  the reader never guesses.

## Antigravity (Gemini group)

- **Source:** `agy -p /usage --output-format json </dev/null`. Takes about 8 s (up to about 20 s) and consumes no tokens. `</dev/null` is required.
- **Fields:** `command.data.groups[]`, only the group named `Gemini Models`; inside it `buckets[].{id, window: 5h|weekly, remaining_fraction 0-1, reset_time}`.
- **Reading:** parse the structured JSON, not the tabulated text in `response`.
- **Normalization:** agy reports the **remaining** fraction; used = 1 - remaining.
- **Why only Gemini:** spawns do not pass `--model`, and observed logs show a single Gemini model. The group filter is configurable in the reader.
