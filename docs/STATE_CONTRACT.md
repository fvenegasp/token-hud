# `state.json` contract, version 1

The collector writes `~/.cache/cuota/state.json` (override the directory with `CUOTA_HOME`). Every view reads only this file.

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-29T17:00:00Z",
  "providers": {
    "codex": {
      "status": "ok",
      "plan": "plus",
      "source": "app-server",
      "fetched_at": "2026-09-29T17:00:00Z",
      "error": null,
      "note": null,
      "windows": [
        { "kind": "5h",     "group": null, "used_pct": 0.0,   "resets_at": "2026-09-29T21:54:49Z", "state": "active" },
        { "kind": "weekly", "group": null, "used_pct": 100.0, "resets_at": "2026-10-03T20:23:01Z", "state": "exhausted" }
      ],
      "pace": { "weekly": { "expected_pct": 42.0, "projected_exhaust_at": null } }
    }
  }
}
```

Provider keys: `claude`, `codex`, `kimi`, `zai`, `agy`.

## Rules

- `used_pct` is always 0 to 100 and always means **used**, whatever form the provider reports (for example Antigravity reports
  the remaining fraction; the reader converts).
- `kind` is one of `5h | weekly | monthly | mcp`. A window is classified by its duration, never by its name or position.
- `state` of a window is one of `active | exhausted | idle`. `idle` means the window has not started (no use yet).
- `status` of a provider is one of `ok | stale | error`. With `error`, `windows` keeps the last good value or is empty; it is never 0.
- `error`, when present, carries a short code and message without sensitive data.
- `note` (text or null) is a brief, non-sensitive reason when a fallback source was used (for example Codex read from a rollout file).
- All times are ISO-8601 UTC. Conversion to local time is a presentation concern only.
- `group` is null except for providers that report groups (Antigravity: `"Gemini Models"`).
- `pace` is optional and per window kind: `expected_pct` (time elapsed in the window) and `projected_exhaust_at`
  (ISO time or null when it cannot be projected).
- **Expired reset**: every window with `resets_at <= now` is reported as 0 % and `active`, and the provider drops to `stale`.
  One rule shared by all readers.

## Compatibility policy

- Adding fields does **not** change `schema_version`. Consumers must ignore unknown fields and unknown enum values.
- An incompatible change (removing or renaming a field, changing a meaning or a unit) bumps `schema_version`.
- Consumers should treat a missing file, invalid JSON or an unsupported `schema_version` as "no data" (not as zeros).
  The bundled menu bar app does exactly that and decodes leniently, dropping malformed windows instead of failing.
- Writers publish atomically (temp file + rename), so readers never see a partial file.
