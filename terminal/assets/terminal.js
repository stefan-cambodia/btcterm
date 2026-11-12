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
    if (document.querySelector(".command-bar")) {
        closeCommand();
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
    "- / +    intervalle plus fin / plus large, jusqu'à 1m",
    "E        bascule $ / €",
    "L        échelle logarithmique",
    "S        afficher ou masquer les flèches",
    "P        capture du graphique en image",
    "T        tracer une ligne de tendance",
    "N        niveaux clés : veille, semaine, mois",
    "M        carte des liquidations",
    "O        gros ordres sur le graphique",
    "F        plein écran du panneau sous la souris",
    "/        ligne de commande",
    "?        cette aide",
    "Échap    revenir",
    "",
    "Sur le graphique",
    "Maj + glisser    zoom sur une zone",
    "Ctrl + glisser   mesurer un mouvement",
    "Double-clic      vue entière",
    "Alt + clic       poser ou retirer une alerte, retirer une ligne",
    "",
    "Ligne de commande",
    "un panneau       etf, perp, analyse, alertes…",
    "un intervalle    1m, 5m, 4h, 1d, 1M…",
    "eur, usd, log, cvd, oi, rsi, crsi, vol, profil, niv, carte, ord, murs, signaux, capture",
    "ligne, effacer   tracer une ligne, retirer toutes les lignes",
    "alerte 80000     seuil de cours, en dollars",
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

// Les chiffres gardent les intervalles qu'ils avaient avant l'arrivée de la
// minute et des cinq minutes : 1 reste le quart d'heure, 7 le jour, 9 le
// mois — une main habituée ne se trompe pas. Les deux nouveaux s'atteignent
// par `-`, qui affine pas à pas, ou par la ligne de commande.
const KEY_INTERVALS = {"1": "15m", "2": "30m", "3": "1h", "4": "4h",
                       "5": "6h", "6": "12h", "7": "1d", "8": "1w", "9": "1M"};

function intervalLabel(value) {
    return labels("#price-interval label")
        .find((label) => label.textContent.trim() === value);
}

// L'intervalle voisin de celui qui est coché : `step` -1 plus fin, +1 plus
// large ; rien au bout de la liste.
function neighbourInterval(step) {
    const all = labels("#price-interval label");
    const current = all.findIndex((label) => {
        const input = label.querySelector("input");
        return input && input.checked;
    });
    return current < 0 ? null : all[current + step] || null;
}

function shortcut(key) {
    if (KEY_INTERVALS[key]) {
        return intervalLabel(KEY_INTERVALS[key]);
    }
    if (key === "-") {
        return neighbourInterval(-1);
    }
    if (key === "+" || key === "=") {
        return neighbourInterval(1);
    }
    switch (key.toLowerCase()) {
    case "e":
        return otherCurrency();
    case "l":
        return document.querySelector("#price-scale input");
    case "p":
        if (window.lwcPrice) {
            window.lwcPrice.capture();
        }
        return null;
    case "s":
        if (window.lwcPrice) {
            window.lwcPrice.toggleSignals();
        }
        return null;
    case "t":
        if (window.lwcPrice) {
            window.lwcPrice.toggleDrawing();
        }
        return null;
    case "f":
        return document.querySelector(".cell:hover .zoom-btn");
    case "n":
        return labels("#price-extras label")
            .find((label) => label.textContent.trim() === "NIV");
    case "m":
        return labels("#price-extras label")
            .find((label) => label.textContent.trim() === "LIQ");
    case "o":
        return labels("#price-extras label")
            .find((label) => label.textContent.trim() === "ORD");
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
    if (event.key === "/") {
        event.preventDefault();
        openCommand();
        return;
    }
    const control = shortcut(event.key);
    if (control) {
        control.click();
    }
});

// ── Ligne de commande ────────────────────────────────────────
//
// Le geste des terminaux de marché : `/`, un mot, Entrée. Comme les
// raccourcis, chaque commande clique le contrôle existant — sauf le seuil
// d'alerte, qui passe par le graphique. Rien ne dépend d'une liste de
// panneaux : les onglets et les sélecteurs se lisent dans la page, y compris
// quand la disposition a été réarrangée.

// Sans accents ni casse : « perpétuel », « Perpetuel » et « PERPÉTUEL » se
// valent, et `/on-chain` comme `/onchain`.
function simplify(text) {
    return text.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-zA-Z0-9]/g, "").toLowerCase();
}

