"""
Panneau liquidations : les positions fermées de force.

Une liquidation est une position à levier que la plateforme ferme au
marché faute de marge. Elles arrivent par rafales, et ces rafales
expliquent une partie des mèches du graphique du cours — c'est pour cela
qu'elles se lisent à côté du carnet, pas dans un onglet de navigateur.

Panneau « rapide » : il ne fait aucun appel réseau, il lit la fenêtre
glissante que le fil du hub entretient en mémoire.

**Le silence est normal — jusqu'à un point.** Le flux est épisodique :
plusieurs minutes sans une seule liquidation ne signalent aucune panne,
et le panneau distingue les deux — un flux coupé le dit, un flux calme
aussi. Mais un lien qui tient sans rien livrer depuis un quart d'heure
(`SILENCE`) n'est plus un marché calme : c'est un flux qui ne livre plus
— Binance l'a fait des mois, écouté à une adresse que la scission de ses
flux futures avait vidée —, et le badge le nomme, en jaune, pas en
rouge, le fil vivant encore par les autres plateformes.
"""

from __future__ import annotations

import time
from datetime import datetime

from dash import Input, Output, html

from ..fx import DOLLARS, display
from ..theme import C, MONO, PANEL_STYLE, TABLE_STYLE, TITLE_STYLE

#: Lignes affichées dans la grille ; le plein écran en montre davantage.
ROWS = 8
ROWS_MAX = 24

#: Fenêtre des totaux affichés dans la barre de titre.
WINDOW = 3600

#: Silence au-delà duquel un lien ouvert est dit muet. Binance diffuse
#: toutes ses paires : un quart d'heure sans une seule liquidation ne
#: s'y voit pas en marché ouvert. Bybit, sur dix paires, se tait plus
#: volontiers, et le seuil est le même — le badge dit « muet », pas
#: « en panne », et l'âge affiché laisse juger.
SILENCE = 15 * 60

SIDE_LABEL = {"long": ("LONG", C["red"]), "short": ("SHORT", C["green"])}

