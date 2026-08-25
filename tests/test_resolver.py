#!/usr/bin/env python3
"""
Vérifie la résolution DNS de secours (btcterm/resolver.py), sans réseau.

Le résolveur d'un fournisseur d'accès qui censure ne dit pas « inconnu » :
il répond 127.0.0.1, et la connexion échoue sur place sans qu'on
comprenne pourquoi. L'enveloppe posée sur `socket.getaddrinfo` doit :

- laisser passer tel quel tout ce qui n'est pas suspect — un nom
  correctement résolu, une adresse littérale, `localhost` ;
- reconnaître l'empoisonnement (toutes les adresses de bouclage ou
  nulles, ou un NXDOMAIN) et redemander le nom au résolveur DoH ;
- reconstruire des entrées `getaddrinfo` de forme normale à partir des
  adresses obtenues, en respectant le type de socket demandé ;
- garder la réponse en cache le temps de son TTL, borné ;
- rendre la réponse du système si aucun résolveur DoH ne répond, et
  relever l'erreur d'origine dans le cas d'un NXDOMAIN ;
- se poser et se retirer proprement (idempotence des deux gestes).

Le résolveur DoH est remplacé par une fonction locale ; le
`getaddrinfo` d'origine par une table.

Lancement :
    python tests/test_resolver.py
"""

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm import resolver  # noqa: E402

VRAI_GETADDRINFO = socket.getaddrinfo
ORIG_FETCH = resolver._fetch_doh


def table(reponses):
    """Un `getaddrinfo` factice : nom → adresses, ou None pour NXDOMAIN."""
    def fake(host, port, family=0, type=0, proto=0, flags=0):
        name = host.decode() if isinstance(host, bytes) else host
        try:
            addrs = reponses[name]
        except KeyError:
            # Adresse littérale (reconstruction) : le vrai getaddrinfo,
            # qui ne touche pas au DNS pour un littéral.
            return VRAI_GETADDRINFO(host, port, family, type, proto, flags)
        if addrs is None:
            raise socket.gaierror(socket.EAI_NONAME, "nom inconnu")
        out = []
        for a in addrs:
            fam = socket.AF_INET6 if ":" in a else socket.AF_INET
            for t in ([type] if type else [socket.SOCK_STREAM, socket.SOCK_DGRAM]):
                out.append((fam, t, 0, "", (a, port)))
        return out
    return fake


class Compteur:
    def __init__(self, reponses):
        self.reponses = reponses
        self.appels = []

    def __call__(self, endpoint, host):
        self.appels.append((endpoint, host))
        rep = self.reponses.get((endpoint, host))
        if rep is None:
            raise ConnectionError("résolveur injoignable")
        return rep


def poser(reponses):
    resolver.uninstall()
    socket.getaddrinfo = table(reponses)
    resolver.install()


def deposer():
    resolver.uninstall()
    socket.getaddrinfo = VRAI_GETADDRINFO


def adresses(infos):
    return sorted({i[4][0] for i in infos})


def test_diagnostic():
    ok = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.2.3.4", 443))]
    poison = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))]
    nul = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("0.0.0.0", 443))]
    mixte = ok + poison
    assert not resolver.looks_poisoned("api.binance.com", ok)
    assert resolver.looks_poisoned("api.binance.com", poison)
    assert resolver.looks_poisoned("api.binance.com", nul)
    assert resolver.looks_poisoned("api.binance.com", [])
    assert not resolver.looks_poisoned("api.binance.com", mixte)
    assert not resolver.looks_poisoned("localhost", poison)
    assert not resolver.looks_poisoned("imprimante.local", poison)
    assert not resolver.looks_poisoned("127.0.0.1", poison)
    assert not resolver.looks_poisoned("::1", [(socket.AF_INET6, 0, 0, "", ("::1", 0))])


