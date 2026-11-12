"""
Panneau sources : ce qui répond, ce qui sert de secours, ce qui se tait.

Le terminal lit une vingtaine de sources — cinq carnets WebSocket, deux
fils de liquidations, une quinzaine d'API REST, des tâches de fond. Quand
l'une tombe, le bandeau en dit le nombre ; le détail, lui, n'était qu'au
journal du service. Ce panneau le pose à l'écran, en trois sections :
flux temps réel, sources REST, tâches de fond — pour chacune l'état,
l'âge de la dernière donnée fraîche et ce qu'il faut savoir : l'erreur en
une ligne, ou ce que la source fait.

Les états : **ok** ; **secours** — la source a échoué à sa dernière
lecture, sa valeur précédente est servie (§4.3) ; **panne** — échec vu
dans le dernier quart d'heure ; **muet** — connecté, mais rien ne vient ;
**coupé** — la connexion ne tient pas ; **inactif** — désactivé au
lancement. L'état s'écrit en toutes lettres à côté de sa pastille : la
couleur ne le porte jamais seule.

Le clic sur l'avertissement du bandeau (« ⚠ 2 sources ») ouvre ce panneau.
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Rafraîchissement propre au panneau, seulement quand il est affiché.
REFRESH_MS = 10_000

#: Les familles du cache, nommées pour un lecteur : la donnée, puis sa
#: source. Une famille inconnue garde sa clé.
FAMILIES = {
    "klines": "Chandeliers · Binance",
    "ticker": "Ticker 24 h · Binance",
    "perp": "Perpétuel · Binance Futures",
    "funding": "Financement · Binance Futures",
    "oi": "Open interest · Binance Futures",
    "long_short": "Positionnement · Binance Futures",
    "perp_bybit": "Perpétuel · Bybit",
    "perp_okx": "Perpétuel · OKX",
    "options": "Chaîne d'options · Deribit",
    "futures": "Futures à échéance · Deribit",
    "dvol": "DVOL · Deribit",
    "market": "Marchés traditionnels · Yahoo",
    "global": "Agrégats de marché · CoinGecko",
    "etf": "Flux ETF · farside",
    "chain_stats": "Réseau · blockchain.info",
    "chain": "Séries de chaîne · blockchain.info",
    "fees": "Frais de transaction · blockstream.info",
    "m2": "Masse monétaire M2 · DBnomics",
    "stables": "Offre de stablecoins · DefiLlama",
    "eur": "Change €/$",
    "week_calendar": "Calendrier de la semaine · Forex Factory",
    "upbit": "Prime coréenne · Upbit",
    "krw": "Change ₩/$ · Yahoo, BCE",
    "fear_greed_history": "Fear & Greed · alternative.me",
}

STATE_COLORS = {"ok": C["green"], "secours": C["yellow"], "panne": C["red"],
                "muet": C["orange"], "coupé": C["red"], "inactif": C["muted"]}

_CELL = {"padding": "2px 6px", "whiteSpace": "nowrap", "textAlign": "left"}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Sources"),
            html.Span(id="sources-badges", style={"fontSize": "9px"}),
        ], style=TITLE_STYLE),
        html.Div(id="sources-table", style={"flex": "1", "minHeight": "0",
                                            "overflow": "auto"}),
        dcc.Interval(id="sources-tick", interval=REFRESH_MS),
    ], style=PANEL_STYLE)


def _age(seconds) -> str:
    """Un âge lisible : secondes, minutes, heures, jours."""
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    if seconds < 2 * 86_400:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86_400:.0f} j"


def _row(row: dict):
    color = STATE_COLORS.get(row["state"], C["muted"])
    name = FAMILIES.get(row["name"], row["name"])
    detail = row.get("detail") or ""
    if row.get("keys", 0) > 1:
        detail = f"{detail} · {row['keys']} lectures".lstrip(" ·")
    return html.Tr([
        html.Td(name, style={**_CELL, "color": C["text"]}),
        html.Td([html.Span("● ", style={"color": color}),
                 html.Span(row["state"], style={"color": C["text"]})],
                style=_CELL),
        html.Td(_age(row.get("age")), style={**_CELL, "textAlign": "right",
                                              "color": C["muted"]},
                title="âge de la dernière donnée fraîche"),
        html.Td(detail, style={**_CELL, "color": C["muted"],
                               "whiteSpace": "normal"}),
    ])


#: Ordre des états dans une section : ce qui ne va pas d'abord.
_RANK = {"panne": 0, "coupé": 0, "muet": 1, "secours": 2, "ok": 3, "inactif": 4}


def table(status: dict[str, list[dict]]):
    """Les trois sections, chacune titrée. Dans chaque section, ce qui ne va
    pas d'abord, puis l'ordre alphabétique des libellés — les clés internes
    du cache donnaient un ordre sans logique pour un lecteur."""
    body = []
    for section, rows in status.items():
        rows = sorted(rows, key=lambda row: (
            _RANK.get(row["state"], 5), FAMILIES.get(row["name"], row["name"])))
        body.append(html.Tr(html.Td(section, colSpan=4, style={
            **_CELL, "color": C["muted"], "paddingTop": "6px",
            "borderBottom": f"1px solid {C['border']}",
            "textTransform": "uppercase", "letterSpacing": "0.1em"})))
        body += [_row(row) for row in rows]
    return html.Table(html.Tbody(body), style={
        "width": "100%", "fontFamily": MONO, "fontSize": "10px",
        "borderCollapse": "collapse"})


def badges(status: dict[str, list[dict]]):
    """Le compte des sources qui ne répondent pas comme elles devraient."""
    rows = [row for rows in status.values() for row in rows]
    bad = [row for row in rows if row["state"] in ("panne", "coupé", "muet")]
    stale = [row for row in rows if row["state"] == "secours"]
    if not bad and not stale:
        return html.Span(f"{len(rows)} sources, toutes répondent",
                         style={"color": C["green"], "fontFamily": MONO})
    parts = []
    if bad:
        parts.append(html.Span(f"{len(bad)} en défaut",
                               style={"color": C["red"]}))
    if stale:
        if parts:
            parts.append(html.Span(" · ", style={"color": C["muted"]}))
        parts.append(html.Span(f"{len(stale)} de secours",
                               style={"color": C["yellow"]}))
    parts.append(html.Span(f" sur {len(rows)}", style={"color": C["muted"]}))
    return html.Span(parts, style={"fontFamily": MONO})


def register(app, hub):
    @app.callback(
        Output("sources-table", "children"),
        Output("sources-badges", "children"),
        Input("sources-tick", "n_intervals"),
    )
    def _refresh(_tick):
        status = hub.sources_status()
        return table(status), badges(status)
