"""
Le bandeau du terminal : cours, variation, spread, état des flux.

Une ligne au-dessus de la grille, rafraîchie au régime lent. Deux de ses
champs sont tenus par d'autres modules et ne font ici que réserver leur
place : la cloche des alertes (`hdr-alerts`, panneau alertes) et le canal
des panneaux rapides (`hdr-push`, assets/push.js).

La cloche est en revanche *cliquable* d'ici : compter les sonneries sans
offrir de chemin vers la liste laissait l'alerte visible et injoignable,
le panneau alertes vivant derrière le troisième onglet d'une cellule.
Le clic est donc traité ici, seul endroit qui connaisse à la fois le
bandeau et la grille (§ `_register_reveal`).
"""

from __future__ import annotations

import time

import dash
from dash import Input, Output, State, html

from btcterm.analysis import position_state
from btcterm.hub import MarketHub

from .fx import DOLLARS, display, spread_text
from .grid import AREAS, reveal
from .theme import C, MONO

#: Style commun des champs du bandeau.
STAT = {"fontFamily": MONO, "fontSize": "11px", "color": C["text"],
        "marginRight": "18px"}


def price_text(live: float | None, currency: str | None, rate: float,
               stale: bool = False) -> str:
    """Le cours du bandeau, dans la devise choisie au panneau prix.

    `rate` est le taux USD→EUR du hub ; il est ignoré en dollars, devise
    par défaut tant que le navigateur n'a rien choisi. Un taux de secours
    (`stale`) marque le cours d'un ⚠ : l'écart peut dépasser les
    variations qu'on vient lire.
    """
    if not live:
        return "—"
    if currency == "EUR":
        return f"{'⚠ ' if stale else ''}{live * rate:,.2f} €"
    return f"{live:,.2f} $"


def price_title(currency: str | None, rate: float, stale: bool = False) -> str:
    """L'infobulle du cours : d'où il vient, et à quel taux il est converti."""
    title = "milieu du carnet de la plateforme la plus fraîche"
    if currency != "EUR":
        return title
    title += f", converti à 1 $ = {rate:.4f} €"
    if stale:
        title += " — taux de secours : source de change injoignable"
    return title


def range_text(ticker: dict, fx=DOLLARS) -> str:
    """Plus haut, plus bas et volume sur 24 h, vides sans réponse de Binance."""
    try:
        high, low = float(ticker["highPrice"]), float(ticker["lowPrice"])
        volume = float(ticker["quoteVolume"])
    except (KeyError, TypeError, ValueError):
        return ""
    return (f"H {high * fx.rate:,.0f} · L {low * fx.rate:,.0f} · "
            f"vol {volume * fx.rate / 1e9:.2f} Md{fx.unit}")


def position_text(position: dict | None, price: float | None,
                  fx=DOLLARS) -> tuple[str, dict]:
    """Le résultat de la position suivie, et son style ; vide sans position."""
    position = position or {}
    state = position_state(position.get("side"), position.get("quantity"),
                           position.get("entry"), price)
    if state is None:
        return "", STAT
    return (f"{position.get('side')} {state['pnl'] * fx.rate:+,.0f} {fx.unit} "
            f"({state['pnl_pct']:+.2f} %)",
            {**STAT, "color": C["green" if state["pnl"] >= 0 else "red"]})


#: Les familles du cache du hub, dites comme le reste du bandeau.
SOURCE_LABELS = {
    "klines": "chandeliers Binance", "perp": "perpétuel", "oi": "open interest",
    "perp_bybit": "perpétuel Bybit", "perp_okx": "perpétuel OKX",
    "funding": "financement", "eur": "taux de change", "ticker": "variation 24 h",
    "global": "dominance", "chain": "on-chain", "chain_stats": "on-chain",
    "fees": "frais on-chain",
    "etf": "flux ETF", "m2": "masse monétaire",
    "fear_greed_history": "Fear & Greed",
}


