"""
Moteur d'alertes — le terminal sait attirer l'attention.

Douze panneaux qu'il faut balayer des yeux couvrent la surveillance
active ; la surveillance passive demande l'inverse : que le terminal
prévienne. Ce module évalue des règles à cadence lente (la boucle
d'observation du hub, 1 s) et publie des alertes — affichées par le
panneau ALERTES, comptées dans le bandeau, notifiées par le navigateur,
et journalisées (§ journal) pour être relues avec la séance.

Huit règles, toutes nourries par ce que le hub tient déjà — aucune
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

S'y ajoutent trois règles **relatives**, assises sur les indicateurs
que le panneau prix calcule déjà — mêmes chandeliers, mêmes formules
(§ indicators), lues sur la dernière bougie horaire *close* pour ne pas
sonner sur le flottement de la bougie courante :

- **écart à la MA 200** : le cours s'étire au-delà du seuil (en %) de
  sa moyenne à 200 heures — l'élastique tendu, dans un sens ou l'autre ;
- **RSI extrême** : le RSI horaire sort des bornes posées (surachat,
  survente) ;
- **signal gradué fort** : un ±2 de `graded_signals` apparaît —
  croisement confirmé par la tendance ou sortie de zone extrême. Une
  sonnerie par bougie au plus : c'est un événement daté, pas un état.

Ces trois règles se taisent sur la série de démonstration : hors ligne,
des signaux calculés sur une marche aléatoire seraient du bruit déguisé
en information.

Les règles d'état (tout sauf les news et le signal) sonnent sur le
**front montant** et pas avant un délai de garde : une condition qui
dure ne sonne qu'une fois, une condition qui clignote ne sonne pas en
rafale.

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

from . import indicators as ind
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
    #: écart du cours à sa MA 200 horaire, en % — l'élastique tendu.
    "ma200_gap_pct": 10.0,
    #: bornes du RSI horaire : surachat au-dessus, survente au-dessous.
    "rsi_overbought": 80.0,
    "rsi_oversold": 20.0,
    #: sonner sur les signaux gradués forts (±2) de la bougie close.
    "signal_strong": True,
    #: bip sonore côté navigateur — le moteur l'ignore, le client le lit.
    "sound": True,
}

#: Délai de garde entre deux sonneries d'une même règle d'état.
COOLDOWN = 600.0

#: Réarmement d'un seuil de prix : le cours doit s'écarter d'autant de
#: l'autre côté du seuil avant que celui-ci puisse resonner.
HYSTERESIS_PCT = 0.2

#: Cadence des contrôles qui coûtent (REST en cache, lecture SQLite) :
#: financement, news et lecture technique ne sont regardés qu'à ce rythme.
SLOW_EVERY = 60.0

#: Chandeliers des règles relatives : l'heure est leur pas — assez lent
#: pour que MA 200 et RSI veuillent dire quelque chose, assez vif pour
#: prévenir dans la séance. 400 bougies nourrissent la MA 200 avec la
#: marge de chauffe des lissages.
KLINE_INTERVAL = "1h"
KLINE_LIMIT = 400


@dataclass
class Alert:
    """Une sonnerie : quand, quelle règle, quel message."""

    time: float
    kind: str      #: price | liq | funding | news | arb | trend | rsi | signal
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
    for key in ("liq_burst_musd", "funding_pct", "news_score", "arb_net_pct",
                "ma200_gap_pct", "rsi_overbought", "rsi_oversold"):
        value = data.get(key)
        if isinstance(value, (int, float)) and value > 0:
            config[key] = float(value)
    # Un couple RSI incohérent — survente au-dessus du surachat, borne
    # hors de [0, 100] — ferait sonner les deux règles en permanence :
    # il retombe entier sur les défauts.
    if not (0 < config["rsi_oversold"]
            < config["rsi_overbought"] <= 100):
        config["rsi_overbought"] = DEFAULT_CONFIG["rsi_overbought"]
        config["rsi_oversold"] = DEFAULT_CONFIG["rsi_oversold"]
    for key in ("sound", "signal_strong"):
        if isinstance(data.get(key), bool):
            config[key] = data[key]
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
        #: Bougie du dernier signal fort sonné : un ±2 est un événement
        #: daté, il ne sonne qu'une fois par bougie.
        self._last_signal_candle = None
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
            for rule in (self._check_funding, self._check_news,
                         self._check_technical):
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

    def _check_technical(self, hub, _opps, config, now) -> None:
        """Les trois règles relatives, sur la dernière bougie horaire close.

        Une seule lecture des chandeliers pour les trois : mêmes données,
        mêmes formules que le panneau prix (§ indicators). La bougie
        *courante* est ignorée — elle flotte, et une règle qui sonne sur
        du provisoire sonne pour rien. La série de démonstration se tait :
        des extrêmes calculés sur une marche aléatoire seraient du bruit.
        """
        df = hub.klines(KLINE_INTERVAL, limit=KLINE_LIMIT)
        if df.attrs.get("demo", False) or len(df) < 210:
            return
        work = df.copy()
        work["ma9"] = ind.sma(work["close"], 9)
        work["ma26"] = ind.sma(work["close"], 26)
        work["ma200"] = ind.sma(work["close"], 200)
        work["rsi"] = ind.rsi(work["close"], 14)

        closed = work.iloc[-2]  # la dernière bougie close
        close, ma200, rsi = (closed["close"], closed["ma200"], closed["rsi"])

        # Écart à la MA 200 : l'élastique tendu, dans un sens ou l'autre.
        if ma200 == ma200:  # NaN si l'historique est court
            gap = (close / ma200 - 1) * 100
            if self._edge("ma200", abs(gap) >= config["ma200_gap_pct"], now):
                self._fire("trend",
                           f"cours à {gap:+.1f} % de la MA 200 h "
                           f"({close:,.0f} $)", now)

        # RSI extrême : chaque borne a son front et son délai de garde.
        if rsi == rsi:
            if self._edge("rsi_hi", rsi >= config["rsi_overbought"], now):
                self._fire("rsi", f"RSI 1 h en surachat : {rsi:.0f}", now)
            if self._edge("rsi_lo", rsi <= config["rsi_oversold"], now):
                self._fire("rsi", f"RSI 1 h en survente : {rsi:.0f}", now)

        # Signal gradué fort : un événement daté — sa bougie —, jamais
        # resonné tant que la même bougie reste la dernière close.
        if config["signal_strong"]:
            grade = int(ind.graded_signals(work).iloc[-2])
            candle = closed["time"]
            if abs(grade) == 2 and candle != self._last_signal_candle:
                self._last_signal_candle = candle
                sens = "achat fort" if grade > 0 else "vente forte"
                self._fire("signal",
                           f"signal {sens} sur la bougie 1 h close "
                           f"({close:,.0f} $)", now)

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
