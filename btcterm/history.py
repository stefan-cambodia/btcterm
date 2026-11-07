"""
Historique du cours sur disque — une bougie close ne se redemande pas.

Le réseau pesait 85 % du temps d'ouverture du panneau prix (mesure
d'octobre 2026 : 360 à 425 ms sur environ 480, indicateurs et sérialisation
compris), pour rapatrier à chaque fois des bougies qui ne changeront plus.
Pire, il suffisait que Binance se taise au démarrage pour que le graphique
retombe sur sa série de démonstration, faute du moindre souvenir.

Une bougie close est immuable : ce module la garde dans
`~/.btcterm/klines.db` et ne demande à Binance que ce qui manque.

- **Vers le présent** (`_sync`) : tout ce qui suit la dernière bougie
  sauvegardée, page après page si l'absence a été longue. C'est aussi cet
  appel qui ramène la bougie en cours.
- **Vers le passé** (`extend_back`) : une page entière de `PAGE` bougies
  dès qu'on dépasse ce qui est sauvegardé, quelle que soit la taille
  demandée par le graphique — elle en sert plusieurs. Le hub s'en sert aussi
  en tâche de fond, pour que la profondeur soit là avant d'être demandée.

Deux règles tiennent la série juste :

- **Seules les bougies réglées s'écrivent.** La bougie en cours bouge
  encore et une bougie tout juste close peut l'être encore un instant côté
  Binance, ou chez nous si l'horloge locale avance : `SETTLE_MS` les laisse
  en mémoire. Elles n'y perdent rien : tant qu'elles ne sont pas écrites,
  l'appel suivant les ramène.
- **La plage sauvegardée n'a pas de trou.** Elle ne s'étend que par ses
  deux bouts, en repartant de sa dernière ou de sa première bougie : ce qui
  manque entre deux bougies est donc un vrai silence de Binance — une
  maintenance — et pas une page jamais demandée. Aucune table de couverture
  n'est nécessaire, et un contrôle d'écart entre bougies signalerait à tort
  ces silences.

La base est un cache : la supprimer ne coûte que le temps de la remplir à
nouveau, et elle se remplit à l'usage, intervalle par intervalle. Comme le
journal, elle n'existe qu'à la première écriture.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from . import sources
from .journal import DB_DIR

__all__ = ["DB_PATH", "PAGE", "SETTLE_MS", "KlineHistory"]

DB_PATH = DB_DIR / "klines.db"

#: Plafond de Binance par appel à `/api/v3/klines`.
PAGE = 1000

#: Délai après la clôture avant de tenir une bougie pour définitive.
SETTLE_MS = 60_000

#: Rattrapage maximal vers le présent, en pages. Au-delà, la série
#: sauvegardée de l'intervalle est oubliée et repart du présent : une
#: minute laissée un mois demanderait quarante-trois pages à Binance avant
#: d'afficher quoi que ce soit, pour des bougies que personne ne relira. Les
#: intervalles longs ne l'atteignent jamais — cinq pages font sept mois
#: d'horaire, treize ans de quotidien —, les courts dès quelques jours
#: d'absence (trois et demi pour la minute).
MAX_CATCHUP_PAGES = 5

#: Les champs de Binance, moins `ignore` : ce qui ne serait pas gardé ici
#: devrait être retéléchargé le jour où un panneau en aurait besoin.
COLUMNS = ("open_time", "open", "high", "low", "close", "volume",
           "close_time", "quote_vol", "trades", "taker_buy_base",
           "taker_buy_quote")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
    symbol          TEXT NOT NULL,
    interval        TEXT NOT NULL,
    open_time       INTEGER NOT NULL,   -- ms epoch UTC
    open            REAL NOT NULL,
    high            REAL NOT NULL,
    low             REAL NOT NULL,
    close           REAL NOT NULL,
    volume          REAL NOT NULL,
    close_time      INTEGER NOT NULL,   -- ms epoch UTC
    quote_vol       REAL NOT NULL,
    trades          INTEGER NOT NULL,
    taker_buy_base  REAL NOT NULL,
    taker_buy_quote REAL NOT NULL,
    PRIMARY KEY (symbol, interval, open_time)
) WITHOUT ROWID;
"""


