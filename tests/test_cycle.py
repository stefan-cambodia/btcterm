#!/usr/bin/env python3
"""
Le cycle du bitcoin, sans réseau.

Vérifié ici :

- le prochain halving se compte depuis la hauteur de la chaîne, à dix
  minutes le bloc ; sans hauteur, elle s'estime et le résultat le dit ;
- une hauteur qui a franchi un halving inconnu de la table vise le suivant ;
- le repli vaut zéro au sommet, et le multiple de Mayer n'existe qu'une
  fois la moyenne de 200 jours pleine ;
- les cycles se superposent en multiple du cours du jour du halving — celui
  de la série, ou la référence écrite quand la série commence après ;
- la situation du jour laisse à `None` ce que la profondeur ne permet pas ;
- le collecteur du réseau rapporte la hauteur ;
- le panneau et ses trois vues tiennent sans données, et avec.

Lancement :
    python tests/test_cycle.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from btcterm import cycle, sources  # noqa: E402


def cloture(depart, valeurs):
    dates = pd.date_range(depart, periods=len(valeurs), freq="D")
    return cycle.closes(pd.DataFrame({"time": dates, "close": valeurs}))


def test_le_halving_se_compte_en_blocs():
    maintenant = datetime(2026, 11, 11, 12, 0)
    etat = cycle.next_halving(974_640, maintenant)
    assert etat["target"] == 1_050_000 and etat["blocks_left"] == 75_360
    assert not etat["estimated"]
    # 75 360 blocs à dix minutes : 523 jours et un tiers.
    assert abs(etat["days_left"] - 75_360 / 144) < 1e-9
    assert etat["eta"].date() == datetime(2028, 4, 17).date()
    assert abs(etat["progress"] - (1 - 75_360 / 210_000)) < 1e-12
    assert abs(etat["days_since"] - 935.5) < 1e-9
    # Une heure avec fuseau se lit en UTC.
    avec_fuseau = cycle.next_halving(
        974_640, maintenant.replace(tzinfo=timezone.utc))
    assert avec_fuseau["eta"] == etat["eta"]
    print("  ✓ blocs restants, date à dix minutes le bloc, part écoulée")


def test_sans_hauteur_elle_s_estime_et_se_dit():
    maintenant = datetime(2024, 4, 30)          # dix jours après le halving
    for hauteur in (None, 0, 500_000):
        etat = cycle.next_halving(hauteur, maintenant)
        assert etat["estimated"], hauteur
        assert etat["height"] == 840_000 + 10 * 144
        assert etat["blocks_left"] == 210_000 - 1_440
    print("  ✓ hauteur absente ou incohérente : estimée, et dite telle")


def test_un_halving_inconnu_de_la_table_vise_le_suivant():
    maintenant = datetime(2028, 6, 1)
    etat = cycle.next_halving(1_050_000 + 1_440, maintenant)
    assert etat["target"] == 1_260_000 and not etat["estimated"]
    assert abs(etat["days_since"] - 10) < 1e-9
    print("  ✓ au-delà du dernier halving connu, le cycle suivant")


def test_repli_et_mayer():
    serie = cloture("2025-01-01", [100.0, 200.0, 150.0, 100.0, 220.0])
    assert np.allclose(cycle.drawdown(serie), [0, 0, -25, -50, 0])
    # Mayer : rien avant 200 jours, puis le cours sur sa moyenne.
    plate = cloture("2025-01-01", [100.0] * 199 + [150.0])
    ratio = cycle.mayer(plate)
    assert len(ratio) == 1
    assert abs(ratio.iloc[0] - 150 / ((199 * 100 + 150) / 200)) < 1e-12
    assert cycle.mayer(cloture("2025-01-01", [100.0] * 50)).empty
    print("  ✓ repli nul au sommet, Mayer seulement une fois la moyenne pleine")


def test_les_cycles_se_superposent():
    # Du 1er mai 2020 au 30 avril 2024 : le halving de 2020 dans la série,
    # celui de 2024 aussi ; celui de 2016 seulement par sa fin.
    jours = pd.date_range("2020-05-01", "2024-04-30", freq="D")
    serie = cycle.closes(pd.DataFrame({
        "time": jours, "close": np.linspace(1_300.0, 65_000.0, len(jours))}))
    chemins = cycle.cycle_paths(serie)
    assert [c["label"] for c in chemins] == ["2016", "2020", "2024"]
    c2016, c2020, c2024 = chemins
    # 2016 : la série commence après, la référence écrite sert de départ.
    assert c2016["base"] == 650.0 and c2016["days"][0] > 1_000
    assert abs(c2016["multiple"][0] - 2.0) < 1e-9
    assert c2016["days"][-1] == (datetime(2020, 5, 10)
                                 - datetime(2016, 7, 9)).days
    # 2020 : part de 1 le jour du halving, s'arrête la veille du suivant.
    assert c2020["days"][0] == 0 and c2020["multiple"][0] == 1.0
    assert c2020["days"][-1] == (datetime(2024, 4, 19)
                                 - datetime(2020, 5, 11)).days
    assert not c2020["current"]
    # 2024 : le cycle en cours, dix jours vécus.
    assert c2024["current"] and list(c2024["days"]) == list(range(11))
    assert cycle.cycle_paths(pd.Series(dtype=float)) == []
    print("  ✓ multiple du cours du halving, référence quand la série manque")


def test_la_situation_du_jour():
    assert cycle.metrics(pd.Series(dtype=float)) is None
    # Trop court pour toute moyenne : les mesures manquent, pas la ligne.
    court = cycle.metrics(cloture("2026-01-01", [100.0, 120.0, 90.0]),
                          now=datetime(2026, 1, 3))
    assert court["ath"] == 120.0 and court["days_since_ath"] == 1
    assert abs(court["drawdown"] + 25) < 1e-9
    assert court["mayer"] is None and court["mayer_rank"] is None
    assert court["ma200w"] is None and court["pi_ratio"] is None
    assert court["halving"]["estimated"]

    jours = pd.date_range("2020-05-01", "2026-11-10", freq="D")
    pas = np.random.default_rng(7).normal(0.001, 0.03, len(jours))
    serie = cycle.closes(pd.DataFrame({
        "time": jours, "close": 9_000 * np.exp(np.cumsum(pas))}))
    etat = cycle.metrics(serie, 974_640, datetime(2026, 11, 10, 12))
    assert etat["cycle_day"] == (datetime(2026, 11, 10)
                                 - datetime(2024, 4, 20)).days
    assert abs(etat["cycle_multiple"]
               - serie.iloc[-1] / serie[pd.Timestamp("2024-04-20")]) < 1e-9
    assert abs(etat["mayer"] - serie.iloc[-1] / serie.iloc[-200:].mean()) < 1e-9
    assert 0 <= etat["mayer_rank"] <= 1
    assert abs(etat["ma200w"] - serie.iloc[-1400:].mean()) < 1e-6
    assert abs(etat["pi_ratio"] - serie.iloc[-111:].mean()
               / (2 * serie.iloc[-350:].mean())) < 1e-9
    # Les cycles précédents au même jour, quand ils l'ont vécu.
    assert [r["label"] for r in etat["same_day"]] == ["2020"]
    jour = pd.Timestamp("2020-05-11") + pd.Timedelta(days=etat["cycle_day"])
    assert abs(etat["same_day"][0]["multiple"]
               - serie[jour] / serie[pd.Timestamp("2020-05-11")]) < 1e-9
    assert not etat["halving"]["estimated"]
    print("  ✓ ce que la profondeur ne permet pas reste vide ; le reste se recoupe")


def test_la_hauteur_vient_du_collecteur():
    class Reponse:
        def raise_for_status(self): pass
        def json(self):
            return {"hash_rate": 9.1e11, "difficulty": 1.2e14,
                    "minutes_between_blocks": 9.6, "n_tx": 400_000,
                    "n_blocks_total": 974_640}
    original = sources._http.get
    sources._http.get = lambda *a, **k: Reponse()
    try:
        assert sources.fetch_chain_stats()["height"] == 974_640
    finally:
        sources._http.get = original
    print("  ✓ hauteur de la chaîne dans l'instantané du réseau")


def test_les_etf_face_a_l_emission():
    """Les flux en bitcoins, face à ce que les mineurs produisent.

    La récompense suit les halvings ; l'émission court tous les jours du
    calendrier ; chaque séance se convertit à sa clôture. Une séance sans
    cours connu n'est pas inventée : la conversion dit sa couverture.
    """
    assert cycle.BLOCKS_PER_DAY == 144
    assert [cycle.block_subsidy(datetime(*jour)) for jour in
            ((2012, 11, 27), (2012, 11, 28), (2020, 5, 10), (2024, 4, 19),
             (2024, 4, 20), (2026, 11, 12))] == [50, 25, 12.5, 6.25, 3.125, 3.125]
    assert cycle.daily_issuance(datetime(2026, 11, 12)) == 450
    assert cycle.daily_issuance(pd.Timestamp("2024-04-19", tz="UTC")) == 900

    clotures = pd.Series([100_000.0, 90_000.0, 80_000.0],
                         index=pd.to_datetime(["2026-11-09", "2026-11-12",
                                               "2026-11-13"]))
    # Lundi 9 au vendredi 13 : la séance du mercredi prend la clôture du
    # lundi, la dernière connue avant elle.
    flux = pd.DataFrame({"Date": pd.to_datetime(["2026-11-09", "2026-11-11",
                                                 "2026-11-13"]),
                         "IBIT": [0.0, 0.0, 0.0],
                         "Total": [90.0, 45.0, -40.0]})
    offre = cycle.etf_supply(flux, clotures)
    assert offre["btc"] == 900 + 450 - 500, offre
    assert offre["calendar_days"] == 5 and offre["issuance"] == 2_250
    assert abs(offre["ratio"] - 850 / 2_250) < 1e-12
    assert offre["priced"] == offre["sessions"] == 3
    assert cycle.etf_supply(flux, clotures, 1)["btc"] == -500
    assert cycle.supply_text(offre) == "≈ +850 BTC, 0.4× l'émission"

    # Une séance d'avant le premier cours : convertie, elle serait inventée.
    plus_tot = pd.concat([pd.DataFrame({"Date": [pd.Timestamp("2026-11-02")],
                                        "IBIT": [0.0], "Total": [500.0]}),
                          flux], ignore_index=True)
    partielle = cycle.etf_supply(plus_tot, clotures)
    assert partielle["priced"] == 3 and partielle["sessions"] == 4
    assert cycle.supply_text(partielle).endswith("(3/4 séances)")

    assert cycle.etf_supply(pd.DataFrame(), clotures) is None
    assert cycle.etf_supply(flux, pd.Series(dtype=float)) is None
    assert cycle.supply_text(None) == ""
    assert [cycle.btc_amount(v) for v in (-15_445.8, 820.4, 0.2)] == \
        ["-15.4 k BTC", "+820 BTC", "+0 BTC"]
    print("  ✓ récompense par halving, 450 BTC/jour, flux convertis à leur "
          "clôture, couverture dite")


def test_l_ajustement_de_difficulte_et_les_rubans():
    """Le prochain ajustement se lit au rythme de la période en cours,
    borné par le protocole ; les rubans disent capitulation, reprise ou
    santé ; le panneau et son graphique les montrent."""
    debut = 1_793_301_298
    # 1 807 blocs en 10,824 minutes chacun : −7,6 % dans 209 blocs.
    estime = cycle.difficulty_estimate(975_535, debut, debut + 1_807 * 649.45)
    assert estime["blocks_done"] == 1_807 and estime["blocks_left"] == 209
    assert estime["start_height"] == 973_728
    assert abs(estime["change_pct"] - (600 / 649.45 - 1) * 100) < 1e-9
    assert abs(estime["eta"] - (debut + 2_016 * 649.45)) < 1e-3
    lent = cycle.difficulty_estimate(975_535, debut, debut + 1_807 * 600 * 10)
    assert lent["change_pct"] == -75.0, "÷ 4 au plus"
    assert cycle.difficulty_estimate(973_728, debut, debut + 10) is None
    assert cycle.difficulty_estimate(None, debut, debut) is None

    jours = pd.date_range("2026-01-01", periods=200, freq="D")
    # Cent jours de croissance, cinquante de chute, cinquante de reprise.
    valeurs = ([800 + i for i in range(100)] + [900 - 4 * i for i in range(50)]
               + [700 + 8 * i for i in range(50)])
    serie = pd.DataFrame({"time": jours, "value": valeurs})
    capitulation = cycle.hash_ribbons(serie.iloc[:150])
    assert capitulation["state"] == "capitulation"
    reprise = cycle.hash_ribbons(serie)
    assert reprise["state"] == "reprise", reprise["state"]
    assert reprise["since"] > jours[150]
    sain = cycle.hash_ribbons(serie.iloc[:100])
    assert sain["state"] == "sain"
    assert cycle.hash_ribbons(serie.iloc[:40]) is None

    from terminal.charts import build_chain_chart
    from terminal.panels import onchain
    figure = build_chain_chart(serie, pd.DataFrame(columns=["time", "value"]),
                               ribbons=reprise, days=120)
    assert {"MA 30", "MA 60"} <= {t.name for t in figure.data}
    assert any("rubans : reprise" in (a.text or "")
               for a in figure.layout.annotations)
    badge = str(onchain._badges({"hash_rate_ghs": 9e11, "difficulty": 1.48e14},
                                None, None, estime))
    assert "ajust. " in badge and "-7.6 % J-" in badge
    print("  ✓ ajustement au rythme de la période, borné ; rubans en trois états ; "
          "badge et graphique")


def test_l_heure_d_un_bloc_chez_blockstream():
    """Deux petites requêtes : le hash par la hauteur, puis le bloc."""
    class Reponse:
        def __init__(self, text="", payload=None):
            self.text, self.payload = text, payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    demandes = []

    def faux_get(url, **_):
        demandes.append(url)
        if "/block-height/" in url:
            return Reponse("00000000abc\n")
        return Reponse(payload={"height": 973_728, "timestamp": 1_793_301_298})

    original = sources._http.get
    sources._http.get = faux_get
    try:
        assert sources.fetch_block_time(973_728) == 1_793_301_298
    finally:
        sources._http.get = original
    assert demandes == [f"{sources.BLOCKSTREAM_API}/block-height/973728",
                        f"{sources.BLOCKSTREAM_API}/block/00000000abc"]
    print("  ✓ heure du bloc : hauteur → hash → horodatage")


def test_le_mvrv_chez_coin_metrics():
    """Le MVRV et le cours, paginés ; le prix réalisé qui s'en déduit, et
    son rang ; la vue du panneau et sa ligne de tableau, Coin Metrics
    nommé — la licence des données le demande."""
    class Reponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    pages = [
        {"data": [{"time": "2018-12-15T00:00:00.000000000Z",
                   "CapMVRVCur": "0.69", "PriceUSD": "3200"},
                  {"time": "2021-11-10T00:00:00.000000000Z",
                   "CapMVRVCur": "2.72", "PriceUSD": "65000"}],
         "next_page_url": "https://exemple/suite"},
        {"data": [{"time": "2026-11-11T00:00:00.000000000Z",
                   "CapMVRVCur": "1.37", "PriceUSD": "73800"}]},
    ]
    demandes = []

    def faux_get(url, params=None, **_):
        demandes.append((url, params))
        return Reponse(pages[len(demandes) - 1])

    original = sources._http.get
    sources._http.get = faux_get
    try:
        mvrv = sources.fetch_mvrv()
    finally:
        sources._http.get = original
    assert demandes[0][1]["metrics"] == "CapMVRVCur,PriceUSD,IssTotUSD"
    assert mvrv["issuance"].isna().all(), "émission absente : colonne vide, pas d'erreur"
    assert demandes[1] == ("https://exemple/suite", None), "la page suivante"
    assert list(mvrv["mvrv"]) == [0.69, 2.72, 1.37]

    etat = cycle.mvrv_state(mvrv)
    assert etat["mvrv"] == 1.37 and abs(etat["realized"] - 73800 / 1.37) < 1e-6
    assert etat["rank"] == 0.5, "plus haut que l'un des deux jours passés"
    assert cycle.mvrv_state(mvrv.iloc[0:0]) is None

    from terminal.charts import build_cycle_chart
    from terminal.panels import cycle as panneau
    petite = build_cycle_chart("MVRV", pd.Series(dtype=float), mvrv=mvrv)
    assert [t.name for t in petite.data] == ["MVRV"]
    assert any("Coin Metrics" in (a.text or "") for a in petite.layout.annotations)
    grande = build_cycle_chart("MVRV", pd.Series(dtype=float), mvrv=mvrv,
                               maximized=True)
    assert [t.name for t in grande.data] == ["cours", "prix réalisé", "MVRV"]
    lignes = panneau.rows(None, etat) + panneau.rows(
        {"halving": {"estimated": False, "eta": pd.Timestamp("2028-04-12"),
                     "target": 1_050_000, "days_left": 517.0,
                     "blocks_left": 74_464, "height": 975_536,
                     "progress": 0.65},
         "cycle_day": None, "same_day": [], "ath": 124_659.0,
         "ath_date": pd.Timestamp("2025-10-06"), "drawdown": -41.0,
         "days_since_ath": 402, "mayer": None, "ma200w": None,
         "pi_ratio": None}, etat)
    assert any(label == "MVRV · Coin Metrics" and "réalisé 53,869 $" in valeur
               for label, valeur, _, _ in lignes), lignes
    print("  ✓ MVRV paginé, prix réalisé et rang ; vue et ligne, Coin Metrics nommé")


def test_le_multiple_de_puell():
    """L'émission du jour en dollars sur sa moyenne d'un an : rien avant un
    an d'historique, le rang, l'année d'après un halving signalée ; la vue
    PUELL, ses bornes et l'ombre des halvings ; la ligne du tableau et la
    synthèse, qui ne le dit qu'à ses bornes."""
    from btcterm import brief
    from terminal.charts import build_cycle_chart
    from terminal.panels import cycle as panneau

    jours = pd.date_range("2023-01-01", periods=800, freq="D")
    emission = pd.Series(10e6, index=range(800))
    emission.iloc[-1] = 30e6                       # un jour faste
    serie = pd.DataFrame({"time": jours, "mvrv": 1.5, "price": 50_000.0,
                          "issuance": emission})
    multiple = cycle.puell(serie)
    assert multiple.index[0] == jours[cycle.PUELL_WINDOW - 1], \
        "rien avant un an d'émission"
    attendu = 30e6 / ((364 * 10e6 + 30e6) / 365)
    assert abs(multiple.iloc[-1] - attendu) < 1e-9
    assert abs(multiple.iloc[-2] - 1.0) < 1e-12

    etat = cycle.puell_state(serie)
    assert etat["date"] == jours[-1] and etat["rank"] == 1.0
    # 800 jours depuis le 1er janvier 2023 : le 10 mars 2025, moins d'un an
    # après le halving du 20 avril 2024.
    assert etat["halving"] == pd.Timestamp("2024-04-20")
    tard = serie.assign(time=pd.date_range("2024-01-01", periods=800, freq="D"))
    assert cycle.puell_state(tard)["halving"] is None, "le 10 mars 2026 : plus d'un an"
    assert cycle.puell_state(serie.iloc[:300]) is None
    assert cycle.puell_state(serie.drop(columns="issuance")) is None
    assert cycle.puell(None).empty

    petite = build_cycle_chart("PUELL", pd.Series(dtype=float), mvrv=serie)
    assert [t.name for t in petite.data] == ["Puell"]
    assert {round(f.y0, 2) for f in petite.layout.shapes
            if f.type == "line"} == {cycle.PUELL_LOW, cycle.PUELL_HIGH}
    assert any(f.type == "rect" for f in petite.layout.shapes), "halving ombré"
    assert petite.layout.yaxis.type == "log"
    assert sorted(round(a.y, 3) for a in petite.layout.annotations
                  if a.yref == "y") == [round(np.log10(cycle.PUELL_LOW), 3),
                                        round(np.log10(cycle.PUELL_HIGH), 3)], \
        "sur un axe log, les légendes des bornes se placent en logarithmes"
    assert any("Coin Metrics" in (a.text or "") for a in petite.layout.annotations)
    grande = build_cycle_chart("PUELL", pd.Series(dtype=float), mvrv=serie,
                               maximized=True)
    assert [t.name for t in grande.data] == ["cours", "Puell"]
    assert grande.layout.yaxis2.title.text == "Puell"
    assert grande.layout.yaxis2.type == "log" and grande.layout.yaxis.type == "log"
    vide = build_cycle_chart("PUELL", pd.Series(dtype=float),
                             mvrv=serie.iloc[:10])
    assert not vide.data and "indisponible" in vide.layout.annotations[0].text

    lignes = panneau.rows(None, None, etat)
    assert lignes == [] or lignes[0][0] == "Puell · Coin Metrics"
    complet = panneau.rows({"halving": {"estimated": False,
                                        "eta": pd.Timestamp("2028-04-12"),
                                        "target": 1_050_000, "days_left": 517.0,
                                        "blocks_left": 74_464, "height": 975_536,
                                        "progress": 0.65},
                            "cycle_day": None, "same_day": [], "ath": 124_659.0,
                            "ath_date": pd.Timestamp("2025-10-06"),
                            "drawdown": -41.0, "days_since_ath": 402,
                            "mayer": None, "ma200w": None, "pi_ratio": None},
                           None, etat)
    ((_, valeur, _, aide),) = [l for l in complet if l[0].startswith("Puell")]
    assert valeur == f"{attendu:.2f}" and "subvention double" in aide

    situation = {"cycle_day": None, "drawdown": -41.0, "days_since_ath": 402,
                 "mayer": None, "halving": {"estimated": False,
                                            "days_left": 517.0}}
    calme = brief.cycle_item(situation, None, {**etat, "puell": 1.2})
    assert "Puell" not in calme.text and calme.tone == ""
    creux = brief.cycle_item(situation, None, {**etat, "puell": 0.42})
    assert "Puell 0.42" in creux.text and creux.tone == "warn"
    print("  ✓ Puell : un an de recul, rang, halving signalé ; vue, ligne, synthèse aux bornes")


