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

# Wind exposure

Using USA radii

```python
%load_ext jupyter_black
%load_ext autoreload
%autoreload 2
```

```python
import io

import matplotlib.pyplot as plt
import ocha_stratus as stratus
import geopandas as gpd
import pandas as pd
from shapely.geometry import box
from matplotlib.ticker import FuncFormatter
from rioxarray.exceptions import NoDataInBounds
from tqdm.auto import tqdm

from src.datasources import codab
from src.constants import *
from src.utils.exposure import calculate_multi_adm_exposure
```

```python
adm2 = codab.load_codab_from_blob(admin_level=2)
```

```python
adm1 = codab.load_codab_from_blob(admin_level=1)
```

```python
adm2.plot()
```

```python
blob_name = "worldpop/pop_count/global_pop_2026_CN_1km_R2025A_UA_v1.tif"
da_wp_global = stratus.open_blob_cog(blob_name, container_name="raster")
```

```python
da_wp = da_wp_global.rio.clip(adm2.geometry).squeeze(drop=True).compute()
```

```python
da_wp_loose = (
    da_wp_global.rio.clip(adm2.geometry, all_touched=True)
    .squeeze(drop=True)
    .compute()
)
```

```python
da_wp.where(da_wp > 0).plot()
```

```python
da_wp.where(da_wp > 0).sum().values
```

```python
da_wp_loose.where(da_wp_loose > 0).sum().values
```

```python
# 1. Reproject once
adm2_proj = adm2.to_crs(3832)

# 2. Dissolve to get full landmass
land = adm2_proj.dissolve()

# 3. Create big bounding box
minx, miny, maxx, maxy = land.total_bounds
pad = 50000  # 50 km padding

sea_box_geom = box(minx - pad, miny - pad, maxx + pad, maxy + pad)

sea_box = gpd.GeoDataFrame(geometry=[sea_box_geom], crs=adm2_proj.crs)

# 4. Ocean = big box minus land
ocean = gpd.overlay(sea_box, land, how="difference")

# 4. Buffer admin2
buffered = adm2_proj.copy()
buffered["geometry"] = adm2_proj.buffer(500)

# 5. Only keep new buffer area
buffer_only = gpd.overlay(buffered, adm2_proj, how="difference")

# 6. Keep ocean part only
buffer_ocean = gpd.overlay(buffer_only, ocean, how="intersection")

# Ensure buffer_ocean carries the admin2 ID
buffer_ocean = buffer_ocean[["ADM2_PCODE", "geometry"]]

# Concatenate original + ocean expansion
combined = gpd.GeoDataFrame(
    pd.concat([adm2_proj, buffer_ocean], ignore_index=True), crs=adm2_proj.crs
)

# Dissolve back to admin2 level
adm2_expanded = combined.dissolve(by="ADM2_PCODE")

adm2_expanded = gpd.GeoDataFrame(
    adm2_expanded,
    geometry="geometry",
    crs=adm2_proj.crs,
)

adm2_expanded = adm2_expanded.reset_index().to_crs(4326)
```

```python
adm2_expanded.unary_union.area
```

```python
adm2_expanded.geometry.area.sum()
```

```python
adm2_expanded = adm2_expanded.sort_values("ADM2_PCODE").reset_index(drop=True)

clean_geoms = []
used = None

for geom in adm2_expanded.geometry:
    if used is None:
        clean_geoms.append(geom)
        used = geom
    else:
        new_geom = geom.difference(used)
        clean_geoms.append(new_geom)
        used = used.union(new_geom)

adm2_expanded["geometry"] = clean_geoms
```

```python
adm2_expanded.unary_union.area
```

```python
adm2_expanded.geometry.area.sum()
```

```python
adm2_expanded
```

```python
fig, ax = plt.subplots(dpi=300)
adm2_expanded.plot(ax=ax)
```

```python
adm2_expanded
```

```python
adm2_expanded_aoi = adm2_expanded[
    adm2_expanded["ADM1_PCODE"].isin(ADM1_AOI_PCODES)
]
```

```python
adm2_expanded_aoi.plot()
```

```python
da_wp_expanded = (
    da_wp_global.rio.clip(adm2_expanded_aoi.geometry)
    .squeeze(drop=True)
    .compute()
)
```

