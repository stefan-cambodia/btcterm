# Architecture

Ce document décrit l'organisation interne du dépôt : structure, patrons
récurrents, flux de données et détail module par module.

---

## 1. Vue d'ensemble

### Cible : un terminal Bloomberg orienté Bitcoin

Le projet vise un **poste de travail unifié** — un terminal à la Bloomberg,
centré sur le Bitcoin — où chaque famille d'information occupe un panneau d'une
même interface : prix et indicateurs, carnet d'ordres, profondeur comparée entre
exchanges, écarts d'arbitrage, liquidations, flux des ETF spot, marché à terme,
fil de news et sentiment, calendrier macro, dominance, données de chaîne,
contexte monétaire, marché des options, marchés traditionnels.

### État actuel : le terminal existe

Le dépôt est désormais une application, entourée de quelques outils en ligne
de commande qui couvrent ce que le terminal n'a pas encore absorbé.

- **Le socle** ([§2](#2-le-socle-btcterm)) : tout ce qui n'est pas rendu —
  calculs d'indicateurs, connexions aux plateformes, collecte, moteur
  d'arbitrage — vit dans `btcterm/`.
- **Le terminal** ([§3](#3-le-terminal-terminal)) : une application Dash unique
  regroupant vingt et un panneaux sur une grille de six cellules — une cellule pouvant
  en héberger plusieurs, choisis par onglets —, avec trois régimes de
  rafraîchissement doublés d'un canal push pour les panneaux rapides, un hub
  qui n'ouvre qu'une connexion par plateforme et un collecteur de news en
  tâche de fond.

```bash
python -m terminal.app        # http://127.0.0.1:8050
```

Les quatre scripts que le terminal remplace — les deux dashboards Dash et les
deux fenêtres matplotlib — ont été supprimés à l'étape 4, après récupération de
ce qu'ils avaient de propre, et `m2supply.html` a suivi à l'étape 5, remplacé
par le panneau macro. La TUI d'arbitrage, dernier doublon d'un panneau, les a
suivis à la clôture de la feuille de route ; ce qui subsiste
([§5](#5-détail-des-outils-restants)) ne fait pas double emploi avec un
panneau : l'export des flux ETF et le tracker de news. La feuille de route qui
a mené ici — soldée — et la liste de ce qui manque encore sont en
[§7](#7-feuille-de-route-vers-le-terminal).

```
/home/stefan/python/btc
├── README.md                  ← guide d'utilisation
├── ARCHITECTURE.md            ← ce fichier
├── pyproject.toml             ← empaquetage : commande `btcterm`
├── requirements.txt           ← dépendances de tout le dépôt
│
├── btcterm/                   ← SOCLE : données, calculs, connexions
│   ├── indicators.py              calculs techniques purs
│   ├── exchanges.py               carnet normalisé + connecteurs WebSocket
│   ├── arbitrage.py               moteur d'écarts inter-plateformes
│   ├── liquidations.py            fil des positions fermées de force
│   │                              (Binance, et Bybit)
│   ├── sources.py                 collecteurs REST : marché, ETF, M2,
│   │                              terme, options, marchés, chaîne, news,
│   │                              sentiment
│   ├── macrocal.py                calendrier macro tenu à la main
│   ├── newsdb.py                  base de news : schéma, scoring, collecte
│   ├── analysis.py                état des intervalles, bilan des signaux,
│   │                              règles simples, position, saisonnalité
│   ├── options.py                 chaîne d'options Deribit : sourire, skew,
│   │                              max pain, base des futures (§2.11)
│   ├── markets.py                 le bitcoin face aux actions, à l'or, au
│   │                              dollar et aux taux (§2.12)
│   ├── cycle.py                   le cycle : halving, repli depuis le sommet,
│   │                              Mayer, cycles superposés (§2.15)
│   ├── tape.py                    ruban des échanges : gros ordres, flux
│   │                              agressif, sens du preneur (§2.13)
│   ├── notify.py                  les alertes vers ntfy, jusqu'au téléphone
│   ├── brief.py                   synthèse des dernières 24 h : panneau,
│   │                              ligne de commande, ntfy du matin (§2.14)
│   ├── history.py                 bougies closes sur disque
│   │                              (~/.btcterm/klines.db)
│   ├── journal.py                 journal des liquidations + épisodes
│   │                              d'arbitrage (~/.btcterm/journal.db)
│   ├── alerts.py                  moteur d'alertes : seuils, rafales,
│   │                              financement, news, arbitrage
│   ├── resolver.py                résolution DNS de secours (DoH) contre
│   │                              l'empoisonnement par le FAI
│   └── hub.py                     connexions mutualisées + caches
│
├── terminal/                  ← TERMINAL : l'application Dash
│   ├── app.py                     assemblage : horloges, Stores partagés,
│   │                              point d'entrée
│   ├── grid.py                    grille : rangement, cellules, onglets,
│   │                              plein écran (§3.4–3.5)
│   ├── placement.py               dialogue de disposition (§3.6)
│   ├── header.py                  bandeau : cours, variation, spread, DVOL,
│   │                              flux
│   ├── push.py                    canal push des panneaux rapides (§3.10)
│   ├── fx.py                      devise d'affichage commune aux panneaux
│   ├── lwc.py                     données des rendus LWC : /api/klines,
│   │                              /api/profile et /api/perp (§3.2)
│   ├── wsgi.py                    fabrique gunicorn du régime service (§3.11)
│   ├── demo.py                    démo statique pour GitHub Pages (§3.12)
│   ├── theme.py                   palette et styles
│   ├── charts.py                  figures Plotly des autres panneaux
│   ├── assets/                    CSS et JS servis au navigateur — dont
│   │                              lwc-price.js et lwc-perp.js, les dessins
│   │                              des panneaux prix et perpétuel, book.js,
│   │                              le carnet écrit en place, et vendor/
│   │                              (Lightweight Charts, sans CDN)
│   └── panels/                    price · orderbook · arbitrage ·
│                                   liquidations · journal · etf · perp ·
│                                   position · news · calendar · alerts ·
│                                   macro · markets · dominance · onchain ·
│                                   analysis · options · sources · tape ·
│                                   brief
│
├── tests/
│   ├── test_indicators_parity.py  non-régression des indicateurs
│   ├── test_news_scoring.py       non-régression du scoring des news
│   ├── test_liquidations.py       lecture du flux de liquidations
│   ├── test_macrocal.py           calendrier macro tenu à la main
│   ├── test_fear_greed.py         indice Fear & Greed : source, hub, couleurs
│   ├── test_etf_flows.py          flux ETF : fenêtres, cumul, classement
│   ├── test_options.py            options : sourire, max pain, base, panneau
│   ├── test_markets.py            marchés : séances, corrélations, tableau
│   ├── test_cycle.py              cycle : halving, repli, Mayer, panneau
│   ├── test_notify.py             alertes vers ntfy, sans réseau
│   ├── test_tape.py               ruban : sens du preneur, flux, panneau
│   ├── test_terminal_wiring.py    panneaux posés et branchés
│   ├── test_grid_layout.py        rangement configurable des panneaux
│   ├── test_fullscreen_toggle.py  bascule plein écran (sous Node)
│   ├── test_push.py               pousseur WebSocket, sans navigateur
│   ├── test_journal.py            journal : événements, épisodes, rétention
│   ├── test_history.py            historique du cours : disque, rattrapage, panne
│   ├── test_analysis.py           bilan des signaux : futur, référence, sens, export
│   ├── test_alerts.py             alertes : seuils, fronts, cadences
│   ├── test_wsgi.py               fabrique gunicorn du régime service
│   ├── test_lwc_serialize.py      contrat de série du rendu du prix
│   ├── test_lwc_api.py            /api/klines : pagination, repli démo
│   ├── test_indicators_incremental.py  dernier point : borné = complet
│   ├── test_prepare_price_frame.py  enrichissement du prix : le contrat
│   ├── test_resolver.py           résolution DNS de secours, sans réseau
│   ├── marionette_client.py       pilotage minimal de Firefox
│   ├── ui_smoke.py                contrôle de l'interface à l'écran
│   ├── ui_captures.py             captures des onglets pour le README
│   └── test_demo.py               démo statique : constructeur, shim (Node)
│
├── docs/                      ← GitHub Pages : index.html (démo, §3.12),
│   ├── demo/                      shim.js, page.js, assets copiés, data/
│   └── captures/                  captures d'écran du README (1600 px)
│
├── etf_bitcoin_flows.py       ← CLI flux ETF
│
├── news/                      ← collecte des news + SQLite
│   ├── btc_news.py
│   ├── requirements.txt
│   ├── setup.fish                 installe venv + fonction fish `btcnews`
│   └── systemd_timer.conf         gabarit de timer systemd --user
│
└── venv/                      ← venv Python 3.14 partagé (racine)
```

### Familles d'interface

| Famille | Où | Boucle d'affichage |
|---|---|---|
| Web (Dash/Plotly) | `terminal/` | `dcc.Interval` → callbacks serveur, doublé d'un push WebSocket pour les panneaux rapides (§3.10) |
| CLI batch | `etf_bitcoin_flows.py`, `news/btc_news.py` | one-shot (ou `watch` en boucle) |

Les deux fenêtres matplotlib et les deux dashboards Dash d'origine occupaient
deux familles de plus, et la TUI Rich d'arbitrage une troisième ; leurs
suppressions — l'étape 4 pour les unes, l'arbitrage rendu à la clôture pour
l'autre (§5) — ont ramené la liste à deux.

---

## 2. Le socle `btcterm`

Huit modules sans dépendance à une quelconque interface. Ils ne connaissent ni
Dash ni Rich : ce sont les panneaux qui les composent, jamais l'inverse. Aucun
n'affiche quoi que ce soit, et seul `newsdb` écrit sur disque — c'est sa raison
d'être.

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

- **SMA contre EMA** pour les MA 9 et 26. La fusion des panneaux a tranché pour
  la SMA, que le panneau prix appelle directement ; `moving_average(...,
  method=…)` garde l'autre variante à portée et permet aux tests de parité de
  la rejouer.
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
plafonné, compteur remis à zéro après une connexion qui a tenu
`STABLE_AFTER` — le code ne le faisait qu'au retour normal du flux, qui
ne revient jamais, si bien qu'après quelques pannes chaque reconnexion
d'une longue séance attendait le plafond) et publie
`connected` / `error` sur le carnet, ce qui permet à l'interface d'afficher
l'état réel de chaque flux. Les sous-classes n'implémentent que `_stream()`.

| Connecteur | Canal | Modèle de flux |
|---|---|---|
| `BinanceConnector` | `<sym>@depth@<vitesse>` + REST `/api/v3/depth` | snapshot REST de 1 000 niveaux, deltas du flux accordés par `U`/`u`, relu toutes les dix minutes |
| `KrakenConnector` | `book` | snapshot `as`/`bs` puis deltas `a`/`b` |
| `CoinbaseConnector` | `level2_batch` (flux public) | snapshot puis `l2update` |
| `CoinbaseAdvancedConnector` | `level2` (Advanced Trade) | snapshot puis deltas, même forme |
| `BybitConnector` | `orderbook.<N>` | snapshot puis deltas |
| `OKXConnector` | `books` | snapshot 400 niveaux puis deltas, suite des `seqId` vérifiée |

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

**Le niveau fantôme.** Un carnet nourri par deltas ne se corrige jamais
seul : une suppression manquée laisse un niveau que rien ne retirera, et
`best_ask` — `min(asks)` — le remonte à chaque lecture. Le journal de
séance (§2.7) l'a montré sans ambiguïté : le prix d'achat Coinbase de
tous les épisodes d'arbitrage entre 15 h 22 et 0 h 52 valait 78 880 $, à
l'unité près, pendant que le marché passait de 79 700 à 80 600 $ ; neuf
heures d'« écart rentable » de +1 à +2 %, jusqu'au redémarrage du
service, et neuf alertes d'arbitrage sur douze dans la journée. Le carnet
était frais (les bids vivaient), connecté, et faux. La cause première
tenait au connecteur Advanced Trade : Coinbase envoie un snapshot à
chaque souscription, sous la même forme que ses mises à jour — une liste
de niveaux — et le connecteur l'appliquait comme telle. Correct à la
première connexion, sur un carnet vide ; mais à chaque reconnexion, le
snapshot neuf se posait par-dessus l'ancien carnet, dont les niveaux
disparus entre-temps ne seraient plus jamais supprimés. Le snapshot
remplace, désormais. Kraken a montré le même symptôme plus brièvement
(79 046 $ pendant 25 minutes), sans cause visible — un message perdu
suffit —, d'où un garde-fou général : un tel carnet est **croisé**, son
meilleur bid dépasse son meilleur ask, ce qu'aucun marché ne cote.
`OrderBook.crossed` le dit ; après chaque mise à jour, `_check_book`
tolère un croisement fugace — les deux côtés d'un mouvement peuvent
arriver dans deux messages — mais un croisement qui tient
`CROSSED_GRACE` (2 s) lève `BookDesync`, que la boucle de reconnexion
traite comme une panne : le carnet porte l'erreur le temps du backoff, la
resouscription apporte un snapshot neuf. Et le moteur d'arbitrage
(`ArbitrageEngine._quote`) écarte un carnet croisé comme il écarte un
carnet vieux — l'écart qu'il montre n'existe
pas. `tests/test_orderbook.py` rejoue les deux causes et les deux
parades, messages au format des plateformes.

Le moteur a deux lectures. `evaluate` rend toutes les paires sans rien
compter : c'est celle des panneaux. `scan` y ajoute le compteur
« détectées » et l'historique, et n'appartient qu'au balayage du hub, une
fois par seconde (§2.7) — le compteur se lit donc en secondes de paire
rentable. Le panneau appelait `scan` : depuis que le pousseur rend
l'arbitrage à chaque trame, une paire rentable y ajoutait dix par seconde
et par onglet ouvert. Chaque balayage lit le meilleur bid et le meilleur ask
d'un carnet une fois (`OrderBook.quote`), et non plus trois fois par paire :
lire un meilleur prix parcourt tout un côté du carnet, et vingt paires y
revenaient cent vingt fois. Le balayage passe de 0,19 à 0,03 ms.

Le garde-fou a ensuite nommé la cause Kraken, qu'on croyait fortuite :
cinquante-quatre resynchronisations dans le journal du service en une
matinée, toutes de Kraken, une toutes les deux minutes quand le marché
bougeait. Le flux `book` de Kraken décrit une **fenêtre** de `depth`
niveaux par côté, pas le carnet : un niveau qu'une insertion pousse
hors de la fenêtre en sort sans message, et Kraken n'en dira plus rien —
pas même sa disparition. Le hub souscrivait vingt-cinq niveaux et le
carnet en gardait cent : les soixante-quinze de trop étaient des
fantômes en attente, que le prix croisait dès qu'il revenait sur eux.
`KrakenConnector` borne désormais `max_levels` à sa fenêtre, et `apply`
élague ce qu'une insertion en chasse, comme Kraken le fait de son côté.
Le test rejoue le contrat : un ask sorti de la fenêtre puis annulé hors
d'elle, le marché qui monte, et un carnet qui ne se croise pas.

### 2.3 `sources` — collecteurs

| Domaine | Fonctions |
|---|---|
| Marché REST | `fetch_klines`, `fetch_ticker_24h`, `fetch_depth` |
| Hors ligne | `generate_demo_ohlcv` |
| Change | `fetch_eur_rate` |
| Institutionnel | `fetch_etf_flows` |
| Macro | `fetch_m2_supply` |
| Marché à terme | `fetch_funding_history`, `fetch_open_interest`, `fetch_perp_snapshot`, `fetch_bybit_perp`, `fetch_okx_perp` |
| Agrégats de marché | `fetch_market_global` |
| Chaîne | `fetch_chain_chart`, `fetch_chain_stats` |
| News | `fetch_rss_entries`, `fetch_cryptopanic_posts`, `fetch_fear_greed`, `fetch_fear_greed_history` |

Ces fonctions récupèrent et normalisent, rien de plus : ni écriture en base, ni
affichage, ni filtrage métier. Ce qui relève du métier des news — scorer,
dédoublonner, stocker — vit à côté, dans `newsdb` (§2.4).

`fetch_m2_supply` passe par DBnomics plutôt que par FRED : c'est la même série
H.6 de la Réserve fédérale, mais sans clé d'API — l'export CSV de FRED ne
répond pas de façon fiable hors navigateur.

`feedparser`, dépendance optionnelle, est importé à l'intérieur de la fonction
qui l'utilise, pour qu'un usage du socle sans news n'ait pas à l'installer.

### 2.4 `newsdb` — la base de news partagée

Le tracker `news/btc_news.py` savait seul scorer un article et où vivent les
news ; le panneau du terminal ne pouvait que lire une base en espérant que
quelqu'un l'ait remplie. `newsdb` tient ce que les deux partagent :

| Couche | Contenu |
|---|---|
| Schéma | tables `news` et `fear_greed`, `init_db`, `connect` (lecture seule) |
| Scoring | `KEYWORDS`, `MIN_SCORE`, `score_article`, `detect_sentiment` |
| Collecte | `collect_rss`, `collect_cryptopanic`, `record_fear_greed` |
| Lecture | `latest`, `last_fear_greed` |
| Boucle | `NewsCollector` — collecte périodique en thread démon |

Aucune fonction n'affiche quoi que ce soit : ce qu'elles trouvent et ce qui
échoue passe par des rappels (`on_new`, `on_error`), que le tracker branche sur
ses `print` en couleurs et que le terminal branche sur son bandeau d'état.

`NewsCollector` existe parce que le terminal ne peut pas attendre six flux RSS
dans un callback Dash : la collecte tourne dans son propre thread, avec sa
propre connexion SQLite — une connexion n'étant pas partageable entre threads —
et les panneaux se contentent de lire. L'état de la dernière tournée est publié
dans `status`, ce qui permet à la barre de titre d'afficher son âge, et de dire
quand elle échoue.

**Au réveil**, la tournée n'attend pas la fin de son intervalle (`nudge`). Elle
attendait un quart d'heure en temps monotone, que le sommeil n'avance pas : le
7 novembre 2026, la machine a dormi de 00:40 à 06:18, et le fil a gardé six
heures de retard pendant les dix premières minutes de la matinée — celles où
l'on ouvre le terminal. La boucle d'observation, qui voit le réveil (§2.7),
relance la collecte dès le réseau revenu. Le titre dit l'âge court (« 5 h 45 »
et non « il y a 345 min », qui poussait le titre contre ses onglets) et passe
en orange au-delà de deux intervalles de retard.

### 2.5 `liquidations` — le fil des positions fermées de force

Une position à levier qui ne couvre plus sa marge est fermée au marché
par la plateforme. Ces fermetures arrivent par rafales, et ces rafales
expliquent une partie des mèches du graphique du cours.

`LiquidationFeed` écoute `!forceOrder@arr` — toutes les paires, sans clé —
et garde une **fenêtre glissante** de 500 événements en mémoire :
l'indicateur de tension du moment. Chaque événement retenu est aussi
signalé par un rappel `on_event`, où le hub branche le journal (§2.7) —
la fenêtre nourrit le panneau, le journal la séance. Le fil expose les derniers événements, les totaux par côté sur une
heure, et la part des paires Bitcoin dans ce total, qui distingue une
cascade locale d'une cascade de marché.

Cinq points méritent attention.

**Le sens.** Une vente forcée ferme une position *longue*, un achat forcé
une position *courte*. L'inverser donnerait un panneau qui raconte le
contraire de ce qui se passe ; `tests/test_liquidations.py` le vérifie,
faute de pouvoir compter sur l'arrivée d'un événement au bon moment.

**Deux sources, un magasin.** Binance tait ses flux WebSocket futures
depuis certains pays (§2.9) : le fil se connecte, s'abonne, et n'entend
rien. `BybitLiquidationConnector` écoute donc aussi le canal
`allLiquidation` de Bybit — par paire, sans joker, d'où la liste
`BYBIT_SYMBOLS` des dix plus grosses capitalisations — et verse ses
événements dans le **même** magasin par `record`, l'entrée commune.
Chez Bybit, `S` est le côté de la *position* (`Buy` = long liquidé),
l'inverse de la convention Binance ; chaque `Liquidation` porte son
`exchange`, que le panneau étiquette et que le journal conserve. Les
totaux — et l'alerte de rafale (§2.8) — additionnent les deux
plateformes. Le fil publie son état **par lien** (`links`, `missing`) :
`connected` vaut dès qu'un lien tient, et le badge du panneau nomme
celui qui manque plutôt que d'annoncer un flux coupé.

**Un lien peut tenir sans rien livrer.** C'est précisément le cas de
Binance depuis certains pays : le lien s'ouvre, s'abonne, et l'état de
connexion n'a rien à redire — le badge disait « sans Bybit » quand Bybit
tombait, et rien du tout quand Binance se taisait, Bybit portant le
panneau seul. Le fil retient donc, par lien, l'heure du **dernier
événement** (`last_seen`, nourri par `record` et par `restore` — un
événement relu du journal dit aussi quand la plateforme a parlé pour la
dernière fois) et celle de l'**ouverture** du lien (`since`, posée par
`mark` au passage à connecté). `last_event_age(link)` compte le silence
depuis le plus récent des deux — un lien qui vient de se rouvrir n'est
pas muet, même si son dernier mot date — et `silent(threshold)` nomme
les liens ouverts dont le silence dépasse le seuil. Le panneau le fixe
à un quart d'heure (`SILENCE`) : Binance diffuse toutes ses paires, et
quinze minutes sans une seule liquidation ne s'y voient pas en marché
ouvert. Le badge écrit alors « Binance muet depuis 22 min » en jaune,
pas en rouge — le fil vit encore par l'autre plateforme, et l'âge
affiché laisse juger si c'est le pays ou le marché qui se tait.

**Le redémarrage ne vide plus le panneau.** La fenêtre ne vivait qu'en
mémoire : relancer le service la perdait, et l'on retrouvait un panneau
vide juste après une cascade — ce qui se lit comme une panne du flux
alors que le journal, lui, gardait tout. `MarketHub.start` appelle donc
`_warm_liquidations` avant d'ouvrir la moindre connexion : la dernière
heure du journal (`WARM_UP_SECONDS`, la fenêtre des totaux du panneau)
est rendue au fil par `restore`, l'entrée jumelle de `record` qui
n'appelle **pas** `on_event` — les réémettre les réinscrirait au
journal, et chaque redémarrage doublerait l'historique. La relecture
précède les connecteurs pour que la fenêtre reste chronologique. Une
conséquence assumée : une rafale encore dans les cinq dernières minutes
au redémarrage refait sonner l'alerte (§2.8), la condition étant
réellement vraie.

**La boucle de reconnexion est celle des connecteurs.** `LiquidationFeed`
hérite d'`ExchangeConnector`, dont le carnet est devenu optionnel pour
l'occasion : un flux qui n'alimente aucun carnet publie son état de
connexion lui-même, en redéfinissant deux marqueurs, et hérite du reste —
backoff exponentiel plafonné, remise à zéro après une connexion qui tient.

**Une troisième source, OKX** (`OKXLiquidationConnector`). Le 7 novembre 2026
au matin, le panneau SOURCES a montré les deux liens « muets » depuis une heure :
Binance, qui tait son flux futures depuis le Cambodge, et Bybit, qui ne suit que
dix paires. Rejoués à part pendant trois minutes, Bybit n'a rien livré et OKX
dix liquidations — le terminal ne reposait plus que sur un fil qui pouvait se
taire. Le canal `liquidation-orders` d'OKX couvre tous ses perpétuels d'un
abonnement. Il compte en contrats : un linéaire vaut `ctVal` unités du
sous-jacent (0,01 BTC pour BTC-USDT), un inverse `ctVal` dollars (100 pour
BTC-USD) — la table des 543 contrats est lue à la connexion. À l'abonnement,
OKX rejoue des liquidations anciennes, de la veille au soir au premier essai :
tout ce qui précède l'ouverture du lien est écarté, sans quoi chaque
reconnexion les reverserait au journal. OKX coupe une connexion muette depuis
trente secondes : le connecteur lui envoie son « ping » toutes les vingt.

**Ce qui est du bitcoin** (`is_bitcoin`) : un contrat sur le BTC coté en dollars
— `BTCUSDT`, `BTCUSDC`, l'inverse `BTCUSD` d'OKX —, échéances comprises. La
règle précédente, « commence par BTC », comptait `BTCDOMUSDT`, le contrat de
Binance sur l'indice de dominance, qui cote quelques milliers de points : il
gonflait le « dont BTC » du panneau et aurait étiré l'axe de la carte des
liquidations. Le fil et la carte partagent désormais la même règle.

### 2.6 `macrocal` — le calendrier macro tenu à la main

Aucune source publique satisfaisante n'existe pour un calendrier
économique : les API ouvertes sont payantes ou sans licence claire. Mais
les émetteurs publient eux-mêmes leurs dates longtemps à l'avance — la
Fed donne ses réunions deux ans avant, l'OMB publie chaque automne le
calendrier de l'année suivante pour toutes les statistiques fédérales.
`macrocal` est la transcription de ces calendriers officiels, vérifiée à
la source : décisions du FOMC (avec le marqueur SEP des réunions à
projections), CPI, rapport emploi (NFP) et inflation PCE, 2026 et 2027.

L'année 2027 des statistiques a été ajoutée le 6 novembre 2026, cinq
semaines avant que la liste ne s'épuise : CPI et NFP depuis les pages de
calendrier du BLS, qui refuse les clients en ligne de commande (403) et se
lit au navigateur — deux lectures du CPI concordantes, les deux dates de
2026 déjà connues retrouvées, et les douze NFP le vendredi ; PCE depuis le
fichier JSON des dates du BEA (`apps.bea.gov/API/signup/release_dates.json`),
qu'on pourrait lire au démarrage le jour où la transcription coûterait plus
qu'une fois l'an. Le même fichier montre que le BEA a avancé ses publications
de janvier à avril 2026 pour rattraper 2025 ; passées, elles sont laissées
comme le calendrier initial les donnait.

Le module expose `EVENTS` (la liste triée), `upcoming` (l'à-venir,
événement du jour compris — « aujourd'hui » est précisément ce qu'on
vient lire), `next_of` (le prochain d'une famille, pour les badges),
`last_dates` (jusqu'où court chaque famille) et `last_date` (jusqu'où la
liste est complète : la plus proche des quatre).

Deux pièges justifient que ce soit du code et non un simple tableau :

- **Le fuseau.** Les publications sont définies en heure de New York
  (8 h 30 pour les statistiques, 14 h pour le FOMC), et l'heure d'été
  américaine ne commence ni ne finit avec l'européenne : deux fois par
  an, le décalage avec Bruxelles change pendant deux à trois semaines.
  `when_utc` convertit via `zoneinfo`, et le panneau affiche l'heure de
  la machine — **et son jour**. Il écrivait le jour de New York à côté de
  l'heure locale : vu d'un fuseau en avance d'une demi-journée, le FOMC du
  mercredi 9 décembre 2026 s'affichait « mer. 9 déc. 02:00 » quand il tombe
  le jeudi 10 à 2 h, et le CPI de la veille au soir restait « auj. », en
  rouge, toute la matinée du lendemain. Jour écrit, compte à rebours et
  « aujourd'hui » se comptent désormais dans le fuseau de la machine ; une
  publication dont l'heure est passée reste listée jusqu'à minuit, mais se
  dit passée, et le titre regarde la suivante
  (`terminal/panels/calendar.py::render`, pur, que `test_macrocal.py`
  appelle depuis Phnom Penh et depuis New York).
- **L'épuisement.** Une liste tenue à la main finit par se périmer ;
  plutôt que de se taire, le panneau affiche l'horizon de la liste et
  prévient quand il approche — le pendant du fil de news qui affiche
  l'âge de sa collecte. Chaque famille s'épuise à son rythme, et le pied
  nomme celle qui s'arrête la première. `last_date` rendait le dernier
  FOMC, décembre 2027, quand le CPI, le NFP et le PCE s'arrêtaient en
  décembre 2026 : l'avertissement ne pouvait pas sonner, et l'alerte
  d'échéance se serait tue sur trois des quatre familles sans que rien
  ne le dise.

### 2.7 `journal` — la séance se relit

Carnets, écarts d'arbitrage et liquidations vivaient en mémoire et
mouraient avec le processus. `Journal` persiste dans
`~/.btcterm/journal.db` (SQLite, 30 jours de rétention, purge au
démarrage) les deux données qui valent d'être relues, chacune selon sa
nature :

- une **liquidation** est un événement — une ligne par événement,
  écrite au fil de l'eau par le rappel `on_event` du fil (§2.5), avec
  la plateforme qui l'a fermée (colonne `exchange`, ajoutée par migration) ;
- une **opportunité d'arbitrage** est un état qui dure — la journaliser
  à chaque balayage rempilerait la même paire dix fois par seconde. Le
  journal tient des **épisodes** : ouvert quand une paire devient
  rentable, tolérant 30 s de flottement (les écarts clignotent au
  rythme des carnets), écrit en une seule ligne à sa clôture — bornes,
  meilleur profit net, prix à ce meilleur, nombre d'observations.

C'est le hub qui fait vivre les épisodes, dans une boucle d'observation
propre à cadence lente (1 s) : aucun callback d'interface ne pouvait
s'en charger — il n'en tourne aucun sans navigateur ouvert, et le
journal doit couvrir la séance entière. L'arrêt du hub clôt et écrit
les épisodes encore ouverts ; une panne d'écriture ne remonte jamais
jusqu'au flux ni à la boucle, qui retentera au balayage suivant. Au
démarrage, la boucle attend le réseau avant son premier tour
(`_wait_for_network`, au plus une minute, sondé toutes les deux
secondes vers une adresse plutôt qu'un nom) : l'unité utilisateur part
avant que la machine ne soit connectée — `network-online.target`
n'engage rien dans un gestionnaire utilisateur —, et le premier tour
déclarait cinq sources en panne deux secondes après le boot, dix lignes
de journal pour une minute de réseau absent. L'attente et sa fin
tiennent en une ligne chacune ; passé le délai, la boucle part quand
même, ses sources disant elles-mêmes ce qui manque. Le réveil de la
machine rejoue le boot : la boucle dort sur `Event.wait`, en temps
monotone, que le sommeil n'avance pas, et son premier tour partait à la
seconde même du réveil — sept salves de « sources en panne » en une
journée, chacune à l'heure exacte où `systemd-sleep` dégelait
`user.slice`. `_after_sleep` rattend donc le réseau à chaque réveil, la
ligne de journal disant combien la machine a dormi. Le réveil se lisait
d'abord à l'heure murale qui saute de plus de trente secondes entre deux
tours ; un sommeil de onze secondes, le 6 novembre 2026, est passé
au-dessous, et l'instantané de marché est parti avant le réseau — deux
sources « en panne » cinq minutes au bandeau, jusqu'à l'instantané
suivant. Il se lit désormais sur les horloges du noyau (`SleepWatch`) :
`CLOCK_BOOTTIME` court pendant le sommeil et l'horloge monotone non, et
leur écart est le sommeil cumulé, exact à la microseconde et sourd aux
réglages de l'heure. Toute lecture du cache peut échantillonner la
montre — le premier qui voit le sommeil date le réveil, et la boucle le
reconnaît à sa date si un autre thread l'a vu avant elle (§4.3).

La base n'existe qu'à la première écriture : construire un `Journal` —
ce que fait tout `MarketHub`, démarré ou non — ne crée aucun fichier,
et les tests ne laissent aucune trace. `--no-journal` dispense le
terminal d'en tenir un. La séance se relit à même la ligne de
commande :

```bash
python -m btcterm.journal --heures 6
python -m btcterm.journal --heures 96    # au-delà d'un jour, les heures sont datées
```

La commande suit ce que le journal a appris à tenir (`resume`, que les
tests appellent sur une base d'essai) : elle ouvre sur la **couverture** —
ce que la fenêtre a vraiment vu, interruptions comptées, sans quoi un
total de liquidations sur 96 h en vaut 28 —, donne le bilan des gros
ordres comme le panneau JOURNAL, et lit la dominance sur la fenêtre
demandée plutôt que sur tout l'historique.

**Un univers de capitalisation qui ne clignote pas.** Constaté le
11 novembre 2026, en donnant à la tendance de dominance une échelle où
elle se lit : quatre-vingt-sept fois en deux mois et demi, la
capitalisation totale gagnait ou perdait 1,1 % d'un instantané au suivant
— 29 Md$ — sans que celle du bitcoin bouge, et la dominance sautait de
0,4 à 0,7 point pour rien, près de la moitié du seuil de l'alerte de
glissement (§2.8). L'écart est la taille de `FIGR_HELOC`, un fonds de
prêts mis en jetons que CoinGecko range par intermittence parmi ses dix
premières capitalisations. Deux réponses, une par époque :

- **à la collecte**, `sources.market_global` retire les actifs
  d'`INTERMITTENT` du total et recalcule les parts ; la colonne
  `cap_excluded` de l'instantané dit la part retirée (zéro quand la source
  ne les comptait pas) ;
- **à la lecture**, pour les lignes d'avant (`cap_excluded` à NULL),
  `rebase_universe` reconnaît un changement d'univers à ce que le total
  saute sans le bitcoin — l'écart entre la variation des autres
  capitalisations et la sienne dépasse 1,5 %, quand le marché n'a jamais
  fait plus de 1,0 % entre deux instantanés voisins — et ramène les
  périodes écrites « avec » à l'univers « sans ». Rien n'est réécrit dans
  la base. Après un trou de séance, un pas n'est retenu que s'il défait le
  précédent : en quelques heures, le marché peut diverger pour de bon.

Sur le journal réel, 85 des 87 sauts disparaissent ; les deux qui restent
sont un second actif entré une fois, et un écart après trois heures de
veille que rien ne permet de distinguer d'un vrai mouvement. `hub.
market_snapshots` (tendance, alertes) et la synthèse lisent la série
rebasée (`Journal.snapshots_rebased`).

Le journal tient aussi la table des **alertes** (§2.8), une ligne par
sonnerie : relire une séance, c'est aussi relire ce qui a sonné.

La relecture a deux fenêtres : la CLI ci-dessous, et le **panneau
JOURNAL** (onglet de la cellule d'arbitrage) — alertes sonnées,
épisodes rentables et bilan des liquidations des dernières vingt-quatre
heures, la profondeur de l'historique d'instantanés dans la barre de
titre. C'était le dernier usage qui obligeait à quitter le terminal
pour une ligne de commande.

S'y ajoutent les **instantanés de marché** — dominance, parts de
capitalisation, open interest, financement —, la seule table qui ne
relit pas la séance mais construit un historique : CoinGecko réserve ces séries à
son offre payante et Binance ne garde que trente jours d'open interest,
alors la boucle d'observation en journalise un toutes les cinq minutes
(`SNAPSHOT_EVERY`), composé de ses deux sources qui échouent
indépendamment — un instantané partiel s'écrit, colonnes manquantes à
NULL ; hors ligne, rien ne s'écrit. Leur rétention est longue à dessein
(`SNAPSHOT_RETENTION_DAYS`, 400 jours contre 30) : purger ces lignes au
rythme de la séance détruirait ce que leur accumulation devait bâtir.
Trois lecteurs côté hub : `market_snapshots()` rend l'historique en
DataFrame — la tendance du panneau dominance —, et
`open_interest_extended()` comme `funding_history_extended()`
prolongent les séries Binance vers le passé sur les instantanés,
rééchantillonnés au pas de leur source (4 h ; 8 h sur la grille des
règlements, étiquette à droite — le dernier relevé avant l'échéance
reconstitue le taux réglé) pour une couture invisible. Une base
antérieure à une colonne s'élargit par ALTER TABLE à l'ouverture :
l'historique accumulé n'est jamais perdu. L'écriture nomme ses colonnes :
une base élargie par une version intermédiaire les a dans l'ordre de ses
ALTER TABLE, pas forcément dans celui du code, et l'insertion par position
qui la précédait les aurait mêlées sans erreur.

Depuis le 6 novembre 2026, l'instantané note aussi le **marché des
options** (§2.11) : DVOL, volatilité à la monnaie de l'échéance à un mois,
skew 25 delta, put/call et base à trois mois. Deribit ne sert gratuitement
l'historique que de DVOL ; les quatre autres n'existent qu'au présent, et
chaque jour sans les noter était perdu — c'est la vue HISTO du panneau
options qui les relit (§3.16). La chaîne pèse 43 Ko compressée, soit une
douzaine de mégaoctets par jour à ce rythme. Comme les autres colonnes, ne
s'écrit que ce que la dernière lecture a rendu frais. L'essai a trouvé au
passage que le résumé des options ne rendait pas la base des futures quand
la chaîne d'options manquait, alors qu'elle n'en dépend pas.

**La séance interrompue.** Sept jours de journal ont montré trente trous
de plus de dix minutes entre deux instantanés, jusqu'à 447 minutes
d'affilée — et pendant ces trous, ni épisode observé ni liquidation
écrite : tout le processus s'arrêtait, pas un thread. Le journal
système a nommé la cause, `systemd-sleep` : la machine dort, le
terminal avec elle, et la séance reprend au réveil. Rien à réparer,
mais quelque chose à dire — les courbes bâties sur les instantanés
tiraient un trait par-dessus sept heures de sommeil, comme si le marché
avait été observé. `interruptions_between` (et `find_gaps`) nomme les
trous de plus de `SNAPSHOT_GAP` (trois cadences, un instantané manqué
n'en est pas un) ; `market_snapshots()` insère une ligne de rupture —
toutes valeurs à NaN, colonne `gap` — juste après le dernier instantané
avant chaque trou, et la courbe de dominance la garde au lieu de
l'écarter : plotly rompt le trait sur un NaN. Les séries prolongées
gardent leurs tranches vides à NaN, que `frame_to_lines(…,
whitespace=True)` sérialise en points blancs, `{time}` sans valeur — la
forme que Lightweight Charts réserve aux trous. Le panneau journal
compte les interruptions de la fenêtre et leur durée dans sa barre de
titre. Et le réveil ne paie plus les pannes d'avant : le compteur de
backoff des connecteurs repart de zéro après une connexion qui a tenu
(§2.2).

`tests/test_journal.py` déroule la vie d'un épisode au temps simulé —
ouverture, meilleur profit, flottement toléré, clôture après la grâce —
et protège les frontières : panne du rappel sans effet sur le flux,
aucun fichier créé sans écriture, rétention appliquée — la double
rétention des instantanés, leur écriture partielle et le prolongement
de l'open interest compris.

### 2.8 `alerts` — le terminal sait attirer l'attention

Douze panneaux qu'il faut balayer des yeux couvraient la surveillance
active ; la passive demande l'inverse — que le terminal prévienne.
`AlertEngine` évalue quatorze règles dans la boucle d'observation du hub
(1 s), toutes nourries par ce que le hub tient déjà, sans aucune
connexion nouvelle :

- **seuils de cours** posés par l'utilisateur — le sens (au-dessus,
  au-dessous) est figé à la pose par rapport au cours du moment, ou au
  dernier connu : poser un seuil n'exige pas un flux vivant à l'instant
  du clic. Un seuil qui sonne se désarme, et ne se réarme que quand le
  cours s'en écarte de 0,2 % de l'autre côté — sans cette hystérésis,
  un cours qui oscille sur le seuil sonnerait en rafale ;
- **rafale de liquidations** — notionnel liquidé sur 5 minutes au-delà
  du seuil ;
- **financement extrême** — |taux par 8 h| au-delà du seuil ;
- **news à fort score** — un article jamais vu atteint le seuil ; la
  première lecture arme sans sonner, sans quoi chaque démarrage
  rejouerait les gros titres de la veille ;
- **écart d'arbitrage** — meilleur net du balayage au-delà du seuil.

S'y ajoutent trois règles **relatives**, assises sur les indicateurs
que le panneau prix calcule déjà — mêmes chandeliers horaires, mêmes
formules (§2.1), lues sur la dernière bougie *close* pour ne pas sonner
sur le flottement de la bougie courante :

- **écart à la MA 200** — le cours s'étire au-delà du seuil (en %) de
  sa moyenne à 200 heures, dans un sens ou l'autre ;
- **RSI extrême** — le RSI horaire sort des bornes posées, chaque borne
  avec son front ; un couple incohérent venu du localStorage (survente
  au-dessus du surachat) retombe entier sur les défauts ;
- **signal gradué fort** — un ±2 de `graded_signals` apparaît sur la
  bougie close : une sonnerie par bougie au plus, c'est un événement
  daté, pas un état. Débrayable d'une case.

Ces trois règles se taisent sur la série de démonstration : hors
ligne, des extrêmes calculés sur une marche aléatoire seraient du
bruit déguisé en information.

Une neuvième s'assoit sur l'historique que le journal accumule (§2.7) :

- **glissement de dominance** — la part du BTC s'est déplacée de plus
  du seuil (en points) sur vingt-quatre heures, une rotation du marché
  invisible d'un instantané seul. La règle se tait tant que
  l'historique local ne couvre pas la fenêtre : c'est la première à ne
  pouvoir exister qu'à l'usage.

Deux autres lisent les collecteurs de contexte que les panneaux ETF et
on-chain tirent déjà — mêmes caches, aucune requête de plus :

- **flux ETF** — le flux net du dernier jour publié dépasse le seuil,
  en entrée comme en sortie. Un jour de flux est un événement daté :
  une sonnerie par date au plus, la date n'étant retenue qu'au moment
  où elle sonne — farside remplit son jour au fil de la soirée,
  émetteur après émetteur, et le total peut franchir le seuil bien
  après l'apparition de la ligne. Le jour présent à la première lecture
  est tenu pour vu, comme les gros titres de la première lecture des
  news ;
- **réseau chargé** — le mempool dépasse le seuil (Mo), ou le rythme
  moyen des blocs s'étire au-delà du seuil (minutes) ; chaque grandeur
  a son front. Un rythme qui s'étire dit que le réseau a perdu des
  mineurs depuis le dernier ajustement — ce que le panneau on-chain
  colore déjà en orange.

Une autre lit le marché des options (§2.11), dont la règle tient le cache
chaud à la minute — c'est sur lui que le bandeau lit DVOL sans réseau :

- **volatilité implicite en hausse** — DVOL monte de plus du seuil (six
  points par défaut) au-dessus de sa clôture de la veille. Le seuil vient de
  l'année écoulée : une telle hausse n'est arrivée que dix jours, dont le
  31 janvier, le 3 et le 5 février 2026 — la semaine du krach, où DVOL est
  passé de 53 à 90 dans la journée. Une sonnerie par montée : la condition tient le reste du
  jour, le front ne sonne qu'une fois.
- **déplacement du skew** — l'écart des ailes 25 delta à un mois s'est
  déplacé de plus du seuil (deux points par défaut) sur vingt-quatre heures,
  lu sur les instantanés du journal comme la dominance ; le message dit le
  sens — vers le bas, les puts se renchérissent. Un écart et non un niveau :
  le niveau dérive avec le régime, et il aurait fallu des semaines
  d'historique pour l'étalonner. Le seuil reste provisoire : sur les cinq
  premiers jours journalisés (6-11 novembre 2026, marché calme), l'écart sur
  24 h n'a pas dépassé 1,4 point. Deux gardes : chaque bout est la médiane
  de trois instantanés — au retour d'une panne de Deribit, le 10 novembre,
  un instantané isolé a donné −6,0 entre deux lectures à −3,1 —, et les deux
  bouts doivent être frais, le présent dans la demi-heure, la référence
  entre 23 et 30 heures : après une longue veille, la règle se tait.

Les deux dernières sont des événements datés, comme le signal gradué,
mais tirés de ce que le bilan du panneau ANALYSE (§3.14) et le
calendrier (§2.6) ont appris au terminal :

- **tendance journalière** — la clôture journalière passe de l'autre
  côté de sa MA 200, la seule lecture que le bilan n'ait pas démentie.
  Elle est lue sur la dernière bougie journalière *close*, et se tait
  sur la démonstration comme sur un historique périmé ;
- **échéance proche** — un FOMC, un CPI, un NFP ou un PCE du calendrier
  tombe dans moins de trente minutes (`EVENT_LEAD`) : un tel chiffre
  bouge le cours en quelques secondes, et c'est avant qu'il faut le
  savoir.

Toutes deux portent leur date dans leur message : relues du journal au
redémarrage (voir plus bas), elles ne sont pas rejouées. Chacune se
débraye d'une case, comme le signal gradué — qui reste armé par défaut
malgré ce que le bilan en dit : c'est un réglage de l'utilisateur, pas
un défaut du terminal.

Les règles d'état sonnent sur le **front montant** et pas avant un
délai de garde de 10 minutes : une condition qui dure ne sonne qu'une
fois, une condition qui clignote ne sonne pas en rafale. Les contrôles
qui coûtent (financement en cache REST, lecture SQLite des news,
lecture technique des chandeliers) ne tournent qu'à la minute. Chaque
sonnerie part au journal (§2.7) — la relecture d'une séance inclut ce
qui a sonné.

**Les sonneries survivent au redémarrage.** La mémoire du moteur — les
deux cents dernières sonneries, d'où le panneau tire sa liste et la
cloche son compte de l'heure — ne vivait qu'en mémoire : un service
relancé, ou une machine qui se réveille et le relance, affichait
« aucune alerte » quand la nuit avait sonné dix fois, et le journal
seul le savait. `MarketHub._warm_alerts`, appelé avec
`_warm_liquidations` avant d'ouvrir la moindre connexion, relit la
journée du journal (`ALERTS_WARM_UP_SECONDS`) et la rend au moteur par
`restore` — même contrat que le fil des liquidations : rien n'est
réécrit, l'état des règles (fronts, seuils armés) n'est pas touché, une
lecture qui échoue laisse la mémoire vide. Le navigateur, lui, ne fait
sonner que ce qui est plus récent que sa dernière trame
(`_alertsLast`) : une sonnerie relue s'affiche sans sonner, page
ouverte pendant le redémarrage ou non.

Le moteur ne connaît pas Dash : il reçoit le hub en paramètre, comme
les fonctions de rendu des panneaux, et `tests/test_alerts.py` le
déroule au temps simulé sur un hub factice — hystérésis, fronts,
cadences, journalisation, un jour de flux qui se remplit puis grossit
sans resonner, et la normalisation des réglages venus du localStorage. Le test garde aussi la normalisation contre un bug
réellement rencontré : une copie superficielle des défauts partageait
la liste des seuils, et le premier seuil posé mutait les réglages par
défaut de tout le processus.

**Jusqu'au téléphone** (`btcterm/notify.py`). Le bip et la notification du
navigateur ne sonnent que dans un onglet ouvert ; sur un serveur, personne ne
le regarde. Chaque sonnerie part aussi, si un sujet est configuré
(`BTCTERM_NTFY`, ou `btcterm --ntfy`), vers ntfy, qui la livre en
notification push : un POST JSON à la racine du serveur, le sujet dans le
corps — un en-tête HTTP ne porte pas proprement l'UTF-8 d'un titre accentué —,
avec un titre lisible, une priorité relevée pour ce qui demande d'agir (seuil
de cours, rafale de liquidations, bond de la volatilité, bascule de tendance)
et une étiquette par règle. L'envoi part d'un thread à lui, par une file
bornée : la boucle d'observation n'attend jamais ntfy, et une panne d'envoi se
dit une fois au journal, comme une source. Les sonneries relues du journal au
démarrage ne repartent pas. Le panneau ALERTES dit le serveur servi, jamais
le sujet, qui fait office de mot de passe ; `python -m btcterm.notify`
envoie un essai.

Côté interface, le panneau ALERTES (onglet de la cellule news) affiche
les sonneries et les réglages, qui vivent dans un Store persisté et
réarment le moteur au chargement ; la cloche du bandeau compte la
dernière heure et **ouvre le panneau d'un clic** — elle demande à la
grille (`reveal`) dans quelle cellule le panneau a été rangé, et quitte
au passage un éventuel plein écran qui le masquerait — DVOL, au bandeau, fait
de même pour le panneau options (`REVEALS` dans `terminal/header.py`) ; un bip et une notification navigateur (permission
demandée d'un geste) partent d'un callback clientside sur le fil global
— la sonnerie retentit même panneau replié, et un rechargement ne
rejoue rien.

### 2.9 `resolver` — quand le DNS ment

Dans certains pays, le résolveur du fournisseur d'accès ne refuse pas les
domaines des exchanges : il y répond **127.0.0.1**. Depuis le Cambodge,
`api.binance.com`, `fapi.binance.com`, `stream.binance.com`,
`stream.bybit.com`, `ws.okx.com` et `advanced-trade-ws.coinbase.com`
résolvent tous en bouclage ; la connexion est refusée sur place, sans
sortir de la machine, et le terminal tombe sur sa série de démonstration
sans que le symptôme désigne la cause. Les serveurs, eux, répondent dès
qu'on leur parle par leur vraie adresse : seul le DNS ment.

En octobre 2026, le mensonge a changé de forme : les mêmes noms résolvent
en `203.190.164.182`, adresse publique qui ne répond pas. Rien ne l'a
signalé, parce que les connexions déjà ouvertes ne repassent pas par le DNS :
le service a tourné normalement jusqu'au redémarrage suivant, qui a perdu
d'un coup l'accès à quatre plateformes sur cinq, chandeliers compris. Une réponse publique
ressemblant à une réponse saine, aucune détection générale n'est possible.
Lister ces adresses dans `SINKHOLES` ne protège que de la dernière panne : les
noms des cinq plateformes (`GUARDED_DOMAINS`) sont donc **toujours** demandés
à DoH, quoi que dise le système. Si DoH se tait, la dernière réponse saine reste
servie, même périmée, et il n'est retenté qu'après `DOH_RETRY`.

`btcterm/resolver.py` enveloppe `socket.getaddrinfo`. Quand le nom est celui
d'une plateforme, ou quand la réponse du système pour un nom public ne contient
que des adresses de bouclage, nulles ou de `SINKHOLES` — ou quand le nom
n'existe soudain plus —, le nom est redemandé à
un résolveur DNS sur HTTPS joint **par son adresse IP** (`1.1.1.1`, puis
`8.8.8.8`), de sorte qu'aucune résolution empoisonnable n'entre dans la
boucle ; les entrées `getaddrinfo` sont reconstruites à partir des
adresses obtenues, en respectant la famille et le type de socket demandés,
et gardées le temps de leur TTL borné (30 s à 1 h). Tout le reste passe tel
quel : un nom sain coûte une comparaison, une adresse littérale et
`localhost` gardent le droit de valoir 127.0.0.1. `requests` (urllib3)
comme `websockets` (`loop.getaddrinfo`) cherchent `socket.getaddrinfo` au
moment de l'appel : l'enveloppe, posée par `hub.start()` avant l'ouverture
des connexions, vaut pour les collecteurs REST et les connecteurs
WebSocket sans qu'ils en sachent rien. Chaque nom sauvé est signalé une
fois, en avertissement.

Ce que ce module ne peut pas : Binance ne livre aucune donnée sur ses flux
WebSocket **futures** (`fstream.binance.com`) depuis certains pays — le
handshake passe, l'abonnement est acquitté, le ping répond, et rien
n'arrive. Le fil des liquidations (§2.5) n'entend donc pas Binance depuis
le Cambodge — c'est Bybit qui le nourrit là-bas —, alors que le REST
futures (`fapi`) répond normalement. Le remède de fond — un résolveur DNS sur TLS configuré dans systemd-resolved,
donné dans le README — couvre aussi le navigateur et les satellites.

### 2.10 `history` — une bougie close ne se redemande pas

Ouvrir le panneau prix prenait environ 480 ms, dont 360 à 425 de réseau :
indicateurs (23 ms) et sérialisation (50 ms) ne pesaient presque rien. On
y rapatriait surtout des bougies qui ne changeront plus, et un redémarrage
pendant une panne de Binance retombait sur la série de démonstration, faute
du moindre souvenir.

`btcterm/history.py` garde les bougies closes dans `~/.btcterm/klines.db`, une
base à part du journal : celle-ci se supprime sans rien perdre d'irremplaçable.
Binance ne fournit plus que ce qui manque.

- **Vers le présent**, tout ce qui suit la dernière bougie sauvegardée, page
  après page si l'absence a été longue. Le même appel ramène la bougie en cours.
- **Vers le passé**, une page entière de 1000 bougies dès qu'on sort de ce qui
  est sauvegardé : elle sert les pans suivants.

Deux règles tiennent la série juste. Seules les bougies **réglées** s'écrivent,
une minute après leur clôture (`SETTLE_MS`) : la bougie en cours bouge, et une
horloge locale en avance figerait sinon sur disque une bougie pas tout à fait
finie. Et la plage sauvegardée n'a **pas de trou**, puisqu'elle ne s'étend que
par ses deux bouts : un écart entre deux bougies y est un vrai silence de
Binance, une maintenance, et aucune table de couverture n'est nécessaire. Un
contrôle d'écart entre bougies aurait au contraire signalé ces silences à tort.

Le cache mémoire du hub reste devant, inchangé : le mémo du pousseur tient à
l'identité de l'objet servi (§3.10). Seul son producteur change, et seule la
première lecture de chaque intervalle coûte une page.

Le rattrapage vers le présent est borné à `MAX_CATCHUP_PAGES` (cinq pages) :
au-delà, la série sauvegardée de l'intervalle est oubliée et repart du présent,
d'une page. Sans borne, la minute laissée un mois demandait quarante-trois
pages à Binance avant d'afficher quoi que ce soit. Oubliée plutôt que trouée :
la plage reste d'un seul tenant. Les intervalles longs ne l'atteignent jamais —
cinq pages font sept mois d'horaire, que les alertes relisent à la minute, et
treize ans de quotidien.

La base est liée à `keep_journal` pour une raison précise : plusieurs tests
remplacent `sources.fetch_klines` par des bougies fabriquées, et ces hubs
construits sans journal ne doivent pas les écrire dans la vraie base — la
mésaventure du journal, dont les fausses liquidations ressortaient au panneau,
ne se rejoue pas ici. Une base inutilisable (disque plein, fichier abîmé) est
abandonnée pour la séance : ce n'est qu'un cache, et le panneau revient à la
lecture directe.

Les chandeliers passent par une connexion gardée ouverte : réduit à la fin de
la série, l'appel ne valait plus que sa poignée de main TLS (337 ms contre
110). Les autres collecteurs ont suivi le 7 novembre 2026 : une seconde session
(`_http`) leur garde, à chacun de leurs hôtes, la connexion d'un appel à
l'autre, quand chaque `requests.get` en ouvrait une neuve, certificats
rechargés compris. Mesuré sur le ticker de Binance, que le bandeau relit toutes
les cinq secondes : 251 ms et 18,8 ms de processeur par appel, 132 ms et 3,4 ms
une fois la connexion gardée. Celle-ci ne refait pas d'essai sur un délai
dépassé — elle doublerait l'attente des sources lentes — alors que celle des
chandeliers en refait un ; elle rejoue en revanche, une fois et sur une
connexion neuve, la lecture qu'une connexion gardée a rompue
(`_ReprendRupture`) : la rupture se dit tout de suite, la reprise ne coûte
pas un délai de plus. Le réveil de la machine oublie les deux
(`reset_connections`) : mortes sans l'avoir dit, elles feraient attendre la
lecture suivante jusqu'au bout de son délai.

### 2.11 `options` — ce que les prix d'options disent du futur

Le carnet, le perpétuel et les liquidations disent ce que le marché *fait* ; les
options disent ce qu'il *craint* et ce qu'il paie pour s'en couvrir. Le module
lit une photographie de Deribit — la plateforme qui tient l'essentiel de
l'open interest des options bitcoin, publique et sans clé — et n'en tire que
des fonctions pures, testées sans réseau (`tests/test_options.py`) :

- `option_chain` met les lignes brutes de `get_book_summary_by_currency` en
  table : échéance (08:00 UTC, l'heure de Deribit), strike, sorte,
  volatilité de marque, open interest en BTC, forward de l'échéance ;
- `term_structure` lit le **sourire** de chaque échéance sur ses seules
  options hors de la monnaie — puts sous le forward, calls au-dessus, celles
  qui s'échangent : volatilité au forward, interpolée en log-strike, et ailes
  à 25 delta, interpolées en delta du call (0,25 pour le call, 0,75 pour le
  put de delta −0,25). Le delta vient de Black-76 à taux nul, la convention
  de Deribit pour des options sur le future de l'échéance. En sortent le
  *risk reversal* (call moins put : négatif, la couverture à la baisse se
  paie) et le *butterfly* (convexité du sourire) ;
- `strike_table` et `max_pain` : l'open interest par strike, et le strike où
  la valeur des options à l'échéance serait la plus faible — une description
  du positionnement ; l'idée que le prix y serait attiré n'a rien d'établi ;
- `futures_curve` : la base des futures à échéance contre l'indice, et son
  annualisation, le pendant à terme du financement du perpétuel ;
- `front_expiry` : l'échéance montrée par défaut, la plus proche qui pèse au
  moins 5 % de l'open interest — Deribit liste une échéance par jour sur la
  semaine qui vient, chacune minuscule — et jamais celle du jour.

Les échéances à moins de deux jours (`MIN_DAYS`) restent dans l'open interest
mais sortent des courbes : à quelques heures du terme, un écart de quelques
dollars fait un taux annualisé absurde, et la volatilité ne mesure plus que
le bruit de la journée.

Le hub sert la chaîne et la courbe des futures à la minute (`TTL_OPTIONS`),
DVOL à dix minutes, et la volatilité réalisée à trente jours sur les clôtures
journalières qu'il tient déjà pour l'alerte de la MA 200 — rien sur la série
de démonstration, comparer DVOL à une marche aléatoire n'aurait aucun sens.
`deribit.com` rejoint les domaines dont la réponse DNS du système ne fait pas
foi (§2.9) : il résout juste aujourd'hui, comme Coinbase et Kraken, et le
module y est prêt le jour où ce ne sera plus le cas.

### 2.12 `markets` — le bitcoin parmi les marchés

La question qu'un poste de marché pose avant toute autre : l'actif bouge-t-il
seul, ou avec le reste ? Le module confronte le bitcoin à cinq marchés — S&P 500,
Nasdaq 100, or, dollar (DXY), taux américain à dix ans — par des fonctions
pures, testées sans réseau (`tests/test_markets.py`) :

- `align` ne garde que les séances du marché comparé, le bitcoin pris à la
  clôture UTC du même jour — trois à quatre heures après la cloche de New
  York, l'écart qu'on accepte plutôt que d'inventer des séances le week-end ;
- `changes` compare un prix en rendements logarithmiques, un taux en
  variations de points : le rendement d'un rendement n'a pas de sens ;
- `rolling_correlation` suit la corrélation des variations quotidiennes sur
  30 et 90 séances — la réponse change d'un régime à l'autre, une
  corrélation d'ensemble la cacherait ;
- `snapshot` fait le tableau : dernier cours, variation sur une séance et sur
  un mois (vingt et une séances, trente jours pour le bitcoin qui cote tous
  les jours), corrélations. Une place muette garde sa ligne, vide.

La source est l'API de graphiques de Yahoo Finance (`fetch_yahoo_daily`) :
non documentée, sans clé, la seule gratuite qui serve ces séries sans
vérification JavaScript — Stooq en impose une. Elle refuse l'agent par défaut
de `requests` (429) ; un nom de client générique, `btcterm/1.1`, suffit. Le
DXY y a une séance vide sur six, écartée. Le hub lit les cinq marchés en
parallèle, chacun son cache de cinq minutes : lus l'un après l'autre, des
ratés de Yahoo — délai dépassé, connexion coupée, vus dès le premier essai —
faisaient attendre le panneau cinq délais au lieu d'un.

### 2.13 `tape` — ce qui s'exécute, et qui l'a voulu

Le carnet montre des intentions, le ruban (le « time & sales » des terminaux)
ce qui s'est exécuté, et surtout le côté de celui qui a **agressé**. Trois
places au comptant, chacune son WebSocket public : Binance (`aggTrade`),
Coinbase (`market_trades`, BTC-USD) et Bybit (`publicTrade`). `TradeTape`
garde une heure de gros ordres (50 000 $ et plus) et, toutes tailles, la
somme achetée et vendue par minute et par place ; il tient l'état de chaque
lien, que le panneau SOURCES montre.

Le sens du preneur ne se lit pas pareil d'une place à l'autre, et il a été
**mesuré** le 6 novembre 2026 contre le meilleur bid/ask du même flux plutôt
que supposé — un achat agresseur s'exécute à l'ask. Binance (`m` vrai :
l'acheteur était teneur, donc vente agressive : 158 échanges au bid contre 30
à l'ask) et Bybit (`S`, côté du preneur : 530 achats à l'ask contre 9 au bid)
confirment leur documentation. Coinbase Advanced, dont la documentation ne
tranche pas, marque le côté du **teneur** : ses « BUY » tombent au bid (180
contre 23 à l'ask) — le preneur est de l'autre côté. Supposée, l'inversion
aurait retourné tout le ruban de Coinbase, la place des acheteurs américains.
L'instantané qu'envoie Coinbase à l'abonnement, qui rejoue ses derniers
échanges, est écarté : le ruban ne dit que ce qui arrive.

**La mémoire du ruban.** Le ruban ne gardait qu'une heure en mémoire, et un
redémarrage du service l'effaçait. Chaque gros ordre part désormais au journal
(table `large_trades`, `on_large`), qui le rend au démarrage (`_warm_tape`,
sans le réécrire) et en fait le bilan de la journée au panneau JOURNAL :
achats, ventes, solde et ordre le plus gros. Le seuil du ruban passe au passage
de 50 000 à 100 000 $, le plus petit seuil du panneau : entre les deux, des
ordres étaient gardés qu'aucun seuil ne montrait.

**La prime Coinbase.** Le ruban tient aussi, par minute et par place, la
quantité échangée : de quoi tirer un prix moyen pondéré (`vwap`) et l'écart du
bitcoin en dollars sur Coinbase — la place des acheteurs américains — à
Binance en USDT (`coinbase_premium`), lecture usuelle de la demande
américaine. Mesurée le 7 novembre 2026 au matin : −0,16 %. Les deux places ne
cotent pas la même monnaie : un USDT décoté sur le dollar fait une part de
l'écart, qui ne se lit qu'au-delà de quelques centièmes. Le titre du RUBAN la
porte, et l'instantané de marché la note toutes les cinq minutes
(`cb_premium`) : un historique qu'aucune API gratuite ne sert.

### 2.14 `brief` — la journée en une page

Chaque panneau dit son présent ; aucun ne disait ce que la nuit avait changé,
et y répondre demandait d'ouvrir six onglets. `brief.build(hub)` rend une
douzaine de lignes, toutes tirées de ce que le terminal tient déjà : le ticker
de Binance pour la journée glissante ; les instantanés du journal pour le
financement, l'open interest, DVOL et le skew, la prime Coinbase et la
dominance, chacun avec son écart sur la journée ; les liquidations, gros
ordres et alertes journalisés ; les flux ETF, le Fear & Greed, le cycle —
jour depuis le halving, distance au sommet, multiple de Mayer, halving à
venir (§2.15) —, le calendrier macro et les news les mieux notées de la
fenêtre (`newsdb.top_since`). Les
fonctions de section sont pures, testées sans réseau
(`tests/test_brief.py`) ; une source muette ne coûte que sa ligne.

**La couverture se dit.** Le portable dort : sur les vingt-quatre heures qui
précédaient la première synthèse, le journal n'en couvrait que sept et
quart, en six interruptions. Les liquidations et les gros ordres n'y comptent
que le temps de veille, et l'écart « sur 24 h » se prend contre l'instantané le
plus proche de la veille — qui peut n'avoir que quinze heures, ce que la ligne
écrit alors (« sur 15 h ») ; sous douze heures, l'écart n'est pas donné. Les
heures couvertes s'écrivent en tête dès qu'il en manque plus de dix minutes :
une nuit de sommeil ne se présente pas comme une nuit calme.

Elle vit en trois endroits : le panneau SYNTHÈSE (§3.20), la ligne de
commande (`python -m btcterm.brief`, qui installe le résolveur DoH comme le
service), et ntfy chaque matin à l'heure de `BTCTERM_BRIEF` (`--brief`).
L'envoi part de la boucle d'observation (`MarketHub._maybe_brief`) dans un
thread à lui — la synthèse lit plusieurs sources jusqu'à leurs délais —, une
fois par jour au plus : la date part sur disque à côté du journal
(`~/.btcterm/brief.json`), et un service relancé après l'heure ne la renvoie
pas. Une machine qui dormait à l'heure dite l'envoie au réveil. Elle part en
priorité 2, sous les sonneries : c'est un résumé, pas une alerte, et elle ne
passe pas par le moteur d'alertes — elle ne sonnerait pas à la cloche du
bandeau ni ne se relirait parmi les alertes.

### 2.15 `cycle` — où en est-on depuis le halving

Le bitcoin a une horloge que les autres marchés n'ont pas : tous les 210 000
blocs l'émission est divisée par deux, et les cycles passés se lisent contre
cette date. Le module situe le cours dans ce cycle, sans rien prédire, et sans
source de plus : tout vient des clôtures journalières que le disque tient
depuis le 17 août 2017 (§2.10), et de la hauteur de la chaîne, ajoutée à
l'instantané de blockchain.info (`height`). Fonctions pures, testées sans
réseau (`tests/test_cycle.py`) :

- `next_halving` — blocs restants et date du prochain halving, comptée à dix
  minutes le bloc : le rythme observé d'un jour ne dit rien des mois à venir,
  que l'ajustement de difficulté ramène à la cible. Sans hauteur — source
  muette —, elle s'estime depuis le dernier halving, et le résultat le dit
  (`estimated`, un tilde à l'écran) ;
- `drawdown` — le repli depuis le plus haut couru. Le sommet d'avant la série
  (3 000 $ en juin 2017) était déjà dépassé à son premier jour : le plus haut
  couru y est bien le plus haut historique, en clôture journalière ;
- `mayer` — le cours sur sa moyenne de 200 jours, avec ses bornes d'usage
  (0,8 et 2,4) ; `metrics` y ajoute son rang, la part des jours passés où il
  était plus bas, sans laquelle le chiffre ne dit pas s'il est banal ou rare ;
- `cycle_paths` — chaque cycle en multiple du cours du jour de son halving,
  jour pour jour. Le cycle de 2016 n'entre dans la série qu'à son
  quatre-centième jour : son cours de départ (650 $) est une référence écrite
  dans la table `HALVINGS`, la seule donnée du module qui ne vienne pas du
  disque ;
- `metrics` — la situation du jour : ce qui précède, la moyenne de 200
  semaines, le rapport du Pi cycle (moyenne de 111 jours sur deux fois celle
  de 350), et ce que valaient les cycles précédents au même jour. Une mesure
  qui demande plus de profondeur que la série n'en a revient vide plutôt
  qu'approchée.

Le hub assemble la série (`cycle_closes`) : les jours clos du disque et le
jour en cours de la lecture courante, gardés une minute. Ce n'est pas une
source, et le panneau SOURCES ne la montre pas. Le panneau CYCLE (rangée
basse) en tire trois vues — CYCLES, sur une échelle logarithmique où une même
hauteur vaut un même multiple, MAYER et REPLI — et un tableau. Tout y reste en
dollars : ce sont des multiples et des rapports.

### 2.16 Non-régression

`tests/test_indicators_parity.py` rejoue les implémentations telles qu'elles
étaient avant l'extraction et vérifie que le socle produit exactement les mêmes
valeurs — RSI, streak, rang centile, Connors RSI, Bollinger, ATR, volatilité,
profil de volume, signaux gradués et marqueurs.

`tests/test_liquidations.py` couvre ce qu'aucune observation ne pourrait
garantir : le flux des liquidations est épisodique, et le contrôle d'interface
trouve presque toujours le panneau vide. Le test lui injecte des messages au
format documenté par Binance et vérifie le sens des événements, les totaux, la
fenêtre glissante, le rejet des messages aberrants, la mise en forme du
panneau, et la relecture du journal au démarrage — sur une base temporaire,
en s'assurant qu'aucun événement relu ne se réinscrit.

`tests/test_macrocal.py` garde la liste de dates contre ses deux façons de se
tromper : la faute de frappe silencieuse — un 30 février lèverait dès l'import,
mais un mardi écrit à la place d'un mercredi passerait sans bruit, d'où le
contrôle qu'aucune publication ne tombe un week-end — et la conversion d'heure,
vérifiée de part et d'autre du changement d'heure américain.

`tests/test_etf_flows.py` protège ce que le terminal *déduit* du tableau de
farside, dont la forme peut changer sans prévenir : le stock se compte sur tout
l'historique et jamais sur la fenêtre affichée — c'est la quantité détenue par
les ETF, elle ne dépend pas de la période qu'on regarde —, quand le classement
par émetteur se compte au contraire sur la fenêtre, la question étant « qui
achète en ce moment ». Le test vérifie aussi qu'un émetteur immobile disparaît
du classement plutôt que d'y figurer à zéro, que la courbe de cumul continue
l'historique antérieur à la fenêtre au lieu de repartir de zéro, et que la barre
de titre reste muette dans la grille sans séparateur orphelin ni chiffre répété.

`tests/test_fear_greed.py` tient les trois façons dont l'indice Fear & Greed
peut mentir sans le dire. alternative.me répond du plus récent au plus ancien :
tracé tel quel, l'historique se lirait à l'envers et une capitulation
ressemblerait à une euphorie. Le hub n'appelle la source qu'une fois et dérive
le chiffre du badge du dernier point de la courbe : se tromper de bout
afficherait une valeur vieille de trois mois sans qu'aucune erreur ne remonte.
Et le badge comme les bandes du graphique doivent colorer une même valeur de la
même façon (§4.4). Le test vérifie en prime qu'une panne survenue après coup
laisse la dernière courbe connue à l'écran plutôt qu'un cadre vide, en
vieillissant le cache à la main.

`tests/test_news_scoring.py` fait de même pour le scoring extrait du tracker —
mêmes scores, mêmes mots-clés, mêmes sentiments qu'avant — et vérifie en prime
ce que l'extraction rend enfin testable sans réseau ni base réelle : la collecte
écarte ce qui passe sous le seuil et n'insère pas deux fois le même article.

```bash
python tests/test_indicators_parity.py
python tests/test_news_scoring.py
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
├── app.py           assemblage : horloges, Stores partagés, point d'entrée
├── grid.py          grille : rangement, cellules, onglets, plein écran
├── placement.py     dialogue de disposition (§3.6)
├── header.py        bandeau : cours, variation, spread, état des flux
├── push.py          canal push des panneaux rapides (§3.10)
├── lwc.py           données des rendus LWC : /api/klines, /api/profile,
│                    /api/perp (§3.2)
├── wsgi.py          fabrique gunicorn du régime service (§3.11)
├── theme.py         palette et styles partagés
├── charts.py        constructeurs de figures Plotly (autres panneaux)
├── assets/          CSS et JS — lwc-price.js et lwc-perp.js dessinent
│                    prix et perpétuel, book.js écrit le carnet en place,
│                    vendor/ porte Lightweight Charts (aucun CDN)
└── panels/          un module par panneau
    ├── price.py         chandeliers, indicateurs, profil de volume
    ├── orderbook.py     carnet (données pour book.js), murs + profondeur
    ├── arbitrage.py     écarts inter-plateformes
    ├── etf.py           flux des ETF spot
    ├── perp.py          financement, open interest, positionnement,
    │                    comparaison Binance · Bybit · OKX
    ├── position.py      position saisie à la main, taille d'après l'ATR
    ├── liquidations.py  positions fermées de force
    ├── journal.py       relecture des 24 h : alertes, épisodes, bilan
    ├── dominance.py     parts de capitalisation
    ├── onchain.py       hashrate, difficulté, mempool
    ├── news.py          fil de news + Fear & Greed (courbe 90 j agrandi)
    ├── calendar.py      échéances macro : FOMC, CPI, NFP, PCE
    ├── alerts.py        sonneries et réglages du moteur d'alertes
    ├── macro.py         cours contre masse monétaire M2
    └── analysis.py      intervalles, bilan des signaux, règles, saisonnalité
```

```
┌─────────┬──────────────────┬─────────────────┐
│         │ CARNET  PROFOND. │ ARBITR. LIQ. J. │
│         │                  ├─────────────────┤
│  prix   │                  │                 │
│         │                  │ NEWS  CALENDRIER│
│         │                  │      ALERTES    │
│         ├──────────────────┤                 │
│         │ ETF  PERP.  POS. │                 │
│         ├──────────────────┴─────────────────┤
│         │ MACRO  DOMINANCE  ON-CHAIN  ANALYSE│
└─────────┴────────────────────────────────────┘
```

Chaque cellule, hormis le prix, héberge plusieurs panneaux choisis par
onglets (§3.5) — les majuscules du croquis sont les barres d'onglets. Le
croquis montre la répartition **par défaut** : quels panneaux vivent
dans quelle cellule se règle depuis le terminal lui-même (§3.6).

Le panneau macro occupe une rangée basse sur toute la largeur restante :
deux séries mensuelles sur dix ans se lisent en longueur, et cette forme
est celle qui coûte le moins de hauteur aux autres. Le carnet et la
profondeur, eux, partagent une cellule haute de deux rangées et se
choisissent par onglets (§3.5) — on ne les regarde pas en même temps, et
la place ainsi rendue permet au carnet d'afficher huit niveaux par côté
au lieu de cinq.

### 3.1 Trois régimes de rafraîchissement

C'est la décision qui rend le terminal utilisable. L'ancien `btc-dash.py` faisait
vivre tous ses éléments au même rythme et resérialisait la figure entière à
chaque tour — d'où un rafraîchissement toutes les 10 secondes, incompatible avec
un carnet d'ordres.

| Horloge | Période | Panneaux | Coût mesuré par tour |
|---|---|---|---|
| `tick-fast` | 250 ms | carnet, profondeur, arbitrage, liquidations | 8–33 ms, 6–25 Ko |
| `tick-slow` | 2 s | prix (repli poll, §3.2), bandeau, fil de news | 0,3–1,3 s, 162 Ko à l'époque Plotly — le prix ne recharge plus qu'une page de données |
| `tick-rare` | 5 min | flux ETF, perpétuel, macro, dominance, on-chain, calendrier | 0,3–0,5 s, 8–10 Ko |

Les panneaux rapides ne touchent jamais le réseau : ils lisent les carnets que
le hub entretient en mémoire, ce qui explique l'écart d'un facteur cinquante
avec le panneau prix. Ce dernier n'a de toute façon aucune raison d'aller plus
vite : une bougie journalière ne change pas quatre fois par seconde.

Le fil de news est passé de l'horloge rare à l'horloge lente : il ne lit qu'une
douzaine de lignes dans une base locale, et l'âge de la dernière collecte qu'il
affiche doit avancer sans attendre cinq minutes. La liste elle-même ne repart
vers le navigateur que si elle a changé : une empreinte (lien, score,
sentiment de chaque ligne, `news_signature`) est gardée dans la page, et le
callback ne renvoie que l'âge et le badge tant qu'elle tient. Douze lignes
étaient reconstruites et redessinées toutes les deux secondes, pour une
collecte toutes les quinze minutes.

Un quatrième rythme échappe aux horloges : la **collecte des news**, toutes les
quinze minutes, dans un thread démon du hub (§2.4). Elle ne pouvait pas vivre
dans un callback — six flux RSS prennent plusieurs secondes, et le panneau
serait resté figé pendant ce temps. Les panneaux lisent la base, jamais le
réseau ; le fil affiche l'âge de la dernière tournée, un fil de news figé devant
se voir.

L'horloge rapide a depuis gagné un second canal : quand le navigateur tient un
WebSocket ouvert, le serveur pousse le rendu et `tick-fast` est coupé (§3.10).
La bougie courante du panneau prix passe par le même canal (§3.2), et
lwc-price.js s'abstient alors de solliciter l'horloge lente — le badge
« push / poll » du bandeau dit le canal réellement actif. Le tableau
ci-dessus reste le régime de repli — et le seul mode des horloges lente et
rare pour les autres panneaux, que rien ne presse.

Une conséquence pratique : `update_title=None` désactive le « Updating… » que
Dash affiche par défaut dans l'onglet, sans quoi il clignoterait en permanence
au rythme de l'horloge rapide.

### 3.2 Le cours d'abord

Le panneau prix ne passe plus par Plotly : il dessine sur canvas dans le
navigateur, en **Lightweight Charts** (TradingView, v5.2.1, vendoré dans
`assets/vendor/` — aucun CDN). C'est l'aboutissement de la « voie A »
(§7, étape 2) : pour la lecture fine en séance d'analyse, la figure
recalculée côté serveur à chaque tour d'horloge atteignait ses limites —
chaque rafraîchissement rejouait un graphique entier là où seule la
dernière bougie changeait, et l'historique était borné à la page chargée.

Le partage des rôles est strict. Le **serveur reste la seule source de
vérité des indicateurs** : `terminal/lwc.py` sérialise le DataFrame
enrichi par `prepare_price_frame` — chandeliers, MA 9/26/200, Bollinger,
volume, RSI, CRSI, signaux — et le sert par `/api/klines`, paginé pour
remonter le passé, et `/api/profile`, le profil de volume d'une plage à la
demande. La bougie courante arrive par le canal `/push` (§3.10), les
derniers points d'indicateurs recalculés de façon bornée —
`test_indicators_incremental` prouve la parité avec le recalcul complet.
Le **navigateur dessine** : `assets/lwc-price.js` pose chandeliers et
moyennes, RSI et CRSI dans leurs panes, crosshair aimanté et ligne du
dernier prix — comportements natifs de la bibliothèque — plus ce qu'elle
n'offre pas : le profil de volume de la plage visible (POC et Value Area,
recalculés quand la fenêtre bouge), les signaux, les seuils d'alerte
(§2.8), le bandeau de démonstration (§4.3) et le zoom rectangulaire.
Lightweight Charts ne zoome qu'à la molette ; Maj + glisser trace un cadre
sur le pane du cours, et le relâcher y ajuste la fenêtre — le temps par
l'échelle logique, le prix par l'échelle de droite, qui quitte alors
l'auto-ajustement. Les écouteurs sont posés en phase de capture sur le div
du panneau : avec Maj, l'événement n'atteint pas le canvas de la
bibliothèque, qui sinon panoterait ; sans Maj, rien ne change. Le
double-clic rend la vue entière et l'auto-ajustement — le geste de Plotly,
que les autres panneaux gardent. En échelle log, la plage se pose en mode
linéaire puis le mode log est remis : `setVisibleRange` prend la plage
telle quelle alors que l'axe log travaille en valeurs transformées, et
c'est la bascule de mode qui convertit. `ui_smoke.py` joue le geste à la
vraie souris (actions WebDriver, pas des `MouseEvent` synthétisés : la
bibliothèque reconnaît ses clics sur `<html>`, qu'un événement dispatché
sur `document` n'atteint pas).

Les réglages de la barre de titre passent par un unique callback
clientside qui relaie l'état des sélecteurs à `window.lwcPrice` : un
refetch seulement quand l'intervalle change ; la devise et l'échelle log
se règlent sur place, le paquet étant gardé en USD et le taux € voyageant
avec lui. Aucun aller-retour serveur pour un réglage d'affichage.
L'historique se charge à la volée : un pan vers le passé demande la page
antérieure de `/api/klines`, jusqu'à ce que la source avoue ne plus rien
avoir. Une page vide faute de source — Binance muet, `stale` dans la
réponse — n'est pas un aveu : elle est redemandée trente secondes plus
tard, sans attendre un nouveau pan. Une page ne se colle que si le tampon
commence toujours là où elle s'arrête. Et un recalage silencieux — retour
d'onglet, reconnexion du canal, repli poll — ne remplace que la fin du
tampon : le passé remonté reste, et la vue posée dedans avec lui.

C'est le cours qu'on vient lire en séance d'analyse ; les oscillateurs
l'accompagnent, ils ne le concurrencent pas. Son pane porte un facteur
d'étirement de 300 contre 70 par oscillateur coché, et tout ce qu'on
décoche lui rend sa hauteur — jusqu'au panneau entier.

**Neuf échelles de temps**, de la bougie de quinze minutes à la mensuelle,
libellées à la casse Binance (`30m` les minutes, `1M` le mois — la barre de
titre est en majuscules, ses sélecteurs non, sans quoi les deux se
confondraient), chacune avec sa profondeur d'historique (`INTERVALS` dans
`panels/price.py`) :
300 à 365 bougies en intraday et en journalier, de quoi nourrir la MA 200, mais
120 mensuelles seulement — dix ans suffisent, et la MA 200 n'a alors plus de
sens de toute façon. La palette vient de `btc_dashboard2.py`, dont c'était le
seul apport sur le panneau prix ; ses alias `1y` et `All` n'ont pas été repris,
n'étant que des raccourcis vers `1d` et `1w`.

La case `LOG` passe l'axe des prix en logarithmique. Elle devient indispensable
dès qu'on remonte plusieurs années : en linéaire, une progression de 4 000 à
80 000 dollars écrase tout le début du graphique contre l'axe.

La barre de titre est devenue le facteur limitant : neuf intervalles, la devise,
l'échelle et quatre sous-graphiques ne tiennent sur une ligne qu'au prix de
sélecteurs en 9 px, d'un titre réduit à `BTC/USDT` et d'une devise notée `$` /
`€`. Deux lignes voleraient de la hauteur au cours ; `ui_smoke.py` mesure donc
que la barre tient sur une ligne et ne déborde pas de la largeur du panneau.
La minute et les cinq minutes, ajoutées le 7 novembre 2026 pour lire un
mouvement en cours, la faisaient déborder de 57 px. La place était ailleurs :
Dash pose 16 px à droite de chaque option en ligne, et dix-neuf pixels
séparaient deux libellés — trois cents sur la barre, que les réglages
précédents, centrés sur la marge de gauche et l'espacement des lettres, ne
voyaient pas. Dix pixels d'un seul côté lui rendent cent quatre-vingts. Les
chiffres gardent leurs intervalles (1 le quart d'heure, 7 le jour, 9 le mois) :
la minute et les cinq minutes s'atteignent par `-`, qui affine pas à pas, `+`
élargissant, ou par la ligne de commande — `/1m` est la minute, `/1M` le mois.
Elles chargent davantage que les autres : une journée de minutes (1 440
bougies), une semaine de cinq minutes (2 016) — six heures de minutes, le
premier réglage, ne montraient presque rien d'une séance. Binance plafonne
chaque appel à 1 000 bougies : `/api/klines` en sert jusqu'à 2 500, que
l'historique sur disque assemble en plusieurs pages, et `fetch_klines` borne
sa demande à 1 000 pour qu'un terminal sans historique (`--no-journal`)
reçoive une série tronquée plutôt qu'un refus. Le tampon du navigateur monte
à 10 000 bougies : une semaine de minutes, un mois de cinq minutes.

**Le perpétuel a rejoint ce côté du partage.** Même contrat que le prix,
en plus simple : `/api/perp` sérialise financement (en % par 8 h) et open
interest (en dollars — la série prolongée par le journal, §2.7) via
`serialize_perp`, et `assets/lwc-perp.js` dessine — histogramme signé
pour le financement, ligne d'open interest sur son axe gauche gradué en
milliards, crosshair commun. Pas de canal push ni de pagination : ces
données bougent par tranches de 4 à 8 heures, le client suit l'horloge
rare et un poll ne vole jamais le zoom en cours. Hors ligne, les listes
reviennent vides et le panneau l'écrit. Les badges de la barre de titre
restent un callback serveur : ce sont des instantanés, pas des séries.
Les autres panneaux restent des figures Plotly — le rendu serveur
convient à ce qu'on regarde ; le canvas se gagne quand on manipule.

### 3.3 Préserver l'état d'analyse

Toutes les figures Plotly portent un `uirevision`. Sans lui, chaque
rafraîchissement réinitialiserait le zoom, le pan et la sélection de légende :
impossible d'examiner une zone pendant que les données coulent, ce qui viderait
de son sens l'usage en séance d'analyse.

La valeur encode ce qui est affiché (`"1M:0"` pour la fenêtre et le décalage
du panneau macro) : stable d'un rafraîchissement à l'autre **et au passage en
plein écran** — le zoom en cours survit donc à l'agrandissement, précisément
le geste qu'on fait pour examiner une zone de plus près — elle change quand le
contenu change, et le recadrage est alors ce qu'on veut. Le panneau prix n'a
pas besoin de l'artifice : son graphique vit dans le navigateur, seules les
données y sont mutées, et l'état de navigation survit de lui-même — le
recadrage au changement d'intervalle vient du refetch, qui repose la série.

L'état survit aussi **au rechargement de la page** : tous les sélecteurs —
intervalle, devise, échelle, sous-graphiques, plateforme du carnet, fenêtre et
décalage du panneau macro — portent `persistence="local"`, et l'onglet actif de
chaque cellule comme la disposition de la grille (§3.6) sont des `Store` en
localStorage. On ne reconfigure pas sa station
de travail à chaque session ; accessoirement, la persistance des sélecteurs
règle aussi le cas du changement d'onglet, qui reconstruit le layout du panneau
quitté à ses défauts. Le plein écran, lui, reste volontairement en mémoire :
un rechargement rend la grille.

Deux pièges, découverts par le contrôle Firefox plutôt qu'en théorie :

- un `Store` persisté auquel le layout fournit `data=` **réécrit cette donnée
  dans le localStorage à chaque chargement** — écrasant précisément ce qu'on
  voulait restaurer. Le Store n'a donc pas de valeur initiale ; le repli sur
  les défauts appartient aux callbacks, qui le faisaient déjà ;
- le montage d'un onglet déclenche le callback pattern-matching des clics avec
  `n_clicks` à zéro. Sans garde, le premier rendu écrivait dans le Store — et
  aurait pu y écrire le mauvais onglet d'une cellule. Un déclencheur sans
  valeur est ignoré : ce n'est pas un clic.

### 3.4 Plein écran

Une grille de six cellules ne laisse pas assez de place pour lire finement un
graphique. Chaque cellule porte donc un bouton ⛶ qui le fait couvrir la fenêtre
entière, les autres étant masqués ; un second clic, ou `Échap`, rend la grille.

La bascule est un callback **clientside** : elle se contente d'échanger des
classes CSS, sans aller-retour serveur ni recalcul de figure. Le panneau agrandi
passe en `position: fixed` sous le bandeau, les autres en `display: none`.

Un détail qui n'est pas optionnel : la fonction émet un événement `resize` sur
la fenêtre après la bascule. Plotly ne redimensionne ses figures qu'à cet
événement — sans lui, le graphique agrandi garderait la taille de sa vignette.
Le panneau prix, lui, suit tout seul (`autoSize` de Lightweight Charts), mais
lwc-price.js recadre sa plage visible au changement d'agrandissement : la
série resterait sinon tassée contre le bord droit du panneau élargi.

Le même piège se referme sur un graphique qui n'existe *que* pendant le plein
écran. Le panneau news montre l'historique de l'indice Fear & Greed sur
quatre-vingt-dix jours, mais seulement agrandi — dans la grille, chaque ligne
de news vaut mieux qu'une courbe écrasée. Ce graphique n'est pas masqué puis
révélé : il est **construit à l'agrandissement**, le callback qui lit `expanded`
rendant le `dcc.Graph` ou rien. Un graphique monté dans un conteneur en
`display: none` se dessine sur une hauteur nulle et reste plat une fois
découvert, l'événement `resize` de la bascule ne le rattrapant pas — il n'a
jamais eu de taille à rattraper. Corollaire utile : replié, il ne coûte rien,
et le hub n'est pas interrogé pour une courbe que personne ne regarde.

Le panneau des flux ETF pousse le principe plus loin : agrandi, il ne montre
pas la même chose en plus grand, mais **trois lectures que la vignette ne peut
pas porter**. Les barres quotidiennes gagnent le cumul depuis le lancement sur
un axe droit — ce sont les barres qui font le bruit et la pente du cumul qui dit
si les institutions accumulent ou distribuent ; un second volet classe les
émetteurs sur la fenêtre, le total masquant des mouvements opposés (GBTC a passé
deux ans à décollecter pendant qu'IBiT encaissait) ; et un sélecteur ouvre la
fenêtre de trente jours à tout l'historique. Ces commandes, comme les chiffres
clés de la barre de titre, ne s'affichent qu'agrandies : la cellule d'origine
partage déjà sa barre de titre avec les onglets du perpétuel.

Cela a demandé de changer de page source. `farside.co.uk/btc/` ne publie plus
que les trois dernières semaines — le panneau annonçait « 30 jours » qu'il
n'avait pas —, quand `farside.co.uk/bitcoin-etf-flow-all-data/` sert le même
tableau depuis le lancement des ETF en janvier 2024, soit six cent soixante-dix
jours ouvrés. Le collecteur n'a pas bougé d'une ligne : c'est la même table, au
même format.

La bascule s'ouvre par le bouton ⛶ — **toujours visible**, une première version
qui ne l'affichait qu'au survol s'étant révélée introuvable — ou par un
double-clic sur le panneau. Le double-clic ignore les graphiques : Plotly y
réserve ce geste à la réinitialisation des axes.

Cette logique vivant en JavaScript, elle échapperait aux tests Python.
`tests/test_fullscreen_toggle.py` extrait la fonction de `grid.py` et l'exécute
sous Node avec un faux `dash_clientside` (test ignoré si Node est absent).

### 3.5 Onglets : plusieurs panneaux par cellule

La grille était pleine à sept panneaux, et un terminal complet en demande le
double — dominance, funding, liquidations, on-chain, calendrier. Plutôt que de
rétrécir encore les cellules, une cellule peut désormais héberger **plusieurs
panneaux**, choisis par des onglets. Le carnet et la profondeur comparée
inaugurent le mécanisme : on ne regarde pas les deux en même temps.

`CELLS`, dans `grid.py`, est la seule liste qui décide de ce qui est affichable :

```python
CELLS = {
    "book": (("book",  "CARNET",     orderbook.layout),
             ("depth", "PROFONDEUR", orderbook.depth_layout)),
    ...
}
```

Trois décisions méritent d'être expliquées.

**Les onglets remplacent le titre du panneau**, ils ne s'ajoutent pas au-dessus.
Chaque `layout()` accepte donc un titre imposé par sa cellule, et retombe sur le
sien quand elle n'en impose pas. Une barre d'onglets supplémentaire aurait coûté
une ligne de hauteur à chaque cellule qui en porte.

**Un panneau caché n'est pas dans la page.** La cellule ne rend que son panneau
actif ; Dash ne fait donc tourner aucun callback des autres — un panneau masqué
en CSS aurait continué de recalculer sa figure à chaque tour d'horloge. En
retour, Dash exécute les callbacks d'un composant dès son apparition : un
panneau lent se remplit à l'ouverture de son onglet, sans attendre les cinq
minutes de son horloge. `ui_smoke.py` le vérifie explicitement.

**Un piège de nommage.** Les classes CSS portent le préfixe `cell-` :
`cell-tabs`, `cell-tab`, `cell-tab-active`. Dash sert en effet la feuille de
style de `dcc.Tabs`, qui revendique `.tab` et y met `padding: 20px` et
`flex: 1 1 0`. Les premiers onglets, nommés `.tab`, héritaient donc d'une barre
de titre haute de 55 px au lieu de 14, sans que rien ne le signale — le
contrôle d'interface mesure désormais la hauteur de chaque barre de titre.

**Le clic passe par un `Store`.** Le callback qui rend le corps d'une cellule
remplacerait ses propres entrées s'il écoutait les onglets directement, et
chaque rendu le redéclencherait. Un callback clientside traduit donc le clic en
une entrée du `Store` `tabs`, seul déclencheur du rendu.

### 3.6 La disposition se configure

La répartition des panneaux dans les cellules n'est plus figée : le ⚙ du
bandeau ouvre un dialogue où chaque panneau se range dans la cellule de
son choix, et le rangement vit dans un `Store` en localStorage
(`placement`), au même régime que les onglets — pas de `data=` initial,
repli dans les callbacks (§3.3). `CELLS` reste la seule liste de ce qui
est affichable ; elle fournit désormais le registre des panneaux, leur
cellule d'origine et le rangement par défaut.

Trois décisions structurent le mécanisme.

**Un sélecteur par panneau, pas une liste par cellule.** Le dialogue
pose une rangée par panneau et six positions au choix : la structure
garantit d'elle-même qu'un panneau vit dans exactement une cellule —
impossible d'en perdre un ou de l'afficher deux fois, les deux erreurs
qu'une liste par cellule aurait laissées constructibles. La seule qui
reste — vider une cellule de tous ses panneaux — est refusée à
l'application. Les sélecteurs réutilisent le style `tf-radio` des barres
de titre, et « Par défaut » ne fait que remplir le formulaire :
« Appliquer » est le seul geste qui écrit.

**Le rangement restauré est normalisé, jamais cru.** Un localStorage
peut dater d'avant un renommage de panneau ou avoir été altéré :
`normalize_placement` écarte les identifiants inconnus, réduit un
panneau rangé deux fois à sa première place, rend un panneau oublié à sa
cellule d'origine, et fait retomber sur le défaut un rangement qui
viderait une cellule. `tests/test_grid_layout.py` éprouve chacun de ces
cas — le contrôle Firefox ne le peut pas, son navigateur partant
toujours d'un localStorage sain.

**Une cellule ne se re-rend que si son contenu change.** `tabs` et
`placement` sont des Stores globaux : sans garde, un clic d'onglet
re-rendrait les six cellules — et remonter un graphique lui fait perdre
son zoom, `uirevision` ne survivant qu'aux mises à jour, pas à un
remontage. Chaque cellule retient donc dans un `Store` mémoire ce
qu'elle affiche (panneau actif, liste d'onglets) et répond `no_update`
quand rien n'en change. Le garde corrige au passage un défaut antérieur :
avant lui, changer d'onglet dans une cellule remontait déjà les corps
des autres.

### 3.7 Anatomie d'un panneau

Chaque module de `panels/` expose exactement deux fonctions :

- `layout(title=None)` — ses composants Dash, titrés par sa cellule ou par
  lui-même
- `register(app, hub)` — ses callbacks

Un panneau ne fait aucun appel réseau : il demande au hub, qui mutualise. Il
n'écrit rien non plus — le panneau news lit la base en lecture seule, c'est le
collecteur du hub qui l'alimente (§2.4).

### 3.8 Contrôle visuel

Une partie des défauts d'interface ne se voit qu'à l'écran, et aucune quantité
de tests Python ne les révèle. `tests/ui_smoke.py` pilote donc Firefox par
**Marionette** — le protocole d'automatisation intégré au navigateur — via un
client d'une soixantaine de lignes (`tests/marionette_client.py`), ce qui évite
d'installer geckodriver ou Selenium.

```bash
python -m terminal.app &
python tests/ui_smoke.py --capture /tmp/captures
```

Il vérifie les cellules posées, la visibilité et le non-recouvrement du bouton,
l'agrandissement réel, le retour par `Échap`, le double-clic, que le carnet
montre bien ses deux côtés, que la barre de titre du panneau prix tient sur une
ligne, que la bascule LOG atteint l'axe, que le panneau macro trace ses deux
séries, que changer d'onglet remplace bien un panneau par l'autre, rempli dès
son apparition — et qu'un rechargement restaure onglets et sélecteurs mais pas
le plein écran (§3.3), en rechargeant réellement la page, seule façon d'éprouver
ce que le localStorage garde et ce qu'il écrase. Le canal push (§3.10) y a sa
section : badge à « push », carnet vivant l'horloge coupée, agrandissement
acheminé par le WebSocket. Le rendu Lightweight Charts du prix (§3.2) a la
sienne, par la sonde `window.lwcPrice.debug()` : canvas rempli, bandeau démo
fidèle au paquet, refetch au changement d'intervalle, bascule € sur place,
historique remonté au pan, profil suivant la fenêtre visible, panes rendus au
cours quand on décoche tout, seuil d'alerte tracé, intervalle restauré au
rechargement. Le dialogue de
disposition (§3.6) passe par le même contrôle : un panneau déménagé
arrive dans sa cellule rempli dès l'ouverture de son onglet, le
déménagement survit au rechargement, et « Par défaut » suivi
d'« Appliquer » rend le rangement d'origine. L'option `--url` pointe le
contrôle sur un port d'essai, pour vérifier une modification sans
toucher au terminal qui tourne. C'est ce contrôle qui a mis au
jour le Store persisté qu'un `data=` initial réécrivait à chaque chargement.
Les captures qu'il dépose ont mis au jour deux
défauts qu'aucun test logique n'aurait signalés : la légende du graphique
recouvrait les bougies, et le carnet, trop haut pour son panneau, n'affichait
que les ventes. C'est aussi ainsi qu'a été repérée une feuille de style qui ne
prenait pas : les sélecteurs étaient stylés via `input:checked + span`, alors
que Dash enveloppe la case dans un `<span>` et marque le `<label>` d'une classe
`selected` — rien n'indiquait donc l'option active.

### 3.9 Câblage vérifié

`terminal/panels/__init__.py` déclare `PANELS`, et `app.py` enregistre les
callbacks en parcourant cette liste : ajouter un module suffit à le brancher.

`tests/test_terminal_wiring.py` vérifie qu'aucun panneau n'est écrit puis
oublié — il découvre les modules **sur le disque**, jamais via `PANELS`, sinon
un panneau absent de la liste échapperait aussi au contrôle. C'est l'erreur qui
a laissé le panneau ETF muet à sa création : écrit, mais ni placé dans la grille
ni enregistré, sans que rien ne le signale au démarrage.

Les onglets ont ajouté une variante moderne de cette erreur : un panneau écrit,
enregistré, mais absent de `CELLS` — donc affichable par aucun clic. Le test
compare pour cela les layouts écrits à ceux que `CELLS` référence.

```bash
python tests/test_terminal_wiring.py
```

### 3.10 Le canal push : l'horloge rapide inversée

L'interrogation à 250 ms convient à une boucle locale — les mesures de §3.1 le
montrent, un tour coûte 8 à 33 ms. Mais chaque tour paie un aller-retour HTTP
complet : sur un tunnel SSH lointain, la latence s'ajoute à la période et le
carnet prend du retard. Le chantier laissé conditionnel à l'étape 2 est
désormais fait : `terminal/push.py` pose une route WebSocket `/push` sur le serveur Flask qui porte Dash (flask-sock), et
pousse le rendu des six cibles rapides — carnet, profondeur, arbitrage et son
compteur, liquidations et leurs badges — à une cadence de 100 ms. Trois
cibles ont la leur. Les liquidations ne se rebâtissent qu'au changement de
leur fil (`LiquidationFeed.version` : un événement, un lien qui tombe ou
revient) et sinon à la seconde, pour ce que seul le temps fait bouger —
totaux de l'heure, liens muets : rebâties à chaque trame, elles valaient
2,2 ms sur 4,8, pour une table qui changeait une fois en dix secondes. La
profondeur part à la seconde, une figure entière que
des carnets jamais immobiles faisaient repartir à chaque trame — la moitié du
temps processeur d'un onglet ouvert, au profil. La cible prix, seule à pouvoir toucher au réseau,
est lue hors de la boucle (`_PriceRead`) : 140 à 350 ms toutes les cinq
secondes, et le délai de connexion entier si Binance se tait, ne figent plus
le carnet. Mesuré cible par cible sur un
hub réel : le carnet coûte 1,4 ms rendu et sérialisation compris,
l'arbitrage 1 ms, les liquidations rien ; la profondeur en coûtait 23,6
— dont vingt de validation `go.Figure` — et 0,9 depuis qu'elle est un
dictionnaire brut, que Dash et l'encodeur prennent tel quel. Une septième
cible s'y est jointe avec la voie A, à la cadence de l'horloge lente qu'elle
double : la bougie courante du panneau prix et ses derniers points
d'indicateurs, jamais la série entière — le navigateur tient la série, le
canal ne transporte que la mutation (§3.2).

**Le carnet ne passe pas par Dash.** Rendu en composants, il ne suivait pas
dix trames par seconde : dans Firefox headless, les minuteries de la page
dérivaient de 0,7 s à 6 s en trente secondes, jusqu'à la figer plus d'une
minute. Ralentir à 250 ms, et à 500 ms en plein écran, contournait la limite
sans la lever : mesurée le 5 novembre 2026 (minuterie de 50 ms pendant
20 s), la page ne tenait que 204 minuteries sur 400, et 30 en plein écran,
avec des gels jusqu'à 8,9 s ; sans le carnet, elle en tenait 361. Le coût
n'était pas au serveur, mais dans la réconciliation de chaque ligne. Le
carnet part donc en données déjà formatées (`book_payload` : prix dans la
devise, quantités, largeur des barres, ligne de spread, murs), et
`assets/book.js` construit le tableau une fois puis n'en touche que les
textes qui changent. Il ne le reconstruit qu'au changement du nombre de
lignes ou quand Dash a remonté le conteneur ; les couleurs sont des
variables CSS que le serveur pose sur ce conteneur (`BOOK_VARS`). Les deux
canaux aboutissent au même appel, `window.btcBook.render` : push.js y remet
la cible `book-data` au lieu de passer par `set_props`, et le repli écrit
le Store du même nom, qu'un callback clientside transmet. Il n'y a donc
qu'un moteur de rendu, et le formatage reste en Python, testé sans
navigateur. Résultat, à 100 ms, plein écran compris : 370 minuteries sur 400,
348 en plein écran, autant que sans carnet ; la trame pèse 0,9 Ko au lieu de
11 (1,7 au lieu de 25 en plein écran), et l'agrandissement passe en 0,4 s
au lieu de 1,8 à 4,6. `ui_smoke.py` vérifie que les lignes restent les
mêmes éléments d'une trame à l'autre.

Trois décisions structurent le canal :

- **Un seul code de rendu.** Les panneaux rapides exposent des fonctions
  `render(…)` pures, appelées par le callback Dash comme par le pousseur : ce
  que le navigateur reçoit est identique quel que soit le canal. Les trames
  `{id: {prop: valeur}}` sont sérialisées par le même encodeur que les réponses
  de callback, et `assets/push.js` les applique par `dash_clientside.set_props`
  — le même chemin de mise à jour, le navigateur ne voit pas la différence.
- **Des trames différentielles.** Le serveur compare la sérialisation de chaque
  cible à la dernière envoyée et n'expédie que ce qui change : un carnet
  immobile ne transmet rien, ce qui permet une cadence plus serrée que
  l'horloge remplacée sans coûter davantage.
- **L'horloge reste le repli.** push.js ne coupe `tick-fast` qu'une fois le
  canal ouvert, et la rallume dès qu'il tombe — serveur relancé, tunnel rompu —
  pendant qu'une reconnexion retente en arrière-plan (backoff plafonné à 30 s).
  Le bandeau dit toujours le canal en vigueur : « push » ou « poll ». Un
  serveur sans la route laisse simplement le terminal en interrogation.
- **La page connaît son serveur.** `push.BOOT`, tiré au chargement du
  module, identifie le processus : la page l'emporte dans une balise
  meta, `/api/boot` dit celui du serveur qui répond, et push.js les
  compare avant chaque connexion — s'ils diffèrent, la page se recharge.
  Sans cela, un onglet chargé avant un redémarrage continuait d'appeler
  le nouveau serveur avec le graphe de callbacks de l'ancien : le journal
  du service en a montré la rafale — `InvalidCallbackReturnValue`,
  `KeyError: None` sur le carnet — au réveil de la machine, quatre
  heures après un redémarrage que l'onglet, endormi avec elle, n'avait
  pas vu. La vérification passe par HTTP, pas par le canal : elle vaut
  aussi en régime d'interrogation, à chaque retentative.

Le rendu dépend d'un état qui vit côté navigateur — plateforme du carnet,
panneau agrandi, devise. Trois callbacks clientside le relaient à push.js, qui l'annonce
au serveur à chaque changement ; le pousseur suit ainsi les mêmes entrées que
les callbacks qu'il double, et vide son cache d'envoi quand l'état change. Le
plein écran a au passage clarifié son vocabulaire : le Store `maximized`
retient la *cellule* agrandie, un Store dérivé `expanded` dit quel *panneau*
est réellement regardé — distinction devenue nécessaire depuis que la
disposition configurable (§3.6) permet au carnet ou aux liquidations de vivre
ailleurs que dans leur cellule d'origine.

Le canal est couvert des deux côtés. Hors ligne, `test_push.py` traite
l'état annoncé comme une entrée hostile (un message malformé ne ferme jamais
le canal), vérifie que le rendu poussé suit la plateforme et l'agrandissement,
et que la sérialisation est stable à données constantes — l'hypothèse dont
vivent les trames différentielles ; `test_terminal_wiring.py` vérifie que la
route `/push` et ses relais d'état sont bien posés, le mutisme d'une route
absente étant de la même famille que celui du panneau oublié (§3.9). À
l'écran, `ui_smoke.py` contrôle le canal dans un vrai Firefox : le badge passe
à « push », le carnet continue de vivre l'horloge coupée, et l'agrandissement
— qui ne peut arriver que par le canal, le callback du carnet ne lisant
`expanded` qu'en State — fait bien passer le carnet de 8 à 20 niveaux.

### 3.11 Le régime service : gunicorn et systemd

Le terminal a deux régimes de lancement. À la main, `btcterm` sert
l'application sur le serveur de développement de Flask (Werkzeug) — le bon
outil pour une session : un processus, Ctrl-C pour finir. Pour un terminal
qui tourne en continu — toujours prêt derrière son tunnel SSH, journal et
alertes couvrant la séance entière sans navigateur ouvert — Werkzeug n'est
pas pensé pour des semaines de fonctionnement, et gunicorn prend le relais :

```bash
gunicorn --workers 1 --threads 32 --bind 127.0.0.1:8050 'terminal.wsgi:build()'
```

`terminal/wsgi.py` expose la fabrique. Elle lit sa configuration dans
l'environnement — `BTCTERM_NO_NEWS`, `BTCTERM_NO_JOURNAL`,
`CRYPTOPANIC_API_KEY`, les mêmes options que l'argv de la CLI, une unité
systemd n'ayant pas d'argv commode —, démarre le hub dans le worker et
confie son arrêt à `atexit` : le SIGTERM de systemd clôt le journal comme
un Ctrl-C. Deux choix structurants :

- **un seul worker, impérativement** — tout l'état du terminal vit en
  mémoire dans le hub ; un deuxième worker ouvrirait sa propre grappe de
  connexions aux plateformes et servirait les navigateurs depuis des états
  divergents. La concurrence vient des threads du worker, pas des
  processus ;
- **des workers threadés, et non gunicorn + gevent** comme la feuille de
  route l'envisageait : le monkey-patching de gevent transformerait les
  threads du hub en greenlets et se disputerait la main avec la boucle
  asyncio des connecteurs. flask-sock supporte les workers sync threadés ;
  chaque WebSocket `/push` occupant un thread pour la durée de la
  connexion, le compte est pris large (32).

**L'arrêt, et pourquoi `atexit` ne suffisait pas.** Le journal du
service a montré deux arrêts sur cinq expirés — « stop-sigterm timed
out », puis SIGKILL — et la lecture de gunicorn a dit pourquoi. Au
SIGTERM, le worker gthread cesse d'accepter puis **attend la fin de ses
requêtes en cours** (`graceful-timeout`, 30 s) avant de sortir ; or une
WebSocket `/push` est une requête en cours qui ne finit qu'au départ du
navigateur, et le délai d'arrêt de systemd est de 10 s. Sans navigateur
attaché, la sortie de l'interpréteur joint de toute façon les threads du
pool, où la même boucle bloquerait. `atexit`, lui, ne tourne qu'après —
trop tard. Deux pièces s'y répondent : le hub porte un événement
`stopping`, que `serve` (le corps de `/push`, §3.10) lit à chaque tour
de boucle avant de fermer lui-même la WebSocket — push.js repasse à
l'horloge et se reconnectera —, et `build` enveloppe les gestionnaires
de signaux que gunicorn a posés avant de charger l'application
(`_stop_on_signal`) : le signal lève l'événement, puis rend la main au
gestionnaire en place. Les WebSockets se ferment en un quart de seconde,
gunicorn n'a plus rien à attendre, et `atexit` referme le hub comme
avant. Détail qui compte : `signal.signal` rend un signal interruptif,
et gunicorn tient à ce que SIGTERM ne dérange pas les requêtes en cours
(`siginterrupt`) — la fabrique le redit après lui.

`terminal/systemd_service.conf` donne, en commentaires à adapter comme le
gabarit de la collecte de news, l'unité utilisateur `btcterm.service` —
`Restart=on-failure`, et `loginctl enable-linger` pour survivre à la
déconnexion. gunicorn est l'extra `serve` du paquet
(`pip install -e '.[serve]'`). `tests/test_wsgi.py` éprouve la fabrique
sans réseau ni gunicorn : la traduction de l'environnement en arguments du
hub, le démarrage, l'arrêt enregistré, la route `/push` posée, et un
SIGTERM réel qui lève `stopping` avant de passer au gestionnaire en
place ; `tests/test_push.py` vérifie que la boucle sort à l'arrêt du hub.

### 3.12 La démo statique : le panneau prix sans serveur

Une démo de btcterm sur GitHub ne peut pas être vivante — GitHub
n'exécute aucun serveur —, mais le panneau prix a une propriété qui
change tout : depuis la bascule de la voie A (§3.2), le serveur ne lui
sert que des données, et tout le dessin vit dans le navigateur.
`terminal/demo.py` en tire une page statique servie par GitHub Pages
depuis `docs/`.

Le constructeur fige un paquet `/api/klines` par intervalle — mille
bougies, enrichies après la même marge de calcul que la route, pour que
la MA 200 soit juste dès la première — dans `docs/demo/data/`, copie
`lwc-price.js`, la bibliothèque vendorisée et la feuille de style depuis
`terminal/assets/`, et écrit `docs/index.html` avec la configuration
(thème, police, profondeurs de page) que le vrai panneau reçoit de son
Store. Dans la page, `docs/demo/shim.js` détourne `fetch` **avant** le
chargement du rendu : `/api/klines` est servi depuis le paquet figé,
paginé par `time` exactement comme `terminal/lwc.py` — `limit` borne,
`before` exclut la bougie tenue, les autres tableaux suivent la fenêtre —
et `/api/profile` est recalculé sur place, port ligne à ligne de
`ind.volume_profile`. `docs/demo/page.js` tient la barre de titre sans
Dash, réglages dans localStorage. Le rendu, lui, n'est pas modifié d'une
ligne : il ne sait pas qu'il n'y a pas de serveur.

`tests/test_demo.py` vérifie le constructeur avec un hub factice, puis
fait tourner le shim sous Node contre un paquet généré et compare
pagination et profil à ce que produit le serveur — la démo dit la même
chose que l'original. Elle est datée dans son bandeau et ne se dit pas
« démo » au sens du repli hors ligne (§4.3) : ce sont de vraies données,
seulement arrêtées.

Les assets étant **copiés**, la démo se regénère après tout changement de
`lwc-price.js` — `python -m terminal.demo docs` —, ce qui rafraîchit
aussi l'instantané.

### 3.13 Lire et agir depuis le graphique

Le panneau prix affichait un crosshair sans rien en dire : un prix sur l'axe,
jamais la bougie sous la souris. `updateReadout` pose sous la légende l'ouverture,
les extrêmes, la clôture, la variation contre la clôture précédente, le volume et
les indicateurs ouverts, dans la devise affichée, et revient à la dernière bougie
quand la souris sort.

Le **CVD** cumule le delta de volume, achats agressifs moins ventes, tiré de
`taker_buy_base` que l'historique sauvegardé gardait sans usage (§2.10). C'est la
seule exception à la règle du serveur seul maître des indicateurs : le delta se
calcule au serveur, bougie par bougie, mais se cumule chez le client. Cumulé au
serveur, il repartirait de zéro à chaque page d'historique, et seul le client
connaît la série entière. Le zéro bouge donc à chaque page ; seule la forme se
lit. La série de démonstration n'a pas ce volume : son pane reste vide plutôt
que d'en inventer un.

**Alt + clic** écrit dans le Store `alert-config`, comme le panneau ALERTES : le
moteur, la liste et les lignes suivent sans chemin propre. Le seuil est enregistré
en dollars quelle que soit la devise affichée, et un clic à moins de six pixels
d'un seuil existant le retire.

Les **lignes de tendance** (`T`, puis glisser) sont des séries à deux points, pas
des dessins sur le canvas : elles suivent le zoom, le pan et l'échelle sans rien
à recalculer, et `autoscaleInfoProvider` les tient hors de l'échelle des prix.
Elles se gardent en dollars et en heures absolues, dans le navigateur, ce qui les
fait survivre à la devise, à l'intervalle et au rechargement : à l'affichage,
leurs bouts retombent sur les bougies de l'intervalle courant. Chacune se
prolonge jusqu'à la dernière bougie, en géométrique si l'échelle est
logarithmique, puisque c'est là qu'elle est droite.

La **mesure** (Ctrl + glisser) dit le mouvement entre deux points : pourcentage,
écart de prix, bougies, durée. Le cadre reste à l'écran, une mesure se lisant
après le geste ; son Échap est capté avant celui du plein écran, comme celui du
zoom.

Les **raccourcis** (`assets/terminal.js`) cliquent sur le contrôle existant :
l'état reste aux callbacks, et le panneau retiré de la grille rend simplement sa
touche sans effet. Le focus d'une case cochée à la souris ne les fait pas taire ;
seuls les champs de saisie gardent le clavier.

La **ligne de commande** (`/`) suit le même principe et lit ses cibles dans la
page, pour qu'aucune liste de panneaux ne soit à tenir à jour et qu'un
réarrangement de la grille ne la casse pas. Une commande inconnue reste affichée
en rouge : une faute de frappe se corrige, elle ne doit pas s'évanouir.

Les **niveaux clés** (case `NIV`, touche `N`) sont des lignes de prix sur les
chandeliers, comme le POC : plus hauts et plus bas de la veille, de la semaine
et du mois passés, ouvertures en cours. `analysis.key_levels` les tire des
bougies journalières que l'alerte de la MA 200 tient déjà en cache, en UTC et
la semaine partant du lundi, et `/api/levels` les sert en dollars ; le
navigateur les convertit avec le reste et les relit toutes les cinq minutes, le
jour qui tourne les changeant. Une période que les bougies ne couvrent pas —
la bougie du jour manquante, Binance muet — n'a pas de niveau : un repère
absent vaut mieux qu'un repère faux. Deux niveaux au même prix fusionnent
(« B S-1/M-1 ») : deux lignes confondues superposaient leurs étiquettes sur
l'axe. Les couleurs sont des gris, de plus en plus clairs du jour au mois — le
violet, l'orange et le bleu tiennent déjà les moyennes et Bollinger. La case a
fait déborder de 36 px la barre de titre du prix, que `ui_smoke.py` surveille :
le titre y est passé de « BTC/USDT » à « BTC », la paire étant au bandeau, et
l'écart entre libellés de 4 à 3 px.

La **carte des liquidations** (case `LIQ`, touche `M`, `/carte`) est un
canvas à gauche du pane du cours, le profil de volume tenant la droite :
`liquidations.price_map` répartit le notionnel liquidé de la plage visible en
soixante tranches de prix, longs et shorts à part, et `/api/liqmap` le lit
dans le journal, qui garde trente jours. Redemandée quand la plage bouge et
chaque minute. Sa légende, posée sous celle des moyennes — en bas du pane, le
volume la recouvrait —, dit le total et les plateformes présentes : depuis le
Cambodge, Bybit seul, Binance taisant son flux, et rien pendant le sommeil de
la machine. Elle a été construite pour la cible d'un serveur allumé en
permanence. La case a fait déborder la barre de titre de 29 px : ses libellés
y passent à 0,06 em d'espacement. `/liq` reste à l'onglet des liquidations.

### 3.14 Le panneau ANALYSE : mettre les signaux à l'épreuve

Le graphique pose des flèches d'achat et de vente depuis l'origine, sans que rien
ne dise ce qu'elles valent. `btcterm/analysis.py` le mesure, et l'essentiel de ce
module tient dans ce qu'il refuse de faire, parce qu'un bilan faux affiche des
chiffres aussi plausibles qu'un bilan juste.

- **Pas de regard sur le futur** : un signal se forme à la clôture, l'entrée se
  fait à l'ouverture suivante.
- **Une référence** : dans un marché qui monte, n'importe quel achat gagne. La
  colonne qui compte est l'**écart** à la moyenne de toutes les bougies, dans le
  sens du signal, et `test_analysis.py` vérifie qu'il est nul quand tout monte.
- **L'effectif** : sous trente signaux, les chiffres restent affichés mais
  éteints.

Les réserves sont écrites sous le tableau : sans frais, fenêtres qui se recouvrent
quand deux signaux sont proches, et description du passé plutôt que promesse.

Les **règles** répondent à la question que ce bilan pose. Trois tenues sont
comparées sur la même période, frais compris : rester investi, ne l'être
qu'au-dessus de la MA 200, ou que quand la MA 9 passe la MA 26. La règle lit la
clôture et n'agit qu'à l'ouverture suivante : elle encaisse donc en entier la
bougie qui la fait sortir, ce qu'un krach simulé vérifie. Sur le journalier
depuis 2018, à 0,1 % par passage, les deux règles font à peu près le rendement de
la détention (25,8 et 27,2 % par an contre 24,7) en n'étant investies que 53 %
du temps, avec un repli maximal de 64 % contre 77. Les liquidités n'y rapportent
rien, et neuf ans d'un seul actif ne disent pas ce qui se passera ensuite.

La **carte des rendements mensuels** (`monthly_returns`) lit les bougies
mensuelles, mois en cours compris : une ligne par année, son total composé au
bout, et pour chaque mois sa moyenne, la part de mois haussiers au survol. Neuf
années font neuf points par mois — c'est une description, pas une régularité
établie, et la réserve est écrite sous la carte comme celles du bilan. L'**ATR** à quatorze bougies, en % du cours, rejoint le
résumé des intervalles : c'est lui que le panneau POSITION lit (§3.15).

Le bilan lit 20 000 bougies closes au plus (23 ms) ; `prepare_price_frame` n'est
pas repris, le Connors RSI coûtant cher pour une colonne que le signal ne lit
pas, et `test_analysis.py` compare les deux pour qu'ils ne divergent jamais. Sa
profondeur vient du complément en tâche de fond (`_backfill_loop`) : une page
toutes les deux secondes, de l'intervalle mensuel à l'heure. Le quart d'heure et
la demi-heure, trois fois et demie plus coûteux, se remplissent à l'usage.
Mesuré à la première exécution : 129 appels, 125 000 bougies, 16 Mo.

### 3.15 Le panneau POSITION : la vôtre, et combien en prendre

Un terminal de marché ne dit pas ce que le marché fait à *votre* position. Le
panneau POSITION (onglet de la cellule ETF) pose deux questions côte à côte :

- **la position tenue** — sens, quantité, entrée, stop : valeur, résultat
  latent, distance qu'il reste jusqu'au stop (négative s'il est franchi) et
  multiple du risque pris (`R`), le résultat rapporté à la perte prévue à
  l'entrée. Un stop du mauvais côté n'a pas de multiple ;
- **la taille qu'autorise un risque** — capital et pourcentage accepté, le stop
  étant placé à quelques ATR journaliers (deux par défaut) : c'est la
  volatilité du moment qui dit combien en prendre, pas l'envie. Le levier
  implicite passe en rouge au-delà de 1, quand le capital ne couvre plus la
  taille.

Le terminal **ne parle à aucun compte** : tout se saisit à la main, en dollars
comme les seuils d'alerte, et les champs vivent dans le `localStorage`. Le
serveur ne les reçoit que le temps du calcul (`position_state`,
`position_size`, testés sans Dash) et n'en écrit rien.

Le **bandeau** affiche le résultat même panneau replié, comme il suit la
devise : un callback clientside recopie sens, quantité et entrée dans le Store
global `position`, persisté, que le bandeau lit à chaque tour d'horloge lente.
Un panneau caché n'étant pas dans la page (§3.5), c'est ce Store, et non les
champs, qui porte la position entre deux ouvertures de l'onglet.

### 3.16 Le panneau OPTIONS : ce que le marché craint

Le dix-septième panneau, en onglet de la rangée basse — la courbe des
échéances et l'open interest par strike se lisent en longueur —, montre le
module `options` (§2.11) en quatre vues :

- **VOL** — DVOL contre la volatilité réalisée à trente jours, sur un an.
  L'écart est la prime des options, positive d'ordinaire ; elle se retourne
  quand le marché bouge plus qu'il ne l'attendait ;
- **TERME** — la volatilité à la monnaie de chaque échéance et ses deux ailes
  à 25 delta, sur un axe de temps et non de rang : la courbe a la forme du
  calendrier. Sous elle, la base annualisée des futures, en courbe : des
  barres, sur un axe de dates où deux échéances sont à un jour d'écart, se
  réduisaient à des traits ;
- **HISTO** — ce que seul le journal garde (§2.7) : DVOL et la volatilité à
  un mois en haut, le skew 25 delta en bas, sur trente jours, coupés aux
  interruptions de séance ; tant qu'il y a moins de deux points, la vue dit
  l'historique en constitution et depuis quand ;
- **STRIKES** — l'open interest d'une échéance par strike, calls contre puts,
  le forward et le max pain en verticales, les strikes bornés à 0,6–1,5 fois
  le forward. Le choix de l'échéance n'apparaît que dans cette vue, limité
  aux six qui pèsent au moins 2 % de l'open interest ; mémorisé, il cède à
  l'échéance par défaut quand la sienne est passée, et cette dernière est
  cochée pour que le sélecteur dise ce que montre le graphique.

Le titre porte DVOL, le skew 25 delta à un mois et le ratio put/call ; le plein
écran y ajoute la réalisée et la prime, la volatilité à un mois, le put/call
en volume, l'open interest total, le max pain et la base à trois mois. Le
panneau a sa propre minuterie d'une minute — l'horloge rare du terminal en
compte cinq —, qui n'existe, comme ses callbacks, que s'il est affiché. Les
strikes restent en dollars quelle que soit la devise choisie : ce sont les
termes des contrats, pas un prix du moment (§4.3).

Le **bandeau** affiche DVOL et sa variation depuis la veille ; il lit le cache
sans jamais appeler Deribit (`dvol_cached`), comme le taux € : il tourne toutes
les deux secondes, et une source en panne y bloquerait le cours le temps de
son délai. Le clic sur DVOL ouvre le panneau, où qu'il soit rangé.

### 3.17 Le panneau MARCHÉS : le bitcoin parmi les autres

Le dix-huitième panneau, en onglet de la rangée basse juste après la macro dont
il prolonge la question, montre le module `markets` (§2.12) : à gauche le
graphique, à droite le tableau des cinq marchés et du bitcoin.

- **PERF** ramène chaque série à 100 au début de la fenêtre (1 mois à 1 an) :
  un seul axe, des unités communes, plutôt que des échelles superposées ;
- **CORR** trace la corrélation glissante sur trente séances, de −1 à 1.

Le graphique ne porte que trois marchés — S&P 500, or, dollar — et le bitcoin.
Les couleurs ont été validées par le calcul (le script du guide de
visualisation) et non à l'œil : la palette du terminal mettait l'or orange à
côté du bitcoin jaune (écart de 8 en vision normale, sous le plancher de 15),
et au-delà de trois courbes qui se croisent aucune palette ne reste
discernable pour un œil daltonien. Les trois premiers pas de la palette de
référence passent tous les contrôles sur la surface des panneaux ; le bitcoin,
la référence, est en encre claire et en trait épais. Le Nasdaq, qui redit
presque le S&P 500, et le taux, qui n'a pas l'unité des autres, ne sont qu'au
tableau. En plein écran, chaque courbe porte son nom au bout, écarté de ses
voisins ; dans la rangée basse, deux noms se superposaient, et la légende y
suffit. Le titre donne la corrélation à 30 séances au S&P 500, à l'or et au
dollar — écrite « corr. » et non ρ, que la barre de titre en majuscules
changeait en P.

### 3.18 Le panneau SOURCES : ce qui répond, et ce qui se tait

Le terminal lit une vingtaine de sources ; le bandeau dit combien sont en
panne (`failing_sources`), mais le détail — laquelle, depuis quand, pourquoi —
n'était qu'au journal du service, que cette séance a dû relire à chaque
incident. Le dix-neuvième panneau, en onglet de la cellule d'arbitrage à côté
du journal, le pose à l'écran ; le clic sur « ⚠ N sources » au bandeau
l'ouvre (`REVEALS`, comme la cloche et DVOL).

`hub.sources_status` compose trois sections. Les **flux temps réel** : chaque
carnet (coupé, muet sans mise à jour depuis trente secondes, ou ok) et chaque
lien de liquidations (muet au même seuil d'un quart d'heure que son panneau).
Les **sources REST** : `TTLCache.status` rend par clé la dernière valeur
fraîche, le secours, la panne vue dans `FAILING_WINDOW` et la dernière
erreur — que le cache garde désormais en une ligne (`brief_error`), le
journal ne disant que la première ; les clés se regroupent par famille, une
par intervalle de chandeliers ou page d'historique ne faisant qu'une source,
la plus mal en point donnant l'état et la plus fraîche l'âge. Les calculs
mis en cache (bilans de l'ANALYSE) n'y figurent pas. Les **tâches de fond** :
collecte des news, instantané de marché, résolution DNS de secours. Le
panneau nomme les familles pour un lecteur (« Agrégats de marché ·
CoinGecko »), met les pannes en tête, et écrit l'état en toutes lettres à
côté de sa pastille. Il a sa propre minuterie de dix secondes, qui n'existe
que s'il est affiché.

### 3.19 Le panneau RUBAN : les gros ordres

Le vingtième panneau, en onglet du carnet dont il est le pendant, montre le
module `tape` (§2.13) : les gros ordres de l'heure au-dessus d'un seuil
réglable (100 k$, 250 k$, 1 M$), du plus récent au plus ancien, ceux d'un
million et plus en gras. Le titre ne garde que la part des achats agressifs
sur cinq minutes, toutes tailles — le détail par place au survol — et les
liens absents ; les montants et le seuil vont sur une ligne à eux. Le premier
rendu les mettait tous au titre, qui débordait de 180 px dans la cellule
étroite du carnet et cachait le seuil ; `ui_smoke.py` surveille désormais la
largeur des deux lignes. Montants et prix suivent la devise du panneau prix :
c'est le marché du moment (§4.3).

### 3.20 Le panneau SYNTHÈSE : la journée en une page

Le vingt et unième panneau, en onglet des news, affiche `brief` (§2.14) : une
ligne par sujet, le nom en petites capitales, le texte coloré selon son sens —
hausse, baisse, à surveiller —, le détail au survol. La couverture du journal
s'écrit en tête, en jaune, et le titre dit « partielle » ; il dit aussi l'heure
de l'envoi du matin quand `BTCTERM_BRIEF` est posé, et si ntfy manque pour
l'envoyer. Refaite à l'horloge rare : une synthèse de la journée n'a rien de
neuf à dire toutes les deux secondes.

La cellule des news est passée à quatre onglets, et la barre de titre à deux
lignes — `ui_smoke.py` l'a vu. Le badge du Fear & Greed y ne garde que le
chiffre, sa couleur disant la zone ; le libellé passe au survol, et dans la
synthèse.

## 4. Patrons transverses

### 4.1 Deux modèles d'acquisition de données

**Polling REST** (chandeliers, taux de change, flux ETF, news) — appel HTTP
synchrone, `try/except` large, valeur de repli en cas d'échec. Simple, latence
de l'ordre de la seconde. Dans le terminal, ces appels passent tous par les
caches du hub, de sorte que deux panneaux réclamant la même donnée dans la même
seconde ne paient qu'un aller-retour.

**Streaming WebSocket** (carnets, arbitrage) — une coroutine par exchange,
boucle infinie de reconnexion, état mutable partagé. Latence de l'ordre de
100 ms.

### 4.2 Séparation producteur / consommateur

Le temps réel sépare systématiquement l'acquisition (réseau) du rendu, le
producteur ne partageant avec le consommateur qu'un état protégé par verrou :

```
terminal/            MarketHub : thread démon (event loop asyncio)
                     → dict[str, OrderBook] verrouillés → callbacks Dash
```

Le consommateur ne prend le verrou que pour **copier** un instantané de l'état,
puis met en forme hors verrou. Les collecteurs REST, eux, restent synchrones
dans le callback : leur latence est absorbée par le cache du hub.

### 4.3 Dégradation contrôlée

Chaque source distante a une stratégie de repli explicite :

| Source | Repli |
|---|---|
| toute donnée REST déjà servie | dernière valeur connue, conservée par `TTLCache` |
| nom d'exchange résolu en 127.0.0.1 par le FAI | résolution de secours par DNS sur HTTPS (`resolver`, §2.9) |
| chandeliers Binance, sans rien en cache | historique sauvegardé, sans la bougie en cours, signalé par un bandeau |
| chandeliers Binance, sans rien en cache ni sur disque | série de démonstration, signalée par un bandeau |
| taux EUR | dernier taux lu, ou à froid `EUR_FALLBACK` ; en euros, ⚠ au bandeau et bandeau orange du graphique ; source réinterrogée une fois par minute |
| masse monétaire M2 | tableau vide, le panneau macro le dit |
| marché à terme Binance | tableau ou dictionnaire vide, le panneau perpétuel le dit |
| agrégats de marché, données de chaîne | idem : le panneau affiche son indisponibilité |
| ticker 24 h | dict vide, le bandeau affiche `—` |
| WebSocket (tous) | reconnexion (3 s fixes, ou backoff exponentiel plafonné à 30 s) |
| flux RSS (`news`) | le feed en échec est sauté, les autres continuent |
| CryptoPanic sans clé | source simplement désactivée |

Deux précautions encadrent le premier repli. **Le secours se dit.** Le
journal de séance a montré six heures et demie d'instantanés sans
financement ni open interest — Binance Futures muet après un réveil,
CoinGecko répondant à côté — sans une ligne dans le journal du service :
le cache servait sa dernière valeur, et rien ne le disait. `TTLCache`
journalise désormais la première panne d'une source (clé, erreur, âge de
la valeur servie) puis son rétablissement avec la durée — une ligne
chacun, pas une par lecture. Cette durée est celle de la panne *vue* :
une source restée sans lecture plus de `FAILING_WINDOW` après son dernier
échec, parce que son panneau était replié ou que la machine dormait, n'a
pas été en panne tout ce temps. Sa ligne de retour dit alors
depuis quand elle n'avait pas été lue et l'intervalle entre ses échecs
(« à sa première lecture depuis 1915 min (panne constatée : moins d'une
minute) »), là où le journal annonçait trente-deux heures de panne pour un
seul échec. La règle vaut aussi à l'échec : celui qui suit un tel silence
ne prolonge pas la panne d'avant, dont on ignore ce qu'elle est devenue,
mais en ouvre une nouvelle, et sa ligne dit la précédente (« en panne à sa
première lecture depuis 180 min : … (panne précédente constatée :
5 min) »).

Le réveil de la machine tordait ces durées autrement. Le réseau y revient
en quelques secondes — quatre à vingt-huit aux réveils mesurés —, et les
lectures qui n'attendent pas la boucle d'observation, onglet qui se
réveille ou pousseur, échouaient dans l'intervalle : dix des soixante-six
pannes journalisées en une journée tombaient dans la minute d'un réveil,
rétablies « après 0 min ». Pire, un tel échec confirmait la panne d'avant
le sommeil, et le journal disait « klines:1h:400 de nouveau servie après
95 min » de quatre-vingt-quatre minutes de sommeil. Dans les
`WAKE_GRACE` secondes d'un réveil (une minute, `SleepWatch` au §2.7), un
échec est donc servi de secours mais ne se journalise pas et ne compte pas
au bandeau. Il armait aussi l'attente de Binance, alors fixée à une
minute : le graphique repartait soixante et une secondes après le retour
du réseau, à chacun des réveils relus.

Cette attente a été revue pour elle-même. Binance injoignable, le
pousseur attendrait sinon chaque délai de connexion, et la source n'est
retentée qu'après un délai (`KLINES_RETRY`) ; mais une minute fixe figeait
le graphique soixante secondes pour un accroc que le premier nouvel essai
effaçait — douze des vingt-trois pannes de Binance relues au journal se
rétablissaient en deux minutes au plus, au premier ou au deuxième essai.
Le délai part désormais de cinq
secondes (`KLINES_RETRY_FIRST`) et suit la durée de la panne déjà vue,
une minute au plus ; dans la minute d'un réveil, il reste à cinq secondes.
Le temps écoulé, et non le compte des échecs, le fait grandir : plusieurs
lecteurs qui échouent ensemble ne le doublent pas d'un coup. Et une
lecture refusée pendant l'attente lève `SourceBackoff`, que le cache sert
de secours sans compter de panne : la source n'a pas été appelée. Un seul
délai de connexion, le 6 novembre à 12:30:01, avait écrit onze lignes au
journal — la panne réelle du quart d'heure, puis chaque intervalle lu
pendant l'attente, « en panne : Binance en attente de nouvel essai »,
suivi de son retour ; ces échos faisaient trente-cinq des quatre-vingt-dix-huit
lignes de panne du journal en trois jours.

Un collecteur qui n'a rien obtenu
lève au lieu de rendre un vide : `fetch_perp_snapshot` rendait `{}`
quand ses deux endpoints tombaient, et le cache le stockait comme une
lecture réussie, invisible au secours. L'erreur y tient en une ligne
lisible : `str()` d'une erreur `requests` commence par le pool et l'URL
complète et finit par la cause — `NameResolutionError`,
`ConnectTimeoutError` —, si bien que les premières lignes de panne du
journal, tronquées, disaient toutes « Max retries exceeded » et rien de
ce qui s'était passé ; `sources.brief_error` garde l'hôte et la cause
(`fapi.binance.com : NameResolutionError`), que le perpétuel compose
aussi pour ses deux endpoints. **Le secours ne s'écrit pas.** Une
valeur datée servie à un panneau est un moindre mal ; écrite dans
l'historique comme si elle datait de l'instant, c'est un mensonge sur
des heures. Le cache tient donc, par clé, si la dernière lecture a été
servie de secours (`stale`) — il ne disqualifie que ce qu'il a vu
tomber —, et `record_market_snapshot` n'écrit pas le secours : NULL,
qui dit vrai. `tests/test_cache.py` couvre
les trois. **Le secours n'est gardé que pour qui le lit.** Une page
d'historique est une clé à usage unique, que le cache gardait jusqu'au
redémarrage ; toutes les dix minutes, `TTLCache.sweep` oublie ce que
personne n'a lu depuis une heure (`CACHE_IDLE`) et dont la durée de vie
est passée. Une lecture par `peek` compte : le taux € que le pousseur lit
sans réseau reste. Une source qu'on relit, même en panne, garde sa
dernière valeur ; celle d'un panneau replié depuis plus d'une heure, et
périmée, est relue à la source à sa réouverture, comme au démarrage.

Le repli sur données de démonstration mérite une précaution : un graphique
synthétique qu'on ne distingue pas d'un vrai est pire que pas de graphique du
tout. `generate_demo_ohlcv` marque donc sa sortie d'un `attrs["demo"]`, que
`/api/klines` relaie en champ du paquet et sur lequel lwc-price.js pose un
bandeau orange. La série est une marche
aléatoire log-normale paramétrée en annualisé (55 % de volatilité, 40 % de
dérive), ramenée à la durée d'une bougie en racine du temps et recalée pour
finir sur un prix plausible — sans quoi une longue série mensuelle dériverait
vers des valeurs absurdes.

Le taux EUR a rejoint la règle quand le bandeau s'est mis à suivre la
devise du panneau prix : `fetch_eur_rate` rendait une constante au lieu
de lever, que le cache stockait une heure comme une lecture réussie, à la
place du dernier bon taux et sans une ligne de journal — 5 % d'écart sur
le cours (0,924 contre 0,882 en septembre 2026) qu'aucun écran ne
signalait. Il lève désormais ; `hub.eur_rate` sert le dernier taux ou, à
froid, `EUR_FALLBACK`, et `eur_rate_stale` le dit au bandeau (⚠ et
infobulle) comme au graphique, qui pose son bandeau orange en euros.
Pendant la panne, la source n'est réinterrogée qu'une fois par
`EUR_RETRY` : le bandeau lit le taux toutes les deux secondes, et chaque
essai peut coûter les cinq secondes du timeout.

Le carnet suit la même devise, et le même ⚠ sur sa ligne de spread, mais
ne lit pas ce taux de la même façon : le pousseur le rend quatre fois par
seconde et dormirait le temps de l'appel. `eur_rate_cached` ne sert donc
que le cache, que le bandeau tient à jour — dès que la devise est l'euro,
il est dans la page quel que soit l'agencement de la grille.

`terminal/fx.py` pose la règle pour tous les panneaux : ce qui décrit le marché
du moment se convertit au taux du moment — carnet, arbitrage, liquidations,
open interest, capitalisation. L'arbitrage y a gagné son unité : ses prix
n'en portaient aucune, et rien n'aurait distingué des dollars d'euros. Les flux
ETF et le journal restent en dollars, faits passés qu'un taux d'aujourd'hui
fausserait, comme les seuils d'alerte, réglages comparés à des dollars.

### 4.4 Palettes de couleurs

Tout est en thème sombre. Le terminal centralise sa palette dans
`terminal/theme.py` (dictionnaire `C` et styles partagés) ; les outils en ligne
de commande gardent la leur en tête de fichier. Convention constante :
**vert = achat/hausse, rouge = vente/baisse**.

L'indice Fear & Greed s'y range plutôt que d'adopter la lecture à contre-courant
qui voudrait la peur verte — la zone où l'on achète. Deux conventions dans un
même panneau se contrediraient à l'écran : le chiffre du badge et la bande où la
courbe le pose doivent être de la même couleur. `charts.fear_greed_color` tient
cette règle une seule fois (rouge sous 45, jaune entre 45 et 55, vert au-dessus)
et sert les deux rendus ; les zones extrêmes ne sont que la teinte de leur
voisine, appuyée.

---

## 5. Détail des outils restants

Les quatre scripts que le terminal a remplacés ne sont plus décrits ici : ils
ont été supprimés à l'étape 4 ([§7](#7-feuille-de-route-vers-le-terminal)), et
leur contenu vit maintenant dans le socle et les panneaux, comme celui de
`m2supply.html`, repris par le panneau macro à l'étape 5. La TUI d'arbitrage
(`arbitrage/main.py`) les a suivis à la clôture de la feuille de route,
l'arbitrage une fois rendu : elle suivait le même moteur que le panneau du
même nom (`btcterm/arbitrage.py`) dont elle n'était qu'une seconde façade, et
son intérêt propre — vivre sans navigateur — ne répondait pas au besoin
énoncé, une station de travail multi-panneaux servie en local et atteignable
par tunnel SSH (§1). Restent deux outils **assumés** : ils ne font double
emploi avec aucun panneau — ils produisent ou exportent ce que le terminal ne
fait qu'afficher, et gardent leur utilité quand il ne tourne pas.

### 5.1 `etf_bitcoin_flows.py` — flux ETF

Pipeline en trois fonctions :

```
fetch_flows()  GET farside.co.uk/bitcoin-etf-flow-all-data/
    │              (User-Agent navigateur)
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

**Le doublon `etf.py` a été supprimé à l'étape 4.** C'était la mouture
antérieure du même script, restée en arrière sur six points :

| Point | `etf.py` (supprimé) | `etf_bitcoin_flows.py` |
|---|---|---|
| Entrée de `read_html` | chaîne HTML (déprécié) | `io.StringIO(...)` |
| En-têtes `MultiIndex` | non gérés | aplatis, `Unnamed*` ignorés |
| Valeurs manquantes | `NaN` conservés | `fillna(0.0)` |
| Colonnes vides | affichées | élaguées si leur somme en valeur absolue est nulle |
| Rendu | `DataFrame.to_string` | `tabulate` |
| `--days` par défaut | 15 | 90 (`0` = tout) |

Seule la v2 avait été migrée sur le socle (`sources.fetch_etf_flows`) : migrer
la v1 n'aurait fait qu'entretenir un doublon voué à disparaître.

### 5.2 `news/btc_news.py` — BTC News Tracker

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

**Ce qui n'est plus ici** — schéma, scoring et collecte sont partis dans
`btcterm/newsdb.py` (§2.4), que le terminal partage : même base, mêmes
pondérations, mêmes déduplications. Les fonctions `fetch_*` du script sont
devenues des enveloppes de trois lignes autour de `newsdb.collect_*`, leur seul
apport étant les rappels d'affichage.

`KEYWORDS` reste le point d'extension principal du scoring : une somme de
pondérations sur recherche de sous-chaîne dans `titre + résumé` en minuscules,
regroupées par thème (macro/régulation 15-25, adoption 10-18, on-chain 8-22,
événements extrêmes 18-20, signaux de prix 8-20, termes génériques 5-10).

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

---

## 6. Environnements Python

| Emplacement | Contenu | Utilisé par |
|---|---|---|
| `venv/` (racine, Python 3.14) | l'ensemble de `requirements.txt` : pandas, numpy, requests, dash, plotly, gunicorn, websockets, lxml, beautifulsoup4, tabulate, feedparser | le terminal et tous les outils |
| `news/.venv` (optionnel, créé par `setup.fish`) | feedparser, requests | usage isolé du tracker |

Un `requirements.txt` à la racine déclare l'ensemble des dépendances, regroupées
par usage (socle, terminal web, temps réel, ETF, news). La suppression des
scripts hérités en a retiré trois : `matplotlib`, `ccxt` et
`dash-bootstrap-components` n'avaient plus d'utilisateur.

```bash
pip install -r requirements.txt
```

Le terminal ayant désormais un point d'entrée unique, le `pyproject.toml` que
la version précédente de ce paragraphe annonçait existe : `pip install -e .`
installe la commande **`btcterm`**, équivalente à `python -m terminal.app`.
Trois choix y sont commentés :

- les **paquets sont nommés explicitement** (`btcterm`, `terminal`,
  `terminal.panels`) — `news/` est un sous-projet à scripts, pas un
  paquet, et une découverte automatique embarquerait `tests/` ;
- `terminal/assets/` est déclaré en **package-data** : sans cela, une
  installation non éditable servirait un terminal sans feuille de style ni
  raccourcis clavier — le défaut ne se voyant qu'à l'écran, il est vérifié en
  inspectant la wheel ;
- les dépendances du projet sont **celles du terminal** ; `tabulate`, qui
  ne sert qu'à l'export ETF en ligne de commande, est dans l'extra `cli`
  (`pip install -e '.[cli]'`), et `gunicorn`, qui ne sert qu'au régime
  service (§3.11), dans l'extra `serve`.

Les scripts restent lançables sans installation : ils trouvent `btcterm/`
parce que Python ajoute le répertoire du script au chemin d'import, et les
deux sous-projets remontent explicitement d'un niveau.

---

## 7. Feuille de route vers le terminal

L'objectif est de passer de « N scripts, N fenêtres » à « un terminal, N
panneaux ». Les étapes ci-dessous sont ordonnées : chacune réduit le coût de la
suivante.

**Où en est-on.** Les cinq étapes sont faites — socle extrait, couche de rendu
tranchée, données mutualisées, doublons supprimés, couverture complète :
dix-huit panneaux couvrent le prix, la liquidité, l'arbitrage, les
liquidations, les flux ETF, le marché à terme, les options, les news, le
calendrier macro, la macro, les marchés traditionnels, la dominance, la chaîne
et la relecture de séance. La piste de confort laissée ouverte à la pause
précédente — une disposition de grille configurable — est faite (§3.6) :
chaque panneau se range dans la cellule de son choix, et le rangement
survit au rechargement. Le dernier chantier, resté conditionnel depuis
l'étape 2, est fait à son tour : le serveur pousse les panneaux rapides par
WebSocket quand le navigateur peut l'entendre, l'interrogation restant le
repli (§3.10). La feuille de route est soldée.

Un chantier de plus a été mené depuis cette clôture, et soldé : la
**voie A**, migration du panneau prix vers un rendu Lightweight Charts
côté navigateur (§3.2) — sept phases, du vendoring de la bibliothèque et
du contrat de série jusqu'à la dépose du rendu Plotly du prix et du
drapeau de transition `BTCTERM_LWC`. Le serveur ne sert plus que des
données au panneau prix — et, depuis le troisième chantier ci-dessous,
au perpétuel ; les autres panneaux restent des figures Plotly.

Trois chantiers ont été ouverts ensemble après la version 1.0, tirés des
limites que la clôture assumait :

1. ~~**Historiser ce que les API ne gardent pas**~~ — fait : le journal
   accumule un instantané de marché toutes les cinq minutes (§2.7), le
   panneau dominance trace la tendance ainsi construite et l'open
   interest du perpétuel remonte au-delà des trente jours de Binance.
2. ~~**Enrichir les alertes**~~ — fait : trois règles relatives lues sur
   la bougie horaire close (§2.8) — écart à la MA 200, RSI hors bornes,
   signal gradué fort —, réglées du panneau ALERTES et muettes sur la
   série de démonstration.
3. ~~**Étendre le rendu LWC au panneau perpétuel**~~ — fait : `/api/perp`
   et `assets/lwc-perp.js` (§3.2) — histogramme de financement signé,
   open interest en ligne sur la série que le chantier 1 allonge, poll à
   l'horloge rare sans canal push. La décision d'étape 2 est révisée
   pour ce seul panneau.

Un quatrième les a suivis, dans le même esprit de convergence :

4. ~~**Le panneau JOURNAL**~~ — fait : la relecture de séance entre dans
   le terminal (§2.7), en onglet de la cellule d'arbitrage — la CLI
   `python -m btcterm.journal` reste, mais plus rien n'oblige à sortir
   du terminal pour relire ce qui a sonné, rapporté ou liquidé.

Puis deux prolongements des chantiers 1 et 2 :

5. ~~**Le financement dans les instantanés**~~ — fait : la colonne
   `funding_rate` rejoint `market_snapshots` (ALTER TABLE sur les bases
   antérieures), et `funding_history_extended()` prolonge l'histogramme
   du perpétuel comme l'open interest l'était déjà (§2.7).
6. ~~**Alerte sur la dominance**~~ — fait : neuvième règle du moteur
   (§2.8), assise sur l'historique journalisé — un glissement de la
   dominance BTC au-delà du seuil en points sur vingt-quatre heures.

Ce cycle est clos à son tour, et l'empaquetage passe en **1.1.0** : six
chantiers livrés depuis la 1.0, tous tirés des limites que sa clôture
assumait, aucun n'en laissant de nouvelle. Au passage, le point d'entrée
`terminal/app.py`, grossi au fil de ces chantiers, a été découpé en
modules d'une responsabilité chacun — grille, disposition, bandeau,
assemblage (§3).

Un septième chantier a été ouvert après la 1.1, dans la ligne du
deuxième — étendre la surveillance passive à ce que le terminal montre
déjà :

7. ~~**Alertes ETF et on-chain**~~ — fait : deux règles de plus au
   moteur (§2.8), lues sur les caches que les panneaux ETF et on-chain
   tirent déjà — un jour de flux ETF au-delà du seuil, en entrée comme
   en sortie, et un réseau chargé (mempool gonflé, blocs lents). Onze
   règles, toutes réglées du panneau ALERTES.

Deux corrections tirées de l'usage ont suivi, sans ouvrir de chantier —
nées l'une et l'autre d'un panneau qui semblait en panne sans l'être :

- **La cloche ouvre le panneau alertes** (§2.8) — elle comptait les
  sonneries de l'heure sans offrir le moindre chemin vers elles, la
  liste vivant derrière le troisième onglet d'une cellule. Le clic
  demande maintenant à la grille où le panneau a été rangé (`reveal`,
  §3.6) et quitte au passage un plein écran qui le masquerait.
- **La fenêtre des liquidations survit au redémarrage** (§2.5) — elle ne
  vivait qu'en mémoire, et relancer le service laissait un panneau vide
  juste après une cascade, ce qui se lit comme une panne du flux.
  `_warm_liquidations` relit la dernière heure du journal avant
  d'ouvrir les connexions. Le premier essai a révélé au passage que les
  fixtures de `test_push.py` s'écrivaient dans le journal de
  l'utilisateur depuis le 20 août — son hub d'essai était construit
  avec le journal par défaut ; il n'en tient plus.

Comme à chaque clôture, plus rien n'est *en cours* : les prochains
chantiers s'ouvriront à l'usage.

Ils se sont ouverts : une série de manques tirés de l'usage a été comblée
depuis — historique des bougies sur disque, résolution DNS qui ne dépend plus
du FAI, bascule € étendue à tout le marché du moment, panneaux ANALYSE et
POSITION, lecture et annotation du graphique, murs du carnet, perpétuel
comparé — et fusionnée dans `main` le 5 novembre 2026 (pull request n° 1,
douze commits). Chacun est barré dans « Ce qui manque encore » ci-dessous.
Le numéro de version, lui, est resté à 1.1.0.

### Étape 1 — Extraire le socle commun ✅ *faite*

Les trois modules décrits en [§2](#2-le-socle-btcterm) sont en place et les huit
scripts y sont ramenés, sans changement de comportement (§2.16) :

- **`indicators.py`** — les deux variantes silencieuses (SMA/EMA, Connors RSI)
  sont désormais explicites, et celle de `btc-dash.py` qui ne suivait pas la
  définition de Connors a été corrigée.
- **`exchanges.py`** — une seule implémentation des connecteurs, réunissant la
  classe de base à backoff exponentiel d'`arbitrage/main.py` et les garde-fous
  `max_levels` / `MAX_WS_SIZE` de `btc_orderbook_live.py`. Un bug de gestion
  des deltas Bybit a été corrigé au passage.
- **`sources.py`** — les collecteurs non temps réel : klines REST, flux ETF
  farside, RSS + CryptoPanic, Fear & Greed.

`etf.py` (v1) n'a jamais été migrée, puisqu'elle devait disparaître à l'étape 4 —
ce qui est fait.

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

**Plus rien d'ouvert.** Le push WebSocket serveur→navigateur, longtemps
différé parce qu'un tour d'horloge rapide coûte 8 à 33 ms pour 6 à 25 Ko — ce
qui passe sans peine en interrogation à 250 ms sur une boucle locale —, est
désormais en place (§3.10) : cadence de 100 ms, trames différentielles, et
l'interrogation en repli dès que le canal manque.

La décision d'étape a depuis été affinée sans être défaite : Dash reste la
charpente — grille, panneaux, callbacks — mais le panneau prix, celui des
séances d'analyse, dessine désormais en Lightweight Charts sur canvas
(§3.2, la « voie A »). Le rendu serveur convenait aux panneaux qu'on
regarde ; il plafonnait pour le graphique qu'on manipule.

### Étape 3 — Mutualiser la couche de données ✅ *faite*

`btcterm/hub.py` — `MarketHub` ouvre **une** connexion par plateforme dans un
thread démon, entretient les cinq carnets, expose le moteur d'arbitrage et met
en cache les appels REST avec une durée de vie propre à chaque nature de donnée
(chandeliers 5 s, taux de change 1 h, flux ETF 30 min, masse monétaire 6 h).

Le cache conserve la dernière valeur connue quand un rafraîchissement échoue :
un panneau qui affiche une donnée un peu datée vaut mieux qu'un panneau vide.

Le hub a gagné depuis un rôle de producteur : le collecteur de news (§2.4)
tourne dans son thread et remplit la base que les panneaux lisent.

### Étape 4 — Fusionner les doublons ✅ *faite*

Cinq scripts ont disparu, après récupération de ce qu'ils avaient de propre :

| Supprimé | Repris par | Ce qu'il a fallu récupérer d'abord |
|---|---|---|
| `btc-dash.py` | panneau prix | `build_chart` → `terminal/charts.py` |
| `btc_dashboard2.py` | panneau prix | palette `15m` → `1M`, échelle log, repli hors ligne |
| `btc-liquidity.py` | panneaux carnet et profondeur | — |
| `btc_orderbook_live.py` | panneaux carnet et profondeur | `max_levels` / `MAX_WS_SIZE` (étape 1) |
| `etf.py` | `etf_bitcoin_flows.py` (§5.1) | — |

Le moteur d'arbitrage avait quitté `arbitrage/main.py` pour `btcterm/arbitrage.py`
à l'étape 3 ; la TUI, autre façade du même moteur, était alors restée — avant
d'être supprimée à son tour, l'arbitrage rendu à la clôture (§5).

Ce qui n'a **pas** été repris, volontairement : les bascules d'affichage de
`btc_dashboard2.py` (signaux, Bollinger, MA 200 activables un à un) — le
panneau prix les affiche toujours —, son bouton « Save PNG », que la barre
d'outils Plotly offre déjà, et ses alias de timeframes `1y` et `All`, simples
raccourcis vers `1d` et `1w`. Les copies conformes des indicateurs d'origine
restent dans `tests/test_indicators_parity.py` : c'est ce qui permet de
supprimer les fichiers sans perdre la garantie de non-régression.

### Étape 5 — Compléter la couverture ✅ *faite*

Fait :

- **Contexte macro** — `m2supply.html`, qui n'était qu'un fragment de page, est
  supprimé au profit d'un septième panneau : cours contre masse monétaire M2
  des États-Unis, décalage réglable de zéro à trois mois, et deux corrélations
  dont seule celle des variations informe. La série vient de la Fed par
  DBnomics, sans clé d'API.
- **Marché à terme** — un panneau perpétuel en onglet des flux ETF : taux de
  financement en barres, open interest en ligne, et dans la barre de titre le
  financement courant, son équivalent annualisé et la part des comptes longs.
  Binance ne conserve que trente jours d'open interest — une borne depuis
  levée par le journal, dont les instantanés prolongent la série (§2.7).
- **Dominance et capitalisation** — en onglet de la cellule macro : parts de
  capitalisation en barres, Bitcoin et stablecoins distingués par la couleur,
  capitalisation totale et volume dans la barre de titre. CoinGecko réserve
  l'historique de ces agrégats à son offre payante : l'API ne donne qu'un
  instantané, mais le journal les accumule désormais (§2.7) et le panneau
  trace la tendance ainsi construite, séance après séance — à côté des
  barres dans la grille, où le panneau est large et bas, dessous en plein
  écran ; la dominance et la part des stablecoins y ont chacune leur
  échelle, faute de quoi un point de dominance valait un pixel.
- **On-chain** — hashrate et difficulté sur un an, rythme des blocs et taille du
  mempool. La source prévue, mempool.space, a dû être abandonnée : elle publie
  souvent des adresses IPv6 seules et cette machine n'a pas de route IPv6, d'où
  des « Network is unreachable » intermittents. blockchain.info répond en IPv4
  et donne les mêmes séries.
- **Liquidations** — en onglet de l'arbitrage, alimenté par le fil du socle
  (§2.5) : les dernières positions fermées de force, toutes paires, et les
  totaux de l'heure par côté. Le flux est épisodique ; le panneau distingue un
  flux coupé d'un marché calme plutôt que de rester muet.
- **Collecte des news** — le terminal remplit désormais la base qu'il lisait,
  toutes les quinze minutes, avec les règles du tracker devenues communes
  (§2.4). Le timer systemd n'est plus un prérequis, seulement une façon de
  garder la base à jour quand le terminal ne tourne pas. `--no-news` rend la
  base au seul tracker.
- **Calendrier macro** — en onglet de la cellule news, sur une liste de dates
  tenue à la main dans le socle (§2.6) : les calendriers économiques ouverts
  étant payants ou sans licence claire, ce sont les calendriers officiels des
  émetteurs qui ont été transcrits — FOMC 2026–2027 chez la Fed, CPI, NFP et
  PCE 2026 au calendrier OMB des statistiques fédérales. Le panneau affiche
  compte à rebours et heure locale, et dit jusqu'où court la liste — une liste
  épuisée doit se voir, pas se taire.
- ~~**La grille est pleine**~~ — réglé : une cellule peut héberger plusieurs
  panneaux, choisis par onglets (§3.5). Ajouter un panneau consiste désormais à
  écrire son module et à l'inscrire dans la cellule qui l'accueille ; la place
  n'est plus le facteur limitant.

Reste, hors étape :

- ~~**Push WebSocket serveur → navigateur**~~ (reporté de l'étape 2) — fait
  (§3.10) : le serveur pousse les panneaux rapides à 100 ms sur `/push`,
  l'horloge à 250 ms restant le repli quand le canal manque. La condition qui
  différait le chantier — descendre sous 100 ms, ou un tunnel SSH lointain —
  n'a plus à être guettée.

### Chantiers d'hygiène (indépendants)

- ~~**Déclarer les dépendances**~~ — fait en phase 1 (§6), et le venv racine
  est à jour : tout ce que déclare `requirements.txt` y est installé.
- ~~**Ports**~~ — réglé : le terminal accepte `--port` et `--host` et reste sur
  `127.0.0.1` par défaut ; les deux dashboards hérités, qui codaient `8050` en
  dur et dont l'un écoutait sur `0.0.0.0`, ont été supprimés à l'étape 4.
- ~~**Versionner le dépôt**~~ — fait : dépôt git local sur `main`.
- ~~**Empaqueter**~~ — fait : `pyproject.toml` et commande `btcterm`, une fois
  remplie la condition que posait §6 — un point d'entrée unique.

### Ce qui manque encore

La feuille de route est soldée et les chantiers d'hygiène aussi : plus rien
n'est *en cours*. Ce qui suit n'est donc pas un chemin ordonné mais l'état
des manques constatés à la clôture et depuis — chacun à ouvrir quand l'usage le
fera sentir, aucun ne conditionnant les autres. Celui qui reste ouvert est
en fin de section, et ne presse pas.

**Point de reprise** (11 novembre 2026, au soir) : `main` porte tout le
travail de la journée, poussé lot par lot, le service tourne sur ce code, et
les contrôles passent — les vingt-neuf fichiers de tests et `ui_smoke.py`,
sur une machine chargée. La journée n'a rien eu d'un plan : l'existant relu à
l'écran, sur le service en marche, puis ce que cette relecture rendait
possible. D'abord les défauts — un titre qui débordait, une profondeur
comparée qui ne comparait plus, un calendrier à cheval sur deux fuseaux, une
dominance qui sautait avec l'univers de CoinGecko, des lignes vides comptées
pour des séances d'ETF, des actions dans le fil des liquidations. Ensuite les
carnets, portés de quelques niveaux à des centaines sur Binance, OKX, Kraken
et Bybit ; et sur eux, la liquidité journalisée, son graphique, sa ligne à la
synthèse et son alerte. Enfin ce que le journal gardait sans le montrer — le
ruban dans le temps, la base et le put/call —, les gaps du CME parmi les
niveaux clés, les frais on-chain, et pour le serveur à venir l'alerte sur une
source en défaut et `/api/health`. Le détail est dans les listes ci-dessous,
entrée par entrée.

Ce qui reste à revoir tient à des seuils posés sans recul : celui des carnets
creux (quarante pour cent sous la moyenne) le jour même où l'historique
commence, celui du skew (deux points en 24 h) — quelques semaines de journal
les étalonneront. Et à ce qui n'a pas pu se vérifier d'ici : le fil des
liquidations de Binance, muet depuis le Cambodge, porte peut-être lui aussi
des contrats qui ne sont pas des cryptomonnaies ; l'alerte sur une source en
défaut et celle des carnets creux n'ont sonné que dans leurs tests ; la
fermeture du CME un jour férié donnera un niveau pris à la mauvaise heure.
L'actif intermittent de la capitalisation est écarté par son nom
(`sources.INTERMITTENT`) : un autre qui se mettrait à clignoter ne le serait
qu'à la lecture des anciennes lignes, pas à la collecte.

**Pour l'analyse :**

- ~~**Un crosshair qui ne dit rien**~~ — fait : la bougie sous la souris se lit
  en clair (§3.13).
- ~~**Le volume agressif jeté**~~ — fait : le CVD, en sous-graphique optionnel,
  exploite ce que `klines.db` conservait déjà (§3.13).
- ~~**Une alerte à trois gestes**~~ — poser un seuil demandait de changer
  d'onglet et de recopier un prix. Fait : Alt + clic sur le graphique (§3.13).
- ~~**Un terminal sans clavier**~~ — fait : intervalles, devise, échelle, plein
  écran et aide au clavier (§3.13).
- ~~**La pression du carnet**~~ — fait : la part des acheteurs dans le volume
  des niveaux affichés, sur la ligne du spread.
- ~~**Des signaux jamais mis à l'épreuve**~~ — fait : le panneau ANALYSE mesure
  les flèches du graphique sur l'historique, contre une référence (§3.14), et
  résume chaque intervalle d'un coup d'œil.
- ~~**Sortir ses données**~~ — fait : export CSV de l'historique sauvegardé.
- ~~**Ce qui marche, alors**~~ — le bilan disant que les flèches n'apportent
  rien, le panneau compare quelques règles simples à la détention (§3.14), et
  les flèches se masquent d'une touche.
- ~~**Un seul perpétuel**~~ — fait : financement, prime et open interest de
  Binance, Bybit et OKX côte à côte ; une plateforme muette n'efface pas les
  autres et se nomme au bandeau.
- ~~**Une ligne de commande**~~ — fait : `/`, un mot, Entrée (§3.13).
- ~~**Mesurer un mouvement**~~ — fait : Ctrl + glisser, qui reprend le cadre du
  zoom et reste à l'écran jusqu'au clic suivant, à Échap ou à un pan (§3.13).
- ~~**La saisonnalité et la volatilité**~~ — fait : carte des rendements mensuels
  et ATR par intervalle au panneau ANALYSE (§3.14). Neuf années font neuf points
  par mois : c'est une description, pas une régularité établie.
- ~~**Les murs du carnet**~~ — fait : la tranche la plus chargée de chaque côté,
  plateformes réunies, par écart à leur prix médian respectif. Le premier essai
  additionnait les prix absolus et posait un mur d'achat au-dessus du mur de
  vente : les plateformes cotent à plusieurs dizaines de dollars d'écart, la
  raison même qui recentre la profondeur comparée. Un carnet coupé ne compte
  plus, ses niveaux étant périmés.
- ~~**Une image du graphique**~~ — fait : touche P et commande `capture`.
- ~~**Tracer une tendance**~~ — fait : `T`, puis glisser (§3.13).
- ~~**Relier le terminal à une position**~~ — fait : le panneau POSITION suit une
  position saisie à la main et dimensionne d'après le risque et l'ATR journalier.
  Il ne parle à aucun compte, et ses champs restent dans le navigateur.
- ~~**Prévenir avant une échéance**~~ — fait : une sonnerie trente minutes avant
  chaque FOMC, CPI, NFP et PCE du calendrier, datée pour ne pas être rejouée.
- ~~**La sortie du plein écran**~~ — jugée lente à tort sur un contrôle qui
  chronométrait mal. Mesurée au millième, la mise en page revient en 170 ms
  (médiane, 372 au pire) et le carnet retrouve ses 17 lignes en 750 ms, le temps
  d'un aller-retour du Store `expanded` : il n'y a rien à corriger.
- ~~**Sonner sur ce qui a tenu**~~ — fait : alerte quand la clôture journalière
  franchit sa MA 200, seule lecture que le bilan n'ait pas démentie. Datée dans
  son message, elle n'est pas rejouée au redémarrage. L'alerte sur les signaux
  ±2 reste active par défaut, malgré ce que le bilan en dit : c'est un réglage
  de l'utilisateur, pas un défaut du terminal.
- ~~**Un terminal aveugle aux options**~~ — tout ce que le terminal lisait
  disait ce que le marché fait — cours, carnet, financement, liquidations —,
  rien de ce qu'il craint : ni volatilité implicite, ni skew, ni échéances, ni
  positionnement par strike, alors que c'est la moitié de ce qu'un poste de
  marché montre d'un actif. Fait : le panneau OPTIONS (§3.16) sur Deribit,
  sans clé — DVOL contre réalisée, courbe des échéances et ailes 25 delta,
  base des futures à échéance, open interest par strike et max pain —, avec
  DVOL au bandeau.
- ~~**Un ruban sans mémoire**~~ — un redémarrage effaçait l'heure de gros
  ordres. Fait : ils vont au journal, reviennent au démarrage, et le panneau
  JOURNAL en fait le bilan du jour (§2.13).
- ~~**La demande américaine**~~ — fait : la prime Coinbase au titre du RUBAN,
  notée toutes les cinq minutes dans les instantanés (§2.13).
- ~~**Un seul fil de liquidations qui livre**~~ — constaté au panneau SOURCES
  le 7 novembre au matin : Binance muet depuis le Cambodge, Bybit sur dix
  paires sans rien depuis une heure. Fait : OKX en troisième source, tous ses
  perpétuels (§2.5), et la part du bitcoin reconnue exactement.
- ~~**Le fil de news en retard au réveil**~~ — constaté au matin du
  7 novembre 2026 : « collecte il y a 345 min », six heures de sommeil et
  dix minutes de plus à attendre l'intervalle. Fait : la collecte repart au
  réveil (§2.4).
- ~~**« 5/5 flux » pour les seuls carnets**~~ — le bandeau ne comptait pas les
  fils de liquidations ni le ruban. Fait : il compte les dix liens temps réel,
  nomme les absents au survol et ouvre le panneau SOURCES au clic.
- ~~**Des titres qui débordent**~~ — le contrôle d'interface ne mesurait que
  la hauteur des barres de titre, sur les onglets visibles au chargement. Il
  ouvre désormais chaque onglet de chaque cellule et en mesure la largeur ; sa
  première passe a trouvé le journal (433 px de trop) et les liquidations
  (17 px), dans la cellule de l'arbitrage passée à quatre onglets avec SOURCES.
  Leurs titres de grille sont compacts, le détail au survol et en plein écran.
- ~~**Des alertes qui ne sonnent que devant l'écran**~~ — sur le serveur
  allumé en permanence qu'on vise, personne ne regarde l'onglet. Fait :
  chaque sonnerie part aussi vers ntfy, jusqu'au téléphone (§2.8).
- ~~**Ce qui s'exécute, et qui l'a voulu**~~ — le carnet ne montrait que des
  intentions. Fait : le panneau RUBAN (§3.19), sur trois places, le sens du
  preneur mesuré plutôt que supposé (§2.13).
- ~~**L'état des sources au seul journal système**~~ — chaque incident de la
  séance s'est lu dans `journalctl`, le bandeau ne disant qu'un nombre.
  Fait : le panneau SOURCES (§3.18), ouvert d'un clic sur l'avertissement
  du bandeau.
- ~~**Le portage en deux unités**~~ — le financement des perpétuels se lisait
  par huit heures dans un panneau, la base des futures par an dans un autre.
  Fait : une ligne « portage /an » au panneau perpétuel, plateformes et
  future à trois mois dans la même unité.
- ~~**Où le levier a sauté**~~ — fait : la carte des liquidations (§3.13).
- ~~**Le skew sans mémoire**~~ — le panneau OPTIONS disait le skew du
  moment sans pouvoir dire s'il était extrême : Deribit ne garde pas son
  historique gratuitement. Fait : l'instantané de marché le note toutes les
  cinq minutes avec la volatilité à un mois, le put/call et la base (§2.7),
  et la vue HISTO les relit. L'historique commence le 6 novembre 2026.
- ~~**Un calendrier qui s'épuise en silence**~~ — le CPI, le NFP et le PCE
  s'arrêtaient en décembre 2026, et le panneau annonçait une liste valable
  jusqu'en décembre 2027 : il lisait le dernier FOMC. Fait : 2027 transcrite
  depuis le BLS et le BEA, et l'horizon est celui de la famille qui s'épuise
  la première (§2.6).
- ~~**Des niveaux à tracer à la main**~~ — les plus hauts et plus bas de
  la veille, de la semaine et du mois, que tout trader repère avant d'ouvrir
  une position, demandaient une ligne posée à la main pour chacun. Fait :
  case `NIV` et touche `N` (§3.13).
- ~~**Le bitcoin, seul au monde**~~ — rien ne disait s'il bougeait avec les
  actions, l'or ou le dollar, la première question d'un poste de marché ; le
  panneau macro le confrontait à la seule masse monétaire, mensuelle. Fait :
  le panneau MARCHÉS (§3.17), performance comparée et corrélations glissantes
  au S&P 500, au Nasdaq, à l'or, au dollar et au taux à dix ans.
- ~~**Ce que la nuit a changé**~~ — au retour devant l'écran, il fallait
  ouvrir six onglets pour savoir ce qui avait bougé. Fait : le panneau
  SYNTHÈSE (§3.20) dit la journée en une page, sa couverture comprise, et
  la même synthèse part chaque matin vers ntfy (§2.14).

**Pour la surveillance :**

- ~~**Alertes**~~ — fait : `btcterm/alerts.py` (§2.8) évalue cinq règles
  dans la boucle d'observation — seuils de cours posés par l'utilisateur,
  rafale de liquidations, financement extrême, news à fort score, écart
  d'arbitrage — et le panneau ALERTES les règle et les affiche ; cloche au
  bandeau, bip et notification navigateur, sonneries journalisées.
- ~~**Historique des données éphémères**~~ — fait : `btcterm/journal.py`
  (§2.7) écrit liquidations et épisodes d'arbitrage rentables dans
  `~/.btcterm/journal.db`, et `python -m btcterm.journal` relit la séance.
  Les alertes y trouveront leur base de comparaison.
- ~~**Liquidations sans Binance Futures**~~ — fait :
  `BybitLiquidationConnector` (§2.5) verse le canal `allLiquidation` de
  Bybit dans le même fil, dix grandes paires ; le panneau étiquette la
  plateforme, le journal la conserve (colonne `exchange`, ajoutée par
  migration), et le badge nomme le lien qui manque.
- ~~**Une source en panne, en silence**~~ — constaté dans le journal de
  séance : six heures et demie d'instantanés sans financement ni open
  interest, Binance Futures muet après un réveil pendant que CoinGecko
  répondait, sans une ligne dans le journal du service ; et le
  collecteur du perpétuel rendait un vide que le cache stockait comme
  une lecture réussie. Fait : le cache journalise la première panne
  d'une source et son rétablissement, tient par clé si la dernière
  lecture était fraîche ou de secours, l'instantané n'écrit que le
  frais, et un collecteur sans rien lève (§4.3).
- ~~**Une panne qui dure tant qu'on ne regarde pas**~~ — constaté dans le
  journal du service : « klines:4h:350 de nouveau servie après 1915 min »,
  pour un seul échec suivi de trente-deux heures sans lecture, le panneau
  ANALYSE étant replié ; la nuit de sommeil gonflait de même la durée des
  pannes d'avant le coucher. Fait : au-delà de `FAILING_WINDOW` sans
  lecture, le retour dit l'oubli et la panne constatée séparément (§4.3).
- ~~**Le sommeil compté comme panne**~~ — constaté dans le journal du
  service le lendemain du correctif précédent : « après 95 min », dont
  quatre-vingt-quatre de sommeil, une lecture ratée à la troisième seconde
  du réveil ayant confirmé la panne de la veille ; dix pannes sur
  soixante-six dans la minute d'un réveil ; le graphique une minute en
  retard sur le réseau revenu ; et un sommeil de onze secondes que la
  boucle ne voyait pas. Fait : le sommeil se lit sur les horloges du noyau
  (`SleepWatch`, §2.7), un échec dans la minute d'un réveil est servi de
  secours sans compter comme panne, Binance y est retenté toutes les cinq
  secondes, et un échec après un long silence ouvre une panne au lieu
  d'allonger l'ancienne (§4.3).
- ~~**Un accroc de Binance fige le graphique une minute**~~ — constaté au
  journal du service une heure après : un seul délai de connexion, à
  12:30:01, et onze lignes de panne, tous les intervalles lus pendant
  l'attente d'une minute se disant « en panne : Binance en attente de
  nouvel essai » — trente-cinq des quatre-vingt-dix-huit lignes de panne
  en trois jours étaient de ces échos. Le graphique, lui, restait figé
  soixante secondes pour un accroc que le premier nouvel essai effaçait.
  Fait : le délai part de cinq secondes et suit la panne vue, une minute au
  plus, et une lecture refusée pendant l'attente (`SourceBackoff`) est
  servie de secours sans compter comme panne (§4.3).
- ~~**Les alertes après un redémarrage**~~ — constaté au balayage des
  panneaux du service en marche : « aucune alerte — les seuils
  veillent » quand le journal en comptait onze sur la journée. Fait :
  `_warm_alerts` relit la journée au démarrage, comme les liquidations
  (§2.8).
- ~~**La séance interrompue**~~ — constaté dans le journal de séance,
  trente trous de plus de dix minutes en sept jours dans les instantanés
  de marché, dont un de 447 min ; le journal système a dit la cause —
  `systemd-sleep`, la machine dort — et les courbes journalisées tiraient
  un trait par-dessus. Fait : le journal nomme les interruptions
  (`interruptions_between`), `market_snapshots` y pose une ligne de
  rupture, la dominance et les séries prolongées s'y rompent, le panneau
  journal les compte (§2.7) ; et le backoff des connecteurs repart de
  zéro après une connexion qui a tenu, pour que le réveil ne paie pas
  les pannes d'avant (§2.2).
- ~~**Niveaux fantômes dans les carnets**~~ — constaté dans le journal de
  séance, des épisodes d'arbitrage de plusieurs heures à prix d'achat
  figé : un ask que le carnet n'avait jamais supprimé. Fait : le
  snapshot Coinbase Advanced remplace le carnet au lieu de s'y
  superposer, un carnet croisé qui persiste se resynchronise, et le
  moteur l'écarte en attendant (§2.2).
- ~~**Kraken se resynchronise toutes les deux minutes**~~ — constaté
  dans le journal du service dès que le garde-fou précédent a su
  compter : cinquante-quatre resynchronisations en une matinée, toutes
  de Kraken. Le flux décrit une fenêtre de vingt-cinq niveaux et le
  carnet en gardait cent — soixante-quinze fantômes en puissance. Fait :
  le carnet Kraken est borné à sa fenêtre (§2.2).
- ~~**Un lien ouvert mais muet**~~ — fait : le fil date, par lien, le
  dernier événement reçu et l'ouverture du lien, et `silent` nomme ceux
  qui tiennent sans rien livrer depuis plus d'un quart d'heure (§2.5).
  Le badge du panneau l'écrit en jaune — « Binance muet depuis 22 min » —
  à côté du lien qui manque, s'il y en a un. C'était le cas de Binance
  depuis le Cambodge : le flux futures s'ouvre, s'abonne et se tait
  (§2.9), Bybit porte le panneau seul, et l'écran le dit désormais.
- ~~**La bascule € s'arrêtait au graphique**~~ — constaté à l'usage : en
  euros, le carnet restait en dollars. Fait : la règle est posée pour tous
  dans `terminal/fx.py` (§4.3) — ce qui décrit le marché du moment se
  convertit, ce qui est un fait passé non. L'arbitrage y a gagné l'unité que
  ses prix n'avaient pas.
- ~~**Le FAI change de mensonge**~~ — constaté à un redémarrage : quatre
  plateformes injoignables et le panneau prix sur sa démonstration. Les noms
  résolvaient en une adresse publique morte, que le module ne reconnaissait
  pas, et les connexions ouvertes avaient masqué le changement. Fait : les
  noms des plateformes passent toujours par DoH, quoi que dise le système
  (§2.9).
- ~~**Un redémarrage perdait tout l'historique**~~ — le réseau pesait 85 %
  de l'ouverture du panneau prix, pour des bougies qui ne changeront plus,
  et un redémarrage pendant une panne de Binance tombait sur la démonstration.
  Fait : les bougies closes vivent sur disque (§2.10) ; changer d'intervalle
  passe de 0,4 à 0,19 s, reculer dans le graphique de 0,4 à 0,07 s, et une
  panne affiche le vrai historique, bandeau à l'appui.
- ~~**Le stress des options, sans alerte**~~ — fait avec le panneau
  OPTIONS : une douzième règle sonne quand DVOL monte de plus de six points
  (réglable) au-dessus de sa clôture de la veille, ce qui n'est arrivé que dix
  jours sur l'année écoulée (§2.8).
- ~~**Une source en panne, au journal seulement**~~ — la journée a montré des
  sources muettes que seul `journalctl` nommait, le bandeau affichant 5/5 flux.
  Fait : `failing_sources` regroupe les pannes par famille, le bandeau en dit le
  nombre et l'infobulle les nomme ; une panne qu'aucun nouvel échec ne confirme
  s'efface après `FAILING_WINDOW`.

**Hygiène technique :**

- ~~**Tests hors ligne du pousseur**~~ — fait : `test_push.py` couvre
  `_merge` et `_frame` avec un hub jamais démarré, carnets remplis à la main
  et liquidations injectées, et `test_terminal_wiring` vérifie la route
  `/push` et ses relais d'état (§3.10). Plus aucune pièce du terminal n'est
  sans test exécutable hors ligne.
- ~~**Serveur de développement**~~ — fait, avec le service utilisateur
  ci-dessous qui constituait justement l'usage long attendu (§3.11) :
  `terminal/wsgi.py` donne sa fabrique à gunicorn — un worker unique,
  threadé plutôt que gevent, dont le monkey-patching se disputerait la
  main avec la boucle asyncio des connecteurs — et l'extra `serve`
  l'installe.
- ~~**Service utilisateur**~~ — fait : `terminal/systemd_service.conf`
  donne l'unité `btcterm.service` sur le modèle du gabarit de la collecte
  de news, configurée par variables d'environnement (§3.11).
- ~~**Arrêt qui expire**~~ — constaté dans le journal du service, deux
  arrêts sur cinq tués par systemd : gunicorn attendait la fin des
  WebSockets `/push`, qui n'en ont pas. Fait : le signal d'arrêt lève
  `hub.stopping`, la boucle du pousseur le lit et prend congé (§3.11).
- ~~**L'onglet d'avant le redémarrage**~~ — constaté dans le journal du
  service : cinq tracebacks Dash en cinq secondes au réveil de la
  machine, un onglet chargé avant le redémarrage de la nuit appelant le
  nouveau serveur avec l'ancien graphe de callbacks. Fait : la page
  emporte le jeton de son serveur, le compare à `/api/boot` avant chaque
  connexion et se recharge s'il a changé (§3.10).
- ~~**Cinq pannes au boot**~~ — constaté dans le journal du service :
  l'unité utilisateur démarre avant le réseau, et le premier tour de la
  boucle d'observation déclarait cinq sources en panne deux secondes
  après le boot, rétablies une minute plus tard. Fait : la boucle
  attend le réseau avant son premier tour, une minute au plus (§2.7).
- ~~**Cinq pannes au réveil**~~ — constaté dans le journal du service
  le jour même du correctif précédent : la même salve, sept fois dans
  la journée, à la seconde exacte de chaque réveil de la machine. La
  boucle dort en temps monotone, que le sommeil n'avance pas, et son
  premier tour partait avant que la machine ne soit reconnectée. Fait :
  un tour dont l'heure murale a sauté est un réveil, et la boucle
  rattend le réseau comme au démarrage (§2.7).
- ~~**Un bitcoin à `7.909e+04`**~~ — constaté dans la sortie du contrôle
  d'interface, qui recopie la première ligne du tableau des
  liquidations : quatre chiffres significatifs pour tous les prix, ce
  qui écrit un dogecoin `0.2345` et un bitcoin en notation
  scientifique. Fait : le nombre de décimales suit la taille du prix
  (`_prix`), et le contrôle refuse un `e+` dans le tableau.
- ~~**Un onglet ouvert coûte un sixième de cœur**~~ — constaté dans le
  journal du service, une instance à 22 % de processeur là où les
  autres tenaient en 2 % ; mesuré ensuite sur le worker, 2 % sans
  client, 16 % avec un onglet. Le profil (py-spy sur une seconde
  instance) a mis la moitié des échantillons dans `build_depth_chart` :
  le pousseur rebâtissait et sérialisait la figure de profondeur dix
  fois par seconde, et des carnets jamais immobiles la faisaient
  repartir à chaque trame. Fait, en deux coupes : la profondeur a sa
  cadence propre, une seconde, comme le prix avait la sienne (§3.10) ;
  et la figure est bâtie en dictionnaire brut plutôt qu'en `go.Figure`,
  dont la validation valait vingt des vingt-trois millisecondes du
  rendu — un banc en processus, cible par cible, l'a montré là où
  py-spy, à deux cents secondes de retard sur un processus à vingt
  threads, ne montrait plus rien. Le contrôle Firefox de cette séance a
  échoué trois fois au même endroit — puis, rejoué sur le code d'avant,
  de la même façon : la machine portait un autre calcul à cinquante-huit
  threads, charge 22 pour 22 cœurs. `ui_smoke.py` le dit désormais en
  tête dès que la charge dépasse la moitié des cœurs.
- ~~**Un onglet ouvert coûte encore un dixième de cœur**~~ — mesuré le
  7 novembre 2026 sur le service, 4,5 % d'un cœur sans onglet et 14,5 avec ;
  sur une seconde instance, le thread du pousseur en prenait 7,2 à lui seul.
  Le banc cible par cible sur un hub réel a mis les liquidations à 2,2 ms
  d'une trame de 4,8 — davantage depuis qu'OKX les nourrit de tous ses
  perpétuels —, rebâties dix fois par seconde pour une table qui changeait
  une fois en dix secondes. Fait : elles suivent leur fil, et la seconde
  (§3.10) ; le thread du pousseur tombe à 4,1 %, et une liquidation reste à
  l'écran à la trame suivante. Un premier échantillonneur en processus
  désignait la compression des trames (permessage-deflate) : rejouée seule,
  elle coûte 1,3 ms par seconde de flux — il comptait en trop les instants
  où un thread rend le verrou de l'interpréteur.
- ~~**Le carnet sature la page**~~ — constaté au contrôle Firefox, deux
  contrôles du plein écran qui échouaient et qu'on mettait sur le compte de
  la charge de la machine, à tort : le rendu Dash ne suivait pas dix carnets
  par seconde. Fait : 250 ms pour le carnet, 500 ms en plein écran (§3.10) —
  un contournement, levé depuis (ci-dessous).
- ~~**Le carnet attend le réseau**~~ — mesuré au même moment : la cible prix,
  lue dans la boucle du pousseur, figeait le carnet 140 à 350 ms toutes les
  cinq secondes, et plus longtemps si Binance se taisait. Fait : elle est lue
  à côté (`_PriceRead`, §3.10). Une socket morte, qui laissait deux traces au
  journal du service, termine désormais la boucle sans bruit.
- ~~**Le coût du carnet à la ligne**~~ — la cadence réduite contournait la
  limite sans la lever. Mesuré le 5 novembre 2026, la page ne tenait que la
  moitié de ses minuteries, et gelait jusqu'à 8,9 s en plein écran ;
  l'arbitrage et les liquidations, eux, n'en coûtaient qu'une vingtaine sur
  400. Fait : le carnet part en données et `assets/book.js` l'écrit en place,
  seul moteur de rendu pour les deux canaux — il remplace `render_book` au
  lieu de le doubler. À 100 ms, plein écran compris, la page tient autant de
  minuteries que sans carnet (§3.10).
- ~~**Un pan manqué ferme le passé**~~ — constaté au journal du service, le
  2 novembre 2026 : une page en quart d'heure demandée pendant la minute
  d'attente de Binance, et que le disque ne tenait pas. Revenue vide, elle
  valait « plus rien avant » pour le graphique, qui ne remontait plus le
  passé jusqu'au changement d'intervalle. Fait : la page servie de secours
  le dit (`stale`), et le client la redemande trente secondes plus tard au
  lieu de clore l'historique — sauf sur la série de démonstration, qui n'a
  pas de passé à attendre.
- ~~**Un recalage fait sauter la vue**~~ — remarqué en relisant le pan,
  mesuré ensuite : retour d'onglet, reconnexion du canal et repli poll
  rechargeaient la dernière page à la place de tout le tampon. Le graphique
  gardant sa plage par rang de bougie et non par date, une vue posée en
  2023 sautait en 2025 ; en repli poll, toutes les deux secondes, et
  remonter le passé y devenait impossible. Fait : le recalage remplace la
  fin du tampon et garde devant elle le passé remonté, s'ils se recouvrent
  — une machine qui a dormi plus longtemps que la page n'en couvre perd ce
  passé plutôt que d'y laisser un trou.
- ~~**Une page égarée au changement d'intervalle**~~ — sortie du contrôle
  écrit pour le point précédent. Un pan pendant le chargement d'un nouvel
  intervalle demandait une page de ce dernier avant la première bougie de
  l'ancienne série, sous le jeton du refetch déjà parti, et la collait
  devant la nouvelle série : rejoué sur le code d'avant, de l'horaire au
  4 h, 1 050 bougies au lieu de 700, et une série qui reculait de 174
  bougies en son milieu. Le recalage complet effaçait le dégât au retour
  d'onglet suivant. Fait : une page ne se colle que si le tampon commence
  toujours là où elle s'arrête. `ui_smoke.py` attendait l'intervalle
  demandé et non celui de la série affichée : il provoquait cette course,
  et passait grâce à elle. La sonde dit désormais les deux (`loaded`), et
  l'écart le plus court et le plus long entre deux bougies (`steps`).
- ~~**Le cache mémoire garde chaque page d'historique**~~ — une page de pan
  est une clé à usage unique (`klines:15m:500:<fin>`) que `TTLCache` ne
  libérait jamais : le journal du service y a montré une « dernière valeur »
  de dix-neuf heures. La durée de vie d'une heure, censée borner la mémoire,
  ne la bornait pas ; une page pèse 33 à 47 Ko, mesurée sur l'historique du
  disque. Fait : toutes les dix minutes, la boucle d'observation oublie ce
  que personne n'a lu depuis une heure et dont la durée de vie est passée
  (`TTLCache.sweep`). Ce qu'on lit encore reste, même périmé — c'est le
  secours d'une source en panne —, et l'état de panne reste aussi, pour
  que le retour de la source se dise au journal. Le disque tient de toute
  façon ces pages.

- ~~**Des connexions gardées rompues sans l'avoir dit**~~ — onze des dix-huit
  pannes du journal du service entre le 7 et le 11 novembre 2026 étaient des
  « Connection reset by peer », toutes sur CoinGecko et blockchain.info :
  lues toutes les cinq ou dix minutes, leurs connexions gardées étaient
  oubliées en route, et urllib3 n'écarte que celles que le serveur a fermées
  proprement. Chaque rupture laissait le panneau sur sa valeur de secours
  jusqu'à la lecture suivante, et un instantané du journal sans agrégats de
  marché (3 % sur cinq jours). Fait : la session rejoue une fois la lecture
  rompue, sur une connexion neuve ; un délai dépassé ou une connexion
  refusée ne sont pas rejoués (§2.10, `tests/test_cache.py`).

- ~~**Ce qui existait, relu à l'écran**~~ — le 11 novembre 2026, sans rien
  ajouter : le contrôle d'interface et les captures, passés sur le service
  en marche, ont montré cinq défauts de l'existant.
  *Le titre des liquidations débordait de 96 px* dès que deux liens se
  taisaient ensemble (« BIN muet · BYB muet ») : le badge de la grille
  tient désormais en trente-deux caractères comptés (`COMPACT_MAX`), un
  seul lien se nomme, plusieurs se comptent, le survol les détaille.
  *La profondeur comparée ne comparait plus rien* : Coinbase porte ses
  niveaux à 3 % du prix, les autres à quelques centièmes de pour cent, et
  l'axe étiré par le premier écrasait les quatre autres autour de zéro. Le
  cadre est l'étendue médiane des carnets, arrondie pour ne pas trembler
  (`depth_window`), le volume s'y ajuste, les courbes restent entières
  pour qui dézoome, et la légende nomme la plateforme plutôt que ses
  « bids ».
  *Le panneau DOMINANCE empilait deux graphiques dans cent pixels* : ils
  sont côte à côte dans la grille, et chaque courbe a son échelle (§7,
  étape 5).
  *Le calendrier mêlait deux fuseaux* (§2.6), et *la dominance sautait
  avec l'univers de CoinGecko* (§2.7) — ce dernier défaut n'étant devenu
  visible qu'une fois la tendance lisible. La commande du journal dit en
  prime sa couverture et ses gros ordres (§2.7).
  Un second tour, le même jour, en a trouvé deux autres.
  *Une ligne sans chiffres comptait pour une séance* : farside pose la
  ligne du jour avant de la publier et garde celle des jours fériés, des
  tirets que la lecture changeait en zéros — « +0 M$ le 10/11 » à la
  synthèse, une barre nulle au graphique, et des « cinq séances » qui
  comptaient ce zéro à la place d'un vrai jour. `clean_etf_table` écarte
  une ligne dont plus de la moitié des fonds n'ont rien publié : dix-sept
  sur 727 ce jour-là, seize jours fériés et le jour en cours ; l'export
  `etf_bitcoin_flows.py` en profite.
  *Les barres de défilement étaient blanches sous Firefox*, qui ignore
  `::-webkit-scrollbar` : `scrollbar-color` les met au thème.
  Un troisième tour a regardé OKX. *Son carnet n'avait que cinq niveaux*
  (`books5`) : cinq lignes là où les autres en montrent dix-sept, cinq
  points dans la profondeur comparée, presque rien dans les murs. Il lit
  désormais `books` — 400 niveaux par côté, un bitcoin de bids visible
  devenu quatre-vingt-dix-neuf —, et vérifie la suite des `seqId` : un
  message perdu vaut resynchronisation (§2.2). Le service au repos coûte
  toujours 4 % d'un cœur. *Et le port 8443 allait fermer* : la plateforme
  l'annonçait à chaque connexion, dans un avis que personne ne lisait ;
  le carnet et le fil des liquidations passent au 443.
  Restait le plus gros marché, devenu le carnet le plus mince : *Binance
  ne montrait que vingt niveaux*, 0,004 % du prix. Le connecteur accorde
  désormais un snapshot REST de mille niveaux au flux des deltas, comme la
  plateforme le prescrit — messages gardés pendant la lecture, redites
  écartées, trou resynchronisé, snapshot en retard relu —, et le relit
  toutes les dix minutes sans fermer le flux, un carnet local borné
  s'amincissant du côté que le cours traverse. Kraken passe de 25 à 100
  niveaux, Bybit de 50 à 200, chacun borné à la fenêtre de son flux. La
  profondeur comparée se cadre à ±0,5 % et montre ce qu'elle devait :
  165 BTC de bids chez Binance, 80 chez OKX, 45 chez Bybit, 20 chez
  Kraken. Sa portée par carnet est celle de neuf niveaux sur dix (un ordre
  isolé à 20 % ne cadre rien), ses courbes sont élaguées à 250 points par
  côté — 30 Ko par seconde au lieu de 140. Coût mesuré, un onglet ouvert :
  11 % d'un cœur contre 9,5 ; au repos, rien de mesurable.
- ~~**Une profondeur sans mémoire**~~ — la profondeur comparée disait le
  carnet de l'instant, pas s'il était plein ou vide pour lui-même ; or un
  carnet qui se vide précède souvent le mouvement qu'il n'amortira pas, et
  aucune API gratuite ne sert cet historique. Fait, une fois les carnets
  assez profonds pour que la mesure ait un sens : la boucle d'observation
  échantillonne toutes les dix secondes le volume offert à moins de 0,1 %
  du prix médian de chaque plateforme (`OrderBook.depth_within`), et en
  écrit la moyenne toutes les cinq minutes dans la table `book_depth` du
  journal, une ligne par plateforme, gardée quatre cents jours comme les
  instantanés. Une moyenne et non une photographie — un carnet respire à
  la seconde —, et rien pour une plateforme coupée ou figée la moitié de
  la période. Le panneau PROFONDEUR la trace sous le carnet de l'instant,
  achats et ventes, sur un jour dans la grille et sept en plein écran ; la
  somme ne porte que sur les plateformes présentes dans neuf relevés sur
  dix (`liquidity_series`) — une plateforme qui se tait cinq minutes ne
  creuse pas la courbe, elle en sort, et le titre le dit — et le trait se
  rompt sur les interruptions de séance. Le titre donne le dernier relevé
  et son écart à la moyenne de la fenêtre. L'historique commence le
  11 novembre 2026.
- ~~**Une variation sur 24 h fausse de deux points**~~ — le titre du
  panneau DOMINANCE recopiait celle de CoinGecko, qui ne dit pas sur quel
  univers elle est calculée : −4,07 % le 11 novembre 2026, quand le journal
  mesurait −1,69 et la capitalisation du bitcoin −1,45. Fait :
  `hub.market_cap_change` la mesure au journal, même univers aux deux
  bouts (§2.7) ; l'infobulle dit les heures réellement couvertes quand la
  machine dormait vingt-quatre heures plus tôt, et celle de la source ne
  s'affiche plus qu'en gris, faute de mieux, tant que le journal n'a pas
  une demi-journée de recul.
- ~~**Des frais de transaction absents**~~ — le panneau ON-CHAIN disait la
  taille du mempool, pas ce que coûte d'y passer : la demande d'espace
  dans les blocs, nulle quand la chaîne est vide et qui s'envole quand
  tout le monde bouge ses bitcoins à la fois. Fait : `fetch_fee_estimates`
  lit les estimations de blockstream.info — au centième de satoshi, là où
  d'autres arrondissent à l'unité un marché qui vit sous le satoshi —, et
  le titre du panneau donne le prochain bloc, l'heure et la journée au
  survol ; orange au-delà de 20 sat/vB, rouge au-delà de 80. La source
  figure au panneau SOURCES comme les autres.
- ~~**Un contrôle qui s'arrêtait net**~~ — `ui_smoke.py` levait une
  exception, et perdait tout le passage, quand le tracé d'une tendance
  manquait son glisser sur une machine chargée : c'est désormais un
  contrôle en échec comme un autre.
- ~~**Un spread à « 0.0000 % »**~~ — à 73 000 $, un centime d'écart vaut
  0,000014 % : le bandeau et le carnet écrivaient quatre zéros à longueur
  de journée. Sous le millième de pour cent, le spread garde deux
  décimales de plus (`fx.spread_text`).
- ~~**La liquidité absente de la synthèse**~~ — fait : une ligne
  « Liquidité » dit le dernier relevé contre la moyenne de la journée et
  la part des achats, en avertissement quand le carnet est à trente pour
  cent sous sa moyenne ; elle part aussi au réveil sur le téléphone. Elle
  se tait sans une heure de relevés. La règle de somme est désormais
  unique (`journal.liquidity_totals`), pour le graphique comme pour elle.
- ~~**Un carnet qui se vide, sans alerte**~~ — fait : une règle sonne
  (`depth`) quand la liquidité tombe à quarante pour cent sous sa moyenne
  de 24 h, réglable au panneau ALERTES. Trois gardes : six heures de
  relevés pour que la moyenne décrive une journée, la médiane de trois
  relevés pris dans la demi-heure pour qu'une plateforme en
  resynchronisation ne vide pas le marché, et la somme sur les seules
  plateformes présentes toute la journée. Le seuil est provisoire : posé
  le jour où l'historique commence, il est à revoir quand le journal aura
  quelques semaines, comme celui du skew.
- ~~**Les murs cherchés dix fois par seconde**~~ — mesuré sur des carnets
  de taille réelle : chaque envoi du carnet prenait 2,6 ms, dont 1,9 à
  chercher les murs dans cinq carnets entiers, dix fois par seconde. Fait :
  ils sont recherchés au plus une fois par seconde (`_wall_levels`). Au
  niveau du service, le gain ne se distingue pas du bruit d'une machine
  chargée — 12 % d'un cœur avec un onglet, avant comme après.
- ~~**Le contrôle ignorait ce qui venait d'être fait**~~ — `ui_smoke.py`
  vérifie désormais le cadre de la profondeur (symétrique, rempli par la
  moitié des courbes), sa légende, l'historique de liquidité et son
  titre, et l'absence d'actif intermittent dans les barres de dominance.
- ~~**Des actions dans le fil des liquidations**~~ — constaté sur les
  captures du 11 novembre 2026 : SNDK, MU, SOXL, ZHONGJI remplissaient le
  tableau. OKX cote désormais des perpétuels sur actions et matières
  premières — 239 de ses 543 contrats ce jour-là —, et un tiers de ses
  liquidations journalisées en quatre jours venaient d'eux (10 M$ sur
  32) : ils gonflaient les totaux de l'heure, pouvaient faire sonner
  l'alerte de rafale, et la « plus grosse liquidation » relue au journal
  était une position sur SanDisk. Fait : la table des contrats retient la
  catégorie que la plateforme leur donne (`instCategory`), et ceux qui ne
  sont pas des cryptomonnaies restent connus mais hors du fil (§2.5). Les
  2 928 lignes déjà journalisées (10,3 M$) ont été retirées de
  `journal.db` le même jour, à la demande de l'utilisateur, la base
  d'avant gardée dans `journal-avant-purge-20261111.db`.
- ~~**Les gaps du CME, à repérer à la main**~~ — les futures du CME
  ferment du vendredi soir au dimanche soir pendant que le bitcoin cote,
  et laissent un trou que le cours revient souvent combler : un niveau
  que beaucoup surveillent, et qu'il fallait aller lire sur un autre
  graphique. Fait : `analysis.cme_gaps` le lit **sur le comptant**, aux
  heures de Chicago — du cours à la fermeture du vendredi au cours à la
  réouverture du dimanche —, sur les bougies d'une heure que le disque
  tient déjà. C'est le gap des futures à la base près, dans les prix du
  graphique et sans source de plus ; les bougies quotidiennes de `BTC=F`
  chez Yahoo, essayées d'abord, sont incomplètes sur les derniers jours
  (ouverture, plus haut et plus bas confondus), ceux qui comptent ici.
  Un gap est comblé dès que le cours est revenu toucher la fermeture du
  vendredi ; les trois plus récents encore ouverts rejoignent les niveaux
  clés (case `NIV`, touche `N`), en bleu, et pendant le week-end la
  fermeture du vendredi s'affiche, le gap se formant. Au 11 novembre
  2026, un seul : « CME 06/11 » à 75 928 $, ouvert de 1,9 %.
- ~~**Une démo statique de six semaines**~~ — `docs/demo` datait du
  30 septembre : son `lwc-price.js` comptait mille lignes quand celui du
  terminal en compte près de deux mille, sans la minute ni les cinq
  minutes, et sa page annonçait « quatorze autres panneaux » pour vingt
  et un. Régénérée (`python -m terminal.demo docs`), onze intervalles
  figés le 11 novembre 2026, vérifiée dans Firefox sur un serveur de
  fichiers ; la liste des panneaux vient désormais de la grille
  (`demo.other_panels`), une liste écrite à la main se périmant. La
  commande du journal, elle, dit la liquidité de la fenêtre.
- ~~**Une source en panne, sans personne pour le voir**~~ — sur le serveur
  allumé en permanence qu'on vise, le panneau SOURCES n'a pas de lecteur :
  un carnet coupé une heure ou Deribit muet une nuit ne se voyaient qu'au
  retour devant l'écran. Fait : une règle `source` sonne — et part vers
  le téléphone — quand un lien temps réel reste coupé ou une source REST
  en panne plus d'un quart d'heure, une fois par épisode, puis au
  rétablissement de ce qui avait sonné (`_check_sources`). Un lien muet
  ne sonne pas : c'est l'ordinaire d'un fil de liquidations calme, et
  l'état permanent de Binance depuis certains pays. Après un réveil de
  la machine, les compteurs repartent de zéro. La règle est débrayée par
  défaut — sur un portable, un réseau qui filtre une plateforme sonnerait
  pour rien — et se coche au panneau ALERTES (case « sources ») une fois
  le terminal sur son serveur. Pour qui surveille de dehors,
  `/api/health` dit le même état en une requête : `ok`, `failing`, et le
  détail du panneau SOURCES.
- ~~**Un ruban sans passé à l'écran**~~ — le journal gardait trente jours
  de gros ordres et la prime Coinbase toutes les cinq minutes, et le
  panneau RUBAN, aux trois quarts vide, n'en montrait que l'heure et
  l'instant. Fait : sous la liste, le solde horaire des gros ordres en
  barres (achats moins ventes, `hub.tape_flow_history`) et la prime
  Coinbase en ligne, sur un jour dans la grille et sept en plein écran,
  à l'heure de la machine ; la ligne se rompt sur les interruptions de
  séance. Une heure sans barre est une heure sans gros ordre — ou sans
  terminal : le graphique ne distingue pas les deux, le panneau JOURNAL
  dit la couverture.
- ~~**La base et le put/call journalisés pour rien**~~ — l'instantané les
  notait depuis le 6 novembre 2026 avec la volatilité et le skew, et la
  vue HISTO du panneau OPTIONS ne traçait que ces deux-là. Fait : en plein
  écran, un troisième étage porte la base annualisée du future à trois
  mois et le put/call en open interest, chacun sur son axe ; la grille
  garde ses deux étages. L'étage se cadre sur l'essentiel de la série —
  une base lue à zéro quand Deribit rend une chaîne incomplète l'écrasait.
- ~~**Une lecture figée de Deribit écrite au journal**~~ — le relevé
  aberrant que l'étage précédent a montré : le 10 novembre 2026, à la
  première lecture après une panne de la source (503), une base de zéro
  et un skew de −6 entre deux relevés à 5,7 %/an et −3,5. Deribit marquait
  alors tous ses futures au prix de l'indice, au centime. Fait :
  `futures_curve` écarte un future coté au prix de l'indice — ce n'est pas
  une base nulle, c'est une absence de cotation — et les compte
  (`attrs["pinned"]`) ; quand ils le sont tous, l'instantané n'écrit rien
  du marché des options pour ce relevé. La ligne du 10 novembre reste au
  journal : l'alerte sur le skew lit déjà une médiane de trois relevés, et
  le graphique la laisse hors cadre.

- ~~**Rien ne situait le cours dans son cycle**~~ — le terminal disait la
  journée, la semaine et les marchés voisins, pas la distance au sommet ni au
  halving, que tout suivi du bitcoin regarde. Fait : le panneau CYCLE (§2.15),
  vingt et unième, sans source de plus. La barre de titre de la rangée basse,
  qui porte désormais sept onglets, débordait de 30 px sur l'onglet MACRO :
  ses badges passent à 9 px, comme ceux des autres panneaux. La synthèse
  (§2.14) en tire une ligne, qui part aussi au réveil sur le téléphone.
- ~~**Une alerte sur le skew**~~ — fait, sur son déplacement en 24 h (§2.8) ;
  seuil provisoire, à revoir avec l'historique.

**Constaté, pas encore ouvert :**

- **Le quart d'heure et la demi-heure se remplissent à l'usage** — le reste est
  complété en tâche de fond (§3.14) ; ces deux-là coûteraient quatre cent
  soixante-dix appels de plus, pour des bougies qui vieillissent vite. La
  minute et les cinq minutes aussi, à plus forte raison, et leur série est
  oubliée après quelques jours sans lecture (§2.10).

- **Les seuils provisoires** — carnets creux et skew (voir le point de
  reprise) : à étalonner sur quelques semaines de journal.
- **Les jours fériés du CME** — `cme_gaps` lit la fermeture du vendredi à
  16 h de Chicago, que le marché ait ouvert ou non ce jour-là.
- **Les liquidations de Binance hors cryptomonnaies** — à vérifier depuis un
  réseau où le flux futures livre (§2.5).

**À trancher :**

- ~~**Le sort des satellites (§5)**~~ — tranché, dans le sens de la
  convergence là où il y avait doublon : la TUI d'arbitrage est supprimée —
  même moteur que le panneau, et « vivre sans navigateur » ne répond pas au
  besoin énoncé (§1) — tandis que l'export ETF et le tracker de news, qui
  produisent ou exportent ce qu'aucun panneau ne couvre, deviennent des
  satellites assumés. §5 n'est plus un « pas encore ».
- ~~**Version 1.0**~~ — fait : l'empaquetage a affiché `1.0.0`, puis
  `1.1.0` à la clôture des six chantiers qui ont suivi. La feuille
  de route est soldée, les chantiers d'hygiène fermés, les deux derniers
  points arbitrés — le numéro dit désormais quelque chose de vrai.
