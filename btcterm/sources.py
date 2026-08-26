"""
Sources de données non temps réel — socle commun du terminal.

Regroupe les collecteurs qui ne passent pas par WebSocket : chandeliers
et tickers REST, taux de change, flux des ETF Bitcoin spot, news et
indice Fear & Greed.

Chaque fonction se limite à **récupérer et normaliser** ; aucune n'écrit
en base ni n'affiche quoi que ce soit, afin qu'un même appel puisse
servir plusieurs panneaux. Les dépendances optionnelles (`feedparser`)
sont importées à l'intérieur des fonctions qui les utilisent, pour qu'un
panneau n'ait pas à les installer s'il ne s'en sert pas.
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
import requests

__all__ = [
    "BINANCE_REST", "OHLCV_COLUMNS", "RSS_FEEDS",
    "fetch_klines", "fetch_ticker_24h", "fetch_depth",
    "KLINE_FREQ", "KLINE_HOURS", "generate_demo_ohlcv",
    "fetch_eur_rate", "fetch_m2_supply",
    "fetch_funding_history", "fetch_open_interest", "fetch_perp_snapshot",
    "fetch_market_global", "fetch_chain_chart", "fetch_chain_stats",
    "fetch_etf_flows",
    "fetch_rss_entries", "fetch_cryptopanic_posts", "fetch_fear_greed",
    "fetch_fear_greed_history", "FEAR_GREED_DAYS",
]

BINANCE_REST = "https://api.binance.com/api/v3"
FX_URL = "https://api.exchangerate-api.com/v4/latest/USD"
#: Flux des ETF spot — la page « all data » de farside.co.uk plutôt que
#: sa page d'accueil : celle-ci ne publie plus que les trois dernières
#: semaines, quand l'autre sert le même tableau depuis le lancement des
#: ETF en janvier 2024. Le panneau annonçait trente jours qu'il n'avait
#: pas.
FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
FEAR_GREED_URL = "https://api.alternative.me/fng/"

#: Profondeur d'historique demandée à alternative.me. L'indice est
#: journalier : quatre-vingt-dix points couvrent un trimestre, la
#: fenêtre où un cycle complet de peur et d'avidité se lit encore d'un
#: seul coup d'œil.
FEAR_GREED_DAYS = 90

#: Données de chaîne — blockchain.info, sans clé.
#:
#: mempool.space, la source la plus riche, a dû être écartée : elle ne
#: publie souvent que des adresses IPv6 et cette machine n'a pas de route
#: IPv6, d'où des « Network is unreachable » intermittents. blockchain.info
#: répond en IPv4 et suffit à ce que le panneau montre.
BLOCKCHAIN_CHARTS = "https://api.blockchain.info/charts"
BLOCKCHAIN_STATS = "https://api.blockchain.info/stats"

#: Agrégats de marché (capitalisation, dominance) — CoinGecko, endpoint
#: public sans clé. Les séries historiques, elles, sont payantes : ce
#: collecteur ne rapporte qu'un instantané.
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

#: API des contrats à terme Binance : financement, open interest et
#: positionnement des comptes. Publique et sans clé, comme le reste.
BINANCE_FUTURES = "https://fapi.binance.com"

#: Masse monétaire M2 des États-Unis (série H.6 de la Réserve fédérale,
#: mensuelle, désaisonnalisée), servie par DBnomics. FRED publie la même
#: série, mais son export CSV réclame une clé pour l'API et ne répond pas
#: de façon fiable sans navigateur ; DBnomics la miroite sans clé.
M2_URL = "https://api.db.nomics.world/v22/series/FED/H6_H6_M2/M2.M"

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

#: Actifs traités comme des dollars dans les parts de capitalisation :
#: leur poids mesure le cash qui attend sur le marché, pas une
#: concurrence faite au Bitcoin. Partagé entre le graphique de dominance
#: et l'instantané que le hub journalise.
STABLES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD"}

# Un User-Agent de navigateur est nécessaire : farside.co.uk renvoie une
# page vide aux clients qui s'annoncent comme des scripts.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

RSS_FEEDS: list[dict[str, str]] = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/.rss/full/"},
    {"name": "The Block", "url": "https://www.theblock.co/rss.xml"},
    {"name": "CryptoSlate", "url": "https://cryptoslate.com/feed/"},
]


# ─────────────────────────────────────────────────────────────
# Marché — REST Binance
# ─────────────────────────────────────────────────────────────

def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1d",
    limit: int = 350,
    index: bool = False,
    end_time: Optional[int] = None,
) -> pd.DataFrame:
    """Chandeliers OHLCV depuis l'API publique Binance.

    `index=False` retourne une colonne `time` et un index entier ;
    `index=True` indexe le DataFrame par l'horodatage d'ouverture.

    `end_time` (millisecondes epoch) borne la fin de la série : Binance
    renvoie alors les `limit` dernières bougies ouvertes au plus tard à
    cet instant — c'est la pagination vers le passé du panneau prix.
    """
    params: dict[str, Any] = {"symbol": symbol, "interval": interval,
                              "limit": limit}
    if end_time is not None:
        params["endTime"] = int(end_time)
    response = requests.get(
        f"{BINANCE_REST}/klines",
        params=params,
        timeout=12,
    )
    response.raise_for_status()

    df = pd.DataFrame(response.json(), columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    for column in OHLCV_COLUMNS:
        df[column] = df[column].astype(float)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms")

    if index:
        return df.set_index("time")[OHLCV_COLUMNS]
    return df.reset_index(drop=True)


def fetch_ticker_24h(symbol: str = "BTCUSDT") -> dict[str, Any]:
    """Statistiques glissantes sur 24 h. Retourne `{}` en cas d'échec."""
    try:
        response = requests.get(
            f"{BINANCE_REST}/ticker/24hr", params={"symbol": symbol}, timeout=6
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def fetch_depth(
    symbol: str = "BTCUSDT", limit: int = 10
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Instantané REST du carnet : `(bids, asks)` triés du meilleur au pire."""
    response = requests.get(
        f"{BINANCE_REST}/depth", params={"symbol": symbol, "limit": limit}, timeout=5
    )
    response.raise_for_status()
    book = response.json()
    return (
        [(float(p), float(q)) for p, q in book["bids"]],
        [(float(p), float(q)) for p, q in book["asks"]],
    )


#: Correspondance intervalle Binance → alias de fréquence pandas, pour les
#: séries de démonstration : Binance parle en `15m` et `1M`, pandas en
#: `15min` et `ME`.
KLINE_FREQ = {
    "15m": "15min", "30m": "30min", "1h": "h", "4h": "4h", "6h": "6h",
    "12h": "12h", "1d": "D", "1w": "W", "1M": "ME",
}

#: Paramètres de la série de démonstration : volatilité et dérive
#: annualisées, et prix d'arrivée de la série.
DEMO_VOL = 0.55
DEMO_DRIFT = 0.40
DEMO_ANCHOR = 80_000.0
HOURS_PER_YEAR = 24 * 365

#: Durée d'une bougie en heures, pour mettre la marche aléatoire de démo
#: à l'échelle de l'intervalle demandé.
KLINE_HOURS = {
    "15m": 0.25, "30m": 0.5, "1h": 1, "4h": 4, "6h": 6,
    "12h": 12, "1d": 24, "1w": 168, "1M": 730,
}


def generate_demo_ohlcv(
    limit: int = 300, seed: int = 42, interval: str = "1h", index: bool = True
) -> pd.DataFrame:
    """Série OHLCV synthétique, pour travailler hors ligne.

    Marche aléatoire log-normale paramétrée en annualisé — 55 % de
    volatilité, 40 % de dérive, ordres de grandeur plausibles pour du
    BTC — puis ramenée à la durée d'une bougie en racine du temps : une
    bougie mensuelle bouge plus qu'une bougie de quinze minutes, quel
    que soit l'intervalle demandé. La série est enfin recalée pour finir
    sur `DEMO_ANCHOR`, faute de quoi une longue série mensuelle
    s'éloignerait de tout prix crédible. Le générateur est ensemencé afin
    que deux appels produisent la même série.

    Le résultat porte `attrs["demo"] = True`, ce qui permet aux panneaux
    de signaler que les chiffres affichés ne sont pas réels.

    `index=False` retourne une colonne `time` et un index entier ;
    `index=True` indexe par l'horodatage — mêmes conventions que
    `fetch_klines`.
    """
    rng = np.random.default_rng(seed)
    hours = KLINE_HOURS.get(interval, 1.0)
    dates = pd.date_range(
        end=datetime.now(), periods=limit, freq=KLINE_FREQ.get(interval, "h")
    )

    years = hours / HOURS_PER_YEAR
    steps = rng.normal(DEMO_DRIFT * years, DEMO_VOL * float(np.sqrt(years)), limit)
    walk = np.exp(np.cumsum(steps))
    close = pd.Series(walk * (DEMO_ANCHOR / walk[-1]))
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close),
        "high": close * (1 + abs(rng.normal(0, 0.005, limit))),
        "low": close * (1 - abs(rng.normal(0, 0.005, limit))),
        "close": close,
        "volume": rng.lognormal(20, 1, limit),
    })
    df.attrs["demo"] = True

    if index:
        df.index = dates
        df.index.name = "ts"
        return df
    df["time"] = dates
    return df


def fetch_eur_rate(default: float = 0.924) -> float:
    """Taux USD→EUR, avec repli sur une valeur figée si l'appel échoue."""
    try:
        response = requests.get(FX_URL, timeout=5)
        response.raise_for_status()
        return float(response.json()["rates"]["EUR"])
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────
# Flux des ETF Bitcoin spot
# ─────────────────────────────────────────────────────────────

def fetch_chain_chart(
    name: str = "hash-rate", timespan: str = "1year"
) -> pd.DataFrame:
    """Une série on-chain de blockchain.info : `time` / `value`.

    Les noms utiles sont `hash-rate` (en TH/s), `difficulty` et
    `mempool-size` (en octets) ; l'unité reste celle de la source, la
    mise à l'échelle appartient à l'affichage.
    """
    response = requests.get(
        f"{BLOCKCHAIN_CHARTS}/{name}",
        params={"timespan": timespan, "format": "json", "cors": "true"},
        timeout=20,
    )
    response.raise_for_status()

    values = response.json().get("values", [])
    if not values:
        return pd.DataFrame(columns=["time", "value"])
    df = pd.DataFrame(values)
    return pd.DataFrame({
        "time": pd.to_datetime(df["x"], unit="s"),
        "value": df["y"].astype(float),
    })


def fetch_chain_stats() -> dict[str, Any]:
    """Instantané du réseau : hashrate, difficulté, rythme des blocs.

    `hash_rate` est en GH/s chez blockchain.info — la conversion en EH/s
    appartient à l'appelant, comme pour les séries.

    Le champ `total_fees_btc` de la source est ignoré : il revient
    négatif, donc inexploitable.
    """
    response = requests.get(BLOCKCHAIN_STATS, timeout=15)
    response.raise_for_status()
    data = response.json()

    return {
        "hash_rate_ghs": float(data.get("hash_rate", 0)),
        "difficulty": float(data.get("difficulty", 0)),
        "minutes_between_blocks": float(data.get("minutes_between_blocks", 0)),
        "n_tx": int(data.get("n_tx", 0)),
    }


def fetch_market_global() -> dict[str, Any]:
    """Capitalisation totale, dominance et volume — instantané.

    CoinGecko ne donne l'historique de ces agrégats que sur son offre
    payante : ce collecteur rapporte l'état du moment, et le panneau qui
    l'utilise le dit plutôt que de faire croire à une série.

    Retourne `{}` en cas d'échec, les parts étant en pourcentage.
    """
    response = requests.get(COINGECKO_GLOBAL, timeout=12)
    response.raise_for_status()
    data = response.json()["data"]

    return {
        "total_cap_usd": float(data["total_market_cap"]["usd"]),
        "total_volume_usd": float(data["total_volume"]["usd"]),
        "cap_change_24h": float(data.get("market_cap_change_percentage_24h_usd", 0)),
        "shares": {k.upper(): float(v) for k, v in data["market_cap_percentage"].items()},
        "updated_at": int(data.get("updated_at", 0)),
    }


def fetch_funding_history(
    symbol: str = "BTCUSDT", limit: int = 90
) -> pd.DataFrame:
    """Historique du taux de financement du perpétuel, un point par 8 h.

    Le taux est le loyer que les longs paient aux shorts (ou l'inverse
    s'il est négatif) toutes les huit heures : c'est la mesure la plus
    directe du déséquilibre entre les deux côtés du marché à terme.

    Retourne `time` / `rate`, le taux en fraction (0,0001 = 0,01 %).
    """
    response = requests.get(
        f"{BINANCE_FUTURES}/fapi/v1/fundingRate",
        params={"symbol": symbol, "limit": limit},
        timeout=12,
    )
    response.raise_for_status()

    df = pd.DataFrame(response.json())
    if df.empty:
        return pd.DataFrame(columns=["time", "rate"])
    return pd.DataFrame({
        "time": pd.to_datetime(df["fundingTime"], unit="ms"),
        "rate": df["fundingRate"].astype(float),
    })


def fetch_open_interest(
    symbol: str = "BTCUSDT", period: str = "4h", limit: int = 180
) -> pd.DataFrame:
    """Open interest du perpétuel, en BTC et en dollars.

    Binance ne conserve que **trente jours** d'historique sur cet
    endpoint : demander plus ne renvoie pas davantage.

    Retourne `time` / `oi` (BTC) / `oi_usd`.
    """
    response = requests.get(
        f"{BINANCE_FUTURES}/futures/data/openInterestHist",
        params={"symbol": symbol, "period": period, "limit": limit},
        timeout=12,
    )
    response.raise_for_status()

    df = pd.DataFrame(response.json())
    if df.empty:
        return pd.DataFrame(columns=["time", "oi", "oi_usd"])
    return pd.DataFrame({
        "time": pd.to_datetime(df["timestamp"], unit="ms"),
        "oi": df["sumOpenInterest"].astype(float),
        "oi_usd": df["sumOpenInterestValue"].astype(float),
    })


def fetch_perp_snapshot(symbol: str = "BTCUSDT") -> dict[str, Any]:
    """Instantané du perpétuel : prix marqué, financement courant, ratio.

    Réunit deux endpoints — `premiumIndex` pour le prix marqué et le
    prochain financement, `globalLongShortAccountRatio` pour la part des
    comptes longs. Chaque partie manquante est simplement absente du
    résultat : un ratio indisponible ne doit pas priver le panneau du
    financement.
    """
    snapshot: dict[str, Any] = {}
    try:
        response = requests.get(
            f"{BINANCE_FUTURES}/fapi/v1/premiumIndex",
            params={"symbol": symbol}, timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        snapshot["mark_price"] = float(data["markPrice"])
        snapshot["index_price"] = float(data["indexPrice"])
        snapshot["funding_rate"] = float(data["lastFundingRate"])
        snapshot["next_funding"] = int(data["nextFundingTime"])
    except Exception:
        pass

    try:
        response = requests.get(
            f"{BINANCE_FUTURES}/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": "1h", "limit": 1}, timeout=8,
        )
        response.raise_for_status()
        row = response.json()[0]
        snapshot["long_account"] = float(row["longAccount"])
        snapshot["long_short_ratio"] = float(row["longShortRatio"])
    except Exception:
        pass

    return snapshot


def fetch_m2_supply(start: str = "2013-01-01") -> pd.DataFrame:
    """Masse monétaire M2 des États-Unis, en milliards de dollars.

    Série mensuelle désaisonnalisée, publiée avec environ deux mois de
    retard — c'est le rythme de la donnée, pas un défaut de collecte.

    Retourne un DataFrame `time` / `m2` trié, tronqué à `start` : la
    série remonte à 1959, alors que le Bitcoin ne cote que depuis 2010.
    """
    response = requests.get(M2_URL, params={"observations": "true"}, timeout=20)
    response.raise_for_status()

    document = response.json()["series"]["docs"][0]
    df = pd.DataFrame({
        "time": pd.to_datetime(document["period_start_day"]),
        "m2": pd.to_numeric(document["value"], errors="coerce"),
    })
    df = df.dropna().sort_values("time")
    return df[df["time"] >= pd.Timestamp(start)].reset_index(drop=True)


def fetch_etf_flows() -> pd.DataFrame:
    """Flux quotidiens des ETF Bitcoin spot américains, en millions de $.

    Source : le tableau public de farside.co.uk, dont on retient la table
    la plus haute de la page. Les montants négatifs y sont notés entre
    parenthèses selon la convention comptable.
    """
    response = requests.get(FARSIDE_URL, headers=BROWSER_HEADERS, timeout=15)
    response.raise_for_status()

    tables = pd.read_html(io.StringIO(response.text))
    if not tables:
        raise RuntimeError("Aucun tableau trouvé sur la page.")
    df = max(tables, key=lambda t: t.shape[0]).copy()

    # Le site renvoie parfois des en-têtes à deux niveaux (ticker + frais).
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            next((str(p) for p in col if not str(p).startswith("Unnamed")), str(col[0]))
            for col in df.columns
        ]
    else:
        df.columns = [str(c).strip() for c in df.columns]

    df = df.rename(columns={df.columns[0]: "Date"})

    # Ne garder que les vraies lignes de données : le tableau contient des
    # en-têtes répétés et des lignes de totaux.
    df = df[df["Date"].astype(str).str.match(r"^\d{1,2} \w{3} \d{4}$", na=False)]
    df["Date"] = pd.to_datetime(df["Date"], format="%d %b %Y", errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)

    for column in df.columns[1:]:
        df[column] = pd.to_numeric(
            df[column]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("(", "-", regex=False)
            .str.replace(")", "", regex=False)
            .str.replace("US$m", "", regex=False)
            .str.strip(),
            errors="coerce",
        )

    return df.fillna(0.0)


# ─────────────────────────────────────────────────────────────
# News et sentiment
# ─────────────────────────────────────────────────────────────

def fetch_rss_entries(
    feeds: Optional[Iterable[dict[str, str]]] = None,
    on_error=None,
) -> list[dict[str, str]]:
    """Articles des flux RSS, normalisés et débarrassés de leur HTML.

    Un flux en échec est signalé via `on_error(nom, exception)` puis
    ignoré : une source indisponible ne doit pas priver le panneau des
    autres.
    """
    import feedparser  # dépendance optionnelle

    entries: list[dict[str, str]] = []
    for feed_info in feeds if feeds is not None else RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries:
                entries.append({
                    "title": entry.get("title", "").strip(),
                    "summary": re.sub(r"<[^>]+>", "", entry.get("summary", "")),
                    "url": entry.get("link", ""),
                    "source": feed_info["name"],
                    "published": entry.get("published", ""),
                })
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(feed_info["name"], exc)
    return entries


def fetch_cryptopanic_posts(api_key: str) -> list[dict[str, Any]]:
    """Publications CryptoPanic filtrées BTC et marquées « important ».

    Retourne une liste vide sans clé : la source est simplement inactive.
    Les votes de la communauté sont conservés bruts, leur exploitation
    (bonus de score, sentiment) appartenant à l'appelant.
    """
    if not api_key:
        return []

    response = requests.get(
        "https://cryptopanic.com/api/v1/posts/",
        params={
            "auth_token": api_key,
            "currencies": "BTC",
            "filter": "important",
            "public": "true",
        },
        timeout=10,
    )
    response.raise_for_status()

    return [
        {
            "title": item.get("title", "").strip(),
            "summary": "",
            "url": item.get("url", ""),
            "source": f"CryptoPanic/{item.get('source', {}).get('title', '')}",
            "published": item.get("published_at", ""),
            "votes": item.get("votes", {}) or {},
        }
        for item in response.json().get("results", [])
    ]


def fetch_fear_greed() -> Optional[dict[str, Any]]:
    """Indice Fear & Greed du jour, ou `None` si la source est injoignable."""
    try:
        response = requests.get(FEAR_GREED_URL, params={"limit": 1}, timeout=8)
        response.raise_for_status()
        data = response.json()["data"][0]
        return {"value": int(data["value"]), "label": data["value_classification"]}
    except Exception:
        return None


def fetch_fear_greed_history(
    days: int = FEAR_GREED_DAYS,
) -> list[dict[str, Any]]:
    """Historique de l'indice, du point le plus ancien au plus récent.

    alternative.me publie une valeur par jour et sert l'historique par la
    même requête que la valeur du jour — demander la série ne coûte donc
    rien de plus qu'un appel, et la valeur courante en est le dernier
    point.

    Contrairement à `fetch_fear_greed`, cette fonction laisse remonter
    l'erreur : le cache du hub préfère resservir sa série précédente
    plutôt que d'effacer la courbe à la première source injoignable.
    """
    response = requests.get(FEAR_GREED_URL, params={"limit": days}, timeout=10)
    response.raise_for_status()

    points = [
        {
            "time": datetime.fromtimestamp(int(item["timestamp"]), timezone.utc),
            "value": int(item["value"]),
            "label": item["value_classification"],
        }
        for item in response.json().get("data", [])
    ]
    # L'API répond du plus récent au plus ancien ; un graphique se lit
    # dans l'autre sens.
    points.sort(key=lambda point: point["time"])
    return points
