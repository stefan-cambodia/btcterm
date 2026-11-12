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
    python -m btcterm.journal --heures 96  # au-delà d'un jour, les heures sont datées
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from .liquidations import BITCOIN_SQL, MAP_BINS

__all__ = ["DB_DIR", "DB_PATH", "GRACE", "RETENTION_DAYS",
           "SNAPSHOT_RETENTION_DAYS", "Journal", "rebase_universe",
           "liquidity_totals"]

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

#: Rétention de la carte de chaleur du carnet (§ heatmap) : une colonne
#: toutes les dix secondes, 8 640 par jour — trois jours suffisent à la
#: lecture des murs, et tiennent en une dizaine de mégaoctets.
HEATMAP_RETENTION_DAYS = 3

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
-- idx_liquidations_btc, partiel, est posé après les migrations (§ Journal).

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

CREATE TABLE IF NOT EXISTS large_trades (
    ts       REAL NOT NULL,      -- epoch de l'exécution
    exchange TEXT NOT NULL,      -- Binance | Coinbase | Bybit
    side     TEXT NOT NULL,      -- côté du preneur : buy | sell
    price    REAL NOT NULL,
    quantity REAL NOT NULL,      -- en BTC
    notional REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_large_trades_ts ON large_trades (ts);

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
    funding_rate     REAL,           -- taux de la période en cours (fraction)
    dvol             REAL,           -- volatilité implicite 30 j (Deribit)
    atm30_iv         REAL,           -- vol. à la monnaie de l'échéance à 1 mois
    rr25             REAL,           -- skew 25 delta à 1 mois, points
    pc_oi            REAL,           -- put/call en open interest
    basis90          REAL,           -- base annualisée du future à 3 mois, %/an
    cb_premium       REAL,           -- prime Coinbase sur Binance, %
    cap_excluded     REAL,           -- part retirée de la cap. totale, %
                                     --   (NULL : écrit avant ce retrait)
    kr_premium       REAL,           -- prime coréenne (Upbit), %
    kr_won           REAL,           -- sa part due au won : USDT d'Upbit, %
    crowd_long       REAL,           -- comptes à la hausse, tous comptes, %
    top_long         REAL            -- positions à la hausse, gros comptes, %
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON market_snapshots (ts);

CREATE TABLE IF NOT EXISTS book_depth (
    ts       REAL NOT NULL,      -- epoch de la fin de la période moyennée
    exchange TEXT NOT NULL,
    bid_btc  REAL NOT NULL,      -- volume moyen à l'achat, à portée du prix
    ask_btc  REAL NOT NULL       -- volume moyen à la vente
);
CREATE INDEX IF NOT EXISTS idx_book_depth_ts ON book_depth (ts);

CREATE TABLE IF NOT EXISTS book_heatmap (
    ts    REAL NOT NULL,      -- epoch de la photographie des carnets
    base  INTEGER NOT NULL,   -- indice de la première tranche (prix / pas)
    step  REAL NOT NULL,      -- pas des tranches, en dollars
    data  BLOB NOT NULL       -- quantités par tranche, float32 compressés
);
CREATE INDEX IF NOT EXISTS idx_book_heatmap_ts ON book_heatmap (ts);
"""

#: Colonnes de `market_snapshots` hors horodatage — l'ordre du schéma.
#: Toute colonne ajoutée ici doit aussi l'être dans `_MIGRATIONS` :
#: CREATE TABLE IF NOT EXISTS n'élargit pas une table existante.
SNAPSHOT_FIELDS = ("btc_dominance", "stable_share",
                   "total_cap_usd", "total_volume_usd", "oi_usd",
                   "funding_rate", "dvol", "atm30_iv", "rr25", "pc_oi",
                   "basis90", "cb_premium", "cap_excluded",
                   "kr_premium", "kr_won", "crowd_long", "top_long")

#: Colonnes apparues après la création de `market_snapshots` : ajoutées
#: par ALTER TABLE à l'ouverture d'une base antérieure, NULL sur les
#: lignes déjà écrites — l'historique accumulé n'est jamais perdu.
_MIGRATIONS = {
    "market_snapshots": ("funding_rate REAL", "dvol REAL", "atm30_iv REAL",
                         "rr25 REAL", "pc_oi REAL", "basis90 REAL",
                         "cb_premium REAL", "cap_excluded REAL",
                         "kr_premium REAL", "kr_won REAL",
                         "crowd_long REAL", "top_long REAL"),
    "liquidations": ("exchange TEXT",),
}


#: Écart, d'un instantané au suivant, entre la variation des autres
#: capitalisations et celle du bitcoin au-delà duquel c'est l'univers
#: compté qui a changé, pas le marché. Sur les 9 500 paires d'instantanés
#: voisins du journal au 11 novembre 2026, le marché n'a jamais dépassé
#: 1,0 % ; les changements d'univers vont de 1,6 à 2,9 %.
UNIVERSE_STEP = 0.015

#: Taille plausible de ce qui entre ou sort, en part de la capitalisation
#: totale : un pas hors de ces bornes n'est pas corrigé.
UNIVERSE_SIZE = (0.004, 0.02)

#: Deux instantanés sont voisins en deçà : au-delà, la machine a dormi,
#: le marché a pu diverger pour de bon, et un pas n'est retenu que s'il
#: défait le précédent.
UNIVERSE_ADJACENT = 900.0


def rebase_universe(rows) -> list[dict]:
    """Ramène les anciens instantanés à l'univers que le journal tient.

    Jusqu'au 11 novembre 2026, la capitalisation totale s'écrivait telle
    que CoinGecko la donnait, un actif d'une trentaine de milliards y
    entrant et en sortant environ une fois par jour (§ sources.
    INTERMITTENT) ; depuis, il en est retiré à la collecte, et la colonne
    `cap_excluded` le dit. Les lignes d'avant — `cap_excluded` à NULL —
    sont corrigées ici, à la lecture : rien n'est réécrit dans la base.

    Un changement d'univers se reconnaît à ce que la capitalisation
    totale saute sans celle du bitcoin (part × total, que l'univers ne
    touche pas). Son sens dit dans quel état on arrive ; les lignes
    écrites « avec » l'actif sont ramenées à l'univers « sans » — total
    réduit, parts relevées d'autant —, la taille de l'actif étant celle
    que mesurent les pas qui bordent la période. Sans aucun pas dans la
    fenêtre, rien n'est corrigé.

    Retourne des dictionnaires, lisibles comme les lignes du journal.
    """
    rows = [dict(row) for row in rows]
    usable = [row for row in rows
              if row.get("btc_dominance") and row.get("total_cap_usd")]
    steps: list[tuple[int, int, float]] = []   # (rang, sens, taille)
    last = 0
    for i in range(1, len(usable)):
        before, after = usable[i - 1], usable[i]
        if before.get("cap_excluded") is not None:
            continue                    # déjà dans l'univers tenu
        btc = (after["btc_dominance"] * after["total_cap_usd"]) / (
            before["btc_dominance"] * before["total_cap_usd"])
        total = after["total_cap_usd"] / before["total_cap_usd"]
        share = before["btc_dominance"] / 100
        others = (total - share * btc) / (1 - share)
        if abs(others - btc) <= UNIVERSE_STEP:
            continue
        sens = 1 if others > btc else -1    # +1 : l'actif entre
        if sens == last and after["ts"] - before["ts"] > UNIVERSE_ADJACENT:
            continue
        if sens > 0 and after.get("cap_excluded") is not None:
            continue                    # rien n'entre dans l'univers tenu
        size = total / btc - 1
        size = size if sens > 0 else 1 / (1 + size) - 1
        if not UNIVERSE_SIZE[0] <= size <= UNIVERSE_SIZE[1]:
            continue
        steps.append((i, sens, size))
        last = sens
    if not steps:
        return rows

    bounds = [0, *(i for i, _, _ in steps), len(usable)]
    for k in range(len(bounds) - 1):
        # La période qui précède un pas sortant, ou suit un pas entrant,
        # a été écrite avec l'actif.
        sizes = []
        if k > 0 and steps[k - 1][1] > 0:
            sizes.append(steps[k - 1][2])
        if k < len(steps) and steps[k][1] < 0:
            sizes.append(steps[k][2])
        if not sizes:
            continue
        factor = 1 + sum(sizes) / len(sizes)
        for row in usable[bounds[k]:bounds[k + 1]]:
            if row.get("cap_excluded") is not None:
                continue
            row["btc_dominance"] *= factor
            row["total_cap_usd"] /= factor
            if row.get("stable_share") is not None:
                row["stable_share"] *= factor
    return rows


def liquidity_totals(rows, presence: float = 0.9
                     ) -> tuple[list[tuple], list[str], list[str]]:
    """La liquidité des plateformes réunies, sur un ensemble qui ne change pas.

    `rows` sont des triplets `(instant, plateforme, achats, ventes)` — les
    lignes de `book_depth`, quelle que soit la forme de l'instant.
    Additionner ce qui est là ferait un trou dans la courbe chaque fois
    qu'une plateforme se tait cinq minutes : le marché n'aurait rien
    perdu, le terminal seulement. La somme ne porte donc que sur les
    plateformes présentes dans au moins `presence` des relevés, et un
    relevé où l'une d'elles manque est écarté.

    Retourne les relevés `(instant, achats, ventes)` dans l'ordre, les
    plateformes comptées, et celles qui ne le sont pas — à dire à l'écran.
    """
    by_time: dict = {}
    for moment, exchange, bid, ask in rows:
        by_time.setdefault(moment, {})[exchange] = (bid, ask)
    if not by_time:
        return [], [], []
    seen: dict[str, int] = {}
    for sample in by_time.values():
        for exchange in sample:
            seen[exchange] = seen.get(exchange, 0) + 1
    kept = sorted(name for name, count in seen.items()
                  if count >= presence * len(by_time))
    dropped = sorted(set(seen) - set(kept))
    totals = []
    for moment in sorted(by_time):
        sample = by_time[moment]
        if kept and all(name in sample for name in kept):
            totals.append((moment,
                           sum(sample[name][0] for name in kept),
                           sum(sample[name][1] for name in kept)))
    return totals, kept, dropped


def find_gaps(timestamps: list[float], gap: float) -> list[tuple[float, float]]:
    """Les intervalles entre deux horodatages consécutifs distants de plus de `gap`."""
    return [(a, b) for a, b in zip(timestamps, timestamps[1:]) if b - a > gap]


#: L'index des seuls contrats bitcoin, couvrant : la carte des
#: liquidations (`liquidation_map`) le lit sans toucher la table. Depuis
#: que Binance livre toutes ses paires, le journal reçoit de l'ordre de
#: 55 000 liquidations par jour, dont un dixième sur le bitcoin — trente
#: jours en font 1,7 million. Partiel, l'index ne pèse que ce dixième.
#: Il porte `symbol`, que la requête doit répéter pour que SQLite le
#: choisisse : sans lui, chaque ligne retournait à la table, dont les
#: pages mêlent bitcoin et altcoins — à froid, presque toute la table. Il
#: porte aussi `exchange`, ajoutée par migration : il est donc posé après
#: elle, et reposé si la règle `BITCOIN_SQL` change.
_BTC_INDEX = "idx_liquidations_btc"
_BTC_INDEX_SQL = (f"CREATE INDEX {_BTC_INDEX} ON liquidations "
                  f"(ts, symbol, price, notional, side, exchange) "
                  f"WHERE {BITCOIN_SQL}")


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
            pose = self._db.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' "
                "AND name = ?", (_BTC_INDEX,)).fetchone()
            if pose is None or pose[0] != _BTC_INDEX_SQL:
                if pose is not None:
                    self._db.execute(f"DROP INDEX {_BTC_INDEX}")
                self._db.execute(_BTC_INDEX_SQL)
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

    def record_trade(self, trade) -> None:
        """Un gros ordre du ruban — branché sur `on_large` (§ tape)."""
        with self._lock:
            self._connection().execute(
                "INSERT INTO large_trades (ts, exchange, side, price, "
                "quantity, notional) VALUES (?, ?, ?, ?, ?, ?)",
                (trade.time, trade.exchange, trade.side, trade.price,
                 trade.quantity, trade.notional))
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
        # Les colonnes nommées : une base élargie par migration les a dans
        # l'ordre de ses ALTER TABLE, pas forcément dans celui du code.
        names = ", ".join(("ts", *SNAPSHOT_FIELDS))
        with self._lock:
            self._connection().execute(
                f"INSERT INTO market_snapshots ({names}) VALUES ({marks})",
                (ts, *values),
            )
            self._db.commit()

    def record_book_depth(self, ts: float, rows) -> None:
        """La liquidité des carnets sur la période qui s'achève : une ligne
        par plateforme, `(plateforme, achats, ventes)` en bitcoins (§ hub.
        _flush_depth). Rien ne s'écrit sans ligne."""
        rows = [(ts, name, bid, ask) for name, bid, ask in rows]
        if not rows:
            return
        with self._lock:
            self._connection().executemany(
                "INSERT INTO book_depth (ts, exchange, bid_btc, ask_btc) "
                "VALUES (?, ?, ?, ?)", rows)
            self._db.commit()

    def record_heatmap(self, ts: float, base: int, step: float,
                       data: bytes) -> None:
        """Une colonne de la carte de chaleur du carnet (§ heatmap.column,
        encodée par `heatmap.encode`)."""
        with self._lock:
            self._connection().execute(
                "INSERT INTO book_heatmap (ts, base, step, data) "
                "VALUES (?, ?, ?, ?)", (ts, int(base), float(step), data))
            self._db.commit()

    def heatmap_between(self, start: float, end: float) -> list[tuple]:
        """Les colonnes d'une plage, décodées : `(ts, base, pas, quantités)`."""
        if not self.path.exists():
            return []
        from .heatmap import decode
        with self._lock:
            rows = self._connection().execute(
                "SELECT ts, base, step, data FROM book_heatmap "
                "WHERE ts BETWEEN ? AND ? ORDER BY ts", (start, end)).fetchall()
        return [(ts, base, step, decode(data)) for ts, base, step, data in rows]

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
            db.execute("DELETE FROM large_trades WHERE ts < ?", (horizon,))
            db.execute("DELETE FROM arbitrage_episodes WHERE last_seen < ?",
                       (horizon,))
            db.execute("DELETE FROM alerts WHERE ts < ?", (horizon,))
            db.execute("DELETE FROM market_snapshots WHERE ts < ?",
                       (time.time() - SNAPSHOT_RETENTION_DAYS * 86_400,))
            # La liquidité des carnets est de la même nature : un
            # historique que personne ne sert, bâti par accumulation.
            db.execute("DELETE FROM book_depth WHERE ts < ?",
                       (time.time() - SNAPSHOT_RETENTION_DAYS * 86_400,))
            db.execute("DELETE FROM book_heatmap WHERE ts < ?",
                       (time.time() - HEATMAP_RETENTION_DAYS * 86_400,))
            db.commit()

    # ── Lectures ────────────────────────────────────────────

    def liquidations_between(self, start: float, end: float) -> list[sqlite3.Row]:
        if not self.path.exists():
            return []
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM liquidations WHERE ts BETWEEN ? AND ?"
                " ORDER BY ts", (start, end)).fetchall()

    def liquidation_summary(self, start: float,
                            end: float) -> Optional[dict]:
        """Le bilan des liquidations d'une fenêtre, calculé par la base.

        `count`, `long`, `short`, `btc` (notionnels en dollars, `btc` selon
        `is_bitcoin`) et `biggest`, la plus grosse (`symbol`, `side`,
        `notional`, `ts`, `exchange`) ; `None` sans événement. Le panneau
        JOURNAL, la synthèse et le bilan de séance lisaient chaque ligne de
        la journée pour n'en garder que ces chiffres : 56 000 lignes à
        Binance livrant, deux secondes sous le verrou du journal — que
        l'écriture des liquidations attend, depuis la boucle des flux.
        """
        if not self.path.exists():
            return None
        with self._lock:
            db = self._connection()
            count, longs, shorts, btc = db.execute(
                "SELECT COUNT(*), "
                "TOTAL(CASE WHEN side = 'long' THEN notional END), "
                "TOTAL(CASE WHEN side = 'short' THEN notional END), "
                f"TOTAL(CASE WHEN {BITCOIN_SQL} THEN notional END) "
                "FROM liquidations WHERE ts BETWEEN ? AND ?",
                (start, end)).fetchone()
            if not count:
                return None
            biggest = db.execute(
                "SELECT * FROM liquidations WHERE ts BETWEEN ? AND ? "
                "ORDER BY notional DESC LIMIT 1", (start, end)).fetchone()
        return {"count": count, "long": longs, "short": shorts, "btc": btc,
                "biggest": dict(biggest)}

    def liquidation_map(self, start: float, end: float,
                        bins: int = MAP_BINS) -> Optional[dict]:
        """La carte des liquidations du bitcoin, calculée par la base.

        Même contrat que `liquidations.price_map`, la règle de référence :
        les tranches de prix, longs et shorts à part, la couverture. La
        carte lisait chaque liquidation de la plage, toutes paires, pour
        n'en garder que le bitcoin : sur la plage d'un graphique journalier
        — tout le journal —, 1,7 million de lignes, des secondes et des
        centaines de mégaoctets chaque minute. Elle ne lit plus que l'index
        partiel des contrats bitcoin (`_BTC_INDEX`), et n'en rapporte que
        des sommes.
        """
        if not self.path.exists():
            return None
        where = f"ts BETWEEN ? AND ? AND {BITCOIN_SQL}"
        with self._lock:
            db = self._connection()
            count, low, high, total, first, last, places = db.execute(
                "SELECT COUNT(*), MIN(price), MAX(price), TOTAL(notional), "
                "MIN(ts), MAX(ts), "
                "group_concat(DISTINCT COALESCE(exchange, 'Binance')) "
                f"FROM liquidations WHERE {where}", (start, end)).fetchone()
            if not count:
                return None
            if high <= low:
                high = low * 1.001 or 1.0
            width = (high - low) / bins
            longs = [0.0] * bins
            shorts = [0.0] * bins
            for index, side, notional in db.execute(
                    "SELECT MIN(?, CAST((price - ?) / ? AS INTEGER)) AS bin, "
                    "side, TOTAL(notional) "
                    f"FROM liquidations WHERE {where} GROUP BY bin, side",
                    (bins - 1, low, width, start, end)):
                (longs if side == "long" else shorts)[index] += notional
        return {
            "centers": [low + (i + 0.5) * width for i in range(bins)],
            "width": width,
            "longs": longs,
            "shorts": shorts,
            "count": count,
            "notional": total,
            "exchanges": sorted(places.split(",")),
            "first": first,
            "last": last,
        }

    def trades_between(self, start: float, end: float) -> list[sqlite3.Row]:
        if not self.path.exists():
            return []
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM large_trades WHERE ts BETWEEN ? AND ?"
                " ORDER BY ts", (start, end)).fetchall()

    def trade_buckets(self, start: float, end: float,
                      step: int) -> Optional[dict]:
        """Les gros ordres d'une plage, sommés par tranche de `step` secondes
        et par côté — pour les bulles du graphique du prix.

        Chaque tranche : `[début, côté, notionnel, prix moyen pondéré,
        nombre, plus gros]`, le côté étant celui du preneur (`buy` | `sell`).
        S'y ajoutent les totaux de la plage par côté et les plateformes
        présentes ; `None` sans un ordre. Les tranches commencent aux
        multiples de `step` depuis l'epoch, comme les bougies de Binance
        jusqu'au jour : le navigateur les range dans ses bougies, semaine
        et mois compris.
        """
        if not self.path.exists():
            return None
        with self._lock:
            db = self._connection()
            rows = db.execute(
                "SELECT CAST(ts / ? AS INTEGER) * ? AS bucket, side, "
                "TOTAL(notional), TOTAL(price * notional), COUNT(*), "
                "MAX(notional) FROM large_trades WHERE ts BETWEEN ? AND ? "
                "GROUP BY bucket, side ORDER BY bucket",
                (step, step, start, end)).fetchall()
            if not rows:
                return None
            places = db.execute(
                "SELECT group_concat(DISTINCT exchange) FROM large_trades "
                "WHERE ts BETWEEN ? AND ?", (start, end)).fetchone()[0]
        buckets = [[int(bucket), side, notional, weighted / notional,
                    count, biggest]
                   for bucket, side, notional, weighted, count, biggest in rows
                   if notional > 0]
        return {"buckets": buckets,
                "buy": sum(b[2] for b in buckets if b[1] == "buy"),
                "sell": sum(b[2] for b in buckets if b[1] == "sell"),
                "count": sum(b[4] for b in buckets),
                "exchanges": sorted((places or "").split(",")),
                "step": step}

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

    def depth_between(self, start: float, end: float) -> list[sqlite3.Row]:
        if not self.path.exists():
            return []
        with self._lock:
            return self._connection().execute(
                "SELECT * FROM book_depth WHERE ts BETWEEN ? AND ?"
                " ORDER BY ts", (start, end)).fetchall()

    def snapshots_rebased(self, start: float, end: float) -> list[dict]:
        """Les instantanés, dominance et capitalisation ramenées à un seul
        univers (§ rebase_universe). Un jour de plus est lu avant la
        fenêtre : c'est un pas qui dit l'état, et il peut la précéder."""
        rows = rebase_universe(self.snapshots_between(start - 86_400, end))
        return [row for row in rows if row["ts"] >= start]

    def interruptions_between(self, start: float, end: float,
                              gap: float) -> list[tuple[float, float]]:
        """Les trous de plus de `gap` secondes entre deux instantanés.

        Un instantané part toutes les cinq minutes tant que le service
        tourne : un trou plus large dit que la machine dormait, ou que
        le service était arrêté. Chaque entrée donne le dernier
        instantané avant le trou et le premier après.
        """
        return find_gaps([row["ts"] for row in self.snapshots_between(start, end)],
                         gap)


