// SPDX-License-Identifier: Apache-2.0
// The home page's lattice: G6's components (read from the registry into #lattice-data) on a 3 x 3 x 3 grid, the
// meta-harness at its centre and one slot left empty, drawn in ink. Scrolling through the four steps acts them out:
//   propose  three drafts leave the centre for the empty slot;
//   test     held-out probes fly in from outside; two drafts fail, and each traces back to a cause;
//   gate     the two fall away and one locks into the slot;
//   approve  one teal light reaches it and it turns teal, with its links (Gelly hears: "g6:approve").
// Each step's action plays while its text scrolls in and is complete when the text is in place. Then, through the list
// of limits, the lattice turns, and the limit at the reading line (or under the pointer) lights the components that
// answer it: they grow, the rest fade, the meta-harness reaches out to each, and each is labelled in plain words. It
// renders only while on screen, caps the pixel ratio at 2, holds still under reduced motion, and draws one 2D frame
// without WebGL.
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.169.0/build/three.module.min.js";

const data = JSON.parse(document.getElementById("lattice-data").textContent);
const canvas = document.getElementById("lattice-canvas");
const sceneEl = canvas.parentElement;
const steps = Array.from(document.querySelectorAll(".step"));
const limits = Array.from(document.querySelectorAll(".limit[data-parts]"));
const labelsEl = document.getElementById("lattice-labels");
const tip = document.getElementById("lattice-tip");
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

const S = 1.6;                                   // grid spacing
const EMPTY = [1, 1, 1];                         // the slot a change locks into
const NODE = 0.17, CENTRE = 0.3, DRAFT = 0.2, PROBE = 0.065;
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const ease = (t) => 1 - Math.pow(1 - clamp(t), 3);
const v3 = (a) => new THREE.Vector3(a[0] * S, a[1] * S, a[2] * S);
const adjacent = (a, b) => Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]) === 1;

// ---- Layout: the centre, then the shell slots in a fixed order, the empty one skipped -------------------------------
const slots = [];
for (const x of [-1, 0, 1]) for (const y of [-1, 0, 1]) for (const z of [-1, 0, 1]) if (x || y || z) slots.push([x, y, z]);
slots.sort((a, b) => (a[1] - b[1]) || (a[0] - b[0]) || (a[2] - b[2]));
const shell = slots.filter((s) => s.join() !== EMPTY.join());
const others = data.nodes.filter((n) => n.name !== data.centre);
const nodes = [{ ...data.nodes.find((n) => n.name === data.centre), slot: [0, 0, 0] },
               ...others.map((n, i) => ({ ...n, slot: shell[i % shell.length] }))];
const index = new Map(nodes.map((n, i) => [n.name, i]));
const limitParts = limits.map((el) => el.dataset.parts.split(" ").filter((n) => index.has(n)).map((n) => index.get(n)));
const home = nodes.map((n) => v3(n.slot));
const emptyPos = v3(EMPTY);

const gridEdges = [];
for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    if (adjacent(nodes[i].slot, nodes[j].slot)) gridEdges.push([i, j]);
}
const depEdges = [];
nodes.forEach((n, i) => (n.requires || []).forEach((r) => index.has(r) && depEdges.push([i, index.get(r)])));
const slotNeighbours = nodes.map((n, i) => (adjacent(n.slot, EMPTY) ? i : -1)).filter((i) => i >= 0);

// The drafts gather at the empty slot; the gate keeps the middle one.
const drafts = [[-0.95, 0.5, 0.15], [0.42, 0.4, 0.38], [0.4, -0.9, 0.42]].map((o) => emptyPos.clone().add(new THREE.Vector3(...o)));
const WINNER = 1, FAILED = [0, 2];
const causes = [index.get("grounding") ?? 2, index.get("context") ?? 3];   // where the two failing drafts trace back to

function tokens() {
    const css = getComputedStyle(document.documentElement);
    const get = (k, d) => new THREE.Color((css.getPropertyValue(k) || d).trim());
    return { paper: get("--paper", "#E8ECE9"), ink: get("--ink", "#000000"), graphite: get("--graphite", "#4A5451"),
             teal: get("--teal", "#10A493") };
}

