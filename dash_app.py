"""Plotly-Dash dashboard for the Xiamen shared-bicycle tidal-imbalance study.

Run with::

    python dash_app.py

and open http://127.0.0.1:8050 .  Every label, hover text and legend entry is in
English.  The app reads only the artifacts written by ``run_pipeline.py``
(``figures/panel_fence_halfhour.csv`` and ``figures/dashboard_fences.csv``), so it
starts in a second and never touches the raw workbook files.

Widgets
-------
* 30-minute slot slider (06:00-10:00, eight slots)
* fence-role filter (all / net source / net sink / balanced)
* fence-level tidal map for the selected slot
* morning demand curve per day with the cross-day mean
* ranking of the strongest net sources and sinks in the selected slot
"""

from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html

HERE = os.path.dirname(os.path.abspath(__file__))
FIG_DIR = os.path.join(HERE, "figures")

SLOT_LABELS = [f"{6 + k // 2:02d}:{(k % 2) * 30:02d}" for k in range(8)]
ROLE_COLORS = {"source": "#c0392b", "sink": "#2c7fb8", "balanced": "#7f7f7f"}


def load_data():
    panel = pd.read_csv(os.path.join(FIG_DIR, "panel_fence_halfhour.csv"), parse_dates=["date"])
    fences = pd.read_csv(os.path.join(FIG_DIR, "dashboard_fences.csv"))
    return panel, fences


