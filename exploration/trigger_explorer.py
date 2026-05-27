import marimo as mo

app = mo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import matplotlib.pyplot as plt
    import ocha_stratus as stratus
    import pandas as pd

    from src.constants import ADM1_AOI_PCODES, PROJECT_PREFIX
    from src.datasources import codab

    return ADM1_AOI_PCODES, PROJECT_PREFIX, codab, mo, pd, plt, stratus


@app.cell
def _(ADM1_AOI_PCODES, PROJECT_PREFIX, codab, mo, pd, stratus):
    with mo.status.spinner(subtitle="Loading data..."):
        _blob_stats = f"{PROJECT_PREFIX}/processed/impact_stats.parquet"
        df_stats = stratus.load_parquet_from_blob(_blob_stats)

        _adm2 = codab.load_codab_from_blob(admin_level=2)
        _aoi_pcodes = _adm2[_adm2["ADM1_PCODE"].isin(ADM1_AOI_PCODES)][
            "ADM2_PCODE"
        ].unique()

        _blob_exp = (
            f"{PROJECT_PREFIX}/processed/ibtracs/adm2_usaradii_exp.parquet"
        )
        _df_exp = stratus.load_parquet_from_blob(_blob_exp)
        _df_exp_aoi = _df_exp[_df_exp["ADM2_PCODE"].isin(_aoi_pcodes)]

        with stratus.get_engine(stage="prod").connect() as _con:
            _df_storms = pd.read_sql(
                "SELECT sid, name, season FROM storms.ibtracs_storms", _con
            )

        _df_exp_aoi = _df_exp_aoi.merge(_df_storms, on="sid", how="left")
        _df_exp_aoi_recent = _df_exp_aoi[_df_exp_aoi["season"] >= 2001]

        _df_exp_sid = (
            _df_exp_aoi_recent.groupby(["sid", "buffer_speed"])["pop_exposed"]
            .sum()
            .reset_index()
            .pivot(columns="buffer_speed", values="pop_exposed", index="sid")
            .reset_index()
        )
        _df_exp_sid.columns.name = None
        _df_exp_sid = _df_exp_sid.rename(
            columns={x: f"exp{x}" for x in [34, 50, 64]}
        )
        _df_exp_sid = _df_exp_sid.fillna(0)

        df = df_stats.merge(_df_exp_sid, on="sid", how="inner")
        df = df[df["season"] >= 2001].reset_index(drop=True)

    total_seasons = 2025 - 2001 + 1
    return df, total_seasons


@app.cell
def _(mo):
    mo.md("# Vanuatu Cyclone Trigger Explorer")


@app.cell
def _(mo):
    mo.md("## Correlations with impact indicators")


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
    _fig_corr


@app.cell
def _(mo):
    mo.md("## Interactive trigger explorer")


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
        step=1000,
        value=10000,
        label="Wind exposure threshold (people)",
        show_value=True,
        disabled=_locked,
    )
    rain_thresh = mo.ui.slider(
        start=0,
        stop=_max_rain,
        step=5,
        value=0,
        label="Rainfall threshold (mm, 2-day)",
        show_value=True,
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
        [wind_knots, wind_thresh, rain_thresh, logic], justify="start", gap=2
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

    _n = int(_df["triggered"].sum())
    _rp = (total_seasons + 1) / _n if _n > 0 else None
    _rp_str = f"{_rp:.1f}" if _rp is not None else "∞"

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
        color="darkorange",
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

    _wind_col = f"Pop. exposed {wind_knots.value}kt wind (AOI)"
    _rain_col = "2-day rainfall (mm)"
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

    mo.vstack(
        [
            mo.hstack(
                [
                    mo.stat(label="Triggered storms", value=str(_n)),
                    mo.stat(label="Target", value="6"),
                    mo.stat(label="Return period", value=f"{_rp_str} seasons"),
                    mo.stat(label="Total seasons", value=str(total_seasons)),
                ],
                justify="start",
            ),
            _fig,
            mo.as_html(_styled),
        ]
    )


@app.cell
def _(mo):
    mo.md("## Optimal trigger combinations (exactly 6 storms)")


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

    _df_opt.set_index("Scenario").style.background_gradient(
        subset=["Total Affected"], cmap="Purples"
    ).format({"Total Affected": "{:,.0f}"})


if __name__ == "__main__":
    app.run()
