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
    column:     { w: 50, h: 190, ports: { feed_top: [-25, -70, "L"], feed: [-25, 0, "L"], feed_bottom: [-25, 70, "L"],
                                          overhead: [0, -95, "U"], bottoms: [0, 95, "D"] },
                  energies: [[25, -76, "R"], [25, 76, "R"]] },
    pipe:       { w: 84, h: 26, ports: { in: [-42, 0, "L"], out: [42, 0, "R"] }, energy: [0, -10, "U"] },
    recycle:    { w: 36, h: 36, ports: { in: [-18, 0, "L"], out: [18, 0, "R"] } },
    adjust:     { w: 40, h: 40, ports: {} },
    // subsea (SURF)
    well:         { w: 40, h: 76, ports: { in: [-20, 30, "L"], out: [20, -30, "R"] } },
    xmas_tree:    { w: 50, h: 56, ports: { in: [-25, 18, "L"], out: [25, -8, "R"] } },
    template:     { w: 96, h: 60, ports: { in: [-48, 8, "L"], out: [48, 8, "R"] } },
    jumper:       { w: 76, h: 34, ports: { in: [-38, 10, "L"], out: [38, 10, "R"] } },
    flowline:     { w: 104, h: 30, ports: { in: [-52, 0, "L"], out: [52, 0, "R"] } },
    riser:        { w: 64, h: 96, ports: { in: [-32, 40, "L"], out: [32, -40, "R"] } },
    subsea_valve: { w: 40, h: 40, ports: { in: [-20, 10, "L"], out: [20, 10, "R"] } },
    subsea_booster: { w: 60, h: 58, ports: { in: [-30, 4, "L"], out: [30, 4, "R"] }, energy: [0, 29, "D"] },
  };

  function portPos(u, port) {
    const g = GEOM[u.type];
    const p = g && g.ports[port];
    if (!p) return null;
    const fx = u.flip ? -1 : 1;
    return { x: u.x + fx * p[0], y: u.y + p[1], dir: u.flip ? MIRROR[p[2]] : p[2] };
  }

  function energyPos(u) {
    const g = GEOM[u.type];
    if (!g || !g.energy) return null;
    const e = g.energy, fx = u.flip ? -1 : 1;
    return { x: u.x + fx * e[0], y: u.y + e[1], dir: u.flip ? MIRROR[e[2]] : e[2] };
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

  /** Can an edge be added between (uOut,pOut) -> (uIn,pIn)? */
  function canConnect(model, catalogue, src, sp, dst, dp) {
    if (!src || !dst || src === dst) return false;
    const us = model.units[src], ud = model.units[dst];
    if (!us || !ud) return false;
    const cs = catalogue[us.type].ports.out[sp], cd = catalogue[ud.type].ports.in[dp];
    if (!cs || !cd) return false;
    for (const s of Object.values(model.streams)) {
      if (!cs.multi && s.src[0] === src && s.src[1] === sp) return false;
      if (!cd.multi && s.dst[0] === dst && s.dst[1] === dp) return false;
      if (s.src[0] === src && s.src[1] === sp && s.dst[0] === dst && s.dst[1] === dp) return false;
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
    subsea_valve: () => `
      <path d="M-18,1 L-18,19 L0,10 Z M18,1 L18,19 L0,10 Z" fill="url(#gV)" ${STK}/>
      <line x1="0" y1="10" x2="0" y2="-6" stroke="#2f3a46" stroke-width="1.4"/>
      <rect x="-9" y="-18" width="18" height="12" rx="2" fill="#f2b33d" ${STK}/>
      <path d="M-5,-12 L5,-12" stroke="#2f3a46" stroke-width="1.2"/>`,
  };
  const ICON_TEXT = { recycle: "R", adjust: "A" };

  // ----------------------------------------------------------- node export
  if (typeof document === "undefined") {
    module.exports = { GEOM, portPos, energyPos, route, simplify, labelAnchor, nextName, nextStreamName,
                       canConnect, ICON };
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
  };

  const svg = document.getElementById("svg");
  const wrap = document.getElementById("canvasWrap");

  // -------------------------------------------------------- streamlit glue
  function post(type, data) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type }, data || {}), "*");
  }
  function setHeight() { post("streamlit:setFrameHeight", { height: S.height }); }
  function structure() {
    const units = {}, streams = {};
    for (const [id, u] of Object.entries(S.model.units))
      units[id] = { type: u.type, name: u.name, x: Math.round(u.x), y: Math.round(u.y), flip: !!u.flip };
    for (const [id, s] of Object.entries(S.model.streams))
      streams[id] = { name: s.name, src: s.src.slice(), dst: s.dst.slice() };
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
  const PAL_ORDER = ["Streams", "Separation", "Pressure change", "Rotating", "Heat transfer", "Piping",
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
    S.model.units[id] = { type, name: nextName(S.model, type, S.catalogue), x, y, flip: false };
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
    svg.innerHTML = DEFS;
    if (S.opts.grid) el("rect", { x: -5000, y: -5000, width: 10000, height: 10000, fill: "url(#grid)",
                                  transform: `translate(${S.view.tx % (20 * S.view.k)},${S.view.ty % (20 * S.view.k)})` }, svg)
      .setAttribute("pointer-events", "none");
    const bg = el("rect", { x: 0, y: 0, width: "100%", height: "100%", fill: "transparent", id: "bg" }, svg);
    bg.addEventListener("pointerdown", onBgDown);
    world = el("g", { transform: `translate(${S.view.tx},${S.view.ty}) scale(${S.view.k})` }, svg);
    const gLinks = el("g", {}, world), gStreams = el("g", {}, world), gUnits = el("g", {}, world);
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
    for (const b of ["bGrid", "bRes", "bEnergy"]) document.getElementById(b).classList.toggle("on",
      { bGrid: S.opts.grid, bRes: S.opts.res, bEnergy: S.opts.energy }[b]);
  }

  function streamPoints(s) {
    const us = S.model.units[s.src[0]], ud = S.model.units[s.dst[0]];
    if (!us || !ud) return null;
    const a = portPos(us, s.src[1]), b = portPos(ud, s.dst[1]);
    if (!a || !b) return null;
    const gs = GEOM[us.type], gd = GEOM[ud.type];
    return route(a, a.dir, b, b.dir, Math.max(gs.h, gd.h) / 2 + 40);
  }

  function drawStream(id, s, parent) {
    const pts = streamPoints(s);
    if (!pts) return;
    const r = (S.results.streams || {})[id] || {};
    const solved = !!r.solved;
    const sel = S.selected.has(id);
    const d = "M" + pts.map((p) => p.x + "," + p.y).join(" L");
    const g = el("g", { "data-id": id }, parent);
    const hit = el("path", { d, class: "streamhit" }, g);
    el("path", { d, class: "stream", stroke: sel ? "var(--sel)" : solved ? "var(--mat)" : "var(--mat-uns)",
                 "stroke-width": sel ? 3 : 2,
                 "marker-end": `url(#${sel ? "mSel" : solved ? "mMat" : "mUns"})` }, g);
    const t = el("title", {}, g); t.textContent = s.name + (r.tip ? "\n" + r.tip : "");
    const us = S.model.units[s.src[0]], ud = S.model.units[s.dst[0]];
    const terminal = us.type === "feed" || ud.type === "product";
    if (!terminal) {
      const la = labelAnchor(pts);
      const tx = el("text", { x: la.x + (la.horiz ? 0 : 6), y: la.y + (la.horiz ? -5 : 4), class: "slabel",
                              "text-anchor": la.horiz ? "middle" : "start" }, g);
      tx.textContent = s.name + (r.warn ? "  ❄" : "");
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

  function drawUnit(id, u, parent) {
    const g = GEOM[u.type] || { w: 40, h: 40, ports: {} };
    const r = (S.results.units || {})[id] || {};
    const grp = el("g", { class: "unit", "data-id": id, transform: `translate(${u.x},${u.y})` }, parent);
    const st = r.status;
    if (st && st !== "ok") {
      const col = st === "error" ? "#d0342c" : st === "warning" ? "#e39a17" : "#e8c21b";
      el("rect", { x: -g.w / 2 - 5, y: -g.h / 2 - 5, width: g.w + 10, height: g.h + 10, rx: 6, class: "statusbox",
                   stroke: col, fill: st === "error" ? "rgba(208,52,44,.07)" : "rgba(232,194,27,.08)" }, grp);
    }
    const body = el("g", { transform: u.flip ? "scale(-1,1)" : "" }, grp);
    body.innerHTML = ICON[u.type] ? ICON[u.type]() : `<rect x="-20" y="-20" width="40" height="40" fill="#ccc"/>`;
    if (ICON_TEXT[u.type]) {
      const t = el("text", { x: 0, y: 5.5, "text-anchor": "middle", "font-size": 15, "font-weight": 700, fill: "#fff",
                             "pointer-events": "none" }, grp);
      t.textContent = ICON_TEXT[u.type];
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
    const above = u.type === "separator3";
    const lx = above ? (u.flip ? -28 : 28) : 0;
    const anchor = above ? (u.flip ? "start" : "end") : "middle";
    const lab = el("text", { x: lx, y: isTerm ? -12 : (above ? -g.h / 2 - 20 : ly), class: "ulabel",
                             "text-anchor": anchor }, grp);
    lab.textContent = u.name;
    if (r.label && !isTerm) {
      const sub = el("text", { x: lx, y: above ? -g.h / 2 - 8 : ly + 12, class: "rlabel", "text-anchor": anchor }, grp);
      sub.textContent = r.label;
    }
    if (isTerm && S.opts.res) {
      // terminal streams show their conditions next to the arrow
      const sid = Object.keys(S.model.streams).find((k) => {
        const s = S.model.streams[k]; return s.src[0] === id || s.dst[0] === id;
      });
      const rr = sid && (S.results.streams || {})[sid];
      if (rr && rr.label) {
        const t = el("text", { x: 0, y: 24, class: "rlabel", "text-anchor": "middle" }, grp);
        t.textContent = rr.label + (rr.warn ? "  ❄" : "");
        if (rr.warn) t.setAttribute("style", "fill:var(--warn)");
      }
    }
    const tt = el("title", {}, grp); tt.textContent = u.name + (r.tip ? "\n" + r.tip : "");
    // energy stream
    if (S.opts.energy && r.energy && r.energy.length) drawEnergy(u, r.energy, grp);
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
    const g = GEOM[u.type];
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
    const moving = kind === "unit" ? Array.from(S.selected).filter((k) => S.model.units[k]) : [];
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
        const ok = tdir !== dir && (dir === "out" ? canConnect(S.model, S.catalogue, uid, port, tid, tport)
                                                  : canConnect(S.model, S.catalogue, tid, tport, uid, port));
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
        S.model.streams[sid] = { name: terminalName(src, dst) || nextStreamName(S.model), src, dst };
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
        S.model.units[tid] = { type, name: nm, x: snap(w.x), y: snap(w.y), flip: false };
        const sid = newId("s");
        S.model.streams[sid] = dir === "out" ? { name: nm, src: [uid, port], dst: [tid, "in"] }
                                             : { name: nm, src: [tid, "out"], dst: [uid, port] };
        S.selected = new Set([tid]);
        render(); send("connect");
      } else render();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

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
      const g = GEOM[u.type] || { w: 40, h: 40 };
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
  function pasteClipboard() {
    if (!clipboard || !clipboard.units.length) return;
    snapshot();
    const map = {}, copies = {};
    for (const [old, u] of clipboard.units) {
      const id = newId("u");
      map[old] = id;
      copies[id] = old;
      S.model.units[id] = { type: u.type, name: nextName(S.model, u.type, S.catalogue), x: u.x + 40, y: u.y + 40,
                            flip: !!u.flip };
    }
    for (const s of clipboard.streams) {
      const sid = newId("s");
      const src = [map[s.src[0]], s.src[1]], dst = [map[s.dst[0]], s.dst[1]];
      S.model.streams[sid] = { name: terminalName(src, dst) || nextStreamName(S.model), src, dst };
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
