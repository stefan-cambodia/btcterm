"""
btcterm — socle commun du terminal Bitcoin.

Sept modules, indépendants de toute interface :

- `indicators` : calculs techniques purs sur des séries pandas
- `exchanges`  : carnet d'ordres normalisé et connecteurs WebSocket
- `sources`    : collecteurs REST — marché, ETF, masse monétaire, news
- `arbitrage`  : moteur d'écarts inter-plateformes
- `liquidations`: fil des positions fermées de force
- `newsdb`     : base de news partagée — schéma, scoring, collecte
- `hub`        : connexions mutualisées, caches et collecte de fond

Les panneaux du terminal, comme les outils en ligne de commande qui
subsistent, composent ces briques ; aucune ne dépend d'eux en retour.
"""

__all__ = ["indicators", "exchanges", "sources", "arbitrage", "liquidations",
           "newsdb", "hub"]
