from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from src.constants import (
    CAT2COLOR,
    FJI_CRS,
    LOCAL_TIMEZONE,
    OUTPUT_DIR,
    VUT_CENTROID_LAT,
    VUT_CENTROID_LON,
)
from src.datasources.fms import str_from_report


def plot_forecast(
    report: dict, forecast: gpd.GeoDataFrame, save_html: bool = False
) -> go.Figure:
    """Plot forecast path and uncertainty over map of Fiji.

    Parameters
    ----------
    report: dict
        dict of forecast report from check_trigger()
    forecast: gpd.GeoDataFrame
        GeoDF of processed FMS forecast
    save_html: bool = False
        If True, saves HTML file of plot in outputs.

    Returns
    -------
    go.Figure
        Plotly figure of forecast
    """
    report_str = str_from_report(report)
    forecast = forecast.to_crs(3832)
    forecast = forecast[forecast["leadtime"] <= 120]

    # produce datetime strings in FJT for plot
    forecast["forecast_time_vut"] = (
        forecast["forecast_time"]
        .dt.tz_localize("UTC")
        .apply(lambda x: x.astimezone(ZoneInfo(LOCAL_TIMEZONE)))
    )
    forecast["formatted_datetime"] = (
        forecast["forecast_time_vut"].apply(
            lambda x: x.strftime("<br><br>%H:%M")
        )
        + "<br>("
        + forecast["leadtime"].astype(str)
        + " hrs)"
    )
    first_dts = forecast.groupby(forecast["forecast_time_vut"].dt.date)[
        "forecast_time_vut"
    ].idxmin()
    forecast.loc[
        forecast.index.isin(first_dts), "formatted_datetime"
    ] = forecast["forecast_time_vut"].dt.strftime("%b %d<br><br>%H:%M")

    official = forecast[forecast["leadtime"] <= 72]
    unofficial = forecast[forecast["leadtime"] >= 72]

    # interpolate forecast to produce smooth uncertainty cone
    cols = ["Latitude", "Longitude", "Uncertainty"]
    interp_o = (
        official.set_index("forecast_time")[cols]
        .resample("15T")
        .interpolate(method="linear")
    )
    interp_o = gpd.GeoDataFrame(
        interp_o,
        geometry=gpd.points_from_xy(
            interp_o["Longitude"], interp_o["Latitude"]
        ),
    )
    interp_o = interp_o.set_crs(FJI_CRS).to_crs(3832)

    # produce uncertainty cone
    circles = []
    for _, row in interp_o.iterrows():
        circles.append(row["geometry"].buffer(row["Uncertainty"] * 1000))
    o_zone = (
        gpd.GeoDataFrame(geometry=circles)
        .dissolve()
        .set_crs(3832)
        .to_crs(FJI_CRS)
    )

    fig = go.Figure()

    # plot uncertainty cone official 72hr
    if o_zone.geometry[0].geom_type == "Polygon":
        x_o, y_o = o_zone.geometry[0].boundary.xy
        x_o, y_o = [x_o], [y_o]
    elif o_zone.geometry[0].geom_type == "MultiPolygon":
        x_o, y_o = [], []
        for g in o_zone.geometry[0].geoms:
            x_p, y_p = g.boundary.xy
            x_o.append(x_p)
            y_o.append(y_p)
    showlegend = True
    for x, y in zip(x_o, y_o):
        fig.add_trace(
            go.Scattermapbox(
                lat=np.array(y),
                lon=np.array(x),
                mode="lines",
                name="Uncertainty",
                line=dict(width=0),
                fill="toself",
                fillcolor="rgba(0, 0, 0, 0.1)",
                hoverinfo="skip",
                legendgroup="official",
                showlegend=showlegend,
            )
        )
        showlegend = False

    # plot official forecast line
    fig.add_trace(
        go.Scattermapbox(
            lat=official["Latitude"],
            lon=official["Longitude"],
            mode="lines",
            line=dict(width=1.5, color="black"),
            name="Best Track",
            customdata=official[["Category", "forecast_time_vut"]],
            hovertemplate="Category: %{customdata[0]}<br>"
            "Datetime: %{customdata[1]}",
            legendgroup="official",
            legendgrouptitle_text="Official 72-hour forecast",
        )
    )

    # plot unofficial forecast line
    fig.add_trace(
        go.Scattermapbox(
            lat=unofficial["Latitude"],
            lon=unofficial["Longitude"],
            mode="lines",
            line=dict(width=1.5, color="white"),
            name="Best Track",
            customdata=unofficial[["Category", "forecast_time_vut"]],
            hovertemplate="Category: %{customdata[0]}<br>"
            "Datetime: %{customdata[1]}",
            legendgroup="unofficial",
            legendgrouptitle_text="Unofficial 120-hour forecast",
        )
    )
    # plot forecast points by category
    for color in CAT2COLOR:
        dff = forecast[forecast["Category"] == color[0]]
        name = "L" if color[0] == 0 else f"Category {color[0]}"
        fig.add_trace(
            go.Scattermapbox(
                lat=dff["Latitude"],
                lon=dff["Longitude"],
                mode="markers",
                line=dict(width=2, color=color[1]),
                marker=dict(size=10),
                name=name,
                hoverinfo="skip",
            )
        )

    # plot text for forecast datetime
    fig.add_trace(
        go.Scattermapbox(
            lat=forecast["Latitude"],
            lon=forecast["Longitude"],
            mode="text",
            text=forecast["formatted_datetime"],
            showlegend=False,
            textfont={"size": 8, "color": "black"},
        )
    )

    # set map bounds with forecast points
    lat_max = max(forecast["Latitude"])
    lat_max = max(lat_max, VUT_CENTROID_LAT)
    lat_min = min(forecast["Latitude"])
    lat_min = min(lat_min, VUT_CENTROID_LAT)
    lon_max = max(forecast["Longitude"])
    lon_max = max(lon_max, VUT_CENTROID_LON)
    lon_min = min(forecast["Longitude"])
    lon_min = min(lon_min, VUT_CENTROID_LON)

    # possible solutions from
    # https://stackoverflow.com/questions/63787612/plotly-automatic-zooming-for-mapbox-maps

    # using range for zoom
    lon_zoom_range = np.array(
        [
            0.0007,
            0.0014,
            0.003,
            0.006,
            0.012,
            0.024,
            0.048,
            0.096,
            0.192,
            0.3712,
            0.768,
            1.536,
            3.072,
            6.144,
            11.8784,
            23.7568,
            47.5136,
            98.304,
            190.0544,
            360.0,
        ]
    )
    width_to_height = 1
    margin = 1.7
    height = (lat_max - lat_min) * margin * width_to_height
    width = (lon_max - lon_min) * margin
    lon_zoom = np.interp(width, lon_zoom_range, range(20, 0, -1))
    lat_zoom = np.interp(height, lon_zoom_range, range(20, 0, -1))
    zoom = round(min(lon_zoom, lat_zoom), 2)

    fig.update_layout(
        mapbox_style="open-street-map",
        mapbox_zoom=zoom,
        mapbox_center_lat=(lat_max + lat_min) / 2,
        mapbox_center_lon=(lon_max + lon_min) / 2,
        margin={"r": 0, "t": 50, "l": 0, "b": 0},
        title=f"RSMC Nadi forecast for {report.get('cyclone')}<br>"
        f"<sup>Produced at {report_str.get('vut_time')} on "
        f"{report_str.get('vut_date')} (Vanuatu time)",
        legend=dict(xanchor="right", x=1, bgcolor="rgba(255, 255, 255, 0.3)"),
        height=850,
        width=800,
    )
    fig.update_geos()
    filepath_stem = (
        OUTPUT_DIR / f"forecast_plot_{report_str.get('file_dt_str')}"
    )
    fig.write_image(f"{filepath_stem}.png", scale=4)
    if save_html:
        fig.write_html(f"{filepath_stem}.html")
    return fig


