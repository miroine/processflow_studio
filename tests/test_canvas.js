/* Canvas tests: pure geometry/routing + a DOM-shim smoke test of the Streamlit protocol
 * and the main interactions (palette drop, connect, drop-to-create terminal, double-click,
 * delete, undo, flip, box select, export). No jsdom available, so a small DOM shim is used.
 *
 * Run: node tests/test_canvas.js
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let passed = 0, failed = 0;
function check(name, ok, detail) {
  if (ok) passed++; else { failed++; console.log("  FAIL  " + name + (detail ? "  [" + detail + "]" : "")); }
}

const SRC = path.join(__dirname, "..", "pfd_canvas", "frontend", "pfd.js");
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "canvas_fixture.json"), "utf8"));

// ------------------------------------------------------------ pure functions
const P = require(SRC);
{
  const u = { type: "separator", x: 100, y: 100, flip: false };
  const p = P.portPos(u, "vapour");
  check("separator vapour port at top", p.x === 100 && p.y === 54 && p.dir === "U", JSON.stringify(p));
  const f = P.portPos({ type: "compressor", x: 0, y: 0, flip: true }, "in");
  check("flipped compressor inlet moves to the right and faces right", f.x === 28 && f.dir === "R", JSON.stringify(f));
  const r = P.route({ x: 0, y: 0 }, "R", { x: 200, y: 50 }, "L");
  const ortho = r.every((q, i) => i === 0 || q.x === r[i - 1].x || q.y === r[i - 1].y);
  check("forward route is orthogonal", ortho, JSON.stringify(r));
  check("forward route starts/ends at the ports", r[0].x === 0 && r[r.length - 1].x === 200 && r[r.length - 1].y === 50);
  const back = P.route({ x: 300, y: 0 }, "R", { x: 0, y: 0 }, "L", 60);
  check("backward (recycle) route detours below", back.some((q) => q.y >= 60) &&
        back.every((q, i) => i === 0 || q.x === back[i - 1].x || q.y === back[i - 1].y), JSON.stringify(back));
  const mix = P.route({ x: 0, y: 0 }, "U", { x: 100, y: -100 }, "L");
  check("vertical-to-horizontal route has one elbow", mix.length <= 5, JSON.stringify(mix));
  check("simplify removes collinear points", P.simplify([{ x: 0, y: 0 }, { x: 5, y: 0 }, { x: 10, y: 0 }]).length === 2);
  const m = JSON.parse(JSON.stringify(fixture.model));
  check("nextName uses the HYSYS-style prefix", /^VLV-\d{3}$/.test(P.nextName(m, "valve", fixture.catalogue)));
  const m2 = { units: { a: { name: "K-100" } }, streams: { s: { name: "1" } } };
  check("nextName skips used names", P.nextName(m2, "compressor", fixture.catalogue) === "K-101");
  check("nextStreamName skips used names", P.nextStreamName(m2) === "2");
  check("nextName for feed", /^Feed \d+$/.test(P.nextName(m, "feed", fixture.catalogue)));
  const ids = Object.keys(m.units);
  const lts = ids.find((k) => m.units[k].type === "separator");
  const jt = ids.find((k) => m.units[k].type === "valve");
  check("canConnect refuses an already-used single outlet", !P.canConnect(m, fixture.catalogue, lts, "vapour", jt, "in"));
  check("canConnect refuses inlet->inlet", !P.canConnect(m, fixture.catalogue, jt, "in", lts, "feed"));
  check("canConnect accepts a free multi inlet", P.canConnect(
    Object.assign({}, m, { units: Object.assign({}, m.units, { q: { type: "valve", name: "q", x: 0, y: 0 } }) }),
    fixture.catalogue, "q", "out", lts, "feed"));
  for (const t of Object.keys(fixture.catalogue))
    check(`icon + geometry defined for ${t}`, !!P.ICON[t] && !!P.GEOM[t]);
  for (const [t, c] of Object.entries(fixture.catalogue)) {
    const g = P.GEOM[t];
    const all = Object.keys(c.ports.in).concat(Object.keys(c.ports.out));
    check(`every catalogue port of ${t} has a position`, all.every((pn) => g.ports[pn]), all.join(","));
  }
}

// ------------------------------------------------------------ v7.6: orientation, zoom, phasing
{
  const v = { type: "separator", x: 100, y: 100 }, h = { type: "separator", x: 100, y: 100, orient: "h" };
  check("separator defaults to vertical", P.orientOf(v) === "v" && P.hasOrient("separator") && !P.hasOrient("valve"));
  const pv = P.portPos(v, "vapour"), ph = P.portPos(h, "vapour");
  check("horizontal separator moves the vapour port off the top", pv.dir === "U" && ph.dir !== pv.dir || ph.x !== pv.x, JSON.stringify([pv, ph]));
  check("horizontal separator is wider than tall", P.extentOf(h).w > P.extentOf(h).h && P.extentOf(v).h > P.extentOf(v).w);
  const z = { type: "separator", x: 100, y: 100, scale: 2 };
  const pz = P.portPos(z, "vapour");
  check("zoom scales the port offset about the centre", pz.y === 100 + (pv.y - 100) * 2 && pz.x === 100, JSON.stringify(pz));
  check("zoom is clamped", P.scaleOf({ scale: 99 }) === 4 && P.scaleOf({ scale: 0.01 }) === 0.4 && P.scaleOf({}) === 1);
  check("platform size comes from the unit", P.extentOf({ type: "platform", w: 700, h: 400 }).w === 700);
  const phs = [{ id: "p1", name: "Tie-in" }, { id: "p2", name: "Boost" }];
  const add1 = { phase: "p1", change: "add" }, rem2 = { phase: "p2", change: "remove" };
  check("presentAt: untagged always", [0, 1, 2].every((s) => P.presentAt(phs, {}, s)));
  check("presentAt: added in p1 appears from stage 1", !P.presentAt(phs, add1, 0) && P.presentAt(phs, add1, 1) && P.presentAt(phs, add1, 2));
  check("presentAt: removed in p2 disappears at stage 2", P.presentAt(phs, rem2, 1) && !P.presentAt(phs, rem2, 2));
  check("presenceOf lists stages", [...P.presenceOf(phs, rem2)].join() === "0,1");
  check("phaseColor: explicit beats phase default", P.phaseColor(phs, { color: "#123456", phase: "p1" }) === "#123456" && /^#/.test(P.phaseColor(phs, add1)) && P.phaseColor(phs, {}) === null);
  for (const t of ["platform", "gas_turbine", "phase_splitter"])
    check(`new type ${t} is in the catalogue`, !!fixture.catalogue[t]);
}

// ------------------------------------------------------------ DOM shim
class ClassList {
  constructor(el) { this.el = el; this.s = new Set(); }
  add(...c) { c.forEach((x) => this.s.add(x)); }
  remove(...c) { c.forEach((x) => this.s.delete(x)); }
  toggle(c, on) { if (on === undefined) on = !this.s.has(c); on ? this.s.add(c) : this.s.delete(c); return on; }
  contains(c) { return this.s.has(c); }
}
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.attrs = {}; this.children = []; this.parentNode = null;
    this.listeners = {}; this.style = {}; this.dataset = {}; this.classList = new ClassList(this);
    this._text = ""; this._html = ""; this.onclick = null;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") { this.classList.s = new Set(String(v).split(/\s+/).filter(Boolean)); } }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c) { if (c.parentNode) c.remove(); c.parentNode = this; this.children.push(c); return c; }
  insertBefore(c, ref) { c.parentNode = this; const i = this.children.indexOf(ref); this.children.splice(i < 0 ? 0 : i, 0, c); return c; }
  remove() { if (this.parentNode) { const a = this.parentNode.children; a.splice(a.indexOf(this), 1); this.parentNode = null; } }
  get firstChild() { return this.children[0] || null; }
  set innerHTML(h) {
    this.children.forEach((c) => (c.parentNode = null));
    this.children = []; this._html = String(h);
    // enough structure for the palette items: <svg ...>...</svg><span>...</span>
    if (/<svg[\s>]/.test(h) && /<span>/.test(h)) {
      const s = new El("svg"); this.appendChild(s);
      const sp = new El("span"); sp.textContent = (h.match(/<span>(.*?)<\/span>/) || [])[1] || ""; this.appendChild(sp);
    }
  }
  get innerHTML() { return this._html; }
  set textContent(t) { this._text = String(t); }
  get textContent() { return this._text; }
  set className(v) { this.setAttribute("class", v); }
  get className() { return Array.from(this.classList.s).join(" "); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  fire(t, ev) { (this.listeners[t] || []).forEach((f) => f(ev)); }
  getBoundingClientRect() { return { left: 0, top: 0, right: 900, bottom: 600, width: 900, height: 600 }; }
  cloneNode() { const c = new El(this.tagName); c.attrs = Object.assign({}, this.attrs); c.children = this.children.slice(); return c; }
  matches(sel) {
    if (sel.startsWith(".")) return sel.slice(1).split(".").every((c) => this.classList.contains(c));
    return this.tagName === sel.toUpperCase();
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => { for (const c of n.children) { if (c.matches(sel)) out.push(c); walk(c); } };
    walk(this); return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const body = new El("body");
const byId = {};
// element ids come from the real index.html, so a button missing from the page fails the test
const HTML = fs.readFileSync(path.join(__dirname, "..", "pfd_canvas", "frontend", "index.html"), "utf8");
for (const [, id] of HTML.matchAll(/id="([^"]+)"/g)) {
  byId[id] = new El(id === "svg" ? "svg" : "div"); body.appendChild(byId[id]);
}
let pointTarget = null;
const posted = [];
const winListeners = {};
const document = {
  getElementById: (id) => byId[id],
  createElementNS: (ns, t) => new El(t),
  createElement: (t) => new El(t),
  querySelectorAll: (sel) => body.querySelectorAll(sel),
  body, styleSheets: [],
  elementFromPoint: () => pointTarget,
  addEventListener: () => {},
};
const window = {
  parent: { postMessage: (m) => posted.push(JSON.parse(JSON.stringify(m))) },
  focus: () => {},
  addEventListener: (t, f) => (winListeners[t] = winListeners[t] || []).push(f),
  removeEventListener: (t, f) => { winListeners[t] = (winListeners[t] || []).filter((g) => g !== f); },
};
function winFire(t, ev) { (winListeners[t] || []).slice().forEach((f) => f(ev)); }
const ctx = { document, window, requestAnimationFrame: (f) => f(), XMLSerializer: class { serializeToString(n) { return "<svg>" + n.children.length + "</svg>"; } },
              console, Math, Date, JSON, Array, Object, Set, String, Number, Infinity, setTimeout, clearTimeout };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(SRC, "utf8"), ctx);

const ev = (o) => Object.assign({ preventDefault() {}, stopPropagation() {}, button: 0, shiftKey: false, ctrlKey: false,
                                  metaKey: false, target: { classList: { contains: () => false } } }, o);
const values = () => posted.filter((m) => m.type === "streamlit:setComponentValue").map((m) => m.value);
function findUnitGroup(id) { return byId.svg.querySelectorAll("g").find((g) => g.getAttribute("data-id") === id && g.classList.contains("unit")); }

check("componentReady posted with apiVersion 1", posted.some((m) => m.type === "streamlit:componentReady" && m.apiVersion === 1));
check("messages flagged isStreamlitMessage", posted.every((m) => m.isStreamlitMessage === true));

const model = JSON.parse(JSON.stringify(fixture.model));
const results = { streams: {}, units: {}, links: [] };
for (const sid of Object.keys(model.streams)) results.streams[sid] = { solved: true, label: "1 °C · 2 bar · 3 kg/h" };
for (const uid of Object.keys(model.units)) results.units[uid] = { status: "ok" };
const comp = Object.keys(model.units).find((k) => model.units[k].type === "valve");
results.units[comp] = { status: "error", tip: "boom", energy: [] };
winFire("message", { data: { type: "streamlit:render", args: { model, catalogue: fixture.catalogue, results, nonce: 1, height: 640, status: "ok" } } });

const unitGroups = byId.svg.querySelectorAll("g").filter((g) => g.classList.contains("unit"));
check("every unit rendered", unitGroups.length === Object.keys(model.units).length, `${unitGroups.length}`);
const streamPaths = byId.svg.querySelectorAll("path").filter((p) => p.classList.contains("stream"));
check("every stream rendered", streamPaths.length === Object.keys(model.streams).length, `${streamPaths.length}`);
check("solved streams use the dark-blue marker", streamPaths.every((p) => p.getAttribute("marker-end") === "url(#mMat)"));
check("frame height requested", posted.some((m) => m.type === "streamlit:setFrameHeight" && m.height === 640));
check("error status outline drawn", findUnitGroup(comp).children.some((c) => c.classList.contains("statusbox")));
check("palette built", byId.palette.querySelectorAll(".pal").length === Object.keys(fixture.catalogue).length);

// palette drag -> add compressor
const palComp = byId.palette.querySelectorAll(".pal").find((p) => p.dataset.type === "compressor");
palComp.fire("pointerdown", ev({ clientX: 5, clientY: 5 }));
winFire("pointermove", ev({ clientX: 300, clientY: 300 }));
winFire("pointerup", ev({ clientX: 400, clientY: 300 }));
let v = values().pop();
check("palette drop sends an 'add' event", v && v.event === "add", v && v.event);
const newId = v.added;
check("new compressor in structure with K- name", v.model.units[newId] && v.model.units[newId].type === "compressor" &&
      /^K-\d+$/.test(v.model.units[newId].name), JSON.stringify(v.model.units[newId]));
check("event carries session, rev and nonce", typeof v.session === "string" && v.rev >= 1 && v.nonce === 1);

// drag from compressor outlet to empty space -> product created
let g = findUnitGroup(newId);
let port = g.children.find((c) => c.getAttribute("data-port") === "out");
port.fire("pointerdown", ev({ clientX: 428, clientY: 300 }));
pointTarget = null;
winFire("pointermove", ev({ clientX: 600, clientY: 300 }));
winFire("pointerup", ev({ clientX: 600, clientY: 300 }));
v = values().pop();
const prodId = Object.keys(v.model.units).find((k) => v.model.units[k].type === "product" && !model.units[k]);
check("dropping an outlet connection on empty space creates a product", v.event === "connect" && !!prodId);
const s1 = Object.values(v.model.streams).find((s) => s.src[0] === newId && s.dst[0] === prodId);
check("product stream named after the product", s1 && s1.name === v.model.units[prodId].name, JSON.stringify(s1));

// drag from compressor inlet to empty space -> feed created
g = findUnitGroup(newId);
port = g.children.find((c) => c.getAttribute("data-port") === "in");
port.fire("pointerdown", ev({ clientX: 372, clientY: 300 }));
winFire("pointerup", ev({ clientX: 250, clientY: 300 }));
v = values().pop();
const feedId = Object.keys(v.model.units).find((k) => v.model.units[k].type === "feed" && !model.units[k]);
check("dropping an inlet connection on empty space creates a feed", !!feedId);

// port-to-port connection: new valve outlet -> LTS multi feed port
const lts = Object.keys(model.units).find((k) => model.units[k].type === "separator");
palComp.parentNode.querySelectorAll(".pal").find((p) => p.dataset.type === "valve").fire("pointerdown", ev({ clientX: 5, clientY: 5 }));
winFire("pointermove", ev({ clientX: 100, clientY: 500 }));
winFire("pointerup", ev({ clientX: 100, clientY: 500 }));
const valveId = values().pop().added;
g = findUnitGroup(valveId);
port = g.children.find((c) => c.getAttribute("data-port") === "out");
const ltsFeed = findUnitGroup(lts).children.find((c) => c.getAttribute("data-port") === "feed");
port.fire("pointerdown", ev({ clientX: 120, clientY: 508 }));
pointTarget = ltsFeed;
winFire("pointermove", ev({ clientX: 10, clientY: 10 }));
check("compatible target port highlighted", ltsFeed.classList.contains("hot"));
winFire("pointerup", ev({ clientX: 10, clientY: 10 }));
pointTarget = null;
v = values().pop();
check("port-to-port connection created on a multi inlet",
      Object.values(v.model.streams).some((s) => s.src[0] === valveId && s.dst[0] === lts && s.dst[1] === "feed"));
const sname = Object.values(v.model.streams).find((s) => s.src[0] === valveId).name;
check("internal stream gets a free integer name", /^\d+$/.test(sname) &&
      Object.values(model.streams).every((s) => s.name !== sname), sname);

// refuse inlet->inlet
port = findUnitGroup(valveId).children.find((c) => c.getAttribute("data-port") === "in");
const nBefore = values().length;
port.fire("pointerdown", ev({ clientX: 80, clientY: 508 }));
pointTarget = findUnitGroup(lts).children.find((c) => c.getAttribute("data-port") === "feed");
winFire("pointermove", ev({ clientX: 11, clientY: 11 }));
winFire("pointerup", ev({ clientX: 11, clientY: 11 }));
pointTarget = null;
check("inlet-to-inlet drop is refused (no event)", values().length === nBefore);

// move a unit
const x0 = values().pop().model.units[valveId].x;
g = findUnitGroup(valveId);
g.fire("pointerdown", ev({ clientX: 100, clientY: 500 }));
winFire("pointermove", ev({ clientX: 160, clientY: 520 }));
winFire("pointerup", ev({ clientX: 160, clientY: 520 }));
v = values().pop();
check("dragging a unit sends 'move' with snapped position", v.event === "move" &&
      v.model.units[valveId].x % 10 === 0 && v.model.units[valveId].x > x0, JSON.stringify(v.model.units[valveId]));
check("the rubber-band connection line ignores the pointer", /"pointer-events": "none"/.test(fs.readFileSync(SRC, "utf8")));

// double-click opens
g = findUnitGroup(valveId);
g.fire("pointerdown", ev({ clientX: 160, clientY: 520 }));
winFire("pointerup", ev({ clientX: 160, clientY: 520 }));
g = findUnitGroup(valveId);
g.fire("pointerdown", ev({ clientX: 160, clientY: 520 }));
v = values().pop();
check("double-click sends 'open' with the unit selected", v.event === "open" && v.selected[0] === valveId, v.event);
winFire("pointerup", ev({}));

// flip
winFire("keydown", { key: "f", preventDefault() {}, target: {} });
v = values().pop();
check("F flips the selection", v.model.units[valveId].flip === true);

// delete + undo
const nStreams = Object.keys(v.model.streams).length;
winFire("keydown", { key: "Delete", preventDefault() {}, target: {} });
v = values().pop();
check("Delete removes the unit and its streams", v.event === "delete" && !v.model.units[valveId] &&
      Object.keys(v.model.streams).length === nStreams - 1);
winFire("keydown", { key: "z", ctrlKey: true, preventDefault() {}, target: {} });
v = values().pop();
check("Ctrl+Z restores it", v.event === "undo" && !!v.model.units[valveId] && Object.keys(v.model.streams).length === nStreams);

// box select with shift-drag on background
const bg = byId.svg.children.find((c) => c.getAttribute("id") === "bg");
bg.fire("pointerdown", ev({ clientX: -2000, clientY: -2000, shiftKey: true }));
winFire("pointermove", ev({ clientX: 3000, clientY: 3000 }));
winFire("pointerup", ev({ clientX: 3000, clientY: 3000 }));
v = values().pop();
check("shift-drag box selects all units", v.event === "select" && v.selected.length === Object.keys(v.model.units).length,
      `${v.selected.length}`);

// re-render with the same nonce keeps local edits; a new nonce adopts Python's model
winFire("message", { data: { type: "streamlit:render", args: { model, catalogue: fixture.catalogue, results, nonce: 1 } } });
check("same nonce keeps local edits", !!findUnitGroup(valveId));
winFire("message", { data: { type: "streamlit:render", args: { model, catalogue: fixture.catalogue, results, nonce: 2 } } });
check("new nonce adopts the Python model", !findUnitGroup(valveId));

// duplicate: select the LTS + JT valve and Ctrl+D -> paste event with a copies map and the internal stream
{
  const lts2 = Object.keys(model.units).find((k) => model.units[k].type === "separator");
  const jt2 = Object.keys(model.units).find((k) => model.units[k].type === "valve");
  findUnitGroup(lts2).fire("pointerdown", ev({ clientX: 1, clientY: 1 }));
  winFire("pointerup", ev({}));
  findUnitGroup(jt2).fire("pointerdown", ev({ clientX: 1, clientY: 1, shiftKey: true }));
  winFire("pointerup", ev({}));
  const nU = Object.keys(values().pop().model.units).length;
  winFire("keydown", { key: "d", ctrlKey: true, preventDefault() {}, target: {} });
  v = values().pop();
  const cp = v.copies || {};
  check("Ctrl+D sends 'paste' with a copies map", v.event === "paste" && Object.keys(cp).length === 2, JSON.stringify(cp));
  check("duplicates added with new names", Object.keys(v.model.units).length === nU + 2 &&
        Object.keys(cp).every((k) => v.model.units[k].name !== v.model.units[cp[k]].name));
  const inner = Object.values(v.model.streams).filter((s) => cp[s.src[0]] && cp[s.dst[0]]);
  check("internal connection between copied units is duplicated", inner.length === 1, `${inner.length}`);
  check("duplicates are offset from the originals", Object.keys(cp).every((k) => v.model.units[k].x === v.model.units[cp[k]].x + 40));
  winFire("keydown", { key: "v", ctrlKey: true, preventDefault() {}, target: {} });
  const v2 = values().pop();
  check("Ctrl+V pastes again, cascading", v2.event === "paste" &&
        Object.keys(v2.copies).every((k) => v2.model.units[k].x === v2.model.units[v2.copies[k]].x + 80));
}

// hydrate warning flag on a stream label
{
  const sid = Object.keys(model.streams).find((k) => {
    const s = model.streams[k]; return model.units[s.src[0]].type !== "feed" && model.units[s.dst[0]].type !== "product";
  });
  const res2 = JSON.parse(JSON.stringify(results));
  res2.streams[sid].warn = "hydrate";
  winFire("message", { data: { type: "streamlit:render", args: { model, catalogue: fixture.catalogue, results: res2, nonce: 3 } } });
  const lab = byId.svg.querySelectorAll("text").find((t) => t.classList.contains("slabel") && t.textContent.includes("❄"));
  check("hydrate-risk stream label carries the ❄ flag", !!lab && lab.textContent.startsWith(model.streams[sid].name));
}

// export svg
byId.bSvg.onclick();
v = values().pop();
check("export sends the SVG to Python", v.event === "export_svg" && typeof v.svg === "string" && v.svg.startsWith("<svg"));

// toolbar toggles don't throw
for (const b of ["bGrid", "bRes", "bEnergy", "bZin", "bZout", "bFit"]) { byId[b].onclick(); }
check("toolbar buttons run", true);

console.log(`canvas: ${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);
