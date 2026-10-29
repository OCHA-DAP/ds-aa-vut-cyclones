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

# Quick trigger

Initial trigger just for first WG meeting

```python
%load_ext jupyter_black
%load_ext autoreload
%autoreload 2
```

```python
import matplotlib.pyplot as plt
import ocha_stratus as stratus
import geopandas as gpd
import pandas as pd
import numpy as np
import matplotlib.colors as mcolors
from shapely.geometry import box
from matplotlib.ticker import FuncFormatter
from rioxarray.exceptions import NoDataInBounds
from tqdm.auto import tqdm

from src.datasources import codab
from src.constants import *
```

```python
blob_name = f"{PROJECT_PREFIX}/processed/ibtracs/adm2_usaradii_exp.parquet"
df_exp = stratus.load_parquet_from_blob(blob_name)
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
df_exp_recent = df_exp[df_exp["season"] >= 2001]
```

```python
df_exp_sid = (
    df_exp_recent.groupby(["sid", "name", "season", "buffer_speed"])[
        "pop_exposed"
    ]
    .sum()
    .reset_index()
)
df_exp_sid = df_exp_sid.pivot(
    columns="buffer_speed", values="pop_exposed", index="sid"
).reset_index()
df_exp_sid = df_exp_sid.rename(columns={x: f"exp{x}" for x in [34, 50, 64]})
```

```python
total_seasons = 2025 - 2001 + 1
```

```python
target_rp = 4
target_storms = int((total_seasons + 1) / target_rp)
```

```python
target_storms
```

```python
df_exp_sid
```

```python
blob_name = f"{PROJECT_PREFIX}/processed/impact_stats.parquet"
df_stats = stratus.load_parquet_from_blob(blob_name)
```

```python
df_stats = df_stats.merge(df_exp_sid)
```

```python
df_stats.corr(numeric_only=True)["Total Affected"].plot.bar()
```

```python
df_stats.sort_values("exp64", ascending=False)
```

```python

```

```python
for s in [34, 50, 64]:
    xcol = f"exp{s}"
    fig, ax = plt.subplots(figsize=(7, 7), dpi=200)

    for _, row in df_stats.iterrows():
        ax.annotate(
            str(row["name"]).capitalize() + "\n" + str(row["season"]),
            (row[xcol], row["Total Affected"]),
            ha="center",
            va="center",
            fontsize=6,
            color="crimson" if row["cerf"] == True else "k",
            zorder=10 if row["cerf"] else 9,
            alpha=0.8,
        )

    df_stats.plot(x=xcol, y="Total Affected", linewidth=0, ax=ax, legend=False)
    ax.set_ylim(bottom=0)
    ax.set_xlim(left=0)
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
df_stats["exp64_trig"] = (
    df_stats["exp64"] >= df_stats["exp64"].nlargest(target_storms).min()
)
```

```python
def color_cerf(val):
    if val == "Yes":
        return "background-color: crimson; color: white;"
    return ""


def color_trig(val):
    return "background-color: fuchsia; color: white;" if val else ""


df_disp = df_stats.copy()

wind_col = "Population exposed<br>to 64-knot wind"
rain_col = "Total 2-day rainfall<br>over whole country<br>(mm)"
impact_col = "EM-DAT:<br>Total Affected"
cerf_col = "CERF<br>Allocation?"
trig_col = "Trigger?"
col_rename = {
    "Total Affected": impact_col,
    "exp64": wind_col,
    "exp64_trig": trig_col,
}

df_disp = df_disp.rename(columns=col_rename)
df_disp["Cyclone"] = (
    df_disp["name"].fillna("Unnamed").str.capitalize()
    + " "
    + df_disp["season"].astype(str)
)
df_disp[cerf_col] = df_disp["cerf"].replace({True: "Yes", False: "No"})
df_disp = df_disp.sort_values([impact_col, wind_col], ascending=False)
cols = [
    wind_col,
    trig_col,
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
    .map(color_cerf, subset=cerf_col)
    .map(color_trig, subset=trig_col)
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
            wind_col: "{:,.0f}",
        }
    )
)
```

```python
df_stats.sort_values("exp50", ascending=False)
```

```python
(total_seasons + 1) / (target_storms - 1)
```
