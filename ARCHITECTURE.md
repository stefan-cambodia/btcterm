# Architecture

Ce document décrit l'organisation interne du dépôt : structure, patrons
récurrents, flux de données et détail module par module.

---

## 1. Vue d'ensemble

### Cible : un terminal Bloomberg orienté Bitcoin

Le projet vise un **poste de travail unifié** — un terminal à la Bloomberg,
centré sur le Bitcoin — où chaque famille d'information occupe un panneau d'une
même interface : prix et indicateurs, carnet d'ordres, profondeur comparée entre
exchanges, écarts d'arbitrage, flux des ETF spot, fil de news et sentiment,
contexte macro.

### État actuel : les briques, pas encore le terminal

Le dépôt n'est aujourd'hui **pas** une application unique mais une collection de
scripts indépendants partageant un thème (le Bitcoin) et un principe : consommer
des APIs publiques en lecture seule et restituer l'information visuellement.
Chaque script correspond à un futur panneau, mais porte encore sa propre boucle
d'événements, son propre thème graphique et sa propre copie des utilitaires
communs.

L'écart entre l'état actuel et la cible tient en trois points, détaillés en
[§5](#5-feuille-de-route-vers-le-terminal) : **une seule couche de rendu** au
lieu de quatre, **une seule couche de données** partagée entre panneaux, et
**un socle commun** (indicateurs, connecteurs) au lieu d'implémentations
dupliquées.

```
/home/stefan/python/btc
├── README.md                  ← guide d'utilisation
├── ARCHITECTURE.md            ← ce fichier
│
├── btc-dash.py                ← dashboard web  (Dash, REST Binance)
├── btc_dashboard2.py          ← dashboard web  (Dash, ccxt)
├── btc-liquidity.py           ← fenêtre GUI    (matplotlib, REST polling)
├── btc_orderbook_live.py      ← fenêtre GUI    (matplotlib, WebSockets ×3)
├── etf.py                     ← CLI            (scraping farside — v1)
├── etf_bitcoin_flows.py       ← CLI            (scraping farside — v2)
├── m2supply.html              ← page statique  (incomplète)
│
├── arbitrage/                 ← sous-projet autonome
│   ├── main.py                    TUI Rich, WebSockets ×5
│   ├── requirements.txt
│   └── README.md
│
├── news/                      ← sous-projet autonome
│   ├── btc_news.py                CLI + SQLite
│   ├── requirements.txt
│   ├── setup.fish                 installe venv + fonction fish `btcnews`
│   └── systemd_timer.conf         gabarit de timer systemd --user
│
├── order/                     ← vide
└── venv/                      ← venv Python 3.14 partagé (racine)
```

### Quatre familles d'interface

| Famille | Scripts | Boucle d'affichage |
|---|---|---|
| Web (Dash/Plotly) | `btc-dash.py`, `btc_dashboard2.py` | `dcc.Interval` → callbacks serveur |
| GUI desktop (matplotlib) | `btc-liquidity.py`, `btc_orderbook_live.py` | `FuncAnimation` |
| TUI (Rich) | `arbitrage/main.py` | `rich.live.Live` piloté par asyncio |
| CLI batch | `etf*.py`, `news/btc_news.py` | one-shot (ou `watch` en boucle) |

---

## 2. Patrons transverses

### 2.1 Deux modèles d'acquisition de données

**Polling REST** (`btc-dash.py`, `btc-liquidity.py`, `etf*.py`) — appel HTTP
synchrone, `try/except` large, valeur de repli en cas d'échec. Simple, latence
de l'ordre de la seconde.

**Streaming WebSocket** (`btc_orderbook_live.py`, `arbitrage/main.py`) — une
coroutine par exchange, boucle infinie de reconnexion, état mutable partagé.
Latence de l'ordre de 100 ms.

### 2.2 Séparation producteur / consommateur

Les scripts temps réel séparent systématiquement l'acquisition (réseau) du
rendu (GUI), car les toolkits graphiques ne sont pas thread-safe :

```
btc-liquidity.py        thread daemon → deque + threading.Lock  → FuncAnimation
btc_orderbook_live.py   thread daemon (event loop asyncio)
                        → dict BOOKS + threading.Lock           → FuncAnimation
arbitrage/main.py       tâches asyncio → dict[str, OrderBook]   → Live (asyncio)
btc-dash.py / dash2     (pas de thread : fetch synchrone dans le callback)
```

Les deux scripts matplotlib prennent le verrou uniquement pour **copier** un
instantané de l'état, puis dessinent hors verrou.

### 2.3 Dégradation contrôlée

Chaque source distante a une stratégie de repli explicite :

| Source | Repli |
|---|---|
| ccxt / Binance (`btc_dashboard2.py`) | données de démo générées localement |
| taux EUR (`btc-dash.py`) | constante `0.924` |
| ticker 24 h (`btc-dash.py`) | dict vide, les cartes affichent `—` |
| WebSocket (tous) | reconnexion (3 s fixes, ou backoff exponentiel plafonné à 30 s) |
| flux RSS (`news`) | le feed en échec est sauté, les autres continuent |
| CryptoPanic sans clé | source simplement désactivée |

### 2.4 Palettes de couleurs

Tous les scripts sont en thème sombre, avec une constante de palette en tête de
fichier (`C` dans `btc-dash.py`, constantes `DARK_BG`/`GREEN`/… dans
`btc_dashboard2.py`, `BID_COLOR`/`ASK_COLOR`/… dans `btc_orderbook_live.py`).
Convention constante : **vert = achat/hausse, rouge = vente/baisse**.

---

## 3. Détail des modules

### 3.1 `btc-dash.py` — BTC Ultra Dashboard

Pipeline linéaire, refait à chaque tick de 10 s :

```
fetch_klines(interval)      GET /api/v3/klines        → DataFrame OHLCV
        ↓
compute_indicators(df)      MA/BB/RSI/CRSI/ATR/vol    → colonnes dérivées
        ↓  _signals(df)                                → colonne `signal` ∈ [-2..2]
volume_profile(df, bins=60)                            → centers, vols, POC, VA
        ↓
build_chart(df, currency, eur_rate)                    → go.Figure
        ↓
refresh_all(...)            callback Dash             → figure + 8 cartes de stats
```

Découpage des fonctions :

- **Réseau** : `fetch_klines`, `fetch_ticker`, `fetch_eur_rate`
- **Indicateurs** : `rsi`, `streak`, `atr`, `compute_indicators`, `_signals`
- **Analyse** : `volume_profile` — répartit le volume de chaque bougie sur les
  bins couverts par son range `low`→`high`, puis retient le bin dominant (POC)
  et l'ensemble minimal de bins cumulant 70 % du volume (Value Area)
- **Rendu** : `build_chart`, `stat_card`, layout Dash
- **Callbacks** : `refresh_eur` (taux mis en `dcc.Store`), `refresh_all`
  (figure + cartes), déclenchés par `Input("tick", "n_intervals")`

Le taux EUR transite par un `dcc.Store` pour éviter un appel FX à chaque
recalcul de graphique.

### 3.2 `btc_dashboard2.py` — BTC Dashboard (ccxt)

Même découpage général, trois différences structurantes :

1. **Abstraction exchange** : `ccxt.binance()` au lieu d'appels REST manuels,
   ce qui rend le changement d'exchange trivial (`EXCHANGE` en tête de fichier).
2. **`TIMEFRAME_MAP`** associe à chaque libellé d'UI un couple
   `(timeframe ccxt, limit)` — c'est la table à éditer pour ajouter un
   intervalle. Les libellés `1y` et `All` sont des alias re-mappés sur `1d`/`1w`.
3. **`generate_demo_data(limit)`** produit une marche aléatoire OHLCV complète
   avec les mêmes colonnes d'indicateurs, de sorte que tout l'aval du pipeline
   fonctionne à l'identique hors ligne.

Chaîne de callbacks (deux étages, pour découpler les 11 boutons de timeframe
du rendu) :

```
11 boutons  tf-*  ──(callback_context)──▶  dcc.Store "store-tf"
                                                  │
  overlay-checks / btn-refresh / auto-refresh ────┼──▶ update_chart(...)
                                                  │       → figure + 7 stats + statut
                             btn-save ──▶ clientside_callback (export PNG navigateur)
```

`update_tf` identifie le bouton cliqué via `callback_context.triggered` — c'est
le seul moyen de multiplexer N boutons vers une sortie unique en Dash.

`build_figure` construit une figure `make_subplots` à 4 rangées
(`row_heights=[0.55, 0.15, 0.15, 0.15]`, axe X partagé) et prend les overlays
en paramètres booléens plutôt qu'en lisant l'état global — la fonction reste
testable isolément.

### 3.3 `btc-liquidity.py` — pools de liquidité

```
thread daemon : background_loop()
    └─ fetch_data()  GET /depth (10 niveaux) + GET /ticker/24hr
         └─ with lock: MAJ de price_history, time_history, bids_data,
                       asks_data, ticker_data
                                    │
thread principal : FuncAnimation(interval=3000)
    └─ animate(frame)
         ├─ with lock: copie de l'état
         ├─ ax_price : ligne + remplissage, couleur = tendance
         ├─ ax_bids / ax_asks : barh des 10 niveaux
         └─ ax_info : 7 métriques texte
```

- État partagé : `deque(maxlen=60)` pour l'historique (fenêtre glissante sans
  gestion de taille explicite) + listes mutables pour le carnet.
