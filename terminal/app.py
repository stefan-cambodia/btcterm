"""
Assemblage du terminal.

Deux régimes de rafraîchissement plutôt qu'un seul — c'est ce qui permet
d'afficher un carnet vivant sans resérialiser les chandeliers quatre fois
par seconde :

    tick-fast  250 ms   carnet, profondeur, arbitrage  (mémoire, zéro réseau)
    tick-slow    2 s    chandeliers, indicateurs, bandeau
    tick-rare    5 min  flux ETF, news, Fear & Greed

Le régime rapide a un second canal : quand le navigateur tient un
WebSocket ouvert sur /push, le serveur pousse le rendu et tick-fast est
coupé (terminal/push.py). L'horloge reste le repli — le terminal
fonctionne à l'identique sans le canal, juste au rythme de
l'interrogation.

Ce module ne fait qu'assembler : la grille et ses onglets vivent dans
terminal/grid.py, le dialogue de disposition dans terminal/placement.py,
le bandeau dans terminal/header.py, et chaque panneau dans panels/.
`create_app` pose les horloges et les Stores partagés, puis enregistre
les callbacks de chacun ; `main` est la ligne de commande.

Lancement :
    python -m terminal.app            # http://127.0.0.1:8050
    python -m terminal.app --port 8060
"""

from __future__ import annotations

import argparse
import os

import dash
from dash import dcc, html

from btcterm.hub import MarketHub

from . import grid, header, lwc, placement, push
from .panels import PANELS
from .theme import C

REFRESH_FAST_MS = 250
REFRESH_SLOW_MS = 2_000
REFRESH_RARE_MS = 300_000


def _clocks() -> list:
    return [
        dcc.Interval(id="tick-fast", interval=REFRESH_FAST_MS),
        dcc.Interval(id="tick-slow", interval=REFRESH_SLOW_MS),
        dcc.Interval(id="tick-rare", interval=REFRESH_RARE_MS),
    ]


def _shared_stores() -> list:
    """Les Stores que plusieurs modules se partagent et qu'aucun ne possède.

    Ceux de la grille (onglets, rangement, plein écran) sont posés par
    `grid.stores()` ; restent ici les relais entre modules.
    """
    return [
        # Puits des relais d'état du pousseur (terminal/push.py) : leurs
        # callbacks clientside n'écrivent que pour avoir une sortie.
        dcc.Store(id="push-sink-expanded"),
        dcc.Store(id="push-sink-exchange"),
        # Alertes : les réglages survivent au rechargement et réarment
        # le moteur au chargement ; le fil et ses puits sont globaux —
        # la sonnerie navigateur doit retentir même panneau replié.
        dcc.Store(id="alert-config", storage_type="local"),
        dcc.Store(id="alerts-feed"),
        dcc.Store(id="alerts-feed-sink"),
        dcc.Store(id="alert-config-sink"),
    ]


def create_app(hub: MarketHub) -> dash.Dash:
    app = dash.Dash(
        __name__,
        title="₿ BTC Terminal",
        update_title=None,          # pas de « Updating… » clignotant à 250 ms
        suppress_callback_exceptions=True,
        # Le jeton du serveur qui a servi la page : push.js le compare à
        # `/api/boot` avant chaque connexion et recharge un onglet
        # d'avant le redémarrage (terminal/push.py).
        meta_tags=[{"name": "btcterm-boot", "content": push.BOOT}],
    )

    app.layout = html.Div([
        *_clocks(),
        *grid.stores(),
        *_shared_stores(),
        header.layout(),
        grid.layout(),
        placement.layout(),
    ], style={"background": C["bg"], "margin": "0", "height": "100vh",
              "overflow": "hidden"})

    for panel in PANELS:
        panel.register(app, hub)

    grid.register(app)
    placement.register(app)
    header.register(app, hub)
    push.register(app, hub)
    lwc.register_api(app, hub)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Terminal Bitcoin")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="127.0.0.1 par défaut : pour un accès distant, préférer un "
             "tunnel SSH (ssh -L 8050:localhost:8050) plutôt que d'exposer "
             "le port sur le réseau",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--no-news", action="store_true",
        help="ne pas alimenter ~/.btc_news/news.db : le panneau news se "
             "contente alors de lire ce que le tracker y a mis",
    )
    parser.add_argument(
        "--no-journal", action="store_true",
        help="ne pas tenir ~/.btcterm/journal.db (liquidations et épisodes "
             "d'arbitrage de la séance)",
    )
    parser.add_argument(
        "--cryptopanic-key", default=os.environ.get("CRYPTOPANIC_API_KEY", ""),
        help="clé CryptoPanic pour la collecte de news (défaut : variable "
             "d'environnement CRYPTOPANIC_API_KEY)",
    )
    args = parser.parse_args()

    hub = MarketHub(
        collect_news=not args.no_news,
        cryptopanic_key=args.cryptopanic_key,
        keep_journal=not args.no_journal,
    )
    hub.start()

    app = create_app(hub)
    print(_banner(args.host, args.port))
    try:
        app.run(debug=args.debug, host=args.host, port=args.port)
    finally:
        hub.stop()


def _banner(host: str, port: int) -> str:
    """Le cartouche de démarrage, aux bords alignés quelle que soit l'adresse."""
    width = 54
    lines = [
        "₿  BTC TERMINAL",
        f"→ http://{host}:{port}",
        f"→ à distance :  ssh -L {port}:localhost:{port} <machine>",
        "→ Ctrl-C pour arrêter",
    ]
    return "\n".join([
        "",
        "╔" + "═" * (width + 2) + "╗",
        *[f"║  {line:<{width}}║" for line in lines],
        "╚" + "═" * (width + 2) + "╝",
        "",
    ])


if __name__ == "__main__":
    main()
