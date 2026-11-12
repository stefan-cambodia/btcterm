"""
Panneau analyse : où en est le marché, et ce que valent les signaux.

Deux tableaux côte à côte. À gauche, chaque intervalle d'un coup d'œil, ce
qu'il faudrait sinon aller lire graphique par graphique. À droite, au choix,
le bilan des signaux du panneau prix sur l'historique sauvegardé — les
flèches du graphique n'avaient jamais été mises à l'épreuve — ou quelques
règles simples face au fait de rester investi.

Les calculs sont dans `btcterm.analysis`, et leurs réserves aussi : le bilan
décrit le passé, sans frais, et ses fenêtres se recouvrent. Le panneau les
rappelle à l'écran, en face des chiffres.

L'horloge est la sienne : la rare (5 min) laisserait vieillir le RSI du quart
d'heure, la lente (2 s) demanderait cinq fins de série pour un tableau qui
ne change pas si vite. Elle ne tourne que panneau affiché.
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from btcterm.analysis import MIN_SAMPLE, WEEKDAYS, timeframe_summary

from ..theme import C, MONO, PANEL_STYLE, TABLE_STYLE, TITLE_STYLE
from .price import INTERVALS

#: Intervalles du tableau de gauche.
SUMMARY_INTERVALS = ("15m", "1h", "4h", "1d", "1w")

#: Intervalles du bilan : en deçà de l'heure, l'historique relu est court.
STATS_INTERVALS = ("1h", "4h", "1d", "1w")

REFRESH_MS = 30_000

MONTH_LABELS = ("jan", "fév", "mar", "avr", "mai", "jun", "jul", "aoû", "sep",
                "oct", "nov", "déc")

GRADE_LABEL = {2: "achat fort", 1: "achat", -1: "vente", -2: "vente forte"}
TREND_LABEL = {"up": ("haussier", "green"), "down": ("baissier", "red"),
               "mixed": ("mixte", "yellow")}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Analyse"),
            html.Div([
                dcc.RadioItems(
                    id="analysis-view",
                    options=[{"label": "signaux", "value": "signals"},
                             {"label": "règles", "value": "rules"},
                             {"label": "mois", "value": "months"},
                             {"label": "heures", "value": "hours"}],
                    value="signals", inline=True, className="tf-radio",
                    persistence=True, persistence_type="local",
                    style={"display": "inline-block", "fontSize": "9px",
                           "marginRight": "10px"},
                ),
                dcc.RadioItems(
                    id="analysis-interval",
                    options=[{"label": k, "value": k} for k in STATS_INTERVALS],
                    value="1d", inline=True, className="tf-radio",
                    persistence=True, persistence_type="local",
                    style={"display": "inline-block", "fontSize": "9px"},
                ),
                html.A("CSV", id="analysis-export", href="/api/export?interval=1d",
                       title="exporter l'historique sauvegardé de cet intervalle",
                       style={"color": C["cyan"], "fontSize": "9px",
                              "marginLeft": "10px", "textDecoration": "none"}),
            ], style={"display": "flex", "alignItems": "center",
                      "whiteSpace": "nowrap"}),
        ], style=TITLE_STYLE),
        dcc.Interval(id="analysis-tick", interval=REFRESH_MS),
        html.Div([
            html.Div(id="analysis-summary", style={"flex": "0 0 38%"}),
            html.Div(id="analysis-stats", style={"flex": "1", "minWidth": "0"}),
        ], style={"display": "flex", "gap": "18px", "flex": "1",
                  "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def _header(*labels):
    return html.Thead(html.Tr([
        html.Th(label, style={"color": C["muted"], "fontSize": "9px",
                              "textAlign": "left" if index == 0 else "right",
                              "padding": "2px 6px", "fontWeight": "400"})
        for index, label in enumerate(labels)
    ]))


def _cell(text, color=None, align="right"):
    return html.Td(text, style={"color": C[color or "text"], "textAlign": align,
                                "padding": "1px 6px", "whiteSpace": "nowrap"})


def _summary_row(interval: str, state: dict | None):
    if state is None:
        return html.Tr([_cell(interval, "muted", "left"),
                        _cell("historique insuffisant", "muted"),
                        _cell(""), _cell(""), _cell(""), _cell("")])
    label, color = TREND_LABEL[state["trend"]]
    rsi = state["rsi"]
    if state["signal"]:
        signal = (f"{GRADE_LABEL[state['signal']]} "
                  f"· il y a {state['signal_age']} b.")
        tone = "green" if state["signal"] > 0 else "red"
    else:
        signal, tone = "—", "muted"
    return html.Tr([
        _cell(interval, "muted", "left"),
        _cell(label, color),
        _cell(f"{state['gap']:+z.1f} %", "green" if state["gap"] >= 0 else "red"),
        _cell(f"{rsi:.0f}", "red" if rsi >= 70 else "green" if rsi <= 30 else None),
        _cell("—" if state["atr_pct"] is None else f"{state['atr_pct']:.1f} %",
              "muted"),
        _cell(signal, tone),
    ])


def render_summary(hub):
    """Le tableau des intervalles, à leur dernière bougie."""
    rows = [_summary_row(interval, timeframe_summary(
                hub.klines(interval, limit=INTERVALS[interval])))
            for interval in SUMMARY_INTERVALS]
    return html.Table([_header("", "tendance", "MA 200", "RSI", "ATR",
                               "dernier signal"),
                       html.Tbody(rows)], style=TABLE_STYLE)


def _stats_cells(cell: dict | None):
    if cell is None:
        return [_cell("—", "muted"), _cell("—", "muted"), _cell("—", "muted")]
    # Sous l'effectif utile, le chiffre reste affiché mais éteint : le lire
    # en vert ou en rouge lui prêterait une assurance qu'il n'a pas.
    solid = cell["count"] >= MIN_SAMPLE
    edge = "green" if cell["edge"] > 0 else "red"
    return [
        _cell(f"{cell['mean']:+z.2f} %", None if solid else "muted"),
        _cell(f"{cell['hit']:.0f} %", None if solid else "muted"),
        _cell(f"{cell['edge']:+z.2f} %", edge if solid else "muted"),
    ]


def render_stats(stats: dict | None):
    """Le bilan d'un intervalle, ses réserves écrites en dessous."""
    if stats is None:
        return html.Div("historique insuffisant pour ce bilan",
                        style={"color": C["muted"], "fontFamily": MONO,
                               "fontSize": "11px", "padding": "12px"})
    horizons = stats["horizons"]
    labels = ["signal", "n"]
    for horizon in horizons:
        labels += [f"{horizon} b. moy.", "réussite", "écart"]
    rows = [
        html.Tr([
            _cell(GRADE_LABEL[row["grade"]],
                  "green" if row["grade"] > 0 else "red", "left"),
            _cell(str(row["count"]),
                  None if row["count"] >= MIN_SAMPLE else "muted"),
            *[td for horizon in horizons
              for td in _stats_cells(row["cells"][horizon])],
        ])
        for row in stats["rows"]
    ]
    base = " · ".join(f"{stats['base'][h]:+z.2f} % à {h} bougies"
                      for h in horizons)
    note = (f"{stats['bars']:,} bougies closes, du {stats['first']:%d/%m/%Y} au "
            f"{stats['last']:%d/%m/%Y} · rester investi : {base} · écart : avance "
            f"sur cette référence, dans le sens du signal · entrée à l'ouverture "
            f"suivante, sans frais · n < {MIN_SAMPLE} en gris · le passé ne "
            f"promet rien")
    return html.Div([
        html.Table([_header(*labels), html.Tbody(rows)], style=TABLE_STYLE),
        html.Div(note, style={"color": C["muted"], "fontFamily": MONO,
                              "fontSize": "9px", "padding": "6px"}),
    ])


