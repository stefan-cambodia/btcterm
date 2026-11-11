#!/usr/bin/env python3
"""
Le cache du hub : dernière valeur de secours, fraîcheur, pannes dites.

Le journal de séance a montré six heures et demie d'instantanés sans
financement ni open interest — Binance Futures muet après un réveil,
CoinGecko répondant à côté — sans une ligne dans le journal du service.
Ce qui est vérifié ici, sans réseau :

- le cache sert sa dernière valeur quand la source tombe, mais sait
  qu'elle est de secours (`stale`) ;
- la première panne d'une source et son rétablissement laissent une
  ligne de journal, une seule chacune, pas une par lecture, et la durée
  dite au retour est celle de la panne vue, pas celle d'un oubli ;
- l'instantané de marché n'écrit que des valeurs fraîches : une source
  en panne donne NULL, pas sa valeur d'il y a six heures ;
- un collecteur qui n'a rien obtenu lève, au lieu de rendre un vide
  qui se ferait mettre en cache comme une lecture réussie ;
- le taux €/$ en panne sert le dernier taux lu — ou un repli dit de
  secours — sans réinterroger la source à chaque lecture ;
- un réveil de la machine, lu sur ses horloges, fait rattendre le
  réseau à la boucle d'observation, et les échecs de la minute qui suit
  ne comptent pas comme pannes ;
- Binance en panne est retenté après un délai qui suit la panne, et les
  lectures refusées pendant ce délai ne se disent pas en panne ;
- une connexion gardée que le réseau a rompue est rejouée une fois, sur
  une connexion neuve ; un délai dépassé ne l'est pas ;
- le cache oublie ce que personne ne lit plus — les pages d'historique,
  clés à usage unique —, mais garde le secours de ce qu'on lit encore.

Lancement :
    python tests/test_cache.py
"""

import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from btcterm import hub as hub_module, sources  # noqa: E402
from btcterm.hub import (FAILING_WINDOW, MarketHub, SleepWatch,  # noqa: E402
                         SourceBackoff, TTLCache)
from btcterm.journal import Journal  # noqa: E402


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lignes = []

    def emit(self, record):
        self.lignes.append(record.getMessage())


def test_secours_et_fraicheur():
    cache = TTLCache()
    appels = []

    def source():
        appels.append(1)
        if len(appels) in (2, 3):
            raise ConnectionError("réseau coupé")
        return len(appels)

    capture = Capture()
    hub_module.log.addHandler(capture)
    try:
        assert cache.get("k", ttl=0, producer=source) == 1 and not cache.stale("k")
        # Deux pannes : la valeur de secours est servie, dite telle,
        # et la panne journalisée une seule fois.
        assert cache.get("k", ttl=0, producer=source) == 1 and cache.stale("k")
        assert cache.get("k", ttl=0, producer=source) == 1 and cache.stale("k")
        assert len(capture.lignes) == 1 and "k en panne" in capture.lignes[0], capture.lignes
        # Rétablissement : valeur neuve, fraîche, et une ligne de plus.
        assert cache.get("k", ttl=0, producer=source) == 4 and not cache.stale("k")
        assert len(capture.lignes) == 2 and "de nouveau servie" in capture.lignes[1]
        # Le cache ne disqualifie que ce qu'il a vu tomber : une clé jamais
        # lue n'est pas de secours ; une panne sans rien à servir lève, et
        # la clé est dite de secours.
        assert not cache.stale("jamais")
        try:
            cache.get("vide", ttl=0, producer=lambda: (_ for _ in ()).throw(OSError("x")))
        except OSError:
            pass
        else:
            raise AssertionError("sans valeur de secours, la panne doit remonter")
        assert cache.stale("vide")
    finally:
        hub_module.log.removeHandler(capture)
    print("  ✓ secours servi mais non frais, panne et retour dits une fois")