#: Trou entre deux instantanés au-delà duquel la relecture compte une
#: interruption — trois instantanés manqués, comme le panneau JOURNAL.
INTERRUPTION_GAP = 900.0


def _duree(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    return f"{minutes} min" if minutes < 60 else \
        f"{minutes // 60} h {minutes % 60:02d}"


def resume(journal: "Journal", hours: float = 24,
           now: Optional[float] = None) -> list[str]:
    """Le résumé d'une séance, ligne par ligne — ce que la commande écrit.

    Il suit ce que le journal a appris à tenir : les gros ordres du ruban
    et les interruptions de la séance s'y lisent comme au panneau JOURNAL,
    et une fenêtre de plus d'un jour date ses heures — « 06:04 » sur
    quatre jours ne désignait aucun matin.
    """
    end = time.time() if now is None else now
    start = end - hours * 3600
    forme = "%d/%m %H:%M" if hours > 24 else "%H:%M"

    def quand(ts: float) -> str:
        return time.strftime(forme, time.localtime(ts))

    lines = []

    # Ce que la fenêtre couvre vraiment : liquidations et gros ordres ne
    # comptent que le temps où le service tournait.
    stamps = [row["ts"] for row in journal.snapshots_between(start, end)]
    edges = [start, *stamps, end]
    trous = [b - a for a, b in zip(edges, edges[1:])
             if b - a > INTERRUPTION_GAP]
    if not stamps:
        lines.append("Couverture : aucun instantané sur la fenêtre — "
                     "le service ne tournait pas.")
    elif trous:
        pluriel = "s" if len(trous) > 1 else ""
        lines.append(f"Couverture : {_duree(end - start - sum(trous))} sur "
                     f"{hours:g} h — {len(trous)} interruption{pluriel} "
                     f"(veille ou arrêt), la plus longue de "
                     f"{_duree(max(trous))}")
    else:
        lines.append("Couverture : complète.")

    bilan = journal.liquidation_summary(start, end)
    if not bilan:
        lines.append("Liquidations : aucune enregistrée.")
    else:
        lines.append(f"Liquidations : {bilan['count']} événements — "
                     f"longs {bilan['long']:,.0f} $ · "
                     f"shorts {bilan['short']:,.0f} $")
        gros = bilan["biggest"]
        lines.append(f"  la plus grosse : {gros['symbol']} {gros['side']} "
                     f"{gros['notional']:,.0f} $ à {quand(gros['ts'])}"
                     f"{' (' + gros['exchange'] + ')' if gros['exchange'] else ''}")

    trades = journal.trades_between(start, end)
    if not trades:
        lines.append("Gros ordres : aucun journalisé.")
    else:
        achats = [r["notional"] for r in trades if r["side"] == "buy"]
        ventes = [r["notional"] for r in trades if r["side"] == "sell"]
        solde = sum(achats) - sum(ventes)
        lines.append(f"Gros ordres : {len(trades)} — "
                     f"achats {len(achats)} · {sum(achats):,.0f} $ · "
                     f"ventes {len(ventes)} · {sum(ventes):,.0f} $ · "
                     f"solde {'+' if solde >= 0 else '−'}{abs(solde):,.0f} $")
        gros = max(trades, key=lambda r: r["notional"])
        sens = "achat" if gros["side"] == "buy" else "vente"
        lines.append(f"  le plus gros : {sens} {gros['exchange']} "
                     f"{gros['notional']:,.0f} $ à {quand(gros['ts'])}")

    totals, kept, dropped = liquidity_totals(
        (row["ts"], row["exchange"], row["bid_btc"], row["ask_btc"])
        for row in journal.depth_between(start, end))
    if totals:
        volumes = [bid + ask for _, bid, ask in totals]
        mean = sum(volumes) / len(volumes)
        lines.append(
            f"Liquidité : {volumes[-1]:.0f} BTC à portée du prix au dernier "
            f"relevé ({quand(totals[-1][0])}) — moyenne {mean:.0f}, "
            f"de {min(volumes):.0f} à {max(volumes):.0f} · {', '.join(kept)}"
            + (f" ; sans {', '.join(dropped)}" if dropped else ""))

    alerts = journal.alerts_between(start, end)
    if not alerts:
        lines.append("Alertes : aucune.")
    else:
        montrees = alerts[-12:]
        reste = len(alerts) - len(montrees)
        lines.append(f"Alertes : {len(alerts)}"
                     + (f" — les {len(montrees)} dernières" if reste else ""))
        for row in montrees:
            lines.append(f"  {quand(row['ts'])}  [{row['kind']:>7s}]  "
                         f"{row['message']}")

    episodes = journal.episodes_between(start, end)
    if not episodes:
        lines.append("Arbitrage : aucun épisode rentable.")
    else:
        lines.append(f"Arbitrage : {len(episodes)} épisode(s) rentable(s)")
        for row in episodes:
            debut = time.strftime(forme + ":%S",
                                  time.localtime(row["first_seen"]))
            duree = row["last_seen"] - row["first_seen"]
            lines.append(f"  {debut}  {row['buy_exchange']:>8s} → "
                         f"{row['sell_exchange']:<8s} "
                         f"net {row['best_net_pct']:+z.3f} %  "
                         f"pendant {duree:5.1f} s  ({row['samples']} obs.)")

    # Les instantanés dépassent la séance : c'est leur accumulation qui
    # fait l'historique, la relecture dit donc jusqu'où il remonte — la
    # dominance, elle, se lit sur la fenêtre demandée.
    snapshots = journal.snapshots_between(0, end)
    if not snapshots:
        lines.append("Instantanés de marché : aucun.")
    else:
        depuis = time.strftime("%d/%m/%Y", time.localtime(snapshots[0]["ts"]))
        lines.append(f"Instantanés de marché : {len(snapshots)}, "
                     f"depuis le {depuis}")
        dominance = [r["btc_dominance"]
                     for r in journal.snapshots_rebased(start, end)
                     if r["btc_dominance"] is not None]
        if dominance:
            lines.append(f"  dominance BTC : {dominance[-1]:.1f} % "
                         f"(de {min(dominance):.1f} à {max(dominance):.1f} % "
                         f"sur la fenêtre)")
    return lines


def _relire(hours: float) -> None:
    """Le résumé d'une séance, à même la ligne de commande."""
    journal = Journal()
    print(f"\nJournal des dernières {hours:g} h — {journal.path}")
    print("─" * 60)
    print("\n".join(resume(journal, hours)))
    print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Relit le journal : liquidations, gros ordres, alertes, "
                    "arbitrage, et ce que la fenêtre couvre vraiment.")
    parser.add_argument("--heures", type=float, default=24,
                        help="fenêtre à relire (défaut : 24)")
    _relire(parser.parse_args().heures)
