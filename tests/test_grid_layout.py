#!/usr/bin/env python3
"""
Vérifie la logique du rangement configurable des panneaux.

Le rangement vit dans le localStorage du navigateur : il peut donc dater
d'avant un renommage de panneau, avoir été altéré, ou simplement ne pas
exister. `normalize_placement` doit rendre un rangement exploitable dans
tous ces cas — c'est la classe de défaut qui casserait le rendu d'une
cellule au chargement, sans qu'aucun test d'interface ne la provoque :
le navigateur du contrôle visuel part d'un localStorage sain.

Aucun réseau n'est touché : tout est logique pure.

Lancement :
    python tests/test_grid_layout.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from terminal.grid import (AREA_LABELS, AREAS, CELLS,  # noqa: E402
                           DEFAULT_PLACEMENT, HOME_AREA, PANEL_REGISTRY,
                           active_panel, normalize_placement, reveal)
from terminal.placement import (DIALOG_COLUMNS,  # noqa: E402
                                placement_from_choices)

TOUS = set(PANEL_REGISTRY)


def _panneaux(placement) -> list[str]:
    return [p for panels in placement.values() for p in panels]


def test_le_rangement_par_defaut_est_coherent():
    """Chaque panneau de CELLS a exactement une place par défaut."""
    ranges = _panneaux(DEFAULT_PLACEMENT)
    assert sorted(ranges) == sorted(TOUS), "panneau sans place ou rangé deux fois"
    assert set(DEFAULT_PLACEMENT) == set(AREAS)
    assert all(DEFAULT_PLACEMENT[area] for area in AREAS), "cellule vide par défaut"
    print(f"  ✓ {len(ranges)} panneaux, chacun une place")


def test_absence_de_rangement_rend_le_defaut():
    """Premier chargement : pas de localStorage, donc le défaut."""
    for donnee in (None, [], "corrompu", 42):
        assert normalize_placement(donnee) == dict(DEFAULT_PLACEMENT), donnee
    print("  ✓ None, liste, chaîne, nombre → rangement par défaut")


def test_un_panneau_renomme_est_ecarte():
    """Un identifiant inconnu — panneau renommé ou disparu — est ignoré."""
    donnee = {area: list(panels) for area, panels in DEFAULT_PLACEMENT.items()}
    donnee["news"] = ["fear-greed-2019"] + donnee["news"]
    propre = normalize_placement(donnee)
    assert "fear-greed-2019" not in _panneaux(propre)
    assert sorted(_panneaux(propre)) == sorted(TOUS)
    print("  ✓ identifiant inconnu écarté, le reste intact")


def test_un_panneau_range_deux_fois_garde_sa_premiere_place():
    donnee = {area: list(panels) for area, panels in DEFAULT_PLACEMENT.items()}
    donnee["book"] = donnee["book"] + ["cal"]  # cal vit aussi dans news
    propre = normalize_placement(donnee)
    assert _panneaux(propre).count("cal") == 1
    assert "cal" in propre["book"], "la première place ne l'a pas emporté"
    print("  ✓ doublon réduit à sa première place")


def test_un_panneau_oublie_revient_chez_lui():
    """Un rangement d'avant l'ajout d'un panneau ne doit pas le perdre."""
    donnee = {area: [p for p in panels if p != "perp"]
              for area, panels in DEFAULT_PLACEMENT.items()}
    propre = normalize_placement(donnee)
    assert "perp" in propre[HOME_AREA["perp"]]
    assert sorted(_panneaux(propre)) == sorted(TOUS)
    print(f"  ✓ panneau absent rendu à sa cellule d'origine ({HOME_AREA['perp']})")


def test_un_demenagement_est_conserve():
    """Le cas nominal : un panneau déplacé reste où on l'a mis."""
    donnee = {area: [p for p in panels if p != "cal"]
              for area, panels in DEFAULT_PLACEMENT.items()}
    donnee["macro"] = donnee["macro"] + ["cal"]
    propre = normalize_placement(donnee)
    assert "cal" in propre["macro"]
    assert "cal" not in propre["news"]
    print("  ✓ calendrier déménagé dans la cellule macro, et il y reste")


