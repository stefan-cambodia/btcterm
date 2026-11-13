// Rendu Lightweight Charts du panneau prix — le pendant navigateur de
// terminal/lwc.py.
//
// Le serveur ne sert que des données (/api/klines) ; tout le dessin vit
// ici : chandeliers, moyennes, Bollinger, volume, RSI et CRSI en panes,
// crosshair aimanté et ligne du dernier prix — les comportements natifs
// de la bibliothèque, vendorée dans vendor/ (v5.2.1) — plus le zoom
// rectangulaire (Maj + glisser) qu'elle n'offre pas.
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

    // Le choix d'afficher ou non les flèches, dans le navigateur.
    var SIGNALS_KEY = "btcterm-signals";

    // De même pour les sonneries marquées sur le graphique (§ sonneries).
    var EVENTS_KEY = "btcterm-events";

    // Les lignes de tendance, dans le navigateur elles aussi.
    var DRAWINGS_KEY = "btcterm-drawings";

    // Les ancres des VWAP posés à la main, et combien on en garde : au-delà
    // de trois courbes, le cours ne se lit plus — la quatrième remplace la
    // plus ancienne.
    var ANCHORS_KEY = "btcterm-anchors";
    var ANCHOR_MAX = 3;

    var SVG_NS = "http://www.w3.org/2000/svg";

    // Marge d'un Alt + clic pour viser un seuil existant, en pixels.
    var ALERT_HIT_PX = 6;

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
        backfillTimer: null,  // page manquée, source muette : le réessai
        banner: null,
        legend: null,
        readout: null,
        measureLabel: null,
        measuring: false,
        drawMode: false,
        drawPreview: null,
        drawHint: null,
        drawLines: [],
        drawings: [],
        anchorMode: false,  // A : le prochain clic pose une ancre
        anchorHint: null,
        anchorLines: [],    // {t, series, points} — series null hors historique
        anchors: [],        // {t} : instants réels des ancres
        hover: null,   // dernier événement du crosshair sur une bougie
        clock: null,   // temps réels ↔ temps affichés (assets/lwc-clock.js)
        signalsHidden: localStorage.getItem(SIGNALS_KEY) === "off",
        eventsHidden: localStorage.getItem(EVENTS_KEY) === "off",
        events: [],       // [temps, règle, message, couleur] (/api/alert-events)
        eventsByBar: {},  // indice de bougie → ses sonneries
        zoomBox: null,  // le cadre du zoom rectangulaire, caché au repos
        drag: null,     // la sélection en cours : {x0, y0, x1, y1, area}
        view: null,     // la fenêtre regardée, depuis la fin (§ trackView)
        viewWidth: 0    // la largeur de l'axe du temps qui l'a vue
    };

    var INTRADAY = {"1m": 1, "5m": 1, "15m": 1, "30m": 1, "1h": 1, "4h": 1,
                    "6h": 1, "12h": 1};

    // ── Heure affichée ──────────────────────────────────────
    //
    // Heure locale du navigateur par défaut, UTC si la case l'est
    // (cfg.utc). Le paquet garde ses temps réels ; l'horloge de
    // lwc-clock.js décale ceux qu'on pose dans les séries, et recale ceux
    // que le graphique rend. Jusqu'au 12 novembre 2026, le graphique était
    // le seul du terminal en UTC : la bougie de 04:20 à Phnom Penh s'y
    // lisait « 21:20 », la veille.
    //
    // Sans lwc-clock.js — une page qui ne l'aurait pas chargé —, une
    // horloge immobile : l'UTC, plutôt qu'un graphique vide.
    var STILL_CLOCK = {
        shift: false,
        add: function (t) { return t; },
        addAll: function () { return this; },
        shown: function (t) { return t; },
        actual: function (t) { return t; },
        points: function (points) { return points; }
    };

    function clock() { return state.clock || STILL_CLOCK; }

    // Rebâtie à chaque versement du paquet, sur toutes ses bougies. Les
    // bougies d'un jour et plus ne se décalent pas : elles sont le jour
    // UTC de Binance, et un fuseau à l'ouest de Greenwich les daterait
    // de la veille ; leur axe n'affiche pas d'heure.
    function rebuildClock() {
        var interval = state.packet.interval || state.cfg.interval;
        var shift = !state.cfg.utc && !!INTRADAY[interval];
        state.clock = window.btcClock && shift
            ? window.btcClock.create(true).addAll(state.packet.bars)
            : STILL_CLOCK;
    }

    //: Historique chargé à la volée : dès que le bord gauche visible
    //: approche à moins de ce nombre de bougies du début du tampon, la
    //: page antérieure est demandée.
    var BACKFILL_MARGIN = 50;

    //: Plafond du tampon par intervalle : au-delà, on cesse de remonter
    //: le passé — la mémoire reste bornée. 10 000 bougies font une semaine
    //: de minutes, un mois de cinq minutes, quatorze mois d'horaire ; 5 000
    //: arrêtaient la minute à trois jours et demi, trois pages après la
    //: première.
    var MAX_BARS = 10000;

    //: Une page antérieure revenue vide parce que Binance se taisait
    //: (`stale`) n'est pas la fin de l'historique : elle est redemandée
    //: après ce délai, même sans nouveau pan. Le serveur, lui, ne
    //: retente la source qu'une fois par minute ; entre-temps, un essai
    //: lui coûte une lecture du disque.
    var BACKFILL_RETRY = 30000;

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
    // Les oscillateurs bornés (RSI, CRSI) : échelle figée sur 0-100, et
    // une marge mesurée autour. Sans marge, « 100 » tombait sur le bord
    // du pane et le séparateur le coupait en deux ; les marges par défaut
    // étiraient l'axe jusqu'à des rangs négatifs. À 8 % de chaque côté,
    // l'axe court de −9,5 à 109,5 : aucune graduation hors de 0-100, que
    // le pas soit de 10 ou de 20. En entiers sur l'axe — la légende garde
    // la décimale.
    var OSCILLATOR_MARGINS = {top: 0.08, bottom: 0.08};
    var OSCILLATOR_FORMAT = {type: "price", precision: 0, minMove: 1};

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
                // Transparent sous la carte de chaleur du carnet, qui se
                // peint dessous (§ Carte de chaleur) : le fond du panneau
                // reste celui du thème.
                background: {type: "solid",
                             color: extras.indexOf("heat") !== -1
                                 ? "rgba(0,0,0,0)" : theme.panel},
                textColor: theme.muted,
                fontFamily: state.conf.mono,
                fontSize: 10,
                panes: {separatorColor: theme.border,
                        separatorHoverColor: theme.border,
                        enableResize: true}
            },
            // Pas de quadrillage sur la carte de chaleur du carnet : ses
            // lignes sombres striaient les tranches.
            grid: {
                vertLines: {color: theme.grid,
                            visible: extras.indexOf("heat") === -1},
                horzLines: {color: theme.grid,
                            visible: extras.indexOf("heat") === -1}
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

        // Le VWAP ancré et ses bandes, avec les niveaux clés (case NIV) :
        // un niveau de référence de plus, et la barre de titre n'avait plus
        // la place d'une case (§ VWAP ancré, plus bas).
        if (extras.indexOf("levels") !== -1 && window.btcVwap) {
            series.vwap = state.chart.addSeries(LWC.LineSeries, {
                color: VWAP_COLOR, lineWidth: 2,
                priceLineVisible: false, lastValueVisible: false,
                crosshairMarkerVisible: false
            }, 0);
            ["vwapUpper", "vwapLower"].forEach(function (name) {
                series[name] = state.chart.addSeries(LWC.LineSeries, {
                    color: VWAP_COLOR, lineWidth: 1,
                    lineStyle: LWC.LineStyle.Dotted,
                    priceLineVisible: false, lastValueVisible: false,
                    crosshairMarkerVisible: false,
                    // Les bandes suivent le cours, elles ne le cadrent pas :
                    // larges en début de période, elles étireraient l'axe.
                    autoscaleInfoProvider: function () { return null; }
                }, 0);
            });
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
                priceFormat: OSCILLATOR_FORMAT,
                autoscaleInfoProvider: fixedScale(0, 100)
            }, pane);
            series.rsi.priceScale().applyOptions({
                scaleMargins: OSCILLATOR_MARGINS
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
                priceFormat: OSCILLATOR_FORMAT,
                autoscaleInfoProvider: fixedScale(0, 100)
            }, pane);
            series.crsi.priceScale().applyOptions({
                scaleMargins: OSCILLATOR_MARGINS
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
        if (extras.indexOf("cvd") !== -1) {
            series.cvd = state.chart.addSeries(LWC.LineSeries, {
                color: theme.cyan, lineWidth: 2,
                priceFormat: {type: "volume"},
                priceLineVisible: false, lastValueVisible: true
            }, pane);
            pane += 1;
        }
        // L'open interest du perpétuel — la case OI : des positions qui
        // s'ouvrent ou se ferment pendant que le cours bouge (§ Open
        // interest, plus bas).
        if (extras.indexOf("oi") !== -1) {
            series.oi = state.chart.addSeries(LWC.LineSeries, {
                color: theme.orange, lineWidth: 2,
                priceFormat: {type: "custom", minMove: 1e6,
                              formatter: billions},
                priceLineVisible: false, lastValueVisible: true
            }, pane);
            pane += 1;
            state.oiData = null;
            state.oiPoll = setInterval(fetchOi, OI_EVERY);
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
        buildBoxZoom();
        updateAlertLines();
        // Le temps qui reste avant la clôture de la bougie, à côté du
        // dernier prix — à la seconde, comme l'horloge du bandeau.
        state.countdownTimer = setInterval(updateCountdown, 1000);

        // Profil de volume : un canvas en surimpression à droite du
        // pane du cours, recalculé sur la plage visible (le VPVR de
        // TradingView) — la case PROFIL de la barre de titre le tient.
        if (extras.indexOf("profile") !== -1) {
            buildProfile();
        }
        // Niveaux clés — la case NIV : veille, semaine et mois passés,
        // ouvertures en cours.
        if (extras.indexOf("levels") !== -1) {
            buildLevels();
        }
        // Carte des liquidations — la case LIQ : où, en prix, le levier a
        // sauté sur la plage visible.
        if (extras.indexOf("liq") !== -1) {
            buildLiqMap();
        }
        // Gros ordres — la case ORD : où les gros ont traité, et dans
        // quel sens.
        if (extras.indexOf("trades") !== -1) {
            buildTrades();
        }
        // Carte de chaleur du carnet — la case MURS : la liquidité posée,
        // niveau par niveau, dans le temps.
        if (extras.indexOf("heat") !== -1) {
            buildHeat();
        }

        // L'historique se charge au pan, comme sur tradingview.com :
        // approcher du début des données déclenche la page antérieure.
        state.chart.timeScale()
            .subscribeVisibleLogicalRangeChange(maybeBackfill);
        // Le cadre d'une mesure vaut pour la vue où il a été tracé.
        state.chart.timeScale()
            .subscribeVisibleLogicalRangeChange(clearMeasure);
        // La fenêtre regardée, que reconstruction et plein écran rendent.
        state.viewWidth = 0;
        state.chart.timeScale()
            .subscribeVisibleLogicalRangeChange(trackView);

        state.chart.subscribeCrosshairMove(function (param) {
            state.hover = param.time === undefined ? null : param;
            updateReadout();
        });
        state.chart.subscribeClick(function (param) {
            var event = param.sourceEvent;
            if (state.anchorMode && event && !event.altKey
                    && param.time !== undefined && param.paneIndex === 0) {
                addAnchor(clock().actual(param.time));
                setAnchorMode(false);
                return;
            }
            if (!event || !event.altKey || !param.point
                    || param.paneIndex !== 0) {
                return;
            }
            var drawing = drawingAt(param.point.x, param.point.y);
            var anchor = drawing === -1 ? anchorAt(param) : -1;
            if (drawing !== -1) {
                state.drawings.splice(drawing, 1);
                saveDrawings();
                renderDrawings();
            } else if (anchor !== -1) {
                state.anchors.splice(anchor, 1);
                saveAnchors();
                renderAnchors();
            } else {
                toggleAlertAt(param.point.y);
            }
        });
    }

    // Le compte à rebours de la bougie en cours (btcClock.countdown, testé
    // sous Node), en titre de la série : la bibliothèque le pose sur
    // l'étiquette du dernier prix, là où l'œil le cherche. Rien sur la
    // démonstration — ses bougies ne clôturent jamais —, ni quand la série
    // affichée n'est pas encore celle de l'intervalle choisi.
    function updateCountdown() {
        if (!state.series || !state.packet) { return; }
        var bars = state.packet.bars;
        var text = "";
        if (window.btcClock && window.btcClock.countdown && bars.length
                && !state.packet.demo
                && (state.packet.interval || state.cfg.interval)
                    === state.cfg.interval) {
            text = window.btcClock.countdown(bars[bars.length - 1].time,
                                             state.cfg.interval,
                                             Date.now() / 1000);
        }
        if (text !== state.countdownText) {
            state.countdownText = text;
            state.series.candles.applyOptions({title: text});
        }
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
        // Le VWAP dit son ancrage, qui suit l'intervalle (§ fillVwap).
        if (state.series && state.series.vwap) {
            var vwapItem = document.createElement("span");
            vwapItem.style.color = VWAP_COLOR;
            div.appendChild(vwapItem);
            state.vwapLegend = vwapItem;
        }
        // Le geste ne se devine pas : le rappeler là où il y a la
        // place, en plein écran (terminal.css ne l'affiche que là).
        var hint = document.createElement("span");
        hint.className = "lwc-zoom-hint";
        hint.textContent = "Maj + glisser : zoom · double-clic : vue entière"
            + " · Ctrl + glisser : mesure · T : ligne · A : VWAP ancré"
            + " · Alt + clic : alerte";
        div.appendChild(hint);
        state.el.appendChild(div);
        state.legend = div;

        var readout = document.createElement("div");
        readout.className = "lwc-readout";
        state.el.appendChild(readout);
        state.readout = readout;
    }

    // ── Lecture au crosshair ────────────────────────────────

    function money(value) {
        return value.toLocaleString("en-US", {minimumFractionDigits: 2,
                                              maximumFractionDigits: 2});
    }

    // La date d'un temps affiché, lue comme l'axe la lit : le décalage de
    // l'heure locale y est déjà. Les heures n'ont de sens qu'en intraday,
    // et l'UTC s'y dit — l'heure locale est celle de tout le terminal.
    function barDate(time) {
        var intraday = !!INTRADAY[state.cfg.interval];
        return new Date(time * 1000).toISOString()
            .slice(0, intraday ? 16 : 10)
            .replace("T", " ") + (intraday && state.cfg.utc ? " UTC" : "");
    }

    // La valeur d'une série sous le crosshair, ou sa dernière valeur.
    function valueOf(line) {
        if (!line) { return undefined; }
        if (state.hover) {
            var point = state.hover.seriesData.get(line);
            return point ? point.value : undefined;
        }
        var data = line.data();
        return data.length ? data[data.length - 1].value : undefined;
    }

    // La bibliothèque ne donne aucune valeur à lire : sans cette ligne, le
    // crosshair montre un prix sur l'axe, jamais la bougie sous la souris.
    // Elle suit la souris, et revient à la dernière bougie quand celle-ci
    // sort du graphique. La variation se lit contre la clôture précédente.
    function updateReadout() {
        if (!state.readout || !state.series) { return; }
        var candles = state.series.candles;
        var bar = null;
        var previous = null;
        if (state.hover) {
            bar = state.hover.seriesData.get(candles) || null;
            previous = bar
                ? candles.dataByIndex(state.hover.logical - 1) : null;
        }
        if (!bar) {
            var data = candles.data();
            bar = data.length ? data[data.length - 1] : null;
            previous = data.length > 1 ? data[data.length - 2] : null;
        }
        state.readout.textContent = "";
        if (!bar) { return; }

        var reference = previous ? previous.close : bar.open;
        var change = (bar.close / reference - 1) * 100;
        var theme = state.conf.theme;
        var unit = state.cfg.currency === "EUR" ? " €" : " $";
        var parts = [
            [barDate(bar.time), theme.muted],
            ["O " + money(bar.open), theme.text],
            ["H " + money(bar.high), theme.text],
            ["L " + money(bar.low), theme.text],
            ["C " + money(bar.close) + unit, theme.text],
            [(change >= 0 ? "+" : "") + change.toFixed(2) + " %",
             change >= 0 ? theme.green : theme.red]
        ];
        var volume = valueOf(state.series.volume);
        if (volume !== undefined) {
            parts.push(["V " + volume.toFixed(1), theme.muted]);
        }
        var rsi = valueOf(state.series.rsi);
        if (rsi !== undefined) {
            parts.push(["RSI " + rsi.toFixed(1), theme.blue]);
        }
        var crsi = valueOf(state.series.crsi);
        if (crsi !== undefined) {
            parts.push(["CRSI " + crsi.toFixed(1), theme.purple]);
        }
        var cvd = valueOf(state.series.cvd);
        if (cvd !== undefined) {
            parts.push(["CVD " + Math.round(cvd).toLocaleString("en-US"),
                        theme.cyan]);
        }
        var vwap = valueOf(state.series.vwap);
        if (vwap !== undefined) {
            parts.push(["VWAP " + money(vwap), VWAP_COLOR]);
        }
        (state.anchorLines || []).forEach(function (line) {
            var value = anchorValue(line);
            if (value !== undefined) {
                parts.push([anchorLabel(line.t) + " " + money(value),
                            theme.text]);
            }
        });
        var oi = valueOf(state.series.oi);
        if (oi !== undefined) {
            parts.push(["OI " + billions(oi) + unit, theme.orange]);
        }
        // Les sonneries de la bougie survolée, en dernier : la ligne passe
        // à la suivante s'il le faut. Au survol seulement — sans lui, la
        // lecture est celle de la dernière bougie, et en journalier toutes
        // les sonneries du jour y restaient affichées, sur deux lignes.
        if (state.hover && state.hover.logical !== undefined) {
            parts = parts.concat(eventParts(Math.round(state.hover.logical)));
        }
        parts.forEach(function (part) {
            var item = document.createElement("span");
            item.textContent = part[0];
            item.style.color = part[1];
            state.readout.appendChild(item);
        });
    }

    // ── Zoom rectangulaire ──────────────────────────────────

    // Lightweight Charts n'a pas de zoom à la boîte — la molette et le
    // pan seulement. Maj + glisser trace un cadre sur le pane du cours ;
    // le relâcher cadre la fenêtre dessus : le temps par l'échelle
    // logique, le prix par l'échelle de droite, qui quitte alors
    // l'auto-ajustement. Le double-clic sur le graphique rend la vue
    // entière — le geste de Plotly, que les autres panneaux gardent.
    //
    // Les écouteurs sont posés en phase de capture sur le div du
    // panneau : avec Maj enfoncée, l'événement est arrêté avant
    // d'atteindre le canvas de la bibliothèque, qui sinon panoterait.
    // Sans Maj, rien ne change au comportement natif.
    function buildBoxZoom() {
        var box = document.createElement("div");
        box.className = "lwc-zoom-box";
        box.style.borderColor = state.conf.theme.cyan;
        box.style.display = "none";
        var label = document.createElement("div");
        label.className = "lwc-measure";
        box.appendChild(label);
        // L'aperçu d'une ligne pendant le tracé : le vrai segment n'existe
        // qu'une fois ses deux bouts posés sur des bougies.
        var preview = document.createElementNS(SVG_NS, "svg");
        preview.setAttribute("class", "lwc-draw-preview");
        var stroke = document.createElementNS(SVG_NS, "line");
        stroke.setAttribute("stroke", state.conf.theme.yellow);
        preview.appendChild(stroke);
        state.el.appendChild(preview);
        state.drawPreview = preview;
        var hint = document.createElement("div");
        hint.className = "lwc-draw-hint";
        hint.textContent = "tracé : glisser d'un point à l'autre · Échap pour annuler";
        hint.style.color = state.conf.theme.yellow;
        state.el.appendChild(hint);
        state.drawHint = hint;
        var anchorHint = document.createElement("div");
        anchorHint.className = "lwc-draw-hint";
        anchorHint.textContent = "ancrage : cliquer la bougie de départ du "
            + "VWAP · Échap pour annuler";
        anchorHint.style.color = state.conf.theme.text;
        state.el.appendChild(anchorHint);
        state.anchorHint = anchorHint;
        state.el.appendChild(box);
        state.zoomBox = box;
        state.measureLabel = label;
        state.el.addEventListener("mousedown", zoomStart, true);
        state.chart.subscribeDblClick(resetView);
    }

    // Le pane du cours : tout le div moins l'axe des prix à droite,
    // haut de la première rangée seulement — les panes RSI/CRSI et
    // l'axe du temps sont hors cadre.
    function paneArea() {
        return {
            width: state.el.clientWidth
                - state.chart.priceScale("right").width(),
            height: state.chart.panes()[0].getHeight()
        };
    }

    function pointIn(ev) {
        var rect = state.el.getBoundingClientRect();
        return {x: ev.clientX - rect.left, y: ev.clientY - rect.top};
    }

    function zoomStart(ev) {
        clearMeasure();
        var measure = ev.ctrlKey && !ev.shiftKey;
        var draw = state.drawMode && !measure && !ev.shiftKey;
        if (!(ev.shiftKey || measure || draw) || ev.button !== 0
                || !state.chart) {
            return;
        }
        var p = pointIn(ev);
        var area = paneArea();
        if (p.x < 0 || p.x > area.width || p.y < 0 || p.y > area.height) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        state.drag = {x0: p.x, y0: p.y, x1: p.x, y1: p.y, area: area,
                      measure: measure, draw: draw};
        drawBox();
        document.addEventListener("mousemove", zoomMove, true);
        document.addEventListener("mouseup", zoomEnd, true);
        document.addEventListener("keydown", zoomKey, true);
    }

    function zoomMove(ev) {
        var drag = state.drag;
        if (!drag) { return; }
        var p = pointIn(ev);
        drag.x1 = Math.max(0, Math.min(drag.area.width, p.x));
        drag.y1 = Math.max(0, Math.min(drag.area.height, p.y));
        drawBox();
    }

    // Échap pendant le tracé abandonne la sélection, et efface une mesure
    // restée à l'écran — sans quitter le plein écran en même temps
    // (terminal.js écoute la même touche).
    function zoomKey(ev) {
        if (ev.key !== "Escape") { return; }
        if (state.drag) {
            ev.stopPropagation();
            zoomStop();
            setDrawMode(false);
        } else if (state.drawMode) {
            ev.stopPropagation();
            setDrawMode(false);
        } else if (state.anchorMode) {
            ev.stopPropagation();
            setAnchorMode(false);
        } else if (state.measuring) {
            ev.stopPropagation();
            clearMeasure();
        }
    }

    function zoomStop() {
        state.drag = null;
        state.measuring = false;
        if (state.zoomBox) {
            state.zoomBox.style.display = "none";
            state.zoomBox.style.borderColor = state.conf.theme.cyan;
            state.zoomBox.style.background = "";
            state.measureLabel.textContent = "";
            state.drawPreview.style.display = "none";
        }
        document.removeEventListener("mousemove", zoomMove, true);
        document.removeEventListener("mouseup", zoomEnd, true);
        document.removeEventListener("keydown", zoomKey, true);
        // Un zoom au cadre pendant qu'un geste reste armé : Échap doit
        // encore pouvoir le désarmer.
        syncArmed();
    }

    function drawBox() {
        var drag = state.drag;
        var box = state.zoomBox;
        if (!drag || !box) { return; }
        if (drag.draw) {
            var line = state.drawPreview.firstChild;
            line.setAttribute("x1", drag.x0);
            line.setAttribute("y1", drag.y0);
            line.setAttribute("x2", drag.x1);
            line.setAttribute("y2", drag.y1);
            state.drawPreview.style.display = "block";
            return;
        }
        box.style.left = Math.min(drag.x0, drag.x1) + "px";
        box.style.top = Math.min(drag.y0, drag.y1) + "px";
        box.style.width = Math.abs(drag.x1 - drag.x0) + "px";
        box.style.height = Math.abs(drag.y1 - drag.y0) + "px";
        box.style.display = "block";
        if (drag.measure) { updateMeasure(drag); }
    }

    // ── Lignes de tendance ──────────────────────────────────

    // T arme le tracé pour une ligne. Les lignes sont des séries à deux
    // points, pas des dessins sur le canvas : elles suivent ainsi le zoom, le
    // pan et l'échelle sans rien à recalculer. Elles se gardent en dollars
    // et en heures absolues, pour survivre à la devise et à l'intervalle.
    function setDrawMode(on) {
        state.drawMode = !!on && !!state.chart;
        if (state.drawMode && state.anchorMode) { setAnchorMode(false); }
        if (!state.drawHint) { return; }
        state.drawHint.style.display = state.drawMode ? "block" : "none";
        syncArmed();
    }

    // Le curseur en croix et l'écoute d'Échap, tant qu'un geste est armé —
    // tracé ou ancrage. Pendant une mesure, l'écouteur reste à celle-ci.
    function syncArmed() {
        var armed = state.drawMode || state.anchorMode;
        if (state.el) { state.el.classList.toggle("lwc-drawing", armed); }
        if (armed) {
            document.addEventListener("keydown", zoomKey, true);
        } else if (!state.measuring && !state.drag) {
            document.removeEventListener("keydown", zoomKey, true);
        }
    }

    function loadDrawings() {
        try {
            var saved = JSON.parse(localStorage.getItem(DRAWINGS_KEY));
            return Array.isArray(saved) ? saved : [];
        } catch (error) {
            return [];
        }
    }

    function saveDrawings() {
        localStorage.setItem(DRAWINGS_KEY, JSON.stringify(state.drawings));
    }

    // L'indice de la dernière bougie ouverte au plus tard à `time`. Une
    // ligne tracée en 1h se lit ainsi en 1d : ses bouts retombent sur les
    // bougies de l'intervalle affiché, sans ajouter d'instant à l'axe.
    function barIndexAt(time) {
        var bars = state.packet.bars;
        var low = 0;
        var high = bars.length - 1;
        var found = -1;
        while (low <= high) {
            var middle = (low + high) >> 1;
            if (bars[middle].time <= time) {
                found = middle;
                low = middle + 1;
            } else {
                high = middle - 1;
            }
        }
        return found;
    }

    // Les deux points affichés, ligne prolongée jusqu'à la dernière bougie :
    // une tendance se lit à sa projection. En échelle log, c'est en log que
    // la ligne est droite : la prolonger en prix la casserait à l'écran.
    function drawingPoints(drawing) {
        var bars = state.packet.bars;
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        var first = barIndexAt(drawing.t0);
        var second = barIndexAt(drawing.t1);
        var last = bars.length - 1;
        if (first < 0 || second <= first) { return null; }
        var progress = (last - first) / (second - first);
        var end = state.cfg.log
            ? drawing.p0 * Math.pow(drawing.p1 / drawing.p0, progress)
            : drawing.p0 + (drawing.p1 - drawing.p0) * progress;
        if (!(end > 0)) { return null; }
        return [{time: clock().shown(bars[first].time),
                 value: drawing.p0 * rate},
                {time: clock().shown(bars[last].time), value: end * rate}];
    }

    function renderDrawings() {
        if (!state.chart || !state.packet) { return; }
        (state.drawLines || []).forEach(function (line) {
            state.chart.removeSeries(line.series);
        });
        state.drawLines = [];
        var theme = state.conf.theme;
        state.drawings.forEach(function (drawing, index) {
            var points = drawingPoints(drawing);
            if (!points) { return; }
            var series = state.chart.addSeries(LightweightCharts.LineSeries, {
                color: theme.yellow, lineWidth: 1,
                priceLineVisible: false, lastValueVisible: false,
                crosshairMarkerVisible: false,
                // L'axe des prix suit le cours, pas les lignes : une tendance
                // qui file vers zéro n'a pas à écraser le graphique.
                autoscaleInfoProvider: function () { return null; }
            }, 0);
            series.setData(points);
            state.drawLines.push({series: series, points: points,
                                  index: index});
        });
    }

    function addDrawing(drag) {
        var candles = state.series.candles;
        var ts = state.chart.timeScale();
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        var ends = [[drag.x0, drag.y0], [drag.x1, drag.y1]].map(function (end) {
            var bar = candles.dataByIndex(
                Math.round(ts.coordinateToLogical(end[0])),
                LightweightCharts.MismatchDirection.NearestLeft);
            var price = candles.coordinateToPrice(end[1]);
            // La bougie rend son temps affiché ; la ligne se garde en
            // temps réel, pour survivre à la bascule UTC / heure locale.
            return bar && price !== null
                ? {time: clock().actual(bar.time), price: price / rate} : null;
        }).sort(function (a, b) {
            return a && b ? a.time - b.time : 0;
        });
        if (!ends[0] || !ends[1] || ends[0].time === ends[1].time) { return; }
        state.drawings.push({t0: ends[0].time, p0: ends[0].price,
                             t1: ends[1].time, p1: ends[1].price});
        saveDrawings();
        renderDrawings();
    }

    // Le tracé visé par un Alt + clic : à quelques pixels de sa droite, entre
    // ses deux bouts. Rend son rang, ou -1.
    function drawingAt(x, y) {
        var candles = state.series.candles;
        var ts = state.chart.timeScale();
        var hit = (state.drawLines || []).find(function (line) {
            var x0 = ts.timeToCoordinate(line.points[0].time);
            var x1 = ts.timeToCoordinate(line.points[1].time);
            var y0 = candles.priceToCoordinate(line.points[0].value);
            var y1 = candles.priceToCoordinate(line.points[1].value);
            if (x0 === null || x1 === null || y0 === null || y1 === null
                    || x < x0 || x > x1 || x1 === x0) {
                return false;
            }
            return Math.abs(y0 + (y1 - y0) * (x - x0) / (x1 - x0) - y)
                <= ALERT_HIT_PX;
        });
        return hit ? hit.index : -1;
    }

    // ── VWAP ancré à la main ────────────────────────────────
    //
    // A arme l'ancrage, un clic pose l'ancre sur la bougie visée : de là,
    // le prix moyen pondéré par le volume jusqu'à la dernière bougie
    // (btcVwap.fromAnchor, testé sous Node). L'ancre se garde en temps
    // réel, comme les lignes : elle survit à la devise, à l'intervalle et
    // au rechargement. Alt + clic sur la courbe la retire.
    function setAnchorMode(on) {
        state.anchorMode = !!on && !!state.chart;
        if (state.anchorMode && state.drawMode) { setDrawMode(false); }
        if (!state.anchorHint) { return; }
        state.anchorHint.style.display = state.anchorMode ? "block" : "none";
        syncArmed();
    }

    function loadAnchors() {
        try {
            var saved = JSON.parse(localStorage.getItem(ANCHORS_KEY));
            return Array.isArray(saved) ? saved.filter(function (a) {
                return a && typeof a.t === "number";
            }).slice(-ANCHOR_MAX) : [];
        } catch (error) {
            return [];
        }
    }

    function saveAnchors() {
        try {
            localStorage.setItem(ANCHORS_KEY, JSON.stringify(state.anchors));
        } catch (error) { /* navigation privée : l'ancre vit sa séance */ }
    }

    function addAnchor(time) {
        if (typeof time !== "number") { return; }
        state.anchors = state.anchors.filter(function (a) {
            return a.t !== time;
        });
        state.anchors.push({t: time});
        state.anchors = state.anchors.slice(-ANCHOR_MAX);
        saveAnchors();
        renderAnchors();
    }

    // Le nom d'une ancre, sur l'axe des prix et dans la lecture : sa date,
    // et son heure en intraday — telles que l'axe les affiche.
    function anchorLabel(time) {
        var shown = new Date(clock().shown(time) * 1000).toISOString();
        var label = "AV " + shown.slice(8, 10) + "/" + shown.slice(5, 7);
        return INTRADAY[state.cfg.interval]
            ? label + " " + shown.slice(11, 16) : label;
    }

    function anchorPoints(anchor, rate) {
        var points = window.btcVwap
            ? window.btcVwap.fromAnchor(state.packet.bars, state.packet.volume,
                                        anchor.t)
            : null;
        if (!points) { return null; }
        var at = clock();
        return points.map(function (p) {
            return {time: at.shown(p.time), value: p.value * rate};
        });
    }

    function renderAnchors() {
        if (!state.chart || !state.packet) { return; }
        (state.anchorLines || []).forEach(function (line) {
            if (line.series) { state.chart.removeSeries(line.series); }
        });
        state.anchorLines = [];
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        state.anchors.forEach(function (anchor) {
            var points = anchorPoints(anchor, rate);
            var line = {t: anchor.t, series: null, points: points};
            if (points && points.length) {
                line.series = state.chart.addSeries(
                    LightweightCharts.LineSeries, {
                        color: state.conf.theme.text, lineWidth: 1,
                        title: anchorLabel(anchor.t),
                        priceLineVisible: false, lastValueVisible: true,
                        crosshairMarkerVisible: false,
                        // Comme les lignes : l'axe suit le cours.
                        autoscaleInfoProvider: function () { return null; }
                    }, 0);
                line.series.setData(points);
            }
            state.anchorLines.push(line);
        });
        updateAnchorLegend();
        updateReadout();
    }

    // La bougie en cours bouge, la fin des VWAP ancrés aussi : le dernier
    // point se repose, et une bougie qui clôture en ajoute un.
    function updateAnchorTails() {
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        (state.anchorLines || []).forEach(function (line) {
            if (!line.series) { return; }
            var points = anchorPoints({t: line.t}, rate);
            if (points && points.length) {
                line.series.update(points[points.length - 1]);
                line.points = points;
            }
        });
    }

    // La légende dit les ancres que l'historique chargé ne couvre pas :
    // une courbe absente sans un mot passerait pour une ancre perdue.
    function updateAnchorLegend() {
        if (!state.legend) { return; }
        if (!state.anchorLegend) {
            state.anchorLegend = document.createElement("span");
            state.anchorLegend.style.color = state.conf.theme.muted;
            var hint = state.legend.querySelector(".lwc-zoom-hint");
            state.legend.insertBefore(state.anchorLegend, hint);
        }
        var missing = state.anchorLines.filter(function (line) {
            return !line.series;
        }).map(function (line) { return anchorLabel(line.t); });
        state.anchorLegend.textContent = missing.length
            ? missing.join(", ") + " : avant l'historique chargé — remonter "
              + "le graphique" : "";
    }

    // La valeur d'un VWAP ancré sous le crosshair. Une courbe posée après
    // le dernier mouvement de souris n'est pas dans le survol retenu : sa
    // valeur se cherche alors au temps survolé — la lecture la disait
    // sinon au prochain mouvement seulement.
    function anchorValue(line) {
        if (!line.series) { return undefined; }
        if (!state.hover) { return valueOf(line.series); }
        var point = state.hover.seriesData.get(line.series);
        if (point) { return point.value; }
        var points = line.points || [];
        var low = 0;
        var high = points.length - 1;
        while (low <= high) {
            var middle = (low + high) >> 1;
            if (points[middle].time === state.hover.time) {
                return points[middle].value;
            }
            if (points[middle].time < state.hover.time) {
                low = middle + 1;
            } else {
                high = middle - 1;
            }
        }
        return undefined;
    }

    // L'ancre dont la courbe passe sous le clic, à la marge d'une alerte.
    function anchorAt(param) {
        var candles = state.series.candles;
        return (state.anchorLines || []).findIndex(function (line) {
            if (!line.series || !param.seriesData) { return false; }
            var point = param.seriesData.get(line.series);
            if (!point) { return false; }
            var y = candles.priceToCoordinate(point.value);
            return y !== null && Math.abs(y - param.point.y) <= ALERT_HIT_PX;
        });
    }

    // ── Mesure ──────────────────────────────────────────────

    // Une durée lisible, en la plus grande unité qui donne un chiffre utile.
    function duration(seconds) {
        if (seconds >= 2 * 86400) {
            return Math.round(seconds / 86400) + " j";
        }
        if (seconds >= 3600) { return Math.round(seconds / 3600) + " h"; }
        return Math.round(seconds / 60) + " min";
    }

    // Ctrl + glisser : ce que le cours a fait entre deux points. Le cadre
    // reprend celui du zoom, coloré dans le sens du mouvement, et reste à
    // l'écran après le relâchement : une mesure se lit, elle ne doit pas
    // disparaître avec le geste. Le clic suivant, Échap ou un pan l'effacent.
    function updateMeasure(drag) {
        var candles = state.series.candles;
        var ts = state.chart.timeScale();
        var from = candles.coordinateToPrice(drag.y0);
        var to = candles.coordinateToPrice(drag.y1);
        var left = ts.coordinateToLogical(drag.x0);
        var right = ts.coordinateToLogical(drag.x1);
        var bars = state.packet.bars;
        if (from === null || to === null || left === null || right === null
                || bars.length < 2) {
            return;
        }
        var theme = state.conf.theme;
        var change = (to / from - 1) * 100;
        var count = Math.round(right - left);
        var step = bars[bars.length - 1].time - bars[bars.length - 2].time;
        var unit = state.cfg.currency === "EUR" ? " €" : " $";
        var sign = change >= 0 ? "+" : "-";
        state.measureLabel.textContent = sign + Math.abs(change).toFixed(2)
            + " % · " + sign + money(Math.abs(to - from)) + unit + " · "
            + Math.abs(count) + " bougies · "
            + duration(Math.abs(count) * step);
        state.measureLabel.style.color = change >= 0 ? theme.green : theme.red;
        state.zoomBox.style.borderColor = state.measureLabel.style.color;
        state.zoomBox.style.background = change >= 0
            ? "rgba(0, 212, 170, 0.10)" : "rgba(255, 61, 107, 0.10)";
    }

    function clearMeasure() {
        if (state.measuring) { zoomStop(); }
    }

    function zoomEnd(ev) {
        var drag = state.drag;
        if (!drag) { return; }
        ev.stopPropagation();
        // Le cadre reste ; seuls s'en vont les écouteurs du tracé. Échap
        // garde le sien, pour l'effacer.
        if (drag.measure && Math.abs(drag.x1 - drag.x0) >= 4) {
            state.drag = null;
            state.measuring = true;
            document.removeEventListener("mousemove", zoomMove, true);
            document.removeEventListener("mouseup", zoomEnd, true);
            return;
        }
        zoomStop();
        if (drag.draw) {
            setDrawMode(false);
            addDrawing(drag);
            return;
        }
        // Un cadre de quelques pixels est un clic avec Maj, pas un zoom.
        if (Math.abs(drag.x1 - drag.x0) < 4
                || Math.abs(drag.y1 - drag.y0) < 4) {
            return;
        }
        applyBox(Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1),
                 Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1));
    }

    // Cadre la fenêtre sur le rectangle (coordonnées du pane du cours).
    function applyBox(left, top, right, bottom) {
        if (!state.chart || !state.series) { return; }
        var ts = state.chart.timeScale();
        var from = ts.coordinateToLogical(left);
        var to = ts.coordinateToLogical(right);
        if (from !== null && to !== null && to - from >= 1) {
            ts.setVisibleLogicalRange({from: from, to: to});
        }
        var candles = state.series.candles;
        var high = candles.coordinateToPrice(top);
        var low = candles.coordinateToPrice(bottom);
        if (high === null || low === null || !(high > low)) { return; }
        var scale = candles.priceScale();
        // setVisibleRange pose la plage telle quelle, alors qu'en
        // échelle log l'axe travaille en valeurs transformées : passer
        // par le mode linéaire, que la bascule de mode convertit
        // elle-même — coordinateToPrice, lui, rend toujours des prix.
        var LWC = LightweightCharts;
        var log = scale.options().mode === LWC.PriceScaleMode.Logarithmic;
        if (log) { scale.applyOptions({mode: LWC.PriceScaleMode.Normal}); }
        scale.setVisibleRange({from: low, to: high});
        if (log) {
            scale.applyOptions({mode: LWC.PriceScaleMode.Logarithmic});
        }
    }

    // La fenêtre regardée, en rangs de bougie, mise de côté pour qu'une
    // reconstruction (une case cochée) ou le plein écran la rendent.
    // Les deux recadraient sur toute la série (fitContent) : sur une série
    // que le pan avait allongée, chaque case cochée montrait tout le passé
    // remonté, dont le bord gauche, atteint, demandait la page d'avant —
    // 350 bougies d'une heure à l'écran, puis 700, 1 050, 1 350, et 2 100
    // au plein écran, en quatre gestes qui n'étaient pas des dézooms.
    //
    // Un redimensionnement déplace aussi la fenêtre — la bibliothèque
    // garde l'espacement des barres et le bord droit, et découvre le
    // passé à gauche — sans que l'utilisateur ait rien regardé de neuf :
    // un changement qui arrive avec une nouvelle largeur d'axe n'est donc
    // pas retenu.
    //
    // Les rangs se comptent depuis la fin de la série : le plein écran
    // découvre le bord gauche, qui demande la page d'avant, et une page
    // posée devant décale tous les rangs — rendue en rangs absolus, la
    // fenêtre retombait 350 bougies trop tôt. La distance à la dernière
    // bougie, elle, ne bouge pas quand le passé s'allonge.
    function barCount() {
        return state.packet ? state.packet.bars.length : 0;
    }

    function trackView(range) {
        if (!state.chart || !range) { return; }
        var width = state.chart.timeScale().width();
        if (state.viewWidth && width !== state.viewWidth) {
            state.viewWidth = width;
            return;
        }
        state.viewWidth = width;
        var n = barCount();
        state.view = {from: range.from - n, to: range.to - n};
    }

    // Rend la fenêtre mise de côté ; sans elle, toute la série.
    function restoreView(view) {
        if (!state.chart) { return; }
        if (view) {
            var n = barCount();
            state.chart.timeScale().setVisibleLogicalRange(
                {from: view.from + n, to: view.to + n});
        } else {
            state.chart.timeScale().fitContent();
        }
    }

    // Vue entière : toute la série en largeur, l'axe des prix rendu à
    // l'auto-ajustement — ce que le zoom à la boîte lui avait retiré.
    function resetView() {
        if (!state.chart || !state.series) { return; }
        state.series.candles.priceScale().setAutoScale(true);
        state.chart.timeScale().fitContent();
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
              + "&from=" + Math.floor(clock().actual(range.from))
              + "&to=" + Math.ceil(clock().actual(range.to)))
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

    // ── Niveaux clés ────────────────────────────────────────
    //
    // Plus hauts et plus bas de la veille, de la semaine et du mois passés,
    // ouvertures en cours : des lignes de prix grises, de plus en plus
    // claires de la journée au mois — le violet, l'orange et le bleu sont
    // déjà aux moyennes et à Bollinger. L'étiquette d'axe dit lequel ; les
    // ouvertures sont pointillées. Relus toutes les cinq minutes : le jour
    // qui tourne les change. Les prix arrivent en dollars, la devise
    // affichée les convertit comme le POC.
    // Les gris des niveaux clés, et l'ambre des niveaux des options
    // (gamma flip, max pain) : ni le jaune du POC, ni les moyennes.
    var LEVEL_COLORS = {day: "#64748b", week: "#94a3b8", month: "#cbd5e1",
                        options: "#f59e0b"};
    var LEVELS_EVERY = 300000;

    function buildLevels() {
        fetchLevels();
        state.levelsTimer = setInterval(fetchLevels, LEVELS_EVERY);
    }

    function fetchLevels() {
        fetch("/api/levels")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!state.chart || !state.levelsTimer) { return; }
                state.levels = data.levels || [];
                updateLevelLines();
            })
            .catch(function () { /* le tour suivant retentera */ });
    }

    function updateLevelLines() {
        var series = state.series;
        if (!series) { return; }
        (state.levelLines || []).forEach(function (line) {
            series.candles.removePriceLine(line);
        });
        state.levelLines = [];
        if (!state.levelsTimer) { return; }
        var rate = (state.cfg.currency === "EUR" && state.packet)
            ? state.packet.eur_rate : 1;
        var LS = LightweightCharts.LineStyle;
        (state.levels || []).forEach(function (level) {
            state.levelLines.push(series.candles.createPriceLine({
                price: level.price * rate,
                color: LEVEL_COLORS[level.span] || LEVEL_COLORS.day,
                lineWidth: 1,
                lineStyle: level.kind === "open" ? LS.Dotted
                    : level.kind === "flip" ? LS.LargeDashed
                    : level.kind === "move" ? LS.SparseDotted : LS.Dashed,
                axisLabelVisible: true,
                title: level.label
            }));
        });
    }

    // ── Carte des liquidations ──────────────────────────────
    //
    // Le notionnel liquidé par tranche de prix sur la plage visible, lu
    // dans le journal (/api/liqmap) : des barres à gauche du pane — le
    // profil de volume tient la droite —, rouge pour les longs fermés de
    // force (des ventes), vert pour les shorts (des achats). Redemandée
    // quand la plage bouge, et chaque minute pour les liquidations neuves.
    // La légende du bas dit le total et les plateformes présentes : la
    // couverture se voit.
    var LIQ_EVERY = 60000;

    function buildLiqMap() {
        var canvas = document.createElement("canvas");
        canvas.className = "lwc-profile";
        state.el.appendChild(canvas);
        state.liqCanvas = canvas;
        state.liqData = null;
        state.liqTimer = null;
        state.chart.timeScale().subscribeVisibleTimeRangeChange(
            function () {
                drawLiqMap();
                scheduleLiqMap();
            });
        state.liqPoll = setInterval(fetchLiqMap, LIQ_EVERY);
    }

    function scheduleLiqMap() {
        if (!state.liqCanvas) { return; }
        if (state.liqTimer) { clearTimeout(state.liqTimer); }
        state.liqTimer = setTimeout(fetchLiqMap, 300);
    }

    function fetchLiqMap() {
        if (!state.chart || !state.packet || !state.liqCanvas) { return; }
        var range = state.chart.timeScale().getVisibleRange();
        if (!range) { return; }
        // La plage visible s'arrête à l'ouverture de la dernière bougie ;
        // ses liquidations courent jusqu'à sa clôture — jusqu'à maintenant
        // pour la bougie en cours.
        var bars = state.packet.bars;
        var n = bars.length;
        var step = n > 1 ? bars[n - 1].time - bars[n - 2].time : 0;
        var from = clock().actual(range.from);
        var until = clock().actual(range.to);
        var to = Math.ceil(until) + step;
        if (n && until >= bars[n - 1].time) {
            to = Math.max(to, Math.ceil(Date.now() / 1000));
        }
        var seq = state.seq;
        fetch("/api/liqmap?from=" + Math.floor(from) + "&to=" + to)
            .then(function (r) { return r.json(); })
            .then(function (carte) {
                if (seq !== state.seq || !state.liqCanvas) { return; }
                state.liqData = carte;
                drawLiqMap();
            })
            .catch(function () { /* le prochain mouvement retentera */ });
    }

    function drawLiqMap() {
        var canvas = state.liqCanvas;
        if (!canvas || !state.chart || !state.series) { return; }
        var paneHeight = state.chart.panes()[0].getHeight();
        var scaleWidth = state.chart.priceScale("right").width();
        var paneWidth = state.el.clientWidth - scaleWidth;
        canvas.width = paneWidth;
        canvas.height = paneHeight;
        var ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, paneWidth, paneHeight);
        var carte = state.liqData;
        if (!carte) { return; }
        ctx.font = "9px " + state.conf.mono;
        if (carte.empty) {
            liqCaption(ctx, "liquidations : " + (carte.reason || "aucune"));
            return;
        }
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        var max = 0;
        for (var k = 0; k < carte.centers.length; k += 1) {
            max = Math.max(max, carte.longs[k] + carte.shorts[k]);
        }
        if (!(max > 0)) { return; }
        var maxLen = Math.round(paneWidth * 0.18);
        var candles = state.series.candles;
        for (var i = 0; i < carte.centers.length; i += 1) {
            var total = carte.longs[i] + carte.shorts[i];
            if (!(total > 0)) { continue; }
            var top = candles.priceToCoordinate(
                (carte.centers[i] + carte.width / 2) * rate);
            var bottom = candles.priceToCoordinate(
                (carte.centers[i] - carte.width / 2) * rate);
            if (top === null || bottom === null) { continue; }
            var y = Math.min(top, bottom);
            var h = Math.max(2, Math.abs(bottom - top) - 1);
            if (y + h < 0 || y > paneHeight) { continue; }
            var len = Math.max(1, Math.round(total / max * maxLen));
            var longLen = Math.round(len * carte.longs[i] / total);
            ctx.fillStyle = "rgba(255,61,107,0.55)";
            ctx.fillRect(0, y, longLen, h);
            ctx.fillStyle = "rgba(0,212,170,0.55)";
            ctx.fillRect(longLen, y, len - longLen, h);
        }
        liqCaption(ctx, "liquidations " + (carte.notional / 1e6).toFixed(1)
                   + " M$ · " + carte.count + " · "
                   + carte.exchanges.join(", ")
                   + " · rouge : longs, vert : shorts");
    }

    // La légende de la carte, sous celle des moyennes : en bas du pane, le
    // volume la recouvrait. Un fond la garde lisible sur les chandeliers.
    // `row` empile les légendes des couches : la carte, puis les ordres.
    function liqCaption(ctx, text, row) {
        var top = 34 + 14 * (row || 0);
        var width = ctx.measureText(text).width;
        ctx.fillStyle = "rgba(14,17,23,0.8)";
        ctx.fillRect(4, top, width + 6, 13);
        ctx.fillStyle = "#94a3b8";
        ctx.fillText(text, 7, top + 10);
    }

    // ── Gros ordres ─────────────────────────────────────────
    //
    // Les ordres de 100 k$ et plus que le ruban a journalisés (§ tape), en
    // bulles sur les chandeliers : une par bougie et par côté, à leur prix
    // moyen pondéré, d'une surface proportionnelle au montant — vert pour
    // les achats au marché, rouge pour les ventes. Ce que le carnet ne dit
    // pas : où les gros ont réellement traité, et dans quel sens. Le
    // serveur somme par tranche d'une bougie, au plus le jour
    // (/api/trades) ; les semaines et les mois regroupent ici leurs jours.
    // Les bulles les plus grosses sont posées d'abord : les petites
    // restent visibles par-dessus.
    var TRADES_EVERY = 60000;
    var TRADES_RADIUS = 14;

    function buildTrades() {
        var canvas = document.createElement("canvas");
        canvas.className = "lwc-profile";
        state.el.appendChild(canvas);
        state.tradesCanvas = canvas;
        state.tradesData = null;
        state.tradesTimer = null;
        state.tradesShown = 0;
        state.chart.timeScale().subscribeVisibleTimeRangeChange(
            function () {
                drawTrades();
                scheduleTrades();
            });
        state.tradesPoll = setInterval(fetchTrades, TRADES_EVERY);
    }

    function scheduleTrades() {
        if (!state.tradesCanvas) { return; }
        if (state.tradesTimer) { clearTimeout(state.tradesTimer); }
        state.tradesTimer = setTimeout(fetchTrades, 300);
    }

    function fetchTrades() {
        if (!state.chart || !state.packet || !state.tradesCanvas) { return; }
        var range = state.chart.timeScale().getVisibleRange();
        if (!range) { return; }
        // Comme la carte : la dernière bougie court jusqu'à sa clôture,
        // jusqu'à maintenant pour celle en cours.
        var bars = state.packet.bars;
        var n = bars.length;
        var step = n > 1 ? bars[n - 1].time - bars[n - 2].time : 86400;
        var from = clock().actual(range.from);
        var until = clock().actual(range.to);
        var to = Math.ceil(until) + step;
        if (n && until >= bars[n - 1].time) {
            to = Math.max(to, Math.ceil(Date.now() / 1000));
        }
        var seq = state.seq;
        fetch("/api/trades?from=" + Math.floor(from) + "&to=" + to
              + "&step=" + Math.max(60, Math.min(step, 86400)))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (seq !== state.seq || !state.tradesCanvas) { return; }
                state.tradesData = data;
                drawTrades();
            })
            .catch(function () { /* le prochain mouvement retentera */ });
    }

    // Les tranches du serveur rangées dans les bougies affichées : une
    // entrée par bougie et par côté, notionnel sommé, prix moyen pondéré.
    // `barIndexAt` (§ tracés) donne la bougie qui contient chaque tranche.
    function tradeGroups(buckets) {
        var groups = {};
        (buckets || []).forEach(function (b) {
            var i = barIndexAt(b[0]);
            if (i < 0) { return; }
            var key = i + ":" + b[1];
            var g = groups[key];
            if (!g) {
                g = groups[key] = {index: i, side: b[1], notional: 0,
                                   weighted: 0, count: 0};
            }
            g.notional += b[2];
            g.weighted += b[3] * b[2];
            g.count += b[4];
        });
        return Object.keys(groups).map(function (key) {
            var g = groups[key];
            g.price = g.weighted / g.notional;
            return g;
        });
    }

    function drawTrades() {
        var canvas = state.tradesCanvas;
        if (!canvas || !state.chart || !state.series || !state.packet) {
            return;
        }
        var paneHeight = state.chart.panes()[0].getHeight();
        var scaleWidth = state.chart.priceScale("right").width();
        var paneWidth = state.el.clientWidth - scaleWidth;
        canvas.width = paneWidth;
        canvas.height = paneHeight;
        var ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, paneWidth, paneHeight);
        state.tradesShown = 0;
        var data = state.tradesData;
        if (!data) { return; }
        ctx.font = "9px " + state.conf.mono;
        var row = state.liqCanvas ? 1 : 0;
        if (data.empty) {
            liqCaption(ctx, "gros ordres : " + (data.reason || "aucun"), row);
            return;
        }
        var bars = state.packet.bars;
        var groups = tradeGroups(data.buckets);
        var max = 0;
        groups.forEach(function (g) { max = Math.max(max, g.notional); });
        if (!(max > 0)) { return; }
        groups.sort(function (a, b) { return b.notional - a.notional; });
        var theme = state.conf.theme;
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        var ts = state.chart.timeScale();
        var candles = state.series.candles;
        var at = clock();
        groups.forEach(function (g) {
            var x = ts.timeToCoordinate(at.shown(bars[g.index].time));
            var y = candles.priceToCoordinate(g.price * rate);
            if (x === null || y === null || x < 0 || x > paneWidth
                    || y < 0 || y > paneHeight) {
                return;
            }
            var r = Math.max(2.5, TRADES_RADIUS * Math.sqrt(g.notional / max));
            var buy = g.side === "buy";
            // Achats et ventes d'une même bougie tombent souvent au même
            // prix : centrées, leurs couleurs se mêlaient en un gris. Les
            // achats s'écartent à gauche, les ventes à droite.
            var cx = x + (buy ? -1 : 1) * r * 0.4;
            ctx.beginPath();
            ctx.arc(cx, y, r, 0, 2 * Math.PI);
            ctx.fillStyle = buy ? "rgba(0,212,170,0.42)"
                                : "rgba(255,61,107,0.42)";
            ctx.fill();
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = buy ? theme.green : theme.red;
            ctx.stroke();
            state.tradesShown += 1;
        });
        liqCaption(ctx, "gros ordres achats " + (data.buy / 1e6).toFixed(1)
                   + " M$ · ventes " + (data.sell / 1e6).toFixed(1)
                   + " M$ · " + data.count + " · "
                   + data.exchanges.join(", ")
                   + " · vert : achats, rouge : ventes", row);
    }

    // ── Carte de chaleur du carnet ──────────────────────────
    //
    // Les carnets des cinq plateformes, photographiés toutes les dix
    // secondes et additionnés par tranche de prix (§ heatmap), peints sous
    // les chandeliers : chaque colonne une bougie, chaque ligne une
    // tranche, l'intensité la quantité posée — du bleu sombre au jaune et
    // au blanc. Les murs qui tiennent tracent des bandes ; ceux qui
    // disparaissent avant d'être touchés s'arrêtent net. Le serveur rend
    // une matrice déjà quantifiée (/api/heatmap) ; on n'y fait que ranger
    // ses colonnes dans les bougies (`barIndexAt`) et poser une image,
    // agrandie sans lissage, entre les bougies et les prix qu'elle couvre.
    var HEAT_EVERY = 60000;
    var HEAT_STOPS = [[0.0, 10, 30, 80, 0], [0.12, 20, 60, 150, 70],
                      [0.4, 0, 150, 200, 140], [0.7, 255, 205, 0, 190],
                      [1.0, 255, 255, 255, 230]];
    var heatLut = null;

    // La palette, une fois : 256 couleurs RGBA interpolées entre les arrêts.
    function heatPalette() {
        if (heatLut) { return heatLut; }
        heatLut = new Uint8ClampedArray(256 * 4);
        for (var level = 0; level < 256; level += 1) {
            var x = level / 255;
            var k = 1;
            while (k < HEAT_STOPS.length - 1 && HEAT_STOPS[k][0] < x) {
                k += 1;
            }
            var a = HEAT_STOPS[k - 1];
            var b = HEAT_STOPS[k];
            var f = (x - a[0]) / (b[0] - a[0]);
            for (var c = 0; c < 4; c += 1) {
                heatLut[level * 4 + c] = Math.round(a[c + 1]
                                                    + (b[c + 1] - a[c + 1]) * f);
            }
        }
        heatLut[3] = 0;
        return heatLut;
    }

    function buildHeat() {
        var canvas = document.createElement("canvas");
        canvas.className = "lwc-heat";
        state.el.insertBefore(canvas, state.el.firstChild);
        state.el.classList.add("lwc-heat-on");
        state.heatCanvas = canvas;
        state.heatData = null;
        state.heatImage = null;
        state.heatTimer = null;
        state.chart.timeScale().subscribeVisibleTimeRangeChange(
            function () {
                drawHeat();
                scheduleHeat();
            });
        state.heatPoll = setInterval(fetchHeat, HEAT_EVERY);
    }

    function scheduleHeat() {
        if (!state.heatCanvas) { return; }
        if (state.heatTimer) { clearTimeout(state.heatTimer); }
        state.heatTimer = setTimeout(fetchHeat, 400);
    }

    function fetchHeat() {
        if (!state.chart || !state.packet || !state.heatCanvas) { return; }
        var range = state.chart.timeScale().getVisibleRange();
        if (!range) { return; }
        var bars = state.packet.bars;
        var n = bars.length;
        var step = n > 1 ? bars[n - 1].time - bars[n - 2].time : 86400;
        var from = clock().actual(range.from);
        var until = clock().actual(range.to);
        var to = Math.ceil(until) + step;
        if (n && until >= bars[n - 1].time) {
            to = Math.max(to, Math.ceil(Date.now() / 1000));
        }
        var seq = state.seq;
        fetch("/api/heatmap?from=" + Math.floor(from) + "&to=" + to
              + "&step=" + Math.max(60, Math.min(step, 86400)))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (seq !== state.seq || !state.heatCanvas) { return; }
                state.heatData = data;
                state.heatImage = data.empty ? null : heatImage(data);
                drawHeat();
            })
            .catch(function () { /* le prochain mouvement retentera */ });
    }

    // La matrice en image : une colonne par bougie couverte, une ligne par
    // tranche, le prix le plus haut en haut. Plusieurs tranches de temps
    // dans une bougie (la semaine, le mois) : la plus intense l'emporte.
    function heatImage(data) {
        var bytes = atob(data.data);
        var rows = data.rows;
        var first = -1;
        var last = -1;
        var columns = data.times.map(function (t) {
            var index = barIndexAt(t);
            if (index >= 0) {
                if (first < 0 || index < first) { first = index; }
                if (index > last) { last = index; }
            }
            return index;
        });
        if (first < 0) { return null; }
        var width = last - first + 1;
        var canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = rows;
        var ctx = canvas.getContext("2d");
        var image = ctx.createImageData(width, rows);
        var levels = new Uint8Array(width * rows);
        columns.forEach(function (index, t) {
            if (index < 0) { return; }
            var x = index - first;
            for (var r = 0; r < rows; r += 1) {
                var level = bytes.charCodeAt(t * rows + r);
                var at = (rows - 1 - r) * width + x;
                if (level > levels[at]) { levels[at] = level; }
            }
        });
        var lut = heatPalette();
        for (var i = 0; i < levels.length; i += 1) {
            var q = levels[i] * 4;
            image.data[i * 4] = lut[q];
            image.data[i * 4 + 1] = lut[q + 1];
            image.data[i * 4 + 2] = lut[q + 2];
            image.data[i * 4 + 3] = lut[q + 3];
        }
        ctx.putImageData(image, 0, 0);
        return {canvas: canvas, first: first, last: last};
    }

    function drawHeat() {
        var canvas = state.heatCanvas;
        if (!canvas || !state.chart || !state.series || !state.packet) {
            return;
        }
        var paneHeight = state.chart.panes()[0].getHeight();
        var scaleWidth = state.chart.priceScale("right").width();
        var paneWidth = state.el.clientWidth - scaleWidth;
        canvas.width = paneWidth;
        canvas.height = paneHeight;
        var ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, paneWidth, paneHeight);
        var data = state.heatData;
        var picture = state.heatImage;
        if (!data || data.empty || !picture) { return; }
        var ts = state.chart.timeScale();
        var candles = state.series.candles;
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        // Les bougies sont régulières en rang : la première et la dernière
        // colonne bornent l'image, d'une demi-bougie de part et d'autre.
        // Par leurs temps (`timeToCoordinate`, comme les bulles des gros
        // ordres) : `logicalToCoordinate` rendait 0 pour un rang
        // fractionnaire, et l'image ne se posait nulle part.
        var bars = state.packet.bars;
        var at = clock();
        var half = ts.options().barSpacing / 2;
        var firstX = ts.timeToCoordinate(at.shown(bars[picture.first].time));
        var lastX = ts.timeToCoordinate(at.shown(bars[picture.last].time));
        var left = firstX === null ? null : firstX - half;
        var right = lastX === null ? null : lastX + half;
        var top = candles.priceToCoordinate((data.low + data.rows * data.step)
                                            * rate);
        var bottom = candles.priceToCoordinate(data.low * rate);
        state.heatBox = {left: left, right: right, top: top, bottom: bottom,
                         painted: false};
        if (left === null || right === null || top === null
                || bottom === null || right <= left || bottom <= top) {
            return;
        }
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(picture.canvas, left, top, right - left, bottom - top);
        state.heatBox.painted = true;
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

    // Alt + clic : pose un seuil à la hauteur du clic, ou retire celui qui
    // s'y trouve déjà. Le geste écrit dans le Store alert-config, comme le
    // panneau ALERTES : le moteur, la liste et les lignes suivent d'eux-mêmes.
    // Le sens est figé à la pose, comme là-bas, par rapport au dernier cours.
    function toggleAlertAt(y) {
        var candles = state.series.candles;
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        var bars = state.packet.bars;
        var levels = (state.alertLevels || []).slice();
        var hit = levels.findIndex(function (item) {
            var at = candles.priceToCoordinate(item.level * rate);
            return at !== null && Math.abs(at - y) <= ALERT_HIT_PX;
        });
        if (hit !== -1) {
            levels.splice(hit, 1);
        } else {
            var price = candles.coordinateToPrice(y);
            if (price === null || !bars.length) { return; }
            var level = Math.round(price / rate);
            levels.push({level: level,
                         dir: level >= bars[bars.length - 1].close
                             ? "above" : "below"});
        }
        writeAlertLevels(levels);
    }

    function writeAlertLevels(levels) {
        window.dash_clientside.set_props("alert-config", {
            data: Object.assign({}, state.alertConfig,
                                {price_levels: levels})
        });
    }

    function buildBanner() {
        var theme = state.conf.theme;
        var div = document.createElement("div");
        div.className = "lwc-demo-banner";
        div.style.background = theme.orange;
        div.style.color = theme.bg;
        div.style.display = "none";
        state.el.appendChild(div);
        state.banner = div;
    }

    // Le bandeau d'avertissement dit ce que le paquet dit : données de
    // démonstration, historique sauvegardé servi seul, ou — en euros
    // seulement — taux de change de secours. La démonstration prime : un
    // taux juste sur des bougies inventées n'apprendrait rien.
    function updateBanner() {
        var packet = state.packet;
        if (!state.banner || !packet) { return; }
        var text = packet.demo
            ? "⚠ DONNÉES DE DÉMONSTRATION · source injoignable"
            : packet.stale
                ? "⚠ HISTORIQUE SAUVEGARDÉ · source injoignable, cours à l'arrêt"
                : (state.cfg.currency === "EUR" && packet.eur_stale)
                    ? "⚠ TAUX € DE SECOURS · source de change injoignable"
                    : "";
        state.banner.textContent = text;
        state.banner.style.display = text ? "block" : "none";
    }

    function teardown() {
        zoomStop();
        if (state.countdownTimer) {
            clearInterval(state.countdownTimer);
            state.countdownTimer = null;
        }
        state.countdownText = null;
        if (state.el) {
            state.el.removeEventListener("mousedown", zoomStart, true);
        }
        state.zoomBox = null;
        if (state.chart) { state.chart.remove(); }
        state.chart = null;
        state.series = null;
        state.legend = null;
        state.readout = null;
        state.measureLabel = null;
        state.drawPreview = null;
        state.drawHint = null;
        state.drawLines = [];
        state.drawMode = false;
        state.anchorHint = null;
        state.anchorLines = [];
        state.anchorLegend = null;
        state.anchorMode = false;
        state.hover = null;
        state.banner = null;
        state.loading = false;
        state.exhausted = false;
        stopBackfillRetry();
        state.profileCanvas = null;
        state.profileData = null;
        state.profileLines = [];
        if (state.levelsTimer) {
            clearInterval(state.levelsTimer);
            state.levelsTimer = null;
        }
        if (state.oiPoll) {
            clearInterval(state.oiPoll);
            state.oiPoll = null;
        }
        state.oiData = null;
        state.levels = null;
        state.levelLines = [];
        if (state.liqPoll) {
            clearInterval(state.liqPoll);
            state.liqPoll = null;
        }
        if (state.liqTimer) {
            clearTimeout(state.liqTimer);
            state.liqTimer = null;
        }
        state.liqCanvas = null;
        state.liqData = null;
        if (state.tradesPoll) {
            clearInterval(state.tradesPoll);
            state.tradesPoll = null;
        }
        if (state.tradesTimer) {
            clearTimeout(state.tradesTimer);
            state.tradesTimer = null;
        }
        state.tradesCanvas = null;
        state.tradesData = null;
        if (state.heatPoll) {
            clearInterval(state.heatPoll);
            state.heatPoll = null;
        }
        if (state.heatTimer) {
            clearTimeout(state.heatTimer);
            state.heatTimer = null;
        }
        state.heatCanvas = null;
        state.heatData = null;
        state.heatImage = null;
        if (state.el) { state.el.classList.remove("lwc-heat-on"); }
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

    // ── VWAP ancré ──────────────────────────────────────────
    //
    // Le calcul vit dans lwc-vwap.js, testé sous Node : ici, la mise en
    // séries. Le dernier point de chaque période est transparent : la
    // bibliothèque colore un segment d'après le point où il commence, et
    // celui qui part de là relierait la fin de la période au début de la
    // suivante — un trait vertical que rien ne justifie. Rendre transparent
    // le premier point de la période, essayé d'abord, cachait le premier
    // segment utile et laissait le saut.
    var VWAP_COLOR = "#ec4899";

    function vwapSeries(rate) {
        var vwap = window.btcVwap;
        var anchor = vwap ? vwap.anchorOf(state.cfg.interval) : null;
        var at = clock();
        var lines = vwap ? vwap.compute(state.packet.bars, state.packet.volume,
                                        anchor)
                         : {vwap: [], upper: [], lower: []};
        function shown(points) {
            return points.map(function (p, i) {
                var point = {time: at.shown(p.time), value: p.value * rate};
                var next = points[i + 1];
                if (next && next.start) { point.color = "rgba(0,0,0,0)"; }
                return point;
            });
        }
        return {anchor: anchor, vwap: shown(lines.vwap),
                upper: shown(lines.upper), lower: shown(lines.lower)};
    }

    function fillVwap() {
        var series = state.series;
        if (!series || !series.vwap) { return; }
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        var lines = vwapSeries(rate);
        series.vwap.setData(lines.vwap);
        series.vwapUpper.setData(lines.upper);
        series.vwapLower.setData(lines.lower);
        state.vwapAnchor = lines.anchor;
        if (state.vwapLegend) {
            state.vwapLegend.textContent = lines.anchor
                ? "— VWAP " + window.btcVwap.label(lines.anchor) : "";
        }
    }

    // La bougie en cours bouge : le VWAP de sa période aussi. Le calcul
    // repart du début des bougies chargées — quelques milliers d'additions,
    // rien à côté d'un rendu — et seuls les derniers points sont posés.
    function updateVwapTail() {
        var series = state.series;
        if (!series || !series.vwap) { return; }
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        var lines = vwapSeries(rate);
        [["vwap", lines.vwap], ["vwapUpper", lines.upper],
         ["vwapLower", lines.lower]].forEach(function (pair) {
            var points = pair[1];
            if (points.length) {
                series[pair[0]].update(points[points.length - 1]);
            }
        });
    }

    // ── Open interest ───────────────────────────────────────
    //
    // Les positions ouvertes sur le perpétuel de Binance, en dollars, au
    // pas le plus proche de l'intervalle (/api/oi : cinq minutes pour la
    // minute, le jour pour la semaine et le mois), prolongées vers le passé
    // par le journal. Chaque relevé se range dans la bougie qui le contient
    // (`barIndexAt`), le dernier l'emportant : la valeur à la clôture de la
    // bougie. Ce que le cours seul ne dit pas — une hausse portée par des
    // positions neuves ou par des shorts qui rachètent, une baisse qui
    // purge le levier ou qui en ajoute.
    var OI_EVERY = 60000;

    function billions(value) {
        return (value / 1e9).toFixed(2) + " Md";
    }

    function fetchOi() {
        if (!state.chart || !state.packet || !state.series
                || !state.series.oi || !state.packet.bars.length) {
            return;
        }
        var interval = state.cfg.interval;
        var from = state.packet.bars[0].time;
        var seq = state.seq;
        fetch("/api/oi?interval=" + encodeURIComponent(interval)
              + "&from=" + from)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (seq !== state.seq || !state.series || !state.series.oi
                        || data.interval !== state.cfg.interval) {
                    return;
                }
                data.from = from;
                state.oiData = data;
                fillOi();
                // La lecture de la dernière bougie a pu partir sans lui.
                updateReadout();
            })
            .catch(function () { /* le prochain tour retentera */ });
    }

    function fillOi() {
        var series = state.series;
        if (!series || !series.oi) { return; }
        var data = state.oiData;
        var bars = state.packet.bars;
        // Une autre série, ou un passé remonté au pan que la dernière
        // lecture ne couvrait pas : on redemande, sans attendre le tour.
        if (!data || data.interval !== state.cfg.interval
                || (bars.length && bars[0].time < data.from)) {
            fetchOi();
            if (!data || data.interval !== state.cfg.interval) {
                series.oi.setData([]);
                return;
            }
        }
        var rate = state.cfg.currency === "EUR" ? state.packet.eur_rate : 1;
        var at = clock();
        var points = [];
        var lastIndex = -1;
        (data.points || []).forEach(function (p) {
            if (p[1] === null) { return; }
            var index = barIndexAt(p[0]);
            if (index < 0) { return; }
            var point = {time: at.shown(bars[index].time), value: p[1] * rate};
            if (index === lastIndex) {
                points[points.length - 1] = point;
            } else {
                points.push(point);
                lastIndex = index;
            }
        });
        series.oi.setData(points);
    }

    // Le delta de chaque bougie, cumulé depuis le début de la série chargée.
    // Seule la forme se lit : le zéro bouge à chaque page d'historique, et
    // la courbe se décale d'autant sans changer de dessin.
    function cvdPoints() {
        var total = 0;
        return (state.packet.panes.delta || []).map(function (p) {
            total += p.value;
            return {time: p.time, value: total};
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
        rebuildClock();
        var at = clock();

        series.candles.setData(at.points(scaled(packet.bars, rate)));
        for (var name in series.lines) {
            series.lines[name].setData(
                at.points(scaled(packet.overlays[name] || [], rate)));
        }
        if (series.volume) {
            series.volume.setData(packet.volume.map(function (p) {
                return {time: at.shown(p.time), value: p.value,
                        color: p.up ? theme.green : theme.red};
            }));
            series.volMa.setData(at.points(packet.volume_ma));
        }
        if (series.rsi) { series.rsi.setData(at.points(packet.panes.rsi || [])); }
        if (series.crsi) {
            series.crsi.setData(at.points(packet.panes.crsi || []));
        }
        if (series.cvd) { series.cvd.setData(at.points(cvdPoints())); }
        if (series.oi) { fillOi(); }
        if (series.vwap) { fillVwap(); }
        applyMarkers();
        renderDrawings();
        renderAnchors();

        // Le survol retient des valeurs à l'ancien taux.
        state.hover = null;
        updateReadout();
        updateBanner();
        // Un autre intervalle, un autre rebours : sans attendre la seconde.
        updateCountdown();
        applyLog();

        if (state.profileCanvas) {
            updateProfileLines();
            drawProfile();
            scheduleProfile();
        }
        if (state.liqCanvas) {
            drawLiqMap();
            scheduleLiqMap();
        }
        if (state.tradesCanvas) {
            drawTrades();
            scheduleTrades();
        }
        if (state.heatCanvas) {
            // Les colonnes se rangent dans des bougies qui ont pu changer
            // (page d'historique, autre série) : l'image se refait.
            state.heatImage = state.heatData && !state.heatData.empty
                ? heatImage(state.heatData) : null;
            drawHeat();
            scheduleHeat();
        }
        // Les lignes portent des prix en unités de série : la bascule
        // de devise les reprend au taux.
        updateAlertLines();
        updateLevelLines();
    }

    // Traduit les signaux gradués du serveur (-2 à +2) en flèches :
    // achat sous la bougie, vente au-dessus, grande taille pour les
    // signaux forts — la grammaire du panneau Plotly.
    function markersFrom(signals) {
        var theme = state.conf.theme;
        if (state.signalsHidden) { return []; }
        return signals.map(function (s) {
            return {
                time: clock().shown(s.time),
                position: s.value > 0 ? "belowBar" : "aboveBar",
                shape: s.value > 0 ? "arrowUp" : "arrowDown",
                color: s.value === 2 ? theme.buy
                    : s.value === 1 ? theme.green
                        : s.value === -1 ? theme.red : theme.sell,
                size: Math.abs(s.value) === 2 ? 2 : 1
            };
        });
    }

    // ── Sonneries ───────────────────────────────────────────
    //
    // Ce que les alertes ont dit, à l'endroit du graphique où elles l'ont
    // dit : un rond au-dessus de la bougie, de la couleur de la règle au
    // panneau ALERTES, et au survol la sonnerie en clair dans la ligne de
    // lecture. Relire une séance demandait de croiser la liste horodatée
    // du panneau et le graphique ; le graphique la porte désormais. Les
    // sonneries viennent du journal (/api/alert-events), pour la plage
    // chargée, et se relisent à chaque sonnerie nouvelle (§ panels/alerts).
    var EVENTS_TEXT_MAX = 90;

    function fetchEvents() {
        if (!state.chart || !state.packet || !state.packet.bars.length) {
            return;
        }
        var from = Math.floor(state.packet.bars[0].time);
        var to = Math.ceil(Date.now() / 1000) + 60;
        var seq = state.seq;
        fetch("/api/alert-events?from=" + from + "&to=" + to)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (seq !== state.seq || !state.series) { return; }
                state.events = data.events || [];
                applyMarkers();
                updateReadout();
            })
            .catch(function () { /* la prochaine sonnerie retentera */ });
    }

    // Une marque par bougie : la couleur de la dernière sonnerie, le détail
    // au survol. Avant la première bougie chargée, rien ; après la dernière,
    // la bougie en cours.
    function eventMarkers() {
        var byBar = {};
        state.eventsByBar = byBar;
        if (state.eventsHidden || !state.packet) { return []; }
        var bars = state.packet.bars;
        state.events.forEach(function (event) {
            var i = barIndexAt(event[0]);
            if (i < 0) { return; }
            (byBar[i] = byBar[i] || []).push(event);
        });
        return Object.keys(byBar).map(function (i) {
            var list = byBar[i];
            return {time: clock().shown(bars[i].time), position: "aboveBar",
                    shape: "circle", size: 1,
                    color: list[list.length - 1][3]};
        });
    }

    // Flèches des signaux et ronds des sonneries partagent la couche de
    // marqueurs de la série : la bibliothèque les veut rangés dans le temps.
    function applyMarkers() {
        if (!state.series || !state.packet) { return; }
        var markers = markersFrom(state.packet.signals || [])
            .concat(eventMarkers());
        markers.sort(function (a, b) { return a.time - b.time; });
        state.series.markers.setMarkers(markers);
    }

    function lastEventX() {
        var keys = Object.keys(state.eventsByBar).map(Number);
        if (!keys.length || !state.chart) { return null; }
        var last = Math.max.apply(null, keys);
        return state.chart.timeScale().timeToCoordinate(
            clock().shown(state.packet.bars[last].time));
    }

    // L'heure d'une sonnerie dans le fuseau du terminal — l'heure locale,
    // ou l'UTC si la case est cochée —, pas dans celui de l'axe : les
    // bougies journalières restent en UTC, et la rafale de 12:00 s'y lisait
    // « 05:00 ». Le jour aussi au-delà de l'intraday, où une bougie couvre
    // plusieurs journées.
    function eventTime(time) {
        var date = new Date(time * 1000);
        var utc = !!state.cfg.utc;
        function two(n) { return (n < 10 ? "0" : "") + n; }
        var hour = two(utc ? date.getUTCHours() : date.getHours()) + ":"
            + two(utc ? date.getUTCMinutes() : date.getMinutes())
            + (utc ? " UTC" : "");
        if (INTRADAY[state.cfg.interval]) { return hour; }
        return two(utc ? date.getUTCDate() : date.getDate()) + "/"
            + two((utc ? date.getUTCMonth() : date.getMonth()) + 1) + " "
            + hour;
    }

    function eventParts(index) {
        var list = state.eventsByBar[index];
        if (!list || !list.length) { return []; }
        var parts = list.slice(-2).reverse().map(function (event) {
            var text = event[2].length > EVENTS_TEXT_MAX
                ? event[2].slice(0, EVENTS_TEXT_MAX - 1) + "…" : event[2];
            return ["⚑ " + eventTime(event[0]) + " " + text, event[3]];
        });
        if (list.length > 2) {
            parts.push(["+" + (list.length - 2) + " sonnerie"
                        + (list.length > 3 ? "s" : ""),
                        state.conf.theme.muted]);
        }
        return parts;
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
                // Recalage silencieux : le passé remonté au pan reste,
                // et avec lui ce qu'on savait de son début.
                var kept = !fit && keepOlder(packet, state.packet);
                state.packet = packet;
                state.loading = false;
                if (!kept) {
                    state.exhausted = false;
                    stopBackfillRetry();
                }
                fillSeries();
                if (fit) { state.chart.timeScale().fitContent(); }
                fetchEvents();
            })
            .catch(function () { /* le prochain réglage retentera */ });
    }

    // Le bord gauche visible approche du début du tampon : demander la
    // page antérieure. Verrou anti-rafale (une requête en vol à la
    // fois) et mémoire du « plus ancien atteint » — une page vide dit
    // que la source n'a plus rien avant, inutile de la marteler. Sauf
    // si elle est vide faute de source (`stale`) : le passé n'est pas
    // fermé, un réessai est programmé.
    function maybeBackfill(range) {
        if (!state.chart || !state.packet || state.loading
                || state.exhausted || state.backfillTimer || !range
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
        var before = bars[0].time;
        state.loading = true;
        fetch("/api/klines?interval=" + encodeURIComponent(interval)
              + "&limit=" + limit + "&before=" + before)
            .then(function (r) { return r.json(); })
            .then(function (page) {
                state.loading = false;
                // La page ne se colle que si le tampon commence toujours
                // là où elle s'arrête. Le jeton seul ne suffisait pas : au
                // changement d'intervalle, le graphique encore à l'ancien
                // demandait une page du nouveau, sous le jeton du refetch
                // déjà parti, et la collait devant la nouvelle série — à
                // des dates qui la chevauchaient. Le recalage complet
                // effaçait le dégât ; celui qui garde le passé l'aurait
                // gardé aussi.
                if (seq !== state.seq || !state.chart
                        || !state.packet.bars.length
                        || state.packet.bars[0].time !== before) {
                    return;
                }
                // La série de démonstration n'a pas de passé à attendre :
                // de vraies bougies n'ont rien à faire devant elle.
                if (!page.bars.length) {
                    if (page.stale && !state.packet.demo) {
                        retryBackfill();
                    } else {
                        state.exhausted = true;
                    }
                    return;
                }
                prepend(state.packet, page);
                // setData du concaténé : la bibliothèque préserve la
                // fenêtre visible, le pan continue sans à-coup.
                fillSeries();
                // Le passé ajouté a peut-être sonné, lui aussi.
                fetchEvents();
            })
            .catch(function () { state.loading = false; });
    }

    // Le réessai part de la fenêtre visible du moment : si l'on a
    // quitté le bord gauche entre-temps, il ne demande rien.
    function retryBackfill() {
        stopBackfillRetry();
        state.backfillTimer = setTimeout(function () {
            state.backfillTimer = null;
            if (state.chart) {
                maybeBackfill(state.chart.timeScale().getVisibleLogicalRange());
            }
        }, BACKFILL_RETRY);
    }

    function stopBackfillRetry() {
        if (state.backfillTimer) {
            clearTimeout(state.backfillTimer);
            state.backfillTimer = null;
        }
    }

    // Recalage silencieux : la page fraîche remplace la fin du tampon, et
    // ce que le pan a remonté reste devant elle. Remplacer tout le tampon,
    // comme au changement d'intervalle, faisait sauter la vue : le
    // graphique garde sa plage par rang de bougie, pas par date, et un
    // retour d'onglet en journalier ramenait de 2023 à 2025 — en repli
    // poll, toutes les deux secondes. Rien n'est gardé si les deux ne se
    // recouvrent pas (la machine a dormi plus longtemps que la page n'en
    // couvre) : mieux vaut perdre le passé remonté qu'y laisser un trou.
    function keepOlder(packet, old) {
        if (!old || old.demo || packet.demo || old.interval !== packet.interval
                || !old.bars.length || !packet.bars.length
                || old.bars[old.bars.length - 1].time < packet.bars[0].time) {
            return false;
        }
        // Chaque série garde l'ancien jusqu'à son propre premier point :
        // la MA 200 d'une page fraîche ne commence que deux cents bougies
        // après elle, et l'ancien tampon a de quoi combler.
        function older(points, fresh) {
            var cut = fresh && fresh.length ? fresh[0].time : Infinity;
            return (points || []).filter(function (p) { return p.time < cut; });
        }
        var page = {bars: older(old.bars, packet.bars),
                    volume: older(old.volume, packet.volume),
                    volume_ma: older(old.volume_ma, packet.volume_ma),
                    overlays: {}, panes: {}};
        var name;
        for (name in old.overlays) {
            page.overlays[name] = older(old.overlays[name],
                                        packet.overlays[name]);
        }
        for (name in old.panes) {
            page.panes[name] = older(old.panes[name], packet.panes[name]);
        }
        prepend(packet, page);
        return page.bars.length > 0;
    }

    // Le plus court et le plus long écart entre deux bougies affichées :
    // un doublon met le premier à zéro, un trou allonge le second — ce
    // que le smoke test compare autour d'un recalage.
    function steps(points) {
        var min = null, max = null;
        for (var i = 1; i < points.length; i++) {
            var gap = points[i].time - points[i - 1].time;
            if (min === null || gap < min) { min = gap; }
            if (max === null || gap > max) { max = gap; }
        }
        return [min, max];
    }

    function prepend(packet, page) {
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

    state.drawings = loadDrawings();
    state.anchors = loadAnchors();

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
            // Mise de côté avant que teardown n'emporte le graphique.
            var view = state.chart ? state.view : null;

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
            if (rebuilt || previous.currency !== cfg.currency
                    || !previous.utc !== !cfg.utc) {
                fillSeries();
                // Les mêmes données, rangées pareil : la fenêtre se rend
                // telle quelle (§ trackView).
                if (rebuilt) { restoreView(view); }
                return;
            }
            if (previous.log !== cfg.log) {
                applyLog();
                renderDrawings();
            }
            // Le plein écran double la largeur mais l'espacement des
            // barres survit au redimensionnement : sans recadrage, la
            // série resterait tassée contre le bord droit. Les bougies
            // regardées s'élargissent donc à la nouvelle taille — et
            // celles-là seulement, pas tout le passé remonté au pan
            // (§ trackView). autoSize a besoin d'un tour de boucle pour
            // mesurer le div agrandi — d'où le différé.
            if (previous.maximized !== cfg.maximized) {
                setTimeout(function () { restoreView(view); }, 120);
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
            packet.stale = update.stale;

            var series = state.series;
            var theme = state.conf.theme;
            var rate = state.cfg.currency === "EUR" ? packet.eur_rate : 1;
            // La bougie qui clôture s'inscrit à l'horloge, à la suite des
            // autres : les points qui suivent la retrouvent.
            var at = clock();
            at.add(update.bar.time);
            function one(point) { return at.points([point])[0]; }
            series.candles.update(one(scaled([update.bar], rate)[0]));
            if (series.vwap) { updateVwapTail(); }
            updateAnchorTails();
            for (name in update.overlays) {
                if (series.lines[name]) {
                    series.lines[name].update(
                        one(scaled([update.overlays[name]], rate)[0]));
                }
            }
            if (series.volume && update.volume) {
                series.volume.update({
                    time: at.shown(update.volume.time),
                    value: update.volume.value,
                    color: update.volume.up ? theme.green : theme.red
                });
                if (update.volume_ma) {
                    series.volMa.update(one(update.volume_ma));
                }
            }
            if (series.rsi && update.panes.rsi) {
                series.rsi.update(one(update.panes.rsi));
            }
            if (series.crsi && update.panes.crsi) {
                series.crsi.update(one(update.panes.crsi));
            }
            if (series.cvd && update.panes.delta) {
                var cvd = cvdPoints();
                series.cvd.update(one(cvd[cvd.length - 1]));
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
                applyMarkers();
            } else if (update.signal) {
                signals.push({time: update.bar.time, value: update.signal});
                applyMarkers();
            }

            updateBanner();
            updateReadout();
            // Une bougie de plus : les lignes se prolongent d'autant.
            if (delta > 0) { renderDrawings(); }
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
        alerts: function (levels, config) {
            state.alertConfig = config || null;
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
                // L'intervalle de la série affichée : `interval` dit celui
                // demandé, la série ne suit qu'un aller-retour plus tard.
                loaded: state.packet.interval || null,
                bars: n,
                demo: !!state.packet.demo,
                eur_rate: state.packet.eur_rate,
                eur_stale: !!state.packet.eur_stale,
                currency: state.cfg.currency,
                log: state.series.candles.priceScale().options().mode
                    === LightweightCharts.PriceScaleMode.Logarithmic,
                autoScale: state.series.candles.priceScale().options()
                    .autoScale,
                priceRange: state.series.candles.priceScale()
                    .getVisibleRange(),
                lastClose: last ? last.close : null,
                // La même bougie dans le paquet, en dollars : le
                // rapport des deux est le taux réellement appliqué.
                packetClose: n ? state.packet.bars[n - 1].close : null,
                firstTime: n ? state.packet.bars[0].time : null,
                // Le temps réel de la dernière bougie, et celui que
                // l'axe affiche : leur écart est le décalage appliqué.
                lastTime: n ? state.packet.bars[n - 1].time : null,
                lastShown: last ? last.time : null,
                utc: !!state.cfg.utc,
                exhausted: state.exhausted,
                backfillRetry: !!state.backfillTimer,
                steps: steps(data),
                panes: state.chart.panes().length,
                signals: (state.packet.signals || []).length,
                signalsShown: !state.signalsHidden,
                events: state.events.length,
                eventBars: Object.keys(state.eventsByBar).length,
                eventsShown: !state.eventsHidden,
                // L'abscisse de la dernière bougie marquée, pour qu'un
                // contrôle vienne la survoler.
                eventX: lastEventX(),
                alerts: (state.alertLevels || []).length,
                liqmap: state.liqData
                    ? (state.liqData.empty ? state.liqData.reason
                       : {count: state.liqData.count,
                          exchanges: state.liqData.exchanges})
                    : null,
                heat: state.heatData
                    ? (state.heatData.empty ? state.heatData.reason
                       : {columns: state.heatData.times.length,
                          rows: state.heatData.rows,
                          step: state.heatData.step,
                          box: state.heatBox || null,
                          drawn: !!(state.heatBox && state.heatBox.painted)})
                    : null,
                trades: state.tradesData
                    ? (state.tradesData.empty ? state.tradesData.reason
                       : {count: state.tradesData.count,
                          exchanges: state.tradesData.exchanges,
                          shown: state.tradesShown})
                    : null,
                levels: (state.levelLines || []).map(function (line) {
                    return line.options().title;
                }),
                alertLevels: state.alertLevels || [],
                readout: state.readout ? state.readout.textContent : null,
                measure: state.measureLabel
                    ? state.measureLabel.textContent : null,
                drawings: state.drawings,
                drawLines: (state.drawLines || []).map(function (line) {
                    return line.points;
                }),
                drawMode: state.drawMode,
                countdown: state.countdownText || "",
                anchorMode: state.anchorMode,
                anchors: state.anchors,
                anchorLines: (state.anchorLines || []).map(function (line) {
                    return {t: line.t, drawn: !!line.series,
                            points: line.points ? line.points.length : 0,
                            last: line.points && line.points.length
                                ? line.points[line.points.length - 1].value
                                : null};
                }),
                anchorLegend: state.anchorLegend
                    ? state.anchorLegend.textContent : null,
                cvd: state.series.cvd
                    ? state.series.cvd.data().length : null,
                oi: state.series.oi
                    ? state.series.oi.data().length : null,
                vwap: state.series.vwap
                    ? {anchor: state.vwapAnchor || null,
                       points: state.series.vwap.data().length,
                       legend: state.vwapLegend
                           ? state.vwapLegend.textContent : null}
                    : null,
                profile: state.profileData
                    ? {poc: state.profileData.poc,
                       vaLow: state.profileData.va_low,
                       vaHigh: state.profileData.va_high,
                       bins: state.profileData.centers.length}
                    : null
            };
        },

        // Masque les flèches, ou les rend. Le bilan du panneau ANALYSE dit
        // qu'elles n'apportent rien : elles restent affichées par défaut,
        // et le choix survit au rechargement. Rend le nouvel état.
        toggleSignals: function () {
            state.signalsHidden = !state.signalsHidden;
            localStorage.setItem(SIGNALS_KEY,
                                 state.signalsHidden ? "off" : "on");
            if (state.series && state.packet) { applyMarkers(); }
            return !state.signalsHidden;
        },

        // J : masque les sonneries du graphique, ou les rend. Affichées par
        // défaut ; le choix survit au rechargement. Rend le nouvel état.
        toggleEvents: function () {
            state.eventsHidden = !state.eventsHidden;
            localStorage.setItem(EVENTS_KEY,
                                 state.eventsHidden ? "off" : "on");
            if (state.series && state.packet) {
                applyMarkers();
                updateReadout();
            }
            return !state.eventsHidden;
        },

        // Une alerte vient de sonner (Store alerts-feed) : la relire.
        refreshEvents: function () { fetchEvents(); },

        // T : arme le tracé d'une ligne, ou le désarme.
        toggleDrawing: function () {
            setDrawMode(!state.drawMode);
            return state.drawMode;
        },

        clearDrawings: function () {
            state.drawings = [];
            saveDrawings();
            renderDrawings();
            state.anchors = [];
            saveAnchors();
            renderAnchors();
            return true;
        },

        // A : arme l'ancrage d'un VWAP, ou le désarme. Rend le nouvel état.
        toggleAnchoring: function () {
            setAnchorMode(!state.anchorMode);
            return state.anchorMode;
        },

        // Le graphique en image, tel qu'il est à l'écran : c'est la seule
        // chose qu'on puisse joindre à un message ou garder d'une séance.
        capture: function () {
            if (!state.chart) { return false; }
            var link = document.createElement("a");
            link.href = state.chart.takeScreenshot().toDataURL("image/png");
            link.download = "btcusdt-" + state.cfg.interval + "-"
                + new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")
                + ".png";
            link.click();
            return true;
        },

        // Un seuil en dollars, pour la ligne de commande. Faux tant que le
        // graphique n'a pas de cours : le sens d'un seuil se fige contre lui.
        addAlert: function (level) {
            var bars = state.packet ? state.packet.bars : [];
            if (!bars.length || !(level > 0)) { return false; }
            var levels = (state.alertLevels || []).slice();
            levels.push({level: level,
                         dir: level >= bars[bars.length - 1].close
                             ? "above" : "below"});
            writeAlertLevels(levels);
            return true;
        },

        // Pour le smoke : la position à l'écran d'un prix, en dollars — le
        // clic d'un test vise ainsi un seuil sans lire le canvas.
        coordinate: function (price) {
            var rate = state.cfg.currency === "EUR"
                ? state.packet.eur_rate : 1;
            return state.series.candles.priceToCoordinate(price * rate);
        },

        // Pour le smoke : où se trouve à l'écran un point d'une série, ce que
        // tout recadrage change. `time` est un temps affiché — celui des
        // points de `drawLines`.
        screen: function (time, value) {
            return {x: state.chart.timeScale().timeToCoordinate(time),
                    y: state.series.candles.priceToCoordinate(value)};
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
