import marimo as mo

app = mo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import matplotlib.pyplot as plt
    import ocha_stratus as stratus
    import pandas as pd
    import plotly.graph_objects as go

    from src.constants import ADM1_AOI_PCODES, CERF_SIDS, PROJECT_PREFIX
    from src.datasources import codab

    return (
        ADM1_AOI_PCODES,
        CERF_SIDS,
        PROJECT_PREFIX,
        codab,
        go,
        mo,
        pd,
        plt,
        stratus,
    )


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
    _max_exp = max(
        int(df["exp34"].max()), int(df["exp50"].max()), int(df["exp64"].max())
    )
    _max_rain = int(df["roll2_mean"].max()) + 10

    wind_knots = mo.ui.dropdown(
        options=[34, 50, 64],
        value=64,
        label="Wind speed (knots)",
    )
    wind_thresh = mo.ui.slider(
        start=0,
        stop=_max_exp,
        step=1000,
        value=0,
        label="Wind exposure threshold (people)",
        show_value=True,
    )
    rain_thresh = mo.ui.slider(
        start=0,
        stop=_max_rain,
        step=5,
        value=0,
        label="Rainfall threshold (mm, 2-day)",
        show_value=True,
    )
    logic = mo.ui.radio(
        options=["AND", "OR"],
        value="OR",
        label="Trigger logic",
    )
    return logic, rain_thresh, wind_knots, wind_thresh


@app.cell
def _(logic, mo, rain_thresh, wind_knots, wind_thresh):
    mo.hstack(
        [wind_knots, wind_thresh, rain_thresh, logic], justify="start", gap=2
    )


@app.cell
def _(df, go, logic, mo, rain_thresh, total_seasons, wind_knots, wind_thresh):
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

    _max_impact = max(float(_df["Total Affected"].max()), 1.0)
    _sizes = (_df["Total Affected"].fillna(0) / _max_impact * 50 + 6).tolist()

    _point_colors = [
        "crimson"
        if row["cerf"]
        else ("#8e44ad" if row["triggered"] else "#aaaaaa")
        for _, row in _df.iterrows()
    ]

    _hover = (
        _df["name"].fillna("Unnamed").str.capitalize()
        + " "
        + _df["season"].astype(str)
        + "<br>Wind exp (AOI): "
        + _df[_xcol].apply(lambda x: f"{x:,.0f} people")
        + "<br>2-day rainfall: "
        + _df["roll2_mean"].apply(lambda x: f"{x:.0f} mm")
        + "<br>Total Affected: "
        + _df["Total Affected"].apply(lambda x: f"{int(x):,}")
        + "<br>CERF: "
        + _df["cerf"].map({True: "Yes", False: "No"})
        + "<br>Triggered: "
        + _df["triggered"].map({True: "✓", False: "✗"})
    )

    _fig = go.Figure()
    _fig.add_trace(
        go.Scatter(
            x=_df[_xcol],
            y=_df["roll2_mean"],
            mode="markers+text",
            marker=dict(
                size=_sizes,
                color=_point_colors,
                opacity=0.65,
                line=dict(width=0),
            ),
            text=(
                _df["name"].fillna("Unnamed").str.capitalize()
                + "<br>"
                + _df["season"].astype(str)
            ).tolist(),
            textposition="middle center",
            textfont=dict(size=6.5, color=_point_colors),
            hovertext=_hover.tolist(),
            hoverinfo="text",
            showlegend=False,
        )
    )

    _fig.add_vline(
        x=wind_thresh.value,
        line_dash="dash",
        line_color="darkorange",
        annotation_text="wind thresh",
        annotation_position="top right",
    )
    _fig.add_hline(
        y=rain_thresh.value,
        line_dash="dash",
        line_color="steelblue",
        annotation_text="rain thresh",
        annotation_position="top right",
    )

    _fig.update_layout(
        title=f"Vanuatu: {wind_knots.value}kt wind exposure (AOI provinces) vs. 2-day rainfall",
        xaxis_title=f"Population exposed to {wind_knots.value}-knot wind, AOI provinces [IBTrACS]",
        yaxis_title="2-day rainfall, mean over country (mm) [IMERG]",
        height=580,
        plot_bgcolor="white",
        xaxis=dict(showgrid=True, gridcolor="#eeeeee", zeroline=True),
        yaxis=dict(showgrid=True, gridcolor="#eeeeee", zeroline=True),
        margin=dict(t=60, b=60, l=80, r=40),
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
        ]
    )


if __name__ == "__main__":
    app.run()