def build_app() -> Dash:
    panel, fences = load_data()
    coord = fences.set_index("fence_id")[["lat", "lng", "role"]]

    app = Dash(__name__)
    app.title = "Xiamen shared-bicycle tidal imbalance"

    header = html.Div(
        [
            html.H2("Xiamen shared-bicycle tidal imbalance explorer"),
            html.P(
                "Five weekdays (2020-12-21 to 2020-12-25), morning peak 06:00-10:00. "
                "Tidal index = (pickups - returns) / total flow, in [-1, 1].",
                style={"color": "#555", "marginTop": "-8px"},
            ),
        ]
    )

    controls = html.Div(
        [
            html.Div(
                [
                    html.Label("30-minute slot"),
                    dcc.Slider(
                        id="slot-slider",
                        min=0,
                        max=7,
                        step=1,
                        value=4,
                        marks={k: SLOT_LABELS[k] for k in range(8)},
                    ),
                ],
                style={"flex": "3", "paddingRight": "30px"},
            ),
            html.Div(
                [
                    html.Label("Fence role filter"),
                    dcc.Dropdown(
                        id="role-filter",
                        options=[
                            {"label": "All fences", "value": "all"},
                            {"label": "Net sources (bikes leave)", "value": "source"},
                            {"label": "Net sinks (bikes arrive)", "value": "sink"},
                            {"label": "Balanced", "value": "balanced"},
                        ],
                        value="all",
                        clearable=False,
                    ),
                ],
                style={"flex": "2"},
            ),
        ],
        style={"display": "flex", "marginBottom": "18px"},
    )

    app.layout = html.Div(
        [
            header,
            controls,
            html.Div(
                [
                    dcc.Graph(id="tidal-map", style={"flex": "3", "height": "560px"}),
                    dcc.Graph(id="demand-curve", style={"flex": "2", "height": "560px"}),
                ],
                style={"display": "flex", "gap": "20px"},
            ),
            dcc.Graph(id="ranking", style={"height": "430px"}),
            html.P(
                "Weather enters the underlying models as a control variable only; the five observed "
                "days are consecutive weekdays with one rainy day, so weather effects are not causal.",
                style={"color": "#777", "fontSize": "12px", "marginTop": "10px"},
            ),
        ],
        style={"fontFamily": "DejaVu Sans, Arial, sans-serif", "padding": "22px", "maxWidth": "1500px", "margin": "0 auto"},
    )

    @app.callback(
        Output("tidal-map", "figure"),
        Output("demand-curve", "figure"),
        Output("ranking", "figure"),
        Input("slot-slider", "value"),
        Input("role-filter", "value"),
    )
    def _update(slot: int, role: str):
        d = panel.loc[panel["halfhour"] == slot]
        agg = (
            d.groupby("fence_id", observed=True)
            .agg(n_pickup=("n_pickup", "sum"), n_return=("n_return", "sum"))
            .reset_index()
        )
        agg["total_flow"] = agg["n_pickup"] + agg["n_return"]
        agg["net_flow"] = agg["n_pickup"] - agg["n_return"]
        agg["tidal_index"] = agg["net_flow"] / agg["total_flow"].replace(0, pd.NA)
        agg = agg.join(coord, on="fence_id", how="left").dropna(subset=["lat", "lng"])

        shown = agg if role == "all" else agg.loc[agg["role"] == role]
        color = [ROLE_COLORS.get(r, "#7f7f7f") for r in shown["role"]]
        size = 5 + 22 * (shown["total_flow"] / max(agg["total_flow"].max(), 1)) ** 0.5
        tidal_map = go.Figure(
            go.Scatter(
                x=shown["lng"],
                y=shown["lat"],
                mode="markers",
                marker=dict(color=color, size=size, opacity=0.8, line=dict(width=0.5, color="#333")),
                text=shown["fence_id"],
                customdata=shown[["n_pickup", "n_return", "net_flow", "tidal_index"]].to_numpy(),
                hovertemplate=(
                    "<b>%{text}</b><br>pickups %{customdata[0]:.0f} | returns %{customdata[1]:.0f}"
                    "<br>net flow %{customdata[2]:.0f} | tidal index %{customdata[3]:.2f}<extra></extra>"
                ),
            )
        )
        tidal_map.update_layout(
            title=f"Fence-level tidal index, slot {SLOT_LABELS[slot]} (n = {len(shown):,})",
            xaxis_title="Longitude (deg)",
            yaxis_title="Latitude (deg)",
            yaxis=dict(scaleanchor="x", scaleratio=1),
            margin=dict(l=50, r=20, t=50, b=45),
            showlegend=False,
        )

        curve = panel.groupby(["date", "halfhour"], observed=True)["n_pickup"].sum().reset_index()
        demand = go.Figure()
        for date, chunk in curve.groupby("date"):
            demand.add_trace(
                go.Scatter(
                    x=[SLOT_LABELS[int(h)] for h in chunk["halfhour"]],
                    y=chunk["n_pickup"],
                    mode="lines+markers",
                    name=str(pd.Timestamp(date).date()),
                    line=dict(width=1.4),
                )
            )
        mean_curve = curve.groupby("halfhour")["n_pickup"].mean()
        demand.add_trace(
            go.Scatter(
                x=[SLOT_LABELS[int(h)] for h in mean_curve.index],
                y=mean_curve.to_numpy(),
                mode="lines",
                name="5-day mean",
                line=dict(width=3, color="#111111", dash="dash"),
            )
        )
        demand.update_layout(
            title="Morning demand curve (pickups per 30 minutes, all fences)",
            xaxis_title="30-minute slot",
            yaxis_title="Pickups",
            legend=dict(title="date"),
            margin=dict(l=50, r=20, t=50, b=45),
        )

        top_sink = agg.nsmallest(12, "net_flow").assign(group="net sink (bikes arrive)")
        top_source = agg.nlargest(12, "net_flow").assign(group="net source (bikes leave)")
        rank = pd.concat([top_sink, top_source]).sort_values("net_flow")
        ranking = go.Figure(
            go.Bar(
                x=rank["net_flow"],
                y=rank["fence_id"],
                orientation="h",
                marker_color=["#2c7fb8" if v < 0 else "#c0392b" for v in rank["net_flow"]],
                hovertemplate="%{y}<br>net flow %{x:.0f}<extra></extra>",
            )
        )
        ranking.update_layout(
            title=f"Strongest net sinks and sources, slot {SLOT_LABELS[slot]}",
            xaxis_title="Net flow (pickups - returns)",
            yaxis_title="Fence ID",
            margin=dict(l=220, r=20, t=50, b=45),
        )
        return tidal_map, demand, ranking

    return app


if __name__ == "__main__":
    application = build_app()
    application.run(debug=False, port=8050)
