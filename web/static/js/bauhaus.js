// SPDX-License-Identifier: Apache-2.0
// The Bauhaus forms behind the kinetic pages (see bauhaus.css). The coloured forms show only where the right of the
// viewport is clear of text. With motion allowed, the page's scroll drifts the square, bar and half-disc a few dozen
// px (--p, 0 to 1 down the page) and turns the arc a quarter for each section heading passed (--k). The ring's slow
// turn is CSS, paused while the tab is hidden. Under reduced motion the forms stay where they are drawn.
(function () {
  "use strict";
  var bh = document.querySelector(".bh"), main = document.querySelector("main");
  if (!bh || !main) return;
  var still = matchMedia("(prefers-reduced-motion: reduce)").matches, acc = bh.querySelector(".bh-acc"), heads = [], queued;
  var TEXT = "p, li, h1, h2, h3, h4, td, th, pre, dd, dt, blockquote, figure, img, .btn";

  function room() {  // the right edge of the text, the coloured forms' left edge, and where each section starts
    var right = 0;
    [].forEach.call(main.querySelectorAll(TEXT), function (e) {
      var r = e.getBoundingClientRect();
      if (r.width) right = Math.max(right, r.right);
    });
    var left = getComputedStyle(acc).display == "none" ? 0 : acc.getBoundingClientRect().left;
    bh.classList.toggle("has-room", left > right + 24);
    heads = [].map.call(main.querySelectorAll("h2"), function (h) { return h.getBoundingClientRect().top + scrollY; });
    draw();
  }

  function draw() {
    var end = document.documentElement.scrollHeight - innerHeight, mid = scrollY + innerHeight / 2;
    queued = 0;
    if (still) return;
    bh.style.setProperty("--p", end > 0 ? Math.min(1, Math.max(0, scrollY / end)).toFixed(4) : 0);
    bh.style.setProperty("--k", heads.filter(function (y) { return y < mid; }).length);
  }

  document.addEventListener("visibilitychange", function () { bh.classList.toggle("is-paused", document.hidden); });
  if (!still) addEventListener("scroll", function () { queued = queued || requestAnimationFrame(draw); }, { passive: true });
  (document.fonts ? document.fonts.ready : Promise.resolve()).then(function () {
    if (window.ResizeObserver) new ResizeObserver(room).observe(main);  // a resize, or a diagram drawn, moves the text
    else room();
  });
})();
