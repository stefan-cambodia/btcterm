"""
Les alertes jusque sur le téléphone, par ntfy.

Le terminal sait sonner — bip, notification du navigateur —, mais seulement
dans un onglet ouvert. Sur un serveur allumé en permanence, personne ne
regarde l'onglet : c'est précisément quand on n'est pas devant l'écran
qu'une alerte doit arriver. ntfy (ntfy.sh, ou un serveur auto-hébergé) livre
une notification push à l'application Android ou iOS abonnée à un sujet,
par un simple POST, sans compte ni clé.

Rien ne part tant qu'aucun sujet n'est configuré :

    BTCTERM_NTFY=https://ntfy.sh/un-sujet-difficile-a-deviner
    BTCTERM_NTFY_TOKEN=tk_…        (serveur protégé seulement)

ou `btcterm --ntfy URL`. Sur ntfy.sh, le sujet fait office de mot de passe :
quiconque le connaît lit les notifications — d'où un nom long et
imprévisible. Les messages ne disent que ce que dit le panneau ALERTES :
un seuil franchi, un cours, une rafale.

L'envoi part d'un thread à lui : un ntfy lent ou injoignable ne retient
jamais la boucle d'observation, qui évalue les alertes. Une panne d'envoi
se dit une fois au journal, son rétablissement aussi, comme une source.

    python -m btcterm.notify "message d'essai"     # vérifie la configuration
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from typing import Callable, Optional
from urllib.parse import urlsplit

import requests

__all__ = ["Notifier", "from_environment", "KIND_PRIORITY", "KIND_TAGS",
           "KIND_TITLES"]

log = logging.getLogger("btcterm.notify")

#: Priorité ntfy par règle, de 1 (discrète) à 5 (urgente) : 4 fait vibrer
#: et passe au-dessus des autres notifications, 3 est l'ordinaire. Ce qui
#: demande d'agir — un seuil posé, une cascade, la volatilité qui bondit,
#: la tendance qui bascule — monte d'un cran.
KIND_PRIORITY = {"price": 4, "liq": 4, "vol": 4, "trend": 4, "flip": 4}
DEFAULT_PRIORITY = 3

#: Une étiquette par règle : ntfy l'affiche en émoji devant le titre.
KIND_TAGS = {
    "price": "dart", "liq": "boom", "funding": "money_with_wings",
    "news": "newspaper", "arb": "scales", "trend": "chart_with_upwards_trend",
    "flip": "chart_with_upwards_trend", "event": "calendar",
    "rsi": "thermometer", "signal": "triangular_flag_on_post",
    "dominance": "pie", "etf": "bank", "chain": "link", "vol": "zap",
}

#: Le titre de la notification, par règle : le nom que lui donne le
#: panneau ALERTES, pas son code interne.
KIND_TITLES = {
    "price": "seuil de cours", "liq": "liquidations", "funding": "financement",
    "news": "news", "arb": "arbitrage", "trend": "tendance journalière",
    "flip": "tendance journalière", "event": "échéance macro",
    "rsi": "RSI", "signal": "signal", "dominance": "dominance",
    "etf": "flux ETF", "chain": "réseau", "vol": "volatilité implicite",
}

#: Au-delà, les notifications en attente sont abandonnées plutôt que
#: d'enfler sans fin pendant une panne de ntfy.
QUEUE_MAX = 50


class Notifier:
    """Envoie les sonneries à un sujet ntfy, depuis un thread à lui."""

    def __init__(self, url: str, token: Optional[str] = None,
                 post: Callable = requests.post, timeout: float = 10.0):
        self.url = url.strip()
        self.token = token or None
        self._post = post
        self._timeout = timeout
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._failing = False
        self.sent = 0
        self._thread = threading.Thread(target=self._run, name="ntfy",
                                        daemon=True)
        self._thread.start()

    def describe(self) -> str:
        """Le serveur, sans le sujet : celui-ci fait office de mot de passe
        et n'a rien à faire à l'écran."""
        return urlsplit(self.url).netloc or self.url

    def send(self, alert) -> bool:
        """Met une sonnerie en file ; faux si la file est pleine."""
        try:
            self._queue.put_nowait(alert)
            return True
        except queue.Full:
            return False

    def payload(self, alert) -> tuple[str, dict]:
        """L'adresse et le corps JSON d'une publication.

        En JSON plutôt qu'en en-têtes : un en-tête HTTP ne porte pas
        proprement l'UTF-8 d'un titre accentué. L'API JSON de ntfy se
        publie à la racine du serveur, le sujet dans le corps — la racine
        étant tout ce qui précède le dernier segment, pour un serveur
        auto-hébergé sous un préfixe.
        """
        parts = urlsplit(self.url)
        path, _, topic = parts.path.rstrip("/").rpartition("/")
        root = f"{parts.scheme}://{parts.netloc}{path}/"
        return root, {
            "topic": topic,
            "title": f"BTC · {KIND_TITLES.get(alert.kind, alert.kind)}",
            "message": alert.message,
            "priority": KIND_PRIORITY.get(alert.kind, DEFAULT_PRIORITY),
            "tags": [KIND_TAGS.get(alert.kind, "bell")],
        }

    def deliver(self, alert) -> None:
        """Un envoi, dans le thread appelant — le thread d'envoi, ou l'essai
        de la ligne de commande, qui veut voir l'erreur."""
        root, body = self.payload(alert)
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = self._post(root, json=body, headers=headers,
                              timeout=self._timeout)
        response.raise_for_status()

    def _run(self) -> None:
        while True:
            alert = self._queue.get()
            if alert is None:
                return
            try:
                self.deliver(alert)
                self.sent += 1
                if self._failing:
                    self._failing = False
                    log.warning("notifications ntfy rétablies (%s)",
                                self.describe())
            except Exception as exc:
                if not self._failing:
                    self._failing = True
                    log.warning("notifications ntfy en panne (%s) : %s",
                                self.describe(), str(exc)[:120])

    def stop(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass


def from_environment(environ=None) -> Optional[Notifier]:
    """Le notificateur que décrit l'environnement, ou `None` sans sujet."""
    environ = os.environ if environ is None else environ
    url = (environ.get("BTCTERM_NTFY") or "").strip()
    if not url:
        return None
    return Notifier(url, environ.get("BTCTERM_NTFY_TOKEN"))


def main(argv=None) -> int:
    """Envoie une notification d'essai avec la configuration du moment."""
    from .alerts import Alert
    import time

    argv = sys.argv[1:] if argv is None else argv
    notifier = from_environment()
    if notifier is None:
        print("BTCTERM_NTFY n'est pas posé : aucune notification ne part.")
        return 1
    message = " ".join(argv) or "essai du terminal BTC"
    try:
        notifier.deliver(Alert(time=time.time(), kind="price", message=message))
    except Exception as exc:
        print(f"échec de l'envoi vers {notifier.describe()} : {exc}")
        return 1
    print(f"notification envoyée vers {notifier.describe()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