def test_passage_tel_quel():
    poser({"api.kraken.com": ["104.17.185.205"], "localhost": ["127.0.0.1"]})
    try:
        doh = Compteur({})
        resolver._fetch_doh = doh
        assert adresses(socket.getaddrinfo("api.kraken.com", 443)) == ["104.17.185.205"]
        assert adresses(socket.getaddrinfo("localhost", 80)) == ["127.0.0.1"]
        assert adresses(socket.getaddrinfo("10.0.0.7", 80)) == ["10.0.0.7"]
        assert doh.appels == [], "un nom sain ne doit pas déclencher de DoH"
    finally:
        resolver._fetch_doh = ORIG_FETCH
        deposer()


def test_empoisonnement_et_reconstruction():
    poser({"api.binance.com": ["127.0.0.1"]})
    ep1, ep2 = resolver.DOH_ENDPOINTS[:2]
    doh = Compteur({(ep1, "api.binance.com"): (["108.158.2.161", "108.158.2.9"], 60.0)})
    orig = resolver._fetch_doh
    resolver._fetch_doh = doh
    try:
        infos = socket.getaddrinfo("api.binance.com", 443, type=socket.SOCK_STREAM)
        assert adresses(infos) == ["108.158.2.161", "108.158.2.9"]
        assert all(i[1] == socket.SOCK_STREAM for i in infos), "le type demandé est respecté"
        assert all(i[4][1] == 443 for i in infos)
        # Nom en bytes, comme certains clients le passent.
        assert adresses(socket.getaddrinfo(b"api.binance.com", 443)) == ["108.158.2.161", "108.158.2.9"]
        # Cache : un seul appel DoH pour ces deux résolutions.
        assert doh.appels == [(ep1, "api.binance.com")]
    finally:
        resolver._fetch_doh = orig
        deposer()


def test_second_resolveur_puis_echec():
    poser({"fapi.binance.com": ["127.0.0.1"], "fstream.binance.com": None})
    ep1, ep2 = resolver.DOH_ENDPOINTS[:2]
    doh = Compteur({(ep2, "fapi.binance.com"): (["13.35.36.9"], 5.0)})
    orig = resolver._fetch_doh
    resolver._fetch_doh = doh
    try:
        assert adresses(socket.getaddrinfo("fapi.binance.com", 443)) == ["13.35.36.9"]
        assert doh.appels == [(ep1, "fapi.binance.com"), (ep2, "fapi.binance.com")]
        # TTL 5 s → borné à TTL_MIN : encore en cache.
        assert adresses(socket.getaddrinfo("fapi.binance.com", 443)) == ["13.35.36.9"]
        assert len(doh.appels) == 2
        # Aucun résolveur ne répond pour fstream : l'erreur d'origine remonte.
        try:
            socket.getaddrinfo("fstream.binance.com", 443)
        except socket.gaierror:
            pass
        else:
            raise AssertionError("NXDOMAIN sans secours doit lever gaierror")
    finally:
        resolver._fetch_doh = orig
        deposer()


def test_sans_secours_rend_la_reponse_du_systeme():
    poser({"api.bybit.com": ["127.0.0.1"]})
    doh = Compteur({})
    orig = resolver._fetch_doh
    resolver._fetch_doh = doh
    try:
        assert adresses(socket.getaddrinfo("api.bybit.com", 443)) == ["127.0.0.1"]
        assert len(doh.appels) == len(resolver.DOH_ENDPOINTS)
    finally:
        resolver._fetch_doh = orig
        deposer()


def test_pose_et_depose():
    deposer()
    assert not resolver.is_installed()
    resolver.install()
    assert resolver.is_installed()
    pose = socket.getaddrinfo
    resolver.install()
    assert socket.getaddrinfo is pose, "install() idempotent"
    resolver.uninstall()
    assert socket.getaddrinfo is VRAI_GETADDRINFO
    resolver.uninstall()
    assert socket.getaddrinfo is VRAI_GETADDRINFO


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"{len(tests)} tests passés")
