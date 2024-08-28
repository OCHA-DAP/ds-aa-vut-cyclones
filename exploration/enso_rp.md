---
jupyter:
  jupytext:
    formats: ipynb,md
    text_representation:
      extension: .md
      format_name: markdown
      format_version: '1.3'
      jupytext_version: 1.16.1
  kernelspec:
    display_name: ds-aa-vut-cyclones
    language: python
    name: ds-aa-vut-cyclones
---

# ENSO-RP

```python
%load_ext jupyter_black
%load_ext autoreload
%autoreload 2
```

```python
import pandas as pd

from src.utils import blob
from src.datasources import ibtracs, codab, enso
```

```python
D_THRESH = 250
CAT_THRESH = 3
MIN_SEASON = 2000
```

```python
df_enso = enso.load_enso()
cols = ["month", "phase"]
df_enso = df_enso.rename(columns={"date": "month", "phase_longterm": "phase"})[
    cols
]
```

```python
df_enso
```

```python
df = ibtracs.load_vut_distances()
df["cat"] = df["usa_wind"].apply(ibtracs.knots2cat)
df["season"] = df["time"].apply(
    lambda x: x.year if x.month >= 7 else x.year - 1
)
df = df[df["season"] >= MIN_SEASON]
df["month"] = df["time"].apply(
    lambda x: pd.Timestamp(year=x.year, month=x.month, day=1)
)
df = df.merge(df_enso)
```

```python
df["lon_pos"] = df["lon"].apply(lambda x: (x + 360) % 360)
```

```python
df.groupby("phase")["lat"].mean()
```

```python
df.groupby("phase")["lon_pos"].mean()
```

```python
d_threshs = [0, 100, 250]
cat_threshs = [1, 3]

dicts = []
for d_thresh in d_threshs:
    for cat_thresh in cat_threshs:
        dff = df[
            (df["cat"] >= cat_thresh) & (df["vut_distance_km"] <= d_thresh)
        ]
        dicts.append(
            {
                "d_thresh": d_thresh,
                "cat_thresh": cat_thresh,
                "phase": "any",
                "count": dff["sid"].nunique(),
            }
        )
        for phase in ["elnino", "lanina", "neutral"]:
            dff = df[
                (df["cat"] >= cat_thresh)
                & (df["vut_distance_km"] <= d_thresh)
                & (df["phase"] == phase)
            ]
            dicts.append(
                {
                    "d_thresh": d_thresh,
                    "cat_thresh": cat_thresh,
                    "phase": phase,
                    "count": dff["sid"].nunique(),
                }
            )
```

```python
df_counts = pd.DataFrame(dicts)
df_counts["per_year"] = df_counts["count"] / df["season"].nunique()
```

```python
df_counts
```

```python
18 / 7
```

```python
dff = df[
    (df["season"] >= MIN_SEASON)
    & (df["cat"] >= CAT_THRESH)
    & (df["vut_distance_km"] >= D_THRESH)
]
```

```python
total_seasons = df["season"].nunique()
```

```python
total_seasons
```

```python
dff["sid"].nunique()
```

```python
dff
```

```python

```
