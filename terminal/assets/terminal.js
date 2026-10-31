// Échappe du plein écran : on reclique le bouton du panneau agrandi, ce
// qui laisse toute la logique d'état dans le callback clientside. Le
// dialogue de disposition, posé au-dessus, se ferme en premier.
document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
        return;
    }
    if (document.querySelector(".keys-help")) {
        toggleKeysHelp();
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

// ── Raccourcis clavier ───────────────────────────────────────
//
// Un terminal se conduit au clavier. Chaque touche joue le geste de la
// souris, un clic sur le contrôle existant : l'état reste aux callbacks, et
// un panneau retiré de la grille rend simplement sa touche sans effet.

const KEYS_HELP = [
    "1 à 9    intervalle, de 15m à 1M",
    "E        bascule $ / €",
    "L        échelle logarithmique",
    "F        plein écran du panneau sous la souris",
    "?        cette aide",
    "Échap    revenir",
    "",
    "Sur le graphique",
    "Maj + glisser    zoom sur une zone",
    "Double-clic      vue entière",
    "Alt + clic       poser ou retirer une alerte",
].join("\n");

function toggleKeysHelp() {
    const open = document.querySelector(".keys-help");
    if (open) {
        open.remove();
        return;
    }
    const box = document.createElement("div");
    box.className = "keys-help";
    box.textContent = KEYS_HELP;
    document.body.appendChild(box);
}

// La bascule $/€ : le libellé de la devise qui n'est pas cochée.
function otherCurrency() {
    return Array.from(document.querySelectorAll("#price-currency label"))
        .find(function (label) {
            const input = label.querySelector("input");
            return input && !input.checked;
        });
}

function shortcut(key) {
    if (key >= "1" && key <= "9") {
        return document.querySelectorAll("#price-interval label")[key - 1];
    }
    switch (key.toLowerCase()) {
    case "e":
        return otherCurrency();
    case "l":
        return document.querySelector("#price-scale input");
    case "f":
        return document.querySelector(".cell:hover .zoom-btn");
    default:
        return null;
    }
}

document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey) {
        return;
    }
    // Les cases à cocher restent admises : un clic y laisse le focus, et le
    // clavier ne doit pas se taire pour autant. Les champs, eux, écrivent.
    if (event.target.matches
            && event.target.matches(
                "textarea, select, input:not([type=radio]):not([type=checkbox])")) {
        return;
    }
    if (event.key === "?") {
        toggleKeysHelp();
        return;
    }
    const control = shortcut(event.key);
    if (control) {
        control.click();
    }
});
