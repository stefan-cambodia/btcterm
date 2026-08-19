"""
Panneau news et sentiment.

Les articles viennent de la base `~/.btc_news/news.db`, partagée avec le
tracker `news/btc_news.py`. Le terminal ne se contente plus de la lire :
le collecteur du hub la remplit toutes les quinze minutes, de sorte que
le panneau ait quelque chose à montrer sans dépendre d'un timer systemd.
L'indice Fear & Greed, lui, est lu en direct via le hub.

La barre de titre porte l'âge de la dernière collecte : un fil de news
figé doit se voir, sans quoi on lit de vieilles nouvelles en croyant
qu'il ne se passe rien.
"""

from __future__ import annotations

import time

from dash import Input, Output, html

from btcterm import newsdb

from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

ROWS = 12

SENTIMENT_COLOR = {"bullish": C["green"], "bearish": C["red"], "neutral": C["muted"]}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("News à impact"),
            html.Span([
                html.Span(id="news-collecte",
                          style={"fontSize": "10px", "marginRight": "10px"}),
                html.Span(id="news-fg", style={"fontSize": "10px"}),
            ]),
        ], style=TITLE_STYLE),
        html.Div(id="news-list", style={"flex": "1", "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def _collecte_badge(status: dict):
    """Âge de la dernière collecte, ou ce qui l'empêche d'aboutir."""
    if status.get("error"):
        return html.Span("collecte en échec", style={"color": C["red"]},
                         title=str(status["error"]))
    if not status.get("last_run"):
        return html.Span(
            "collecte en cours…" if status.get("running") else "collecte inactive",
            style={"color": C["muted"]})

    minutes = int((time.time() - status["last_run"]) // 60)
    age = "à l'instant" if minutes < 1 else f"il y a {minutes} min"
    neuf = f" · +{status['new']}" if status.get("new") else ""
    return html.Span(f"collecte {age}{neuf}", style={"color": C["muted"]})


def register(app, hub):
    @app.callback(
        Output("news-list", "children"),
        Output("news-fg", "children"),
        Output("news-collecte", "children"),
        # L'horloge lente plutôt que la rare : la collecte tourne sur son
        # propre rythme, et l'âge affiché doit avancer sans attendre
        # cinq minutes.
        Input("tick-slow", "n_intervals"),
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

        collecte = _collecte_badge(hub.news.status)

        rows = newsdb.latest(ROWS)
        if not rows:
            attente = ("la première collecte arrive" if hub.collect_news else
                       "collecte désactivée — lancer  python news/btc_news.py fetch")
            return html.Div(
                f"base vide — {attente}",
                style={"color": C["muted"], "fontFamily": MONO,
                       "fontSize": "11px", "padding": "12px"},
            ), badge, collecte

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
        ], badge, collecte
