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
