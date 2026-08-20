#!/usr/bin/env python3
"""
Vérifie la fabrique WSGI du régime service (terminal/wsgi.py), sans
réseau ni gunicorn.

Le régime service n'a pas d'argv : la configuration passe par des
variables d'environnement, et une variable mal lue ne se verrait qu'en
production — news jamais collectées, ou l'inverse. Un hub factice
enregistre ce que la fabrique lui transmet, et le test vérifie :

- la traduction de l'environnement en arguments du hub — défauts quand
  rien n'est posé, drapeaux posés, et `=0` qui ne compte pas ;
- que la fabrique démarre le hub et enregistre son arrêt (atexit) —
  c'est ce qui fait qu'un SIGTERM de systemd clôt le journal ;
- que l'objet rendu est l'application WSGI complète : la page répond,
  et la route /push du pousseur est posée.

Lancement :
    python tests/test_wsgi.py
"""

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm.hub import MarketHub  # noqa: E402
from terminal import wsgi  # noqa: E402

ENV_VARS = ("BTCTERM_NO_NEWS", "BTCTERM_NO_JOURNAL", "CRYPTOPANIC_API_KEY")


class HubFactice(MarketHub):
    """Un vrai hub, sauf le cycle de vie : rien ne touche le réseau."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.kwargs = kwargs
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _build(env):
    """Appelle la fabrique sous un environnement donné, atexit capturé.

    L'environnement est remis en état quoi qu'il arrive : un test ne
    doit pas léguer ses variables au suivant.
    """
    saved = {name: os.environ.pop(name, None) for name in ENV_VARS}
    os.environ.update(env)
    registered = []
    real_atexit = wsgi.atexit
    wsgi.atexit = types.SimpleNamespace(register=registered.append)
    try:
        hubs = []

        def factory(**kwargs):
            hub = HubFactice(**kwargs)
            hubs.append(hub)
            return hub

        server = wsgi.build(hub_factory=factory)
    finally:
        wsgi.atexit = real_atexit
        for name, value in saved.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
    return server, hubs[0], registered


def test_defauts():
    server, hub, registered = _build({})
    assert hub.kwargs == {"collect_news": True, "cryptopanic_key": "",
                          "keep_journal": True}, hub.kwargs
    assert hub.started, "la fabrique doit démarrer le hub"
    assert registered == [hub.stop], "l'arrêt du hub doit partir à atexit"
    routes = {rule.rule for rule in server.url_map.iter_rules()}
    assert "/push" in routes, routes
    reponse = server.test_client().get("/")
    assert reponse.status_code == 200, reponse.status_code


def test_drapeaux():
    _, hub, _ = _build({"BTCTERM_NO_NEWS": "1", "BTCTERM_NO_JOURNAL": "true",
                        "CRYPTOPANIC_API_KEY": "cle-test"})
    assert hub.kwargs == {"collect_news": False,
                          "cryptopanic_key": "cle-test",
                          "keep_journal": False}, hub.kwargs


def test_pose_mais_faux():
    # `Environment=BTCTERM_NO_NEWS=0` : la variable existe, le drapeau non.
    _, hub, _ = _build({"BTCTERM_NO_NEWS": "0", "BTCTERM_NO_JOURNAL": ""})
    assert hub.kwargs["collect_news"] is True
    assert hub.kwargs["keep_journal"] is True


if __name__ == "__main__":
    test_defauts()
    test_drapeaux()
    test_pose_mais_faux()
    print("OK — fabrique WSGI : environnement traduit, hub démarré, "
          "arrêt enregistré, route /push posée")