def render_rules(stats: dict | None):
    """Les règles simples face à la détention, sur la même période."""
    if stats is None:
        return html.Div("historique insuffisant pour ce bilan",
                        style={"color": C["muted"], "fontFamily": MONO,
                               "fontSize": "11px", "padding": "12px"})
    hold = stats["rows"][0]
    rows = []
    for row in stats["rows"]:
        better = row["annual"] > hold["annual"]
        safer = row["drawdown"] > hold["drawdown"]
        rows.append(html.Tr([
            _cell(row["label"], None, "left"),
            _cell(f"{row['annual']:+z.1f} %",
                  None if row is hold else "green" if better else "red"),
            _cell(f"{row['total']:+z,.0f} %"),
            _cell(f"{row['drawdown']:.1f} %",
                  None if row is hold else "green" if safer else "red"),
            _cell(f"{row['exposure']:.0f} %"),
            _cell(str(row["entries"])),
            _cell(f"{'investie' if row['invested'] else 'hors marché'} "
                  f"depuis {row['since']:%d/%m/%Y}",
                  "green" if row["invested"] else "yellow"),
        ]))
    note = (f"du {stats['first']:%d/%m/%Y} au {stats['last']:%d/%m/%Y}, "
            f"{stats['years']:.1f} ans · entrée à l'ouverture qui suit la "
            f"clôture · frais {stats['fee']:g} % par passage · liquidités "
            f"sans rendement · le passé ne promet rien")
    return html.Div([
        html.Table([_header("règle", "par an", "total", "repli max",
                            "investi", "entrées", "aujourd'hui"),
                    html.Tbody(rows)],
                   style=TABLE_STYLE),
        html.Div(note, style={"color": C["muted"], "fontFamily": MONO,
                              "fontSize": "9px", "padding": "6px"}),
    ])


