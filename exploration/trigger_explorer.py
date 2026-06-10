import marimo as mo

app = mo.App(width="medium")


@app.cell
def _():
    import jinja2  # noqa: F401 — required by pandas.style in Pyodide
    import marimo as mo
    import matplotlib.pyplot as plt
    import pandas as pd

    return mo, pd, plt


@app.cell
def _(mo, pd):
    import importlib
    import sys

    with mo.status.spinner(subtitle="Loading data..."):
        if sys.platform == "emscripten":
            # Pyodide/WASM: mo.notebook_location() returns the page URL.
            # urllib.request is patched by pyodide-http to use XHR, which
            # handles gzip content-encoding. Wrapping in StringIO prevents
            # pandas from seeing the URL and applying its own gzip logic.
            import io
            import urllib.request

            _url = str(mo.notebook_location() / "public" / "trigger_data.csv")
            with urllib.request.urlopen(_url) as _resp:
                df = pd.read_csv(io.StringIO(_resp.read().decode("utf-8")))
            df["cerf"] = df["cerf"].astype(bool)
        else:
            # Local: load live from blob storage and DB.
            # importlib hides these from marimo's AST scanner so the WASM
            # bundle doesn't try to install them in Pyodide.
            _stratus = importlib.import_module("ocha_stratus")
            _constants = importlib.import_module("src.constants")
            _codab = importlib.import_module("src.datasources.codab")

            _AOI_PCODES = _constants.ADM1_AOI_PCODES
            _PREFIX = _constants.PROJECT_PREFIX

            _df_stats = _stratus.load_parquet_from_blob(
                f"{_PREFIX}/processed/impact_stats.parquet"
            )

            _adm2 = _codab.load_codab_from_blob(admin_level=2)
            _aoi_pcodes = _adm2[_adm2["ADM1_PCODE"].isin(_AOI_PCODES)][
                "ADM2_PCODE"
            ].unique()

            _df_exp = _stratus.load_parquet_from_blob(
                f"{_PREFIX}/processed/ibtracs/adm2_usaradii_exp.parquet"
            )
            _df_exp_aoi = _df_exp[_df_exp["ADM2_PCODE"].isin(_aoi_pcodes)]

            with _stratus.get_engine(stage="prod").connect() as _con:
                _df_storms = pd.read_sql(
                    "SELECT sid, name, season FROM storms.ibtracs_storms",
                    _con,
                )

            _df_exp_aoi = _df_exp_aoi.merge(_df_storms, on="sid", how="left")
            _df_exp_aoi = _df_exp_aoi[_df_exp_aoi["season"] >= 2003]

            _df_exp_sid = (
                _df_exp_aoi.groupby(["sid", "buffer_speed"])["pop_exposed"]
                .sum()
                .reset_index()
                .pivot(
                    columns="buffer_speed",
                    values="pop_exposed",
                    index="sid",
                )
                .reset_index()
            )
            _df_exp_sid.columns.name = None
            _df_exp_sid = _df_exp_sid.rename(
                columns={x: f"exp{x}" for x in [34, 50, 64]}
            )
            _df_exp_sid = _df_exp_sid.fillna(0)

            df = _df_stats.merge(_df_exp_sid, on="sid", how="inner")
            df = df[df["season"] >= 2003].reset_index(drop=True)

    # Record covers the 2003–2025 seasons inclusive.
    total_seasons = 2025 - 2003 + 1
    return df, total_seasons


@app.cell
def _(mo):
    mo.md("# Vanuatu Cyclone Trigger Explorer")


@app.cell
def _(df, mo, plt):
    _cols = ["exp34", "exp50", "exp64", "roll2_mean"]
    _labels = [
        "Exp 34 kt (AOI)",
        "Exp 50 kt (AOI)",
        "Exp 64 kt (AOI)",
        "Rainfall 2d",
    ]
    _df_c = df[_cols + ["Total Affected", "cerf"]].copy()
    _df_c["cerf"] = _df_c["cerf"].astype(int)

    _r_ta = [_df_c[c].corr(_df_c["Total Affected"]) for c in _cols]
    _r_cerf = [_df_c[c].corr(_df_c["cerf"]) for c in _cols]

    _fig_corr, (_ax1, _ax2) = plt.subplots(1, 2, figsize=(10, 3.5), dpi=150)

    for _ax, _vals, _title in [
        (_ax1, _r_ta, "Correlation with Total Affected"),
        (_ax2, _r_cerf, "Correlation with CERF Allocation"),
    ]:
        _bar_colors = ["steelblue" if v >= 0 else "tomato" for v in _vals]
        _ax.barh(_labels, _vals, color=_bar_colors)
        _ax.set_xlim(-1, 1)
        _ax.axvline(0, color="k", linewidth=0.8)
        _ax.set_title(_title, fontsize=10)
        _ax.spines["top"].set_visible(False)
        _ax.spines["right"].set_visible(False)
        _ax.tick_params(labelsize=9)

    plt.tight_layout()

    _corr_note = mo.md(
        """
        Pearson correlation of each candidate indicator with **Total Affected**
        (left) and with whether a **CERF allocation** occurred (right). Taller
        bars flag indicators that more closely track historical impact, helping
        decide which to build the trigger on.
        """
    )

    mo.accordion(
        {
            "Correlations with impact indicators": mo.vstack(
                [_corr_note, _fig_corr]
            )
        }
    )


