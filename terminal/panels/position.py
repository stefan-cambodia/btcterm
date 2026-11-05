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


def render_state(state: dict | None, fx):
    """La position tenue, ou ce qu'il manque pour la calculer."""
    if state is None:
        return html.Div("saisir une quantité et un prix d'entrée pour suivre "
                        "une position", style={"color": C["muted"]})
    tone = "green" if state["pnl"] >= 0 else "red"
    lines = [
        _line("valeur", _money(state["value"], fx)),
        _line("résultat", f"{_money(state['pnl'], fx, True)} "
                          f"({state['pnl_pct']:+.2f} %)", tone),
    ]
    if state["stop_pct"] is not None:
        lines.append(_line(
            "jusqu'au stop", f"{state['stop_pct']:.2f} % · "
            f"{_money(state['stop_loss'], fx, True)}",
            "red" if state["stop_pct"] <= 0 else "text"))
    if state["r"] is not None:
        lines.append(_line("multiple", f"{state['r']:+.2f} R", tone,
                           title="résultat rapporté à la perte prévue au stop"))
    return html.Div(lines)


def render_size(size: dict | None, side: str, fx):
    """La taille que permet le risque choisi."""
    if size is None:
        return html.Div("saisir un capital et un risque pour dimensionner",
                        style={"color": C["muted"]})
    sign = -1 if side == "long" else 1
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
        size = position_size(capital, risk,
                             atr * multiple if atr and multiple else None,
                             price)
        return [render_state(state, fx), render_size(size, side, fx)]
