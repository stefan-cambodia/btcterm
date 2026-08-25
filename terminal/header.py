"""
Le bandeau du terminal : cours, variation, spread, état des flux.

Une ligne au-dessus de la grille, rafraîchie au régime lent. Deux de ses
champs sont tenus par d'autres modules et ne font ici que réserver leur
place : la cloche des alertes (`hdr-alerts`, panneau alertes) et le canal
des panneaux rapides (`hdr-push`, assets/push.js).
"""

from __future__ import annotations

import dash
from dash import Input, Output, html

from btcterm.hub import MarketHub

from .theme import C, MONO

#: Style commun des champs du bandeau.
STAT = {"fontFamily": MONO, "fontSize": "11px", "color": C["text"],
        "marginRight": "18px"}


def layout():
    return html.Div([
        html.Span("₿ BTC TERMINAL", style={
            "fontFamily": MONO, "fontWeight": "700", "fontSize": "13px",
            "color": C["yellow"], "letterSpacing": "0.14em", "marginRight": "24px"}),
        html.Span(id="hdr-price", style={**STAT, "fontSize": "14px",
                                         "fontWeight": "600"}),
        html.Span(id="hdr-change", style=STAT),
        html.Span(id="hdr-spread", style=STAT),
        html.Button("⚙", id="layout-btn", className="layout-btn",
                    title="disposition de la grille"),
        # La cloche : sonneries de la dernière heure. Tenue par le
        # callback du panneau alertes — qui tourne toujours, le fil
        # devant compter et sonner même panneau replié.
        html.Span(id="hdr-alerts", title="alertes de la dernière heure",
                  style={**STAT, "color": C["muted"], "fontSize": "11px"}),
        html.Span("⛶ ou double-clic sur un panneau · Échap pour revenir",
                  style={**STAT, "marginLeft": "12px", "color": C["muted"],
                         "fontSize": "10px"}),
        # Canal des panneaux rapides : « push » quand le WebSocket est
        # ouvert, « poll » en repli. Tenu par assets/push.js, jamais par
        # un callback — c'est un état du navigateur, pas du serveur.
        html.Span(id="hdr-push", title="canal des panneaux rapides",
                  style={**STAT, "color": C["muted"], "fontSize": "10px"}),
        html.Span(id="hdr-status", style={**STAT, "color": C["muted"]}),
    ], style={
        "display": "flex", "alignItems": "center", "padding": "0 14px",
        "height": "38px", "background": C["panel"],
        "borderBottom": f"1px solid {C['border']}",
    })


def register(app: dash.Dash, hub: MarketHub) -> None:
    @app.callback(
        Output("hdr-price", "children"),
        Output("hdr-change", "children"),
        Output("hdr-change", "style"),
        Output("hdr-spread", "children"),
        Output("hdr-status", "children"),
        Input("tick-slow", "n_intervals"),
    )
    def _refresh(_tick):
        ticker = hub.ticker()
        live = hub.reference_price()
        price_txt = f"{live:,.2f} $" if live else "—"

        change = float(ticker.get("priceChangePercent", 0) or 0)
        change_style = {**STAT, "color": C["green"] if change >= 0 else C["red"]}
        change_txt = f"{change:+.2f} % 24 h"

        spreads = [b.spread_pct for b in hub.books.values() if b.spread_pct]
        spread_txt = f"spread min {min(spreads):.4f} %" if spreads else ""

        uptime = hub.uptime_seconds
        status = (f"{hub.connected_count}/5 flux · "
                  f"{uptime // 3600:02d}:{uptime % 3600 // 60:02d}:{uptime % 60:02d}")

        return price_txt, change_txt, change_style, spread_txt, status
