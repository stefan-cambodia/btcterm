"""
Panneau dominance : la part du Bitcoin dans la capitalisation totale.

La dominance dit où va l'argent à l'intérieur du marché : elle monte
quand le Bitcoin absorbe les flux, elle baisse quand ils partent vers le
reste. La part des stablecoins se lit autrement — c'est du cash qui
attend, pas un concurrent —, d'où leur couleur distincte.

**L'instantané vient de l'API, la tendance du journal.** CoinGecko
réserve l'historique de ces agrégats à son offre payante ; le hub
journalise donc un instantané toutes les cinq minutes (§ journal), et
c'est cette accumulation locale que le panneau trace sous les barres —
une tendance qui n'existe qu'à partir du deuxième instantané et
s'allonge séance après séance, jusqu'à couvrir l'année que l'API refuse.
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from ..charts import build_dominance_chart
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Nombre d'actifs détaillés ; le reste est regroupé sous « AUTRES ».
TOP = 8


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Dominance"),
            html.Span(id="dominance-badges",
                      style={"fontSize": "9px", "whiteSpace": "nowrap",
                             "marginLeft": "10px"}),
        ], style=TITLE_STYLE),
        dcc.Graph(
            id="dominance-chart",
            style={"flex": "1", "minHeight": "0"},
            config={"displayModeBar": False},
        ),
    ], style=PANEL_STYLE)


def _badges(agregats: dict):
    """Dominance du Bitcoin, capitalisation totale et volume."""
    if not agregats:
        return html.Span("agrégats indisponibles", style={"color": C["muted"]})

    btc = agregats["shares"].get("BTC")
    variation = agregats.get("cap_change_24h", 0.0)
    children = []
    if btc is not None:
        children += [
            html.Span("BTC ", style={"color": C["muted"]}),
            html.Span(f"{btc:.1f} %", style={"color": C["yellow"]},
                      title="part du Bitcoin dans la capitalisation totale"),
        ]
    children += [
        html.Span(" · cap ", style={"color": C["muted"]}),
        html.Span(f"{agregats['total_cap_usd'] / 1e12:.2f} T$",
                  style={"color": C["text"]},
                  title="capitalisation de l'ensemble des cryptomonnaies"),
        html.Span(f" {variation:+.2f} %",
                  style={"color": C["green"] if variation >= 0 else C["red"]},
                  title="variation de la capitalisation sur 24 h"),
        html.Span(" · vol ", style={"color": C["muted"]}),
        html.Span(f"{agregats['total_volume_usd'] / 1e9:.0f} Md$",
                  style={"color": C["cyan"]}, title="volume échangé sur 24 h"),
    ]
    return html.Span(children, style={"fontFamily": MONO})


def register(app, hub):
    @app.callback(
        Output("dominance-chart", "figure"),
        Output("dominance-badges", "children"),
        Input("tick-rare", "n_intervals"),
    )
    def _refresh(_tick):
        agregats = hub.market_global()
        return (
            build_dominance_chart(agregats.get("shares", {}),
                                  history=hub.market_snapshots(), top=TOP),
            _badges(agregats),
        )
