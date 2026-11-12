"""
Panneau cycle : où en est le bitcoin depuis le halving, et depuis le sommet.

À gauche, le graphique, en trois vues — **CYCLES**, les cycles superposés
en multiple du cours du jour du halving ; **MAYER**, le cours rapporté à
sa moyenne de 200 jours ; **REPLI**, la distance au plus haut couru. À
droite, la situation du jour : compte à rebours du prochain halving, jour
du cycle et ce que valaient les précédents au même jour, sommet et repli,
multiple de Mayer et son rang, moyenne de 200 semaines, Pi cycle
(`btcterm.cycle`).

Tout y est en dollars : ce sont des multiples et des rapports, et les
cycles se comparent dans l'unité où ils ont été cotés.

Sources : les clôtures journalières du disque, la hauteur de la chaîne
chez blockchain.info — sans elle, le compte à rebours est estimé, et le dit.
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from btcterm import cycle

from ..charts import CYCLE_DEFAULT_VIEW, CYCLE_VIEWS, build_cycle_chart
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

_MOIS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.",
         "août", "sept.", "oct.", "nov.", "déc.")

_CELL = {"padding": "2px 6px", "whiteSpace": "nowrap"}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Cycle"),
            html.Div([
                html.Span(id="cycle-badges", style={"fontSize": "9px"}),
                dcc.RadioItems(
                    id="cycle-view",
                    options=[{"label": k, "value": k} for k in CYCLE_VIEWS],
                    value=CYCLE_DEFAULT_VIEW, inline=True,
                    className="tf-radio", style={"marginLeft": "8px"},
                    persistence=True, persistence_type="local",
                ),
            ], style={"display": "flex", "alignItems": "center",
                      "whiteSpace": "nowrap", "overflow": "hidden"}),
        ], style=TITLE_STYLE),
        html.Div([
            dcc.Graph(id="cycle-chart",
                      style={"flex": "1", "minWidth": "0", "minHeight": "0"},
                      config={"displaylogo": False, "scrollZoom": True,
                              "modeBarButtonsToRemove": ["select2d",
                                                         "lasso2d"]}),
            html.Div(id="cycle-table",
                     style={"flexShrink": "0", "overflow": "auto",
                            "marginLeft": "8px"}),
        ], style={"display": "flex", "flex": "1", "minHeight": "0"}),
    ], style=PANEL_STYLE)


def _date(when) -> str:
    return f"{when.day} {_MOIS[when.month - 1]} {when.year}"


def _days(days: float) -> str:
    """Une durée en jours, dite en années et mois au-delà de trois mois."""
    days = int(round(days))
    if days < 90:
        return f"{days} j"
    years, months = divmod(round(days / 30.44), 12)
    if not years:
        return f"{days} j · {months} mois"
    return f"{days} j · {years} an{'s' if years > 1 else ''}" + (
        f" {months} mois" if months else "")


def _mayer_color(value: float) -> str:
    if value >= cycle.MAYER_HIGH:
        return C["red"]
    if value <= cycle.MAYER_LOW:
        return C["green"]
    return C["text"]


def _mvrv_color(value: float) -> str:
    return (C["red"] if value >= cycle.MVRV_HIGH
            else C["green"] if value < cycle.MVRV_LOW else C["text"])


def rows(state: dict | None, mvrv: dict | None = None
         ) -> list[tuple[str, str, str, str]]:
    """Les lignes du tableau : intitulé, valeur, couleur, infobulle.

    Une mesure que la série ne permet pas de calculer n'a pas de ligne.
    """
    if not state:
        return []
    halving = state["halving"]
    out = [(
        "prochain halving",
        f"{'~' if halving['estimated'] else ''}{_date(halving['eta'])}",
        C["text"],
        f"bloc {halving['target']:,} — date comptée à dix minutes le bloc"
        + (" ; hauteur estimée, l'instantané du réseau manque"
           if halving["estimated"] else ""),
    ), (
        "dans",
        f"{_days(halving['days_left'])} · {halving['blocks_left']:,} blocs",
        C["cyan"],
        f"hauteur {halving['height']:,} — {halving['progress'] * 100:.0f} % du "
        "cycle d'émission écoulé",
    )]
    if state["cycle_day"] is not None:
        out.append((
            "depuis le halving",
            f"J+{state['cycle_day']} · ×{state['cycle_multiple']:.2f}",
            C["text"],
            "jours écoulés depuis le dernier halving, et le cours en "
            "multiple de celui de ce jour-là"))
    if state["same_day"]:
        out.append((
            "au même jour",
            " · ".join(f"{row['label']} ×{row['multiple']:.2f}"
                       for row in state["same_day"]),
            C["muted"],
            "ce que valaient les cycles précédents au même jour, en "
            "multiple du cours de leur halving"))
    out.append((
        "sommet",
        f"{state['ath']:,.0f} $ · {_date(state['ath_date'])}",
        C["text"], "plus haute clôture journalière"))
    at_peak = state["drawdown"] > -0.05
    out.append((
        "repli",
        "au sommet" if at_peak else
        f"{state['drawdown']:+z.1f} % · {_days(state['days_since_ath'])}",
        C["green"] if at_peak else C["red"],
        "distance au sommet, et depuis combien de temps"))
    if state["mayer"] is not None:
        rank = state["mayer_rank"]
        out.append((
            "Mayer",
            f"{state['mayer']:.2f}" + (
                f" · au-dessus de {rank * 100:.0f} % des jours"
                if rank is not None else ""),
            _mayer_color(state["mayer"]),
            f"cours / moyenne de 200 jours ({state['ma200']:,.0f} $) ; "
            f"bornes d'usage {cycle.MAYER_LOW:g} et {cycle.MAYER_HIGH:g}"))
    if state["ma200w"] is not None:
        out.append((
            "MA 200 sem.",
            f"{state['ma200w']:,.0f} $ · ×{state['ma200w_ratio']:.2f}",
            C["text"] if state["ma200w_ratio"] >= 1 else C["green"],
            "moyenne de 200 semaines, plancher des marchés baissiers "
            "passés, et le cours en multiple de celle-ci"))
    if state["pi_ratio"] is not None:
        out.append((
            "Pi cycle",
            f"{state['pi_ratio']:.2f}",
            C["red"] if state["pi_ratio"] >= 1 else C["text"],
            "moyenne de 111 jours sur deux fois celle de 350 : 1 aux "
            "sommets de 2017 et 2021"))
    if mvrv is not None:
        rank = mvrv["rank"]
        out.append((
            "MVRV · Coin Metrics",
            f"{mvrv['mvrv']:.2f}" + (f" · réalisé {mvrv['realized']:,.0f} $"
                                     if mvrv["realized"] else ""),
            _mvrv_color(mvrv["mvrv"]),
            "capitalisation sur capitalisation réalisée, au "
            f"{mvrv['date']:%d/%m/%Y} : sous {cycle.MVRV_LOW:g} les creux "
            f"passés, au-dessus de {cycle.MVRV_HIGH:g} les sommets ; le prix "
            "réalisé, prix moyen auquel chaque bitcoin a bougé pour la "
            "dernière fois, est le prix de revient du marché"
            + (f" ; plus haut que {rank * 100:.0f} % des jours depuis 2012"
               if rank is not None else "")
            + " — données Coin Metrics, CC BY-NC"))
    return out


def table(state: dict | None, mvrv: dict | None = None):
    lines = rows(state, mvrv)
    if not lines:
        return html.Div("historique indisponible",
                        style={"fontFamily": MONO, "fontSize": "10px",
                               "color": C["muted"]})
    return html.Table(html.Tbody([
        html.Tr([
            html.Td(label, style={**_CELL, "color": C["muted"]}),
            html.Td(value, style={**_CELL, "color": color,
                                  "textAlign": "right"}),
        ], title=hint)
        for label, value, color, hint in lines
    ]), style={"fontFamily": MONO, "fontSize": "10px",
               "borderCollapse": "collapse"})


def badges(state: dict | None):
    """Le halving, le repli et Mayer, d'un coup d'œil."""
    if not state:
        return html.Span("cycle indisponible", style={"color": C["muted"]})
    halving = state["halving"]
    parts = [html.Span("halving ", style={"color": C["muted"]}),
             html.Span(f"{halving['days_left']:.0f} j",
                       style={"color": C["cyan"]}),
             html.Span(" · sommet ", style={"color": C["muted"]}),
             html.Span(f"{state['drawdown']:+z.1f} %",
                       style={"color": C["red"] if state["drawdown"] < -0.05
                              else C["green"]})]
    if state["mayer"] is not None:
        parts += [html.Span(" · Mayer ", style={"color": C["muted"]}),
                  html.Span(f"{state['mayer']:.2f}",
                            style={"color": _mayer_color(state["mayer"])})]
    return html.Span(parts, style={"fontFamily": MONO},
                     title="jours avant le prochain halving, distance au "
                           "sommet, cours / moyenne de 200 jours")


def register(app, hub):
    @app.callback(
        Output("cycle-chart", "figure"),
        Output("cycle-table", "children"),
        Output("cycle-badges", "children"),
        Input("tick-rare", "n_intervals"),
        Input("cycle-view", "value"),
        Input("expanded", "data"),
    )
    def _refresh(_tick, view, expanded):
        maximized = expanded == "cycle"
        view = view if view in CYCLE_VIEWS else CYCLE_DEFAULT_VIEW
        closes = hub.cycle_closes()
        height = (hub.chain_stats() or {}).get("height")
        state = cycle.metrics(closes, height)
        # La révision suit la vue et l'agrandissement : en changer
        # recadre, le tour d'horloge non.
        mvrv = hub.mvrv()
        figure = build_cycle_chart(
            view, closes, maximized=maximized,
            uirevision=f"cycle:{view}:{maximized}", mvrv=mvrv)
        return figure, table(state, cycle.mvrv_state(mvrv)), badges(state)