def test_le_retour_ne_compte_pas_l_oubli():
    """Une source relue après un long oubli n'a pas été en panne tout ce temps.

    Le journal du service a dit « de nouveau servie après 1915 min » d'une
    source qui avait échoué une fois, puis que personne n'avait relue
    pendant trente-deux heures : son panneau était replié.
    """
    def panne():
        raise ConnectionError("injoignable")

    def echoue(cache, cle):
        try:
            cache.get(cle, 0, panne)
        except ConnectionError:
            pass

    def recule(cache, cle, debut, fin):
        cache._failing[cle] -= debut
        cache._failed_at[cle] -= fin

    cache = TTLCache()
    capture = Capture()
    hub_module.log.addHandler(capture)
    try:
        #: Relue sans relâche pendant dix minutes : la durée est celle de
        #: la panne, comme avant.
        echoue(cache, "suivie")
        recule(cache, "suivie", 600, 30)
        cache.get("suivie", 0, lambda: 1)
        assert capture.lignes[-1] == "source suivie de nouveau servie après 10 min"

        #: Un échec, puis trente-deux heures sans lecture.
        echoue(cache, "oubliee")
        recule(cache, "oubliee", 1915 * 60, 1915 * 60)
        cache.get("oubliee", 0, lambda: 1)
        assert capture.lignes[-1] == (
            "source oubliee de nouveau servie à sa première lecture depuis "
            "1915 min (panne constatée : moins d'une minute)"), capture.lignes[-1]

        #: Quatre minutes d'échecs, puis la machine dort une nuit.
        echoue(cache, "endormie")
        recule(cache, "endormie", 836 * 60, 832 * 60)
        cache.get("endormie", 0, lambda: 1)
        assert capture.lignes[-1].endswith(
            "depuis 832 min (panne constatée : 4 min)"), capture.lignes[-1]
    finally:
        hub_module.log.removeHandler(capture)
    print("  ✓ la durée dite est celle de la panne vue, pas celle de l'oubli")


def test_l_instantane_n_ecrit_que_le_frais():
    """Une source en panne donne NULL, pas sa valeur d'il y a six heures."""
    reponses = {"global": True, "oi": True, "perp": True}

    def faux_global():
        if not reponses["global"]:
            raise ConnectionError("CoinGecko")
        return {"total_cap_usd": 2.7e12, "total_volume_usd": 9e10,
                "shares": {"BTC": 59.0, "USDT": 6.0}}

    def faux_oi(symbol, period, limit):
        if not reponses["oi"]:
            raise ConnectionError("Binance")
        return pd.DataFrame({"time": [pd.Timestamp("2026-08-25")],
                             "oi": [1e5], "oi_usd": [8.4e9]})

    def faux_perp(symbol):
        if not reponses["perp"]:
            raise ConnectionError("Binance")
        return {"funding_rate": 6e-5}

    def faux_deribit(kind="option", currency="BTC"):
        raise ConnectionError("Deribit")

    def faux_dvol(days=365, currency="BTC"):
        raise ConnectionError("Deribit")

    originaux = (sources.fetch_market_global, sources.fetch_open_interest,
                 sources.fetch_perp_snapshot, sources.fetch_deribit_summary,
                 sources.fetch_dvol)
    sources.fetch_market_global = faux_global
    sources.fetch_open_interest = faux_oi
    sources.fetch_perp_snapshot = faux_perp
    # Deribit muet de bout en bout : le test porte sur les trois autres.
    sources.fetch_deribit_summary = faux_deribit
    sources.fetch_dvol = faux_dvol
    try:
        with tempfile.TemporaryDirectory() as tmp:
            hub = MarketHub(collect_news=False, keep_journal=False)
            hub.journal = Journal(Path(tmp) / "journal.db")
            hub.TTL_GLOBAL = hub.TTL_OPEN_INTEREST = hub.TTL_PERP = 0
            t0 = time.time()
            hub.record_market_snapshot(now=t0)
            # Binance tombe : CoinGecko s'écrit, le reste reste NULL —
            # alors que le cache, lui, sert encore l'OI et le financement.
            reponses["oi"] = reponses["perp"] = False
            assert hub.open_interest()["oi_usd"].iloc[-1] == 8.4e9, "secours servi au panneau"
            hub.record_market_snapshot(now=t0 + 300)
            # Tout tombe : rien ne s'écrit.
            reponses["global"] = False
            hub.record_market_snapshot(now=t0 + 600)
            rows = hub.journal.snapshots_between(0, t0 + 900)
            assert len(rows) == 2, len(rows)
            assert rows[0]["oi_usd"] == 8.4e9 and rows[0]["funding_rate"] == 6e-5
            assert rows[1]["btc_dominance"] == 59.0
            assert rows[1]["oi_usd"] is None and rows[1]["funding_rate"] is None
            hub.journal.close()
    finally:
        (sources.fetch_market_global, sources.fetch_open_interest,
         sources.fetch_perp_snapshot, sources.fetch_deribit_summary,
         sources.fetch_dvol) = originaux
    print("  ✓ NULL pour la source en panne, même quand le cache la sert encore")


