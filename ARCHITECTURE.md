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

### État actuel : le terminal existe

Le dépôt est désormais une application, doublée de scripts hérités en sursis.

- **Le socle** ([§2](#2-le-socle-btcterm)) : tout ce qui n'est pas rendu —
  calculs d'indicateurs, connexions aux plateformes, collecte, moteur
  d'arbitrage — vit dans `btcterm/`.
- **Le terminal** ([§3](#3-le-terminal-terminal)) : une application Dash unique
  regroupant six panneaux sur une grille, avec trois régimes de
  rafraîchissement et un hub qui n'ouvre qu'une connexion par plateforme.

```bash
python -m terminal.app        # http://127.0.0.1:8050
```

Restent les scripts d'origine ([§5](#5-détail-des-panneaux)), maintenus le temps
de la transition : ils partagent le socle mais gardent chacun leur fenêtre.
Ce qui manque encore pour atteindre la cible est détaillé en
[§7](#7-feuille-de-route-vers-le-terminal).

```
/home/stefan/python/btc
├── README.md                  ← guide d'utilisation
├── ARCHITECTURE.md            ← ce fichier
├── requirements.txt           ← dépendances de tout le dépôt
│
├── btcterm/                   ← SOCLE : données, calculs, connexions
│   ├── indicators.py              calculs techniques purs
│   ├── exchanges.py               carnet normalisé + connecteurs WebSocket
│   ├── arbitrage.py               moteur d'écarts inter-plateformes
│   ├── sources.py                 collecteurs REST, ETF, news, sentiment
│   └── hub.py                     connexions mutualisées + caches
│
├── terminal/                  ← TERMINAL : l'application Dash
│   ├── app.py                     grille, horloges, bandeau, plein écran
│   ├── theme.py                   palette et styles
│   ├── charts.py                  figures Plotly
│   ├── assets/                    CSS et JS servis au navigateur
│   └── panels/                    price · orderbook · arbitrage · etf · news
│
├── tests/
│   ├── test_indicators_parity.py  non-régression de l'extraction
│   ├── test_terminal_wiring.py    panneaux posés et branchés
│   └── test_fullscreen_toggle.py  bascule plein écran (sous Node)
│
├── btc-dash.py                ← hérité, couvert par le panneau prix
├── btc_dashboard2.py          ← hérité (Dash, ccxt)
├── btc-liquidity.py           ← hérité (matplotlib, REST polling)
├── btc_orderbook_live.py      ← hérité (matplotlib, WebSockets ×3)
├── etf.py                     ← hérité, v1 non migrée
├── etf_bitcoin_flows.py       ← CLI flux ETF (v2)
├── m2supply.html              ← page statique (incomplète)
│
├── arbitrage/                 ← TUI Rich, partage le moteur du socle
│   ├── main.py
│   ├── requirements.txt
│   └── README.md
│
├── news/                      ← collecte des news + SQLite
│   ├── btc_news.py
│   ├── requirements.txt
│   ├── setup.fish                 installe venv + fonction fish `btcnews`
│   └── systemd_timer.conf         gabarit de timer systemd --user
│
├── order/                     ← vide
└── venv/                      ← venv Python 3.14 partagé (racine)
```

### Familles d'interface héritées

| Famille | Scripts | Boucle d'affichage |
|---|---|---|
| Web (Dash/Plotly) | `btc-dash.py`, `btc_dashboard2.py` | `dcc.Interval` → callbacks serveur |
| GUI desktop (matplotlib) | `btc-liquidity.py`, `btc_orderbook_live.py` | `FuncAnimation` |
| TUI (Rich) | `arbitrage/main.py` | `rich.live.Live` piloté par asyncio |
| CLI batch | `etf*.py`, `news/btc_news.py` | one-shot (ou `watch` en boucle) |

---

## 2. Le socle `btcterm`

Trois modules sans dépendance à une quelconque interface. Ils ne connaissent
ni Dash, ni matplotlib, ni Rich : ce sont les panneaux qui les composent, jamais
l'inverse. Aucun n'écrit sur disque ni n'affiche quoi que ce soit.

### 2.1 `indicators` — calculs techniques

Fonctions pures sur des `Series`/`DataFrame` pandas, sans effet de bord ni
écriture dans le DataFrame d'entrée.

| Famille | Fonctions |
|---|---|
| Moyennes | `sma`, `ema`, `moving_average(method=…)` |
| Oscillateurs | `rsi`, `streak`, `percent_rank`, `connors_rsi` |
| Volatilité | `bollinger`, `atr`, `volatility` |
| Liquidité | `volume_profile` (POC + Value Area) |
| Croisements | `cross_up`, `cross_down`, `crosses_above`, `crosses_below` |
| Signaux | `graded_signals` (-2..+2), `marker_signals` (points d'achat/vente) |

Deux divergences historiques entre les dashboards ont dû être arbitrées :

- **SMA contre EMA** pour les MA 9 et 26. Les deux comportements sont
  conservés, mais le choix devient explicite au point d'appel via
  `moving_average(..., method=…)` au lieu d'être enfoui dans chaque fichier.
  La divergence sera tranchée à la fusion des deux panneaux (§7, étape 4).
- **Connors RSI**. `btc-dash.py` calculait sa troisième composante comme le rang
  d'un ROC 100 périodes sur **tout l'historique chargé**, au lieu du rang
  centile roulant de la variation d'une période. La valeur dépendait donc du
  nombre de bougies affichées : changer de fenêtre temporelle changeait le CRSI
  d'une bougie déjà passée. C'est la définition standard qui est retenue ;
  l'écart atteint 32 points sur le jeu de test.

### 2.2 `exchanges` — carnet normalisé et connecteurs

`OrderBook` stocke les niveaux en `{prix: quantité}` — la seule forme qui
accepte indifféremment un snapshot complet et une mise à jour incrémentale — et
produit les vues triées à la lecture (`top`, `cumulative_depth`, `best_bid`,
`spread`, `age_ms`). Toutes les opérations passent par un verrou, de sorte
qu'un thread de rendu puisse lire pendant qu'un thread réseau écrit.

`ExchangeConnector` fournit la boucle de reconnexion (backoff exponentiel
plafonné, compteur remis à zéro après une connexion qui tient) et publie
`connected` / `error` sur le carnet, ce qui permet à l'interface d'afficher
l'état réel de chaque flux. Les sous-classes n'implémentent que `_stream()`.

| Connecteur | Canal | Modèle de flux |
|---|---|---|
| `BinanceConnector` | `<sym>@depth<N>@<vitesse>` | snapshot répété |
| `KrakenConnector` | `book` | snapshot `as`/`bs` puis deltas `a`/`b` |
| `CoinbaseConnector` | `level2_batch` (flux public) | snapshot puis `l2update` |
| `CoinbaseAdvancedConnector` | `level2` (Advanced Trade) | deltas seuls |
| `BybitConnector` | `orderbook.<N>` | snapshot puis deltas |
| `OKXConnector` | `books5` | snapshot 5 niveaux |

Deux plateformes cotent des paires différentes selon le flux — le flux public
Coinbase ne connaît pas l'USDT — d'où les deux connecteurs Coinbase et le
produit passé en paramètre partout.

Deux garde-fous, hérités de `btc_orderbook_live.py`, s'appliquent désormais à
tous les connecteurs : `max_levels` (le snapshot Coinbase compte des milliers
de niveaux, dont seuls les plus proches du marché sont exploitables) et
`MAX_WS_SIZE` à 20 Mo (ce même snapshot dépasse la limite de message par défaut
de `websockets`, qui referme alors la connexion).

**Correction au passage** : le connecteur Bybit n'appliquait que les messages
de type `snapshot` en ignorant les deltas, tout en rafraîchissant l'horodatage
à chaque message. Le carnet paraissait donc frais alors qu'il était figé sur
son premier état — et le moteur d'arbitrage, qui écarte les carnets de plus de
5 secondes, n'avait aucun moyen de s'en apercevoir.

### 2.3 `sources` — collecteurs

| Domaine | Fonctions |
|---|---|
| Marché REST | `fetch_klines`, `fetch_ticker_24h`, `fetch_depth` |
| Marché ccxt | `fetch_ohlcv_ccxt` |
| Hors ligne | `generate_demo_ohlcv` |
| Change | `fetch_eur_rate` |
| Institutionnel | `fetch_etf_flows` |
| News | `fetch_rss_entries`, `fetch_cryptopanic_posts`, `fetch_fear_greed` |

Ces fonctions récupèrent et normalisent, rien de plus : ni écriture en base, ni
affichage, ni filtrage métier. Le scoring des news et leur stockage SQLite
restent dans `news/btc_news.py`, dont ce sont les décisions propres.

Les dépendances optionnelles (`ccxt`, `feedparser`) sont importées à
l'intérieur des fonctions qui les utilisent, pour qu'un panneau n'ait pas à les
installer s'il ne s'en sert pas.

### 2.4 Non-régression

`tests/test_indicators_parity.py` rejoue les implémentations telles qu'elles
étaient avant l'extraction et vérifie que le socle produit exactement les mêmes
valeurs — RSI, streak, rang centile, Connors RSI, Bollinger, ATR, volatilité,
profil de volume, signaux gradués et marqueurs.

```bash
python tests/test_indicators_parity.py
```

Le comparateur refuse les séries comportant moins de 100 valeurs exploitables :
sans ce garde-fou, deux séries entièrement `NaN` — cas typique d'une fixture mal
indexée — se compareraient comme égales et le test passerait sans rien
vérifier.

## 3. Le terminal `terminal/`

Une application Dash unique, servie en local, qui regroupe tous les panneaux.
C'est la couche de rendu retenue à l'étape 2 : elle satisfait les trois
exigences d'usage — station de travail multi-panneaux, analyse graphique fine
(zoom, crosshair), et accès aussi bien sur la machine qu'à distance par tunnel
SSH, ce qui écartait à la fois une interface texte et une application Qt.

```
terminal/
├── app.py           assemblage de la grille, horloges, bandeau, point d'entrée
├── theme.py         palette et styles partagés
├── charts.py        constructeurs de figures Plotly
└── panels/          un module par panneau
    ├── price.py         chandeliers, indicateurs, profil de volume
    ├── orderbook.py     carnet + profondeur comparée
    ├── arbitrage.py     écarts inter-plateformes
    ├── etf.py           flux des ETF spot
    └── news.py          fil de news + Fear & Greed
```

```
┌─────────┬────────┬───────────┐
│         │ carnet │ arbitrage │
│  prix   ├────────┼───────────┤
│         │ profo. │           │
│         ├────────┤   news    │
│         │  etf   │           │
└─────────┴────────┴───────────┘
```

### 3.1 Trois régimes de rafraîchissement

C'est la décision qui rend le terminal utilisable. L'ancien `btc-dash.py` faisait
vivre tous ses éléments au même rythme et resérialisait la figure entière à
chaque tour — d'où un rafraîchissement toutes les 10 secondes, incompatible avec
un carnet d'ordres.

| Horloge | Période | Panneaux | Coût mesuré par tour |
|---|---|---|---|
| `tick-fast` | 250 ms | carnet, profondeur, arbitrage | 8–33 ms, 6–25 Ko |
| `tick-slow` | 2 s | prix, bandeau | 0,3–1,3 s, 162 Ko |
| `tick-rare` | 5 min | ETF, news, Fear & Greed | 0,3–0,5 s, 8–10 Ko |

Les panneaux rapides ne touchent jamais le réseau : ils lisent les carnets que
le hub entretient en mémoire, ce qui explique l'écart d'un facteur cinquante
avec le panneau prix. Ce dernier n'a de toute façon aucune raison d'aller plus
vite : une bougie journalière ne change pas quatre fois par seconde.

Une conséquence pratique : `update_title=None` désactive le « Updating… » que
Dash affiche par défaut dans l'onglet, sans quoi il clignoterait en permanence
au rythme de l'horloge rapide.

### 3.2 Le cours d'abord

Le panneau prix est un `make_subplots` dont la **structure est construite à la
demande** : `build_price_chart` reçoit la liste des sous-graphiques voulus
(`rsi`, `crsi`, `volume`) et un drapeau pour le profil de volume, puis compose
la grille en conséquence.

La hauteur laissée au cours dépend de deux choses — combien de sous-graphiques
l'accompagnent, et si le panneau est agrandi :

| Sous-graphiques | Dans la grille | En plein écran |
|---|---|---|
| 3 | 66 % | 75 % |
| 2 (défaut) | 72 % | 80 % |
| 1 | 80 % | 86 % |
| 0 | 100 % | 100 % |

C'est le cours qu'on vient lire en séance d'analyse ; les oscillateurs
l'accompagnent, ils ne le concurrencent pas. Un partage fixe à 54 % — celui de
l'ancien dashboard — le rendait illisible dès que le panneau rétrécissait.

### 3.3 Préserver l'état d'analyse

Toutes les figures portent un `uirevision`. Sans lui, chaque rafraîchissement
réinitialiserait le zoom, le pan et la sélection de légende : impossible
d'examiner une zone de prix pendant que les données coulent, ce qui viderait de
son sens l'usage en séance d'analyse.

La valeur encode la série affichée et la structure du graphique
(`"1d:USD:profile,rsi,volume"`). Elle reste stable d'un rafraîchissement à
l'autre **et au passage en plein écran** — le zoom en cours survit donc à
l'agrandissement, ce qui est précisément le geste qu'on fait pour examiner une
zone de plus près. Elle change en revanche quand on change d'intervalle, de
devise ou de sous-graphiques : le recadrage est alors ce qu'on veut.

### 3.4 Plein écran

Une grille de six panneaux ne laisse pas assez de place pour lire finement un
graphique. Chaque panneau porte donc un bouton ⛶ qui le fait couvrir la fenêtre
entière, les autres étant masqués ; un second clic, ou `Échap`, rend la grille.

La bascule est un callback **clientside** : elle se contente d'échanger des
classes CSS, sans aller-retour serveur ni recalcul de figure. Le panneau agrandi
passe en `position: fixed` sous le bandeau, les autres en `display: none`.

Un détail qui n'est pas optionnel : la fonction émet un événement `resize` sur
la fenêtre après la bascule. Plotly ne redimensionne ses figures qu'à cet
événement — sans lui, le graphique agrandi garderait la taille de sa vignette.

La bascule s'ouvre par le bouton ⛶ — **toujours visible**, une première version
qui ne l'affichait qu'au survol s'étant révélée introuvable — ou par un
double-clic sur le panneau. Le double-clic ignore les graphiques : Plotly y
réserve ce geste à la réinitialisation des axes.

Cette logique vivant en JavaScript, elle échapperait aux tests Python.
`tests/test_fullscreen_toggle.py` extrait la fonction de `app.py` et l'exécute
sous Node avec un faux `dash_clientside` (test ignoré si Node est absent).

### 3.5 Anatomie d'un panneau

Chaque module de `panels/` expose exactement deux fonctions :

- `layout()` — ses composants Dash
- `register(app, hub)` — ses callbacks

Un panneau ne fait aucun appel réseau : il demande au hub, qui mutualise. Il
n'écrit rien non plus — le panneau news lit la base du tracker en lecture seule,
la collecte et le scoring restant la responsabilité de `news/btc_news.py`.

### 3.6 Contrôle visuel

Une partie des défauts d'interface ne se voit qu'à l'écran, et aucune quantité
de tests Python ne les révèle. `tests/ui_smoke.py` pilote donc Firefox par
**Marionette** — le protocole d'automatisation intégré au navigateur — via un
client d'une soixantaine de lignes (`tests/marionette_client.py`), ce qui évite
d'installer geckodriver ou Selenium.

```bash
python -m terminal.app &
python tests/ui_smoke.py --capture /tmp/captures
```

Il vérifie les panneaux posés, la visibilité et le non-recouvrement du bouton,
l'agrandissement réel, le retour par `Échap`, le double-clic, et que le carnet
montre bien ses deux côtés. Les captures qu'il dépose ont mis au jour deux
défauts qu'aucun test logique n'aurait signalés : la légende du graphique
recouvrait les bougies, et le carnet, trop haut pour son panneau, n'affichait
que les ventes. C'est aussi ainsi qu'a été repérée une feuille de style qui ne
prenait pas : les sélecteurs étaient stylés via `input:checked + span`, alors
que Dash enveloppe la case dans un `<span>` et marque le `<label>` d'une classe
`selected` — rien n'indiquait donc l'option active.

### 3.7 Câblage vérifié

`terminal/panels/__init__.py` déclare `PANELS`, et `app.py` enregistre les
callbacks en parcourant cette liste : ajouter un module suffit à le brancher.

`tests/test_terminal_wiring.py` vérifie qu'aucun panneau n'est écrit puis
oublié — il découvre les modules **sur le disque**, jamais via `PANELS`, sinon
un panneau absent de la liste échapperait aussi au contrôle. C'est l'erreur qui
a laissé le panneau ETF muet à sa création : écrit, mais ni placé dans la grille
ni enregistré, sans que rien ne le signale au démarrage.

```bash
python tests/test_terminal_wiring.py
```

## 4. Patrons transverses

### 4.1 Deux modèles d'acquisition de données

**Polling REST** (`btc-dash.py`, `btc-liquidity.py`, `etf*.py`) — appel HTTP
synchrone, `try/except` large, valeur de repli en cas d'échec. Simple, latence
de l'ordre de la seconde.

**Streaming WebSocket** (`btc_orderbook_live.py`, `arbitrage/main.py`) — une
coroutine par exchange, boucle infinie de reconnexion, état mutable partagé.
Latence de l'ordre de 100 ms.

### 4.2 Séparation producteur / consommateur

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

### 4.3 Dégradation contrôlée

Chaque source distante a une stratégie de repli explicite :

| Source | Repli |
|---|---|
| ccxt / Binance (`btc_dashboard2.py`) | données de démo générées localement |
| taux EUR (`btc-dash.py`) | constante `0.924` |
| ticker 24 h (`btc-dash.py`) | dict vide, les cartes affichent `—` |
| WebSocket (tous) | reconnexion (3 s fixes, ou backoff exponentiel plafonné à 30 s) |
| flux RSS (`news`) | le feed en échec est sauté, les autres continuent |
| CryptoPanic sans clé | source simplement désactivée |

### 4.4 Palettes de couleurs

Tous les scripts sont en thème sombre, avec une constante de palette en tête de
fichier (`C` dans `btc-dash.py`, constantes `DARK_BG`/`GREEN`/… dans
`btc_dashboard2.py`, `BID_COLOR`/`ASK_COLOR`/… dans `btc_orderbook_live.py`).
Convention constante : **vert = achat/hausse, rouge = vente/baisse**.

---

## 5. Détail des panneaux

### 5.1 `btc-dash.py` — BTC Ultra Dashboard

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

Depuis la phase 1, le fichier ne contient plus que ce qui lui est propre :

- **Composition** : `compute_indicators` choisit les périodes et les colonnes,
  `_signals` et `volume_profile` ne font que déléguer au socle
- **Rendu** : `build_chart`, `stat_card`, layout Dash
- **Callbacks** : `refresh_eur` (taux mis en `dcc.Store`), `refresh_all`
  (figure + cartes), déclenchés par `Input("tick", "n_intervals")`

Le réseau passe par `sources.fetch_klines` / `fetch_ticker_24h` /
`fetch_eur_rate`, et les calculs par `indicators`. Ce panneau utilise des
**SMA** pour les MA 9 et 26.

Le taux EUR transite par un `dcc.Store` pour éviter un appel FX à chaque
recalcul de graphique.

### 5.2 `btc_dashboard2.py` — BTC Dashboard (ccxt)

Même découpage général, trois différences structurantes :

1. **Abstraction exchange** : `ccxt.binance()` au lieu d'appels REST manuels,
   ce qui rend le changement d'exchange trivial (`EXCHANGE` en tête de fichier).
2. **`TIMEFRAME_MAP`** associe à chaque libellé d'UI un couple
   `(timeframe ccxt, limit)` — c'est la table à éditer pour ajouter un
   intervalle. Les libellés `1y` et `All` sont des alias re-mappés sur `1d`/`1w`.
3. **Repli hors ligne** : `sources.generate_demo_ohlcv` produit une marche
   aléatoire OHLCV que `add_indicators` enrichit exactement comme les données
   réelles, de sorte que tout l'aval du pipeline fonctionne à l'identique.

La phase 1 a supprimé la duplication qui existait entre `fetch_ohlcv` et
`generate_demo_data` : les deux chemins passent maintenant par le même
`add_indicators`. Ce panneau utilise des **EMA** pour les MA 9 et 26.

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

### 5.3 `btc-liquidity.py` — pools de liquidité

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

- Acquisition déléguée à `sources.fetch_depth` et `sources.fetch_ticker_24h`.
- État partagé : `deque(maxlen=60)` pour l'historique (fenêtre glissante sans
  gestion de taille explicite) + listes mutables pour le carnet.
- `compute_trend(prices)` compare le dernier point au 10ᵉ point antérieur et
  retourne `(libellé, couleur, flèche)` — seuil ±0,05 %.
- Mise en page en `GridSpec(3, 3)` : le graphe de prix occupe les deux
  premières rangées sur toute la largeur, les trois panneaux se partagent la
  troisième.
- Un `fetch_data()` synchrone est appelé avant le démarrage du thread pour que
  la première frame ne soit pas vide.

### 5.4 `btc_orderbook_live.py` — carnet d'ordres multi-exchange

Depuis la phase 1, ce fichier ne fait plus que du rendu : les 300 lignes de
plomberie WebSocket sont parties dans `btcterm.exchanges`, il en reste 120.

```
build_books_and_connectors()   3 OrderBook + 3 connecteurs (BTC/USD)
        ↓
run_connectors_in_thread(...)  event loop asyncio dans un thread démon
        ↓
FuncAnimation(update, 1000ms)  matplotlib garde le thread principal
        └─ book.cumulative_depth("bids"/"asks") → courbe en escalier remplie
```

Chaque panneau affiche la profondeur cumulée des deux côtés, le prix médian et
le spread. La normalisation des trois formats de flux (snapshot répété chez
Binance, snapshot + deltas chez Coinbase et Kraken) est décrite en §2.2.

### 5.5 `arbitrage/main.py` — moteur d'arbitrage

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

- `OrderBook` vient du socle (§2.2) ; toute la logique de fraîcheur découle de
  sa propriété `age_ms`.
- `ArbitrageOpportunity` — les deux exchanges, les prix, profit brut et net,
  frais, horodatage, et `is_profitable` (`net > MIN_PROFIT_PCT`).

**Connecteurs** — fournis par le socle (§2.2) depuis la phase 1.
`build_connectors()` se contente d'associer chaque plateforme à son produit en
USDT ; Coinbase passe par le flux Advanced Trade, son flux public historique ne
cotant pas cette paire.

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

### 5.6 `etf.py` / `etf_bitcoin_flows.py` — flux ETF

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
sur la v2. Seule la v2 a été migrée sur le socle (`sources.fetch_etf_flows`) :
migrer la v1 n'aurait fait qu'entretenir un doublon voué à disparaître à
l'étape 4.

### 5.7 `news/btc_news.py` — BTC News Tracker

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

**Pipeline d'ingestion** — la récupération vient du socle, le scoring et le
stockage restent ici :

```
fetch_rss(conn)          sources.fetch_rss_entries   (6 flux, feedparser)
fetch_cryptopanic(conn)  sources.fetch_cryptopanic_posts (sautée sans clé)
fetch_fear_greed(conn)   sources.fetch_fear_greed → table fear_greed
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

### 5.8 `m2supply.html`

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

## 6. Environnements Python

| Emplacement | Contenu | Utilisé par |
|---|---|---|
| `venv/` (racine, Python 3.14) | l'ensemble de `requirements.txt` : pandas, numpy, matplotlib, requests, lxml, beautifulsoup4, tabulate, websockets, dash, plotly, ccxt, rich, feedparser | le terminal et tous les scripts |
| `news/.venv` (créé par `setup.fish`) | feedparser, requests | `news/btc_news.py` |
| `news/.venv` (optionnel) | feedparser, requests | usage isolé du tracker via `news/setup.fish` |

Un `requirements.txt` à la racine déclare désormais l'ensemble des dépendances,
regroupées par usage (socle, dashboards, fenêtres matplotlib, temps réel, ETF,
news) :

```bash
pip install -r requirements.txt
```

Aucune installation du paquet `btcterm` n'est nécessaire : les scripts de la
racine le trouvent parce que Python ajoute le répertoire du script au chemin
d'import, et les deux sous-projets remontent explicitement d'un niveau. Un
`pyproject.toml` deviendra utile quand le terminal aura un point d'entrée
unique.

---

## 7. Feuille de route vers le terminal

L'objectif est de passer de « N scripts, N fenêtres » à « un terminal, N
panneaux ». Les étapes ci-dessous sont ordonnées : chacune réduit le coût de la
suivante.

### Étape 1 — Extraire le socle commun ✅ *faite*

Les trois modules décrits en [§2](#2-le-socle-btcterm) sont en place et les huit
scripts y sont ramenés, sans changement de comportement (§2.4) :

- **`indicators.py`** — les deux variantes silencieuses (SMA/EMA, Connors RSI)
  sont désormais explicites, et celle de `btc-dash.py` qui ne suivait pas la
  définition de Connors a été corrigée.
- **`exchanges.py`** — une seule implémentation des connecteurs, réunissant la
  classe de base à backoff exponentiel d'`arbitrage/main.py` et les garde-fous
  `max_levels` / `MAX_WS_SIZE` de `btc_orderbook_live.py`. Un bug de gestion
  des deltas Bybit a été corrigé au passage.
- **`sources.py`** — les collecteurs non temps réel : klines REST, flux ETF
  farside, RSS + CryptoPanic, Fear & Greed.

Reste ouvert de cette étape : `etf.py` (v1) n'a pas été migrée, puisqu'elle doit
disparaître à l'étape 4.

### Étape 2 — Choisir une couche de rendu unique ✅ *faite*

**Décision : une application web Dash**, décrite en [§3](#3-le-terminal-terminal).

Trois exigences d'usage ont tranché : une **station de travail professionnelle**
multi-panneaux, des **séances d'analyse active** (zoom, crosshair, lecture fine
des chandeliers), et un accès **sur la machine comme à distance par SSH**.

| Piste écartée | Raison |
|---|---|
| TUI (Rich / Textual) | des chandeliers en braille ne supportent ni zoom ni lecture fine, et l'esthétique texte n'était pas le but |
| GUI native (PyQt + pyqtgraph) | inutilisable à distance : le X11 forwarding sur SSH ne tient pas pour du graphique temps réel |

Le web est la seule famille à satisfaire les trois : servi en local, ouvert dans
le navigateur ici, atteignable ailleurs par `ssh -L 8050:localhost:8050`. Le port
reste lié à `127.0.0.1` — le tunnel dispense de l'exposer sur le réseau.

Les faiblesses de Dash relevées lors de l'analyse ont été traitées plutôt que
subies : trois horloges au lieu d'une (§3.1) et `uirevision` sur toutes les
figures (§3.2).

**Reste ouvert.** Le push WebSocket serveur→navigateur n'a pas été implémenté :
les mesures montrent qu'un tour d'horloge rapide coûte 8 à 33 ms pour 6 à 25 Ko,
ce qui passe sans peine en interrogation à 250 ms sur une boucle locale. Il
deviendra utile pour descendre sous 100 ms, ou si la latence d'un tunnel SSH
lointain se fait sentir.

### Étape 3 — Mutualiser la couche de données ✅ *faite*

`btcterm/hub.py` — `MarketHub` ouvre **une** connexion par plateforme dans un
thread démon, entretient les cinq carnets, expose le moteur d'arbitrage et met
en cache les appels REST avec une durée de vie propre à chaque nature de donnée
(chandeliers 5 s, taux de change 1 h, flux ETF 30 min).

Le cache conserve la dernière valeur connue quand un rafraîchissement échoue :
un panneau qui affiche une donnée un peu datée vaut mieux qu'un panneau vide.

### Étape 4 — Fusionner les doublons ⏳ *engagée*

Fait :

- `build_chart` a quitté `btc-dash.py` pour `terminal/charts.py` ; le script ne
  fait plus que l'appeler.
- Le moteur d'arbitrage a quitté `arbitrage/main.py` pour `btcterm/arbitrage.py`,
  partagé avec le panneau du terminal.

Reste :

- **Supprimer les scripts couverts par le terminal.** Le panneau prix reprend
  `btc-dash.py` à l'identique ; les panneaux carnet et profondeur couvrent
  `btc-liquidity.py` et `btc_orderbook_live.py`. À récupérer au préalable depuis
  `btc_dashboard2.py` : la palette de timeframes plus large (`15m` → `1M`) et le
  repli sur données de démo.
- **Supprimer `etf.py`** au profit d'`etf_bitcoin_flows.py` (voir §5.6).

### Étape 5 — Compléter la couverture

- **Contexte macro** : `m2supply.html` est tronqué (§5.8). La corrélation
  BTC / masse monétaire M2 mérite un panneau ; la couche de rendu étant
  tranchée, autant l'écrire directement en panneau Dash plutôt que réparer la
  page.
- **Collecte des news** : le panneau lit la base du tracker mais ne la remplit
  pas. Il faut soit brancher `fetch` sur l'horloge lente du terminal, soit
  documenter le timer systemd comme prérequis.
- **Panneaux absents** d'un terminal Bitcoin complet : dominance et
  capitalisation, funding rates et open interest sur les perpétuels,
  liquidations, métriques on-chain (hashrate, flux exchanges), calendrier macro.
- `order/` est un répertoire vide — soit il matérialise un panneau prévu et
  reste à écrire, soit il est à supprimer.

### Chantiers d'hygiène (indépendants)

- ~~**Déclarer les dépendances**~~ — fait en phase 1 (§5). Le venv racine reste
  toutefois incomplet : ni `dash`, ni `plotly`, ni `ccxt`, ni `rich`, ni
  `feedparser` n'y sont installés.
- **Ports** : le terminal accepte `--port` et `--host`, et reste sur
  `127.0.0.1` par défaut. Les deux dashboards hérités codent encore `8050` en
  dur — et `btc-dash.py` écoute sur `0.0.0.0` —, ce qui les empêche de tourner
  en même temps que le terminal. Réglé par leur suppression à l'étape 4.
- ~~**Versionner le dépôt**~~ — fait : dépôt git local sur `main`.
