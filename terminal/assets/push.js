// Canal « push » des panneaux rapides — le pendant navigateur de
// terminal/push.py.
//
// Tant que le WebSocket tient, l'horloge tick-fast est coupée : les
// trames du serveur arrivent par set_props, le même chemin de mise à
// jour qu'une réponse de callback. S'il tombe — serveur relancé, tunnel
// rompu — l'horloge est rallumée et le terminal continue en
// interrogation, pendant que la reconnexion retente en arrière-plan.
// Le bandeau dit toujours le canal en vigueur : « push » ou « poll ».
(function () {
    "use strict";

    var RETRY_MIN = 1000;
    var RETRY_MAX = 30000;

    var socket = null;
    var retry = RETRY_MIN;
    // L'état annoncé au serveur. Il est alimenté par les callbacks
    // clientside que pose terminal/push.py — pas lu dans le DOM, où la
    // plateforme choisie n'existe pas toujours (onglet profondeur).
    var state = {exchange: null, expanded: null};

    function badge(text) {
        var el = document.getElementById("hdr-push");
        if (el) { el.textContent = text; }
    }

    function announce() {
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify(state));
        }
    }

    // Point d'entrée des callbacks clientside : retenir l'état, et ne
    // l'annoncer que s'il change — le serveur vide son cache d'envoi à
    // chaque annonce, autant ne pas le faire pour rien.
    window.btcPush = {
        state: function (partial) {
            var changed = false;
            for (var key in partial) {
                if (state[key] !== partial[key]) {
                    state[key] = partial[key];
                    changed = true;
                }
            }
            if (changed) { announce(); }
        }
    };

    function apply(frame) {
        for (var id in frame) {
            // Un panneau replié derrière un onglet n'est pas dans la
            // page : sa mise à jour est écartée. Au remontage, Dash
            // rejoue lui-même le callback du panneau — il se remplit
            // sans attendre le canal.
            if (document.getElementById(id)) {
                window.dash_clientside.set_props(id, frame[id]);
            }
        }
    }

    function connect() {
        var proto = location.protocol === "https:" ? "wss://" : "ws://";
        socket = new WebSocket(proto + location.host + "/push");
        socket.onopen = function () {
            retry = RETRY_MIN;
            badge("push");
            announce();
            // L'horloge n'est coupée qu'une fois le canal ouvert : pas
            // de fenêtre où plus rien ne rafraîchit.
            window.dash_clientside.set_props("tick-fast", {disabled: true});
        };
        socket.onmessage = function (event) {
            apply(JSON.parse(event.data));
        };
        socket.onclose = function () {
            socket = null;
            badge("poll");
            window.dash_clientside.set_props("tick-fast", {disabled: false});
            setTimeout(connect, retry);
            retry = Math.min(retry * 2, RETRY_MAX);
        };
    }

    // set_props et la grille n'existent qu'une fois le rendu Dash
    // démarré ; d'ici là, on repasse plus tard. Un serveur sans la
    // route /push fait simplement échouer la connexion : le badge reste
    // à « poll » et l'horloge n'est jamais coupée.
    (function start() {
        if (!window.dash_clientside || !window.dash_clientside.set_props
                || !document.getElementById("grid")) {
            setTimeout(start, 200);
            return;
        }
        badge("poll");
        connect();
    }());
}());
