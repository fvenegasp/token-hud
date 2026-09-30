# Static menu bar mockup

- `menubar.html` (self-contained, no network) shows a dark bar, a light bar and an error variant;
  `?v=dark|light|error` shows only one. Data: `state-snapshot.json` (copy of a real state, clock fixed at 2026-09-29 20:55Z).
- Bar: monochrome logo + two lines (5 h on top, weekly below; Kimi monthly). Blocked = 45 % opacity;
  idle 5 h = "—"; `stale` = 72 %; `error` = "!" badge with the last good values.
- Menu: one block per provider (plan, 5 h with pace or "blocked until…", weekly/monthly, freshness or error) and a footer.
- Optional proposals (dotted label, not part of the menu): "Open panel" (http://127.0.0.1:6739/index.html) and "Refresh now" (`cuota collect`). "Quit" is standard.
- Screenshots: `menubar-dark.png`, `menubar-light.png` (2×, window about 1400×600).
- The Swift tests read `state-snapshot.json`; do not move it.

# Sidebar mockup

- `sidebar.html` (self-contained, no network): floating side panel replacing the menu bar items when the notch hides them
  (MacBook Pro 14", 1512 × 982 pt, notch x 656–856). Same data, clock, logos and state rules as `menubar.html`.
  The mockup predates the implementation; implemented values below win where they differ.
- `?v=dark|hover|light|menu|error` (comma list allowed); `&shot=1` drops titles for screenshots.
- Modes are exclusive ("Mostrar en: Barra de menú / Barra lateral", ⌥⌘L toggles, remembered): in sidebar mode Token HUD
  has no menu bar items; switching back restores them. The menu opens from the sidebar (grip or row click / right-click).
- Panel: non-activating NSPanel, right or left edge, 6 pt inset, vertically centered; grip on top; one row per provider.
  Width by size: Compacto 64 / Normal 84 / Grande 108 pt (Normal: digits 15 pt semibold mono, labels 11 pt, logo 18 pt).
- Rows: "5h" over "sem" ("mes" for Kimi), % remaining with a small "%"; 4 pt meter = remaining on a subtle gray track.
  Color by remaining level: >50 green, 20–50 orange, <20 red; exhausted = red "0" with red-tinted track; idle 5 h = "—".
  Pace warning (`se_agota` / `sobre_ritmo`) = small orange "▲" after the label.
- Screenshots: `sidebar-dark.png` (dark, hover, menu stacked), `sidebar-light.png` (2×, 1512 pt wide).