def test_le_vide_vaut_panne():
    """Un collecteur sans rien lève au lieu de rendre un vide cacheable."""
    class Reponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return []

    original = sources._http.get
    sources._http.get = lambda *a, **k: Reponse()
    try:
        for fetch in (lambda: sources.fetch_open_interest("BTCUSDT"),
                      lambda: sources.fetch_perp_snapshot("BTCUSDT")):
            try:
                fetch()
            except Exception as exc:
                assert "vide" in str(exc) or "injoignable" in str(exc), exc
            else:
                raise AssertionError("un résultat vide doit lever")
    finally:
        sources._http.get = original
    print("  ✓ open interest vide et perpétuel sans réponse lèvent")


def test_les_sources_en_panne_se_disent_par_famille():
    """Une clé par intervalle au cache, une source au bandeau.

    Et une clé que personne ne relit — une page d'historique ratée une
    fois — ne reste pas affichée indéfiniment.
    """
    def panne():
        raise ConnectionError("injoignable")

    hub = MarketHub(collect_news=False, keep_journal=False)
    assert hub.failing_sources() == {}
    for cle in ("klines:1h:350", "klines:1d:365", "perp"):
        try:
            hub._cache.get(cle, 60, panne)
        except ConnectionError:
            pass
    assert set(hub.failing_sources()) == {"klines", "perp"}

    #: Le rétablissement l'efface.
    hub._cache.get("perp", 60, lambda: {"ok": 1})
    assert set(hub.failing_sources()) == {"klines"}

    #: Sans nouvel échec, la panne sort du bandeau avec sa fenêtre.
    for cle in hub._cache._failed_at:
        hub._cache._failed_at[cle] -= hub.FAILING_WINDOW + 1
    assert hub.failing_sources() == {}
    print("  ✓ regroupées par famille, effacées au retour ou avec la fenêtre")


def test_le_taux_en_cache_ne_touche_pas_au_reseau():
    """`eur_rate_cached` sert le carnet à cadence du pousseur : aucun appel,
    et un taux jamais lu n'est pas présenté comme une lecture fraîche."""
    def appel_interdit():
        raise AssertionError("appel réseau depuis eur_rate_cached")

    original = sources.fetch_eur_rate
    sources.fetch_eur_rate = appel_interdit
    try:
        hub = MarketHub(collect_news=False, keep_journal=False)
        assert hub.eur_rate_cached() == (MarketHub.EUR_FALLBACK, True)
        hub._cache.get("eur", hub.TTL_EUR, lambda: 0.9)
        assert hub.eur_rate_cached() == (0.9, False)
        # Source tombée : le dernier taux, dit de secours.
        hub._cache._stale["eur"] = True
        assert hub.eur_rate_cached() == (0.9, True)
    finally:
        sources.fetch_eur_rate = original
    print("  ✓ taux en cache servi sans réseau, secours signalé")


def test_le_taux_eur_se_dit_de_secours():
    """La source de change tombe : le dernier bon taux reste servi, dit
    de secours, et la source n'est réinterrogée qu'après `EUR_RETRY`.

    Avant, `fetch_eur_rate` rendait un taux figé au lieu de lever : il
    se faisait mettre en cache une heure comme une lecture réussie, à
    la place du dernier bon taux, et le cours en euros dérivait sans
    qu'aucun écran ne le dise.
    """
    reponses = [0.88, ConnectionError("réseau coupé")]
    appels = []

    def faux_taux():
        appels.append(1)
        reponse = reponses[min(len(appels), len(reponses)) - 1]
        if isinstance(reponse, Exception):
            raise reponse
        return reponse

    original = sources.fetch_eur_rate
    sources.fetch_eur_rate = faux_taux
    try:
        hub = MarketHub(collect_news=False, keep_journal=False)
        hub.TTL_EUR = 0
        assert hub.eur_rate() == 0.88 and not hub.eur_rate_stale()
        # Panne : le dernier bon taux, pas le repli figé.
        assert hub.eur_rate() == 0.88 and hub.eur_rate_stale()
        # Pendant EUR_RETRY, pas un appel de plus.
        for _ in range(10):
            assert hub.eur_rate() == 0.88
        assert len(appels) == 2, len(appels)
        # Délai écoulé : la source est retentée.
        hub._eur_retry_at = 0.0
        hub.eur_rate()
        assert len(appels) == 3, len(appels)

        # Démarrage hors ligne : rien en cache, le repli — dit de secours.
        froid = MarketHub(collect_news=False, keep_journal=False)
        appels.clear(); reponses[0] = ConnectionError("hors ligne")
        assert froid.eur_rate() == MarketHub.EUR_FALLBACK
        assert froid.eur_rate_stale()
    finally:
        sources.fetch_eur_rate = original
    print("  ✓ taux € en panne : dernier bon taux ou repli, dit de secours, "
          "source ménagée")


