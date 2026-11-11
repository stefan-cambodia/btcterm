"""
La devise d'affichage, commune aux panneaux.

Le terminal calcule en dollars et n'affiche en euros que sur demande, du
sélecteur du panneau prix (Store `currency`). Une seule règle pour tous :
ce qui décrit le marché du moment se convertit, au taux du moment. Les flux
ETF et le journal restent en dollars — ce sont des faits passés, qu'un taux
d'aujourd'hui fausserait —, et les seuils d'alerte aussi, réglages comparés
à des dollars.

Le taux vient de `hub.eur_rate_cached`, sans réseau : ces panneaux tournent
pour certains dans la boucle du pousseur. Un taux de secours se dit de la même
façon partout, par un ⚠ devant le premier montant converti, et l'infobulle
donne le taux appliqué.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = ["DOLLARS", "Display", "display"]


@dataclass(frozen=True)
class Display:
    """Comment écrire un montant en dollars dans la devise choisie."""

    rate: float = 1.0
    unit: str = "$"
    warning: str = ""
    note: Optional[str] = None

    def title(self, text: str) -> str:
        """Une infobulle, complétée du taux appliqué s'il y en a un."""
        return f"{text} — {self.note}" if self.note else text


DOLLARS = Display()


def display(hub, currency: Optional[str]) -> Display:
    """L'affichage demandé ; le dollar tant que le navigateur n'a rien dit."""
    if currency != "EUR":
        return DOLLARS
    rate, stale = hub.eur_rate_cached()
    note = f"converti à 1 $ = {rate:.4f} €"
    if stale:
        note += " — taux de secours : source de change injoignable"
    return Display(rate, "€", "⚠ " if stale else "", note)


def spread_text(pct: float) -> str:
    """Un spread en pourcentage qui dit quelque chose.

    Quatre décimales suffisaient quand le pas de cotation pesait : à
    73 000 $, un centime d'écart vaut 0,000014 %, et le bandeau comme le
    carnet écrivaient « 0.0000 % » à longueur de journée. Sous le millième
    de pour cent, deux décimales de plus.
    """
    return f"{pct:.4f} %" if pct >= 0.001 else f"{pct:.6f} %"