@app.cell
def _(mo):
    mo.md("## Interactive trigger explorer")


@app.cell
def _(mo):
    mo.md(
        """
        **How to use:** Set a wind-exposure and/or rainfall threshold with the
        sliders, choose whether both (**AND**) or either (**OR**) must be met,
        and pick the wind speed. Storms meeting the rule are **triggered**
        (bold labels). Aim for the **Target** number of triggers — staying at
        or below it. We want to trigger for storms that caused the highest
        impact; one way to do this is to try and maximize the **Total
        Affected** output. Red dots = past CERF allocations; dashed lines =
        your thresholds; the shaded region is the trigger zone.
        """
    )


@app.cell
def _(df, mo):
    _locked = mo.cli_args().get("locked") == "true"
    _max_exp = max(
        int(df["exp34"].max()), int(df["exp50"].max()), int(df["exp64"].max())
    )
    _max_rain = int(df["roll2_mean"].max()) + 10

    wind_knots = mo.ui.dropdown(
        options=[34, 50, 64],
        value=64,
        label="Wind speed (knots)",
        disabled=_locked,
    )
    wind_thresh = mo.ui.slider(
        start=0,
        stop=_max_exp,
        step=5000,
        value=100000,
        label=(
            "<span style='color:#8B4513'>"
            "Wind exposure threshold (people)</span>"
        ),
        show_value=True,
        full_width=True,
        disabled=_locked,
    )
    rain_thresh = mo.ui.slider(
        start=0,
        stop=_max_rain,
        step=5,
        value=100,
        label=(
            "<span style='color:#4682b4'>"
            "Rainfall threshold (mm, 2-day)</span>"
        ),
        show_value=True,
        full_width=True,
        disabled=_locked,
    )
    logic = mo.ui.radio(
        options=["AND", "OR"],
        value="AND",
        label="Trigger logic",
        disabled=_locked,
    )
    return logic, rain_thresh, wind_knots, wind_thresh


@app.cell
def _(logic, mo, rain_thresh, wind_knots, wind_thresh):
    mo.hstack(
        [wind_knots, wind_thresh, rain_thresh, logic],
        justify="start",
        gap=2,
        widths=[1, 2, 2, 1],
    )


