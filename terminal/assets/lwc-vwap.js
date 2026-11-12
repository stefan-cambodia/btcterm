// VWAP ancré du panneau prix : le prix moyen pondéré par le volume depuis
// le début de la période, et ses bandes à un écart-type.
//
// La référence de prix « juste » d'une séance — celle que les desks
// regardent : au-dessus, les acheteurs de la journée sont en gain, en
// dessous en perte. L'ancrage suit l'intervalle du graphique : le jour
// UTC jusqu'à l'heure, la semaine (lundi 0 h UTC) de 2 h à 12 h, le mois
// en journalier ; aucun au-delà, où une période ne compterait qu'une ou
// deux bougies.
//
// Calcul côté navigateur, sur les bougies chargées : le canal push ne
// recalcule côté serveur que les 250 dernières bougies, moins qu'une
// journée de minutes — un VWAP du jour y serait faux. Le prix typique
// (haut + bas + clôture) / 3 de chaque bougie pèse son volume ; l'écart-
// type est celui de ce prix autour du VWAP, pondéré pareil.
//
// Les temps sont des temps réels (UTC) ; le graphique les décale à
// l'affichage. Chaque début de période est marqué (`start`) : le tracé
// y rompt la courbe au lieu de relier deux périodes.
//
// `fromAnchor` est l'autre VWAP, celui qu'on ancre soi-même (touche A) :
// depuis une bougie choisie — le sommet, le dernier creux, le jour d'une
// annonce —, le prix moyen payé par tous ceux qui ont traité depuis.
// Au-dessus, ils sont en gain ; le cours y revient souvent chercher appui
// ou résistance.
(function () {
    "use strict";

    var DAY = 86400;
    var ANCHORS = {"1m": "day", "5m": "day", "15m": "day", "30m": "day",
                   "1h": "day", "2h": "week", "4h": "week", "6h": "week",
                   "12h": "week", "1d": "month"};
    var LABELS = {day: "J", week: "S", month: "M"};

    function anchorOf(interval) {
        return ANCHORS[interval] || null;
    }

    // Le début, en secondes UTC, de la période qui contient `t`.
    function periodStart(t, anchor) {
        var day = Math.floor(t / DAY);
        if (anchor === "day") {
            return day * DAY;
        }
        if (anchor === "week") {
            // Le 1er janvier 1970 était un jeudi : (jour + 3) % 7 compte
            // les jours depuis le lundi.
            return (day - (day + 3) % 7) * DAY;
        }
        var date = new Date(t * 1000);
        return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1) / 1000;
    }

    // `bars` : {time, high, low, close} ; `volumes` : {time, value}, rangés
    // comme les bougies. Rend trois listes de points — vwap, haut, bas —
    // de même longueur que les bougies qui ont un volume.
    function compute(bars, volumes, anchor) {
        var out = {vwap: [], upper: [], lower: []};
        if (!anchor || !bars || !bars.length) { return out; }
        var volume = {};
        (volumes || []).forEach(function (v) { volume[v.time] = v.value; });
        var current = null;
        var sumV = 0;
        var sumPV = 0;
        var sumP2V = 0;
        bars.forEach(function (bar) {
            var v = volume[bar.time];
            if (!(v > 0)) { return; }
            var start = periodStart(bar.time, anchor);
            var first = start !== current;
            if (first) {
                current = start;
                sumV = 0;
                sumPV = 0;
                sumP2V = 0;
            }
            var price = (bar.high + bar.low + bar.close) / 3;
            sumV += v;
            sumPV += price * v;
            sumP2V += price * price * v;
            var vwap = sumPV / sumV;
            var sd = Math.sqrt(Math.max(0, sumP2V / sumV - vwap * vwap));
            out.vwap.push({time: bar.time, value: vwap, start: first});
            out.upper.push({time: bar.time, value: vwap + sd, start: first});
            out.lower.push({time: bar.time, value: vwap - sd, start: first});
        });
        return out;
    }

    // Le VWAP ancré sur l'instant `start` (secondes UTC) : depuis la bougie
    // qui le contient — la dernière ouverte au plus tard à cet instant —
    // jusqu'à la dernière. Une ancre posée en 1h et lue en 1d part ainsi
    // du début de sa journée : la bougie entière compte. Rend `null` quand
    // l'ancre précède la première bougie chargée : le volume d'avant
    // manque, et un VWAP qui l'ignorerait serait faux — remonter le
    // graphique charge l'historique qui le rend juste.
    function fromAnchor(bars, volumes, start) {
        if (!bars || !bars.length || !(start >= bars[0].time)) {
            return null;
        }
        var volume = {};
        (volumes || []).forEach(function (v) { volume[v.time] = v.value; });
        var low = 0;
        var high = bars.length - 1;
        var first = 0;
        while (low <= high) {
            var middle = (low + high) >> 1;
            if (bars[middle].time <= start) {
                first = middle;
                low = middle + 1;
            } else {
                high = middle - 1;
            }
        }
        var out = [];
        var sumV = 0;
        var sumPV = 0;
        for (var i = first; i < bars.length; i++) {
            var bar = bars[i];
            var v = volume[bar.time];
            if (!(v > 0)) { continue; }
            sumV += v;
            sumPV += (bar.high + bar.low + bar.close) / 3 * v;
            out.push({time: bar.time, value: sumPV / sumV});
        }
        return out;
    }

    window.btcVwap = {anchorOf: anchorOf, periodStart: periodStart,
                      compute: compute, fromAnchor: fromAnchor,
                      label: function (anchor) {
                          return LABELS[anchor] || "";
                      }};
}());
