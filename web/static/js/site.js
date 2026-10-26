// SPDX-License-Identifier: Apache-2.0
// The site's small behaviours: the phone menu, copy buttons, tabs, the self-modification modes switch, the footer's
// contact dialog and the confetti on Get started. Native scrolling:
// nothing here runs per frame.
(function () {
    "use strict";

    var btn = document.getElementById("mobile-menu-btn");
    var menu = document.getElementById("mobile-menu");
    if (btn && menu) {
        var setOpen = function (open) {
            btn.setAttribute("aria-expanded", String(open));
            btn.textContent = open ? "Close" : "Menu";
            menu.classList.toggle("open", open);
        };
        btn.addEventListener("click", function () { setOpen(btn.getAttribute("aria-expanded") !== "true"); });
        document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
    }

    // <button data-copy="#id"> copies the text of the element it names, and says so.
    document.querySelectorAll("[data-copy]").forEach(function (b) {
        b.addEventListener("click", function () {
            var target = document.querySelector(b.getAttribute("data-copy"));
            if (!target || !navigator.clipboard) return;
            navigator.clipboard.writeText(target.textContent.trim()).then(function () {
                var label = b.textContent;
                b.textContent = "Copied";
                setTimeout(function () { b.textContent = label; }, 1600);
            });
        });
    });

    // Tabs: every panel shows without JavaScript; with it, one at a time (arrow keys move between tabs).
    document.querySelectorAll("[data-tabs]").forEach(function (root) {
        var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
        function select(tab, focus) {
            tabs.forEach(function (t) {
                var on = t === tab;
                t.setAttribute("aria-selected", String(on));
                t.tabIndex = on ? 0 : -1;
                var panel = document.getElementById(t.getAttribute("aria-controls"));
                if (panel) panel.hidden = !on;
            });
            if (focus) tab.focus();
        }
        tabs.forEach(function (t, i) {
            t.addEventListener("click", function () { select(t, false); });
            t.addEventListener("keydown", function (e) {
                var step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                if (step) { e.preventDefault(); select(tabs[(i + step + tabs.length) % tabs.length], true); }
            });
        });
        root.classList.add("is-tabbed");
        select(tabs.filter(function (t) { return t.getAttribute("aria-selected") === "true"; })[0] || tabs[0], false);
    });

    // The modes switch: one pressed button, one visible panel.
    document.querySelectorAll("[data-modes]").forEach(function (root) {
        var buttons = root.querySelectorAll("[data-mode]");
        buttons.forEach(function (b) {
            b.addEventListener("click", function () {
                buttons.forEach(function (o) {
                    var on = o === b;
                    o.setAttribute("aria-pressed", String(on));
                    var panel = document.getElementById(o.getAttribute("aria-controls"));
                    if (panel) panel.hidden = !on;
                });
                document.dispatchEvent(new CustomEvent("g6:mode", { detail: b.getAttribute("data-mode") }));
            });
        });
    });

    // The footer's Contact opens a dialog. The address is put together from its two parts when it opens, so no page
    // writes it whole; where a dialog cannot open, the Contact item stays hidden.
    var contact = document.getElementById("contact");
    var address = document.getElementById("contact-address");
    if (contact && address && typeof contact.showModal === "function") {
        document.querySelectorAll("[data-contact]").forEach(function (b) {
            b.parentElement.hidden = false;
            b.addEventListener("click", function () {
                var email = address.getAttribute("data-user") + "@" + address.getAttribute("data-domain");
                address.textContent = email;
                address.setAttribute("href", "mailto:" + email);
                contact.showModal();
            });
        });
        contact.querySelector("[data-close]").addEventListener("click", function () { contact.close(); });
        // a click on the backdrop (the dialog itself, outside its body) closes it, as Escape does
        contact.addEventListener("click", function (e) { if (e.target === contact) contact.close(); });
    }

    // The call to action (owner, 2026-10-23: "when users click on this can you do a tasteful confetti animation"): an
    // ordinary click on Get started throws a short burst of the site's colours from the button, then follows the link.
    // The library (tsParticles confetti, pinned in static/vendor/) is fetched on the first such click, never with the
    // page. A click with a modifier key or another button, reduced motion, or a library that fails or is slow to load
    // follows the link at once; without JavaScript it is a plain link.
    var BURST_MS = 700;                 // long enough to see the burst, short enough not to feel held
    var WAIT_MS = 1500;                 // however the library fares, the visitor is on their way by then
    var bundle = document.currentScript && new URL("../vendor/tsparticles-confetti/tsparticles.confetti.bundle.min.js",
                                                   document.currentScript.src).href;
    var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");
    var busy = false;
    window.addEventListener("pageshow", function () { busy = false; });     // back from the next page: ready again
    document.querySelectorAll("a.cta").forEach(function (a) {
        a.addEventListener("click", function (e) {
            if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
            if (!bundle || a.target || (still && still.matches)) return;
            e.preventDefault();
            if (busy) return;                                   // a second click while the burst plays
            busy = true;
            var gone = false;
            var go = function () { if (!gone) { gone = true; window.location.assign(a.href); } };
            var burst = function () {
                var css = getComputedStyle(document.documentElement);
                var colour = function (name, fallback) { return css.getPropertyValue(name).trim() || fallback; };
                var box = a.getBoundingClientRect();
                var x = (box.left + box.width / 2) / window.innerWidth;
                var y = (box.top + box.height / 2) / window.innerHeight;
                var fromBar = y < 0.2;                          // from the title bar it falls; from the hero it rises
                try {
                    window.confetti({
                        particleCount: 90, angle: fromBar ? 270 : 90, spread: fromBar ? 120 : 80,
                        startVelocity: fromBar ? 22 : 34, decay: 0.9, gravity: 0.9, ticks: 150, scalar: 1.1,
                        origin: { x: x, y: y }, shapes: ["circle", "square", "triangle"], zIndex: 60,
                        colors: [colour("--ochre", "#C4953A"), colour("--teal", "#10A493"), colour("--ink", "#000000"),
                                 colour("--teal-glass", "#7FE6D8"), colour("--graphite", "#4A5451")],
                    }).catch(go);
                } catch (err) {
                    go();
                    return;
                }
                setTimeout(go, BURST_MS);
            };
            setTimeout(go, WAIT_MS);
            if (typeof window.confetti === "function") return burst();
            var s = document.createElement("script");
            s.src = bundle;
            s.onload = function () { if (typeof window.confetti === "function") burst(); else go(); };
            s.onerror = go;
            document.head.appendChild(s);
        });
    });
})();
