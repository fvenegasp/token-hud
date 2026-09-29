# Token HUD (menu bar)

Claude, Codex, Kimi, GLM and Gemini quotas in the macOS menu bar. It reads the same `state.json`
(contract v1, see `../docs/STATE_CONTRACT.md`) as the web panel; no network and no credentials.

```
[logo] 68   <- % remaining in the 5 h window ("—" when idle)
       70   <- % remaining in the weekly window (Kimi: monthly)
```

A blocked provider (weekly/monthly window exhausted) is drawn at 45 % opacity; `stale` at 72 %;
`error` shows a "!" badge with the last good values. The menu lists, per provider, the 5 h and
weekly/monthly lines with pace ("on track", "N× over pace", "runs out…") and data freshness, plus
"Open panel", "Refresh now" and "Quit". The UI text is in Spanish.

![Menu bar, dark](mockup/menubar-dark.png)

## Requirements

- macOS 14+ (SVG logos are rasterized with `NSImage`; `LSMinimumSystemVersion` 14.0).
- Swift 6 toolchain (Xcode with a recent macOS SDK).
- The `cuota` collector (`~/.local/bin/cuota`) writing `~/.cache/cuota/state.json`.

## Build and test

```sh
swift test            # CuotaCoreTests: exact texts of the approved mockup
./build-app.sh        # swift build -c release + dist/Token HUD.app (ad-hoc signed)
```

`build-app.sh` copies the SwiftPM resource bundle (`CuotaBar_CuotaBar.bundle`, with `logos/*.svg`) into
`Contents/Resources`, so the generated `Bundle.module` resolves it from the main bundle's `resourceURL`.

## Install / uninstall

```sh
./install.sh install     # migrates the legacy install (com.fan.cuotabar), copies to ~/Applications/Token HUD.app, loads the LaunchAgent
./install.sh status
./install.sh uninstall
./install.sh install --dry-run   # shows what it would do without touching anything
```

The LaunchAgent (`~/Library/LaunchAgents/com.fan.tokenhud.plist`) starts the binary with `RunAtLoad`,
`KeepAlive { SuccessfulExit = false }` and `ProcessType Interactive`. It is idempotent: a `bootout`
first (errors ignored) then `bootstrap`.

## Data source

- Reads `~/.cache/cuota/state.json` and watches the **directory** `~/.cache/cuota` with a write
  `DispatchSource` (the collector publishes with an atomic rename), plus a 60 s fallback timer.
  Relative texts ("N min ago") are recomputed every 30 s.
- If the file is missing, corrupt or `schema_version` is not 1, the bar shows "—" and the menu says
  there is no data and suggests `cuota doctor`.
- "Refresh now" runs `~/.local/bin/cuota collect` off the main thread with the `PATH` from the `PATH=`
  line of `~/.config/cuota/env` (it never reads credentials); exit code 75 (another collect running)
  is not an error. It refreshes when done.
- "Open panel" opens http://127.0.0.1:6739/index.html in the browser (see `../panel/README.md`).

## Layout

- `Sources/CuotaCore/Model.swift`: tolerant contract v1 (lossy decoding, `StateParser`).
- `Sources/CuotaCore/Presenter.swift`: pure presentation logic (Foundation only): strip, flags and
  menu lines with color roles. Same rules as `mockup/menubar.html`.
- `Sources/CuotaBar/`: AppKit app (`@MainActor`, `LSUIElement`): status items, 2× template image
  renderer, menu rebuilt on each refresh.
- `Tests/CuotaCoreTests/PresenterTests.swift`: snapshot of the mockup with a fixed clock
  (2026-09-29T20:55:00Z, America/Santiago) plus blocked/idle/stale/error/schema rules. It reads
  `mockup/state-snapshot.json`, so keep that path valid.

## Troubleshooting

- **Everything shows "—"**: run `cuota doctor` and check that `~/.cache/cuota/state.json` exists.
- **Does not start at login**: `./install.sh status`; reload with
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fan.tokenhud.plist`.
- **"Refresh now" does nothing**: check `~/.local/bin/cuota` and the `PATH=` in `~/.config/cuota/env`;
  an already running collect (exit 75) is ignored on purpose.
- **Blank logos**: macOS < 14 cannot rasterize SVG; rebuild with `./build-app.sh` on 14+.
