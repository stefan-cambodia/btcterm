// Carnet d'ordres écrit en place — le pendant navigateur de
// `book_payload` (terminal/panels/orderbook.py).
//
// Rendu par Dash, chaque trame du carnet était un arbre de composants à
// réconcilier, ligne par ligne : la page ne tenait plus que la moitié de
// ses minuteries, et gelait jusqu'à neuf secondes en plein écran. Ici, le
// tableau est construit une fois ; une trame ne touche que les textes et
// les barres qui ont changé.
//
// Les données arrivent par deux chemins qui aboutissent au même appel,
// `window.btcBook.render` : le canal push (push.js) et, en repli, le
// callback clientside du Store `book-data`. Le formatage — devise,
// décimales, ⚠ d'un taux de secours — est fait au serveur ; ce script ne
// fait que poser les textes. Les couleurs sont les variables CSS que le
// serveur pose sur le conteneur (`BOOK_VARS`).
(function () {
    "use strict";

    // Le tableau construit, et ce qu'il affiche : null tant que rien
    // n'est construit, ou quand un message tient la place du carnet.
    var view = null;

    function element(tag, style, parent) {
        var el = document.createElement(tag);
        if (style) { el.style.cssText = style; }
        if (parent) { parent.appendChild(el); }
        return el;
    }

    // Une ligne de niveau : prix, quantité, barre de volume.
    function level(tbody, side) {
        var tr = element("tr", null, tbody);
        var cell = "text-align: right; padding: 1px 6px; color: ";
        return {
            side: side,
            price: element("td", cell + "var(--book-" + side + ")", tr),
            qty: element("td", cell + "var(--book-muted)", tr),
            bar: element("td", "width: 45%", tr),
            shown: [null, null, null]
        };
    }

    function build(container, asks, bids) {
        var root = element("div");
        var table = element("table", "width: 100%; font-family: var(--book-mono);"
            + " font-size: 11px; color: var(--book-text);"
            + " border-collapse: collapse", root);
        var tbody = element("tbody", null, table);
        var rows = {asks: [], bids: []};
        var i;
        for (i = 0; i < asks; i++) { rows.asks.push(level(tbody, "ask")); }
        var spread = element("td", "color: var(--book-spread); text-align: center;"
            + " font-size: 10px; padding: 3px;"
            + " border-top: 1px solid var(--book-border);"
            + " border-bottom: 1px solid var(--book-border)",
            element("tr", null, tbody));
        spread.colSpan = 3;
        for (i = 0; i < bids; i++) { rows.bids.push(level(tbody, "bid")); }

        var walls = element("div", "font-family: var(--book-mono); font-size: 10px;"
            + " text-align: center; padding: 6px", root);
        element("span", "color: var(--book-muted)", walls).textContent = "murs ";
        var wallBid = element("span", "color: var(--book-bid)", walls);
        element("span", "color: var(--book-muted)", walls).textContent = " | ";
        var wallAsk = element("span", "color: var(--book-ask)", walls);

        container.replaceChildren(root);
        return {container: container, root: root, rows: rows, spread: spread,
                walls: walls, wallBid: wallBid, wallAsk: wallAsk, shown: {}};
    }

    // N'écrit que ce qui change : c'est tout l'intérêt du script.
    function set(target, key, el, prop, value) {
        if (target[key] !== value) {
            target[key] = value;
            el[prop] = value;
        }
    }

    function fill(rows, data) {
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i], values = data[i];
            set(row.shown, 0, row.price, "textContent", values[0]);
            set(row.shown, 1, row.qty, "textContent", values[1]);
            if (row.shown[2] !== values[2]) {
                row.shown[2] = values[2];
                row.bar.style.background = "linear-gradient(to right, var(--book-"
                    + row.side + "-bar) " + values[2] + "%, transparent "
                    + values[2] + "%)";
            }
        }
    }

    function message(container, text) {
        var div = element("div", "color: var(--book-muted);"
            + " font-family: var(--book-mono); font-size: 11px; padding: 12px");
        div.textContent = text;
        container.replaceChildren(div);
        view = null;
    }

    function render(data) {
        // Le panneau replié derrière un onglet n'est pas dans la page.
        var container = document.getElementById("book-table");
        if (!container || !data) { return; }
        if (data.message) { message(container, data.message); return; }
        // Reconstruire si le nombre de niveaux change (plein écran), ou si
        // Dash a remonté le conteneur (retour sur l'onglet).
        if (!view || view.container !== container
                || !container.contains(view.root)
                || view.rows.asks.length !== data.asks.length
                || view.rows.bids.length !== data.bids.length) {
            view = build(container, data.asks.length, data.bids.length);
        }
        fill(view.rows.asks, data.asks);
        fill(view.rows.bids, data.bids);
        set(view.shown, "spread", view.spread, "textContent", data.spread);
        set(view.shown, "spreadTitle", view.spread, "title", data.spread_title);
        var walls = data.walls;
        set(view.shown, "wallsDisplay", view.walls.style, "display",
            walls ? "" : "none");
        if (walls) {
            set(view.shown, "wallBid", view.wallBid, "textContent", walls.bid);
            set(view.shown, "wallAsk", view.wallAsk, "textContent", walls.ask);
            set(view.shown, "wallsTitle", view.walls, "title", walls.title);
        }
    }

    window.btcBook = {render: render};
}());