@app.cell
def _(df, logic, mo, plt, rain_thresh, total_seasons, wind_knots, wind_thresh):
    import matplotlib.colors as _mcolors
    import numpy as _np

    _xcol = f"exp{wind_knots.value}"
    _df = df.copy()

    _wind_trig = _df[_xcol] >= wind_thresh.value
    _rain_trig = _df["roll2_mean"] >= rain_thresh.value
    _df["triggered"] = (
        (_wind_trig & _rain_trig)
        if logic.value == "AND"
        else (_wind_trig | _rain_trig)
    )

    _target = 6
    _n = int(_df["triggered"].sum())
    _rp = (total_seasons + 1) / _n if _n > 0 else None
    _rp_str = f"{_rp:.1f}" if _rp is not None else "∞"
    _ta_trig = int(_df.loc[_df["triggered"], "Total Affected"].fillna(0).sum())

    # Color only indicates CERF; bold indicates triggered
    _point_colors = [
        "crimson" if row["cerf"] else "k" for _, row in _df.iterrows()
    ]

    _max_impact = max(float(_df["Total Affected"].max()), 1.0)
    _bubble_sizes = _df["Total Affected"].fillna(0) / _max_impact * 3000 + 30

    _fig, _ax = plt.subplots(figsize=(8, 6), dpi=150)
    _ax.scatter(
        _df[_xcol],
        _df["roll2_mean"],
        s=_bubble_sizes,
        c=_point_colors,
        alpha=0.4,
        edgecolors="none",
        zorder=2,
    )
    for (_i, _row), _color in zip(_df.iterrows(), _point_colors):
        _ax.annotate(
            str(_row["name"]).capitalize() + "\n" + str(_row["season"]),
            (_row[_xcol], _row["roll2_mean"]),
            ha="center",
            va="center",
            fontsize=6,
            fontweight="bold" if _row["triggered"] else "normal",
            color=_color,
            zorder=3,
            alpha=0.9,
        )
    _ax.axvline(
        wind_thresh.value,
        color="#8B4513",
        linestyle="--",
        linewidth=1,
    )
    _ax.axhline(
        rain_thresh.value,
        color="steelblue",
        linestyle="--",
        linewidth=1,
    )
    _ax.set_xlabel(
        f"Population exposed to {wind_knots.value}-knot wind,"
        " AOI provinces [IBTrACS]"
    )
    _ax.set_ylabel("2-day rainfall, mean over country (mm) [IMERG]")
    _ax.set_title(
        f"Vanuatu: {wind_knots.value}kt wind exposure (AOI) vs. 2-day rainfall"
    )
    _ax.set_xlim(left=0)
    _ax.set_ylim(bottom=0)

    # Shade the trigger zone. For AND it's the upper-right rectangle (both
    # thresholds exceeded); for OR it's the L-shape where either is exceeded.
    _x0, _x1 = _ax.get_xlim()
    _y0, _y1 = _ax.get_ylim()
    _wt, _rt = wind_thresh.value, rain_thresh.value
    _shade = {"color": "gold", "alpha": 0.18, "zorder": 0, "linewidth": 0}
    from matplotlib.patches import Rectangle as _Rectangle

    if logic.value == "AND":
        _ax.add_patch(_Rectangle((_wt, _rt), _x1 - _wt, _y1 - _rt, **_shade))
    else:
        # Right strip + top-left strip = non-overlapping union of the L-shape.
        _ax.add_patch(_Rectangle((_wt, _y0), _x1 - _wt, _y1 - _y0, **_shade))
        _ax.add_patch(_Rectangle((_x0, _rt), _wt - _x0, _y1 - _rt, **_shade))

    # The top-right corner is in the trigger zone under both AND and OR.
    _ax.annotate(
        "Trigger zone",
        (_x1 - (_x1 - _x0) * 0.02, _y1 - (_y1 - _y0) * 0.02),
        ha="right",
        va="top",
        fontsize=9,
        fontweight="bold",
        color="#b8860b",
        zorder=1,
    )
    _ax.set_xlim(_x0, _x1)
    _ax.set_ylim(_y0, _y1)

    _ax.spines["top"].set_visible(False)
    _ax.spines["right"].set_visible(False)
    plt.tight_layout()

    # --- Styled table (following quick_trigger_aoi.md) ---
    def _lighten_cmap(cmap_name, blend=0.3):
        _cmap = plt.get_cmap(cmap_name)
        _colors = _cmap(_np.linspace(0, 1, 256))
        _white = _np.array([1, 1, 1, 1])
        _colors = _colors * (1 - blend) + _white * blend
        return _mcolors.LinearSegmentedColormap.from_list(
            f"{cmap_name}_light", _colors
        )

    _light_oranges = _lighten_cmap("Oranges")
    _light_blues = _lighten_cmap("Blues")

    _wind_col = f"Pop. exposed<br>{wind_knots.value}kt wind<br>(AOI)"
    _rain_col = "2-day rainfall<br>(mm)"
    _trig_col = "Trigger?"
    _cerf_col = "CERF?"
    _impact_col = "Total Affected"

    _df_disp = _df.copy()
    _df_disp["Cyclone"] = (
        _df_disp["name"].fillna("Unnamed").str.capitalize()
        + " "
        + _df_disp["season"].astype(str)
    )
    _df_disp = _df_disp.rename(
        columns={
            _xcol: _wind_col,
            "roll2_mean": _rain_col,
            "Total Affected": _impact_col,
        }
    )
    _df_disp[_trig_col] = _df_disp["triggered"].map({True: "Yes", False: "No"})
    _df_disp[_cerf_col] = _df_disp["cerf"].map({True: "Yes", False: "No"})
    _df_disp = _df_disp.sort_values(
        [_impact_col, _cerf_col, _wind_col, _rain_col],
        ascending=[False, True, False, False],
    )

    def _color_cerf(val):
        return (
            "background-color: crimson; color: white;" if val == "Yes" else ""
        )

    def _color_trig(val):
        return (
            "background-color: #8e44ad; color: white;" if val == "Yes" else ""
        )

    _styled = (
        _df_disp.set_index("Cyclone")[
            [_wind_col, _rain_col, _trig_col, _cerf_col, _impact_col]
        ]
        .style.bar(subset=_impact_col, color="#b8a3e0", props="width: 120px;")
        .background_gradient(subset=_wind_col, cmap=_light_oranges)
        .background_gradient(subset=_rain_col, cmap=_light_blues)
        .map(_color_cerf, subset=_cerf_col)
        .map(_color_trig, subset=_trig_col)
        .format(
            {_impact_col: "{:,.0f}", _wind_col: "{:,.0f}", _rain_col: "{:.0f}"}
        )
    )

    def _card(label, value, caption="", value_color="#111827", muted=False):
        _lc = "#9ca3af" if muted else "#6b7280"
        _vc = "#9ca3af" if muted else value_color
        _cap = (
            f'<div style="font-size:0.7rem;color:{value_color};'
            f'font-weight:600;margin-top:1px;">{caption}</div>'
            if caption
            else ""
        )
        return (
            f'<div style="padding:0.25rem 1.25rem 0.25rem 0;min-width:7rem;">'
            f'<div style="font-size:0.8rem;color:{_lc};">{label}</div>'
            f'<div style="font-size:1.6rem;font-weight:600;color:{_vc};'
            f'line-height:1.2;">{value}</div>{_cap}</div>'
        )

    _trig_too_many = _n > _target
    _readouts = mo.md(
        '<div style="display:flex;flex-wrap:wrap;align-items:flex-start;">'
        + _card("Target", str(_target), muted=True)
        + _card(
            "Triggered storms",
            str(_n),
            caption="Too many" if _trig_too_many else "",
            value_color="#dc2626" if _trig_too_many else "#111827",
        )
        + _card("Return period", f"{_rp_str} seasons")
        + _card("Total Affected (triggered)", f"{_ta_trig:,}")
        + "</div>"
    )

    mo.vstack([_readouts, _fig, mo.as_html(_styled)])


