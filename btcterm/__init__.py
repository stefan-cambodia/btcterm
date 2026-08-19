"""
btcterm — socle commun du terminal Bitcoin.

Trois couches, indépendantes de toute interface :

- `indicators` : calculs techniques purs sur des séries pandas
- `exchanges`  : carnet d'ordres normalisé et connecteurs WebSocket
- `sources`    : collecteurs REST, ETF, news et sentiment

Les panneaux (dashboards web, fenêtres matplotlib, TUI, CLI) composent
ces briques ; aucune ne dépend d'eux en retour.
"""

__all__ = ["indicators", "exchanges", "sources"]
