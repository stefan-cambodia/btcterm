// Échappe du plein écran : on reclique le bouton du panneau agrandi, ce
// qui laisse toute la logique d'état dans le callback clientside.
document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
        return;
    }
    const button = document.querySelector(".cell-max .zoom-btn");
    if (button) {
        button.click();
    }
});

// Double-clic sur un panneau : même effet que le bouton. Les graphiques
// sont exclus — Plotly y réserve le double-clic pour réinitialiser les
// axes, et le lui prendre casserait la navigation dans le graphique.
document.addEventListener("dblclick", function (event) {
    if (event.target.closest(".js-plotly-plot")) {
        return;
    }
    const cell = event.target.closest(".cell");
    if (!cell) {
        return;
    }
    const button = cell.querySelector(".zoom-btn");
    if (button) {
        button.click();
    }
});