- `compute_trend(prices)` compare le dernier point au 10ᵉ point antérieur et
  retourne `(libellé, couleur, flèche)` — seuil ±0,05 %.
- Mise en page en `GridSpec(3, 3)` : le graphe de prix occupe les deux
  premières rangées sur toute la largeur, les trois panneaux se partagent la
  troisième.
- Un `fetch_data()` synchrone est appelé avant le démarrage du thread pour que
  la première frame ne soit pas vide.

### 3.4 `btc_orderbook_live.py` — carnet d'ordres multi-exchange

Le module le plus intéressant côté protocoles : il normalise trois formats de
flux différents vers une seule structure.

```
BOOKS = {exchange: {"bids": {prix: qté}, "asks": {prix: qté}}}   + LOCK
```

Deux primitives de mutation :

- `set_book(exchange, bids, asks)` — remplacement complet (snapshot)
- `apply_updates(exchange, bid_updates, ask_updates)` — application de deltas,
  une quantité `0` supprimant le niveau

Adaptation par exchange :

| Exchange | Canal | Modèle | Traduction |
|---|---|---|---|
| Binance | `btcusdt@depth20@100ms` | snapshot répété | `set_book` à chaque message |
| Coinbase | `level2_batch` | `snapshot` puis `l2update` | `set_book` puis `apply_updates` sur `changes` |
| Kraken | `book` depth 100 | `as`/`bs` puis `a`/`b` | `set_book` puis `apply_updates` |

