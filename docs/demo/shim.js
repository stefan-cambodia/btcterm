// Démo statique : le serveur en moins.
//
// lwc-price.js ne connaît que deux routes — /api/klines pour les pages
// de chandeliers, /api/profile pour le profil de volume de la plage
// visible. Ici, `fetch` est détourné avant son chargement : ces deux
// routes sont servies depuis des paquets figés (demo/data/<intervalle>.json,
// écrits par `python -m terminal.demo`), paginés par `time` exactement
// comme le ferait terminal/lwc.py ; le profil est recalculé dans le
// navigateur, port fidèle de btcterm.indicators.volume_profile. Tout
// autre appel passe au vrai `fetch`.
//
// Le fichier est aussi chargé sous Node par tests/test_demo.py, qui
// compare pagination et profil à ce que produit le serveur.
(function (root) {
    "use strict";

    var realFetch = root.fetch ? root.fetch.bind(root) : null;
    var cache = {};
    var DATA_PATH = "demo/data/";
    var VOL_BINS = 60;
    var VALUE_AREA = 0.70;

    function load(interval) {
        if (!cache[interval]) {
            cache[interval] = realFetch(DATA_PATH + interval + ".json")
                .then(function (r) {
                    if (!r.ok) { throw new Error("paquet absent : " + interval); }
                    return r.json();
                });
        }
        return cache[interval];
    }

    // ── /api/klines : la page se découpe par `time` ─────────
    // `before` exclut la bougie que le client tient déjà ; `limit`
    // borne la page ; les autres tableaux suivent l'intervalle de
    // temps couvert par les bougies retenues.
    function page(packet, before, limit) {
        var bars = before === null ? packet.bars
            : packet.bars.filter(function (b) { return b.time < before; });
        bars = bars.slice(-limit);
        var empty = {bars: [], volume: [], overlays: {}, panes: {},
                     volume_ma: [], signals: [], demo: false,
                     interval: packet.interval, eur_rate: packet.eur_rate};
        if (!bars.length) { return empty; }
        var lo = bars[0].time, hi = bars[bars.length - 1].time;
        function within(points) {
            return (points || []).filter(function (p) {
                return p.time >= lo && p.time <= hi;
            });
        }
        var overlays = {}, panes = {}, name;
        for (name in packet.overlays) { overlays[name] = within(packet.overlays[name]); }
        for (name in packet.panes) { panes[name] = within(packet.panes[name]); }
        return {bars: bars, volume: within(packet.volume), overlays: overlays,
                panes: panes, volume_ma: within(packet.volume_ma),
                signals: within(packet.signals), demo: false,
                interval: packet.interval, eur_rate: packet.eur_rate};
    }

    // ── /api/profile : volume_profile, tel quel ─────────────
    function searchSorted(edges, x, side) {
        var lo = 0, hi = edges.length;
        while (lo < hi) {
            var mid = (lo + hi) >> 1;
            if (side === "left" ? edges[mid] < x : edges[mid] <= x) { lo = mid + 1; }
            else { hi = mid; }
        }
        return lo;
    }

    function volumeProfile(rows, bins) {
        var low = Infinity, high = -Infinity, i;
        for (i = 0; i < rows.length; i++) {
            if (rows[i].low < low) { low = rows[i].low; }
            if (rows[i].high > high) { high = rows[i].high; }
        }
        var edges = [];
        for (i = 0; i <= bins; i++) { edges.push(low + (high - low) * i / bins); }
        var volumes = new Array(bins).fill(0);
        rows.forEach(function (row) {
            var idxLo = Math.max(0, Math.min(searchSorted(edges, row.low, "left"), bins - 1));
            var idxHi = Math.max(0, Math.min(searchSorted(edges, row.high, "right"), bins));
            var span = Math.max(1, idxHi - idxLo);
            for (var k = idxLo; k < idxHi; k++) { volumes[k] += row.volume / span; }
        });
        var centers = [];
        for (i = 0; i < bins; i++) { centers.push((edges[i] + edges[i + 1]) / 2); }
        var pocIndex = 0;
        for (i = 1; i < bins; i++) { if (volumes[i] > volumes[pocIndex]) { pocIndex = i; } }
        var total = volumes.reduce(function (a, b) { return a + b; }, 0);
        var order = volumes.map(function (v, idx) { return idx; })
            .sort(function (a, b) { return volumes[b] - volumes[a] || a - b; });
        var cumulative = 0, selected = [];
        for (i = 0; i < order.length; i++) {
            cumulative += volumes[order[i]];
            selected.push(order[i]);
            if (cumulative >= VALUE_AREA * total) { break; }
        }
        return {centers: centers, volumes: volumes, poc: centers[pocIndex],
                va_low: centers[Math.min.apply(null, selected)],
                va_high: centers[Math.max.apply(null, selected)]};
    }

    function profile(packet, interval, from, to) {
        var volumeByTime = {};
        (packet.volume || []).forEach(function (p) { volumeByTime[p.time] = p.value; });
        var rows = packet.bars.filter(function (b) {
            return b.time >= from && b.time <= to;
        }).map(function (b) {
            return {low: b.low, high: b.high, volume: volumeByTime[b.time] || 0};
        });
        if (!rows.length) { return {empty: true, demo: false, interval: interval}; }
        var result = volumeProfile(rows, VOL_BINS);
        result.interval = interval;
        result.demo = false;
        return result;
    }

    function respond(body) {
        return new Response(JSON.stringify(body),
                            {headers: {"Content-Type": "application/json"}});
    }

    function serve(url) {
        var query = new URLSearchParams(url.split("?")[1] || "");
        var interval = query.get("interval") || "1d";
        return load(interval).then(function (packet) {
            if (url.indexOf("/api/profile") === 0) {
                return respond(profile(packet, interval,
                                       Number(query.get("from")),
                                       Number(query.get("to"))));
            }
            var before = query.get("before");
            return respond(page(packet,
                                before === null ? null : Number(before),
                                Number(query.get("limit") || 365)));
        });
    }

    root.fetch = function (url, options) {
        var target = String(url);
        if (target.indexOf("/api/klines") === 0 || target.indexOf("/api/profile") === 0) {
            return serve(target);
        }
        return realFetch(url, options);
    };

    // Exposé pour les tests (Node) et la page (état du chargement).
    root.demoShim = {page: page, profile: profile, volumeProfile: volumeProfile,
                     load: load, setDataPath: function (p) { DATA_PATH = p; },
                     setFetch: function (f) { realFetch = f; }};
}(typeof window !== "undefined" ? window : globalThis));
