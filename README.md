# 🪙 BTC Terminal

**Objectif du projet : construire une sorte de terminal Bloomberg orienté
Bitcoin** — un poste de travail unique regroupant, sur des panneaux
synchronisés, tout ce qu'il faut pour lire le marché : prix et indicateurs
techniques, carnets d'ordres et profondeur multi-exchange, opportunités
d'arbitrage, flux des ETF spot, news à impact et sentiment de marché.

## Lancement

```bash
python -m terminal.app
```

→ **http://127.0.0.1:8050**

À distance, par tunnel SSH (le port n'est pas exposé sur le réseau) :

```bash
ssh -L 8050:localhost:8050 <machine>
```

## Les panneaux

| Panneau | Contenu | Rafraîchissement |
|---|---|---|
| **Prix** | chandeliers, MA 9/26/200, Bollinger, POC + Value Area, RSI, CRSI, volume, signaux, bascule USD/EUR | 2 s |
| **Carnet** | 12 niveaux de chaque côté, spread, âge du flux, choix de la plateforme | 250 ms |
| **Profondeur** | profondeur cumulée des 5 plateformes superposées, recentrées en % du prix médian | 250 ms |
| **Arbitrage** | écarts inter-plateformes nets de frais, triés par rentabilité | 250 ms |
| **Flux ETF** | entrées/sorties nettes des ETF spot sur 30 jours | 5 min |
| **News** | fil scoré + indice Fear & Greed | 5 min |

Le graphique conserve zoom et pan pendant que les données coulent — c'est ce qui
permet d'analyser une zone sans être recadré à chaque tour d'horloge.

Le panneau news **lit** la base alimentée par `news/btc_news.py` ; il ne la
remplit pas. Pour l'alimenter : `python news/btc_news.py fetch`, ou le timer
systemd fourni.

## Architecture

- **`btcterm/`** — le socle : indicateurs, carnets et connecteurs WebSocket,
  moteur d'arbitrage, collecteurs, et le hub qui n'ouvre qu'une connexion par
  plateforme pour tous les panneaux.
- **`terminal/`** — l'application Dash : grille, thème, figures, panneaux.

Détail complet dans [`ARCHITECTURE.md`](ARCHITECTURE.md), feuille de route en
[§7](ARCHITECTURE.md#7-feuille-de-route-vers-le-terminal).

## Outils hérités

Les scripts d'origine restent lançables le temps de la transition ; ils
partagent le socle mais gardent chacun leur fenêtre. Le terminal couvre déjà
`btc-dash.py`, `btc-liquidity.py` et `btc_orderbook_live.py`.

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

```fish
# Activer le venv existant (fish)
source venv/bin/activate.fish

# Toutes les dépendances du dépôt
pip install -r requirements.txt
```

Le venv présent à la racine (`venv/`, Python 3.14) contient déjà toutes ces
dépendances.

`requirements.txt` est groupé par usage : pour n'installer qu'une partie, il
suffit de reprendre le bloc concerné. Le socle `btcterm/` ne demande que
`pandas`, `numpy` et `requests`.

Aucune installation de paquet n'est nécessaire : les scripts trouvent
`btcterm/` par eux-mêmes, où que soit le répertoire courant.

`arbitrage/requirements.txt` et `news/requirements.txt` restent disponibles
pour installer un seul sous-projet, et `news/setup.fish` crée un venv dédié
plus une fonction fish `btcnews`.

### Tests

```bash
python tests/test_indicators_parity.py   # indicateurs identiques à l'origine
python tests/test_terminal_wiring.py     # panneaux posés et branchés
```

Le premier vérifie que les indicateurs du socle produisent exactement les mêmes
valeurs que les implémentations d'avant l'extraction. Le second qu'aucun panneau
n'a été écrit puis oublié — ni dans la grille, ni dans l'enregistrement des
callbacks. Aucun des deux ne touche au réseau.

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

Le Connors RSI affiché a changé de valeur en phase 1 : ce panneau utilisait une
troisième composante non conforme à la définition de Connors, qui dépendait du
nombre de bougies chargées. Voir `ARCHITECTURE.md` §2.1.

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
carnets de plus de **5 s** sont ignorés. Le carnet Bybit était figé sur son
premier snapshot tout en paraissant frais ; corrigé en phase 1. Voir `arbitrage/README.md` pour la
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
- Le dépôt est versionné avec git (branche `main`, pas de remote).
