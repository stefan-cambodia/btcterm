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
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
import requests
from urllib3.exceptions import ProtocolError
from urllib3.util.retry import Retry

__all__ = [
    "BINANCE_REST", "OHLCV_COLUMNS", "RSS_FEEDS",
    "fetch_klines", "fetch_ticker_24h", "fetch_depth",
    "KLINE_FREQ", "KLINE_HOURS", "generate_demo_ohlcv",
    "fetch_eur_rate", "fetch_m2_supply", "fetch_block_time", "fetch_mvrv",
    "fetch_funding_history", "fetch_open_interest", "fetch_perp_snapshot",
    "fetch_bybit_perp", "fetch_okx_perp",
    "clean_etf_table", "fetch_market_global", "market_global", "INTERMITTENT",
    "fetch_fee_estimates", "fee_estimates",
    "fetch_stablecoin_supply", "stablecoin_supply", "fetch_chain_chart", "fetch_chain_stats",
    "fetch_etf_flows", "etf_pending",
    "fetch_rss_entries", "fetch_cryptopanic_posts", "fetch_fear_greed",
    "fetch_fear_greed_now",
    "fetch_fear_greed_history", "FEAR_GREED_DAYS",
    "brief_error",
]

BINANCE_REST = "https://api.binance.com/api/v3"

#: Connexion gardée ouverte pour les chandeliers : le panneau ne demande
#: plus que la fin de la série (§ history), et la poignée de main TLS
#: pesait alors l'essentiel de l'appel — 337 ms pour deux bougies, 110 ms
#: une fois la connexion établie. Binance peut la fermer entre deux appels :
#: une relecture suffit à en rouvrir une. La connexion elle-même n'est pas
#: retentée — une adresse morte coûterait deux fois son délai.
_klines_http = requests.Session()
_klines_http.mount("https://", requests.adapters.HTTPAdapter(
    max_retries=Retry(total=1, connect=0, read=1, status=0, redirect=0)))
#: Une session pour tous les autres collecteurs : la connexion à chaque
#: hôte est gardée d'un appel à l'autre. Chaque `requests.get` en ouvrait
#: une neuve — poignée de main TLS et rechargement des certificats compris :
#: mesuré le 7 novembre 2026 sur le ticker de Binance, 251 ms et 18,8 ms de
#: processeur par appel, contre 132 ms et 3,4 ms une fois la connexion
#: gardée ; et le bandeau relit ce ticker toutes les cinq secondes. Pas de
#: nouvel essai sur un délai dépassé — il doublerait l'attente des sources
#: lentes (jusqu'à vingt secondes) —, mais un seul sur une connexion rompue
#: (`_ReprendRupture`).


class _ReprendRupture(requests.adapters.HTTPAdapter):
    """Rejoue une fois la lecture qu'une connexion gardée a rompue.

    urllib3 écarte une connexion que le serveur a fermée proprement, pas
    celle qu'un intermédiaire a oubliée : rien n'est arrivé sur la prise, et
    c'est l'envoi suivant qui reçoit le « Connection reset by peer ». Les
    sources lues toutes les cinq ou dix minutes y tombaient — onze des
    dix-huit pannes du journal entre le 7 et le 11 novembre 2026, toutes
    sur CoinGecko et blockchain.info, chacune laissant le panneau sur sa
    valeur de secours jusqu'à la lecture suivante. La rupture se dit tout
    de suite : la rejouer sur une connexion neuve ne coûte pas un délai de
    plus. Un délai dépassé ou une connexion refusée ne sont pas rejoués.
    """

    def send(self, request, **kwargs):
        try:
            return super().send(request, **kwargs)
        except requests.exceptions.ConnectionError as exc:
            rompue = exc.args and isinstance(exc.args[0], ProtocolError)
            if not rompue or request.method != "GET":
                raise
            return super().send(request, **kwargs)


_http = requests.Session()
_http.mount("https://", _ReprendRupture(pool_connections=32, pool_maxsize=16))
#: Le taux USD→EUR : le marché EUR/USDT de Binance, et en secours le taux
#: de référence de la BCE (Frankfurter v1, sans clé). Voir `fetch_eur_rate`.
FX_MARKET_URL = f"{BINANCE_REST}/ticker/price"
FX_REFERENCE_URL = "https://api.frankfurter.dev/v1/latest"
#: Le ticker public d'Upbit, première place coréenne : le bitcoin et l'USDT
#: en wons, sans clé (§ fetch_upbit_quotes).
UPBIT_TICKER = "https://api.upbit.com/v1/ticker"
#: Le won contre le dollar chez Yahoo, au fil du marché (§ fetch_krw_rate).
KRW_MARKET_SYMBOL = "KRW=X"
#: Plage plausible du won pour un dollar : 900 à 1 600 depuis vingt ans ;
#: au-delà de ces bornes larges, la réponse est une panne qui a pris la
#: forme d'un cours.
KRW_PLAUSIBLE = (500.0, 5_000.0)
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
BLOCKSTREAM_FEES = "https://blockstream.info/api/fee-estimates"
#: L'heure d'un bloc par sa hauteur, en deux petites requêtes : blockchain.
#: info le sert aussi, mais avec toutes ses transactions — 7 Mo pour une
#: heure.
BLOCKSTREAM_API = "https://blockstream.info/api"
#: Les métriques on-chain gratuites de Coin Metrics (données communautaires,
#: licence CC BY-NC 4.0 : usage non commercial, « Coin Metrics » nommé là
#: où elles s'affichent). Sans clé ; dix requêtes par six secondes au plus.
COINMETRICS_API = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