def test_la_panne_se_lit_en_une_ligne():
    """`brief_error` garde l'hôte et la cause, pas l'URL ni le pool."""
    dns = RuntimeError(
        "HTTPSConnectionPool(host='fapi.binance.com', port=443): Max retries "
        "exceeded with url: /fapi/v1/premiumIndex?symbol=BTCUSDT (Caused by "
        "NameResolutionError(\"HTTPSConnection(host='fapi.binance.com', "
        "port=443): Failed to resolve 'fapi.binance.com' ([Errno -2] Name or "
        "service not known)\"))")
    assert sources.brief_error(dns) == "fapi.binance.com : NameResolutionError"
    timeout = RuntimeError(
        "HTTPSConnectionPool(host='farside.co.uk', port=443): Read timed out. "
        "(read timeout=15)")
    assert sources.brief_error(timeout) == "farside.co.uk : Read timed out"
    http = RuntimeError("503 Server Error: Service Unavailable for url: "
                        "https://api.blockchain.info/stats")
    assert sources.brief_error(http, 30) == "503 Server Error: Service Unav"
    assert sources.brief_error(RuntimeError("")) == "RuntimeError"

    # Le perpétuel en compose deux : chacune reste lisible.
    class Panne:
        def raise_for_status(self): raise dns
        def json(self): return {}
    original = sources._http.get
    sources._http.get = lambda *a, **k: Panne()
    try:
        sources.fetch_perp_snapshot("BTCUSDT")
    except RuntimeError as exc:
        assert str(exc) == ("Binance Futures injoignable — premiumIndex : "
                            "fapi.binance.com : NameResolutionError ; "
                            "ratio : fapi.binance.com : NameResolutionError"), exc
    else:
        raise AssertionError("les deux endpoints en panne doivent lever")
    finally:
        sources._http.get = original
    print("  ✓ hôte et cause, sans l'URL ni le pool")


def test_une_connexion_rompue_est_rejouee():
    """La connexion gardée qu'un intermédiaire a oubliée répond par une
    rupture à l'envoi suivant : la lecture est rejouée sur une connexion
    neuve, une seule fois ; un délai dépassé, lui, n'est pas rejoué."""
    import socket
    import struct
    import threading

    import requests

    serveur = socket.create_server(("127.0.0.1", 0))
    port = serveur.getsockname()[1]
    recues = []
    reponse = (b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
               b"Connection: keep-alive\r\n\r\nok")

    def servir(scenario):
        # scenario : par connexion acceptée, la suite de ses réponses.
        for actes in scenario:
            prise, _ = serveur.accept()
            for acte in actes:
                recues.append(prise.recv(4096))
                if acte == "sert":
                    prise.sendall(reponse)
                elif acte == "rompt":
                    prise.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                     struct.pack("ii", 1, 0))
                    break
                else:                      # se tait
                    time.sleep(0.6)
                    break
            prise.close()

    def session():
        s = requests.Session()
        s.mount("http://", sources._ReprendRupture())
        return s

    url = f"http://127.0.0.1:{port}/"
    # Servie, puis rompue à la relecture ; la reprise ouvre une connexion.
    fil = threading.Thread(target=servir,
                           args=([["sert", "rompt"], ["sert"]],), daemon=True)
    fil.start()
    http = session()
    assert http.get(url, timeout=2).text == "ok"
    assert http.get(url, timeout=2).text == "ok"
    fil.join(2)
    assert len(recues) == 3, recues

    # Rompue deux fois de suite : un seul nouvel essai, puis la panne.
    del recues[:]
    fil = threading.Thread(target=servir,
                           args=([["rompt"], ["rompt"]],), daemon=True)
    fil.start()
    try:
        session().get(url, timeout=2)
    except requests.exceptions.ConnectionError:
        pass
    else:
        raise AssertionError("deux ruptures de suite doivent lever")
    fil.join(2)
    assert len(recues) == 2, recues

    # Un délai dépassé n'est pas rejoué : il coûterait deux fois l'attente.
    del recues[:]
    fil = threading.Thread(target=servir, args=([["se tait"]],), daemon=True)
    fil.start()
    try:
        session().get(url, timeout=0.2)
    except requests.exceptions.ReadTimeout:
        pass
    else:
        raise AssertionError("un délai dépassé doit lever")
    fil.join(2)
    assert len(recues) == 1, recues
    serveur.close()
    print("  ✓ rupture rejouée une fois, délai dépassé laissé tel quel")


