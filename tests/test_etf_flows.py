#!/usr/bin/env python3
"""
Vérifie la page des flux ETF : chiffres clés, figure et barre de titre.

Le panneau lit un tableau public dont la forme peut changer sans
prévenir ; ce que ce test protège, c'est ce que le terminal en déduit :

- le **stock** se compte sur tout l'historique, jamais sur la fenêtre
  affichée — c'est la quantité détenue par les ETF, elle ne dépend pas
  de la période qu'on regarde ;
- le **classement par émetteur**, lui, se compte sur la fenêtre : c'est
  la question « qui achète en ce moment », et le total la masque, GBTC
  décollectant pendant qu'IBiT encaisse ;
- la barre de titre est muette dans la grille et bavarde en plein écran,
  sans séparateur orphelin ni chiffre répété.

Aucun réseau n'est touché : le tableau est fabriqué.

Lancement :
    python tests/test_etf_flows.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from terminal.charts import (ETF_WINDOWS, build_etf_chart,  # noqa: E402
                             etf_issuers, etf_stats, etf_total_column,
                             format_flow)
from terminal.panels import etf as panneau  # noqa: E402


def flux() -> pd.DataFrame:
    """Vingt jours ouvrés, deux collecteurs et un décollecteur.

    IBIT encaisse tous les jours, GBTC saigne tous les jours, MSBT ne
    fait rien — le cas qui doit disparaître du classement plutôt que d'y
    figurer à zéro.
    """
    jours = pd.bdate_range("2026-01-01", periods=20)
    ibit = [100.0] * 20
    gbtc = [-40.0] * 20
    ibit[5] = 900.0        # record d'entrée
    gbtc[12] = -800.0      # record de sortie
    frame = pd.DataFrame({"Date": jours, "IBIT": ibit, "GBTC": gbtc,
                          "MSBT": [0.0] * 20})
    frame["Total"] = frame[["IBIT", "GBTC", "MSBT"]].sum(axis=1)
    return frame


def test_colonnes_reconnues():
    frame = flux()
    assert etf_total_column(frame) == "Total"
    assert etf_issuers(frame) == ["IBIT", "GBTC", "MSBT"]
    print("  ✓ total et émetteurs distingués")


def test_le_stock_ignore_la_fenetre():
    """Le cumul depuis le lancement ne bouge pas avec la fenêtre."""
    frame = flux()
    entier = etf_stats(frame)["cumul"]
    for jours in (5, 10, 20, None):
        stats = etf_stats(frame, jours)
        assert stats["cumul"] == entier, \
            "le stock doit se compter sur tout l'historique"
        assert stats["last"] == 60.0, "le dernier jour reste le dernier jour"
    print(f"  ✓ stock constant à {format_flow(entier)} quelle que soit la fenêtre")


def test_la_fenetre_borne_le_net_et_les_records():
    frame = flux()
    court = etf_stats(frame, 5)
    assert court["days"] == 5
    assert court["window"] == 5 * 60.0, "cinq jours ordinaires à +60 M$"
    assert court["best"][1] == 60.0 and court["worst"][1] == 60.0, \
        "aucun choc dans les cinq derniers jours"

    tout = etf_stats(frame, None)
    assert tout["best"][1] == 860.0 and tout["worst"][1] == -700.0, \
        "les records de l'historique doivent ressortir sur la fenêtre pleine"
    assert tout["full"] is True and court["full"] is False
    print("  ✓ net et records suivent la fenêtre, pas l'historique")


def test_la_figure_se_deplie_en_plein_ecran():
    """Une trace dans la grille, trois agrandie : barres, cumul, classement."""
    frame = flux()
    assert len(build_etf_chart(frame, 10).data) == 1, \
        "la vignette ne montre que les barres"

    grande = build_etf_chart(frame, 10, maximized=True)
    types = [trace.type for trace in grande.data]
    assert types == ["bar", "scatter", "bar"], types

    classement = grande.data[2]
    assert "MSBT" not in classement.y, \
        "un émetteur immobile n'a rien à faire dans le classement"
    assert list(classement.y) == ["GBTC", "IBIT"], \
        "trié croissant : le plus gros collecteur se lit en haut"
    print("  ✓ barres seules dans la grille, trois volets en plein écran")


def test_le_cumul_est_celui_du_lancement():
    """La courbe part du stock accumulé, pas de zéro à l'entrée de fenêtre."""
    frame = flux()
    grande = build_etf_chart(frame, 5, maximized=True)
    cumul = grande.data[1].y
    assert len(cumul) == 5
    # En Md$ dans la figure ; les quinze jours antérieurs sont déjà là.
    assert abs(cumul[0] * 1000 - frame["Total"].head(16).sum()) < 1e-6, \
        "le premier point doit inclure l'historique antérieur à la fenêtre"
    print("  ✓ la courbe de cumul continue l'historique")