```python
da_wp_expanded.where(da_wp_expanded > 0).sum().values
```

```python
blob_name = f"pa-aa-fji-storms/processed/ibtracs/wind_buffers.parquet"
gdf_buffers = gpd.read_parquet(io.BytesIO(stratus.load_blob_data(blob_name)))
```

```python
gdf_buffers = gdf_buffers.to_crs(FJI_CRS)
```

```python
adm2_expanded
```

```python
gdf_buffers
```

```python
df_exp = calculate_multi_adm_exposure(
    gdf_buffers,
    da_wp_expanded,
    adm2_expanded,
    adm_index="ADM2_PCODE",
    disable_tqdm=False,
    geo_crs=FJI_CRS,
)
```

```python
df_exp[df_exp["buffer_speed"] == 64].sort_values(
    "pop_exposed", ascending=False
)
```

```python
fig, ax = plt.subplots()
adm2_expanded.boundary.plot(ax=ax)
gdf_buffers.set_index("sid").loc[JUDY].plot(alpha=0.1, ax=ax)
```

```python
df_exp["pop_exposed"].max()
```

```python
blob_name = f"{PROJECT_PREFIX}/processed/ibtracs/adm2_usaradii_exp.parquet"
stratus.upload_parquet_to_blob(df_exp, blob_name)
```

```python
dicts = []
for pcode, row in adm2_expanded.set_index("ADM2_PCODE").iterrows():
    _da_clip = da_wp_expanded.rio.clip([row.geometry])
    dicts.append(
        {
            "ADM2_PCODE": pcode,
            "total_pop": int(_da_clip.where(_da_clip > 0).sum()),
        }
    )

df_pop = pd.DataFrame(dicts)
```

```python
df_pop["total_pop"].sum()
```

```python
blob_name = f"{PROJECT_PREFIX}/processed/worldpop/adm2_total_pop.parquet"
stratus.upload_parquet_to_blob(df_pop, blob_name)
```

```python
df_exp = df_exp.merge(df_pop)
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
df_exp = df_exp.merge(df_storms)
```

```python
df_exp.groupby(["buffer_speed", "season"])[
    "pop_exposed"
].max().reset_index().pivot(
    columns="buffer_speed", index="season", values="pop_exposed"
).plot()
```

```python
df_exp_recent = df_exp[df_exp["season"] >= 2001]
```

```python
df_exp_recent["pop_exposed"] = df_exp_recent[["total_pop", "pop_exposed"]].min(
    axis=1
)
```

```python
df_exp_recent["pop_exposed_frac"] = (
    df_exp_recent["pop_exposed"] / df_exp_recent["total_pop"]
)
```

```python
df_exp_recent
```

```python
df_exp_recent_nonzero = df_exp_recent[df_exp_recent["pop_exposed"] > 0]
```

```python
df_exp_recent_nonzero["pop_exposed_frac"].hist()
```

```python
df_exp_adm2_count = (
    df_exp_recent_nonzero.groupby(["ADM2_PCODE", "buffer_speed"])
    .size()
    .reset_index()
)
```

```python
df_exp_adm1_recent_nonzero = df_exp_recent_nonzero.merge(
    adm2[["ADM2_PCODE", "ADM1_PCODE"]]
)

df_exp_adm1_recent_nonzero = (
    df_exp_adm1_recent_nonzero.groupby(
        ["sid", "name", "season", "buffer_speed", "ADM1_PCODE"]
    )[["pop_exposed", "total_pop"]]
    .sum()
    .reset_index()
)
```

```python
df_exp_adm1_recent_nonzero["pop_exposed_frac"] = (
    df_exp_adm1_recent_nonzero["pop_exposed"]
    / df_exp_adm1_recent_nonzero["total_pop"]
)
```

```python
df_exp_adm1_recent_nonzero
```

```python
df_exp_adm2_count = df_exp_adm2_count.rename(columns={0: "count"})
```

```python
df_exp_adm2_count.sort_values("count")
```

```python
adm2[["ADM2_PCODE", "ADM2_EN"]].merge(df_exp_adm2_count).sort_values("count")
```

```python
adm2.merge(df_exp_adm2_count).plot(column="count", legend=True)
```

