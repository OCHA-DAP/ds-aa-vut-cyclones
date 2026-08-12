# Vanuatu Anticipatory Action: tropical cyclones

[![Generic badge](https://img.shields.io/badge/STATUS-UNDER%20DEVELOPMENT-%23007CE0)](https://shields.io/)

## Published pages

Both are served from `docs/` on GitHub Pages:

| Page | What it is |
|---|---|
| [`/forecast-check/`](https://ocha-dap.github.io/ds-aa-vut-cyclones/forecast-check/) | Trigger design on the observed record **and** the forecast check (plain JS + Leaflet, two tabs) |
| [`/slides/`](https://ocha-dap.github.io/ds-aa-vut-cyclones/slides/) | Two-slide summary deck: trigger definition + RP table, historical storm table (reads the forecast-check data) |

`/` redirects there — the old marimo WASM explorer was retired 2026-08-11
(slow to load; fully superseded).

The forecast-check page is a static JS app with its data pre-baked into
`docs/forecast-check/data/`:

```shell
# observed-record trigger data (hist.json, obsgeom/, plus a provenance CSV)
uv run python exploration/make_trigger_data.py
# forecast-cycle exposure + map geometry (core.json, geom/)
uv run python exploration/make_forecast_check_data.py
```

`make_forecast_check_data.py` needs a local copy of the VMGD archive zip
(`ds-aa-vut-cyclones/raw/vmgd/vmgd_historical_tc_archive_2026-08-05.zip` on
blob) — set the path at the top of the script.

## Directory structure

The code in this repository is organized as follows:

```shell

├── analysis      # Main repository of analytical work for the AA pilot
├── docs          # .Rmd files or other relevant documentation
├── exploration   # Experimental work not intended to be replicated
├── src           # Code to run any relevant data acquisition/processing pipelines
|
├── .gitignore
├── README.md
└── requirements.txt

```

## Reproducing this analysis

Create a directory where you would like the data to be stored,
and point to it using an environment variable called
`AA_DATA_DIR`.

Next create a new virtual environment

Install the GloFAS branch of the toolbox with

```shell
pip install git+https://github.com/OCHA-DAP/pa-aa-toolbox.git@feature/glofas#egg=aa-toolbox
```

and install the requirements with:

```shell
pip install -r requirements.txt
```

Finally, install any code in `src` using the command:

```shell
pip install -e .
```

To run the pipeline that downloads and processes the data, execute:

```shell
python src/main.py
```

To see runtime options, execute:

```shell
python src/main.py -h
```

If you would like to instead receive the processed data from our team, please
[contact us](mailto:centrehumdata@un.org).

## Development

All code is formatted according to black and flake8 guidelines.
The repo is set-up to use pre-commit.
Before you start developing in this repository, you will need to run

```shell
pre-commit install
```

The `markdownlint` hook will require
[Ruby](https://www.ruby-lang.org/en/documentation/installation/)
to be installed on your computer.

You can run all hooks against all your files using

```shell
pre-commit run --all-files
```

It is also **strongly** recommended to use `jupytext`
to convert all Jupyter notebooks (`.ipynb`) to Markdown files (`.md`)
before committing them into version control. This will make for
cleaner diffs (and thus easier code reviews) and will ensure that cell outputs aren't
committed to the repo (which might be problematic if working with sensitive data).
