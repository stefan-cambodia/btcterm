#!/usr/bin/env python3
"""
Vérifie la bascule plein écran des panneaux.

Cette logique vit dans un callback *clientside* : elle s'exécute dans le
navigateur, hors de portée des tests Python. On extrait donc la fonction
de `terminal/app.py` et on la fait tourner sous Node avec un faux
`dash_clientside`, ce qui la couvre sans navigateur.

Le test est ignoré si Node n'est pas installé.

Lancement :
    python tests/test_fullscreen_toggle.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from terminal.app import AREAS  # noqa: E402

APP_SOURCE = Path(__file__).resolve().parent.parent / "terminal" / "app.py"

HARNESS = """
let resized = 0;
global.setTimeout = (fn) => { fn(); };
global.window = { dispatchEvent: () => { resized++; } };
global.Event = function (name) { this.name = name; };
global.dash_clientside = { no_update: "NO_UPDATE", callback_context: { triggered: [] } };

const AREAS = %(areas)s;
const results = {};

function click(area, current) {
    dash_clientside.callback_context.triggered = [{ prop_id: `zoom-${area}.n_clicks` }];
    return toggle(...AREAS.map(() => 1), current);
}

let r = click(AREAS[0], null);
results.agrandi_etat = r[0];
results.agrandi_classes = r.slice(1);

r = click(AREAS[0], AREAS[0]);
results.restaure_etat = r[0];
results.restaure_classes = r.slice(1);

r = click(AREAS[1], AREAS[0]);
results.bascule_etat = r[0];
results.bascule_classes = r.slice(1);

dash_clientside.callback_context.triggered = [];
results.sans_declencheur = toggle(...AREAS.map(() => 0), null);

results.resizes = resized;
results.max_par_zone = AREAS.map((a) =>
    click(a, null).slice(1).filter((c) => c.includes("cell-max")).length);

console.log(JSON.stringify(results));
"""


def extract_js() -> str:
    """Isole le corps de la fonction clientside et résout son gabarit."""
    source = APP_SOURCE.read_text()
    start = source.index('        """\n        function (...args)')
    end = source.index('        """ % {"areas"', start)
    body = source[start + len('        """\n'):end]
    return body.replace("%(areas)s", json.dumps(list(AREAS)))


def run_in_node() -> dict:
    script = (
        "const toggle = " + extract_js().strip() + ";\n"
        + HARNESS % {"areas": json.dumps(list(AREAS))}
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "toggle.js"
        path.write_text(script)
        completed = subprocess.run(
            ["node", str(path)], capture_output=True, text=True, timeout=30
        )
    if completed.returncode != 0:
        raise AssertionError(f"Node a échoué :\n{completed.stderr}")
    return json.loads(completed.stdout)


RESULTS = run_in_node() if shutil.which("node") else None


def test_agrandir_isole_le_panneau():
    """Un clic agrandit le panneau visé et masque tous les autres."""
    assert RESULTS["agrandi_etat"] == AREAS[0]
    classes = RESULTS["agrandi_classes"]
    assert classes[0] == "cell cell-max"
    assert all(c == "cell cell-hidden" for c in classes[1:]), classes
    print(f"  ✓ {AREAS[0]} agrandi, {len(classes) - 1} panneaux masqués")


def test_recliquer_restaure_la_grille():
    assert RESULTS["restaure_etat"] is None
    assert all(c == "cell" for c in RESULTS["restaure_classes"])
    print("  ✓ second clic : retour à la grille complète")


def test_bascule_directe_entre_panneaux():
    """Passer d'un panneau agrandi à un autre sans repasser par la grille."""
    assert RESULTS["bascule_etat"] == AREAS[1]
    classes = RESULTS["bascule_classes"]
    assert classes[1] == "cell cell-max" and classes[0] == "cell cell-hidden"
    print(f"  ✓ {AREAS[0]} → {AREAS[1]} directement")


def test_sans_declencheur_rien_ne_bouge():
    assert RESULTS["sans_declencheur"] == "NO_UPDATE"
    print("  ✓ aucun déclencheur : no_update")


def test_plotly_est_previenu():
    """Sans événement `resize`, un graphique agrandi garderait sa taille."""
    assert RESULTS["resizes"] == 3, RESULTS["resizes"]
    print("  ✓ resize émis à chaque bascule")


def test_toutes_les_zones_sont_couvertes():
    assert all(count == 1 for count in RESULTS["max_par_zone"]), RESULTS["max_par_zone"]
    print(f"  ✓ les {len(AREAS)} zones s'agrandissent chacune seule")


if __name__ == "__main__":
    if RESULTS is None:
        print("\nNode absent — test de la bascule plein écran ignoré.\n")
        sys.exit(0)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nBascule plein écran — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Logique de plein écran validée.\n")
