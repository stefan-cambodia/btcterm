"""
Synthèse — les dernières vingt-quatre heures en une page.

Chaque panneau dit son présent ; aucun ne disait ce que la nuit a changé.
Au retour devant l'écran — ou au réveil, sur le téléphone —, la question
est la même : où en est le cours, qu'est-ce qui a bougé autour, qu'est-ce
qui a été liquidé, qu'est-ce qui arrive aujourd'hui. La synthèse y répond
en une douzaine de lignes, toutes tirées de ce que le terminal tient
déjà : le ticker de Binance, les instantanés de marché du journal (§2.7),
ses liquidations, ses gros ordres et ses alertes, les flux ETF, le Fear &
Greed, le cycle (§ cycle), le calendrier macro et les news les mieux
notées.

Elle vit en trois endroits :

- le panneau SYNTHÈSE (terminal/panels/brief.py) ;
- un message ntfy chaque matin, à l'heure de `BTCTERM_BRIEF` (§ hub,
  `_maybe_brief`) — le serveur allumé en permanence qu'on vise n'a
  personne devant son écran ;
- la ligne de commande : `python -m btcterm.brief`.

**La couverture se dit.** Un portable dort : les liquidations et les gros
ordres journalisés ne couvrent que le temps où il veillait, et les écarts
sur vingt-quatre heures se calculent sur l'instantané le plus proche, qui
peut dater de quinze heures seulement. La synthèse l'écrit — heures
couvertes, durée réelle de chaque écart — au lieu de présenter une nuit
de sommeil comme une nuit calme.

Les fonctions de section sont pures : elles prennent des données et
rendent une ligne, testées sans réseau (tests/test_brief.py). `build`
seul parle au hub, et une section qui échoue n'emporte pas les autres.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from . import cycle, macrocal, newsdb
from .sources import etf_pending
from .journal import liquidity_totals

log = logging.getLogger("btcterm.brief")

__all__ = ["Item", "Brief", "build", "parse_time", "SentLog"]

#: La fenêtre relue, en heures.
HOURS = 24
#: Un écart calculé sur moins d'heures ne dit plus « la journée » : il
#: n'est pas donné, plutôt que donné pour ce qu'il n'est pas.
MIN_SPAN_H = 12
#: Échéances macro listées : celles des prochaines heures — et toujours
#: la prochaine, si loin soit-elle.
AGENDA_HOURS = 48
#: News retenues, les mieux notées de la fenêtre.
NEWS_ROWS = 3
#: Un trou plus long entre deux instantanés est une interruption — la
#: même valeur que `MarketHub.SNAPSHOT_GAP`.
GAP = 900

_JOURS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


@dataclass
class Item:
    """Une ligne : son nom, ce qu'elle dit, sa couleur et son détail."""

    label: str
    text: str
    #: "up" | "down" | "warn" | "muted" | "" (neutre).
    tone: str = ""
    #: Le détail, au survol dans le panneau.
    detail: str = ""


@dataclass
class Brief:
    """La synthèse d'un instant : ses lignes, et ce que le journal couvre."""

    at: float
    items: list[Item] = field(default_factory=list)
    #: Absente quand le journal couvre toute la fenêtre.
    coverage: Optional[str] = None

    def text(self) -> str:
        """La version texte : ligne de commande et message ntfy."""
        head = "Synthèse du " + time.strftime("%d/%m à %H:%M",
                                              time.localtime(self.at))
        lines = [head]
        if self.coverage:
            lines.append(f"⚠ {self.coverage}")
        lines += [f"{item.label} : {item.text}" if item.label
                  else f"  {item.text}" for item in self.items]
        return "\n".join(lines)


# ── Mise en forme ───────────────────────────────────────────


def _usd(value: float) -> str:
    """Un montant sans signe : « 7.26 Md$ », « 48.2 M$ », « 310 k$ »."""
    value = abs(value)
    if value >= 1e9:
        return f"{value / 1e9:.2f} Md$"
    if value >= 1e6:
        return f"{value / 1e6:.1f} M$"
    if value >= 1e3:
        return f"{value / 1e3:.0f} k$"
    return f"{value:.0f} $"


