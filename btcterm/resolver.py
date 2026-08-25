"""
Résolution DNS de secours — contre l'empoisonnement par le fournisseur d'accès.

Dans certains pays, le résolveur du fournisseur d'accès ne refuse pas les
domaines interdits : il y répond, par une adresse de bouclage. Depuis le
Cambodge, `api.binance.com`, `fapi.binance.com`, `stream.binance.com`,
`api.bybit.com`, `ws.okx.com` ou `ws-feed.exchange.coinbase.com` résolvent
tous en `127.0.0.1` — et la connexion qui suit est refusée sur place, sans
même sortir de la machine. Les serveurs, eux, répondent normalement dès
qu'on leur parle par leur vraie adresse : seul le DNS ment.

Ce module enveloppe `socket.getaddrinfo`. Quand la réponse du système, pour
un nom public, ne contient que des adresses de bouclage ou nulles (ou quand
le nom n'existe soudain plus), il redemande le nom à un résolveur DNS sur
HTTPS joint **par son adresse IP** — 1.1.1.1 puis 8.8.8.8 —, de sorte
qu'aucune résolution empoisonnable n'entre dans la boucle. La réponse
saine est gardée le temps de son TTL. Tout le reste passe tel quel : un
hôte correctement résolu ne coûte qu'une comparaison, et un nom local
(`localhost`, `*.local`) garde le droit de valoir 127.0.0.1.

`requests` (urllib3) comme `websockets` (via `loop.getaddrinfo`) cherchent
`socket.getaddrinfo` au moment de l'appel : l'enveloppe posée par
`install()` vaut donc pour les collecteurs REST et les connecteurs
WebSocket, sans rien changer à leur code.

Le remède de fond reste de configurer le résolveur du système
(systemd-resolved avec DNS sur TLS, voir le README) ; ce module fait que le
terminal marche même sans.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from typing import Callable, Optional

import requests

__all__ = ["install", "uninstall", "is_installed", "resolve_doh",
           "looks_poisoned", "DOH_ENDPOINTS"]

log = logging.getLogger("btcterm.resolver")

#: Résolveurs DNS sur HTTPS (réponse JSON), joints par adresse IP : les
#: certificats de Cloudflare et de Google couvrent l'adresse elle-même.
DOH_ENDPOINTS: tuple[str, ...] = (
    "https://1.1.1.1/dns-query",
    "https://8.8.8.8/resolve",
)

#: Le TTL renvoyé est borné : jamais moins d'une demi-minute (ne pas
#: marteler le résolveur), jamais plus d'une heure (suivre un CDN).
TTL_MIN = 30.0
TTL_MAX = 3600.0

#: Délai d'une requête DoH. Court : elle s'ajoute au chemin d'une connexion.
DOH_TIMEOUT = 4.0

_original: Optional[Callable] = None
_lock = threading.Lock()
_cache: dict[str, tuple[float, list[str]]] = {}
_reported: set[str] = set()


# ── Diagnostic ──────────────────────────────────────────────

def _is_local_name(host: str) -> bool:
    h = host.rstrip(".").lower()
    return h == "localhost" or h.endswith((".localhost", ".local", ".home.arpa"))


def _is_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_sinkhole(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def looks_poisoned(host: str, infos: list) -> bool:
    """Vrai si la réponse du système, pour `host`, sent l'empoisonnement.

    `infos` est le résultat brut de `socket.getaddrinfo` (une liste vide
    vaut pour un NXDOMAIN). Un nom local ou une adresse littérale n'est
    jamais suspect ; un nom public dont **toutes** les adresses sont de
    bouclage ou nulles l'est.
    """
    if not host or _is_local_name(host) or _is_literal(host):
        return False
    if not infos:
        return True
    return all(_is_sinkhole(info[4][0]) for info in infos)


# ── Résolution de secours ──────────────────────────────────

def _fetch_doh(endpoint: str, host: str) -> tuple[list[str], float]:
    """Interroge un résolveur DoH ; renvoie (adresses IPv4, TTL minimal)."""
    r = requests.get(
        endpoint, params={"name": host, "type": "A"},
        headers={"accept": "application/dns-json"}, timeout=DOH_TIMEOUT,
    )
    r.raise_for_status()
    answers = r.json().get("Answer") or []
    addrs = [a["data"] for a in answers if a.get("type") == 1]
    ttls = [float(a.get("TTL", TTL_MIN)) for a in answers if a.get("type") == 1]
    addrs = [a for a in addrs if _is_literal(a) and not _is_sinkhole(a)]
    return addrs, (min(ttls) if ttls else TTL_MIN)


def resolve_doh(host: str, fetch: Optional[Callable] = None) -> list[str]:
    """Adresses IPv4 de `host` par DNS sur HTTPS, avec cache borné par TTL.

    Essaie les résolveurs de `DOH_ENDPOINTS` dans l'ordre ; une liste vide
    signifie qu'aucun n'a répondu utilement. `fetch` remplace `_fetch_doh`
    et n'existe que pour les tests.
    """
    fetch = fetch or _fetch_doh
    now = time.monotonic()
    with _lock:
        hit = _cache.get(host)
        if hit and hit[0] > now:
            return list(hit[1])
    for endpoint in DOH_ENDPOINTS:
        try:
            addrs, ttl = fetch(endpoint, host)
        except Exception as exc:  # réseau, JSON, HTTP : on passe au suivant
            log.debug("DoH %s pour %s : %s", endpoint, host, exc)
            continue
        if addrs:
            ttl = min(max(ttl, TTL_MIN), TTL_MAX)
            with _lock:
                _cache[host] = (now + ttl, addrs)
            return list(addrs)
    return []


def _rebuild(addrs: list[str], port, family, type_, proto, flags) -> list:
    """Reconstruit des entrées `getaddrinfo` à partir d'adresses littérales.

    Passer par `getaddrinfo` sur le littéral donne des tuples de la forme
    exacte attendue par l'appelant (familles, types de socket), sans
    toucher au DNS.
    """
    assert _original is not None
    out: list = []
    for addr in addrs:
        try:
            out.extend(_original(addr, port, family, type_, proto, flags))
        except socket.gaierror:
            continue
    return out


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    assert _original is not None
    name = host.decode() if isinstance(host, bytes) else host
    try:
        infos = _original(host, port, family, type, proto, flags)
    except socket.gaierror:
        infos = []
        error = True
    else:
        error = False
    if not isinstance(name, str) or not looks_poisoned(name, infos):
        if error:
            raise socket.gaierror(socket.EAI_NONAME, f"nom inconnu : {name}")
        return infos
    addrs = resolve_doh(name)
    rebuilt = _rebuild(addrs, port, family, type, proto, flags) if addrs else []
    if rebuilt:
        with _lock:
            first = name not in _reported
            _reported.add(name)
        if first:
            log.warning("résolution empoisonnée pour %s (%s) : "
                        "DoH répond %s", name,
                        infos[0][4][0] if infos else "NXDOMAIN", ", ".join(addrs))
        return rebuilt
    if error:
        raise socket.gaierror(socket.EAI_NONAME, f"nom inconnu : {name}")
    return infos


# ── Pose et dépose ─────────────────────────────────────────

def install() -> None:
    """Pose l'enveloppe sur `socket.getaddrinfo`. Idempotent."""
    global _original
    with _lock:
        if _original is not None:
            return
        _original = socket.getaddrinfo
        socket.getaddrinfo = _getaddrinfo


def uninstall() -> None:
    """Retire l'enveloppe et vide le cache. Sans effet si rien n'est posé."""
    global _original
    with _lock:
        if _original is None:
            return
        socket.getaddrinfo = _original
        _original = None
        _cache.clear()
        _reported.clear()


def is_installed() -> bool:
    return _original is not None
