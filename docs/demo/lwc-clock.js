// L'heure des graphiques Lightweight Charts : locale par défaut, UTC sur
// demande — la case UTC de la barre de titre du panneau prix.
//
// La bibliothèque lit tout temps comme de l'UTC et n'a aucune notion de
// fuseau : sa documentation conseille de décaler les temps qu'on lui
// donne. C'est ce que fait une horloge, à l'affichage seulement. Les
// données gardent leur temps réel, en secondes epoch UTC — le paquet du
// serveur, les lignes enregistrées, les plages demandées au profil et à la
// carte des liquidations — ; seuls les temps posés dans les séries sont
// décalés, et ceux que le graphique rend (bougie sous un tracé, plage
// visible) sont recalés par `actual`.
//
// Le décalage est celui du fuseau du navigateur, instant par instant : une
// bougie d'août garde son heure d'été en décembre. Au passage à l'heure
// d'hiver, l'heure locale se répète, et deux bougies d'une heure ou moins
// tomberaient sur le même temps affiché — ce que la bibliothèque refuse.
// L'horloge garde l'ordre strict : une bougie qui ne dépasserait pas la
// précédente est posée une seconde après elle. Une heure par an, dans les
// fuseaux qui changent d'heure, les bougies de l'heure répétée se tassent
// donc sous une même étiquette ; ailleurs — le Cambodge, l'UTC — rien ne
// bouge.
//
// L'horloge dit aussi le temps qui reste avant la clôture de la bougie en
// cours (`countdown`), posé par lwc-price.js à côté du dernier prix : la
// clôture d'une bougie de quatre heures ou d'un jour est un rendez-vous.
(function () {
    "use strict";

    // Le décalage du fuseau du navigateur à l'instant `time`, en secondes.
    function offsetAt(time) {
        return -new Date(time * 1000).getTimezoneOffset() * 60;
    }

    // `shift` faux : l'horloge rend les temps tels quels — l'UTC, ou une
    // échelle qui n'a pas d'heure (bougies d'un jour et plus).
    function Clock(shift) {
        this.shift = !!shift;
        this.toShown = new Map();
        this.toActual = new Map();
        this.lastActual = -Infinity;
        this.lastShown = -Infinity;
    }

    // Inscrit un temps réel à la suite des précédents ; rend son temps
    // affiché. Un temps déjà connu rend le sien ; un temps plus ancien que
    // le dernier inscrit n'est pas inscrit — il se décale sans garantie
    // d'ordre, comme `shown` le ferait.
    Clock.prototype.add = function (time) {
        var known = this.toShown.get(time);
        if (known !== undefined) { return known; }
        if (!this.shift) { return time; }
        if (time < this.lastActual) { return time + offsetAt(time); }
        var shown = Math.max(time + offsetAt(time), this.lastShown + 1);
        this.toShown.set(time, shown);
        this.toActual.set(shown, time);
        this.lastActual = time;
        this.lastShown = shown;
        return shown;
    };

    // Inscrit, dans l'ordre, tous les temps d'une ou plusieurs listes de
    // points — triés et dédoublonnés d'abord : plusieurs séries peuvent
    // partager un axe sans partager leurs instants.
    Clock.prototype.addAll = function () {
        var times = [];
        for (var i = 0; i < arguments.length; i += 1) {
            (arguments[i] || []).forEach(function (p) { times.push(p.time); });
        }
        times.sort(function (a, b) { return a - b; });
        var self = this;
        times.forEach(function (t) { self.add(t); });
        return this;
    };

    // Le temps affiché d'un temps réel.
    Clock.prototype.shown = function (time) {
        if (!this.shift) { return time; }
        var known = this.toShown.get(time);
        return known !== undefined ? known : time + offsetAt(time);
    };

    // Le temps réel d'un temps affiché — celui d'une bougie sous la souris,
    // ou d'un bord de la plage visible.
    Clock.prototype.actual = function (time) {
        if (!this.shift) { return time; }
        var known = this.toActual.get(time);
        return known !== undefined ? known : time - offsetAt(time - offsetAt(time));
    };

    // Une liste de points, temps décalés ; la même liste sans décalage.
    Clock.prototype.points = function (points) {
        if (!this.shift) { return points; }
        var self = this;
        return points.map(function (p) {
            var copy = {};
            for (var key in p) { copy[key] = p[key]; }
            copy.time = self.shown(p.time);
            return copy;
        });
    };

    // Le fuseau du navigateur en clair, « UTC+7 » ou « UTC−4:30 », à
    // l'instant donné (maintenant par défaut).
    function zone(time) {
        var offset = offsetAt(time === undefined ? Date.now() / 1000 : time);
        var minutes = Math.abs(offset) / 60;
        var text = "UTC" + (offset < 0 ? "−" : "+") + Math.floor(minutes / 60);
        if (minutes % 60) {
            text += ":" + String(minutes % 60).padStart(2, "0");
        }
        return offset === 0 ? "UTC" : text;
    }

    // La durée d'une bougie, en secondes ; le mois n'en a pas de fixe.
    var STEPS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
                 "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400,
                 "1w": 604800};

    // La clôture de la bougie ouverte à `open` (secondes UTC) : une durée
    // plus tard — la semaine de Binance, ouverte le lundi 0 h UTC, se clôt
    // le lundi suivant —, le 1er du mois suivant pour le mois.
    function closeOf(open, interval) {
        if (interval === "1M") {
            var date = new Date(open * 1000);
            return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1)
                / 1000;
        }
        return STEPS[interval] ? open + STEPS[interval] : null;
    }

    function two(n) { return String(n).padStart(2, "0"); }

    // Le temps qui reste avant la clôture de la bougie ouverte à `open`,
    // tel qu'il s'affiche à côté du dernier prix : « 04:59 » sous l'heure,
    // « 13:22:10 » sous le jour, « 3j 04h » au-delà. Vide hors de la
    // bougie — close, la suivante pas encore arrivée, ou l'horloge du
    // navigateur qui dérive : rien plutôt qu'un chiffre faux.
    function countdown(open, interval, now) {
        var close = closeOf(open, interval);
        if (close === null) { return ""; }
        var left = Math.floor(close - now);
        if (left < 0 || left > close - open) { return ""; }
        var hours = Math.floor(left / 3600);
        var minutes = Math.floor(left % 3600 / 60);
        var seconds = left % 60;
        if (left < 3600) { return two(minutes) + ":" + two(seconds); }
        if (left < 86400) {
            return hours + ":" + two(minutes) + ":" + two(seconds);
        }
        return Math.floor(left / 86400) + "j " + two(hours % 24) + "h";
    }

    window.btcClock = {
        create: function (shift) { return new Clock(shift); },
        offsetAt: offsetAt,
        zone: zone,
        closeOf: closeOf,
        countdown: countdown
    };
}());
