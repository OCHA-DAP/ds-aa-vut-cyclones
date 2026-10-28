"""Monitoring email plots, copied from pa-aa-fji-storms (src/plotting.py).

Changed for Vanuatu: area councils (ADM2) instead of tikinas (ADM3), the
15,000-people threshold, and thermometer labels spaced out automatically
(Judy/Kevin and Harold/Pam sit close together).
"""

from __future__ import annotations

import base64
import io
import math
import os
import re
import warnings

import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import to_hex, to_rgb
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Patch

from src.constants import EXP_THRESHOLD_64_KNOTS, FJI_CRS

ID_COL = "ADM2_PCODE"
NAME_COL = "ADM2_EN"

# -------------------------------
# Helpers
# -------------------------------


def _to_equal_area(
    gdf: gpd.GeoDataFrame, crs_equal_area: str = "EPSG:6933"
) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError(
            "GeoDataFrame has no CRS; set it with gdf.set_crs(...)"
        )
    return gdf.to_crs(crs_equal_area)


def _radii_from_population(
    pop: np.ndarray, area_per_person: float
) -> np.ndarray:
    areas = np.maximum(pop, 0.0) * float(area_per_person)
    return np.sqrt(areas / math.pi)


def _resolve_overlaps(
    centers: np.ndarray,
    radii: np.ndarray,
    home: np.ndarray,
    *,
    k_spring: float = 1e-3,
    k_repulse: float = 1.0,
    friction: float = 0.85,
    max_step: float = 5_000.0,
    iters: int = 400,
) -> np.ndarray:
    N = centers.shape[0]
    pos = centers.astype(float).copy()
    vel = np.zeros_like(pos)
    for _ in range(iters):
        force = -k_spring * (pos - home)
        for i in range(N):
            for j in range(i + 1, N):
                dx, dy = pos[j] - pos[i]
                dist = math.hypot(dx, dy)
                min_d = radii[i] + radii[j]
                if dist < 1e-9:
                    dist = 1e-9
                    ux, uy = 1.0, 0.0
                else:
                    ux, uy = dx / dist, dy / dist
                overlap = min_d - dist
                if overlap > 0:
                    f = k_repulse * overlap
                    force[i] -= f * np.array([ux, uy])
                    force[j] += f * np.array([ux, uy])
        vel = vel + force
        vel *= friction
        step = np.linalg.norm(vel, axis=1, keepdims=True)
        clip = np.where(step > max_step, max_step / (step + 1e-12), 1.0)
        vel *= clip
        pos += vel
    return pos


# -------------------------------
# Public API
# -------------------------------


def build_circle_template(
    gdf_admin: gpd.GeoDataFrame,
    *,
    id_col: str = ID_COL,
    pop_col: str = "pop_total",
    area_per_person: float = 2_000.0,
    crs_equal_area: str = "EPSG:6933",
    iters: int = 400,
    k_spring: float = 1e-3,
    k_repulse: float = 1.0,
) -> pd.DataFrame:
    """
    Returns DataFrame with columns: [id_col, 'x','y','radius_total',
    'pop_total'] (+ any passthrough cols you merge later).
    Coordinates are in the equal-area CRS.
    """
    g = _to_equal_area(
        gdf_admin[[id_col, pop_col, "geometry"]], crs_equal_area=crs_equal_area
    ).copy()
    g = g.dropna(subset=[id_col, pop_col])

    home = np.vstack(
        g.geometry.centroid.apply(lambda p: (p.x, p.y)).to_numpy()
    )
    pop = g[pop_col].to_numpy(float)
    radii = _radii_from_population(pop, area_per_person=area_per_person)

    final = _resolve_overlaps(
        centers=home.copy(),
        radii=radii,
        home=home,
        k_spring=k_spring,
        k_repulse=k_repulse,
        iters=iters,
    )

    out = pd.DataFrame(
        {
            id_col: g[id_col].to_numpy(),
            "x": final[:, 0],
            "y": final[:, 1],
            "radius_total": radii,
            "pop_total": pop,
        }
    )
    return out


