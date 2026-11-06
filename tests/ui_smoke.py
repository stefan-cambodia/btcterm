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
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
#: La racine, pour lire le registre des panneaux (terminal/grid.py).
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))

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

        check("les murs se lisent sous le carnet", browser.wait_for(
            "(document.getElementById('book-table')?.textContent || '')"
            ".includes('murs')", timeout=5))

        print("\nCanal push")
        check("le WebSocket prend la main", browser.wait_for(
            "document.getElementById('hdr-push').textContent === 'push'"))
        avant = browser.js(
            "return document.querySelector('#book-table').textContent;")
        time.sleep(1.2)
        check("le carnet vit sans l'horloge", browser.js(
            "return document.querySelector('#book-table').textContent;") != avant)
        # Écrit en place par assets/book.js : les lignes restent les mêmes
        # éléments, seuls leurs textes changent. Rendu par Dash, chaque trame
        # les recréait, et la page gelait en plein écran.
        browser.js("window.__ligne = document.querySelector('#book-table tr');"
                   "window.__texte = document.querySelector('#book-table')"
                   ".textContent;")
        time.sleep(1.0)
        check("le carnet s'écrit en place, sans recréer ses lignes", browser.js(
            "return document.querySelector('#book-table tr') === window.__ligne"
            " && document.querySelector('#book-table').textContent"
            " !== window.__texte;"))
        # L'agrandissement ne peut arriver que par le canal : le callback
        # du carnet ne lit `expanded` qu'en State, et l'horloge qui
        # l'aurait rejoué est coupée tant que le push tient. On attend le
        # résultat au lieu de dormir : la transition redimensionne tous les
        # graphiques, et c'est le passage par le canal qui est contrôlé ici.
        # La durée reste affichée, pour qu'une dérive se voie.
        lignes = "document.querySelectorAll('#book-table tr').length"
        browser.js("document.getElementById('zoom-book').click();")
        debut = time.monotonic()
        browser.wait_for(f"{lignes} >= 30", timeout=6)
        rows = browser.js(f"return {lignes};")
        check("l'agrandissement passe par le canal", rows >= 30,
              f"{rows} lignes en {time.monotonic() - debut:.1f} s")
        browser.js("document.dispatchEvent(new KeyboardEvent('keydown',"
                   " {key: 'Escape', bubbles: true}));")
        debut = time.monotonic()
        browser.wait_for(f"{lignes} <= 20", timeout=6)
        rows = browser.js(f"return {lignes};")
        check("le retour à la grille aussi", rows <= 20,
              f"{rows} lignes en {time.monotonic() - debut:.1f} s")

        print("\nPanneau prix Lightweight Charts")
        check("le canvas est posé", browser.js(
            "return document.querySelectorAll('#price-lwc canvas')"
            ".length;") >= 2)
        etat = browser.js("return window.lwcPrice.debug();")
        check("la série initiale est chargée", etat["bars"] > 200,
              f"{etat['bars']} bougies")
        # Le bandeau d'avertissement dit exactement ce que le paquet
        # dit — visible en repli hors ligne, ou en euros sur un taux de
        # secours ; absent sur données réelles.
        banniere = browser.js(
            "return getComputedStyle(document.querySelector("
            "'.lwc-demo-banner')).display === 'block';")
        attendu = etat["demo"] or (etat["currency"] == "EUR"
                                   and etat["eur_stale"])
        check("le bandeau d'avertissement suit le paquet",
              banniere == attendu,
              f"démo {etat['demo']}, taux de secours {etat['eur_stale']}, "
              f"bandeau {banniere}")

        # Changement d'intervalle : un refetch, une nouvelle série.
        browser.js("""
            Array.from(document.querySelectorAll('#price-interval label'))
                 .find(l => l.textContent.trim() === '4h').click();
        """)
        check("l'intervalle 4h recharge sa série", browser.wait_for(
            "window.lwcPrice.debug().interval === '4h'"
            " && window.lwcPrice.debug().bars > 200", timeout=15))

        # Bascule € : les chandeliers changent d'échelle sur place,
        # au taux voyageant avec les données — aucun refetch. Le taux
        # se lit dans un seul instantané, bougie affichée contre bougie
        # du paquet en dollars : comparer deux lectures espacées
        # mesurait aussi le cours, qui bouge entre-temps par le push.
        browser.js("""
            Array.from(document.querySelectorAll('#price-currency label'))
                 .find(l => l.textContent.trim() === '€').click();
        """)
        time.sleep(1.2)
        apres = browser.js("return window.lwcPrice.debug();")
        check("la bascule € met les bougies au taux",
              apres["lastClose"] and apres["packetClose"] and abs(
                  apres["lastClose"] / apres["packetClose"]
                  - apres["eur_rate"]) < 1e-6,
              f"{apres['packetClose']} $ → {apres['lastClose']} €")
        check("le carnet passe en euros avec le graphique", browser.wait_for(
            "document.querySelector('#book-table').textContent"
            ".includes(' € ·')", timeout=5))
        check("l'arbitrage aussi", browser.wait_for(
            "document.querySelector('#arb-table').textContent"
            ".includes('(€)')", timeout=5))
        browser.js("""
            Array.from(document.querySelectorAll('#price-currency label'))
                 .find(l => l.textContent.trim() === '$').click();
        """)
        check("le carnet revient en dollars", browser.wait_for(
            "document.querySelector('#book-table').textContent"
            ".includes(' $ ·')", timeout=5))
        time.sleep(1)

        print("\nLecture et gestes du panneau prix")
        lecture = browser.js("return window.lwcPrice.debug().readout;")
        check("la dernière bougie se lit sans survol",
              "O " in (lecture or "") and "%" in lecture,
              (lecture or "")[:60])
        cadre = browser.js("""
            const r = document.querySelector('#price-lwc canvas')
                .getBoundingClientRect();
            return {left: r.left, top: r.top, width: r.width, height: r.height};
        """)
        browser.hover(cadre["left"] + cadre["width"] * 0.3,
                      cadre["top"] + cadre["height"] * 0.3)
        survol = browser.wait_for(
            f"window.lwcPrice.debug().readout !== {lecture!r}", timeout=4)
        lu = browser.js("return window.lwcPrice.debug().readout;")
        check("le crosshair lit la bougie sous la souris", survol, lu[:60])

        y = int(cadre["top"] + cadre["height"] * 0.25)
        x = int(cadre["left"] + cadre["width"] * 0.3)
        avant = browser.js("return window.lwcPrice.debug().alerts;")
        browser.alt_click(x, y)
        check("Alt + clic pose un seuil", browser.wait_for(
            f"window.lwcPrice.debug().alerts === {avant + 1}", timeout=5))
        # Au-delà de la fenêtre du double-clic, que la bibliothèque garde
        # pour elle : c'est un second clic, pas un double.
        time.sleep(0.8)
        browser.alt_click(x, y)
        check("Alt + clic au même endroit le retire", browser.wait_for(
            f"window.lwcPrice.debug().alerts === {avant}", timeout=5))

        panes = browser.js("return window.lwcPrice.debug().panes;")
        browser.js("""
            Array.from(document.querySelectorAll('#price-extras label'))
                 .find(l => l.textContent.trim() === 'CVD').click();
        """)
        check("le CVD ouvre son pane", browser.wait_for(
            f"window.lwcPrice.debug().panes === {panes + 1}"
            " && window.lwcPrice.debug().cvd > 200", timeout=8),
            browser.js("return window.lwcPrice.debug().cvd;"))
        browser.js("""
            Array.from(document.querySelectorAll('#price-extras label'))
                 .find(l => l.textContent.trim() === 'CVD').click();
        """)
        browser.wait_for(f"window.lwcPrice.debug().panes === {panes}", timeout=8)

        browser.press("3")
        check("la touche 3 passe en 1h", browser.wait_for(
            "window.lwcPrice.debug().interval === '1h'"
            " && window.lwcPrice.debug().bars > 200", timeout=10))
        browser.press("e")
        check("E bascule la devise", browser.wait_for(
            "window.lwcPrice.debug().currency === 'EUR'", timeout=5))
        browser.press("e")
        browser.wait_for("window.lwcPrice.debug().currency === 'USD'", timeout=5)
        browser.press("?")
        check("? ouvre l'aide", browser.wait_for(
            "!!document.querySelector('.keys-help')", timeout=3))
        browser.press("\ue00c")
        check("Échap la referme", browser.wait_for(
            "!document.querySelector('.keys-help')", timeout=3))
        # Une ligne de tendance : tracée à la souris, gardée à travers la
        # devise et l'intervalle, retirée d'un Alt + clic.
        echelle = browser.js("return window.lwcPrice.debug().autoScale;")
        browser.press("t")
        check("T arme le tracé", browser.wait_for(
            "window.lwcPrice.debug().drawMode === true", timeout=3))
        x0, x1 = int(cadre["left"] + cadre["width"] * 0.3), \
            int(cadre["left"] + cadre["width"] * 0.6)
        y0, y1 = int(cadre["top"] + cadre["height"] * 0.45), \
            int(cadre["top"] + cadre["height"] * 0.35)
        browser.drag(x0, y0, x1, y1)
        check("un glisser trace la ligne, et désarme le tracé", browser.wait_for(
            "window.lwcPrice.debug().drawLines.length === 1"
            " && window.lwcPrice.debug().drawMode === false", timeout=5))
        check("la ligne ne touche pas à l'échelle des prix", browser.js(
            "return window.lwcPrice.debug().autoScale;") == echelle)
        browser.press("e")
        browser.wait_for("window.lwcPrice.debug().currency === 'EUR'", timeout=5)
        ligne = browser.js("""
            const d = window.lwcPrice.debug();
            return d.drawLines[0][0].value / d.drawings[0].p0 / d.eur_rate;
        """)
        check("elle suit la devise", abs(ligne - 1) < 1e-9, ligne)
        browser.press("e")
        browser.wait_for("window.lwcPrice.debug().currency === 'USD'", timeout=5)
        browser.press("7")
        browser.wait_for("window.lwcPrice.debug().interval === '1d'"
                         " && window.lwcPrice.debug().bars > 200", timeout=10)
        check("elle survit au changement d'intervalle", browser.js(
            "return window.lwcPrice.debug().drawLines.length;") == 1)
        browser.press("4")
        browser.wait_for("window.lwcPrice.debug().interval === '4h'"
                         " && window.lwcPrice.debug().drawLines.length === 1"
                         " && window.lwcPrice.debug().bars > 200", timeout=10)
        time.sleep(0.8)
        # Le changement d'intervalle a recadré la vue : la ligne n'est plus
        # où elle a été tracée, et c'est le graphique qui sait où elle est.
        milieu = browser.js("""
            const [a, b] = window.lwcPrice.debug().drawLines[0];
            const p = window.lwcPrice.screen(a.time, a.value);
            const q = window.lwcPrice.screen(b.time, b.value);
            return {x: (p.x + q.x) / 2, y: (p.y + q.y) / 2};
        """)
        browser.alt_click(cadre["left"] + milieu["x"], cadre["top"] + milieu["y"])
        check("Alt + clic retire la ligne, sans poser d'alerte", browser.wait_for(
            "window.lwcPrice.debug().drawLines.length === 0", timeout=5)
            and browser.js("return window.lwcPrice.debug().alerts;") == 0)

        browser.press("s")
        check("S masque les flèches", browser.wait_for(
            "window.lwcPrice.debug().signalsShown === false", timeout=3))
        browser.press("s")
        browser.wait_for("window.lwcPrice.debug().signalsShown === true",
                         timeout=3)

        # N : les niveaux clés. Sur la démonstration, aucun — des repères
        # tirés d'une marche aléatoire seraient faux.
        demo = browser.js("return window.lwcPrice.debug().demo;")
        browser.press("n")
        niveaux = browser.wait_for(
            "window.lwcPrice.debug() && window.lwcPrice.debug().levels.length > 0",
            timeout=15)
        trouves = browser.js("return window.lwcPrice.debug().levels;") or []
        check("N trace les niveaux clés, aucun sur la démo",
              (not niveaux and demo) or {"H J-1", "B J-1"} <= {
                  t.split("/")[0] for t in trouves},
              ", ".join(trouves))
        browser.press("n")
        check("N les retire", browser.wait_for(
            "window.lwcPrice.debug() && window.lwcPrice.debug().levels.length === 0",
            timeout=15))

        # M : la carte des liquidations, lue au journal — un terminal lancé
        # sans journal le dit sur le graphique au lieu de rester muet.
        browser.press("m")
        carte = browser.wait_for(
            "window.lwcPrice.debug() && window.lwcPrice.debug().liqmap !== null",
            timeout=15)
        lue = browser.js("return window.lwcPrice.debug().liqmap;")
        check("M pose la carte des liquidations, ou dit pourquoi elle est vide",
              carte, str(lue)[:60])
        browser.press("m")
        check("M la retire", browser.wait_for(
            "window.lwcPrice.debug() && window.lwcPrice.debug().liqmap === null",
            timeout=15))

        browser.press("/")
        check("/ ouvre la ligne de commande", browser.wait_for(
            "document.activeElement === document.querySelector('.command-bar input')",
            timeout=3))
        for touche in ("1", "d", "\ue007"):
            browser.press(touche)
        check("une commande change l'intervalle", browser.wait_for(
            "window.lwcPrice.debug().interval === '1d'"
            " && !document.querySelector('.command-bar')", timeout=8))
        browser.press("/")
        browser.wait_for("!!document.querySelector('.command-bar')", timeout=3)
        for touche in ("z", "z", "\ue007"):
            browser.press(touche)
        check("une commande inconnue reste à l'écran et le dit", browser.wait_for(
            "!!document.querySelector('.command-bar-error')", timeout=3))
        browser.press("\ue00c")
        check("Échap ferme la ligne de commande", browser.wait_for(
            "!document.querySelector('.command-bar')", timeout=3))
        check("alerte 90000 pose le seuil", browser.js(
            "return window.btcCommand('alerte 90000');") and browser.wait_for(
                "window.lwcPrice.debug().alertLevels.some(a => a.level === 90000)",
                timeout=5))
        browser.js("window.dash_clientside.set_props('alert-config',"
                   " {data: {price_levels: []}});")
        browser.wait_for("window.lwcPrice.debug().alerts === 0", timeout=5)
        check("une commande ouvre un onglet", browser.js(
            "return window.btcCommand('perpetuel');") and browser.wait_for(
                "document.querySelector('#cell-etf .cell-tab-active')"
                ".textContent === 'PERPÉTUEL'", timeout=5))
        browser.js("window.btcCommand('etf');")
        capture = browser.js("""
            const vrai = HTMLAnchorElement.prototype.click;
            let lien = null;
            HTMLAnchorElement.prototype.click = function () { lien = this; };
            window.btcCommand('capture');
            HTMLAnchorElement.prototype.click = vrai;
            return lien ? [lien.download, lien.href.slice(0, 22),
                           lien.href.length] : null;
        """)
        check("la capture produit une image",
              capture and capture[1] == "data:image/png;base64,"
              and capture[2] > 10_000, capture and capture[0])
        browser.press("4")
        # La série affichée, pas l'intervalle demandé : pan sur l'ancienne
        # série, ce contrôle provoquait la course qu'il croyait vérifier —
        # une page du nouvel intervalle collée devant la série du précédent.
        browser.wait_for("window.lwcPrice.debug().loaded === '4h'"
                         " && window.lwcPrice.debug().bars > 200", timeout=10)

        # Historique infini : un pan appuyé vers la gauche déclenche
        # la page antérieure. Hors ligne, la source n'a rien avant :
        # le tampon ne grandit pas, et selon la série, le « plus ancien
        # atteint » est retenu (démonstration) ou un réessai est
        # programmé (disque sans cette page) — les trois issues prouvent
        # l'aller-retour.
        avant = browser.js("return window.lwcPrice.debug();")
        browser.js("window.lwcPrice.pan(-30, 120);")
        recharge = browser.wait_for(
            f"window.lwcPrice.debug().bars > {avant['bars']}"
            " || window.lwcPrice.debug().exhausted === true"
            " || window.lwcPrice.debug().backfillRetry === true", timeout=15)
        apres = browser.js("return window.lwcPrice.debug();")
        if apres["bars"] > avant["bars"]:
            check("le pan vers le passé charge des bougies antérieures",
                  apres["firstTime"] < avant["firstTime"],
                  f"{avant['bars']} → {apres['bars']} bougies")
        elif apres["backfillRetry"]:
            check("page manquée faute de source : réessai, passé non clos",
                  recharge and not apres["exhausted"],
                  f"{apres['bars']} bougies, exhausted={apres['exhausted']}")
        else:
            check("historique épuisé retenu (source sans passé)",
                  recharge and apres["exhausted"],
                  f"{apres['bars']} bougies, exhausted={apres['exhausted']}")

        # Recalage silencieux — reconnexion, retour d'onglet, repli poll :
        # la page fraîche remplace la fin du tampon, le passé remonté
        # reste, et la vue posée dedans ne bouge pas. Il remplaçait tout,
        # et la vue sautait de la longueur du passé remonté.
        if apres["bars"] > avant["bars"]:
            browser.js("window.lwcPrice.pan(100, 200);")
            time.sleep(0.5)
            pose = browser.js("return window.lwcPrice.debug();")
            serie = ("performance.getEntriesByType('resource').filter(e =>"
                     " e.name.includes('/api/klines?')"
                     " && !e.name.includes('before=')).length")
            browser.js("performance.clearResourceTimings();")
            browser.js("window.lwcPrice.resync();")
            browser.wait_for(f"{serie} > 0", timeout=10)
            time.sleep(0.3)
            recale = browser.js("return window.lwcPrice.debug();")
            check("un recalage garde le passé remonté, et la vue",
                  recale["firstTime"] == pose["firstTime"]
                  and recale["bars"] >= pose["bars"]
                  and abs(recale["range"]["from"] - pose["range"]["from"]) < 1
                  and recale["steps"] == pose["steps"],
                  f"{pose['bars']} → {recale['bars']} bougies, plage "
                  f"{pose['range']['from']:.0f} → {recale['range']['from']:.0f},"
                  f" écarts {pose['steps']} → {recale['steps']}")

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

        print("\nZoom rectangulaire")
        # Maj + glisser sur le pane du cours : le geste que la
        # bibliothèque n'offre pas, joué à la vraie souris — c'est aussi
        # ce qui prouve que lwc-price.js capte l'événement avant le
        # canvas, qui sinon panoterait. La sonde dit le résultat
        # effectif : l'échelle des prix a quitté l'auto-ajustement et
        # les deux plages se sont resserrées.
        check("le rappel du geste est visible en plein écran", browser.js(
            "const h = document.querySelector('#price-lwc .lwc-zoom-hint');"
            "return !!h && getComputedStyle(h).display !== 'none';"))
        avant = browser.js("return window.lwcPrice.debug();")
        check("l'axe des prix s'auto-ajuste au départ",
              avant["autoScale"] is True)
        cadre = browser.js(
            "const r = document.getElementById('price-lwc')"
            ".getBoundingClientRect();"
            "return {left: r.left, top: r.top, width: r.width,"
            " height: r.height};")
        # Le cadre entoure la dernière clôture — ±5 % de la plage
        # visible, en coordonnées du pane : le cours est seul, l'axe du
        # temps prend le bas du panneau. Un cadre posé dans le vide
        # au-dessus des bougies zoomerait tout autant, mais ne montrerait
        # rien.
        plage = avant["priceRange"]
        pane = cadre["height"] - 28
        def y(price):
            return int(cadre["top"] + (plage["to"] - price)
                       / (plage["to"] - plage["from"]) * pane)
        marge = 0.05 * (plage["to"] - plage["from"])
        browser.shift_drag(int(cadre["left"] + cadre["width"] * 0.55),
                           y(avant["lastClose"] + marge),
                           int(cadre["left"] + cadre["width"] * 0.85),
                           y(avant["lastClose"] - marge))
        time.sleep(1)
        apres = browser.js("return window.lwcPrice.debug();")
        check("le cadre fixe l'axe des prix", apres["autoScale"] is False)
        check("la plage de prix s'est resserrée",
              apres["priceRange"] is not None
              and (apres["priceRange"]["to"] - apres["priceRange"]["from"])
              < 0.9 * (avant["priceRange"]["to"] - avant["priceRange"]["from"]),
              f"{avant['priceRange']} → {apres['priceRange']}")
        check("la fenêtre de temps s'est resserrée",
              (apres["range"]["to"] - apres["range"]["from"])
              < 0.9 * (avant["range"]["to"] - avant["range"]["from"]),
              f"{avant['range']} → {apres['range']}")
        check("le cadre ne reste pas à l'écran", browser.js(
            "return document.querySelector('#price-lwc .lwc-zoom-box')"
            ".style.display === 'none';"))
        if capture_dir:
            browser.screenshot(str(capture_dir / "zoom-cadre.png"))
        # Double-clic sur le graphique : la vue entière, l'axe rendu à
        # l'auto-ajustement — le geste de Plotly, que les autres panneaux
        # gardent.
        browser.double_click(int(cadre["left"] + cadre["width"] * 0.5),
                             int(cadre["top"] + 200))
        time.sleep(1)
        entier = browser.js("return window.lwcPrice.debug();")
        check("double-clic : l'axe des prix s'auto-ajuste de nouveau",
              entier["autoScale"] is True)
        # Mesuré contre la série d'avant le geste : la vue entière
        # touche le bord gauche, ce qui demande la page antérieure — et
        # les bougies qui arrivent entre-temps ne sont pas dans la vue.
        check("double-clic : toute la série en largeur",
              entier["range"]["to"] - entier["range"]["from"]
              >= 0.95 * apres["bars"],
              f"{entier['range']} pour {apres['bars']} bougies")

        browser.ctrl_drag(int(cadre["left"] + cadre["width"] * 0.3),
                          int(cadre["top"] + cadre["height"] * 0.6),
                          int(cadre["left"] + cadre["width"] * 0.6),
                          int(cadre["top"] + cadre["height"] * 0.3))
        mesure = browser.js("return window.lwcPrice.debug().measure;")
        check("Ctrl + glisser mesure et reste à l'écran",
              "bougies" in (mesure or "") and browser.js(
                  "return getComputedStyle(document.querySelector("
                  "'.lwc-zoom-box')).display === 'block';"), mesure)
        browser.press("\ue00c")
        check("Échap efface la mesure sans quitter le plein écran",
              browser.wait_for(
                  "!window.lwcPrice.debug().measure", timeout=3) and browser.js(
                  "return !!document.querySelector('.cell-max');"))

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
        # Ces panneaux lisent des sources REST : deux secondes à froid, et
        # davantage quand le réseau traîne. Attendre le contenu plutôt que
        # de dormir : c'est lui qu'on contrôle, pas la latence de la source.
        # Trente secondes : Binance Futures dépasse ses délais depuis
        # certains pays, et le rendu enchaîne plusieurs lectures.
        browser.wait_for(
            "(t => t.includes('OI') || t.includes('indisponible'))"
            "(document.getElementById('perp-badges')?.textContent || '')",
            timeout=30)
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

        check("les plateformes se comparent", browser.wait_for(
            "(document.getElementById('perp-compare')?.textContent || '')"
            ".includes('OI')", timeout=30),
            browser.js("return document.getElementById('perp-compare')"
                       ".textContent;")[:60])
        check("et tout tient dans la largeur", browser.js(
            "const e = document.getElementById('perp-compare');"
            "return e.scrollWidth <= e.clientWidth;"))

        print("\nOnglet position")
        browser.js("window.btcCommand('position');")
        browser.wait_for("!!document.getElementById('position-result')", timeout=8)

        def saisir(champ, valeur):
            browser.js(f"document.getElementById('{champ}').focus();")
            for touche in (*valeur, "\ue007"):
                browser.press(touche)

        saisir("position-qty", "0.5")
        saisir("position-entry", "70000")
        saisir("position-stop", "65000")
        saisir("position-capital", "10000")
        check("la position se suit", browser.wait_for(
            "document.getElementById('position-result').textContent"
            ".includes('multiple')", timeout=8),
            browser.js("return document.getElementById('position-result')"
                       ".textContent;")[:70])
        check("la taille se calcule d'après l'ATR", browser.wait_for(
            "document.getElementById('position-result').textContent"
            ".includes('taille')", timeout=8))
        check("le panneau tient dans sa cellule", browser.js(
            "const e = document.getElementById('position-result');"
            "return e.scrollHeight <= e.clientHeight;"))
        check("les six champs tiennent sur une ligne", browser.js(
            "const a = document.getElementById('position-qty');"
            "const b = document.getElementById('position-atr');"
            "return Math.abs(a.getBoundingClientRect().top"
            " - b.getBoundingClientRect().top) < 3;"))
        check("le bandeau la suit, panneau replié", browser.js(
            "return window.btcCommand('etf');") and browser.wait_for(
                "document.getElementById('hdr-position').textContent"
                ".startsWith('long')", timeout=8),
            browser.js("return document.getElementById('hdr-position')"
                       ".textContent;"))

        print("\nOnglets dominance et on-chain")
        for label, graphe, badge in (("DOMINANCE", "dominance-chart", "dominance-badges"),
                                     ("ON-CHAIN", "onchain-chart", "onchain-badges")):
            browser.js(f"""
                Array.from(document.querySelectorAll('#cell-macro .cell-tab'))
                     .find(t => t.textContent === '{label}').click();
            """)
            browser.wait_for(
                f"!!document.querySelector('#{graphe} .js-plotly-plot')"
                f" && (document.getElementById('{badge}')?.textContent"
                f" || '').length > 8",
                timeout=30)
            etat = browser.js(f"""
                const gd = document.getElementById('{graphe}')
                    .querySelector('.js-plotly-plot');
                const notes = gd ? (gd.layout.annotations || [])
                    .map(a => a.text).join(' ') : '';
                return {{series: gd ? gd.data.length : 0, notes: notes,
                        badge: document.getElementById('{badge}').textContent}};
            """)
            # Une série de chaîne qui dépasse son délai laisse le graphique
            # vide pendant que l'instantané chiffre le titre : le panneau
            # doit alors le dire, pas rester muet.
            check(f"{label} : tracé et chiffré, ou l'absence dite",
                  (etat["series"] >= 1 or "indisponible" in etat["notes"])
                  and len(etat["badge"]) > 8,
                  etat["badge"][:44])

        print("\nOnglet analyse")
        browser.js("""
            Array.from(document.querySelectorAll('#cell-macro .cell-tab'))
                 .find(t => t.textContent === 'ANALYSE').click();
        """)
        browser.wait_for(
            "(document.getElementById('analysis-summary')?.textContent || '')"
            ".includes('RSI')"
            " && (document.getElementById('analysis-stats')?.textContent || '')"
            ".length > 20", timeout=15)
        analyse = browser.js("""
            return {resume: document.getElementById('analysis-summary').textContent,
                    bilan: document.getElementById('analysis-stats').textContent};
        """)
        check("les intervalles se lisent d'un coup d'œil",
              "15m" in analyse["resume"] and "1w" in analyse["resume"],
              analyse["resume"][:60])
        check("le bilan dit sa référence",
              "rester investi" in analyse["bilan"]
              or "insuffisant" in analyse["bilan"], analyse["bilan"][:60])
        browser.js("""
            Array.from(document.querySelectorAll('#analysis-view label'))
                 .find(l => l.textContent.trim() === 'règles').click();
        """)
        check("les règles se comparent à la détention", browser.wait_for(
            "(document.getElementById('analysis-stats')?.textContent || '')"
            ".includes('rester investi')"
            " && document.getElementById('analysis-stats').textContent"
            ".includes('repli max')", timeout=10))
        browser.js("""
            Array.from(document.querySelectorAll('#analysis-view label'))
                 .find(l => l.textContent.trim() === 'mois').click();
        """)
        check("les mois se lisent par année", browser.wait_for(
            "(document.getElementById('analysis-stats')?.textContent || '')"
            ".includes('déc')", timeout=10))
        lien = browser.js(
            "return document.getElementById('analysis-export').href;")
        with urllib.request.urlopen(lien, timeout=10) as reponse:
            debut = reponse.read(200).decode()
        check("l'export se télécharge", debut.startswith("time,open,"), lien)
        browser.js("""
            Array.from(document.querySelectorAll('#cell-macro .cell-tab'))
                 .find(t => t.textContent === 'MACRO').click();
        """)

        print("\nOnglet liquidations")
        browser.js("""
            Array.from(document.querySelectorAll('#cell-arb .cell-tab'))
                 .find(t => t.textContent === 'LIQUIDATIONS').click();
        """)
        browser.wait_for("!!document.getElementById('liq-table')"
                         " && !!document.getElementById('liq-badges')", timeout=10)
        time.sleep(1)
        liq = browser.js("""
            return {badges: document.getElementById('liq-badges').textContent,
                    table: document.getElementById('liq-table').textContent};
        """)
        # Le flux est épisodique : le panneau peut légitimement être vide,
        # mais il doit alors le dire — et jamais rester muet.
        check("le fil dit son état", len(liq["badges"]) > 8, liq["badges"][:44])
        check("le tableau dit le sien", len(liq["table"]) > 4, liq["table"][:44])
        check("les prix s'y écrivent en clair", "e+" not in liq["table"])

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
        # Attendre les deux séries plutôt que dormir trois secondes : sous
        # charge, la figure n'était pas encore là.
        browser.wait_for("""(() => {
            const el = document.getElementById('macro-chart');
            const gd = el && el.querySelector('.js-plotly-plot');
            return !!(gd && gd.data && gd.data.length >= 2);
        })()""", timeout=30)

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

        print("\nPanneau options")
        # Onglet de la rangée basse ; Deribit muet, le panneau doit le dire
        # plutôt que rester vide — le contrôle vérifie l'écran, pas la source.
        browser.js("""
            Array.from(document.querySelectorAll('.cell-tab'))
                 .find(t => t.textContent.trim() === 'OPTIONS').click();
        """)
        check("l'onglet ouvre le panneau", browser.wait_for(
            "!!document.querySelector('#options-chart .js-plotly-plot')",
            timeout=20))
        time.sleep(4)
        badges = browser.js(
            "return document.getElementById('options-badges').textContent;")
        check("DVOL au bandeau, ou l'absence de Deribit dite",
              "DVOL" in badges or "indisponible" in badges, badges[:48])

        #: Ce qui dit qu'une vue est rendue : une de ses courbes, ou une
        #: figure vide qui annonce son absence. Un délai fixe lisait sous
        #: charge la figure de la vue précédente.
        signature = {"TERME": "à la monnaie", "STRIKES": "calls",
                     "HISTO": "skew 25Δ", "VOL": "DVOL (implicite 30 j)"}

        def options_view(label):
            browser.js("""
                Array.from(document.querySelectorAll('#options-view label'))
                     .find(l => l.textContent.trim() === arguments[0]).click();
            """, [label])
            browser.wait_for(f"""(() => {{
                const gd = document.querySelector('#options-chart .js-plotly-plot');
                if (!gd || !gd.data) return false;
                const vide = gd.data.length === 0
                    && (gd.layout.annotations || []).length > 0;
                return vide || gd.data.some(t => t.name === '{signature[label]}');
            }})()""", timeout=30)
            return browser.js("""
                const gd = document.querySelector('#options-chart .js-plotly-plot');
                return {traces: gd.data.map(t => t.name),
                        vide: (gd.layout.annotations || []).length > 0 &&
                              gd.data.length === 0,
                        echeances: Array.from(document.querySelectorAll(
                            '#options-expiry label')).length,
                        cochee: !!document.querySelector(
                            '#options-expiry input:checked'),
                        selecteur: getComputedStyle(document.getElementById(
                            'options-expiry')).display};
            """)

        terme = options_view("TERME")
        check("TERME : monnaie, ailes 25Δ et base, ou vide annoté",
              terme["vide"] or {"à la monnaie", "put 25Δ", "call 25Δ",
                                "base annualisée"} <= set(terme["traces"]),
              ", ".join(terme["traces"]))
        check("TERME : pas de sélecteur d'échéance",
              terme["selecteur"] == "none", terme["selecteur"])
        strikes = options_view("STRIKES")
        check("STRIKES : calls et puts, ou vide annoté",
              strikes["vide"] or {"calls", "puts"} <= set(strikes["traces"]),
              ", ".join(strikes["traces"]))
        check("STRIKES : échéances au choix, l'une cochée",
              strikes["vide"] or (strikes["echeances"] >= 1
                                  and strikes["cochee"]),
              f"{strikes['echeances']} échéances")
        histo = options_view("HISTO")
        annotation = browser.js("""
            const gd = document.querySelector('#options-chart .js-plotly-plot');
            return ((gd.layout.annotations || [])[0] || {}).text || '';
        """)
        check("HISTO : DVOL, monnaie et skew, ou l'historique en constitution",
              {"DVOL", "skew 25Δ"} <= set(histo["traces"])
              or "en constitution" in annotation,
              ", ".join(histo["traces"]) or annotation[:48])
        vol = options_view("VOL")
        check("VOL : DVOL contre réalisée, ou vide annoté",
              vol["vide"] or "DVOL (implicite 30 j)" in vol["traces"],
              ", ".join(vol["traces"]))
        hdr_dvol = browser.js(
            "return document.getElementById('hdr-dvol').textContent;")
        check("DVOL au bandeau, s'il est connu",
              hdr_dvol.startswith("DVOL ") or "indisponible" in badges,
              hdr_dvol)

        def macro_tab():
            browser.js("""
                Array.from(document.querySelectorAll('.cell-tab'))
                     .find(t => t.textContent.trim() === 'MACRO').click();
            """)
            time.sleep(1.5)

        # Le clic sur DVOL ramène le panneau, comme la cloche les alertes.
        macro_tab()
        browser.js("document.getElementById('hdr-dvol').click();")
        check("le clic sur DVOL ouvre le panneau options", browser.wait_for(
            "!!document.querySelector('#options-chart')", timeout=8))
        macro_tab()

        print("\nPanneau marchés")
        browser.js("""
            Array.from(document.querySelectorAll('.cell-tab'))
                 .find(t => t.textContent.trim() === 'MARCHÉS').click();
        """)
        check("l'onglet ouvre le panneau, tableau rempli", browser.wait_for(
            "(document.getElementById('markets-table') || {textContent: ''})"
            ".textContent.length > 0", timeout=30))
        marches = browser.js("""
            return {lignes: document.querySelectorAll(
                        '#markets-table tbody tr').length,
                    badges: document.getElementById('markets-badges')
                        .textContent};
        """)
        check("une ligne pour le bitcoin et une par marché",
              marches["lignes"] == 6, f"{marches['lignes']} lignes")
        check("corrélations au titre, ou l'absence dite",
              "corr." in marches["badges"] or "indisponible" in marches["badges"],
              marches["badges"][:48])
        browser.js("""
            Array.from(document.querySelectorAll('#markets-view label'))
                 .find(l => l.textContent.trim() === 'CORR').click();
        """)
        correlation = browser.wait_for("""(() => {
            const gd = document.querySelector('#markets-chart .js-plotly-plot');
            return gd && gd.data && (gd.data.length === 0
                || gd.data.every(t => t.name !== 'Bitcoin'));
        })()""", timeout=15)
        check("CORR : les marchés seuls, sans le bitcoin", correlation)
        browser.js("""
            Array.from(document.querySelectorAll('#markets-view label'))
                 .find(l => l.textContent.trim() === 'PERF').click();
        """)
        time.sleep(1)
        macro_tab()

        print("\nPanneau sources")
        # Ouvert par l'avertissement du bandeau, comme la cloche ouvre les
        # alertes ; puis la cellule rend l'onglet des liquidations, que la
        # persistance au rechargement attend plus bas.
        browser.js("document.getElementById('hdr-sources').click();")
        check("l'avertissement du bandeau ouvre le panneau", browser.wait_for(
            "(document.getElementById('sources-table') || {textContent: ''})"
            ".textContent.includes('Flux temps réel')", timeout=20))
        etat = browser.js("""
            return {lignes: document.querySelectorAll(
                        '#sources-table tr').length,
                    badges: document.getElementById('sources-badges')
                        .textContent};
        """)
        check("trois sections, chaque source sur sa ligne",
              etat["lignes"] >= 3 + 5 + 3, f"{etat['lignes']} lignes")
        check("le titre compte ce qui ne répond pas",
              "sources" in etat["badges"] or "défaut" in etat["badges"]
              or "secours" in etat["badges"], etat["badges"][:48])
        browser.js("""
            Array.from(document.querySelectorAll('#cell-arb .cell-tab'))
                 .find(t => t.textContent === 'LIQUIDATIONS').click();
        """)
        time.sleep(1.5)

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
        # Le compte vient du registre : une constante écrite ici cassait à
        # chaque panneau ajouté.
        from terminal.grid import PANEL_REGISTRY
        rangs = browser.js(
            "return document.querySelectorAll('.layout-row').length;")
        check("un rang par panneau", rangs == len(PANEL_REGISTRY),
              f"{rangs} rangs, {len(PANEL_REGISTRY)} panneaux")
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
    # Les contrôles attendent l'interface à délai fixe — une seconde et
    # demie pour un agrandissement, quarante-cinq pour une série. Une
    # machine saturée par un autre calcul les fait échouer sans que
    # l'interface soit en cause : le dire avant, plutôt que de laisser
    # chercher une régression qui n'existe pas. Le seuil est la moitié des
    # cœurs : à ce point, le navigateur attend déjà son tour.
    charge, coeurs = os.getloadavg()[0], os.cpu_count() or 1
    if charge > coeurs / 2:
        print(f"  ⚠ machine chargée — charge {charge:.0f} pour {coeurs} "
              "cœurs : les contrôles à délai peuvent échouer sans que "
              "l'interface soit en cause")
    failures = run(args.capture, args.url)
    print("\n" + "─" * 60)
    print("Interface conforme.\n" if not failures else f"{failures} contrôle(s) en échec.\n")
    sys.exit(1 if failures else 0)
