// Échappe du plein écran : on reclique le bouton du panneau agrandi, ce
// qui laisse toute la logique d'état dans le callback clientside. Le
// dialogue de disposition, posé au-dessus, se ferme en premier.
document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
        return;
    }
    const overlay = document.querySelector(
        ".layout-overlay:not(.layout-overlay-hidden)");
    if (overlay) {
        document.getElementById("layout-close").click();
        return;
    }
    const button = document.querySelector(".cell-max .zoom-btn");
    if (button) {
        button.click();
    }
});

// Cliquer le fond assombri ferme le dialogue de disposition — même
// détour que pour Échap : le clic simulé laisse l'état au callback.
document.addEventListener("click", function (event) {
    if (event.target.classList
        && event.target.classList.contains("layout-overlay")) {
        document.getElementById("layout-close").click();
    }
});

// Double-clic sur un panneau : même effet que le bouton. Les graphiques
// sont exclus — Plotly comme Lightweight Charts y réservent le
// double-clic à la réinitialisation des axes, et le leur prendre
// casserait la navigation dans le graphique.
document.addEventListener("dblclick", function (event) {
    if (event.target.closest(".js-plotly-plot") ||
            event.target.closest("#price-lwc")) {
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