def plot_template_circles(
    template_df: pd.DataFrame,
    *,
    id_col: str = ID_COL,
    label_col: str = None,  # e.g., 'name' if you've merged it into template_df
    min_font: float = 6,
    max_font: float = 16,
    fig_size: tuple = (10, 8),
    bounds: tuple | None = None,  # (xmin, xmax, ymin, ymax)
    outline_color: str = "black",
    outline_alpha: float = 1.0,
    outline_lw: float = 0.2,
    dpi: int = 200,
    ax=None,
):
    """
    Plot ONLY the outer, unfilled circles from the template (for debugging sizing/layout).
    Labels (if label_col provided) are centered and scaled by total population.
    """
    t = template_df.copy()
    if ax is not None:
        fig = ax.figure
    else:
        fig, ax = plt.subplots(figsize=fig_size, dpi=dpi)

    # label font scaling (by total pop)
    p = t["pop_total"].to_numpy() if "pop_total" in t.columns else np.array([])
    if p.size:
        pmin, pmax = float(p.min()), float(p.max())
        denom = (pmax - pmin) if pmax > pmin else 1.0
        font_sizes = min_font + (p - pmin) / denom * (max_font - min_font)
    else:
        font_sizes = np.array([])

    for i, row in t.iterrows():
        x, y = float(row["x"]), float(row["y"])
        r_total = float(row["radius_total"])
        ax.add_patch(
            Circle(
                (x, y),
                r_total,
                fill=False,
                lw=outline_lw,
                alpha=outline_alpha,
                edgecolor=outline_color,
            )
        )

        if label_col and label_col in t.columns:
            fs = (
                float(font_sizes[i])
                if i in t.index and i < len(font_sizes)
                else min_font
            )
            ax.text(
                x,
                y,
                str(row[label_col]),
                ha="center",
                va="center",
                fontsize=fs,
            )

    ax.set_aspect("equal")

    # bounds
    if bounds is not None:
        xmin, xmax, ymin, ymax = bounds
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
    else:
        cx, cy = t["x"].to_numpy(), t["y"].to_numpy()
        rt = t["radius_total"].to_numpy()
        if len(cx):
            xmin = np.min(cx - rt)
            xmax = np.max(cx + rt)
            ymin = np.min(cy - rt)
            ymax = np.max(cy + rt)
            pad = 0.03 * max(xmax - xmin, ymax - ymin)
            ax.set_xlim(xmin - pad, xmax + pad)
            ax.set_ylim(ymin - pad, ymax + pad)

    return fig, ax


def lighten(color, amount=0.5):
    """Blend color toward white by `amount` (0=no change, 1=white)."""
    c = np.array(to_rgb(color))
    return to_hex(1 - amount * (1 - c))