class KlineHistory:
    """Les chandeliers d'un symbole : le disque d'abord, Binance pour le reste.

    Une erreur réseau remonte à l'appelant, qui sait quoi en faire ; ce qui
    a déjà été écrit reste acquis. Deux threads peuvent rattraper le même
    intervalle en même temps : ils écrivent les mêmes bougies, sans dégât.
    """

    def __init__(self, symbol: str = "BTCUSDT", path: Path = DB_PATH):
        self.symbol = symbol
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        #: Intervalles dont le début de l'historique a été atteint : il
        #: n'y a plus rien à demander vers le passé.
        self._exhausted: set[str] = set()

    # ── Lecture ─────────────────────────────────────────────

    def latest(self, interval: str, limit: int) -> pd.DataFrame:
        """Les `limit` dernières bougies, celle en cours comprise."""
        live = self._sync(interval)
        wanted = limit - len(live)
        if wanted <= 0:
            return live.iloc[-limit:].reset_index(drop=True)
        closed = self._older(interval, None, wanted)
        if live.empty:
            return closed
        if closed.empty:
            return live.reset_index(drop=True)
        return pd.concat([closed, live], ignore_index=True)

    def page(self, interval: str, end_ms: int, limit: int) -> pd.DataFrame:
        """Les `limit` bougies ouvertes au plus tard à `end_ms`."""
        last = self._bound(interval, "MAX")
        if last is None or end_ms > last:
            # La borne touche au présent — le profil de la plage visible.
            frame = self.latest(interval, limit)
            return frame[frame["open_time"] <= end_ms].reset_index(drop=True)
        return self._older(interval, end_ms, limit)

    def stored(self, interval: str, limit: int,
               end_ms: Optional[int] = None) -> pd.DataFrame:
        """Ce que le disque tient, sans toucher au réseau."""
        return self._read(interval, end_ms, limit)

    # ── Réseau ──────────────────────────────────────────────

    def _sync(self, interval: str) -> pd.DataFrame:
        """Rattrape Binance et rend les bougies pas encore réglées."""
        last = self._bound(interval, "MAX")
        if last is not None and self._too_far(interval, last):
            # Oubliée plutôt que trouée : la plage sauvegardée reste d'un
            # seul tenant, et repart du présent.
            self._forget(interval)
            last = None
        while True:
            page = sources.fetch_klines(
                self.symbol, interval, PAGE,
                start_time=None if last is None else last + 1)
            settled = page["close_time"] + SETTLE_MS < time.time() * 1000
            self._save(interval, page[settled])
            live = page[~settled]
            # Une bougie vivante en fin de page, ou une page incomplète :
            # il n'y a rien au-delà.
            if len(live) or len(page) < PAGE:
                return live[["time", *COLUMNS]]
            last = int(page["open_time"].iloc[-1])

    def _older(self, interval: str, end_ms: Optional[int],
               limit: int) -> pd.DataFrame:
        """Les bougies sauvegardées jusqu'à `end_ms`, quitte à reculer."""
        df = self._read(interval, end_ms, limit)
        while len(df) < limit and self.extend_back(interval):
            df = self._read(interval, end_ms, limit)
        return self._read(interval, end_ms, limit)

    def extend_back(self, interval: str) -> bool:
        """Recule d'une page ; faux s'il n'y a plus rien à aller chercher."""
        if interval in self._exhausted:
            return False
        first = self._bound(interval, "MIN")
        if first is None:
            self._sync(interval)
            if self._bound(interval, "MIN") is None:
                self._exhausted.add(interval)
                return False
            return True
        page = sources.fetch_klines(self.symbol, interval, PAGE,
                                    end_time=first - 1)
        self._save(interval, page)
        if len(page) < PAGE:
            self._exhausted.add(interval)
            return False
        return True

    def _too_far(self, interval: str, last_ms: int) -> bool:
        """Vrai si rattraper `interval` demanderait plus de pages qu'on n'en
        accorde. Un intervalle sans durée connue n'est jamais oublié."""
        hours = sources.KLINE_HOURS.get(interval)
        if not hours:
            return False
        behind = (time.time() * 1000 - last_ms) / (hours * 3_600_000)
        return behind > MAX_CATCHUP_PAGES * PAGE

    def _forget(self, interval: str) -> None:
        with self._lock:
            db = self._connection()
            db.execute("DELETE FROM klines WHERE symbol = ? AND interval = ?",
                       (self.symbol, interval))
            db.commit()
        self._exhausted.discard(interval)

    # ── Base ────────────────────────────────────────────────

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            self._conn = conn
        return self._conn

    def _absent(self) -> bool:
        """Vrai tant que rien n'a été écrit : lire ne crée pas la base."""
        return self._conn is None and not self.path.exists()

    def _bound(self, interval: str, function: str) -> Optional[int]:
        """`MIN` ou `MAX` de la plage sauvegardée, nul si elle est vide."""
        if self._absent():
            return None
        with self._lock:
            row = self._connection().execute(
                f"SELECT {function}(open_time) FROM klines"
                " WHERE symbol = ? AND interval = ?",
                (self.symbol, interval)).fetchone()
        return row[0]

    def _read(self, interval: str, end_ms: Optional[int],
              limit: int) -> pd.DataFrame:
        if self._absent():
            return _frame([])
        query = (f"SELECT {', '.join(COLUMNS)} FROM klines"
                 " WHERE symbol = ? AND interval = ?")
        params: list = [self.symbol, interval]
        if end_ms is not None:
            query += " AND open_time <= ?"
            params.append(int(end_ms))
        query += " ORDER BY open_time DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._connection().execute(query, params).fetchall()
        return _frame(rows[::-1])

    def _save(self, interval: str, bars: pd.DataFrame) -> None:
        if bars.empty:
            return
        rows = list(bars[list(COLUMNS)].itertuples(index=False, name=None))
        marks = ", ".join("?" * (len(COLUMNS) + 2))
        with self._lock:
            conn = self._connection()
            with conn:
                conn.executemany(
                    f"INSERT OR REPLACE INTO klines VALUES ({marks})",
                    [(self.symbol, interval, *row) for row in rows])

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def _frame(rows: list) -> pd.DataFrame:
    """Des lignes de la base, au contrat de `sources.fetch_klines`."""
    df = pd.DataFrame(rows, columns=list(COLUMNS)).astype(
        {column: float for column in sources.OHLCV_COLUMNS})
    df.insert(0, "time", pd.to_datetime(df["open_time"], unit="ms"))
    return df
