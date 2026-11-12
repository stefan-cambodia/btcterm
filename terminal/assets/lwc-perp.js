// Rendu Lightweight Charts du panneau perpétuel — le pendant navigateur
// de /api/perp (terminal/lwc.py).
//
// Le serveur ne sert que des données : financement en % par période de
// 8 h, open interest en dollars — la série Binance prolongée vers le
// passé par les instantanés journalisés. Tout le dessin vit ici :
// histogramme signé pour le financement (les longs paient en vert, les
// shorts en rouge), ligne d'open interest sur son axe gauche en
// milliards, crosshair commun. Dessous, un pane du positionnement : la
// part des longs de la foule (tous les comptes) et celle de l'argent des
// gros (les positions des 20 % de comptes à la plus forte marge), autour
// de 50 % — quand la foule charge un côté que les gros allègent, c'est
// elle, d'ordinaire, qui paie.
//
// Le point d'entrée est window.lwcPerp.configure(cfg, conf), appelé par
// le callback clientside de panels/perp.py au montage et à chaque
// bascule de plein écran ; poll() suit l'horloge rare (5 min) — ces
// données bougent par tranches de 4 à 8 heures, aucun canal push n'est
// justifié.
//
// L'heure suit la case UTC du panneau prix (Store `clock`) : locale par
// défaut, par l'horloge de lwc-clock.js, qui décale les temps posés dans
// les séries sans toucher aux données.
(function () {
    "use strict";

    var state = {
        el: null,       // le div #perp-lwc où vit le graphique
        chart: null,
        funding: null,  // série histogramme
        oi: null,       // série ligne
        crowd: null,    // part des longs, tous comptes — pane 1
        top: null,      // part des longs, argent des gros — pane 1
        note: null,     // « marché à terme indisponible »
        fitted: false,  // fitContent déjà fait — le zoom d'analyse survit
        seq: 0
    };

    function euros() {
        return !!state.cfg && state.cfg.currency === "EUR";
    }

    function buildChart() {
        var theme = state.conf.theme;
        var LWC = LightweightCharts;

        state.chart = LWC.createChart(state.el, {
            autoSize: true,
            layout: {
                background: {type: "solid", color: theme.panel},
                textColor: theme.muted,
                fontFamily: state.conf.mono,
                fontSize: 10
            },
            grid: {
                vertLines: {color: theme.grid},
                horzLines: {color: theme.grid}
            },
            crosshair: {
                mode: LWC.CrosshairMode.Magnet,
                vertLine: {color: theme.muted, labelBackgroundColor: theme.card},
                horzLine: {color: theme.muted, labelBackgroundColor: theme.card}
            },
            timeScale: {
                borderColor: theme.border,
                timeVisible: true,
                secondsVisible: false
            },
            rightPriceScale: {borderColor: theme.border},
            leftPriceScale: {visible: true, borderColor: theme.border},
            localization: {locale: "fr-FR"}
        });

        // Financement : histogramme signé sur l'axe droit, quatre
        // décimales — l'ordre de grandeur d'un taux par 8 h.
        state.funding = state.chart.addSeries(LWC.HistogramSeries, {
            priceScaleId: "right",
            priceFormat: {type: "custom", minMove: 0.0001,
                          formatter: function (v) {
                              return v.toFixed(4) + " %";
                          }},
            priceLineVisible: false, lastValueVisible: true
        });

        // Open interest : ligne sur l'axe gauche, lisible en milliards —
        // une affaire de format d'axe. Les données arrivent en dollars, et
        // la devise du panneau prix les convertit au dernier moment.
        state.oi = state.chart.addSeries(LWC.LineSeries, {
            priceScaleId: "left",
            color: theme.cyan, lineWidth: 2,
            priceFormat: {type: "custom", minMove: 1e7,
                          formatter: function (v) {
                              return (v / 1e9).toFixed(2)
                                  + (euros() ? " Md€" : " Md$");
                          }},
            priceLineVisible: false, lastValueVisible: true,
            crosshairMarkerVisible: true
        });

        // Le positionnement, en %, dans son pane : chaque courbe nommée sur
        // son étiquette, 50 % en pointillé — l'équilibre.
        function percent(color, width, title) {
            return state.chart.addSeries(LWC.LineSeries, {
                color: color, lineWidth: width, title: title,
                priceFormat: {type: "custom", minMove: 0.1,
                              formatter: function (v) {
                                  return v.toFixed(0) + " %";
                              }},
                priceLineVisible: false, lastValueVisible: true,
                crosshairMarkerVisible: true
            }, 1);
        }
        state.crowd = percent(theme.orange, 1, "foule");
        state.top = percent(theme.text, 2, "gros");
        state.top.createPriceLine({price: 50, color: theme.border,
                                   lineWidth: 1,
                                   lineStyle: LWC.LineStyle.Dashed,
                                   axisLabelVisible: false});
        var panes = state.chart.panes();
        panes[0].setStretchFactor(300);
        if (panes[1]) { panes[1].setStretchFactor(110); }

        var note = document.createElement("div");
        note.className = "lwc-perp-note";
        note.textContent = "marché à terme indisponible";
        note.style.position = "absolute";
        note.style.inset = "0";
        note.style.display = "none";
        note.style.alignItems = "center";
        note.style.justifyContent = "center";
        note.style.color = theme.muted;
        note.style.fontFamily = state.conf.mono;
        note.style.fontSize = "11px";
        note.style.pointerEvents = "none";
        state.el.appendChild(note);
        state.note = note;
    }

    function teardown() {
        if (state.chart) { state.chart.remove(); }
        state.chart = null;
        state.funding = null;
        state.oi = null;
        state.crowd = null;
        state.top = null;
        state.note = null;
        state.fitted = false;
        if (state.el) { state.el.textContent = ""; }
    }

    // `fit` recadre la fenêtre sur la série : vrai au premier paquet
    // seulement — un poll de l'horloge rare ne doit pas voler le zoom.
    function refetch(fit) {
        var seq = ++state.seq;
        fetch("/api/perp")
            .then(function (r) { return r.json(); })
            .then(function (packet) {
                if (seq !== state.seq || !state.chart) { return; }
                var theme = state.conf.theme;
                // Une horloge pour les deux séries : elles partagent l'axe,
                // l'ordre strict des temps affichés vaut pour leur union.
                var crowd = packet.crowd || [];
                var top = packet.top || [];
                var at = window.btcClock && !(state.cfg && state.cfg.utc)
                    ? window.btcClock.create(true)
                        .addAll(packet.funding, packet.oi, crowd, top)
                    : null;
                function shown(t) { return at ? at.shown(t) : t; }
                state.funding.setData(packet.funding.map(function (p) {
                    // Un point sans valeur est un point blanc : la
                    // séance était interrompue, la série s'y rompt.
                    if (p.value === undefined) { return {time: shown(p.time)}; }
                    return {time: shown(p.time), value: p.value,
                            color: p.value >= 0 ? theme.green : theme.red};
                }));
                var rate = euros() ? packet.eur_rate : 1;
                state.oi.setData(packet.oi.map(function (p) {
                    return {time: shown(p.time), value: p.value * rate};
                }));
                function percents(points) {
                    return points.map(function (p) {
                        return p.value === undefined ? {time: shown(p.time)}
                            : {time: shown(p.time), value: p.value};
                    });
                }
                state.crowd.setData(percents(crowd));
                state.top.setData(percents(top));
                var vide = !packet.funding.length && !packet.oi.length;
                state.note.style.display = vide ? "flex" : "none";
                if (fit && !vide && !state.fitted) {
                    state.fitted = true;
                    state.chart.timeScale().fitContent();
                }
            })
            .catch(function () { /* l'horloge rare retentera */ });
    }

    window.lwcPerp = {
        configure: function (cfg, conf) {
            var el = document.getElementById("perp-lwc");
            if (!el || !window.LightweightCharts || !conf) { return; }

            var previous = state.cfg;

            // Un div neuf — premier rendu, retour d'onglet, panneau
            // re-rendu par un déménagement — repart de zéro.
            if (el !== state.el || !state.chart) {
                teardown();
                state.el = el;
                state.cfg = cfg;
                state.conf = conf;
                buildChart();
                refetch(true);
                return;
            }
            state.cfg = cfg;
            state.conf = conf;

            if (previous && (previous.currency !== cfg.currency
                             || !previous.utc !== !cfg.utc)) {
                refetch(false);
            }

            // Le plein écran double la largeur ; autoSize a besoin d'un
            // tour de boucle pour mesurer le div agrandi.
            if (previous && previous.maximized !== cfg.maximized) {
                setTimeout(function () {
                    if (state.chart) { state.chart.timeScale().fitContent(); }
                }, 120);
            }
        },

        poll: function () {
            if (!state.chart) { return; }
            refetch(false);
        },

        // Sonde du smoke test : ce que les séries portent vraiment.
        debug: function () {
            if (!state.chart) { return null; }
            function last(series) {
                var data = series.data();
                for (var i = data.length - 1; i >= 0; i--) {
                    if (data[i].value !== undefined) { return data[i].value; }
                }
                return null;
            }
            return {panes: state.chart.panes().length,
                    funding: state.funding.data().length,
                    oi: state.oi.data().length,
                    crowd: state.crowd.data().length,
                    top: state.top.data().length,
                    crowdLast: last(state.crowd), topLast: last(state.top)};
        }
    };
})();
