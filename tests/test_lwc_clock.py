#!/usr/bin/env python3
"""
L'horloge des graphiques Lightweight Charts (`terminal/assets/lwc-clock.js`).

Le panneau prix et le perpétuel affichent l'heure locale du navigateur,
l'UTC sur demande ; la bibliothèque, elle, ne connaît que l'UTC, et
l'horloge décale les temps qu'on lui donne. Ce qui se vérifie mal à
l'écran se vérifie ici, sous Node, dans trois fuseaux posés par `TZ` :

- Phnom Penh, décalage fixe : sept heures, aller et retour exacts ;
- Paris, au passage à l'heure d'hiver du 25 octobre 2026 : l'heure locale
  se répète, deux bougies horaires tomberaient sur le même temps affiché
  — l'horloge garde l'ordre strict, et chaque temps se recale sur le
  sien ; une bougie d'août garde son heure d'été ;
- Calcutta, décalage d'une demi-heure, pour le libellé du fuseau.

Ignoré si Node est absent.

Lancement :
    python tests/test_lwc_clock.py
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CLOCK = (Path(__file__).resolve().parent.parent
         / "terminal" / "assets" / "lwc-clock.js")

HARNESS = r"""
globalThis.window = {};
require(process.argv[2]);
const C = window.btcClock;
const H = 3600;
const out = {};

// Bougies horaires du 24 octobre 2026, 22 h UTC, au 25, 4 h UTC.
const debut = Date.UTC(2026, 9, 24, 22) / 1000;
const horaires = [];
for (let i = 0; i <= 6; i++) horaires.push({time: debut + i * H, value: i});
const horloge = C.create(true).addAll(horaires);
out.shown = horaires.map(p => horloge.shown(p.time));
out.retour = out.shown.map(t => horloge.actual(t));
out.points = horloge.points(horaires);
out.source_intacte = horaires[0].time === debut;

// Bougies de 4 h, une semaine plus tôt et une d'août : rien à tasser.
const aout = Date.UTC(2026, 7, 10, 12) / 1000;
const decembre = Date.UTC(2026, 11, 10, 12) / 1000;
out.decalage_aout = C.create(true).shown(aout) - aout;
out.decalage_decembre = C.create(true).shown(decembre) - decembre;
// Un temps inconnu de l'horloge se recale quand même.
out.recale_inconnu = C.create(true).actual(C.create(true).shown(decembre));

// Sans décalage : l'UTC, la même liste, les mêmes temps.
const immobile = C.create(false).addAll(horaires);
out.immobile_meme_liste = immobile.points(horaires) === horaires;
out.immobile_temps = immobile.shown(debut) === debut
    && immobile.actual(debut) === debut;

// Deux séries sur un axe : l'union de leurs temps, dans l'ordre.
const a = [{time: debut}, {time: debut + 2 * H}];
const b = [{time: debut + H}, {time: debut + 3 * H}];
const deux = C.create(true).addAll(a, b);
out.union = [debut, debut + H, debut + 2 * H, debut + 3 * H]
    .map(t => deux.shown(t));

// Une bougie poussée après coup s'inscrit à la suite ; une plus ancienne
// ne bouscule pas l'ordre inscrit.
const suite = C.create(true).addAll(horaires);
out.poussee = suite.add(debut + 7 * H) - suite.shown(debut + 6 * H);
suite.add(debut - 10 * H);
out.ancienne_non_inscrite = !suite.toShown.has(debut - 10 * H);

out.zone_decembre = C.zone(decembre);
out.zone_aout = C.zone(aout);
console.log(JSON.stringify(out));
"""


def lancer(fuseau: str) -> dict:
    with tempfile.TemporaryDirectory() as dossier:
        harness = Path(dossier) / "harness.js"
        harness.write_text(HARNESS)
        fait = subprocess.run(
            ["node", str(harness), str(CLOCK)], capture_output=True,
            text=True, timeout=30, env={**os.environ, "TZ": fuseau})
        assert fait.returncode == 0, fait.stderr
        return json.loads(fait.stdout)


def test_phnom_penh_sept_heures_exactes():
    r = lancer("Asia/Phnom_Penh")
    debut = 1_792_879_200  # 24 octobre 2026, 22 h UTC
    assert r["shown"] == [debut + i * 3600 + 7 * 3600 for i in range(7)]
    assert r["retour"] == [debut + i * 3600 for i in range(7)]
    assert [p["time"] for p in r["points"]] == r["shown"]
    assert [p["value"] for p in r["points"]] == list(range(7)), "valeurs gardées"
    assert r["source_intacte"], "le paquet garde ses temps réels"
    assert r["decalage_aout"] == r["decalage_decembre"] == 7 * 3600
    assert r["immobile_meme_liste"] and r["immobile_temps"]
    assert r["union"] == [debut + i * 3600 + 7 * 3600 for i in range(4)]
    assert r["poussee"] == 3600 and r["ancienne_non_inscrite"]
    assert r["zone_decembre"] == r["zone_aout"] == "UTC+7"
    print("  ✓ Phnom Penh : sept heures, aller et retour exacts, UTC+7")


def test_paris_l_heure_qui_se_repete():
    r = lancer("Europe/Paris")
    shown = r["shown"]
    assert all(b > a for a, b in zip(shown, shown[1:])), \
        f"ordre strict perdu au passage à l'heure d'hiver : {shown}"
    debut = 1_792_879_200
    assert r["retour"] == [debut + i * 3600 for i in range(7)], \
        "chaque temps affiché se recale sur sa bougie"
    # 22 h et 23 h UTC à l'heure d'été (+2), 1 h UTC et après à l'heure
    # d'hiver (+1) : 1 h UTC tomberait sur 2 h locales, déjà prises par
    # minuit UTC — elle est posée une seconde après.
    assert shown[0] - debut == 7200 and shown[2] - (debut + 2 * 3600) == 7200
    assert shown[3] == shown[2] + 1
    assert shown[6] - (debut + 6 * 3600) == 3600
    assert r["decalage_aout"] == 7200 and r["decalage_decembre"] == 3600, \
        "une bougie d'août garde son heure d'été"
    assert r["recale_inconnu"] == 1_796_904_000
    assert r["zone_decembre"] == "UTC+1" and r["zone_aout"] == "UTC+2"
    print("  ✓ Paris : l'heure répétée tassée sans désordre, été et hiver tenus")


def test_calcutta_la_demi_heure():
    r = lancer("Asia/Kolkata")
    assert r["zone_decembre"] == "UTC+5:30"
    assert r["decalage_decembre"] == 5 * 3600 + 1800
    print("  ✓ Calcutta : UTC+5:30")


if __name__ == "__main__":
    if not shutil.which("node"):
        print("Node absent : contrôle de l'horloge ignoré")
        raise SystemExit(0)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nHorloge des graphiques — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Heure locale, UTC sur demande, et jamais deux bougies au même instant.\n")