def _heat(value: float | None, text: str, title: str | None = None):
    """Une case de la carte : plus le mois a bougé, plus sa teinte est franche."""
    style = {"textAlign": "right", "padding": "0 4px", "whiteSpace": "nowrap",
             "lineHeight": "12px"}
    if value is None:
        return html.Td(text, style={**style, "color": C["muted"]})
    alpha = min(0x70, 0x12 + int(abs(value) / 40 * 0x5e))
    return html.Td(text, title=title, style={
        **style, "color": C["text"],
        "background": f"{C['green'] if value >= 0 else C['red']}{alpha:02x}"})


def render_months(stats: dict | None):
    """La carte des rendements mensuels, année la plus récente en haut."""
    if stats is None:
        return html.Div("historique insuffisant pour ce bilan",
                        style={"color": C["muted"], "fontFamily": MONO,
                               "fontSize": "11px", "padding": "12px"})
    rows = []
    for year in sorted(stats["grid"], reverse=True):
        row = stats["grid"][year]
        rows.append(html.Tr([
            _heat(None, str(year)),
            *[_heat(row.get(month),
                    f"{row[month]:+z.0f}" if month in row else "")
              for month in range(1, 13)],
            _heat(stats["years"][year], f"{stats['years'][year]:+z.0f}"),
        ]))
    moyenne = stats["months"]
    rows.append(html.Tr([
        _heat(None, "moy."),
        *[_heat(moyenne[month]["mean"], f"{moyenne[month]['mean']:+z.0f}",
                f"{moyenne[month]['up']:.0f} % de mois haussiers")
          if month in moyenne else _heat(None, "")
          for month in range(1, 13)],
        _heat(None, ""),
    ]))
    # Neuf années font neuf points par mois : la réserve est écrite sous la
    # carte, comme celles du bilan sous son tableau.
    note = (f"{len(stats['grid'])} années, donc autant de points par mois · "
            f"une description du passé, pas une saisonnalité établie")
    return html.Div([
        html.Table([_header("%", *MONTH_LABELS, "an"), html.Tbody(rows)],
                   style={**TABLE_STYLE, "fontSize": "9px"}),
        html.Div(note, style={"color": C["muted"], "fontFamily": MONO,
                              "fontSize": "9px", "padding": "6px"}),
    ])


def _activity(value: float | None, top: float, text: str,
              title: str | None = None, bold: bool = False):
    """Une case de la carte des heures : plus l'heure bouge, plus elle luit."""
    style = {"textAlign": "right", "padding": "0 2px", "whiteSpace": "nowrap",
             "lineHeight": "12px", "fontWeight": "600" if bold else "400"}
    if value is None or not top:
        return html.Td(text, title=title, style={**style, "color": C["muted"]})
    alpha = 0x0c + int(min(1.0, value / top) * 0x64)
    return html.Td(text, title=title, style={
        **style, "color": C["text"], "background": f"{C['yellow']}{alpha:02x}"})


