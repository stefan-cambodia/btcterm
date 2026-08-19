# 🪙 BTC Terminal

**Objectif du projet : construire une sorte de terminal Bloomberg orienté
Bitcoin** — un poste de travail unique regroupant, sur des panneaux
synchronisés, tout ce qu'il faut pour lire le marché : prix et indicateurs
techniques, carnets d'ordres et profondeur multi-exchange, opportunités
d'arbitrage, flux des ETF spot, news à impact et sentiment de marché.

**État actuel : briques séparées.** Le dépôt contient aujourd'hui les modules
qui alimenteront ces panneaux, mais chacun est encore un script **autonome** :
ni package installable, ni point d'entrée unique. On lance directement le
fichier voulu. La feuille de route vers l'interface unifiée est décrite dans
[`ARCHITECTURE.md`](ARCHITECTURE.md#5-feuille-de-route-vers-le-terminal).

| Panneau visé | Brique existante |
|---|---|
| Prix, chandeliers, indicateurs, signaux | `btc-dash.py`, `btc_dashboard2.py` |
| Carnet d'ordres et liquidité | `btc-liquidity.py` |
| Profondeur comparée multi-exchange | `btc_orderbook_live.py` |
| Écarts inter-exchange / arbitrage | `arbitrage/main.py` |
| Flux institutionnels (ETF spot) | `etf_bitcoin_flows.py` |
| Fil de news et Fear & Greed | `news/btc_news.py` |
| Contexte macro (masse monétaire M2) | `m2supply.html` ⚠️ incomplet |

> Données de marché : APIs publiques (Binance, Kraken, Coinbase, Bybit, OKX) —
> **aucune clé API n'est requise**, aucun ordre n'est jamais passé.

---

## Table des outils

| Outil | Type | Sources | Lancement |
|---|---|---|---|
| `btc-dash.py` | Dashboard web (Dash) | Binance REST + FX | `python btc-dash.py` → http://127.0.0.1:8050 |
| `btc_dashboard2.py` | Dashboard web (Dash + ccxt) | Binance via ccxt | `python btc_dashboard2.py` → http://127.0.0.1:8050 |
| `btc-liquidity.py` | Fenêtre matplotlib | Binance REST (depth + ticker) | `python btc-liquidity.py` |
| `btc_orderbook_live.py` | Fenêtre matplotlib | WebSockets Binance / Coinbase / Kraken | `python btc_orderbook_live.py` |
| `arbitrage/main.py` | TUI terminal (Rich) | WebSockets 5 exchanges | `python arbitrage/main.py` |
| `etf_bitcoin_flows.py` | CLI | farside.co.uk (scraping) | `python etf_bitcoin_flows.py --days 90` |
| `etf.py` | CLI (version antérieure) | farside.co.uk (scraping) | `python etf.py --days 15` |
| `news/btc_news.py` | CLI + SQLite | RSS, CryptoPanic, Fear & Greed | `python news/btc_news.py fetch` |
| `m2supply.html` | Page statique Plotly | — | ⚠️ fichier incomplet, voir plus bas |

Voir [`ARCHITECTURE.md`](ARCHITECTURE.md) pour le détail interne de chaque module.

---

## Installation

Le venv présent à la racine (`venv/`, Python 3.14) contient les dépendances
« lourdes » communes (`pandas`, `numpy`, `matplotlib`, `requests`, `lxml`,
`beautifulsoup4`, `tabulate`, `websockets`) mais **pas** `dash`, `plotly`,
`ccxt`, `rich` ni `feedparser`.

```fish
# Activer le venv existant (fish)
source venv/bin/activate.fish

# Dépendances par outil (à compléter selon ce que vous lancez)
pip install dash dash-bootstrap-components plotly          # btc-dash.py
pip install dash dash-bootstrap-components plotly ccxt     # btc_dashboard2.py
pip install matplotlib requests                            # btc-liquidity.py
pip install websockets matplotlib                          # btc_orderbook_live.py
pip install requests pandas lxml beautifulsoup4 tabulate   # etf*.py
```

Les sous-projets `arbitrage/` et `news/` ont leur propre `requirements.txt` :

```bash
pip install -r arbitrage/requirements.txt   # websockets, rich
pip install -r news/requirements.txt        # feedparser, requests
```

`news/setup.fish` crée un venv dédié (`news/.venv`), installe les dépendances et
pose une fonction fish `btcnews` dans `~/.config/fish/functions/`.

---

## Les outils en détail

### 1. `btc-dash.py` — BTC Ultra Dashboard

Dashboard Dash « TradingView-like » sur fond sombre, rafraîchi toutes les
**10 secondes** depuis l'API REST publique de Binance.

- Intervalles : `1H`, `4H`, `1D`, `1W`
- Indicateurs : MA 9 / 26 / 200, Bollinger (20, 2σ), RSI 14, Connors RSI,
  ATR 14, volatilité annualisée 252 j, MA de volume 20
- **Volume Profile** : POC (Point of Control) + Value Area 70 %
- Signaux gradués de `-2` (Strong Sell) à `+2` (Strong Buy), croisant
  MA 9/26, position vs MA 200 et sorties de zones RSI 30/70
- Bascule d'affichage **USD / EUR** (taux via `exchangerate-api.com`,
  repli sur `0.924` si l'appel échoue)

⚠️ Écoute sur `0.0.0.0:8050` : le dashboard est exposé sur tout le réseau
local. Passer à `127.0.0.1` si ce n'est pas voulu.

### 2. `btc_dashboard2.py` — BTC Dashboard (Ultimate Monitor)

Variante bâtie sur **ccxt** plutôt que sur des appels REST bruts, avec une
palette de timeframes beaucoup plus large (`1s`, `15m`, `30m`, `1h`, `6h`,
`12h`, `1d`, `1w`, `1M`, `1y`, `All`).

- Chart en 4 rangées : chandeliers / RSI + CRSI / volatilité / volume
- Overlays activables : échelle log, signaux, Bollinger, MA 50 + MA 200
- MA 9 et 26 en **EMA** (contrairement à `btc-dash.py` qui utilise des SMA)
- Bouton « Save PNG » (callback clientside), dossier `~/btc_charts`
- **Repli automatique sur des données de démo** générées localement si
  l'appel à l'exchange échoue — utile hors ligne, mais les chiffres
  affichés ne sont alors plus réels

### 3. `btc-liquidity.py` — Moniteur de pools de liquidité

Fenêtre matplotlib en 4 panneaux, rafraîchie toutes les **3 secondes** :
courbe de prix (60 derniers points), barres des 10 meilleurs bids, barres des
10 meilleurs asks, et un panneau de métriques (spread absolu et %, ratio
bid/ask avec qualification « pression acheteuse / vendeuse / équilibré »,
volume 24 h, tendance sur les 10 derniers points).

### 4. `btc_orderbook_live.py` — Carnet d'ordres multi-exchange

Depth charts cumulés (bids en vert, asks en rouge) pour **Binance, Coinbase et
Kraken** côte à côte, alimentés par WebSockets. Gère les deux modèles de flux :
snapshot complet répété (Binance) et snapshot initial + deltas
(Coinbase `l2update`, Kraken `a`/`b`). Reconnexion automatique après 3 s.

Le carnet est tronqué à **100 niveaux** de chaque côté, sinon le snapshot
complet de Coinbase (plusieurs milliers de lignes) sature mémoire et CPU.

### 5. `arbitrage/main.py` — Moniteur d'arbitrage temps réel

TUI Rich plein écran surveillant **5 exchanges** (Binance, Kraken, Bybit, OKX,
Coinbase) et scannant toutes les paires ordonnées 5 fois par seconde :

```
profit_brut = (best_bid_vente - best_ask_achat) / best_ask_achat * 100
profit_net  = profit_brut - frais_achat - frais_vente
```

Une opportunité est retenue au-delà de `MIN_PROFIT_PCT = 0.1 %`, et les
carnets de plus de **5 s** sont ignorés. Voir `arbitrage/README.md` pour la
grille de frais et les avertissements — c'est un outil d'observation, pas un
bot d'exécution.

### 6. `etf_bitcoin_flows.py` et `etf.py` — Flux des ETF Bitcoin spot

Récupèrent le tableau public de `farside.co.uk/btc/` (flux quotidiens IBIT,
FBTC, GBTC, ARKB, BITB, HODL…) et affichent les N derniers jours en millions
de dollars, plus le flux net cumulé et le décompte des jours entrants/sortants.

```bash
python etf_bitcoin_flows.py                 # 90 derniers jours
python etf_bitcoin_flows.py --days 0        # tout l'historique
python etf_bitcoin_flows.py --csv flows.csv # export CSV complet
```

**`etf_bitcoin_flows.py` est la version à utiliser.** `etf.py` est la mouture
antérieure du même script : elle ne gère pas les en-têtes multi-niveaux du
site, n'élague pas les colonnes vides, passe encore le HTML directement à
`pd.read_html()` (déprécié) et affiche via `DataFrame.to_string` au lieu de
`tabulate`.

### 7. `news/btc_news.py` — BTC News Tracker

CLI d'agrégation de news à impact sur le cours, stockées dans SQLite
(`~/.btc_news/news.db`). Sources : 6 flux RSS (CoinDesk, CoinTelegraph,
Decrypt, Bitcoin Magazine, The Block, CryptoSlate), l'API CryptoPanic
(optionnelle, clé gratuite) et le Fear & Greed Index.

Chaque article reçoit un **score 0-100** par pondération de mots-clés
(régulation, ETF, Fed, halving, hack, ATH…) ; sous `MIN_SCORE = 30`, il est
écarté. Un sentiment `bullish` / `bearish` / `neutral` est déduit du
vocabulaire (ou des votes CryptoPanic quand ils existent).

```bash
btcnews fetch -v                 # récupérer
btcnews list --min-score 60      # seulement les news importantes
btcnews list --sentiment bearish
btcnews unread                   # non lues, puis marquées lues
btcnews search "etf"
btcnews stats
btcnews watch --interval 30      # boucle de surveillance
```

`news/systemd_timer.conf` contient (en commentaires, à décommenter et adapter)
les unités systemd `--user` pour un `fetch` automatique toutes les 30 minutes.

### 8. `m2supply.html` — ⚠️ fichier incomplet

Page Plotly censée superposer le cours du BTC et la masse monétaire M2
normalisés. **En l'état le fichier est tronqué** : il ne contient que la fin du
script (fin de `loadData`, layout, `Plotly.newPlot`, `setInterval` de 5 min).
Il manque le `<head>`, le chargement de la bibliothèque Plotly, le `<div
id="chart">` et tout le début de `loadData` — donc la récupération des données
BTC et M2. Ouvert tel quel dans un navigateur, il ne produit rien.

---

## Notes

- `order/` est un répertoire vide.
- Aucun de ces scripts n'écrit d'ordre sur un exchange ; ils sont en lecture
  seule sur des endpoints publics.
- Les deux dashboards Dash utilisent le même port `8050` : ne pas les lancer
  simultanément sans changer le port de l'un des deux.
- Le répertoire n'est pas un dépôt git.
