---
jupyter:
  jupytext:
    formats: ipynb,md
    text_representation:
      extension: .md
      format_name: markdown
      format_version: '1.3'
      jupytext_version: 1.19.1
  kernelspec:
    display_name: ds-aa-vut-cyclones
    language: python
    name: ds-aa-vut-cyclones
---

# EM-DAT

```python
%load_ext jupyter_black
%load_ext autoreload
%autoreload 2
```

```python
import ocha_stratus as stratus
import pandas as pd
import geopandas as gpd
import xarray as xr
from tqdm.auto import tqdm
from dask.diagnostics import ProgressBar
from rasterio.errors import RasterioIOError

from src.datasources import emdat
from src.constants import *
```

```python
iso3s = ["vut"]
```

```python
df_emdat_new = emdat.load_emat(iso3s=iso3s)
```

```python
df_emdat_new
```

```python
query = """
SELECT *
FROM storms.ibtracs_storms
"""
with stratus.get_engine(stage="prod").connect() as con:
    df_storms = pd.read_sql(query, con)
```

```python
blob_name = "pa-aa-phl-storms/processed/emdat-tropicalcyclone-2000-2022-processed-sids.csv"
df_emdat_old_raw = stratus.load_csv_from_blob(blob_name)
```

```python
df_emdat_old = df_emdat_old_raw[
    df_emdat_old_raw["iso3"].str.lower().isin(iso3s)
].copy()
```

```python
df_emdat_old
```

```python
# see which ones never had SIDs filled in
df_emdat_old[df_emdat_old["sid"].isnull()][
    [
        "DisNo.",
        "Event Name",
        "Start Year",
        "Start Month",
        "Start Day",
        "End Year",
        "End Month",
        "End Day",
        "Total Affected",
    ]
]
```

```python
# see which new EM-DAT events aren't in the old matched up CSV file
df_emdat_new[~df_emdat_new["DisNo."].isin(df_emdat_old["DisNo."].values)][
    ["DisNo.", "Event Name", "Start Year", "Start Month", "Start Day"]
]
```

```python
df_emdat_new_recent = df_emdat_new[
    ~df_emdat_new["DisNo."].isin(df_emdat_old["DisNo."].values)
]
```

```python
# match to IBTrACS
disno2sid_new = {
    "2023-0119-VUT": JUDY,
    "2023-0128-VUT": KEVIN,
    "2023-0680-VUT": LOLA,
}
```

```python
df_emdat_new_recent["sid"] = df_emdat_new_recent["DisNo."].replace(
    disno2sid_new
)
```

```python
df_emdat_new_recent["Total Affected"]
```

```python
df_emdat_combined = pd.concat(
    [df_emdat_old, df_emdat_new_recent], ignore_index=True
)
```

```python
df_emdat_combined[df_emdat_combined["sid"].isnull()]
```

```python
df_emdat_non_null = df_emdat_combined[~df_emdat_combined["sid"].isnull()]
```

```python
blob_name = f"{PROJECT_PREFIX}/processed/emdat_sid_{'_'.join(iso3s)}.parquet"
stratus.upload_parquet_to_blob(df_emdat_non_null, blob_name)
```

```python

```
