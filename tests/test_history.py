#!/usr/bin/env python3
"""
L'historique du cours sur disque, contrôlé sans réseau.

Binance est remplacé par `FauxBinance`, qui respecte le contrat de
`/api/v3/klines` : `startTime` sert les premières bougies à partir de
l'instant, `endTime` les dernières jusqu'à lui, et sans borne la fin de la
série — jamais plus de 1000 par appel. La bougie en cours est celle dont
la clôture n'est pas passée, et l'horloge est simulée : ce qui compte ici,
c'est ce qui s'écrit et quand.

Ce que le test verrouille :

- une bougie sauvegardée ne se redemande pas, redémarrage compris ;
- la bougie en cours ne s'écrit jamais, et une bougie close s'écrit avec
  ses valeurs finales ;
- la plage reste sans trou, en rattrapage comme vers le passé ;
- Binance muet : le disque répond et le dit, sans attendre le délai
  à chaque lecture, et la démo ne sert que s'il n'a rien ;
- une base inutilisable ne fait pas tomber le panneau ;
- les tests et `keep_journal=False` ne touchent jamais à la vraie base.

Lancement :
    python tests/test_history.py
"""

import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from btcterm import history as history_module  # noqa: E402
from btcterm import sources  # noqa: E402
from btcterm.history import PAGE, SETTLE_MS, KlineHistory  # noqa: E402
from btcterm.hub import MarketHub  # noqa: E402

HEURE = 3_600_000
DEBUT = 1_700_000_000_000 // HEURE * HEURE


class FauxBinance:
    """Une série horaire de `total` bougies, au contrat de Binance."""

    def __init__(self, total: int):
        self.total = total
        self.appels: list[dict] = []
        self.panne = False
        #: Le cours de la bougie en cours, pour la voir bouger.
        self.cours = 100.0

    @property
    def maintenant(self) -> int:
        """L'horloge : au milieu de la bougie d'indice `total - 1`."""
        return DEBUT + (self.total - 1) * HEURE + HEURE // 2

    def bougie(self, i: int) -> dict:
        en_cours = i == self.total - 1
        prix = self.cours if en_cours else 50.0 + i
        ouverture = DEBUT + i * HEURE
        return {
            "open_time": ouverture, "open": prix, "high": prix + 1,
            "low": prix - 1, "close": prix, "volume": 2.0,
            "close_time": ouverture + HEURE - 1, "quote_vol": 3.0,
            "trades": 4, "taker_buy_base": 1.0, "taker_buy_quote": 1.5,
        }

    def __call__(self, symbol, interval, limit, index=False, end_time=None,
                 start_time=None):
        self.appels.append({"limit": limit, "end": end_time,
                            "start": start_time})
        if self.panne:
            raise ConnectionError("api.binance.com : ConnectTimeoutError")
        toutes = [self.bougie(i) for i in range(self.total)]
        if start_time is not None:
            lignes = [b for b in toutes if b["open_time"] >= start_time][:limit]
        elif end_time is not None:
            lignes = [b for b in toutes if b["open_time"] <= end_time][-limit:]
        else:
            lignes = toutes[-limit:]
        df = pd.DataFrame(lignes, columns=list(history_module.COLUMNS))
        df = df.astype({c: float for c in sources.OHLCV_COLUMNS})
        df["time"] = pd.to_datetime(df["open_time"], unit="ms")
        return df


class Banc:
    """Pose la fausse source et l'horloge simulée, puis les retire."""

    def __init__(self, total: int):
        self.binance = FauxBinance(total)

    def __enter__(self):
        self.fetch, self.temps = sources.fetch_klines, history_module.time
        sources.fetch_klines = self.binance
        history_module.time = SimpleNamespace(
            time=lambda: self.binance.maintenant / 1000)
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = Path(self.dossier.name) / "klines.db"
        return self.binance

    def __exit__(self, *_):
        sources.fetch_klines = self.fetch
        history_module.time = self.temps
        self.dossier.cleanup()

    def base(self) -> KlineHistory:
        return KlineHistory(path=self.chemin)


def test_une_bougie_close_ne_se_redemande_pas():
    banc = Banc(1500)
    with banc as binance:
        h = banc.base()
        premier = h.latest("1h", 365)
        assert len(premier) == 365
        assert premier["open_time"].is_monotonic_increasing
        assert premier["open_time"].is_unique
        assert premier["time"].iloc[-1] == pd.Timestamp(
            DEBUT + 1499 * HEURE, unit="ms"), "la bougie en cours manque"
        assert len(binance.appels) == 1 and binance.appels[0]["limit"] == PAGE

        #: Ensuite, Binance n'est interrogé que depuis la dernière close.
        binance.appels.clear()
        second = h.latest("1h", 365)
        assert binance.appels == [{"limit": PAGE, "end": None,
                                   "start": DEBUT + 1498 * HEURE + 1}]
        pd.testing.assert_frame_equal(premier, second)

        #: Et un redémarrage ne repart pas de zéro.
        binance.appels.clear()
        h.close()
        relu = banc.base().latest("1h", 365)
        assert len(binance.appels) == 1 and binance.appels[0]["start"]
        pd.testing.assert_frame_equal(premier, relu)
    print("  ✓ une page au premier appel, la fin seule ensuite, redémarrage compris")