```python
df_exp_adm2_sum = (
    df_exp_recent.groupby(["buffer_speed", "ADM2_PCODE"])["pop_exposed"]
    .sum()
    .reset_index()
)
df_exp_adm2_sum = df_exp_adm2_sum.merge(df_pop)
```

```python
total_seasons = 2025 - 2001 + 1
```

```python
total_seasons
```

```python
df_exp_adm2_sum["pop_exposed_per_season"] = (
    df_exp_adm2_sum["pop_exposed"] / total_seasons
)

df_exp_adm2_sum["frac_pop_exposed_per_season"] = (
    df_exp_adm2_sum["pop_exposed_per_season"] / df_exp_adm2_sum["total_pop"]
)
```

```python
df_exp_adm2_sum.sort_values("frac_pop_exposed_per_season").iloc[-20:]
```

```python
speeds = [34, 50, 64]

fig, axes = plt.subplots(
    1, 3, figsize=(12, 7), dpi=200, gridspec_kw={"wspace": 0.3}
)

for ax, speed in zip(axes, speeds):

    gdf_plot = adm2.merge(
        df_exp_adm2_sum[df_exp_adm2_sum["buffer_speed"] == speed],
        on="ADM2_PCODE",  # adjust if needed
        how="left",
    )

    gdf_plot.plot(
        column="frac_pop_exposed_per_season",
        vmin=0,
        cmap="viridis_r",
        legend=True,
        ax=ax,
        legend_kwds={
            "shrink": 0.6,  # 🔹 shorter colorbar
            "aspect": 25,  # 🔹 thinner
            "pad": 0.01,  # 🔹 closer to map
        },
    )

    ax.set_title(f"{speed} knots")
    ax.axis("off")

fig.suptitle(
    "Vanuatu: average fraction of population exposed to wind speed per season\nAll storms since 2001",
    y=0.95,
)
```

```python
speeds = [34, 50, 64]

fig, axes = plt.subplots(
    1, 3, figsize=(12, 7), dpi=200, gridspec_kw={"wspace": 0.3}
)

for ax, speed in zip(axes, speeds):

    gdf_plot = adm2.merge(
        df_exp_adm2_sum[df_exp_adm2_sum["buffer_speed"] == speed],
        on="ADM2_PCODE",  # adjust if needed
        how="left",
    )

    gdf_plot.plot(
        column="pop_exposed_per_season",
        vmin=0,
        cmap="viridis_r",
        legend=True,
        ax=ax,
        legend_kwds={
            "shrink": 0.6,  # 🔹 shorter colorbar
            "aspect": 25,  # 🔹 thinner
            "pad": 0.01,  # 🔹 closer to map
            "format": "{x:,.0f}",
        },
    )

    ax.set_title(f"{speed} knots")
    ax.axis("off")

fig.suptitle(
    "Vanuatu: total fraction of population exposed to wind speed per season\nAll storms since 2001",
    y=0.95,
)
```

```python
df_out = adm2[["ADM1_PCODE", "ADM1_EN", "ADM2_PCODE", "ADM2_EN"]].merge(
    df_exp_adm2_sum
)
df_out = df_out.sort_values(["buffer_speed", "ADM1_EN", "ADM2_EN"])

out_path = "temp/vut_adm2_exposure_sum.csv"
df_out.to_csv(out_path, index=False)
```

```python
df_exp_adm2_sum
```

```python
df_exp_adm1_sum = (
    df_exp_adm2_sum.merge(adm2[["ADM1_PCODE", "ADM2_PCODE"]])
    .groupby(["ADM1_PCODE", "buffer_speed"])[["pop_exposed", "total_pop"]]
    .sum()
    .reset_index()
)
```

```python
df_exp_adm1_sum["pop_exposed_per_season"] = (
    df_exp_adm1_sum["pop_exposed"] / total_seasons
)

df_exp_adm1_sum["frac_pop_exposed_per_season"] = (
    df_exp_adm1_sum["pop_exposed_per_season"] / df_exp_adm1_sum["total_pop"]
)
```

```python
df_exp_adm1_sum
```

