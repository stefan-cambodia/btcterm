"""
btcterm — socle commun du terminal Bitcoin.

Trois couches, indépendantes de toute interface :

- `indicators` : calculs techniques purs sur des séries pandas
- `exchanges`  : carnet d'ordres normalisé et connecteurs WebSocket
- `sources`    : collecteurs REST, ETF, news et sentiment

Les panneaux du terminal, comme les outils en ligne de commande qui
subsistent, composent ces briques ; aucune ne dépend d'eux en retour.
"""

__all__ = ["indicators", "exchanges", "sources"]
