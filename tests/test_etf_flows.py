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
    print("  ✓ barre de titre : compacte, dépliée, sans chiffre répété")


def test_fenetres_declarees():
    assert list(ETF_WINDOWS) == ["30 J", "90 J", "1 AN", "TOUT"]
    assert ETF_WINDOWS["TOUT"] is None, "« tout » ne borne pas la fenêtre"
    print("  ✓ quatre fenêtres, la dernière sans borne")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nFlux ETF — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("La page des flux ETF dit ce qu'elle montre.\n")