```python
speeds = [34, 50, 64]

fig, axes = plt.subplots(
    1, 3, figsize=(12, 7), dpi=200, gridspec_kw={"wspace": 0.3}
)

for ax, speed in zip(axes, speeds):

    gdf_plot = adm1.merge(
        df_exp_adm1_sum[df_exp_adm1_sum["buffer_speed"] == speed],
        on="ADM1_PCODE",  # adjust if needed
        how="left",
    )

    gdf_plot.plot(
        column="frac_pop_exposed_per_season",
        vmin=0,
        cmap="viridis_r",
        legend=True,
        ax=ax,
        legend_kwds={
            "shrink": 0.6,  # 🔹 shorter colorbar
            "aspect": 25,  # 🔹 thinner
            "pad": 0.01,  # 🔹 closer to map
        },
    )

    ax.set_title(f"{speed} knots")
    ax.axis("off")

fig.suptitle(
    "Vanuatu: fraction of population exposed to wind speed per season\nAll storms since 2001",
    y=0.95,
)
```

```python
speeds = [34, 50, 64]

fig, axes = plt.subplots(
    1, 3, figsize=(12, 7), dpi=200, gridspec_kw={"wspace": 0.3}
)

for ax, speed in zip(axes, speeds):

    gdf_plot = adm1.merge(
        df_exp_adm1_sum[df_exp_adm1_sum["buffer_speed"] == speed],
        on="ADM1_PCODE",  # adjust if needed
        how="left",
    )

    gdf_plot.plot(
        column="pop_exposed_per_season",
        vmin=0,
        cmap="viridis_r",
        legend=True,
        ax=ax,
        legend_kwds={
            "shrink": 0.6,  # 🔹 shorter colorbar
            "aspect": 25,  # 🔹 thinner
            "pad": 0.01,  # 🔹 closer to map
            "format": "{x:,.0f}",
        },
    )

    ax.set_title(f"{speed} knots")
    ax.axis("off")

fig.suptitle(
    "Vanuatu: total population exposed to wind speed per season\nAll storms since 2001",
    y=0.95,
)
```

```python
df_plot = adm1[["ADM1_PCODE", "ADM1_EN"]].merge(df_exp_adm1_sum)
df_plot = df_plot.pivot(
    index="ADM1_EN",
    columns="buffer_speed",
    values="pop_exposed_per_season",
)
df_plot[50] = df_plot[50] - df_plot[64]
df_plot[34] = df_plot[34] - df_plot[50] - df_plot[64]

fig, ax = plt.subplots(dpi=200)
df_plot.plot.bar(stacked=True, ax=ax, color=["gold", "darkorange", "crimson"])

ax.legend(
    title="Wind speed\n(knots)",
    bbox_to_anchor=(1, 1),
    loc="upper left",
    frameon=False,
)
ax.set_xlabel("Province")
ax.set_ylabel("Total population exposed\nto wind speed per season")

ax.yaxis.set_major_formatter(FuncFormatter(lambda x, pos: f"{int(x):,}"))

[ax.spines[x].set_visible(False) for x in ["top", "right"]]
```

```python
df_plot = adm1[["ADM1_PCODE", "ADM1_EN"]].merge(df_exp_adm1_sum)
df_plot = df_plot.pivot(
    index="ADM1_EN",
    columns="buffer_speed",
    values="frac_pop_exposed_per_season",
)
df_plot[50] = df_plot[50] - df_plot[64]
df_plot[34] = df_plot[34] - df_plot[50] - df_plot[64]

fig, ax = plt.subplots(dpi=200)
df_plot.plot.bar(stacked=True, ax=ax, color=["gold", "darkorange", "crimson"])

ax.legend(
    title="Wind speed\n(knots)",
    bbox_to_anchor=(1, 1),
    loc="upper left",
    frameon=False,
)
ax.set_xlabel("Province")
ax.set_ylabel("Fraction of population exposed\nto wind speed per season")

[ax.spines[x].set_visible(False) for x in ["top", "right"]]
```

```python
df_out = adm1[["ADM1_PCODE", "ADM1_EN"]].merge(df_exp_adm1_sum)
df_out = df_out.sort_values(["buffer_speed", "ADM1_EN"])
df_out
```

```python
out_path = "temp/vut_adm1_exposure_sum.csv"
df_out.to_csv(out_path, index=False)
```