def test_source_muette():
    figure = build_etf_chart(pd.DataFrame(), maximized=True)
    assert not figure.data
    assert "indisponibles" in figure.layout.annotations[0].text
    print("  ✓ tableau vide : cadre expliqué, pas d'exception")


def test_barre_de_titre():
    """Compacte dans la grille, dépliée agrandie, sans redite ni orphelin."""
    frame = flux()
    compact = str(panneau._badges(etf_stats(frame, 5), "30 J", verbose=False))
    assert "30 J : " in compact and " · " not in compact, \
        "pas de séparateur devant le premier chiffre"
    assert "record" not in compact

    long = str(panneau._badges(etf_stats(frame, 5), "30 J", verbose=True))
    for attendu in ("5 j", "stock", "record d'entrée", "record de sortie"):
        assert attendu in long, f"« {attendu} » manque en plein écran"

    plein = str(panneau._badges(etf_stats(frame, None), "TOUT", verbose=True))
    assert "stock" not in plein, \
        "sur la fenêtre pleine, net et stock sont le même chiffre"

    # La fenêtre en bitcoins face à l'émission : en plein écran seulement.
    from btcterm import cycle
    stats = etf_stats(frame, 5)
    stats["supply"] = cycle.etf_supply(
        frame, pd.Series([50_000.0], index=[frame["Date"].iloc[0]]), 5)
    assert "l'émission" in str(panneau._badges(stats, "30 J", verbose=True))
    assert "l'émission" not in str(panneau._badges(stats, "30 J", verbose=False))
    print("  ✓ barre de titre : compacte, dépliée, sans chiffre répété")


def test_fenetres_declarees():
    assert list(ETF_WINDOWS) == ["30 J", "90 J", "1 AN", "TOUT"]
    assert ETF_WINDOWS["TOUT"] is None, "« tout » ne borne pas la fenêtre"
    print("  ✓ quatre fenêtres, la dernière sans borne")


def test_une_ligne_sans_chiffres_n_est_pas_une_seance():
    """La ligne du jour, posée avant d'être publiée, et celle d'un jour
    férié ne sont pas des séances à zéro.

    Constaté le 11 novembre 2026 : « +0 M$ le 10/11 » à la synthèse.
    """
    import pandas as pd

    from btcterm.sources import clean_etf_table

    brut = pd.DataFrame(
        [["Fee", "0.25%", "0.25%", "0.20%", "0.21%", ""],
         ["05 Nov 2026", "(105.0)", "(199.9)", "0.0", "-", "(304.9)"],
         ["06 Nov 2026", "(403.0)", "42.8", "(20.5)", "1,020.1", "639.4"],
         ["09 Nov 2026", "-", "-", "-", "-", "0.0"],          # férié
         ["10 Nov 2026", "-", "-", "-", "0.0", "0.0"],        # pas publié
         ["Total", "1.0", "2.0", "3.0", "4.0", "10.0"]],
        columns=["Unnamed: 0", "IBIT", "FBTC", "BITB", "ARKB", "Total"])
    propre = clean_etf_table(brut)
    assert [f"{d:%d/%m}" for d in propre["Date"]] == ["05/11", "06/11"], propre
    assert propre["Total"].tolist() == [-304.9, 639.4]
    assert propre["ARKB"].tolist() == [0.0, 1020.1], \
        "un tiret isolé reste un zéro, les milliers se lisent"
    assert brut.shape == (6, 6) and "Unnamed: 0" in brut.columns, \
        "le tableau reçu n'est pas modifié"
    # Une ligne à moitié publiée reste : elle se complète dans la soirée.
    brut.iloc[4] = ["10 Nov 2026", "55.0", "-", "-", "0.0", "55.0"]
    assert clean_etf_table(brut)["Total"].tolist() == [-304.9, 639.4, 55.0]
    print("  ✓ ni la ligne du jour vide ni le jour férié ne comptent pour une séance")