function labels(selector) {
    return Array.from(document.querySelectorAll(selector));
}

// Un onglet, dont la cellule peut être cachée par un plein écran voisin.
function openTab(tab) {
    const max = document.querySelector(".cell-max");
    if (max && !max.contains(tab)) {
        max.querySelector(".zoom-btn").click();
    }
    tab.click();
    return true;
}

function clicked(control) {
    if (control) {
        control.click();
    }
    return !!control;
}

function setCurrency(symbol) {
    const label = labels("#price-currency label")
        .find((item) => item.textContent.trim() === symbol);
    const input = label && label.querySelector("input");
    return input && !input.checked ? clicked(label) : !!label;
}

function findTab(word) {
    const tabs = labels(".cell-tab");
    return tabs.find((tab) => simplify(tab.textContent) === word)
        || tabs.find((tab) => word.length >= 3
                      && simplify(tab.textContent).startsWith(word))
        || tabs.find((tab) => word.length >= 3
                      && simplify(tab.textContent).includes(word));
}

// Rend vrai si la commande a été comprise et jouée.
function runCommand(text) {
    const raw = text.trim();
    const alert = raw.match(/^(?:alerte?|seuil)\s+([\d\s.,]+)$/i);
    if (alert) {
        const level = parseFloat(alert[1].replace(/[\s,]/g, ""));
        return !!window.lwcPrice && window.lwcPrice.addAlert(level);
    }
    const word = simplify(raw);
    if (!word) {
        return false;
    }
    if (word === "eur" || raw === "€") {
        return setCurrency("€");
    }
    if (word === "usd" || raw === "$") {
        return setCurrency("$");
    }
    if (word === "log") {
        return clicked(document.querySelector("#price-scale input"));
    }
    if (word === "signaux" || word === "fleches") {
        return !!window.lwcPrice && (window.lwcPrice.toggleSignals(), true);
    }
    if (word === "ligne") {
        return !!window.lwcPrice && (window.lwcPrice.toggleDrawing(), true);
    }
    if (word === "effacer") {
        return !!window.lwcPrice && window.lwcPrice.clearDrawings();
    }
    if (word === "capture") {
        return !!window.lwcPrice && window.lwcPrice.capture();
    }
    if (word === "carte") {
        return clicked(labels("#price-extras label")
            .find((label) => label.textContent.trim() === "LIQ"));
    }
    if (word === "niveaux") {
        return clicked(labels("#price-extras label")
            .find((label) => label.textContent.trim() === "NIV"));
    }
    if (word === "aide") {
        toggleKeysHelp();
        return true;
    }
    // La casse compte d'abord : `1M` est le mois.
    const interval = labels("#price-interval label");
    const exact = interval.find((label) => label.textContent.trim() === raw)
        || interval.find((label) => simplify(label.textContent) === word);
    if (exact) {
        return clicked(exact);
    }
    // `liq` reste à l'onglet des liquidations, qu'il ouvrait avant que la
    // case LIQ n'existe : la carte répond à `carte`.
    const extra = word !== "liq" && labels("#price-extras label")
        .find((label) => simplify(label.textContent) === word);
    if (extra) {
        return clicked(extra);
    }
    const tab = findTab(word);
    return tab ? openTab(tab) : false;
}

window.btcCommand = runCommand;

function closeCommand() {
    const bar = document.querySelector(".command-bar");
    if (bar) {
        bar.remove();
    }
}

function openCommand() {
    if (document.querySelector(".command-bar")) {
        return;
    }
    const bar = document.createElement("div");
    bar.className = "command-bar";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "etf, 4h, eur, alerte 80000…  (? pour la liste)";
    bar.appendChild(input);
    document.body.appendChild(bar);
    input.focus();
    input.addEventListener("keydown", function (event) {
        event.stopPropagation();
        if (event.key === "Escape") {
            closeCommand();
        } else if (event.key === "Enter") {
            if (runCommand(input.value)) {
                closeCommand();
            } else {
                // Rester ouvert : une faute de frappe se corrige, elle ne
                // doit pas s'évanouir sans rien dire.
                bar.classList.add("command-bar-error");
                input.select();
            }
        } else {
            bar.classList.remove("command-bar-error");
        }
    });
}