def test_la_boucle_attend_le_reseau():
    """Au boot, la boucle d'observation attend le réseau au lieu de
    déclarer toutes les sources en panne au premier tour ; l'attente et
    sa fin tiennent en une ligne chacune, et l'arrêt du hub l'interrompt."""
    hub = MarketHub(collect_news=False, keep_journal=False)
    hub.NETWORK_PROBE_EVERY = 0.01
    hub.NETWORK_WAIT = 1.0
    sondes = []

    def sonde():
        sondes.append(1)
        return len(sondes) >= 3
    hub._network_reachable = sonde

    capture = Capture()
    hub_module.log.addHandler(capture)
    try:
        assert hub._wait_for_network() is True
        assert len(sondes) == 3
        assert [l for l in capture.lignes if "absent au démarrage" in l], capture.lignes
        assert [l for l in capture.lignes if l.startswith("réseau présent après")]

        # Réseau présent d'emblée : rien à dire.
        capture.lignes.clear(); sondes.clear()
        sondes.extend([1, 1])
        assert hub._wait_for_network() is True and capture.lignes == []

        # Jamais de réseau : la boucle part quand même, et le dit.
        capture.lignes.clear()
        hub._network_reachable = lambda: False
        assert hub._wait_for_network() is False
        assert [l for l in capture.lignes if "part sans lui" in l], capture.lignes

        # L'arrêt du hub coupe l'attente sans attendre l'expiration.
        hub.NETWORK_WAIT = 60.0
        hub._observe_stop.set()
        debut = time.monotonic()
        assert hub._wait_for_network() is False
        assert time.monotonic() - debut < 1.0
    finally:
        hub_module.log.removeHandler(capture)
    print("  ✓ la boucle d'observation attend le réseau, et le dit une fois")


def test_le_reveil_rattend_le_reseau():
    """Un sommeil lu sur les horloges est un réveil de la machine : la
    boucle rattend le réseau, comme au démarrage, et le dit avec la durée
    du sommeil — onze secondes comprises, que l'heure murale ne trahissait
    pas ; un tour ordinaire ne sonde rien, un réveil ne se traite qu'une
    fois, même vu d'abord par une autre lecture."""
    endormi = [0.0]
    hub = MarketHub(collect_news=False, keep_journal=False)
    hub._sleep = SleepWatch(lambda: endormi[0])
    hub._cache = TTLCache(hub._sleep, hub.WAKE_GRACE)
    hub.NETWORK_PROBE_EVERY = 0.01
    sondes = []

    def sonde():
        sondes.append(1)
        return len(sondes) >= 2
    hub._network_reachable = sonde

    capture = Capture()
    hub_module.log.addHandler(capture)
    try:
        # Un tour ordinaire : rien.
        hub._after_sleep()
        assert sondes == [] and capture.lignes == []

        # Trente-deux minutes de sommeil.
        endormi[0] += 32 * 60
        hub._after_sleep()
        assert len(sondes) == 2
        assert [l for l in capture.lignes
                if "au réveil, après 32 min de sommeil" in l], capture.lignes
        assert [l for l in capture.lignes if l.startswith("réseau présent après")]

        # Le tour suivant ne rejoue pas le même réveil.
        capture.lignes.clear(); sondes.clear()
        hub._after_sleep()
        assert sondes == [] and capture.lignes == []

        # Onze secondes, vues d'abord par une lecture du cache.
        endormi[0] += 11
        assert hub._cache.waking()
        hub._after_sleep()
        assert [l for l in capture.lignes
                if "au réveil, après 11 s de sommeil" in l], capture.lignes

        # Réveil avec le réseau déjà là : la boucle reprend sans un mot.
        capture.lignes.clear(); sondes.clear(); sondes.extend([1, 1])
        endormi[0] += 3600
        hub._after_sleep()
        assert len(sondes) == 3 and capture.lignes == []
    finally:
        hub_module.log.removeHandler(capture)
    print("  ✓ le réveil de la machine rattend le réseau, et dit combien elle a dormi")


