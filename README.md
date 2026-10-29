# Vanuatu Anticipatory Action: tropical cyclones

[![Generic badge](https://img.shields.io/badge/STATUS-UNDER%20DEVELOPMENT-%23007CE0)](https://shields.io/)

## Published pages

Both are served from `docs/` on GitHub Pages:

| Page | What it is |
|---|---|
| [`/forecast-check/`](https://ocha-dap.github.io/ds-aa-vut-cyclones/forecast-check/) | Trigger design on the observed record **and** the forecast check (plain JS + Leaflet, two tabs) |
| [`/trigger/`](https://ocha-dap.github.io/ds-aa-vut-cyclones/trigger/) | Plain-language explainer: how the trigger works, historical activations, why no rainfall, interactive forecast explorer |
| [`/slides/`](https://ocha-dap.github.io/ds-aa-vut-cyclones/slides/) | Summary deck: trigger definition + Trigger Mechanism Statistics, historical storm table (reads the forecast-check data) |

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

## Monitoring

Live trigger monitoring for the endorsed framework (2026/27 and 2027/28
seasons). All three triggers count people exposed to winds of at least
64 knots, threshold **15,000**:

| Trigger | Forecast | Part of the track | Where |
|---|---|---|---|
| Readiness | RSMC Nadi (FMS) | 72 to 120 h after the forecast was produced | Sanma, Shefa, Tafea |
| Action | VMHS | 24 to 72 h | Sanma, Shefa, Tafea |
| Observational | VMHS observed track | the analysis (0 h) position of every VMHS forecast for the storm | all of Vanuatu |

```text
FMS (RSMC Nadi)                       VMHS (Vanuatu Met)
  |  forecast CSV by email              |  forecast CSV by email
  v                                     v
ocha-datascience@un.org  --forwarded-->  the maintainer's un.org mailbox
  |  Power Automate flows: fji_trigger_monitoring (sender met.gov.fj)
  |                        vut_trigger_monitoring (sender vanuatu.gov.vu
  |                          or meteo.gov.vu, or subject vut_test)
  v
humdata.org relay inbox: "[fji-relay] fms_forecast" / "[vut-relay] vmhs_forecast"
  |  pa-aa-fji-storms "Poll FMS inbox" (GitHub Actions, every 10 minutes)
  |  with its repo variable VUT_MONITORING = test | live
  v
Databricks job "Vanuatu Cyclone Monitoring" (databricks.yml), one run per CSV
  |  pipelines/monitor_forecast_trigger.py --source fms | vmhs
  v
Listmonk: readiness / action / observational emails as soon as a trigger
is met, and one information email per forecast time with both forecasts
```

FMS forecasts reach the job through the existing Fiji chain: the poller
sends every FMS CSV to the Fiji job and then to this one. The Fiji job
ingests it into `storms.fms_*`; this job reads the same staged CSV itself,
so it never waits on that ingest. VMHS CSVs come through a second Power
Automate flow (`vut_trigger_monitoring`, a copy of the Fiji flow) with
their own relay subject, and only ever go to this job. The flow and its
relay are described in the pa-aa-fji-storms README (Monitoring); the
internal KB has the addresses.

**What gets emailed.** FMS forecasts whose track (to 120 h) never comes
within 1,000 km of Vanuatu are skipped: FMS covers the whole South Pacific.

- *Trigger emails* go out at once, from the forecast that meets them:
  readiness (RSMC Nadi), action and observational (VMHS). Readiness and
  action go out for every forecast that meets them, as in Fiji; the
  observational email once per storm (the observed track only grows).
- *The information email* is one per storm and forecast time, with both
  providers: the VMHS forecast in the Fiji format (exposure, a likely-range
  plot against past storms, the area-council exposure as a CSV and province
  plots as a PDF) and the RSMC Nadi readiness result as a few lines (no
  RSMC Nadi maps or data). VMHS is named as the only official source
  throughout. The first forecast of a pair waits for the second
  (`src/monitoring/info_bundle.py`): a forecast of the other source issued
  within 3 h and with the same disturbance id, the same name, or an
  analysis position within 300 km. If none arrives within 2 h
  (`INFO_WAIT_HOURS`), the email goes out with the one forecast; a
  late partner, or a re-issue, sends the pair again. The next forecast run
  sends overdue emails, and so does the poller in pa-aa-fji-storms: it
  reads the queue (`monitoring/info_pending.csv`, `test_...` for tests)
  every 10 minutes and starts this job with the event `info_flush`
  (`info_flush_test`) when one is overdue.

Each email is sent at most once per CSV (or pair of CSVs)
(`ds-aa-vut-cyclones/monitoring/email_record.csv` on the dev blob; tests
use `test_email_record.csv`). The observed track is the analysis (0 h)
point of every VMHS forecast received for the storm, as ds-storms-pipeline
does for NHC (`monitoring/vmhs_observed_points.csv`, tests: `test_...`),
keyed by disturbance id and season, which stay the same when a storm is
named.

The CSV and the province plots count, as ds-storms-alerts does, what was
observed so far plus the whole forecast (the union of the two swaths, per
wind speed); only the trigger values and the likely-range plot are about
the trigger window. The maps draw the observed swath faded, the forecast
swath beyond it (the forecast minus the observed swath of the same speed,
ds-storms-pipeline's forecast-only buffers), and the trigger window's
64 kt swath as an outline.

**Method.** The swaths are built from the forecast's own 34/50/64 kt radii
(FMS and VMHS use 10-minute winds, so the 64 kt radius is used as is), with
the same functions and the same population counting as the trigger design
(`src/monitoring/exposure.py` follows
`exploration/make_forecast_check_data.py` step for step: WorldPop 2026
1 km, area councils expanded 500 m into the sea). A forecast is only
monitored if its header says `Official Forecast Track`.

**VMHS format.** VMHS had not sent a sample CSV by October 2026. VMHS runs
the same Tropical Cyclone Module as FMS, so the parser assumes the same
export (`src/monitoring/forecast.py`). If the first VMHS CSV differs, the
run fails (the failure email goes to the maintainer) and the parser needs
updating.

**Lists.** Real sends: lists 129 and 130 ("[AA framework] Vanuatu Tropical
Cyclones", informational and trigger), plus the general AA lists 14 and 15
and CHD Data Science (6), as for Fiji. Tests: list 5 only, with `[TEST]` in
the subject.

**Switches.** `TEST_EMAIL` (default True: list 5), `DRY_RUN` (default True:
nothing sent or written), `SIMULATE_TRIGGER` (with no CSV, run the made-up
forecast in `src/monitoring/data/simulated_forecast.csv`, which reaches the
readiness and action thresholds), `MAX_FORECAST_AGE_HOURS` (48 on
Databricks: no real-list emails about older forecasts). On Databricks only
`csv_blob`, `event` and `test_email` can be set per run; a `*_test` event
always forces the test list.

**Going live.** Deploy the bundle (`databricks bundle deploy -t prod -p
DEFAULT` from a clean `main`), then in pa-aa-fji-storms set the repo
variables `VUT_DBX_JOB_ID` and `VUT_MONITORING=test` (every Vanuatu run to
list 5), turn the `vut_trigger_monitoring` flow on, and run the end-to-end
test. `VUT_MONITORING=live` sends real forecasts to the real lists.
`VUT_MONITORING=off` (or unset) stops all Vanuatu runs; the Fiji chain is
not affected either way. Always go `off` → `test` → `live`, never straight
to `live`: the first tick after switching on sends every CSV of the
poller's 3-day look-back to the job (to list 5 in `test`), and the record
of those test runs is what stops `live` sending them again to the real
lists. Test runs keep their own observed-track record, so a storm already
under way at the switch to `live` starts its real observed track there.

**Testing.**

```shell
# locally, nothing sent: writes temp/*_info.html, *_adm2_exposure.csv/.pdf
DRY_RUN=True SIMULATE_TRIGGER=True uv run python \
  pipelines/monitor_forecast_trigger.py --source vmhs
uv run pytest tests
```

End to end: email the maintainer's un.org mailbox with the subject exactly
`vut_test` and an FMS-format CSV attached (a VMHS test), or `fji_test` (an
FMS test, which also runs the Fiji job). The `[TEST]` emails arrive on list
5 within about 15 minutes.

**When something breaks.** Follow the chain: the flow's run history (and
the CSV in OneDrive `vut_vmhs_forecasts/`), the relay inbox, the "Poll FMS
inbox" log in pa-aa-fji-storms, the Databricks run, the Listmonk campaign.
A failed run emails the maintainer's un.org address. To send a forecast by
hand: `databricks bundle run vut_monitoring` with its `csv_blob` and
`event` (see `databricks.yml`).

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