def _offset_label(offset: int) -> str:
    if not offset:
        return "UTC"
    hours, minutes = divmod(abs(offset) // 60, 60)
    return (f"UTC{'+' if offset > 0 else '-'}{hours}"
            + (f":{minutes:02d}" if minutes else ""))


def render_hours(stats: dict | None):
    """Quand le bitcoin bouge : l'amplitude moyenne par jour et par heure.

    Les valeurs sont en points de base — 71 pour 0,71 % — : vingt-quatre
    colonnes de « 0.71 » ne tenaient pas dans la grille. La dernière ligne
    dit le sens : le rendement moyen de chaque heure, vert ou rouge.
    """
    if stats is None:
        return html.Div("historique horaire insuffisant pour ce bilan",
                        style={"color": C["muted"], "fontFamily": MONO,
                               "fontSize": "11px", "padding": "12px"})
    top = max((cell["range"] for row in stats["grid"].values()
               for cell in row.values()), default=0.0)

    def bp(value: float) -> str:
        return f"{value * 100:.0f}"

    def detail(label: str, cell: dict) -> str:
        return (f"{label} : amplitude moyenne {cell['range']:.2f} %, "
                f"rendement moyen {cell['ret']:+z.3f} %, {cell['up']:.0f} % de "
                f"bougies haussières, {cell['n']} bougies")

    rows = []
    for day, name in enumerate(WEEKDAYS):
        row = stats["grid"].get(day, {})
        marge = stats["days"].get(day)
        rows.append(html.Tr([
            _activity(None, top, name),
            *[_activity(row[hour]["range"], top, bp(row[hour]["range"]),
                        detail(f"{name} {hour} h", row[hour]))
              if hour in row else _activity(None, top, "")
              for hour in range(24)],
            _activity(marge["range"], top, bp(marge["range"]),
                      detail(name, marge), bold=True)
            if marge else _activity(None, top, ""),
        ]))
    hours = stats["hours"]
    rows.append(html.Tr([
        _activity(None, top, "moy."),
        *[_activity(hours[h]["range"], top, bp(hours[h]["range"]),
                    detail(f"{h} h", hours[h]), bold=True)
          if h in hours else _activity(None, top, "")
          for h in range(24)],
        _activity(None, top, ""),
    ]))
    # Le sens de chaque heure : un rendement moyen, en points de base.
    rows.append(html.Tr([
        _activity(None, top, "sens"),
        *[html.Td(f"{hours[h]['ret'] * 100:+z.0f}" if h in hours else "",
                  title=(f"{h} h : {hours[h]['up']:.0f} % de bougies haussières"
                         if h in hours else None),
                  style={"textAlign": "right", "padding": "0 2px",
                         "lineHeight": "12px",
                         "color": C["green"] if h in hours
                         and hours[h]["ret"] >= 0 else C["red"]})
          for h in range(24)],
        _activity(None, top, ""),
    ]))
    note = (f"{stats['bars']} bougies horaires, du {stats['first']:%d/%m/%y} au "
            f"{stats['last']:%d/%m/%y} · heures locales du serveur "
            f"({_offset_label(stats['offset'])}) · amplitude moyenne (haut − bas) "
            "en points de base, 100 = 1 % · une description du passé, pas une "
            "promesse")
    return html.Div([
        html.Table([_header("pb", *[str(h) for h in range(24)], "moy."),
                    html.Tbody(rows)],
                   style={**TABLE_STYLE, "fontSize": "9px"}),
        html.Div(note, style={"color": C["muted"], "fontFamily": MONO,
                              "fontSize": "9px", "padding": "6px"}),
    ])


def register(app, hub):
    @app.callback(
        Output("analysis-summary", "children"),
        Input("analysis-tick", "n_intervals"),
    )
    def _summary(_tick):
        return render_summary(hub)

    @app.callback(
        Output("analysis-stats", "children"),
        Output("analysis-export", "href"),
        Input("analysis-interval", "value"),
        Input("analysis-view", "value"),
        Input("analysis-tick", "n_intervals"),
    )
    def _stats(interval, view, _tick):
        if view == "months":
            body = render_months(hub.monthly_returns())
        elif view == "hours":
            body = render_hours(hub.hourly_profile())
        elif view == "rules":
            body = render_rules(hub.rule_stats(interval))
        else:
            body = render_stats(hub.signal_stats(interval))
        return body, f"/api/export?interval={interval}"
