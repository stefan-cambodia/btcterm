"""
Panneau arbitrage : écarts exploitables entre plateformes.

Panneau « rapide », alimenté par le moteur du hub. Les montants affichés
sont nets des frais taker des deux plateformes, au premier palier
(§ arbitrage.DEFAULT_FEES) ; ils ne tiennent compte ni des frais de
transfert, ni du temps d'exécution.

Sous le titre, les primes régionales — les écarts qu'aucun arbitrage ne
ferme vite : Coinbase en dollars, la demande américaine (§ tape), et la
Corée en wons, celle des particuliers coréens derrière leur contrôle des
changes (§ premiums). Lues en mémoire seulement, à l'horloge de deux
secondes : la boucle d'observation du hub relit Upbit et le won.
"""

from __future__ import annotations

from typing import Optional

from dash import Input, Output, html

from btcterm.premiums import KOREA_HOT

from ..fx import display
from ..theme import C, MONO, PANEL_STYLE, TABLE_STYLE, TITLE_STYLE

ROWS = 8


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Arbitrage"),
            html.Span(id="arb-count", style={"color": C["muted"], "fontSize": "9px"}),
        ], style=TITLE_STYLE),
        html.Div(id="arb-premiums",
                 style={"fontFamily": MONO, "fontSize": "9px",
                        "padding": "0 10px 3px", "whiteSpace": "nowrap",
                        "overflow": "hidden"}),
        html.Div(id="arb-table", style={"flex": "1", "overflow": "hidden"}),
    ], style=PANEL_STYLE)


def _signed(value: float, color: Optional[str] = None, title: str = ""):
    """Un pourcentage signé, vert ou rouge selon son signe à défaut d'une
    couleur imposée."""
    return html.Span(f"{value * 100:+z.2f} %",
                     style={"color": color or (C["green"] if value >= 0
                                               else C["red"])},
                     title=title or None)


def premiums_line(coinbase: Optional[float], korea: Optional[dict]):
    """Les primes régionales, en une ligne : Coinbase, puis la Corée et ses
    deux facteurs (§ premiums). Vide sans l'une ni l'autre."""
    muted = {"color": C["muted"]}
    parts = []
    if coinbase is not None:
        parts += [html.Span("Coinbase ", style=muted), _signed(
            coinbase, title="le bitcoin en dollars sur Coinbase contre "
                            "Binance en USDT, prix moyens de cinq minutes : "
                            "la demande américaine")]
    if korea is not None:
        hot = korea["premium"] * 100 >= KOREA_HOT
        fx = ("cours du won en séance, Yahoo" if korea["source"] == "marché"
              else "référence du jour de la BCE, faute du cours en séance")
        if parts:
            parts.append(html.Span(" · ", style=muted))
        parts += [
            html.Span("Corée ", style=muted),
            _signed(korea["premium"], C["orange"] if hot else None,
                    f"le bitcoin d'Upbit en wons, {korea['price_usd']:,.0f} $ "
                    f"au change de {korea['rate']:,.1f} ₩ ({fx}), contre le "
                    f"cours mondial — la « prime kimchi » ; au-delà de "
                    f"{KOREA_HOT:g} %, l'appétit coréen se paie"),
            html.Span(" = won ", style=muted),
            _signed(korea["won"], C["text"],
                    "l'USDT en wons sur Upbit contre le dollar : le prix de "
                    "la sortie du won vers la cryptomonnaie, que le contrôle "
                    "des changes rend structurel"),
            html.Span(" + BTC ", style=muted),
            _signed(korea["bitcoin"], None,
                    "le bitcoin contre l'USDT sur Upbit, face au cours "
                    "mondial : la demande propre au bitcoin, une fois le won "
                    "franchi (les deux facteurs se composent)"),
        ]
    if not parts:
        return ""
    return [html.Span("primes · ", style=muted), *parts]


def render(hub, currency=None):
    """Table et badge de l'arbitrage, prêts à poser dans le panneau.

    Fonction pure vis-à-vis du rendu, partagée entre le callback Dash et
    le pousseur WebSocket.
    """
    # `evaluate`, pas `scan` : le rendu ne compte rien — le compteur est
    # celui du balayage du hub, une fois par seconde.
    opportunities = hub.engine.evaluate()
    total = hub.engine.stats["total_opportunities"]
    badge = f"{total} détectées · {hub.connected_count}/5 connectés"

    if not opportunities:
        return html.Div("en attente des carnets…",
                        style={"color": C["muted"], "fontFamily": MONO,
                               "fontSize": "11px", "padding": "12px"}), badge

    fx = display(hub, currency)
    net = ("net des frais taker des deux plateformes, au premier palier "
           "d'un compte — hors transfert et délai d'exécution")
    header = html.Thead(html.Tr([
        html.Th(label, title=title,
                style={"color": C["muted"], "fontSize": "9px",
                       "textAlign": align, "padding": "2px 6px",
                       "fontWeight": "400"})
        for label, align, title in (
            (f"Acheter ({fx.warning}{fx.unit})", "left", fx.note),
            (f"Vendre ({fx.unit})", "left", fx.note),
            ("Brut %", "right", fx.note), ("Net %", "right", net))
    ]))

    rows = []
    for opportunity in opportunities[:ROWS]:
        color = C["green"] if opportunity.is_profitable else C["muted"]
        rows.append(html.Tr([
            html.Td(f"{opportunity.buy_exchange} "
                    f"{opportunity.buy_price * fx.rate:,.2f}",
                    style={"padding": "1px 6px"}),
            html.Td(f"{opportunity.sell_exchange} "
                    f"{opportunity.sell_price * fx.rate:,.2f}",
                    style={"padding": "1px 6px"}),
            html.Td(f"{opportunity.gross_profit_pct:+z.3f}",
                    style={"textAlign": "right", "color": C["muted"],
                           "padding": "1px 6px"}),
            html.Td(f"{opportunity.net_profit_pct:+z.3f}",
                    style={"textAlign": "right", "color": color,
                           "fontWeight": "600", "padding": "1px 6px"}),
        ]))

    return html.Table([header, html.Tbody(rows)], style=TABLE_STYLE), badge


def register(app, hub):
    @app.callback(
        Output("arb-table", "children"),
        Output("arb-count", "children"),
        Input("tick-fast", "n_intervals"),
        Input("currency", "data"),
    )
    def _refresh(_tick, currency):
        return render(hub, currency)

    @app.callback(
        Output("arb-premiums", "children"),
        Input("tick-slow", "n_intervals"),
    )
    def _premiums(_tick):
        return premiums_line(hub.tape.coinbase_premium(300),
                             hub.korea_premium())
