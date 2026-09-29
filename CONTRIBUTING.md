# Contributing

Thanks for helping out. Token HUD is small on purpose: one collector, two read-only views.

## Running the tests

Collector (Python 3.12+, [uv](https://docs.astral.sh/uv/)):

```sh
cd collector
uv run --group dev pytest
uv run --group dev ruff check .
uv run --group dev ruff format --check .
```

Menu bar app (Swift 6 toolchain, macOS 14+):

```sh
cd menubar
swift test
```

The Swift tests read `menubar/mockup/state-snapshot.json`; keep that path valid.
The panel has no build step; check it with `?state=mockups/fixtures/normal.json` and `error.json`.

## Code style

- Python: ruff (config in `collector/pyproject.toml`, line length 110). No runtime dependencies: standard library only.
- Swift: keep presentation logic pure in `CuotaCore` (Foundation only) and AppKit code in `CuotaBar`; match the surrounding style.
- Views never touch the network. Only the collector talks to providers.

## Pull requests

1. Fork and create a topic branch.
2. Keep changes focused; add or update tests for behavior changes.
3. Make sure tests and ruff pass before opening the PR, and describe what you verified.
4. If a provider endpoint changes, update `docs/PROVIDERS.md` together with the reader.

## No credentials in fixtures

Fixtures must be scrubbed: no API keys, tokens, account IDs, e-mail addresses, hostnames or local paths.
Use obviously fake values (for example `FAKE-SECRET-0123`). Never paste a real response body without reviewing it line by line.
