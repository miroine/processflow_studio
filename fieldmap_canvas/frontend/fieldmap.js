/* ProcessFlow Studio - field layout drawing (Streamlit bidirectional component, no build step).
 *
 * Draws the subsea field the way a field-layout drawing does: the host platform, templates with their well slots,
 * satellites, PLEMs, subsea stations and the DUTA, joined by colour-coded service lines (production, gas lift,
 * water injection, chemical umbilical, power / DC-FO cable) with their lengths, plus a legend, a north arrow, a
 * scale bar and a title block.  Positions are in km (east, north); symbols keep a constant screen size.
 *
 * Interaction: drag a structure to move it, drag a line to bend its route, R rotates the selected structure,
 * wheel zooms, dragging the sea pans, the legend switches services on and off.  Every change is sent to Python
 * with (session, rev) so it is applied exactly once; Python saves it with the flowsheet and never re-solves.
 */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const FONT = 'Equinor, Inter, "Segoe UI", Arial, sans-serif';
  const ORDER = ["production", "heating", "gaslift", "water", "chemical", "power"];
  const C = { ink: "#243746", muted: "#6f7a83", sea: "#eef5f9", grid: "#d8e6ef", yellow: "#F2C200",
              yellowDark: "#8a6d00", green: "#2E9E4F", red: "#E0322B", blue: "#2F6FD1", grey: "#8c959c",
              steel: "#5b6770", sel: "#007079" };

  const S = {
    d: { items: [], lines: [], services: {}, host: "", extent_km: 1, bends: {} },
    nonce: null, session: Math.random().toString(36).slice(2) + Date.now().toString(36), rev: 0,
    view: { tx: 0, ty: 0, k: 20 }, kfit: 20, fitted: false, height: 640, title: "Field layout",
    sel: null, hidden: new Set(), opts: { dist: true, info: true, slots: false, grid: true, curve: true },
    drag: null,
  };
  const svg = document.getElementById("svg");
  const wrap = document.getElementById("wrap");
  const tip = document.getElementById("tip");

  // -------------------------------------------------------------------- streamlit glue
  function post(type, data) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type }, data || {}), "*");
  }
  function send(event, extra) {
    S.rev += 1;
    post("streamlit:setComponentValue", {
      value: Object.assign({ session: S.session, rev: S.rev, event, nonce: S.nonce }, extra || {}), dataType: "json" });
  }
  window.addEventListener("message", (ev) => {
    const m = ev.data;
    if (!m || m.type !== "streamlit:render") return;
    const a = m.args || {};
    if (a.height) S.height = a.height;
    if (a.title) S.title = a.title;
    if (!S.drag) S.d = JSON.parse(JSON.stringify(a.drawing || S.d));
    S.d.bends = S.d.bends || {};
    document.getElementById("app").style.height = S.height + "px";
    post("streamlit:setFrameHeight", { height: S.height });
    if (a.nonce !== S.nonce || !S.fitted) {
      S.nonce = a.nonce;
      S.fitted = true;
      requestAnimationFrame(() => { fit(); });
    } else {
      render();
    }
  });

  // -------------------------------------------------------------------- helpers
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function text(parent, x, y, s, o) {
    o = o || {};
    const t = el("text", { x, y, "font-size": o.size || 11, "font-weight": o.weight || 400, fill: o.fill || C.ink,
      "text-anchor": o.anchor || "middle", "font-family": FONT, "paint-order": "stroke", stroke: o.halo || "#fff",
      "stroke-width": o.haloW === undefined ? 3.2 : o.haloW, "stroke-linejoin": "round", "pointer-events": "none" }, parent);
    if (o.style) t.setAttribute("font-style", o.style);
    t.textContent = s;
    return t;
  }
  const sx = (x) => S.view.tx + S.view.k * x;
  const sy = (y) => S.view.ty - S.view.k * y;
  function toKm(cx, cy) {
    const r = svg.getBoundingClientRect();
    return { x: (cx - r.left - S.view.tx) / S.view.k, y: -(cy - r.top - S.view.ty) / S.view.k };
  }
  function LU() { return S.d.units || { len: "km", f: 1 }; }
  function itemById(id) { return S.d.items.find((i) => i.id === id); }
  function symScale() { return Math.max(0.8, Math.min(1.7, Math.pow(S.view.k / (S.kfit || S.view.k), 0.3))); }
  function hash(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return h; }
  function niceStep(v) {
    const p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p;
    return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * p;
  }
  function fmtKm(v) { return v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1).replace(/\.0$/, "") : v.toFixed(2); }
  function showTip(evt, html) {
    if (!html) { tip.style.display = "none"; return; }
    const r = wrap.getBoundingClientRect();
    tip.innerHTML = html;
    tip.style.display = "block";
    const x = Math.min(evt.clientX - r.left + 14, r.width - tip.offsetWidth - 6);
    const y = Math.min(evt.clientY - r.top + 14, r.height - tip.offsetHeight - 6);
    tip.style.left = Math.max(4, x) + "px"; tip.style.top = Math.max(4, y) + "px";
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  function hint(s) {
    document.getElementById("hint").textContent = s ||
      "Drag a structure to move it · drag a line to bend its route · R rotates · click the legend to hide a service";
  }

  // -------------------------------------------------------------------- view
  function fit() {
    const r = svg.getBoundingClientRect();
    const W = Math.max(r.width, 200), H = Math.max(r.height, 200);
    const its = S.d.items.length ? S.d.items : [{ x: 0, y: 0 }];
    let x0 = Math.min(...its.map((i) => i.x)), x1 = Math.max(...its.map((i) => i.x));
    let y0 = Math.min(...its.map((i) => i.y)), y1 = Math.max(...its.map((i) => i.y));
    const span = Math.max(x1 - x0, y1 - y0, 0.5);
    const padx = 0.06 * span, pady = 0.06 * span;
    x0 -= padx; x1 += padx; y0 -= pady; y1 += pady;
    const mL = 70, mR = 70, mT = W < 640 ? 135 : 80, mB = 120;   // room for symbols, labels, legend and scale bar
    const k = Math.min((W - mL - mR) / Math.max(x1 - x0, 0.1), (H - mT - mB) / Math.max(y1 - y0, 0.1));
    S.view.k = k; S.kfit = k;
    S.view.tx = mL + (W - mL - mR - k * (x1 - x0)) / 2 - k * x0;
    S.view.ty = mT + (H - mT - mB - k * (y1 - y0)) / 2 + k * y1;
    render();
  }
  function zoomAt(cx, cy, f) {
    const r = svg.getBoundingClientRect();
    const px = cx - r.left, py = cy - r.top;
    const k = Math.max(S.kfit * 0.2, Math.min(S.kfit * 40, S.view.k * f));
    const g = k / S.view.k;
    S.view.tx = px - g * (px - S.view.tx); S.view.ty = py - g * (py - S.view.ty); S.view.k = k;
    render();
  }

  // -------------------------------------------------------------------- symbols (local px, centred at 0,0)
  function slotGrid(n) {
    const cols = n <= 3 ? n : Math.ceil(n / 2), rows = n <= 3 ? 1 : 2;
    return { cols, rows };
  }
  function drawTemplate(g, it, f) {
    const slots = it.slots || [];
    const n = Math.max(it.n_slots || slots.length || 4, slots.length, 1);
    const { cols, rows } = slotGrid(n);
    const cell = 17 * f, w = cols * cell + 12 * f, h = rows * cell + 12 * f;
    // protective structure: frame, corner piles and a light diagonal brace
    el("rect", { x: -w / 2 - 3 * f, y: -h / 2 - 3 * f, width: w + 6 * f, height: h + 6 * f, rx: 2 * f,
      fill: "none", stroke: C.yellowDark, "stroke-width": 1, "stroke-dasharray": `${3 * f} ${2 * f}` }, g);
    el("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 2 * f, fill: C.yellow, stroke: C.ink,
      "stroke-width": 1.4 }, g);
    for (const [cx, cy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]])
      el("rect", { x: cx * w / 2 - 3 * f, y: cy * h / 2 - 3 * f, width: 6 * f, height: 6 * f, fill: C.ink }, g);
    el("line", { x1: -w / 2, y1: -h / 2, x2: w / 2, y2: h / 2, stroke: C.yellowDark, "stroke-width": 0.6, opacity: 0.5 }, g);
    el("line", { x1: -w / 2, y1: h / 2, x2: w / 2, y2: -h / 2, stroke: C.yellowDark, "stroke-width": 0.6, opacity: 0.5 }, g);
    for (let k = 0; k < n; k++) {
      const s = slots[k] || { kind: "spare", name: "" };
      const c = k % cols, r = Math.floor(k / cols);
      const x = -w / 2 + 6 * f + cell * (c + 0.5), y = -h / 2 + 6 * f + cell * (r + 0.5);
      const kind = s.kind;
      if (s.gl) el("circle", { cx: x, cy: y, r: 7.2 * f, fill: "none", stroke: C.red, "stroke-width": 2 * f }, g);
      el("circle", { cx: x, cy: y, r: 5 * f, fill: kind === "producer" ? C.green : kind === "injector" ? C.blue : "#fff",
        stroke: kind === "spare" ? C.grey : C.ink, "stroke-width": 1, "stroke-dasharray": kind === "spare" ? "2 1.5" : "" }, g);
      if (kind === "injector") el("path", { d: `M${x} ${y - 3 * f}L${x} ${y + 3 * f}M${x - 2.2 * f} ${y + 0.8 * f}L${x} ${y + 3 * f}L${x + 2.2 * f} ${y + 0.8 * f}`,
        stroke: "#fff", "stroke-width": 1.1, fill: "none" }, g);
    }
    return { w: w + 6 * f, h: h + 6 * f, slotPos: (k) => {
      const c = k % cols, r = Math.floor(k / cols);
      return [-w / 2 + 6 * f + cell * (c + 0.5), -h / 2 + 6 * f + cell * (r + 0.5)];
    } };
  }
  function drawHost(g, it, f) {
    const s = 46 * f;
    for (const [cx, cy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]])
      el("rect", { x: cx * s / 2 - 5 * f, y: cy * s / 2 - 5 * f, width: 10 * f, height: 10 * f, fill: "#3d464d" }, g);
    el("rect", { x: -s / 2, y: -s / 2, width: s, height: s, fill: "#9aa3aa", stroke: C.ink, "stroke-width": 1.6, rx: 2 }, g);
    el("rect", { x: -s / 2 + 5 * f, y: -s / 2 + 5 * f, width: s * 0.5, height: s * 0.38, fill: "#c9cfd3", stroke: C.steel, "stroke-width": 0.8 }, g);
    el("rect", { x: -s / 2 + 5 * f, y: s * 0.02, width: s * 0.38, height: s * 0.4, fill: "#7d878e", stroke: C.steel, "stroke-width": 0.8 }, g);
    // helideck
    el("circle", { cx: s * 0.22, cy: s * 0.2, r: 10 * f, fill: C.green, stroke: "#fff", "stroke-width": 1.4 }, g);
    text(g, s * 0.22, s * 0.2 + 4 * f, "H", { size: 11 * f, weight: 700, fill: "#fff", haloW: 0 });
    // flare boom and crane
    el("line", { x1: s / 2, y1: -s / 2, x2: s / 2 + 18 * f, y2: -s / 2 - 18 * f, stroke: C.steel, "stroke-width": 2.4 * f }, g);
    el("path", { d: `M${s / 2 + 18 * f} ${-s / 2 - 18 * f}l4 -8 l3 9z`, fill: "#E8862A" }, g);
    el("line", { x1: -s * 0.05, y1: -s * 0.2, x2: s * 0.32, y2: -s * 0.42, stroke: C.yellow, "stroke-width": 2.4 * f }, g);
    return { w: s + 10 * f, h: s + 10 * f };
  }
  function drawSatellite(g, it, f) {
    const s = 18 * f;
    el("rect", { x: -s / 2, y: -s / 2, width: s, height: s, fill: C.yellow, stroke: C.ink, "stroke-width": 1.3, rx: 2 }, g);
    if (it.gl) el("circle", { r: 7.2 * f, fill: "none", stroke: C.red, "stroke-width": 2 * f }, g);
    el("circle", { r: 5 * f, fill: C.green, stroke: C.ink, "stroke-width": 1 }, g);
    return { w: s, h: s };
  }
  function drawPlem(g, it, f) {
    el("rect", { x: -16 * f, y: -6 * f, width: 32 * f, height: 12 * f, fill: C.yellow, stroke: C.ink, "stroke-width": 1.3, rx: 1.5 }, g);
    el("line", { x1: -8 * f, y1: -6 * f, x2: -8 * f, y2: 6 * f, stroke: C.ink, "stroke-width": 0.8 }, g);
    el("line", { x1: 8 * f, y1: -6 * f, x2: 8 * f, y2: 6 * f, stroke: C.ink, "stroke-width": 0.8 }, g);
    return { w: 32 * f, h: 12 * f };
  }
  function drawDuta(g, it, f) {
    const s = 16 * f;
    el("rect", { x: -s / 2, y: -s / 2, width: s, height: s, fill: "#D9A21B", stroke: C.ink, "stroke-width": 1.3 }, g);
    for (const d of [-4, 0, 4]) el("line", { x1: -s / 2 + 3 * f, y1: d * f, x2: s / 2 - 3 * f, y2: d * f, stroke: C.ink, "stroke-width": 0.8 }, g);
    return { w: s, h: s };
  }
  function drawStation(g, it, f) {
    const w = 34 * f, h = 24 * f;
    el("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 3 * f, fill: "#e4e8eb", stroke: C.ink, "stroke-width": 1.3 }, g);
    const k = it.kind;
    if (k === "booster") {
      el("circle", { r: 8 * f, fill: "#fff", stroke: C.ink, "stroke-width": 1.2 }, g);
      el("path", { d: `M${-4 * f} ${-5 * f}L${6 * f} 0L${-4 * f} ${5 * f}z`, fill: "#E8862A", stroke: C.ink, "stroke-width": 0.8 }, g);
    } else if (k === "compressor") {
      el("path", { d: `M${-9 * f} ${-8 * f}L${9 * f} ${-4 * f}L${9 * f} ${4 * f}L${-9 * f} ${8 * f}z`, fill: "#E8862A", stroke: C.ink, "stroke-width": 1 }, g);
    } else if (k === "separator") {
      el("rect", { x: -13 * f, y: -6 * f, width: 26 * f, height: 12 * f, rx: 6 * f, fill: "#fff", stroke: C.ink, "stroke-width": 1.2 }, g);
      el("line", { x1: -10 * f, y1: 2 * f, x2: 10 * f, y2: 2 * f, stroke: C.blue, "stroke-width": 1.2 }, g);
    } else if (k === "cooler") {
      el("path", { d: `M${-12 * f} 0 l4 -6 l4 12 l4 -12 l4 12 l4 -12 l4 6`, fill: "none", stroke: C.blue, "stroke-width": 1.5 * f }, g);
    } else if (k === "intensifier") {
      el("path", { d: `M${-10 * f} 0H${8 * f}M${3 * f} ${-5 * f}L${9 * f} 0L${3 * f} ${5 * f}`, fill: "none", stroke: C.ink, "stroke-width": 1.5 * f }, g);
    }
    return { w, h };
  }
  function drawSsiv(g, it, f) {
    el("path", { d: `M${-10 * f} ${-7 * f}L${10 * f} ${7 * f}L${10 * f} ${-7 * f}L${-10 * f} ${7 * f}z`, fill: "#fff", stroke: C.ink, "stroke-width": 1.4, "stroke-linejoin": "round" }, g);
    el("line", { x1: 0, y1: 0, x2: 0, y2: -11 * f, stroke: C.ink, "stroke-width": 1.2 }, g);
    el("rect", { x: -4 * f, y: -15 * f, width: 8 * f, height: 4 * f, fill: C.ink }, g);
    return { w: 20 * f, h: 22 * f };
  }
  function drawCimv(g, it, f) {
    el("path", { d: `M0 ${-9 * f}L${9 * f} 0L0 ${9 * f}L${-9 * f} 0z`, fill: "#fff", stroke: C.ink, "stroke-width": 1.3 }, g);
    el("circle", { r: 3 * f, fill: C.ink }, g);
    return { w: 18 * f, h: 18 * f };
  }
  const DRAW = { host: drawHost, template: drawTemplate, injector: drawTemplate, satellite: drawSatellite, plem: drawPlem,
                 duta: drawDuta, booster: drawStation, compressor: drawStation, separator: drawStation, cooler: drawStation,
                 intensifier: drawStation, ssiv: drawSsiv, cimv: drawCimv };

  // -------------------------------------------------------------------- lines
  function head(id) {
    if (id === "duta") return "host";
    const it = itemById(id);
    return (it && it.cluster) || id;
  }
  function corridor(ln) {
    // lines between two clusters share the corridor of the cluster heads (template <-> host), so production,
    // gas lift, umbilical and cable run side by side as on a layout drawing; lines inside a cluster keep their own
    let a = head(ln.a), b = head(ln.b);
    if (a === b) { a = ln.a; b = ln.b; }
    return a < b ? a + "|" + b : b + "|" + a;
  }
  function geometry() {
    // bundles: lines along the same corridor run side by side, in a fixed service order
    const groups = {};
    for (const ln of S.d.lines) {
      if (S.hidden.has(ln.service)) continue;
      const A = itemById(ln.a), B = itemById(ln.b);
      if (!A || !B) continue;
      (groups[corridor(ln)] = groups[corridor(ln)] || []).push(ln);
    }
    const out = [];
    const gap = 5.5 * symScale();
    for (const [key, lst] of Object.entries(groups)) {
      lst.sort((p, q) => ORDER.indexOf(p.service) - ORDER.indexOf(q.service));
      const n = lst.length;
      lst.forEach((ln, i) => {
        const A = itemById(ln.a), B = itemById(ln.b);
        // orient every member of the bundle the same way (corridor's first end -> second end)
        const [e0] = key.split("|");
        const fwd = (head(ln.a) === e0) || ln.a === e0;
        const P = fwd ? A : B, Q = fwd ? B : A;
        const p0 = [sx(P.x), sy(P.y)], p1 = [sx(Q.x), sy(Q.y)];
        const dx = p1[0] - p0[0], dy = p1[1] - p0[1], L = Math.hypot(dx, dy) || 1;
        const nx = -dy / L, ny = dx / L;
        let bend = S.d.bends[key];
        if (bend === undefined || bend === null) bend = (L < 90 ? 0 : 0.12) * (hash(key) % 2 ? 1 : -1);
        if (!S.opts.curve) bend = 0;
        const off = (i - (n - 1) / 2) * gap;
        const c = [(p0[0] + p1[0]) / 2 + nx * bend * L + nx * off, (p0[1] + p1[1]) / 2 + ny * bend * L + ny * off];
        const a0 = [p0[0] + nx * off, p0[1] + ny * off], a1 = [p1[0] + nx * off, p1[1] + ny * off];
        out.push({ ln, key, a0, a1, c, nx, ny, L, bend, off, n, i });
      });
    }
    return out;
  }
  function qpath(G) { return `M${G.a0[0].toFixed(1)} ${G.a0[1].toFixed(1)}Q${G.c[0].toFixed(1)} ${G.c[1].toFixed(1)} ${G.a1[0].toFixed(1)} ${G.a1[1].toFixed(1)}`; }
  function qmid(G) {
    return [0.25 * G.a0[0] + 0.5 * G.c[0] + 0.25 * G.a1[0], 0.25 * G.a0[1] + 0.5 * G.c[1] + 0.25 * G.a1[1]];
  }

  // -------------------------------------------------------------------- render
  function render() {
    const r = svg.getBoundingClientRect();
    const W = r.width || 800, H = r.height || 600;
    svg.innerHTML = "";
    svg.setAttribute("font-family", FONT);
    el("rect", { x: 0, y: 0, width: W, height: H, fill: C.sea, class: "sea" }, svg);
    if (S.opts.grid) drawGrid(W, H);
    const f = symScale();
    // ---- lines
    const gL = el("g", {}, svg);
    const geo = geometry();
    for (const G of geo) {
      const sv = S.d.services[G.ln.service] || { color: "#000" };
      const dash = sv.dashed || G.ln.dashed ? "7 4" : "";
      const w = G.ln.service === "production" ? 3 : G.ln.service === "heating" ? 1.6 : 2.2;
      el("path", { d: qpath(G), fill: "none", stroke: "#fff", "stroke-width": w + 1.6, opacity: 0.85,
        "stroke-linecap": "round" }, gL);
      el("path", { d: qpath(G), fill: "none", stroke: sv.color, "stroke-width": w, "stroke-dasharray": dash,
        "stroke-linecap": "round" }, gL);
    }
    // hit areas: one per corridor, used to bend the route
    const seen = new Set();
    for (const G of geo) {
      if (seen.has(G.key)) continue;
      seen.add(G.key);
      const mates = geo.filter((x) => x.key === G.key);
      const hit = el("path", { d: qpath(Object.assign({}, G, { a0: [G.a0[0] - G.nx * G.off, G.a0[1] - G.ny * G.off],
        a1: [G.a1[0] - G.nx * G.off, G.a1[1] - G.ny * G.off], c: [G.c[0] - G.nx * G.off, G.c[1] - G.ny * G.off] })),
        fill: "none", stroke: "transparent", "stroke-width": Math.max(14, G.n * 6 + 8), class: "hit" }, gL);
      hit.addEventListener("pointerdown", (e) => startBend(e, G));
      hit.addEventListener("pointermove", (e) => {
        if (S.drag) return;
        showTip(e, mates.map((m) => {
          const sv = S.d.services[m.ln.service] || {};
          const L = m.ln.length_km ? ` · ${(m.ln.length_km * LU().f).toFixed(1)} ${LU().len}` : "";
          const nm = m.ln.names && m.ln.names.length ? `<br><span style="opacity:.8">${esc(m.ln.names.join(" → "))}</span>` : "";
          return `<b style="color:${sv.color === "#1C1C1C" ? "#ddd" : sv.color}">■</b> ${esc(sv.label || m.ln.service)}${L}${nm}`;
        }).join("<br>") + `<br><span style="opacity:.7">Drag to bend the route</span>`);
      });
      hit.addEventListener("pointerleave", () => showTip(null));
    }
    // distance labels (production lines)
    if (S.opts.dist) {
      for (const G of geo) {
        let lab = G.ln.label;
        if (G.ln.service === "water" && !lab) {
          const A = itemById(G.ln.a), B = itemById(G.ln.b);
          const dkm = Math.hypot(A.x - B.x, A.y - B.y);
          lab = dkm > 0.2 * (S.d.extent_km || 1) ? "~" + (dkm * LU().f).toFixed(1) + " " + LU().len : "";
        }
        if ((G.ln.service !== "production" && G.ln.service !== "water") || !lab) continue;
        if (G.ln.service === "water" && geo.some((o) => o.key === G.key && o.ln.service === "production" && o.ln.label)) continue;
        const m = qmid(G);
        // on the side away from the other lines of the corridor
        const mates = geo.filter((o) => o.key === G.key && o !== G);
        let side = G.bend >= 0 ? -1 : 1;
        if (mates.length) {
          let v = 0;
          for (const o of mates) { const q = qmid(o); v += (q[0] - m[0]) * G.nx + (q[1] - m[1]) * G.ny; }
          side = v > 0 ? -1 : 1;
        }
        const offs = side * 14;
        const tx = m[0] + G.nx * offs, ty = m[1] + G.ny * offs + 4;
        text(svg, tx, ty, lab, { size: 12, weight: 700, fill: C.ink, haloW: 4 });
      }
    }
    // ---- items
    const order = ["duta", "plem", "ssiv", "cimv", "intensifier", "cooler", "separator", "compressor", "booster",
                   "satellite", "injector", "template", "host"];
    const items = S.d.items.slice().sort((p, q) => order.indexOf(p.kind) - order.indexOf(q.kind));
    for (const it of items) {
      const X = sx(it.x), Y = sy(it.y);
      const g = el("g", { transform: `translate(${X.toFixed(1)} ${Y.toFixed(1)})`, class: "item" }, svg);
      const body = el("g", { transform: `rotate(${-(it.rot || 0)})` }, g);
      const sh = el("g", { transform: "translate(1.5 2)", opacity: 0.18 }, body);
      const dim = (DRAW[it.kind] || drawStation)(body, it, f);
      // soft shadow: a blurred-looking offset box (keeps the SVG export simple)
      el("rect", { x: -dim.w / 2, y: -dim.h / 2, width: dim.w, height: dim.h, rx: 3, fill: "#000" }, sh);
      body.insertBefore(sh, body.firstChild);
      if (S.sel === it.id)
        el("rect", { x: -dim.w / 2 - 6, y: -dim.h / 2 - 6, width: dim.w + 12, height: dim.h + 12, rx: 5, fill: "none",
          stroke: C.sel, "stroke-width": 1.6, "stroke-dasharray": "5 3" }, body);
      // slot names
      if (S.opts.slots && dim.slotPos && it.slots) {
        it.slots.forEach((s, k) => {
          if (!s.name) return;
          const [px, py] = dim.slotPos(k);
          const lx = px >= 0 ? dim.w / 2 + 3 : -dim.w / 2 - 3;      // outside the frame, level with the slot
          const a = -(it.rot || 0) * Math.PI / 180;
          const qx = lx * Math.cos(a) - py * Math.sin(a), qy = lx * Math.sin(a) + py * Math.cos(a);
          text(g, qx, qy + 3.5, s.name.replace(/ .*/, ""),
            { size: 9.5, anchor: qx >= 0 ? "start" : "end", fill: "#3b4756", haloW: 3 });
        });
      }
      // labels: name above templates / host, below the small symbols
      const big = it.kind === "host" || it.kind === "template" || it.kind === "injector";
      const rad = Math.max(dim.w, dim.h) / 2 * (it.rot ? 1.2 : 1);
      const ly = big ? -rad - 8 : rad + 13;
      text(g, 0, ly, shortName(it), { size: big ? 12.5 : 11, weight: 700 });
      if (S.opts.info && it.info) text(g, 0, big ? rad + 15 : ly + 13, it.info, { size: 10, fill: C.muted });
      g.addEventListener("pointerdown", (e) => startMove(e, it));
      g.addEventListener("pointermove", (e) => {
        if (S.drag) return;
        const sl = (it.slots || []).filter((s) => s.kind !== "spare").map((s) => esc(s.name) + (s.gl ? " (gas lift)" : ""));
        showTip(e, `<b>${esc(it.name)}</b><br>${esc(KIND[it.kind] || it.kind)}${it.info ? " · " + esc(it.info) : ""}` +
          (sl.length ? `<br>Wells: ${sl.join(", ")}` : "") +
          `<br><span style="opacity:.7">E ${(it.x * LU().f).toFixed(2)} ${LU().len} · N ${(it.y * LU().f).toFixed(2)} ${LU().len}${it.rot ? " · " + Math.round(it.rot) + "°" : ""}</span>`);
      });
      g.addEventListener("pointerleave", () => showTip(null));
    }
    drawOverlays(W, H);
  }
  const KIND = { host: "Host platform", template: "Production template", injector: "Water injection template",
                 satellite: "Satellite well (XT)", plem: "PLEM / PLET", duta: "DUTA (umbilical termination)",
                 booster: "Subsea pump / booster", compressor: "Subsea compressor", separator: "Subsea separator",
                 cooler: "Subsea cooler", intensifier: "Pressure intensifier", ssiv: "SSIV",
                 cimv: "Chemical injection point" };
  function shortName(it) {
    // "TMP-100 Template" -> "TMP-100 Template"; strip long host names to their tag + first word
    const s = String(it.name || "");
    if (!["host", "template", "injector", "satellite", "duta"].includes(it.kind)) return s.split(" ")[0];
    return s.length > 28 ? s.slice(0, 27) + "…" : s;
  }

  function drawGrid(W, H) {
    const step = niceStep(70 / S.view.k);
    const g = el("g", {}, svg);
    const x0 = Math.floor(-S.view.tx / S.view.k / step) * step, x1 = (W - S.view.tx) / S.view.k;
    for (let x = x0; x <= x1; x += step)
      el("line", { x1: sx(x), y1: 0, x2: sx(x), y2: H, stroke: C.grid, "stroke-width": Math.abs(x) < 1e-9 ? 1.2 : 0.7 }, g);
    const y1 = S.view.ty / S.view.k, y0 = (S.view.ty - H) / S.view.k;
    for (let y = Math.floor(y0 / step) * step; y <= y1; y += step)
      el("line", { x1: 0, y1: sy(y), x2: W, y2: sy(y), stroke: C.grid, "stroke-width": Math.abs(y) < 1e-9 ? 1.2 : 0.7 }, g);
  }

  function drawOverlays(W, H) {
    const g = el("g", {}, svg);
    // title block (top-left)
    const tb = el("g", { transform: "translate(10 10)" }, g);
    const box = el("rect", { width: 200, height: 44, fill: "#fff", stroke: C.ink, "stroke-width": 1, rx: 3, opacity: 0.95 }, tb);
    el("rect", { width: 5, height: 44, fill: "#FF1243" }, tb);
    const t1 = text(tb, 14, 18, S.title.toUpperCase(), { size: 12, weight: 700, anchor: "start", haloW: 0 });
    const t2 = text(tb, 14, 34, `Host: ${S.d.host || "-"} · symbols not to scale`, { size: 10, anchor: "start", fill: C.muted, haloW: 0 });
    let tw = 200;
    try { tw = Math.max(tw, t1.getComputedTextLength() + 26, t2.getComputedTextLength() + 26); } catch (e) { /* not laid out */ }
    box.setAttribute("width", Math.min(tw, W - 80));
    // north arrow (top-right)
    const na = el("g", { transform: `translate(${W - 34} 46)` }, g);
    el("circle", { r: 18, fill: "#fff", stroke: C.ink, "stroke-width": 1, opacity: 0.95 }, na);
    el("path", { d: "M0 -14L6 6L0 2L-6 6z", fill: C.ink }, na);
    text(na, 0, -20 + 0, "N", { size: 11, weight: 700, haloW: 3 }).setAttribute("y", -22);
    // scale bar (bottom-right)
    const km = niceStep(120 / S.view.k * LU().f), px = km / LU().f * S.view.k;
    const narrow = W < 640;
    const sb = el("g", { transform: narrow ? `translate(${W - px - 24} 96)` : `translate(${W - px - 24} ${H - 34})` }, g);
    el("rect", { x: -8, y: -16, width: px + 16, height: 30, fill: "#fff", opacity: 0.9, rx: 3 }, sb);
    for (let i = 0; i < 4; i++)
      el("rect", { x: i * px / 4, y: 0, width: px / 4, height: 5, fill: i % 2 ? "#fff" : C.ink, stroke: C.ink, "stroke-width": 0.8 }, sb);
    text(sb, 0, -4, "0", { size: 9.5, haloW: 0 });
    text(sb, px, -4, fmtKm(km) + " " + LU().len, { size: 9.5, haloW: 0 });
    // legend (bottom-left)
    const svs = ORDER.filter((k) => S.d.services[k]);
    const extra = W < 640 ? [] : [["producer", "Producer slot"], ["gl", "Gas-lifted well"], ["injector", "Injector slot"],
                                  ["spare", "Spare slot"]];
    const lh = 17, lw = 238, LH = 24 + lh * (svs.length + Math.ceil(extra.length / 2)) + 4;
    const lg = el("g", { transform: `translate(10 ${H - LH - 10})` }, g);
    el("rect", { width: lw, height: LH, fill: "#fff", stroke: C.ink, "stroke-width": 1, rx: 3, opacity: 0.95 }, lg);
    text(lg, 10, 16, "LEGEND", { size: 10.5, weight: 700, anchor: "start", haloW: 0 });
    svs.forEach((k, i) => {
      const sv = S.d.services[k], y = 30 + i * lh;
      const row = el("g", { class: "legend-row", opacity: S.hidden.has(k) ? 0.3 : 1 }, lg);
      el("rect", { x: 4, y: y - 9, width: lw - 8, height: lh, fill: "transparent" }, row);
      el("line", { x1: 10, y1: y - 1, x2: 44, y2: y - 1, stroke: sv.color, "stroke-width": k === "production" ? 3 : 2.2,
        "stroke-dasharray": sv.dashed ? "7 4" : "" }, row);
      text(row, 52, y + 3, sv.label, { size: 10.5, anchor: "start", haloW: 0 });
      row.addEventListener("pointerdown", (e) => {
        e.stopPropagation();
        if (S.hidden.has(k)) S.hidden.delete(k); else S.hidden.add(k);
        render();
      });
    });
    extra.forEach(([k, lab], j) => {
      const x = 10 + (j % 2) * 116, y = 30 + svs.length * lh + Math.floor(j / 2) * lh;
      if (k === "gl") {
        el("circle", { cx: x + 6, cy: y - 2, r: 6, fill: "none", stroke: C.red, "stroke-width": 2 }, lg);
        el("circle", { cx: x + 6, cy: y - 2, r: 4, fill: C.green, stroke: C.ink, "stroke-width": 0.8 }, lg);
      } else {
        el("circle", { cx: x + 6, cy: y - 2, r: 4.5, fill: k === "producer" ? C.green : k === "injector" ? C.blue : "#fff",
          stroke: k === "spare" ? C.grey : C.ink, "stroke-width": 1, "stroke-dasharray": k === "spare" ? "2 1.5" : "" }, lg);
      }
      text(lg, x + 17, y + 2, lab, { size: 10, anchor: "start", haloW: 0 });
    });
    // credit
    text(g, W / 2, H - 6, "ProcessFlow Studio · educational screening drawing", { size: 9, fill: "#9aa5ad", haloW: 0 });
  }

  // -------------------------------------------------------------------- interaction
  function startMove(e, it) {
    e.stopPropagation(); e.preventDefault();
    window.focus();                     // preventDefault stops the frame taking focus; R must reach it
    S.sel = it.id;
    const p = toKm(e.clientX, e.clientY);
    S.drag = { type: "move", it, dx: it.x - p.x, dy: it.y - p.y, moved: false, x0: e.clientX, y0: e.clientY };
    svg.setPointerCapture && svg.setPointerCapture(e.pointerId);
    showTip(null);
    render();
  }
  function startBend(e, G) {
    e.stopPropagation(); e.preventDefault();
    window.focus();
    S.drag = { type: "bend", key: G.key, moved: false, x0: e.clientX, y0: e.clientY };
    svg.setPointerCapture && svg.setPointerCapture(e.pointerId);
    showTip(null);
  }
  svg.addEventListener("pointerdown", (e) => {
    if (e.button !== undefined && e.button !== 0) return;
    S.drag = { type: "pan", x0: e.clientX, y0: e.clientY, tx: S.view.tx, ty: S.view.ty, moved: false };
    svg.classList.add("panning");
    svg.setPointerCapture && svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", (e) => {
    const D = S.drag;
    if (!D) return;
    if (Math.hypot(e.clientX - D.x0, e.clientY - D.y0) > 3) D.moved = true;
    if (D.type === "pan") {
      S.view.tx = D.tx + e.clientX - D.x0; S.view.ty = D.ty + e.clientY - D.y0;
      render();
    } else if (D.type === "move" && D.moved) {
      const p = toKm(e.clientX, e.clientY);
      D.it.x = p.x + D.dx; D.it.y = p.y + D.dy;
      render();
    } else if (D.type === "bend" && D.moved) {
      const [ea, eb] = D.key.split("|");
      const A = itemById(ea), B = itemById(eb);
      if (!A || !B) return;
      const r = svg.getBoundingClientRect();
      const p0 = [sx(A.x), sy(A.y)], p1 = [sx(B.x), sy(B.y)];
      const dx = p1[0] - p0[0], dy = p1[1] - p0[1], L = Math.hypot(dx, dy) || 1;
      const nx = -dy / L, ny = dx / L;
      const mx = (p0[0] + p1[0]) / 2, my = (p0[1] + p1[1]) / 2;
      // the curve passes through the midpoint of chord+control/2, so the control offset is twice the drag offset
      const d = ((e.clientX - r.left - mx) * nx + (e.clientY - r.top - my) * ny) * 2;
      S.d.bends[D.key] = Math.max(-0.8, Math.min(0.8, d / L));
      render();
    }
  });
  function endDrag(e) {
    const D = S.drag;
    if (!D) return;
    S.drag = null;
    svg.classList.remove("panning");
    if (D.type === "pan" && !D.moved) { if (S.sel) { S.sel = null; render(); } return; }
    if (D.type === "move" && D.moved) send("move", { id: D.it.id, x: +D.it.x.toFixed(4), y: +D.it.y.toFixed(4) });
    if (D.type === "bend" && D.moved) send("bend", { pair: D.key, bend: +S.d.bends[D.key].toFixed(4) });
  }
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.15 : 1 / 1.15); }, { passive: false });
  svg.addEventListener("dblclick", (e) => { if (e.target.classList && e.target.classList.contains("sea")) fit(); });

  function rotateSel(dir) {
    const it = S.sel && itemById(S.sel);
    if (!it) { hint("Select a structure first, then rotate it"); return; }
    it.rot = (((it.rot || 0) + 15 * dir) % 360 + 360) % 360;
    render();
    send("rotate", { id: it.id, rot: it.rot });
  }
  function toggle(id, key) {
    const b = document.getElementById(id);
    b.onclick = () => { S.opts[key] = !S.opts[key]; b.classList.toggle("on", S.opts[key]); render(); };
  }
  toggle("bDist", "dist"); toggle("bInfo", "info"); toggle("bSlots", "slots"); toggle("bGrid", "grid"); toggle("bCurve", "curve");
  document.getElementById("bFit").onclick = fit;
  document.getElementById("bZin").onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(r.left + r.width / 2, r.top + r.height / 2, 1.25); };
  document.getElementById("bZout").onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(r.left + r.width / 2, r.top + r.height / 2, 1 / 1.25); };
  document.getElementById("bRot").onclick = () => rotateSel(1);
  document.getElementById("bReset").onclick = () => { S.d.bends = {}; send("reset"); hint("Layout reset to the flowsheet positions"); };
  document.getElementById("bSvg").onclick = exportSvg;
  window.addEventListener("keydown", (e) => {
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
    if (e.key.toLowerCase() === "r" && !e.ctrlKey && !e.metaKey) rotateSel(e.shiftKey ? -1 : 1);
    else if (e.key === "Escape") { S.sel = null; render(); }
  });
  window.addEventListener("resize", () => render());

  function exportSvg() {
    const saved = S.sel;
    S.sel = null; render();
    const clone = svg.cloneNode(true);
    S.sel = saved; render();
    clone.querySelectorAll(".hit").forEach((p) => p.remove());
    const r = svg.getBoundingClientRect();
    clone.setAttribute("viewBox", `0 0 ${Math.round(r.width)} ${Math.round(r.height)}`);
    clone.setAttribute("width", Math.round(r.width)); clone.setAttribute("height", Math.round(r.height));
    clone.setAttribute("xmlns", NS);
    clone.removeAttribute("id"); clone.removeAttribute("class");
    send("export_svg", { svg: new XMLSerializer().serializeToString(clone) });
    hint("SVG ready - download it under the drawing");
  }

  window.__fieldmap = { S, sx, sy, geometry };      // read-only hooks for the browser tests
  hint();
  post("streamlit:componentReady", { apiVersion: 1 });
})();
