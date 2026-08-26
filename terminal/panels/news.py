"""
Panneau news et sentiment.

Les articles viennent de la base `~/.btc_news/news.db`, partagée avec le
tracker `news/btc_news.py`. Le terminal ne se contente plus de la lire :
le collecteur du hub la remplit toutes les quinze minutes, de sorte que
le panneau ait quelque chose à montrer sans dépendre d'un timer systemd.
L'indice Fear & Greed, lui, est lu en direct via le hub — chiffre du
jour dans la barre de titre, et courbe des trois derniers mois quand le
panneau passe en plein écran : la valeur seule ne dit pas si le marché
sort de la peur ou y entre, la pente le dit.

La barre de titre porte l'âge de la dernière collecte : un fil de news
figé doit se voir, sans quoi on lit de vieilles nouvelles en croyant
qu'il ne se passe rien.
"""

from __future__ import annotations

import time

from dash import Input, Output, dcc, html

from btcterm import newsdb

from ..charts import build_fear_greed_chart, fear_greed_color
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

ROWS = 12

#: Hauteur de la courbe Fear & Greed en plein écran. Un tiers du
#: panneau : de quoi lire une pente sur trois mois, sans que le fil
#: des news — ce pour quoi on ouvre ce panneau — passe au second plan.
FG_HEIGHT = "32%"

SENTIMENT_COLOR = {"bullish": C["green"], "bearish": C["red"], "neutral": C["muted"]}

_WRAP_HIDDEN = {"display": "none"}
_WRAP_SHOWN = {"height": FG_HEIGHT, "minHeight": "0",
               "marginBottom": "6px",
               "borderBottom": f"1px solid {C['border']}"}


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
        # La courbe n'existe qu'en plein écran : dans la grille, la
        # cellule fait quelques centaines de pixels de haut, et chaque
        # ligne de news y vaut mieux qu'un graphique écrasé. Le cadre
        # est vide tant qu'on n'agrandit pas — un `dcc.Graph` monté dans
        # un conteneur en `display: none` se dessine sur une hauteur
        # nulle et reste plat une fois révélé.
        html.Div(id="news-fg-wrap", style=_WRAP_HIDDEN),
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
            badge = html.Span(f"F&G {value}/100 · {fear_greed['label']}",
                              style={"color": fear_greed_color(value)})
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

    @app.callback(
        Output("news-fg-wrap", "children"),
        Output("news-fg-wrap", "style"),
        # L'horloge rare suffit : alternative.me ne publie qu'une valeur
        # par jour. `expanded` est la seconde entrée pour que la courbe
        # se dessine à l'instant où on agrandit le panneau, sans
        # attendre le prochain tour de cinq minutes.
        Input("tick-rare", "n_intervals"),
        Input("expanded", "data"),
    )
    def _fear_greed_chart(_tick, expanded):
        # Repliée, la courbe n'est pas seulement cachée : elle n'est pas
        # construite, et le hub n'est pas interrogé pour rien.
        if expanded != "news":
            return None, _WRAP_HIDDEN
        return dcc.Graph(
            figure=build_fear_greed_chart(hub.fear_greed_history()),
            style={"height": "100%"}, config={"displayModeBar": False},
        ), _WRAP_SHOWN