// ---- Without WebGL: one still frame in 2D ----------------------------------------------------------------------------
function still2d() {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const t = tokens(), r = sceneEl.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = r.width * dpr; canvas.height = r.height * dpr;
    ctx.scale(dpr, dpr);
    const k = Math.min(r.width, r.height) / 9;
    const p = (v) => [r.width / 2 + (v.x - v.z * 0.5) * k, r.height / 2 - (v.y - v.z * 0.32) * k];
    ctx.strokeStyle = "#" + t.ink.getHexString(); ctx.globalAlpha = 0.25; ctx.lineWidth = 1;
    for (const [a, b] of [...gridEdges, ...depEdges]) {
        const [x1, y1] = p(home[a]), [x2, y2] = p(home[b]);
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    }
    ctx.globalAlpha = 1; ctx.fillStyle = "#" + t.ink.getHexString();
    home.forEach((v, i) => { const [x, y] = p(v); ctx.beginPath(); ctx.arc(x, y, i ? 3.5 : 6, 0, Math.PI * 2); ctx.fill(); });
    const [ex, ey] = p(emptyPos);
    ctx.fillStyle = "#" + t.teal.getHexString(); ctx.beginPath(); ctx.arc(ex, ey, 5, 0, Math.PI * 2); ctx.fill();
}

let renderer = null;
try { renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "low-power" }); } catch (err) { renderer = null; }
if (renderer) start(); else still2d();

