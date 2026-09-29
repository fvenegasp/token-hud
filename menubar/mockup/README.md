# Static menu bar mockup

- `menubar.html` (self-contained, no network) shows a dark bar, a light bar and an error variant;
  `?v=dark|light|error` shows only one. Data: `state-snapshot.json` (copy of a real state, clock fixed at 2026-09-29 20:55Z).
- Bar: monochrome logo + two lines (5 h on top, weekly below; Kimi monthly). Blocked = 45 % opacity;
  idle 5 h = "—"; `stale` = 72 %; `error` = "!" badge with the last good values.
- Menu: one block per provider (plan, 5 h with pace or "blocked until…", weekly/monthly, freshness or error) and a footer.
- Optional proposals (dotted label, not part of the menu): "Open panel" (http://127.0.0.1:6739/index.html) and "Refresh now" (`cuota collect`). "Quit" is standard.
- Screenshots: `menubar-dark.png`, `menubar-light.png` (2×, window about 1400×600).
- The Swift tests read `state-snapshot.json`; do not move it.