def test_un_echec_au_reveil_n_est_pas_une_panne():
    """Dans la minute d'un réveil, le réseau manque encore : l'échec est
    servi de secours, mais il ne se journalise pas, ne compte pas au
    bandeau, et ne relie pas la panne d'avant le sommeil à son retour.

    Le journal du service, le 6 novembre : « klines:1h:400 de nouveau
    servie après 95 min », dont quatre-vingt-quatre de sommeil — une
    lecture ratée à la troisième seconde du réveil avait confirmé la
    panne de la veille.
    """
    endormi = [0.0]
    montre = SleepWatch(lambda: endormi[0])
    cache = TTLCache(montre, grace=60.0)

    def panne():
        raise ConnectionError("réseau absent")

    capture = Capture()
    hub_module.log.addHandler(capture)
    try:
        cache.get("k", 0, lambda: 1)
        cache.get("k", 0, panne)
        assert len(capture.lignes) == 1 and "k en panne" in capture.lignes[0]
        # Dix minutes de panne, puis quatre-vingt-quatre de sommeil.
        cache._failing["k"] -= 94 * 60
        cache._failed_at["k"] -= 84 * 60
        endormi[0] += 84 * 60

        # Au réveil : secours servi, dit tel, mais rien au journal ni au
        # bandeau, pour cette clé comme pour une autre.
        assert cache.get("k", 0, panne) == 1 and cache.stale("k")
        assert cache.get("autre", 0, lambda: 2) == 2
        assert cache.get("autre", 0, panne) == 2 and cache.stale("autre")
        assert len(capture.lignes) == 1, capture.lignes
        assert cache.failing(FAILING_WINDOW) == {}

        # Le réseau revient : la panne d'avant le sommeil garde sa durée.
        cache.get("k", 0, lambda: 3)
        assert capture.lignes[-1] == (
            "source k de nouveau servie à sa première lecture depuis 84 min "
            "(panne constatée : 10 min)"), capture.lignes[-1]
        cache.get("autre", 0, lambda: 4)
        assert len(capture.lignes) == 2, capture.lignes

        # La minute passée, un échec redevient une panne.
        montre.woke_at -= 61
        cache.get("autre", 0, panne)
        assert capture.lignes[-1].startswith("source autre en panne"), capture.lignes
        assert set(cache.failing(FAILING_WINDOW)) == {"autre"}
    finally:
        hub_module.log.removeHandler(capture)
    print("  ✓ un échec du réveil est servi de secours, sans panne dite")


def test_l_echec_apres_un_silence_ouvre_une_panne():
    """Un échec qui suit un long silence ne prolonge pas la panne d'avant :
    on ignore ce qu'elle est devenue pendant que personne ne lisait. C'est
    le pendant, à l'échec, de ce que le retour fait déjà."""
    def panne():
        raise ConnectionError("injoignable")

    cache = TTLCache()
    capture = Capture()
    hub_module.log.addHandler(capture)
    try:
        cache.get("k", 0, lambda: 1)
        cache.get("k", 0, panne)
        # Cinq minutes d'échecs, puis trois heures sans lecture.
        cache._failing["k"] -= 185 * 60
        cache._failed_at["k"] -= 180 * 60
        cache._entries["k"] = (cache._entries["k"][0] - 186 * 60, 1)
        cache.get("k", 0, panne)
        assert capture.lignes[-1] == (
            "source k en panne à sa première lecture depuis 180 min : "
            "injoignable — dernière valeur : 11160 s (panne précédente "
            "constatée : 5 min)"), capture.lignes[-1]
        # Le retour, deux minutes plus tard, compte la nouvelle panne.
        cache._failing["k"] -= 120
        cache._failed_at["k"] -= 60
        cache.get("k", 0, lambda: 2)
        assert capture.lignes[-1] == "source k de nouveau servie après 2 min"

        # Des échecs rapprochés restent une seule panne.
        capture.lignes.clear()
        for _ in range(3):
            cache.get("k", 0, panne)
        assert len(capture.lignes) == 1, capture.lignes
    finally:
        hub_module.log.removeHandler(capture)
    print("  ✓ après un silence, un échec ouvre une panne au lieu d'allonger l'ancienne")