Deux garde-fous nés de la réalité de ces flux, documentés dans le code :

- `MAX_LEVELS = 100` et `_trim()` — le snapshot Coinbase est un carnet
  **complet** (des milliers de niveaux) ; sans troncature autour du marché, la
  mémoire et le temps de rendu explosent.
- `MAX_WS_SIZE = 20 Mo` — ce même snapshot dépasse la limite de message par
  défaut de `websockets` (1 Mo), ce qui ferme la connexion.

Le rendu : `cumulative_depth(levels, descending)` transforme le dict en courbe
de profondeur cumulée, tracée en escalier (`drawstyle="steps-post"`) avec
remplissage, un panneau par exchange. L'event loop asyncio tourne dans un
thread daemon (`start_websocket_thread`) pendant que matplotlib garde le thread
principal.

### 3.5 `arbitrage/main.py` — moteur d'arbitrage

Architecture en couches, entièrement asyncio :

```
                  ┌─────────────────────────────────────────┐
   5 × Connector  │  dict[str, OrderBook]  (état partagé)    │
   (WebSocket)  ──▶│                                         │──▶ ArbitrageEngine.scan()
                  └─────────────────────────────────────────┘        (toutes les 0,2 s)
                                                                            │
                                                              Dashboard.render() → Rich Live
                                                                      (4 fois/s)
```

**Modèles (`@dataclass`)**

- `OrderBook` — bids/asks, `timestamp`, `connected`, `error`, plus des
  propriétés dérivées : `best_bid`, `best_ask`, `spread`, `spread_pct`,
  `age_ms`. Toute la logique de fraîcheur découle de `age_ms`.
