// SPDX-License-Identifier: Apache-2.0
// The home page's results, once, as they scroll into view: each G6 number counts up from the model alone's, and the
// bars grow from nothing. The page holds the final numbers; this only plays them in. Nothing is hidden unless it runs,
// and under reduced motion it does nothing.
(function () {
  "use strict";
  var root = document.querySelector("[data-count]");
  if (!root || !window.IntersectionObserver || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  var nums = [].slice.call(root.querySelectorAll(".ev__to[data-from]")).map(function (el) {
    return { el: el, from: parseFloat(el.dataset.from), to: parseFloat(el.textContent), text: el.textContent };
  }).filter(function (n) { return isFinite(n.from) && isFinite(n.to); });
  root.classList.add("is-armed");
  nums.forEach(function (n) { n.el.textContent = String(Math.round(n.from)); });
  var io = new IntersectionObserver(function (seen) {
    if (!seen[0].isIntersecting) return;
    io.disconnect();
    root.classList.add("is-counted");
    var t0 = performance.now(), MS = 1400;
    requestAnimationFrame(function step(now) {
      var u = Math.min(1, (now - t0) / MS), e = 1 - Math.pow(1 - u, 3);
      nums.forEach(function (n) { n.el.textContent = u < 1 ? String(Math.round(n.from + (n.to - n.from) * e)) : n.text; });
      if (u < 1) requestAnimationFrame(step);
    });
  }, { threshold: 0.35 });
  io.observe(root);
})();
