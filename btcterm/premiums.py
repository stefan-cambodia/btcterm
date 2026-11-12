"""
Primes régionales : ce que le bitcoin coûte ailleurs que sur les grandes
places en USDT.

Une prime dit où la demande presse. Celle de Coinbase (§ tape.
coinbase_premium) lit l'Amérique et ses institutions ; celle de la Corée
— la « prime kimchi » — lit les particuliers coréens, qui achètent en wons
sur Upbit et Bithumb derrière un contrôle des changes : l'argent ne sort
pas assez vite pour que l'arbitrage ferme l'écart. Elle a touché 21,6 %
le 19 mai 2021 et 10,3 % le 6 mars 2024, le jour où Upbit cotait un
record à 96,7 millions de wons (relevés CryptoQuant repris par la
presse), et vire à la décote dans les marchés las.

La prime se décompose en deux facteurs, exactement :

    (1 + prime) = (1 + won) × (1 + bitcoin)

- `won` — l'USDT en wons sur Upbit contre le dollar : le prix de la
  sortie du won vers la cryptomonnaie, structurel, autour de 1 % le
  12 novembre 2026 ;
- `bitcoin` — le bitcoin contre l'USDT sur Upbit, face au cours mondial :
  la demande propre au bitcoin, une fois le won franchi.

Une prime portée par le seul won dit le contrôle des changes ; portée par
le bitcoin, l'appétit. Le cours mondial est celui des carnets du terminal,
en USDT : l'USDT et le dollar s'écartent de quelques centièmes, sous la
précision qui compte ici.

Calcul pur, testé sans réseau (`tests/test_premiums.py`) ; le hub lit les
sources (§ hub.korea_premium).
"""

from __future__ import annotations

from typing import Optional

__all__ = ["KOREA_HOT", "korea"]

#: Prime coréenne, en %, au-delà de laquelle elle se signale : l'ordinaire
#: du won en porte déjà un point ; à trois, l'appétit coréen se paie.
KOREA_HOT = 3.0


def korea(quotes: Optional[dict], fx: Optional[dict],
          reference: Optional[float]) -> Optional[dict]:
    """La prime coréenne et ses deux facteurs, en fractions.

    `quotes` : `btc`, `usdt` en wons et `at` (§ sources.fetch_upbit_
    quotes) ; `fx` : `rate` (wons pour un dollar) et `source` (§ sources.
    fetch_krw_rate) ; `reference` : le cours mondial. Rend `premium`,
    `won`, `bitcoin`, `price_usd` (le bitcoin d'Upbit en dollars),
    `rate`, `source` et `at` ; `None` s'il manque une pièce.
    """
    if not quotes or not fx or not reference or reference <= 0:
        return None
    rate = fx.get("rate")
    btc, usdt = quotes.get("btc"), quotes.get("usdt")
    if not rate or not btc or not usdt:
        return None
    price_usd = btc / rate
    return {"premium": price_usd / reference - 1,
            "won": usdt / rate - 1,
            "bitcoin": btc / usdt / reference - 1,
            "price_usd": price_usd, "rate": rate,
            "source": fx.get("source"), "at": quotes.get("at")}
