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

# IBTrACS

```python
%load_ext jupyter_black
%load_ext autoreload
%autoreload 2
```

```python
import geopandas as gpd

from src.utils import blob
from src.datasources import ibtracs, codab, enso
```

```python
# codab.download_codab_to_blob()
```

```python
# ibtracs.download_ibtracs()
```

```python
# ibtracs.process_all_ibtracs()
```

```python
ibtracs.process_all_ibtracs(wind_provider="usa")
```

```python
ibtracs.process_vut_distances()
```

```python
df = ibtracs.load_vut_distances()
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
df_close = df[df["vut_distance_km"] < 250]
```

```python
df_close
```
