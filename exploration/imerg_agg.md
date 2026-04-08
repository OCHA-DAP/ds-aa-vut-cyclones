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

# IMERG aggregation

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
import matplotlib.pyplot as plt
from tqdm.auto import tqdm
from dask.diagnostics import ProgressBar
from rasterio.errors import RasterioIOError

from src.datasources import codab
from src.constants import *
```

```python
iso3 = "vut"
```

```python
adm0 = codab.load_codab_from_blob(admin_level=0)
```

```python
adm0.plot()
```

```python
query = """
SELECT *
FROM storms.ibtracs_tracks_geo
WHERE basin = 'SP'
"""
with stratus.get_engine(stage="prod").connect() as con:
    gdf_tracks = gpd.read_postgis(query, con, geom_col="geometry")
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
gdf_tracks = gdf_tracks.merge(df_storms)
```

```python
gdf_tracks[["sid", "valid_time", "season"]]
```

```python
gdf_tracks_recent = gdf_tracks[
    (gdf_tracks["season"] >= 2000) & (gdf_tracks["season"] < 2026)
].copy()
gdf_tracks_recent = gdf_tracks_recent.sort_values("valid_time")
```

```python
adm0_proj = adm0.to_crs(3832)
target_geom = adm0_proj.geometry.iloc[0]
gdf_tracks_recent_proj = gdf_tracks_recent.to_crs(3832)
```

```python
distances = []

for geom in tqdm(gdf_tracks_recent_proj.geometry):
    distances.append(geom.distance(target_geom))

gdf_tracks_recent["distance_m"] = distances
```

```python
gdf_tracks_recent["distance_m"].hist()
```

```python
d_thresh = 250
```

```python
adm0_proj_buffer = adm0_proj.buffer(d_thresh * 1000)
```

```python
gdf_tracks_close = gdf_tracks_recent[
    gdf_tracks_recent["distance_m"] <= d_thresh * 1000
].copy()
```

```python
df_tracks_agg = (
    gdf_tracks_close.groupby("sid")
    .agg(
        valid_time_min=("valid_time", "min"),
        valid_time_max=("valid_time", "max"),
        wind_speed_max=("wind_speed", "max"),
    )
    .reset_index()
).dropna()
```

```python
df_tracks_agg
```

```python
query = """
SELECT *
FROM public.imerg
WHERE pcode = 'VU'
"""
with stratus.get_engine(stage="prod").connect() as conn:
    df_imerg = pd.read_sql(query, conn)
```

```python
df_imerg
```

```python
df_imerg["valid_date"] = pd.to_datetime(df_imerg["valid_date"])
```

```python
df_imerg = df_imerg.sort_values("valid_date")
```

```python
df_imerg["roll2_mean"] = df_imerg["mean"].rolling(2).sum()
```

```python
def get_storm_rainfall(row):
    row = row.copy()
    min_date = row["valid_time_min"].date() - pd.DateOffset(days=1)
    max_date = row["valid_time_max"].date() + pd.DateOffset(days=2)
    dates = pd.date_range(min_date, max_date)
    dff = df_imerg[df_imerg["valid_date"].isin(dates)]
    return dff["roll2_mean"].max()
```

```python
df_tracks_agg["roll2_mean"] = df_tracks_agg.apply(get_storm_rainfall, axis=1)
```

```python
blob_name = f"{PROJECT_PREFIX}/processed/ibtracs_imerg_stats_{iso3}.parquet"
stratus.upload_parquet_to_blob(df_tracks_agg, blob_name)
```

```python

```

```python
gdf_tracks_landfall = gdf_tracks_recent[
    gdf_tracks_recent["distance_m"] <= d_thresh * 10
].copy()
```

```python
df_tracks_agg_landfall = (
    gdf_tracks_landfall.groupby("sid")
    .agg(
        valid_time_min=("valid_time", "min"),
        valid_time_max=("valid_time", "max"),
        wind_speed_max=("wind_speed", "max"),
    )
    .reset_index()
).dropna()
```

```python
df_tracks_agg_landfall["wind_rank"] = df_tracks_agg_landfall[
    "wind_speed_max"
].rank(ascending=False)
df_tracks_agg_landfall["wind_rp"] = (26 + 1) / df_tracks_agg_landfall[
    "wind_rank"
]
```

```python
df_tracks_agg_landfall.sort_values("wind_rank").merge(
    df_storms[["sid", "name"]]
)[["name", "wind_speed_max", "wind_rank", "wind_rp"]]
```
