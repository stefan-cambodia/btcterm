#!/usr/bin/env python3
"""
Vérifie le câblage du terminal : tout panneau déclaré doit être posé dans
la grille **et** avoir ses callbacks enregistrés.

C'est la classe d'erreur qui a laissé le panneau ETF muet à sa création :
il était écrit, mais ni placé dans la grille ni enregistré, et rien ne le
signalait — l'application démarrait normalement, le panneau restait
simplement absent.

Aucun réseau n'est touché : le hub est construit sans être démarré.

Lancement :
    python tests/test_terminal_wiring.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib  # noqa: E402
import pkgutil  # noqa: E402

from btcterm.hub import MarketHub  # noqa: E402
from terminal.app import CELLS, DEFAULT_PLACEMENT, create_app  # noqa: E402
from terminal.app import _body  # noqa: E402
from terminal.panels import PANELS  # noqa: E402


def discover_panels():
    """Modules de panneaux présents sur le disque.

    La découverte ne passe surtout pas par `PANELS` : un test qui
    parcourrait `PANELS` ne verrait pas un panneau qu'on aurait oublié
    d'y inscrire — c'est précisément le défaut à détecter.
    """
    import terminal.panels as package

    return [
        importlib.import_module(f"terminal.panels.{info.name}")
        for info in pkgutil.iter_modules(package.__path__)
        if not info.name.startswith("_")
    ]


DISCOVERED = discover_panels()


def collect_ids(node, found=None):
    """Identifiants de tous les composants d'un arbre de layout Dash."""
    found = found if found is not None else set()
    if isinstance(node, (list, tuple)):
        for child in node:
            collect_ids(child, found)
    elif hasattr(node, "children") or hasattr(node, "id"):
        component_id = getattr(node, "id", None)
        if isinstance(component_id, str):
            found.add(component_id)
        collect_ids(getattr(node, "children", None), found)
    return found


def panel_ids(panel) -> set[str]:
    """Identifiants déclarés par un panneau, layouts secondaires compris."""
    ids = collect_ids(panel.layout())
    for name in dir(panel):
        if name.endswith("_layout"):
            ids |= collect_ids(getattr(panel, name)())
    return ids


HUB = MarketHub()
APP = create_app(HUB)

#: Identifiants présents dans la page, onglets compris. Une cellule ne
#: rend que son panneau actif — c'est ce qui rend les onglets gratuits —
#: mais un panneau derrière un onglet reste posé dans la grille, et le
#: test doit le voir comme tel.
APP_IDS = collect_ids(APP.layout)
for _area, _panels in CELLS.items():
    for _panel_id, _label, _layout in _panels:
        APP_IDS |= collect_ids(_body(_area, _panel_id, DEFAULT_PLACEMENT))

CALLBACK_KEYS = " ".join(APP.callback_map)


def test_tous_les_panneaux_sont_declares():
    """Tout module de panneau sur le disque doit figurer dans `PANELS`."""
    assert DISCOVERED, "aucun module de panneau découvert"
    oublies = [p.__name__ for p in DISCOVERED if p not in PANELS]
    assert not oublies, f"panneaux écrits mais absents de PANELS : {oublies}"
    print(f"  ✓ {len(DISCOVERED)} panneaux découverts, tous déclarés")


def test_panneaux_places_dans_la_grille():
    """Tout identifiant déclaré par un panneau doit exister dans la page."""
    for panel in DISCOVERED:
        name = panel.__name__.rsplit(".", 1)[-1]
        manquants = panel_ids(panel) - APP_IDS
        assert not manquants, f"panneau {name} : {sorted(manquants)} absents de la grille"
        print(f"  ✓ {name:10s} posé ({len(panel_ids(panel))} composants)")


def test_panneaux_enregistres():
    """Tout panneau doit piloter au moins une sortie de callback."""
    for panel in DISCOVERED:
        name = panel.__name__.rsplit(".", 1)[-1]
        pilotes = [i for i in panel_ids(panel) if f"{i}." in CALLBACK_KEYS]
        assert pilotes, f"panneau {name} : aucun callback enregistré"
        print(f"  ✓ {name:10s} branché ({len(pilotes)} sorties)")


def test_tous_les_layouts_sont_atteignables():
    """Tout layout écrit doit être posé dans une cellule.

    Depuis les onglets, un panneau peut exister, être enregistré, et
    n'être atteignable par aucun clic — l'équivalent moderne du panneau
    oublié. `CELLS` est la seule liste qui décide de ce qu'on peut
    afficher : c'est donc elle qu'on compare aux layouts écrits.
    """
    poses = {fonction for panels in CELLS.values() for _, _, fonction in panels}
    for panel in DISCOVERED:
        name = panel.__name__.rsplit(".", 1)[-1]
        ecrits = {getattr(panel, attribut) for attribut in dir(panel)
                  if attribut == "layout" or attribut.endswith("_layout")}
        orphelins = ecrits - poses
        assert not orphelins, f"panneau {name} : layout écrit mais absent de CELLS"
        print(f"  ✓ {name:10s} atteignable ({len(ecrits)} layout(s))")


def test_horloges_declarees():
    """Les trois régimes de rafraîchissement doivent être présents."""
    for tick in ("tick-fast", "tick-slow", "tick-rare"):
        assert tick in APP_IDS, f"horloge {tick} absente"
    print("  ✓ tick-fast / tick-slow / tick-rare")


def test_entete_branche():
    for component_id in ("hdr-price", "hdr-change", "hdr-spread", "hdr-status"):
        assert component_id in APP_IDS, f"{component_id} absent de l'en-tête"
        assert f"{component_id}." in CALLBACK_KEYS, f"{component_id} sans callback"
    print("  ✓ bandeau supérieur")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCâblage du terminal — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Tous les panneaux sont posés et branchés.\n")
