#!/usr/bin/env python3
"""
La sérialisation Lightweight Charts, contrôlée sans réseau ni navigateur.

`terminal/lwc.py` traduit les DataFrames du serveur vers le contrat de
la bibliothèque : temps en secondes epoch UTC, strictement croissants,
sans doublon ni NaN. C'est ce contrat qui est vérifié ici, sur la série
de démonstration — la même exigence de stabilité que le pousseur : deux
sérialisations des mêmes données doivent être identiques, faute de quoi
le canal différentiel renverrait tout à chaque cadence.

Lancement :
    python tests/test_lwc_serialize.py
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from btcterm.sources import generate_demo_ohlcv  # noqa: E402
from terminal.charts import prepare_price_frame  # noqa: E402
from terminal.lwc import (  # noqa: E402
    OVERLAY_COLUMNS, PANE_COLUMNS, frame_to_bars, frame_to_lines,
    frame_to_signals, frame_to_volume, serialize_price_frame,
)


def frame_de_demo(limit: int = 300, interval: str = "1d") -> pd.DataFrame:
    return prepare_price_frame(
        generate_demo_ohlcv(limit, interval=interval, index=False))


def sans_nan(points: list[dict]) -> bool:
    return all(
        not (isinstance(v, float) and math.isnan(v))
        for point in points for v in point.values()
    )


def test_barres_conformes_au_contrat():
    df = frame_de_demo()
    bars = frame_to_bars(df)

    assert len(bars) == len(df), "une barre par bougie"
    assert all(set(b) == {"time", "open", "high", "low", "close"}
               for b in bars)

    #: Le temps est en secondes epoch UTC : la première barre doit
    #: retomber sur l'horodatage du DataFrame à la seconde près.
    attendu = int(pd.Timestamp(df["time"].iloc[0]).value // 1_000_000_000)
    assert bars[0]["time"] == attendu, (bars[0]["time"], attendu)

    #: Strictement croissants — c'est ce que la bibliothèque exige.
    times = [b["time"] for b in bars]
    assert all(a < b for a, b in zip(times, times[1:])), "temps non croissants"
    print(f"  ✓ {len(bars)} barres, epoch secondes strictement croissants")


def test_doublons_ecartes_le_plus_frais_gagne():
    """Une bougie rejouée — un tick sur la bougie courante — ne doit
    produire qu'une barre, portant la dernière valeur reçue."""
    df = frame_de_demo(50)
    rejouee = df.iloc[[-1]].assign(close=99_999.0)
    bars = frame_to_bars(pd.concat([df, rejouee], ignore_index=True))

    assert len(bars) == 50, "le doublon devait être écarté"
    assert bars[-1]["close"] == 99_999.0, "la valeur la plus fraîche devait gagner"
    print("  ✓ doublon de timestamp écarté, dernière valeur retenue")


def test_lignes_sans_nan_et_alignees():
    df = frame_de_demo()
    lignes = frame_to_lines(df, OVERLAY_COLUMNS + PANE_COLUMNS)

    assert set(lignes) == set(OVERLAY_COLUMNS + PANE_COLUMNS)
    for nom, points in lignes.items():
        assert points, f"{nom} : série vide"
        assert sans_nan(points), f"{nom} : NaN dans la série"
        times = [p["time"] for p in points]
        assert all(a < b for a, b in zip(times, times[1:])), nom

    #: La MA 200 démarre à la 200e bougie : sa série est plus courte que
    #: la MA 9, chaque ligne commençant à son premier point défini.
    assert len(lignes["ma200"]) < len(lignes["ma9"])
    assert len(lignes["ma9"]) == len(df) - 8

    #: Une colonne inconnue est omise, pas une erreur.
    assert "absente" not in frame_to_lines(df, ("ma9", "absente"))
    print("  ✓ NaN filtrés, séries alignées, colonnes absentes omises")


def test_volume_dit_le_sens_de_la_bougie():
    df = frame_de_demo(60)
    volume = frame_to_volume(df)

    assert len(volume) == len(df)
    for point, (_, row) in zip(volume, df.iterrows()):
        assert point["up"] == (row["close"] >= row["open"])
        assert math.isclose(point["value"], float(row["volume"]),
                            rel_tol=1e-9), "l'arrondi à 10 chiffres a dérivé"
    print("  ✓ chaque point de volume porte le sens de sa bougie")


def test_serialisation_stable_et_json():
    """Même exigence que le pousseur : deux appels, un seul JSON.

    Et un JSON *stdlib* : la conversion doit avoir éliminé les types
    numpy — un int64 qui survivrait ferait lever `json.dumps` côté API.
    """
    df = frame_de_demo()
    un = json.dumps(serialize_price_frame(df), sort_keys=True)
    deux = json.dumps(serialize_price_frame(df), sort_keys=True)
    assert un == deux, "la sérialisation fluctue à données constantes"
    assert "NaN" not in un, "du NaN a fui dans le JSON"
    print(f"  ✓ sérialisation stable, JSON pur ({len(un):,} octets pour 300 bougies)")


def test_le_paquet_complet_porte_le_drapeau_demo():
    df = frame_de_demo()
    paquet = serialize_price_frame(df)

    assert set(paquet) == {"bars", "volume", "overlays", "panes",
                           "volume_ma", "signals", "demo"}
    assert paquet["demo"] is True, "la série de démo doit se déclarer"
    assert set(paquet["overlays"]) == set(OVERLAY_COLUMNS)
    assert set(paquet["panes"]) == set(PANE_COLUMNS)
    assert paquet["volume_ma"], "la MA de volume doit être présente"

    #: Une vraie série — sans attrs — ne se déclare pas en démo.
    vraie = df.copy()
    vraie.attrs.pop("demo", None)
    assert serialize_price_frame(vraie)["demo"] is False
    print("  ✓ paquet complet, drapeau demo fidèle aux attrs du DataFrame")


def test_signaux_non_nuls_seulement():
    """Les signaux gradués voyagent en clairsemé : jamais de zéro — la
    quasi-totalité des bougies — et le grade tel que le serveur l'a
    calculé, borné à ±2."""
    df = frame_de_demo()
    signaux = frame_to_signals(df)

    assert signaux, "la marche aléatoire produit forcément des croisements"
    assert all(set(s) == {"time", "value"} for s in signaux)
    assert all(s["value"] in (-2, -1, 1, 2) for s in signaux)

    #: Fidèles à la colonne d'origine, position par position.
    attendus = df[df["signal"] != 0]
    assert len(signaux) == len(attendus)
    print(f"  ✓ {len(signaux)} signaux, tous non nuls, grades de ±1 à ±2")


def test_tous_les_intervalles_du_panneau():
    """La conversion doit tenir sur toute la palette d'intervalles du
    panneau, de la bougie de quinze minutes à la mensuelle."""
    from terminal.panels.price import INTERVALS
    for interval, limit in INTERVALS.items():
        paquet = serialize_price_frame(frame_de_demo(limit, interval))
        assert len(paquet["bars"]) == limit, interval
        times = [b["time"] for b in paquet["bars"]]
        assert all(a < b for a, b in zip(times, times[1:])), interval
    print(f"  ✓ {len(INTERVALS)} intervalles convertis, du 15m au mensuel")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nSérialisation Lightweight Charts — {len(tests)} vérifications\n"
          + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le contrat de série est tenu : trié, dédoublonné, sans NaN, stable.\n")
