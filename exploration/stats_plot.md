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

# Scatter plot and table

```python
%load_ext jupyter_black
%load_ext autoreload
%autoreload 2
```

```python
import calendar

import geopandas as gpd
import ocha_stratus as stratus
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

# import statsmodels.api as sm
import xarray as xr


# from adjustText import adjust_text
from tqdm.auto import tqdm
from dask.diagnostics import ProgressBar

from src.constants import *
from src.datasources import codab
```

```python
iso3 = "vut"
```

```python
blob_name = f"{PROJECT_PREFIX}/processed/ibtracs_imerg_stats_{iso3}.parquet"
df_stats_raw = stratus.load_parquet_from_blob(blob_name)
```

```python
blob_name = f"{PROJECT_PREFIX}/processed/emdat_sid_{iso3}.parquet"
df_emdat_raw = stratus.load_parquet_from_blob(blob_name)
```

```python
df_emdat_raw[["iso3", "Event Name", "Total Affected"]]
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
df_stats = df_stats_raw.merge(
    df_emdat_raw[["sid", "Total Affected"]], how="left"
).merge(df_storms)
df_stats["Total Affected"] = df_stats["Total Affected"].fillna(0)
df_stats["cerf"] = df_stats["sid"].isin(CERF_SIDS)
```

```python
blob_name = f"{PROJECT_PREFIX}/processed/impact_stats.parquet"
stratus.upload_parquet_to_blob(df_stats, blob_name)
```

```python
df_stats.corr(numeric_only=True)["Total Affected"].plot.bar()
```

```python
df_stats.set_index("sid").loc[JUDY]
```

```python
def plot_stats(
    wind_col="wind_speed_max",
    rain_col="roll2_mean",
    impact_col="Total Affected",
    name_col="name",
    only_with_impact: bool = False,
    min_season=None,
):
    df_plot = df_stats.copy()
    ymax = df_plot[rain_col].max() * 1.1
    xmax = df_plot[wind_col].max() * 1.1
    if only_with_impact:
        df_plot = df_plot[df_plot[impact_col] > 0]
    if min_season is not None:
        df_plot = df_plot[df_plot["season"] >= min_season]
    # df_plot = df_plot[df_plot["season"] < 2025]
    cerf_color = "crimson"
    fig, ax = plt.subplots(figsize=(7, 7), dpi=200)

    bubble_sizes = df_plot[impact_col].fillna(0)
    bubble_sizes_scaled = bubble_sizes / bubble_sizes.max() * 5000

    ax.scatter(
        df_plot[wind_col],
        df_plot[rain_col],
        s=bubble_sizes_scaled,
        c=df_plot["cerf"].apply(lambda x: cerf_color if x else "k"),
        alpha=0.3,
        edgecolor="none",
        zorder=1,
    )

    for _, row in df_plot.iterrows():
        ax.annotate(
            str(row[name_col]).capitalize() + "\n" + str(row["season"]),
            (row[wind_col], row[rain_col]),
            ha="center",
            va="center",
            fontsize=6,
            color=cerf_color if row["cerf"] == True else "k",
            zorder=10 if row["cerf"] else 9,
            alpha=0.8,
        )

    legend_text = (
        "Size of bubble proportional to\n"
        "total number of people affected [EM-DAT]\n\n"
        "Historical CERF allocations in red"
    )
    ax.annotate(
        legend_text,
        (xmax * 0.03, ymax * 0.97),
        va="top",
        ha="left",
        fontsize=6,
        fontstyle="italic",
        color="grey",
    )

    ylabel = (
        "Two-day rainfall, mean over whole country (mm) [IMERG]"
        if rain_col == "roll2_mean"
        else rain_col
    )
    ax.set_ylabel(ylabel)
    ax.set_xlabel(
        "Max. wind speed while within 250 km of country (knots) [IBTrACS]"
    )

    ax.set_xlim(left=0, right=xmax)
    ax.set_ylim(bottom=0, top=ymax)

    ax.set_title(
        "Vanuatu: tropical cyclone historical rainfall and wind speed"
    )

    ax.spines.top.set_visible(False)
    ax.spines.right.set_visible(False)
    return fig, ax
```

```python
plot_stats()
```

```python
plot_stats(only_with_impact=True)
```

```python
plot_stats(min_season=2010)
```

```python
plot_stats(min_season=2015)
```

```python
df_stats
```

```python
def lighten_cmap(cmap_name, blend=0.5):
    """
    blend=0 → original cmap
    blend=1 → fully white
    """
    cmap = plt.get_cmap(cmap_name)
    colors = cmap(np.linspace(0, 1, 256))

    white = np.array([1, 1, 1, 1])
    colors = colors * (1 - blend) + white * blend

    return mcolors.LinearSegmentedColormap.from_list(
        f"{cmap_name}_light", colors
    )


# Create lighter maps
light_oranges = lighten_cmap("Oranges", blend=0.3)
light_blues = lighten_cmap("Blues", blend=0.3)
```

```python
def color_cerf(val):
    if val == "Yes":
        return "background-color: crimson; color: white;"
    return ""


df_disp = df_stats[df_stats["season"] >= 2010]

wind_col = "Max. wind speed<br>while within 250km<br>(knots)"
rain_col = "Total 2-day rainfall<br>over whole country<br>(mm)"
impact_col = "EM-DAT:<br>Total Affected"
cerf_col = "CERF<br>Allocation?"
col_rename = {
    "Total Affected": impact_col,
    "wind_speed_max": wind_col,
    "roll2_mean": rain_col,
}

df_disp = df_disp.rename(columns=col_rename)
df_disp["Cyclone"] = (
    df_disp["name"].fillna("Unnamed").str.capitalize()
    + " "
    + df_disp["season"].astype(str)
)
df_disp[cerf_col] = df_disp["cerf"].replace({True: "Yes", False: "No"})
df_disp = df_disp.sort_values("sid", ascending=False)
cols = [
    wind_col,
    rain_col,
    cerf_col,
    impact_col,
]


display(
    df_disp.set_index("Cyclone")[cols]
    .style.bar(
        subset=impact_col,
        color="#b8a3e0",
        props="width: 150px;",
    )
    .background_gradient(
        subset=wind_col,
        cmap=light_oranges,
    )
    .background_gradient(
        subset=rain_col,
        cmap=light_blues,
    )
    .map(color_cerf, subset=cerf_col)
    .set_table_styles(
        {
            impact_col: [
                {"selector": "th", "props": [("text-align", "left")]},
                {"selector": "td", "props": [("text-align", "left")]},
            ]
        }
    )
    .format(
        {
            impact_col: "{:,.0f}",
            wind_col: "{:.0f}",
            rain_col: "{:.0f}",
        }
    )
)
```

```python
n_seasons
```

```python
df_stats["wind_rank"] = df_stats["wind_speed_max"].rank(ascending=False)
df_stats["wind_rp"] = (n_seasons + 1) / df_stats["wind_rank"]
```

```python
df_stats.sort_values("wind_rank")[
    ["name", "season", "wind_speed_max", "wind_rp"]
]
```