def plot_distances(report: dict, distances: pd.DataFrame) -> go.Figure:
    """Plot distance of each admin2 to forecast

    Parameters
    ----------
    report: dict
        dict of forecast report from check_trigger()
    distances: pd.DataFrame
        Calculated distances of each admin2 to forecast

    Returns
    -------
    go.Figure
    """
    report_str = str_from_report(report)
    fig = go.Figure()
    distances = distances.sort_values("distance (km)", ascending=False)
    adm_order = distances["ADM1_EN"]
    for color in CAT2COLOR:
        dff = distances[distances["category"] == color[0]]
        name = "L" if color[0] == 0 else color[0]
        fig.add_trace(
            go.Scatter(
                y=dff["ADM1_EN"],
                x=dff["distance (km)"],
                mode="markers",
                error_x=dict(
                    type="data", array=dff["uncertainty (km)"], visible=True
                ),
                marker=dict(size=8, color=color[1]),
                name=name,
            )
        )
    max_dist = (
        distances["distance (km)"] + distances["uncertainty (km)"]
    ).max()
    fig.update_yaxes(categoryorder="array", categoryarray=adm_order)
    fig.update_xaxes(
        range=(0, max_dist * 1.01),
        title="Minimum distance from best track forecast to Province (km)",
    )
    title = (
        f"Cyclone {report.get('cyclone').split(' ')[0]} predicted closest "
        "pass to provinces<br>"
        "<sub>Based on forecast produced at "
        f"{report_str.get('vut_time')} on {report_str.get('vut_date')} "
        "(Vanuatu time)</sub><br>"
        "<sup>Error bars estimated based on uncertainty cone of forecast</sup>"
    )
    fig.update_layout(
        template="simple_white",
        title_text=title,
        margin={"r": 0, "t": 100, "l": 0, "b": 0},
        legend=dict(
            xanchor="right",
            x=1,
            bgcolor="rgba(255, 255, 255, 0.3)",
            title="Category at<br>closest pass",
        ),
        showlegend=True,
    )
    filepath_stem = (
        OUTPUT_DIR / f"distances_plot_{report_str.get('file_dt_str')}"
    )
    fig.write_image(f"{filepath_stem}.png", scale=4)
    return fig
