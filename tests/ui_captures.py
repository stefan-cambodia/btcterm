#!/usr/bin/env python3
"""
Captures d'écran du terminal pour la documentation (docs/captures/).

Toutes les captures du README, d'un seul passage : la grille par défaut,
les onglets secondaires — ceux qu'on ne voit pas au premier regard —, le
journal et les alertes, la synthèse et le ruban, puis le prix en plein
écran. Chaque vue repart des onglets par défaut et fixe l'intervalle du
panneau prix : une capture ne dépend pas de celle qui la précède. Il
pilote Firefox par Marionette et **suppose le terminal lancé** :

    python -m terminal.app &          # ou le service btcterm.service
    python tests/ui_captures.py docs/captures

`ui_smoke.py --capture` produit aussi la grille et le plein écran, mais
en plein contrôle : il pose et retire un seuil d'alerte, que la page
transmet au serveur. Sur le service en marche, ce script-ci ne fait que
cliquer des onglets.

Les images sont enregistrées en pleine résolution (1920 px) puis
réduites à 1600 px et quantifiées à 256 couleurs pour rester légères ;
`--brut` garde la pleine résolution.

Ignoré si Firefox est absent.
"""

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

URL = "http://127.0.0.1:8050/"

#: Vue par vue : les onglets à activer (libellés des barres de titre,
#: comparés en majuscules), l'intervalle du panneau prix, et le plein
#: écran du prix.
VUES = {
    "grille.png": {"onglets": (), "intervalle": "1d"},
    "grille-onglets.png": {
        "onglets": ("PROFONDEUR", "LIQUIDATIONS", "PERPÉTUEL", "CALENDRIER",
                    "DOMINANCE"),
        "intervalle": "1d"},
    "grille-journal.png": {
        "onglets": ("JOURNAL", "POSITION", "ALERTES", "ON-CHAIN"),
        "intervalle": "1h"},
    "grille-synthese.png": {
        "onglets": ("RUBAN", "SOURCES", "PERPÉTUEL", "SYNTHÈSE", "OPTIONS"),
        "intervalle": "5m"},
    "plein-ecran.png": {"onglets": (), "intervalle": "4h",
                        "plein_ecran": True},
    # Les couches du graphique du prix : niveaux clés et VWAP, gros ordres,
    # carte du carnet, open interest — décochées au départ, recochées
    # après pour ne pas peser sur les vues suivantes.
    "plein-ecran-couches.png": {"onglets": (), "intervalle": "15m",
                                "plein_ecran": True,
                                "cases": ("NIV", "ORD", "MURS", "OI")},
    # L'exposition gamma des teneurs, en plein écran de la rangée basse.
    "options-gex.png": {"onglets": ("OPTIONS",), "intervalle": "1d",
                        "plein_ecran": "macro",
                        "selecteurs": {"options-view": "GEX"}},
}
#: Largeur des images du dépôt.
LARGEUR = 1600
PRIX_PRET = ("window.lwcPrice && !!window.lwcPrice.debug()"
             " && window.lwcPrice.debug().bars > 0")


def onglet(browser, label: str) -> bool:
    return bool(browser.js("""
        const t = Array.from(document.querySelectorAll('.cell-tab'))
          .find(e => e.textContent.trim().toUpperCase() === arguments[0]);
        if (!t) return false; t.click(); return true;""", [label]))


def onglets_par_defaut(browser) -> None:
    """Le premier onglet de chaque cellule : le rangement par défaut."""
    browser.js("""
        for (const cell of document.querySelectorAll('.cell')) {
            const t = cell.querySelector('.cell-tab');
            if (t && !t.classList.contains('cell-tab-active')) t.click();
        }""")


def intervalle(browser, valeur: str) -> bool:
    browser.js("""
        const l = Array.from(document.querySelectorAll('#price-interval label'))
          .find(e => e.textContent.trim() === arguments[0]);
        if (l) l.click();""", [valeur])
    return browser.wait_for(
        f"(d => d.loaded === '{valeur}' && d.bars > 0)"
        "(window.lwcPrice.debug())", timeout=30)


def cocher(browser, conteneur: str, libelle: str) -> None:
    """Clique l'option `libelle` d'un sélecteur (case ou bouton radio)."""
    browser.js("""
        const l = Array.from(document.querySelectorAll('#' + arguments[0] + ' label'))
          .find(e => e.textContent.trim() === arguments[1]);
        if (l) l.click();""", [conteneur, libelle])


def reduire(image: Path) -> None:
    """1600 px et 256 couleurs : les captures du dépôt restent légères."""
    from PIL import Image

    with Image.open(image) as source:
        hauteur = round(source.height * LARGEUR / source.width)
        petite = source.convert("RGB").resize((LARGEUR, hauteur),
                                              Image.LANCZOS)
    petite.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(
        image, optimize=True)


def run(out: Path, url: str = URL, brut: bool = False) -> int:
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
        for fichier, vue in VUES.items():
            onglets_par_defaut(browser)
            time.sleep(1)
            for label in vue["onglets"]:
                if not onglet(browser, label):
                    print(f"  ✗ onglet introuvable : {label}")
                    return 1
                time.sleep(1.5)
            if not intervalle(browser, vue["intervalle"]):
                print(f"  ✗ l'intervalle {vue['intervalle']} ne s'est pas chargé")
                return 1
            for case in vue.get("cases", ()):
                cocher(browser, "price-extras", case)
                time.sleep(1.5)
            for selecteur, libelle in vue.get("selecteurs", {}).items():
                cocher(browser, selecteur, libelle)
                time.sleep(2)
            zone = vue.get("plein_ecran")
            if zone:
                cellule = "price" if zone is True else zone
                browser.js(f"document.getElementById('zoom-{cellule}').click();")
                time.sleep(2)
                browser.wait_for(PRIX_PRET)
            # Le temps que les panneaux se remplissent — la synthèse et
            # les options lisent plusieurs sources à leur premier rendu.
            time.sleep(8)
            browser.screenshot(str(out / fichier))
            if vue.get("plein_ecran"):
                browser.js("document.body.dispatchEvent(new KeyboardEvent("
                           "'keydown', {key: 'Escape', bubbles: true}));")
                time.sleep(1.5)
            for case in vue.get("cases", ()):
                cocher(browser, "price-extras", case)
                time.sleep(1)
            if not brut:
                reduire(out / fichier)
            print(f"  ✓ {fichier}")
        return 0
    finally:
        browser.close()


if __name__ == "__main__":
    if not shutil.which("firefox"):
        print("\nFirefox absent — captures ignorées.\n")
        sys.exit(0)
    args = [a for a in sys.argv[1:] if a != "--brut"]
    dossier = Path(args[0] if args else "docs/captures")
    dossier.mkdir(parents=True, exist_ok=True)
    print(f"\nCaptures du terminal — {URL} → {dossier}\n" + "─" * 60)
    sys.exit(run(dossier, brut="--brut" in sys.argv[1:]))
