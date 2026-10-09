/* ProcessFlow Studio - PFD canvas (Streamlit bidirectional component, no build step).
 *
 * Protocol (implemented by hand, same messages as streamlit-component-lib):
 *   -> parent: streamlit:componentReady {apiVersion: 1}
 *   <- parent: streamlit:render {args}
 *   -> parent: streamlit:setComponentValue {value, dataType: "json"}
 *   -> parent: streamlit:setFrameHeight {height}
 *
 * The canvas owns positions/connections while the user edits; Python owns
 * parameters.  Python bumps `nonce` whenever it changes the structure itself
 * (example loaded, rename, delete from a panel); the canvas then adopts the
 * model it is sent.  Every edit sent back carries (session, rev) so Python can
 * process each event exactly once.
 */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const V = { L: { x: -1, y: 0 }, R: { x: 1, y: 0 }, U: { x: 0, y: -1 }, D: { x: 0, y: 1 } };
  const MIRROR = { L: "R", R: "L", U: "U", D: "D" };

  // --------------------------------------------------------------- geometry
  const GEOM = {
    feed:       { w: 44, h: 26, ports: { out: [22, 0, "R"] } },
    product:    { w: 44, h: 26, ports: { in: [-22, 0, "L"] } },
    separator:  { w: 46, h: 92, ports: { feed: [-23, 0, "L"], vapour: [0, -46, "U"], liquid: [0, 46, "D"] },
                  energy: [23, 24, "R"] },
    separator3: { w: 124, h: 72, ports: { feed: [-62, -6, "L"], vapour: [38, -26, "U"], oil: [46, 26, "D"],
                                          water: [-5, 44, "D"] }, energy: [62, 8, "R"] },
    valve:      { w: 40, h: 36, ports: { in: [-20, 8, "L"], out: [20, 8, "R"] } },
    mixer:      { w: 40, h: 40, ports: { in: [-20, 0, "L"], out: [20, 0, "R"] } },
    splitter:   { w: 40, h: 40, ports: { in: [-20, 0, "L"], out: [20, 0, "R"] } },
    compressor: { w: 56, h: 52, ports: { in: [-28, 0, "L"], out: [28, 0, "R"] }, energy: [0, 26, "D"] },
    expander:   { w: 56, h: 52, ports: { in: [-28, 0, "L"], out: [28, 0, "R"] }, energy: [0, 26, "D"] },
    pump:       { w: 48, h: 50, ports: { in: [-24, 4, "L"], out: [24, -14, "R"] }, energy: [0, 25, "D"] },
    heater:     { w: 44, h: 44, ports: { in: [-22, 0, "L"], out: [22, 0, "R"] }, energy: [0, -22, "U"] },
    cooler:     { w: 44, h: 44, ports: { in: [-22, 0, "L"], out: [22, 0, "R"] }, energy: [0, -22, "U"] },
    aircooler:  { w: 78, h: 46, ports: { in: [-39, -8, "L"], out: [39, -8, "R"] }, energy: [0, -20, "U"] },
    hx:         { w: 104, h: 44, ports: { tube_in: [-52, 0, "L"], tube_out: [52, 0, "R"],
                                          shell_in: [26, -18, "U"], shell_out: [-26, 18, "D"] } },
    scrubber:   { w: 40, h: 104, ports: { feed: [-20, 8, "L"], vapour: [0, -52, "U"], liquid: [0, 52, "D"] } },
    teg_contactor: { w: 44, h: 110, ports: { feed: [-22, 34, "L"], dry: [0, -55, "U"], water: [22, 42, "R"] } },
    amine_contactor: { w: 44, h: 110, ports: { feed: [-22, 34, "L"], sweet: [0, -55, "U"], acid: [22, 42, "R"] } },
    relief_valve: { w: 40, h: 44, ports: { in: [-20, 10, "L"], out: [0, -22, "U"] } },
    flare:      { w: 36, h: 100, ports: { in: [-18, 40, "L"] } },
    comp_splitter: { w: 50, h: 70, ports: { feed: [-25, 0, "L"], top: [25, -20, "R"], bottom: [25, 20, "R"] }, energy: [0, 35, "D"] },
    conv_reactor: { w: 50, h: 76, ports: { feed: [-25, 0, "L"], out: [25, 0, "R"] }, energy: [0, 38, "D"] },
    eq_reactor: { w: 50, h: 76, ports: { feed: [-25, 0, "L"], out: [25, 0, "R"] }, energy: [0, 38, "D"] },
    column:     { w: 50, h: 190, ports: { feed_top: [-25, -70, "L"], feed: [-25, 0, "L"], feed_bottom: [-25, 70, "L"],
                                          overhead: [0, -95, "U"], bottoms: [0, 95, "D"], water: [25, 30, "R"] },
                  energies: [[25, -76, "R"], [25, 76, "R"]] },
    pipe:       { w: 84, h: 26, ports: { in: [-42, 0, "L"], out: [42, 0, "R"] }, energy: [0, -10, "U"] },
    recycle:    { w: 36, h: 36, ports: { in: [-18, 0, "L"], out: [18, 0, "R"] } },
    adjust:     { w: 40, h: 40, ports: {} },
    // subsea (SURF)
    well:         { w: 40, h: 76, ports: { in: [-20, 30, "L"], lift: [-20, -6, "L"], out: [20, -30, "R"] } },
    injection_well: { w: 40, h: 76, ports: { in: [-20, -30, "L"], out: [20, 30, "R"] } },
    xmas_tree:    { w: 50, h: 56, ports: { in: [-25, 18, "L"], out: [25, -8, "R"] } },
    template:     { w: 96, h: 60, ports: { in: [-48, 8, "L"], out: [48, 8, "R"] } },
    jumper:       { w: 76, h: 34, ports: { in: [-38, 10, "L"], out: [38, 10, "R"] } },
    flowline:     { w: 104, h: 30, ports: { in: [-52, 0, "L"], out: [52, 0, "R"] } },
    riser:        { w: 64, h: 96, ports: { in: [-32, 40, "L"], out: [32, -40, "R"] } },
    subsea_valve: { w: 40, h: 40, ports: { in: [-20, 10, "L"], out: [20, 10, "R"] } },
    subsea_booster: { w: 60, h: 58, ports: { in: [-30, 4, "L"], out: [30, 4, "R"] }, energy: [0, 29, "D"] },
    subsea_pump:       { w: 56, h: 58, ports: { in: [-28, 6, "L"], out: [28, -4, "R"] }, energy: [0, 29, "D"] },
    subsea_compressor: { w: 64, h: 58, ports: { in: [-32, 4, "L"], out: [32, 4, "R"] }, energy: [0, 29, "D"] },
    subsea_separator:  { w: 50, h: 100, ports: { feed: [-25, 0, "L"], vapour: [0, -50, "U"], oil: [25, 26, "R"],
                                                 water: [0, 50, "D"] } },
    subsea_cooler:     { w: 70, h: 56, ports: { in: [-35, -16, "L"], out: [35, 16, "R"] } },
    intensifier:       { w: 64, h: 40, ports: { in: [-32, 6, "L"], out: [32, 6, "R"] }, energy: [-8, -20, "U"] },
    cimv:              { w: 44, h: 44, ports: { in: [-22, 8, "L"], chem: [0, -22, "U"], out: [22, 8, "R"] } },
    // v7.6: installation area (size comes from the unit), gas turbine, ideal phase splitter
    platform:       { w: 96, h: 60, ports: {} },
    gas_turbine:    { w: 76, h: 50, ports: { fuel: [-8, -25, "U"] }, energy: [30, 25, "D"] },
    phase_splitter: { w: 56, h: 56, ports: { feed: [-28, 0, "L"], vapour: [0, -28, "U"], oil: [28, 8, "R"], water: [0, 28, "D"] },
                      energy: [22, 22, "R"] },
    // alternative orientations (key "type:orientation"; the orientation named in DEFAULT_ORIENT is the base geometry)
    "separator:h":  { w: 96, h: 46, ports: { feed: [-48, 0, "L"], vapour: [14, -23, "U"], liquid: [32, 23, "D"] },
                      energy: [48, 8, "R"] },
    "scrubber:h":   { w: 108, h: 42, ports: { feed: [-54, 5, "L"], vapour: [14, -21, "U"], liquid: [38, 21, "D"] } },
    "separator3:v": { w: 50, h: 112, ports: { feed: [-25, 0, "L"], vapour: [0, -56, "U"], oil: [25, 24, "R"],
                                              water: [0, 56, "D"] }, energy: [25, 40, "R"] },
  };
  const DEFAULT_ORIENT = { separator: "v", scrubber: "v", separator3: "h" };
  const ORIENT_NAME = { v: "vertical", h: "horizontal" };
  const MIN_SCALE = 0.4, MAX_SCALE = 4;

  function orientOf(u) { return u.orient || DEFAULT_ORIENT[u.type] || "v"; }
  function hasOrient(type) { return !!DEFAULT_ORIENT[type]; }
  function geomKey(u) { const k = u.type + ":" + orientOf(u); return GEOM[k] ? k : u.type; }
  function scaleOf(u) { const k = Number(u && u.scale); return k > 0 ? Math.min(MAX_SCALE, Math.max(MIN_SCALE, k)) : 1; }
  /** Geometry of a unit (orientation variant; a platform frame takes its size from the unit). */
  function geomOf(u) {
    if (u.type === "platform") return { w: Number(u.w) > 0 ? Number(u.w) : 520, h: Number(u.h) > 0 ? Number(u.h) : 320, ports: {} };
    return GEOM[geomKey(u)] || { w: 40, h: 40, ports: {} };
  }
  /** Width/height of a unit on the canvas (geometry x element zoom). */
  function extentOf(u) { const g = geomOf(u), k = scaleOf(u); return { w: g.w * k, h: g.h * k }; }

  function portPos(u, port) {
    const g = geomOf(u);
    const p = g && g.ports[port];
    if (!p) return null;
    const fx = u.flip ? -1 : 1, k = scaleOf(u);
    return { x: u.x + fx * p[0] * k, y: u.y + p[1] * k, dir: u.flip ? MIRROR[p[2]] : p[2] };
  }

  function energyPos(u) {
    const g = geomOf(u);
    if (!g || !g.energy) return null;
    const e = g.energy, fx = u.flip ? -1 : 1, k = scaleOf(u);
    return { x: u.x + fx * e[0] * k, y: u.y + e[1] * k, dir: u.flip ? MIRROR[e[2]] : e[2] };
  }

  function simplify(pts) {
    const out = [];
    for (const p of pts) {
      const q = out[out.length - 1];
      if (q && Math.abs(q.x - p.x) < 0.01 && Math.abs(q.y - p.y) < 0.01) continue;
      out.push({ x: p.x, y: p.y });
    }
    let changed = true;
    while (changed && out.length > 2) {
      changed = false;
      for (let i = 1; i < out.length - 1; i++) {
        const a = out[i - 1], b = out[i], c = out[i + 1];
        if ((Math.abs(a.x - b.x) < 0.01 && Math.abs(b.x - c.x) < 0.01) ||
            (Math.abs(a.y - b.y) < 0.01 && Math.abs(b.y - c.y) < 0.01)) {
          out.splice(i, 1); changed = true; break;
        }
      }
    }
    return out;
  }

  /** Orthogonal route from outlet a (outward dir da) to inlet b (outward dir db). */
  function route(a, da, b, db, detour) {
    const S = 16;
    const s = { x: a.x + V[da].x * S, y: a.y + V[da].y * S };
    const e = { x: b.x + V[db].x * S, y: b.y + V[db].y * S };
    const hA = da === "L" || da === "R", hB = db === "L" || db === "R";
    let mid = [];
    detour = detour || 60;
    if (hA && hB) {
      const fwd = (da === "R" && db === "L" && e.x >= s.x) || (da === "L" && db === "R" && e.x <= s.x);
      if (fwd) {
        const mx = (s.x + e.x) / 2;
        mid = [{ x: mx, y: s.y }, { x: mx, y: e.y }];
      } else if (da === db) {
        const mx = da === "R" ? Math.max(s.x, e.x) : Math.min(s.x, e.x);
        mid = [{ x: mx, y: s.y }, { x: mx, y: e.y }];
      } else {
        const yr = Math.max(a.y, b.y) + detour;
        mid = [{ x: s.x, y: yr }, { x: e.x, y: yr }];
      }
    } else if (!hA && !hB) {
      const fwd = (da === "D" && db === "U" && e.y >= s.y) || (da === "U" && db === "D" && e.y <= s.y);
      if (fwd) {
        const my = (s.y + e.y) / 2;
        mid = [{ x: s.x, y: my }, { x: e.x, y: my }];
      } else if (da === db) {
        const my = da === "D" ? Math.max(s.y, e.y) : Math.min(s.y, e.y);
        mid = [{ x: s.x, y: my }, { x: e.x, y: my }];
      } else {
        const xr = Math.max(a.x, b.x) + detour;
        mid = [{ x: xr, y: s.y }, { x: xr, y: e.y }];
      }
    } else if (hA && !hB) {
      mid = [{ x: e.x, y: s.y }];
    } else {
      mid = [{ x: s.x, y: e.y }];
    }
    return simplify([a, s, ...mid, e, b]);
  }

  function labelAnchor(pts) {
    let best = null, bl = -1;
    for (let i = 0; i < pts.length - 1; i++) {
      const l = Math.abs(pts[i + 1].x - pts[i].x) + Math.abs(pts[i + 1].y - pts[i].y);
      if (l > bl) { bl = l; best = i; }
    }
    if (best === null) return { x: pts[0].x, y: pts[0].y, horiz: true };
    const a = pts[best], b = pts[best + 1];
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, horiz: Math.abs(a.y - b.y) < 0.01, len: bl };
  }

  function usedNames(model) {
    const s = new Set();
    for (const u of Object.values(model.units)) s.add(u.name);
    for (const t of Object.values(model.streams)) s.add(t.name);
    return s;
  }

  function nextName(model, type, catalogue) {
    const used = usedNames(model);
    const prefix = (catalogue[type] && catalogue[type].prefix) || type.toUpperCase();
    if (type === "feed" || type === "product") {
      let n = 1; while (used.has(prefix + " " + n)) n++; return prefix + " " + n;
    }
    let n = 100; while (used.has(prefix + "-" + n)) n++; return prefix + "-" + n;
  }

  function nextStreamName(model) {
    const used = usedNames(model);
    let n = 1; while (used.has(String(n))) n++; return String(n);
  }

  // ------------------------------------------------------------ phasing (v7.6)
  // phases = [{id, name, color}] in order; an element may carry phase (id), change ("add" | "remove"), color.
  // Stage 0 = today, i = after phase i.  Presence mirrors procsim/phasing.py.
  const PHASE_COLORS = ["#e8590c", "#7048e8", "#0b7285", "#c2255c", "#5c940d", "#f08c00"];
  function phaseIndex(phases, pid) {
    if (!pid) return 0;
    return (phases || []).findIndex((p) => p.id === pid) + 1;
  }
  function presentAt(phases, el, stage) {
    const i = phaseIndex(phases, el && el.phase);
    if (!i) return true;
    return el.change === "remove" ? stage < i : stage >= i;
  }
  function presenceOf(phases, el) {
    const out = new Set();
    for (let k = 0; k <= (phases || []).length; k++) if (presentAt(phases, el, k)) out.add(k);
    return out;
  }
  function phaseColor(phases, el) {
    if (!el) return null;
    if (el.color) return el.color;
    const i = phaseIndex(phases, el.phase);
    return i ? ((phases[i - 1].color) || PHASE_COLORS[(i - 1) % PHASE_COLORS.length]) : null;
  }
  function parseDraw(txt) {
    if (!txt) return {};
    const [phase, change] = String(txt).split(":");
    return phase ? { phase, change: change === "remove" ? "remove" : "add" } : {};
  }
  function intersects(a, b) { for (const x of a) if (b.has(x)) return true; return false; }
  function streamPresence(model, phases, s) {
    const a = presenceOf(phases, s);
    for (const end of [s.src[0], s.dst[0]]) {
      const u = model.units[end];
      if (!u) continue;
      const pu = presenceOf(phases, u);
      for (const x of Array.from(a)) if (!pu.has(x)) a.delete(x);
    }
    return a;
  }

  /** Can an edge be added between (uOut,pOut) -> (uIn,pIn)?  ctx = {phases, draw}: with phasing, two streams may share a
   *  single-connection port when they are never in place together (old route / new route of a modification). */
  function canConnect(model, catalogue, src, sp, dst, dp, ctx) {
    if (!src || !dst || src === dst) return false;
    const us = model.units[src], ud = model.units[dst];
    if (!us || !ud) return false;
    const cs = catalogue[us.type].ports.out[sp], cd = catalogue[ud.type].ports.in[dp];
    if (!cs || !cd) return false;
    const phases = ctx && ctx.phases && ctx.phases.length ? ctx.phases : null;
    let cand = null;
    if (phases) {
      const draw = parseDraw(ctx.draw);
      cand = streamPresence(Object.assign({}, model, { units: model.units }), phases,
                            { phase: draw.phase, change: draw.change, src: [src, sp], dst: [dst, dp] });
    }
    for (const s of Object.values(model.streams)) {
      const sharesOut = s.src[0] === src && s.src[1] === sp, sharesIn = s.dst[0] === dst && s.dst[1] === dp;
      if (!sharesOut && !sharesIn) continue;
      if (cand && !intersects(streamPresence(model, phases, s), cand)) continue;
      if (!cs.multi && sharesOut) return false;
      if (!cd.multi && sharesIn) return false;
      if (sharesOut && sharesIn) return false;
    }
    return true;
  }

  // ------------------------------------------------------------------ icons
  const DEFS = `
  <defs>
    <linearGradient id="gV" x1="0" x2="1" y1="0" y2="0">
      <stop offset="0" stop-color="#7c8998"/><stop offset=".42" stop-color="#f8fafc"/><stop offset="1" stop-color="#8a97a6"/></linearGradient>
    <linearGradient id="gH" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#7c8998"/><stop offset=".40" stop-color="#f8fafc"/><stop offset="1" stop-color="#8a97a6"/></linearGradient>
    <linearGradient id="gB" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#4e72a8"/><stop offset=".45" stop-color="#dfe9f7"/><stop offset="1" stop-color="#43669b"/></linearGradient>
    <linearGradient id="gR" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#c2463d"/><stop offset=".45" stop-color="#ffe1dc"/><stop offset="1" stop-color="#ad372f"/></linearGradient>
    <linearGradient id="gC" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#3b7cc0"/><stop offset=".45" stop-color="#dcefff"/><stop offset="1" stop-color="#2d67a5"/></linearGradient>
    <linearGradient id="gG" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#3c9660"/><stop offset=".45" stop-color="#dcf3e4"/><stop offset="1" stop-color="#2e7b4c"/></linearGradient>
    <linearGradient id="gS" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#2848c6"/><stop offset=".45" stop-color="#a9bfff"/><stop offset="1" stop-color="#1f3aa8"/></linearGradient>
    <linearGradient id="gMot" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#59616b"/><stop offset=".5" stop-color="#b9c0c8"/><stop offset="1" stop-color="#4b525b"/></linearGradient>
    <marker id="mMat" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--mat)"/></marker>
    <marker id="mUns" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--mat-uns)"/></marker>
    <marker id="mSel" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--sel)"/></marker>
    <marker id="mEn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--energy)"/></marker>
    <pattern id="rock" width="6" height="6" patternUnits="userSpaceOnUse">
      <rect width="6" height="6" fill="#efe3c8"/><circle cx="2" cy="2" r=".9" fill="#a88f5f"/>
      <circle cx="5" cy="5" r=".7" fill="#a88f5f"/></pattern>
    <pattern id="mesh" width="4" height="4" patternUnits="userSpaceOnUse">
      <path d="M0,0 L4,4 M4,0 L0,4" stroke="#6f7c8a" stroke-width=".6"/></pattern>
    <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
      <path d="M 20 0 L 0 0 0 20" fill="none" stroke="var(--grid)" stroke-width="1"/></pattern>
  </defs>`;

  const STK = 'stroke="#2f3a46" stroke-width="1.3" stroke-linejoin="round"';
  const LIQ = "rgba(64,128,204,.38)";
  const WAT = "rgba(40,110,190,.55)";

  const ICON = {
    feed: () => `<path d="M-20,-7 L7,-7 L7,-12 L20,0 L7,12 L7,7 L-20,7 Z" fill="url(#gS)" stroke="#172a86" stroke-width="1.2"/>`,
    product: () => `<path d="M-20,-7 L7,-7 L7,-12 L20,0 L7,12 L7,7 L-20,7 Z" fill="url(#gS)" stroke="#172a86" stroke-width="1.2"/>`,
    separator: () => `
      <path d="M-20,-34 A20,11 0 0 1 20,-34 L20,34 A20,11 0 0 1 -20,34 Z" fill="url(#gV)" ${STK}/>
      <path d="M-19.3,14 L19.3,14 L19.3,34 A19.3,10.4 0 0 1 -19.3,34 Z" fill="${LIQ}"/>
      <line x1="-19" y1="14" x2="19" y2="14" stroke="#2d6db5" stroke-width="1" stroke-dasharray="3 2"/>
      <line x1="-12" y1="-38" x2="12" y2="-38" stroke="#8792a0" stroke-width="1" stroke-dasharray="2 2"/>
      <rect x="-3" y="-46" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-3" y="39" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-23" y="-3" width="4" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`,
    separator3: () => `
      <rect x="-14" y="18" width="18" height="24" rx="3" fill="url(#gV)" ${STK}/>
      <rect x="-13.3" y="26" width="16.6" height="15.3" rx="2.5" fill="${WAT}"/>
      <path d="M-46,-26 L46,-26 A12,26 0 0 1 46,26 L-46,26 A12,26 0 0 1 -46,-26 Z" fill="url(#gH)" ${STK}/>
      <path d="M-46,4 L28,4 L28,25.3 L-46,25.3 A11.3,25.3 0 0 1 -57,8 Z" fill="${LIQ}"/>
      <path d="M-40,14 L28,14 L28,25.3 L-40,25.3 Z" fill="${WAT}"/>
      <line x1="28" y1="26" x2="28" y2="0" stroke="#2f3a46" stroke-width="1.6"/>
      <path d="M31,4 L46,4 L46,25.3 L31,25.3 Z" fill="${LIQ}"/>
      <rect x="35" y="-32" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="43" y="25" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-8" y="41" width="6" height="4" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`,
    valve: () => `
      <path d="M-18,-1 L-18,17 L0,8 Z M18,-1 L18,17 L0,8 Z" fill="url(#gV)" ${STK}/>
      <line x1="0" y1="8" x2="0" y2="-7" stroke="#2f3a46" stroke-width="1.4"/>
      <path d="M-10,-7 A10,8 0 0 1 10,-7 Z" fill="url(#gB)" ${STK}/>`,
    mixer: () => `<path d="M-18,-18 L18,0 L-18,18 Z" fill="url(#gV)" ${STK}/>
      <line x1="-18" y1="0" x2="10" y2="0" stroke="#8792a0" stroke-width="1" stroke-dasharray="2 2"/>`,
    splitter: () => `<path d="M18,-18 L-18,0 L18,18 Z" fill="url(#gV)" ${STK}/>
      <line x1="-10" y1="0" x2="18" y2="0" stroke="#8792a0" stroke-width="1" stroke-dasharray="2 2"/>`,
    compressor: () => `
      <path d="M-26,-23 L26,-10 L26,10 L-26,23 Z" fill="url(#gB)" ${STK}/>
      <line x1="-18" y1="-14" x2="-18" y2="14" stroke="#34507a" stroke-width="1"/>
      <line x1="-6" y1="-11" x2="-6" y2="11" stroke="#34507a" stroke-width="1"/>
      <line x1="6" y1="-8" x2="6" y2="8" stroke="#34507a" stroke-width="1"/>
      <rect x="-2.5" y="15" width="5" height="11" fill="url(#gMot)" stroke="#2f3a46" stroke-width="1"/>`,
    expander: () => `
      <path d="M-26,-10 L26,-23 L26,23 L-26,10 Z" fill="url(#gB)" ${STK}/>
      <line x1="18" y1="-14" x2="18" y2="14" stroke="#34507a" stroke-width="1"/>
      <line x1="6" y1="-11" x2="6" y2="11" stroke="#34507a" stroke-width="1"/>
      <line x1="-6" y1="-8" x2="-6" y2="8" stroke="#34507a" stroke-width="1"/>
      <rect x="-2.5" y="15" width="5" height="11" fill="url(#gMot)" stroke="#2f3a46" stroke-width="1"/>`,
    pump: () => `
      <path d="M-15,14 L15,14 L20,24 L-20,24 Z" fill="url(#gMot)" ${STK}/>
      <path d="M6,-19 L24,-19 L24,-9 L14,-9 Z" fill="url(#gB)" ${STK}/>
      <circle cx="0" cy="0" r="18" fill="url(#gB)" ${STK}/>
      <circle cx="0" cy="0" r="5" fill="#f3f6fa" stroke="#34507a" stroke-width="1"/>
      <rect x="-24" y="1" width="7" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`,
    heater: () => `
      <circle cx="0" cy="0" r="18" fill="url(#gR)" ${STK}/>
      <polyline points="-22,0 -10,0 -5,-9 5,9 10,0 22,0" fill="none" stroke="#6d1812" stroke-width="1.8" stroke-linejoin="round"/>`,
    cooler: () => `
      <circle cx="0" cy="0" r="18" fill="url(#gC)" ${STK}/>
      <polyline points="-22,0 -10,0 -5,-9 5,9 10,0 22,0" fill="none" stroke="#123f70" stroke-width="1.8" stroke-linejoin="round"/>`,
    aircooler: () => `
      <rect x="-34" y="-18" width="68" height="20" rx="2" fill="url(#gC)" ${STK}/>
      <line x1="-34" y1="-12" x2="34" y2="-12" stroke="#1d4f86" stroke-width=".8"/>
      <line x1="-34" y1="-6" x2="34" y2="-6" stroke="#1d4f86" stroke-width=".8"/>
      <line x1="-34" y1="0" x2="34" y2="0" stroke="#1d4f86" stroke-width=".8"/>
      <line x1="-24" y1="2" x2="-24" y2="18" stroke="#2f3a46" stroke-width="1.2"/>
      <line x1="24" y1="2" x2="24" y2="18" stroke="#2f3a46" stroke-width="1.2"/>
      <ellipse cx="0" cy="11" rx="17" ry="5" fill="#eef2f6" stroke="#2f3a46" stroke-width="1.2"/>
      <path d="M0,11 L-13,8 M0,11 L13,14 M0,11 L-6,15 M0,11 L6,7" stroke="#2f3a46" stroke-width="1.2"/>
      <rect x="-3" y="15" width="6" height="7" fill="url(#gMot)" stroke="#2f3a46" stroke-width="1"/>`,
    hx: () => `
      <path d="M-40,-16 L40,-16 A8,16 0 0 1 40,16 L-40,16 A8,16 0 0 1 -40,-16 Z" fill="url(#gH)" ${STK}/>
      <rect x="-52" y="-12" width="12" height="24" rx="2" fill="url(#gV)" ${STK}/>
      <rect x="42" y="-12" width="10" height="24" rx="2" fill="url(#gV)" ${STK}/>
      <line x1="-40" y1="-7" x2="42" y2="-7" stroke="#4d5a68" stroke-width=".9"/>
      <line x1="-40" y1="0" x2="42" y2="0" stroke="#4d5a68" stroke-width=".9"/>
      <line x1="-40" y1="7" x2="42" y2="7" stroke="#4d5a68" stroke-width=".9"/>
      <line x1="-15" y1="-16" x2="-15" y2="8" stroke="#6f7c8a" stroke-width="1"/>
      <line x1="10" y1="-8" x2="10" y2="16" stroke="#6f7c8a" stroke-width="1"/>
      <rect x="23" y="-22" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-29" y="15" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`,
    teg_contactor: () => `
      <rect x="-15" y="-48" width="30" height="96" rx="14" fill="url(#gV)" ${STK}/>
      <path d="M-15,-30 L15,-30 M-15,-18 L15,-18 M-15,-6 L15,-6 M-15,6 L15,6 M-15,18 L15,18" stroke="#4d5a68" stroke-width="1"/>
      <path d="M15,-38 L24,-38" stroke="#b08400" stroke-width="2"/>
      <path d="M15,28 L24,28" stroke="#b08400" stroke-width="2"/>
      <text x="0" y="40" font-size="8" text-anchor="middle" fill="#2f3a46">TEG</text>`,
    amine_contactor: () => `
      <rect x="-15" y="-48" width="30" height="96" rx="14" fill="url(#gV)" ${STK}/>
      <path d="M-15,-30 L15,-30 M-15,-18 L15,-18 M-15,-6 L15,-6 M-15,6 L15,6 M-15,18 L15,18" stroke="#4d5a68" stroke-width="1"/>
      <path d="M15,-38 L24,-38" stroke="#7a5ea8" stroke-width="2"/>
      <path d="M15,28 L24,28" stroke="#7a5ea8" stroke-width="2"/>
      <text x="0" y="40" font-size="8" text-anchor="middle" fill="#2f3a46">AMINE</text>`,
    relief_valve: () => `
      <path d="M-14,16 L-14,-2 L0,8 L14,-2 L14,16 Z M-14,-2 L-14,-14 L14,-14 L14,-2" fill="url(#gV)" ${STK}/>
      <line x1="0" y1="-14" x2="0" y2="-22" stroke="#2f3a46" stroke-width="1.6"/>
      <path d="M-8,-14 L8,-14 L8,-22 L-8,-22 Z" fill="none" stroke="#c0392b" stroke-width="1.4"/>
      <path d="M-4,-18 L4,-18" stroke="#c0392b" stroke-width="1.4"/>`,
    flare: () => `
      <rect x="-3" y="-20" width="6" height="68" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <path d="M-9,-20 L9,-20 L5,-26 L-5,-26 Z" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <path d="M0,-50 C9,-40 8,-31 0,-27 C-8,-31 -9,-40 0,-50 Z" fill="#f2a33a" stroke="#c0392b" stroke-width="1.1"/>
      <path d="M0,-42 C4,-37 3,-32 0,-30 C-3,-32 -4,-37 0,-42 Z" fill="#fde28a"/>`,
    comp_splitter: () => `
      <rect x="-18" y="-30" width="36" height="60" rx="3" fill="url(#gV)" ${STK}/>
      <path d="M-18,0 L0,0 M0,0 L18,-20 M0,0 L18,20" stroke="#4d5a68" stroke-width="1.3" fill="none"/>
      <text x="-2" y="-18" font-size="8" text-anchor="middle" fill="#2f3a46">SPLIT</text>`,
    conv_reactor: () => `
      <rect x="-15" y="-34" width="30" height="68" rx="12" fill="url(#gV)" ${STK}/>
      <path d="M-15,-6 L15,-6 M-15,8 L15,8" stroke="#4d5a68" stroke-width="1"/>
      <text x="0" y="-12" font-size="9" text-anchor="middle" font-weight="700" fill="#2f3a46">R</text>
      <text x="0" y="24" font-size="7" text-anchor="middle" fill="#2f3a46">conv</text>`,
    eq_reactor: () => `
      <rect x="-15" y="-34" width="30" height="68" rx="12" fill="url(#gV)" ${STK}/>
      <path d="M-15,-6 L15,-6 M-15,8 L15,8" stroke="#4d5a68" stroke-width="1"/>
      <text x="0" y="-12" font-size="9" text-anchor="middle" font-weight="700" fill="#2f3a46">R</text>
      <text x="0" y="24" font-size="9" text-anchor="middle" fill="#2f3a46">⇌</text>`,
    scrubber: () => `
      <path d="M-16,-40 A16,9 0 0 1 16,-40 L16,40 A16,9 0 0 1 -16,40 Z" fill="url(#gV)" ${STK}/>
      <rect x="-15.3" y="-33" width="30.6" height="7" fill="url(#mesh)" stroke="#4d5a68" stroke-width=".8"/>
      <path d="M-15.3,26 L15.3,26 L15.3,40 A15.3,8.4 0 0 1 -15.3,40 Z" fill="${LIQ}"/>
      <line x1="-15" y1="26" x2="15" y2="26" stroke="#2d6db5" stroke-width="1" stroke-dasharray="3 2"/>
      <path d="M-15,6 L-6,6 L-6,14" fill="none" stroke="#4d5a68" stroke-width="1.2"/>
      <rect x="-3" y="-52" width="6" height="5" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-3" y="47" width="6" height="5" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`,
    column: () => {
      let trays = "";
      for (let k = 0; k < 11; k++) {
        const y = -62 + k * 12.4, left = k % 2 === 0;
        trays += `<line x1="${left ? -19 : -8}" y1="${y}" x2="${left ? 8 : 19}" y2="${y}" stroke="#4d5a68" stroke-width="1.1"/>`;
      }
      return `
      <path d="M-19,-80 A19,10 0 0 1 19,-80 L19,80 A19,10 0 0 1 -19,80 Z" fill="url(#gV)" ${STK}/>
      ${trays}
      <path d="M-18.3,70 L18.3,70 L18.3,80 A18.3,9.4 0 0 1 -18.3,80 Z" fill="${LIQ}"/>
      <rect x="-3" y="-95" width="6" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-3" y="89" width="6" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`;
    },
    pipe: () => `
      <rect x="-36" y="-6" width="72" height="12" fill="url(#gH)" ${STK}/>
      <rect x="-42" y="-10" width="6" height="20" rx="1" fill="url(#gV)" ${STK}/>
      <rect x="36" y="-10" width="6" height="20" rx="1" fill="url(#gV)" ${STK}/>
      <path d="M-24,-6 L-18,6 M-6,-6 L0,6 M12,-6 L18,6" stroke="#6f7c8a" stroke-width="1"/>`,
    recycle: () => `
      <circle cx="0" cy="0" r="16" fill="url(#gG)" ${STK}/>
      <path d="M-9,-7 A11,11 0 0 1 10,-5" fill="none" stroke="#1f5a37" stroke-width="1.4" marker-end="url(#mMat)"/>`,
    adjust: () => `<path d="M0,-19 L19,0 L0,19 L-19,0 Z" fill="url(#gG)" ${STK}/>`,
    // ---- subsea (SURF): schematic symbols -------------------------------------------------
    well: () => `
      <path d="M-18,24 L18,24 L18,36 L-18,36 Z" fill="url(#rock)" stroke="#6f5a3a" stroke-width="1"/>
      <line x1="-18" y1="-24" x2="18" y2="-24" stroke="#7a6a4f" stroke-width="1.4" stroke-dasharray="4 2"/>
      <rect x="-9" y="-24" width="18" height="52" fill="url(#gV)" ${STK}/>
      <rect x="-4" y="-24" width="8" height="52" fill="#f4f7fa" stroke="#4d5a68" stroke-width=".9"/>
      <path d="M-9,28 L-14,26 M-9,32 L-14,34 M9,28 L14,26 M9,32 L14,34" stroke="#b5451b" stroke-width="1.3"/>
      <rect x="-7" y="-36" width="14" height="12" rx="2" fill="url(#gB)" ${STK}/>
      <line x1="7" y1="-30" x2="20" y2="-30" stroke="#2f3a46" stroke-width="2"/>`,
    injection_well: () => `
      <path d="M-18,24 L18,24 L18,36 L-18,36 Z" fill="url(#rock)" stroke="#6f5a3a" stroke-width="1"/>
      <line x1="-18" y1="-24" x2="18" y2="-24" stroke="#7a6a4f" stroke-width="1.4" stroke-dasharray="4 2"/>
      <rect x="-9" y="-24" width="18" height="52" fill="url(#gV)" ${STK}/>
      <rect x="-4" y="-24" width="8" height="52" fill="#dbe9f6" stroke="#4d5a68" stroke-width=".9"/>
      <path d="M0,-14 L0,14 M0,14 L-4,7 M0,14 L4,7" fill="none" stroke="#3d5fc4" stroke-width="1.8" stroke-linecap="round"/>
      <path d="M9,28 L14,26 M9,32 L14,34 M-9,28 L-14,26 M-9,32 L-14,34" stroke="#3d5fc4" stroke-width="1.3"/>
      <rect x="-7" y="-36" width="14" height="12" rx="2" fill="url(#gB)" ${STK}/>
      <line x1="-7" y1="-30" x2="-20" y2="-30" stroke="#2f3a46" stroke-width="2"/>`,
    xmas_tree: () => `
      <rect x="-9" y="-26" width="18" height="50" rx="2" fill="url(#gV)" ${STK}/>
      <path d="M-6,-16 L6,-16 L-6,-8 L6,-8 Z" fill="url(#gB)" stroke="#2f3a46" stroke-width="1"/>
      <path d="M-6,4 L6,4 L-6,12 L6,12 Z" fill="url(#gB)" stroke="#2f3a46" stroke-width="1"/>
      <line x1="9" y1="-8" x2="25" y2="-8" stroke="#2f3a46" stroke-width="2"/>
      <path d="M12,-14 L12,-2 L19,-8 Z M26,-14 L26,-2 L19,-8 Z" fill="url(#gV)" stroke="#2f3a46" stroke-width="1"/>
      <path d="M14,-20 L23,-2 M23,-2 L22.6,-7.4 M23,-2 L18.8,-5.4" fill="none" stroke="#b5451b" stroke-width="1.4"
            stroke-linecap="round"/>
      <line x1="-25" y1="18" x2="-9" y2="18" stroke="#2f3a46" stroke-width="2"/>
      <rect x="-12" y="24" width="24" height="4" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`,
    template: () => `
      <rect x="-44" y="-22" width="88" height="44" fill="#eef2f6" ${STK}/>
      <path d="M-44,-22 L-22,22 L0,-22 L22,22 L44,-22" fill="none" stroke="#8792a0" stroke-width="1"/>
      <rect x="-44" y="4" width="88" height="8" fill="url(#gH)" ${STK}/>
      <circle cx="-30" cy="-10" r="6" fill="url(#gB)" ${STK}/><circle cx="-10" cy="-10" r="6" fill="url(#gB)" ${STK}/>
      <circle cx="10" cy="-10" r="6" fill="url(#gB)" ${STK}/><circle cx="30" cy="-10" r="6" fill="url(#gB)" ${STK}/>
      <line x1="-30" y1="-4" x2="-30" y2="4" stroke="#2f3a46"/><line x1="-10" y1="-4" x2="-10" y2="4" stroke="#2f3a46"/>
      <line x1="10" y1="-4" x2="10" y2="4" stroke="#2f3a46"/><line x1="30" y1="-4" x2="30" y2="4" stroke="#2f3a46"/>
      <path d="M-48,26 L48,26" stroke="#7a6a4f" stroke-width="1.4" stroke-dasharray="4 2"/>`,
    jumper: () => `
      <path d="M-34,10 L-24,10 L-24,-10 L-8,-10 L-8,6 L8,6 L8,-10 L24,-10 L24,10 L34,10" fill="none"
            stroke="#2f3a46" stroke-width="6" stroke-linejoin="round"/>
      <path d="M-34,10 L-24,10 L-24,-10 L-8,-10 L-8,6 L8,6 L8,-10 L24,-10 L24,10 L34,10" fill="none"
            stroke="#c9d3de" stroke-width="3.4" stroke-linejoin="round"/>
      <rect x="-38" y="4" width="6" height="12" rx="1" fill="url(#gV)" ${STK}/>
      <rect x="32" y="4" width="6" height="12" rx="1" fill="url(#gV)" ${STK}/>`,
    flowline: () => `
      <rect x="-46" y="-10" width="92" height="20" rx="3" fill="#e9d9b8" ${STK}/>
      <rect x="-46" y="-5" width="92" height="10" fill="url(#gH)" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-52" y="-12" width="6" height="24" rx="1" fill="url(#gV)" ${STK}/>
      <rect x="46" y="-12" width="6" height="24" rx="1" fill="url(#gV)" ${STK}/>
      <path d="M-30,-10 L-26,-5 M-10,-10 L-6,-5 M10,-10 L14,-5 M30,-10 L34,-5" stroke="#a08a5c" stroke-width="1"/>
      <line x1="-52" y1="15" x2="52" y2="15" stroke="#7a6a4f" stroke-width="1.4" stroke-dasharray="4 2"/>`,
    riser: () => `
      <path d="M-30,-40 q7.5,-4 15,0 t15,0 t15,0 t15,0" fill="none" stroke="#2d6db5" stroke-width="1.4"/>
      <line x1="-32" y1="46" x2="32" y2="46" stroke="#7a6a4f" stroke-width="1.4" stroke-dasharray="4 2"/>
      <path d="M-32,40 C-18,40 -14,10 -2,6 C8,3 8,24 14,18 C22,8 24,-24 26,-40 L32,-40" fill="none"
            stroke="#2f3a46" stroke-width="5" stroke-linecap="round"/>
      <path d="M-32,40 C-18,40 -14,10 -2,6 C8,3 8,24 14,18 C22,8 24,-24 26,-40 L32,-40" fill="none"
            stroke="#c9d3de" stroke-width="2.6" stroke-linecap="round"/>
      <ellipse cx="-8" cy="10" rx="4" ry="3" fill="#f2b33d" stroke="#2f3a46" stroke-width=".8"/>
      <ellipse cx="-1" cy="6" rx="4" ry="3" fill="#f2b33d" stroke="#2f3a46" stroke-width=".8"/>
      <ellipse cx="6" cy="7" rx="4" ry="3" fill="#f2b33d" stroke="#2f3a46" stroke-width=".8"/>`,
    subsea_booster: () => `
      <rect x="-22" y="-26" width="44" height="10" rx="2" fill="#f2b33d" ${STK}/>
      <line x1="-10" y1="-21" x2="10" y2="-21" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-26" y="-14" width="52" height="30" rx="6" fill="url(#gB)" ${STK}/>
      <path d="M-20,1 C-16,-9 -12,-9 -8,1 S0,11 4,1 S12,-9 16,1" fill="none" stroke="#1d4f86" stroke-width="2"/>
      <line x1="-22" y1="1" x2="22" y2="1" stroke="#34507a" stroke-width=".8" stroke-dasharray="2 2"/>
      <rect x="-30" y="-2" width="5" height="12" rx="1" fill="url(#gV)" ${STK}/>
      <rect x="25" y="-2" width="5" height="12" rx="1" fill="url(#gV)" ${STK}/>
      <rect x="-3" y="16" width="6" height="10" fill="url(#gMot)" stroke="#2f3a46" stroke-width="1"/>
      <line x1="-30" y1="26" x2="30" y2="26" stroke="#7a6a4f" stroke-width="1.4" stroke-dasharray="4 2"/>`,
    subsea_pump: () => `
      <rect x="-12" y="-28" width="24" height="10" rx="2" fill="#f2b33d" ${STK}/>
      <rect x="-3" y="-18" width="6" height="6" fill="url(#gMot)" stroke="#2f3a46" stroke-width="1"/>
      <circle cx="0" cy="2" r="16" fill="url(#gB)" ${STK}/>
      <path d="M0,2 L0,-11 M0,2 L11,9 M0,2 L-11,9" stroke="#1d4f86" stroke-width="2.2" stroke-linecap="round"/>
      <circle cx="0" cy="2" r="3" fill="#f4f7fa" stroke="#1d4f86" stroke-width="1"/>
      <rect x="-28" y="2" width="12" height="8" fill="url(#gV)" ${STK}/>
      <path d="M12,-9 L28,-9 L28,1 L15,1" fill="url(#gV)" ${STK}/>
      <line x1="-28" y1="26" x2="28" y2="26" stroke="#7a6a4f" stroke-width="1.4" stroke-dasharray="4 2"/>`,
    subsea_compressor: () => `
      <rect x="-14" y="-28" width="28" height="9" rx="2" fill="#f2b33d" ${STK}/>
      <rect x="-30" y="-17" width="60" height="34" rx="9" fill="#e9eef3" ${STK}/>
      <path d="M-22,-11 L22,-5 L22,13 L-22,19 Z" transform="translate(0,-4)" fill="url(#gB)" stroke="#2f3a46" stroke-width="1.2"/>
      <line x1="-12" y1="-9" x2="-12" y2="9" stroke="#34507a" stroke-width="1"/>
      <line x1="0" y1="-7" x2="0" y2="7" stroke="#34507a" stroke-width="1"/>
      <line x1="11" y1="-5" x2="11" y2="5" stroke="#34507a" stroke-width="1"/>
      <rect x="-3" y="17" width="6" height="9" fill="url(#gMot)" stroke="#2f3a46" stroke-width="1"/>
      <line x1="-32" y1="26" x2="32" y2="26" stroke="#7a6a4f" stroke-width="1.4" stroke-dasharray="4 2"/>`,
    subsea_separator: () => `
      <rect x="-24" y="38" width="48" height="6" fill="#f2b33d" ${STK}/>
      <path d="M-18,-36 A18,10 0 0 1 18,-36 L18,30 A18,10 0 0 1 -18,30 Z" fill="url(#gV)" ${STK}/>
      <rect x="-17.3" y="-30" width="34.6" height="6" fill="url(#mesh)" stroke="#4d5a68" stroke-width=".8"/>
      <path d="M-17.3,8 L17.3,8 L17.3,30 A17.3,9.3 0 0 1 -17.3,30 Z" fill="${LIQ}"/>
      <path d="M-17.3,22 L17.3,22 L17.3,30 A17.3,9.3 0 0 1 -17.3,30 Z" fill="${WAT}"/>
      <line x1="-17" y1="8" x2="17" y2="8" stroke="#2d6db5" stroke-width="1" stroke-dasharray="3 2"/>
      <rect x="-3" y="-50" width="6" height="5" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-3" y="44" width="6" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <line x1="-25" y1="50" x2="25" y2="50" stroke="#7a6a4f" stroke-width="1.4" stroke-dasharray="4 2"/>`,
    subsea_cooler: () => `
      <rect x="-30" y="-24" width="60" height="44" rx="3" fill="#eef5fb" ${STK}/>
      <path d="M-35,-16 L-22,-16 L-22,10 L-12,10 L-12,-16 L-2,-16 L-2,10 L8,10 L8,-16 L18,-16 L18,16 L35,16" fill="none"
            stroke="#2f3a46" stroke-width="4.5" stroke-linejoin="round"/>
      <path d="M-35,-16 L-22,-16 L-22,10 L-12,10 L-12,-16 L-2,-16 L-2,10 L8,10 L8,-16 L18,-16 L18,16 L35,16" fill="none"
            stroke="#7fb3e0" stroke-width="2.2" stroke-linejoin="round"/>
      <path d="M-26,-30 q4,-3 8,0 t8,0 t8,0 t8,0 t8,0 t8,0" fill="none" stroke="#2d6db5" stroke-width="1.2"/>`,
    intensifier: () => `
      <rect x="-26" y="-10" width="26" height="28" rx="2" fill="url(#gB)" ${STK}/>
      <rect x="0" y="-1" width="26" height="14" rx="2" fill="url(#gV)" ${STK}/>
      <line x1="-12" y1="-8" x2="-12" y2="16" stroke="#34507a" stroke-width="2"/>
      <line x1="-12" y1="6" x2="18" y2="6" stroke="#34507a" stroke-width="2"/>
      <rect x="16" y="1" width="3" height="10" fill="#34507a"/>
      <rect x="-32" y="2" width="6" height="8" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="26" y="2" width="6" height="8" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <text x="-13" y="-13" text-anchor="middle" font-size="7" font-weight="700" fill="#2f3a46">HYD</text>`,
    cimv: () => `
      <path d="M-18,0 L-18,16 L0,8 Z M18,0 L18,16 L0,8 Z" fill="url(#gV)" ${STK}/>
      <line x1="0" y1="8" x2="0" y2="-10" stroke="#2f3a46" stroke-width="1.4"/>
      <circle cx="0" cy="-12" r="7" fill="#f2b33d" ${STK}/>
      <path d="M-4,-12 L4,-12 M0,-12 L3,-15" stroke="#2f3a46" stroke-width="1.2"/>
      <line x1="0" y1="-19" x2="0" y2="-22" stroke="#2f3a46" stroke-width="1.6"/>`,
    subsea_valve: () => `
      <path d="M-18,1 L-18,19 L0,10 Z M18,1 L18,19 L0,10 Z" fill="url(#gV)" ${STK}/>
      <line x1="0" y1="10" x2="0" y2="-6" stroke="#2f3a46" stroke-width="1.4"/>
      <rect x="-9" y="-18" width="18" height="12" rx="2" fill="#f2b33d" ${STK}/>
      <path d="M-5,-12 L5,-12" stroke="#2f3a46" stroke-width="1.2"/>`,
  };
  // ---- v7.6 symbols ------------------------------------------------------------------------------------------
  Object.assign(ICON, {
    gas_turbine: () => `
      <line x1="-36" y1="0" x2="26" y2="0" stroke="#2f3a46" stroke-width="2.2"/>
      <path d="M-36,-14 L-14,-6 L-14,6 L-36,14 Z" fill="url(#gB)" ${STK}/>
      <line x1="-29" y1="-10" x2="-29" y2="10" stroke="#34507a" stroke-width="1"/>
      <line x1="-22" y1="-8" x2="-22" y2="8" stroke="#34507a" stroke-width="1"/>
      <rect x="-14" y="-12" width="14" height="24" rx="3" fill="url(#gR)" ${STK}/>
      <path d="M-7,6 C-12,0 -6,-3 -7,-8 C-2,-4 0,2 -7,6 Z" fill="#f2a33a" stroke="#c0392b" stroke-width=".8"/>
      <path d="M0,-6 L22,-16 L22,16 L0,6 Z" fill="url(#gR)" ${STK}/>
      <line x1="8" y1="-9" x2="8" y2="9" stroke="#8c2a22" stroke-width="1"/>
      <line x1="15" y1="-12" x2="15" y2="12" stroke="#8c2a22" stroke-width="1"/>
      <circle cx="30" cy="0" r="8.5" fill="url(#gG)" ${STK}/>
      <text x="30" y="3.6" text-anchor="middle" font-size="10" font-weight="700" fill="#17492c">G</text>
      <rect x="-12" y="-25" width="8" height="12" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-40" y="14" width="76" height="5" rx="1" fill="url(#gMot)" stroke="#2f3a46" stroke-width="1"/>`,
    phase_splitter: () => `
      <circle cx="0" cy="0" r="22" fill="url(#gV)" ${STK}/>
      <path d="M-21.17,6 L21.17,6 A22,22 0 0 1 -21.17,6 Z" fill="${LIQ}"/>
      <path d="M-16.97,14 L16.97,14 A22,22 0 0 1 -16.97,14 Z" fill="${WAT}"/>
      <line x1="-21" y1="6" x2="21" y2="6" stroke="#2d6db5" stroke-width="1" stroke-dasharray="3 2"/>
      <line x1="-17" y1="14" x2="17" y2="14" stroke="#1d5fa6" stroke-width="1" stroke-dasharray="3 2"/>
      <text x="0" y="-5" text-anchor="middle" font-size="9" font-weight="700" fill="#2f3a46">PS</text>
      <rect x="-3" y="-28" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-3" y="21" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="21" y="5" width="7" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-28" y="-3" width="7" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`,
    "separator:h": () => `
      <path d="M-34,-17 L34,-17 A14,17 0 0 1 34,17 L-34,17 A14,17 0 0 1 -34,-17 Z" fill="url(#gH)" ${STK}/>
      <path d="M-40,3 L40,3 L40,12 A13.5,16 0 0 1 34,16.6 L-34,16.6 A13.5,16 0 0 1 -40,5 Z" fill="${LIQ}"/>
      <line x1="-44" y1="3" x2="44" y2="3" stroke="#2d6db5" stroke-width="1" stroke-dasharray="3 2"/>
      <line x1="22" y1="-17" x2="22" y2="16" stroke="#4d5a68" stroke-width="1" stroke-dasharray="2 2"/>
      <rect x="11" y="-23" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="29" y="16" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-48" y="-3" width="7" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`,
    "scrubber:h": () => `
      <path d="M-40,-14 L40,-14 A12,14 0 0 1 40,14 L-40,14 A12,14 0 0 1 -40,-14 Z" fill="url(#gH)" ${STK}/>
      <rect x="2" y="-13.4" width="10" height="26.8" fill="url(#mesh)" stroke="#4d5a68" stroke-width=".8"/>
      <path d="M12,3 L40,3 L40,10 A12,13 0 0 1 34,13.4 L12,13.4 Z" fill="${LIQ}"/>
      <line x1="12" y1="3" x2="44" y2="3" stroke="#2d6db5" stroke-width="1" stroke-dasharray="3 2"/>
      <rect x="11" y="-20" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="35" y="13" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-54" y="2" width="7" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`,
    "separator3:v": () => `
      <path d="M-18,-44 A18,10 0 0 1 18,-44 L18,44 A18,10 0 0 1 -18,44 Z" fill="url(#gV)" ${STK}/>
      <path d="M-17.3,-6 L17.3,-6 L17.3,44 A17.3,9.3 0 0 1 -17.3,44 Z" fill="${LIQ}"/>
      <path d="M-17.3,26 L17.3,26 L17.3,44 A17.3,9.3 0 0 1 -17.3,44 Z" fill="${WAT}"/>
      <line x1="-17" y1="-6" x2="17" y2="-6" stroke="#2d6db5" stroke-width="1" stroke-dasharray="3 2"/>
      <line x1="-17" y1="26" x2="17" y2="26" stroke="#1d5fa6" stroke-width="1" stroke-dasharray="3 2"/>
      <rect x="-14" y="-38" width="28" height="5" fill="url(#mesh)" stroke="#4d5a68" stroke-width=".8"/>
      <rect x="-3" y="-56" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-3" y="49" width="6" height="7" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="-25" y="-3" width="7" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>
      <rect x="18" y="21" width="7" height="6" fill="#9aa6b3" stroke="#2f3a46" stroke-width="1"/>`,
  });

  /** Pictograms for the installation types (about 60 x 40 units around the origin). */
  const WAVE = '<path d="M-30,12 q5,-4 10,0 t10,0 t10,0 t10,0 t10,0 t10,0" fill="none" stroke="#2d6db5" stroke-width="1.3"/>';
  const PLATFORM_PICTO = {
    "Fixed jacket platform": () => `${WAVE}
      <rect x="-20" y="-16" width="40" height="8" fill="url(#gV)" ${STK}/><rect x="-12" y="-24" width="10" height="8" fill="#e9eef3" ${STK}/>
      <rect x="4" y="-22" width="8" height="6" fill="#e9eef3" ${STK}/><line x1="14" y1="-24" x2="14" y2="-40" stroke="#2f3a46" stroke-width="1.2"/>
      <path d="M-16,-8 L-22,22 M16,-8 L22,22 M-16,-8 L22,22 M16,-8 L-22,22 M-19,7 L19,7" fill="none" stroke="#2f3a46" stroke-width="1.3"/>`,
    "Gravity-based platform": () => `${WAVE}
      <rect x="-20" y="-16" width="40" height="8" fill="url(#gV)" ${STK}/><rect x="-12" y="-24" width="10" height="8" fill="#e9eef3" ${STK}/>
      <path d="M-12,-8 L-12,6 L-24,22 L24,22 L12,6 L12,-8 Z" fill="#cfd6dd" ${STK}/>`,
    "Jack-up": () => `${WAVE}
      <rect x="-22" y="-14" width="44" height="7" fill="url(#gV)" ${STK}/><rect x="-10" y="-22" width="10" height="8" fill="#e9eef3" ${STK}/>
      <path d="M-14,-7 L-14,24 M0,-7 L0,24 M14,-7 L14,24" stroke="#2f3a46" stroke-width="1.6"/>`,
    "Semi-submersible": () => `${WAVE}
      <rect x="-22" y="-20" width="44" height="8" fill="url(#gV)" ${STK}/><rect x="-8" y="-28" width="10" height="8" fill="#e9eef3" ${STK}/>
      <path d="M-15,-12 L-15,12 M15,-12 L15,12" stroke="#2f3a46" stroke-width="3"/>
      <rect x="-26" y="12" width="52" height="7" rx="3.5" fill="url(#gB)" ${STK}/>`,
    "FPSO": () => `${WAVE}
      <path d="M-28,-2 L24,-2 L30,-10 L-28,-10 Z" fill="url(#gV)" ${STK}/>
      <path d="M-30,-2 L28,-2 L22,12 L-24,12 Z" fill="url(#gB)" ${STK}/>
      <rect x="-20" y="-18" width="8" height="8" fill="#e9eef3" ${STK}/><rect x="-6" y="-16" width="8" height="6" fill="#e9eef3" ${STK}/>
      <rect x="10" y="-22" width="8" height="12" fill="#e9eef3" ${STK}/>`,
    "Spar / TLP": () => `${WAVE}
      <rect x="-20" y="-20" width="40" height="8" fill="url(#gV)" ${STK}/><rect x="-8" y="-28" width="10" height="8" fill="#e9eef3" ${STK}/>
      <rect x="-5" y="-12" width="10" height="36" rx="3" fill="url(#gB)" ${STK}/>`,
    "Subsea / seabed area": () => `
      <path d="M-30,-14 q5,-4 10,0 t10,0 t10,0 t10,0 t10,0 t10,0" fill="none" stroke="#2d6db5" stroke-width="1.3"/>
      <rect x="-14" y="6" width="28" height="12" fill="#eef2f6" ${STK}/><circle cx="-7" cy="12" r="3" fill="url(#gB)" ${STK}/>
      <circle cx="7" cy="12" r="3" fill="url(#gB)" ${STK}/><path d="M-30,22 L30,22" stroke="#7a6a4f" stroke-width="1.4" stroke-dasharray="4 2"/>`,
    "Onshore plant": () => `
      <rect x="-24" y="-4" width="16" height="22" rx="3" fill="url(#gV)" ${STK}/><rect x="-4" y="2" width="14" height="16" rx="3" fill="url(#gV)" ${STK}/>
      <rect x="14" y="-10" width="8" height="28" fill="#e9eef3" ${STK}/><line x1="-30" y1="22" x2="30" y2="22" stroke="#7a6a4f" stroke-width="1.4"/>`,
  };
  ICON.platform = () => `<g transform="scale(1)">${PLATFORM_PICTO["Fixed jacket platform"]()}</g>`;
  GEOM.platform = { w: 76, h: 62, ports: {} };

  const ICON_TEXT = { recycle: "R", adjust: "A" };

  // ----------------------------------------------------------- node export
  if (typeof document === "undefined") {
    module.exports = { GEOM, portPos, energyPos, route, simplify, labelAnchor, nextName, nextStreamName,
                       canConnect, ICON, geomOf, scaleOf, orientOf, hasOrient, extentOf, presentAt, presenceOf, phaseColor,
                       DEFAULT_ORIENT };
    return;
  }

  // ------------------------------------------------------------------ state
  const S = {
    model: { units: {}, streams: {} },
    catalogue: {},
    results: { streams: {}, units: {}, links: [] },
    nonce: null,
    session: Math.random().toString(36).slice(2) + Date.now().toString(36),
    rev: 0,
    selected: new Set(),
    view: { tx: 40, ty: 40, k: 1 },
    opts: { grid: true, res: true, energy: true },
    undo: [],
    height: 620,
    status: "",
    fitted: false,
    armed: null,     // palette type armed for click-to-place (touch)
    draw: "",        // phase of newly drawn items ("" = existing, "p1:add", "p1:remove")
    ghost: true,     // stage views: show items that are not in place as faint ghosts
  };
  const phases = () => (S.model && S.model.phases) || [];
  const stageNow = () => (S.model && Number.isInteger(S.model.stage) ? S.model.stage : -1);
  const EL_FIELDS = ["phase", "change", "color"];

  const svg = document.getElementById("svg");
  const wrap = document.getElementById("canvasWrap");

  // -------------------------------------------------------- streamlit glue
  function post(type, data) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type }, data || {}), "*");
  }
  function setHeight() { post("streamlit:setFrameHeight", { height: S.height }); }
  function structure() {
    const units = {}, streams = {};
    for (const [id, u] of Object.entries(S.model.units)) {
      const o = { type: u.type, name: u.name, x: Math.round(u.x), y: Math.round(u.y), flip: !!u.flip };
      if (hasOrient(u.type) && u.orient && u.orient !== DEFAULT_ORIENT[u.type]) o.orient = u.orient;
      if (u.scale && Math.abs(u.scale - 1) > 1e-6) o.scale = Math.round(scaleOf(u) * 1000) / 1000;
      for (const f of EL_FIELDS) if (u[f]) o[f] = u[f];
      if (u.type === "platform") { o.w = Math.round(geomOf(u).w); o.h = Math.round(geomOf(u).h); }
      units[id] = o;
    }
    for (const [id, s] of Object.entries(S.model.streams)) {
      const o = { name: s.name, src: s.src.slice(), dst: s.dst.slice() };
      for (const f of EL_FIELDS) if (s[f]) o[f] = s[f];
      streams[id] = o;
    }
    return { units, streams };
  }
  function send(event, extra) {
    S.rev += 1;
    const value = Object.assign({ session: S.session, rev: S.rev, event, nonce: S.nonce,
                                  model: structure(), selected: Array.from(S.selected) }, extra || {});
    post("streamlit:setComponentValue", { value, dataType: "json" });
  }
  function snapshot() {
    S.undo.push(JSON.stringify(structure()));
    if (S.undo.length > 60) S.undo.shift();
  }

  window.addEventListener("message", (ev) => {
    const d = ev.data;
    if (!d || d.type !== "streamlit:render") return;
    const a = d.args || {};
    S.catalogue = a.catalogue || S.catalogue;
    S.results = a.results || { streams: {}, units: {}, links: [] };
    S.status = a.status || "";
    if (a.height && a.height !== S.height) { S.height = a.height; }
    if (a.nonce !== S.nonce) {
      S.nonce = a.nonce;
      S.model = JSON.parse(JSON.stringify(a.model || { units: {}, streams: {} }));
      for (const u of Object.values(S.model.units)) u.flip = !!u.flip;
      S.model.phases = S.model.phases || [];
      S.selected = new Set((a.selected || []).filter((id) => S.model.units[id] || S.model.streams[id]));
      if (!S.fitted || a.fit) { S.fitted = true; requestAnimationFrame(fit); }
    }
    buildPalette();
    document.getElementById("app").style.height = S.height + "px";
    setHeight();
    render();
  });

  // -------------------------------------------------------------- helpers
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function toWorld(cx, cy) {
    const r = svg.getBoundingClientRect();
    return { x: (cx - r.left - S.view.tx) / S.view.k, y: (cy - r.top - S.view.ty) / S.view.k };
  }
  function snap(v) { return S.opts.grid ? Math.round(v / 10) * 10 : v; }
  function newId(p) { return p + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
  function escapeXml(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // -------------------------------------------------------------- palette
  const PAL_ORDER = ["Streams", "Facilities", "Separation", "Pressure change", "Rotating", "Heat transfer", "Piping",
                     "Subsea (SURF)", "Logical"];
  let paletteBuilt = "";
  function buildPalette() {
    const key = JSON.stringify(Object.keys(S.catalogue));
    if (key === paletteBuilt) return;
    paletteBuilt = key;
    const pal = document.getElementById("palette");
    pal.innerHTML = "";
    const groups = {};
    for (const [t, c] of Object.entries(S.catalogue)) (groups[c.category] = groups[c.category] || []).push([t, c]);
    for (const cat of PAL_ORDER.concat(Object.keys(groups).filter((g) => !PAL_ORDER.includes(g)))) {
      if (!groups[cat]) continue;
      const h = document.createElement("h4"); h.textContent = cat; pal.appendChild(h);
      for (const [t, c] of groups[cat]) {
        const item = document.createElement("div");
        item.className = "pal"; item.dataset.type = t;
        const g = GEOM[t] || { w: 40, h: 40 };
        const pad = 6, vw = g.w + pad * 2, vh = g.h + pad * 2;
        item.innerHTML = `<svg viewBox="${-vw / 2} ${-vh / 2} ${vw} ${vh}">${DEFS}${ICON[t] ? ICON[t]() : ""}` +
          (ICON_TEXT[t] ? `<text x="0" y="5" text-anchor="middle" font-size="15" font-weight="700" fill="#fff">${ICON_TEXT[t]}</text>` : "") +
          `</svg><span>${escapeXml(c.short || c.label.replace(" (feed)", "").replace(" (product)", ""))}</span>`;
        if (t === "feed") item.querySelector("span").textContent = "Feed stream";
        if (t === "product") item.querySelector("span").textContent = "Product stream";
        item.addEventListener("pointerdown", (ev) => startPaletteDrag(ev, t, item));
        pal.appendChild(item);
      }
    }
  }

  let ghost = null;
  function startPaletteDrag(ev, type, item) {
    ev.preventDefault();
    const sx = ev.clientX, sy = ev.clientY;
    let moved = false;
    const move = (e) => {
      if (!moved && Math.hypot(e.clientX - sx, e.clientY - sy) > 5) {
        moved = true;
        ghost = item.querySelector("svg").cloneNode(true);
        Object.assign(ghost.style, { position: "fixed", width: "60px", height: "50px", pointerEvents: "none",
                                     opacity: ".8", zIndex: 10 });
        document.body.appendChild(ghost);
      }
      if (ghost) { ghost.style.left = e.clientX - 30 + "px"; ghost.style.top = e.clientY - 25 + "px"; }
    };
    const up = (e) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (ghost) { ghost.remove(); ghost = null; }
      if (moved) {
        const r = svg.getBoundingClientRect();
        if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
          const w = toWorld(e.clientX, e.clientY);
          addUnit(type, snap(w.x), snap(w.y));
        }
      } else {
        // click: arm for click-to-place (works on touch screens)
        S.armed = S.armed === type ? null : type;
        document.querySelectorAll(".pal").forEach((p) => p.classList.toggle("armed", p.dataset.type === S.armed));
        hint(S.armed ? "Click on the canvas to place the " + (S.catalogue[type] || {}).label : null);
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function addUnit(type, x, y) {
    snapshot();
    const id = newId("u");
    S.model.units[id] = Object.assign({ type, name: nextName(S.model, type, S.catalogue), x, y, flip: false }, parseDraw(S.draw));
    if (type === "platform") S.model.units[id].kind = "Fixed jacket platform";
    S.selected = new Set([id]);
    render();
    send("add", { added: id });
    return id;
  }

  let hintTimer = null;
  function hint(text) {
    const h = document.getElementById("hint");
    if (text) { h.textContent = text; }
    else h.textContent = "Drag equipment from the palette · drag from a port to connect · drop on empty space for a feed/product";
    clearTimeout(hintTimer);
  }

  // ---------------------------------------------------------------- render
  let world = null;
  function render() {
    svg.innerHTML = DEFS + dynDefs();
    if (S.opts.grid) el("rect", { x: -5000, y: -5000, width: 10000, height: 10000, fill: "url(#grid)",
                                  transform: `translate(${S.view.tx % (20 * S.view.k)},${S.view.ty % (20 * S.view.k)})` }, svg)
      .setAttribute("pointer-events", "none");
    const bg = el("rect", { x: 0, y: 0, width: "100%", height: "100%", fill: "transparent", id: "bg" }, svg);
    bg.addEventListener("pointerdown", onBgDown);
    world = el("g", { transform: `translate(${S.view.tx},${S.view.ty}) scale(${S.view.k})` }, svg);
    const gPlat = el("g", {}, world), gLinks = el("g", {}, world), gStreams = el("g", {}, world), gUnits = el("g", {}, world);
    for (const [id, u] of Object.entries(S.model.units)) if (u.type === "platform") drawPlatform(id, u, gPlat);
    // adjust links
    for (const [a, b] of (S.results.links || [])) {
      const ua = S.model.units[a];
      let p = null;
      if (S.model.units[b]) p = S.model.units[b];
      else if (S.model.streams[b]) { const pts = streamPoints(S.model.streams[b]); if (pts) p = labelAnchor(pts); }
      if (ua && p) el("line", { x1: ua.x, y1: ua.y, x2: p.x, y2: p.y, stroke: "var(--adj)", "stroke-width": 1.2,
                                "stroke-dasharray": "5 4" }, gLinks);
    }
    for (const [id, s] of Object.entries(S.model.streams)) drawStream(id, s, gStreams);
    for (const [id, u] of Object.entries(S.model.units)) drawUnit(id, u, gUnits);
    document.getElementById("status").textContent = S.status;
    syncToolbar();
    for (const b of ["bGrid", "bRes", "bEnergy"]) document.getElementById(b).classList.toggle("on",
      { bGrid: S.opts.grid, bRes: S.opts.res, bEnergy: S.opts.energy }[b]);
  }

  function streamPoints(s) {
    const us = S.model.units[s.src[0]], ud = S.model.units[s.dst[0]];
    if (!us || !ud) return null;
    const a = portPos(us, s.src[1]), b = portPos(ud, s.dst[1]);
    if (!a || !b) return null;
    return route(a, a.dir, b, b.dir, Math.max(extentOf(us).h, extentOf(ud).h) / 2 + 40);
  }

  // --- phasing helpers (stage view, colours) --------------------------------------------------------------------
  function inStage(el) { const st = stageNow(); return st < 0 || presentAt(phases(), el, st); }
  function streamIn(s) {
    const us = S.model.units[s.src[0]], ud = S.model.units[s.dst[0]];
    return inStage(s) && (!us || inStage(us)) && (!ud || inStage(ud));
  }
  /** colour of a stream: its own, else the colour of an end that is part of a project phase */
  function streamColor(s) {
    const c = phaseColor(phases(), s);
    if (c) return c;
    for (const end of [s.src[0], s.dst[0]]) {
      const u = S.model.units[end];
      const cu = u && u.type !== "feed" && u.type !== "product" ? phaseColor(phases(), u) : null;
      if (cu) return cu;
    }
    const f = S.model.units[s.src[0]], pr = S.model.units[s.dst[0]];
    return (f && f.type === "feed" && phaseColor(phases(), f)) || (pr && pr.type === "product" && phaseColor(phases(), pr)) || null;
  }
  function hexRgb(c) {
    const m = /^#?([0-9a-f]{6})$/i.exec(c || "");
    if (!m) return [90, 90, 90];
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const ckey = (c) => String(c).replace("#", "");
  /** duotone filters and arrow heads for every colour in use */
  function dynDefs() {
    const cols = new Set();
    for (const u of Object.values(S.model.units)) { const c = phaseColor(phases(), u); if (c) cols.add(c); }
    for (const t of Object.values(S.model.streams)) { const c = streamColor(t); if (c) cols.add(c); }
    let out = "<defs>";
    for (const c of cols) {
      const [r, g, b] = hexRgb(c);
      const dk = [r, g, b].map((v) => (v * 0.30 / 255).toFixed(3)), lt = [r, g, b].map((v) => ((v + (255 - v) * 0.80) / 255).toFixed(3));
      out += `<filter id="tint_${ckey(c)}" color-interpolation-filters="sRGB" x="-5%" y="-5%" width="110%" height="110%">` +
        `<feColorMatrix type="matrix" values=".3 .59 .11 0 0  .3 .59 .11 0 0  .3 .59 .11 0 0  0 0 0 1 0"/>` +
        `<feComponentTransfer><feFuncR type="table" tableValues="${dk[0]} ${lt[0]}"/><feFuncG type="table" tableValues="${dk[1]} ${lt[1]}"/>` +
        `<feFuncB type="table" tableValues="${dk[2]} ${lt[2]}"/></feComponentTransfer></filter>` +
        `<marker id="mk_${ckey(c)}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">` +
        `<path d="M0,0 L10,5 L0,10 z" fill="${c}"/></marker>`;
    }
    return out + "</defs>";
  }
  function badge(el) {
    const i = phaseIndex(phases(), el.phase);
    return i ? (el.change === "remove" ? "−P" : "+P") + i : "";
  }
  function phaseTitle(el) {
    const i = phaseIndex(phases(), el.phase);
    if (!i) return "";
    return "\n" + (el.change === "remove" ? "Removed in " : "Added in ") + (phases()[i - 1].name || "phase " + i);
  }

  function drawStream(id, s, parent) {
    const pts = streamPoints(s);
    if (!pts) return;
    const present = streamIn(s);
    if (!present && !S.ghost) return;
    const r = (S.results.streams || {})[id] || {};
    const solved = !!r.solved && present;
    const sel = S.selected.has(id);
    const col = streamColor(s);
    const d = "M" + pts.map((p) => p.x + "," + p.y).join(" L");
    const g = el("g", { "data-id": id, class: present ? "" : "ghost" }, parent);
    el("path", { d, class: "streamhit" }, g);
    const attrs = { d, class: "stream", stroke: sel ? "var(--sel)" : col ? col : solved ? "var(--mat)" : "var(--mat-uns)",
                    "stroke-width": sel ? 3 : 2,
                    "marker-end": `url(#${sel ? "mSel" : col ? "mk_" + ckey(col) : solved ? "mMat" : "mUns"})` };
    if (col) attrs["stroke-dasharray"] = s.change === "remove" || (!s.phase && (S.model.units[s.src[0]] || {}).change === "remove") ? "3 4" : "9 4";
    el("path", attrs, g);
    const t = el("title", {}, g); t.textContent = s.name + (r.tip ? "\n" + r.tip : "") + phaseTitle(s);
    const us = S.model.units[s.src[0]], ud = S.model.units[s.dst[0]];
    const terminal = us.type === "feed" || ud.type === "product";
    if (!terminal && present) {
      const la = labelAnchor(pts);
      const tx = el("text", { x: la.x + (la.horiz ? 0 : 6), y: la.y + (la.horiz ? -5 : 4), class: "slabel",
                              "text-anchor": la.horiz ? "middle" : "start" }, g);
      tx.textContent = s.name + (r.warn ? "  ❄" : "") + (badge(s) ? "  " + badge(s) : "");
      if (col) tx.setAttribute("style", "fill:" + col);
      if (r.warn) { tx.setAttribute("fill", "var(--warn)"); tx.setAttribute("style", "fill:var(--warn)"); }
      // conditions only where the segment is long enough to carry them (hover shows them anyway)
      const fits = !!r.label && (la.horiz ? la.len >= r.label.length * 5.4 + 12 : la.len >= 40);
      if (S.opts.res && r.label && fits) {
        const rt = el("text", { x: la.x + (la.horiz ? 0 : 6), y: la.y + (la.horiz ? 12 : 16), class: "rlabel",
                                "text-anchor": la.horiz ? "middle" : "start" }, g);
        rt.textContent = r.label;
      }
    }
    g.addEventListener("pointerdown", (ev) => onItemDown(ev, id, "stream"));
  }

  function drawPlatform(id, u, parent) {
    const present = inStage(u);
    if (!present && !S.ghost) return;
    const g = geomOf(u), k = scaleOf(u), w = g.w, h = g.h;
    const col = phaseColor(phases(), u) || "#3b6a8f";
    const grp = el("g", { class: "unit platform" + (present ? "" : " ghost"), "data-id": id,
                          transform: `translate(${u.x},${u.y}) scale(${k})` }, parent);
    el("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 12, fill: col, "fill-opacity": 0.06, stroke: col,
                 "stroke-width": 1.8, "stroke-dasharray": u.change === "remove" ? "3 4" : "10 5", "pointer-events": "none" }, grp);
    // header strip (grab area), the frame line (grab area) - the inside stays click-through so equipment can be picked
    el("rect", { x: -w / 2, y: -h / 2, width: w, height: 32, rx: 12, fill: col, "fill-opacity": 0.15 }, grp);
    el("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 12, fill: "none", stroke: "transparent", "stroke-width": 14,
                 "pointer-events": "stroke" }, grp);
    const pic = el("g", { transform: `translate(${-w / 2 + 36},${-h / 2 + 17}) scale(.5)` }, grp);
    pic.innerHTML = (PLATFORM_PICTO[u.kind] || PLATFORM_PICTO["Fixed jacket platform"])();
    const nm = el("text", { x: -w / 2 + 70, y: -h / 2 + 15, class: "ulabel platname", "text-anchor": "start" }, grp);
    nm.textContent = u.name + (badge(u) ? "  " + badge(u) : "");
    nm.setAttribute("style", "text-anchor:start;fill:" + col);
    const sub = [u.kind || "", u.note || ""].filter(Boolean).join(" · ");
    if (sub) {
      const t2 = el("text", { x: -w / 2 + 70, y: -h / 2 + 27, class: "rlabel", "text-anchor": "start", style: "text-anchor:start" }, grp);
      t2.textContent = sub;
    }
    const tt = el("title", {}, grp); tt.textContent = u.name + (sub ? "\n" + sub : "") + phaseTitle(u);
    if (S.selected.has(id)) {
      el("rect", { x: -w / 2 - 6, y: -h / 2 - 6, width: w + 12, height: h + 12, rx: 14, class: "selbox" }, grp);
      const grip = el("rect", { x: w / 2 - 12, y: h / 2 - 12, width: 18, height: 18, rx: 3, class: "grip" }, grp);
      grip.addEventListener("pointerdown", (ev) => startResize(ev, id));
    }
    grp.addEventListener("pointerdown", (ev) => { if (!ev.target.classList.contains("grip")) onItemDown(ev, id, "unit"); });
  }

  function startResize(ev, id) {
    ev.stopPropagation(); ev.preventDefault();
    const u = S.model.units[id], g0 = geomOf(u), k = scaleOf(u);
    const w0 = toWorld(ev.clientX, ev.clientY);
    snapshot();
    const move = (e) => {
      const w = toWorld(e.clientX, e.clientY);
      u.w = Math.max(120, Math.round((g0.w + 2 * (w.x - w0.x) / k) / 10) * 10);
      u.h = Math.max(100, Math.round((g0.h + 2 * (w.y - w0.y) / k) / 10) * 10);
      render();
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      send("move");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function drawUnit(id, u, parent) {
    if (u.type === "platform") return;                   // drawn in the area layer underneath
    const present = inStage(u);
    if (!present && !S.ghost) return;
    const g = geomOf(u), k = scaleOf(u);
    const r = (S.results.units || {})[id] || {};
    const col = phaseColor(phases(), u);
    const grp = el("g", { class: "unit" + (present ? "" : " ghost"), "data-id": id,
                          transform: `translate(${u.x},${u.y})` + (k !== 1 ? ` scale(${k})` : "") }, parent);
    const st = present ? r.status : null;
    if (st && st !== "ok" && st !== "inactive") {
      const c2 = st === "error" ? "#d0342c" : st === "warning" ? "#e39a17" : "#e8c21b";
      el("rect", { x: -g.w / 2 - 5, y: -g.h / 2 - 5, width: g.w + 10, height: g.h + 10, rx: 6, class: "statusbox",
                   stroke: c2, fill: st === "error" ? "rgba(208,52,44,.07)" : "rgba(232,194,27,.08)" }, grp);
    }
    const body = el("g", { transform: u.flip ? "scale(-1,1)" : "" }, grp);
    if (col) body.setAttribute("filter", `url(#tint_${ckey(col)})`);
    const key = geomKey(u);
    body.innerHTML = ICON[key] ? ICON[key]() : (ICON[u.type] ? ICON[u.type]() : `<rect x="-20" y="-20" width="40" height="40" fill="#ccc"/>`);
    if (ICON_TEXT[u.type]) {
      const t = el("text", { x: 0, y: 5.5, "text-anchor": "middle", "font-size": 15, "font-weight": 700, fill: "#fff",
                             "pointer-events": "none" }, grp);
      t.textContent = ICON_TEXT[u.type];
    }
    if (col) {                                           // project phase: dashed frame in the phase colour, badge, strike-through if removed
      el("rect", { x: -g.w / 2 - 4, y: -g.h / 2 - 4, width: g.w + 8, height: g.h + 8, rx: 6, fill: "none", stroke: col,
                   "stroke-width": 1.6, "stroke-dasharray": u.change === "remove" ? "3 3" : "7 3", "pointer-events": "none" }, grp);
      if (u.change === "remove")
        el("path", { d: `M${-g.w / 2 - 2},${-g.h / 2 - 2} L${g.w / 2 + 2},${g.h / 2 + 2} M${g.w / 2 + 2},${-g.h / 2 - 2} L${-g.w / 2 - 2},${g.h / 2 + 2}`,
                     stroke: col, "stroke-width": 1.4, opacity: 0.7, "pointer-events": "none" }, grp);
      const bd = badge(u);
      if (bd) {
        const tb = el("text", { x: g.w / 2 + 4, y: -g.h / 2 - 7, "text-anchor": "end", "font-size": 9.5, "font-weight": 700,
                                "pointer-events": "none" }, grp);
        tb.textContent = bd; tb.setAttribute("style", "fill:" + col);
      }
    }
    // invisible hit area
    el("rect", { x: -g.w / 2, y: -g.h / 2, width: g.w, height: g.h, fill: "transparent" }, grp);
    if (S.selected.has(id))
      el("rect", { x: -g.w / 2 - 8, y: -g.h / 2 - 8, width: g.w + 16, height: g.h + 16, rx: 4, class: "selbox" }, grp);
    // label
    let ly = g.h / 2 + 14;
    if (g.energy && g.energy[2] === "D") ly += 6;
    const isTerm = u.type === "feed" || u.type === "product";
    // horizontal 3-phase vessels have nozzles underneath: put their labels above, clear of the vapour nozzle
    const above = u.type === "separator3" && orientOf(u) === "h";
    const lx = above ? (u.flip ? -28 : 28) : 0;
    const anchor = above ? (u.flip ? "start" : "end") : "middle";
    const lab = el("text", { x: lx, y: isTerm ? -12 : (above ? -g.h / 2 - 20 : ly), class: "ulabel",
                             "text-anchor": anchor }, grp);
    lab.textContent = u.name;
    if (r.label && !isTerm && present) {
      const sub = el("text", { x: lx, y: above ? -g.h / 2 - 8 : ly + 12, class: "rlabel", "text-anchor": anchor }, grp);
      sub.textContent = r.label;
    }
    if (isTerm && S.opts.res && present) {
      // terminal streams show their conditions next to the arrow
      const sid = Object.keys(S.model.streams).find((k2) => {
        const s2 = S.model.streams[k2]; return s2.src[0] === id || s2.dst[0] === id;
      });
      const rr = sid && (S.results.streams || {})[sid];
      if (rr && rr.label) {
        const t = el("text", { x: 0, y: 24, class: "rlabel", "text-anchor": "middle" }, grp);
        t.textContent = rr.label + (rr.warn ? "  ❄" : "");
        if (rr.warn) t.setAttribute("style", "fill:var(--warn)");
      }
    }
    const tt = el("title", {}, grp); tt.textContent = u.name + (r.tip ? "\n" + r.tip : "") + phaseTitle(u);
    // energy stream
    if (S.opts.energy && r.energy && r.energy.length && present) drawEnergy(u, r.energy, grp);
    // ports
    for (const [pn, p] of Object.entries(g.ports)) {
      const fx = u.flip ? -1 : 1;
      const cat = S.catalogue[u.type] || { ports: { in: {}, out: {} } };
      const isOut = !!(cat.ports.out || {})[pn];
      const multi = isOut ? (cat.ports.out[pn] || {}).multi : ((cat.ports.in || {})[pn] || {}).multi;
      const c = el("circle", { cx: fx * p[0], cy: p[1], r: 5, class: "port" + (multi ? " multi" : ""),
                               "data-port": pn, "data-dir": isOut ? "out" : "in" }, grp);
      const t = el("title", {}, c); t.textContent = pn.replace("_", " ") + (isOut ? " (outlet)" : " (inlet)");
      c.addEventListener("pointerdown", (ev) => onPortDown(ev, id, pn, isOut ? "out" : "in"));
    }
    grp.addEventListener("pointerdown", (ev) => { if (!ev.target.classList.contains("port")) onItemDown(ev, id, "unit"); });
  }

  function drawEnergy(u, list, grp) {
    const g = geomOf(u);
    const slots = g.energies || (g.energy ? [g.energy] : []);
    for (const en of list) {
      const e = slots[en.slot || 0];
      if (!e) continue;
      const fx = u.flip ? -1 : 1;
      const dir = u.flip ? MIRROR[e[2]] : e[2];
      const px = fx * e[0], py = e[1];
      const L = 30;
      const ox = px + V[dir].x * L, oy = py + V[dir].y * L;
      const into = en.dir !== "out";
      el("line", { x1: into ? ox : px, y1: into ? oy : py, x2: into ? px : ox, y2: into ? py : oy,
                   stroke: "var(--energy)", "stroke-width": 1.8, "marker-end": "url(#mEn)" }, grp);
      const t = el("text", { x: ox + (dir === "L" ? -4 : 4), y: oy + (dir === "U" ? -4 : dir === "D" ? 12 : 4),
                             class: "elabel", "text-anchor": dir === "L" ? "end" : dir === "R" ? "start" : "middle" }, grp);
      t.textContent = en.short ? en.short + (en.label ? " " + en.label : "") : en.name;
      const tt = el("title", {}, t); tt.textContent = en.name + (en.label ? ": " + en.label : "");
    }
  }

  // ------------------------------------------------------------ pointer
  let drag = null;
  let lastDown = { id: null, t: 0 };
  function onItemDown(ev, id, kind) {
    if (ev.button !== undefined && ev.button > 0) return;
    ev.stopPropagation();
    ev.preventDefault();
    const now = Date.now();
    if (lastDown.id === id && now - lastDown.t < 380) {
      // double-click / double-tap: open the property view
      lastDown = { id: null, t: 0 };
      S.selected = new Set([id]);
      render(); send("open");
      return;
    }
    lastDown = { id, t: now };
    const additive = ev.shiftKey || ev.ctrlKey || ev.metaKey;
    if (additive) { S.selected.has(id) ? S.selected.delete(id) : S.selected.add(id); }
    else if (!S.selected.has(id)) S.selected = new Set([id]);
    const start = toWorld(ev.clientX, ev.clientY);
    let moving = kind === "unit" ? Array.from(S.selected).filter((k) => S.model.units[k]) : [];
    if (!ev.altKey) {                      // an installation frame carries its equipment (Alt = move the frame alone)
      const extra = new Set(moving);
      for (const k of moving) {
        const pu = S.model.units[k];
        if (pu.type !== "platform") continue;
        const e = extentOf(pu);
        for (const [k2, u2] of Object.entries(S.model.units))
          if (u2.type !== "platform" && Math.abs(u2.x - pu.x) <= e.w / 2 && Math.abs(u2.y - pu.y) <= e.h / 2) extra.add(k2);
      }
      moving = Array.from(extra);
    }
    const orig = {};
    for (const k of moving) orig[k] = { x: S.model.units[k].x, y: S.model.units[k].y };
    drag = { kind: "move", start, orig, moved: false, snapTaken: false };
    render();
    const move = (e) => {
      const w = toWorld(e.clientX, e.clientY);
      const dx = w.x - start.x, dy = w.y - start.y;
      if (!drag.moved && Math.hypot(dx, dy) * S.view.k < 3) return;
      if (!drag.snapTaken && moving.length) { snapshot(); drag.snapTaken = true; }
      drag.moved = true;
      for (const k of moving) { S.model.units[k].x = snap(orig[k].x + dx); S.model.units[k].y = snap(orig[k].y + dy); }
      render();
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (drag && drag.moved && moving.length) send("move");
      else send("select");
      drag = null;
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function onPortDown(ev, uid, port, dir) {
    ev.stopPropagation();
    ev.preventDefault();
    const u = S.model.units[uid];
    const p = portPos(u, port);
    svg.classList.add("showports");
    // the rubber-band line must not sit under the cursor, or it hides the target port from elementFromPoint
    const line = el("path", { d: "", fill: "none", stroke: "var(--sel)", "stroke-width": 1.6,
                              "stroke-dasharray": "5 3", "pointer-events": "none" }, world);
    let target = null;
    const move = (e) => {
      const w = toWorld(e.clientX, e.clientY);
      line.setAttribute("d", `M${p.x},${p.y} L${w.x},${w.y}`);
      const hitEl = document.elementFromPoint(e.clientX, e.clientY);
      document.querySelectorAll(".port.hot").forEach((c) => c.classList.remove("hot"));
      target = null;
      if (hitEl && hitEl.classList && hitEl.classList.contains("port")) {
        const tid = hitEl.parentNode.getAttribute("data-id");
        const tport = hitEl.getAttribute("data-port"), tdir = hitEl.getAttribute("data-dir");
        const ok = tdir !== dir && (dir === "out" ? canConnect(S.model, S.catalogue, uid, port, tid, tport, phCtx())
                                                  : canConnect(S.model, S.catalogue, tid, tport, uid, port, phCtx()));
        if (ok) { hitEl.classList.add("hot"); target = { id: tid, port: tport }; }
      }
    };
    const up = (e) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      svg.classList.remove("showports");
      line.remove();
      const w = toWorld(e.clientX, e.clientY);
      const dist = Math.hypot(w.x - p.x, w.y - p.y);
      const under = document.elementFromPoint(e.clientX, e.clientY);
      const onPort = !!(under && under.classList && under.classList.contains("port"));
      if (!target && onPort) { hint("Those ports cannot be connected"); render(); return; }
      if (target) {
        snapshot();
        const sid = newId("s");
        const src = dir === "out" ? [uid, port] : [target.id, target.port];
        const dst = dir === "out" ? [target.id, target.port] : [uid, port];
        S.model.streams[sid] = Object.assign({ name: terminalName(src, dst) || nextStreamName(S.model), src, dst }, parseDraw(S.draw));
        S.selected = new Set([sid]);
        render(); send("connect");
      } else if (dist > 25) {
        // dropped on empty canvas: create a feed (from an inlet) or product (from an outlet)
        const cat = S.catalogue[u.type].ports[dir][port];
        const already = Object.values(S.model.streams).some((s) =>
          (dir === "out" ? s.src[0] === uid && s.src[1] === port : s.dst[0] === uid && s.dst[1] === port));
        if (already && !(cat && cat.multi)) { hint("That port is already connected"); render(); return; }
        snapshot();
        const type = dir === "out" ? "product" : "feed";
        const tid = newId("u");
        const nm = nextName(S.model, type, S.catalogue);
        S.model.units[tid] = Object.assign({ type, name: nm, x: snap(w.x), y: snap(w.y), flip: false }, parseDraw(S.draw));
        const sid = newId("s");
        S.model.streams[sid] = Object.assign(dir === "out" ? { name: nm, src: [uid, port], dst: [tid, "in"] }
                                                           : { name: nm, src: [tid, "out"], dst: [uid, port] }, parseDraw(S.draw));
        S.selected = new Set([tid]);
        render(); send("connect");
      } else render();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function phCtx() { return { phases: phases(), draw: S.draw }; }

  function terminalName(src, dst) {
    const us = S.model.units[src[0]], ud = S.model.units[dst[0]];
    if (us && us.type === "feed") return us.name;
    if (ud && ud.type === "product") return ud.name;
    return null;
  }

  function onBgDown(ev) {
    if (S.armed) {
      const w = toWorld(ev.clientX, ev.clientY);
      const t = S.armed; S.armed = null;
      document.querySelectorAll(".pal").forEach((p) => p.classList.remove("armed"));
      hint(null);
      addUnit(t, snap(w.x), snap(w.y));
      return;
    }
    const box = ev.shiftKey;
    const sx = ev.clientX, sy = ev.clientY, tx0 = S.view.tx, ty0 = S.view.ty;
    const w0 = toWorld(sx, sy);
    let moved = false, rect = null;
    const move = (e) => {
      if (Math.hypot(e.clientX - sx, e.clientY - sy) > 3) moved = true;
      if (!moved) return;
      if (box) {
        const w = toWorld(e.clientX, e.clientY);
        if (!rect) rect = el("rect", { class: "selbox" }, world);
        rect.setAttribute("x", Math.min(w.x, w0.x)); rect.setAttribute("y", Math.min(w.y, w0.y));
        rect.setAttribute("width", Math.abs(w.x - w0.x)); rect.setAttribute("height", Math.abs(w.y - w0.y));
      } else {
        S.view.tx = tx0 + e.clientX - sx; S.view.ty = ty0 + e.clientY - sy;
        world.setAttribute("transform", `translate(${S.view.tx},${S.view.ty}) scale(${S.view.k})`);
      }
    };
    const up = (e) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (box && moved) {
        const w = toWorld(e.clientX, e.clientY);
        const x1 = Math.min(w.x, w0.x), x2 = Math.max(w.x, w0.x), y1 = Math.min(w.y, w0.y), y2 = Math.max(w.y, w0.y);
        S.selected = new Set(Object.entries(S.model.units)
          .filter(([, u]) => u.x >= x1 && u.x <= x2 && u.y >= y1 && u.y <= y2).map(([k]) => k));
        render(); send("select");
      } else if (!moved) {
        if (S.selected.size) { S.selected = new Set(); render(); send("select"); }
      } else render();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // pointer handlers call preventDefault (no text selection / native drag), which also stops the browser
  // from focusing the component iframe - focus it explicitly so Delete / F / Ctrl+Z / Ctrl+D reach us
  document.addEventListener("pointerdown", () => { try { window.focus(); } catch (e) { /* ignore */ } }, true);

  svg.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    if (ev.altKey) {                                     // Alt + wheel over an element: zoom that element only
      const host = ev.target && ev.target.closest ? ev.target.closest(".unit") : null;
      const id = host && host.getAttribute("data-id");
      if (id && S.model.units[id]) {
        const ids = S.selected.has(id) ? Array.from(S.selected).filter((k) => S.model.units[k]) : [id];
        scaleUnits(ids, ev.deltaY < 0 ? 1.1 : 1 / 1.1);
        return;
      }
    }
    zoomAt(ev.clientX, ev.clientY, ev.deltaY < 0 ? 1.12 : 1 / 1.12);
  }, { passive: false });

  function zoomAt(cx, cy, f) {
    const r = svg.getBoundingClientRect();
    const k2 = Math.min(3, Math.max(0.2, S.view.k * f));
    const px = cx - r.left, py = cy - r.top;
    S.view.tx = px - (px - S.view.tx) * (k2 / S.view.k);
    S.view.ty = py - (py - S.view.ty) * (k2 / S.view.k);
    S.view.k = k2;
    render();
  }

  function fit() {
    const us = Object.values(S.model.units);
    const r = svg.getBoundingClientRect();
    if (!us.length || !r.width) { S.view = { tx: 40, ty: 40, k: 1 }; render(); return; }
    let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    for (const u of us) {
      const g = extentOf(u);
      const padx = (u.type === "feed" || u.type === "product") ? 110 : 50;
      x1 = Math.min(x1, u.x - g.w / 2 - padx); x2 = Math.max(x2, u.x + g.w / 2 + padx);
      y1 = Math.min(y1, u.y - g.h / 2 - 40); y2 = Math.max(y2, u.y + g.h / 2 + 70);
    }
    const k = Math.min(1.6, Math.max(0.2, Math.min(r.width / (x2 - x1), r.height / (y2 - y1))));
    S.view = { k, tx: (r.width - (x2 - x1) * k) / 2 - x1 * k, ty: (r.height - (y2 - y1) * k) / 2 - y1 * k };
    render();
  }

  // ---------------------------------------------------------------- actions
  let clipboard = null;
  function copySelection() {
    const ids = Array.from(S.selected).filter((k) => S.model.units[k]);
    if (!ids.length) return false;
    const set = new Set(ids);
    clipboard = {
      units: ids.map((k) => [k, JSON.parse(JSON.stringify(S.model.units[k]))]),
      streams: Object.values(S.model.streams).filter((s) => set.has(s.src[0]) && set.has(s.dst[0]))
        .map((s) => JSON.parse(JSON.stringify(s))),
    };
    hint(ids.length + " object(s) copied - Ctrl+V to paste");
    return true;
  }
  /** fields that describe how an element looks / which project phase it belongs to (copied with it) */
  function appearance(u) {
    const o = {};
    for (const f of EL_FIELDS) if (u[f]) o[f] = u[f];
    for (const f of ["orient", "scale", "kind", "note", "w", "h"]) if (u[f] !== undefined && u[f] !== "") o[f] = u[f];
    return o;
  }
  function pasteClipboard() {
    if (!clipboard || !clipboard.units.length) return;
    snapshot();
    const map = {}, copies = {};
    for (const [old, u] of clipboard.units) {
      const id = newId("u");
      map[old] = id;
      copies[id] = old;
      S.model.units[id] = Object.assign({ type: u.type, name: nextName(S.model, u.type, S.catalogue), x: u.x + 40, y: u.y + 40,
                                          flip: !!u.flip }, appearance(u));
    }
    for (const s of clipboard.streams) {
      const sid = newId("s");
      const src = [map[s.src[0]], s.src[1]], dst = [map[s.dst[0]], s.dst[1]];
      S.model.streams[sid] = Object.assign({ name: terminalName(src, dst) || nextStreamName(S.model), src, dst }, appearance(s));
    }
    // shift the clipboard so repeated pastes cascade
    for (const pair of clipboard.units) { pair[1].x += 40; pair[1].y += 40; }
    S.selected = new Set(Object.values(map));
    render(); send("paste", { copies });
  }
  function duplicateSelection() { if (copySelection()) pasteClipboard(); }

  function deleteSelection() {
    if (!S.selected.size) return;
    snapshot();
    for (const id of S.selected) {
      if (S.model.units[id]) {
        delete S.model.units[id];
        for (const [sid, s] of Object.entries(S.model.streams))
          if (s.src[0] === id || s.dst[0] === id) delete S.model.streams[sid];
      }
      delete S.model.streams[id];
    }
    S.selected = new Set();
    render(); send("delete");
  }
  function flipSelection() {
    const ids = Array.from(S.selected).filter((k) => S.model.units[k]);
    if (!ids.length) return;
    snapshot();
    for (const k of ids) S.model.units[k].flip = !S.model.units[k].flip;
    render(); send("move");
  }
  // ---- v7.6: element zoom, vessel orientation, project phase and colour of the selection ---------------------------
  let sendTimer = null, scaleSnapAt = 0;
  function sendSoon(ev) { clearTimeout(sendTimer); sendTimer = setTimeout(() => send(ev), 350); }
  function scaleUnits(ids, f, absolute) {
    ids = ids.filter((k) => S.model.units[k]);
    if (!ids.length) return;
    if (Date.now() - scaleSnapAt > 1500) snapshot();            // one undo step for a burst of wheel notches
    scaleSnapAt = Date.now();
    for (const k of ids) {
      const u = S.model.units[k];
      u.scale = absolute ? absolute : Math.round(Math.min(MAX_SCALE, Math.max(MIN_SCALE, scaleOf(u) * f)) * 100) / 100;
    }
    render(); sendSoon("move");
  }
  function sizeSelection(f, absolute) { scaleUnits(Array.from(S.selected), f, absolute); }
  function orientSelection() {
    const ids = Array.from(S.selected).filter((k) => S.model.units[k] && hasOrient(S.model.units[k].type));
    if (!ids.length) { hint("Select a separator or scrubber, then press Orient (O) to turn it vertical / horizontal"); return; }
    snapshot();
    for (const k of ids) {
      const u = S.model.units[k], def = DEFAULT_ORIENT[u.type];
      u.orient = orientOf(u) === def ? (def === "v" ? "h" : "v") : def;
    }
    render(); send("move");
  }
  function applyPhase(val) {
    S.draw = val;
    const ids = Array.from(S.selected).filter((k) => S.model.units[k] || S.model.streams[k]);
    if (!ids.length) { hint(val ? "New items are now drawn as " + val.replace(":", " / ") : "New items are drawn as existing"); render(); return; }
    snapshot();
    const d = parseDraw(val);
    for (const k of ids) {
      const e = S.model.units[k] || S.model.streams[k];
      delete e.phase; delete e.change;
      Object.assign(e, d);
    }
    render(); send("move");
  }
  function applyColor(c, commit) {
    const ids = Array.from(S.selected).filter((k) => S.model.units[k] || S.model.streams[k]);
    if (!ids.length) return;
    if (commit) snapshot();
    for (const k of ids) { const e = S.model.units[k] || S.model.streams[k]; if (c) e.color = c; else delete e.color; }
    render(); if (commit) send("move");
  }
  let phaseSig = "";
  function syncToolbar() {
    const sel = document.getElementById("selPhase");
    if (!sel || !sel.options) return;
    const ph = phases();
    const sig = JSON.stringify(ph.map((p) => [p.id, p.name]));
    if (sig !== phaseSig) {
      phaseSig = sig;
      sel.innerHTML = "";
      const add = (v, t) => { const o = document.createElement("option"); o.value = v; o.textContent = t; sel.appendChild(o); };
      add("", "Existing (in place today)");
      for (const p of ph) { add(p.id + ":add", "＋ " + p.name + " — new"); add(p.id + ":remove", "− " + p.name + " — removed"); }
    }
    const els = Array.from(S.selected).map((k) => S.model.units[k] || S.model.streams[k]).filter(Boolean);
    let val = S.draw;
    if (els.length) {
      const vals = new Set(els.map((e) => (e.phase ? e.phase + ":" + (e.change === "remove" ? "remove" : "add") : "")));
      val = vals.size === 1 ? Array.from(vals)[0] : null;
    }
    if (val !== null && !Array.from(sel.options).some((o) => o.value === val)) val = "";
    sel.selectedIndex = val === null ? -1 : Array.from(sel.options).findIndex((o) => o.value === val);
    const ci = document.getElementById("inColor");
    const first = els.find((e) => e.color) || (els[0] && { color: phaseColor(ph, els[0]) });
    if (ci && first && first.color && /^#[0-9a-f]{6}$/i.test(first.color)) ci.value = first.color;
    document.getElementById("bGhost").classList.toggle("on", S.ghost);
    document.getElementById("bGhost").style.display = stageNow() >= 0 ? "" : "none";
    const lg = document.getElementById("legend");
    if (lg) {
      if (!ph.length) { lg.style.display = "none"; }
      else {
        const st = stageNow();
        const name = st < 0 ? "Design view — all phases" : st === 0 ? "Today (before any phase)" : "After " + (ph[st - 1] || {}).name;
        lg.style.display = "block";
        lg.innerHTML = `<b>${escapeXml(name)}</b><br>` +
          `<span class="chip" style="border-color:#8a97a6"></span>existing &nbsp;` +
          ph.map((p, i) => `<span class="chip" style="background:${p.color || PHASE_COLORS[i % PHASE_COLORS.length]}"></span>${escapeXml(p.name)}`).join(" &nbsp;") +
          `<br><span class="dim">dashed frame = new (＋) or to be removed (−)</span>`;
      }
    }
  }

  function undo() {
    const last = S.undo.pop();
    if (!last) return;
    const m = JSON.parse(last);
    S.model = m;
    S.selected = new Set();
    render(); send("undo");
  }
  const EXPORT_CLASSES = [":root", ".ulabel", ".slabel", ".rlabel", ".elabel", ".stream", ".streamhit",
                          ".statusbox"];
  function exportSvg() {
    // export the whole flowsheet (zoom-to-fit), without grid, ports or selection marks
    const saved = Object.assign({}, S.view), savedSel = S.selected, savedGrid = S.opts.grid;
    S.selected = new Set(); S.opts.grid = false;
    fit();
    const clone = svg.cloneNode(true);
    S.view = saved; S.selected = savedSel; S.opts.grid = savedGrid;
    render();
    const style = document.createElementNS(NS, "style");
    const css = [];
    for (const ss of Array.from(document.styleSheets)) {
      let rules = [];
      try { rules = Array.from(ss.cssRules); } catch (e) { rules = []; }
      for (const r of rules) {
        const sel = r.selectorText || "";
        if (EXPORT_CLASSES.some((c) => sel.split(",").some((part) => part.trim().startsWith(c)))) css.push(r.cssText);
      }
    }
    style.textContent = 'svg { font-family: Equinor, Inter, "Segoe UI", Arial, sans-serif; }\n' + css.join("\n");
    clone.insertBefore(style, clone.firstChild);
    clone.querySelectorAll(".port").forEach((p) => p.remove());
    clone.querySelectorAll(".selbox").forEach((p) => p.remove());
    const r = svg.getBoundingClientRect();
    clone.setAttribute("viewBox", `0 0 ${Math.round(r.width)} ${Math.round(r.height)}`);
    clone.setAttribute("width", Math.round(r.width)); clone.setAttribute("height", Math.round(r.height));
    clone.setAttribute("xmlns", NS);
    clone.setAttribute("style", "background:#fff");
    const txt = new XMLSerializer().serializeToString(clone);
    send("export_svg", { svg: txt });
    hint("SVG ready - download it under the diagram; it is also included in the report");
  }

  document.getElementById("bDelete").onclick = deleteSelection;
  document.getElementById("bFlip").onclick = flipSelection;
  document.getElementById("bOrient").onclick = orientSelection;
  document.getElementById("bSzUp").onclick = () => sizeSelection(1.15);
  document.getElementById("bSzDn").onclick = () => sizeSelection(1 / 1.15);
  document.getElementById("bSz1").onclick = () => sizeSelection(1, 1);
  document.getElementById("selPhase").onchange = (e) => applyPhase(e.target.value);
  document.getElementById("inColor").oninput = (e) => applyColor(e.target.value, false);
  document.getElementById("inColor").onchange = (e) => applyColor(e.target.value, true);
  document.getElementById("bColorClr").onclick = () => applyColor(null, true);
  document.getElementById("bGhost").onclick = () => { S.ghost = !S.ghost; render(); };
  document.getElementById("bDup").onclick = duplicateSelection;
  document.getElementById("bUndo").onclick = undo;
  document.getElementById("bZin").onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(r.left + r.width / 2, r.top + r.height / 2, 1.2); };
  document.getElementById("bZout").onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(r.left + r.width / 2, r.top + r.height / 2, 1 / 1.2); };
  document.getElementById("bFit").onclick = fit;
  document.getElementById("bGrid").onclick = () => { S.opts.grid = !S.opts.grid; render(); };
  document.getElementById("bRes").onclick = () => { S.opts.res = !S.opts.res; render(); };
  document.getElementById("bEnergy").onclick = () => { S.opts.energy = !S.opts.energy; render(); };
  document.getElementById("bSvg").onclick = exportSvg;

  window.addEventListener("keydown", (e) => {
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); deleteSelection(); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); }
    else if (e.key.toLowerCase() === "f" && !e.ctrlKey && !e.metaKey) flipSelection();
    else if (e.key.toLowerCase() === "o" && !e.ctrlKey && !e.metaKey) orientSelection();
    else if ((e.key === "+" || e.key === "=") && !e.ctrlKey && !e.metaKey) sizeSelection(1.15);
    else if ((e.key === "-" || e.key === "_") && !e.ctrlKey && !e.metaKey) sizeSelection(1 / 1.15);
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") { copySelection(); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") { e.preventDefault(); pasteClipboard(); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") { e.preventDefault(); duplicateSelection(); }
    else if (e.key === "Escape") { S.armed = null; S.selected = new Set(); render(); hint(null); send("select"); }
  });
  window.addEventListener("resize", () => render());

  document.getElementById("app").style.height = S.height + "px";
  post("streamlit:componentReady", { apiVersion: 1 });
  setHeight();
})();
