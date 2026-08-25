#!/usr/bin/env python3
"""
Démo statique (terminal/demo.py + docs/demo/shim.js), sans réseau.

La démo remplace le serveur par un `fetch` détourné : ce test vérifie
que ce remplaçant dit la même chose que l'original.

- Le constructeur écrit ce qu'il annonce : un paquet par intervalle au
  contrat de `/api/klines` (barres triées, `interval`, `eur_rate`, pas
  de drapeau démo), les assets copiés, une page qui les référence, et
  `.nojekyll` pour GitHub Pages. Un hub factice sert des séries
  synthétiques ; un intervalle vide est écarté de la page.
- Sous Node, le shim pagine comme terminal/lwc.py — `limit` borne la
  page, `before` exclut la bougie tenue, les autres tableaux suivent la
  fenêtre — et son profil de volume est celui de
  `btcterm.indicators.volume_profile`, aux arrondis près.

La partie Node est ignorée si Node est absent.

Lancement :
    python tests/test_demo.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm import indicators as ind  # noqa: E402
from btcterm import sources  # noqa: E402
from terminal import demo  # noqa: E402
from terminal.charts import VOL_BINS  # noqa: E402

SHIM = Path(__file__).resolve().parent.parent / "docs" / "demo" / "shim.js"


class HubFactice:
    """Sert une marche aléatoire par intervalle ; rien pour `1M`."""

    def __init__(self, seed_rows=1300):
        self.rows = seed_rows
        self.calls = []

    def klines_history(self, interval, end_ms, limit=350):
        self.calls.append((interval, end_ms, limit))
        if interval == "1M":
            return pd.DataFrame()
        return sources.generate_demo_ohlcv(min(limit, self.rows),
                                           interval=interval, index=False)

    def eur_rate(self):
        return 0.86


def construire(tmp, bars=300):
    return demo.build(Path(tmp), HubFactice(), bars=bars, now=1_787_000_000,
                      log=lambda *_: None)


def test_le_constructeur_ecrit_ce_qu_il_annonce():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        resume = construire(tmp)
        assert "1M" not in resume["intervals"], "intervalle vide écarté"
        assert resume["intervals"]["1d"] == 300
        for dst in demo.ASSETS.values():
            assert (out / "demo" / dst).exists(), dst
        assert (out / ".nojekyll").exists()

        page = (out / "index.html").read_text()
        for ref in ("demo/terminal.css", "demo/shim.js", "demo/lwc-price.js",
                    "demo/page.js", "demo/vendor/lightweight-charts"):
            assert ref in page, ref
        from datetime import datetime, timezone
        attendu = datetime.fromtimestamp(1_787_000_000, timezone.utc).strftime("%d/%m/%Y")
        assert attendu in page, "date figée écrite dans la page"
        conf = json.loads(page.split("window.DEMO_CONF = ", 1)[1].split(";</script>", 1)[0])
        assert set(conf["intervals"]) == set(resume["intervals"])
        assert conf["default_interval"] == "1d"
        assert conf["theme"]["bg"] and conf["mono"]

        packet = json.loads((out / "demo" / "data" / "1d.json").read_text())
        times = [b["time"] for b in packet["bars"]]
        assert times == sorted(times) and len(set(times)) == len(times)
        assert packet["interval"] == "1d" and packet["eur_rate"] == 0.86
        assert packet["demo"] is False, "la démo ne se dit pas démo : elle est datée"
        assert packet["overlays"]["ma200"], "la MA 200 existe dès la première page"
        assert packet["overlays"]["ma200"][0]["time"] == times[0], \
            "marge de calcul tronquée : la MA 200 est juste dès la première bougie"
    print("  ✓ paquets, assets, page et .nojekyll écrits ; intervalle vide écarté")


HARNESS = r"""
const fs = require("fs");
const packet = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
globalThis.window = undefined;
globalThis.fetch = async (url) => ({ok: true, json: async () => packet});
require(process.argv[3]);
(async () => {
  const j = async (u) => (await fetch(u)).json();
  const p1 = await j("/api/klines?interval=1d&limit=100");
  const p2 = await j("/api/klines?interval=1d&limit=100&before=" + p1.bars[0].time);
  const p3 = await j("/api/klines?interval=1d&limit=100&before=" + packet.bars[0].time);
  const from = packet.bars[50].time, to = packet.bars[120].time;
  const prof = await j("/api/profile?interval=1d&from=" + from + "&to=" + to);
  const vide = await j("/api/profile?interval=1d&from=1&to=2");
  console.log(JSON.stringify({p1, p2, p3, prof, vide}));
})();
"""


def test_le_shim_pagine_et_profile_comme_le_serveur():
    if not shutil.which("node"):
        print("  – Node absent : contrôle du shim ignoré")
        return
    with tempfile.TemporaryDirectory() as tmp:
        construire(tmp)
        data = Path(tmp) / "demo" / "data" / "1d.json"
        harness = Path(tmp) / "harness.js"
        harness.write_text(HARNESS)
        done = subprocess.run(["node", str(harness), str(data), str(SHIM)],
                              capture_output=True, text=True, timeout=30)
        assert done.returncode == 0, done.stderr
        out = json.loads(done.stdout)
        packet = json.loads(data.read_text())

        p1, p2, p3 = out["p1"], out["p2"], out["p3"]
        assert len(p1["bars"]) == 100 and p1["bars"][-1] == packet["bars"][-1]
        assert p1["interval"] == "1d" and p1["eur_rate"] == 0.86 and p1["demo"] is False
        assert len(p2["bars"]) == 100 and p2["bars"][-1]["time"] < p1["bars"][0]["time"]
        assert p2["bars"] == packet["bars"][100:200], "la page antérieure est contiguë"
        lo, hi = p2["bars"][0]["time"], p2["bars"][-1]["time"]
        for name, points in list(p2["overlays"].items()) + list(p2["panes"].items()):
            assert all(lo <= pt["time"] <= hi for pt in points), name
        assert [v["time"] for v in p2["volume"]] == [b["time"] for b in p2["bars"]]
        assert p3["bars"] == [], "avant la première bougie : page vide, le client s'arrête"

        # Profil : le même calcul que le serveur, sur la même tranche,
        # reconstruite depuis le paquet lui-même.
        bars = packet["bars"][50:121]
        vol = {v["time"]: v["value"] for v in packet["volume"]}
        part = pd.DataFrame({"low": [b["low"] for b in bars],
                             "high": [b["high"] for b in bars],
                             "volume": [vol[b["time"]] for b in bars]})
        centers, volumes, poc, va_low, va_high = ind.volume_profile(part, VOL_BINS)
        prof = out["prof"]
        assert prof["interval"] == "1d" and len(prof["centers"]) == VOL_BINS
        for a, b in zip(prof["centers"], centers):
            assert abs(a - b) < 1e-6 * max(1, abs(b)), (a, b)
        for a, b in zip(prof["volumes"], volumes):
            assert abs(a - b) < 1e-6 * max(1, abs(b)), (a, b)
        assert abs(prof["poc"] - poc) < 1e-6 * poc
        assert abs(prof["va_low"] - va_low) < 1e-6 * va_low
        assert abs(prof["va_high"] - va_high) < 1e-6 * va_high
        assert out["vide"]["empty"] is True and out["vide"]["interval"] == "1d"
    print("  ✓ pagination et profil du shim identiques au serveur")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nDémo statique — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("La démo dit la même chose que le serveur.\n")
