// Démo statique : la barre de titre du panneau prix, sans Dash.
//
// Reproduit ce que fait le callback clientside de terminal/panels/price.py :
// relayer les sélecteurs à window.lwcPrice.configure(cfg, conf). Les
// réglages tiennent dans localStorage, comme la persistance Dash du
// vrai terminal. `conf` (thème, police, intervalles) est écrit dans
// index.html par `python -m terminal.demo`.
(function () {
    "use strict";

    var conf = window.DEMO_CONF;
    var STORE = "btcterm-demo";
    var state = load() || {interval: conf.default_interval || "1d",
                           currency: "USD", log: false,
                           extras: ["rsi", "volume", "profile"]};

    function load() {
        try { return JSON.parse(localStorage.getItem(STORE) || "null"); }
        catch (e) { return null; }
    }
    function save() {
        try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) { /* privé */ }
    }

    function radio(id, options, current, onPick) {
        var box = document.getElementById(id);
        box.innerHTML = "";
        options.forEach(function (opt) {
            var label = document.createElement("label");
            label.textContent = opt.label;
            label.dataset.value = opt.value;
            if (opt.value === current) { label.classList.add("selected"); }
            label.addEventListener("click", function () {
                Array.prototype.forEach.call(box.querySelectorAll("label"), function (l) {
                    l.classList.remove("selected");
                });
                label.classList.add("selected");
                onPick(opt.value);
            });
            box.appendChild(label);
        });
    }

    function check(id, options, current, onChange) {
        var box = document.getElementById(id);
        box.innerHTML = "";
        options.forEach(function (opt) {
            var label = document.createElement("label");
            label.textContent = opt.label;
            label.dataset.value = opt.value;
            if (current.indexOf(opt.value) >= 0) { label.classList.add("selected"); }
            label.addEventListener("click", function () {
                label.classList.toggle("selected");
                onChange(Array.prototype.map.call(
                    box.querySelectorAll("label.selected"),
                    function (l) { return l.dataset.value; }));
            });
            box.appendChild(label);
        });
    }

    function apply() {
        save();
        if (window.lwcPrice) {
            window.lwcPrice.configure({
                interval: state.interval, currency: state.currency,
                log: state.log, extras: state.extras.slice(), maximized: false
            }, conf);
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        if (!(conf.intervals || {})[state.interval]) { state.interval = "1d"; }
        radio("price-interval",
              Object.keys(conf.intervals).map(function (k) { return {label: k, value: k}; }),
              state.interval, function (v) { state.interval = v; apply(); });
        radio("price-currency", [{label: "$", value: "USD"}, {label: "€", value: "EUR"}],
              state.currency, function (v) { state.currency = v; apply(); });
        check("price-scale", [{label: "LOG", value: "log"}], state.log ? ["log"] : [],
              function (v) { state.log = v.indexOf("log") >= 0; apply(); });
        check("price-extras", [{label: "RSI", value: "rsi"}, {label: "CRSI", value: "crsi"},
                               {label: "VOL", value: "volume"}, {label: "PROFIL", value: "profile"}],
              state.extras, function (v) { state.extras = v; apply(); });
        apply();
    });
}());