def _signed_usd(value: float) -> str:
    return ("+" if value >= 0 else "-") + _usd(value)


def _tone(value: float, dead: float = 0.0) -> str:
    return "up" if value > dead else "down" if value < -dead else ""


def _span(hours: float) -> str:
    """« 24 h » quand l'écart couvre la journée, la durée réelle sinon."""
    return f"{HOURS} h" if hours >= HOURS - 1 else f"{hours:.0f} h"


def _duree(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


def _hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _jour(ts: float) -> str:
    """« ven. 13/11 20:30 », à l'heure de la machine."""
    moment = time.localtime(ts)
    return f"{_JOURS[moment.tm_wday]} {time.strftime('%d/%m %H:%M', moment)}"


def _dans(seconds: float) -> str:
    if seconds < 3600:
        return f"dans {int(seconds // 60)} min"
    if seconds < 48 * 3600:
        return f"dans {_duree(seconds)}"
    return f"dans {int(seconds // 86400)} j"


# ── Instantanés : maintenant, et il y a vingt-quatre heures ──


def change(rows, name: str, now: float, hours: float = HOURS):
    """`(actuel, référence, heures)` d'un champ des instantanés, ou `None`.

    L'actuel est le dernier instantané qui porte le champ ; la référence,
    celui qui tombe le plus près de `hours` heures plus tôt — la machine a
    pu dormir à ce moment-là, et `heures` dit alors l'écart réel. Une
    référence à moins de `MIN_SPAN_H` heures vaut `None` : l'écart ne
    dirait pas la journée.
    """
    points = [(row["ts"], row[name]) for row in rows if row[name] is not None]
    if not points:
        return None
    ts_now, current = points[-1]
    target = now - hours * 3600
    ts_ref, reference = min(points, key=lambda point: abs(point[0] - target))
    span = (ts_now - ts_ref) / 3600
    if span < MIN_SPAN_H:
        return current, None, span
    return current, reference, span


def coverage(stamps: list[float], start: float, end: float,
             gap: float = GAP) -> Optional[str]:
    """Ce que le journal couvre de la fenêtre, s'il en manque.

    Un instantané part toutes les cinq minutes tant que le service tourne :
    les trous de plus de `gap` — entre deux instantanés, avant le premier,
    après le dernier — sont le temps où la machine dormait, ou le service
    était arrêté. `None` quand il manque moins de dix minutes.
    """
    hours = (end - start) / 3600
    if not stamps:
        return f"journal vide sur {hours:.0f} h : rien de journalisé"
    edges = [start, *sorted(stamps), end]
    holes = [b - a for a, b in zip(edges, edges[1:]) if b - a > gap]
    missing = sum(holes)
    if missing < 600:
        return None
    covered = (end - start) - missing
    pluriel = "s" if len(holes) > 1 else ""
    return (f"journal : {_duree(covered)} couvertes sur {hours:.0f} h "
            f"({len(holes)} interruption{pluriel}) — liquidations et gros "
            f"ordres n'y comptent que ce temps-là")


# ── Sections ─────────────────────────────────────────────────


def price_item(ticker: dict) -> Item:
    """Le cours et sa journée glissante, d'après le ticker de Binance."""
    try:
        last = float(ticker["lastPrice"])
        pct = float(ticker["priceChangePercent"])
        high, low = float(ticker["highPrice"]), float(ticker["lowPrice"])
    except (KeyError, TypeError, ValueError):
        return Item("Cours", "Binance injoignable", "muted")
    amplitude = (high - low) / low * 100 if low else 0.0
    where = (last - low) / (high - low) * 100 if high > low else 50.0
    return Item(
        "Cours",
        f"{last:,.0f} $ · {pct:+z.2f} % · haut {high:,.0f} · bas {low:,.0f}",
        _tone(pct),
        f"journée glissante de Binance : amplitude {amplitude:.1f} %, le "
        f"cours à {where:.0f} % de la fourchette")


def perp_item(funding_rate: Optional[float], open_interest) -> Item:
    """Financement courant, open interest et son écart sur la journée."""
    parts, detail = [], []
    if funding_rate is not None:
        parts.append(f"financement {funding_rate * 100:+z.4f} % /8 h")
        detail.append(f"soit {funding_rate * 3 * 365 * 100:+z.1f} % par an")
    if open_interest:
        current, reference, span = open_interest
        text = f"OI {_usd(current)}"
        if reference:
            text += f" ({(current / reference - 1) * 100:+z.1f} % sur {_span(span)})"
        parts.append(text)
    if not parts:
        return Item("Perpétuel", "indisponible", "muted")
    detail.append("open interest Binance, d'après les instantanés du journal")
    tone = "warn" if funding_rate is not None and abs(funding_rate) >= 5e-4 else ""
    return Item("Perpétuel", " · ".join(parts), tone, " ; ".join(detail))


def liquidations_item(rows) -> Item:
    """Le bilan des liquidations journalisées : côtés, part du bitcoin."""
    if not rows:
        return Item("Liquidations", "aucune journalisée", "muted")
    longs = sum(r["notional"] for r in rows if r["side"] == "long")
    shorts = sum(r["notional"] for r in rows if r["side"] == "short")
    btc = sum(r["notional"] for r in rows if r["symbol"].startswith("BTC"))
    biggest = max(rows, key=lambda r: r["notional"])
    # Des longs liquidés ont été vendus de force : la journée a baissé.
    tone = ("down" if longs > 1.5 * shorts else
            "up" if shorts > 1.5 * longs else "")
    return Item(
        "Liquidations",
        f"longs {_usd(longs)} · shorts {_usd(shorts)} · dont BTC {_usd(btc)}",
        tone,
        f"{len(rows)} événements ; la plus grosse : {biggest['symbol']} "
        f"{biggest['side']} {_usd(biggest['notional'])} à "
        f"{_hhmm(biggest['ts'])}")


def trades_item(rows) -> Item:
    """Les gros ordres du ruban : ce que chaque côté a pesé."""
    if not rows:
        return Item("Gros ordres", "aucun journalisé", "muted")
    buys = sum(r["notional"] for r in rows if r["side"] == "buy")
    sells = sum(r["notional"] for r in rows if r["side"] == "sell")
    net = buys - sells
    biggest = max(rows, key=lambda r: r["notional"])
    sens = "achat" if biggest["side"] == "buy" else "vente"
    return Item(
        "Gros ordres",
        f"achats {_usd(buys)} · ventes {_usd(sells)} · solde {_signed_usd(net)}",
        _tone(net),
        f"{len(rows)} ordres de 100 k$ et plus, côté de l'agresseur ; le plus "
        f"gros : {sens} {biggest['exchange']} {_usd(biggest['notional'])} à "
        f"{_hhmm(biggest['ts'])}")


def etf_item(frame: Optional[pd.DataFrame]) -> Item:
    """Le dernier jour de flux publié, et les cinq dernières séances."""
    if frame is None or frame.empty:
        return Item("ETF", "flux indisponibles", "muted")
    column = "Total" if "Total" in frame.columns else frame.columns[-1]
    totals = pd.to_numeric(frame[column], errors="coerce")
    known = frame[totals.notna()]
    if known.empty:
        return Item("ETF", "flux indisponibles", "muted")
    flows = pd.to_numeric(known[column])
    day = pd.Timestamp(known["Date"].iloc[-1]).date()
    last, week = float(flows.iloc[-1]), float(flows.tail(5).sum())
    # Une journée encore partielle le dit dans le texte même : la synthèse
    # part aussi vers le téléphone, où il n'y a pas de survol.
    pending = etf_pending(frame)
    partiel = (f", partiel ({', '.join(pending)} à venir)" if pending else "")
    return Item("ETF", f"{last:+z,.0f} M$ le {day:%d/%m}{partiel} · 5 séances "
                       f"{week:+z,.0f} M$", "warn" if pending else _tone(last),
                "flux nets des ETF spot américains, farside.co.uk")


def sentiment_item(history: list[dict]) -> Item:
    """Le Fear & Greed du jour, et celui de la veille."""
    if not history:
        return Item("Sentiment", "Fear & Greed indisponible", "muted")
    last = history[-1]
    text = f"Fear & Greed {last['value']} ({last['label']})"
    if len(history) > 1:
        text += f", {history[-2]['value']} la veille"
    return Item("Sentiment", text, "", "alternative.me, une valeur par jour")


def volatility_item(dvol, rr25) -> Optional[Item]:
    """DVOL et son écart, skew des ailes 25 delta."""
    if not dvol:
        return None
    current, reference, span = dvol
    text = f"DVOL {current:.1f}"
    tone = ""
    if reference is not None:
        text += f" ({current - reference:+z.1f} sur {_span(span)})"
        tone = "warn" if current - reference >= 6 else ""
    if rr25:
        text += f" · skew 25Δ {rr25[0]:+z.1f}"
    return Item("Volatilité", text, tone,
                "volatilité implicite Deribit à 30 jours ; skew négatif : "
                "les puts plus chers que les calls")


def premium_item(rows, now: float) -> Optional[Item]:
    """La prime Coinbase : la dernière, et sa moyenne sur la fenêtre."""
    values = [row["cb_premium"] for row in rows
              if row["cb_premium"] is not None
              and row["ts"] >= now - HOURS * 3600]
    if not values:
        return None
    average = sum(values) / len(values)
    return Item("Prime Coinbase",
                f"{values[-1]:+z.2f} % · moyenne {average:+z.2f} %",
                _tone(average, 0.02),
                "Coinbase en dollars contre Binance en USDT : la demande "
                "américaine, et une part de décote de l'USDT")


#: Relevés de liquidité exigés pour en parler : une heure de service.
LIQUIDITY_MIN = 12
#: Écart à la moyenne en deçà duquel le carnet est dit creux, en %.
LIQUIDITY_THIN = -30.0


def liquidity_item(rows, band: float = 0.1) -> Optional[Item]:
    """La liquidité des carnets : le dernier relevé contre sa moyenne.

    `rows` sont les lignes `book_depth` de la fenêtre (§ hub.
    _flush_depth). Un carnet n'est ni plein ni vide dans l'absolu : c'est
    l'écart à sa propre moyenne qui se lit, et un carnet creux est un
    avertissement — il amortira mal le prochain mouvement.
    """
    totals, kept, dropped = liquidity_totals(
        (row["ts"], row["exchange"], row["bid_btc"], row["ask_btc"])
        for row in rows)
    if len(totals) < LIQUIDITY_MIN:
        return None
    volumes = [bid + ask for _, bid, ask in totals]
    _, bid, ask = totals[-1]
    mean = sum(volumes) / len(volumes)
    gap = (volumes[-1] / mean - 1) * 100 if mean else 0.0
    share = 100 * bid / (bid + ask) if bid + ask else 50.0
    detail = (f"volume offert à moins de {band:g} % du prix, moyenne de "
              f"chaque période de cinq minutes — {', '.join(kept)}"
              + (f" ; sans {', '.join(dropped)}" if dropped else ""))
    return Item("Liquidité",
                f"{volumes[-1]:.0f} BTC à ±{band:g} % ({gap:+z.0f} % contre "
                f"la moyenne) · achats {share:.0f} %",
                "warn" if gap <= LIQUIDITY_THIN else "", detail)


def dominance_item(dominance) -> Optional[Item]:
    if not dominance:
        return None
    current, reference, span = dominance
    text = f"BTC {current:.1f} %"
    if reference is not None:
        text += f" ({current - reference:+z.2f} pt sur {_span(span)})"
    return Item("Dominance", text, "", "part du bitcoin dans la "
                                       "capitalisation totale, CoinGecko")


def agenda_item(now: float, events) -> Item:
    """Les échéances macro qui arrivent — la prochaine au moins."""
    ahead = [e for e in events if e.when_utc.timestamp() > now]
    if not ahead:
        return Item("Agenda", "calendrier épuisé — à compléter "
                              "(btcterm/macrocal.py)", "warn")
    soon = [e for e in ahead
            if e.when_utc.timestamp() - now <= AGENDA_HOURS * 3600] or ahead[:1]
    parts = []
    for event in soon[:3]:
        ts = event.when_utc.timestamp()
        parts.append(f"{event.kind} {_jour(ts)} ({_dans(ts - now)})")
    first = soon[0].when_utc.timestamp() - now
    return Item("Agenda", " · ".join(parts),
                "warn" if first <= 24 * 3600 else "",
                " ; ".join(e.label for e in soon[:3]))


def alerts_item(rows) -> Item:
    """Ce qui a sonné, et la dernière sonnerie."""
    if not rows:
        return Item("Alertes", "rien n'a sonné", "muted")
    last = rows[-1]
    message = last["message"]
    if len(message) > 90:
        message = message[:89] + "…"
    return Item("Alertes", f"{len(rows)} · dernière à {_hhmm(last['ts'])} : "
                           f"{message}", "",
                "les sonneries de la fenêtre, panneau ALERTES et journal")


def news_items(rows) -> list[Item]:
    """Les news les mieux notées de la fenêtre, une ligne chacune."""
    items = []
    for index, row in enumerate(rows[:NEWS_ROWS]):
        title = row["title"]
        if len(title) > 100:
            title = title[:99] + "…"
        tone = {"bullish": "up", "bearish": "down"}.get(row["sentiment"], "")
        items.append(Item("News" if index == 0 else "",
                          f"({row['score']}) {title}", tone, row["source"]))
    return items


# ── Assemblage ───────────────────────────────────────────────


def cycle_item(state: Optional[dict]) -> Optional[Item]:
    """Où en est le cours dans son cycle (§ cycle) : jour depuis le
    halving, distance au sommet, multiple de Mayer, halving à venir."""
    if not state:
        return None
    parts = []
    if state["cycle_day"] is not None:
        parts.append(f"J+{state['cycle_day']} du halving "
                     f"(×{state['cycle_multiple']:.2f})")
    if state["drawdown"] > -0.05:
        parts.append("au sommet")
    else:
        parts.append(f"{state['drawdown']:+z.1f} % du sommet, "
                     f"depuis {state['days_since_ath']} j")
    tone = ""
    if state["mayer"] is not None:
        parts.append(f"Mayer {state['mayer']:.2f}")
        # Aux bornes d'usage seulement : entre les deux, le multiple
        # situe, il n'avertit pas.
        if not cycle.MAYER_LOW < state["mayer"] < cycle.MAYER_HIGH:
            tone = "warn"
    halving = state["halving"]
    parts.append(f"halving dans {'~' if halving['estimated'] else ''}"
                 f"{halving['days_left']:.0f} j")
    return Item("Cycle", " · ".join(parts), tone,
                "multiple du cours du jour du halving ; sommet en clôture "
                "journalière ; Mayer : cours / moyenne de 200 jours ; "
                "halving compté à dix minutes le bloc")


def _cycle_state(hub, now: float) -> Optional[dict]:
    """La situation du cycle d'après le hub ; la hauteur de la chaîne
    peut manquer, le compte à rebours est alors estimé."""
    try:
        height = (hub.chain_stats() or {}).get("height")
    except Exception:  # noqa: BLE001 — la hauteur est facultative
        height = None
    return cycle.metrics(hub.cycle_closes(), height,
                         dt.datetime.fromtimestamp(now, dt.timezone.utc))


def _funding(hub, snapshots, now: float) -> Optional[float]:
    """Le financement en cours ; à défaut — Binance Futures muet après un
    réveil —, le dernier que le journal a noté."""
    rate = hub.perp_snapshot().get("funding_rate")
    if rate is not None:
        return rate
    noted = change(snapshots, "funding_rate", now)
    return noted[0] if noted else None


def build(hub, now: Optional[float] = None,
          news: Optional[Callable[[dt.datetime, int], list]] = None) -> Brief:
    """La synthèse du moment, d'après le hub.

    Chaque section est lue à part : une source muette ne coûte que sa
    ligne, dite « indisponible ». `news` remplace la lecture de la base
    de news, pour les tests.
    """
    now = time.time() if now is None else now
    start = now - HOURS * 3600
    journal = getattr(hub, "journal", None)
    brief = Brief(at=now)

    def add(label: str, read: Callable) -> None:
        try:
            item = read()
        except Exception as exc:  # noqa: BLE001 — une ligne, pas la page
            log.debug("synthèse : %s indisponible (%s)", label, exc)
            item = Item(label, "indisponible", "muted")
        if isinstance(item, list):
            brief.items.extend(item)
        elif item is not None:
            brief.items.append(item)

    snapshots = (journal.snapshots_rebased(start - 3600, now)
                 if journal is not None else [])

    add("Cours", lambda: price_item(hub.ticker()))
    add("Perpétuel", lambda: perp_item(
        _funding(hub, snapshots, now), change(snapshots, "oi_usd", now)))
    if journal is not None:
        add("Liquidations",
            lambda: liquidations_item(journal.liquidations_between(start, now)))
        add("Gros ordres",
            lambda: trades_item(journal.trades_between(start, now)))
        add("Liquidité", lambda: liquidity_item(
            journal.depth_between(start, now),
            getattr(hub, "DEPTH_BAND", 0.1)))
    add("ETF", lambda: etf_item(hub.etf_flows()))
    add("Sentiment", lambda: sentiment_item(hub.fear_greed_history()))
    add("Volatilité", lambda: volatility_item(change(snapshots, "dvol", now),
                                              change(snapshots, "rr25", now)))
    add("Prime Coinbase", lambda: premium_item(snapshots, now))
    add("Dominance", lambda: dominance_item(
        change(snapshots, "btc_dominance", now)))
    add("Cycle", lambda: cycle_item(_cycle_state(hub, now)))
    add("Agenda", lambda: agenda_item(now, macrocal.upcoming(
        dt.datetime.fromtimestamp(now, macrocal.NEW_YORK).date())))
    if journal is not None:
        add("Alertes", lambda: alerts_item(journal.alerts_between(start, now)))
    read_news = news or newsdb.top_since
    add("News", lambda: news_items(read_news(
        dt.datetime.fromtimestamp(start, dt.timezone.utc), NEWS_ROWS)))

    if journal is None:
        brief.coverage = ("journal désactivé : ni liquidations, ni gros "
                          "ordres, ni écarts sur la journée")
    else:
        brief.coverage = coverage(
            [row["ts"] for row in snapshots if row["ts"] >= start],
            start, now, getattr(hub, "SNAPSHOT_GAP", GAP))
    return brief


# ── Envoi du matin ───────────────────────────────────────────


def parse_time(value: Optional[str]) -> Optional[dt.time]:
    """« 07:00 », « 7:30 » ou « 7 » ; `None` si vide ou illisible."""
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        if ":" in text:
            hour, minute = text.split(":", 1)
            return dt.time(int(hour), int(minute))
        return dt.time(int(text))
    except ValueError:
        log.warning("BTCTERM_BRIEF illisible (%r) : pas de synthèse du "
                    "matin — attendu HH:MM", value)
        return None


class SentLog:
    """Le dernier jour où la synthèse est partie.

    Sur disque quand le journal est tenu : un service relancé après
    l'heure ne la renvoie pas. En mémoire sinon.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self._day: Optional[str] = None
        if path is not None:
            try:
                self._day = json.loads(path.read_text()).get("sent")
            except (OSError, ValueError, AttributeError):
                self._day = None

    def sent_on(self, day: dt.date) -> bool:
        return self._day == day.isoformat()

    def mark(self, day: dt.date) -> None:
        self._day = day.isoformat()
        if self.path is None:
            return
        try:
            self.path.write_text(json.dumps({"sent": self._day}))
        except OSError as exc:
            log.warning("synthèse : date d'envoi non écrite (%s)", exc)


def main(argv=None) -> int:
    """`python -m btcterm.brief` : la synthèse, à la ligne de commande."""
    import argparse

    from .hub import MarketHub

    parser = argparse.ArgumentParser(
        description="Les dernières 24 h du marché, en une page")
    parser.parse_args(argv)
    from . import resolver

    # Le résolveur du terminal : sans lui, un FAI qui ment sur les noms des
    # plateformes rend Binance injoignable d'ici alors que le service, qui
    # l'installe au démarrage, la lit.
    resolver.install()
    # Un hub jamais démarré : il lit le journal et interroge les sources
    # par son cache, sans ouvrir un seul flux temps réel.
    hub = MarketHub(collect_news=False)
    print(build(hub).text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
