# Token HUD panel

A single-file web panel (`index.html`, vanilla JS, no build) designed for a small 3" secondary display
(480×320) that scales to any size. It shows Claude, Codex, Kimi, GLM and Gemini quotas: 5 h and weekly/monthly
(% remaining), pace ("runs out Thu 14:00" / "on track"), reset countdown, blocked state and data freshness.
The UI text is in Spanish.

![Panel at 480×320](mockups/live-480.png)

## Data source

Only the `cuota` collector: `data/state.json` is a symlink to `~/.cache/cuota/state.json` (contract in
`../docs/STATE_CONTRACT.md`). The panel polls it every 15 s. `data/` is git-ignored.

```sh
# from the repository root
mkdir -p panel/data && ln -sfn ~/.cache/cuota/state.json panel/data/state.json
python3 -m http.server 6739 --directory panel
# open http://127.0.0.1:6739/index.html
```

`python3 -m http.server` binds to all interfaces by default. Add `--bind 127.0.0.1` if you do not want LAN access.

## Phone access (LAN)

The QR button in the header shows a QR code with the panel URL (`http://<lan-ip>:6739/index.html`, based on the
address you opened it with; open the panel through the LAN IP, not 127.0.0.1). The phone loads `data/state.json`
from the same server. It requires serving on all interfaces and being on the same Wi-Fi. There is no
authentication: anyone on the network can read your quota state. The page loads fonts from Google Fonts and `qrcodejs` from jsDelivr; everything else is local.

## QA states

- `?state=mockups/fixtures/normal.json` and `?state=mockups/fixtures/error.json` render fixed states.
- `?state=data/nope.json` shows the "no data" screen.
- Compare with `cuota --json` (remaining = 100 − `used_pct`).
- Browsers cache `index.html`; append `&v=N` when iterating.
- Visual design reference: `mockups/v2-desktop.png`.