def test_la_bougie_en_cours_ne_s_ecrit_pas():
    banc = Banc(1200)
    with banc as binance:
        h = banc.base()
        binance.cours = 100.0
        assert h.latest("1h", 10)["close"].iloc[-1] == 100.0
        binance.cours = 123.0
        assert h.latest("1h", 10)["close"].iloc[-1] == 123.0, "bougie figée"
        assert h.stored("1h", 1)["open_time"].iloc[0] == DEBUT + 1198 * HEURE

        #: Elle se clôt : elle reste en mémoire pendant `SETTLE_MS`...
        binance.total += 1
        horloge = binance.maintenant - HEURE // 2 + SETTLE_MS // 2
        history_module.time = SimpleNamespace(time=lambda: horloge / 1000)
        h.latest("1h", 10)
        assert h.stored("1h", 1)["open_time"].iloc[0] == DEBUT + 1198 * HEURE

        #: ... puis s'écrit, avec ses valeurs finales.
        horloge = binance.maintenant
        history_module.time = SimpleNamespace(time=lambda: horloge / 1000)
        h.latest("1h", 10)
        derniere = h.stored("1h", 1).iloc[0]
        assert derniere["open_time"] == DEBUT + 1199 * HEURE
        assert derniere["close"] == 50.0 + 1199
    print("  ✓ en cours : jamais écrite ; close : écrite après son délai, définitive")


def test_le_rattrapage_ne_laisse_pas_de_trou():
    banc = Banc(1500)
    with banc as binance:
        h = banc.base()
        h.latest("1h", 10)
        #: Le terminal s'arrête le temps de 2500 bougies.
        binance.total += 2500
        binance.appels.clear()
        serie = h.latest("1h", 3000)
        assert len(serie) == 3000
        assert (serie["open_time"].diff().dropna() == HEURE).all()
        assert len(binance.appels) == 3, binance.appels
    print("  ✓ trois pages de rattrapage après une longue absence, série continue")


def test_vers_le_passe_une_page_en_sert_plusieurs():
    banc = Banc(2500)
    with banc as binance:
        h = banc.base()
        h.latest("1h", 365)
        premiere = h.stored("1h", PAGE)["open_time"].iloc[0]

        #: Déjà sur le disque : aucun appel.
        binance.appels.clear()
        page = h.page("1h", premiere + 400 * HEURE, 300)
        assert len(page) == 300 and not binance.appels

        #: Au-delà : une page entière, pour la suite du pan aussi.
        page = h.page("1h", premiere + 100 * HEURE, 300)
        assert len(page) == 300
        assert binance.appels == [{"limit": PAGE, "start": None,
                                   "end": premiere - 1}]
        binance.appels.clear()
        h.page("1h", premiere - 500 * HEURE, 300)
        assert not binance.appels, "la page aurait dû servir le pan suivant"
        serie = h.stored("1h", 5000)
        assert (serie["open_time"].diff().dropna() == HEURE).all()

        #: Le début de l'historique atteint, on ne le redemande plus.
        assert len(h.page("1h", DEBUT + 10 * HEURE, 300)) == 11
        binance.appels.clear()
        assert len(h.page("1h", DEBUT + 10 * HEURE, 300)) == 11
        assert not binance.appels, "historique épuisé redemandé"
    print("  ✓ recul par pages entières, sans trou, arrêté au début de l'historique")


def test_la_borne_du_present_sert_la_bougie_en_cours():
    """Le profil de la plage visible va jusqu'à la bougie en cours."""
    banc = Banc(1200)
    with banc as binance:
        h = banc.base()
        page = h.page("1h", binance.maintenant, 50)
        assert page["open_time"].iloc[-1] == DEBUT + 1199 * HEURE
    print("  ✓ une page qui touche au présent porte la bougie en cours")


def hub_sur(banc: Banc) -> MarketHub:
    hub = MarketHub(collect_news=False, keep_journal=False)
    hub.history = banc.base()
    return hub


