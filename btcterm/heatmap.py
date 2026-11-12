"""
Carte de chaleur du carnet : la liquidité posée, niveau par niveau, dans le temps.

Le carnet du moment dit où sont les murs ; il ne dit pas lesquels tiennent,
lesquels reculent devant le prix, ni lesquels disparaissent juste avant
d'être touchés — l'ordre posé pour être vu, pas pour être exécuté. Une
photographie régulière des carnets, mise bout à bout, le montre : chaque
colonne est un instant, chaque ligne une tranche de prix, et l'intensité
la quantité posée — les cinq plateformes additionnées.

Le hub prend une colonne toutes les dix secondes (§ hub._sample_heatmap) et
le journal la garde trois jours (§ journal). Ce module ne fait que les
calculs, purs et testés sans réseau (`tests/test_heatmap.py`) :

- `nice_step` — le pas des tranches : un nombre rond (1, 2, 2,5 ou 5 fois
  une puissance de dix) proche de 0,04 % du prix, 25 $ vers 74 000 $ ;
- `column` — une colonne, sur ±2 % du prix (`BAND`) : la somme, par tranche,
  des quantités posées dans tous les carnets fournis ;
- `encode` / `decode` — la colonne en flottants 32 bits compressés, pour
  le journal ;
- `aggregate` — des colonnes vers une matrice prête à peindre : regroupées
  par tranche de temps (la bougie du graphique), moyennées, ramenées à une
  grille de prix commune — le pas change quand le prix franchit un nombre
  rond —, puis quantifiées sur 256 niveaux d'une échelle en racine carrée,
  bornée au 99e centile : un mur isolé ne doit pas éteindre tout le reste.
  Le logarithme, essayé d'abord, rendait les quantités moyennes déjà
  claires : près du prix, où le carnet est dense, tout était jaune et blanc
  et les murs ne ressortaient plus.
"""

from __future__ import annotations

import base64
import math
import zlib
from typing import Iterable, Optional

import numpy as np

__all__ = ["BAND", "TARGET", "nice_step", "column", "encode", "decode",
           "aggregate"]

#: La bande couverte autour du prix, de part et d'autre : la portée des
#: carnets les plus profonds du terminal (Binance, OKX, Coinbase) sans
#: peindre des tranches que seuls un ou deux d'entre eux atteignent.
BAND = 0.02

#: La largeur visée d'une tranche, en fraction du prix.
TARGET = 0.0004

_MULTIPLES = (1.0, 2.0, 2.5, 5.0)


def nice_step(price: float, target: float = TARGET) -> float:
    """Le pas rond le plus grand qui ne dépasse pas `price × target`."""
    if not price or price <= 0:
        raise ValueError("prix requis")
    raw = price * target
    power = 10 ** math.floor(math.log10(raw))
    best = power
    for multiple in _MULTIPLES:
        if multiple * power <= raw:
            best = multiple * power
    return best


def column(books: Iterable[tuple[dict, dict]], mid: float,
           band: float = BAND) -> Optional[tuple[int, float, np.ndarray]]:
    """Une colonne : `(base, pas, quantités)`.

    `books` donne, par plateforme, ses deux côtés (`{prix: quantité}`, la
    forme d'`OrderBook.snapshot`). La tranche `k` couvre les prix de
    `(base + k) × pas` à `(base + k + 1) × pas` ; acheteurs et vendeurs s'y
    additionnent — le prix les sépare de lui-même. `None` sans prix.
    """
    if not mid or mid <= 0:
        return None
    step = nice_step(mid)
    low = math.floor(mid * (1 - band) / step)
    high = math.floor(mid * (1 + band) / step)
    values = np.zeros(high - low + 1, dtype=np.float64)
    for bids, asks in books:
        for side in (bids, asks):
            if not side:
                continue
            prices = np.fromiter(side.keys(), dtype=np.float64, count=len(side))
            sizes = np.fromiter(side.values(), dtype=np.float64, count=len(side))
            index = np.floor(prices / step).astype(np.int64) - low
            keep = (index >= 0) & (index < len(values))
            np.add.at(values, index[keep], sizes[keep])
    return low, step, values.astype(np.float32)


def encode(values: np.ndarray) -> bytes:
    """Une colonne pour le journal : flottants 32 bits, compressés."""
    return zlib.compress(np.asarray(values, dtype=np.float32).tobytes(), 6)


def decode(blob: bytes) -> np.ndarray:
    return np.frombuffer(zlib.decompress(blob), dtype=np.float32)


def aggregate(columns: Iterable[tuple[float, int, float, np.ndarray]],
              bucket: int) -> Optional[dict]:
    """Des colonnes `(ts, base, pas, quantités)` vers une matrice à peindre.

    Les colonnes se regroupent par tranche de `bucket` secondes (début aux
    multiples de `bucket` depuis l'epoch, comme les bougies jusqu'au jour)
    et s'y moyennent. Le pas commun est le plus grand rencontré : une
    colonne au pas plus fin y verse ses tranches par leur prix. Rend `None`
    sans colonne ; sinon `step` (le pas commun), `low` (le prix du bas de
    la première ligne), `rows`, `times` (les débuts de tranche), `vmax`
    (la quantité, en BTC, du blanc de l'échelle — racine carrée, bornée au
    99e centile) et `data` : une ligne de
    `rows` octets par tranche de temps, du prix le plus bas au plus haut,
    en base64.
    """
    columns = [c for c in columns if c[3] is not None and len(c[3])]
    if not columns:
        return None
    step = max(c[2] for c in columns)
    # La grille commune : du plus bas au plus haut prix couvert.
    low = min(math.floor(c[1] * c[2] / step) for c in columns)
    high = max(math.floor((c[1] + len(c[3]) - 1) * c[2] / step)
               for c in columns)
    rows = high - low + 1
    starts = sorted({int(c[0] // bucket * bucket) for c in columns})
    where = {start: i for i, start in enumerate(starts)}
    sums = np.zeros((len(starts), rows), dtype=np.float64)
    counts = np.zeros(len(starts), dtype=np.int64)
    for ts, base, own, values in columns:
        t = where[int(ts // bucket * bucket)]
        counts[t] += 1
        if own == step:
            offset = base - low
            sums[t, offset:offset + len(values)] += values
        else:
            prices = (base + np.arange(len(values))) * own
            index = np.floor(prices / step).astype(np.int64) - low
            np.add.at(sums[t], index, values)
    means = sums / counts[:, None]
    filled = means[means > 0]
    if not filled.size:
        return None
    vmax = float(np.percentile(filled, 99))
    vmax = vmax if vmax > 0 else float(filled.max())
    scale = np.sqrt(np.minimum(means, vmax) / vmax)
    levels = np.rint(scale * 255).astype(np.uint8)
    return {"step": step, "low": low * step, "rows": rows,
            "times": starts, "vmax": vmax,
            "data": base64.b64encode(levels.tobytes()).decode("ascii")}
