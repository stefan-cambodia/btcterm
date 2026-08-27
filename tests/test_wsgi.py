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
- qu'un signal d'arrêt, reçu pour de vrai par le processus, lève
  `hub.stopping` avant de rendre la main au gestionnaire en place —
  c'est ce qui fait sortir les WebSockets /push, sans quoi gunicorn
  attendrait leur fin et systemd finirait par tuer le service ;
- que l'objet rendu est l'application WSGI complète : la page répond,
  et la route /push du pousseur est posée.

Lancement :
    python tests/test_wsgi.py
"""

import os
import signal
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


def _build(env, keep_signals=False):
    """Appelle la fabrique sous un environnement donné, atexit capturé.

    L'environnement et les gestionnaires de signaux sont remis en état
    quoi qu'il arrive : un test ne doit pas léguer ses variables au
    suivant, ni ses enveloppes de signaux au processus — sauf demande
    (`keep_signals`), pour le test qui les éprouve.
    """
    saved = {name: os.environ.pop(name, None) for name in ENV_VARS}
    os.environ.update(env)
    handlers = {s: signal.getsignal(s) for s in wsgi.STOP_SIGNALS}
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
        if not keep_signals:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
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


def test_le_signal_previent_le_hub():
    """Un SIGTERM réel lève `hub.stopping`, puis passe au gestionnaire en place."""
    recus = []
    originaux = {s: signal.getsignal(s) for s in wsgi.STOP_SIGNALS}
    try:
        # Le gestionnaire « de gunicorn » : un enregistreur, posé avant la
        # fabrique comme gunicorn pose le sien avant de charger l'application.
        signal.signal(signal.SIGTERM, lambda sig, frame: recus.append(sig))
        _, hub, _ = _build({}, keep_signals=True)
        assert not hub.stopping.is_set()
        signal.raise_signal(signal.SIGTERM)
        assert hub.stopping.is_set(), "le signal n'a pas prévenu le hub"
        assert recus == [signal.SIGTERM], "le gestionnaire en place n'a pas eu la main"
        # SIGINT et SIGQUIT sont enveloppés aussi : les trois signaux
        # d'arrêt de gunicorn.
        for signum in wsgi.STOP_SIGNALS:
            assert signal.getsignal(signum) is not originaux[signum]
    finally:
        for signum, handler in originaux.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    test_defauts()
    test_drapeaux()
    test_pose_mais_faux()
    test_le_signal_previent_le_hub()
    print("OK — fabrique WSGI : environnement traduit, hub démarré, "
          "arrêt enregistré, signal relayé, route /push posée")