def plot_bullseye_exposures(
    template_df: pd.DataFrame,
    exposures_df: pd.DataFrame,
    *,
    id_col: str = ID_COL,
    pop_exposed_col: str = "pop_exposed",
    speed_col: str = "buffer_speed",
    speeds_order=(
        34,
        50,
        64,
    ),  # will be drawn largest→smallest (34, then 50, then 64)
    colors={34: "gold", 50: "crimson", 64: "indigo"},
    # face_alpha: float = 0.5,
    enforce_monotonic: bool = True,  # enforce r64 ≤ r50 ≤ r34
    # template-draw options:
    draw_template_first: bool = True,
    label_col: str | None = None,
    min_font: float = 6,
    max_font: float = 16,
    fig_size: tuple = (10, 8),
    bounds: tuple | None = None,
    outline_color: str = "black",
    outline_alpha: float = 1.0,
    outline_lw: float = 0.2,
    dpi: int = 200,
    ax=None,
    legend_loc: str = "best",
    legend_anchor: tuple | None = None,
):
    """
    Draws (optionally) the template empty circles first, then concentric filled disks for exposures.
    Inner radii per admin:
        r_speed = r_total * sqrt(pop_exposed / pop_total), clipped to r_total.
    """
    # Prep exposures wide table
    exp = exposures_df[[id_col, speed_col, pop_exposed_col]].copy()
    exp = exp.groupby([id_col, speed_col], as_index=False)[
        pop_exposed_col
    ].sum()
    exp_wide = exp.pivot(
        index=id_col, columns=speed_col, values=pop_exposed_col
    ).fillna(0.0)

    # Merge with template
    t = template_df.copy()
    t = t.merge(exp_wide, left_on=id_col, right_index=True, how="left").fillna(
        0.0
    )

    # Start figure (and optionally draw template)
    colors_pale = {s: lighten(colors[s]) for s in colors}
    if draw_template_first:
        fig, ax = plot_template_circles(
            t,
            id_col=id_col,
            label_col=label_col,
            min_font=min_font,
            max_font=max_font,
            fig_size=fig_size,
            bounds=bounds,
            outline_color=outline_color,
            outline_alpha=outline_alpha,
            outline_lw=outline_lw,
            dpi=dpi,
            ax=ax,
        )
    else:
        fig, ax = plt.subplots(figsize=fig_size, dpi=dpi)

    # Draw exposures: largest radius first so smaller ones are visible on top
    for _, row in t.iterrows():
        x, y = float(row["x"]), float(row["y"])
        r_total = float(row["radius_total"])
        pop_total = max(float(row["pop_total"]), 1e-12)

        # radii by speed
        r = {}
        for spd in speeds_order:
            pop_exp = float(row.get(spd, 0.0))
            rs = r_total * math.sqrt(max(pop_exp, 0.0) / pop_total)
            r[spd] = min(rs, r_total)

        # enforce r64 ≤ r50 ≤ r34 (if those speeds are present)
        if enforce_monotonic and {34, 50, 64}.issubset(set(speeds_order)):
            r34 = min(r.get(34, 0.0), r_total)
            r50 = min(r.get(50, 0.0), r34)
            r64 = min(r.get(64, 0.0), r50)
            r.update({34: r34, 50: r50, 64: r64})

        for spd in sorted(speeds_order):  # 34 first, then 50, then 64
            rs = r.get(spd, 0.0)
            if rs > 0:
                ax.add_patch(
                    Circle(
                        (x, y),
                        rs,
                        color=colors_pale.get(spd, "gray"),
                        # alpha=face_alpha,
                        lw=0,
                    )
                )

    # If we didn’t draw template first, set bounds now
    if not draw_template_first:
        if bounds is not None:
            xmin, xmax, ymin, ymax = bounds
            ax.set_xlim(xmin, xmax)
            ax.set_ylim(ymin, ymax)
        else:
            cx, cy = t["x"].to_numpy(), t["y"].to_numpy()
            rt = t["radius_total"].to_numpy()
            if len(cx):
                xmin = np.min(cx - rt)
                xmax = np.max(cx + rt)
                ymin = np.min(cy - rt)
                ymax = np.max(cy + rt)
                pad = 0.03 * max(xmax - xmin, ymax - ymin)
                ax.set_xlim(xmin - pad, xmax + pad)
                ax.set_ylim(ymin - pad, ymax + pad)

    # --- add legend for bullseye colors ---
    legend_patches = [
        mpatches.Patch(
            facecolor="white", edgecolor="gainsboro", label="< 34 kt"
        ),
        mpatches.Patch(facecolor=colors_pale[34], label="≥ 34 kt"),
        mpatches.Patch(facecolor=colors_pale[50], label="≥ 50 kt"),
        mpatches.Patch(facecolor=colors_pale[64], label="≥ 64 kt"),
    ]
    ax.legend(
        handles=legend_patches,
        title="Population exposed\nto wind speed (circle size\nproportional to population)",
        frameon=True,
        fontsize=7,
        title_fontsize=8,
        loc=legend_loc,
        bbox_to_anchor=legend_anchor,
    )

    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def wrap_text(text, max_len=40, break_anywhere=False):
    """
    Wrap text to ~max_len chars per line.

    - break_anywhere=False: break only at spaces or dashes; preserves spacing/dashes,
      removes trailing spaces when breaking.
    - break_anywhere=True : can break mid-word. If a mid-word break is needed,
      split roughly halfway through the word and append a hyphen at the end of the line.
    """
    if not break_anywhere:
        # Soft wrap (only at spaces or dashes)
        tokens = re.findall(r"\S+-|\S+|[-]", text)
        lines, current = [], ""

        for token in tokens:
            add_space = current != "" and not current.endswith("-")
            if len(current) + (1 if add_space else 0) + len(token) > max_len:
                if current:
                    lines.append(
                        current.rstrip()
                    )  # <-- remove trailing spaces
                current = token
            else:
                if add_space:
                    current += " "
                current += token

        if current:
            lines.append(current.rstrip())

        return "\n".join(lines)

    # Hard wrap with smart mid-word hyphenation
    tokens = list(re.finditer(r"\w+|\W+", text))
    lines, current = [], ""

    def flush():
        nonlocal current
        lines.append(
            current.rstrip()
        )  # <-- ensure no trailing space before newline
        current = ""

    for m in tokens:
        tok = m.group(0)
        is_word = tok.isalnum() or re.fullmatch(r"\w+", tok) is not None

        while tok:
            remaining = max_len - len(current)
            if remaining <= 0:
                flush()
                remaining = max_len

            if len(tok) <= remaining:
                current += tok
                tok = ""
            else:
                if is_word:
                    # Split inside word, roughly halfway through it
                    half = max(1, len(tok) // 2)
                    split_at = half if half <= remaining else remaining
                    piece = tok[:split_at]
                    if split_at < len(tok):
                        # Add hyphen if word continues
                        if (
                            len(current) + len(piece) + 1 <= max_len
                            or len(current) == 0
                        ):
                            current += piece + "-"
                        else:
                            flush()
                            continue
                        tok = tok[split_at:]
                        flush()
                    else:
                        current += piece
                        tok = tok[split_at:]
                else:
                    # Non-word: break cleanly
                    piece = tok[:remaining]
                    current += piece
                    tok = tok[remaining:]
                    flush()

    if current:
        lines.append(current.rstrip())

    return "\n".join(lines).removeprefix("\n")


# observed swaths: the forecast colours, faded (as in ds-storms-alerts)
OBSERVED_ALPHA = 0.3


def plot_wind_buffers(gdf_adm, gdf_buffers, ax=None, gdf_observed=None):
    warnings.filterwarnings("ignore", "GeoSeries.notna", UserWarning)
    if ax is not None:
        fig = ax.figure
    else:
        fig, ax = plt.subplots(dpi=200, figsize=(10, 8))
    colors = {34: "gold", 50: "crimson", 64: "indigo"}
    colors_pale = {s: lighten(colors[s]) for s in colors}

    gdf_adm.to_crs(FJI_CRS).boundary.plot(ax=ax, color="black", linewidth=0.5)
    xlims, ylims = ax.get_xlim(), ax.get_ylim()

    ax.axis("off")
    if gdf_observed is not None and not gdf_observed.empty:
        for speed, color in colors.items():
            obs = gdf_observed[gdf_observed["buffer_speed"] == speed]
            obs = obs[~obs.geometry.is_empty & obs.geometry.notna()]
            if not obs.empty:
                obs.plot(
                    ax=ax,
                    color=color,
                    alpha=OBSERVED_ALPHA,
                    edgecolor="none",
                    aspect="equal",
                    zorder=0.5,
                )
    for speed, color in colors_pale.items():
        gdf_speed = gdf_buffers[gdf_buffers["buffer_speed"] == speed]
        gdf_speed = gdf_speed[
            ~gdf_speed.geometry.is_empty
            & gdf_speed.geometry.notna()
            & gdf_speed.is_valid
        ]

        if gdf_speed.empty:
            continue

        gdf_speed.plot(ax=ax, color=color, aspect="equal")

    legend_patches = [
        mpatches.Patch(
            facecolor="white", edgecolor="gainsboro", label="< 34 kt"
        ),
        mpatches.Patch(facecolor=colors_pale[34], label="≥ 34 kt"),
        mpatches.Patch(facecolor=colors_pale[50], label="≥ 50 kt"),
        mpatches.Patch(facecolor=colors_pale[64], label="≥ 64 kt"),
    ]
    if gdf_observed is not None and not gdf_observed.empty:
        legend_patches.append(
            mpatches.Patch(
                facecolor=colors[64],
                alpha=OBSERVED_ALPHA,
                label="faded: observed",
            )
        )
    ax.legend(
        handles=legend_patches,
        title="Wind speed",
        frameon=True,
        loc="upper left",
        fontsize=7,
        title_fontsize=8,
    )

    ax.set_xlim(xlims)
    ax.set_ylim(ylims)

    return fig, ax


def plot_thermometer(
    main_value: float,
    df_stats: pd.DataFrame,
    low_bound: float = None,
    high_bound: float = None,
    trigger_threshold: float = EXP_THRESHOLD_64_KNOTS,
    cyclone_name: str = "",
    forecast_display_str: str = "",
    buffer_speed: int = 64,
    max_value: float = 200_000,
    title: str = "64 knot exposure",
):
    if buffer_speed == 64:
        df_stats_major = df_stats[
            df_stats["exp_64"] >= trigger_threshold
        ].copy()
    else:
        df_stats_major = df_stats.copy()

    fig, ax = plt.subplots(figsize=(3, 6))

    width = 1

    ax.bar(x=0, height=max_value, color="gainsboro", width=width, alpha=0.5)
    ax.hlines(
        y=0, xmin=-width / 2 - 0.1, xmax=width / 2 + 0.1, color="black", lw=2
    )
    ax.annotate(
        f"{max_value:,.0f}\npeople",
        (-width / 2 - 0.05, max_value),
        ha="right",
        va="center",
        fontsize=6,
        fontstyle="italic",
        color="grey",
    )
    ax.hlines(
        y=max_value, xmin=-width / 2, xmax=width / 2, color="grey", lw=0.5
    )

    for value, label, is_main in [
        (low_bound, "Min. reasonable", False),
        (main_value, "Most likely", True),
        (high_bound, "Max. reasonable", False),
    ]:
        # don't plot the high or low bound if they are 0
        if not value > 0 and not is_main:
            continue
        fontweight = "bold" if is_main else "normal"
        plot_value = min(value, max_value)
        label_value = plot_value
        gap = 0.07 * max_value
        if not is_main and value == low_bound and main_value - low_bound < gap:
            label_value = min(value, max_value) - gap
        if (
            not is_main
            and value == high_bound
            and high_bound - main_value < gap
        ):
            label_value = min(value, max_value) + gap
        full_label = f"{label}:\n{round(value, -2):,.0f} people"
        if value > max_value:
            full_label = "↑ " + full_label
        ax.bar(x=0, height=plot_value, alpha=0.3, color="indigo", width=width)
        ax.annotate(
            full_label,
            (width / 2 + 0.6, label_value),
            ha="left",
            va="center",
            color="indigo",
            fontweight=fontweight,
        )
        if not value > max_value:
            ax.hlines(
                y=plot_value,
                xmin=-width / 2,
                xmax=width / 2 + 0.5,
                color="indigo",
            )

    # Trigger threshold line
    ax.hlines(
        y=trigger_threshold,
        xmin=-width / 2 - 1.7,
        xmax=width / 2,
        color="darkorange",
    )
    if buffer_speed == 64:
        ax.text(
            -width / 2 - 1.7,
            trigger_threshold,
            f"Trig. threshold:\n{trigger_threshold:,.0f} people",
            va="center",
            ha="right",
            fontsize=8,
            color="darkorange",
        )

    # Plot historical values to the left, labels spaced so they don't overlap
    df_stats_major = df_stats_major.sort_values(f"exp_{buffer_speed}")
    label_ys = _spread(
        df_stats_major[f"exp_{buffer_speed}"].clip(upper=max_value).tolist(),
        min_gap=0.075 * max_value,
    )
    for (_, row), ylabel in zip(df_stats_major.iterrows(), label_ys):
        y = row[f"exp_{buffer_speed}"]
        name_season = row["name_season"]
        is_cerf = row["cerf"]
        color = "crimson" if is_cerf else "black"
        label = f"{name_season}:\n{round(y, -2):,.0f} people"

        if y <= max_value:
            ax.hlines(
                y=y,
                xmin=-width / 2,
                xmax=width / 2,
                color=color,
                lw=0.5,
                # alpha=0.5,
            )
            ax.text(
                -width / 2 - 0.1,
                ylabel,
                label,
                va="center",
                ha="right",
                fontsize=8,
                color=color,
                zorder=3,
                fontstyle="italic",
            )
        else:
            ax.annotate(
                f"↑ {label}",
                xy=(-width / 2 - 1, ylabel),
                va="center",
                ha="right",
                fontsize=8,
                color=color,
                fontstyle="italic",
            )

    # Final plot tweaks
    ax.set_xlim(-2, 2)
    ax.set_ylim(0, max_value * 1.05)
    ax.set_title(
        f"{title}:\n{cyclone_name} forecast issued\n{forecast_display_str}",
        loc="center",
        fontsize=12,
    )
    ax.axis("off")
    ax.tick_params(axis="x", which="both", bottom=False, labelbottom=False)
    ax.grid(False)
    return fig, ax


def _spread(values: list[float], min_gap: float) -> list[float]:
    """Label positions near ``values`` (ascending), at least ``min_gap``
    apart: pushed up, then the group re-centred on the values."""
    out = []
    for v in values:
        out.append(max(v, out[-1] + min_gap) if out else v)
    if out:
        shift = (sum(out) - sum(values)) / len(out)
        out = [max(o - shift, values[0]) for o in out]
        for i in range(1, len(out)):
            out[i] = max(out[i], out[i - 1] + min_gap)
    return out


def fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    return img_base64


def plot_bubbles_and_swaths(
    gdf_mostlikely_buffers: gpd.GeoDataFrame,
    gdf_worst_buffers: gpd.GeoDataFrame,
    gdf_best_buffers: gpd.GeoDataFrame,
    gdf_adm_swath_plot: gpd.GeoDataFrame,
    df_adm_template: pd.DataFrame,
    gdf_adm: gpd.GeoDataFrame,
    df_exp_adm: pd.DataFrame,
    cyclone_name: str = "",
    forecast_display_str: str = "",
    forecast_id: str = "",
    save_local: bool = False,
    gdf_tracks_plot: gpd.GeoDataFrame = None,
    uncertainty_cone=None,
    forecast_label: str = "",
    id_col: str = ID_COL,
    name_col: str = NAME_COL,
    file_tag: str = "adm2",
    label_max_len: int = 9,
    label_font: tuple = (4, 20),
    legend_loc: str = "best",
    legend_anchor: tuple | None = None,
    gdf_observed_buffers: gpd.GeoDataFrame | None = None,
    gdf_observed_track: gpd.GeoDataFrame | None = None,
    swath_note: str = "",
):
    fig, axes = plt.subplots(2, 3, figsize=(18, 12))

    gdf_adm = gdf_adm.copy()
    gdf_adm["adm_label"] = gdf_adm[name_col].apply(
        wrap_text, max_len=label_max_len, break_anywhere=False
    )

    row_specs = [
        ("middle", "Most likely track", gdf_mostlikely_buffers),
        (
            "worst",
            "Upper bound exposure\n(worst case scenario)",
            gdf_worst_buffers,
        ),
        (
            "best",
            "Lower bound exposure\n(best case scenario)",
            gdf_best_buffers,
        ),
    ]

    # ---- Plotting loop ----
    for col, (limit, title_str, gdf_buffers) in enumerate(row_specs):
        top_ax = axes[0, col]
        bottom_ax = axes[1, col]

        # Wind swaths
        plot_wind_buffers(
            gdf_adm_swath_plot,
            gdf_buffers,
            ax=top_ax,
            gdf_observed=gdf_observed_buffers,
        )

        # Track and uncertainty cone
        _track = (
            gdf_tracks_plot[col]
            if isinstance(gdf_tracks_plot, list)
            else gdf_tracks_plot
        )
        has_obs = gdf_observed_track is not None and not (
            gdf_observed_track.empty
        )
        if has_obs:
            # observed: solid, as in ds-storms-alerts
            top_ax.plot(
                gdf_observed_track.geometry.x.values,
                gdf_observed_track.geometry.y.values,
                color="#222222",
                linewidth=1.5,
                zorder=9,
            )
            gdf_observed_track.plot(
                ax=top_ax, color="#222222", markersize=12, zorder=10
            )
        if _track is not None and uncertainty_cone is not None:
            # forecast: dashed, with its 6-hourly points
            xs = _track.geometry.x.values
            ys = _track.geometry.y.values
            top_ax.plot(
                xs,
                ys,
                color="black",
                linewidth=1.5,
                linestyle=(0, (2.4, 1.4)),
                zorder=9,
            )
            _track.plot(
                ax=top_ax, color="black", marker="D", markersize=10, zorder=10
            )
            gpd.GeoSeries([uncertainty_cone], crs=gdf_adm.crs).plot(
                ax=top_ax,
                facecolor="none",
                edgecolor="grey",
                linewidth=1.5,
                linestyle=":",
                zorder=10,
            )
            existing_legend = top_ax.get_legend()
            if existing_legend is not None:
                top_ax.add_artist(existing_legend)
            col_label = (
                forecast_label[col]
                if isinstance(forecast_label, list)
                else forecast_label
            )
            track_handle = Line2D(
                [0],
                [0],
                color="black",
                linewidth=1.5,
                linestyle=(0, (2.4, 1.4)),
                label=col_label,
            )
            cone_handle = Patch(
                facecolor="none",
                edgecolor="grey",
                linewidth=1.5,
                linestyle=":",
                label="Uncertainty cone",
            )
            handles = [track_handle, cone_handle]
            if has_obs:
                handles.insert(
                    0,
                    Line2D(
                        [0],
                        [0],
                        color="#222222",
                        linewidth=1.5,
                        label="Observed track",
                    ),
                )
            legend_track = top_ax.legend(
                handles=handles,
                loc="upper right",
                fontsize=7,
                frameon=True,
            )
            top_ax.add_artist(legend_track)

        # Column titles
        if col == 0:
            title_color = "black"
            title_weight = "bold"
        elif col == 1:
            title_color = "red"
            title_weight = "normal"
        else:
            title_color = "green"
            title_weight = "normal"

        top_ax.set_title(
            title_str,
            fontsize=20,
            fontweight=title_weight,
            color=title_color,
            pad=6,
        )

        # Population exposure
        plot_bullseye_exposures(
            df_adm_template.merge(gdf_adm[[id_col, "adm_label"]]),
            df_exp_adm[df_exp_adm["limit"] == limit],
            id_col=id_col,
            label_col="adm_label",
            min_font=label_font[0],
            max_font=label_font[1],
            legend_loc=legend_loc,
            legend_anchor=legend_anchor,
            ax=bottom_ax,
        )

    # ---- Row labels (left side) ----
    for row_idx, row_label in enumerate(
        ["Wind swaths", "Population exposure"]
    ):
        axes[row_idx, 0].text(
            -0.02,
            0.5,
            row_label,
            fontsize=18,
            va="center",
            ha="right",
            rotation=90,
            transform=axes[row_idx, 0].transAxes,
        )

    # ---- Main title ----
    fig.suptitle(
        f"{cyclone_name}: forecast issued {forecast_display_str}",
        fontsize=22,
        fontweight="bold",
        y=1,
    )
    if swath_note:
        fig.text(0.5, 0.965, swath_note, ha="center", fontsize=13)

    # ---- Layout ----
    fig.tight_layout(rect=[0, 0, 1, 1])

    if save_local:
        if not forecast_id:
            forecast_id = "test"
        filepath = f"temp/{forecast_id}_{file_tag}_exposure.pdf"
        if not os.path.exists("temp/"):
            os.makedirs("temp/", exist_ok=True)
        fig.savefig(
            filepath,
            format="pdf",
            bbox_inches="tight",
        )
    else:
        filepath = None
    return fig, axes, filepath
