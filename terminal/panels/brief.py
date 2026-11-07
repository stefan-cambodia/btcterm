"""
Panneau SYNTHÈSE : les dernières vingt-quatre heures en une page.

Le retour devant l'écran pose une seule question — qu'est-ce qui a
changé ? — à laquelle il fallait répondre en ouvrant six onglets. Ce
panneau la pose au module `btcterm.brief` et en affiche les lignes :
cours et journée glissante, perpétuel, liquidations et gros ordres
journalisés, flux ETF, sentiment, volatilité, prime Coinbase, dominance,
agenda macro, alertes et news les mieux notées. Le détail de chaque ligne
passe au survol.

La couverture du journal s'écrit en tête quand elle manque : sur un
portable qui a dormi, « aucune liquidation » ne veut pas dire « marché
calme ».

Le même texte part chaque matin vers ntfy si `BTCTERM_BRIEF` est posé
(§ hub) ; le titre du panneau le rappelle.
"""

from __future__ import annotations

import time

from dash import Input, Output, html

from btcterm import brief

from ..theme import C, MONO, PANEL_STYLE, TABLE_STYLE, TITLE_STYLE

#: La couleur de chaque ton de ligne.
TONE_COLORS = {"up": C["green"], "down": C["red"], "warn": C["yellow"],
               "muted": C["muted"], "": C["text"]}

_CELL = {"padding": "2px 6px", "verticalAlign": "top"}
_LABEL = {**_CELL, "color": C["muted"], "fontSize": "9px",
          "letterSpacing": "1px", "textTransform": "uppercase",
          "whiteSpace": "nowrap", "paddingTop": "4px"}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Synthèse"),
            html.Span(id="brief-badges",
                      style={"fontSize": "9px", "whiteSpace": "nowrap",
                             "marginLeft": "10px"}),
        ], style=TITLE_STYLE),
        html.Div(id="brief-view", style={"flex": "1", "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def _row(item: brief.Item):
    return html.Tr([
        html.Td(item.label, style=_LABEL),
        html.Td(item.text, title=item.detail or None, style={
            **_CELL, "color": TONE_COLORS.get(item.tone, C["text"])}),
    ])


def render(hub, now=None):
    """Le contenu du panneau et ses badges — pur vis-à-vis de Dash."""
    synthese = brief.build(hub, now)
    head = []
    if synthese.coverage:
        head.append(html.Div(synthese.coverage, style={
            "color": C["yellow"], "fontSize": "10px", "padding": "4px 6px",
            "borderBottom": f"1px solid {C['border']}"}))
    view = html.Div(head + [html.Table(
        [html.Tbody([_row(item) for item in synthese.items])],
        style={**TABLE_STYLE, "width": "100%"})],
        style={"fontFamily": MONO, "fontSize": "11px"})

    badges = [html.Span(f"{brief.HOURS} h · ", style={"color": C["muted"]}),
              html.Span(time.strftime("%H:%M", time.localtime(synthese.at)),
                        style={"color": C["text"]},
                        title="heure de la synthèse, refaite toutes les "
                              "cinq minutes")]
    if synthese.coverage:
        badges.append(html.Span(" · partielle", style={"color": C["yellow"]},
                                title=synthese.coverage))
    brief_at = getattr(hub, "brief_at", None)
    if brief_at is not None:
        envoi = ("vers ntfy" if hub.alerts.notifier is not None
                 else "mais ntfy n'est pas configuré (BTCTERM_NTFY)")
        badges.append(html.Span(
            f" · {brief_at:%H:%M}", style={"color": C["cyan"]},
            title=f"la synthèse part chaque matin à {brief_at:%H:%M} {envoi}"))
    return view, html.Span(badges, style={"fontFamily": MONO})


def register(app, hub):
    @app.callback(
        Output("brief-view", "children"),
        Output("brief-badges", "children"),
        # L'horloge rare : une synthèse de la journée n'a rien à dire de
        # neuf toutes les deux secondes, et chaque tour relit le journal.
        # Elle part aussi à l'ouverture de l'onglet, comme tout callback
        # dont les composants entrent dans la page.
        Input("tick-rare", "n_intervals"),
    )
    def _refresh(_tick):
        return render(hub)