#: Agrégats de marché (capitalisation, dominance) — CoinGecko, endpoint
#: public sans clé. Les séries historiques, elles, sont payantes : ce
#: collecteur ne rapporte qu'un instantané.
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

#: API des contrats à terme Binance : financement, open interest et
#: positionnement des comptes. Publique et sans clé, comme le reste.
BINANCE_FUTURES = "https://fapi.binance.com"
BYBIT_REST = "https://api.bybit.com"
OKX_REST = "https://www.okx.com"

#: Options et futures à échéance — Deribit, API publique sans clé. La
#: plateforme tient l'essentiel de l'open interest des options bitcoin ;
#: une requête rend toute la chaîne (près d'un millier d'options).
DERIBIT_REST = "https://www.deribit.com/api/v2/public"

#: Marchés traditionnels — actions, or, dollar, taux — par l'API de
#: graphiques de Yahoo Finance : non documentée, sans clé, la seule source
#: gratuite qui serve ces séries sans vérification JavaScript (Stooq en
#: impose une). Elle refuse l'agent par défaut de requests (429) ; un nom
#: de client générique suffit.
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_HEADERS = {"User-Agent": "btcterm/1.1"}

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

#: Actifs que CoinGecko compte **par intermittence** dans la capitalisation
#: totale. Constaté au journal le 11 novembre 2026 : quatre-vingt-six fois
#: en deux mois et demi, la capitalisation totale a gagné ou perdu 1,1 %
#: d'un instantané au suivant — 29 Md$ — pendant que celle du bitcoin ne
#: bougeait pas, et la dominance sautait de 0,65 point pour rien : près de
#: la moitié du seuil de l'alerte de glissement. L'écart est la taille de
#: `FIGR_HELOC`, un fonds de prêts immobiliers mis en jetons, que la source
#: range tantôt parmi les dix premières capitalisations, tantôt nulle part.
#: Il n'entre donc jamais dans les agrégats du terminal : ni lui ni son
#: clignotement ne disent quoi que ce soit du marché.
INTERMITTENT = {"FIGR_HELOC"}

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
    # L'ancienne adresse, `/.rss/full/`, redirigeait vers `http://…/feed` :
    # en clair, le 12 novembre 2026. La nouvelle répond en HTTPS sans détour.
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/feed"},
    {"name": "The Block", "url": "https://www.theblock.co/rss.xml"},
    {"name": "CryptoSlate", "url": "https://cryptoslate.com/feed/"},
]

#: Délais d'un flux RSS : connexion, puis lecture. Confiée à feedparser,
#: l'URL partait sans délai du tout — un serveur qui accepte la connexion
#: sans répondre aurait figé la collecte pour de bon.
RSS_TIMEOUT = (5, 20)


# ─────────────────────────────────────────────────────────────
# Erreurs réseau, en une ligne lisible
# ─────────────────────────────────────────────────────────────

_ERROR_HOST = re.compile(r"host='([^']+)'")
_ERROR_CAUSE = re.compile(r"Caused by (\w+)\(")
_ERROR_AFTER_POOL = re.compile(r"port=\d+\): ([^(]+?)\.? ?\(")


def brief_error(exc: BaseException, limit: int = 120) -> str:
    """Résume une erreur `requests` pour le journal du service.

    `str(exc)` d'une erreur de connexion commence par le pool et l'URL
    complète et finit par la cause — `NameResolutionError`,
    `ConnectTimeoutError` — qui seule dit ce qui s'est passé ; tronquée
    à la longueur d'une ligne de journal, il n'en restait que le
    début, identique d'une panne à l'autre. Ici : l'hôte, puis la
    cause ; sinon le message, borné.
    """
    text = str(exc) or type(exc).__name__
    host = _ERROR_HOST.search(text)
    if host is None:
        return text[:limit]
    cause = _ERROR_CAUSE.search(text)
    if cause is not None:
        return f"{host.group(1)} : {cause.group(1)}"
    tail = _ERROR_AFTER_POOL.search(text)
    return f"{host.group(1)} : {tail.group(1) if tail else text[:limit]}"


# ─────────────────────────────────────────────────────────────
# Marché — REST Binance
# ─────────────────────────────────────────────────────────────