def test_le_delai_de_binance_suit_la_panne():
    """Un accroc isolé ne fige plus le graphique une minute : le délai part
    de cinq secondes et suit la durée de la panne vue, une minute au plus.
    Dans la minute d'un réveil, il ne grandit pas — c'est le réseau qui
    manque, et le graphique repartait soixante et une secondes après son
    retour."""
    endormi = [0.0]
    hub = MarketHub(collect_news=False, keep_journal=False)
    hub._sleep = SleepWatch(lambda: endormi[0])
    hub._cache = TTLCache(hub._sleep, hub.WAKE_GRACE)
    hub.history = object()  # présent : la lecture passe par l'attente
    lectures = []

    def panne():
        lectures.append(1)
        raise ConnectionError("injoignable")

    def essai(depuis=None):
        """Un essai qui échoue, la panne ayant commencé `depuis` s plus tôt ;
        rend le délai armé."""
        hub._klines_retry_at = 0.0
        if depuis is not None:
            hub._klines_down_since = time.time() - depuis
        avant = time.time()
        try:
            hub._through_history(panne, panne)
        except ConnectionError:
            pass
        return round(hub._klines_retry_at - avant)

    assert essai() == 5, "premier échec : cinq secondes"
    assert essai(depuis=5) == 5
    assert essai(depuis=20) == 20, "le délai suit la panne vue"
    assert essai(depuis=600) == 60, "une minute au plus"

    # Pendant le délai : la source n'est pas appelée, et le dit.
    lectures.clear()
    try:
        hub._through_history(panne, panne)
    except SourceBackoff:
        pass
    else:
        raise AssertionError("une lecture pendant le délai doit lever SourceBackoff")
    assert lectures == []

    # Un succès clôt la panne : l'échec suivant repart de cinq secondes.
    hub._klines_retry_at = 0.0
    hub._through_history(lambda: "ok", panne)
    assert hub._klines_down_since is None
    assert essai() == 5

    # Réveil : le délai reste à cinq secondes, même sur une longue panne.
    endormi[0] += 600
    assert essai(depuis=600) == 5
    print("  ✓ Binance retenté en secondes après un accroc, en minute sur une panne")


def test_l_attente_ne_se_compte_pas_comme_panne():
    """Le 6 novembre à 12:30:01, un délai de connexion chez Binance a écrit
    onze lignes au journal : la panne réelle du quart d'heure, puis chaque
    intervalle lu pendant l'attente, « en panne : Binance en attente de
    nouvel essai », chacun suivi de son retour. Une lecture qu'on refuse
    n'a pas échoué : le secours est servi, sans panne dite."""
    hub = MarketHub(collect_news=False, keep_journal=False)
    hub._cache = TTLCache()
    hub.history = object()
    binance = {"panne": False}

    def lecture():
        if binance["panne"]:
            raise ConnectionError("api.binance.com : ConnectTimeoutError")
        return pd.DataFrame({"close": [1.0]})

    def lire(cle):
        return hub._cache.get(cle, 0, lambda: hub._through_history(lecture, lecture))

    cles = ["klines:15m:300", "klines:1h:350", "klines:4h:350", "klines:1d:365"]
    capture = Capture()
    hub_module.log.addHandler(capture)
    try:
        for cle in cles:
            lire(cle)
        binance["panne"] = True
        for cle in cles:
            lire(cle)
            assert hub._cache.stale(cle), cle
        assert len(capture.lignes) == 1, capture.lignes
        assert capture.lignes[0].startswith("source klines:15m:300 en panne : "
                                            "api.binance.com"), capture.lignes
        assert set(hub.failing_sources()) == {"klines"}

        # Retour : une ligne, celle de la panne réelle.
        binance["panne"] = False
        hub._klines_retry_at = 0.0
        for cle in cles:
            lire(cle)
            assert not hub._cache.stale(cle), cle
        assert len(capture.lignes) == 2, capture.lignes
        assert capture.lignes[1].startswith("source klines:15m:300 de nouveau servie")
        assert hub.failing_sources() == {}
    finally:
        hub_module.log.removeHandler(capture)
    print("  ✓ une panne réelle, une ligne : l'attente sert le secours sans bruit")


