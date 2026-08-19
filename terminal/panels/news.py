"""
Panneau news et sentiment.

Les articles proviennent de la base alimentée par `news/btc_news.py` —
le terminal la lit, il ne l'écrit pas : la collecte et le scoring restent
la responsabilité de cet outil, lancé à la main ou par son timer systemd.
L'indice Fear & Greed, lui, est lu en direct via le hub.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from dash import Input, Output, html

from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

DB_PATH = Path.home() / ".btc_news" / "news.db"
ROWS = 12

SENTIMENT_COLOR = {"bullish": C["green"], "bearish": C["red"], "neutral": C["muted"]}


def layout():
    return html.Div([
        html.Div([
            html.Span("News à impact"),
            html.Span(id="news-fg", style={"fontSize": "10px"}),
        ], style=TITLE_STYLE),
        html.Div(id="news-list", style={"flex": "1", "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def _read_latest(limit: int) -> list[sqlite3.Row]:
    """Lecture seule de la base du tracker, vide si elle n'existe pas encore."""
    if not DB_PATH.exists():
        return []
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT title, source, score, sentiment, url, published"
            " FROM news ORDER BY fetched_at DESC, score DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        connection.close()


def register(app, hub):
    @app.callback(
        Output("news-list", "children"),
        Output("news-fg", "children"),
        Input("tick-rare", "n_intervals"),
    )
    def _refresh(_tick):
        fear_greed = hub.fear_greed()
        if fear_greed:
            value = fear_greed["value"]
            color = (C["green"] if value >= 55 else
                     C["red"] if value < 45 else C["yellow"])
            badge = html.Span(f"F&G {value}/100 · {fear_greed['label']}",
                              style={"color": color})
        else:
            badge = ""

        rows = _read_latest(ROWS)
        if not rows:
            return html.Div(
                "base vide — lancer  python news/btc_news.py fetch",
                style={"color": C["muted"], "fontFamily": MONO,
                       "fontSize": "11px", "padding": "12px"},
            ), badge

        return [
            html.A([
                html.Span(f"{row['score']:3d}", style={
                    "color": SENTIMENT_COLOR.get(row["sentiment"], C["muted"]),
                    "fontWeight": "600", "marginRight": "8px"}),
                html.Span(row["title"][:88]),
                html.Span(f"  · {row['source']}", style={"color": C["muted"]}),
            ], href=row["url"], target="_blank", style={
                "display": "block", "fontFamily": MONO, "fontSize": "11px",
                "color": C["text"], "textDecoration": "none",
                "padding": "3px 4px", "borderBottom": f"1px solid {C['border']}",
            })
            for row in rows
        ], badge
