"""
Journal des données éphémères — liquidations et arbitrage.

Carnets, écarts d'arbitrage et liquidations vivent en mémoire et meurent
avec le processus : le fil des liquidations se décrit lui-même comme
« un indicateur de tension du moment, pas un journal ». Ce module est le
journal qui manquait — il permet de relire une séance a posteriori, et
donnera aux futures alertes une base de comparaison.

Deux natures d'enregistrement, à l'image des données :

- une **liquidation** est un événement : une ligne par événement, écrite
  au fil de l'eau par le rappel `on_event` du fil (§ LiquidationFeed) ;
- une **opportunité d'arbitrage** est un état qui dure : la journaliser
  à chaque balayage rempilerait la même paire dix fois par seconde. Le
  journal tient donc des **épisodes** — ouvert quand une paire devient
  rentable, clos quand elle a cessé de l'être depuis `GRACE` secondes —
  et n'écrit qu'une ligne par épisode : bornes, meilleur profit, prix à
  ce meilleur, nombre d'observations.

Les **alertes** (§ alerts) s'y écrivent aussi, une ligne par sonnerie :
relire une séance, c'est aussi relire ce qui a sonné.

S'y ajoutent les **instantanés de marché** : dominance, capitalisation et
open interest, que leurs sources refusent de servir en série — CoinGecko
réserve l'historique à son offre payante, Binance ne garde que trente
jours d'open interest. La boucle d'observation du hub en écrit un toutes
les cinq minutes, et c'est l'accumulation locale qui construit, séance
après séance, l'historique que les API ne donnent pas. Leur rétention
est donc bien plus longue que celle des données de séance
(`SNAPSHOT_RETENTION_DAYS`) : effacer ces lignes au bout d'un mois
détruirait précisément ce que leur journalisation devait bâtir.

La base n'existe qu'à la première écriture : construire un `Journal`
(comme le fait tout `MarketHub`, démarré ou non) ne crée aucun fichier —
les tests et les usages sans réseau ne laissent aucune trace.

Une connexion SQLite n'étant pas partageable entre threads sans
précaution, l'unique connexion est protégée par un verrou : elle reçoit
des écritures du thread des connecteurs (liquidations) et de la boucle
d'observation du hub (arbitrage).

Relire une séance :

    python -m btcterm.journal              # les dernières 24 h
    python -m btcterm.journal --heures 6
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

__all__ = ["DB_DIR", "DB_PATH", "GRACE", "RETENTION_DAYS",
           "SNAPSHOT_RETENTION_DAYS", "Journal"]

DB_DIR = Path.home() / ".btcterm"
DB_PATH = DB_DIR / "journal.db"

#: Tolérance de flottement d'un épisode d'arbitrage : une paire qui
#: cesse d'être rentable moins de `GRACE` secondes reste dans son
#: épisode — les écarts clignotent au rythme des carnets, et un épisode
#: par clignotement ne raconterait rien.
GRACE = 30.0

#: Au-delà, les lignes sont purgées au démarrage du hub : le journal
#: sert à relire des séances, pas à archiver des années.
RETENTION_DAYS = 30

#: Rétention des instantanés de marché, elle, longue à dessein : ces
#: lignes remplacent un historique que les API refusent de servir, et
#: une par cinq minutes ne pèse presque rien (≈ 100 000 lignes par an).
#: Quatre cents jours couvrent une année de tendance avec de la marge.
SNAPSHOT_RETENTION_DAYS = 400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS liquidations (
    ts       REAL NOT NULL,      -- epoch de l'événement
    symbol   TEXT NOT NULL,
    side     TEXT NOT NULL,      -- la position qui a sauté : long | short
    price    REAL NOT NULL,
    quantity REAL NOT NULL,
    notional REAL NOT NULL,      -- price × quantity, précalculé pour SUM()
    exchange TEXT                -- la plateforme : Binance | Bybit
);
CREATE INDEX IF NOT EXISTS idx_liquidations_ts ON liquidations (ts);

CREATE TABLE IF NOT EXISTS arbitrage_episodes (
    first_seen    REAL NOT NULL, -- première observation rentable
    last_seen     REAL NOT NULL, -- dernière observation rentable
    buy_exchange  TEXT NOT NULL,
    sell_exchange TEXT NOT NULL,
    best_net_pct  REAL NOT NULL, -- meilleur profit net observé
    best_gross_pct REAL NOT NULL,
    buy_price     REAL NOT NULL, -- les prix au moment du meilleur net
    sell_price    REAL NOT NULL,
    samples       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_ts ON arbitrage_episodes (first_seen);

CREATE TABLE IF NOT EXISTS alerts (
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,       -- price | liq | funding | news | arb
                                 --   | trend | rsi | signal | dominance
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts (ts);

CREATE TABLE IF NOT EXISTS market_snapshots (
    ts               REAL NOT NULL,  -- epoch de l'observation
    btc_dominance    REAL,           -- part du BTC dans la cap. totale, %
    stable_share     REAL,           -- part des stablecoins, %
    total_cap_usd    REAL,
    total_volume_usd REAL,
    oi_usd           REAL,           -- open interest du perpétuel, $
    funding_rate     REAL            -- taux de la période en cours (fraction)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON market_snapshots (ts);
"""

