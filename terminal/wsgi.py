"""
Le terminal servi par gunicorn — le régime « service ».

Le serveur de développement de Flask (Werkzeug) suffit à une session
lancée à la main, mais il n'est pas pensé pour des semaines de
fonctionnement continu. Ce module expose la fabrique que gunicorn
attend :

    gunicorn --workers 1 --threads 32 \\
             --bind 127.0.0.1:8050 'terminal.wsgi:build()'

Deux choix méritent d'être posés par écrit :

- **Un seul worker, impérativement.** Tout l'état du terminal — carnets,
  liquidations, alertes, caches — vit en mémoire dans le hub du
  processus. Un deuxième worker ouvrirait une deuxième grappe de
  connexions aux plateformes et servirait les navigateurs depuis des
  états divergents. La concurrence vient des threads du worker, pas des
  processus.
- **Des workers threadés, pas gevent.** Le monkey-patching de gevent
  transformerait les threads du hub en greenlets et se disputerait la
  main avec la boucle asyncio des connecteurs. flask-sock
  (simple-websocket) supporte les workers sync threadés : chaque
  WebSocket `/push` occupe un thread pour la durée de la connexion,
  d'où un compte de threads confortable (32).

La configuration passe par l'environnement — une unité systemd n'a pas
d'`argv` commode. Les variables recouvrent exactement les options de la
ligne de commande (`btcterm --help`) :

    BTCTERM_NO_NEWS=1       ne pas alimenter la base de news
    BTCTERM_NO_JOURNAL=1    ne pas tenir le journal de séance
    CRYPTOPANIC_API_KEY=…   clé CryptoPanic pour la collecte de news

`terminal/systemd_service.conf` contient le gabarit d'unité `--user`
qui va avec.
"""

from __future__ import annotations

import atexit
import os

from btcterm.hub import MarketHub

from .app import create_app

#: Valeurs d'environnement tenues pour « vrai ». Une unité systemd écrit
#: volontiers `Environment=BTCTERM_NO_NEWS=1`, un shell `…=true` : les
#: deux doivent couper la collecte — et `…=0`, posé mais faux, ne le
#: doit pas.
_TRUE = {"1", "true", "yes", "oui", "on"}


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def build(hub_factory=MarketHub):
    """Construit le terminal et rend son application WSGI à gunicorn.

    La fabrique est appelée dans le worker — gunicorn importe
    l'application après le fork, tant qu'on n'utilise pas `--preload`
    (à proscrire ici) : le hub et ses threads vivent donc dans le bon
    processus. À l'arrêt du worker (le SIGTERM de systemd), `atexit`
    referme le hub — les épisodes encore ouverts partent au journal,
    comme au Ctrl-C de la ligne de commande.

    `hub_factory` n'existe que pour les tests : un hub factice s'y
    substitue au vrai, et la fabrique s'éprouve sans réseau.
    """
    hub = hub_factory(
        collect_news=not _flag("BTCTERM_NO_NEWS"),
        cryptopanic_key=os.environ.get("CRYPTOPANIC_API_KEY", ""),
        keep_journal=not _flag("BTCTERM_NO_JOURNAL"),
    )
    hub.start()
    atexit.register(hub.stop)
    return create_app(hub).server
