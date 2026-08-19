#!/usr/bin/env python3
"""
Contrôle de l'interface dans un vrai navigateur.

Le reste des tests couvre la logique ; celui-ci vérifie ce qu'on ne peut
constater qu'à l'écran : les panneaux sont bien posés, les sélecteurs de la barre
de titre tiennent dans la largeur, le bouton plein écran est visible et
ne recouvre rien, la bascule agrandit réellement le panneau, et le carnet
montre ses deux côtés au lieu d'être coupé.

Il pilote Firefox par Marionette et **suppose le terminal déjà lancé** :

    python -m terminal.app &
    python tests/ui_smoke.py [--capture dossier/]

Ignoré si Firefox est absent.
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

URL = "http://127.0.0.1:8050/"


def run(capture_dir: Path | None) -> int:
    from marionette_client import Firefox

    browser = Firefox()
    failures = 0

    #: Le domaine de l'axe des prix n'existe qu'une fois la figure posée
    #: par Plotly. L'attendre plutôt que dormir : au premier passage après
    #: un démarrage du serveur, deux secondes ne suffisent pas toujours.
    PRIX_PRET = ("((document.getElementById('price-chart') || {})"
                 ".querySelector('.js-plotly-plot') || {}).layout"
                 " && !!document.getElementById('price-chart')"
                 ".querySelector('.js-plotly-plot').layout.yaxis"
                 " && !!document.getElementById('price-chart')"
                 ".querySelector('.js-plotly-plot').layout.yaxis.domain")

    def check(label, condition, detail=""):
        nonlocal failures
        print(f"  {'✓' if condition else '✗'} {label}{'  ' + str(detail) if detail else ''}")
        if not condition:
            failures += 1

    try:
        browser.get(URL)
        if not browser.wait_for("document.querySelectorAll('.js-plotly-plot').length >= 3"):
            print("  ✗ les graphiques ne se sont pas rendus — le terminal tourne-t-il ?")
            return 1
        time.sleep(2.5)

        print("\nGrille")
        check("6 cellules posées",
              browser.js("return document.querySelectorAll('.cell').length;") == 6)
        check("6 boutons plein écran",
              browser.js("return document.querySelectorAll('.zoom-btn').length;") == 6)
        check("feuille de style chargée", browser.js(
            "return !!Array.from(document.styleSheets)"
            ".find(s => (s.href || '').includes('terminal.css'));"))

        # Une barre de titre qui passe à deux lignes vole sa hauteur au
        # graphique. C'est arrivé pour de bon : la feuille de style de
        # `dcc.Tabs` revendique la classe `.tab` et y met 20 px de
        # remplissage, ce qui avait fait passer les cellules à onglets de
        # 14 à 55 px sans que rien ne le signale.
        print("\nBarres de titre")
        hauteurs = browser.js("""
            const out = {};
            for (const cell of document.querySelectorAll('.cell')) {
                const bar = cell.children[1].firstElementChild.firstElementChild;
                out[cell.id] = Math.round(bar.getBoundingClientRect().height);
            }
            return out;
        """)
        trop_hautes = {k: v for k, v in hauteurs.items() if v > 24}
        check("toutes tiennent sur une ligne", not trop_hautes,
              trop_hautes or f"max {max(hauteurs.values())} px")

        print("\nBarre de titre du panneau prix")
        check("9 intervalles proposés", browser.js(
            "return document.querySelectorAll('#price-interval input').length;") == 9)
        # La palette élargie a rempli la barre : si les sélecteurs débordent
        # de la largeur du panneau, les derniers deviennent inatteignables
        # dans la grille — c'est le défaut que ce contrôle guette.
        overflow = browser.js("""
            const controles = document.getElementById('price-interval').parentElement;
            const barre = controles.parentElement;
            return {deborde: barre.scrollWidth - barre.clientWidth,
                    hauteur: Math.round(controles.getBoundingClientRect().height)};
        """)
        check("les sélecteurs tiennent dans la largeur",
              overflow["deborde"] <= 1, f"{overflow['deborde']} px de trop")
        check("la barre tient sur une ligne",
              overflow["hauteur"] <= 24, f"{overflow['hauteur']} px de haut")

        print("\nBouton plein écran")
        check("visible sans survol",
              browser.js("return getComputedStyle("
                         "document.getElementById('zoom-price')).opacity;") == "1")
        check("ne recouvre aucun sélecteur", not browser.js("""
            const b = document.getElementById('zoom-price').getBoundingClientRect();
            return Array.from(document.querySelectorAll('#cell-price label')).some(l => {
                const r = l.getBoundingClientRect();
                return !(r.right < b.left || r.left > b.right
                         || r.bottom < b.top || r.top > b.bottom);
            });
        """))

        print("\nCarnet")
        rows = browser.js("return document.querySelectorAll('#book-table tr').length;")
        check("les deux côtés visibles (achats + ventes + spread)", rows >= 9, f"{rows} lignes")
        check("séparateur de spread présent", browser.js(
            "return document.querySelector('#book-table').textContent.includes('spread');"))

        print("\nBascule plein écran")
        if capture_dir:
            browser.screenshot(str(capture_dir / "grille.png"))
        browser.js("document.getElementById('zoom-price').click();")
        time.sleep(2)
        browser.wait_for(PRIX_PRET)
        geometry = browser.js("""
            const el = document.getElementById('cell-price');
            const r = el.getBoundingClientRect();
            const g = el.querySelector('.js-plotly-plot');
            return {classe: el.className,
                    couvre: r.width / window.innerWidth,
                    graphe: g ? Math.round(g.getBoundingClientRect().width) : 0,
                    autres: document.querySelectorAll('.cell-hidden').length};
        """)
        check("panneau agrandi", "cell-max" in geometry["classe"])
        check("couvre la fenêtre", geometry["couvre"] > 0.95,
              f"{geometry['couvre']*100:.0f} %")
        check("graphique redimensionné", geometry["graphe"] > 900,
              f"{geometry['graphe']} px")
        check("autres panneaux masqués", geometry["autres"] == 5)
        if capture_dir:
            browser.screenshot(str(capture_dir / "plein-ecran.png"))

        print("\nPart du cours")
        share = browser.js("""
            const gd = document.getElementById('price-chart')
                .querySelector('.js-plotly-plot');
            const d = gd.layout.yaxis.domain;
            return Math.round((d[1] - d[0]) * 100);
        """)
        check("le cours domine en plein écran", share >= 75, f"{share} %")

        for value in ("rsi", "volume", "profile"):
            browser.js(
                "document.querySelectorAll('#price-extras input')"
                f"[{('rsi', 'crsi', 'volume', 'profile').index(value)}].click();")
            time.sleep(2.2)
        browser.wait_for(PRIX_PRET)
        share = browser.js("""
            const gd = document.getElementById('price-chart')
                .querySelector('.js-plotly-plot');
            const d = gd.layout.yaxis.domain;
            return Math.round((d[1] - d[0]) * 100);
        """)
        check("tout décoché : le cours occupe tout", share == 100, f"{share} %")
        if capture_dir:
            browser.screenshot(str(capture_dir / "cours-seul.png"))

        print("\nSélecteurs")
        check("l'état coché est visible", browser.js("""
            const label = document.querySelector('#price-currency label.selected');
            return label && getComputedStyle(label).color !== 'rgb(75, 85, 99)';
        """))

        print("\nÉchelle logarithmique")
        browser.js("document.querySelector('#price-scale input').click();")
        time.sleep(2.2)
        check("LOG passe l'axe des prix en logarithmique", browser.js("""
            const gd = document.getElementById('price-chart')
                .querySelector('.js-plotly-plot');
            return gd.layout.yaxis.type;
        """) == "log")
        browser.js("document.querySelector('#price-scale input').click();")
        time.sleep(2.2)

        print("\nRetour à la grille")
        browser.js("document.dispatchEvent("
                   "new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));")
        time.sleep(2)
        check("Échap restaure la grille",
              browser.js("return document.getElementById('cell-price').className;") == "cell")

        print("\nOnglets de cellule")
        check("le carnet est l'onglet actif", browser.js(
            "return document.querySelector('#cell-book .cell-tab-active').textContent;")
            == "CARNET")
        check("la profondeur n'est pas rendue",
              browser.js("return !document.getElementById('depth-chart');"))
        browser.js("""
            Array.from(document.querySelectorAll('#cell-book .cell-tab'))
                 .find(t => t.textContent === 'PROFONDEUR').click();
        """)
        time.sleep(2.5)
        check("l'onglet profondeur affiche son graphique",
              browser.js("return !!document.getElementById('depth-chart');"))
        check("et remplace le carnet",
              browser.js("return !document.getElementById('book-table');"))
        # Le graphique doit se remplir sans attendre le tour d'horloge du
        # panneau : un onglet qui s'ouvre vide pendant cinq minutes serait
        # inutilisable pour les panneaux lents.
        check("rempli à l'ouverture, sans attendre l'horloge", browser.js("""
            const gd = document.getElementById('depth-chart')
                .querySelector('.js-plotly-plot');
            return gd && gd.data && gd.data.length > 0;
        """))
        browser.js("""
            Array.from(document.querySelectorAll('#cell-book .cell-tab'))
                 .find(t => t.textContent === 'CARNET').click();
        """)
        time.sleep(2)
        check("retour au carnet",
              browser.js("return !!document.getElementById('book-table');"))

        print("\nOnglet perpétuel")
        browser.js("""
            Array.from(document.querySelectorAll('#cell-etf .cell-tab'))
                 .find(t => t.textContent === 'PERPÉTUEL').click();
        """)
        time.sleep(3)
        perp = browser.js("""
            const gd = document.getElementById('perp-chart')
                .querySelector('.js-plotly-plot');
            return {series: gd.data.map(t => t.name),
                    badges: document.getElementById('perp-badges').textContent};
        """)
        check("financement et open interest tracés",
              perp["series"] == ["financement", "open interest"], perp["series"])
        check("financement et OI chiffrés dans le titre",
              "%" in perp["badges"] and "OI" in perp["badges"],
              perp["badges"][:46])

        print("\nOnglets dominance et on-chain")
        for label, graphe, badge in (("DOMINANCE", "dominance-chart", "dominance-badges"),
                                     ("ON-CHAIN", "onchain-chart", "onchain-badges")):
            browser.js(f"""
                Array.from(document.querySelectorAll('#cell-macro .cell-tab'))
                     .find(t => t.textContent === '{label}').click();
            """)
            time.sleep(3.5)
            etat = browser.js(f"""
                const gd = document.getElementById('{graphe}')
                    .querySelector('.js-plotly-plot');
                return {{series: gd ? gd.data.length : 0,
                        badge: document.getElementById('{badge}').textContent}};
            """)
            check(f"{label} : tracé et chiffré",
                  etat["series"] >= 1 and len(etat["badge"]) > 8,
                  etat["badge"][:44])

        print("\nOnglet liquidations")
        browser.js("""
            Array.from(document.querySelectorAll('#cell-arb .cell-tab'))
                 .find(t => t.textContent === 'LIQUIDATIONS').click();
        """)
        time.sleep(2.5)
        liq = browser.js("""
            return {badges: document.getElementById('liq-badges').textContent,
                    table: document.getElementById('liq-table').textContent};
        """)
        # Le flux est épisodique : le panneau peut légitimement être vide,
        # mais il doit alors le dire — et jamais rester muet.
        check("le fil dit son état", len(liq["badges"]) > 8, liq["badges"][:44])
        check("le tableau dit le sien", len(liq["table"]) > 4, liq["table"][:44])

        print("\nOnglet calendrier")
        browser.js("""
            Array.from(document.querySelectorAll('#cell-news .cell-tab'))
                 .find(t => t.textContent === 'CALENDRIER').click();
        """)
        time.sleep(2.5)
        cal = browser.js("""
            return {liste: document.getElementById('cal-list').textContent,
                    badge: document.getElementById('cal-next').textContent};
        """)
        # Les dates sont tenues à la main : le panneau doit montrer des
        # événements à venir, ou dire que la liste est épuisée — jamais
        # rester muet.
        check("des échéances listées, ou l'épuisement dit",
              "FOMC" in cal["liste"] or "épuisée" in cal["liste"],
              cal["liste"][:44])
        check("compte à rebours dans le titre", len(cal["badge"]) > 4,
              cal["badge"][:32])

        browser.js("""
            Array.from(document.querySelectorAll('#cell-macro .cell-tab'))
                 .find(t => t.textContent === 'MACRO').click();
        """)
        time.sleep(3)

        print("\nPanneau macro")
        macro = browser.js("""
            const gd = document.getElementById('macro-chart')
                .querySelector('.js-plotly-plot');
            return {series: gd.data.length,
                    axes: (gd.layout.yaxis || {}).type + '/' +
                          ((gd.layout.yaxis2 || {}).side || '—'),
                    stats: document.getElementById('macro-stats').textContent};
        """)
        check("cours et masse monétaire tracés", macro["series"] == 2,
              f"{macro['series']} séries")
        check("axe des prix log, M2 à droite", macro["axes"] == "log/right",
              macro["axes"])
        check("corrélations affichées", "r niveaux" in macro["stats"],
              macro["stats"][:48])

        print("\nDouble-clic")
        browser.js("document.querySelector('#cell-arb')"
                   ".dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));")
        time.sleep(2)
        check("agrandit le panneau", "cell-max" in browser.js(
            "return document.getElementById('cell-arb').className;"))

        print("\nPersistance au rechargement")
        # Retour à la grille, puis un réglage à retrouver : le carnet sur
        # Kraken. Les onglets laissés actifs par les sections précédentes
        # (liquidations, calendrier) servent de témoins.
        browser.js("document.body.dispatchEvent("
                   "new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));")
        time.sleep(1.5)
        browser.js("""
            Array.from(document.querySelectorAll('#book-exchange label'))
                 .find(l => l.textContent.trim() === 'KRK').click();
        """)
        time.sleep(1.5)
        browser.get(URL)
        browser.wait_for("document.querySelectorAll('.js-plotly-plot').length >= 1")
        time.sleep(3)
        check("le plein écran ne survit pas, la grille oui",
              browser.js("return document.getElementById('cell-arb').className;")
              == "cell")
        check("l'onglet liquidations est restauré, panneau compris",
              browser.js("""
                  return document.querySelector('#cell-arb .cell-tab-active')
                      .textContent === 'LIQUIDATIONS'
                      && !!document.getElementById('liq-table');
              """))
        check("l'onglet calendrier est restauré",
              browser.js("return document.querySelector('#cell-news .cell-tab-active')"
                         ".textContent;") == "CALENDRIER")
        check("le carnet retrouve Kraken", browser.js(
            "return (document.querySelector('#book-exchange label.selected')"
            " || {}).textContent;") == "KRK")
    finally:
        browser.close()

    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=None,
                        help="dossier où déposer les captures d'écran")
    args = parser.parse_args()

    if not shutil.which("firefox"):
        print("\nFirefox absent — contrôle de l'interface ignoré.\n")
        sys.exit(0)
    if args.capture:
        args.capture.mkdir(parents=True, exist_ok=True)

    print("\nContrôle de l'interface — " + URL + "\n" + "─" * 60)
    failures = run(args.capture)
    print("\n" + "─" * 60)
    print("Interface conforme.\n" if not failures else f"{failures} contrôle(s) en échec.\n")
    sys.exit(1 if failures else 0)
