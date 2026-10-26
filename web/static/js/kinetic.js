// SPDX-License-Identifier: Apache-2.0
// Kinetic type (kinetic.css): headings set themselves as they come in; the h1 narrows as it goes.
(function () {
  "use strict";
  var main = document.querySelector("main");
  if (!main || !window.IntersectionObserver || !main.animate || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  var EASE = "cubic-bezier(.2,.7,0,1)", MIN = 62, WORD = / |[^ \-–—]*[\-–—]+|[^ \-–—]+/g;
  function all(el, s) { return [].slice.call(el.querySelectorAll(s)); }
  function esc(t) { return t.replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
  function plain(h) { return h && !h.children.length && /\S/.test(h.textContent); }
  function axes(el) {  // the set width and weight; v: the face has them (Archivo, not Plex Mono)
    var s = getComputedStyle(el), m = /"wdth" ([\d.]+)/.exec(s.fontVariationSettings);
    return { w: m ? +m[1] : 100, g: +s.fontWeight, v: /Archivo/.test(s.fontFamily) };
  }

  // A block per line, broken where the browser broke it, so narrower letters never re-wrap the heading.
  function split(h) {
    var saved = h.innerHTML, text = h.textContent.trim().replace(/\s+/g, " "), bits = text.match(WORD), lines = [], n = 0, top;
    h.innerHTML = bits.map(function (b) { return b == " " ? b : "<span>" + esc(b) + "</span>"; }).join("");
    bits.forEach(function (b) {
      var s = b != " " && h.children[n++];
      if (s && s.offsetTop !== top) { lines.push(""); top = s.offsetTop; }
      lines[lines.length - 1] += b;
    });
    h.innerHTML = lines.map(function (l) {
      return '<span class="k-l" aria-hidden="true"><span class="k-i">' + esc(l.trim()) + "</span></span>";
    }).join(" ");
    h.setAttribute("aria-label", text);
    if (!all(h, ".k-l").some(function (l) { return l.scrollWidth > l.clientWidth + 1; })) return all(h, ".k-i");
    h.innerHTML = saved; h.removeAttribute("aria-label"); return [];
  }

  function enter(h) {
    var a = axes(h), saved = h.innerHTML, from = { offset: 0, transform: "translateY(calc(100% + .35em))" }, last;
    h.classList.remove("k-wait");
    if (a.v) { from.fontVariationSettings = '"wdth" ' + Math.max(MIN, a.w - 14); from.fontWeight = Math.max(300, a.g - 260); }
    split(h).forEach(function (line, i) {
      line.parentNode.classList.add("k-m");
      last = line.animate([from], { duration: 1100, delay: i * 90, easing: EASE, fill: "backwards" });
    });
    if (last) last.onfinish = function () { h.innerHTML = saved; h.removeAttribute("aria-label"); };
  }

  function scrolled(h) {  // up to 12 units narrower as it leaves; never wider than set
    var a = axes(h), saved = h.innerHTML, span, width, shown, queued, timer;
    function measure() {
      h.style.fontVariationSettings = ""; h.innerHTML = saved; width = innerWidth; shown = -1;
      span = split(h).length && h.getBoundingClientRect().bottom + scrollY;
      draw();
    }
    function draw() {
      var p = Math.min(1, Math.max(0, scrollY / span));
      queued = 0;
      if (span && p != shown) h.style.fontVariationSettings = '"wdth" ' + (a.w - Math.min(12, a.w - MIN) * (shown = p)).toFixed(2);
    }
    if (!a.v || a.w <= MIN) return;
    addEventListener("scroll", function () { queued = queued || requestAnimationFrame(draw); }, { passive: true });
    addEventListener("resize", function () { if (innerWidth != width) { clearTimeout(timer); timer = setTimeout(measure, 150); } });
    measure();
  }

  (document.fonts ? document.fonts.ready : Promise.resolve()).then(function () {
    var head = main.querySelector("h1"), io = new IntersectionObserver(function (seen) {
      seen.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); enter(e.target); } });
    }, { rootMargin: "0px 0px -12% 0px" });
    all(main, "h1,h2,.statement").forEach(function (h) {  // plain text, not in a link, table or figure
      if (h != head && plain(h) && !h.closest("a, table, figure") && h.getBoundingClientRect().top > innerHeight * 0.88) {
        h.classList.add("k-wait");
        io.observe(h);
      }
    });
    if (plain(head)) scrolled(head);
  });
})();
