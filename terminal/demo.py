"""
Démo statique du panneau prix — pour GitHub Pages.

GitHub ne fait tourner aucun serveur : une démo « vivante » y est
impossible, mais le panneau prix, lui, se dessine entièrement dans le
navigateur (assets/lwc-price.js) à partir du JSON de `/api/klines`. Ce
module fige ce JSON — un paquet par intervalle, un millier de bougies
chacun — et pose autour la page qui le sert sans serveur : `fetch` y
est détourné (docs/demo/shim.js) vers les paquets figés, paginés par
`time` comme le ferait terminal/lwc.py, et le profil de volume est
recalculé dans le navigateur. Zoom, crosshair, pan vers le passé,
bascule $/€, échelle log, panes : tout ce que fait le vrai panneau,
sur des données datées et annoncées comme telles.

    python -m terminal.demo docs        # écrit docs/index.html et docs/demo/

Le rendu et sa bibliothèque sont **copiés** depuis terminal/assets/ au
moment de la construction : GitHub Pages ne sert que `docs/`, et la
démo doit se suffire. Les regénérer après chaque changement de
lwc-price.js tient à cette commande.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .charts import prepare_price_frame
from .lwc import serialize_price_frame
from .panels.price import DEFAULT_EXTRAS, DEFAULT_INTERVAL, INTERVALS
from .theme import C, MONO

__all__ = ["build", "BARS_PER_INTERVAL", "ASSETS"]

#: Bougies figées par intervalle : Binance sert 1 000 bougies par page,
#: et lwc-price.js en tient jusqu'à 5 000 — mille suffisent à plusieurs
#: écrans de pan, sans faire du dépôt un entrepôt de données.
BARS_PER_INTERVAL = 1000

#: Marge de calcul en amont, tronquée après enrichissement : la MA 200
#: est juste dès la première bougie servie (même règle que lwc.py).
WARMUP_BARS = 200

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

#: Fichiers copiés de terminal/assets/ vers docs/demo/.
ASSETS = {
    "vendor/lightweight-charts.standalone.production.js":
        "vendor/lightweight-charts.standalone.production.js",
    "lwc-price.js": "lwc-price.js",
    "terminal.css": "terminal.css",
}

PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BTC Terminal — démo statique du panneau prix</title>
<link rel="stylesheet" href="demo/terminal.css">
<style>
  html, body { height: 100%; margin: 0; }
  body { background: __BG__; color: __TEXT__; font-family: __MONO__;
         display: flex; flex-direction: column; }
  a { color: __CYAN__; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .demo-header { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
                 padding: 8px 14px; border-bottom: 1px solid __BORDER__;
                 font-size: 11px; color: __MUTED__; flex-shrink: 0; }
  .demo-brand { color: __YELLOW__; font-weight: 700; letter-spacing: 0.08em;
                font-size: 13px; }
  .demo-note { color: __ORANGE__; }
  .demo-main { flex: 1; min-height: 0; padding: 8px; display: flex; }
  .demo-panel { flex: 1; min-height: 0; background: __PANEL__;
                border: 1px solid __BORDER__; border-radius: 6px;
                padding: 8px 10px; display: flex; flex-direction: column;
                overflow: hidden; }
  .demo-title { display: flex; justify-content: space-between; align-items: center;
                flex-shrink: 0; margin-bottom: 6px; color: __MUTED__;
                font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase;
                white-space: nowrap; overflow-x: auto; }
  .demo-title .tf-radio, .demo-title .tf-check { display: inline-block;
                font-size: 9px; margin-left: 10px; }
  .demo-title .tf-radio:first-child { margin-left: 0; }
  #price-lwc { flex: 1; min-height: 0; position: relative; }
  .demo-footer { padding: 6px 14px; border-top: 1px solid __BORDER__;
                 font-size: 10px; color: __MUTED__; flex-shrink: 0; }
</style>
</head>
<body>
<div class="demo-header">
  <span class="demo-brand">₿ BTC TERMINAL</span>
  <span>démo statique du panneau prix</span>
  <span class="demo-note">instantané figé du __DATE__ · aucune donnée en direct</span>
  <span style="margin-left:auto"><a href="https://github.com/__REPO__">le dépôt sur GitHub</a></span>
</div>
<div class="demo-main">
  <div class="demo-panel">
    <div class="demo-title">
      <span style="font-size:9px;letter-spacing:0.02em">BTC/USDT</span>
      <div style="display:flex;align-items:center;white-space:nowrap">
        <div id="price-interval" class="tf-radio"></div>
        <div id="price-currency" class="tf-radio"></div>
        <div id="price-scale" class="tf-check"></div>
        <div id="price-extras" class="tf-check"></div>
      </div>
    </div>
    <div id="price-lwc"></div>
  </div>
</div>
<div class="demo-footer">
  Le vrai terminal sert ces données en temps réel depuis cinq plateformes,
  avec treize autres panneaux — carnet, profondeur, arbitrage, liquidations,
  ETF, perpétuel, news, calendrier, alertes, journal, macro, dominance,
  chaîne. Ici, seul le panneau prix, sur __BARS__ bougies par intervalle
  figées le __DATE__ : zoom à la molette, pan à la souris (l'historique se
  charge en glissant vers le passé), crosshair, $/€, échelle log, panes.
</div>
<script src="demo/vendor/lightweight-charts.standalone.production.js"></script>
<script>window.DEMO_CONF = __CONF__;</script>
<script src="demo/shim.js"></script>
<script src="demo/lwc-price.js"></script>
<script src="demo/page.js"></script>
</body>
</html>
"""


