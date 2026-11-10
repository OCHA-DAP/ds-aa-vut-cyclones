# ds-aa-vut-cyclones — Claude guidance

## The Pages site (`docs/`, served from `main`)

The README's "Pages site" section is the reference. In short:

- **`/trigger/` is linked from the published framework document** (Annex 2).
  Never move or rename it. Its storm table and Trigger Mechanism Statistics
  come from `docs/assets/historical.js` (shared with `/slides/`) and must
  match the framework: `tests/test_pages_framework.py` checks them, and the
  "Check pages against the framework" workflow runs it on every change to
  `docs/`. Don't edit its expected values to make regenerated data pass.
- The root `docs/index.html` is the team landing page (cards, HDX v2 styles in
  `docs/assets/`). Every product page starts with the back link to it.
- Pages are plain `index.html` + JS + CSS with data pre-baked as JSON by the
  scripts in `exploration/` (`docs/forecast-check/data/`). Marimo is **not**
  used here any more (team decision D93; the WASM explorer was retired
  2026-08-11, `exploration/trigger_explorer.py` is kept for reference only).
  Never run `marimo export html-wasm -o docs/`: it would overwrite the
  landing page.
- Leaflet and other CDN assets are fine (GH Pages has no CSP restriction).
  Basemaps: Esri's key-free canvas, not CARTO (its tiles now need a key).

## The VMGD historical archive

`ds-aa-vut-cyclones/raw/vmgd/vmgd_historical_tc_archive_2026-08-05.zip` (9 GB).
It is **zip64** — `unzip`/`zipinfo` wrongly report it corrupt; Python's
`zipfile` reads it fine. Parsers live in `src/datasources/vmgd.py`.

The only forecast wind radii in it are in the JTWC ATCF a-decks (`*.dat`).
VMGD's own Forecast Track Maps have position and intensity category only — the
wind boundaries are drawn on the map image, not tabulated.