def sources_text(failing: dict[str, float], now: float) -> tuple[str, str]:
    """Le témoin des sources en panne, et son infobulle ; vide sinon.

    Une source tombée ne se lisait que dans `journalctl`, et le bandeau
    affichait 5/5 flux pendant que le perpétuel se taisait depuis des heures.
    """
    if not failing:
        return "", ""
    # Deux familles peuvent porter le même nom, comme les deux lectures de
    # la chaîne : la plus ancienne dit depuis quand la source est en panne.
    longest: dict[str, int] = {}
    for name, since in failing.items():
        label = SOURCE_LABELS.get(name, name)
        longest[label] = max(int((now - since) // 60), longest.get(label, 0))
    title = " · ".join(f"{label} depuis {minutes} min"
                       for label, minutes in sorted(longest.items()))
    title += " — cliquer pour le détail"
    count = len(longest)
    return f"⚠ {count} source{'s' if count > 1 else ''}", title


def links_text(links: list[tuple[str, bool]]) -> tuple[str, str]:
    """« 9/10 flux » et l'infobulle qui nomme ceux qui manquent.

    Tous les liens temps réel comptent — carnets, liquidations, ruban : le
    bandeau disait « 5/5 flux » pour les seuls carnets, muet sur un fil de
    liquidations ou un ruban tombé.
    """
    up = sum(1 for _, ok in links if ok)
    missing = [name for name, ok in links if not ok]
    tip = ("sans " + ", ".join(missing) if missing
           else "tous les liens temps réel tiennent")
    return f"{up}/{len(links)} flux", tip + " — cliquer pour le détail"


def dvol_text(dvol) -> tuple[str, str]:
    """DVOL et sa variation depuis la clôture de la veille, avec l'infobulle.

    Vide tant que le cache n'a rien : le bandeau ne dit pas « DVOL — »
    d'une source qu'il n'a jamais lue.
    """
    if dvol is None or dvol.empty:
        return "", ""
    last = float(dvol["close"].iloc[-1])
    text = f"DVOL {last:.1f}"
    if len(dvol) >= 2:
        text += f" {last - float(dvol['close'].iloc[-2]):+.1f}"
    return text, ("volatilité implicite à 30 jours (Deribit), et sa variation "
                  "depuis la clôture d'hier — cliquer pour ouvrir le panneau "
                  "options")


def change_text(ticker: dict) -> tuple[str, dict]:
    """La variation sur 24 h et son style.

    Sans réponse de Binance, le ticker est vide : la variation est dite
    inconnue, pas affichée à +0,00 % en vert.
    """
    raw = ticker.get("priceChangePercent")
    if raw in (None, ""):
        return "— % 24 h", {**STAT, "color": C["muted"]}
    change = float(raw)
    return (f"{change:+.2f} % 24 h",
            {**STAT, "color": C["green"] if change >= 0 else C["red"]})


def layout():
    return html.Div([
        html.Span("₿ BTC TERMINAL", style={
            "fontFamily": MONO, "fontWeight": "700", "fontSize": "13px",
            "color": C["yellow"], "letterSpacing": "0.14em", "marginRight": "24px"}),
        html.Span(id="hdr-price", style={**STAT, "fontSize": "14px",
                                         "fontWeight": "600"}),
        html.Span(id="hdr-change", style=STAT),
        html.Span(id="hdr-range", style={**STAT, "color": C["muted"]},
                  title="plus haut, plus bas et volume échangé sur 24 h"),
        html.Span(id="hdr-spread", style=STAT),
        # La volatilité implicite à trente jours, toujours sous les yeux ;
        # le clic ouvre le panneau options, comme la cloche les alertes.
        html.Span(id="hdr-dvol", className="hdr-bell", n_clicks=0,
                  style={**STAT, "color": C["yellow"]}),
        html.Span(id="hdr-position", style=STAT,
                  title="résultat de la position suivie au panneau POSITION"),
        # Le nombre de sources en panne ; le clic ouvre le panneau SOURCES,
        # où chacune dit son état et sa dernière erreur.
        html.Span(id="hdr-sources", className="hdr-bell", n_clicks=0,
                  style={**STAT, "color": C["orange"]}),
        html.Button("⚙", id="layout-btn", className="layout-btn",
                    title="disposition de la grille"),
        # La cloche : sonneries de la dernière heure. Son contenu est
        # tenu par le callback du panneau alertes — qui tourne toujours,
        # le fil devant compter et sonner même panneau replié ; son clic
        # ouvre le panneau (§ `_register_reveal`).
        #
        # Le curseur et le survol passent par la classe : le callback des
        # alertes réécrit `style` à chaque tour et effacerait ce qu'on y
        # mettrait ici.
        html.Span(id="hdr-alerts", className="hdr-bell", n_clicks=0,
                  title="alertes de la dernière heure — cliquer pour ouvrir "
                        "le panneau",
                  style={**STAT, "color": C["muted"], "fontSize": "11px"}),
        html.Span("⛶ ou double-clic sur un panneau · Échap pour revenir · "
                  "? : raccourcis",
                  style={**STAT, "marginLeft": "12px", "color": C["muted"],
                         "fontSize": "10px"}),
        # Canal des panneaux rapides : « push » quand le WebSocket est
        # ouvert, « poll » en repli. Tenu par assets/push.js, jamais par
        # un callback — c'est un état du navigateur, pas du serveur.
        html.Span(id="hdr-push", title="canal des panneaux rapides",
                  style={**STAT, "color": C["muted"], "fontSize": "10px"}),
        # Les liens temps réel qui tiennent ; le clic ouvre SOURCES.
        html.Span(id="hdr-status", className="hdr-bell", n_clicks=0,
                  style={**STAT, "color": C["muted"]}),
    ], style={
        "display": "flex", "alignItems": "center", "padding": "0 14px",
        "height": "38px", "background": C["panel"],
        "borderBottom": f"1px solid {C['border']}",
    })


def register(app: dash.Dash, hub: MarketHub) -> None:
    _register_reveal(app)

    @app.callback(
        Output("hdr-price", "children"),
        Output("hdr-price", "title"),
        Output("hdr-change", "children"),
        Output("hdr-change", "style"),
        Output("hdr-range", "children"),
        Output("hdr-spread", "children"),
        Output("hdr-dvol", "children"),
        Output("hdr-dvol", "title"),
        Output("hdr-status", "children"),
        Output("hdr-status", "title"),
        Output("hdr-sources", "children"),
        Output("hdr-sources", "title"),
        Output("hdr-position", "children"),
        Output("hdr-position", "style"),
        Input("tick-slow", "n_intervals"),
        # En Input et non en State : basculer $/€ se voit tout de suite,
        # sans attendre le tour d'horloge suivant.
        Input("currency", "data"),
        State("position", "data"),
    )
    def _refresh(_tick, currency, position):
        live = hub.reference_price()
        # Le taux n'est demandé qu'en euros : il vient d'un appel réseau,
        # mis en cache une heure par le hub.
        euros = currency == "EUR"
        rate = hub.eur_rate() if euros else 1.0
        stale = euros and hub.eur_rate_stale()
        price_txt = price_text(live, currency, rate, stale)
        price_tip = price_title(currency, rate, stale)

        ticker = hub.ticker()
        change_txt, change_style = change_text(ticker)
        range_txt = range_text(ticker, display(hub, currency))

        spreads = [b.spread_pct for b in hub.books.values() if b.spread_pct]
        spread_txt = f"spread min {spread_text(min(spreads))}" if spreads else ""

        uptime = hub.uptime_seconds
        flux, flux_tip = links_text(hub.realtime_links())
        status = (f"{flux} · "
                  f"{uptime // 3600:02d}:{uptime % 3600 // 60:02d}:{uptime % 60:02d}")

        return (price_txt, price_tip, change_txt, change_style, range_txt,
                spread_txt, *dvol_text(hub.dvol_cached()), status, flux_tip,
                *sources_text(hub.failing_sources(), time.time()),
                *position_text(position, live, display(hub, currency)))


#: Ce que le bandeau sait ouvrir : élément cliquable → panneau.
REVEALS = {"hdr-alerts": "alerts", "hdr-dvol": "options",
           "hdr-sources": "sources", "hdr-status": "sources"}


def _register_reveal(app: dash.Dash) -> None:
    for trigger, panel_id in REVEALS.items():
        _register_reveal_one(app, trigger, panel_id)


def _register_reveal_one(app: dash.Dash, trigger: str, panel_id: str) -> None:
    """Le clic sur un élément du bandeau amène son panneau à l'écran — la
    cloche les alertes, DVOL les options.

    Deux gestes, parce qu'ils ne se décident pas au même endroit :

    - **choisir l'onglet**, côté serveur : un panneau n'a pas de cellule
      fixe depuis que le rangement est configurable, et seule la grille
      (`reveal`) sait où il a atterri ;
    - **quitter le plein écran**, côté navigateur : si une *autre*
      cellule est agrandie, elle masque la grille et changer d'onglet
      dessous ne se verrait pas. Le geste est le même que celui du
      bouton ⛶ — remettre la classe `cell` partout — et se termine par
      l'événement `resize` que Plotly attend pour se redimensionner.
    """
    @app.callback(
        Output("tabs", "data", allow_duplicate=True),
        Input(trigger, "n_clicks"),
        State("tabs", "data"),
        State("placement", "data"),
        prevent_initial_call=True,
    )
    def _open(clicks, tabs, placement):
        # Le montage de l'élément déclenche aussi ce callback, n_clicks à
        # zéro : même garde que pour les onglets.
        if not clicks:
            return dash.no_update
        choix = reveal(panel_id, tabs, placement)
        return dash.no_update if choix is None else choix

    app.clientside_callback(
        """
        function (clicks) {
            const areas = %(areas)s;
            if (!clicks) {
                return [dash_clientside.no_update].concat(
                    areas.map(function () {
                        return dash_clientside.no_update;
                    }));
            }
            setTimeout(function () {
                window.dispatchEvent(new Event('resize'));
            }, 60);
            return [null].concat(areas.map(function () { return 'cell'; }));
        }
        """ % {"areas": list(AREAS)},
        [Output("maximized", "data", allow_duplicate=True)]
        + [Output(f"cell-{area}", "className", allow_duplicate=True)
           for area in AREAS],
        Input(trigger, "n_clicks"),
        prevent_initial_call=True,
    )
