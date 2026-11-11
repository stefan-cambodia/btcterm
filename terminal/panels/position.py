"""
Panneau position : ce que le marché fait à la vôtre, et combien en prendre.

Deux questions qu'un terminal de marché seul ne pose pas. À gauche, la position
tenue : résultat, distance au stop, multiple du risque pris. À droite, la taille
qu'autorise un risque choisi, le stop étant placé à quelques ATR journaliers :
c'est la volatilité du moment, pas l'envie, qui dit combien en prendre.

Le terminal ne parle à aucun compte : tout se saisit à la main, et rien ne
quitte le navigateur. Les champs vivent dans le `localStorage`, le serveur ne
les reçoit que pour faire le calcul et n'en écrit rien. Les prix saisis sont en
dollars, comme les seuils d'alerte et les marchés eux-mêmes.
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from btcterm.analysis import (position_size, position_state,
                              timeframe_summary)

from ..fx import display
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE
from .alerts import _INPUT
from .price import INTERVALS

#: Multiple d'ATR journalier du stop proposé : deux, l'usage courant.
ATR_MULTIPLE = 2.0


def _field(label: str, field_id: str, value=None, step="any"):
    return html.Div([
        html.Span(label, style={"color": C["muted"], "fontSize": "9px",
                                "display": "block"}),
        dcc.Input(id=field_id, type="number", value=value, min=0, step=step,
                  debounce=True, persistence=True, persistence_type="local",
                  style={**_INPUT, "width": "70px"}),
    ], style={"display": "inline-block", "marginRight": "6px"})


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Position"),
            dcc.RadioItems(
                id="position-side",
                options=[{"label": "long", "value": "long"},
                         {"label": "short", "value": "short"}],
                value="long", inline=True, className="tf-radio",
                persistence=True, persistence_type="local",
                style={"fontSize": "9px"},
            ),
        ], style=TITLE_STYLE),
        html.Div([
            _field("quantité (BTC)", "position-qty"),
            _field("entrée ($)", "position-entry"),
            _field("stop ($)", "position-stop"),
            _field("capital ($)", "position-capital"),
            _field("risque (%)", "position-risk", 1),
            _field("stop (× ATR 1d)", "position-atr", ATR_MULTIPLE),
        ], style={"padding": "2px 6px"}),
        html.Div(id="position-result",
                 style={"flex": "1", "overflowY": "auto", "padding": "2px 8px",
                        "fontFamily": MONO, "fontSize": "10px",
                        "display": "flex", "gap": "20px"}),
    ], style=PANEL_STYLE)


def _line(label: str, value: str, color: str = "text", title: str | None = None):
    return html.Div([
        html.Span(label, style={"color": C["muted"], "display": "inline-block",
                                "width": "104px"}),
        html.Span(value, style={"color": C[color]}, title=title),
    ])


def _money(value: float, fx, signed: bool = False) -> str:
    return f"{value * fx.rate:{'+' if signed else ''},.2f} {fx.unit}"


def render_state(state: dict | None, fx, missing: str | None = None):
    """La position tenue, ou ce qu'il manque pour la calculer.

    `missing` dit pourquoi, quand ce n'est pas la saisie : sans cours, le
    panneau demandait une quantité et un prix d'entrée déjà saisis.
    """
    if state is None:
        return html.Div(missing or "saisir une quantité et un prix d'entrée "
                        "pour suivre une position", style={"color": C["muted"]})
    tone = "green" if state["pnl"] >= 0 else "red"
    lines = [
        _line("valeur", _money(state["value"], fx)),
        _line("résultat", f"{_money(state['pnl'], fx, True)} "
                          f"({state['pnl_pct']:+.2f} %)", tone),
    ]
    if state["stop_pct"] is not None:
        # Un stop que le cours a déjà dépassé — ou posé du mauvais côté :
        # sous le cours pour un short, au-dessus pour un long — ne protège
        # plus rien, et une distance négative ne le disait qu'à demi.
        if state["stop_pct"] <= 0:
            # Court : la ligne partage la largeur avec le dimensionnement,
            # et une phrase y passait à la ligne en écrasant sa voisine.
            lines.append(_line(
                "stop franchi", f"de {-state['stop_pct']:.2f} %", "red",
                title="le cours a dépassé ce stop : il aurait déjà joué, ou "
                      "il est du mauvais côté — sous le cours pour un short, "
                      "au-dessus pour un long"))
        else:
            lines.append(_line(
                "jusqu'au stop", f"{state['stop_pct']:.2f} % · "
                f"{_money(state['stop_loss'], fx, True)}"))
    if state["r"] is not None:
        lines.append(_line("multiple", f"{state['r']:+.2f} R", tone,
                           title="résultat rapporté à la perte prévue au stop"))
    return html.Div(lines)


def size_gap(capital, risk, multiple, atr, price) -> str | None:
    """Ce qui empêche de dimensionner, dit pour ce que c'est — ou `None`.

    Le panneau répondait « saisir un capital et un risque » à tout : à un
    multiple d'ATR effacé, à un cours absent, à des bougies journalières
    que Binance ne servait pas. Chaque cas a son message, et deux saisies
    qui n'ont pas de sens ont le leur : un risque de plus de cent pour
    cent du capital, un stop que le multiple pousserait sous zéro.
    """
    if not capital or not risk or capital <= 0 or risk <= 0:
        return "saisir un capital et un risque pour dimensionner"
    if risk > 100:
        return "un risque de plus de 100 % du capital ne se dimensionne pas"
    if not multiple or multiple <= 0:
        return "saisir un multiple d'ATR pour placer le stop"
    if not price:
        return "cours indisponible : pas de dimensionnement"
    if not atr:
        return ("ATR journalier indisponible — les bougies d'un jour "
                "manquent : pas de dimensionnement")
    return None


def render_size(size: dict | None, side: str, fx, missing: str | None = None):
    """La taille que permet le risque choisi."""
    if size is None:
        return html.Div(missing or "saisir un capital et un risque pour "
                        "dimensionner", style={"color": C["muted"]})
    sign = -1 if side == "long" else 1
    if sign < 0 and size["distance"] >= size["price"]:
        return html.Div("à ce multiple d'ATR, le stop d'un long tomberait "
                        "sous zéro", style={"color": C["muted"]})
    return html.Div([
        _line("perte acceptée", _money(size["risk"], fx)),
        _line("stop proposé", _money(size["price"] + sign * size["distance"], fx),
              title=f"à {size['distance_pct']:.2f} % du cours"),
        _line("taille", f"{size['quantity']:.4f} BTC · "
                        f"{_money(size['value'], fx)}", "yellow"),
        _line("levier implicite", f"{size['leverage']:.2f} ×",
              "red" if size["leverage"] > 1 else "text",
              title="valeur de la position rapportée au capital"),
    ])


def register(app, hub):
    # Le bandeau suit la position même panneau replié, comme il suit la
    # devise : le Store global survit au démontage des champs.
    app.clientside_callback(
        "function (side, quantity, entry) {"
        " return {side: side, quantity: quantity, entry: entry}; }",
        Output("position", "data"),
        Input("position-side", "value"),
        Input("position-qty", "value"),
        Input("position-entry", "value"),
    )

    @app.callback(
        Output("position-result", "children"),
        Input("tick-slow", "n_intervals"),
        Input("position-side", "value"),
        Input("position-qty", "value"),
        Input("position-entry", "value"),
        Input("position-stop", "value"),
        Input("position-capital", "value"),
        Input("position-risk", "value"),
        Input("position-atr", "value"),
        Input("currency", "data"),
    )
    def _refresh(_tick, side, quantity, entry, stop, capital, risk, multiple,
                 currency):
        fx = display(hub, currency)
        price = hub.reference_price()
        state = position_state(side, quantity, entry, price, stop)
        summary = timeframe_summary(hub.klines("1d", limit=INTERVALS["1d"]))
        atr = (summary["atr_pct"] / 100 * price
               if summary and summary["atr_pct"] and price else None)
        gap = size_gap(capital, risk, multiple, atr, price)
        size = None if gap else position_size(capital, risk, atr * multiple,
                                              price)
        sans_cours = ("cours indisponible : la position ne se suit pas"
                      if quantity and entry and not price else None)
        return [render_state(state, fx, sans_cours),
                render_size(size, side, fx, gap)]
