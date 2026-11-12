"""
Moteur d'alertes — le terminal sait attirer l'attention.

Douze panneaux qu'il faut balayer des yeux couvrent la surveillance
active ; la surveillance passive demande l'inverse : que le terminal
prévienne. Ce module évalue des règles à cadence lente (la boucle
d'observation du hub, 1 s) et publie des alertes — affichées par le
panneau ALERTES, comptées dans le bandeau, notifiées par le navigateur,
et journalisées (§ journal) pour être relues avec la séance.

Douze règles, toutes nourries par ce que le hub tient déjà — aucune
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

Une neuvième s'assoit sur l'historique que le journal accumule (§2.7) :

- **glissement de dominance** : la part du BTC dans la capitalisation
  s'est déplacée de plus du seuil (en points) sur vingt-quatre heures —
  une rotation du marché, invisible d'un instantané seul. La règle se
  tait tant que l'historique local ne couvre pas la fenêtre : elle est
  la première à ne pouvoir exister qu'à l'usage.

Deux autres lisent les collecteurs de contexte que les panneaux ETF et
on-chain tirent déjà :

- **flux ETF** : le flux net du dernier jour publié dépasse le seuil,
  en entrée comme en sortie — un événement daté, une sonnerie par jour
  au plus ; le jour présent au démarrage est tenu pour vu, comme les
  news de la première lecture ;
- **réseau chargé** : le mempool dépasse le seuil (Mo), ou le rythme
  moyen des blocs s'étire au-delà du seuil (minutes) — chacun son front.

Une dernière lit le marché des options, que le panneau du même nom tire de
Deribit :

- **volatilité implicite en hausse** : DVOL, la volatilité que les options
  attendent pour les trente jours qui viennent, monte de plus du seuil (en
  points) au-dessus de sa clôture de la veille. C'est le signal de stress
  du marché des options : sur l'année écoulée, une hausse de six points
  n'est arrivée que dix jours, dont deux dans la semaine qui a mené au
  krach du 5 février 2026, et ce jour-là.

Et une lit ce que le journal garde de ce marché (§2.7), comme la
dominance :

- **déplacement du skew** : l'écart des ailes 25 delta à un mois (call
  moins put) s'est déplacé de plus du seuil (en points) sur vingt-quatre
  heures — vers le bas, les puts se renchérissent : le marché paie pour
  se couvrir. Un écart et non un niveau : le niveau du skew dérive avec
  le régime, et l'historique local est trop court pour l'étalonner. Le
  seuil de deux points est provisoire — sur les cinq premiers jours
  journalisés (6-11 novembre 2026, marché calme), l'écart sur 24 h n'a
  pas dépassé 1,4 point. Les deux bouts se lisent en médiane de trois
  instantanés : au retour d'une panne de Deribit, un instantané isolé a
  donné −6,0 entre deux lectures à −3,1.

Les règles d'état (tout sauf les news, le signal et le flux ETF) sonnent sur le
**front montant** et pas avant un délai de garde : une condition qui
dure ne sonne qu'une fois, une condition qui clignote ne sonne pas en
rafale.

Le moteur ne connaît pas Dash : il reçoit le hub en paramètre, comme
les fonctions de rendu des panneaux, et se teste sans réseau avec un
hub factice.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

from . import indicators as ind
from . import macrocal, newsdb, sources
from . import options as opt
from .journal import liquidity_totals

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
    #: sonner quand la clôture journalière franchit sa MA 200 : la seule
    #: lecture que le bilan du panneau ANALYSE n'ait pas démentie.
    "trend_flip": True,
    #: prévenir avant les échéances macro du calendrier (§ macrocal).
    "event_soon": True,
    #: sonner quand le cours franchit le gamma flip des options (§ options).
    "gamma_flip": True,
    #: glissement de la dominance BTC sur 24 h, en points de part.
    "dominance_shift_pts": 1.5,
    #: flux ETF : |flux net du dernier jour publié| en M$.
    "etf_flow_musd": 500.0,
    #: réseau chargé : mempool en Mo, minutes moyennes entre blocs.
    "mempool_mb": 150.0,
    "block_minutes": 12.0,
    #: DVOL au-dessus de sa clôture de la veille, en points de volatilité.
    "dvol_jump_pts": 6.0,
    #: déplacement du skew 25 delta à un mois sur 24 h, en points.
    "skew_shift_pts": 2.0,
    #: carnets creux : liquidité à tant de % sous sa moyenne de 24 h.
    "liquidity_drop_pct": 40.0,
    #: prévenir quand une source ou un lien temps réel reste en défaut.
    #: Débrayée par défaut : sur un portable, un réseau qui filtre une
    #: plateforme sonnerait pour rien ; à cocher sur le serveur.
    "sources_down": False,
    #: bip sonore côté navigateur — le moteur l'ignore, le client le lit.
    "sound": True,
}

#: Délai de garde entre deux sonneries d'une même règle d'état.
COOLDOWN = 600.0

#: Réarmement d'un seuil de prix : le cours doit s'écarter d'autant de
#: l'autre côté du seuil avant que celui-ci puisse resonner.
HYSTERESIS_PCT = 0.2

#: Marge du gamma flip : le cours doit le dépasser d'autant, d'un côté puis
#: de l'autre, pour que le régime soit dit changé. Le flip bouge avec la
#: chaîne d'options, et un cours qui l'effleure sonnerait à chaque minute.
GAMMA_MARGIN_PCT = 0.5

#: Cadence des contrôles qui coûtent (REST en cache, lecture SQLite) :
#: financement, news et lecture technique ne sont regardés qu'à ce rythme.
SLOW_EVERY = 60.0

#: Chandeliers des règles relatives : l'heure est leur pas — assez lent
#: pour que MA 200 et RSI veuillent dire quelque chose, assez vif pour
#: prévenir dans la séance. 400 bougies nourrissent la MA 200 avec la
#: marge de chauffe des lissages.
KLINE_INTERVAL = "1h"
KLINE_LIMIT = 400

#: Avance de l'alerte d'échéance, en secondes.
EVENT_LEAD = 1800.0

#: Le skew se lit en médiane de trois instantanés, pris dans la
#: demi-heure pour le présent (§ `_check_skew`).
SKEW_POINTS = 3
SKEW_FRESH = 1800.0

#: La liquidité se lit en médiane de trois relevés pris dans la demi-heure,
#: contre la moyenne d'une journée qui compte au moins six heures de
#: relevés (§ `_check_liquidity`).
LIQUIDITY_POINTS = 3
LIQUIDITY_FRESH = 1800.0
LIQUIDITY_MIN_POINTS = 72

#: Durée au-delà de laquelle une source en défaut sonne, et délai après un
#: réveil de la machine pendant lequel rien ne se compte (§ `_check_sources`).
SOURCE_DOWN_AFTER = 900.0
SOURCE_WAKE_GRACE = 120.0

#: L'alerte de tendance lit le journalier : assez de bougies pour sa MA 200.
TREND_WINDOW = 200
TREND_LIMIT = 365


@dataclass
class Alert:
    """Une sonnerie : quand, quelle règle, quel message."""

    time: float
    kind: str      #: price | liq | funding | news | arb
                   #:   | trend | rsi | signal | dominance | etf | chain
                   #:   | vol | skew | depth | source
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
                "ma200_gap_pct", "rsi_overbought", "rsi_oversold",
                "dominance_shift_pts", "etf_flow_musd", "mempool_mb",
                "block_minutes", "dvol_jump_pts", "skew_shift_pts",
                "liquidity_drop_pct"):
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
    for key in ("sound", "signal_strong", "trend_flip", "event_soon",
                "gamma_flip", "sources_down"):
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
        #: Sources vues en défaut : nom → {since, rung} (§ `_check_sources`).
        self._down: dict[str, dict] = {}
        #: Armement des seuils de prix : (level, dir) → bool.
        self._armed: dict[tuple[float, str], bool] = {}
        #: Titres déjà vus ; la première lecture arme sans sonner.
        self._seen_titles: set[str] = set()
        self._news_primed = False
        #: Bougie du dernier signal fort sonné : un ±2 est un événement
        #: daté, il ne sonne qu'une fois par bougie.
        self._last_signal_candle = None
        #: Dernier jour de flux ETF sonné — ou tenu pour vu au démarrage.
        self._etf_last_date = None
        #: Côté du cours par rapport au gamma flip (`above`, `below`), et
        #: instant de la dernière sonnerie de cette règle.
        self._gamma_side: Optional[str] = None
        self._gamma_last = float("-inf")
        self._next_slow = 0.0
        #: Où envoyer chaque sonnerie hors du navigateur (§ notify) ;
        #: `None` tant qu'aucun sujet ntfy n'est configuré.
        self.notifier = None

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

    def restore(self, alerts) -> int:
        """Repeuple la mémoire de sonneries **déjà journalisées**, sans resonner.

        La mémoire ne vit qu'ici : relancer le service la vidait, et le
        panneau disait « aucune alerte » juste après une nuit qui avait
        sonné — le journal, lui, gardait tout. Comme `restore` du fil
        des liquidations : rien ne repasse par le journal, rien ne
        touche l'état des règles (fronts, seuils armés), et le
        navigateur ne fait sonner que ce qui est plus récent que sa
        dernière trame — une sonnerie relue est plus ancienne, elle
        s'affiche sans sonner. À appeler avant la boucle d'observation,
        dans l'ordre chronologique ; la mémoire est bornée, les plus
        anciennes sortent d'elles-mêmes.
        """
        kept = 0
        with self._lock:
            for alert in alerts:
                self.alerts.append(alert)
                kept += 1
        return kept

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
                         self._check_technical, self._check_trend,
                         self._check_gamma,
                         self._check_event, self._check_dominance,
                         self._check_etf, self._check_chain,
                         self._check_volatility, self._check_skew,
                         self._check_liquidity, self._check_sources):
                try:
                    rule(hub, opportunities, config, now)
                except Exception:
                    pass

    def _fire(self, kind: str, message: str, now: float) -> None:
        alert = Alert(time=now, kind=kind, message=message)
        with self._lock:
            self.alerts.append(alert)
        if self.notifier is not None:
            # File d'attente, jamais d'appel réseau ici : la boucle
            # d'observation n'attend pas ntfy.
            self.notifier.send(alert)
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
                       f"net {best.net_profit_pct:+z.3f} %", now)

    def _check_funding(self, hub, _opps, config, now) -> None:
        rate = hub.perp_snapshot().get("funding_rate")
        if rate is None:
            return
        pct = rate * 100
        if self._edge("funding", abs(pct) >= config["funding_pct"], now):
            self._fire("funding",
                       f"financement extrême : {pct:+z.4f} % / 8 h", now)

    def _check_technical(self, hub, _opps, config, now) -> None:
        """Les trois règles relatives, sur la dernière bougie horaire close.

        Une seule lecture des chandeliers pour les trois : mêmes données,
        mêmes formules que le panneau prix (§ indicators). La bougie
        *courante* est ignorée — elle flotte, et une règle qui sonne sur
        du provisoire sonne pour rien. La série de démonstration se tait :
        des extrêmes calculés sur une marche aléatoire seraient du bruit.
        L'historique sauvegardé aussi : sans bougie en cours, l'avant-dernière
        ligne n'est plus la dernière close, et le cours est à l'arrêt.
        """
        df = hub.klines(KLINE_INTERVAL, limit=KLINE_LIMIT)
        if (df.attrs.get("demo", False) or df.attrs.get("stale", False)
                or len(df) < 210):
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
                           f"cours à {gap:+z.1f} % de la MA 200 h "
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

    def _check_trend(self, hub, _opps, config, now) -> None:
        """La clôture journalière passe de l'autre côté de sa MA 200.

        Un événement daté, comme le signal gradué, mais sur l'intervalle où
        il a un sens. Sa date est dans le message : relue du journal au
        redémarrage, la sonnerie du jour n'est pas rejouée.
        """
        if not config["trend_flip"]:
            return
        df = hub.klines("1d", limit=TREND_LIMIT)
        if (df.attrs.get("demo", False) or df.attrs.get("stale", False)
                or len(df) < TREND_WINDOW + 3):
            return
        ma = ind.sma(df["close"], TREND_WINDOW)
        # La dernière ligne est la bougie en cours : elle flotte.
        above, before = (df["close"].iloc[-2] > ma.iloc[-2],
                         df["close"].iloc[-3] > ma.iloc[-3])
        if above == before:
            return
        day = pd.Timestamp(df["time"].iloc[-2]).strftime("%Y-%m-%d")
        with self._lock:
            known = any(a.kind == "flip" and day in a.message
                        for a in self.alerts)
        if not known:
            sens = "au-dessus" if above else "sous"
            self._fire("flip", f"clôture du {day} {sens} de la MA 200 "
                                f"journalière ({df['close'].iloc[-2]:,.0f} $)",
                       now)

    def _check_gamma(self, hub, _opps, config, now) -> None:
        """Le cours franchit le gamma flip : les teneurs de marché changent
        de régime (§ options.gamma_flip). D'un côté, ils vendent la hausse
        et achètent la baisse — le marché se tasse ; de l'autre, ils suivent
        le mouvement — il s'étire. Le cours doit dépasser le flip de
        `GAMMA_MARGIN_PCT`, et la première lecture fixe le côté sans sonner,
        comme celle des flux ETF. Le message dit le signe réel du GEX au
        prix du moment plutôt que de le supposer.
        """
        if not config["gamma_flip"]:
            return
        reader = getattr(hub, "option_chain", None)
        price = hub.reference_price()
        if reader is None or not price:
            return
        chain = reader()
        moment = pd.Timestamp(now, unit="s", tz="UTC")
        gex = opt.gamma_exposure(chain, moment)
        if gex is None:
            return
        flip = opt.gamma_flip(opt.gamma_profile(chain, moment), gex["spot"])
        if flip is None:
            return
        margin = GAMMA_MARGIN_PCT / 100
        side = ("above" if price >= flip * (1 + margin)
                else "below" if price <= flip * (1 - margin) else None)
        if side is None:
            return
        previous, self._gamma_side = self._gamma_side, side
        if previous is None or previous == side:
            return
        if now - self._gamma_last < COOLDOWN:
            return
        self._gamma_last = now
        sens = "au-dessus du" if side == "above" else "sous le"
        regime = "tassent" if gex["total"] >= 0 else "étirent"
        self._fire("gamma",
                   f"cours {sens} gamma flip ({flip:,.0f} $, cours "
                   f"{price:,.0f} $) : les teneurs {regime} désormais les "
                   f"mouvements (GEX {gex['total'] / 1e6:+z,.0f} M$/1 %)", now)

    def _check_event(self, _hub, _opps, config, now) -> None:
        """Une échéance macro approche : le temps de réduire ou de s'écarter.

        Un FOMC ou un CPI bougent le cours en quelques secondes : c'est avant
        qu'il faut le savoir. Une sonnerie par échéance, datée dans son
        message, donc pas rejouée au redémarrage.
        """
        if not config["event_soon"]:
            return
        moment = dt.datetime.fromtimestamp(now, tz=dt.timezone.utc)
        for event in macrocal.upcoming(moment.date() - dt.timedelta(days=1)):
            left = (event.when_utc - moment).total_seconds()
            if left > EVENT_LEAD:
                break
            if left <= 0:
                continue
            stamp = event.when_utc.strftime("%Y-%m-%d %H:%M")
            with self._lock:
                known = any(a.kind == "event" and stamp in a.message
                            for a in self.alerts)
            if not known:
                self._fire("event", f"{event.kind} dans {int(left // 60)} min "
                                    f"({stamp} UTC) : {event.label}", now)

    def _check_dominance(self, hub, _opps, config, now) -> None:
        """Glissement de la dominance BTC sur vingt-quatre heures.

        La référence est l'instantané journalisé le plus proche de la
        fenêtre — au moins 23 h d'âge : tant que l'historique local ne
        couvre pas la fenêtre, la règle se tait plutôt que de comparer
        à un passé trop proche, où tout écart semblerait un glissement.
        """
        history = hub.market_snapshots(days=2)
        if history.empty:
            return
        serie = history.dropna(subset=["btc_dominance"])
        if serie.empty:
            return
        # Le passage explicite par la nanoseconde rend la conversion en
        # secondes indépendante de la résolution du DataFrame — le même
        # piège que la sérialisation LWC a déjà contourné (§ lwc).
        epoch = (pd.to_datetime(serie["time"]).astype("datetime64[ns]")
                 .astype("int64") // 1_000_000_000)
        latest = float(serie["btc_dominance"].iloc[-1])
        anciens = serie[epoch <= now - 23 * 3600]
        if anciens.empty:
            return
        reference = float(anciens["btc_dominance"].iloc[-1])
        delta = latest - reference
        if self._edge("dominance",
                      abs(delta) >= config["dominance_shift_pts"], now):
            self._fire("dominance",
                       f"dominance BTC {delta:+z.1f} pt en 24 h "
                       f"({latest:.1f} %)", now)

    def _check_etf(self, hub, _opps, config, now) -> None:
        """Flux net du dernier jour publié, en entrée comme en sortie.

        Un jour de flux est un événement daté : une sonnerie par date au
        plus. La date n'est retenue qu'au moment où elle sonne — la
        source remplit son jour au fil de la soirée, émetteur après
        émetteur, et le total peut franchir le seuil bien après
        l'apparition de la ligne. Le jour présent à la première lecture
        est tenu pour vu sans sonner : rejouer la veille à chaque
        démarrage serait le défaut que la règle des news évite déjà.
        """
        df = hub.etf_flows()
        if df is None or df.empty:
            return
        total_col = "Total" if "Total" in df.columns else df.columns[-1]
        last = df.iloc[-1]
        date = pd.Timestamp(last["Date"]).date()
        if self._etf_last_date is None:
            self._etf_last_date = date
            return
        flow = float(last[total_col])
        if date == self._etf_last_date or abs(flow) < config["etf_flow_musd"]:
            return
        self._etf_last_date = date
        sens = "entrée" if flow >= 0 else "sortie"
        # Le seuil peut être franchi avant qu'IBIT ait publié : la
        # sonnerie le dit, le total final pouvant encore changer de sens.
        pending = sources.etf_pending(df)
        partiel = (f" — partiel, {', '.join(pending)} à venir"
                   if pending else "")
        self._fire("etf",
                   f"flux ETF du {date:%d/%m} : {sens} nette {flow:+z,.0f} M$"
                   f"{partiel}", now)

    def _check_chain(self, hub, _opps, config, now) -> None:
        """Réseau chargé : mempool gonflé ou blocs lents, chacun son front.

        Mêmes lectures que le panneau on-chain — la série du mempool en
        cache et l'instantané du réseau —, ramenées à leur dernier
        point. blockchain.info donne le mempool en octets et le rythme
        des blocs en moyenne sur la journée : un rythme qui s'étire dit
        que le réseau a perdu des mineurs depuis le dernier ajustement.
        """
        mempool = hub.chain_chart("mempool-size", "5weeks")
        if mempool is not None and not mempool.empty:
            size_mb = float(mempool["value"].iloc[-1]) / 1e6
            if self._edge("mempool", size_mb >= config["mempool_mb"], now):
                self._fire("chain",
                           f"mempool chargé : {size_mb:,.0f} Mo en attente",
                           now)
        minutes = float((hub.chain_stats() or {})
                        .get("minutes_between_blocks") or 0)
        if minutes and self._edge("blocks",
                                  minutes >= config["block_minutes"], now):
            self._fire("chain",
                       f"blocs lents : {minutes:.1f} min en moyenne "
                       "(cible 10)", now)

    def _check_volatility(self, hub, _opps, config, now) -> None:
        """Volatilité implicite en hausse : DVOL au-dessus de la veille.

        La bougie journalière en cours porte la dernière valeur de l'indice,
        la précédente la clôture de la veille. Une sonnerie par montée : la
        condition tient le reste de la journée, le front ne sonne qu'une
        fois, et une nouvelle journée la réévalue contre sa propre veille.
        """
        dvol = hub.dvol()
        if dvol is None or len(dvol) < 2:
            return
        last = float(dvol["close"].iloc[-1])
        jump = last - float(dvol["close"].iloc[-2])
        if self._edge("dvol", jump >= config["dvol_jump_pts"], now):
            self._fire("vol",
                       f"volatilité implicite en hausse : DVOL {last:.1f}, "
                       f"{jump:+z.1f} points depuis la clôture d'hier", now)

    def _check_skew(self, hub, _opps, config, now) -> None:
        """Déplacement du skew 25 delta sur vingt-quatre heures.

        Même lecture que la dominance, sur les instantanés du journal,
        avec deux gardes de plus. Chaque bout est la médiane de
        `SKEW_POINTS` instantanés : une cotation isolée, au retour d'une
        panne de la source, ne fait pas un déplacement. Et les deux bouts
        doivent être frais — le présent dans la demi-heure, la référence
        entre 23 et 30 heures : après une longue veille, l'écart ne
        serait plus « sur 24 h ».
        """
        history = hub.market_snapshots(days=2)
        if history.empty or "rr25" not in history.columns:
            return
        serie = history.dropna(subset=["rr25"])
        if serie.empty:
            return
        epoch = (pd.to_datetime(serie["time"]).astype("datetime64[ns]")
                 .astype("int64") // 1_000_000_000)
        recents = serie[epoch >= now - SKEW_FRESH]["rr25"].tail(SKEW_POINTS)
        anciens = serie[(epoch <= now - 23 * 3600)
                        & (epoch >= now - 30 * 3600)]["rr25"].tail(SKEW_POINTS)
        if len(recents) < SKEW_POINTS or len(anciens) < SKEW_POINTS:
            return
        latest = float(recents.median())
        delta = latest - float(anciens.median())
        if self._edge("skew", abs(delta) >= config["skew_shift_pts"], now):
            sens = ("les puts se renchérissent" if delta < 0
                    else "les calls se renchérissent")
            self._fire("skew",
                       f"skew 25Δ {delta:+z.1f} pt en 24 h "
                       f"({latest:+z.1f}) : {sens}", now)

    def _check_liquidity(self, hub, _opps, config, now) -> None:
        """Les carnets qui se vident : la liquidité loin sous sa moyenne.

        Le volume offert à portée du prix, plateformes réunies (§ hub.
        _flush_depth), contre sa propre moyenne de la journée : un carnet
        creux n'amortit pas le mouvement qui vient. Trois gardes. La
        moyenne demande six heures de relevés, sans quoi elle ne décrit
        que l'heure d'ouverture du terminal. Le présent est la médiane de
        trois relevés pris dans la demi-heure — une plateforme en
        resynchronisation ne vide pas le marché. Et la somme ne porte que
        sur les plateformes présentes toute la journée
        (`liquidity_totals`) : une plateforme qui se tait sort du compte
        au lieu de creuser le total.
        """
        history = hub.book_depth_history(days=1)
        if history is None or history.empty:
            return
        totals, _kept, _dropped = liquidity_totals(
            history[["time", "exchange", "bid", "ask"]]
            .itertuples(index=False))
        if len(totals) < LIQUIDITY_MIN_POINTS:
            return
        volumes = [bid + ask for _, bid, ask in totals]
        recents = sorted(
            bid + ask for moment, bid, ask in totals[-LIQUIDITY_POINTS:]
            if pd.Timestamp(moment).timestamp() >= now - LIQUIDITY_FRESH)
        if len(recents) < LIQUIDITY_POINTS:
            return
        latest = recents[len(recents) // 2]
        mean = sum(volumes) / len(volumes)
        if mean <= 0:
            return
        drop = (1 - latest / mean) * 100
        if self._edge("liquidity", drop >= config["liquidity_drop_pct"], now):
            band = getattr(hub, "DEPTH_BAND", 0.1)
            self._fire("depth",
                       f"carnets à {drop:.0f} % sous leur moyenne de 24 h : "
                       f"{latest:.0f} BTC à ±{band:g} % du prix, contre "
                       f"{mean:.0f}", now)

    def _check_sources(self, hub, _opps, config, now) -> None:
        """Une source ou un lien temps réel qui reste en défaut.

        Sur le serveur allumé en permanence qu'on vise, personne ne
        regarde le panneau SOURCES : un carnet coupé depuis une heure, ou
        Deribit en panne toute une nuit, ne se voyaient qu'au retour
        devant l'écran. Sonne ce qui est « coupé » (un lien temps réel)
        ou « en panne » (une source REST, une tâche de fond) depuis plus
        de `SOURCE_DOWN_AFTER` — une fois par épisode, et une seconde fois
        au rétablissement de ce qui avait sonné. Un lien « muet » ne sonne
        pas : c'est l'état ordinaire d'un fil de liquidations calme — le
        panneau SOURCES le montre, l'âge du silence laisse juger. Après un réveil de
        la machine, les compteurs repartent de zéro : tout est coupé à
        cet instant, et rien de cela n'est une panne.
        """
        if not config["sources_down"]:
            self._down.clear()
            return
        woke = getattr(hub, "woke_within", None)
        if woke is not None and woke(SOURCE_WAKE_GRACE):
            self._down.clear()
            return
        down: dict[str, tuple[str, Optional[float]]] = {}
        for rows in hub.sources_status(now).values():
            for row in rows:
                if row["state"] in ("coupé", "panne"):
                    down[row["name"]] = (row.get("detail") or "",
                                         row.get("since"))
        for name in list(self._down):
            if name not in down:
                entry = self._down.pop(name)
                if entry["rung"]:
                    minutes = (now - entry["since"]) / 60
                    self._fire("source",
                               f"{name} : rétabli après {minutes:.0f} min", now)
        for name, (detail, since) in down.items():
            entry = self._down.setdefault(
                name, {"since": since if since is not None else now,
                       "rung": False})
            if not entry["rung"] and now - entry["since"] >= SOURCE_DOWN_AFTER:
                entry["rung"] = True
                minutes = (now - entry["since"]) / 60
                self._fire("source",
                           f"{name} : en défaut depuis {minutes:.0f} min"
                           + (f" — {detail[:80]}" if detail else ""), now)

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