def test_une_cellule_vide_rend_le_defaut():
    """Le dialogue refuse de vider une cellule ; un localStorage périmé
    pourrait quand même en contenir une — le tout retombe alors sur le
    défaut plutôt que de rendre une cellule sans contenu."""
    donnee = {area: [] if area == "book" else list(panels)
              for area, panels in DEFAULT_PLACEMENT.items()}
    assert normalize_placement(donnee) == dict(DEFAULT_PLACEMENT)
    print("  ✓ cellule vide → rangement par défaut")


def test_les_choix_du_dialogue_suivent_l_ordre_du_registre():
    """L'ordre des onglets d'une cellule est l'ordre d'affichage du
    dialogue, quel que soit l'ordre des clics."""
    panel_ids = list(PANEL_REGISTRY)
    chosen = [HOME_AREA[p] for p in panel_ids]
    reconstruit = placement_from_choices(panel_ids, chosen)
    assert {a: tuple(p) for a, p in reconstruit.items()} == dict(DEFAULT_PLACEMENT)

    # Tout envoyer dans la cellule macro : l'ordre reste celui du registre.
    tout_en_bas = placement_from_choices(panel_ids, ["macro"] * len(panel_ids))
    assert tout_en_bas["macro"] == panel_ids
    assert [a for a in AREAS if not tout_en_bas[a]], "aucune cellule vidée ?"
    print("  ✓ ordre du registre préservé, cellules vidées détectables")


def test_une_cellule_inconnue_rend_le_panneau_chez_lui():
    """Un identifiant de cellule aberrant ne perd pas le panneau."""
    reconstruit = placement_from_choices(["cal"], ["cellule-fantome"])
    assert "cal" in reconstruit[HOME_AREA["cal"]]
    print("  ✓ cellule inconnue → cellule d'origine")


def test_le_dialogue_couvre_toutes_les_cellules():
    """Une cellule absente des colonnes du dialogue serait une cellule
    vers laquelle aucun panneau ne peut déménager — et un libellé absent
    la rendrait innommable."""
    assert set(DIALOG_COLUMNS) == set(AREAS)
    assert set(AREA_LABELS) == set(AREAS)
    print("  ✓ chaque cellule a sa colonne et son nom")


def test_montrer_un_panneau_le_trouve_ou_qu_il_soit():
    """La cloche du bandeau ouvre le panneau alertes — encore faut-il
    savoir dans quelle cellule il vit. Un rangement configurable peut
    l'avoir déplacé, et rien ne doit dépendre de sa cellule d'origine."""
    choix = reveal("alerts", None, None)
    assert choix[HOME_AREA["alerts"]] == "alerts"

    demenage = {area: [p for p in panels if p != "alerts"]
                for area, panels in DEFAULT_PLACEMENT.items()}
    demenage["arb"] = demenage["arb"] + ["alerts"]
    choix = reveal("alerts", {"arb": "liq"}, demenage)
    assert choix["arb"] == "alerts"
    assert choix != {"arb": "liq"}, "l'onglet précédent n'a pas cédé"
    print("  ✓ le panneau est retrouvé dans sa cellule du moment")


def test_montrer_un_panneau_deja_visible_ne_change_rien():
    """`None` vaut `no_update` : réécrire le Store re-rendrait la cellule
    — et remonter un graphique lui fait perdre son zoom."""
    assert reveal("alerts", {"news": "alerts"}, None) is None
    # Un onglet retenu qui n'existe plus : la cellule montre son premier
    # panneau, et `reveal` doit raisonner sur ce qui est *affiché*.
    tabs = {"news": "fear-greed-2019"}
    assert active_panel("news", tabs, dict(DEFAULT_PLACEMENT)) == "news"
    assert reveal("news", tabs, None) is None
    print("  ✓ panneau déjà à l'écran → aucun changement d'onglets")


def test_cells_reste_la_reference():
    """Le registre et le rangement par défaut dérivent de CELLS : un
    panneau ajouté à CELLS est automatiquement rangeable."""
    declares = {panel_id for panels in CELLS.values()
                for panel_id, _, _ in panels}
    assert declares == TOUS
    assert set(HOME_AREA) == TOUS
    print("  ✓ registre, cellules d'origine et défaut dérivés de CELLS")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nRangement des panneaux — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le rangement configurable est cohérent.\n")