@app.cell
def _(df, mo, pd):
    _TARGET_N = 6
    _exp_vals = {k: df[f"exp{k}"].values for k in [34, 50, 64]}
    _rain_vals = df["roll2_mean"].values
    _impact_vals = df["Total Affected"].values
    _storm_labels = (
        df["name"].fillna("Unnamed").str.capitalize()
        + " "
        + df["season"].astype(str)
    ).values

    _wt_max = max(int(df[f"exp{k}"].max()) for k in [34, 50, 64])
    _rt_max = int(df["roll2_mean"].max()) + 5
    _wt_range = range(0, _wt_max + 1001, 1000)
    _rt_range = range(0, _rt_max + 6, 5)

    def _best_record(scenario, wt_label, rt_label, trig):
        return {
            "Scenario": scenario,
            "Wind thresh": wt_label,
            "Rain thresh": rt_label,
            "Total Affected": int(_impact_vals[trig].sum()),
            "Storms": ", ".join(_storm_labels[trig]),
        }

    _results = []

    # Wind only (no rain condition)
    for _k in [34, 50, 64]:
        _exp = _exp_vals[_k]
        _best_ta, _best = -1, None
        for _wt in _wt_range:
            _trig = _exp >= _wt
            if _trig.sum() == _TARGET_N:
                _ta = int(_impact_vals[_trig].sum())
                if _ta > _best_ta:
                    _best_ta = _ta
                    _best = _best_record(
                        f"Wind only ({_k}kt)", f"{_wt:,}", "—", _trig
                    )
        if _best:
            _results.append(_best)

    # Rain only (no wind condition)
    _best_ta, _best = -1, None
    for _rt in _rt_range:
        _trig = _rain_vals >= _rt
        if _trig.sum() == _TARGET_N:
            _ta = int(_impact_vals[_trig].sum())
            if _ta > _best_ta:
                _best_ta = _ta
                _best = _best_record("Rain only", "—", str(_rt), _trig)
    if _best:
        _results.append(_best)

    # AND / OR combinations
    for _logic in ["AND", "OR"]:
        for _k in [34, 50, 64]:
            _exp = _exp_vals[_k]
            _best_ta, _best = -1, None
            for _wt in _wt_range:
                _wind_trig = _exp >= _wt
                for _rt in _rt_range:
                    _rain_trig = _rain_vals >= _rt
                    _trig = (
                        (_wind_trig & _rain_trig)
                        if _logic == "AND"
                        else (_wind_trig | _rain_trig)
                    )
                    if _trig.sum() == _TARGET_N:
                        _ta = int(_impact_vals[_trig].sum())
                        if _ta > _best_ta:
                            _best_ta = _ta
                            _best = _best_record(
                                f"{_logic} ({_k}kt)",
                                f"{_wt:,}",
                                str(_rt),
                                _trig,
                            )
            if _best:
                _results.append(_best)

    _df_opt = pd.DataFrame(_results)

    _opt_styled = (
        _df_opt.set_index("Scenario")
        .style.background_gradient(subset=["Total Affected"], cmap="Purples")
        .format({"Total Affected": "{:,.0f}"})
    )

    mo.accordion(
        {
            "Optimal trigger combinations (exactly 6 storms)": mo.as_html(
                _opt_styled
            )
        }
    )


if __name__ == "__main__":
    app.run()
