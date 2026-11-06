#!/usr/bin/env python3
"""
Les alertes jusque sur le téléphone, sans réseau : un faux `post` tient
lieu de ntfy.

Vérifié ici :

- rien ne part sans sujet configuré ;
- la publication est en JSON, à la racine du serveur, le sujet dans le
  corps — un serveur auto-hébergé sous un préfixe compris — avec un titre
  lisible, une priorité et une étiquette par règle, et le jeton s'il y en
  a un ;
- chaque sonnerie du moteur part vers le notificateur, et aucune de celles
  relues du journal au démarrage ;
- l'envoi part d'un thread à lui : une panne de ntfy se dit une fois au
  journal, son retour aussi, et la file pleine abandonne au lieu d'enfler ;
- le panneau ALERTES dit si le téléphone est servi, sans jamais montrer
  le sujet.

Lancement :
    python tests/test_notify.py
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm import notify  # noqa: E402
from btcterm.alerts import DEFAULT_CONFIG, Alert, AlertEngine  # noqa: E402


class FauxNtfy:
    """Retient chaque publication ; tombe en panne sur commande."""

    def __init__(self):
        self.envois = []
        self.panne = False

    def __call__(self, url, json=None, headers=None, timeout=None):
        if self.panne:
            raise ConnectionError("ntfy.sh injoignable")
        self.envois.append((url, json, headers))

        class Reponse:
            def raise_for_status(self):
                pass
        return Reponse()


def attendre(condition, delai=3.0):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_rien_sans_sujet():
    assert notify.from_environment({}) is None
    assert notify.from_environment({"BTCTERM_NTFY": "  "}) is None
    notificateur = notify.from_environment(
        {"BTCTERM_NTFY": "https://ntfy.sh/sujet", "BTCTERM_NTFY_TOKEN": "tk_1"})
    assert notificateur.url == "https://ntfy.sh/sujet"
    assert notificateur.token == "tk_1"
    notificateur.stop()
    print("  ✓ aucun envoi sans sujet ; sujet et jeton lus de l'environnement")


def test_la_publication():
    ntfy = FauxNtfy()
    notificateur = notify.Notifier("https://ntfy.sh/mon-sujet", token="tk_9",
                                   post=ntfy)
    try:
        notificateur.deliver(Alert(time=0, kind="vol",
                                   message="DVOL 52.3, +9.1 points"))
        url, corps, entetes = ntfy.envois[0]
        assert url == "https://ntfy.sh/"
        assert corps == {"topic": "mon-sujet",
                         "title": "BTC · volatilité implicite",
                         "message": "DVOL 52.3, +9.1 points",
                         "priority": 4, "tags": ["zap"]}
        assert entetes == {"Authorization": "Bearer tk_9"}
        # Une règle ordinaire : priorité 3 ; une inconnue : son code, une cloche.
        assert notificateur.payload(Alert(0, "news", "x"))[1]["priority"] == 3
        inconnue = notificateur.payload(Alert(0, "autre", "x"))[1]
        assert inconnue["title"] == "BTC · autre" and inconnue["tags"] == ["bell"]
        # Un serveur auto-hébergé sous un préfixe.
        maison = notify.Notifier("https://maison.example/ntfy/alertes/",
                                 post=ntfy)
        assert maison.payload(Alert(0, "price", "x"))[0] == "https://maison.example/ntfy/"
        assert maison.payload(Alert(0, "price", "x"))[1]["topic"] == "alertes"
        assert maison.describe() == "maison.example"
        maison.stop()
    finally:
        notificateur.stop()
    print("  ✓ JSON à la racine, sujet au corps, titre lisible, priorité, jeton")


def test_le_moteur_envoie_ses_sonneries():
    class Retenu:
        def __init__(self):
            self.recues = []

        def send(self, alerte):
            self.recues.append(alerte)
            return True

    moteur = AlertEngine(fetch_news=lambda: [])
    moteur.configure({**DEFAULT_CONFIG, "price_levels": [
        {"level": 100.0, "dir": "above"}]})
    moteur.notifier = Retenu()
    # Relue du journal au démarrage : affichée, jamais renvoyée.
    moteur.restore([Alert(time=1.0, kind="liq", message="rafale d'hier")])
    moteur._fire("price", "cours au-dessus de 100", time.time())
    assert [a.message for a in moteur.notifier.recues] == ["cours au-dessus de 100"]
    print("  ✓ chaque sonnerie part au téléphone, aucune relue du journal")


def test_le_thread_d_envoi():
    ntfy = FauxNtfy()
    lignes = []

    class Capture(logging.Handler):
        def emit(self, record):
            lignes.append(record.getMessage())

    capture = Capture()
    notify.log.addHandler(capture)
    notificateur = notify.Notifier("https://ntfy.sh/s", post=ntfy)
    try:
        assert notificateur.send(Alert(0, "price", "un"))
        assert attendre(lambda: notificateur.sent == 1)
        ntfy.panne = True
        notificateur.send(Alert(0, "price", "deux"))
        notificateur.send(Alert(0, "price", "trois"))
        assert attendre(lambda: notificateur._queue.empty() and lignes)
        time.sleep(0.05)
        assert len(lignes) == 1 and "en panne" in lignes[0], lignes
        assert "s :" not in lignes[0] and "ntfy.sh" in lignes[0], \
            "le journal dit le serveur, pas le sujet"
        ntfy.panne = False
        notificateur.send(Alert(0, "price", "quatre"))
        assert attendre(lambda: notificateur.sent == 2)
        assert attendre(lambda: len(lignes) == 2)
        assert "rétablies" in lignes[1]
    finally:
        notify.log.removeHandler(capture)
        notificateur.stop()

    # File pleine : les sonneries en trop sont abandonnées, pas empilées.
    bloque = notify.Notifier("https://ntfy.sh/s", post=ntfy)
    bloque.stop()
    bloque._thread.join(timeout=2)
    resultats = [bloque.send(Alert(0, "price", str(i)))
                 for i in range(notify.QUEUE_MAX + 5)]
    assert resultats.count(False) == 5
    print("  ✓ envoi en thread, panne et retour dits une fois, file bornée")


def test_le_panneau_le_dit():
    from types import SimpleNamespace
    from terminal.panels import alerts as panneau

    ancien = panneau._hub
    try:
        panneau._hub = SimpleNamespace(alerts=SimpleNamespace(notifier=None))
        assert "non configuré" in str(panneau._phone_status())
        notificateur = notify.Notifier("https://ntfy.sh/sujet-secret",
                                       post=FauxNtfy())
        panneau._hub = SimpleNamespace(alerts=SimpleNamespace(
            notifier=notificateur))
        texte = str(panneau._phone_status())
        assert "ntfy · ntfy.sh" in texte and "sujet-secret" not in texte
        notificateur.stop()
    finally:
        panneau._hub = ancien
    print("  ✓ le panneau dit le serveur, jamais le sujet")


def test_l_essai_en_ligne_de_commande():
    import os
    ancien = os.environ.pop("BTCTERM_NTFY", None)
    try:
        assert notify.main(["bonjour"]) == 1, "sans sujet, l'essai le dit"
    finally:
        if ancien is not None:
            os.environ["BTCTERM_NTFY"] = ancien
    print("  ✓ l'essai sans sujet le dit")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nNotifications — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Les alertes partent jusqu'au téléphone, et rien sans sujet.\n")
