#!/usr/bin/env python3
"""
Le VWAP ancré du panneau prix (`terminal/assets/lwc-vwap.js`).

Ce qui se vérifie mal à l'écran se vérifie ici, sous Node :

- l'ancrage suit l'intervalle — le jour jusqu'à l'heure, la semaine de 2 h
  à 12 h, le mois en journalier, aucun au-delà ;
- les périodes commencent au bon instant UTC : minuit, le lundi, le 1er ;
- le VWAP est la moyenne du prix typique pondérée par le volume, ses
  bandes l'écart-type pondéré pareil, et tout repart à zéro au début
  d'une période, marqué pour que la courbe s'y rompe ;
- une bougie sans volume ne pèse rien.

Ignoré si Node est absent.

Lancement :
    python tests/test_lwc_vwap.py
"""

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

VWAP = (Path(__file__).resolve().parent.parent
        / "terminal" / "assets" / "lwc-vwap.js")

HARNESS = r"""
globalThis.window = {};
require(process.argv[2]);
const V = window.btcVwap;
const out = {};
out.anchors = ["1m", "1h", "4h", "12h", "1d", "1w", "1M"].map(V.anchorOf);
const jeudi = Date.UTC(2026, 10, 12, 15, 30) / 1000;
out.jour = V.periodStart(jeudi, "day");
out.semaine = V.periodStart(jeudi, "week");
out.mois = V.periodStart(jeudi, "month");
out.lundi = V.periodStart(Date.UTC(2026, 10, 9, 0, 0) / 1000, "week");
out.dimanche = V.periodStart(Date.UTC(2026, 10, 15, 23, 59) / 1000, "week");

const j1 = Date.UTC(2026, 10, 11) / 1000, j2 = Date.UTC(2026, 10, 12) / 1000;
const bars = [
    {time: j1, high: 101, low: 99, close: 100},        // prix typique 100
    {time: j1 + 3600, high: 112, low: 108, close: 110},  // 110
    {time: j1 + 7200, high: 500, low: 500, close: 500},  // sans volume
    {time: j2, high: 201, low: 199, close: 200},       // 200, nouvelle période
];
const volumes = [{time: j1, value: 1}, {time: j1 + 3600, value: 3},
                 {time: j1 + 7200, value: 0}, {time: j2, value: 2}];
out.calc = V.compute(bars, volumes, "day");
out.sans = V.compute(bars, volumes, null);
out.labels = ["day", "week", "month", null].map(V.label);
console.log(JSON.stringify(out));
"""


def lancer() -> dict:
    with tempfile.TemporaryDirectory() as dossier:
        harness = Path(dossier) / "harness.js"
        harness.write_text(HARNESS)
        fait = subprocess.run(["node", str(harness), str(VWAP)],
                              capture_output=True, text=True, timeout=30,
                              env={**os.environ, "TZ": "Asia/Phnom_Penh"})
        assert fait.returncode == 0, fait.stderr
        return json.loads(fait.stdout)


def test_l_ancrage_suit_l_intervalle():
    r = lancer()
    assert r["anchors"] == ["day", "day", "week", "week", "month", None, None]
    assert r["labels"] == ["J", "S", "M", ""]
    print("  ✓ jour jusqu'à 1h, semaine de 4h à 12h, mois en 1d, rien au-delà")


def test_les_periodes_commencent_en_utc():
    r = lancer()
    lundi_9 = 1_794_182_400          # 9 novembre 2026, 0 h UTC
    assert r["jour"] == lundi_9 + 3 * 86_400
    assert r["semaine"] == lundi_9 and r["lundi"] == lundi_9
    assert r["dimanche"] == lundi_9, "le dimanche appartient à la semaine du lundi"
    assert r["mois"] == 1_793_491_200, "1er novembre 2026, 0 h UTC"
    print("  ✓ minuit, lundi et 1er du mois en UTC, quel que soit le fuseau")


def test_le_vwap_et_ses_bandes():
    r = lancer()
    vwap = r["calc"]["vwap"]
    assert len(vwap) == 3, "la bougie sans volume ne compte pas"
    assert vwap[0]["value"] == 100 and vwap[0]["start"] is True
    assert vwap[1]["value"] == 107.5 and vwap[1]["start"] is False
    sd = math.sqrt((100**2 * 1 + 110**2 * 3) / 4 - 107.5**2)
    assert abs(r["calc"]["upper"][1]["value"] - (107.5 + sd)) < 1e-9
    assert abs(r["calc"]["lower"][1]["value"] - (107.5 - sd)) < 1e-9
    assert vwap[2]["value"] == 200 and vwap[2]["start"] is True, \
        "nouvelle journée : tout repart, la courbe se rompt"
    assert r["calc"]["upper"][2]["value"] == 200
    assert r["sans"] == {"vwap": [], "upper": [], "lower": []}
    print("  ✓ moyenne pondérée, écart-type pondéré, remise à zéro marquée")


if __name__ == "__main__":
    if not shutil.which("node"):
        print("Node absent : contrôle du VWAP ignoré")
        raise SystemExit(0)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nVWAP ancré — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le prix juste de la séance, au bon instant.\n")