- `ArbitrageOpportunity` — les deux exchanges, les prix, profit brut et net,
  frais, horodatage, et `is_profitable` (`net > MIN_PROFIT_PCT`).

**Connecteurs** — `ExchangeConnector` fournit `connect_with_retry(name,
coro_factory, max_retries=10)` : backoff exponentiel `min(2**n, 30)` s, remise
à zéro du compteur après une connexion réussie, et marquage de
`connected`/`error` sur le carnet concerné (l'UI affiche donc l'état réel de
chaque flux). Les cinq sous-classes n'implémentent que `_stream()`.

**Moteur** — `scan()` parcourt les paires **ordonnées** (acheter sur A, vendre
sur B ≠ acheter sur B, vendre sur A) et écarte : exchanges déconnectés,
carnets sans meilleur prix, carnets de plus de 5 s, et paires où
`sell_price <= buy_price`. Le profit net retranche les frais des deux côtés.
L'historique est borné à 100 opportunités rentables (pop en tête).

**Vue** — `Dashboard` compose un `rich.layout.Layout` : en-tête, une table par
carnet, la table des opportunités triées et un panneau d'historique. Aucune
logique métier dans cette couche.

**Orchestration** — `main()` lance 7 tâches concourantes (5 connecteurs +
`scan_loop` + `display_loop`) via `asyncio.gather`, avec arrêt propre sur
`KeyboardInterrupt` (`stop()` sur chaque connecteur puis `cancel()`).

### 3.6 `etf.py` / `etf_bitcoin_flows.py` — flux ETF

Pipeline en trois fonctions, identique dans les deux fichiers :

```
fetch_flows()  GET farside.co.uk/btc/ (User-Agent navigateur)
    ├─ pd.read_html → on retient la table la plus haute
    ├─ normalisation des en-têtes
    ├─ filtre des lignes par regex de date "^\d{1,2} \w{3} \d{4}$"
    │     (élimine les en-têtes répétés et les lignes vides)
    ├─ parse des dates "%d %b %Y", tri chronologique
    └─ nettoyage numérique : virgules, "US$m", parenthèses → négatif
          ↓
summarize(df, days)   → df.tail(days)
          ↓
main()   affichage + net cumulé + jours entrants/sortants + export CSV
```

La convention comptable « `(123)` = -123 » est traitée par un simple
remplacement `(` → `-` et suppression de `)`.

**Ce qui distingue la v2 (`etf_bitcoin_flows.py`)** :

| Point | `etf.py` | `etf_bitcoin_flows.py` |
|---|---|---|
| Entrée de `read_html` | chaîne HTML (déprécié) | `io.StringIO(...)` |
| En-têtes `MultiIndex` | non gérés | aplatis, `Unnamed*` ignorés |
| Valeurs manquantes | `NaN` conservés | `fillna(0.0)` |
| Colonnes vides | affichées | élaguées si leur somme en valeur absolue est nulle |
| Rendu | `DataFrame.to_string` | `tabulate` |
| `--days` par défaut | 15 | 90 (`0` = tout) |

`etf.py` est conservé comme version antérieure ; toute évolution doit se faire
sur la v2.

### 3.7 `news/btc_news.py` — BTC News Tracker

Application CLI à sous-commandes, la plus « structurée » du dépôt.

**Schéma SQLite** (`~/.btc_news/news.db`, créé par `init_db()`)

```sql
news(id TEXT PK, title, summary, url, source, published,
     fetched_at, score INT, keywords TEXT/*JSON*/, sentiment, read INT)
fear_greed(id INTEGER PK AUTOINCREMENT, value INT, label, fetched_at)
```

L'`id` est un `sha1(url + title)` : la déduplication repose donc sur la
contrainte de clé primaire, et `save_article()` retourne `False` en
interceptant `sqlite3.IntegrityError` — pas de `SELECT` préalable.

**Pipeline d'ingestion**

```
fetch_rss(conn)          6 flux via feedparser
fetch_cryptopanic(conn)  API (sautée si pas de clé) — bonus de score selon les votes
fetch_fear_greed(conn)   alternative.me → table fear_greed
        │
        ├─ score_article(title, summary)  → (score plafonné à 100, mots-clés trouvés)
        ├─ detect_sentiment(title, summary) → bullish / bearish / neutral
        └─ filtre score < MIN_SCORE (30), puis save_article()
```