def test_le_panneau_tient_avec_et_sans_donnees():
    from terminal.charts import CYCLE_VIEWS, build_cycle_chart
    from terminal.panels import cycle as panneau

    vide = pd.Series(dtype=float)
    for vue in CYCLE_VIEWS:
        figure = build_cycle_chart(vue, vide)
        assert not figure.data
        assert "indisponible" in figure.layout.annotations[0].text
    assert "indisponible" in str(panneau.table(None))
    assert "indisponible" in str(panneau.badges(None))

    jours = pd.date_range("2017-08-17", "2026-11-10", freq="D")
    pas = np.random.default_rng(3).normal(0.001, 0.03, len(jours))
    serie = cycle.closes(pd.DataFrame({
        "time": jours, "close": 4_300 * np.exp(np.cumsum(pas))}))
    cycles = build_cycle_chart("CYCLES", serie, maximized=True)
    assert [t.name for t in cycles.data] == [
        "halving 2016", "halving 2020", "halving 2024"]
    assert cycles.layout.yaxis.type == "log"
    # Le cycle en cours se distingue par l'épaisseur, pas par la seule teinte.
    assert cycles.data[-1].line.width > cycles.data[0].line.width
    assert {a.text for a in cycles.layout.annotations} >= {"2016", "2020", "2024"}
    assert len(build_cycle_chart("MAYER", serie).data) == 1
    repli = build_cycle_chart("REPLI", serie).data[0]
    assert max(repli.y) == 0 and min(repli.y) < 0

    etat = cycle.metrics(serie, 974_640, datetime(2026, 11, 10, 12))
    lignes = panneau.rows(etat)
    intitules = [ligne[0] for ligne in lignes]
    assert intitules == ["prochain halving", "dans", "depuis le halving",
                         "au même jour", "sommet", "repli", "Mayer",
                         "MA 200 sem.", "Pi cycle"]
    valeurs = dict((ligne[0], ligne[1]) for ligne in lignes)
    assert valeurs["prochain halving"] == "16 avr. 2028"
    assert valeurs["dans"].startswith("523 j · 1 an 5 mois · 75,360 blocs")
    assert valeurs["depuis le halving"].startswith("J+934 · ×")
    # Hauteur absente : la date porte son tilde.
    estime = panneau.rows(cycle.metrics(serie, None,
                                        datetime(2026, 11, 10, 12)))
    assert estime[0][1].startswith("~")
    # Série courte : les lignes sans mesure disparaissent.
    court = panneau.rows(cycle.metrics(serie.iloc[-30:], 974_640,
                                       datetime(2026, 11, 10, 12)))
    assert "Mayer" not in [ligne[0] for ligne in court]
    texte = str(panneau.badges(etat))
    assert "halving" in texte and "523 j" in texte and "Mayer" in texte
    assert [panneau._days(j) for j in (12, 120, 400, 730)] == [
        "12 j", "120 j · 4 mois", "400 j · 1 an 1 mois", "730 j · 2 ans"]
    print("  ✓ trois vues, tableau et badges, vides comme pleins")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCycle — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le cycle situe, il ne prédit pas.\n")