def freeze_packet(hub, interval: str, bars: int = BARS_PER_INTERVAL,
                  now_ms: int | None = None) -> dict | None:
    """Le paquet `/api/klines` d'un intervalle, figé : les `bars`
    dernières bougies, enrichies après une marge de calcul tronquée.
    `None` si la source n'a rien rendu."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    df = hub.klines_history(interval, now_ms, bars + WARMUP_BARS)
    if df is None or df.empty:
        return None
    prepared = prepare_price_frame(df).iloc[-bars:]
    packet = serialize_price_frame(prepared)
    packet["interval"] = interval
    packet["eur_rate"] = float(hub.eur_rate())
    packet["demo"] = False
    return packet


def build(out: Path, hub, bars: int = BARS_PER_INTERVAL,
          repo: str = "stefan-cambodia/btcterm",
          now: float | None = None, log=print) -> dict:
    """Écrit la démo dans `out` : index.html, demo/ (assets, data/).

    Rend le résumé — intervalles figés, fichiers écrits — pour les
    tests et le compte rendu. `hub` n'a besoin que de `klines_history`
    et `eur_rate` : il n'est jamais démarré.
    """
    out = Path(out)
    demo = out / "demo"
    data = demo / "data"
    data.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now

    for src, dst in ASSETS.items():
        target = demo / dst
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ASSETS_DIR / src, target)

    frozen: dict[str, int] = {}
    for interval, _ in INTERVALS.items():
        packet = freeze_packet(hub, interval, bars, int(now * 1000))
        if packet is None or not packet["bars"]:
            log(f"  – {interval:>3s} : rien reçu, intervalle écarté")
            continue
        (data / f"{interval}.json").write_text(
            json.dumps(packet, separators=(",", ":")), encoding="utf-8")
        frozen[interval] = len(packet["bars"])
        log(f"  ✓ {interval:>3s} : {len(packet['bars'])} bougies")

    conf = {
        "theme": C, "mono": MONO,
        # Les profondeurs de page du vrai panneau : le premier
        # chargement montre la même fenêtre que le terminal.
        "intervals": {k: v for k, v in INTERVALS.items() if k in frozen},
        "default_interval": DEFAULT_INTERVAL if DEFAULT_INTERVAL in frozen
        else next(iter(frozen), "1d"),
        "default_extras": DEFAULT_EXTRAS,
        "frozen_at": now,
    }
    date = datetime.fromtimestamp(now, timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    page = PAGE
    for key, value in {
        "__BG__": C["bg"], "__TEXT__": C["text"], "__MONO__": MONO,
        "__CYAN__": C["cyan"], "__BORDER__": C["border"], "__MUTED__": C["muted"],
        "__YELLOW__": C["yellow"], "__ORANGE__": C["orange"], "__PANEL__": C["panel"],
        "__DATE__": date, "__REPO__": repo, "__BARS__": str(bars),
        "__CONF__": json.dumps(conf, ensure_ascii=False),
    }.items():
        page = page.replace(key, value)
    (out / "index.html").write_text(page, encoding="utf-8")
    # GitHub Pages passe par Jekyll par défaut : ce fichier l'en dispense.
    (out / ".nojekyll").touch()
    return {"intervals": frozen, "index": out / "index.html", "date": date}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0]) if argv else Path("docs")
    from btcterm import resolver
    from btcterm.hub import MarketHub
    resolver.install()
    hub = MarketHub(collect_news=False, keep_journal=False)
    print(f"\nDémo statique → {out}\n" + "─" * 60)
    summary = build(out, hub)
    print("─" * 60)
    print(f"{len(summary['intervals'])} intervalles figés le {summary['date']} ; "
          f"ouvrir {summary['index']}\n")
    return 0 if summary["intervals"] else 1


if __name__ == "__main__":
    sys.exit(main())