def test_le_cache_oublie_ce_que_personne_ne_lit():
    """Une page d'historique est une clé à usage unique : chaque pan restait
    en mémoire jusqu'au redémarrage. Le ménage oublie ce que personne n'a
    lu depuis `idle` et dont la durée de vie est passée — et garde ce qu'on
    lit encore, même périmé, qui sert de secours."""
    cache = TTLCache()
    heure = 3600

    def vieillit(cle, ecrit, lu):
        date, valeur = cache._entries[cle]
        cache._entries[cle] = (date - ecrit, valeur)
        cache._read_at[cle] -= lu

    def panne():
        raise ConnectionError("injoignable")

    # Une page lue une fois, il y a plus d'une heure : oubliée.
    cache.get("klines:15m:500:1793938500000", heure, lambda: "page")
    vieillit("klines:15m:500:1793938500000", 2 * heure, 2 * heure)
    # Une source relue sans relâche, périmée parce qu'en panne : gardée.
    cache.get("perp", 30, lambda: "financement")
    vieillit("perp", 2 * heure, 2 * heure)
    assert cache.get("perp", 30, panne) == "financement" and cache.stale("perp")
    # Une valeur que personne ne lit, mais encore dans sa durée de vie
    # (la masse monétaire, douze heures) : gardée.
    cache.get("m2", 12 * heure, lambda: "M2")
    vieillit("m2", 2 * heure, 2 * heure)
    # Une valeur lue par `peek` seulement — le taux € du pousseur : gardée.
    cache.get("eur", heure, lambda: 0.9)
    vieillit("eur", 2 * heure, 2 * heure)
    assert cache.peek("eur") == 0.9
    # Une page en panne, sans rien à servir, jamais relue : son secours
    # est oublié, mais son état de panne reste, pour que le retour se dise.
    try:
        cache.get("klines:1h:300:1793900000000", heure, panne)
    except ConnectionError:
        pass
    cache._read_at["klines:1h:300:1793900000000"] -= 2 * heure

    assert cache.sweep(heure) == 2
    assert set(cache._entries) == {"perp", "m2", "eur"}
    assert set(cache._read_at) == {"perp", "m2", "eur"}
    assert not cache.stale("klines:1h:300:1793900000000")
    assert "klines:1h:300:1793900000000" in cache._failing

    # La page redemandée revient comme neuve : rien n'a disparu que la mémoire.
    assert cache.get("klines:15m:500:1793938500000", heure, lambda: "page") == "page"
    assert cache.sweep(heure) == 0
    print("  ✓ le cache oublie ce que personne ne lit, garde le secours lu")


def test_l_etat_des_sources():
    """Le cache dit, par clé, sa dernière valeur fraîche, son secours, sa
    panne et sa dernière erreur ; le hub les regroupe par famille, la plus
    mal en point donnant l'état, et y ajoute flux et tâches de fond."""
    hub = MarketHub(collect_news=False, keep_journal=False)
    hub._cache = TTLCache()

    erreur = ConnectionError("HTTPSConnectionPool(host='api.coingecko.com', "
                             "port=443): Read timed out. (read timeout=12)")
    attendue = sources.brief_error(erreur)

    def panne():
        raise erreur

    hub._cache.get("klines:1h:350", 0, lambda: 1)
    hub._cache.get("klines:4h:350", 0, lambda: 2)
    hub._cache.get("klines:4h:350", 0, panne)          # secours servi
    hub._cache.get("signal_stats:1h", 0, lambda: {})   # un calcul, pas une source
    try:
        hub._cache.get("global", 0, panne)
    except ConnectionError:
        pass
    etat = hub._cache.status()
    assert etat["global"]["error"] == attendue == "api.coingecko.com : Read timed out"
    assert etat["global"]["failing_since"] is not None
    assert etat["global"]["fresh_at"] is None
    assert etat["klines:1h:350"]["stale"] is False

    sections = hub.sources_status()
    assert list(sections) == ["Flux temps réel", "Sources REST", "Tâches de fond"]
    rest = {row["name"]: row for row in sections["Sources REST"]}
    assert "signal_stats" not in rest
    assert rest["global"]["state"] == "panne"
    assert rest["global"]["detail"] == attendue
    # Deux lectures de chandeliers, l'une en panne : la famille l'est.
    assert rest["klines"]["state"] == "panne" and rest["klines"]["keys"] == 2
    assert rest["klines"]["age"] is not None
    # Les pannes en tête.
    assert sections["Sources REST"][0]["state"] == "panne"
    # Les carnets d'un hub jamais démarré sont coupés ; les tâches désactivées
    # au lancement le disent.
    assert {row["state"] for row in sections["Flux temps réel"]} == {"coupé"}
    assert {row["state"] for row in sections["Tâches de fond"]} == {"inactif"}

    from terminal.panels import sources as panneau
    texte = str(panneau.table(sections))
    assert "Agrégats de marché · CoinGecko" in texte and "Chandeliers · Binance" in texte
    assert "en défaut" in str(panneau.badges(sections))
    assert [panneau._age(s) for s in (None, 12, 600, 7200, 3 * 86_400)] == [
        "—", "12 s", "10 min", "2 h", "3 j"]
    print("  ✓ état par clé, familles, pannes en tête, flux et tâches dits")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCache du hub — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Une source en panne se voit, et ne s'écrit pas.\n")