def brut_du_12_novembre() -> pd.DataFrame:
    """La fin du tableau lue le 12 novembre 2026 au matin, telle quelle :
    le 11 encore en cours de publication — IBIT, FBTC et HODL en tirets,
    les petits fonds déjà à 0.0 —, et une colonne lancée récemment (MSBT)
    en tirets sur les jours d'avant son lancement."""
    return pd.DataFrame(
        [["Fee", "0.25%", "0.25%", "0.20%", "0.21%", "0.25%", "0.25%",
          "0.15%", "1.50%", ""],
         ["05 Jan 2026", "12.0", "3.0", "0.0", "0.0", "0.0", "0.0", "-",
          "0.0", "15.0"],
         ["09 Nov 2026", "(213.2)", "0.0", "0.0", "(9.9)", "0.0", "0.0",
          "22.1", "0.0", "(201.0)"],
         ["10 Nov 2026", "(78.2)", "(36.8)", "0.0", "0.0", "0.0", "0.0",
          "20.0", "0.0", "(95.0)"],
         ["11 Nov 2026", "-", "-", "0.0", "0.0", "0.0", "-", "9.0",
          "(39.1)", "(30.1)"],
         ["Total", "64810", "10238", "2027", "941", "140", "985", "865",
          "(28,149)", "55440"]],
        columns=["Unnamed: 0", "IBIT", "FBTC", "BITB", "ARKB", "BTCO",
                 "HODL", "MSBT", "GBTC", "Total"])


def test_une_journee_partielle_le_dit():
    """La ligne du jour se remplit émetteur après émetteur, IBIT parmi les
    derniers : gardée, elle est dite partielle partout où elle s'affiche.

    Constaté le 12 novembre 2026 : « −30 M$ le 11/11 », IBIT absent.
    """
    from btcterm import brief
    from btcterm.sources import clean_etf_table, etf_pending

    propre = clean_etf_table(brut_du_12_novembre())
    assert propre["Total"].iloc[-1] == -30.1, "la ligne partielle reste"
    assert etf_pending(propre) == ["IBIT", "FBTC", "HODL"], etf_pending(propre)
    assert propre["MSBT"].iloc[0] == 0.0, "un fonds pas encore lancé reste un zéro"
    assert etf_pending(propre.tail(30)) == ["IBIT", "FBTC", "HODL"], \
        "la liste suit le tableau découpé"

    # La journée complétée : plus rien en attente.
    complet = brut_du_12_novembre()
    complet.iloc[4] = ["11 Nov 2026", "(150.0)", "12.0", "0.0", "0.0",
                       "0.0", "0.0", "9.0", "(39.1)", "(168.1)"]
    assert etf_pending(clean_etf_table(complet)) == []
    assert etf_pending(flux()) == [], "un tableau fabriqué n'attend rien"
    assert etf_pending(None) == []

    stats = etf_stats(propre, 30)
    assert stats["pending"] == ["IBIT", "FBTC", "HODL"]
    for verbose in (False, True):
        barre = str(panneau._badges(stats, "30 J", verbose=verbose))
        assert "partiel" in barre and "IBIT, FBTC, HODL pas encore publiés" in barre, \
            f"la barre de titre doit le dire (verbose={verbose}) : {barre}"
    assert "partiel" not in str(panneau._badges(
        etf_stats(clean_etf_table(complet), 30), "30 J", verbose=True))

    barres = build_etf_chart(propre, 30).data[0]
    assert list(barres.marker.opacity)[-1] < 1 == list(barres.marker.opacity)[-2], \
        "la barre du jour partiel est pâlie, les autres non"
    assert "IBIT, FBTC, HODL à venir" in barres.customdata[-1]
    assert barres.customdata[-2] == ""

    ligne = brief.etf_item(propre)
    assert ligne.text == ("-30 M$ le 11/11, partiel (IBIT, FBTC, HODL à venir)"
                          " · 5 séances -311 M$"), ligne.text
    assert ligne.tone == "warn"
    print("  ✓ IBIT, FBTC et HODL en attente : barre de titre, barre pâlie,"
          " synthèse")

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nFlux ETF — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("La page des flux ETF dit ce qu'elle montre.\n")