def test_binance_muet_le_disque_repond():
    banc = Banc(1200)
    with banc as binance:
        hub = hub_sur(banc)
        vivant = hub.klines("1h", 365)
        assert not vivant.attrs.get("stale") and not vivant.attrs.get("demo")

        #: Comme un redémarrage pendant la panne : plus rien en mémoire.
        binance.panne = True
        froid = MarketHub(collect_news=False, keep_journal=False)
        froid.history = hub.history
        servi = froid.klines("1h", 365)
        assert servi.attrs.get("stale") is True
        assert not servi.attrs.get("demo"), "la démo a remplacé l'historique"
        assert servi["open_time"].iloc[-1] == DEBUT + 1198 * HEURE

        #: Une seule tentative : le pousseur lit toutes les deux secondes.
        binance.appels.clear()
        for _ in range(5):
            froid.klines("1h", 365)
            froid._cache._entries.clear()
        assert not binance.appels, "la source retentée à chaque lecture"

        #: La panne passée, la bougie en cours revient.
        binance.panne = False
        froid._klines_retry_at = 0.0
        retour = froid.klines("1h", 365)
        assert not retour.attrs.get("stale")
        assert retour["open_time"].iloc[-1] == DEBUT + 1199 * HEURE
        froid.history.close()
    print("  ✓ panne à froid : le disque répond, le dit, et Binance n'est pas harcelé")


def test_binance_muet_en_pleine_seance_se_dit():
    """Le cache mémoire sert sa dernière série : elle se dit figée aussi."""
    banc = Banc(1200)
    with banc as binance:
        hub = hub_sur(banc)
        vivante = hub.klines("1h", 365)
        assert not vivante.attrs.get("stale")

        binance.panne = True
        cle = "klines:1h:365"
        date, valeur = hub._cache._entries[cle]
        hub._cache._entries[cle] = (date - 3600, valeur)
        figee = hub.klines("1h", 365)
        assert figee.attrs.get("stale") is True
        assert figee["open_time"].iloc[-1] == DEBUT + 1199 * HEURE
        assert not vivante.attrs.get("stale"), "l'objet du cache a été modifié"

        #: La panne passée, le marqueur disparaît de lui-même.
        binance.panne = False
        hub._klines_retry_at = 0.0
        hub._cache._entries[cle] = (date - 3600, valeur)
        assert not hub.klines("1h", 365).attrs.get("stale")
        hub.history.close()
    print("  ✓ panne en séance : la bougie figée se dit, puis le marqueur s'efface")


def test_disque_vide_la_demo_reste_le_dernier_recours():
    banc = Banc(1200)
    with banc as binance:
        hub = hub_sur(banc)
        binance.panne = True
        assert hub.klines("1h", 100).attrs.get("demo") is True
        assert not banc.chemin.exists(), "une lecture a créé la base"
    print("  ✓ rien sur le disque : la démo, et aucun fichier créé pour rien")


def test_page_d_historique_sans_reseau():
    banc = Banc(1200)
    with banc as binance:
        hub = hub_sur(banc)
        hub.klines("1h", 365)
        binance.panne = True
        hub._cache._entries.clear()
        fin = DEBUT + 1100 * HEURE
        #: Sur le disque : servie sans appel.
        assert len(hub.klines_history("1h", fin, 200)) == 200
        #: Hors du disque : ce qu'il tient, sans lever.
        partielle = hub.klines_history("1h", DEBUT + 300 * HEURE, 200)
        assert partielle.empty or partielle["open_time"].iloc[-1] <= (
            DEBUT + 300 * HEURE)
        hub.history.close()
    print("  ✓ pan pendant la panne : le disque sert ce qu'il tient, sans lever")


def test_base_inutilisable_lecture_directe():
    banc = Banc(1200)
    with banc as binance:
        hub = hub_sur(banc)
        banc.chemin.mkdir()      # un dossier à la place du fichier
        hub.history.close()
        df = hub.klines("1h", 100)
        assert len(df) == 100 and not df.attrs.get("demo")
        assert hub.history is None, "la base cassée doit être abandonnée"
        assert binance.appels[-1]["limit"] == 100, "lecture directe attendue"
    print("  ✓ base inutilisable : le panneau continue sur la source")


def test_le_contrat_du_panneau_prix():
    """Le disque rend ce que la source rendait : le panneau n'y voit rien."""
    from terminal.charts import prepare_price_frame

    banc = Banc(1200)
    with banc as binance:
        hub = hub_sur(banc)
        df = hub.klines("1h", 365)
        direct = binance("BTCUSDT", "1h", 365)
        for colonne in ("time", *sources.OHLCV_COLUMNS):
            assert df[colonne].dtype == direct[colonne].dtype, colonne
            assert (df[colonne].values == direct[colonne].values).all(), colonne
        assert not prepare_price_frame(df).empty

        #: Le mémo du pousseur tient à l'identité de l'objet servi.
        assert hub.klines("1h", 365) is df
        hub.history.close()
    print("  ✓ mêmes colonnes, mêmes valeurs, même objet tant que le cache tient")


def test_les_tests_ne_touchent_pas_la_vraie_base():
    assert MarketHub(collect_news=False, keep_journal=False).history is None
    print("  ✓ keep_journal=False : pas d'historique, donc pas de fausses bougies")


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    debut = time.monotonic()
    print(f"\nHistorique du cours — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print(f"Une bougie close ne se redemande pas. ({time.monotonic() - debut:.1f} s)\n")
