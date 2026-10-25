/* ProcessFlow Studio - 3D plant view (Three.js r160).  Made by Merouane Hamdani - for educational purposes only.
 *
 * Protocol (Streamlit custom component):  -> componentReady,  <- render {scene, ports, height}.
 * The scene (procsim/scene3d.py) lists equipment (type, name, x, z, results, description) and pipes (service, size, velocity).
 * Everything is drawn procedurally: no model files, no network.  Pipe sizes and equipment shapes are indicative.
 */
(function () {
  "use strict";
  const T = THREE;
  const $ = (id) => document.getElementById(id);
  const canvas = $("gl");

  // ------------------------------------------------------------------ renderer, scene, camera
  let renderer;
  try {
    renderer = new T.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true, powerPreference: "high-performance" });
  } catch (e) {
    $("msg").style.display = "flex";
    $("msg").textContent = "3D view needs WebGL, which this browser does not provide.";
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.outputColorSpace = T.SRGBColorSpace;
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFSoftShadowMap;
  renderer.shadowMap.autoUpdate = false;           // static scene: the shadow map is re-rendered only when something moved
  let shadowFrames = 3;
  const shadowsDirty = (n) => { shadowFrames = Math.max(shadowFrames, n || 2); };

  const scene = new T.Scene();
  const camera = new T.PerspectiveCamera(38, 1, 0.1, 2000);
  const world = new T.Group();           // rebuilt on every new scene
  scene.add(world);

  const sky = { day: ["#9ec7ea", "#e9f1f7", "#c9d4dc"], dusk: ["#1a2740", "#e08a5a", "#2b2f3d"] };
  let themeName = "studio";
  function skyTexture(c) {
    const cv = document.createElement("canvas"); cv.width = 2; cv.height = 256;
    const g = cv.getContext("2d"), gr = g.createLinearGradient(0, 0, 0, 256);
    gr.addColorStop(0, c[0]); gr.addColorStop(0.62, c[1]); gr.addColorStop(1, c[2]);
    g.fillStyle = gr; g.fillRect(0, 0, 2, 256);
    const t = new T.CanvasTexture(cv); t.colorSpace = T.SRGBColorSpace; return t;
  }
  const hemi = new T.HemisphereLight(0xdcebff, 0x6b6558, 0.55);
  const sun = new T.DirectionalLight(0xfff2dc, 2.4);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.04;
  scene.add(hemi, sun, sun.target);
  const fill = new T.DirectionalLight(0x9fb8d8, 0.55); fill.position.set(-40, 30, -30); scene.add(fill);

  // procedural reflection environment (soft boxes) so metals read as metal
  (function () {
    const env = new T.Scene();
    const sphere = new T.Mesh(new T.SphereGeometry(50, 32, 16), new T.MeshBasicMaterial({ map: skyTexture(sky.day), side: T.BackSide }));
    env.add(sphere);
    const panel = (w, h, x, y, z, i) => {
      const m = new T.Mesh(new T.PlaneGeometry(w, h), new T.MeshBasicMaterial({ color: new T.Color(i, i, i), side: T.DoubleSide }));
      m.position.set(x, y, z); m.lookAt(0, 0, 0); env.add(m);
    };
    panel(30, 14, 0, 40, 10, 6); panel(18, 18, -40, 20, 0, 3); panel(18, 18, 40, 15, -10, 2.2);
    const pm = new T.PMREMGenerator(renderer);
    scene.environment = pm.fromScene(env, 0.03).texture;
    scene.environmentIntensity = 0.7;
  })();

  // ------------------------------------------------------------------ materials
  const PAINT = (c, r = 0.42, m = 0.35) => new T.MeshStandardMaterial({ color: c, roughness: r, metalness: m });
  const M = {
    vessel: PAINT(0xaeb9c4, 0.38, 0.55), vesselDk: PAINT(0x6d7a88, 0.4, 0.6), steel: PAINT(0x8c969f, 0.45, 0.7),
    steelDk: PAINT(0x4b535c, 0.5, 0.7), galv: PAINT(0xb8c0c7, 0.55, 0.6), conc: PAINT(0x9a9a94, 0.95, 0),
    yellow: PAINT(0xe8b923, 0.5, 0.2), orange: PAINT(0xe0662b, 0.5, 0.25), blue: PAINT(0x2f6fb2, 0.4, 0.4), red: PAINT(0xb8322a, 0.45, 0.3),
    green: PAINT(0x3c8f5f, 0.45, 0.3), white: PAINT(0xe6eaee, 0.4, 0.3), black: PAINT(0x20252b, 0.6, 0.4),
    motor: PAINT(0x2c6fa8, 0.4, 0.5), ins: PAINT(0xc8ced4, 0.7, 0.2), copper: PAINT(0xb87333, 0.35, 0.8),
    glass: new T.MeshPhysicalMaterial({ color: 0x9fd8ff, roughness: 0.05, metalness: 0, transmission: 0.0, transparent: true, opacity: 0.28 }),
    seabed: PAINT(0xb9a67e, 1.0, 0), subsea: PAINT(0xd9a21b, 0.5, 0.25),
    liq: new T.MeshStandardMaterial({ color: 0x3b82c4, roughness: 0.15, metalness: 0.1, transparent: true, opacity: 0.55 }),
  };
  const SERVICE = { gas: 0xf2b01e, liquid: 0xc8521e, water: 0x1f8fe0, "two-phase": 0x8e5bd6 };
  const FLOWCOL = { gas: 0xfff1b8, liquid: 0xffc9a0, water: 0xa9ddff, "two-phase": 0xe9dcff };
  // ---- look: "studio" (clean, saturated, light - the look of commercial plant-design viewers) and "realistic" (outdoor, muted)
  const PAL_REAL = { vessel: 0xaeb9c4, vesselDk: 0x6d7a88, steel: 0x8c969f, steelDk: 0x4b535c, galv: 0xb8c0c7, conc: 0x9a9a94, yellow: 0xe8b923, orange: 0xe0662b, blue: 0x2f6fb2, red: 0xb8322a, green: 0x3c8f5f, white: 0xe6eaee, black: 0x20252b, motor: 0x2c6fa8, ins: 0xc8ced4, copper: 0xb87333 };
  const THEMES = {
    studio: { sky: ["#d3e1ef", "#f1f6fb", "#c4d1dd"], pad: "#dde4ea", grid: "rgba(110,130,150,.6)", ground: 0xb9c5cf, fog: 0xe7eef4, hemi: 0.95, sun: 1.8, exposure: 1.0, env: 0.9, tint: 1.0,
      pal: { vessel: 0xc9d8e8, vesselDk: 0x8ea4ba, steel: 0xa2b0be, steelDk: 0x566a7d, galv: 0xdbe2e9, conc: 0xcfd2d6, yellow: 0xffc21a, orange: 0xff7a1a, blue: 0x1f78d2, red: 0xe03a3a, green: 0x1fa66d, white: 0xf5f8fb, black: 0x252a31, motor: 0x1f6fd0, ins: 0xe8edf1, copper: 0xdb8b4b } },
    realistic: { sky: null, pad: "#7b7f7d", grid: null, ground: 0x4b504c, fog: 0xc9d6df, hemi: 0.55, sun: 2.4, exposure: 1.05, env: 0.7, tint: 0.3, pal: PAL_REAL },
  };
  const TINT = { separator: 0xb5d1f0, scrubber: 0xb5d1f0, separator3: 0xa9c8ee, phase_splitter: 0xb5d1f0, deliquifier: 0xb5d1f0, comp_splitter: 0xcdd8e4, column: 0xd2dce6, teg_contactor: 0xe6d6a6, amine_contactor: 0xbfe2d0,
    conv_reactor: 0xf2b27c, eq_reactor: 0xf2b27c, mol_sieve: 0xe4cba1, heater: 0xf5a98a, cooler: 0x8fc8f2, chiller: 0x82d6d8, hx: 0xa2d3cb, compressor: 0x4f80d2, expander: 0x8a6cd9, pump: 0x2fa896,
    subsea_separator: 0xe3b53a, subsea_booster: 0xe3b53a, subsea_pump: 0xe3b53a, subsea_compressor: 0xe3b53a, subsea_hydrocyclone: 0xe3b53a, subsea_heater: 0xe3b53a, subsea_cooler: 0xe3b53a };
  const TINTED = new Set(), tintCache = new Map();
  // ---- colour by results
  const RAMP = [[0, 0x2f6fd6], [0.25, 0x1fb2c9], [0.5, 0x3fbf6a], [0.75, 0xf2c230], [1, 0xe5484d]];
  function rampColor(t, out) {
    t = Math.max(0, Math.min(1, t)); const o = out || new T.Color();
    for (let i = 1; i < RAMP.length; i++) if (t <= RAMP[i][0]) { const a = RAMP[i - 1], b = RAMP[i], f = (t - a[0]) / (b[0] - a[0]); return o.setHex(a[1]).lerp(new T.Color(b[1]), f); }
    return o.setHex(RAMP[RAMP.length - 1][1]);
  }
  const COLOR_MODES = { none: "Colour: service", pressure: "Pressure [bar]", temperature: "Temperature [°C]", velocity: "Velocity / indicative limit", util: "Utilisation of capacity [%]" };
  function streamMetric(s, mode) {
    if (!s.solved) return null;
    if (mode === "pressure") return s.P; if (mode === "temperature") return s.T; if (mode === "velocity") return (s.vel || 0) / (s.velLimit || 15);
    return null;
  }
  function metricRange(mode) {                  // {lo, hi} over the solved pipes (or the fixed utilisation / velocity scale)
    if (mode === "util") return { lo: 0, hi: 120 };
    if (mode === "velocity") return { lo: 0, hi: 1.2 };
    let lo = 1e18, hi = -1e18; for (const p of S.pipes) { const v = streamMetric(p.s, mode); if (v !== null && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }
    if (!(hi > lo)) { lo = isFinite(lo) ? lo : 0; hi = lo + 1; } return { lo, hi };
  }

  function applyPalette() { const th = THEMES[themeName]; for (const k in th.pal) if (M[k]) M[k].color.setHex(th.pal[k]); tintCache.forEach((m) => { TINTED.delete(m); m.dispose(); }); tintCache.clear(); }
  const STATUS = { ok: null, warning: 0xf5a524, error: 0xff4d4d, missing: 0xff4d4d, unsolved: 0xff4d4d };

  // ------------------------------------------------------------------ small geometry helpers
  const mesh = (g, m, sh = true) => { const o = new T.Mesh(g, m); o.castShadow = sh; o.receiveShadow = true; return o; };
  const cyl = (r, h, m, seg = 40, r2) => mesh(new T.CylinderGeometry(r2 === undefined ? r : r2, r, h, seg), m);
  const box = (w, h, d, m) => mesh(new T.BoxGeometry(w, h, d), m);
  function at(o, x, y, z) { o.position.set(x, y, z); return o; }
  function dome(r, m, flat = 0.5) {        // elliptical head, open side down
    const o = mesh(new T.SphereGeometry(r, 40, 16, 0, Math.PI * 2, 0, Math.PI / 2), m); o.scale.y = flat; return o;
  }
  function flange(r, m, y = 0) { const o = cyl(r * 1.55, r * 0.35, m || M.steelDk, 24); o.position.y = y; return o; }
  function nozzle(g, x, y, z, dir, r = 0.12, len = 0.5) {   // short stub with a flange; dir = "x+","x-","y+","y-"
    const o = new T.Group(); const s = cyl(r, len, M.steel, 20); o.add(s);
    const f = flange(r, M.steelDk); f.position.y = len / 2; o.add(f);
    if (dir[0] === "x") o.rotation.z = dir[1] === "+" ? -Math.PI / 2 : Math.PI / 2;
    else if (dir === "y-") o.rotation.z = Math.PI;
    const off = len / 2;
    o.position.set(x + (dir === "x+" ? off : dir === "x-" ? -off : 0), y + (dir === "y+" ? off : dir === "y-" ? -off : 0), z);
    g.add(o); return o;
  }
  function ring(r, tube, m, rot) { const o = mesh(new T.TorusGeometry(r, tube, 8, 40), m); if (rot !== false) o.rotation.x = Math.PI / 2; return o; }
  function railing(r, y, m) {              // circular platform with a handrail
    const g = new T.Group();
    const f = mesh(new T.RingGeometry(r * 0.99, r + 0.9, 40, 1), M.galv); f.rotation.x = -Math.PI / 2; f.position.y = y; g.add(f);
    const rl = ring(r + 0.9, 0.025, M.yellow); rl.position.y = y + 1.0; g.add(rl);
    const rl2 = ring(r + 0.9, 0.02, M.yellow); rl2.position.y = y + 0.55; g.add(rl2);
    for (let i = 0; i < 12; i++) { const a = i / 12 * Math.PI * 2; const p = cyl(0.02, 1.0, M.yellow, 6); p.position.set(Math.cos(a) * (r + 0.9), y + 0.5, Math.sin(a) * (r + 0.9)); g.add(p); }
    return g;
  }
  function ladder(h, m) {
    const g = new T.Group();
    for (const x of [-0.18, 0.18]) g.add(at(box(0.04, h, 0.04, M.galv), x, h / 2, 0));
    for (let y = 0.3; y < h; y += 0.3) g.add(at(box(0.36, 0.03, 0.03, M.galv), 0, y, 0));
    return g;
  }
  function saddles(g, L, r, y0 = 0) {
    for (const x of [-L * 0.32, L * 0.32]) {
      const s = box(0.5, r * 1.1, r * 1.5, M.conc); s.position.set(x, y0 + r * 0.55 - 0.02, 0); g.add(s);
    }
  }
  function skid(g, w, d, h = 0.25, m = M.steelDk) { const s = box(w, h, d, m); s.position.y = h / 2; g.add(s); return s; }
  function plate(g, w, d, color) {            // phase / status colour plate under the equipment
    const p = mesh(new T.BoxGeometry(w, 0.06, d), new T.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0, emissive: color, emissiveIntensity: 0.25 }), false);
    p.position.y = 0.035; g.add(p); return p;
  }


  // ---- detail helpers (v7.10.1): instrumentation, bolted covers, fins, coupling guards, tube bundles
  function instr(g, x, y, z, kind) {            // transmitter (or gauge) on a root-valve stub
    const st = cyl(0.025, 0.32, M.steel, 8); st.position.set(x, y + 0.16, z); g.add(st);
    g.add(at(cyl(0.05, 0.07, M.red, 10), x, y + 0.1, z));
    if (kind === "gauge") { const d = cyl(0.09, 0.05, M.white, 20); d.rotation.x = Math.PI / 2; d.position.set(x, y + 0.42, z + 0.03); g.add(d); }
    else { g.add(at(box(0.11, 0.16, 0.11, M.blue), x, y + 0.42, z)); g.add(at(cyl(0.04, 0.06, M.black, 8), x, y + 0.54, z)); }
  }
  function boltRing(g, r, n, x, y, z, axis) {   // bolted flange: n bolt heads on a circle
    const grp = new T.Group();
    for (let i = 0; i < n; i++) { const a = i / n * Math.PI * 2; const b = cyl(0.025, 0.07, M.black, 6); b.position.set(Math.cos(a) * r, 0, Math.sin(a) * r); grp.add(b); }
    grp.position.set(x, y, z); if (axis === "x") grp.rotation.z = Math.PI / 2; g.add(grp); return grp;
  }
  function finRing(g, r, L, n, x, y, z, m) {    // cooling fins along a horizontal machine
    for (let i = 0; i < n; i++) { const f = cyl(r * 1.04, 0.025, m, 28); f.rotation.z = Math.PI / 2; f.position.set(x - L / 2 + (i + 0.5) * L / n, y, z); g.add(f); }
  }
  function couplingGuard(g, x, y, z, len, r) {  // yellow half-round guard over a shaft coupling
    const c = mesh(new T.CylinderGeometry(r, r, len, 24, 1, true, 0, Math.PI), M.yellow); c.rotation.z = Math.PI / 2; c.rotation.x = -Math.PI / 2; c.position.set(x, y, z); g.add(c);
    const e = box(0.04, r * 1.0, r * 2, M.yellow); e.position.set(x - len / 2, y - r * 0.5, z); g.add(e); const e2 = e.clone(); e2.position.x = x + len / 2; g.add(e2);
  }
  function gridPlate(g, w, d, y) { const p = box(w, 0.05, d, M.galv); p.position.y = y; g.add(p); for (let i = 0; i < 8; i++) g.add(at(box(w, 0.012, 0.012, M.steelDk), 0, y + 0.03, -d / 2 + (i + 0.5) * d / 8)); }
  function anchorBolts(g, w, d, y) { for (const sx of [-1, 1]) for (const sz of [-1, 1]) { g.add(at(cyl(0.035, 0.18, M.black, 6), sx * (w / 2 - 0.1), y + 0.1, sz * (d / 2 - 0.1))); g.add(at(cyl(0.06, 0.04, M.steelDk, 6), sx * (w / 2 - 0.1), y + 0.02, sz * (d / 2 - 0.1))); } }
  function psvOnTop(g, x, y, z, r0) {            // relief valve + tail pipe on a vessel top
    const n = cyl(0.1, 0.35, M.steel, 14); n.position.set(x, y + 0.17, z); g.add(n); g.add(at(flange(0.1, M.steelDk), x, y + 0.36, z));
    const b = box(0.28, 0.28, 0.28, M.red); b.position.set(x, y + 0.62, z); g.add(b);
    const o = cyl(0.14, 0.55, M.steel, 14); o.rotation.z = Math.PI / 2; o.position.set(x + 0.4, y + 0.62, z); g.add(o);
    g.add(at(cyl(0.1, 0.5, M.vesselDk, 14), x, y + 0.98, z));
  }
  function tubeBundle(L, r, n, plateEvery) {     // X-ray content of a shell & tube exchanger
    const grp = new T.Group(); grp.userData.internal = true;
    const tg = new T.CylinderGeometry(0.018, 0.018, L, 6), im = new T.InstancedMesh(tg, M.copper, n), mtx = new T.Matrix4(), q = new T.Quaternion().setFromEuler(new T.Euler(0, 0, Math.PI / 2));
    const rings = Math.ceil(Math.sqrt(n)); let k = 0;
    for (let j = 0; j < rings * 2 && k < n; j++) for (let i = 0; i < rings * 2 && k < n; i++) {
      const y = (j - rings + 0.5) * r * 0.78 / rings * 1.05, z = (i - rings + 0.5) * r * 0.78 / rings * 1.05; if (y * y + z * z > r * r * 0.52) continue;
      mtx.compose(new T.Vector3(0, y, z), q, new T.Vector3(1, 1, 1)); im.setMatrixAt(k++, mtx);
    }
    im.count = k; grp.add(im);
    for (let x = -L / 2 + plateEvery; x < L / 2; x += plateEvery) { const b = cyl(r * 0.97, 0.025, M.galv, 28); b.rotation.z = Math.PI / 2; b.position.x = x; b.scale.set(1, 1, 1); grp.add(b); }
    const ts = cyl(r * 0.99, 0.08, M.steelDk, 32); ts.rotation.z = Math.PI / 2; ts.position.x = -L / 2; grp.add(ts); const ts2 = ts.clone(); ts2.position.x = L / 2; grp.add(ts2);
    return grp;
  }

  // ------------------------------------------------------------------ equipment archetypes
  // Each builder returns {g, W, H, yMid, ySpan, spin:[{o, axis, rate}], level:[...], ports?:{name:{p:[x,y,z], d:[x,y,z]}}}
  // Local frame: x = inlet -> outlet, y up, z towards the viewer.  Scale 1 = metres.
  const SPIN = (o, axis, rate) => ({ o, axis, rate });
  function vesselV(D, H, o = {}) {
    const g = new T.Group(), r = D / 2, y0 = 0.7;
    g.add(at(cyl(r * 0.94, y0, M.steelDk, 40), 0, y0 / 2, 0));
    g.add(at(cyl(r, H, M.vessel, 48), 0, y0 + H / 2, 0));
    const top = dome(r, M.vessel, 0.5); top.position.y = y0 + H; g.add(top);
    const bot = dome(r, M.vessel, 0.5); bot.rotation.x = Math.PI; bot.position.y = y0; g.add(bot);
    for (const f of [0.33, 0.66]) { const w = ring(r + 0.01, 0.018, M.vesselDk); w.position.y = y0 + H * f; g.add(w); }
    if (o.jacket) { const j = cyl(r * 1.06, H * 0.5, M.orange, 40); j.position.y = y0 + H * 0.4; g.add(j); }
    // manway, bridle level gauge, ladder, platform
    const mw = cyl(0.26, 0.4, M.steelDk, 20); mw.rotation.x = Math.PI / 2; mw.position.set(0, y0 + H * 0.22, r + 0.2); g.add(mw);
    const gl = cyl(0.07, H * 0.34, M.glass, 12); gl.position.set(r * 0.5, y0 + H * 0.3, r + 0.35); g.add(gl);
    const lv = cyl(0.055, H * 0.34, M.liq, 12); lv.position.copy(gl.position); lv.scale.y = 0.45; lv.position.y = y0 + H * 0.3 - H * 0.34 * 0.275; g.add(lv);
    for (const dy of [-0.15, 0.15]) g.add(at(cyl(0.05, 0.25, M.steelDk, 8), r * 0.5, y0 + H * 0.3 + dy * H * 0.34 / 0.15 * 0.5, r + 0.25).rotateX(Math.PI / 2));
    const lad = ladder(H * 0.8, M.galv); lad.position.set(-r * 0.35, y0, r + 0.15); g.add(lad);
    if (H > 4) { const pf = railing(r, y0 + H * 0.78); g.add(pf); }
    if (o.trays) for (let i = 1; i < 12; i++) { const t = ring(r * 1.0, 0.012, M.vesselDk); t.position.y = y0 + (H * i) / 12; g.add(t); }
    if (o.agitator) { const a = cyl(0.08, 1.6, M.steel, 10); a.position.y = y0 + H + 0.8; g.add(a); const mt = cyl(0.3, 0.7, M.motor, 20); mt.position.y = y0 + H + 1.9; g.add(mt); }
    const ints = new T.Group(); ints.userData.internal = true;
    ints.add(at(cyl(r * 0.96, H * 0.3, M.liq, 36), 0, y0 + H * 0.15, 0));
    ints.add(at(cyl(r * 0.95, 0.22, M.galv, 36), 0, y0 + H * 0.84, 0));
    ints.add(at(box(r * 1.2, 0.08, 0.5, M.yellow), -r * 0.45, y0 + H * 0.5, 0));
    g.add(ints);
    // detail: skirt vent + anchor chairs, nameplate, bolted bridle, transmitters, PSV, davit, second platform, internals
    g.add(at(box(0.5, 0.45, 0.08, M.black), 0, 0.3, r * 0.94 + 0.02));
    for (let i = 0; i < 8; i++) { const a = i / 8 * Math.PI * 2 + 0.2; g.add(at(box(0.16, 0.3, 0.12, M.steelDk), Math.cos(a) * r * 0.97, 0.08, Math.sin(a) * r * 0.97)); }
    g.add(at(box(0.3, 0.2, 0.02, M.galv), r * 0.6, y0 + H * 0.12, r * 0.8 + 0.02));
    for (const f of [0.3 - 0.17, 0.3 + 0.17]) { const dy = H * f; g.add(at(cyl(0.035, 0.22, M.steel, 8), r * 0.5, y0 + dy, r + 0.24).rotateX(Math.PI / 2)); }
    instr(g, -r * 0.2, y0 + H * 0.5, r + 0.0, "tx"); instr(g, r * 0.2, y0 + H * 0.62, r, "gauge");
    psvOnTop(g, r * 0.45, y0 + H + r * 0.46 - 0.05, 0);
    const dv = cyl(0.03, 1.1, M.steelDk, 8); dv.position.set(-r * 0.4, y0 + H + r * 0.4 + 0.55, 0); g.add(dv); g.add(at(box(0.8, 0.04, 0.04, M.steelDk), -r * 0.4 + 0.35, y0 + H + r * 0.4 + 1.1, 0));
    if (H > 7) { g.add(railing(r, y0 + H * 0.45)); }
    boltRing(g, 0.3, 12, 0, y0 + H * 0.22, r + 0.4, "z").rotation.x = Math.PI / 2;
    if (o.trays) { for (let i = 1; i < 12; i++) { const d = cyl(r * 0.96, 0.03, M.galv, 36); d.position.y = y0 + H * i / 12; ints.add(d); const dc = box(r * 0.5, H / 12 * 0.85, 0.05, M.steelDk); dc.position.set((i % 2 ? 1 : -1) * r * 0.62, y0 + H * (i + 0.5) / 12, 0); ints.add(dc); } }
    else { ints.add(at(cyl(r * 0.3, 0.1, M.steelDk, 18), 0, y0 + 0.28, 0)); ints.add(at(box(r * 1.3, 0.04, 0.3, M.galv), 0, y0 + H * 0.2, 0)); }
    const topY = y0 + H + r * 0.5 + 0.55;
    return { g, W: D + 1.2, H: topY, yMid: y0 + H * 0.5, ySpan: H * 0.8, yLow: 0.35, lowR: r * 0.94, topR: r, level: [{ o: lv, base: y0 + H * 0.3 - H * 0.34 * 0.5, h: H * 0.34 }], radius: r };
  }
  function vesselH(D, L, o = {}) {
    const g = new T.Group(), r = D / 2, yc = 1.0 + r;
    const body = cyl(r, L, M.vessel, 48); body.rotation.z = Math.PI / 2; body.position.y = yc; g.add(body);
    for (const s of [-1, 1]) { const h = dome(r, M.vessel, 0.5); h.rotation.z = -s * Math.PI / 2; h.position.set(s * L / 2, yc, 0); g.add(h); }
    saddles(g, L, r + 0.2, 0);
    for (const f of [-0.15, 0.2]) { const w = ring(r + 0.01, 0.018, M.vesselDk); w.rotation.set(0, 0, Math.PI / 2); w.rotation.x = 0; w.rotation.y = Math.PI / 2; w.position.set(f * L, yc, 0); g.add(w); }
    if (o.boot) { const b = cyl(0.36, 1.2, M.vessel, 24); b.position.set(-L * 0.04, 1.0 - 0.3 + 0.0, 0); g.add(b); const bd = dome(0.36, M.vessel, 0.5); bd.rotation.x = Math.PI; bd.position.set(-L * 0.04, 0.4, 0); g.add(bd); }
    const mw = cyl(0.26, 0.4, M.steelDk, 20); mw.position.set(L * 0.1, yc + r + 0.15, 0); g.add(mw);
    const gl = cyl(0.07, 1.4, M.glass, 12); gl.rotation.z = Math.PI / 2; gl.position.set(L * 0.28, yc - r * 0.25, r + 0.3); g.add(gl);
    const lv = cyl(0.055, 1.4, M.liq, 12); lv.rotation.z = Math.PI / 2; lv.position.copy(gl.position); lv.scale.y = 0.55; g.add(lv);
    const tw = box(0.5, 0.05, 1.0, M.galv); tw.position.set(L * 0.1, yc + r + 0.6, 0); g.add(tw);
    const ints = new T.Group(); ints.userData.internal = true;
    const hl = r * 0.9; ints.add(at(box(L * 0.96, hl, Math.sqrt(Math.max(0.01, r * r - (r - hl) * (r - hl))) * 1.7, M.liq), 0, yc - r + hl / 2 + 0.02, 0));
    ints.add(at(box(0.06, r * 1.3, r * 1.9, M.galv), L * 0.18, yc - r * 0.35, 0));
    ints.add(at(box(0.06, r * 1.6, r * 1.9, M.galv), -L * 0.3, yc, 0));
    // detail: inlet device, vortex breaker, mesh pad, weir, bolted manway, transmitters, PSV, nozzle stubs, insulation clips
    ints.add(at(cyl(0.18, r * 1.2, M.steelDk, 12), -L * 0.42, yc + r * 0.35, 0));
    ints.add(at(box(0.5, 0.3, 0.5, M.galv), L * 0.45, yc - r * 0.75, 0));
    ints.add(at(box(0.4, r * 0.55, r * 1.8, M.galv), L * 0.36, yc + r * 0.55, 0));
    boltRing(g, 0.3, 12, L * 0.1, yc + r + 0.3, 0, "y");
    for (const x of [-L * 0.36, L * 0.36]) { g.add(at(cyl(0.1, 0.4, M.steel, 14), x, yc + r + 0.18, 0)); g.add(at(flange(0.1, M.steelDk), x, yc + r + 0.4, 0)); }
    psvOnTop(g, L * 0.22, yc + r - 0.02, 0);
    instr(g, -L * 0.15, yc + r - 0.05, r * 0.4, "tx"); instr(g, L * 0.0, yc + r - 0.02, -r * 0.4, "gauge");
    g.add(at(cyl(0.1, 0.5, M.steel, 14), -L * 0.44, yc - r * 0.3, r + 0.2).rotateX(Math.PI / 2)); g.add(at(flange(0.1, M.steelDk), -L * 0.44, yc - r * 0.3, r + 0.5).rotateX(Math.PI / 2));
    for (let i = 0; i < 9; i++) g.add(at(box(0.04, 0.08, 0.04, M.steelDk), -L * 0.45 + i * L * 0.11, yc + r + 0.02, 0));
    g.add(at(box(0.3, 0.2, 0.02, M.galv), L * 0.3, yc + 0.2, r + 0.01));
    g.add(ints);
    return { g, W: L + 1.4, H: yc + r + 0.6, yMid: yc, ySpan: D * 0.6, yLow: 0.35, hz: true, level: [{ o: lv, base: yc - r * 0.25, h: 0.0, along: true }], radius: r, bottomY: 1.0 };
  }
  function exchanger(L, D, cool) {
    const g = new T.Group(), r = D / 2, yc = 0.9 + r;
    const sh = cyl(r, L * 0.78, M.vessel, 36); sh.rotation.z = Math.PI / 2; sh.position.set(L * 0.04, yc, 0); g.add(sh);
    const ch = cyl(r * 0.96, L * 0.14, M.blue, 36); ch.rotation.z = Math.PI / 2; ch.position.set(-L * 0.41, yc, 0); g.add(ch);
    const chd = dome(r * 0.96, M.blue, 0.55); chd.rotation.z = Math.PI / 2; chd.position.set(-L * 0.48, yc, 0); g.add(chd);
    const rh = cyl(r * 0.98, L * 0.1, M.vesselDk, 36); rh.rotation.z = Math.PI / 2; rh.position.set(L * 0.45, yc, 0); g.add(rh);
    const rhd = dome(r * 0.98, M.vesselDk, 0.5); rhd.rotation.z = -Math.PI / 2; rhd.position.set(L * 0.5, yc, 0); g.add(rhd);
    for (const x of [-L * 0.34, L * 0.34]) { const s = box(0.45, r * 1.1, r * 1.5, M.conc); s.position.set(x, 0.45 + r * 0.45, 0); g.add(s); }
    for (const x of [-0.28 * L, 0.3 * L]) { const f = cyl(r * 1.08, 0.1, M.steelDk, 36); f.rotation.z = Math.PI / 2; f.position.set(x, yc, 0); g.add(f); }
    const utility = cool ? M.blue : M.red;
    for (const x of [L * 0.18, L * 0.3]) { const n = cyl(0.1, 0.5, utility, 14); n.position.set(x, yc + r + 0.2, 0); g.add(n); const fl = flange(0.1, M.steelDk, yc + r + 0.45); fl.position.x = x; g.add(fl); }
    // detail: channel nozzles, tubesheet bolts, vent / drain, slotted saddle holes, bundle (X-ray), drain pot, name plate
    for (const [x, dy] of [[-L * 0.41, r + 0.2], [-L * 0.41, -r - 0.2]]) { g.add(at(cyl(0.12, 0.35, utility, 14), x, yc + dy, 0)); g.add(at(flange(0.12, M.steelDk), x, yc + dy + (dy > 0 ? 0.18 : -0.18), 0)); }
    boltRing(g, r * 1.04, 20, -L * 0.28, yc, 0, "x"); boltRing(g, r * 1.04, 20, L * 0.3, yc, 0, "x");
    g.add(at(cyl(0.035, 0.4, M.steel, 8), L * 0.1, yc + r + 0.2, 0)); g.add(at(cyl(0.035, 0.4, M.steel, 8), L * 0.3, yc - r - 0.15, 0));
    instr(g, L * 0.2, yc + r - 0.02, 0, "gauge");
    g.add(at(box(0.3, 0.2, 0.02, M.galv), -L * 0.05, yc + 0.1, r + 0.01));
    for (const x of [-L * 0.34, L * 0.34]) g.add(at(cyl(0.04, 0.2, M.black, 6), x - 0.12, 0.35, r * 0.55));
    const bun = tubeBundle(L * 0.74, r * 0.92, 90, L * 0.12); bun.position.set(L * 0.04, yc, 0); g.add(bun);
    return { g, W: L + 0.8, H: yc + r + 0.7, yMid: yc, ySpan: r * 1.2, yLow: 0.3, hz: true };
  }
  function airCooler(L) {
    const g = new T.Group(), spin = [], legH = 3.2, W = 3.0;
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) g.add(at(box(0.22, legH, 0.22, M.galv), sx * (L / 2 - 0.3), legH / 2, sz * (W / 2 - 0.1)));
    g.add(at(box(L, 0.18, W, M.steel), 0, legH, 0));
    for (let i = 0; i < 14; i++) g.add(at(box(L * 0.88, 0.05, 0.12, M.copper), 0, legH + 0.14 + (i % 2) * 0.07, -W / 2 + 0.2 + i * (W - 0.4) / 13));
    for (const sx of [-1, 1]) g.add(at(box(0.5, 0.5, W, M.blue), sx * (L / 2 - 0.1), legH + 0.28, 0));
    for (const fx of [-L * 0.22, L * 0.22]) {
      const ringO = cyl(0.95, 0.5, M.galv, 40); ringO.material = M.galv; ringO.position.set(fx, legH + 0.6, 0);
      const hole = cyl(0.88, 0.52, M.black, 40); hole.position.set(fx, legH + 0.6, 0); g.add(ringO); g.add(hole);
      const fan = new T.Group(); for (let k = 0; k < 4; k++) { const b = box(0.85, 0.02, 0.18, M.steel); b.position.x = 0.45; const h = new T.Group(); h.add(b); h.rotation.y = k * Math.PI / 2; fan.add(h); }
      fan.position.set(fx, legH + 0.82, 0); g.add(fan); spin.push(SPIN(fan, "y", 9));
      g.add(at(cyl(0.2, 0.25, M.motor, 16), fx, legH + 0.82, 0));
    }
    return { g, W: L + 0.8, H: legH + 1.2, yMid: legH + 0.28, ySpan: 0.3, yLow: legH, spin };
  }
  function compressor() {
    const g = new T.Group(), spin = [];
    skid(g, 8.4, 2.8, 0.3, M.steelDk); anchorBolts(g, 8.4, 2.8, 0.3);
    gridPlate(g, 1.6, 1.0, 0.31); g.children[g.children.length - 1].position.set(0, 0.31, 1.7);
    // electric motor (TEFC): finned frame, fan cowl, terminal box, bearing housings, feet
    const mot = cyl(0.62, 2.5, M.motor, 32); mot.rotation.z = Math.PI / 2; mot.position.set(-2.9, 1.0, 0); g.add(mot);
    finRing(g, 0.62, 2.2, 16, -2.9, 1.0, 0, M.motor);
    g.add(at(cyl(0.5, 0.35, M.steelDk, 24), -4.3, 1.0, 0).rotateZ(Math.PI / 2));
    const cowl = mesh(new T.SphereGeometry(0.5, 20, 12, 0, Math.PI * 2, 0, Math.PI / 2), M.steelDk); cowl.rotation.z = Math.PI / 2; cowl.position.set(-4.45, 1.0, 0); g.add(cowl);
    for (const x of [-3.7, -2.1]) { g.add(at(box(0.5, 0.35, 1.0, M.steelDk), x, 0.5, 0)); }
    const tb = box(0.6, 0.35, 0.55, M.black); tb.position.set(-2.9, 1.78, 0); g.add(tb); g.add(at(cyl(0.05, 0.4, M.steelDk, 8), -2.9, 2.05, 0));
    for (const x of [-4.1, -1.7]) g.add(at(cyl(0.2, 0.3, M.vesselDk, 18), x, 1.0, 0).rotateZ(Math.PI / 2));
    // couplings, guards, gearbox with split line and bolts
    const shaft = cyl(0.12, 2.0, M.steel, 12); shaft.rotation.z = Math.PI / 2; shaft.position.set(-1.2, 1.0, 0); g.add(shaft); spin.push(SPIN(shaft, "x", 14));
    couplingGuard(g, -1.65, 1.0, 0, 0.9, 0.34);
    const gear = box(1.2, 1.5, 1.4, M.vesselDk); gear.position.set(-0.55, 0.95, 0); g.add(gear);
    g.add(at(box(1.22, 0.03, 1.42, M.black), -0.55, 0.95, 0));
    for (let i = 0; i < 6; i++) { g.add(at(cyl(0.03, 0.05, M.black, 6), -1.05 + i * 0.2, 1.72, 0.55)); g.add(at(cyl(0.03, 0.05, M.black, 6), -1.05 + i * 0.2, 1.72, -0.55)); }
    g.add(at(cyl(0.18, 0.2, M.steel, 14), -0.55, 1.78, 0)); g.add(at(cyl(0.25, 0.1, M.steelDk, 14), -0.55, 1.88, 0));
    couplingGuard(g, 0.3, 1.0, 0, 0.7, 0.3);
    // compressor barrel: bolted end covers, bearing housings, dry-gas-seal cartridges, nozzles with spool pieces
    const cs = cyl(0.85, 2.6, M.vessel, 36); cs.rotation.z = Math.PI / 2; cs.position.set(1.6, 1.05, 0); g.add(cs);
    for (const x of [0.35, 2.85]) { const f = cyl(0.92, 0.12, M.steelDk, 36); f.rotation.z = Math.PI / 2; f.position.set(x, 1.05, 0); g.add(f); boltRing(g, 0.87, 24, x, 1.05, 0, "x"); }
    for (const x of [0.05, 3.15]) { g.add(at(cyl(0.34, 0.5, M.vesselDk, 24), x, 1.05, 0).rotateZ(Math.PI / 2)); g.add(at(cyl(0.28, 0.12, M.steel, 24), x + (x < 1 ? -0.3 : 0.3), 1.05, 0).rotateZ(Math.PI / 2)); }
    for (const x of [0.9, 2.3]) { const vol = cyl(0.5, 1.2, M.vesselDk, 24); vol.position.set(x, 1.9, 0); g.add(vol); g.add(at(flange(0.5, M.steelDk, 2.5), x, 2.5, 0)); boltRing(g, 0.7, 16, x, 2.53, 0, "y"); }
    for (const sz of [-1, 1]) for (const x of [0.6, 2.6]) g.add(at(box(0.4, 0.5, 0.2, M.steelDk), x, 0.55, sz * 0.7));
    // anti-surge recycle: control valve + line + cooler stub, pressure & temperature instruments
    const rc = cyl(0.09, 1.4, M.steel, 12); rc.rotation.z = Math.PI / 2; rc.position.set(1.6, 2.85, 0.5); g.add(rc);
    for (const x of [0.9, 2.3]) g.add(at(cyl(0.09, 0.4, M.steel, 12), x, 2.62, 0.5));
    g.add(at(mesh(new T.SphereGeometry(0.2, 14, 10), M.red), 1.6, 2.85, 0.5)); g.add(at(dome(0.22, M.green, 0.7), 1.6, 3.3, 0.5)); g.add(at(cyl(0.03, 0.4, M.steel, 8), 1.6, 3.1, 0.5));
    instr(g, 0.9, 2.4, 0.5, "gauge"); instr(g, 2.3, 2.4, -0.5, "tx");
    // lube-oil console: tank, duplex filters, coolers, pumps, seal-gas panel
    g.add(at(box(2.0, 0.9, 0.9, M.green), 0.4, 0.75, 1.4)); g.add(at(box(2.04, 0.04, 0.94, M.steelDk), 0.4, 1.22, 1.4));
    for (const x of [-0.2, 0.3]) g.add(at(cyl(0.16, 0.7, M.vessel, 16), x, 1.58, 1.4)); g.add(at(cyl(0.2, 0.9, M.blue, 16), 1.0, 1.65, 1.4).rotateZ(Math.PI / 2));
    g.add(at(box(0.8, 0.9, 0.3, M.white), 2.3, 0.8, 1.4)); for (let i = 0; i < 4; i++) g.add(at(cyl(0.025, 0.5, M.steel, 6), 2.1 + i * 0.12, 1.1, 1.58));
    g.add(at(cyl(0.025, 3.0, M.steel, 6), 1.6, 1.0, 0.9).rotateZ(Math.PI / 2));
    // internals for X-ray: rotor with impellers
    const ints = new T.Group(); ints.userData.internal = true;
    const rot = cyl(0.14, 3.4, M.steel, 12); rot.rotation.z = Math.PI / 2; rot.position.set(1.6, 1.05, 0); ints.add(rot);
    for (let i = 0; i < 6; i++) { const im = cyl(0.55, 0.12, M.copper, 20, 0.2); im.rotation.z = Math.PI / 2; im.position.set(0.75 + i * 0.32, 1.05, 0); ints.add(im); const dg = cyl(0.62, 0.03, M.galv, 20); dg.rotation.z = Math.PI / 2; dg.position.set(0.9 + i * 0.32, 1.05, 0); ints.add(dg); }
    g.add(ints);
    return { g, W: 8.4, H: 3.5, yMid: 1.0, ySpan: 0.3, yLow: 0.3, spin, ports: { in: { p: [0.9, 2.55, 0], d: [0, 1, 0] }, out: { p: [2.3, 2.55, 0], d: [0, 1, 0] } } };
  }
  function expander() {
    const g = new T.Group(), spin = [];
    skid(g, 6.2, 2.4, 0.3, M.steelDk);
    const cs = cyl(0.8, 1.8, M.vessel, 36); cs.rotation.z = Math.PI / 2; cs.position.set(-0.9, 1.0, 0); g.add(cs);
    const vol = cyl(0.55, 1.2, M.vesselDk, 24); vol.position.set(-0.9, 1.95, 0); g.add(vol);
    const sh = cyl(0.1, 1.6, M.steel, 12); sh.rotation.z = Math.PI / 2; sh.position.set(0.9, 1.0, 0); g.add(sh); spin.push(SPIN(sh, "x", 12));
    const gen = cyl(0.65, 2.3, M.green, 32); gen.rotation.z = Math.PI / 2; gen.position.set(2.2, 1.0, 0); g.add(gen);
    return { g, W: 6.2, H: 3.1, yMid: 1.0, ySpan: 0.3, yLow: 0.3, spin, ports: { in: { p: [-0.9, 2.55, 0], d: [0, 1, 0] }, out: { p: [-0.1, 0.62, 0.8], d: [0, 0, 1] } } };
  }
  function pump(big) {
    const g = new T.Group(), spin = [], k = big ? 1.0 : 0.8;
    skid(g, 3.4 * k, 1.3 * k, 0.2, M.steelDk);                                   // fabricated baseplate with drip rim
    g.add(at(box(3.5 * k, 0.05, 1.4 * k, M.galv), 0, 0.22, 0)); anchorBolts(g, 3.4 * k, 1.3 * k, 0.2);
    const mot = cyl(0.42 * k, 1.5 * k, M.motor, 28); mot.rotation.z = Math.PI / 2; mot.position.set(-0.95 * k, 0.75 * k, 0); g.add(mot);
    finRing(g, 0.42 * k, 1.3 * k, 14, -0.95 * k, 0.75 * k, 0, M.motor);
    g.add(at(box(0.3 * k, 0.28 * k, 0.28 * k, M.black), -0.95 * k, 1.28 * k, 0));
    for (const x of [-1.4 * k, -0.5 * k]) g.add(at(box(0.3 * k, 0.32 * k, 0.8 * k, M.steelDk), x, 0.4 * k, 0));
    const fan = cyl(0.34 * k, 0.1, M.steelDk, 18); fan.rotation.z = Math.PI / 2; fan.position.set(-1.78 * k, 0.75 * k, 0); g.add(fan); spin.push(SPIN(fan, "x", 20));
    g.add(at(cyl(0.3 * k, 0.18 * k, M.black, 18), -1.82 * k, 0.75 * k, 0).rotateZ(Math.PI / 2));
    const sh = cyl(0.07, 0.5 * k, M.steel, 10); sh.rotation.z = Math.PI / 2; sh.position.set(-0.05 * k, 0.75 * k, 0); g.add(sh); spin.push(SPIN(sh, "x", 22));
    couplingGuard(g, -0.05 * k, 0.75 * k, 0, 0.55 * k, 0.22 * k);
    const br = cyl(0.17 * k, 0.4 * k, M.vesselDk, 18); br.rotation.z = Math.PI / 2; br.position.set(0.3 * k, 0.75 * k, 0); g.add(br);       // bearing housing
    g.add(at(box(0.2 * k, 0.3 * k, 0.5 * k, M.steelDk), 0.3 * k, 0.4 * k, 0));
    const vo = cyl(0.5 * k, 0.5 * k, M.vessel, 28); vo.rotation.x = Math.PI / 2; vo.position.set(0.7 * k, 0.75 * k, 0); g.add(vo);          // volute
    boltRing(g, 0.45 * k, 16, 0.7 * k, 0.75 * k, 0.26 * k, "z").rotation.x = Math.PI / 2;
    const dis = cyl(0.17 * k, 0.7 * k, M.vessel, 16); dis.position.set(0.7 * k, 1.35 * k, 0); g.add(dis); { const fl = flange(0.17 * k, M.steelDk, 1.72 * k); fl.position.x = 0.7 * k; g.add(fl); }
    const suc = cyl(0.2 * k, 0.7 * k, M.vessel, 16); suc.rotation.z = Math.PI / 2; suc.position.set(1.25 * k, 0.75 * k, 0); g.add(suc); { const fl = flange(0.2 * k, M.steelDk, 0); fl.rotation.z = Math.PI / 2; fl.position.set(1.62 * k, 0.75 * k, 0); g.add(fl); }
    g.add(at(box(0.28 * k, 0.22 * k, 0.6 * k, M.steelDk), 0.7 * k, 0.28 * k, 0));               // casing foot
    // seal flush (API plan 11): small bore loop from discharge to the seal chamber; pressure gauge on discharge
    const loop = mesh(new T.TorusGeometry(0.25 * k, 0.018, 6, 20, Math.PI), M.steel); loop.position.set(0.5 * k, 1.2 * k, 0.3 * k); loop.rotation.y = Math.PI / 2; g.add(loop);
    g.add(at(cyl(0.02, 0.45 * k, M.steel, 6), 0.7 * k, 1.0 * k, 0.3 * k).rotateZ(Math.PI / 2));
    instr(g, 0.85 * k, 1.5 * k, 0, "gauge");
    const ints = new T.Group(); ints.userData.internal = true;
    const imp = cyl(0.36 * k, 0.12, M.copper, 20); imp.rotation.x = Math.PI / 2; imp.position.set(0.7 * k, 0.75 * k, 0); ints.add(imp);
    for (let i = 0; i < 6; i++) { const v = box(0.3 * k, 0.04, 0.08, M.copper); v.rotation.y = i * Math.PI / 3; v.position.set(0.7 * k, 0.75 * k, 0); ints.add(v); }
    g.add(ints);
    return { g, W: 3.6 * k, H: 2.0 * k, yMid: 0.75 * k, ySpan: 0.3, yLow: 0.2, spin, ports: { in: { p: [1.68 * k, 0.75 * k, 0], d: [1, 0, 0] }, out: { p: [0.7 * k, 1.8 * k, 0], d: [0, 1, 0] } } };
  }
  function gasTurbine() {
    const g = new T.Group(), spin = [];
    skid(g, 11, 3.4, 0.3, M.steelDk);
    g.add(at(box(6.2, 3.0, 3.0, M.white), -0.6, 1.8, 0));
    g.add(at(box(2.4, 2.4, 2.4, M.vesselDk), -4.9, 1.6, 0));
    g.add(at(box(2.6, 2.6, 2.6, M.blue), 3.7, 1.7, 0));
    const stack = cyl(0.7, 5.5, M.steelDk, 24); stack.position.set(2.0, 5.2, 0); g.add(stack);
    const intake = box(2.4, 1.6, 2.2, M.galv); intake.position.set(-4.9, 3.6, 0); g.add(intake);
    for (let i = 0; i < 6; i++) g.add(at(box(0.04, 1.5, 2.1, M.steelDk), -5.8 + i * 0.4, 3.6, 0));
    const fan = new T.Group(); for (let k = 0; k < 6; k++) { const b = box(0.04, 0.9, 0.1, M.steel); b.position.y = 0.5; const h = new T.Group(); h.add(b); h.rotation.x = k * Math.PI / 3; fan.add(h); } fan.position.set(-6.15, 1.6, 0); g.add(fan); spin.push(SPIN(fan, "x", 16));
    const plume = []; for (let i = 0; i < 7; i++) { const s = new T.Mesh(new T.SphereGeometry(0.5, 12, 8), new T.MeshBasicMaterial({ color: 0xcfd6dc, transparent: true, opacity: 0.2, depthWrite: false })); g.add(s); plume.push(s); }
    return { g, W: 11, H: 8, yMid: 1.6, ySpan: 0.6, yLow: 0.3, spin, plume: { list: plume, x: 2.0, y: 8.0 }, ports: { fuel: { p: [-4.9, 3.0, 0], d: [0, 1, 0] } } };
  }
  function valve(o = {}) {
    const g = new T.Group(), y = 0.7;
    const pipe = cyl(0.14, 1.9, M.steel, 16); pipe.rotation.z = Math.PI / 2; pipe.position.y = y; g.add(pipe);
    for (const x of [-0.85, 0.85]) g.add(at(flange(0.14, M.steelDk, 0), x, y, 0).rotateZ(Math.PI / 2));
    const body = mesh(new T.SphereGeometry(0.32, 24, 16), M.red); body.position.y = y; g.add(body);
    const bon = cyl(0.14, 0.6, M.steelDk, 16); bon.position.y = y + 0.5; g.add(bon);
    if (o.diaphragm) { const d1 = dome(0.38, M.green, 0.7); d1.position.y = y + 1.0; g.add(d1); const d2 = dome(0.38, M.green, 0.5); d2.rotation.x = Math.PI; d2.position.y = y + 1.0; g.add(d2); g.add(at(cyl(0.04, 0.5, M.steel, 8), 0, y + 0.8, 0)); }
    else { const hw = ring(0.28, 0.03, M.red); hw.rotation.x = Math.PI / 2; hw.position.y = y + 0.95; g.add(hw); g.add(at(cyl(0.03, 0.5, M.steel, 8), 0, y + 0.8, 0)); }
    return { g, W: 1.9, H: 2.0, yMid: y, ySpan: 0.3, yLow: 0.2, ports: { in: { p: [-0.95, y, 0], d: [-1, 0, 0] }, out: { p: [0.95, y, 0], d: [1, 0, 0] } } };
  }
  function reliefValve() {
    const g = new T.Group();
    const inl = cyl(0.12, 0.9, M.steel, 16); inl.position.y = 0.45; g.add(inl); g.add(flange(0.12, M.steelDk, 0.05));
    const body = box(0.5, 0.5, 0.5, M.red); body.position.y = 1.15; g.add(body);
    const out = cyl(0.17, 0.9, M.steel, 16); out.rotation.z = Math.PI / 2; out.position.set(0.55, 1.15, 0); g.add(out);
    const sp = cyl(0.22, 0.8, M.vesselDk, 20); sp.position.y = 1.75; g.add(sp); g.add(at(dome(0.22, M.red, 0.8), 0, 2.15, 0));
    return { g, W: 1.5, H: 2.4, yMid: 0.5, ySpan: 0.3, yLow: 0.2, ports: { in: { p: [0, 0.05, 0], d: [0, -1, 0] }, out: { p: [1.0, 1.15, 0], d: [1, 0, 0] } } };
  }
  function manifold(n) {
    const g = new T.Group(), y = 0.7;
    const hd = cyl(0.22, 2.2, M.steel, 20); hd.rotation.z = Math.PI / 2; hd.position.y = y; g.add(hd);
    for (let i = 0; i < n; i++) { const x = -0.7 + i * (1.4 / Math.max(1, n - 1)); const b = cyl(0.1, 0.8, M.steel, 12); b.position.set(x, y + 0.4, 0); g.add(b); const fl = flange(0.1, M.steelDk, y + 0.8); fl.position.x = x; g.add(fl); }
    g.add(at(mesh(new T.SphereGeometry(0.3, 16, 12), M.steelDk), 0, y, 0));
    return { g, W: 2.4, H: 1.8, yMid: y, ySpan: 0.2, yLow: 0.2, ports: { in: { p: [-1.2, y, 0], d: [-1, 0, 0] }, out: { p: [1.2, y, 0], d: [1, 0, 0] } } };
  }
  function pipeSpool(len, ins, vertical) {
    const g = new T.Group(), y = 0.7;
    const p = cyl(0.2, len, ins ? M.ins : M.steel, 24); p.rotation.z = Math.PI / 2; p.position.y = y; g.add(p);
    if (ins) for (const x of [-len * 0.3, len * 0.3]) { const b = ring(0.215, 0.02, M.steelDk); b.rotation.set(0, 0, Math.PI / 2); b.rotation.x = 0; b.rotation.y = Math.PI / 2; b.position.set(x, y, 0); g.add(b); }
    for (const x of [-len / 2, len / 2]) g.add(at(flange(0.2, M.steelDk, 0), x, y, 0).rotateZ(Math.PI / 2));
    for (const x of [-len * 0.3, len * 0.3]) g.add(at(box(0.3, y - 0.2, 0.3, M.galv), x, (y - 0.2) / 2, 0));
    return { g, W: len, H: 1.2, yMid: y, ySpan: 0.2, yLow: 0.2, ports: { in: { p: [-len / 2, y, 0], d: [-1, 0, 0] }, out: { p: [len / 2, y, 0], d: [1, 0, 0] } } };
  }
  function terminal(feed) {
    const g = new T.Group(), y = 0.8, s = feed ? 1 : -1;
    const p = cyl(0.2, 1.5, M.steel, 20); p.rotation.z = Math.PI / 2; p.position.set(0, y, 0); g.add(p);
    g.add(at(flange(0.2, M.steelDk, 0), s * 0.75, y, 0).rotateZ(Math.PI / 2));
    const v = mesh(new T.SphereGeometry(0.28, 16, 12), M.blue); v.position.set(-s * 0.2, y, 0); g.add(v);
    g.add(at(cyl(0.05, 0.6, M.steel, 8), -s * 0.2, y + 0.45, 0)); g.add(at(ring(0.2, 0.025, M.blue), -s * 0.2, y + 0.8, 0));
    g.add(at(box(0.3, y - 0.2, 0.3, M.galv), -s * 0.4, (y - 0.2) / 2, 0));
    const sign = box(1.3, 0.55, 0.05, M.blue); sign.position.set(0, 1.9, 0); g.add(sign);
    const post = box(0.06, 1.4, 0.06, M.galv); post.position.set(0, 1.1, 0); g.add(post);
    const ar = mesh(new T.ConeGeometry(0.2, 0.4, 3), M.white); ar.rotation.z = -s * Math.PI / 2; ar.position.set(s * 0.45, 1.9, 0.04); g.add(ar);
    g.add(at(box(0.7, 0.07, 0.06, M.white), -s * 0.1, 1.9, 0.04));
    return { g, W: 1.6, H: 2.3, yMid: y, ySpan: 0.2, yLow: 0.3, ports: feed ? { out: { p: [0.78, y, 0], d: [1, 0, 0] } } : { in: { p: [-0.78, y, 0], d: [-1, 0, 0] } } };
  }
  function flare() {
    const g = new T.Group(), H = 26;
    g.add(at(box(2.6, 0.4, 2.6, M.conc), 0, 0.2, 0));
    const st = cyl(0.5, H, M.steel, 24, 0.32); st.position.y = 0.4 + H / 2; g.add(st);
    for (let i = 0; i < 4; i++) { const a = i * Math.PI / 2 + Math.PI / 4; const l = cyl(0.04, 12, M.galv, 6); l.position.set(Math.cos(a) * 2.2, 6.2, Math.sin(a) * 2.2); l.lookAt(0, 12, 0); l.rotateX(Math.PI / 2); g.add(l); }
    for (let y = 3; y < H; y += 3) g.add(at(ring(0.5 - y * 0.007 + 0.03, 0.02, M.steelDk), 0, y, 0));
    const tip = cyl(0.4, 0.8, M.steelDk, 20); tip.position.y = H + 0.8; g.add(tip);
    const flame = new T.Group(); const mats = [];
    for (const [c, s] of [[0xff5a1a, 1.0], [0xffa31a, 0.7], [0xfff1a8, 0.4]]) { const m = new T.MeshBasicMaterial({ color: c, transparent: true, opacity: 0.8, depthWrite: false }); mats.push(m); const f = mesh(new T.ConeGeometry(0.45 * s + 0.1, 3.2 * s + 0.4, 16, 1, true), m, false); f.position.y = 1.6 * s + 0.2; flame.add(f); }
    flame.position.y = H + 1.2; flame.scale.setScalar(2.1); g.add(flame);
    const light = new T.PointLight(0xff7a2a, 0, 40, 2); light.position.set(0, H + 3, 0); g.add(light);
    return { g, W: 2.6, H: H + 5, yMid: 1.0, ySpan: 0.3, yLow: 0.3, flame, flameLight: light, ports: { in: { p: [-1.3, 1.0, 0], d: [-1, 0, 0] } } };
  }
  function membrane() {
    const g = new T.Group();
    skid(g, 5.6, 2.6, 0.3, M.steelDk);
    for (let row = 0; row < 2; row++) for (let i = 0; i < 3; i++) {
      const t = cyl(0.3, 4.6, M.white, 24); t.rotation.z = Math.PI / 2; t.position.set(0, 0.7 + row * 0.8, -0.8 + i * 0.8); g.add(t);
      for (const s of [-1, 1]) { const c = cyl(0.33, 0.25, M.steelDk, 24); c.rotation.z = Math.PI / 2; c.position.set(s * 2.35, 0.7 + row * 0.8, -0.8 + i * 0.8); g.add(c); }
    }
    for (const s of [-1, 1]) g.add(at(cyl(0.14, 2.2, M.steel, 16), s * 2.6, 1.1, 0).rotateX(Math.PI / 2));
    g.add(at(box(5.0, 0.12, 2.4, M.galv), 0, 0.3, 0));
    return { g, W: 5.6, H: 2.4, yMid: 1.1, ySpan: 0.7, yLow: 0.3, ports: { feed: { p: [-2.8, 1.1, 0], d: [-1, 0, 0] }, retentate: { p: [2.8, 1.1, 0], d: [1, 0, 0] }, permeate: { p: [0, 2.3, 0], d: [0, 1, 0] } } };
  }
  function molSieve() {
    const a = vesselV(1.7, 5.2), b = vesselV(1.7, 5.2), g = new T.Group();
    a.g.position.z = -1.5; b.g.position.z = 1.5; g.add(a.g, b.g);
    const hdr = cyl(0.18, 3.0, M.steel, 14); hdr.rotation.x = Math.PI / 2; hdr.position.set(-1.35, 3.2, 0); g.add(hdr);
    return { g, W: 3.4, H: a.H, yMid: 3.2, ySpan: 3.0, yLow: 0.35, lowR: 0.8, topR: 0.85, radius: 0.85 };
  }
  // ---- subsea
  function seabed(g, w, d) { const s = box(w, 0.14, d, M.seabed); s.position.y = 0.07; g.add(s); }
  function frame(g, w, d, h, y0 = 0.14) {
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) g.add(at(box(0.12, h, 0.12, M.subsea), sx * w / 2, y0 + h / 2, sz * d / 2));
    for (const y of [y0 + 0.05, y0 + h]) for (const sz of [-1, 1]) g.add(at(box(w, 0.1, 0.1, M.subsea), 0, y, sz * d / 2));
    for (const y of [y0 + 0.05, y0 + h]) for (const sx of [-1, 1]) g.add(at(box(0.1, 0.1, d, M.subsea), sx * w / 2, y, 0));
  }
  function tree(extra) {
    const g = new T.Group(); seabed(g, 4, 3.4); frame(g, 3.0, 2.6, 2.6);
    g.add(at(cyl(0.5, 0.5, M.steelDk, 24), 0, 0.4, 0));
    for (let i = 0; i < 4; i++) g.add(at(cyl(0.2, 0.34, i % 2 ? M.red : M.steel, 16), 0, 0.85 + i * 0.4, 0));
    g.add(at(cyl(0.12, 1.6, M.steel, 12), 0.8, 1.45, 0).rotateZ(Math.PI / 2));
    g.add(at(cyl(0.08, 0.5, M.steel, 12), 1.6, 1.45, 0).rotateZ(Math.PI / 2));
    g.add(at(box(0.5, 0.35, 0.3, M.orange), 0, 2.45, 0.2));
    return { g, W: 4, H: 3.0, yMid: 1.45, ySpan: 0.6, yLow: 0.3, ports: { in: { p: [-1.5, 1.0, 0], d: [-1, 0, 0] }, out: { p: [1.9, 1.45, 0], d: [1, 0, 0] }, lift: { p: [-1.0, 2.7, 0], d: [0, 1, 0] } } };
  }
  function subseaCanister(motor) {
    const g = new T.Group(); seabed(g, 5, 3.6); frame(g, 4.4, 3.0, 3.2);
    const can = cyl(0.8, 2.4, M.vessel, 32); can.position.set(0.4, 1.4, 0); g.add(can);
    const mt = cyl(0.62, 1.2, M.motor, 28); mt.position.set(0.4, 3.2, 0); g.add(mt);
    const tp = dome(0.8, M.vessel, 0.5); tp.position.set(0.4, 2.6, 0); g.add(tp);
    const sp = SPIN(g.add(at(cyl(0.05, 0.1, M.red, 6), 0.4, 3.9, 0)) && g.children[g.children.length - 1], "y", 6);
    return { g, W: 5, H: 4.1, yMid: 1.0, ySpan: 0.5, yLow: 0.3, spin: [sp] };
  }
  function subseaCooler() {
    const g = new T.Group(); seabed(g, 5, 3.6); frame(g, 4.4, 3.0, 2.4);
    for (let i = 0; i < 12; i++) g.add(at(box(0.05, 1.8, 2.6, M.galv), -2.0 + i * 0.36, 1.3, 0));
    g.add(at(cyl(0.16, 4.2, M.steel, 12), 0, 0.5, 1.2).rotateZ(Math.PI / 2)); g.add(at(cyl(0.16, 4.2, M.steel, 12), 0, 2.2, -1.2).rotateZ(Math.PI / 2));
    return { g, W: 5, H: 3.0, yMid: 0.6, ySpan: 0.2, yLow: 0.3 };
  }
  function subseaTemplate() {
    const g = new T.Group(); seabed(g, 9, 6.4); frame(g, 8.4, 5.8, 1.2);
    for (let i = 0; i < 4; i++) { const sl = ring(0.7, 0.07, M.steelDk); sl.position.set(-3.0 + i * 2.0, 1.0, -1.7); g.add(sl); }
    g.add(at(cyl(0.3, 8.0, M.steel, 20), 0, 0.7, 1.7).rotateZ(Math.PI / 2));
    for (let i = 0; i < 4; i++) g.add(at(cyl(0.12, 1.4, M.steel, 10), -3.0 + i * 2.0, 0.8, 0.6).rotateX(Math.PI / 2));
    return { g, W: 9, H: 2.0, yMid: 0.7, ySpan: 0.2, yLow: 0.3 };
  }
  function subseaMeter(kind) {
    const g = new T.Group(); seabed(g, 4.4, 2.6); frame(g, 3.8, 2.0, 1.8);
    const sp = cyl(0.25, 3.8, M.steel, 20); sp.rotation.z = Math.PI / 2; sp.position.y = 0.8; g.add(sp);
    if (kind === "mpfm") { g.add(at(cyl(0.4, 0.8, M.steelDk, 20), 0, 0.8, 0).rotateZ(Math.PI / 2)); g.add(at(box(0.9, 0.7, 0.8, M.orange), 0, 1.7, 0)); }
    if (kind === "pig") { const b = cyl(0.42, 3.0, M.vessel, 24); b.rotation.z = Math.PI / 2; b.position.set(-0.3, 0.9, 0); g.add(b); g.add(at(cyl(0.5, 0.2, M.red, 24), 1.3, 0.9, 0).rotateZ(Math.PI / 2)); }
    if (kind === "cimv") { g.add(at(cyl(0.3, 0.9, M.orange, 20), 0, 1.2, 0)); g.add(at(box(0.5, 0.3, 0.4, M.green), 0.9, 1.3, 0)); }
    return { g, W: 4.4, H: 2.2, yMid: 0.8, ySpan: 0.2, yLow: 0.3, ports: { in: { p: [-1.9, 0.8, 0], d: [-1, 0, 0] }, out: { p: [1.9, 0.8, 0], d: [1, 0, 0] } } };
  }
  function subseaChoke() {
    const g = new T.Group(); seabed(g, 3.6, 2.4); frame(g, 3.0, 1.8, 1.6);
    const sp = cyl(0.2, 3.0, M.steel, 20); sp.rotation.z = Math.PI / 2; sp.position.y = 0.8; g.add(sp);
    g.add(at(mesh(new T.SphereGeometry(0.4, 20, 14), M.red), 0, 0.8, 0)); g.add(at(cyl(0.18, 0.7, M.steelDk, 16), 0, 1.35, 0)); g.add(at(box(0.7, 0.3, 0.5, M.orange), 0, 1.85, 0));
    return { g, W: 3.6, H: 2.2, yMid: 0.8, ySpan: 0.2, yLow: 0.3, ports: { in: { p: [-1.6, 0.8, 0], d: [-1, 0, 0] }, out: { p: [1.6, 0.8, 0], d: [1, 0, 0] } } };
  }
  function hydrocyclone() {
    const g = new T.Group(); seabed(g, 4, 3); frame(g, 3.4, 2.4, 3.6);
    const co = cyl(0.55, 1.4, M.vessel, 28); co.position.y = 2.6; g.add(co); g.add(at(cyl(0.55, 1.6, M.vessel, 28, 0.12), 0, 1.1, 0));
    g.add(at(dome(0.55, M.vessel, 0.4), 0, 3.3, 0));
    return { g, W: 4, H: 4.0, yMid: 2.4, ySpan: 1.4, yLow: 0.3 };
  }
  function subseaHeater() {
    const g = new T.Group(); seabed(g, 4.4, 2.6); frame(g, 3.8, 2.0, 1.8);
    const h = cyl(0.5, 3.2, M.vessel, 28); h.rotation.z = Math.PI / 2; h.position.y = 0.95; g.add(h);
    g.add(at(cyl(0.2, 0.5, M.red, 16), -0.6, 1.7, 0)); g.add(at(box(0.7, 0.5, 0.7, M.orange), 0.8, 1.7, 0));
    return { g, W: 4.4, H: 2.3, yMid: 0.95, ySpan: 0.2, yLow: 0.3 };
  }


  // ---- draw-call reduction: bake the static meshes of one machine into one mesh per material
  function mergeGeoms(list) {
    let nv = 0, ni = 0;
    for (const g of list) { nv += g.attributes.position.count; ni += g.index ? g.index.count : g.attributes.position.count; }
    const pos = new Float32Array(nv * 3), nor = new Float32Array(nv * 3), uv = new Float32Array(nv * 2), idx = new Uint32Array(ni);
    let vo = 0, io = 0;
    for (const g of list) {
      const n = g.attributes.position.count;
      pos.set(g.attributes.position.array, vo * 3);
      if (g.attributes.normal) nor.set(g.attributes.normal.array, vo * 3);
      if (g.attributes.uv) uv.set(g.attributes.uv.array, vo * 2);
      if (g.index) for (let i = 0; i < g.index.count; i++) idx[io + i] = g.index.array[i] + vo; else for (let i = 0; i < n; i++) idx[io + i] = vo + i;
      io += g.index ? g.index.count : n; vo += n;
    }
    const out = new T.BufferGeometry();
    out.setAttribute("position", new T.BufferAttribute(pos, 3)); out.setAttribute("normal", new T.BufferAttribute(nor, 3)); out.setAttribute("uv", new T.BufferAttribute(uv, 2));
    out.setIndex(new T.BufferAttribute(idx, 1)); out.computeBoundingSphere(); out.computeBoundingBox();
    return out;
  }
  function mergeStatic(arch) {
    const g = arch.g; g.updateMatrixWorld(true);
    const keep = new Set();
    const protect = (o) => { if (!o) return; o.traverse((c) => keep.add(c)); let p = o.parent; while (p && p !== g) { keep.add(p); p = p.parent; } };
    (arch.spin || []).forEach((q) => protect(q.o)); (arch.level || []).forEach((l) => protect(l.o)); protect(arch.flame); ((arch.plume && arch.plume.list) || []).forEach(protect);
    g.traverse((c) => { if (c.userData && c.userData.internal) protect(c); });
    const inv = new T.Matrix4().copy(g.matrixWorld).invert(), byMat = new Map(), drop = [];
    g.traverse((c) => {
      if (!c.isMesh || keep.has(c) || Array.isArray(c.material)) return;
      const geo = c.geometry.clone(); geo.applyMatrix4(new T.Matrix4().multiplyMatrices(inv, c.matrixWorld));
      if (!byMat.has(c.material)) byMat.set(c.material, []);
      byMat.get(c.material).push(geo); drop.push(c);
    });
    for (const c of drop) { c.parent.remove(c); }
    for (const [m, list] of byMat) { const geo = mergeGeoms(list); list.forEach((x) => x.dispose()); const me = new T.Mesh(geo, m); me.castShadow = true; me.receiveShadow = true; g.add(me); }
    return arch;
  }

  function tintArch(arch, type, u) {           // body colour by equipment family (shared vessel paint -> a tinted copy)
    if (S.colorBy === "util" && u) {            // utilisation: one material per machine
      const key = "util:" + u.id + ":" + u.util; let mu = tintCache.get(key);
      if (!mu) { mu = M.vessel.clone(); mu.color.copy(u.util === null || u.util === undefined ? new T.Color(0xc1c9d1) : rampColor(u.util / 120)); mu.metalness = 0.2; mu.roughness = 0.45; tintCache.set(key, mu); TINTED.add(mu); }
      arch.g.traverse((c) => { if (c.isMesh && c.material === M.vessel) c.material = mu; });
      return arch;
    }
    const hex = TINT[type]; if (hex === undefined) return arch;
    const key = themeName + ":" + type; let m = tintCache.get(key);
    if (!m) { const th = THEMES[themeName]; m = M.vessel.clone(); m.color.setHex(0xaeb9c4).lerp(new T.Color(hex), th.tint); if (themeName === "studio") m.color.setHex(hex); m.metalness = themeName === "studio" ? 0.25 : 0.55; m.roughness = themeName === "studio" ? 0.42 : 0.38; tintCache.set(key, m); TINTED.add(m); }
    arch.g.traverse((c) => { if (c.isMesh && c.material === M.vessel) c.material = m; });
    return arch;
  }
  const AIR = { aircooler: 1 };
  function archetype(u) {
    const t = u.type, key = t + ":" + (u.orient || "");
    switch (t) {
      case "separator": case "scrubber":
        if (u.orient === "h") return vesselH(t === "scrubber" ? 1.6 : 2.0, t === "scrubber" ? 5.0 : 6.0);
        return vesselV(t === "scrubber" ? 1.5 : 2.2, t === "scrubber" ? 4.2 : 6.0);
      case "separator3": case "phase_splitter":
        if (u.orient === "v") return vesselV(2.6, 7.0);
        return vesselH(2.6, 9.5, { boot: true });
      case "deliquifier": return vesselV(1.2, 3.6);
      case "comp_splitter": return vesselV(1.8, 10);
      case "column": return vesselV(2.2, 16, { trays: true });
      case "teg_contactor": return vesselV(1.7, 11, { trays: true });
      case "amine_contactor": return vesselV(1.9, 13, { trays: true });
      case "conv_reactor": return vesselV(2.4, 5.0, { jacket: true, agitator: true });
      case "eq_reactor": return vesselV(2.2, 6.0, { jacket: true });
      case "mol_sieve": return molSieve();
      case "membrane": return membrane();
      case "heater": return exchanger(5.0, 0.9, false);
      case "cooler": return exchanger(5.0, 0.9, true);
      case "chiller": return exchanger(5.6, 1.05, true);
      case "hx": return exchanger(6.0, 1.1, true);
      case "aircooler": return airCooler(7.0);
      case "compressor": return compressor();
      case "expander": return expander();
      case "pump": return pump(true);
      case "gas_turbine": return gasTurbine();
      case "valve": return valve({ diaphragm: true });
      case "relief_valve": return reliefValve();
      case "mixer": return manifold(3);
      case "splitter": return manifold(3);
      case "pipe": return pipeSpool(4.0, true);
      case "feed": return terminal(true);
      case "product": return terminal(false);
      case "flare": return flare();
      case "well": case "xmas_tree": case "injection_well": return tree();
      case "template": return subseaTemplate();
      case "subsea_manifold": { const g = new T.Group(); seabed(g, 5, 3.4); frame(g, 4.4, 2.8, 1.8); const m = manifold(4); m.g.scale.setScalar(1.6); g.add(m.g); return { g, W: 5, H: 2.6, yMid: 1.1, ySpan: 0.2, yLow: 0.3, ports: { in: { p: [-1.9, 1.1, 0], d: [-1, 0, 0] }, out: { p: [1.9, 1.1, 0], d: [1, 0, 0] } } }; }
      case "jumper": case "flowline": case "riser": case "subsea_valve": { const g = new T.Group(); seabed(g, 5.2, 2.0); const sp = pipeSpool(4.6, t !== "subsea_valve"); sp.g.position.y = 0.14; g.add(sp.g); if (t === "subsea_valve") { const v = valve(); v.g.position.y = 0.14; g.add(v.g); } const p = sp.ports; p.in.p[1] += 0.14; p.out.p[1] += 0.14; return { g, W: 5.2, H: 1.6, yMid: 0.85, ySpan: 0.2, yLow: 0.3, ports: p }; }
      case "subsea_booster": case "subsea_pump": case "subsea_compressor": return subseaCanister();
      case "subsea_separator": { const g = new T.Group(); seabed(g, 8.4, 4.6); frame(g, 7.8, 4.0, 3.6); const v = vesselH(1.9, 6.0); v.g.position.y = 0.14; g.add(v.g); return { g, W: 8.4, H: 4.2, yMid: 1.9, ySpan: 0.4, yLow: 0.4, hz: true }; }
      case "subsea_cooler": return subseaCooler();
      case "subsea_heater": return subseaHeater();
      case "subsea_hydrocyclone": return hydrocyclone();
      case "subsea_choke": return subseaChoke();
      case "mpfm": return subseaMeter("mpfm");
      case "pig_station": return subseaMeter("pig");
      case "cimv": case "intensifier": return subseaMeter("cimv");
      case "intensifier2": return subseaMeter("cimv");
      default: { const g = new T.Group(); g.add(at(box(3, 2.2, 2.2, M.steel), 0, 1.1, 0)); return { g, W: 3, H: 2.4, yMid: 1.1, ySpan: 0.5, yLow: 0.3 }; }
    }
  }

  // ------------------------------------------------------------------ state
  const S = { scene: null, ports: {}, units: {}, pipes: [], pickables: [], labels: [], mats: [], flow: true, labelsOn: true, xray: false,
              dusk: false, rot: false, sel: null, hover: null, spin: [], sprites: [], boxes: [], size: 40, centre: new T.Vector3(), sig: "",
              beads: false, edit: false, snap: true, recs: [], streams: [], handles: [], layout: { units: {}, routes: {} }, pyLayout: "", layoutVer: -1, colorBy: "none", clash: false, clashes: [], measure: false, meas: [], secOn: false, stats: { valves: 0, posts: 0 }, bounds: new T.Box3(), touched: new Set(), hist: [], fut: [], nonce: 0, editChanged: false };
  const cam = { target: new T.Vector3(), theta: 0.9, phi: 1.0, radius: 60, vt: 0, vp: 0, fly: null };

  function sigOf(x) { const s = JSON.stringify(x); let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return s.length + ":" + h; }
  function disposeTree(o) {
    o.traverse((c) => { if (c.geometry) c.geometry.dispose(); if (c.material && !Object.values(M).includes(c.material) && !TINTED.has(c.material)) { if (c.material.map) c.material.map.dispose(); if (c.material.emissiveMap) c.material.emissiveMap.dispose(); c.material.dispose(); } });
  }

  // ------------------------------------------------------------------ labels
  function labelSprite(title, sub, accent) {
    const cv = document.createElement("canvas"); cv.width = 384; cv.height = 96;
    const g = cv.getContext("2d");
    g.fillStyle = "rgba(12,20,28,.86)"; g.strokeStyle = accent || "rgba(255,255,255,.4)"; g.lineWidth = 4;
    const rr = 22, x0 = 6, y0 = 6, w = 372, h = 84; g.beginPath(); g.moveTo(x0 + rr, y0); g.arcTo(x0 + w, y0, x0 + w, y0 + h, rr); g.arcTo(x0 + w, y0 + h, x0, y0 + h, rr); g.arcTo(x0, y0 + h, x0, y0, rr); g.arcTo(x0, y0, x0 + w, y0, rr); g.closePath(); g.fill(); g.stroke();
    g.fillStyle = "#f4f8fb"; g.textAlign = "center"; g.textBaseline = "middle";
    let fs = 46; g.font = `600 ${fs}px Inter, Segoe UI, Arial`; while (g.measureText(title).width > 340 && fs > 22) { fs -= 2; g.font = `600 ${fs}px Inter, Segoe UI, Arial`; }
    g.fillText(title, 192, 50);
    const tx = new T.CanvasTexture(cv); tx.colorSpace = T.SRGBColorSpace; tx.anisotropy = 4;
    const sp = new T.Sprite(new T.SpriteMaterial({ map: tx, transparent: true, depthWrite: false, depthTest: false, sizeAttenuation: false }));
    sp.scale.set(0.075, 0.0188, 1); sp.renderOrder = 20; return sp;
  }

  // ------------------------------------------------------------------ build the scene
  function portOf(arch, u, name) {
    if (arch.ports && arch.ports[name]) { const q = arch.ports[name]; return { p: new T.Vector3(...q.p), d: new T.Vector3(...q.d), stub: false }; }
    const key = u.type + ":" + (u.orient || ""), tab = S.ports[key] || S.ports[u.type];
    const e = tab && tab.ports && tab.ports[name];
    let fx = 0, fy = 0, side = "L";
    if (e) { fx = e[0]; fy = e[1]; side = e[2]; } else { side = name === "out" || /out|vapour|gas|dry|oil|water|liquid|bottom|top|sweet|acid|retentate|permeate/.test(name) ? "R" : "L"; }
    const sx = fx >= 0 ? 1 : -1, W = arch.W;
    if (side === "L") return { p: new T.Vector3(-W / 2, arch.yMid - fy * arch.ySpan, 0), d: new T.Vector3(-1, 0, 0), stub: true };
    if (side === "R") return { p: new T.Vector3(W / 2, arch.yMid - fy * arch.ySpan, 0), d: new T.Vector3(1, 0, 0), stub: true };
    if (side === "U") {
      const lim = arch.topR ? arch.topR * 0.55 : W * 0.46;
      return { p: new T.Vector3(Math.max(-lim, Math.min(lim, fx * W)), arch.H - (arch.topR ? 0.25 : 0.1), 0), d: new T.Vector3(0, 1, 0), stub: true };
    }
    if (arch.lowR) return { p: new T.Vector3(sx * arch.lowR, arch.yLow, 0), d: new T.Vector3(sx, 0, 0), stub: false };
    const x = Math.max(-W * 0.46, Math.min(W * 0.46, fx * W));
    return { p: new T.Vector3(x, arch.bottomY !== undefined ? arch.bottomY : arch.yLow + 0.3, 0), d: new T.Vector3(0, -1, 0), stub: true };
  }

  let pipeGroup = null;
  function clearWorld() {
    while (world.children.length) { const c = world.children.pop(); disposeTree(c); }
    S.units = {}; S.pipes = []; S.pickables = []; S.spin = []; S.sprites = []; S.boxes = []; S.labels = []; S.recs = []; pipeGroup = null; clashGroup = null; clearHandles();
    S.sel = null; S.hover = null;
  }

  function groundAndSun(bb) {
    const c = bb.getCenter(new T.Vector3()), sz = bb.getSize(new T.Vector3()), R = Math.max(sz.x, sz.z, 24) * 0.75 + 16;
    const padW = sz.x + 26, padD = sz.z + 26;
    S.centre.copy(c); S.size = Math.max(sz.x, sz.z, sz.y, 20);
    const cv = document.createElement("canvas"); cv.width = cv.height = 512; const g = cv.getContext("2d");
    const th = THEMES[themeName];
    g.fillStyle = th.pad; g.fillRect(0, 0, 512, 512);
    if (th.grid) { g.strokeStyle = th.grid; g.lineWidth = 3; g.strokeRect(0, 0, 512, 512); g.lineWidth = 1; for (let i = 1; i < 4; i++) { g.beginPath(); g.moveTo(i * 128, 0); g.lineTo(i * 128, 512); g.moveTo(0, i * 128); g.lineTo(512, i * 128); g.stroke(); } }
    else { for (let i = 0; i < 2600; i++) { const v = 128 + Math.random() * 40; g.fillStyle = `rgba(${v},${v},${v - 4},.35)`; g.fillRect(Math.random() * 512, Math.random() * 512, 2, 2); } g.strokeStyle = "rgba(60,60,60,.55)"; g.lineWidth = 2; g.strokeRect(0, 0, 512, 512); }
    const tx = new T.CanvasTexture(cv); tx.wrapS = tx.wrapT = T.RepeatWrapping; tx.colorSpace = T.SRGBColorSpace; tx.repeat.set(padW / 6, padD / 6); tx.anisotropy = 8;
    const pad = mesh(new T.PlaneGeometry(padW, padD), new T.MeshStandardMaterial({ map: tx, roughness: 0.95, metalness: 0 }), false);
    pad.rotation.x = -Math.PI / 2; pad.position.set(c.x, 0, c.z); pad.receiveShadow = true; world.add(pad);
    const ground = mesh(new T.CircleGeometry(900, 48), new T.MeshStandardMaterial({ color: THEMES[themeName].ground, roughness: 1 }), false);
    ground.rotation.x = -Math.PI / 2; ground.position.set(c.x, -0.06, c.z); world.add(ground);
    sun.position.set(c.x + R * 0.9, R * 1.5, c.z + R * 0.7); sun.target.position.copy(c);
    const e = R * 1.25; sun.shadow.camera.left = -e; sun.shadow.camera.right = e; sun.shadow.camera.top = e; sun.shadow.camera.bottom = -e;
    sun.shadow.camera.near = 1; sun.shadow.camera.far = R * 5; sun.shadow.camera.updateProjectionMatrix();
    scene.fog = new T.Fog(S.dusk ? 0x202a3a : THEMES[themeName].fog, R * 2.2, R * 9);
  }

  function placeUnit(R) {                       // (re)position one machine: ports, label, footprint
    const { u, arch, holder, sc_ } = R;
    holder.rotation.y = (u.flip ? Math.PI : 0) + (u.rot || 0) * Math.PI / 2;
    holder.position.set(u.x, 0, u.z); holder.updateMatrixWorld(true);
    const ports = {};
    for (const n of R.names) {
      const q = portOf(arch, u, n);
      ports[n] = { p: holder.localToWorld(q.p.clone().multiplyScalar(sc_)), d: q.d.clone().transformDirection(holder.matrixWorld).normalize(), stub: q.stub };
    }
    R.ports = ports;
    R.box = new T.Box3().setFromObject(holder); R.top = R.box.max.y;
    R.label.position.set(u.x, arch.H * sc_ + 1.6, u.z);
    if (R.beacon) R.beacon.position.set(u.x, arch.H * sc_ + 0.5, u.z);
  }

  function build(sc) {
    clearWorld();
    const units = sc.units || [], streams = sc.streams || [];
    S.streams = streams;
    const bb = new T.Box3();
    // platforms (deck outlines)
    for (const p of sc.platforms || []) {
      const g = new T.Group(); const d = box(p.w, 0.18, p.d, M.galv); d.position.y = 0.09; d.material = d.material.clone(); d.material.transparent = true; d.material.opacity = 0.35; g.add(d);
      for (const sx of [-1, 1]) g.add(at(box(0.08, 0.9, p.d, M.yellow), sx * p.w / 2, 0.55, 0));
      for (const sz of [-1, 1]) g.add(at(box(p.w, 0.08, 0.08, M.yellow), 0, 1.0, sz * p.d / 2));
      g.position.set(p.x, 0, p.z); world.add(g); bb.expandByObject(g);
      const lb = labelSprite(p.name, "platform / module", "#e8b923"); lb.position.set(p.x, 1.8, p.z + p.d / 2 + 0.5); world.add(lb); S.labels.push(lb);
    }
    // equipment: pass 1 - shapes and footprints; pass 2 - keep neighbours apart (the 2D drawing is far tighter than a plot plan);
    // hand-placed machines (S.layout) are never moved by the clearance pass
    const recs = [];
    for (const u of units) {
      const L = S.layout.units[u.id];
      if (L) { u.x = L.x; u.z = L.z; u.rot = L.rot | 0; u.placed = true; } else { u.x = u.ox; u.z = u.oz; u.rot = 0; u.placed = false; }
      const arch = mergeStatic(tintArch(archetype(u), u.type, u)), g = arch.g;
      const holder = new T.Group(); holder.add(g);
      const sc_ = Math.max(0.5, Math.min(2.2, u.scale || 1));
      g.scale.setScalar(sc_);
      holder.rotation.y = (u.flip ? Math.PI : 0) + (u.rot || 0) * Math.PI / 2;
      holder.position.set(u.x, 0, u.z);
      world.add(holder); holder.updateMatrixWorld(true);
      const fb = new T.Box3().setFromObject(holder);
      const names = new Set(); for (const s_ of streams) { if (s_.src[0] === u.id) names.add(s_.src[1]); if (s_.dst[0] === u.id) names.add(s_.dst[1]); }
      recs.push({ u, arch, holder, sc_, names: [...names], hw: (fb.max.x - fb.min.x) / 2, hd: (fb.max.z - fb.min.z) / 2, cx: (fb.max.x + fb.min.x) / 2 - u.x, cz: (fb.max.z + fb.min.z) / 2 - u.z });
    }
    const GAP = 3.2;
    for (let it = 0; it < 120; it++) {
      let moved = false;
      for (let i = 0; i < recs.length; i++) for (let j = i + 1; j < recs.length; j++) {
        const A = recs[i], B = recs[j];
        const ax = A.holder.position.x + A.cx, az = A.holder.position.z + A.cz, bx = B.holder.position.x + B.cx, bz = B.holder.position.z + B.cz;
        const ox = A.hw + B.hw + GAP - Math.abs(ax - bx), oz = A.hd + B.hd + GAP - Math.abs(az - bz);
        if (ox > 0 && oz > 0) {
          if (A.u.placed && B.u.placed) continue;
          moved = true;
          const wa = A.u.placed ? 0 : (B.u.placed ? 1 : 0.5), wb = 1 - wa;
          if (ox < oz) { const d = (ax <= bx ? -1 : 1) * ox; A.holder.position.x += d * wa; B.holder.position.x -= d * wb; }
          else { const d = (az <= bz ? -1 : 1) * oz; A.holder.position.z += d * wa; B.holder.position.z -= d * wb; }
        }
      }
      if (!moved) break;
    }
    for (const R of recs) {
      const { u, arch, holder, sc_ } = R;
      u.x = holder.position.x; u.z = holder.position.z;
      const pc = u.color || (STATUS[u.status] && u.type !== "feed" && u.type !== "product" ? "#" + STATUS[u.status].toString(16) : "");
      if (pc) plate(holder, R.hw * 1.8, R.hd * 1.8, new T.Color(pc));
      R.label = labelSprite(u.name, u.title, u.color || null); world.add(R.label); S.labels.push(R.label);
      if (STATUS[u.status] && u.type !== "feed" && u.type !== "product") {
        const bc = new T.Mesh(new T.SphereGeometry(0.32, 16, 12), new T.MeshBasicMaterial({ color: STATUS[u.status] }));
        world.add(bc); S.sprites.push({ o: bc, kind: "beacon" }); R.beacon = bc;
      }
      placeUnit(R); bb.union(R.box);
      R.flameLight = arch.flameLight; R.ports = R.ports;
      S.units[u.id] = R; S.recs.push(R);
      holder.traverse((c) => { if (c.isMesh) { c.userData.uid = u.id; S.pickables.push(c); } });
      for (const sp of arch.spin || []) S.spin.push({ o: sp.o, axis: sp.axis, rate: sp.rate, on: u.status === "ok" || u.status === "warning" });
      if (arch.flame) R.flame = arch.flame;
      if (arch.plume) R.plume = arch.plume;
      if (arch.level) R.level = arch.level;
    }
    buildPipes();
    S.bounds = bb.isEmpty() ? new T.Box3(new T.Vector3(-10, 0, -10), new T.Vector3(10, 5, 10)) : bb.clone();
    groundAndSun(S.bounds);
    runClash(); applySection();
    applyToggles(); shadowsDirty(3);
  }

  // ---- pipes: auto-routed along a rack, or along the hand-edited route stored in S.layout.routes
  function clearHandles() { for (const h of S.handles) { if (h.parent) h.parent.remove(h); h.geometry.dispose(); h.material.dispose(); } S.handles = []; }
  function buildPipes() {
    if (pipeGroup) { world.remove(pipeGroup); disposeTree(pipeGroup); }
    S.pickables = S.pickables.filter((o) => !(o.userData && o.userData.pipePart));
    S.labels = S.labels.filter((l) => !l.userData.pipeTag);
    S.pipes = []; pipeGroup = new T.Group(); world.add(pipeGroup);
    const streams = S.streams;
    const obstacles = S.recs.map((R) => ({ x0: R.box.min.x - 0.6, x1: R.box.max.x + 0.6, z0: R.box.min.z - 0.6, z1: R.box.max.z + 0.6, top: R.box.max.y, id: R.u.id }));
    const maxTop = obstacles.reduce((a, o) => Math.max(a, o.top), 6);
    const posts = new Set(), inst = { posts: [], caps: [], fl: [], stubs: [], valves: [] };
    streams.forEach((s, idx) => {
      const A = S.units[s.src[0]], B = S.units[s.dst[0]];
      if (!A || !B) return;
      const pa = A.ports[s.src[1]], pb = B.ports[s.dst[1]];
      if (!pa || !pb) return;
      const rad = Math.max(0.05, Math.min(0.7, (s.d || 0.14) / 2 * 1.08));
      // stagger several connections that share one port
      const shareA = streams.filter((o, j) => j < idx && o.src[0] === s.src[0] && o.src[1] === s.src[1]).length;
      const shareB = streams.filter((o, j) => j < idx && o.dst[0] === s.dst[0] && o.dst[1] === s.dst[1]).length;
      const off = (n) => new T.Vector3(0, n * 0.55, 0);
      const a = pa.p.clone().add(off(shareA)), b = pb.p.clone().add(off(shareB));
      let path = null;
      const rt = S.layout.routes[s.id];
      if (rt && rt.length >= 3) {
        const pts = rt.map((q) => new T.Vector3(q[0], q[1], q[2]));
        if (pts[0].distanceTo(a) < 0.08 && pts[pts.length - 1].distanceTo(b) < 0.08) { pts[0].copy(a); pts[pts.length - 1].copy(b); path = pts; }
      }
      if (!path) path = routePipe(a, pa.d, b, pb.d, idx, rad, obstacles, maxTop, s.src[0], s.dst[0]);
      makePipe(s, path, rad, A, B, pa, pb, posts, inst);
    });
    flushInstances(inst);
    buildFlow(); applyPipeLook(); S.stats.valves = inst.valves.length; S.stats.posts = inst.posts.length; shadowsDirty(2);
    if (S.recs.length && S.recs.every((R) => R.box)) runClash();
  }
  function flushInstances(inst) {
    const dm = new T.Object3D(), Y = new T.Vector3(0, 1, 0);
    const mk = (geo, mat, arr, fn) => {
      if (!arr.length) return;
      const im = new T.InstancedMesh(geo, mat, arr.length);
      arr.forEach((q, i) => { dm.position.set(0, 0, 0); dm.quaternion.identity(); dm.scale.set(1, 1, 1); fn(dm, q); dm.updateMatrix(); im.setMatrixAt(i, dm.matrix); });
      im.castShadow = true; im.receiveShadow = true; im.frustumCulled = false; pipeGroup.add(im);
    };
    mk(new T.BoxGeometry(1, 1, 1), M.galv, inst.posts, (d, q) => { d.position.set(q.x, q.h / 2, q.z); d.scale.set(0.16, q.h, 0.16); });
    mk(new T.BoxGeometry(1, 1, 1), M.galv, inst.caps, (d, q) => { d.position.set(q.x, q.y, q.z); d.scale.set(0.9, 0.1, 0.18); });
    mk(new T.CylinderGeometry(1, 1, 1, 18), M.steelDk, inst.fl, (d, q) => { d.position.copy(q.p); d.quaternion.setFromUnitVectors(Y, q.dir); d.scale.set(q.r, 0.1, q.r); });
    mk(new T.SphereGeometry(1, 16, 12), M.red, inst.valves, (d, q) => { d.position.copy(q.p); d.scale.set(q.r * 1.7, q.r * 1.5, q.r * 1.7); });
    mk(new T.CylinderGeometry(1, 1, 1, 12), M.steelDk, inst.valves, (d, q) => { d.position.copy(q.p).y += q.r * 1.9; d.scale.set(q.r * 0.45, q.r * 2.4, q.r * 0.45); });
    mk(new T.CylinderGeometry(1, 1, 1, 18), M.black, inst.valves, (d, q) => { d.position.copy(q.p).y += q.r * 3.2; d.scale.set(q.r * 1.5, q.r * 0.14, q.r * 1.5); });
    mk(new T.CylinderGeometry(1, 1, 1, 18), M.steelDk, inst.valves, (d, q) => { for (const k of [-1, 1]) {} d.position.copy(q.p).addScaledVector(q.dir, q.r * 1.9); d.quaternion.setFromUnitVectors(Y, q.dir); d.scale.set(q.r * 1.35, 0.1, q.r * 1.35); });
    mk(new T.CylinderGeometry(1, 1, 1, 18), M.steelDk, inst.valves, (d, q) => { d.position.copy(q.p).addScaledVector(q.dir, -q.r * 1.9); d.quaternion.setFromUnitVectors(Y, q.dir); d.scale.set(q.r * 1.35, 0.1, q.r * 1.35); });
    mk(new T.CylinderGeometry(1, 1, 1, 14), M.steel, inst.stubs, (d, q) => { d.position.copy(q.p); d.quaternion.setFromUnitVectors(Y, q.dir); d.scale.set(q.r, 0.7, q.r); });
  }

  // ------------------------------------------------------------------ pipe routing
  function legLen(p, d, y) { if (d.y < -0.5) return Math.max(0.25, Math.min(1.0, p.y - 0.25)); return 0.9; }
  function segHits(a, b, obstacles, skip, hr, rad) {
    let hits = 0;
    for (const o of obstacles) {
      if (o.top < hr - rad - 0.15) continue;
      const x0 = Math.min(a.x, b.x) - rad, x1 = Math.max(a.x, b.x) + rad, z0 = Math.min(a.z, b.z) - rad, z1 = Math.max(a.z, b.z) + rad;
      if (x1 > o.x0 && x0 < o.x1 && z1 > o.z0 && z0 < o.z1) hits++;
    }
    return hits;
  }
  function routePipe(a, da, b, db, idx, rad, obstacles, maxTop, ia, ib) {
    const p1 = a.clone().addScaledVector(da, legLen(a, da)), p4 = b.clone().addScaledVector(db, legLen(b, db));
    const dist = Math.hypot(p1.x - p4.x, p1.z - p4.z);
    const lowish = a.y < 4 && b.y < 4;
    let hr = dist < 14 && lowish ? Math.max(p1.y, p4.y, 1.4) + 1.6 + (idx % 3) * 0.6 : 6.4 + (idx % 4) * 0.8;
    hr = Math.max(hr, p1.y + 0.4, p4.y + 0.4);
    const mid = (p1.z + p4.z) / 2, jit = ((idx * 7) % 5 - 2) * 0.45;
    const best = (h) => {
      let pick = null;
      const lanes = [p1.z + jit * 0.4, p4.z + jit * 0.4, mid + jit, mid + 2.5 + jit, mid - 2.5 + jit, mid + 5 + jit, mid - 5 + jit, mid + 9, mid - 9];
      for (const zl of lanes) {
        const q = [new T.Vector3(p1.x, h, p1.z), new T.Vector3(p1.x, h, zl), new T.Vector3(p4.x, h, zl), new T.Vector3(p4.x, h, p4.z)];
        let c = 0; for (let i = 0; i < 3; i++) c += segHits(q[i], q[i + 1], obstacles, null, h, rad);
        if (!pick || c < pick.c) pick = { c, q, zl };
        if (c === 0) break;
      }
      return pick;
    };
    let pk = best(hr);
    if (pk.c > 0) { hr = maxTop + 1.6 + (idx % 4) * 0.7; pk = best(hr); }
    const pts = [a.clone(), p1, ...pk.q, p4, b.clone()];
    // drop duplicates and collinear points
    const out = [pts[0]];
    for (let i = 1; i < pts.length; i++) { if (pts[i].distanceTo(out[out.length - 1]) > 0.02) out.push(pts[i]); }
    const res = [out[0]];
    for (let i = 1; i < out.length - 1; i++) {
      const u1 = out[i].clone().sub(out[i - 1]).normalize(), u2 = out[i + 1].clone().sub(out[i]).normalize();
      if (u1.dot(u2) < 0.999) res.push(out[i]);
    }
    res.push(out[out.length - 1]);
    return res;
  }
  function roundedPolyline(pts, rc) {
    const out = [pts[0].clone()];
    for (let i = 1; i < pts.length - 1; i++) {
      const p = pts[i], a = pts[i - 1], b = pts[i + 1];
      const v1 = a.clone().sub(p), v2 = b.clone().sub(p), l1 = v1.length(), l2 = v2.length();
      const r = Math.min(rc, l1 * 0.48, l2 * 0.48);
      v1.normalize(); v2.normalize();
      const s = p.clone().addScaledVector(v1, r), e = p.clone().addScaledVector(v2, r);
      out.push(s);
      for (let k = 1; k < 8; k++) { const t = k / 8, w0 = (1 - t) * (1 - t), w1 = 2 * (1 - t) * t, w2 = t * t; out.push(new T.Vector3(w0 * s.x + w1 * p.x + w2 * e.x, w0 * s.y + w1 * p.y + w2 * e.y, w0 * s.z + w1 * p.z + w2 * e.z)); }
      out.push(e);
    }
    out.push(pts[pts.length - 1].clone());
    return out;
  }
  const STRIPE_PERIOD = 2.4;
  let stripeBase = null;
  function stripeTexture() {
    if (stripeBase) return stripeBase;
    const cv = document.createElement("canvas"); cv.width = 128; cv.height = 4; const g = cv.getContext("2d");
    const gr = g.createLinearGradient(0, 0, 128, 0); gr.addColorStop(0, "#000"); gr.addColorStop(0.7, "#7a7a7a"); gr.addColorStop(0.88, "#fff"); gr.addColorStop(0.9, "#000"); gr.addColorStop(1, "#000");
    g.fillStyle = gr; g.fillRect(0, 0, 128, 4);
    stripeBase = new T.CanvasTexture(cv); stripeBase.wrapS = T.RepeatWrapping; stripeBase.wrapT = T.ClampToEdgeWrapping; stripeBase.colorSpace = T.SRGBColorSpace;
    return stripeBase;
  }
  function makePipe(s, path, rad, A, B, pa, pb, posts, inst) {
    const dense = roundedPolyline(path, Math.max(0.45, rad * 3.2));
    const cum = [0]; for (let i = 1; i < dense.length; i++) cum.push(cum[i - 1] + dense[i].distanceTo(dense[i - 1]));
    const len = cum[cum.length - 1];
    const curve = new T.CatmullRomCurve3(dense, false, "centripetal");
    const segs = Math.max(24, Math.min(400, Math.round(len / 0.45)));
    const flowing = s.solved && (s.mass || 0) > 0;
    const mat = new T.MeshStandardMaterial({ color: 0xcccccc, roughness: 0.34, metalness: 0.3 });
    let tex = null;
    if (flowing) { tex = stripeTexture().clone(); tex.needsUpdate = true; tex.repeat.set(Math.max(1, len / STRIPE_PERIOD), 1); mat.emissiveMap = tex; }
    const tube = new T.Mesh(new T.TubeGeometry(curve, segs, rad, 14, false), mat);
    tube.castShadow = true; tube.receiveShadow = false; tube.userData.sid = s.id; tube.userData.pipePart = true;
    pipeGroup.add(tube); S.pickables.push(tube);
    // flanges at both ends, nozzle stubs on equipment (instanced: one draw call each)
    const d0 = dense[1].clone().sub(dense[0]).normalize(), n = dense.length, d1 = dense[n - 1].clone().sub(dense[n - 2]).normalize();
    inst.fl.push({ p: dense[0].clone().addScaledVector(d0, 0.05), dir: d0, r: rad * 1.35 }, { p: dense[n - 1].clone().addScaledVector(d1, -0.05), dir: d1, r: rad * 1.35 });
    for (const pp of [pa, pb]) if (pp.stub) inst.stubs.push({ p: pp.p.clone().addScaledVector(pp.d, -0.3), dir: pp.d.clone(), r: rad * 1.02 });
    // supports under long horizontal runs
    for (let i = 1; i < path.length; i++) {
      const p = path[i - 1], q = path[i], hrz = Math.abs(p.y - q.y) < 0.01 && p.y > 2.2;
      if (!hrz) continue;
      const L = p.distanceTo(q);
      for (let d = 2.5; d < L; d += 6) {
        const x = p.x + (q.x - p.x) * d / L, z = p.z + (q.z - p.z) * d / L, key = Math.round(x / 3) + "," + Math.round(z / 3) + "," + Math.round(p.y);
        if (posts.has(key)) continue; posts.add(key);
        inst.posts.push({ x, z, h: p.y - rad }); inst.caps.push({ x, z, y: p.y - rad - 0.02 });
      }
    }
    // inline block valve at 30 % of the longest straight horizontal run (>= 3.2 m), line tag above the middle of it
    let best = null;
    for (let i = 1; i < path.length; i++) { const a_ = path[i - 1], b_ = path[i], L_ = a_.distanceTo(b_); if (Math.abs(a_.y - b_.y) < 0.01 && L_ >= 3.2 && (!best || L_ > best.L)) best = { a: a_, b: b_, L: L_ }; }
    if (best) {
      const dir = best.b.clone().sub(best.a).normalize();
      inst.valves.push({ p: best.a.clone().addScaledVector(dir, best.L * 0.3), dir, r: rad });
      const tg = labelSprite(s.name, "", "#9fb4c8"); tg.position.copy(best.a).addScaledVector(dir, best.L * 0.55); tg.position.y += rad + 0.9; tg.userData.pipeTag = true; tg.userData.k = 0.8; tg.visible = false;
      pipeGroup.add(tg); S.labels.push(tg);
    }
    const rec = { s, tube, mat, tex, dense, cum, len, rad, flowing, parts: [], path };
    if (flowing && len > 0.5) {
      const dens = { gas: 1.0, liquid: 0.7, water: 0.7, "two-phase": 0.9 }[s.service] || 0.8;
      const nP = Math.max(4, Math.min(140, Math.round(len * dens)));
      const speed = Math.max(0.7, Math.min(6.5, 0.9 + 2.2 * Math.log10(1 + (s.vel || 1))));
      rec.speed = speed; rec.nP = nP;
      for (let k = 0; k < nP; k++) rec.parts.push({ f: k / nP + (Math.sin(k * 12.9898 + s.id.length) * 0.5 + 0.5) * 0.4 / nP, svc: s.service === "two-phase" ? (k % 2 ? "gas" : "liquid") : s.service });
    }
    S.pipes.push(rec);
  }
  function applyPipeLook() {                    // service colours or a colour-by ramp; stripes (flow) or see-through pipes with beads
    const mode = S.colorBy, rg = (mode !== "none" && mode !== "util") ? metricRange(mode) : null;
    S.range = rg;
    for (const p of S.pipes) {
      let col = new T.Color(SERVICE[p.s.service] || 0xcccccc), glow = FLOWCOL[p.s.service] || 0xffffff;
      if (mode === "util") { col = new T.Color(0xc4ccd4); glow = 0xffffff; }
      else if (rg) { const v = streamMetric(p.s, mode); if (v === null || !isFinite(v)) col = new T.Color(0xaab2ba); else { col = rampColor((v - rg.lo) / (rg.hi - rg.lo)); } glow = 0xffffff; }
      p.mat.color.copy(p.flowing ? col : new T.Color(0x8d969e));
      p.mat.emissive.set(p.flowing ? glow : 0x000000); p.mat.emissiveIntensity = p.flowing ? (mode === "none" ? 1.0 : 0.55) : 0;
      p.mat.metalness = p.flowing ? 0.3 : 0.6;
      const tr = S.beads && p.flowing;
      p.mat.transparent = tr; p.mat.opacity = tr ? 0.5 : 1; p.mat.depthWrite = !tr; p.mat.needsUpdate = true;
      if (p.tex) p.mat.emissiveMap = S.flow ? p.tex : null;
    }
  }

  // ------------------------------------------------------------------ flow particles (one instanced mesh)
  let flowMesh = null, flowCount = 0;
  const dummy = new T.Object3D(), tmpC = new T.Color();
  function buildFlow() {
    if (flowMesh) { scene.remove(flowMesh); flowMesh.geometry.dispose(); flowMesh.material.dispose(); flowMesh = null; }
    flowCount = S.pipes.reduce((a, p) => a + p.parts.length, 0);
    if (!flowCount) return;
    flowMesh = new T.InstancedMesh(new T.SphereGeometry(1, 10, 8), new T.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }), flowCount);
    flowMesh.frustumCulled = false; flowMesh.renderOrder = 6;
    let i = 0;
    for (const p of S.pipes) for (const q of p.parts) { tmpC.set(FLOWCOL[q.svc] || 0xffffff); flowMesh.setColorAt(i++, tmpC); }
    flowMesh.instanceColor.needsUpdate = true;
    scene.add(flowMesh);
  }
  function samplePath(p, s, out) {
    const cum = p.cum; let lo = 0, hi = cum.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (cum[m] <= s) lo = m; else hi = m; }
    const t = (s - cum[lo]) / Math.max(1e-6, cum[hi] - cum[lo]); out.lerpVectors(p.dense[lo], p.dense[hi], t); return out;
  }
  const tmpV = new T.Vector3(), tmpV2 = new T.Vector3();
  function updateFlow(t) {
    if (!flowMesh && !S.pipes.length) return;
    if (flowMesh) flowMesh.visible = S.flow && S.beads;
    for (const p of S.pipes) if (p.tex && S.flow) p.tex.offset.x = -((t * p.speed / STRIPE_PERIOD) % 1);
    if (!(S.flow && S.beads) || !flowMesh) return;
    let i = 0;
    for (const p of S.pipes) {
      const r = Math.max(0.04, p.rad * 0.46);
      for (const q of p.parts) {
        const s = ((q.f * p.len + t * p.speed) % p.len + p.len) % p.len;
        samplePath(p, s, tmpV);
        const k = q.svc === "gas" ? r * 0.85 : r * 1.25;
        dummy.position.copy(tmpV); dummy.scale.setScalar(k); dummy.updateMatrix(); flowMesh.setMatrixAt(i++, dummy.matrix);
      }
    }
    flowMesh.instanceMatrix.needsUpdate = true;
  }

  // ------------------------------------------------------------------ toggles
  const XRAY_MATS = [M.vessel, M.vesselDk, M.white, M.blue];
  function applyToggles() {
    for (const l of S.labels) l.visible = S.labelsOn && (!l.userData.pipeTag || S.pipeTags);
    for (const m of [...XRAY_MATS, ...TINTED]) { m.transparent = S.xray; m.opacity = S.xray ? 0.22 : 1; m.depthWrite = !S.xray; m.needsUpdate = true; }
    world.traverse((o) => { if (o.userData && o.userData.internal) o.visible = S.xray; }); shadowsDirty(2);
    const th = THEMES[themeName];
    scene.background = skyTexture(S.dusk ? sky.dusk : (th.sky || sky.day));
    hemi.intensity = S.dusk ? 0.22 : th.hemi; sun.intensity = S.dusk ? 0.7 : th.sun; sun.color.set(S.dusk ? 0xffb27a : 0xfff2dc);
    renderer.toneMappingExposure = S.dusk ? 0.85 : th.exposure; scene.environmentIntensity = S.dusk ? 0.25 : th.env;
    if (scene.fog) scene.fog.color.set(S.dusk ? 0x202a3a : th.fog);
    applyPipeLook();
    for (const b of ["bFlow", "bBeads", "bLabels", "bXray", "bNight", "bRot", "bEdit", "bSnap", "bPTags"]) { const el = $(b), on = { bFlow: S.flow, bBeads: S.beads, bLabels: S.labelsOn, bXray: S.xray, bNight: S.dusk, bRot: S.rot, bEdit: S.edit, bSnap: S.snap, bPTags: S.pipeTags }[b]; if (el) el.classList.toggle("on", !!on); }
    const et = $("editTools"); if (et) et.style.display = S.edit ? "flex" : "none";
    const ht = $("hint"); if (ht) ht.textContent = S.edit ? "EDIT: drag equipment to move · R: rotate · select a pipe and drag its handles · Ctrl+Z undo" : "drag: rotate · right-drag / Shift: pan · wheel: zoom · click: inspect · double-click: fly to";
  }

  // ------------------------------------------------------------------ camera (orbit / pan / zoom / fly-to)
  function placeCamera() {
    const sp = Math.sin(cam.phi), x = cam.radius * sp * Math.sin(cam.theta), y = cam.radius * Math.cos(cam.phi), z = cam.radius * sp * Math.cos(cam.theta);
    camera.position.set(cam.target.x + x, cam.target.y + y, cam.target.z + z);
    camera.lookAt(cam.target);
  }
  function flyTo(tx, ty, tz, radius, theta, phi) {
    cam.fly = { t0: performance.now(), dur: 750, from: { x: cam.target.x, y: cam.target.y, z: cam.target.z, r: cam.radius, th: cam.theta, ph: cam.phi },
                to: { x: tx, y: ty, z: tz, r: radius, th: theta === undefined ? cam.theta : theta, ph: phi === undefined ? cam.phi : phi } };
  }
  function view(kind) {
    const c = S.centre, R = S.size;
    if (kind === "top") flyTo(c.x, 0, c.z, R * 1.5, cam.theta, 0.001 + 0.02);
    else if (kind === "front") flyTo(c.x, Math.min(8, R * 0.12), c.z, R * 1.5, 0, 1.45);
    else flyTo(c.x, R * 0.05, c.z, Math.max(26, R * 0.72), 0.85, 1.0);
  }
  function lerp(a, b, t) { return a + (b - a) * t; }
  const ease = (t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

  const ptr = new Map(); let drag = null, moved = 0, pendingHover = null;
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId); ptr.set(e.pointerId, { x: e.clientX, y: e.clientY });
    drag = { mode: e.button === 2 || e.shiftKey || e.button === 1 ? "pan" : "rot", x: e.clientX, y: e.clientY }; moved = 0; cam.fly = null; S.rot = false; applyToggles();
    if (S.edit && e.button === 0 && !e.shiftKey && ptr.size === 1 && editDown(e)) drag.mode = "edit";
  });
  canvas.addEventListener("pointermove", (e) => {
    const p = ptr.get(e.pointerId);
    if (!p) { pendingHover = e; return; }
    const old = { x: p.x, y: p.y }; p.x = e.clientX; p.y = e.clientY;
    if (ptr.size === 2) {                    // pinch
      const [a, b] = [...ptr.values()]; const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (drag && drag.pinch) cam.radius = Math.max(3, Math.min(900, cam.radius * drag.pinch / Math.max(1, d)));
      if (drag) drag.pinch = d; return;
    }
    const dx = e.clientX - old.x, dy = e.clientY - old.y; moved += Math.abs(dx) + Math.abs(dy);
    if (!drag) return;
    if (drag.mode === "edit") { editMove(e); return; }
    if (drag.mode === "rot") { cam.vt -= dx * 0.0052; cam.vp -= dy * 0.0052; cam.theta -= dx * 0.0052; cam.phi = Math.max(0.02, Math.min(1.52, cam.phi - dy * 0.0052)); }
    else {
      const k = cam.radius * 0.0014, right = new T.Vector3().setFromMatrixColumn(camera.matrix, 0), fwd = new T.Vector3(-Math.sin(cam.theta), 0, -Math.cos(cam.theta));
      cam.target.addScaledVector(right, -dx * k).addScaledVector(fwd, dy * k);
    }
  });
  const up = (e) => {
    ptr.delete(e.pointerId);
    if (drag && drag.mode === "edit") editUp();
    else if (drag && moved < 5 && e.type === "pointerup") { if (S.measure) measureClick(e); else pickAt(e, false); }
    if (!ptr.size) drag = null; else if (drag) drag.pinch = 0;
  };
  canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);
  canvas.addEventListener("dblclick", (e) => pickAt(e, true));
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); cam.fly = null; const f = Math.exp(e.deltaY * 0.0012); cam.radius = Math.max(3, Math.min(900, cam.radius * f)); }, { passive: false });


  // ------------------------------------------------------------------ layout editing (move / rotate equipment, drag pipe runs)
  const SNAP_U = 1.0, SNAP_P = 0.5;
  const snapV = (v, g) => (S.snap ? Math.round(v / g) * g : Math.round(v * 20) / 20);
  const groundPlane = new T.Plane(new T.Vector3(0, 1, 0), 0), planeTmp = new T.Vector3();
  function rayAt(e) { const r = canvas.getBoundingClientRect(); ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1); ray.setFromCamera(ndc, camera); return ray; }
  function groundPoint(e, y) { groundPlane.constant = -(y || 0); return rayAt(e).ray.intersectPlane(groundPlane, planeTmp) ? planeTmp.clone() : null; }
  function verticalPoint(e, at_) {            // point on a camera-facing vertical plane through at_
    const n = new T.Vector3(camera.position.x - at_.x, 0, camera.position.z - at_.z).normalize();
    const pl = new T.Plane().setFromNormalAndCoplanarPoint(n, at_);
    return rayAt(e).ray.intersectPlane(pl, planeTmp) ? planeTmp.clone() : null;
  }
  function beginEdit() { if (!S.inEdit) { S.hist.push(JSON.stringify(S.layout)); S.fut = []; S.inEdit = true; S.editChanged = false; } }
  function snapshotLayout() {
    const L = { units: {}, routes: JSON.parse(JSON.stringify(S.layout.routes || {})), views: JSON.parse(JSON.stringify(S.layout.views || [])) };
    for (const id in S.units) { const u = S.units[id].u; L.units[id] = { x: Math.round(u.x * 100) / 100, z: Math.round(u.z * 100) / 100, rot: (u.rot || 0) % 4 }; }
    return L;
  }
  function postLayout(extra) {
    S.pendingEcho = true;
    const touched = [...S.touched]; S.touched.clear();
    post("streamlit:setComponentValue", { value: Object.assign({ nonce: Date.now() + "-" + (++S.nonce), layout: S.layout, touched }, extra || {}), dataType: "json" });
  }
  function endEdit() {
    if (!S.inEdit) return; S.inEdit = false;
    if (!S.editChanged) { S.hist.pop(); return; }
    for (const id of S.touched) { const R = S.units[id]; if (R) { R.u.ox = R.u.x; R.u.oz = R.u.z; } }
    MAP.dirty = true;
    S.layout = snapshotLayout(); postLayout(); shadowsDirty(3); updateUndoButtons();
  }
  function updateUndoButtons() { const u = $("bUndo"), r = $("bRedo"); if (u) u.disabled = !S.hist.length; if (r) r.disabled = !S.fut.length; }
  function invalidateRoutes(uid) { for (const s of S.streams) if (s.src[0] === uid || s.dst[0] === uid) delete S.layout.routes[s.id]; }
  let rebuildQueued = false;
  function schedulePipes() { if (rebuildQueued) return; rebuildQueued = true; setTimeout(() => { rebuildQueued = false; buildPipes(); if (S.sel && S.sel.kind === "stream") refreshHandles(); refreshOutline(); }, 40); }
  function refreshOutline() { if (boxSel) { scene.remove(boxSel); boxSel = null; } if (S.sel) { boxSel = outline(S.sel.kind, S.sel.id, new T.Color(0x2dd4c4)); if (boxSel) scene.add(boxSel); } }
  function applyLayoutFromHistory(json) {
    const before = {}; for (const id in S.units) before[id] = [S.units[id].u.x, S.units[id].u.z];
    S.layout = JSON.parse(json); const sel = S.sel; build(S.scene); legend(S.scene);
    for (const id in S.units) if (before[id] && (Math.abs(before[id][0] - S.units[id].u.x) > 0.01 || Math.abs(before[id][1] - S.units[id].u.z) > 0.01)) S.touched.add(id);
    S.layout = snapshotLayout(); postLayout();
    if (sel && ((sel.kind === "unit" && S.units[sel.id]) || (sel.kind === "stream" && S.pipes.find((q) => q.s.id === sel.id)))) select(sel.kind, sel.id, false); else select(null);
    updateUndoButtons();
  }
  function undo() { if (!S.hist.length) return; S.fut.push(JSON.stringify(S.layout)); applyLayoutFromHistory(S.hist.pop()); }
  function redo() { if (!S.fut.length) return; S.hist.push(JSON.stringify(S.layout)); applyLayoutFromHistory(S.fut.pop()); }
  function rotateSel() {
    if (!S.sel || S.sel.kind !== "unit") return;
    const R = S.units[S.sel.id]; beginEdit(); R.u.rot = ((R.u.rot || 0) + 1) % 4; invalidateRoutes(R.u.id); placeUnit(R); S.editChanged = true;
    buildPipes(); refreshOutline(); endEdit();
  }
  function resetLayout() { S.hist.push(JSON.stringify(S.layout)); S.fut = []; S.layout = { units: {}, routes: {} }; postLayout({ reset: true }); const sel = S.sel; build(S.scene); legend(S.scene); if (sel && S.units[sel.id]) select(sel.kind, sel.id, false); else select(null); updateUndoButtons(); }

  // unit drag
  let ud = null;
  function startUnitDrag(id, e) {
    const R = S.units[id], g0 = groundPoint(e, 0); if (!g0) return;
    beginEdit(); ud = { R, g0, x0: R.u.x, z0: R.u.z };
  }
  function moveUnit(e) {
    const g = groundPoint(e, 0); if (!g || !ud) return;
    const nx = snapV(ud.x0 + (g.x - ud.g0.x), SNAP_U), nz = snapV(ud.z0 + (g.z - ud.g0.z), SNAP_U);
    if (nx === ud.R.u.x && nz === ud.R.u.z) return;
    ud.R.u.x = nx; ud.R.u.z = nz; S.editChanged = true; S.touched.add(ud.R.u.id); invalidateRoutes(ud.R.u.id); placeUnit(ud.R); schedulePipes(); shadowsDirty(2);
  }

  // pipe handles
  const axisOf = (a, b) => { const d = [Math.abs(b.x - a.x), Math.abs(b.y - a.y), Math.abs(b.z - a.z)]; const m = Math.max(...d); if (m < 1e-4) return null; return ["x", "y", "z"][d.indexOf(m)]; };
  function refreshHandles() {
    clearHandles();
    if (!S.edit || !S.sel || S.sel.kind !== "stream") return;
    const rec = S.pipes.find((q) => q.s.id === S.sel.id); if (!rec) return;
    const P = rec.path, n = P.length, add = (pos, color, hd) => {
      const m = new T.Mesh(new T.OctahedronGeometry(0.34, 0), new T.MeshBasicMaterial({ color, depthTest: false, transparent: true, opacity: 0.95 }));
      m.position.copy(pos); m.renderOrder = 40; m.userData.handle = hd; scene.add(m); S.handles.push(m);
    };
    for (let k = 1; k <= n - 3; k++) {
      const a = P[k], b = P[k + 1]; if (a.distanceTo(b) < 0.7) continue;
      const ax = axisOf(a, b), prev = axisOf(P[k - 1], a), next = axisOf(b, P[k + 2]);
      const ok = ["x", "z"].filter((q) => q !== ax && (prev === null || prev === q) && (next === null || next === q));
      if (ok.length) add(a.clone().add(b).multiplyScalar(0.5), 0x2dd4c4, { sid: rec.s.id, kind: "seg", k, axes: ok });
    }
    const top = Math.max(...P.map((q) => q.y)), idx = []; for (let i = 2; i <= n - 3; i++) if (P[i].y >= top - 0.01) idx.push(i);
    if (idx.length >= 2 && top > Math.max(P[0].y, P[n - 1].y) + 0.3) {
      const c = new T.Vector3(); idx.forEach((i) => c.add(P[i])); c.multiplyScalar(1 / idx.length); c.y += 0.9;
      add(c, 0xff9f1c, { sid: rec.s.id, kind: "elev", idx });
    }
  }
  let hd = null;
  function hitHandle(e) { if (!S.handles.length) return null; const hs = rayAt(e).intersectObjects(S.handles, false); return hs.length ? hs[0].object.userData.handle : null; }
  function startHandleDrag(h, e) {
    const rec = S.pipes.find((q) => q.s.id === h.sid); if (!rec) return;
    const base = rec.path.map((v) => v.clone()), at_ = h.kind === "elev" ? base[h.idx[0]].clone() : base[h.k].clone().add(base[h.k + 1]).multiplyScalar(0.5);
    const p0 = h.kind === "elev" ? verticalPoint(e, at_) : groundPoint(e, at_.y); if (!p0) return;
    beginEdit(); hd = { h, rec, base, p0, at: at_, axis: h.axes && h.axes.length === 1 ? h.axes[0] : null };
  }
  function moveHandle(e) {
    if (!hd) return; const { h, base } = hd;
    const P = base.map((v) => v.clone());
    if (h.kind === "elev") {
      const q = verticalPoint(e, hd.at); if (!q) return;
      const floor = Math.max(P[0].y, P[P.length - 1].y) + 0.5;
      const ny = Math.max(floor, Math.min(40, snapV(base[h.idx[0]].y + (q.y - hd.p0.y), SNAP_P)));
      for (const i of h.idx) P[i].y = ny;
    } else {
      const q = groundPoint(e, hd.at.y); if (!q) return;
      const dx = q.x - hd.p0.x, dz = q.z - hd.p0.z;
      if (!hd.axis) { if (Math.max(Math.abs(dx), Math.abs(dz)) < 0.4) return; hd.axis = Math.abs(dx) >= Math.abs(dz) ? "x" : "z"; if (!h.axes.includes(hd.axis)) hd.axis = h.axes[0]; }
      const d = hd.axis === "x" ? dx : dz, nv = snapV(base[h.k][hd.axis] + d, SNAP_P);
      P[h.k][hd.axis] = nv; P[h.k + 1][hd.axis] = nv;
    }
    hd.rec.path = P; S.layout.routes[hd.h.sid] = P.map((v) => [Math.round(v.x * 100) / 100, Math.round(v.y * 100) / 100, Math.round(v.z * 100) / 100]); S.editChanged = true; schedulePipesKeep(hd.h.sid);
  }
  function schedulePipesKeep() { schedulePipes(); }
  function cleanRoute(sid) {                    // drop zero-length and collinear points (keeps the end points)
    const r = S.layout.routes[sid]; if (!r) return;
    const pts = r.map((q) => new T.Vector3(q[0], q[1], q[2])), out = [pts[0]];
    for (let i = 1; i < pts.length; i++) if (pts[i].distanceTo(out[out.length - 1]) > 0.02) out.push(pts[i]);
    const res = [out[0]];
    for (let i = 1; i < out.length - 1; i++) { const a = out[i].clone().sub(out[i - 1]).normalize(), b = out[i + 1].clone().sub(out[i]).normalize(); if (a.dot(b) < 0.999) res.push(out[i]); }
    res.push(out[out.length - 1]);
    S.layout.routes[sid] = res.map((v) => [Math.round(v.x * 100) / 100, Math.round(v.y * 100) / 100, Math.round(v.z * 100) / 100]);
  }
  function autoRouteSel() {
    if (!S.sel || S.sel.kind !== "stream") return; beginEdit(); delete S.layout.routes[S.sel.id]; S.editChanged = true; buildPipes(); refreshHandles(); refreshOutline(); endEdit();
  }
  function editDown(e) {
    const h = hitHandle(e); if (h) { startHandleDrag(h, e); return !!hd; }
    const o = hit(e); if (o && o.kind === "unit") { select("unit", o.id, false); startUnitDrag(o.id, e); return !!ud; }
    return false;
  }
  function editMove(e) { if (ud) moveUnit(e); else if (hd) moveHandle(e); }
  function editUp() {
    if (hd) { cleanRoute(hd.h.sid); buildPipes(); const sid = hd.h.sid; hd = null; endEdit(); if (S.sel && S.sel.kind === "stream" && S.sel.id === sid) refreshHandles(); refreshOutline(); }
    else if (ud) { ud = null; endEdit(); refreshOutline(); }
  }
  window.addEventListener("keydown", (e) => {
    if (!S.edit) return;
    if ((e.ctrlKey || e.metaKey) && (e.key === "z" || e.key === "Z")) { e.preventDefault(); e.shiftKey ? redo() : undo(); }
    else if ((e.ctrlKey || e.metaKey) && (e.key === "y" || e.key === "Y")) { e.preventDefault(); redo(); }
    else if (e.key === "r" || e.key === "R") rotateSel();
    else if (e.key === "Escape") select(null);
  });

  // ------------------------------------------------------------------ picking, hover, info panel
  const ray = new T.Raycaster(), ndc = new T.Vector2();
  function hit(e) {
    const r = canvas.getBoundingClientRect(); ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hs = ray.intersectObjects(S.pickables, false);
    for (const h of hs) { if (h.object.userData.uid) return { kind: "unit", id: h.object.userData.uid, point: h.point }; if (h.object.userData.sid) return { kind: "stream", id: h.object.userData.sid, point: h.point }; }
    return null;
  }
  let boxSel = null, boxHov = null;
  function boxFor(rec, color) { const b = new T.Box3Helper(rec, color); b.material.depthTest = false; b.renderOrder = 30; return b; }
  function outline(kind, id, color) {
    if (kind === "unit" && S.units[id]) return boxFor(S.units[id].box.clone().expandByScalar(0.25), color);
    if (kind === "stream") { const p = S.pipes.find((q) => q.s.id === id); if (p) { const b = new T.Box3().setFromPoints(p.dense).expandByScalar(p.rad + 0.15); return boxFor(b, color); } }
    return null;
  }
  function hoverAt(e) {
    const h = hit(e); const key = h ? h.kind + h.id : "";
    const tip = $("tip");
    S.hoverObj = h ? { kind: h.kind, id: h.id } : null; MAP.dirty = true;
    if (key !== (S.hover || "")) {
      S.hover = key;
      if (boxHov) { scene.remove(boxHov); boxHov = null; }
      if (h && (!S.sel || S.sel.kind + S.sel.id !== key)) { boxHov = outline(h.kind, h.id, new T.Color(0xffffff)); if (boxHov) scene.add(boxHov); }
    }
    if (h) {
      const nm = h.kind === "unit" ? S.units[h.id].u.name + " · " + S.units[h.id].u.title : "Stream " + (S.pipes.find((q) => q.s.id === h.id) || { s: { name: h.id } }).s.name;
      tip.textContent = nm; tip.style.display = "block"; const r = canvas.getBoundingClientRect(); tip.style.left = Math.min(r.width - 220, e.clientX - r.left + 14) + "px"; tip.style.top = (e.clientY - r.top + 16) + "px"; canvas.style.cursor = "pointer";
    } else { tip.style.display = "none"; canvas.style.cursor = "grab"; }
  }
  function esc(t) { return String(t === undefined || t === null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  function phaseLine(el) {
    if (!el.phase) return ""; const ph = (S.scene.meta.phases || []).find((p) => p.id === el.phase);
    const nm = ph ? ph.name : el.phase; return `<span class="tag" style="color:${esc(el.color || (ph && ph.color) || "#ccc")};border-color:${esc(el.color || (ph && ph.color) || "#888")}">${el.change === "remove" ? "Removed in " : "Added in "}${esc(nm)}</span>`;
  }
  function showInfo(kind, id) {
    const box = $("info"), body = $("infoBody");
    if (!kind) { box.style.display = "none"; return; }
    let h = "";
    if (kind === "unit") {
      const u = S.units[id].u;
      h += `<h2>${esc(u.name)}</h2><div class="sub">${esc(u.title)}</div>`;
      h += `<span class="tag ${esc(u.status)}">${u.status === "ok" ? "solved" : esc(u.status)}</span>` + phaseLine(u);
      if (u.error) h += `<p style="color:#ff8a8a">${esc(u.error)}</p>`;
      if (u.what) h += `<h3>What it is</h3><p>${esc(u.what)}</p>`;
      if (u.model) h += `<h3>How it is modelled</h3><p>${esc(u.model)}</p>`;
      if (u.results && u.results.length) h += `<h3>Results</h3><table>${u.results.map((r) => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td></tr>`).join("")}</table>`;
      const conns = S.scene.streams.filter((s) => s.src[0] === id || s.dst[0] === id);
      if (conns.length) h += `<h3>Connected streams</h3><table>${conns.map((s) => `<tr><td>${s.src[0] === id ? "out" : "in"} · ${esc(s.name)}</td><td>${s.solved ? esc(Number(s.T).toFixed(1)) + " °C · " + esc(Number(s.P).toFixed(1)) + " bar" : "—"}</td></tr>`).join("")}</table>`;
    } else {
      const s = S.pipes.find((q) => q.s.id === id).s, from = S.units[s.src[0]].u.name, to = S.units[s.dst[0]].u.name;
      h += `<h2>Stream ${esc(s.name)}</h2><div class="sub">${esc(from)} → ${esc(to)}</div>`;
      h += `<span class="tag">${esc(s.service)}</span>` + phaseLine(s);
      h += `<h3>Properties</h3><table>${(s.rows || []).map((r) => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td></tr>`).join("")}</table>`;
      h += `<p class="sub" style="margin-top:8px">Pipe size and velocity are indicative (typical design velocities), not a line-sizing result.</p>`;
    }
    body.innerHTML = h; box.style.display = "block";
  }
  function select(kind, id, fly) {
    S.sel = kind ? { kind, id } : null;
    if (boxSel) { scene.remove(boxSel); boxSel = null; }
    if (kind) { boxSel = outline(kind, id, new T.Color(0x2dd4c4)); if (boxSel) scene.add(boxSel); }
    showInfo(kind, id); refreshHandles(); MAP.dirty = true;
    if (kind && fly) {
      let c, r;
      if (kind === "unit") { const b = S.units[id].box, v = b.getCenter(new T.Vector3()), sz = b.getSize(new T.Vector3()); c = v; r = Math.max(8, Math.max(sz.x, sz.y, sz.z) * 2.4); }
      else { const p = S.pipes.find((q) => q.s.id === id), b = new T.Box3().setFromPoints(p.dense); c = b.getCenter(new T.Vector3()); r = Math.max(8, b.getSize(new T.Vector3()).length() * 1.1); }
      flyTo(c.x, c.y, c.z, r);
    }
  }
  function pickAt(e, fly) { const h = hit(e); if (h) select(h.kind, h.id, fly); else if (!fly) select(null); }
  $("infoX").onclick = () => select(null);


  // ------------------------------------------------------------------ 2D inset: the diagram next to the 3D view, linked by hover and selection
  const MAP = { on: false, size: 1, hit: [], dirty: true, last: 0, hover: null };
  const MAP_SIZES = [[270, 200], [400, 290], [560, 400]];
  const mapCv = $("map"), mctx = mapCv.getContext("2d");
  function mapColors() { const l = THEMES[themeName] === THEMES.studio && !S.dusk; return l ? { bg: "#f4f7fa", ink: "#33475b", fill: "#ffffff", dim: "#9aa9b8", grid: "#e2e8ee" } : { bg: "#101a24", ink: "#c9d6e2", fill: "#1b2a38", dim: "#6c7f91", grid: "#1a2735" }; }
  function mapResize() { const [w, h] = MAP_SIZES[MAP.size]; mapCv.width = w * 2; mapCv.height = (h - 26) * 2; mapCv.style.width = w + "px"; mapCv.style.height = (h - 26) + "px"; MAP.dirty = true; }
  function glyph(g, t, x, y, r, orient, col, fill) {
    g.lineWidth = 1.6; g.strokeStyle = col; g.fillStyle = fill;
    const rect = (w, h, rr) => { g.beginPath(); g.roundRect(x - w / 2, y - h / 2, w, h, rr); g.fill(); g.stroke(); };
    const circ = (q) => { g.beginPath(); g.arc(x, y, q, 0, Math.PI * 2); g.fill(); g.stroke(); };
    switch (t) {
      case "separator": case "scrubber": case "separator3": case "phase_splitter": case "deliquifier": case "comp_splitter": case "mol_sieve":
        if (orient === "h") rect(r * 2.2, r * 1.2, r * 0.6); else rect(r * 1.2, r * 2.2, r * 0.6); break;
      case "column": case "teg_contactor": case "amine_contactor": rect(r * 1.05, r * 2.6, r * 0.5); g.beginPath(); for (let i = -2; i <= 2; i++) { g.moveTo(x - r * 0.5, y + i * r * 0.45); g.lineTo(x + r * 0.5, y + i * r * 0.45); } g.stroke(); break;
      case "conv_reactor": case "eq_reactor": rect(r * 1.5, r * 2, r * 0.7); g.beginPath(); g.moveTo(x - r * 0.5, y); g.lineTo(x + r * 0.5, y); g.stroke(); break;
      case "compressor": case "subsea_compressor": circ(r); g.beginPath(); g.moveTo(x - r * 0.6, y - r * 0.6); g.lineTo(x + r * 0.7, y - r * 0.25); g.moveTo(x - r * 0.6, y + r * 0.6); g.lineTo(x + r * 0.7, y + r * 0.25); g.stroke(); break;
      case "expander": circ(r); g.beginPath(); g.moveTo(x - r * 0.7, y - r * 0.25); g.lineTo(x + r * 0.6, y - r * 0.6); g.moveTo(x - r * 0.7, y + r * 0.25); g.lineTo(x + r * 0.6, y + r * 0.6); g.stroke(); break;
      case "pump": case "subsea_pump": case "subsea_booster": circ(r); g.beginPath(); g.moveTo(x - r * 0.3, y - r * 0.7); g.lineTo(x + r * 0.8, y - r * 0.7); g.lineTo(x + r * 0.8, y); g.stroke(); break;
      case "gas_turbine": circ(r); g.fillStyle = col; g.font = `700 ${Math.round(r)}px Inter,Arial`; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText("GT", x, y + 1); break;
      case "heater": case "cooler": case "chiller": case "hx": case "subsea_cooler": case "subsea_heater": circ(r); g.beginPath(); g.moveTo(x - r, y); g.lineTo(x - r * 0.5, y); g.lineTo(x - r * 0.25, y - r * 0.45); g.lineTo(x + r * 0.25, y + r * 0.45); g.lineTo(x + r * 0.5, y); g.lineTo(x + r, y); g.stroke(); break;
      case "aircooler": rect(r * 2.4, r * 1.3, 2); circ(r * 0.45); break;
      case "valve": case "relief_valve": case "subsea_valve": case "subsea_choke": g.beginPath(); g.moveTo(x - r, y - r * 0.6); g.lineTo(x + r, y + r * 0.6); g.lineTo(x + r, y - r * 0.6); g.lineTo(x - r, y + r * 0.6); g.closePath(); g.fill(); g.stroke(); break;
      case "mixer": case "splitter": circ(r * 0.55); g.beginPath(); g.arc(x, y, 1.6, 0, 7); g.fillStyle = col; g.fill(); break;
      case "feed": g.beginPath(); g.moveTo(x - r, y - r * 0.7); g.lineTo(x + r * 0.4, y - r * 0.7); g.lineTo(x + r, y); g.lineTo(x + r * 0.4, y + r * 0.7); g.lineTo(x - r, y + r * 0.7); g.closePath(); g.fill(); g.stroke(); break;
      case "product": g.beginPath(); g.moveTo(x + r, y - r * 0.7); g.lineTo(x - r * 0.4, y - r * 0.7); g.lineTo(x - r, y); g.lineTo(x - r * 0.4, y + r * 0.7); g.lineTo(x + r, y + r * 0.7); g.closePath(); g.fill(); g.stroke(); break;
      case "flare": g.beginPath(); g.moveTo(x, y - r); g.lineTo(x + r * 0.7, y + r * 0.8); g.lineTo(x - r * 0.7, y + r * 0.8); g.closePath(); g.fill(); g.stroke(); break;
      case "well": case "xmas_tree": case "injection_well": circ(r * 0.6); g.beginPath(); g.moveTo(x - r, y); g.lineTo(x + r, y); g.moveTo(x, y - r); g.lineTo(x, y + r); g.stroke(); break;
      default: rect(r * 1.8, r * 1.3, 3); g.fillStyle = col; g.font = `600 ${Math.round(r * 0.8)}px Inter,Arial`; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText((t || "?").slice(0, 2).toUpperCase(), x, y + 1);
    }
  }
  function drawMap(now) {
    const C = mapColors(), W = mapCv.width, H = mapCv.height, g = mctx;
    g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, W, H); g.fillStyle = C.bg; g.fillRect(0, 0, W, H);
    const us = S.recs; MAP.hit = [];
    if (!us.length) return;
    let x0 = 1e9, x1 = -1e9, z0 = 1e9, z1 = -1e9;
    for (const R of us) { x0 = Math.min(x0, R.u.ox); x1 = Math.max(x1, R.u.ox); z0 = Math.min(z0, R.u.oz); z1 = Math.max(z1, R.u.oz); }
    const pad = 34 * 2, bw = Math.max(1, x1 - x0), bh = Math.max(1, z1 - z0), k = Math.min((W - 2 * pad) / bw, (H - 2 * pad) / bh);
    const ox = (W - bw * k) / 2 - x0 * k, oy = (H - bh * k) / 2 - z0 * k, P = (R) => [R.u.ox * k + ox, R.u.oz * k + oy];
    const r = Math.max(9, Math.min(18, 12 + MAP.size * 3)) * 2 * 0.62;
    g.strokeStyle = C.grid; g.lineWidth = 1; for (let i = 0; i < W; i += 40) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, H); g.stroke(); } for (let j = 0; j < H; j += 40) { g.beginPath(); g.moveTo(0, j); g.lineTo(W, j); g.stroke(); }
    const hov = S.hoverObj, sel = S.sel, same = (o, kind, id) => o && o.kind === kind && o.id === id;
    // streams
    const ph = (now || 0) / 60;
    for (const p of S.pipes) {
      const A = S.units[p.s.src[0]], B = S.units[p.s.dst[0]]; if (!A || !B) continue;
      const [ax, ay] = P(A), [bx, by] = P(B), mx = (ax + bx) / 2, pts = [[ax, ay], [mx, ay], [mx, by], [bx, by]];
      const on = same(hov, "stream", p.s.id) || same(MAP.hover, "stream", p.s.id), se = same(sel, "stream", p.s.id);
      g.lineWidth = on || se ? 5 : 2.6; g.strokeStyle = on || se ? "#2dd4c4" : (p.flowing ? "#" + (SERVICE[p.s.service] || 0xaaaaaa).toString(16).padStart(6, "0") : C.dim);
      g.setLineDash(p.flowing ? [10, 8] : []); g.lineDashOffset = -ph; g.beginPath(); g.moveTo(pts[0][0], pts[0][1]); for (let i = 1; i < 4; i++) g.lineTo(pts[i][0], pts[i][1]); g.stroke(); g.setLineDash([]);
      MAP.hit.push({ kind: "stream", id: p.s.id, pts });
    }
    // equipment
    for (const R of us) {
      const [x, y] = P(R), id = R.u.id, on = same(hov, "unit", id) || same(MAP.hover, "unit", id), se = same(sel, "unit", id);
      const st = R.u.status, edge = st === "error" || st === "missing" || st === "unsolved" ? "#e5484d" : st === "warning" ? "#f5a524" : C.ink;
      if (on || se) { g.beginPath(); g.arc(x, y, r * 1.9, 0, 7); g.fillStyle = se ? "rgba(45,212,196,.28)" : "rgba(45,212,196,.18)"; g.fill(); }
      glyph(g, R.u.type, x, y, r, R.u.orient, on || se ? "#0fa89a" : edge, C.fill);
      if (R.u.color) { g.strokeStyle = R.u.color; g.lineWidth = 3; g.beginPath(); g.arc(x, y, r * 1.45, 0, 7); g.stroke(); }
      g.fillStyle = on || se ? "#0fa89a" : C.ink; g.font = `${on || se ? 700 : 500} ${MAP.size ? 20 : 18}px Inter,Arial`; g.textAlign = "center"; g.textBaseline = "top";
      g.fillText(R.u.name.length > 16 ? R.u.name.slice(0, 15) + "…" : R.u.name, x, y + r * 1.25);
      MAP.hit.push({ kind: "unit", id, x, y, r: r * 1.5 });
    }
    const hd = $("insetTitle"), cur = hov || MAP.hover || sel;
    hd.textContent = cur ? (cur.kind === "unit" ? (S.units[cur.id] ? S.units[cur.id].u.name : "") : "Stream " + ((S.pipes.find((q) => q.s.id === cur.id) || { s: { name: "" } }).s.name)) : "2D diagram";
  }
  function mapPick(e) {
    const r = mapCv.getBoundingClientRect(), x = (e.clientX - r.left) * mapCv.width / r.width, y = (e.clientY - r.top) * mapCv.height / r.height;
    let best = null, bd = 1e9;
    for (const h of MAP.hit) if (h.kind === "unit") { const d = Math.hypot(x - h.x, y - h.y); if (d < h.r && d < bd) { bd = d; best = h; } }
    if (best) return { kind: "unit", id: best.id };
    for (const h of MAP.hit) if (h.kind === "stream") for (let i = 0; i < 3; i++) {
      const [ax, ay] = h.pts[i], [bx, by] = h.pts[i + 1], dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy || 1, t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / L2)), d = Math.hypot(x - (ax + t * dx), y - (ay + t * dy));
      if (d < 9 && d < bd) { bd = d; best = h; }
    }
    return best ? { kind: "stream", id: best.id } : null;
  }
  function setBoxHover(o) { if (boxHov) { scene.remove(boxHov); boxHov = null; } if (o && !(S.sel && S.sel.kind === o.kind && S.sel.id === o.id)) { boxHov = outline(o.kind, o.id, new T.Color(0xffffff)); if (boxHov) scene.add(boxHov); } }
  mapCv.addEventListener("pointermove", (e) => { const o = mapPick(e); const k = o ? o.kind + o.id : ""; if (k !== (MAP.hover ? MAP.hover.kind + MAP.hover.id : "")) { MAP.hover = o; setBoxHover(o); MAP.dirty = true; } mapCv.style.cursor = o ? "pointer" : "default"; });
  mapCv.addEventListener("pointerleave", () => { MAP.hover = null; setBoxHover(null); MAP.dirty = true; });
  mapCv.addEventListener("click", (e) => { const o = mapPick(e); if (o) select(o.kind, o.id, false); else select(null); MAP.dirty = true; });
  mapCv.addEventListener("dblclick", (e) => { const o = mapPick(e); if (o) select(o.kind, o.id, true); });
  function applyInset() { $("inset").style.display = MAP.on ? "block" : "none"; $("bInset").classList.toggle("on", MAP.on); if (MAP.on) mapResize(); }
  $("bInset").onclick = () => { MAP.on = !MAP.on; applyInset(); };
  $("insetX").onclick = () => { MAP.on = false; applyInset(); };
  $("insetSize").onclick = () => { MAP.size = (MAP.size + 1) % MAP_SIZES.length; mapResize(); };


  // ------------------------------------------------------------------ review tools: clash check, measure, section, saved views, bill of quantities
  const CLR = { equip: 1.5, pipe: 0.15 };
  let clashGroup = null;
  function segDist(p1, q1, p2, q2) {            // closest distance between two segments (Ericson) + the point on the first
    const d1 = q1.clone().sub(p1), d2 = q2.clone().sub(p2), r = p1.clone().sub(p2), a = d1.dot(d1), e = d2.dot(d2), f = d2.dot(r);
    let s_, t_;
    if (a <= 1e-9 && e <= 1e-9) { s_ = t_ = 0; }
    else if (a <= 1e-9) { s_ = 0; t_ = Math.max(0, Math.min(1, f / e)); }
    else {
      const c = d1.dot(r);
      if (e <= 1e-9) { t_ = 0; s_ = Math.max(0, Math.min(1, -c / a)); }
      else {
        const b = d1.dot(d2), den = a * e - b * b;
        s_ = den > 1e-9 ? Math.max(0, Math.min(1, (b * f - c * e) / den)) : 0;
        t_ = (b * s_ + f) / e;
        if (t_ < 0) { t_ = 0; s_ = Math.max(0, Math.min(1, -c / a)); } else if (t_ > 1) { t_ = 1; s_ = Math.max(0, Math.min(1, (b - c) / a)); }
      }
    }
    const c1 = p1.clone().addScaledVector(d1, s_), c2 = p2.clone().addScaledVector(d2, t_);
    return { d: c1.distanceTo(c2), at: c1.add(c2).multiplyScalar(0.5) };
  }
  function runClash() {
    const out = [], nearOwn = (p, pt) => [p.s.src[0], p.s.dst[0]].some((id) => S.units[id] && S.units[id].box.clone().expandByScalar(2.0).containsPoint(pt));
    // pipe vs equipment (bounding boxes, slightly shrunk)
    for (const p of S.pipes) {
      const P = p.path, seen = new Set();
      for (let k = 0; k < P.length - 1; k++) {
        const leg = k === 0 || k === P.length - 2, bs = new T.Box3().setFromPoints([P[k], P[k + 1]]).expandByScalar(p.rad);
        for (const R of S.recs) {
          if (leg && (R.u.id === p.s.src[0] || R.u.id === p.s.dst[0])) continue;
          if (seen.has(R.u.id)) continue;
          const bx = R.box.clone().expandByScalar(-0.2);
          if (bx.intersectsBox(bs)) { seen.add(R.u.id); const at = bs.clone().intersect(bx).getCenter(new T.Vector3()); out.push({ kind: "pipe-equipment", a: p.s.id, b: R.u.id, at, text: `Pipe ${p.s.name} passes through ${R.u.name}` }); }
        }
      }
    }
    // pipe vs pipe (clearance between pipe walls)
    for (let i = 0; i < S.pipes.length; i++) for (let j = i + 1; j < S.pipes.length; j++) {
      const A = S.pipes[i], B = S.pipes[j], lim = A.rad + B.rad + CLR.pipe; let hit_ = null;
      for (let ka = 0; ka < A.path.length - 1 && !hit_; ka++) for (let kb = 0; kb < B.path.length - 1; kb++) {
        const r = segDist(A.path[ka], A.path[ka + 1], B.path[kb], B.path[kb + 1]);
        if (r.d < lim && !(nearOwn(A, r.at) && nearOwn(B, r.at) && (A.s.src[0] === B.s.src[0] || A.s.dst[0] === B.s.dst[0] || A.s.src[0] === B.s.dst[0] || A.s.dst[0] === B.s.src[0]))) { hit_ = r; break; }
      }
      if (hit_) out.push({ kind: "pipe-pipe", a: A.s.id, b: B.s.id, at: hit_.at, text: `Pipes ${A.s.name} and ${B.s.name} are closer than ${(CLR.pipe * 1000).toFixed(0)} mm (wall to wall)` });
    }
    // equipment spacing
    for (let i = 0; i < S.recs.length; i++) for (let j = i + 1; j < S.recs.length; j++) {
      const A = S.recs[i], B = S.recs[j];
      const gx = Math.max(0, Math.max(A.box.min.x - B.box.max.x, B.box.min.x - A.box.max.x)), gz = Math.max(0, Math.max(A.box.min.z - B.box.max.z, B.box.min.z - A.box.max.z)), g = Math.hypot(gx, gz);
      if (g < CLR.equip) out.push({ kind: "equipment-equipment", a: A.u.id, b: B.u.id, at: A.box.getCenter(new T.Vector3()).add(B.box.getCenter(new T.Vector3())).multiplyScalar(0.5), text: `${A.u.name} and ${B.u.name}: ${g.toFixed(1)} m apart (limit ${CLR.equip.toFixed(1)} m)` });
    }
    S.clashes = out;
    const b = $("bClash"); if (b) { b.textContent = out.length ? `⚠ Clashes (${out.length})` : "✓ No clashes"; b.classList.toggle("warn", out.length > 0); }
    drawClash();
  }
  function drawClash() {
    if (clashGroup) { world.remove(clashGroup); disposeTree(clashGroup); clashGroup = null; }
    const pnl = $("clash"); if (pnl) pnl.style.display = S.clash ? "block" : "none";
    if (!S.clash) return;
    clashGroup = new T.Group(); world.add(clashGroup);
    S.clashes.forEach((c) => {
      const m = new T.Mesh(new T.SphereGeometry(0.55, 16, 12), new T.MeshBasicMaterial({ color: c.kind === "equipment-equipment" ? 0xf5a524 : 0xff3b3b, transparent: true, opacity: 0.55, depthTest: false }));
      m.position.copy(c.at); m.renderOrder = 35; clashGroup.add(m);
    });
    $("clashList").innerHTML = S.clashes.length ? S.clashes.map((c, i) => `<div class="row" data-i="${i}"><i style="background:${c.kind === "equipment-equipment" ? "#f5a524" : "#ff3b3b"}"></i>${esc(c.text)}</div>`).join("") : "<p class=\"sub\">No pipe or equipment clashes found.</p>";
    $("clashList").querySelectorAll(".row").forEach((el) => el.addEventListener("click", () => { const c = S.clashes[+el.dataset.i]; flyTo(c.at.x, c.at.y, c.at.z, 11); }));
    const cl = $("clashClr"); if (cl) cl.value = CLR.equip;
  }
  function toggleClash(v) { S.clash = v === undefined ? !S.clash : v; $("bClash").classList.toggle("on", S.clash); drawClash(); }

  // measure
  let measGroup = null;
  function clearMeasure() { S.meas = []; if (measGroup) { scene.remove(measGroup); disposeTree(measGroup); measGroup = null; } $("measTxt").textContent = S.measure ? "click two points" : ""; }
  function measureClick(e) {
    const h = hit(e); let pt = h ? h.point.clone() : groundPoint(e, 0); if (!pt) return;
    if (S.meas.length >= 2) clearMeasure();
    S.meas.push(pt);
    if (measGroup) { scene.remove(measGroup); disposeTree(measGroup); }
    measGroup = new T.Group(); scene.add(measGroup);
    for (const q of S.meas) { const m = new T.Mesh(new T.SphereGeometry(0.22, 12, 8), new T.MeshBasicMaterial({ color: 0xff2d95, depthTest: false })); m.position.copy(q); m.renderOrder = 40; measGroup.add(m); }
    if (S.meas.length === 2) {
      const [a, b] = S.meas, d = a.distanceTo(b);
      const ln = new T.Line(new T.BufferGeometry().setFromPoints([a, b]), new T.LineBasicMaterial({ color: 0xff2d95, depthTest: false })); ln.renderOrder = 40; measGroup.add(ln);
      const lb = labelSprite(d.toFixed(2) + " m", "", "#ff2d95"); lb.position.copy(a).add(b).multiplyScalar(0.5); lb.position.y += 0.8; lb.renderOrder = 41; lb.userData.k = 1.1; measGroup.add(lb); S.labels.push(lb);
      $("measTxt").textContent = `${d.toFixed(2)} m  (Δx ${(b.x - a.x).toFixed(1)} · Δz ${(b.z - a.z).toFixed(1)} · Δy ${(b.y - a.y).toFixed(1)})`;
    } else $("measTxt").textContent = "click the second point";
  }
  function setMeasure(v) { S.measure = v; $("bMeasure").classList.toggle("on", v); canvas.style.cursor = v ? "crosshair" : "grab"; clearMeasure(); }

  // section plane
  const secPlane = new T.Plane(new T.Vector3(0, -1, 0), 100);
  function applySection() {
    $("secTools").style.display = S.secOn ? "inline-flex" : "none"; $("bSec").classList.toggle("on", S.secOn);
    renderer.clippingPlanes = S.secOn ? [secPlane] : [];
    if (!S.secOn) { shadowsDirty(2); return; }
    const ax = $("secAxis").value, bb = S.bounds, f = +$("secPos").value / 100;
    const lo = bb.min[ax], hi = bb.max[ax] + (ax === "y" ? 2 : 0.5), v = lo + (hi - lo) * f;
    secPlane.normal.set(ax === "x" ? -1 : 0, ax === "y" ? -1 : 0, ax === "z" ? -1 : 0); secPlane.constant = v;
    shadowsDirty(2);
  }

  // saved views (kept in the stored layout)
  function refreshViews() {
    const sel = $("selViews"), vs = (S.layout && S.layout.views) || [];
    sel.innerHTML = `<option value="">Views…</option>` + vs.map((v, i) => `<option value="${i}">${esc(v.name)}</option>`).join("");
    $("bViewDel").style.display = S.curView !== undefined && vs[S.curView] ? "inline-block" : "none";
  }
  function saveView() {
    const vs = (S.layout.views = S.layout.views || []);
    if (vs.length >= 12) vs.shift();
    vs.push({ name: "View " + (S.viewCount = (S.viewCount || vs.length) + 1), t: [cam.target.x, cam.target.y, cam.target.z].map((q) => Math.round(q * 100) / 100), r: Math.round(cam.radius * 100) / 100, th: Math.round(cam.theta * 1000) / 1000, ph: Math.round(cam.phi * 1000) / 1000 });
    S.curView = vs.length - 1; refreshViews(); postLayout();
  }
  function gotoView(i) { const v = ((S.layout.views || [])[i]); if (!v) return; S.curView = i; flyTo(v.t[0], v.t[1], v.t[2], v.r, v.th, v.ph); refreshViews(); $("selViews").value = String(i); }
  function delView() { const vs = S.layout.views || []; if (S.curView === undefined || !vs[S.curView]) return; vs.splice(S.curView, 1); S.curView = undefined; refreshViews(); postLayout(); }

  // bill of quantities
  function boqData() {
    const eq = {}, pipes = {}; let steel = 0, bends = 0, len = 0;
    for (const R of S.recs) { const t = R.u.title || R.u.type; eq[t] = (eq[t] || 0) + 1; }
    for (const p of S.pipes) {
      const nps = p.s.nps || 6, di = p.s.d || 0.15, t = Math.max(0.0045, di * 0.025), Do = di + 2 * t; let L = 0;
      for (let i = 1; i < p.path.length; i++) L += p.path[i].distanceTo(p.path[i - 1]);
      bends += Math.max(0, p.path.length - 2); len += L;
      const m = 7850 * Math.PI / 4 * (Do * Do - di * di) * L; steel += m;
      const e = pipes[nps] || (pipes[nps] = { nps, n: 0, len: 0, mass: 0 }); e.n++; e.len += L; e.mass += m;
    }
    return { eq, pipes: Object.values(pipes).sort((a, b) => a.nps - b.nps), len, steel, bends, valves: S.stats.valves, posts: S.stats.posts, flanges: S.pipes.length * 2 + S.stats.valves * 2 };
  }
  function showBoq(v) {
    const el = $("boq"); S.boqOn = v === undefined ? !S.boqOn : v; $("bBoq").classList.toggle("on", S.boqOn); el.style.display = S.boqOn ? "block" : "none"; if (!S.boqOn) return;
    const d = boqData(), n0 = (x) => x.toFixed(0), n1 = (x) => x.toFixed(1);
    el.querySelector("#boqBody").innerHTML = `<h3>Equipment</h3><table>${Object.entries(d.eq).map(([k, v_]) => `<tr><td>${esc(k)}</td><td>${v_}</td></tr>`).join("")}</table>` +
      `<h3>Piping (indicative)</h3><table><tr><td><b>NPS</b></td><td><b>runs · length · steel</b></td></tr>${d.pipes.map((p) => `<tr><td>${p.nps} in</td><td>${p.n} · ${n1(p.len)} m · ${n0(p.mass)} kg</td></tr>`).join("")}<tr><td>Total</td><td>${n1(d.len)} m · ${n0(d.steel)} kg</td></tr></table>` +
      `<h3>Fittings</h3><table><tr><td>Bends</td><td>${d.bends}</td></tr><tr><td>Block valves</td><td>${d.valves}</td></tr><tr><td>Flange pairs</td><td>${d.flanges}</td></tr><tr><td>Pipe supports</td><td>${d.posts}</td></tr></table>` +
      `<p class="sub">Lengths follow the 3D routes; wall thickness is 2.5 % of the bore (min 4.5 mm). Not a take-off for ordering.</p>`;
  }
  function boqCsv() {
    const d = boqData(), rows = [["section", "item", "count", "length_m", "steel_kg"]];
    for (const [k, v] of Object.entries(d.eq)) rows.push(["equipment", k, v, "", ""]);
    for (const p of d.pipes) rows.push(["piping", `NPS ${p.nps}`, p.n, p.len.toFixed(1), p.mass.toFixed(0)]);
    rows.push(["fittings", "bends", d.bends, "", ""], ["fittings", "block valves", d.valves, "", ""], ["fittings", "flange pairs", d.flanges, "", ""], ["fittings", "supports", d.posts, "", ""]);
    const a = document.createElement("a"); a.download = "boq_3d.csv"; a.href = URL.createObjectURL(new Blob([rows.map((r) => r.join(",")).join("\n")], { type: "text/csv" })); a.click();
  }

  // ------------------------------------------------------------------ animation loop
  let tPrev = performance.now(), clock = 0;
  let ema = 0, nFrames = 0, lowQ = false;
  function lowQuality() {                       // slow GPU / software rendering: drop shadows and the extra resolution
    if (lowQ) return; lowQ = true;
    renderer.shadowMap.enabled = false; sun.castShadow = false; renderer.setPixelRatio(1); resize();
    world.traverse((o) => { if (o.material) o.material.needsUpdate = true; });
  }
  function frame(now) {
    const raw = (now - tPrev) / 1000; nFrames++;
    if (nFrames > 4) { ema = ema ? ema * 0.8 + raw * 0.2 : raw; if (nFrames >= 8 && ema > 0.12) lowQuality(); }
    const dt = Math.min(0.05, raw); tPrev = now; clock += dt;
    if (cam.fly) {
      const k = Math.min(1, (now - cam.fly.t0) / cam.fly.dur), e = ease(k), f = cam.fly.from, t = cam.fly.to;
      cam.target.set(lerp(f.x, t.x, e), lerp(f.y, t.y, e), lerp(f.z, t.z, e)); cam.radius = lerp(f.r, t.r, e); cam.theta = lerp(f.th, t.th, e); cam.phi = lerp(f.ph, t.ph, e);
      if (k >= 1) cam.fly = null;
    }
    if (S.rot && !drag) cam.theta += dt * 0.12;
    placeCamera();
    for (const s of S.spin) if (s.on) s.o.rotation[s.axis] += s.rate * dt * (S.flow ? 1 : 0.15);
    for (const id in S.units) {
      const r = S.units[id];
      if (r.flame) { const fl = 0.85 + 0.25 * Math.sin(clock * 11 + 1.3) + 0.12 * Math.sin(clock * 23); r.flame.scale.set((1 + 0.08 * Math.sin(clock * 17)) * 2.1, fl * 2.1, (1 + 0.08 * Math.cos(clock * 13)) * 2.1); r.flame.rotation.y += dt * 2; if (r.flameLight) r.flameLight.intensity = (S.dusk ? 900 : 180) * fl; }
      if (r.plume) r.plume.list.forEach((s, i) => { const ph = (clock * 0.35 + i / r.plume.list.length) % 1; s.position.set(r.plume.x + ph * 1.6, r.plume.y + ph * 6, 0); s.scale.setScalar(0.6 + ph * 2.4); s.material.opacity = 0.3 * (1 - ph); });
      if (r.level) r.level.forEach((lv, i) => { lv.o.scale.y = (lv.along ? 0.55 : 0.45) * (1 + 0.04 * Math.sin(clock * 2.4 + i)); });
    }
    for (const l of S.labels) { const d = l.getWorldPosition(tmpV2).distanceTo(camera.position), k = (0.07 + 0.03 * Math.max(0, Math.min(1, (70 - d) / 55))) * (l.userData.k || 1); l.scale.set(k, k / 4, 1); }
    for (const s of S.sprites) if (s.kind === "beacon") { const k = 0.7 + 0.5 * Math.sin(clock * 5); s.o.scale.setScalar(k); }
    updateFlow(clock);
    if (boxSel) boxSel.material.opacity = 0.7 + 0.3 * Math.sin(clock * 4);
    if (shadowFrames > 0) { renderer.shadowMap.needsUpdate = true; shadowFrames--; }
    renderer.render(scene, camera);
    if (MAP.on && (MAP.dirty || now - MAP.last > 90)) { MAP.last = now; MAP.dirty = false; drawMap(now); }
    if (pendingHover) { const e = pendingHover; pendingHover = null; hoverAt(e); }
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------ UI wiring
  function resize() {
    const w = canvas.clientWidth || 800, h = canvas.clientHeight || 600;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  document.querySelectorAll("#bar button[data-v]").forEach((b) => b.addEventListener("click", () => view(b.dataset.v)));
  $("bFlow").onclick = () => { S.flow = !S.flow; applyToggles(); };
  function setTheme(n) { if (!THEMES[n]) return; themeName = n; applyPalette(); if (S.scene) { const sel = S.sel; build(S.scene); legend(S.scene); if (sel && ((sel.kind === "unit" && S.units[sel.id]) || (sel.kind === "stream" && S.pipes.find((q) => q.s.id === sel.id)))) select(sel.kind, sel.id, false); } else applyToggles(); }
  $("selTheme").onchange = (e) => setTheme(e.target.value);
  $("bBeads").onclick = () => { S.beads = !S.beads; applyToggles(); };
  $("bLabels").onclick = () => { S.labelsOn = !S.labelsOn; applyToggles(); };
  $("bXray").onclick = () => { S.xray = !S.xray; applyToggles(); };
  $("bNight").onclick = () => { S.dusk = !S.dusk; applyToggles(); };
  $("bRot").onclick = () => { S.rot = !S.rot; applyToggles(); };
  $("bEdit").onclick = () => { S.edit = !S.edit; if (!S.edit) clearHandles(); else refreshHandles(); applyToggles(); updateUndoButtons(); };
  $("bSnap").onclick = () => { S.snap = !S.snap; applyToggles(); };
  $("bRotU").onclick = rotateSel; $("bUndo").onclick = undo; $("bRedo").onclick = redo; $("bAuto").onclick = autoRouteSel; $("bResetL").onclick = resetLayout;
  $("selColor").onchange = (e) => { S.colorBy = e.target.value; if (S.scene) { const sel = S.sel; build(S.scene); legend(S.scene); if (sel && ((sel.kind === "unit" && S.units[sel.id]) || (sel.kind === "stream" && S.pipes.find((q) => q.s.id === sel.id)))) select(sel.kind, sel.id, false); } };
  $("bPTags").onclick = () => { S.pipeTags = !S.pipeTags; applyToggles(); };
  $("bClash").onclick = () => toggleClash(); $("clashX").onclick = () => toggleClash(false);
  $("bMeasure").onclick = () => setMeasure(!S.measure);
  $("bSec").onclick = () => { S.secOn = !S.secOn; applySection(); };
  $("secAxis").onchange = applySection; $("secPos").oninput = applySection;
  $("bViewSave").onclick = saveView; $("bViewDel").onclick = delView;
  $("selViews").onchange = (e) => { if (e.target.value !== "") gotoView(+e.target.value); };
  $("bBoq").onclick = () => showBoq(); $("boqX").onclick = () => showBoq(false); $("boqCsv").onclick = boqCsv;
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.measure) { if (S.meas.length) clearMeasure(); else setMeasure(false); } });
  $("bShot").onclick = () => { renderer.render(scene, camera); const a = document.createElement("a"); a.download = "plant_3d.png"; a.href = canvas.toDataURL("image/png"); a.click(); };

  function legend(sc) {
    const sv = [["gas", "Gas"], ["liquid", "Oil / liquid"], ["water", "Water"], ["two-phase", "Two-phase"]];
    let h = "";
    if (S.colorBy === "none") h = sv.map(([k, n]) => `<span><i style="background:#${SERVICE[k].toString(16).padStart(6, "0")}"></i>${n}</span>`).join("");
    else {
      const rg = S.colorBy === "util" ? { lo: 0, hi: 120 } : (S.range || metricRange(S.colorBy)), stops = RAMP.map((r) => "#" + r[1].toString(16).padStart(6, "0")).join(",");
      const f = (v) => (S.colorBy === "velocity" ? v.toFixed(2) : Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));
      h = `<span><b>${esc(COLOR_MODES[S.colorBy])}</b></span><span style="display:flex;align-items:center;gap:6px">${f(rg.lo)}<i style="width:120px;height:8px;background:linear-gradient(90deg,${stops})"></i>${f(rg.hi)}</span>` +
          (S.colorBy === "util" ? "<span>grey = no capacity check</span>" : S.colorBy === "velocity" ? "<span>limits: gas 20, liquid / water 3, two-phase 12 m/s (indicative)</span>" : "");
    }
    for (const p of sc.meta.phases || []) h += `<span><i style="background:${esc(p.color || "#e8590c")}"></i>${esc(p.name)}</span>`;
    $("legend").innerHTML = h;
  }

  // ------------------------------------------------------------------ Streamlit protocol
  function post(type, data) { window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type }, data || {}), "*"); }
  function canonLayout(L) {                      // layout without the 2D stamps Python adds
    const u = {}; for (const k in (L.units || {})) u[k] = { x: L.units[k].x, z: L.units[k].z, rot: L.units[k].rot | 0 };
    return JSON.stringify({ units: u, routes: L.routes || {} });
  }
  function setScene(sc, ports) {
    S.ports = ports || S.ports;
    const lay = sc.layout || { units: {}, routes: {} }, layJ = canonLayout(lay), lv = (sc.meta && sc.meta.layoutVer) || 0;
    let force = false;
    if (S.layoutVer !== lv) { S.layoutVer = lv; S.layout = JSON.parse(JSON.stringify(lay)); S.hist = []; S.fut = []; S.pyLayout = layJ; force = true; }
    else if (layJ !== S.pyLayout) {            // the Python side changed the layout: either the echo of our own edit or something else (another case, a reset)
      S.pyLayout = layJ;
      if (layJ !== canonLayout(S.layout) && !S.pendingEcho) { S.layout = JSON.parse(JSON.stringify(lay)); S.hist = []; S.fut = []; force = true; }
    }
    S.pendingEcho = false;
    const sg = sigOf(Object.assign({}, sc, { layout: null }));
    S.scene = sc;
    for (const u of sc.units || []) { u.ox = u.x; u.oz = u.z; }        // every fresh scene object needs its drawing positions (theme, undo and reset rebuild from S.scene)
    $("stageName").innerHTML = `Stage: <b>${esc((sc.meta && sc.meta.stageName) || "")}</b>${sc.meta && !sc.meta.solved ? " · not solved" : ""}`;
    if (sg === S.sig && !force) return;
    const first = !S.sig; S.sig = sg;
    const prevSel = S.sel;
    build(sc); legend(sc);
    if (first) { const c = S.centre, R = S.size; cam.target.set(c.x, R * 0.05, c.z); cam.radius = Math.max(26, R * 0.72); cam.theta = 0.85; cam.phi = 1.0; }
    if (prevSel && ((prevSel.kind === "unit" && S.units[prevSel.id]) || (prevSel.kind === "stream" && S.pipes.find((q) => q.s.id === prevSel.id)))) select(prevSel.kind, prevSel.id, false); else select(null);
    if ((sc.units || []).length === 0) { $("msg").style.display = "flex"; $("msg").textContent = "Nothing to show yet: build a flowsheet first."; } else $("msg").style.display = "none";
  }
  window.addEventListener("message", (e) => {
    const d = e.data; if (!d || d.type !== "streamlit:render") return;
    const a = d.args || {};
    if (a.height) { post("streamlit:setFrameHeight", { height: a.height }); document.getElementById("wrap").style.height = a.height + "px"; }
    resize();
    if (a.scene) setScene(a.scene, a.ports);
  });
  // test hooks
  window.__p3d = {
    review: { clashes: () => S.clashes.map((c) => ({ kind: c.kind, a: c.a, b: c.b, text: c.text })), toggleClash, measure: (a, b) => { setMeasure(true); S.meas = [new T.Vector3(...a), new T.Vector3(...b)]; const d = S.meas[0].distanceTo(S.meas[1]); return d; }, setMeasure, section: (on, axis, pos) => { S.secOn = on; if (axis) $("secAxis").value = axis; if (pos !== undefined) $("secPos").value = pos; applySection(); }, clipping: () => renderer.clippingPlanes.length, boq: boqData, boqCsv: () => 0, saveView, gotoView, delView, views: () => (S.layout.views || []).slice(), stats: () => S.stats, tags: () => S.labels.filter((l) => l.userData.pipeTag).length, tagsVisible: () => S.labels.filter((l) => l.userData.pipeTag && l.visible).length, setColor: (m) => { $("selColor").value = m; $("selColor").dispatchEvent(new Event("change")); }, pipeColor: (id) => S.pipes.find((q) => q.s.id === id).mat.color.getHex(), unitUtil: (id) => S.units[id].u.util }, state: S, select: (k, id, fly) => select(k, id, !!fly), view, setScene, counts: () => ({ units: Object.keys(S.units).length, pipes: S.pipes.length, particles: flowCount, pickables: S.pickables.length }),
                   info: () => $("infoBody").innerText, fps: () => ({ ema, lowQ }), setFlow: (v) => { S.flow = v; applyToggles(); }, renderer, cam, scene, camera, setTheme, theme: () => themeName, inset: { toggle: (v) => { MAP.on = v; applyInset(); MAP.dirty = true; }, hits: () => MAP.hit.map((h) => Object.assign({}, h)), draw: () => drawMap(0), hover: () => MAP.hover, mapRect: () => { const r = mapCv.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height, cw: mapCv.width, ch: mapCv.height }; } }, screenOf: (id, dy) => { const R = S.units[id], c = R.box.getCenter(new T.Vector3()); c.y = R.box.min.y + (dy === undefined ? 0.8 : dy); c.project(camera); const r = canvas.getBoundingClientRect(); return { x: r.left + (c.x + 1) / 2 * r.width, y: r.top + (1 - c.y) / 2 * r.height }; }, screenOfPoint: (x, y, z) => { const c = new T.Vector3(x, y, z).project(camera), r = canvas.getBoundingClientRect(); return { x: r.left + (c.x + 1) / 2 * r.width, y: r.top + (1 - c.y) / 2 * r.height }; }, edit: { snapshot: snapshotLayout, undo, redo, rotate: rotateSel, moveUnit: (id, x, z) => { const R = S.units[id]; beginEdit(); R.u.x = x; R.u.z = z; S.touched.add(id); invalidateRoutes(id); placeUnit(R); S.editChanged = true; buildPipes(); endEdit(); }, layout: () => S.layout, setEdit: (v) => { S.edit = v; if (!v) clearHandles(); else refreshHandles(); applyToggles(); }, handles: () => S.handles.map((h) => h.userData.handle), dragHandle: (i, kind, val) => { const h = S.handles[i].userData.handle; const rec = S.pipes.find((q) => q.s.id === h.sid); beginEdit(); const P = rec.path.map((v) => v.clone()); if (h.kind === 'elev') h.idx.forEach((k) => { P[k].y = val; }); else { const ax = h.axes[0]; P[h.k][ax] = val; P[h.k + 1][ax] = val; } S.layout.routes[h.sid] = P.map((v) => [v.x, v.y, v.z]); S.editChanged = true; cleanRoute(h.sid); buildPipes(); refreshHandles(); endEdit(); }, autoRoute: autoRouteSel, reset: resetLayout } };
  canvas.style.cursor = "grab";
  applyPalette(); resize(); applyToggles(); applyInset();
  if (/[?&]lite=1/.test(location.search)) lowQuality();
  requestAnimationFrame(frame);
  post("streamlit:componentReady", { apiVersion: 1 });
  post("streamlit:setFrameHeight", { height: 720 });
})();
