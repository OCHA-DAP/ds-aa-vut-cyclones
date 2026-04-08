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

# CODAB

```python
%load_ext jupyter_black
%load_ext autoreload
%autoreload 2
```

```python
import matplotlib.pyplot as plt

from src.datasources import codab
```

```python
adm0 = codab.load_codab_from_blob(admin_level=0)
```

```python
adm0.plot()
```

```python
d_thresh = 250
```

```python
adm0_buffer = adm0.to_crs(3832).buffer(d_thresh * 1000).to_crs(4326)
```

```python
fig, ax = plt.subplots(dpi=200, figsize=(7, 7))
adm0_buffer.plot(ax=ax, alpha=0.1, color="crimson")
adm0.plot(ax=ax, color="k")
ax.axis("off")
ax.set_title(f"Vanuatu with {d_thresh}km buffer")
```

```python
d_thresh = 10
```

```python
adm0_buffer = adm0.to_crs(3832).buffer(d_thresh * 1000).to_crs(4326)
```

```python
fig, ax = plt.subplots(dpi=200, figsize=(7, 7))
adm0_buffer.plot(ax=ax, alpha=0.1, color="crimson")
adm0.plot(ax=ax, color="k")
ax.axis("off")
ax.set_title(f"Vanuatu with {d_thresh}km buffer")
```

```python

```