def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1d",
    limit: int = 350,
    index: bool = False,
    end_time: Optional[int] = None,
    start_time: Optional[int] = None,
) -> pd.DataFrame:
    """Chandeliers OHLCV depuis l'API publique Binance.

    `index=False` retourne une colonne `time` et un index entier ;
    `index=True` indexe le DataFrame par l'horodatage d'ouverture.

    `end_time` (millisecondes epoch) borne la fin de la série : Binance
    renvoie alors les `limit` dernières bougies ouvertes au plus tard à
    cet instant — c'est la pagination vers le passé du panneau prix.
    `start_time` borne son début : les `limit` premières bougies ouvertes
    à partir de cet instant — c'est le rattrapage de l'historique sauvegardé.
    """
    # Binance refuse un `limit` au-delà de 1000 : la série est servie
    # tronquée plutôt que pas du tout — l'historique sur disque, lui,
    # enchaîne les pages (§ history).
    params: dict[str, Any] = {"symbol": symbol, "interval": interval,
                              "limit": min(limit, KLINES_MAX)}
    if end_time is not None:
        params["endTime"] = int(end_time)
    if start_time is not None:
        params["startTime"] = int(start_time)
    # Quatre secondes pour se connecter : une adresse morte se reconnaît
    # vite, et c'est elle que le pousseur attendrait, carnet figé.
    response = _klines_http.get(
        f"{BINANCE_REST}/klines",
        params=params,
        timeout=(4, 12),
    )
    response.raise_for_status()

    df = pd.DataFrame(response.json(), columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    for column in (*OHLCV_COLUMNS, "quote_vol", "taker_buy_base",
                   "taker_buy_quote"):
        df[column] = df[column].astype(float)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms")

    if index:
        return df.set_index("time")[OHLCV_COLUMNS]
    return df.reset_index(drop=True)


def reset_connections() -> None:
    """Oublie les connexions gardées.

    Au réveil de la machine, elles sont mortes sans que rien ne l'ait dit :
    la lecture suivante attendrait son délai entier avant de s'en apercevoir.
    """
    _klines_http.close()
    _http.close()


def fetch_ticker_24h(symbol: str = "BTCUSDT") -> dict[str, Any]:
    """Statistiques glissantes sur 24 h. Retourne `{}` en cas d'échec."""
    try:
        response = _http.get(
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
    response = _http.get(
        f"{BINANCE_REST}/depth", params={"symbol": symbol, "limit": limit}, timeout=5
    )
    response.raise_for_status()
    book = response.json()
    return (
        [(float(p), float(q)) for p, q in book["bids"]],
        [(float(p), float(q)) for p, q in book["asks"]],
    )


#: Plafond de Binance par appel à `/api/v3/klines`.
KLINES_MAX = 1000

#: Correspondance intervalle Binance → alias de fréquence pandas, pour les
#: séries de démonstration : Binance parle en `15m` et `1M`, pandas en
#: `15min` et `ME`.
KLINE_FREQ = {
    "1m": "min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "h", "4h": "4h", "6h": "6h",
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
    "1m": 1 / 60, "5m": 5 / 60, "15m": 0.25, "30m": 0.5, "1h": 1, "4h": 4, "6h": 6,
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


def fetch_eur_rate() -> float:
    """Taux USD→EUR. Lève si aucune source ne répond.

    **Le marché, pas une référence.** Les « dollars » du terminal sont
    des USDT — le cours vient de BTC/USDT —, et le seul taux qui les
    change en euros sans écart est celui du marché EUR/USDT : le 12
    novembre 2026, BTC/USDT ÷ EUR/USDT tombait à 0,001 % du carnet
    BTC/EUR de Binance, quand les taux de référence le sous-évaluaient
    de 0,3 à 0,9 % — 200 € sur le cours, affiché au centime. La source
    précédente (exchangerate-api v4) rendait de plus un taux arrondi à
    deux décimales, 0,9 pour 0,899865, et se disait périmée.

    En secours, le taux de référence de la BCE (Frankfurter v1, sans
    clé, publié chaque jour ouvré vers 16 h) : moins juste que le
    marché, mieux qu'un taux figé. Le repli ultime appartient au hub :
    une valeur figée rendue d'ici se faisait mettre en cache comme une
    lecture réussie, à la place du dernier bon taux, et sans une ligne
    de journal.
    """
    try:
        response = _http.get(FX_MARKET_URL, params={"symbol": "EURUSDT"},
                             timeout=5)
        response.raise_for_status()
        price = float(response.json()["price"])
        # Un euro entre 0,5 et 2 dollars : une réponse hors de cette
        # plage n'est pas un cours, c'est une panne qui a pris sa forme.
        if not 0.5 < price < 2.0:
            raise ValueError(f"EUR/USDT {price} hors de toute plage plausible")
        return 1.0 / price
    except Exception as marche:  # noqa: BLE001 — le secours est là pour ça
        try:
            response = _http.get(FX_REFERENCE_URL,
                                 params={"base": "USD", "symbols": "EUR"},
                                 timeout=5)
            response.raise_for_status()
            return float(response.json()["rates"]["EUR"])
        except Exception as reference:  # noqa: BLE001
            raise RuntimeError(f"marché EUR/USDT : {brief_error(marche)} ; "
                               f"référence BCE : {brief_error(reference)}"
                               ) from reference


def fetch_upbit_quotes() -> dict[str, float]:
    """Le dernier prix du bitcoin et de l'USDT en wons chez Upbit.

    Rend `btc` et `usdt` (₩) et `at`, l'instant de la lecture — pas celui
    du dernier échange, que le ticker date aussi : la fraîcheur qui compte
    est celle de la lecture, comparée au cours mondial du moment. Lève
    sans les deux prix : l'un sans l'autre ne décompose pas la prime.
    """
    response = _http.get(UPBIT_TICKER, params={"markets": "KRW-BTC,KRW-USDT"},
                         timeout=8)
    response.raise_for_status()
    prices = {row.get("market"): float(row["trade_price"])
              for row in response.json() if row.get("trade_price")}
    btc, usdt = prices.get("KRW-BTC"), prices.get("KRW-USDT")
    if not btc or not usdt or btc <= 0 or usdt <= 0:
        raise ValueError("Upbit : KRW-BTC ou KRW-USDT absent de la réponse")
    return {"btc": btc, "usdt": usdt, "at": time.time()}


def fetch_krw_rate() -> dict[str, Any]:
    """Wons pour un dollar : `rate`, et `source` — « marché » ou « BCE ».

    Le marché d'abord : le cours de Yahoo (`KRW=X`) suit la séance, quand
    la référence de la BCE ne se publie qu'une fois par jour ouvré — le
    won bouge d'un demi-pour-cent dans une journée agitée, l'ordre de
    grandeur de la prime elle-même. La référence en secours (Frankfurter
    v1, sans clé). Lève si aucune ne répond, ou ne répond rien de
    plausible (`KRW_PLAUSIBLE`).
    """
    low, high = KRW_PLAUSIBLE
    try:
        response = _http.get(f"{YAHOO_CHART}/{KRW_MARKET_SYMBOL}",
                             params={"range": "1d", "interval": "5m"},
                             headers=YAHOO_HEADERS, timeout=8)
        response.raise_for_status()
        meta = response.json()["chart"]["result"][0]["meta"]
        rate = float(meta["regularMarketPrice"])
        if not low < rate < high:
            raise ValueError(f"USD/KRW {rate} hors de toute plage plausible")
        return {"rate": rate, "source": "marché"}
    except Exception as marche:  # noqa: BLE001 — le secours est là pour ça
        try:
            response = _http.get(FX_REFERENCE_URL,
                                 params={"base": "USD", "symbols": "KRW"},
                                 timeout=8)
            response.raise_for_status()
            rate = float(response.json()["rates"]["KRW"])
            if not low < rate < high:
                raise ValueError(f"USD/KRW {rate} hors de toute plage plausible")
            return {"rate": rate, "source": "BCE"}
        except Exception as reference:  # noqa: BLE001
            raise RuntimeError(f"marché USD/KRW : {brief_error(marche)} ; "
                               f"référence BCE : {brief_error(reference)}"
                               ) from reference


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
    response = _http.get(
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


def _deribit(method: str, params: dict, timeout: float = 10) -> Any:
    response = _http.get(f"{DERIBIT_REST}/{method}", params=params,
                            timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if "result" not in payload:
        raise RuntimeError(f"Deribit {method} : {payload.get('error')}")
    return payload["result"]


def fetch_deribit_summary(kind: str = "option",
                          currency: str = "BTC") -> list[dict[str, Any]]:
    """Le résumé de chaque instrument coté : `kind` vaut `option` ou `future`.

    Une ligne par instrument — nom, prix de marque, open interest, volume,
    et pour les options la volatilité de marque (`mark_iv`, en pour cent)
    et le prix du future de l'échéance (`underlying_price`). Une liste vide
    lève : le cache la stockerait sinon comme une lecture réussie.
    """
    rows = _deribit("get_book_summary_by_currency",
                    {"currency": currency, "kind": kind})
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"Deribit : aucun instrument {kind}")
    return rows


def fetch_dvol(days: int = 365, currency: str = "BTC") -> pd.DataFrame:
    """DVOL, l'indice de volatilité implicite à trente jours de Deribit.

    Bougies journalières, `time` / `open` / `high` / `low` / `close`, en
    points de volatilité annualisée. C'est le VIX du bitcoin : la
    volatilité que les options attendent pour le mois qui vient.
    """
    end = int(time.time() * 1000)
    result = _deribit("get_volatility_index_data", {
        "currency": currency, "resolution": "1D",
        "start_timestamp": end - days * 86_400_000, "end_timestamp": end,
    })
    data = result.get("data") or []
    if not data:
        raise RuntimeError("Deribit : DVOL vide")
    frame = pd.DataFrame(data, columns=["time", "open", "high", "low", "close"])
    frame["time"] = pd.to_datetime(frame["time"], unit="ms")
    return frame.sort_values("time").reset_index(drop=True)


def fetch_yahoo_daily(symbol: str, span: str = "2y") -> pd.DataFrame:
    """Clôtures quotidiennes d'un marché coté : `time` (date de la séance,
    à l'heure de sa place) / `close`.

    La dernière ligne est la séance en cours tant que la place est ouverte.
    Les séances sans cotation — le DXY en compte une sur six — sont
    écartées. Une série vide lève : le cache la stockerait sinon comme une
    lecture réussie.
    """
    response = _http.get(f"{YAHOO_CHART}/{symbol}",
                            params={"range": span, "interval": "1d"},
                            headers=YAHOO_HEADERS, timeout=10)
    response.raise_for_status()
    result = ((response.json().get("chart") or {}).get("result") or [None])[0]
    if not result or not result.get("timestamp"):
        raise RuntimeError(f"Yahoo : aucune série pour {symbol}")
    zone = (result.get("meta") or {}).get("exchangeTimezoneName") or "UTC"
    closes = result["indicators"]["quote"][0].get("close") or []
    frame = pd.DataFrame({
        "time": (pd.to_datetime(result["timestamp"], unit="s", utc=True)
                 .tz_convert(zone).tz_localize(None).normalize()),
        "close": pd.to_numeric(pd.Series(closes, dtype="object"),
                               errors="coerce"),
    }).dropna()
    if frame.empty:
        raise RuntimeError(f"Yahoo : série vide pour {symbol}")
    return (frame.drop_duplicates("time", keep="last")
            .sort_values("time").reset_index(drop=True))


def fetch_chain_stats() -> dict[str, Any]:
    """Instantané du réseau : hashrate, difficulté, rythme des blocs.

    `hash_rate` est en GH/s chez blockchain.info — la conversion en EH/s
    appartient à l'appelant, comme pour les séries.

    Le champ `total_fees_btc` de la source est ignoré : il revient
    négatif, donc inexploitable.
    """
    response = _http.get(BLOCKCHAIN_STATS, timeout=15)
    response.raise_for_status()
    data = response.json()

    return {
        "hash_rate_ghs": float(data.get("hash_rate", 0)),
        "difficulty": float(data.get("difficulty", 0)),
        "minutes_between_blocks": float(data.get("minutes_between_blocks", 0)),
        "n_tx": int(data.get("n_tx", 0)),
        # Hauteur de la chaîne : le compte à rebours du halving (§ cycle).
        "height": int(data.get("n_blocks_total", 0)),
    }


def fetch_block_time(height: int) -> int:
    """L'heure (epoch) du bloc à cette hauteur, chez blockstream.info."""
    response = _http.get(f"{BLOCKSTREAM_API}/block-height/{int(height)}",
                         timeout=10)
    response.raise_for_status()
    block_hash = response.text.strip()
    response = _http.get(f"{BLOCKSTREAM_API}/block/{block_hash}", timeout=10)
    response.raise_for_status()
    return int(response.json()["timestamp"])


def fetch_mvrv(start: str = "2012-01-01") -> pd.DataFrame:
    """Le MVRV du bitcoin, son cours et l'émission, un point par jour, chez
    Coin Metrics.

    `CapMVRVCur` — capitalisation de marché sur capitalisation réalisée —
    est libre ; la capitalisation réalisée elle-même ne l'est pas, mais le
    prix réalisé s'en déduit (`price / mvrv`). `IssTotUSD` — la valeur en
    dollars des bitcoins créés ce jour-là, subvention de bloc seule — nourrit
    le multiple de Puell (§ cycle.puell) ; les revenus des mineurs frais
    compris (`RevUSD`) ne sont pas dans l'offre libre. Retourne `time`,
    `mvrv`, `price`, `issuance` ; lève si la source ne répond pas.
    """
    params = {"assets": "btc", "metrics": "CapMVRVCur,PriceUSD,IssTotUSD",
              "frequency": "1d", "start_time": start, "page_size": 10_000}
    rows: list[dict] = []
    url: Optional[str] = COINMETRICS_API
    for _ in range(4):
        response = _http.get(url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        rows += payload.get("data", [])
        url, params = payload.get("next_page_url"), None
        if not url:
            break
    frame = pd.DataFrame(rows)
    if frame.empty or "CapMVRVCur" not in frame:
        raise ValueError("réponse vide de Coin Metrics")
    def column(name: str) -> pd.Series:
        # Une métrique absente de toute la réponse : une colonne vide,
        # pas une erreur — le MVRV n'a pas à tomber avec l'émission.
        if name not in frame:
            return pd.Series(np.nan, index=frame.index)
        return pd.to_numeric(frame[name], errors="coerce")

    frame = pd.DataFrame({
        "time": pd.to_datetime(frame["time"]).dt.tz_localize(None),
        "mvrv": column("CapMVRVCur"),
        "price": column("PriceUSD"),
        "issuance": column("IssTotUSD"),
    })
    return frame.dropna(subset=["mvrv"]).sort_values("time").reset_index(drop=True)


def fetch_fee_estimates() -> dict[str, float]:
    """Ce que coûte une transaction, en sat/vB, selon le délai visé.

    Les frais disent la demande d'espace dans les blocs : nuls quand la
    chaîne est vide, ils s'envolent quand tout le monde veut bouger ses
    bitcoins à la fois — vers les plateformes, souvent. Blockstream les
    estime par nombre de blocs d'attente, au centième de satoshi, là où
    d'autres arrondissent à l'unité un marché qui vit sous le satoshi.

    Retourne `next` (prochain bloc), `hour` (six blocs) et `day` (144).
    """
    response = _http.get(BLOCKSTREAM_FEES, timeout=12)
    response.raise_for_status()
    return fee_estimates(response.json())


def fee_estimates(data: dict) -> dict[str, float]:
    """Les trois horizons utiles d'une table `{blocs: sat/vB}` ; une table
    sans prochain bloc ne vaut rien et lève."""
    table = {int(blocks): float(rate) for blocks, rate in data.items()}
    if not table:
        raise RuntimeError("estimations de frais vides")

    def at(target: int) -> float:
        # L'horizon demandé, ou le plus proche que la table donne.
        return table[min(table, key=lambda blocks: abs(blocks - target))]

    return {"next": at(1), "hour": at(6), "day": at(144)}


def fetch_market_global() -> dict[str, Any]:
    """Capitalisation totale, dominance et volume — instantané.

    CoinGecko ne donne l'historique de ces agrégats que sur son offre
    payante : ce collecteur rapporte l'état du moment, et le panneau qui
    l'utilise le dit plutôt que de faire croire à une série.

    Retourne `{}` en cas d'échec, les parts étant en pourcentage.
    """
    response = _http.get(COINGECKO_GLOBAL, timeout=12)
    response.raise_for_status()
    return market_global(response.json()["data"])


def market_global(data: dict) -> dict[str, Any]:
    """Les agrégats de CoinGecko, ramenés à un univers qui ne clignote pas.

    Les actifs d'`INTERMITTENT` sont retirés de la capitalisation totale
    et les parts recalculées sur ce qui reste ; `excluded` dit la part
    retirée, en pourcentage de la capitalisation annoncée — zéro quand la
    source ne les comptait pas. La variation sur 24 h reste celle de la
    source, qui ne dit pas sur quel univers elle l'a calculée.
    """
    shares = {k.upper(): float(v)
              for k, v in data["market_cap_percentage"].items()}
    excluded = sum(shares.pop(name) for name in list(shares)
                   if name in INTERMITTENT)
    keep = 1 - excluded / 100
    return {
        "total_cap_usd": float(data["total_market_cap"]["usd"]) * keep,
        "total_volume_usd": float(data["total_volume"]["usd"]),
        "cap_change_24h": float(data.get("market_cap_change_percentage_24h_usd", 0)),
        "shares": {name: part / keep for name, part in shares.items()},
        "excluded": excluded,
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
    response = _http.get(
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
    response = _http.get(
        f"{BINANCE_FUTURES}/futures/data/openInterestHist",
        params={"symbol": symbol, "period": period, "limit": limit},
        timeout=12,
    )
    response.raise_for_status()

    df = pd.DataFrame(response.json())
    if df.empty:
        # Binance ne répond jamais vide pour une paire vivante : un
        # tableau vide est une réponse de restriction, pas une donnée.
        # Lever laisse le cache servir la dernière valeur, et le dire.
        raise ValueError("réponse vide de Binance Futures")
    return pd.DataFrame({
        "time": pd.to_datetime(df["timestamp"], unit="ms"),
        "oi": df["sumOpenInterest"].astype(float),
        "oi_usd": df["sumOpenInterestValue"].astype(float),
    })


def fetch_long_short(
    symbol: str = "BTCUSDT", period: str = "4h", limit: int = 180
) -> pd.DataFrame:
    """La part des longs, la foule contre les gros, en % — trente jours.

    Deux séries de Binance Futures, au même pas :

    - `crowd` — `globalLongShortAccountRatio` : la part des **comptes**
      positionnés à la hausse, tous comptes confondus ;
    - `top` — `topLongShortPositionRatio` : la part des **positions** à la
      hausse chez les gros comptes, les 20 % à la plus forte marge —
      pondérée par la taille, donc par l'argent engagé.

    Le 12 novembre 2026 : 74,8 % de la foule à la hausse, 69,5 % de
    l'argent des gros — qui, comptés par tête, étaient 77,0 %. Retourne
    `time` / `crowd` / `top` ; une série absente reste à NaN, l'autre
    passe. Lève si les deux manquent : un vide se ferait mettre en cache.
    """
    series = {}
    errors = []
    for name, path in (("crowd", "globalLongShortAccountRatio"),
                       ("top", "topLongShortPositionRatio")):
        try:
            response = _http.get(
                f"{BINANCE_FUTURES}/futures/data/{path}",
                params={"symbol": symbol, "period": period, "limit": limit},
                timeout=12)
            response.raise_for_status()
            rows = response.json()
            if not rows:
                raise ValueError("réponse vide")
            series[name] = pd.Series(
                [float(row["longAccount"]) * 100 for row in rows],
                index=pd.to_datetime([row["timestamp"] for row in rows],
                                     unit="ms"))
        except Exception as exc:  # noqa: BLE001 — l'autre série passe
            errors.append(f"{path} : {brief_error(exc, 60)}")
    if not series:
        raise RuntimeError("Binance Futures injoignable — " + " ; ".join(errors))
    frame = pd.DataFrame(series).sort_index()
    for name in ("crowd", "top"):
        if name not in frame:
            frame[name] = np.nan
    frame.index.name = "time"
    return frame.reset_index()[["time", "crowd", "top"]]


def fetch_bybit_perp(symbol: str = "BTCUSDT") -> dict[str, float]:
    """Financement, prix marqué et indice, open interest du perpétuel Bybit."""
    response = _http.get(
        f"{BYBIT_REST}/v5/market/tickers",
        params={"category": "linear", "symbol": symbol}, timeout=8,
    )
    response.raise_for_status()
    row = response.json()["result"]["list"][0]
    return {"funding_rate": float(row["fundingRate"]),
            "mark_price": float(row["markPrice"]),
            "index_price": float(row["indexPrice"]),
            "oi_usd": float(row["openInterestValue"])}


def _okx(path: str, **params: str) -> dict[str, Any]:
    response = _http.get(f"{OKX_REST}/api/v5/{path}", params=params, timeout=8)
    response.raise_for_status()
    return response.json()["data"][0]


def fetch_binance_tradfi_symbols() -> frozenset[str]:
    """Les perpétuels de Binance qui ne portent pas sur des cryptomonnaies.

    Comme OKX, Binance cote des contrats sur actions et matières premières
    — 234 de ses 946 contrats le 11 novembre 2026, sous le type
    `TRADIFI_PERPETUAL` —, et son flux de liquidations diffuse tous les
    symboles. Un catalogue vide lève : il ne dirait pas « aucun ».
    """
    response = _http.get(f"{BINANCE_FUTURES}/fapi/v1/exchangeInfo", timeout=20)
    response.raise_for_status()
    return binance_tradfi_symbols(response.json().get("symbols") or [])


def binance_tradfi_symbols(symbols: list) -> frozenset[str]:
    if not symbols:
        raise RuntimeError("Binance Futures : catalogue de contrats vide")
    return frozenset(
        item["symbol"] for item in symbols
        if str(item.get("contractType", "")).startswith("TRADIFI")
        or "TradFi" in (item.get("underlyingSubType") or []))


#: La catégorie qu'OKX donne à ses contrats sur cryptomonnaies
#: (`instCategory`) ; « 3 » désigne les actions, « 4 » les matières premières.
OKX_CRYPTO = "1"


def fetch_okx_swap_contracts() -> dict[str, Optional[tuple[float, str]]]:
    """La valeur de chaque contrat perpétuel d'OKX : `instId` → (`ctVal`,
    `ctValCcy`). Un contrat linéaire vaut `ctVal` unités du sous-jacent, un
    inverse `ctVal` dollars. Une table vide lève.

    **Un perpétuel n'est plus forcément une cryptomonnaie.** Le
    11 novembre 2026, 239 des 543 perpétuels d'OKX portaient sur des
    actions ou des matières premières — Micron, SanDisk, des ETF à levier,
    l'or —, et un tiers de ses liquidations journalisées en quatre jours
    (10 M$ sur 32) venaient d'eux : la plus grosse « liquidation du
    marché » relue au journal était une position sur SanDisk. Ces contrats
    sont rendus à `None` : connus, donc pas à redemander, mais hors du
    fil. Un contrat sans catégorie reste compté.
    """
    response = _http.get(f"{OKX_REST}/api/v5/public/instruments",
                            params={"instType": "SWAP"}, timeout=15)
    response.raise_for_status()
    return okx_swap_contracts(response.json().get("data") or [])


def okx_swap_contracts(items: list) -> dict[str, Optional[tuple[float, str]]]:
    contracts = {
        item["instId"]: (
            (float(item["ctVal"]), item["ctValCcy"])
            if item.get("instCategory", OKX_CRYPTO) in (OKX_CRYPTO, "", None)
            else None)
        for item in items if item.get("ctVal")}
    if not any(contracts.values()):
        raise RuntimeError("OKX : aucun contrat perpétuel")
    return contracts


def fetch_okx_perp(inst_id: str = "BTC-USDT-SWAP") -> dict[str, float]:
    """La même chose chez OKX, qui répartit ces champs sur quatre endpoints."""
    return {
        "funding_rate": float(_okx("public/funding-rate",
                                   instId=inst_id)["fundingRate"]),
        "mark_price": float(_okx("public/mark-price", instType="SWAP",
                                 instId=inst_id)["markPx"]),
        "index_price": float(_okx("market/index-tickers",
                                  instId=inst_id.removesuffix("-SWAP"))["idxPx"]),
        "oi_usd": float(_okx("public/open-interest", instType="SWAP",
                             instId=inst_id)["oiUsd"]),
    }


def fetch_perp_snapshot(symbol: str = "BTCUSDT") -> dict[str, Any]:
    """Instantané du perpétuel : prix marqué, financement courant, ratio.

    Réunit deux endpoints — `premiumIndex` pour le prix marqué et le
    prochain financement, `globalLongShortAccountRatio` pour la part des
    comptes longs. Chaque partie manquante est simplement absente du
    résultat : un ratio indisponible ne doit pas priver le panneau du
    financement. Mais un résultat vide — les deux en panne — lève : un
    vide rendu comme une valeur se ferait mettre en cache et passerait
    pour une lecture réussie, six heures durant s'il le faut.
    """
    snapshot: dict[str, Any] = {}
    errors: list[str] = []
    try:
        response = _http.get(
            f"{BINANCE_FUTURES}/fapi/v1/premiumIndex",
            params={"symbol": symbol}, timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        snapshot["mark_price"] = float(data["markPrice"])
        snapshot["index_price"] = float(data["indexPrice"])
        snapshot["funding_rate"] = float(data["lastFundingRate"])
        snapshot["next_funding"] = int(data["nextFundingTime"])
    except Exception as exc:  # noqa: BLE001 — partie manquante, pas panne
        errors.append(f"premiumIndex : {brief_error(exc, 60)}")

    try:
        response = _http.get(
            f"{BINANCE_FUTURES}/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": "1h", "limit": 1}, timeout=8,
        )
        response.raise_for_status()
        row = response.json()[0]
        snapshot["long_account"] = float(row["longAccount"])
        snapshot["long_short_ratio"] = float(row["longShortRatio"])
    except Exception as exc:  # noqa: BLE001 — partie manquante, pas panne
        errors.append(f"ratio : {brief_error(exc, 60)}")

    if not snapshot:
        raise RuntimeError("Binance Futures injoignable — " + " ; ".join(errors))
    return snapshot


def fetch_m2_supply(start: str = "2013-01-01") -> pd.DataFrame:
    """Masse monétaire M2 des États-Unis, en milliards de dollars.

    Série mensuelle désaisonnalisée, publiée avec environ deux mois de
    retard — c'est le rythme de la donnée, pas un défaut de collecte.

    Retourne un DataFrame `time` / `m2` trié, tronqué à `start` : la
    série remonte à 1959, alors que le Bitcoin ne cote que depuis 2010.
    """
    response = _http.get(M2_URL, params={"observations": "true"}, timeout=20)
    response.raise_for_status()

    document = response.json()["series"]["docs"][0]
    df = pd.DataFrame({
        "time": pd.to_datetime(document["period_start_day"]),
        "m2": pd.to_numeric(document["value"], errors="coerce"),
    })
    df = df.dropna().sort_values("time")
    return df[df["time"] >= pd.Timestamp(start)].reset_index(drop=True)


STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoincharts/all"


def fetch_stablecoin_supply(start: str = "2018-01-01") -> pd.DataFrame:
    """L'offre de stablecoins adossés au dollar, en milliards de dollars.

    La liquidité du marché lui-même, là où M2 dit celle du dollar : les
    stablecoins sont le cash avec lequel on achète du bitcoin sur les
    plateformes, et leur encours monte quand de l'argent entre. DefiLlama
    en sert l'historique quotidien, sans clé — vérifié le 11 novembre
    2026 : 291 Md$, contre 306 un mois plus tôt.

    Retourne `time` / `supply`, un point par jour, trié.
    """
    response = _http.get(STABLECOINS_URL, timeout=20)
    response.raise_for_status()
    return stablecoin_supply(response.json(), start)


def stablecoin_supply(rows: list, start: str = "2018-01-01") -> pd.DataFrame:
    """La série de DefiLlama réduite au dollar ; vide, elle lève."""
    points = [(int(row["date"]),
               float((row.get("totalCirculatingUSD") or {}).get("peggedUSD") or 0))
              for row in rows or [] if row.get("date")]
    frame = pd.DataFrame(points, columns=["time", "supply"])
    frame = frame[frame["supply"] > 0]
    if frame.empty:
        raise RuntimeError("DefiLlama : série de stablecoins vide")
    frame["time"] = pd.to_datetime(frame["time"], unit="s").dt.normalize()
    frame["supply"] = frame["supply"] / 1e9
    frame = (frame.drop_duplicates("time", keep="last").sort_values("time"))
    return frame[frame["time"] >= pd.Timestamp(start)].reset_index(drop=True)


def fetch_etf_flows() -> pd.DataFrame:
    """Flux quotidiens des ETF Bitcoin spot américains, en millions de $.

    Source : le tableau public de farside.co.uk, dont on retient la table
    la plus haute de la page. Les montants négatifs y sont notés entre
    parenthèses selon la convention comptable.
    """
    response = _http.get(FARSIDE_URL, headers=BROWSER_HEADERS, timeout=15)
    response.raise_for_status()

    tables = pd.read_html(io.StringIO(response.text))
    if not tables:
        raise RuntimeError("Aucun tableau trouvé sur la page.")
    return clean_etf_table(max(tables, key=lambda t: t.shape[0]))


def clean_etf_table(table: pd.DataFrame) -> pd.DataFrame:
    """Le tableau de la page, réduit à ses séances publiées.

    **Une ligne n'est pas une séance.** La source pose la ligne du jour
    avant d'avoir un seul chiffre, et garde celle des jours fériés : des
    tirets partout, que la lecture changeait en zéros. Constaté le
    11 novembre 2026 : la synthèse annonçait « +0 M$ le 10/11 », le
    graphique une barre nulle, et les « cinq séances » comptaient ce zéro
    à la place d'un vrai jour — comme seize jours fériés depuis 2024. Une
    ligne dont plus de la moitié des fonds n'ont rien publié est écartée ;
    les tirets isolés (un fonds pas encore lancé, un émetteur en retard
    d'un soir) restent des zéros, et la ligne se complète d'elle-même.

    **Une ligne gardée n'est pas forcément complète.** Les petits fonds
    publient tôt, souvent un 0,0 ; IBIT, qui fait d'ordinaire l'essentiel
    du flux, publie parmi les derniers. Constaté le 12 novembre 2026 au
    matin : la ligne du 11 portait neuf chiffres sur douze, IBIT, FBTC et
    HODL encore en tirets, et le terminal annonçait « −30 M$ le 11/11 »
    comme le flux de la séance. Les fonds de la **dernière** ligne restés
    en tiret alors qu'ils avaient publié la veille sont donc nommés dans
    `attrs["pending"]` (voir `etf_pending`) : la ligne reste, puisqu'elle
    dit déjà quelque chose, mais chaque affichage la dit partielle.
    """
    df = table.copy()

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

    funds = [c for c in df.columns[1:] if c != "Total"]
    if funds:
        published = df[funds].notna().sum(axis=1) * 2 >= len(funds)
        df = df[published].reset_index(drop=True)
    pending: list[str] = []
    if funds and len(df) >= 2:
        last, previous = df[funds].iloc[-1], df[funds].iloc[-2]
        pending = [f for f in funds
                   if pd.isna(last[f]) and pd.notna(previous[f])]
    df = df.fillna(0.0)
    df.attrs["pending"] = pending
    return df


def etf_pending(frame: Optional[pd.DataFrame]) -> list[str]:
    """Les fonds que la dernière ligne attend encore — vide si elle est
    complète, ou si le tableau ne vient pas de `clean_etf_table`."""
    if frame is None:
        return []
    return list(frame.attrs.get("pending", ()))


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

    Le flux est lu par `requests`, puis seulement analysé par feedparser.
    Celui-ci, à qui l'URL était confiée, ne lève rien quand le réseau
    manque : il rend un flux vide marqué `bozo`, et un site en panne
    passait pour un site sans nouvelles — `on_error` n'était jamais
    appelé. Un statut HTTP d'erreur lève donc, et une réponse dont
    feedparser ne tire aucun article alors qu'il la juge mal formée
    aussi ; un flux bien formé mais vide reste un flux vide.

    La requête garde l'identité que feedparser envoyait quand il lisait
    lui-même l'URL : changer de lecteur ne change pas la façon dont le
    terminal se présente aux sites.
    """
    import feedparser  # dépendance optionnelle

    entries: list[dict[str, str]] = []
    for feed_info in feeds if feeds is not None else RSS_FEEDS:
        try:
            response = _http.get(feed_info["url"], timeout=RSS_TIMEOUT,
                                 headers={"User-Agent": feedparser.USER_AGENT})
            response.raise_for_status()
            feed = feedparser.parse(response.content, response_headers={
                "content-type": response.headers.get("content-type", "")})
            if not feed.entries and feed.bozo:
                raise ValueError(f"flux illisible ({feed.get('bozo_exception')})")
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

    response = _http.get(
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


def fetch_fear_greed_now() -> dict[str, Any]:
    """Indice Fear & Greed du jour ; l'erreur remonte, pour être nommée."""
    response = _http.get(FEAR_GREED_URL, params={"limit": 1}, timeout=8)
    response.raise_for_status()
    data = response.json()["data"][0]
    return {"value": int(data["value"]), "label": data["value_classification"]}


def fetch_fear_greed() -> Optional[dict[str, Any]]:
    """Indice Fear & Greed du jour, ou `None` si la source est injoignable."""
    try:
        return fetch_fear_greed_now()
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
    response = _http.get(FEAR_GREED_URL, params={"limit": days}, timeout=10)
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