#: Colonnes de `market_snapshots` hors horodatage — l'ordre du schéma.
#: Toute colonne ajoutée ici doit aussi l'être dans `_MIGRATIONS` :
#: CREATE TABLE IF NOT EXISTS n'élargit pas une table existante.
SNAPSHOT_FIELDS = ("btc_dominance", "stable_share",
                   "total_cap_usd", "total_volume_usd", "oi_usd",
                   "funding_rate")

#: Colonnes apparues après la création de `market_snapshots` : ajoutées
#: par ALTER TABLE à l'ouverture d'une base antérieure, NULL sur les
#: lignes déjà écrites — l'historique accumulé n'est jamais perdu.
_MIGRATIONS = {
    "market_snapshots": ("funding_rate REAL",),
    "liquidations": ("exchange TEXT",),
}


class Journal:
    """Écritures au fil de l'eau, lectures par fenêtre de temps."""

    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._db: Optional[sqlite3.Connection] = None
        #: Épisodes d'arbitrage en cours, par paire ordonnée
        #: (achat, vente) — l'état que `observe` fait vivre.
        self._open: dict[tuple[str, str], dict] = {}

    # ── Connexion ───────────────────────────────────────────

    def _connection(self) -> sqlite3.Connection:
        """La connexion unique, créée — fichier compris — au besoin."""
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(_SCHEMA)
            # Élargir les tables d'une base antérieure : CREATE TABLE IF
            # NOT EXISTS n'ajoute pas de colonne, ALTER TABLE si — les
            # lignes déjà écrites restent, la colonne neuve à NULL.
            for table, additions in _MIGRATIONS.items():
                present = {row["name"] for row in self._db.execute(
                    f"PRAGMA table_info({table})")}
                for declaration in additions:
                    if declaration.split()[0] not in present:
                        self._db.execute(
                            f"ALTER TABLE {table} ADD COLUMN {declaration}")
            self._db.commit()
        return self._db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    # ── Écritures ───────────────────────────────────────────

    def record_liquidation(self, event) -> None:
        """Une ligne par événement — branché sur `on_event` du fil."""
        with self._lock:
            self._connection().execute(
                "INSERT INTO liquidations (ts, symbol, side, price, "
                "quantity, notional, exchange) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event.time, event.symbol, event.side,
                 event.price, event.quantity, event.notional,
                 getattr(event, "exchange", None)),
            )
            self._db.commit()

    def record_alert(self, alert) -> None:
        """Une ligne par sonnerie — branché sur le moteur d'alertes."""
        with self._lock:
            self._connection().execute(
                "INSERT INTO alerts VALUES (?, ?, ?)",
                (alert.time, alert.kind, alert.message),
            )
            self._db.commit()

    def record_market_snapshot(self, ts: float, **fields) -> None:
        """Un instantané de marché — dominance, capitalisation, OI.

        Les champs absents ou à `None` restent NULL : les deux sources
        (CoinGecko, Binance Futures) échouent indépendamment, et un
        instantané partiel vaut mieux que pas d'instantané.
        """
        values = [fields.get(name) for name in SNAPSHOT_FIELDS]
        marks = ", ".join("?" * (1 + len(SNAPSHOT_FIELDS)))
        with self._lock:
            self._connection().execute(
                f"INSERT INTO market_snapshots VALUES ({marks})",
                (ts, *values),
            )
            self._db.commit()

    def observe(self, opportunities, now: Optional[float] = None) -> None:
        """Un balayage du moteur : fait vivre les épisodes.

        Appelée à cadence lente (la boucle du hub, 1 s) avec le résultat
        de `ArbitrageEngine.scan()` : seules les opportunités rentables
        comptent, les autres bornent les épisodes en cours.
        """
        now = time.time() if now is None else now
        seen: set[tuple[str, str]] = set()

        for opportunity in opportunities:
            if not opportunity.is_profitable:
                continue
            pair = (opportunity.buy_exchange, opportunity.sell_exchange)
            seen.add(pair)
            episode = self._open.get(pair)
            if episode is None:
                episode = self._open[pair] = {
                    "first_seen": now, "samples": 0,
                    "best_net_pct": float("-inf"),
                }
            episode["last_seen"] = now
            episode["samples"] += 1
            if opportunity.net_profit_pct > episode["best_net_pct"]:
                episode.update(
                    best_net_pct=opportunity.net_profit_pct,
                    best_gross_pct=opportunity.gross_profit_pct,
                    buy_price=opportunity.buy_price,
                    sell_price=opportunity.sell_price,
                )

        for pair in list(self._open):
            if pair in seen:
                continue
            if now - self._open[pair]["last_seen"] >= GRACE:
                self._write_episode(pair, self._open.pop(pair))

    def flush(self) -> None:
        """Clôt et écrit les épisodes en cours — à l'arrêt du hub."""
        for pair in list(self._open):
            self._write_episode(pair, self._open.pop(pair))

    def _write_episode(self, pair: tuple[str, str], episode: dict) -> None:
        with self._lock:
            self._connection().execute(
                "INSERT INTO arbitrage_episodes VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (episode["first_seen"], episode["last_seen"], *pair,
                 episode["best_net_pct"], episode["best_gross_pct"],
                 episode["buy_price"], episode["sell_price"],
                 episode["samples"]),
            )
            self._db.commit()

    def purge(self, days: float = RETENTION_DAYS) -> None:
        """Oublie ce qui dépasse la rétention. Sans base, ne crée rien.

        Les instantanés de marché ont leur propre horizon, bien plus
        lointain : ils sont l'historique que les API refusent, pas une
        donnée de séance.
        """
        if not self.path.exists():
            return
        horizon = time.time() - days * 86_400
        with self._lock:
            db = self._connection()
            db.execute("DELETE FROM liquidations WHERE ts < ?", (horizon,))
            db.execute("DELETE FROM arbitrage_episodes WHERE last_seen < ?",
                       (horizon,))
            db.execute("DELETE FROM alerts WHERE ts < ?", (horizon,))
            db.execute("DELETE FROM market_snapshots WHERE ts < ?",
                       (time.time() - SNAPSHOT_RETENTION_DAYS * 86_400,))
            db.commit()

    # ── Lectures ────────────────────────────────────────────

    def liquidations_between(self, start: float, end: float) -> list[sqlite3.Row]:
        if not self.path.exists():
            return []
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM liquidations WHERE ts BETWEEN ? AND ?"
                " ORDER BY ts", (start, end)).fetchall()

    def episodes_between(self, start: float, end: float) -> list[sqlite3.Row]:
        """Les épisodes dont la fenêtre chevauche [start, end]."""
        if not self.path.exists():
            return []
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM arbitrage_episodes"
                " WHERE last_seen >= ? AND first_seen <= ?"
                " ORDER BY first_seen", (start, end)).fetchall()

    def alerts_between(self, start: float, end: float) -> list[sqlite3.Row]:
        if not self.path.exists():
            return []
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM alerts WHERE ts BETWEEN ? AND ?"
                " ORDER BY ts", (start, end)).fetchall()

    def snapshots_between(self, start: float, end: float) -> list[sqlite3.Row]:
        if not self.path.exists():
            return []
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM market_snapshots WHERE ts BETWEEN ? AND ?"
                " ORDER BY ts", (start, end)).fetchall()


