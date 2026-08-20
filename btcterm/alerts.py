"""
Moteur d'alertes — le terminal sait attirer l'attention.

Douze panneaux qu'il faut balayer des yeux couvrent la surveillance
active ; la surveillance passive demande l'inverse : que le terminal
prévienne. Ce module évalue des règles à cadence lente (la boucle
d'observation du hub, 1 s) et publie des alertes — affichées par le
panneau ALERTES, comptées dans le bandeau, notifiées par le navigateur,
et journalisées (§ journal) pour être relues avec la séance.

Cinq règles, toutes nourries par ce que le hub tient déjà — aucune
connexion nouvelle :

- **seuils de prix**, posés par l'utilisateur : le sens (au-dessus,
  au-dessous) est figé à la pose, par rapport au cours du moment ; le
  seuil se désarme quand il sonne et se réarme quand le cours s'en
  écarte de 0,2 % de l'autre côté — sans cette hystérésis, un cours qui
  oscille sur le seuil sonnerait en rafale ;
- **rafale de liquidations** : le notionnel liquidé sur 5 minutes,
  toutes paires, dépasse le seuil configuré ;
- **financement extrême** : |taux par 8 h| au-delà du seuil ;
- **news à fort score** : un article encore jamais vu atteint le seuil
  — la première lecture arme sans sonner, sinon chaque démarrage
  rejouerait les gros titres de la veille ;
- **écart d'arbitrage** : le meilleur net du balayage dépasse le seuil.

Les règles d'état (tout sauf les news) sonnent sur le **front montant**
et pas avant un délai de garde : une condition qui dure ne sonne qu'une
fois, une condition qui clignote ne sonne pas en rafale.

Le moteur ne connaît pas Dash : il reçoit le hub en paramètre, comme
les fonctions de rendu des panneaux, et se teste sans réseau avec un
hub factice.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from . import newsdb

__all__ = ["Alert", "AlertEngine", "DEFAULT_CONFIG",
           "COOLDOWN", "HYSTERESIS_PCT", "SLOW_EVERY"]

#: Réglages par défaut — le panneau ALERTES les expose, le Store
#: `alert-config` (localStorage) les fait survivre au rechargement.
DEFAULT_CONFIG: dict = {
    #: [{"level": 120000.0, "dir": "above" | "below"}, …]
    "price_levels": [],
    #: rafale : notionnel liquidé sur 5 min, toutes paires, en M$.
    "liq_burst_musd": 10.0,
    #: financement : |taux par 8 h| en %.
    "funding_pct": 0.05,
    #: news : score minimal (0-100).
    "news_score": 80,
    #: arbitrage : profit net minimal en %.
    "arb_net_pct": 0.5,
    #: bip sonore côté navigateur — le moteur l'ignore, le client le lit.
    "sound": True,
}

#: Délai de garde entre deux sonneries d'une même règle d'état.
COOLDOWN = 600.0

#: Réarmement d'un seuil de prix : le cours doit s'écarter d'autant de
#: l'autre côté du seuil avant que celui-ci puisse resonner.
HYSTERESIS_PCT = 0.2

#: Cadence des contrôles qui coûtent (REST en cache, lecture SQLite) :
#: financement et news ne sont regardés qu'à ce rythme.
SLOW_EVERY = 60.0


@dataclass
class Alert:
    """Une sonnerie : quand, quelle règle, quel message."""

    time: float
    kind: str      #: price | liq | funding | news | arb
    message: str


def normalize_config(data) -> dict:
    """Rend un réglage exploitable, quoi que contienne le localStorage.

    Même rôle que `normalize_placement` pour la grille : les clés
    inconnues sont écartées, les valeurs du mauvais type retombent sur
    le défaut, les seuils de prix malformés sont éliminés.
    """
    # Copie clé par clé ET liste neuve : une copie superficielle
    # partagerait `price_levels` avec DEFAULT_CONFIG, et le premier
    # append du panneau muterait les défauts de tout le processus.
    config = {key: value for key, value in DEFAULT_CONFIG.items()}
    config["price_levels"] = []
    if not isinstance(data, dict):
        return config
    for key in ("liq_burst_musd", "funding_pct", "news_score", "arb_net_pct"):
        value = data.get(key)
        if isinstance(value, (int, float)) and value > 0:
            config[key] = float(value)
    if isinstance(data.get("sound"), bool):
        config["sound"] = data["sound"]
    levels = []
    for item in (data.get("price_levels") or []):
        if (isinstance(item, dict)
                and isinstance(item.get("level"), (int, float))
                and item["level"] > 0
                and item.get("dir") in ("above", "below")):
            entry = {"level": float(item["level"]), "dir": item["dir"]}
            if entry not in levels:
                levels.append(entry)
    config["price_levels"] = levels
    return config


class AlertEngine:
    """Évalue les règles, garde les sonneries récentes, journalise."""

    def __init__(self, journal=None,
                 fetch_news: Optional[Callable[[], list]] = None):
        self.journal = journal
        self._fetch_news = fetch_news or (lambda: newsdb.latest(20))
        self.config = normalize_config(None)
        self.alerts: deque[Alert] = deque(maxlen=200)
        self._lock = threading.Lock()
        #: État des règles à front montant : clé → {active, last}.
        self._rules: dict[str, dict] = {}
        #: Armement des seuils de prix : (level, dir) → bool.
        self._armed: dict[tuple[float, str], bool] = {}
        #: Titres déjà vus ; la première lecture arme sans sonner.
        self._seen_titles: set[str] = set()
        self._news_primed = False
        self._next_slow = 0.0

    # ── Réglages et lecture ─────────────────────────────────

    def configure(self, data) -> None:
        config = normalize_config(data)
        with self._lock:
            self.config = config
            kept = {(item["level"], item["dir"])
                    for item in config["price_levels"]}
            # Un seuil retiré puis reposé repart armé.
            self._armed = {key: armed for key, armed in self._armed.items()
                           if key in kept}

    def recent(self, limit: int = 20) -> list[Alert]:
        """Les dernières sonneries, la plus récente d'abord."""
        with self._lock:
            return list(self.alerts)[-limit:][::-1]

    def count_since(self, seconds: float, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            return sum(1 for a in self.alerts if now - a.time <= seconds)

    # ── Évaluation ──────────────────────────────────────────

    def evaluate(self, hub, opportunities=None,
                 now: Optional[float] = None) -> None:
        """Un tour de toutes les règles. Chacune échoue seule."""
        now = time.time() if now is None else now
        with self._lock:
            config = self.config
        for rule in (self._check_price, self._check_liquidations,
                     self._check_arbitrage):
            try:
                rule(hub, opportunities, config, now)
            except Exception:
                pass
        if now >= self._next_slow:
            self._next_slow = now + SLOW_EVERY
            for rule in (self._check_funding, self._check_news):
                try:
                    rule(hub, opportunities, config, now)
                except Exception:
                    pass

    def _fire(self, kind: str, message: str, now: float) -> None:
        alert = Alert(time=now, kind=kind, message=message)
        with self._lock:
            self.alerts.append(alert)
        if self.journal is not None:
            try:
                self.journal.record_alert(alert)
            except Exception:
                pass

    def _edge(self, key: str, condition: bool, now: float) -> bool:
        """Front montant sous délai de garde.

        Vrai une seule fois quand `condition` passe à vrai, et jamais
        deux fois en moins de `COOLDOWN` — une condition qui clignote
        au ras du seuil ne sonne pas en rafale.
        """
        state = self._rules.setdefault(key, {"active": False, "last": 0.0})
        fire = (condition and not state["active"]
                and now - state["last"] >= COOLDOWN)
        state["active"] = condition
        if fire:
            state["last"] = now
        return fire

    # ── Les règles ──────────────────────────────────────────

    def _check_price(self, hub, _opps, config, now) -> None:
        price = hub.reference_price()
        if not price:
            return
        margin = HYSTERESIS_PCT / 100
        for item in config["price_levels"]:
            level, direction = item["level"], item["dir"]
            key = (level, direction)
            armed = self._armed.setdefault(key, True)
            crossed = (price >= level if direction == "above"
                       else price <= level)
            if armed and crossed:
                sens = "≥" if direction == "above" else "≤"
                self._fire("price",
                           f"cours {sens} {level:,.0f} $ ({price:,.0f} $)",
                           now)
                self._armed[key] = False
            elif not armed:
                cleared = (price <= level * (1 - margin)
                           if direction == "above"
                           else price >= level * (1 + margin))
                if cleared:
                    self._armed[key] = True

    def _check_liquidations(self, hub, _opps, config, now) -> None:
        totals = hub.liquidations.totals(window=300)
        notional = totals["long"] + totals["short"]
        if self._edge("liq", notional >= config["liq_burst_musd"] * 1e6, now):
            self._fire("liq",
                       f"rafale de liquidations : {notional / 1e6:,.1f} M$ "
                       f"en 5 min (longs {totals['long'] / 1e6:,.1f} · "
                       f"shorts {totals['short'] / 1e6:,.1f})", now)

    def _check_arbitrage(self, hub, opportunities, config, now) -> None:
        best = next((o for o in opportunities or [] if o.is_profitable), None)
        condition = (best is not None
                     and best.net_profit_pct >= config["arb_net_pct"])
        if self._edge("arb", condition, now):
            self._fire("arb",
                       f"arbitrage {best.buy_exchange} → {best.sell_exchange} "
                       f"net {best.net_profit_pct:+.3f} %", now)

    def _check_funding(self, hub, _opps, config, now) -> None:
        rate = hub.perp_snapshot().get("funding_rate")
        if rate is None:
            return
        pct = rate * 100
        if self._edge("funding", abs(pct) >= config["funding_pct"], now):
            self._fire("funding",
                       f"financement extrême : {pct:+.4f} % / 8 h", now)

    def _check_news(self, hub, _opps, config, now) -> None:
        rows = self._fetch_news()
        if not self._news_primed:
            # Première lecture : tout marquer vu, ne rien sonner — sans
            # quoi chaque démarrage rejouerait les gros titres du jour.
            self._seen_titles.update(row["title"] for row in rows)
            self._news_primed = True
            return
        for row in rows:
            if row["title"] in self._seen_titles:
                continue
            self._seen_titles.add(row["title"])
            if row["score"] >= config["news_score"]:
                self._fire("news",
                           f"news ({row['score']}) : {row['title']}", now)
        if len(self._seen_titles) > 500:
            # Garder une mémoire bornée : ce qui n'est plus dans les
            # dernières lectures ne reviendra pas dans `latest`.
            self._seen_titles = {row["title"] for row in rows}
