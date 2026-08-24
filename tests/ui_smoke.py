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

Les contrôles du panneau prix suivent son rendu Lightweight Charts —
canvas et sonde `window.lwcPrice.debug()` — le seul depuis la bascule
de la voie A ; les autres panneaux restent des figures Plotly.

Ignoré si Firefox est absent.
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

URL = "http://127.0.0.1:8050/"


def run(capture_dir: Path | None, url: str = URL) -> int:
    from marionette_client import Firefox

    browser = Firefox()
    failures = 0

    #: Le panneau prix est prêt quand ses séries Lightweight Charts
    #: portent réellement des données. L'attendre plutôt que dormir :
    #: au premier passage après un démarrage du serveur, deux secondes
    #: ne suffisent pas toujours.
    PRIX_PRET = ("window.lwcPrice && !!window.lwcPrice.debug()"
                 " && window.lwcPrice.debug().bars > 0")

    def check(label, condition, detail=""):
        nonlocal failures
        print(f"  {'✓' if condition else '✗'} {label}{'  ' + str(detail) if detail else ''}")
        if not condition:
            failures += 1

    try:
        browser.get(url)
        # Le panneau prix n'est pas une figure Plotly : ce sont les
        # graphiques ETF et macro qui attestent le rendu Dash.
        if not browser.wait_for("document.querySelectorAll('.js-plotly-plot')"
                                ".length >= 2"):
            print("  ✗ les graphiques ne se sont pas rendus — le terminal tourne-t-il ?")
            return 1
        if not browser.wait_for(PRIX_PRET, timeout=15):
            print("  ✗ le panneau prix Lightweight Charts ne s'est pas rempli")
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

        print("\nCanal push")
        check("le WebSocket prend la main", browser.wait_for(
            "document.getElementById('hdr-push').textContent === 'push'"))
        avant = browser.js(
            "return document.querySelector('#book-table').textContent;")
        time.sleep(1.2)
        check("le carnet vit sans l'horloge", browser.js(
            "return document.querySelector('#book-table').textContent;") != avant)
        # L'agrandissement ne peut arriver que par le canal : le callback
        # du carnet ne lit `expanded` qu'en State, et l'horloge qui
        # l'aurait rejoué est coupée tant que le push tient.
        browser.js("document.getElementById('zoom-book').click();")
        time.sleep(1.5)
        rows = browser.js("return document.querySelectorAll('#book-table tr').length;")
        check("l'agrandissement passe par le canal", rows >= 30, f"{rows} lignes")
        browser.js("document.dispatchEvent(new KeyboardEvent('keydown',"
                   " {key: 'Escape', bubbles: true}));")
        time.sleep(1.5)
        rows = browser.js("return document.querySelectorAll('#book-table tr').length;")
        check("le retour à la grille aussi", rows <= 20, f"{rows} lignes")

        print("\nPanneau prix Lightweight Charts")
        check("le canvas est posé", browser.js(
            "return document.querySelectorAll('#price-lwc canvas')"
            ".length;") >= 2)
        etat = browser.js("return window.lwcPrice.debug();")
        check("la série initiale est chargée", etat["bars"] > 200,
              f"{etat['bars']} bougies")
        # Le bandeau de démonstration dit exactement ce que le paquet
        # dit — visible en repli hors ligne, absent sur données réelles.
        banniere = browser.js(
            "return getComputedStyle(document.querySelector("
            "'.lwc-demo-banner')).display === 'block';")
        check("le bandeau démo suit le paquet", banniere == etat["demo"],
              f"paquet {etat['demo']}, bandeau {banniere}")

        # Changement d'intervalle : un refetch, une nouvelle série.
        browser.js("""
            Array.from(document.querySelectorAll('#price-interval label'))
                 .find(l => l.textContent.trim() === '4h').click();
        """)
        check("l'intervalle 4h recharge sa série", browser.wait_for(
            "window.lwcPrice.debug().interval === '4h'"
            " && window.lwcPrice.debug().bars > 200", timeout=15))

        # Bascule € : les chandeliers changent d'échelle sur place,
        # au taux voyageant avec les données — aucun refetch.
        avant = browser.js("return window.lwcPrice.debug();")
        browser.js("""
            Array.from(document.querySelectorAll('#price-currency label'))
                 .find(l => l.textContent.trim() === '€').click();
        """)
        time.sleep(1.2)
        apres = browser.js("return window.lwcPrice.debug();")
        check("la bascule € met les bougies au taux",
              apres["lastClose"] and abs(
                  apres["lastClose"] / avant["lastClose"]
                  - avant["eur_rate"]) < 1e-6,
              f"{avant['lastClose']} → {apres['lastClose']}")
        browser.js("""
            Array.from(document.querySelectorAll('#price-currency label'))
                 .find(l => l.textContent.trim() === '$').click();
        """)
        time.sleep(1)

        # Historique infini : un pan appuyé vers la gauche déclenche
        # la page antérieure. Hors ligne, la source n'a rien avant :
        # le tampon ne grandit pas mais le « plus ancien atteint »
        # est retenu — les deux issues prouvent l'aller-retour.
        avant = browser.js("return window.lwcPrice.debug();")
        browser.js("window.lwcPrice.pan(-30, 120);")
        recharge = browser.wait_for(
            f"window.lwcPrice.debug().bars > {avant['bars']}"
            " || window.lwcPrice.debug().exhausted === true", timeout=15)
        apres = browser.js("return window.lwcPrice.debug();")
        if apres["bars"] > avant["bars"]:
            check("le pan vers le passé charge des bougies antérieures",
                  apres["firstTime"] < avant["firstTime"],
                  f"{avant['bars']} → {apres['bars']} bougies")
        else:
            check("historique épuisé retenu (source sans passé)",
                  recharge and apres["exhausted"],
                  f"{apres['bars']} bougies, exhausted={apres['exhausted']}")

        # Signaux et profil de volume fenêtré (case PROFIL cochée
        # par défaut) : le profil suit la plage visible.
        check("des signaux posés sur les chandeliers",
              apres["signals"] > 0, f"{apres['signals']} signaux")
        check("le profil de la plage visible est calculé",
              browser.wait_for("!!window.lwcPrice.debug().profile",
                               timeout=10))
        p1 = browser.js("return window.lwcPrice.debug().profile;")
        fenetre = browser.js("return window.lwcPrice.debug().range;")
        browser.js("window.lwcPrice.pan(%f, %f);"
                   % (fenetre["to"] - 40, fenetre["to"]))
        change = browser.wait_for(
            "(function (p) {"
            f" return p && (p.poc !== {p1['poc']}"
            f" || p.vaLow !== {p1['vaLow']}"
            f" || p.vaHigh !== {p1['vaHigh']}); }})"
            "(window.lwcPrice.debug().profile)", timeout=10)
        p2 = browser.js("return window.lwcPrice.debug().profile;")
        check("le profil change avec la fenêtre visible", change,
              f"POC {p1['poc']} → {p2 and p2['poc']}")
        browser.js("window.lwcPrice.pan(%f, %f);"
                   % (fenetre["from"], fenetre["to"]))
        time.sleep(1)

        print("\nBascule plein écran")
        if capture_dir:
            browser.screenshot(str(capture_dir / "grille.png"))
        browser.js("document.getElementById('zoom-price').click();")
        time.sleep(2)
        browser.wait_for(PRIX_PRET)
        geometry = browser.js("""
            const el = document.getElementById('cell-price');
            const r = el.getBoundingClientRect();
            const g = el.querySelector('%s');
            return {classe: el.className,
                    couvre: r.width / window.innerWidth,
                    graphe: g ? Math.round(g.getBoundingClientRect().width) : 0,
                    autres: document.querySelectorAll('.cell-hidden').length};
        """ % "#price-lwc canvas")
        check("panneau agrandi", "cell-max" in geometry["classe"])
        check("couvre la fenêtre", geometry["couvre"] > 0.95,
              f"{geometry['couvre']*100:.0f} %")
        check("graphique redimensionné", geometry["graphe"] > 900,
              f"{geometry['graphe']} px")
        check("autres panneaux masqués", geometry["autres"] == 5)
        if capture_dir:
            browser.screenshot(str(capture_dir / "plein-ecran.png"))

        print("\nPart du cours")
        # Le partage de hauteur est affaire de panes : RSI en a un,
        # tout décocher n'en laisse qu'un seul — celui du cours.
        check("le RSI vit dans son pane",
              browser.js("return window.lwcPrice.debug().panes;") == 2)
        for value in ("rsi", "volume", "profile"):
            browser.js(
                "document.querySelectorAll('#price-extras input')"
                f"[{('rsi', 'crsi', 'volume', 'profile').index(value)}].click();")
            time.sleep(2.2)
        browser.wait_for(PRIX_PRET)
        check("tout décoché : le cours occupe tout",
              browser.js("return window.lwcPrice.debug().panes;") == 1)
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
        # La sonde lit le mode *effectif* de l'échelle, pas la case.
        check("LOG passe l'axe des prix en logarithmique",
              browser.js("return window.lwcPrice.debug().log;") is True)
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
        # Rendu Lightweight Charts, comme le prix : un canvas dans le
        # div #perp-lwc, pas de figure Plotly.
        perp = browser.js("""
            const el = document.getElementById('perp-lwc');
            return {canvas: !!(el && el.querySelector('canvas')),
                    note: el ? getComputedStyle(el.querySelector(
                        '.lwc-perp-note') || el).display : null,
                    badges: document.getElementById('perp-badges').textContent};
        """)
        check("le canvas LWC du perpétuel est monté", perp["canvas"])
        check("financement et OI chiffrés dans le titre — ou l'absence dite",
              ("%" in perp["badges"] and "OI" in perp["badges"])
              or "indisponible" in perp["badges"],
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

        print("\nOnglet alertes")
        browser.js("""
            Array.from(document.querySelectorAll('#cell-news .cell-tab'))
                 .find(t => t.textContent === 'ALERTES').click();
        """)
        time.sleep(2.5)
        check("cloche posée dans le bandeau", browser.js(
            "return (document.getElementById('hdr-alerts') || {textContent:"
            " ''}).textContent.includes('🔔');"))
        check("réglages et liste posés", browser.js(
            "return !!document.getElementById('alert-price-input')"
            " && !!document.getElementById('alerts-list')"
            " && document.getElementById('alerts-list').textContent.length > 4;"))
        check("les seuils par défaut remplissent les champs", browser.js(
            "return document.getElementById('alert-liq').value;") == "10")
        # Poser un seuil : la valeur passe par le setter natif pour que
        # React la voie, et Entrée la committe (debounce). Les
        # événements synthétiques étant capricieux au premier essai,
        # la pose retente une fois.
        pose = False
        for _ in range(2):
            browser.js("""
                const input = document.getElementById('alert-price-input');
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                input.focus();
                setter.call(input, '999999');
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new KeyboardEvent('keydown',
                    {key: 'Enter', bubbles: true}));
                // Le blur committe la valeur (debounce) : un vrai clic
                // sur « poser » le provoquerait de lui-même, le clic
                // synthétique ne déplace pas le focus.
                input.blur();
            """)
            time.sleep(1)
            browser.js("document.getElementById('alert-price-add').click();")
            pose = browser.wait_for(
                "document.getElementById('alert-price-chips')"
                ".textContent.includes('999,999')", timeout=5)
            if pose:
                break
        check("poser un seuil crée sa puce", pose)
        if pose:
            # Le seuil posé gagne sa ligne sur le graphique prix — le
            # relais alert-config → lwcPrice.alerts.
            check("le seuil se trace sur le graphique prix", browser.wait_for(
                "window.lwcPrice && window.lwcPrice.debug()"
                " && window.lwcPrice.debug().alerts >= 1", timeout=6))
            browser.js("""
                const chips = document.getElementById('alert-price-chips');
                Array.from(chips.querySelectorAll('span span'))
                     .find(s => s.textContent.trim() === '×').click();
            """)
            check("sa croix le retire", browser.wait_for(
                "!document.getElementById('alert-price-chips')"
                ".textContent.includes('999,999')", timeout=10))

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
        browser.get(url)
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
        # L'intervalle 4h choisi plus haut est persisté : le rendu
        # LWC doit le restaurer et recharger sa série avec.
        check("le panneau prix restaure son intervalle (4h)",
              browser.wait_for(
                  "window.lwcPrice && !!window.lwcPrice.debug()"
                  " && window.lwcPrice.debug().interval === '4h'"
                  " && window.lwcPrice.debug().bars > 200", timeout=15))

        print("\nDisposition configurable")
        # Déménager le calendrier de la cellule news vers la rangée basse,
        # appliquer, vérifier — puis rendre le rangement d'origine. Le
        # dialogue vit dans la page dès le chargement, seulement masqué.
        check("le dialogue est masqué au départ", browser.js(
            "return document.getElementById('layout-overlay')"
            ".className.includes('layout-overlay-hidden');"))
        browser.js("document.getElementById('layout-btn').click();")
        # L'ouverture comme l'application passent par un aller-retour
        # serveur : attendre la condition, pas un délai — la durée varie
        # avec la charge et les ticks d'horloge qui tombent en même temps.
        check("⚙ ouvre le dialogue", browser.wait_for(
            "!document.getElementById('layout-overlay')"
            ".className.includes('layout-overlay-hidden')", timeout=8))
        check("un rang par panneau", browser.js(
            "return document.querySelectorAll('.layout-row').length;") == 13)
        browser.js("""
            const rang = Array.from(document.querySelectorAll('.layout-row'))
                .find(r => r.querySelector('.layout-panel-name')
                            .textContent === 'CALENDRIER');
            Array.from(rang.querySelectorAll('label'))
                .find(l => l.textContent.trim() === 'rangée basse').click();
        """)
        time.sleep(0.5)
        browser.js("document.getElementById('layout-apply').click();")
        check("Appliquer ferme le dialogue", browser.wait_for(
            "document.getElementById('layout-overlay')"
            ".className.includes('layout-overlay-hidden')", timeout=10))
        check("le calendrier a rejoint la rangée basse", browser.wait_for("""
            Array.from(document.querySelectorAll('#cell-macro .cell-tab'))
                .some(t => t.textContent === 'CALENDRIER')
        """, timeout=10))
        check("et a quitté la cellule news", browser.js("""
            return !Array.from(document.querySelectorAll('#cell-news .cell-tab'))
                .some(t => t.textContent === 'CALENDRIER');
        """))
        browser.js("""
            Array.from(document.querySelectorAll('#cell-macro .cell-tab'))
                 .find(t => t.textContent === 'CALENDRIER').click();
        """)
        time.sleep(2.5)
        check("et s'y remplit dès son ouverture", browser.js(
            "return !!document.getElementById('cal-list')"
            " && document.getElementById('cal-list').textContent.length > 8;"))
        # Le déménagement doit survivre au rechargement, comme les onglets.
        browser.get(url)
        browser.wait_for("document.querySelectorAll('.js-plotly-plot').length >= 1")
        time.sleep(3)
        check("le déménagement survit au rechargement", browser.js("""
            return Array.from(document.querySelectorAll('#cell-macro .cell-tab'))
                .some(t => t.textContent === 'CALENDRIER');
        """))
        # Rangement d'origine : Par défaut ne fait que remplir le
        # formulaire, c'est Appliquer qui écrit. Les vérifications
        # attendent leur condition plutôt qu'un délai fixe : le re-rendu
        # de la grille remonte tous les panneaux, et sa durée varie avec
        # ce que les montages coûtent — un tick d'horloge rare qui tombe
        # au même moment suffit à dépasser un sommeil de 2,5 s.
        browser.js("document.getElementById('layout-btn').click();")
        browser.wait_for("!document.getElementById('layout-overlay')"
                         ".className.includes('layout-overlay-hidden')",
                         timeout=8)
        browser.js("document.getElementById('layout-reset').click();")
        time.sleep(1)
        browser.js("document.getElementById('layout-apply').click();")
        check("Par défaut + Appliquer rendent le calendrier aux news",
              browser.wait_for("""
                  Array.from(document.querySelectorAll('#cell-news .cell-tab'))
                      .some(t => t.textContent === 'CALENDRIER')
              """, timeout=10))
        browser.js("document.getElementById('layout-btn').click();")
        browser.wait_for("!document.getElementById('layout-overlay')"
                         ".className.includes('layout-overlay-hidden')",
                         timeout=8)
        browser.js("document.dispatchEvent(new KeyboardEvent('keydown',"
                   " {key: 'Escape', bubbles: true}));")
        check("Échap ferme le dialogue rouvert", browser.wait_for(
            "document.getElementById('layout-overlay')"
            ".className.includes('layout-overlay-hidden')", timeout=8))
    finally:
        browser.close()

    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=None,
                        help="dossier où déposer les captures d'écran")
    parser.add_argument("--url", default=URL,
                        help="adresse du terminal à contrôler (défaut : "
                             f"{URL} — utile pour un port d'essai)")
    parser.add_argument(
        "--lwc", action="store_true",
        help="sans effet — le rendu Lightweight Charts est le défaut "
             "depuis la bascule de la voie A ; l'option reste pour les "
             "habitudes de lancement")
    args = parser.parse_args()

    if not shutil.which("firefox"):
        print("\nFirefox absent — contrôle de l'interface ignoré.\n")
        sys.exit(0)
    if args.capture:
        args.capture.mkdir(parents=True, exist_ok=True)

    print("\nContrôle de l'interface — " + args.url + "\n" + "─" * 60)
    failures = run(args.capture, args.url)
    print("\n" + "─" * 60)
    print("Interface conforme.\n" if not failures else f"{failures} contrôle(s) en échec.\n")
    sys.exit(1 if failures else 0)
