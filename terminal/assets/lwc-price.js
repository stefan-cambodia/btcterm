// Rendu Lightweight Charts du panneau prix — le pendant navigateur de
// terminal/lwc.py.
//
// Le serveur ne sert que des données (/api/klines) ; tout le dessin vit
// ici : chandeliers, moyennes, Bollinger, volume, RSI et CRSI en panes,
// crosshair aimanté et ligne du dernier prix — les comportements natifs
// de la bibliothèque, vendorée dans vendor/ (v5.2.1).
//
// Le point d'entrée est window.lwcPrice.configure(cfg, conf), appelé par
// le callback clientside de panels/price.py à chaque changement de
// réglage. La règle : un refetch seulement quand l'intervalle change ;
// la devise et l'échelle log se règlent sur place — le paquet est gardé
// en USD, le taux € voyage avec lui.
//
// Actif seulement sous BTCTERM_LWC=1 : sans le drapeau, le div
// #price-lwc n'existe pas et ce fichier ne fait rien.
(function () {
    "use strict";

    var state = {
        el: null,      // le div #price-lwc où vit le graphique
        chart: null,
        series: null,  // {candles, lines: {nom: série}, volume, volMa, rsi, crsi}
        packet: null,  // dernier /api/klines — toujours en USD
        cfg: null,     // {interval, currency, log, extras, maximized}
        conf: null,    // {theme, mono, intervals} — la définition du serveur
        seq: 0,        // jeton anti-course des fetchs
        banner: null,
        legend: null
    };

    var INTRADAY = {"15m": 1, "30m": 1, "1h": 1, "4h": 1, "6h": 1, "12h": 1};

    // ── Construction ────────────────────────────────────────

    function overlayStyles(theme) {
        var LS = LightweightCharts.LineStyle;
        return {
            ma9:      {color: theme.ma9,  lineWidth: 2, lineStyle: LS.Solid,
                       title: "MA 9"},
            ma26:     {color: theme.ma26, lineWidth: 2, lineStyle: LS.Solid,
                       title: "MA 26"},
            ma200:    {color: theme.ma200, lineWidth: 2, lineStyle: LS.Dotted,
                       title: "MA 200"},
            bb_upper: {color: theme.bb, lineWidth: 1, lineStyle: LS.Dashed,
                       title: "BB"},
            bb_lower: {color: theme.bb, lineWidth: 1, lineStyle: LS.Dashed,
                       title: ""}
        };
    }

    // Oscillateurs bornés : l'axe reste 0-100 quel que soit le tracé.
    function fixedScale(min, max) {
        return function () {
            return {priceRange: {minValue: min, maxValue: max}};
        };
    }

    function buildChart() {
        var theme = state.conf.theme;
        var extras = state.cfg.extras;
        var LWC = LightweightCharts;

        state.chart = LWC.createChart(state.el, {
            autoSize: true,
            layout: {
                background: {type: "solid", color: theme.panel},
                textColor: theme.muted,
                fontFamily: state.conf.mono,
                fontSize: 10,
                panes: {separatorColor: theme.border,
                        separatorHoverColor: theme.border,
                        enableResize: true}
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
                timeVisible: !!INTRADAY[state.cfg.interval],
                secondsVisible: false
            },
            rightPriceScale: {borderColor: theme.border},
            localization: {locale: "fr-FR"}
        });

        var series = {lines: {}};

        // ── Pane 0 : chandeliers, moyennes, Bollinger, volume ──
        series.candles = state.chart.addSeries(LWC.CandlestickSeries, {
            upColor: theme.green, downColor: theme.red,
            borderUpColor: theme.green, borderDownColor: theme.red,
            wickUpColor: theme.green, wickDownColor: theme.red,
            priceFormat: {type: "price", precision: 0, minMove: 1},
            priceLineVisible: true, lastValueVisible: true
        }, 0);

        var styles = overlayStyles(theme);
        for (var name in styles) {
            series.lines[name] = state.chart.addSeries(LWC.LineSeries, {
                color: styles[name].color,
                lineWidth: styles[name].lineWidth,
                lineStyle: styles[name].lineStyle,
                priceLineVisible: false, lastValueVisible: false,
                crosshairMarkerVisible: false
            }, 0);
        }

        var hasVolume = extras.indexOf("volume") !== -1;
        if (hasVolume) {
            series.volume = state.chart.addSeries(LWC.HistogramSeries, {
                priceScaleId: "vol",
                priceFormat: {type: "volume"},
                priceLineVisible: false, lastValueVisible: false
            }, 0);
            series.volume.priceScale().applyOptions({
                scaleMargins: {top: 0.82, bottom: 0}
            });
            series.volMa = state.chart.addSeries(LWC.LineSeries, {
                priceScaleId: "vol",
                color: theme.orange, lineWidth: 1,
                priceLineVisible: false, lastValueVisible: false,
                crosshairMarkerVisible: false
            }, 0);
        }
        series.candles.priceScale().applyOptions({
            scaleMargins: {top: 0.04, bottom: hasVolume ? 0.22 : 0.06}
        });

        // ── Panes suivants : un par oscillateur demandé ────────
        var pane = 1;
        if (extras.indexOf("rsi") !== -1) {
            series.rsi = state.chart.addSeries(LWC.LineSeries, {
                color: theme.blue, lineWidth: 2,
                priceLineVisible: false, lastValueVisible: false,
                autoscaleInfoProvider: fixedScale(0, 100)
            }, pane);
            // Sans quoi les marges par défaut étirent l'axe au-delà
            // de 0-100 et l'échelle affiche des rangs négatifs.
            series.rsi.priceScale().applyOptions({
                scaleMargins: {top: 0, bottom: 0}
            });
            [[30, theme.green], [50, theme.muted], [70, theme.red]]
                .forEach(function (level) {
                    series.rsi.createPriceLine({
                        price: level[0], color: level[1], lineWidth: 1,
                        lineStyle: LWC.LineStyle.Dotted,
                        axisLabelVisible: false
                    });
                });
            pane += 1;
        }
        if (extras.indexOf("crsi") !== -1) {
            series.crsi = state.chart.addSeries(LWC.LineSeries, {
                color: theme.purple, lineWidth: 2,
                priceLineVisible: false, lastValueVisible: false,
                autoscaleInfoProvider: fixedScale(0, 100)
            }, pane);
            series.crsi.priceScale().applyOptions({
                scaleMargins: {top: 0, bottom: 0}
            });
            [20, 50, 80].forEach(function (level) {
                series.crsi.createPriceLine({
                    price: level, color: theme.muted, lineWidth: 1,
                    lineStyle: LWC.LineStyle.Dotted,
                    axisLabelVisible: false
                });
            });
            pane += 1;
        }

        // Le cours domine : les panes d'oscillateurs se partagent le bas.
        var panes = state.chart.panes();
        panes[0].setStretchFactor(300);
        for (var i = 1; i < panes.length; i += 1) {
            panes[i].setStretchFactor(70);
        }

        state.series = series;
        buildLegend(styles);
        buildBanner();
    }

    // Une légende statique : la bibliothèque n'en fournit pas, et sans
    // elle rien ne dit quelle moyenne est quelle couleur.
    function buildLegend(styles) {
        var div = document.createElement("div");
        div.className = "lwc-legend";
        var seen = {};
        for (var name in styles) {
            if (!styles[name].title || seen[styles[name].title]) { continue; }
            seen[styles[name].title] = true;
            var item = document.createElement("span");
            item.textContent = "— " + styles[name].title;
            item.style.color = styles[name].color;
            div.appendChild(item);
        }
        state.el.appendChild(div);
        state.legend = div;
    }

    function buildBanner() {
        var theme = state.conf.theme;
        var div = document.createElement("div");
        div.className = "lwc-demo-banner";
        div.textContent = "⚠ DONNÉES DE DÉMONSTRATION · source injoignable";
        div.style.background = theme.orange;
        div.style.color = theme.bg;
        div.style.display = "none";
        state.el.appendChild(div);
        state.banner = div;
    }

    function teardown() {
        if (state.chart) { state.chart.remove(); }
        state.chart = null;
        state.series = null;
        state.legend = null;
        state.banner = null;
        if (state.el) { state.el.textContent = ""; }
    }

    // ── Données ─────────────────────────────────────────────

    function scaled(points, rate) {
        if (rate === 1) { return points; }
        return points.map(function (p) {
            return p.value !== undefined
                ? {time: p.time, value: p.value * rate}
                : {time: p.time, open: p.open * rate, high: p.high * rate,
                   low: p.low * rate, close: p.close * rate};
        });
    }

    // Verse le paquet USD dans les séries, converti à la devise choisie.
    // C'est le seul endroit qui touche aux données : la bascule $/€
    // repasse ici avec le même paquet, jamais par le réseau.
    function fillSeries() {
        var packet = state.packet;
        var series = state.series;
        var theme = state.conf.theme;
        var rate = state.cfg.currency === "EUR" ? packet.eur_rate : 1;

        series.candles.setData(scaled(packet.bars, rate));
        for (var name in series.lines) {
            series.lines[name].setData(
                scaled(packet.overlays[name] || [], rate));
        }
        if (series.volume) {
            series.volume.setData(packet.volume.map(function (p) {
                return {time: p.time, value: p.value,
                        color: p.up ? theme.green : theme.red};
            }));
            series.volMa.setData(packet.volume_ma);
        }
        if (series.rsi) { series.rsi.setData(packet.panes.rsi || []); }
        if (series.crsi) { series.crsi.setData(packet.panes.crsi || []); }

        state.banner.style.display = packet.demo ? "block" : "none";
        applyLog();
    }

    function applyLog() {
        state.series.candles.priceScale().applyOptions({
            mode: state.cfg.log
                ? LightweightCharts.PriceScaleMode.Logarithmic
                : LightweightCharts.PriceScaleMode.Normal
        });
    }

    function refetch() {
        var interval = state.cfg.interval;
        var limit = (state.conf.intervals || {})[interval] || 365;
        var seq = ++state.seq;
        fetch("/api/klines?interval=" + encodeURIComponent(interval)
              + "&limit=" + limit)
            .then(function (r) { return r.json(); })
            .then(function (packet) {
                // Une réponse dépassée — l'intervalle a rechangé entre
                // temps — est simplement écartée.
                if (seq !== state.seq || !state.chart) { return; }
                state.packet = packet;
                fillSeries();
                state.chart.timeScale().fitContent();
            })
            .catch(function () { /* le prochain réglage retentera */ });
    }

    // ── Point d'entrée ──────────────────────────────────────

    function sameExtras(a, b) {
        return a.slice().sort().join(",") === b.slice().sort().join(",");
    }

    window.lwcPrice = {
        configure: function (cfg, conf) {
            var el = document.getElementById("price-lwc");
            if (!el || !window.LightweightCharts || !conf) { return; }

            var previous = state.cfg;
            var rebuilt = false;

            // Un div neuf — premier rendu ou panneau re-rendu par un
            // déménagement de cellule — repart de zéro.
            if (el !== state.el || !state.chart
                    || !previous
                    || !sameExtras(previous.extras, cfg.extras)) {
                teardown();
                state.el = el;
                state.cfg = cfg;
                state.conf = conf;
                buildChart();
                rebuilt = true;
            }
            state.cfg = cfg;
            state.conf = conf;

            if (!state.packet || !previous
                    || previous.interval !== cfg.interval) {
                if (previous && previous.interval !== cfg.interval) {
                    state.chart.timeScale().applyOptions({
                        timeVisible: !!INTRADAY[cfg.interval]
                    });
                }
                refetch();
                return;
            }
            if (rebuilt || previous.currency !== cfg.currency) {
                fillSeries();
                if (rebuilt) { state.chart.timeScale().fitContent(); }
                return;
            }
            if (previous.log !== cfg.log) {
                applyLog();
            }
            // Le plein écran double la largeur mais l'espacement des
            // barres survit au redimensionnement : sans recadrage, la
            // série resterait tassée contre le bord droit. autoSize a
            // besoin d'un tour de boucle pour mesurer le div agrandi —
            // d'où le différé.
            if (previous.maximized !== cfg.maximized) {
                setTimeout(function () {
                    if (state.chart) { state.chart.timeScale().fitContent(); }
                }, 120);
            }
        },

        // Sonde du smoke test (tests/ui_smoke.py --lwc) : l'état
        // *effectif* du graphique — données posées dans les séries,
        // mode réel de l'échelle — pas la configuration demandée.
        debug: function () {
            if (!state.chart || !state.packet) { return null; }
            var n = state.packet.bars.length;
            var last = n ? state.series.candles.dataByIndex(n - 1) : null;
            return {
                interval: state.cfg.interval,
                bars: n,
                demo: !!state.packet.demo,
                eur_rate: state.packet.eur_rate,
                log: state.series.candles.priceScale().options().mode
                    === LightweightCharts.PriceScaleMode.Logarithmic,
                lastClose: last ? last.close : null,
                panes: state.chart.panes().length
            };
        }
    };
}());
