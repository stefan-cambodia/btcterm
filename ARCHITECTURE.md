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
contexte monétaire.

### État actuel : le terminal existe

Le dépôt est désormais une application, entourée de quelques outils en ligne
de commande qui couvrent ce que le terminal n'a pas encore absorbé.

- **Le socle** ([§2](#2-le-socle-btcterm)) : tout ce qui n'est pas rendu —
  calculs d'indicateurs, connexions aux plateformes, collecte, moteur
  d'arbitrage — vit dans `btcterm/`.
- **Le terminal** ([§3](#3-le-terminal-terminal)) : une application Dash unique
  regroupant quatorze panneaux sur une grille de six cellules — une cellule pouvant
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
│   │                              terme, chaîne, news, sentiment
│   ├── macrocal.py                calendrier macro tenu à la main
│   ├── newsdb.py                  base de news : schéma, scoring, collecte
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
│   ├── header.py                  bandeau : cours, variation, spread, flux
│   ├── push.py                    canal push des panneaux rapides (§3.10)
│   ├── lwc.py                     données des rendus LWC : /api/klines,
│   │                              /api/profile et /api/perp (§3.2)
│   ├── wsgi.py                    fabrique gunicorn du régime service (§3.11)
│   ├── demo.py                    démo statique pour GitHub Pages (§3.12)
│   ├── theme.py                   palette et styles
│   ├── charts.py                  figures Plotly des autres panneaux
│   ├── assets/                    CSS et JS servis au navigateur — dont
│   │                              lwc-price.js et lwc-perp.js, les dessins
│   │                              des panneaux prix et perpétuel, et
│   │                              vendor/ (Lightweight Charts, sans CDN)
│   └── panels/                    price · orderbook · arbitrage ·
│                                   liquidations · etf · perp · news ·
│                                   calendar · alerts · macro ·
│                                   dominance · onchain
│
├── tests/
│   ├── test_indicators_parity.py  non-régression des indicateurs
│   ├── test_news_scoring.py       non-régression du scoring des news
│   ├── test_liquidations.py       lecture du flux de liquidations
│   ├── test_macrocal.py           calendrier macro tenu à la main
│   ├── test_fear_greed.py         indice Fear & Greed : source, hub, couleurs
│   ├── test_etf_flows.py          flux ETF : fenêtres, cumul, classement
│   ├── test_terminal_wiring.py    panneaux posés et branchés
│   ├── test_grid_layout.py        rangement configurable des panneaux
│   ├── test_fullscreen_toggle.py  bascule plein écran (sous Node)
│   ├── test_push.py               pousseur WebSocket, sans navigateur
│   ├── test_journal.py            journal : événements, épisodes, rétention
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
| Hors ligne | `generate_demo_ohlcv` |
| Change | `fetch_eur_rate` |
| Institutionnel | `fetch_etf_flows` |
| Macro | `fetch_m2_supply` |
| Marché à terme | `fetch_funding_history`, `fetch_open_interest`, `fetch_perp_snapshot` |
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

### 2.6 `macrocal` — le calendrier macro tenu à la main

Aucune source publique satisfaisante n'existe pour un calendrier
économique : les API ouvertes sont payantes ou sans licence claire. Mais
les émetteurs publient eux-mêmes leurs dates longtemps à l'avance — la
Fed donne ses réunions deux ans avant, l'OMB publie chaque automne le
calendrier de l'année suivante pour toutes les statistiques fédérales.
`macrocal` est la transcription de ces calendriers officiels, vérifiée à
la source : décisions du FOMC 2026–2027 (avec le marqueur SEP des
réunions à projections), CPI, rapport emploi (NFP) et inflation PCE 2026.

Le module expose `EVENTS` (la liste triée), `upcoming` (l'à-venir,
événement du jour compris — « aujourd'hui » est précisément ce qu'on
vient lire), `next_of` (le prochain d'une famille, pour les badges) et
`last_date` (jusqu'où court la liste).

Deux pièges justifient que ce soit du code et non un simple tableau :

- **Le fuseau.** Les publications sont définies en heure de New York
  (8 h 30 pour les statistiques, 14 h pour le FOMC), et l'heure d'été
  américaine ne commence ni ne finit avec l'européenne : deux fois par
  an, le décalage avec Bruxelles change pendant deux à trois semaines.
  `when_utc` convertit via `zoneinfo`, et le panneau affiche l'heure de
  la machine.
- **L'épuisement.** Une liste tenue à la main finit par se périmer ;
  plutôt que de se taire, le panneau affiche l'horizon de la liste et
  prévient quand il approche — le pendant du fil de news qui affiche
  l'âge de sa collecte.

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
jusqu'au flux ni à la boucle, qui retentera au balayage suivant.

La base n'existe qu'à la première écriture : construire un `Journal` —
ce que fait tout `MarketHub`, démarré ou non — ne crée aucun fichier,
et les tests ne laissent aucune trace. `--no-journal` dispense le
terminal d'en tenir un. La séance se relit à même la ligne de
commande :

```bash
python -m btcterm.journal --heures 6
```

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
l'historique accumulé n'est jamais perdu.

`tests/test_journal.py` déroule la vie d'un épisode au temps simulé —
ouverture, meilleur profit, flottement toléré, clôture après la grâce —
et protège les frontières : panne du rappel sans effet sur le flux,
aucun fichier créé sans écriture, rétention appliquée — la double
rétention des instantanés, leur écriture partielle et le prolongement
de l'open interest compris.

### 2.8 `alerts` — le terminal sait attirer l'attention

Douze panneaux qu'il faut balayer des yeux couvraient la surveillance
active ; la passive demande l'inverse — que le terminal prévienne.
`AlertEngine` évalue onze règles dans la boucle d'observation du hub
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

Les règles d'état sonnent sur le **front montant** et pas avant un
délai de garde de 10 minutes : une condition qui dure ne sonne qu'une
fois, une condition qui clignote ne sonne pas en rafale. Les contrôles
qui coûtent (financement en cache REST, lecture SQLite des news,
lecture technique des chandeliers) ne tournent qu'à la minute. Chaque
sonnerie part au journal (§2.7) — la relecture d'une séance inclut ce
qui a sonné.

Le moteur ne connaît pas Dash : il reçoit le hub en paramètre, comme
les fonctions de rendu des panneaux, et `tests/test_alerts.py` le
déroule au temps simulé sur un hub factice — hystérésis, fronts,
cadences, journalisation, un jour de flux qui se remplit puis grossit
sans resonner, et la normalisation des réglages venus du localStorage. Le test garde aussi la normalisation contre un bug
réellement rencontré : une copie superficielle des défauts partageait
la liste des seuils, et le premier seuil posé mutait les réglages par
défaut de tout le processus.

Côté interface, le panneau ALERTES (onglet de la cellule news) affiche
les sonneries et les réglages, qui vivent dans un Store persisté et
réarment le moteur au chargement ; la cloche du bandeau compte la
dernière heure et **ouvre le panneau d'un clic** — elle demande à la
grille (`reveal`) dans quelle cellule le panneau a été rangé, et quitte
au passage un éventuel plein écran qui le masquerait ; un bip et une notification navigateur (permission
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

`btcterm/resolver.py` enveloppe `socket.getaddrinfo`. Quand la réponse du
système pour un nom public ne contient que des adresses de bouclage ou
nulles — ou quand le nom n'existe soudain plus —, le nom est redemandé à
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

### 2.10 Non-régression

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
│                    prix et perpétuel, vendor/ porte Lightweight Charts
│                    (aucun CDN)
└── panels/          un module par panneau
    ├── price.py         chandeliers, indicateurs, profil de volume
    ├── orderbook.py     carnet + profondeur comparée
    ├── arbitrage.py     écarts inter-plateformes
    ├── etf.py           flux des ETF spot
    ├── perp.py          financement, open interest, positionnement
    ├── liquidations.py  positions fermées de force
    ├── dominance.py     parts de capitalisation
    ├── onchain.py       hashrate, difficulté, mempool
    ├── news.py          fil de news + Fear & Greed (courbe 90 j agrandi)
    ├── calendar.py      échéances macro : FOMC, CPI, NFP, PCE
    ├── alerts.py        sonneries et réglages du moteur d'alertes
    └── macro.py         cours contre masse monétaire M2
```

```
┌─────────┬──────────────────┬─────────────────┐
│         │ CARNET  PROFOND. │ ARBITRAGE  LIQ. │
│         │                  ├─────────────────┤
│  prix   │                  │                 │
│         │                  │ NEWS  CALENDRIER│
│         │                  │      ALERTES    │
│         ├──────────────────┤                 │
│         │ ETF   PERPÉTUEL  │                 │
│         ├──────────────────┴─────────────────┤
│         │ MACRO  DOMINANCE  ON-CHAIN         │
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
affiche doit avancer sans attendre cinq minutes.

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
(§2.8) et le bandeau de démonstration (§4.3).

Les réglages de la barre de titre passent par un unique callback
clientside qui relaie l'état des sélecteurs à `window.lwcPrice` : un
refetch seulement quand l'intervalle change ; la devise et l'échelle log
se règlent sur place, le paquet étant gardé en USD et le taux € voyageant
avec lui. Aucun aller-retour serveur pour un réglage d'affichage.
L'historique se charge à la volée : un pan vers le passé demande la page
antérieure de `/api/klines`, jusqu'à ce que la source avoue ne plus rien
avoir.

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
compteur, liquidations et leurs badges — à une cadence de 100 ms. Une septième
cible s'y est jointe avec la voie A, à la cadence de l'horloge lente qu'elle
double : la bougie courante du panneau prix et ses derniers points
d'indicateurs, jamais la série entière — le navigateur tient la série, le
canal ne transporte que la mutation (§3.2).

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

Le rendu dépend d'un état qui vit côté navigateur — plateforme du carnet,
panneau agrandi. Deux callbacks clientside le relaient à push.js, qui l'annonce
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
| chandeliers Binance, sans rien en cache | série de démonstration, signalée par un bandeau |
| taux EUR | constante `0.924` |
| masse monétaire M2 | tableau vide, le panneau macro le dit |
| marché à terme Binance | tableau ou dictionnaire vide, le panneau perpétuel le dit |
| agrégats de marché, données de chaîne | idem : le panneau affiche son indisponibilité |
| ticker 24 h | dict vide, le bandeau affiche `—` |
| WebSocket (tous) | reconnexion (3 s fixes, ou backoff exponentiel plafonné à 30 s) |
| flux RSS (`news`) | le feed en échec est sauté, les autres continuent |
| CryptoPanic sans clé | source simplement désactivée |

Le repli sur données de démonstration mérite une précaution : un graphique
synthétique qu'on ne distingue pas d'un vrai est pire que pas de graphique du
tout. `generate_demo_ohlcv` marque donc sa sortie d'un `attrs["demo"]`, que
`/api/klines` relaie en champ du paquet et sur lequel lwc-price.js pose un
bandeau orange. La série est une marche
aléatoire log-normale paramétrée en annualisé (55 % de volatilité, 40 % de
dérive), ramenée à la durée d'une bougie en racine du temps et recalée pour
finir sur un prix plausible — sans quoi une longue série mensuelle dériverait
vers des valeurs absurdes.

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
quatorze panneaux couvrent le prix, la liquidité, l'arbitrage, les
liquidations, les flux ETF, le marché à terme, les news, le calendrier macro,
la macro, la dominance, la chaîne et la relecture de séance. La piste de confort laissée ouverte à la pause
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

### Étape 1 — Extraire le socle commun ✅ *faite*

Les trois modules décrits en [§2](#2-le-socle-btcterm) sont en place et les huit
scripts y sont ramenés, sans changement de comportement (§2.10) :

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
  trace sous les barres la tendance ainsi construite, séance après séance.
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
des manques constatés à la clôture — chacun à ouvrir quand l'usage le fera
sentir, aucun ne conditionnant les autres.

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
- ~~**Un lien ouvert mais muet**~~ — fait : le fil date, par lien, le
  dernier événement reçu et l'ouverture du lien, et `silent` nomme ceux
  qui tiennent sans rien livrer depuis plus d'un quart d'heure (§2.5).
  Le badge du panneau l'écrit en jaune — « Binance muet depuis 22 min » —
  à côté du lien qui manque, s'il y en a un. C'était le cas de Binance
  depuis le Cambodge : le flux futures s'ouvre, s'abonne et se tait
  (§2.9), Bybit porte le panneau seul, et l'écran le dit désormais.

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
