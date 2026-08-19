"""
Sources de données non temps réel — socle commun du terminal.

Regroupe les collecteurs qui ne passent pas par WebSocket : chandeliers
et tickers REST, taux de change, flux des ETF Bitcoin spot, news et
indice Fear & Greed.

Chaque fonction se limite à **récupérer et normaliser** ; aucune n'écrit
en base ni n'affiche quoi que ce soit, afin qu'un même appel puisse
servir plusieurs panneaux. Les dépendances optionnelles (`ccxt`,
`feedparser`) sont importées à l'intérieur des fonctions qui les
utilisent, pour qu'un panneau n'ait pas à les installer s'il ne s'en sert
pas.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
import requests

__all__ = [
    "BINANCE_REST", "OHLCV_COLUMNS", "RSS_FEEDS",
    "fetch_klines", "fetch_ticker_24h", "fetch_depth",
    "fetch_ohlcv_ccxt", "generate_demo_ohlcv",
    "fetch_eur_rate",
    "fetch_etf_flows",
    "fetch_rss_entries", "fetch_cryptopanic_posts", "fetch_fear_greed",
]

BINANCE_REST = "https://api.binance.com/api/v3"
FX_URL = "https://api.exchangerate-api.com/v4/latest/USD"
FARSIDE_URL = "https://farside.co.uk/btc/"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

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
) -> pd.DataFrame:
    """Chandeliers OHLCV depuis l'API publique Binance.

    `index=False` retourne une colonne `time` et un index entier ;
    `index=True` indexe le DataFrame par l'horodatage d'ouverture.
    """
    response = requests.get(
        f"{BINANCE_REST}/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
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


def fetch_ohlcv_ccxt(exchange, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Chandeliers via un exchange `ccxt` déjà instancié.

    Passer par ccxt plutôt que par l'API Binance permet de changer de
    plateforme sans toucher au reste du code.
    """
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", *OHLCV_COLUMNS])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.set_index("ts")


def generate_demo_ohlcv(limit: int = 300, seed: int = 42) -> pd.DataFrame:
    """Série OHLCV synthétique, pour travailler hors ligne.

    Marche aléatoire log-normale de paramètres plausibles pour du BTC
    horaire. Le générateur est ensemencé afin que deux appels produisent
    la même série.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=datetime.now(), periods=limit, freq="1h")

    price = 80_000.0
    prices = []
    for _ in range(limit):
        price *= np.exp(rng.normal(0.0002, 0.018))
        prices.append(price)

    close = pd.Series(prices)
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close),
        "high": close * (1 + abs(rng.normal(0, 0.005, limit))),
        "low": close * (1 - abs(rng.normal(0, 0.005, limit))),
        "close": close,
        "volume": rng.lognormal(20, 1, limit),
    }, index=dates)
    df.index.name = "ts"
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
        response = requests.get(FEAR_GREED_URL, timeout=8)
        response.raise_for_status()
        data = response.json()["data"][0]
        return {"value": int(data["value"]), "label": data["value_classification"]}
    except Exception:
        return None
