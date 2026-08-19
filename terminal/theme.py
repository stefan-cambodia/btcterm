"""Palette et styles communs du terminal."""

C = {
    "bg":       "#080b12",
    "panel":    "#0e1117",
    "card":     "#111827",
    "border":   "#1e2639",
    "text":     "#e2e8f0",
    "muted":    "#4b5563",
    "green":    "#00d4aa",
    "red":      "#ff3d6b",
    "orange":   "#f59e0b",
    "blue":     "#3b82f6",
    "purple":   "#a78bfa",
    "yellow":   "#fbbf24",
    "cyan":     "#22d3ee",
    "grid":     "#111827",
    "ma9":      "#00d4aa",
    "ma26":     "#f59e0b",
    "ma200":    "#a78bfa",
    "bb":       "#3b82f6",
    "buy":      "#00ff99",
    "sell":     "#ff2255",
    "poc":      "#fbbf24",
    "va":       "rgba(251,191,36,0.07)",
}

MONO = "'JetBrains Mono', 'Fira Code', monospace"

PANEL_STYLE = {
    "background": C["panel"],
    "border": f"1px solid {C['border']}",
    "borderRadius": "6px",
    "padding": "8px 10px",
    "height": "100%",
    "overflow": "hidden",
    "display": "flex",
    "flexDirection": "column",
}

TITLE_STYLE = {
    # La marge droite réserve la place du bouton plein écran, qui est
    # posé en absolu par la grille et recouvrirait sinon les sélecteurs.
    "paddingRight": "30px",
    "color": C["muted"],
    "fontFamily": MONO,
    "fontSize": "10px",
    "letterSpacing": "0.12em",
    "textTransform": "uppercase",
    "marginBottom": "6px",
    "display": "flex",
    "justifyContent": "space-between",
    "alignItems": "center",
    "flexShrink": "0",
}

TABLE_STYLE = {
    "width": "100%",
    "fontFamily": MONO,
    "fontSize": "11px",
    "color": C["text"],
    "borderCollapse": "collapse",
}