`KEYWORDS` est un simple `dict[str, int]` — c'est le point d'extension
principal : le scoring est une somme de pondérations sur recherche de
sous-chaîne dans `titre + résumé` en minuscules. Les poids sont regroupés par
thème (macro/régulation 15-25, adoption 10-18, on-chain 8-22, événements
extrêmes 18-20, signaux de prix 8-20, termes génériques 5-10).

Pour CryptoPanic, le sentiment issu des votes prime sur la détection lexicale ;
`detect_sentiment` n'est utilisé qu'en repli.

**Couche CLI** — `argparse` avec sous-parseurs, puis un dict `dispatch`
associant nom de commande → `cmd_*(args, conn)` : `fetch`, `list`, `unread`,
`search`, `stats`, `watch`. Toutes les fonctions `cmd_*` reçoivent la même
signature, ce qui rend l'ajout d'une commande mécanique.

À noter : `list` et `unread` marquent les news comme lues en effet de bord
après affichage (sauf `list --unread-only`).

**Rendu terminal** — codes ANSI bruts (`SENTIMENT_COLOR`, `BOLD`, `RESET`),
barres de score par paliers (`SCORE_BARS`) et emoji par sentiment. Pas de
dépendance à une bibliothèque de rendu.

**Déploiement** — `setup.fish` crée `news/.venv`, installe
`requirements.txt`, `chmod +x` le script et génère
`~/.config/fish/functions/btcnews.fish` pointant sur le python du venv.
`systemd_timer.conf` fournit, en commentaires, les unités `.service` et
`.timer` utilisateur (`OnUnitActiveSec=30min`, `Persistent=true`).

### 3.8 `m2supply.html`

Page Plotly autonome censée superposer BTC et masse monétaire M2 normalisés
(deux axes Y, `rangeslider`, `hovermode: 'x unified'`, rafraîchissement par
`setInterval` de 300 000 ms).

**Le fichier est tronqué** : après `<!DOCTYPE html>` il reprend directement au
milieu de la définition de `traceM2`. Sont absents le `<head>`, le `<script>`
de chargement de Plotly, le conteneur `<div id="chart">`, la déclaration de
`loadData(range)` et toute la récupération des séries BTC et M2. Il faut
reconstituer ces parties avant de pouvoir l'utiliser ; en l'état c'est un
fragment, pas une page fonctionnelle.

---

## 4. Environnements Python

| Emplacement | Contenu | Utilisé par |
|---|---|---|
| `venv/` (racine, Python 3.14) | pandas, numpy, matplotlib, requests, lxml, beautifulsoup4, tabulate, websockets, pillow | `btc-liquidity.py`, `btc_orderbook_live.py`, `etf*.py` |
| `news/.venv` (créé par `setup.fish`) | feedparser, requests | `news/btc_news.py` |
| — | dash, dash-bootstrap-components, plotly, ccxt, rich | **non installés** : à ajouter pour les dashboards et l'arbitrage |

Il n'y a ni `requirements.txt` ni fichier de projet à la racine : les
dépendances des scripts racine ne sont déclarées que dans leurs docstrings.

---

## 5. Feuille de route vers le terminal

L'objectif est de passer de « N scripts, N fenêtres » à « un terminal, N
panneaux ». Les étapes ci-dessous sont ordonnées : chacune réduit le coût de la
suivante.

### Étape 1 — Extraire le socle commun

Aujourd'hui la logique métier est mélangée au rendu dans chaque script. Trois
modules à extraire, sans changer le comportement :

- **`indicators.py`** — `rsi`, Connors RSI, Bollinger, MA/EMA, ATR, volatilité,
  volume profile, détection de croisements. Ces calculs sont réimplémentés dans
  `btc-dash.py` et `btc_dashboard2.py` avec des **variantes silencieuses** :
  MA 9/26 en SMA d'un côté, en EMA de l'autre, et deux calculs différents du
  percent rank du Connors RSI. Deux panneaux d'un même terminal ne peuvent pas
  afficher deux valeurs différentes du même indicateur.
