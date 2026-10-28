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
import matplotlib.patheffects as path_effects
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
    show_legend: bool = True,
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
    if show_legend:
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


# --- Map style, from ds-storms-alerts (src/plots.py) -----------------------
OCEAN = "#e3ecf0"  # pale blue-grey, so sea reads as sea
LAND = "#f7f6f2"  # warm off-white
COAST = "#a3aeb4"
INK = "#1f2324"
INK_2 = "#5e6a6b"
PANEL = "#ffffff"
GREY_EDGE = "#9db1b3"
# One step of the HDX status ramp per wind threshold, amber to deep red
WIND_COLORS = {34: "#d48f2a", 50: "#d06a5e", 64: "#9d372b"}
# swath layers, drawn in this order for each wind speed: what was observed,
# the rest of the forecast, the trigger window (cut so they don't overlap)
LAYER_ALPHA = {"observed": 0.2, "other": 0.34, "window": 0.6}
LEGEND_EDGE = "#c5cdd2"
TRACK_CASING = [path_effects.withStroke(linewidth=3.4, foreground=PANEL)]
LABEL_HALO = [path_effects.withStroke(linewidth=2.5, foreground=PANEL)]
OBS_TRACK = dict(color="#222222", linewidth=2)
# short dash, so it reads as dashed in the legend's short sample too
FCAST_TRACK = dict(color="#444444", linewidth=2, linestyle=(0, (2.4, 1.4)))
CONE = dict(
    facecolor="none", edgecolor="#7a8a8c", linewidth=1.2, linestyle=":"
)
SPEEDS = (34, 50, 64)
MAP_MIN_ASPECT = 0.8  # width / height of a swath map


def plot_wind_buffers(gdf_adm, layers: dict, ax=None):
    """One swath map: land on a pale sea, then for each wind speed (widest
    first) the observed, other-forecast and trigger-window swaths, which
    are cut so they don't overlap; coastlines on top so islands read
    through. No legend: plot_bubbles_and_swaths draws it under the maps."""
    warnings.filterwarnings("ignore", "GeoSeries.notna", UserWarning)
    if ax is not None:
        fig = ax.figure
    else:
        fig, ax = plt.subplots(dpi=200, figsize=(10, 8))
    adm = gdf_adm.to_crs(FJI_CRS)
    minx, miny, maxx, maxy = adm.total_bounds
    padx, pady = (maxx - minx) * 0.06, (maxy - miny) * 0.04
    ax.set_facecolor(OCEAN)
    adm.plot(ax=ax, facecolor=LAND, edgecolor="none", zorder=1)
    for i, speed in enumerate(SPEEDS):
        for j, name in enumerate(("observed", "other", "window")):
            gdf = layers.get(name)
            if gdf is None or gdf.empty:
                continue
            g = gdf[gdf["buffer_speed"] == speed]
            g = g[~g.geometry.is_empty & g.geometry.notna()]
            if not g.empty:
                g.plot(
                    ax=ax,
                    color=WIND_COLORS[speed],
                    alpha=LAYER_ALPHA[name],
                    edgecolor="none",
                    zorder=2 + i * 0.1 + j * 0.01,
                )
    adm.boundary.plot(ax=ax, color=COAST, linewidth=0.5, zorder=3)
    # Vanuatu is tall and thin: widen the view (more sea either side, and
    # the swaths east and west of the islands) to at least MAP_MIN_ASPECT
    x0, x1 = minx - padx, maxx + padx
    y0, y1 = miny - pady, maxy + pady
    min_width = MAP_MIN_ASPECT * (y1 - y0)
    if x1 - x0 < min_width:
        mid = (x0 + x1) / 2
        x0, x1 = mid - min_width / 2, mid + min_width / 2
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    return fig, ax


def draw_track(ax, track, kind: str, start=None, label_hours: bool = False):
    """A track line with its fix markers (observed: solid, dots; forecast:
    dashed, diamonds), white-cased. ``start`` (x, y) bridges the forecast
    line from the last observed position, as in ds-storms-alerts."""
    style = OBS_TRACK if kind == "observed" else FCAST_TRACK
    xs, ys = list(track.geometry.x), list(track.geometry.y)
    if start is not None:
        xs, ys = [start[0]] + xs, [start[1]] + ys
    ax.plot(xs, ys, **style, zorder=5, path_effects=TRACK_CASING)
    ax.scatter(
        track.geometry.x,
        track.geometry.y,
        color=style["color"],
        s=15 if kind == "observed" else 18,
        marker="o" if kind == "observed" else "D",
        edgecolors=PANEL,
        linewidths=0.6,
        zorder=6,
    )
    if label_hours and "leadtime" in track.columns:
        for x, y, lt in zip(
            track.geometry.x, track.geometry.y, track["leadtime"]
        ):
            if lt > 0 and lt % 24 == 0:
                ax.annotate(
                    f"{lt:.0f} h",
                    (x, y),
                    xytext=(7, 0),
                    textcoords="offset points",
                    fontsize=9,
                    color=INK_2,
                    va="center",
                    zorder=7,
                    path_effects=LABEL_HALO,
                )


