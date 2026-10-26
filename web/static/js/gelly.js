// SPDX-License-Identifier: Apache-2.0
// Gelly's day (site design §3), a small state machine:
//   walk -> idle -> walk ...; idle long enough without a click -> nap (sits, hugs her knees, dozes)
//   click -> giggle; five clicks within 4 s -> huff (a pout and a "hmph") -> leaving (marches off the nearest edge) -> away
//   away 30-90 s -> peek (head and one hand from a random edge, looks around) -> duck -> peek -> return -> walk
//   "g6:approve" from the home page's lattice -> cheer (arms up)
// Her cat trails a step behind, curls up when she naps, leaves with her and comes back with her; a click pets it.
// Reduced motion: both sit still and nap; clicks still giggle and purr. "Hide Gelly" in the footer is remembered per visitor.
(function () {
    "use strict";
    var el = document.getElementById("gelly");
    var cat = document.getElementById("gelly-cat");
    var toggle = document.getElementById("gelly-toggle");
    if (!el || !cat) return;

    var KEY = "g6.gelly.hidden";
    var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var SPEED = 34, MARCH = 150;                       // px per second
    var state = "idle", x = 0, y = 0, rot = 0, timers = [], raf = 0, tickles = [];
    var cx = 0, craf = 0, purring = 0;
    var lastPoke = Date.now(), napAfter = 20000 + Math.random() * 20000;

    function hiddenStored() { try { return localStorage.getItem(KEY) === "1"; } catch (e) { return false; } }
    function storeHidden(v) { try { if (v) localStorage.setItem(KEY, "1"); else localStorage.removeItem(KEY); } catch (e) { /* private window */ } }
    function W() { return el.offsetWidth || 64; }
    function H() { return el.offsetHeight || 83; }
    function CW() { return cat.offsetWidth || 40; }
    function maxX() { return Math.max(16 + CW(), window.innerWidth - W() - 16); }
    function minX() { return window.innerWidth < 640 ? window.innerWidth * 0.62 : 16 + CW(); }   // phones: off the text
    function set(s) { state = s; el.setAttribute("data-state", s); }
    function place() { el.style.transform = "translate3d(" + x + "px," + y + "px,0) rotate(" + rot + "deg)"; }
    function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
    function stop() { timers.forEach(clearTimeout); timers = []; if (raf) cancelAnimationFrame(raf); raf = 0; }
    function rand(a, b) { return a + Math.random() * (b - a); }

    // The cat: a step behind her, or off the same edge when she has gone.
    function setCat(s) { if (!purring || s === "away") cat.setAttribute("data-state", s); }
    function placeCat() { cat.style.transform = "translate3d(" + cx + "px,0,0)"; }
    function gone() { return ["leaving", "away", "peek", "duck"].indexOf(state) >= 0; }
    function catTarget() {
        if (gone()) return x < window.innerWidth / 2 ? -CW() - 80 : window.innerWidth + 80;
        return el.classList.contains("is-left") ? x + W() - 4 : x - CW() + 4;
    }
    function catRest() {
        if (gone()) { setCat("away"); return; }
        cat.classList.toggle("is-left", x + W() / 2 < cx + CW() / 2);
        setCat(state === "nap" ? "sleep" : "sit");
    }
    function follow() {
        if (craf || cat.hidden) return;
        var last = performance.now();
        craf = requestAnimationFrame(function step(now) {
            var dt = Math.min(0.05, (now - last) / 1000), d = catTarget() - cx;
            last = now;
            if (Math.abs(d) < 1.5 && !raf) { craf = 0; catRest(); return; }
            var top = gone() ? MARCH * 1.2 : SPEED * 2.5, v = Math.max(-top, Math.min(top, d * 2.4));
            cx += v * dt;
            placeCat();
            if (Math.abs(v) > 5) { cat.classList.toggle("is-left", v < 0); setCat("walk"); }
            craf = requestAnimationFrame(step);
        });
    }

    function walkTo(tx, speed, as, then) {
        set(as);
        el.classList.toggle("is-left", tx < x);
        var last = performance.now();
        raf = requestAnimationFrame(function step(now) {
            var dt = Math.min(0.05, (now - last) / 1000), d = tx - x, mv = speed * dt;
            last = now;
            if (Math.abs(d) <= mv) { x = tx; place(); raf = 0; then(); return; }
            x += (d > 0 ? 1 : -1) * mv;
            place();
            raf = requestAnimationFrame(step);
        });
        follow();
    }
    function wander() { walkTo(rand(minX(), maxX()), SPEED, "walk", idle); }
    function idle() {
        set("idle");
        follow();
        if (Date.now() - lastPoke > napAfter) { nap(); return; }
        later(wander, rand(2000, 6000));
    }
    function nap() {
        set("nap");
        follow();
        if (reduce) return;
        later(function () { lastPoke = Date.now(); napAfter = rand(20000, 40000); idle(); }, rand(30000, 45000));
    }
    function huff() {
        set("huff");
        later(function () {
            var right = x > window.innerWidth / 2;
            walkTo(right ? window.innerWidth + 24 : -W() - 24, MARCH, "leaving", away);
        }, 1600);
    }
    function away() { set("away"); follow(); later(peek, rand(30000, 90000)); }

    // A peek: her head and one hand in from the left, the right or the bottom edge, then a duck back, then again.
    function peekPose(edge, inset) {
        var h = H(), w = W();
        if (edge === "bottom") { x = rand(minX(), maxX()); y = h * (inset ? 0.5 : 1.1); rot = 0; }
        else {
            y = -rand(window.innerHeight * 0.2, window.innerHeight * 0.55); rot = edge === "left" ? 90 : -90;
            var c = (inset ? 0.42 : -0.1) * h - h / 2;       // the rotated box's centre, measured in from the edge
            x = edge === "left" ? c - w / 2 : window.innerWidth - c - w / 2;
        }
        place();
    }
    function peek() {
        var edge = ["left", "right", "bottom"][Math.floor(Math.random() * 3)];
        peekPose(edge, false);                           // moved while still away (hidden, no transition)
        later(function () { set("peek"); peekPose(edge, true); }, 60);
        later(function () { set("duck"); peekPose(edge, false); }, 2600);
        later(function () { set("peek"); peekPose(edge, true); }, 4300);
        later(function () { comeBack(edge); }, 6200);
    }
    function comeBack(edge) {
        rot = 0; y = 0;
        var from = edge === "left" || (edge === "bottom" && x < window.innerWidth / 2) ? "left" : "right";
        x = from === "left" ? -W() - 8 : window.innerWidth + 8; place();
        cx = from === "left" ? x - CW() - 40 : x + W() + 40; placeCat();
        lastPoke = Date.now();
        walkTo(rand(minX(), maxX()), SPEED, "walk", idle);
    }

    el.addEventListener("click", function () {
        if (state === "away" || state === "leaving" || state === "huff") return;
        if (state === "peek") { stop(); set("duck"); later(function () { comeBack("bottom"); }, 600); return; }
        var now = Date.now();
        lastPoke = now;
        tickles = tickles.filter(function (t) { return now - t < 4000; });
        tickles.push(now);
        stop();
        rot = 0; y = 0; place();
        if (tickles.length >= 5 && !reduce) { tickles = []; huff(); return; }
        set("idle");
        void el.offsetWidth;                             // restart the giggle on a fast second click
        set("giggle");
        later(reduce ? nap : idle, 900);
    });

    cat.addEventListener("click", function () {
        if (cat.getAttribute("data-state") === "away") return;
        clearTimeout(purring);
        purring = 0; setCat("sit");
        void cat.offsetWidth;
        setCat("happy");
        purring = setTimeout(function () { purring = 0; catRest(); }, 1200);
    });

    document.addEventListener("g6:approve", function () {
        if (["idle", "walk", "nap"].indexOf(state) < 0) return;
        stop(); y = 0; rot = 0; place(); set("cheer");
        if (!purring) { setCat("happy"); purring = setTimeout(function () { purring = 0; catRest(); }, 1200); }
        later(idle, 1300);
    });

    window.addEventListener("resize", function () { if (!gone()) { x = Math.min(x, maxX()); place(); follow(); } });

    function show(visible) {
        el.hidden = cat.hidden = !visible;
        if (toggle) { toggle.textContent = visible ? "Hide Gelly" : "Show Gelly"; toggle.setAttribute("aria-pressed", String(!visible)); }
        stop();
        if (craf) { cancelAnimationFrame(craf); craf = 0; }
        if (!visible) return;
        x = maxX() - 8; y = 0; rot = 0; place();
        cx = x - CW() + 4; placeCat();
        if (reduce) { set("nap"); setCat("sleep"); return; }
        set("idle"); setCat("sit");
        later(wander, 1800);
    }
    if (toggle) toggle.addEventListener("click", function () { var hide = !el.hidden; storeHidden(hide); show(!hide); });
    show(!hiddenStored());
})();