function start() {
    let colors = tokens();
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 60);
    camera.position.set(0, 2.4, 13);
    camera.lookAt(0, 0.25, 0);
    scene.fog = new THREE.Fog(colors.paper, 10, 19);
    renderer.setClearColor(colors.paper, 1);
    const group = new THREE.Group();
    scene.add(group);

    // Lines, in draw order under the points: grid (faint), dependencies (ink), the slot's dashed links, the moving
    // links (drafts, traces, the kept change), the approval (teal).
    const segs = (n) => {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 6), 3));
        return g;
    };
    const mat = (color, opacity) => new THREE.LineBasicMaterial({ color, transparent: true, opacity, fog: true, depthWrite: false });
    const grid = new THREE.LineSegments(segs(gridEdges.length), mat(colors.ink, 0.14));
    const deps = new THREE.LineSegments(segs(depEdges.length), mat(colors.ink, 0.42));
    const slot = new THREE.LineSegments(segs(slotNeighbours.length),
        new THREE.LineDashedMaterial({ color: colors.ink, dashSize: 0.09, gapSize: 0.09, transparent: true, opacity: 0.4, fog: true, depthWrite: false }));
    const live = new THREE.LineSegments(segs(12), mat(colors.ink, 0.75));
    const lit = new THREE.LineSegments(segs(8), mat(colors.teal, 0));
    const reach = new THREE.LineSegments(segs(8), mat(colors.ink, 0));          // the meta-harness to a lit component
    // Every part moves every frame, so bounds measured once are wrong: never cull (a few hundred vertices).
    [grid, deps, slot, live, lit, reach].forEach((l, k) => { l.renderOrder = k; l.frustumCulled = false; group.add(l); });

    // Points: components, the empty slot, three drafts, the probes, the approving light and its halo, in one draw call.
    const PROBES = reduce ? 0 : 24;
    const iEmpty = nodes.length, iDraft = iEmpty + 1, iProbe = iDraft + drafts.length, iLight = iProbe + PROBES, iHalo = iLight + 1;
    const count = iHalo + 1;
    const pos = new Float32Array(count * 3), size = new Float32Array(count), fill = new Float32Array(count);
    const col = new Float32Array(count * 3), alpha = new Float32Array(count);
    const pg = new THREE.BufferGeometry();
    pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    pg.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    pg.setAttribute("aFill", new THREE.BufferAttribute(fill, 1));
    pg.setAttribute("aColor", new THREE.BufferAttribute(col, 3));
    pg.setAttribute("aAlpha", new THREE.BufferAttribute(alpha, 1));
    const pm = new THREE.ShaderMaterial({
        transparent: true, depthWrite: false, depthTest: false,
        uniforms: { uScale: { value: 400 }, uFog: { value: colors.paper }, uNear: { value: 10 }, uFar: { value: 19 } },
        vertexShader: `
            attribute float aSize; attribute float aFill; attribute vec3 aColor; attribute float aAlpha;
            uniform float uScale; uniform float uNear; uniform float uFar;
            varying float vFill; varying vec3 vColor; varying float vAlpha; varying float vFog;
            void main() {
                vec4 mv = modelViewMatrix * vec4(position, 1.0);
                gl_Position = projectionMatrix * mv;
                gl_PointSize = aSize * uScale / -mv.z;
                vFill = aFill; vColor = aColor; vAlpha = aAlpha; vFog = smoothstep(uNear, uFar, -mv.z);
            }`,
        fragmentShader: `
            uniform vec3 uFog;
            varying float vFill; varying vec3 vColor; varying float vAlpha; varying float vFog;
            void main() {
                float r = length(gl_PointCoord * 2.0 - 1.0);
                float aa = max(fwidth(r), 0.02);
                float disc = 1.0 - smoothstep(1.0 - aa * 1.5, 1.0, r);
                float hole = (1.0 - smoothstep(0.72 - aa * 1.5, 0.72, r)) * (1.0 - vFill);
                float a = (disc - hole) * vAlpha;
                if (a < 0.01) discard;
                gl_FragColor = vec4(mix(vColor, uFog, vFog * 0.8), a);
            }`,
    });
    const points = new THREE.Points(pg, pm);
    points.renderOrder = 10;                     // over every line, so a node is never crossed by its own links
    points.frustumCulled = false;
    group.add(points);

    const rng = (() => { let s = 20261019; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); })();
    const probes = Array.from({ length: PROBES }, (_, k) => {
        const th = rng() * Math.PI * 2, ph = Math.acos(2 * rng() - 1), R = 7;
        return { from: new THREE.Vector3(R * Math.sin(ph) * Math.cos(th), R * Math.cos(ph) * 0.55, R * Math.sin(ph) * Math.sin(th)),
                 draft: k % 3, delay: rng() * 0.4 };
    });
    const scatter = home.map(() => new THREE.Vector3((rng() - 0.5) * 14, (rng() - 0.5) * 9, (rng() - 0.5) * 10));

    const setP = (i, v, s, f, c, a) => {
        pos[i * 3] = v.x; pos[i * 3 + 1] = v.y; pos[i * 3 + 2] = v.z;
        size[i] = s; fill[i] = f; col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b; alpha[i] = a;
    };
    const setSeg = (line, k, a, b) => line.geometry.attributes.position.array.set([a.x, a.y, a.z, b.x, b.y, b.z], k * 6);
    const tmp = new THREE.Vector3(), mixC = new THREE.Color();

    // ---- One frame at scroll position s (0..4) and assembly a (0..1) ----------------------------------------------
    let approved = false;
    // The limits: the one at the reading line, the one under the pointer (it wins), each node's light (0..1) and how far
    // the rest are faded, both eased; and how far the list has scrolled, which turns the lattice.
    let focus = -1, hover = -1, dim = 0, turn = 0, turnTarget = 0;
    const glow = new Float32Array(nodes.length);
    function settle(k) {
        const on = hover >= 0 ? hover : focus, parts = on >= 0 ? limitParts[on] : [];
        for (let i = 0; i < glow.length; i++) glow[i] += ((parts.includes(i) ? 1 : 0) - glow[i]) * k;
        dim += ((on >= 0 ? 1 : 0) - dim) * k;
        turn += (turnTarget - turn) * k;
    }
    function frame(s, a) {
        const P = clamp(s), T = clamp(s - 1), G = clamp(s - 2), A = clamp(s - 3);
        const now = home.map((h, i) => scatter[i].clone().lerp(h, ease(a)));
        now.forEach((v, i) => setP(i, v, i === 0 ? CENTRE : NODE, 1, colors.ink, 1));
        gridEdges.forEach(([i, j], k) => setSeg(grid, k, now[i], now[j]));
        depEdges.forEach(([i, j], k) => setSeg(deps, k, now[i], now[j]));
        slotNeighbours.forEach((i, k) => setSeg(slot, k, now[i], emptyPos));
        slot.computeLineDistances();
        grid.material.opacity = 0.14 * a; deps.material.opacity = 0.42 * a; slot.material.opacity = 0.4 * a * (1 - ease(G));

        // The empty slot: a ring until something locks in.
        setP(iEmpty, emptyPos, NODE, 0, colors.graphite, a * (1 - ease(G * 2)));

        // Drafts: out of the centre (propose), failing (test), falling or locking in (gate), lit (approve).
        const draftPos = drafts.map((d, k) => {
            const p = now[0].clone().lerp(d, ease(P * 1.2 - k * 0.1));
            if (k === WINNER) p.lerp(emptyPos, ease(G * 1.3));
            else p.y -= ease(G * 1.3) * 3.5;
            return p;
        });
        const lit01 = ease((A - 0.45) / 0.35);
        draftPos.forEach((p, k) => {
            const failing = k !== WINNER && T > 0.55;
            const shown = P > 0.02 ? (k === WINNER ? 1 : 1 - ease(G * 1.3)) : 0;
            const c = k === WINNER ? mixC.copy(colors.ink).lerp(colors.teal, lit01) : failing ? colors.graphite : colors.ink;
            setP(iDraft + k, p, k === WINNER ? DRAFT + (NODE - DRAFT) * ease(G) : DRAFT, k === WINNER ? ease(G) : 0, c, shown);
        });
        let n = 0;
        if (P > 0.02 && G < 1) draftPos.forEach((p) => setSeg(live, n++, now[0], p));       // each draft's tie to the centre
        if (T > 0.6 && G < 0.7) FAILED.forEach((k, j) => setSeg(live, n++, draftPos[k], now[causes[j]]));   // the traces
        if (G > 0.5) slotNeighbours.forEach((i) => setSeg(live, n++, draftPos[WINNER], now[i]));            // locked in
        for (; n < 12; n++) setSeg(live, n, now[0], now[0]);
        live.material.opacity = 0.75 * a;

        // Probes: in from outside during the test; those aimed at failing drafts stop short and fade.
        probes.forEach((pr, k) => {
            const u = clamp((T - pr.delay) / 0.45);
            const passes = pr.draft === WINNER;
            const p = tmp.copy(pr.from).lerp(draftPos[pr.draft], ease(u) * (passes ? 1 : 0.8));
            const vis = T > 0 && G < 0.35 ? clamp(u * 5) * (passes ? 1 - clamp((u - 0.92) / 0.08) : 1 - clamp((u - 0.75) / 0.25) * 0.7) : 0;
            setP(iProbe + k, p, PROBE, 1, colors.ink, vis * (1 - clamp(G / 0.35)));
        });

        // Approve: one teal light descends to the kept change; it turns teal with its links, and a halo marks it.
        const fall = 1 - ease(A / 0.5);
        setP(iLight, tmp.copy(emptyPos).add(new THREE.Vector3(0.3 * fall, 5 * fall, 2.2 * fall)), 0.16, 1, colors.teal,
             A > 0 && A < 0.52 ? 1 : 0);
        setP(iHalo, emptyPos, 0.62 + 0.25 * (1 - lit01), 0, colors.teal, 0.55 * lit01);
        const litPairs = A > 0.45 ? [[draftPos[WINNER], now[0]], ...slotNeighbours.map((i) => [draftPos[WINNER], now[i]])] : [];
        for (let k = 0; k < 8; k++) { const pr = litPairs[k] || [now[0], now[0]]; setSeg(lit, k, pr[0], pr[1]); }
        lit.material.opacity = 0.95 * lit01;

        if (lit01 > 0.5 && !approved) { approved = true; document.dispatchEvent(new CustomEvent("g6:approve")); }
        if (A < 0.2) approved = false;

        // A limit in focus: its components grow, everything else fades, and the meta-harness reaches out to each.
        if (dim > 0.001) {
            for (let i = 0; i < count; i++) {
                const g = i < nodes.length ? glow[i] : 0;
                size[i] *= 1 + 0.85 * g;
                alpha[i] *= 1 - 0.82 * dim * (1 - g);
            }
            grid.material.opacity *= 1 - 0.5 * dim; deps.material.opacity *= 1 - 0.75 * dim;
            slot.material.opacity *= 1 - 0.8 * dim; live.material.opacity *= 1 - 0.8 * dim; lit.material.opacity *= 1 - 0.8 * dim;
        }
        let m = 0;
        for (let i = 1; i < nodes.length && m < 8; i++) if (glow[i] > 0.5) setSeg(reach, m++, now[0], now[i]);
        for (; m < 8; m++) setSeg(reach, m, now[0], now[0]);
        reach.material.opacity = 0.8 * dim;

        for (const l of [grid, deps, slot, live, lit, reach]) l.geometry.attributes.position.needsUpdate = true;
        for (const k of ["position", "aSize", "aFill", "aColor", "aAlpha"]) pg.attributes[k].needsUpdate = true;
        return now;
    }

    // ---- Size, scroll, pointer ------------------------------------------------------------------------------------
    let target = 0, shown = 0, px = 0, py = 0, last = [], sceneW = 1, sceneH = 1;
    function resize() {
        const r = sceneEl.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
        sceneW = r.width; sceneH = r.height;
        renderer.setPixelRatio(dpr);
        renderer.setSize(r.width, r.height, false);
        camera.aspect = r.width / Math.max(1, r.height);
        camera.position.z = camera.aspect < 0.9 ? 15 : 13;
        camera.updateProjectionMatrix();
        pm.uniforms.uScale.value = (r.height * dpr) / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)));
    }
    function scrollS() {
        const vh = innerHeight;
        let s = 0, active = -1;
        steps.forEach((el, i) => {
            const r = el.getBoundingClientRect();
            s += clamp((vh * 0.92 - r.top) / Math.max(1, r.height * 0.8));    // done by the time the text is in place
            if (r.top < vh * 0.6 && r.bottom > vh * 0.4) active = i;
        });
        steps.forEach((el, i) => el.classList.toggle("is-active", i === active));
        // The limit whose top has passed the reading line (lower on a phone, where the lattice sits above the text),
        // none once the list has gone; and how far through the list the reader is.
        focus = -1;
        if (limits.length) {
            const line = vh * (innerWidth <= 900 ? 0.66 : 0.5);
            limits.forEach((el, i) => { if (el.getBoundingClientRect().top < line) focus = i; });
            const first = limits[0].getBoundingClientRect(), end = limits[limits.length - 1].getBoundingClientRect();
            if (end.bottom < vh * 0.22) focus = -1;
            turnTarget = clamp((line - first.top) / Math.max(1, end.bottom - first.top));
            limits.forEach((el, i) => el.classList.toggle("is-active", i === focus));
        }
        return s;
    }

    // Each lit component, labelled in plain words beside it (HTML over the canvas, so it is real text).
    const labels = [];
    function placeLabels() {
        let k = 0;
        for (let i = 0; i < nodes.length; i++) {
            if (glow[i] < 0.2 || !last[i]) continue;
            const p = tmp.copy(last[i]).applyMatrix4(group.matrixWorld).project(camera);
            let el = labels[k];
            if (!el) { el = labels[k] = document.createElement("span"); el.className = "lattice-label"; labelsEl.append(el); }
            if (el.textContent !== nodes[i].title) el.textContent = nodes[i].title;
            el.style.transform = `translate(${((p.x + 1) / 2 * sceneW).toFixed(1)}px, ${((1 - p.y) / 2 * sceneH).toFixed(1)}px)`;
            el.style.opacity = glow[i].toFixed(3);
            el.hidden = false;
            k++;
        }
        for (; k < labels.length; k++) labels[k].hidden = true;
    }
    function pick(x, y, r) {
        let best = -1, bestD = 16;
        last.forEach((v, i) => {
            const p = v.clone().applyMatrix4(group.matrixWorld).project(camera);
            const d = Math.hypot((p.x + 1) / 2 * r.width - x, (1 - p.y) / 2 * r.height - y);
            if (d < bestD) { bestD = d; best = i; }
        });
        if (best < 0) { tip.hidden = true; return; }
        const nd = nodes[best], b = document.createElement("b"), g = document.createElement("span");
        b.textContent = nd.title;
        g.className = "lattice-tip__guard";
        g.textContent = "Guards against: " + nd.guards + ".";
        tip.replaceChildren(b, document.createTextNode(nd.plain + "."), g);
        tip.hidden = false;
        tip.style.left = Math.min(x + 14, r.width - 310) + "px";
        tip.style.top = Math.max(8, Math.min(y - 12, r.height - tip.offsetHeight - 8)) + "px";   // stays in the scene
    }
    sceneEl.addEventListener("pointermove", (e) => {
        const r = sceneEl.getBoundingClientRect();
        px = (e.clientX - r.left) / r.width - 0.5; py = (e.clientY - r.top) / r.height - 0.5;
        pick(e.clientX - r.left, e.clientY - r.top, r);
    });
    sceneEl.addEventListener("pointerleave", () => { tip.hidden = true; });

    // ---- Drawing: a loop only while on screen and the tab is visible; single frames under reduced motion ----------
    let visible = true, running = false, assembled = reduce ? 1 : 0;
    const t0 = performance.now();
    let tPrev = t0;
    function draw(nowT = performance.now()) {
        const t = (nowT - t0) / 1000;
        group.rotation.y = 0.36 + (reduce ? 0 : 0.18 * Math.sin(t * 0.11) + turn * 1.5) + shown * 0.06 + px * 0.12;
        group.rotation.x = 0.1 + py * 0.06;
        group.updateMatrixWorld();
        last = frame(shown, assembled);
        renderer.render(scene, camera);
        placeLabels();
    }
    function tick() {
        if (running || reduce) return;
        running = true;
        requestAnimationFrame(function loop(nowT) {
            if (!visible || document.hidden) { running = false; return; }
            const dt = Math.min(0.05, (nowT - tPrev) / 1000);
            tPrev = nowT;
            assembled = Math.min(1, assembled + dt / 1.6);
            shown += (target - shown) * Math.min(1, dt * 5);
            settle(Math.min(1, dt * 7));
            draw(nowT);
            requestAnimationFrame(loop);
        });
    }
    function now1() { settle(1); draw(); }                 // reduced motion: straight to the new state, one frame
    addEventListener("scroll", () => { target = scrollS(); if (reduce) { shown = target; now1(); } }, { passive: true });
    limits.forEach((el, i) => {
        const on = () => { hover = i; if (reduce) now1(); };
        const off = () => { if (hover === i) hover = -1; if (reduce) now1(); };
        el.addEventListener("pointerenter", on); el.addEventListener("pointerleave", off);
        el.addEventListener("focusin", on); el.addEventListener("focusout", off);
    });
    new ResizeObserver(() => { resize(); draw(); }).observe(sceneEl);
    new IntersectionObserver((e) => { visible = e[0].isIntersecting; if (visible) { tPrev = performance.now(); tick(); } }).observe(canvas);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { tPrev = performance.now(); tick(); } });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
        colors = tokens();
        scene.fog.color = colors.paper; renderer.setClearColor(colors.paper, 1); pm.uniforms.uFog.value = colors.paper;
        for (const l of [grid, deps, slot, live, reach]) l.material.color = colors.ink;
        lit.material.color = colors.teal;
        draw();
    });

    target = scrollS();
    shown = reduce ? target : 0;
    if (reduce) settle(1);
    resize();
    draw();
    sceneEl.closest(".stage").classList.add("is-live");
    tick();
}