# boxed keys (sizes in inches)
KEY_PAD, KEY_TITLE_H, KEY_HEAD_H, KEY_ROW_H = 0.16, 0.3, 0.26, 0.27
KEY_LABEL_W, KEY_COL_W, KEY_SW_W, KEY_SW_H = 2.9, 0.82, 0.5, 0.17
KEY_FONTSIZE = 11.5


def swath_key_width() -> float:
    return 2 * KEY_PAD + KEY_LABEL_W + KEY_COL_W * len(SPEEDS)


def swath_key(fig, x_in: float, top_in: float, rows: list) -> None:
    """Boxed key for the swath layers: a row per layer, a swatch per wind
    speed. Placed by its top-left corner, in inches from the figure's
    bottom-left."""
    fw, fh = fig.get_size_inches()
    w = swath_key_width()
    h = 2 * KEY_PAD + KEY_TITLE_H + KEY_HEAD_H + KEY_ROW_H * len(rows)
    ax = fig.add_axes([x_in / fw, (top_in - h) / fh, w / fw, h / fh])
    ax.set_axis_off()
    ax.add_patch(
        mpatches.Rectangle(
            (0, 0),
            1,
            1,
            transform=ax.transAxes,
            facecolor=PANEL,
            edgecolor=LEGEND_EDGE,
            linewidth=1,
            clip_on=False,
        )
    )

    def at(x, y_from_top):  # inches inside the box -> axes fraction
        return x / w, 1 - y_from_top / h

    text = dict(fontsize=KEY_FONTSIZE, color=INK)
    ax.text(*at(KEY_PAD, KEY_PAD), "Wind swaths", fontweight="bold",
            va="top", ha="left", **text)  # fmt: skip
    y = KEY_PAD + KEY_TITLE_H
    centres = [
        KEY_PAD + KEY_LABEL_W + KEY_COL_W * (i + 0.5)
        for i in range(len(SPEEDS))
    ]
    for cx, speed in zip(centres, SPEEDS):
        ax.text(*at(cx, y + KEY_HEAD_H / 2), f"≥ {speed} kt",
                va="center", ha="center", **text)  # fmt: skip
    y += KEY_HEAD_H
    for label, alpha in rows:
        ax.text(*at(KEY_PAD, y + KEY_ROW_H / 2), label,
                va="center", ha="left", **text)  # fmt: skip
        for cx, speed in zip(centres, SPEEDS):
            x0, y0 = at(cx - KEY_SW_W / 2, y + (KEY_ROW_H + KEY_SW_H) / 2)
            ax.add_patch(
                mpatches.Rectangle(
                    (x0, y0),
                    KEY_SW_W / w,
                    KEY_SW_H / h,
                    transform=ax.transAxes,
                    facecolor=WIND_COLORS[speed],
                    alpha=alpha,
                    edgecolor="none",
                )
            )
        y += KEY_ROW_H


