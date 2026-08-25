#!/usr/bin/env python3
"""
Captures d'écran du terminal pour la documentation (docs/captures/).

`ui_smoke.py --capture` produit déjà la grille par défaut et le plein
écran du prix ; ce script complète avec les onglets secondaires — ceux
qu'on ne voit pas au premier regard : profondeur comparée, liquidations,
perpétuel, calendrier, dominance, puis journal, alertes, données de
chaîne. Il pilote Firefox par Marionette et **suppose le terminal
lancé** :

    python -m terminal.app &          # ou le service btcterm.service
    python tests/ui_captures.py docs/captures

Les images sont enregistrées en pleine résolution (1920 px) ; celles du
dépôt sont réduites à 1600 px et quantifiées à 256 couleurs pour rester
légères — voir « Aperçu » dans le README.

Ignoré si Firefox est absent.
"""

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

URL = "http://127.0.0.1:8050/"

#: Onglets à activer, capture par capture. Les libellés sont ceux des
#: barres de titre, comparés en majuscules.
VUES = {
    "grille-onglets.png": ("PROFONDEUR", "LIQUIDATIONS", "PERPÉTUEL",
                           "CALENDRIER", "DOMINANCE"),
    "grille-journal.png": ("JOURNAL", "ALERTES", "ON-CHAIN"),
}
PRIX_PRET = ("window.lwcPrice && !!window.lwcPrice.debug()"
             " && window.lwcPrice.debug().bars > 0")


def onglet(browser, label: str) -> bool:
    return bool(browser.js("""
        const t = Array.from(document.querySelectorAll('.cell-tab'))
          .find(e => e.textContent.trim().toUpperCase() === arguments[0]);
        if (!t) return false; t.click(); return true;""", [label]))


def run(out: Path, url: str = URL) -> int:
    from marionette_client import Firefox
    browser = Firefox()
    try:
        browser.get(url)
        if not browser.wait_for("document.querySelectorAll('.js-plotly-plot')"
                                ".length >= 2"):
            print("  ✗ le terminal ne répond pas — tourne-t-il ?")
            return 1
        browser.wait_for(PRIX_PRET, timeout=20)
        time.sleep(3)
        for fichier, labels in VUES.items():
            for label in labels:
                if not onglet(browser, label):
                    print(f"  ✗ onglet introuvable : {label}")
                    return 1
                time.sleep(1.5)
            time.sleep(4)   # le temps que les panneaux se remplissent
            browser.screenshot(str(out / fichier))
            print(f"  ✓ {fichier}")
        return 0
    finally:
        browser.close()


if __name__ == "__main__":
    if not shutil.which("firefox"):
        print("\nFirefox absent — captures ignorées.\n")
        sys.exit(0)
    dossier = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/captures")
    dossier.mkdir(parents=True, exist_ok=True)
    print(f"\nCaptures du terminal — {URL} → {dossier}\n" + "─" * 60)
    sys.exit(run(dossier))
