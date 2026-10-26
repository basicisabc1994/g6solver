// SPDX-License-Identifier: Apache-2.0
// The Research page without this script shows every tile open and every bar at full length. With it, tiles fold to
// their headers (click or Enter to open), and bars grow when they scroll into view.
(function () {
  "use strict";
  var root = document.querySelector("[data-research]");
  if (!root) return;
  root.classList.add("js");

  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var seen = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
    entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("is-in"); seen.unobserve(e.target); } });
  }, { threshold: 0.3 }) : null;
  function watch(el) { if (still || !seen) el.classList.add("is-in"); else seen.observe(el); }
  function grow(scope) { scope.querySelectorAll(".rs-group, .rs-pair").forEach(watch); }

  root.querySelectorAll(".rs-tile").forEach(function (tile, i) {
    var head = tile.querySelector(".rs-tile-head");
    var body = tile.querySelector(".rs-tile-body");
    function show(state) {
      head.setAttribute("aria-expanded", String(state));
      body.hidden = !state;
      if (state) grow(body);
    }
    head.addEventListener("click", function () { show(head.getAttribute("aria-expanded") !== "true"); });
    show(i === 0);                                        // the latest round starts open
  });
  ["#the-meta-harness", "#studies"].forEach(function (id) { var s = root.querySelector(id); if (s) grow(s); });
})();