def boxed_legend(fig, handles, title, ncol=1):
    """A legend in the same box style as swath_key. Returns it and its
    width in inches; place it with set_bbox_to_anchor."""
    leg = fig.legend(
        handles=handles,
        title=title,
        loc="upper left",
        ncol=ncol,
        frameon=True,
        fancybox=False,
        edgecolor=LEGEND_EDGE,
        framealpha=1,
        fontsize=KEY_FONTSIZE,
        title_fontsize=KEY_FONTSIZE,
        borderpad=0.8,
        handlelength=2.8,
        handleheight=0.9,
        columnspacing=1.4,
        alignment="left",
    )
    leg.get_frame().set_linewidth(1)
    leg.get_title().set_fontweight("bold")
    leg.get_title().set_color(INK)
    width = leg.get_window_extent(fig.canvas.get_renderer()).width
    return leg, width / fig.dpi


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
    layers: list,
    gdf_adm_swath_plot: gpd.GeoDataFrame,
    df_adm_template: pd.DataFrame,
    gdf_adm: gpd.GeoDataFrame,
    df_exp_adm: pd.DataFrame,
    cyclone_name: str = "",
    forecast_display_str: str = "",
    forecast_id: str = "",
    save_local: bool = False,
    gdf_tracks_plot: list | None = None,
    uncertainty_cone=None,
    id_col: str = ID_COL,
    name_col: str = NAME_COL,
    file_tag: str = "adm2",
    label_max_len: int = 9,
    label_font: tuple = (4, 20),
    gdf_observed_track: gpd.GeoDataFrame | None = None,
    swath_note: str = "",
    window_label: str = "Forecast, trigger window",
):
    """Three columns (most likely, worst case, best case): wind swath maps
    on top, population exposure bubbles below, boxed keys under each row
    (map style from ds-storms-alerts). ``layers``: per column, the
    "window", "other" and "observed" swath GeoDataFrames."""
    fig, axes = plt.subplots(2, 3, figsize=(18, 13.5))

    gdf_adm = gdf_adm.copy()
    gdf_adm["adm_label"] = gdf_adm[name_col].apply(
        wrap_text, max_len=label_max_len, break_anywhere=False
    )
    has_obs = gdf_observed_track is not None and not gdf_observed_track.empty
    has_obs_swath = any(
        lay.get("observed") is not None and not lay["observed"].empty
        for lay in layers
    )
    last_obs = None
    if has_obs:
        last = gdf_observed_track.iloc[-1].geometry
        last_obs = (last.x, last.y)

    columns = [
        ("middle", "Most likely track", INK, "bold"),
        (
            "worst",
            "Upper bound exposure\n(worst case scenario)",
            "#c0392b",
            "normal",
        ),
        (
            "best",
            "Lower bound exposure\n(best case scenario)",
            "#2e7d32",
            "normal",
        ),
    ]
    for col, (limit, title, color, weight) in enumerate(columns):
        top_ax, bottom_ax = axes[0, col], axes[1, col]
        plot_wind_buffers(gdf_adm_swath_plot, layers[col], ax=top_ax)
        if uncertainty_cone is not None:
            gpd.GeoSeries([uncertainty_cone], crs=gdf_adm.crs).plot(
                ax=top_ax, zorder=4, **CONE
            )
        if has_obs:
            draw_track(top_ax, gdf_observed_track, "observed")
        if gdf_tracks_plot is not None:
            draw_track(
                top_ax,
                gdf_tracks_plot[col],
                "forecast",
                start=last_obs,
                label_hours=True,
            )
        top_ax.set_title(
            title, fontsize=20, fontweight=weight, color=color, pad=6
        )
        plot_bullseye_exposures(
            df_adm_template.merge(gdf_adm[[id_col, "adm_label"]]),
            df_exp_adm[df_exp_adm["limit"] == limit],
            id_col=id_col,
            label_col="adm_label",
            min_font=label_font[0],
            max_font=label_font[1],
            colors=WIND_COLORS,
            show_legend=False,
            ax=bottom_ax,
        )

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

    fig.suptitle(
        f"{cyclone_name}: forecast issued {forecast_display_str}",
        fontsize=22,
        fontweight="bold",
        y=1,
    )
    if swath_note:
        fig.text(0.5, 0.967, swath_note, ha="center", fontsize=13)

    # room between and under the rows for the boxed keys
    fig.tight_layout(rect=[0, 0.08, 1, 0.985], h_pad=14)
    fw, fh = fig.get_size_inches()

    # Under the maps: the swath key and the track key, side by side, centred
    track_handles = []
    if has_obs:
        track_handles.append(
            Line2D(
                [0],
                [0],
                **OBS_TRACK,
                marker="o",
                ms=5,
                mfc=OBS_TRACK["color"],
                mec=PANEL,
                label="Observed",
            )
        )
    track_handles += [
        Line2D(
            [0],
            [0],
            **FCAST_TRACK,
            marker="D",
            ms=5,
            mfc=FCAST_TRACK["color"],
            mec=PANEL,
            label="Forecast",
        ),
        Patch(**CONE, label="Uncertainty cone"),
    ]
    rows = [
        (window_label, LAYER_ALPHA["window"]),
        ("Forecast, other lead times", LAYER_ALPHA["other"]),
    ]
    if has_obs_swath:
        rows.append(("Observed so far", LAYER_ALPHA["observed"]))
    top_in = axes[0, 0].get_position().y0 * fh - 0.2
    track_leg, track_w = boxed_legend(fig, track_handles, "Track")
    gap = 0.35
    x_in = (fw - swath_key_width() - gap - track_w) / 2
    swath_key(fig, x_in, top_in, rows)
    track_leg.set_bbox_to_anchor(
        ((x_in + swath_key_width() + gap) / fw, top_in / fh),
        transform=fig.transFigure,
    )

    # Under the bubbles: the population key, centred
    pale = {s: lighten(WIND_COLORS[s]) for s in SPEEDS}
    pop_handles = [
        Patch(facecolor=PANEL, edgecolor=GREY_EDGE, label="< 34 kt"),
        *[Patch(facecolor=pale[s], label=f"≥ {s} kt") for s in SPEEDS],
    ]
    pop_leg, pop_w = boxed_legend(
        fig,
        pop_handles,
        "Population exposed to wind speed "
        "(circle size proportional to population)",
        ncol=len(pop_handles),
    )
    top_in = axes[1, 0].get_position().y0 * fh - 0.2
    pop_leg.set_bbox_to_anchor(
        ((fw - pop_w) / 2 / fw, top_in / fh), transform=fig.transFigure
    )

    if save_local:
        if not forecast_id:
            forecast_id = "test"
        filepath = f"temp/{forecast_id}_{file_tag}_exposure.pdf"
        if not os.path.exists("temp/"):
            os.makedirs("temp/", exist_ok=True)
        fig.savefig(filepath, format="pdf", bbox_inches="tight")
    else:
        filepath = None
    return fig, axes, filepath
