# ds-aa-vut-cyclones — Claude guidance

## Static JS pages under `docs/`

Marimo is **not** the default for anything new (team decision D93), and this
repo's marimo WASM explorer was retired on 2026-08-11 — `docs/index.html` is
now a redirect to `docs/forecast-check/`. Interactive pages are plain
`index.html` + `app.js` + `style.css` in a subdirectory of `docs/`, with data
pre-baked as JSON by a script in `exploration/`. See `docs/forecast-check/`
and `exploration/make_forecast_check_data.py`.

Do NOT run `marimo export html-wasm -o docs/ -f` — it would overwrite the
redirect at `docs/index.html` and recreate `docs/assets/`.
`exploration/trigger_explorer.py` is kept for reference only.

Leaflet and other CDN assets are fine here (unlike Artifacts, GH Pages has no
CSP restriction).

## The VMGD historical archive

`ds-aa-vut-cyclones/raw/vmgd/vmgd_historical_tc_archive_2026-08-05.zip` (9 GB).
It is **zip64** — `unzip`/`zipinfo` wrongly report it corrupt; Python's
`zipfile` reads it fine. Parsers live in `src/datasources/vmgd.py`.

The only forecast wind radii in it are in the JTWC ATCF a-decks (`*.dat`).
VMGD's own Forecast Track Maps have position and intensity category only — the
wind boundaries are drawn on the map image, not tabulated.

## Deploying interactive marimo notebooks to GitHub Pages

GH Pages runs no server, so WASM notebooks can't reach Azure blob storage or
the Postgres DB. The pattern is: pre-compute once locally, commit a CSV, load
it via HTTP in the WASM path.

### 1. Pre-compute and commit the data

Write a one-off script (e.g. `exploration/make_<name>.py`) that uses
`ocha_stratus` normally and writes its output to
`exploration/public/<name>.csv`. Commit the CSV — it travels with the repo
and gets bundled by `html-wasm`.

Use CSV, not parquet. `pyarrow` is not available in Pyodide.

### 2. Branch on `sys.platform == "emscripten"` in the data loading cell

```python
@app.cell
def _(mo, pd):
    import importlib
    import sys

    with mo.status.spinner(subtitle="Loading data..."):
        if sys.platform == "emscripten":
            import io
            import urllib.request

            _url = str(mo.notebook_location() / "public" / "<name>.csv")
            with urllib.request.urlopen(_url) as _resp:
                df = pd.read_csv(io.StringIO(_resp.read().decode("utf-8")))
        else:
            # importlib.import_module() hides these from marimo's AST scanner
            # so Pyodide never tries to micropip-install ocha_stratus.
            _stratus = importlib.import_module("ocha_stratus")
            _constants = importlib.import_module("src.constants")
            # ... normal stratus/DB loading ...

    return df, ...
```

Key rules for the WASM path:

- **`mo.notebook_location()`** returns the page URL (e.g.
  `https://ocha-dap.github.io/ds-aa-vut-cyclones/`). The `/` operator works
  for path joining. Do NOT pass it to `open()` — it's a URL, not a file path.
- **`urllib.request.urlopen(url)`** is patched by pyodide-http to use XHR. It
  handles gzip content-encoding transparently.
- **Wrap the response in `io.StringIO`** before passing to `pd.read_csv()`.
  Passing a URL string directly triggers pandas' compression auto-detection
  and causes `BadGzipFile` errors.
- **Do NOT use `pyodide.http.open_url("relative/path")`**. In a Web Worker,
  relative URLs resolve against the worker script URL (`/assets/worker.js`),
  not the page URL — it fetches the wrong location.

Key rules for the local (`else`) path:

- **Use `importlib.import_module("package_name")`** (string argument) for any
  package that can't be micropip-installed in Pyodide (e.g. `ocha_stratus`,
  `src.constants`, any package with binary C extensions). Marimo's AST scanner
  reads `import X` statements statically; string refs bypass it.
- **`try/except ImportError` does NOT hide imports from the scanner** — marimo
  scans AST before execution.

### 3. Declare all pure-Python WASM dependencies in the imports cell

Marimo installs packages it sees as direct `import` statements. If a package
is only required as a lazy dependency (e.g. `jinja2` for `pandas.style`), add
a bare import to the top-level imports cell so the scanner picks it up:

```python
@app.cell
def _():
    import jinja2  # noqa: F401 — required by pandas.style in Pyodide
    import marimo as mo
    import matplotlib.pyplot as plt
    import pandas as pd
    return mo, pd, plt
```

### 4. Export and deploy

```bash
uv run marimo export html-wasm exploration/<notebook>.py \
    --no-show-code -o docs/ -f
git add docs/ && git commit -m "deploy WASM export"
git push
```

- Output goes to `docs/` (configured as GH Pages source on the relevant branch).
- The `exploration/public/` folder is copied automatically into `docs/public/`.
- No `-- --locked true` flag needed; WASM is interactive by default
  (`mo.cli_args()` returns `{}` in Pyodide).
- Pre-commit hooks will auto-fix trailing whitespace in the generated JS files
  on first attempt; just re-stage and commit again.
