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
// C'est le seul rendu du panneau prix depuis la bascule de la voie A :
// l'ancienne figure Plotly recalculée côté serveur a été déposée.
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
        loading: false,    // une page d'historique en vol, une seule
        exhausted: false,  // plus rien avant : la source l'a dit
        banner: null,
        legend: null
    };

    var INTRADAY = {"15m": 1, "30m": 1, "1h": 1, "4h": 1, "6h": 1, "12h": 1};

    //: Historique chargé à la volée : dès que le bord gauche visible
    //: approche à moins de ce nombre de bougies du début du tampon, la
    //: page antérieure est demandée.
    var BACKFILL_MARGIN = 50;

    //: Plafond du tampon par intervalle : au-delà, on cesse de remonter
    //: le passé — la mémoire reste bornée, et 5 000 bougies font déjà
    //: sept mois d'horaire ou treize ans de quotidien.
    var MAX_BARS = 5000;

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

        // Signaux BUY/SELL : des flèches sur les chandeliers, taille
        // selon la force du signal — le pendant natif des marqueurs
        // Plotly.
        series.markers = LWC.createSeriesMarkers(series.candles, []);

        state.series = series;
        buildLegend(styles);
        buildBanner();
        updateAlertLines();

        // Profil de volume : un canvas en surimpression à droite du
        // pane du cours, recalculé sur la plage visible (le VPVR de
        // TradingView) — la case PROFIL de la barre de titre le tient.
        if (extras.indexOf("profile") !== -1) {
            buildProfile();
        }

        // L'historique se charge au pan, comme sur tradingview.com :
        // approcher du début des données déclenche la page antérieure.
        state.chart.timeScale()
            .subscribeVisibleLogicalRangeChange(maybeBackfill);
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

    // ── Profil de volume ────────────────────────────────────

    function buildProfile() {
        var canvas = document.createElement("canvas");
        canvas.className = "lwc-profile";
        state.el.appendChild(canvas);
        state.profileCanvas = canvas;
        state.profileData = null;
        state.profileTimer = null;

        // La plage visible bouge : redessiner tout de suite avec le
        // profil en main (le pan vertical déplace les prix à l'écran),
        // et redemander le profil de la nouvelle plage après une pause
        // — pas une requête par frame de pan.
        state.chart.timeScale().subscribeVisibleTimeRangeChange(
            function () {
                drawProfile();
                scheduleProfile();
            });
    }

    function scheduleProfile() {
        if (!state.profileCanvas) { return; }
        if (state.profileTimer) { clearTimeout(state.profileTimer); }
        state.profileTimer = setTimeout(fetchProfile, 300);
    }

    function fetchProfile() {
        if (!state.chart || !state.packet || !state.profileCanvas) {
            return;
        }
        var range = state.chart.timeScale().getVisibleRange();
        if (!range) { return; }
        var interval = state.cfg.interval;
        var seq = state.seq;
        fetch("/api/profile?interval=" + encodeURIComponent(interval)
              + "&from=" + Math.floor(range.from)
              + "&to=" + Math.ceil(range.to))
            .then(function (r) { return r.json(); })
            .then(function (profile) {
                if (seq !== state.seq || !state.chart
                        || !state.profileCanvas
                        || profile.interval !== state.cfg.interval) {
                    return;
                }
                state.profileData = profile.empty ? null : profile;
                updateProfileLines();
                drawProfile();
            })
            .catch(function () { /* le prochain mouvement retentera */ });
    }

    // POC et Value Area : des lignes de prix sur les chandeliers,
    // étiquetées sur l'axe — elles suivent le profil visible.
    function updateProfileLines() {
        var series = state.series;
        if (!series) { return; }
        (state.profileLines || []).forEach(function (line) {
            series.candles.removePriceLine(line);
        });
        state.profileLines = [];
        var profile = state.profileData;
        if (!profile) { return; }
        var theme = state.conf.theme;
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        var LS = LightweightCharts.LineStyle;
        [[profile.poc, "POC", 2, LS.Dashed],
         [profile.va_high, "VAH", 1, LS.Dotted],
         [profile.va_low, "VAL", 1, LS.Dotted]]
            .forEach(function (spec) {
                state.profileLines.push(series.candles.createPriceLine({
                    price: spec[0] * rate,
                    color: theme.poc,
                    lineWidth: spec[2],
                    lineStyle: spec[3],
                    axisLabelVisible: true,
                    title: spec[1]
                }));
            });
    }

    function drawProfile() {
        var canvas = state.profileCanvas;
        if (!canvas || !state.chart || !state.series) { return; }
        var profile = state.profileData;
        var paneHeight = state.chart.panes()[0].getHeight();
        var scaleWidth = state.chart.priceScale("right").width();
        var paneWidth = state.el.clientWidth - scaleWidth;
        canvas.width = paneWidth;
        canvas.height = paneHeight;
        var ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, paneWidth, paneHeight);
        if (!profile) { return; }

        var theme = state.conf.theme;
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        var maxVol = Math.max.apply(null, profile.volumes);
        if (!(maxVol > 0)) { return; }
        var maxLen = Math.round(paneWidth * 0.16);
        var barHeight = Math.max(
            2, Math.floor(paneHeight / profile.centers.length) - 1);

        for (var i = 0; i < profile.centers.length; i += 1) {
            var center = profile.centers[i];
            var y = state.series.candles.priceToCoordinate(center * rate);
            if (y === null || y < 0 || y > paneHeight) { continue; }
            var len = Math.max(1,
                Math.round(profile.volumes[i] / maxVol * maxLen));
            ctx.fillStyle =
                Math.abs(center - profile.poc) < profile.poc * 0.004
                    ? theme.poc
                    : (profile.va_low <= center && center <= profile.va_high)
                        ? "rgba(0,212,170,0.45)"
                        : "rgba(100,116,139,0.35)";
            ctx.fillRect(paneWidth - len, y - barHeight / 2, len, barHeight);
        }
    }

    // ── Seuils d'alerte ─────────────────────────────────────

    // Les seuils de cours posés depuis le panneau ALERTES, tracés sur
    // le graphique — cyan tireté long, distinct du POC jaune. Les
    // niveaux sont en dollars ; la conversion suit la devise affichée.
    function updateAlertLines() {
        var series = state.series;
        if (!series) { return; }
        (state.alertLines || []).forEach(function (line) {
            series.candles.removePriceLine(line);
        });
        state.alertLines = [];
        var theme = state.conf.theme;
        var rate = (state.cfg.currency === "EUR" && state.packet)
            ? state.packet.eur_rate : 1;
        var LS = LightweightCharts.LineStyle;
        (state.alertLevels || []).forEach(function (item) {
            state.alertLines.push(series.candles.createPriceLine({
                price: item.level * rate,
                color: theme.cyan,
                lineWidth: 1,
                lineStyle: LS.LargeDashed,
                axisLabelVisible: true,
                title: "⚠ " + (item.dir === "above" ? "≥" : "≤")
            }));
        });
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
        state.loading = false;
        state.exhausted = false;
        state.profileCanvas = null;
        state.profileData = null;
        state.profileLines = [];
        if (state.profileTimer) {
            clearTimeout(state.profileTimer);
            state.profileTimer = null;
        }
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
        series.markers.setMarkers(markersFrom(packet.signals || []));

        state.banner.style.display = packet.demo ? "block" : "none";
        applyLog();

        if (state.profileCanvas) {
            updateProfileLines();
            drawProfile();
            scheduleProfile();
        }
        // Les lignes portent des prix en unités de série : la bascule
        // de devise les reprend au taux.
        updateAlertLines();
    }

    // Traduit les signaux gradués du serveur (-2 à +2) en flèches :
    // achat sous la bougie, vente au-dessus, grande taille pour les
    // signaux forts — la grammaire du panneau Plotly.
    function markersFrom(signals) {
        var theme = state.conf.theme;
        return signals.map(function (s) {
            return {
                time: s.time,
                position: s.value > 0 ? "belowBar" : "aboveBar",
                shape: s.value > 0 ? "arrowUp" : "arrowDown",
                color: s.value === 2 ? theme.buy
                    : s.value === 1 ? theme.green
                        : s.value === -1 ? theme.red : theme.sell,
                size: Math.abs(s.value) === 2 ? 2 : 1
            };
        });
    }

    function applyLog() {
        state.series.candles.priceScale().applyOptions({
            mode: state.cfg.log
                ? LightweightCharts.PriceScaleMode.Logarithmic
                : LightweightCharts.PriceScaleMode.Normal
        });
    }

    // `fit` recadre la fenêtre sur la série : vrai au premier chargement
    // et au changement d'intervalle, jamais pour un recalage silencieux
    // (reconnexion, retour d'onglet, repli poll) — le zoom de l'analyse
    // en cours doit y survivre.
    function refetch(fit) {
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
                state.loading = false;
                state.exhausted = false;
                fillSeries();
                if (fit) { state.chart.timeScale().fitContent(); }
            })
            .catch(function () { /* le prochain réglage retentera */ });
    }

    // Le bord gauche visible approche du début du tampon : demander la
    // page antérieure. Verrou anti-rafale (une requête en vol à la
    // fois) et mémoire du « plus ancien atteint » — une page vide dit
    // que la source n'a plus rien avant, inutile de la marteler.
    function maybeBackfill(range) {
        if (!state.chart || !state.packet || state.loading
                || state.exhausted || !range
                || range.from > BACKFILL_MARGIN) {
            return;
        }
        var bars = state.packet.bars;
        if (!bars.length) { return; }
        if (bars.length >= MAX_BARS) {
            state.exhausted = true;
            return;
        }
        var interval = state.cfg.interval;
        var limit = (state.conf.intervals || {})[interval] || 365;
        var seq = state.seq;
        state.loading = true;
        fetch("/api/klines?interval=" + encodeURIComponent(interval)
              + "&limit=" + limit + "&before=" + bars[0].time)
            .then(function (r) { return r.json(); })
            .then(function (page) {
                state.loading = false;
                // Un refetch est passé entre temps : le tampon a changé
                // de série, cette page ne le concerne plus.
                if (seq !== state.seq || !state.chart) { return; }
                if (!page.bars.length) {
                    state.exhausted = true;
                    return;
                }
                prepend(page);
                // setData du concaténé : la bibliothèque préserve la
                // fenêtre visible, le pan continue sans à-coup.
                fillSeries();
            })
            .catch(function () { state.loading = false; });
    }

    function prepend(page) {
        var packet = state.packet;
        var name;
        packet.bars = page.bars.concat(packet.bars);
        packet.volume = page.volume.concat(packet.volume);
        for (name in page.overlays) {
            packet.overlays[name] = (page.overlays[name] || [])
                .concat(packet.overlays[name] || []);
        }
        for (name in page.panes) {
            packet.panes[name] = (page.panes[name] || [])
                .concat(packet.panes[name] || []);
        }
        packet.volume_ma = (page.volume_ma || []).concat(packet.volume_ma);
    }

    // Ajoute ou remplace le dernier point d'une série du paquet local :
    // le paquet reste le miroir USD de ce que les séries affichent,
    // c'est lui qui rejoue la bascule $/€ sans réseau.
    function mergeTail(points, point) {
        if (!point) { return; }
        var last = points.length ? points[points.length - 1] : null;
        if (last && last.time === point.time) {
            points[points.length - 1] = point;
        } else if (!last || point.time > last.time) {
            points.push(point);
        }
    }

    // ── Point d'entrée ──────────────────────────────────────

    function sameExtras(a, b) {
        return a.slice().sort().join(",") === b.slice().sort().join(",");
    }

    window.lwcPrice = {
        configure: function (cfg, conf) {
            var el = document.getElementById("price-lwc");
            if (!el || !window.LightweightCharts || !conf) { return; }

            // Un localStorage d'avant un renommage peut restaurer un
            // intervalle que le serveur ne connaît plus : retomber sur
            // le quotidien plutôt que d'échouer en silence.
            if (!(conf.intervals || {})[cfg.interval]) {
                cfg.interval = "1d";
            }

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

            // Le pousseur suit l'intervalle affiché : il n'envoie la
            // mutation du panneau prix qu'aux navigateurs qui
            // l'annoncent (contrat de terminal/push.py).
            if (window.btcPush) {
                window.btcPush.state({price_interval: cfg.interval});
            }

            if (!state.packet || !previous
                    || previous.interval !== cfg.interval) {
                if (previous && previous.interval !== cfg.interval) {
                    state.chart.timeScale().applyOptions({
                        timeVisible: !!INTRADAY[cfg.interval]
                    });
                }
                refetch(true);
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

        // La mutation du canal push : une bougie, les derniers points
        // d'indicateurs — series.update, zéro re-rendu. Une bougie qui
        // clôture arrive comme un update à time nouveau : la
        // bibliothèque l'ajoute d'elle-même.
        push: function (update) {
            if (!state.chart || !state.packet || !update) { return; }
            // Dépassé — l'utilisateur vient de changer d'échelle, la
            // trame suivante suivra le nouvel intervalle.
            if (update.interval !== state.cfg.interval) { return; }
            var packet = state.packet;
            var bars = packet.bars;
            var lastTime = bars.length ? bars[bars.length - 1].time : 0;
            // Plus vieux que la série : un reliquat d'avant recalage.
            if (update.bar.time < lastTime) { return; }
            // Un ajout n'est accepté qu'au pas de la série : la bougie
            // qui clôture arrive exactement un intervalle plus loin.
            // Tout autre décalage — la série de démonstration hors
            // ligne, réancrée sur l'horloge à chaque régénération — ne
            // doit pas semer de bougies parasites.
            var spacing = bars.length > 1
                ? bars[bars.length - 1].time - bars[bars.length - 2].time
                : 0;
            var delta = update.bar.time - lastTime;
            if (delta > 0 && spacing > 0 && delta < spacing * 0.9) {
                return;
            }
            // Plus d'un intervalle d'écart : un trou (onglet longtemps
            // gelé) — l'appliquer laisserait une brèche dans la série,
            // le recalage du retour d'onglet fera propre.
            if (delta > 0 && spacing > 0 && delta > spacing * 1.5) {
                return;
            }

            mergeTail(bars, update.bar);
            mergeTail(packet.volume, update.volume);
            var name;
            for (name in update.overlays) {
                mergeTail(packet.overlays[name]
                          || (packet.overlays[name] = []),
                          update.overlays[name]);
            }
            for (name in update.panes) {
                mergeTail(packet.panes[name] || (packet.panes[name] = []),
                          update.panes[name]);
            }
            mergeTail(packet.volume_ma, update.volume_ma);
            packet.demo = update.demo;

            var series = state.series;
            var theme = state.conf.theme;
            var rate = state.cfg.currency === "EUR" ? packet.eur_rate : 1;
            series.candles.update(scaled([update.bar], rate)[0]);
            for (name in update.overlays) {
                if (series.lines[name]) {
                    series.lines[name].update(
                        scaled([update.overlays[name]], rate)[0]);
                }
            }
            if (series.volume && update.volume) {
                series.volume.update({
                    time: update.volume.time, value: update.volume.value,
                    color: update.volume.up ? theme.green : theme.red
                });
                if (update.volume_ma) {
                    series.volMa.update(update.volume_ma);
                }
            }
            if (series.rsi && update.panes.rsi) {
                series.rsi.update(update.panes.rsi);
            }
            if (series.crsi && update.panes.crsi) {
                series.crsi.update(update.panes.crsi);
            }

            // Le signal de la bougie courante peut naître, changer de
            // grade ou s'éteindre tant qu'elle vit : la liste locale
            // suit, les flèches aussi.
            var signals = packet.signals || (packet.signals = []);
            var lastSignal = signals.length
                ? signals[signals.length - 1] : null;
            if (lastSignal && lastSignal.time === update.bar.time) {
                if (update.signal) { lastSignal.value = update.signal; }
                else { signals.pop(); }
                series.markers.setMarkers(markersFrom(signals));
            } else if (update.signal) {
                signals.push({time: update.bar.time, value: update.signal});
                series.markers.setMarkers(markersFrom(signals));
            }

            state.banner.style.display = update.demo ? "block" : "none";
            // Une bougie qui clôture déplace le volume : le profil de
            // la plage visible suit, au débit du debounce.
            if (delta > 0 && state.profileCanvas) { scheduleProfile(); }
        },

        // Recalage : reconnexion du canal, retour d'onglet — les
        // mutations perdues ne reviendront pas, une page fraîche comble
        // le trou sans toucher au zoom.
        resync: function () {
            if (state.chart && state.packet) { refetch(false); }
        },

        // Les seuils de cours du panneau ALERTES, relayés par le Store
        // alert-config (panels/price.py) : retenus même sans graphique
        // — ils seront tracés au prochain montage.
        alerts: function (levels) {
            state.alertLevels = (levels || []).filter(function (item) {
                return item && typeof item.level === "number"
                    && item.level > 0;
            });
            updateAlertLines();
        },

        // Repli poll : appelé par tick-slow (panels/price.py) ; ne fait
        // rien tant que le canal push tient.
        poll: function () {
            if (!state.chart || !state.packet) { return; }
            if (window.btcPush && window.btcPush.connected()) { return; }
            refetch(false);
        },

        // Sonde du smoke test (tests/ui_smoke.py --lwc) : l'état
        // *effectif* du graphique — données posées dans les séries,
        // mode réel de l'échelle — pas la configuration demandée.
        debug: function () {
            if (!state.chart || !state.packet) { return null; }
            var n = state.packet.bars.length;
            // Par .data(), pas dataByIndex(n-1) : après un chargement
            // d'historique, les bougies préfixées prennent des indices
            // logiques négatifs — la dernière ne vit plus à n-1.
            var data = state.series.candles.data();
            var last = data.length ? data[data.length - 1] : null;
            return {
                range: state.chart.timeScale().getVisibleLogicalRange(),
                interval: state.cfg.interval,
                bars: n,
                demo: !!state.packet.demo,
                eur_rate: state.packet.eur_rate,
                log: state.series.candles.priceScale().options().mode
                    === LightweightCharts.PriceScaleMode.Logarithmic,
                lastClose: last ? last.close : null,
                firstTime: n ? state.packet.bars[0].time : null,
                exhausted: state.exhausted,
                panes: state.chart.panes().length,
                signals: (state.packet.signals || []).length,
                alerts: (state.alertLevels || []).length,
                profile: state.profileData
                    ? {poc: state.profileData.poc,
                       vaLow: state.profileData.va_low,
                       vaHigh: state.profileData.va_high,
                       bins: state.profileData.centers.length}
                    : null
            };
        },

        // Second point d'entrée du smoke : un pan programmatique — le
        // même chemin que la souris, setVisibleLogicalRange déclenche
        // subscribeVisibleLogicalRangeChange, donc le backfill.
        pan: function (from, to) {
            if (state.chart) {
                state.chart.timeScale().setVisibleLogicalRange(
                    {from: from, to: to});
            }
        }
    };

    // Retour d'onglet : le navigateur a pu geler les timers et le
    // WebSocket pendant l'absence — recalage silencieux.
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden && state.chart && state.packet) {
            refetch(false);
        }
    });
}());
