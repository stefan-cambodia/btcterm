"""
Panneau flux ETF : entrées et sorties nettes des ETF Bitcoin spot.

Panneau « lent » : la source ne publie qu'une valeur par jour ouvré, le
cache du hub la conserve une demi-heure.
"""

from __future__ import annotations

import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

DAYS = 30


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Flux ETF spot (M$)"),
            html.Span(id="etf-total", style={"fontSize": "10px"}),
        ], style=TITLE_STYLE),
        dcc.Graph(id="etf-chart", style={"flex": "1", "minHeight": "0"},
                  config={"displayModeBar": False}),
    ], style=PANEL_STYLE)


def register(app, hub):
    @app.callback(
        Output("etf-chart", "figure"),
        Output("etf-total", "children"),
        Input("tick-rare", "n_intervals"),
    )
    def _refresh(_tick):
        try:
            df = hub.etf_flows()
        except Exception as exc:
            return _empty(f"source indisponible : {exc}"), ""

        total_col = "Total" if "Total" in df.columns else df.columns[-1]
        view = df.tail(DAYS)
        net = view[total_col].sum()

        fig = go.Figure(go.Bar(
            x=view["Date"], y=view[total_col],
            marker_color=[C["green"] if v >= 0 else C["red"] for v in view[total_col]],
            hovertemplate="%{x|%d %b}<br>%{y:+,.1f} M$<extra></extra>",
        ))
        fig.update_layout(
            paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
            font=dict(family=MONO, color=C["text"], size=10),
            margin=dict(l=8, r=8, t=4, b=4), showlegend=False,
            hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                            font_color=C["text"], font_size=10),
            uirevision="etf",
        )
        axis = dict(gridcolor=C["grid"], zerolinecolor=C["border"],
                    tickfont=dict(size=9, color=C["muted"]))
        fig.update_xaxes(**axis)
        fig.update_yaxes(**axis)

        color = C["green"] if net >= 0 else C["red"]
        label = html.Span(f"{DAYS} j : {net:+,.0f} M$", style={"color": color})
        return fig, label


def _empty(message: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        margin=dict(l=8, r=8, t=4, b=4),
        annotations=[dict(text=message, showarrow=False,
                          font=dict(family=MONO, color=C["muted"], size=10))],
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )
    return fig