#: Étiquette courte de la plateforme, dans la table — les mêmes
#: abréviations que le sélecteur du carnet.
EXCHANGE_TAG = {"Binance": "BIN", "Bybit": "BYB", "OKX": "OKX"}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Liquidations"),
            html.Span(id="liq-badges",
                      style={"fontSize": "9px", "whiteSpace": "nowrap",
                             "marginLeft": "10px"}),
        ], style=TITLE_STYLE),
        html.Div(id="liq-table", style={"flex": "1", "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def _montant(valeur: float, unit: str = "$") -> str:
    """Montant abrégé — les liquidations vont du k$ au M$."""
    if valeur >= 1e6:
        return f"{valeur / 1e6:.2f} M{unit}"
    if valeur >= 1e3:
        return f"{valeur / 1e3:.0f} k{unit}"
    return f"{valeur:.0f} {unit}"


def _badges(feed, fx=DOLLARS, verbose: bool = True):
    """Totaux de l'heure, part du Bitcoin, et état du flux.

    Dans la grille (`verbose` faux), une version courte : la cellule de
    l'arbitrage porte quatre onglets depuis le panneau SOURCES, et il n'y
    reste qu'environ 200 px — « aucune liquidation depuis une heure » y
    débordait. Le détail passe au survol, et s'écrit en plein écran.
    """
    if not verbose:
        return _badges_compact(feed, fx)
    if not feed.connected:
        return html.Span(f"flux coupé{' · ' + feed.error if feed.error else ''}",
                         style={"color": C["red"]})

    # Un lien sur deux qui manque se dit, en queue de badge : le fil
    # reste vivant, mais ne voit qu'une plateforme.
    manque = feed.missing()
    sans = ([html.Span(" · sans " + ", ".join(manque),
                       style={"color": C["red"]},
                       title=feed.error or "lien non établi")]
            if manque else [])

    # Un lien qui tient sans rien livrer se dit aussi : rien d'autre ne
    # le signalerait — Binance l'a fait des mois sans une erreur.
    muets = [html.Span(f" · {name} muet depuis {int(age // 60)} min",
                       style={"color": C["yellow"]},
                       title="lien ouvert, mais aucune liquidation reçue — "
                             "marché calme, ou flux qui ne livre plus "
                             "(adresse changée par la plateforme, "
                             "réseau qui filtre)")
             for name, age in feed.silent(SILENCE)]

    totals = feed.totals(WINDOW)
    if not totals["count"]:
        return html.Span([html.Span("aucune liquidation depuis une heure",
                                    style={"color": C["muted"]})] + sans + muets,
                         style={"fontFamily": MONO})

    return html.Span([
        html.Span("1 h · ", style={"color": C["muted"]}),
        html.Span(f"{fx.warning}longs "
                  f"{_montant(totals['long'] * fx.rate, fx.unit)}",
                  style={"color": C["red"]},
                  title=fx.title("positions longues fermées de force")),
        html.Span(" · ", style={"color": C["muted"]}),
        html.Span(f"shorts {_montant(totals['short'] * fx.rate, fx.unit)}",
                  style={"color": C["green"]},
                  title=fx.title("positions courtes fermées de force")),
        html.Span(" · dont BTC ", style={"color": C["muted"]}),
        html.Span(_montant(totals["btc"] * fx.rate, fx.unit),
                  style={"color": C["yellow"]},
                  title=fx.title("part des paires Bitcoin dans le total")),
    ] + sans + muets, style={"fontFamily": MONO})


#: Caractères que le badge de la grille peut porter. La cellule de
#: l'arbitrage laisse 214 px à côté de ses quatre onglets, et un caractère
#: du badge en prend 6,6 (9 px, espacement compris) : trente-deux tiennent,
#: pas un de plus. Les formes ci-dessous sont comptées pour ne jamais les
#: dépasser, et `tests/test_liquidations.py` les recompte.
COMPACT_MAX = 32


def _montant_court(valeur: float, unit: str = "$") -> str:
    """Montant en sept caractères au plus, pour le badge de la grille :
    une décimale sous cent, aucune au-delà — et aucune pour les milliers,
    où elle ne dit rien."""
    if valeur < 999.5:
        return f"{valeur:.0f} {unit}"
    if valeur < 999_500:
        return f"{valeur / 1e3:.0f} k{unit}"
    for seuil, suffixe in ((1e6, "M"), (1e9, "Md")):
        reduit = valeur / seuil
        if reduit < 99.95:
            return f"{reduit:.1f} {suffixe}{unit}"
        if reduit < 999.5:
            return f"{reduit:.0f} {suffixe}{unit}"
    return f"{valeur / 1e9:.0f} Md{unit}"


def _alerte_compacte(feed):
    """L'état des liens en huit caractères au plus, ou rien.

    Un seul lien en défaut se nomme par son sigle. Plusieurs ne tiennent
    pas côte à côte — « BIN muet · BYB muet » débordait de 96 px le
    11 novembre 2026, Binance et Bybit se taisant ensemble — et se
    comptent : le survol les nomme, et le panneau SOURCES les détaille.
    """
    absents = feed.missing()
    muets = feed.silent(SILENCE)
    if not absents and not muets:
        return None
    detail = " ; ".join(
        [f"lien {name} non établi" for name in absents]
        + [f"lien {name} ouvert, rien reçu depuis {int(age // 60)} min"
           for name, age in muets])
    sigle = lambda name: EXCHANGE_TAG.get(name, name[:3].upper())
    if len(absents) + len(muets) == 1:
        texte = (f"sans {sigle(absents[0])}" if absents
                 else f"{sigle(muets[0][0])} muet")
    elif not absents:
        texte = f"{len(muets)} muets"
    elif not muets:
        texte = f"{len(absents)} coupés"
    else:
        # Des liens tombés et d'autres muets : on dit ce qui livre encore.
        vifs = len(feed.links) - len(absents) - len(muets)
        texte = f"{vifs} lien/{len(feed.links)}"
    return html.Span(texte, title=detail,
                     style={"color": C["red"] if absents else C["yellow"]})


def _badges_compact(feed, fx=DOLLARS):
    """La version de la grille : totaux de l'heure en abrégé, liens absents
    ou muets en un seul mot — le tout en `COMPACT_MAX` caractères."""
    if not feed.connected:
        return html.Span("flux coupé", style={"color": C["red"]},
                         title=feed.error or "aucun lien ne tient")
    alerte = _alerte_compacte(feed)
    queue = ([html.Span(" · ", style={"color": C["muted"]}), alerte]
             if alerte is not None else [])
    totals = feed.totals(WINDOW)
    if not totals["count"]:
        return html.Span([html.Span("aucune en 1 h",
                                    style={"color": C["muted"]})] + queue,
                         style={"fontFamily": MONO})
    detail = fx.title(
        f"sur une heure : longs {_montant(totals['long'] * fx.rate, fx.unit)}, "
        f"shorts {_montant(totals['short'] * fx.rate, fx.unit)}, dont BTC "
        f"{_montant(totals['btc'] * fx.rate, fx.unit)}")
    # Avec une alerte, le rappel de la fenêtre cède sa place : le survol
    # la redit, et un lien qui se tait compte plus qu'un « 1 h ».
    tete = ([] if alerte is not None
            else [html.Span("1 h · ", style={"color": C["muted"]})])
    return html.Span(tete + [
        html.Span(f"{fx.warning}L "
                  f"{_montant_court(totals['long'] * fx.rate, fx.unit)}",
                  style={"color": C["red"]}, title=detail),
        html.Span(" · ", style={"color": C["muted"]}),
        html.Span(f"S {_montant_court(totals['short'] * fx.rate, fx.unit)}",
                  style={"color": C["green"]}, title=detail),
    ] + queue, style={"fontFamily": MONO})


def _prix(valeur: float) -> str:
    """Un prix lisible d'un bout à l'autre du fil : un bitcoin à 79 090 $,
    un ether à 4 322 $, un solana à 180.25 $, un dogecoin à 0.2345 $ — le
    nombre de décimales suit la taille du prix. Quatre chiffres
    significatifs pour tout le monde écrivaient le bitcoin `7.909e+04`."""
    if valeur >= 1000:
        return f"{valeur:,.0f}"
    if valeur >= 1:
        return f"{valeur:.2f}"
    return f"{valeur:.4g}"


def _row(event, fx=DOLLARS):
    """Une liquidation : heure, plateforme, paire, côté liquidé, prix, montant."""
    label, color = SIDE_LABEL.get(event.side, ("?", C["muted"]))
    cell = {"padding": "1px 6px", "whiteSpace": "nowrap"}
    return html.Tr([
        html.Td(datetime.fromtimestamp(event.time).strftime("%H:%M:%S"),
                style={**cell, "color": C["muted"]}),
        html.Td(EXCHANGE_TAG.get(event.exchange, event.exchange[:3].upper()),
                style={**cell, "color": C["muted"], "fontSize": "9px"},
                title=event.exchange),
        html.Td(event.symbol.replace("USDT", ""),
                style={**cell, "color": C["text"]}),
        html.Td(label, style={**cell, "color": color, "fontWeight": "600"}),
        html.Td(_prix(event.price * fx.rate),
                style={**cell, "color": C["muted"], "textAlign": "right"}),
        html.Td(_montant(event.notional * fx.rate, fx.unit),
                style={**cell, "color": color, "textAlign": "right"}),
    ])


def render(hub, expanded: bool, currency=None):
    """Table et badges des liquidations, prêts à poser dans le panneau.

    Fonction pure vis-à-vis du rendu, partagée entre le callback Dash et
    le pousseur WebSocket.
    """
    fx = display(hub, currency)
    feed = hub.liquidations
    limit = ROWS_MAX if expanded else ROWS
    events = feed.latest(limit)

    if not events:
        age = feed.last_event_age()
        attente = ("en attente du flux…" if not feed.connected else
                   "marché calme — aucune liquidation reçue"
                   if age is None else
                   f"rien depuis {int(age // 60)} min")
        table = html.Div(attente, style={
            "color": C["muted"], "fontFamily": MONO,
            "fontSize": "11px", "padding": "12px"})
    else:
        table = html.Table([html.Tbody([_row(e, fx) for e in events])],
                           style=TABLE_STYLE)

    return table, _badges(feed, fx, verbose=expanded)


def register(app, hub):
    @app.callback(
        Output("liq-table", "children"),
        Output("liq-badges", "children"),
        Input("tick-fast", "n_intervals"),
        # `expanded` désigne le panneau agrandi, plus sa cellule : depuis
        # la disposition configurable, les liquidations peuvent vivre
        # ailleurs que dans la cellule de l'arbitrage.
        Input("expanded", "data"),
        Input("currency", "data"),
    )
    def _refresh(_tick, expanded, currency):
        return render(hub, expanded == "liq", currency)