def _relire(hours: float) -> None:
    """Le résumé d'une séance, à même la ligne de commande."""
    journal = Journal()
    end = time.time()
    start = end - hours * 3600

    print(f"\nJournal des dernières {hours:g} h — {journal.path}")
    print("─" * 60)

    events = journal.liquidations_between(start, end)
    if not events:
        print("Liquidations : aucune enregistrée.")
    else:
        par_cote: dict[str, float] = {"long": 0.0, "short": 0.0}
        for row in events:
            par_cote[row["side"]] += row["notional"]
        print(f"Liquidations : {len(events)} événements — "
              f"longs {par_cote['long']:,.0f} $ · "
              f"shorts {par_cote['short']:,.0f} $")
        gros = max(events, key=lambda r: r["notional"])
        quand = time.strftime("%H:%M", time.localtime(gros["ts"]))
        print(f"  la plus grosse : {gros['symbol']} {gros['side']} "
              f"{gros['notional']:,.0f} $ à {quand}"
              f"{' (' + gros['exchange'] + ')' if gros['exchange'] else ''}")

    alerts = journal.alerts_between(start, end)
    if not alerts:
        print("Alertes : aucune.")
    else:
        print(f"Alertes : {len(alerts)}")
        for row in alerts[-12:]:
            quand = time.strftime("%H:%M", time.localtime(row["ts"]))
            print(f"  {quand}  [{row['kind']:>7s}]  {row['message']}")

    episodes = journal.episodes_between(start, end)
    if not episodes:
        print("Arbitrage : aucun épisode rentable.")
    else:
        print(f"Arbitrage : {len(episodes)} épisode(s) rentable(s)")
        for row in episodes:
            debut = time.strftime("%H:%M:%S", time.localtime(row["first_seen"]))
            duree = row["last_seen"] - row["first_seen"]
            print(f"  {debut}  {row['buy_exchange']:>8s} → "
                  f"{row['sell_exchange']:<8s} "
                  f"net {row['best_net_pct']:+.3f} %  "
                  f"pendant {duree:5.1f} s  ({row['samples']} obs.)")

    # Les instantanés dépassent la séance : c'est leur accumulation qui
    # fait l'historique, la relecture dit donc jusqu'où il remonte.
    snapshots = journal.snapshots_between(0, end)
    if not snapshots:
        print("Instantanés de marché : aucun.")
    else:
        depuis = time.strftime("%d/%m/%Y", time.localtime(snapshots[0]["ts"]))
        print(f"Instantanés de marché : {len(snapshots)}, depuis le {depuis}")
        dominance = [r["btc_dominance"] for r in snapshots
                     if r["btc_dominance"] is not None]
        if dominance:
            print(f"  dominance BTC : {dominance[-1]:.1f} % "
                  f"(de {min(dominance):.1f} à {max(dominance):.1f} %)")
    print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Relit le journal des liquidations et de l'arbitrage.")
    parser.add_argument("--heures", type=float, default=24,
                        help="fenêtre à relire (défaut : 24)")
    _relire(parser.parse_args().heures)