- **`exchanges.py`** — les connecteurs WebSocket. `btc_orderbook_live.py` et
  `arbitrage/main.py` normalisent les mêmes flux Binance / Coinbase / Kraken
  vers la même structure `{prix: quantité}`, avec deux implémentations
  distinctes, deux politiques de reconnexion et deux stratégies de troncature.
  La version d'`arbitrage/main.py` (classe de base + backoff exponentiel + état
  `connected`/`error` exposé) est la meilleure base ; celle de
  `btc_orderbook_live.py` apporte les garde-fous `MAX_LEVELS` et `MAX_WS_SIZE`
  qu'il faut conserver.
- **`sources.py`** — les collecteurs non temps réel : klines REST, flux ETF
  farside, RSS + CryptoPanic, Fear & Greed.

### Étape 2 — Choisir une couche de rendu unique

Les quatre familles d'interface (§1) sont incompatibles entre elles : on ne peut
pas composer un panneau Dash et un panneau matplotlib dans une même fenêtre. Il
faut trancher, et les deux candidats sérieux sont déjà présents dans le dépôt :

| Piste | Base existante | Pour | Contre |
|---|---|---|---|
| **Web (Dash/Plotly)** | `btc-dash.py`, `btc_dashboard2.py` | graphiques riches et interactifs déjà écrits, mise en page en grille, accessible à distance | lourd, un panneau = un callback, latence de rafraîchissement |
| **TUI (Rich / Textual)** | `arbitrage/main.py` | esthétique « terminal » fidèle à la cible, densité d'information, très faible latence, `Layout` déjà utilisé | chandeliers et profils de volume difficiles à rendre en caractères |

Une piste mixte est possible : TUI pour les panneaux tabulaires (carnets,
arbitrage, news, flux ETF) et vues web pour les graphiques.

### Étape 3 — Mutualiser la couche de données

Dans un terminal, plusieurs panneaux consomment la même donnée : le prix Binance
alimente à la fois le chandelier, le carnet et le scan d'arbitrage. Il faut donc
un **hub d'état unique** — une seule connexion par exchange, un état partagé, et
des panneaux qui s'y abonnent — plutôt que la situation actuelle où chaque
script rouvre ses propres connexions. Le couple `dict[str, OrderBook]` +
`ArbitrageEngine` d'`arbitrage/main.py` en est déjà une version réduite et
constitue un bon point de départ.

### Étape 4 — Fusionner les doublons

Deux paires de scripts font le même travail et doivent converger avant d'être
transformées en panneaux :

- **Les deux dashboards.** Garder la profondeur analytique de `btc-dash.py`
  (volume profile avec POC et Value Area, signaux gradués `-2..+2`, ATR,
  bascule USD/EUR) et l'abstraction `ccxt` + la palette de timeframes de
  `btc_dashboard2.py`, ainsi que son repli sur données de démo.
- **Les deux scripts ETF.** Supprimer `etf.py` au profit d'`etf_bitcoin_flows.py`
  (voir §3.6).

### Étape 5 — Compléter la couverture

- **Contexte macro** : `m2supply.html` est tronqué (§3.8). La corrélation
  BTC / masse monétaire M2 est un panneau pertinent pour la cible ; il faut soit
  reconstruire la page, soit réimplémenter la vue dans la couche de rendu
  retenue.
- **Panneaux absents** d'un terminal Bitcoin complet : dominance et
  capitalisation, funding rates et open interest sur les perpétuels,
  liquidations, métriques on-chain (hashrate, flux exchanges), calendrier macro.
- `order/` est un répertoire vide — soit il matérialise un panneau prévu et
  reste à écrire, soit il est à supprimer.

### Chantiers d'hygiène (indépendants)

- **Déclarer les dépendances** dans un `requirements.txt` à la racine (ou un
  `pyproject.toml`) plutôt qu'en docstring ; le venv racine n'a ni `dash`, ni
  `plotly`, ni `ccxt`, ni `rich`, ni `feedparser` (§4).
- **Ports** : les deux dashboards codent `8050` en dur, ce qui les empêche de
  tourner ensemble. `btc-dash.py` écoute de plus sur `0.0.0.0`, exposé au
  réseau local.
- **Versionner le dépôt** : il n'est pas sous git, ce qui rend risquée toute
  refonte de cette ampleur.
