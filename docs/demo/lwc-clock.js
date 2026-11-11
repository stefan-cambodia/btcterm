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

    window.btcClock = {
        create: function (shift) { return new Clock(shift); },
        offsetAt: offsetAt,
        zone: zone
    };
}());
