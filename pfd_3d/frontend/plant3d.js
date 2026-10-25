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
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = T.SRGBColorSpace;
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFSoftShadowMap;

  const scene = new T.Scene();
  const camera = new T.PerspectiveCamera(38, 1, 0.1, 2000);
  const world = new T.Group();           // rebuilt on every new scene
  scene.add(world);

  const sky = { day: ["#9ec7ea", "#e9f1f7", "#c9d4dc"], dusk: ["#1a2740", "#e08a5a", "#2b2f3d"] };
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
  const SERVICE = { gas: 0x8fc3e6, liquid: 0xd48a22, water: 0x2f7de0, "two-phase": 0xa9b86a };
  const FLOWCOL = { gas: 0xa8ecff, liquid: 0xffb743, water: 0x4d9aff, "two-phase": 0xffffff };
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
    skid(g, 8.4, 2.8, 0.3, M.steelDk);
    const mot = cyl(0.62, 2.5, M.motor, 32); mot.rotation.z = Math.PI / 2; mot.position.set(-2.9, 1.0, 0); g.add(mot);
    for (let i = 0; i < 6; i++) g.add(at(box(0.04, 0.08, 1.15, M.steelDk), -3.6 + i * 0.42, 1.65, 0));
    const mb = box(0.5, 0.18, 0.7, M.black); mb.position.set(-2.9, 1.68, 0); g.add(mb);
    const shaft = cyl(0.12, 2.0, M.steel, 12); shaft.rotation.z = Math.PI / 2; shaft.position.set(-0.9, 1.0, 0); g.add(shaft); spin.push(SPIN(shaft, "x", 14));
    const gear = box(1.2, 1.5, 1.4, M.vesselDk); gear.position.set(-0.55, 0.95, 0); g.add(gear);
    const cs = cyl(0.85, 2.6, M.vessel, 36); cs.rotation.z = Math.PI / 2; cs.position.set(1.6, 1.05, 0); g.add(cs);
    for (const x of [0.35, 2.85]) { const f = cyl(0.92, 0.12, M.steelDk, 36); f.rotation.z = Math.PI / 2; f.position.set(x, 1.05, 0); g.add(f); }
    const vol = cyl(0.5, 1.2, M.vesselDk, 24); vol.position.set(0.9, 1.9, 0); g.add(vol); const vol2 = cyl(0.5, 1.2, M.vesselDk, 24); vol2.position.set(2.3, 1.9, 0); g.add(vol2);
    const lube = cyl(0.45, 1.1, M.green, 20); lube.position.set(0.4, 0.8, 1.2); g.add(lube);
    return { g, W: 8.4, H: 3.1, yMid: 1.0, ySpan: 0.3, yLow: 0.3, spin, ports: { in: { p: [0.9, 2.55, 0], d: [0, 1, 0] }, out: { p: [2.3, 2.55, 0], d: [0, 1, 0] } } };
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
    skid(g, 3.4 * k, 1.3 * k, 0.2, M.steelDk);
    const mot = cyl(0.42 * k, 1.5 * k, M.motor, 28); mot.rotation.z = Math.PI / 2; mot.position.set(-0.95 * k, 0.75 * k, 0); g.add(mot);
    const fan = cyl(0.34 * k, 0.1, M.steelDk, 18); fan.rotation.z = Math.PI / 2; fan.position.set(-1.75 * k, 0.75 * k, 0); g.add(fan); spin.push(SPIN(fan, "x", 20));
    const sh = cyl(0.07, 0.5 * k, M.steel, 10); sh.rotation.z = Math.PI / 2; sh.position.set(0, 0.75 * k, 0); g.add(sh); spin.push(SPIN(sh, "x", 22));
    const vo = cyl(0.5 * k, 0.5 * k, M.vessel, 28); vo.rotation.x = Math.PI / 2; vo.position.set(0.5 * k, 0.75 * k, 0); g.add(vo);
    const dis = cyl(0.17 * k, 0.7 * k, M.vessel, 16); dis.position.set(0.5 * k, 1.35 * k, 0); g.add(dis); { const fl = flange(0.17 * k, M.steelDk, 1.72 * k); fl.position.x = 0.5 * k; g.add(fl); }
    const suc = cyl(0.2 * k, 0.7 * k, M.vessel, 16); suc.rotation.z = Math.PI / 2; suc.position.set(1.1 * k, 0.75 * k, 0); g.add(suc);
    return { g, W: 3.6 * k, H: 2.0 * k, yMid: 0.75 * k, ySpan: 0.3, yLow: 0.2, spin, ports: { in: { p: [1.5 * k, 0.75 * k, 0], d: [1, 0, 0] }, out: { p: [0.5 * k, 1.8 * k, 0], d: [0, 1, 0] } } };
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
              dusk: false, rot: false, sel: null, hover: null, spin: [], sprites: [], boxes: [], size: 40, centre: new T.Vector3(), sig: "" };
  const cam = { target: new T.Vector3(), theta: 0.9, phi: 1.0, radius: 60, vt: 0, vp: 0, fly: null };

  function sigOf(x) { const s = JSON.stringify(x); let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return s.length + ":" + h; }
  function disposeTree(o) {
    o.traverse((c) => { if (c.geometry) c.geometry.dispose(); if (c.material && !Object.values(M).includes(c.material)) { if (c.material.map) c.material.map.dispose(); c.material.dispose(); } });
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

  function clearWorld() {
    while (world.children.length) { const c = world.children.pop(); disposeTree(c); }
    S.units = {}; S.pipes = []; S.pickables = []; S.spin = []; S.sprites = []; S.boxes = []; S.labels = [];
    S.sel = null; S.hover = null;
  }

  function groundAndSun(bb) {
    const c = bb.getCenter(new T.Vector3()), sz = bb.getSize(new T.Vector3()), R = Math.max(sz.x, sz.z, 24) * 0.75 + 16;
    const padW = sz.x + 26, padD = sz.z + 26;
    S.centre.copy(c); S.size = Math.max(sz.x, sz.z, sz.y, 20);
    const cv = document.createElement("canvas"); cv.width = cv.height = 512; const g = cv.getContext("2d");
    g.fillStyle = "#7b7f7d"; g.fillRect(0, 0, 512, 512);
    for (let i = 0; i < 2600; i++) { const v = 128 + Math.random() * 40; g.fillStyle = `rgba(${v},${v},${v - 4},.35)`; g.fillRect(Math.random() * 512, Math.random() * 512, 2, 2); }
    g.strokeStyle = "rgba(60,60,60,.55)"; g.lineWidth = 2; g.strokeRect(0, 0, 512, 512);
    const tx = new T.CanvasTexture(cv); tx.wrapS = tx.wrapT = T.RepeatWrapping; tx.colorSpace = T.SRGBColorSpace; tx.repeat.set(padW / 6, padD / 6); tx.anisotropy = 8;
    const pad = mesh(new T.PlaneGeometry(padW, padD), new T.MeshStandardMaterial({ map: tx, roughness: 0.95, metalness: 0 }), false);
    pad.rotation.x = -Math.PI / 2; pad.position.set(c.x, 0, c.z); pad.receiveShadow = true; world.add(pad);
    const ground = mesh(new T.CircleGeometry(900, 48), new T.MeshStandardMaterial({ color: 0x4b504c, roughness: 1 }), false);
    ground.rotation.x = -Math.PI / 2; ground.position.set(c.x, -0.06, c.z); world.add(ground);
    sun.position.set(c.x + R * 0.9, R * 1.5, c.z + R * 0.7); sun.target.position.copy(c);
    const e = R * 1.25; sun.shadow.camera.left = -e; sun.shadow.camera.right = e; sun.shadow.camera.top = e; sun.shadow.camera.bottom = -e;
    sun.shadow.camera.near = 1; sun.shadow.camera.far = R * 5; sun.shadow.camera.updateProjectionMatrix();
    scene.fog = new T.Fog(S.dusk ? 0x202a3a : 0xc9d6df, R * 2.2, R * 9);
  }

  function build(sc) {
    clearWorld();
    const units = sc.units || [], streams = sc.streams || [];
    const bb = new T.Box3();
    // platforms (deck outlines)
    for (const p of sc.platforms || []) {
      const g = new T.Group(); const d = box(p.w, 0.18, p.d, M.galv); d.position.y = 0.09; d.material = d.material.clone(); d.material.transparent = true; d.material.opacity = 0.35; g.add(d);
      for (const sx of [-1, 1]) g.add(at(box(0.08, 0.9, p.d, M.yellow), sx * p.w / 2, 0.55, 0));
      for (const sz of [-1, 1]) g.add(at(box(p.w, 0.08, 0.08, M.yellow), 0, 1.0, sz * p.d / 2));
      g.position.set(p.x, 0, p.z); world.add(g); bb.expandByObject(g);
      const lb = labelSprite(p.name, "platform / module", "#e8b923"); lb.position.set(p.x, 1.8, p.z + p.d / 2 + 0.5); world.add(lb); S.labels.push(lb);
    }
    // equipment: pass 1 - shapes and footprints; pass 2 - keep neighbours apart (the 2D drawing is far tighter than a plot plan)
    const obstacles = [];
    const recs = [];
    for (const u of units) {
      const arch = archetype(u), g = arch.g;
      const holder = new T.Group(); holder.add(g);
      const sc_ = Math.max(0.5, Math.min(2.2, u.scale || 1));
      g.scale.setScalar(sc_);
      holder.position.set(u.x, 0, u.z);
      if (u.flip) holder.rotation.y = Math.PI;
      world.add(holder); holder.updateMatrixWorld(true);
      const fb = new T.Box3().setFromObject(holder);
      recs.push({ u, arch, holder, sc_, hw: (fb.max.x - fb.min.x) / 2, hd: (fb.max.z - fb.min.z) / 2, cx: (fb.max.x + fb.min.x) / 2 - u.x, cz: (fb.max.z + fb.min.z) / 2 - u.z });
    }
    const GAP = 3.2;
    for (let it = 0; it < 120; it++) {
      let moved = false;
      for (let i = 0; i < recs.length; i++) for (let j = i + 1; j < recs.length; j++) {
        const A = recs[i], B = recs[j];
        const ax = A.holder.position.x + A.cx, az = A.holder.position.z + A.cz, bx = B.holder.position.x + B.cx, bz = B.holder.position.z + B.cz;
        const ox = A.hw + B.hw + GAP - Math.abs(ax - bx), oz = A.hd + B.hd + GAP - Math.abs(az - bz);
        if (ox > 0 && oz > 0) {
          moved = true;
          if (ox < oz) { const d = (ax <= bx ? -1 : 1) * ox / 2; A.holder.position.x += d; B.holder.position.x -= d; }
          else { const d = (az <= bz ? -1 : 1) * oz / 2; A.holder.position.z += d; B.holder.position.z -= d; }
        }
      }
      if (!moved) break;
    }
    for (const R of recs) {
      const { u, arch, holder, sc_ } = R;
      u.x = holder.position.x; u.z = holder.position.z; holder.updateMatrixWorld(true);
      const pc = u.color || (STATUS[u.status] && u.type !== "feed" && u.type !== "product" ? "#" + STATUS[u.status].toString(16) : "");
      if (pc) plate(holder, R.hw * 1.8, R.hd * 1.8, new T.Color(pc));
      const ports = {};
      const names = new Set(); for (const s of streams) { if (s.src[0] === u.id) names.add(s.src[1]); if (s.dst[0] === u.id) names.add(s.dst[1]); }
      for (const n of names) {
        const q = portOf(arch, u, n);
        const wp = holder.localToWorld(q.p.clone().multiplyScalar(sc_));
        const wd = q.d.clone().transformDirection(holder.matrixWorld).normalize();
        ports[n] = { p: wp, d: wd, stub: q.stub };
      }
      const label = labelSprite(u.name, u.title, u.color || null); label.position.set(u.x, arch.H * sc_ + 1.6, u.z); world.add(label); S.labels.push(label);
      const tip = new T.Box3().setFromObject(holder); bb.union(tip);
      obstacles.push({ x0: tip.min.x - 0.6, x1: tip.max.x + 0.6, z0: tip.min.z - 0.6, z1: tip.max.z + 0.6, top: tip.max.y, id: u.id });
      const rec = { u, arch, holder, ports, label, top: tip.max.y, box: tip, flameLight: arch.flameLight };
      S.units[u.id] = rec;
      holder.traverse((c) => { if (c.isMesh) { c.userData.uid = u.id; S.pickables.push(c); } });
      for (const sp of arch.spin || []) S.spin.push({ o: sp.o, axis: sp.axis, rate: sp.rate, on: u.status === "ok" || u.status === "warning" });
      if (arch.flame) rec.flame = arch.flame;
      if (arch.plume) rec.plume = arch.plume;
      if (arch.level) rec.level = arch.level;
      if (STATUS[u.status] && u.type !== "feed" && u.type !== "product") {
        const bc = new T.Mesh(new T.SphereGeometry(0.32, 16, 12), new T.MeshBasicMaterial({ color: STATUS[u.status] }));
        bc.position.set(u.x, arch.H * sc_ + 0.5, u.z); world.add(bc); S.sprites.push({ o: bc, kind: "beacon" });
      }
    }
    // pipes
    const maxTop = obstacles.reduce((a, o) => Math.max(a, o.top), 6);
    const posts = new Set();
    streams.forEach((s, idx) => {
      const A = S.units[s.src[0]], B = S.units[s.dst[0]];
      if (!A || !B) return;
      const pa = A.ports[s.src[1]], pb = B.ports[s.dst[1]];
      if (!pa || !pb) return;
      const rad = Math.max(0.05, Math.min(0.7, (s.d || 0.14) / 2 * 1.08));
      // stagger several connections that share one port
      const shareA = streams.filter((o, j) => j < idx && o.src[0] === s.src[0] && o.src[1] === s.src[1]).length;
      const shareB = streams.filter((o, j) => j < idx && o.dst[0] === s.dst[0] && o.dst[1] === s.dst[1]).length;
      const off = (n, d) => { const side = new T.Vector3(-d.z, 0, d.x); if (side.lengthSq() < 1e-6) side.set(1, 0, 0); return side.multiplyScalar(0.0).add(new T.Vector3(0, n * 0.55, 0)); };
      const a = pa.p.clone().add(off(shareA, pa.d)), b = pb.p.clone().add(off(shareB, pb.d));
      const path = routePipe(a, pa.d, b, pb.d, idx, rad, obstacles, maxTop, s.src[0], s.dst[0]);
      makePipe(s, path, rad, A, B, pa, pb, posts);
    });
    groundAndSun(bb.isEmpty() ? new T.Box3(new T.Vector3(-10, 0, -10), new T.Vector3(10, 5, 10)) : bb);
    applyToggles();
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
  function makePipe(s, path, rad, A, B, pa, pb, posts) {
    const dense = roundedPolyline(path, Math.max(0.45, rad * 3.2));
    const cum = [0]; for (let i = 1; i < dense.length; i++) cum.push(cum[i - 1] + dense[i].distanceTo(dense[i - 1]));
    const len = cum[cum.length - 1];
    const curve = new T.CatmullRomCurve3(dense, false, "centripetal");
    const segs = Math.max(24, Math.min(600, Math.round(len / 0.35)));
    const flowing = s.solved && (s.mass || 0) > 0;
    const col = new T.Color(SERVICE[s.service] || 0xcccccc);
    const mat = new T.MeshPhysicalMaterial({ color: col, emissive: col, emissiveIntensity: 0.12, roughness: 0.3, metalness: 0.25, transparent: true, opacity: flowing ? 0.66 : 0.95, clearcoat: 0.7, clearcoatRoughness: 0.2, depthWrite: false, side: T.DoubleSide });
    if (!flowing) { mat.color.set(0x8d969e); mat.metalness = 0.6; }
    const tube = new T.Mesh(new T.TubeGeometry(curve, segs, rad, 16, false), mat);
    tube.renderOrder = 5; tube.castShadow = false; tube.receiveShadow = false; tube.userData.sid = s.id;
    world.add(tube); S.pickables.push(tube);
    // flanges at both ends and every ~14 m, nozzle stubs on equipment
    const addFl = (p, dir) => { const f = cyl(rad * 1.35, 0.1, M.steelDk, 20); f.position.copy(p); f.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), dir); world.add(f); f.userData.sid = s.id; S.pickables.push(f); };
    addFl(dense[0].clone().addScaledVector(dense[1].clone().sub(dense[0]).normalize(), 0.05), dense[1].clone().sub(dense[0]).normalize());
    const n = dense.length; addFl(dense[n - 1].clone().addScaledVector(dense[n - 2].clone().sub(dense[n - 1]).normalize(), 0.05), dense[n - 1].clone().sub(dense[n - 2]).normalize());
    for (const [pp, un] of [[pa, A], [pb, B]]) if (pp.stub) {
      const st = cyl(rad * 1.02, 0.7, M.steel, 16); st.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), pp.d); st.position.copy(pp.p).addScaledVector(pp.d, -0.3); world.add(st);
      st.userData.uid = un.u.id; S.pickables.push(st);
    }
    // supports under long horizontal runs
    for (let i = 1; i < path.length; i++) {
      const p = path[i - 1], q = path[i], hrz = Math.abs(p.y - q.y) < 0.01 && p.y > 2.2;
      if (!hrz) continue;
      const L = p.distanceTo(q);
      for (let d = 2.5; d < L; d += 6) {
        const x = p.x + (q.x - p.x) * d / L, z = p.z + (q.z - p.z) * d / L, key = Math.round(x / 3) + "," + Math.round(z / 3) + "," + Math.round(p.y);
        if (posts.has(key)) continue; posts.add(key);
        const post = box(0.16, p.y - rad, 0.16, M.galv); post.position.set(x, (p.y - rad) / 2, z); world.add(post);
        const cap = box(0.9, 0.1, 0.18, M.galv); cap.position.set(x, p.y - rad - 0.02, z); world.add(cap);
      }
    }
    const rec = { s, tube, mat, dense, cum, len, rad, flowing, parts: [] };
    if (flowing && len > 0.5) {
      const dens = { gas: 1.0, liquid: 0.7, water: 0.7, "two-phase": 0.9 }[s.service] || 0.8;
      const nP = Math.max(4, Math.min(140, Math.round(len * dens)));
      const speed = Math.max(0.7, Math.min(6.5, 0.9 + 2.2 * Math.log10(1 + (s.vel || 1))));
      rec.speed = speed; rec.nP = nP;
      for (let k = 0; k < nP; k++) rec.parts.push({ f: k / nP + (Math.sin(k * 12.9898 + s.id.length) * 0.5 + 0.5) * 0.4 / nP, svc: s.service === "two-phase" ? (k % 2 ? "gas" : "liquid") : s.service });
    }
    S.pipes.push(rec);
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
  const tmpV = new T.Vector3();
  function updateFlow(t) {
    if (!flowMesh) return;
    flowMesh.visible = S.flow;
    if (!S.flow) return;
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
    for (const l of S.labels) l.visible = S.labelsOn;
    for (const m of XRAY_MATS) { m.transparent = S.xray; m.opacity = S.xray ? 0.22 : 1; m.depthWrite = !S.xray; m.needsUpdate = true; }
    world.traverse((o) => { if (o.userData && o.userData.internal) o.visible = S.xray; });
    scene.background = skyTexture(S.dusk ? sky.dusk : sky.day);
    hemi.intensity = S.dusk ? 0.22 : 0.55; sun.intensity = S.dusk ? 0.7 : 2.4; sun.color.set(S.dusk ? 0xffb27a : 0xfff2dc);
    renderer.toneMappingExposure = S.dusk ? 0.85 : 1.05; scene.environmentIntensity = S.dusk ? 0.25 : 0.7;
    if (scene.fog) scene.fog.color.set(S.dusk ? 0x202a3a : 0xc9d6df);
    for (const b of ["bFlow", "bLabels", "bXray", "bNight", "bRot"]) { const el = $(b), on = { bFlow: S.flow, bLabels: S.labelsOn, bXray: S.xray, bNight: S.dusk, bRot: S.rot }[b]; el.classList.toggle("on", !!on); }
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

  const ptr = new Map(); let drag = null, moved = 0;
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId); ptr.set(e.pointerId, { x: e.clientX, y: e.clientY });
    drag = { mode: e.button === 2 || e.shiftKey || e.button === 1 ? "pan" : "rot", x: e.clientX, y: e.clientY }; moved = 0; cam.fly = null; S.rot = false; applyToggles();
  });
  canvas.addEventListener("pointermove", (e) => {
    const p = ptr.get(e.pointerId);
    if (!p) { hoverAt(e); return; }
    const old = { x: p.x, y: p.y }; p.x = e.clientX; p.y = e.clientY;
    if (ptr.size === 2) {                    // pinch
      const [a, b] = [...ptr.values()]; const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (drag && drag.pinch) cam.radius = Math.max(3, Math.min(900, cam.radius * drag.pinch / Math.max(1, d)));
      if (drag) drag.pinch = d; return;
    }
    const dx = e.clientX - old.x, dy = e.clientY - old.y; moved += Math.abs(dx) + Math.abs(dy);
    if (!drag) return;
    if (drag.mode === "rot") { cam.vt -= dx * 0.0052; cam.vp -= dy * 0.0052; cam.theta -= dx * 0.0052; cam.phi = Math.max(0.02, Math.min(1.52, cam.phi - dy * 0.0052)); }
    else {
      const k = cam.radius * 0.0014, right = new T.Vector3().setFromMatrixColumn(camera.matrix, 0), fwd = new T.Vector3(-Math.sin(cam.theta), 0, -Math.cos(cam.theta));
      cam.target.addScaledVector(right, -dx * k).addScaledVector(fwd, dy * k);
    }
  });
  const up = (e) => {
    ptr.delete(e.pointerId);
    if (drag && moved < 5 && e.type === "pointerup") pickAt(e, false);
    if (!ptr.size) drag = null; else if (drag) drag.pinch = 0;
  };
  canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);
  canvas.addEventListener("dblclick", (e) => pickAt(e, true));
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); cam.fly = null; const f = Math.exp(e.deltaY * 0.0012); cam.radius = Math.max(3, Math.min(900, cam.radius * f)); }, { passive: false });

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
    showInfo(kind, id);
    if (kind && fly) {
      let c, r;
      if (kind === "unit") { const b = S.units[id].box, v = b.getCenter(new T.Vector3()), sz = b.getSize(new T.Vector3()); c = v; r = Math.max(8, Math.max(sz.x, sz.y, sz.z) * 2.4); }
      else { const p = S.pipes.find((q) => q.s.id === id), b = new T.Box3().setFromPoints(p.dense); c = b.getCenter(new T.Vector3()); r = Math.max(8, b.getSize(new T.Vector3()).length() * 1.1); }
      flyTo(c.x, c.y, c.z, r);
    }
  }
  function pickAt(e, fly) { const h = hit(e); if (h) select(h.kind, h.id, fly); else if (!fly) select(null); }
  $("infoX").onclick = () => select(null);

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
    for (const l of S.labels) { const d = l.position.distanceTo(camera.position), k = 0.07 + 0.03 * Math.max(0, Math.min(1, (70 - d) / 55)); l.scale.set(k, k / 4, 1); }
    for (const s of S.sprites) if (s.kind === "beacon") { const k = 0.7 + 0.5 * Math.sin(clock * 5); s.o.scale.setScalar(k); }
    updateFlow(clock);
    if (boxSel) boxSel.material.opacity = 0.7 + 0.3 * Math.sin(clock * 4);
    renderer.render(scene, camera);
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
  $("bLabels").onclick = () => { S.labelsOn = !S.labelsOn; applyToggles(); };
  $("bXray").onclick = () => { S.xray = !S.xray; applyToggles(); };
  $("bNight").onclick = () => { S.dusk = !S.dusk; applyToggles(); };
  $("bRot").onclick = () => { S.rot = !S.rot; applyToggles(); };
  $("bShot").onclick = () => { renderer.render(scene, camera); const a = document.createElement("a"); a.download = "plant_3d.png"; a.href = canvas.toDataURL("image/png"); a.click(); };

  function legend(sc) {
    const sv = [["gas", "Gas"], ["liquid", "Oil / liquid"], ["water", "Water"], ["two-phase", "Two-phase"]];
    let h = sv.map(([k, n]) => `<span><i style="background:#${SERVICE[k].toString(16).padStart(6, "0")}"></i>${n}</span>`).join("");
    for (const p of sc.meta.phases || []) h += `<span><i style="background:${esc(p.color || "#e8590c")}"></i>${esc(p.name)}</span>`;
    $("legend").innerHTML = h;
  }

  // ------------------------------------------------------------------ Streamlit protocol
  function post(type, data) { window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type }, data || {}), "*"); }
  function setScene(sc, ports) {
    S.ports = ports || S.ports;
    const sg = sigOf(sc);
    S.scene = sc;
    $("stageName").innerHTML = `Stage: <b>${esc((sc.meta && sc.meta.stageName) || "")}</b>${sc.meta && !sc.meta.solved ? " · not solved" : ""}`;
    if (sg === S.sig) return;
    const first = !S.sig; S.sig = sg;
    const prevSel = S.sel;
    build(sc); buildFlow(); legend(sc);
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
  window.__p3d = { state: S, select: (k, id, fly) => select(k, id, !!fly), view, setScene, counts: () => ({ units: Object.keys(S.units).length, pipes: S.pipes.length, particles: flowCount, pickables: S.pickables.length }),
                   info: () => $("infoBody").innerText, fps: () => ({ ema, lowQ }), setFlow: (v) => { S.flow = v; applyToggles(); }, renderer, cam };
  canvas.style.cursor = "grab";
  resize(); applyToggles();
  if (/[?&]lite=1/.test(location.search)) lowQuality();
  requestAnimationFrame(frame);
  post("streamlit:componentReady", { apiVersion: 1 });
  post("streamlit:setFrameHeight", { height: 720 });
})();
