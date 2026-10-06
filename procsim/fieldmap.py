"""Field-layout drawing (v6.4): the subsea field as a layout drawing - host, templates with their well slots,
satellites, PLEMs, subsea stations and injectors, connected by colour-coded service lines (production, gas lift,
water injection, chemical umbilical, power / fibre-optic) with their lengths.

Positions start from ``subsea_ops.field_layout`` (host at the origin, lines along their bearings) and can be dragged
in the app: ``model["layout"]["pos"][item id] = [east km, north km]`` and ``model["layout"]["rot"][item id] = deg``
(saved with the flowsheet, never re-solved). The drawing itself is done by the ``fieldmap_canvas`` component.
"""
from __future__ import annotations

import math

from . import surf
from .flowsheet import port_edges
from .subsea_ops import field_layout, _len_km

SERVICES = {   # key: (label, colour, dashed)
    "production": ("Production", "#2E9E4F", False),
    "gaslift": ("Gas lift", "#E0322B", False),
    "water": ("Water injection", "#2F6FD1", False),
    "chemical": ("Chemical lines (MEG, scale, wax)", "#1C1C1C", False),
    "power": ("Power / DC-FO cable", "#E8862A", False),
    "heating": ("Electrical heating (DEH)", "#E8862A", True),
}
STATION = {"subsea_booster": "booster", "subsea_pump": "booster", "subsea_compressor": "compressor",
           "subsea_separator": "separator", "subsea_cooler": "cooler", "subsea_valve": "ssiv", "cimv": "cimv",
           "intensifier": "intensifier"}


def _src(model, sid):
    return model["streams"][sid]["src"][0]


def _dst(model, sid):
    return model["streams"][sid]["dst"][0]


def _ins(model, uid):
    return [s for lst in port_edges(model, uid, "in").values() for s in lst]


def _outs(model, uid):
    return [s for lst in port_edges(model, uid, "out").values() for s in lst]


def _is_plem(u):
    return u["type"] == "jumper" and any(k in str(u["params"].get("kind", "")).upper() for k in ("PLEM", "PLET"))


def _well_upstream(model, uid, limit=12):
    """(well uid or None, branch length km, units passed) walking upstream from uid through single-inlet units."""
    units = model["units"]
    cur, L, seen = uid, 0.0, []
    for _ in range(limit):
        u = units[cur]
        seen.append(cur)
        if u["type"] == "well":
            return cur, L, seen
        L += _len_km(u) if u["type"] in ("jumper", "flowline") else 0.0
        ins = [s for s in port_edges(model, cur, "in").get("in", [])] or _ins(model, cur)
        if len(ins) != 1:
            return None, L, seen
        cur = _src(model, ins[0])
        if units[cur]["type"] == "feed":
            return None, L, seen
    return None, L, seen


def _declutter(items, fixed, dmin, rounds=60):
    """Push apart items closer than dmin [km] (markers are drawn at a constant screen size). The host, templates
    and anything placed by the user stay put; the others move."""
    lst = list(items.values())
    for _ in range(rounds):
        moved = False
        for i, a in enumerate(lst):
            for b in lst[i + 1:]:
                dx, dy = b["x"] - a["x"], b["y"] - a["y"]
                d = math.hypot(dx, dy)
                if d >= dmin:
                    continue
                if d < 1e-9:
                    dx, dy, d = 0.6, -0.8, 1.0
                ma = a["id"] not in fixed and a["kind"] not in ("host", "template")
                mb = b["id"] not in fixed and b["kind"] not in ("host", "template")
                if not (ma or mb):
                    continue
                push = (dmin - d) / (2.0 if ma and mb else 1.0) + 1e-6
                ux, uy = dx / d, dy / d
                if mb:
                    b["x"] += ux * push
                    b["y"] += uy * push
                if ma:
                    a["x"] -= ux * push
                    a["y"] -= uy * push
                moved = True
        if not moved:
            break


def _place_stations(items, lines, fixed):
    """Subsea stations next to a template (short or zero-length connections) are laid out in flow order along the
    template-to-host direction, branches side by side - as on a layout drawing (not to scale)."""
    host = items["host"]
    ext = max([math.hypot(i["x"] - host["x"], i["y"] - host["y"]) for i in items.values()] + [1.0])
    step = 0.15 * ext
    near = {}
    for ln in lines:
        if ln["service"] == "production" and (ln["length_km"] or 0.0) < 0.5:
            near.setdefault(ln["a"], []).append(ln["b"])
            near.setdefault(ln["b"], []).append(ln["a"])
    seen = {"host"}
    layer = ["host"]                     # units next to the host (an SSIV) belong to the host's corridor end
    while layer:
        nxt = [o for iid in layer for o in near.get(iid, []) if o not in seen
               and items[o]["kind"] not in ("template", "satellite", "injector")]
        for o in nxt:
            seen.add(o)
            items[o]["cluster"] = "host"
        layer = nxt
    for t in [i for i in items.values() if i["kind"] in ("template", "satellite")]:
        dx, dy = host["x"] - t["x"], host["y"] - t["y"]
        d = math.hypot(dx, dy) or 1.0
        ux, uy = dx / d, dy / d
        px, py = -uy, ux
        layer, depth = [t["id"]], 0
        seen.add(t["id"])
        t["cluster"] = t["id"]
        while layer:
            nxt = []
            for iid in layer:
                for o in near.get(iid, []):
                    if o not in seen and items[o]["kind"] not in ("template", "satellite", "host", "injector"):
                        seen.add(o)
                        items[o]["cluster"] = t["id"]
                        nxt.append(o)
            depth += 1
            for k, o in enumerate(nxt):
                if o in fixed:
                    continue
                lat = (k - (len(nxt) - 1) / 2.0) * 0.9 * step
                items[o]["x"] = t["x"] + depth * step * ux + lat * px
                items[o]["y"] = t["y"] + depth * step * uy + lat * py
            layer = nxt


def _qf_si(label, v):
    unit = label[label.rfind("[") + 1:-1] if label.endswith("]") else ""
    return f"{v:,.0f} {unit}".strip()


def field_drawing(model, sol=None, qf=None, length_unit=("km", 1.0)):
    """{"items": [...], "lines": [...], "services": {...}, "host": name, "extent_km": float, "units": {...}}.

    qf(label, SI value) formats a result for the info lines (the app passes its unit-system formatter);
    length_unit = (name, factor from km) for the distance labels."""
    qf = qf or _qf_si
    lu, lf = length_unit
    units = model["units"]
    lay = field_layout(model, sol)
    lset = model.get("layout") or {}
    pos_over = lset.get("pos") or {}
    rot_over = lset.get("rot") or {}
    npos = {n["name"]: (n["x"], n["y"]) for n in lay["nodes"]}
    host_name = next((n["name"] for n in lay["nodes"] if n["type"] == "host"), "Host")
    host_uid = next((u for u, x in units.items() if x["name"] == host_name), None)
    res = (sol.results if sol else {}) or {}
    items, item_of = {}, {}

    def add(iid, kind, name, xy, **kw):
        x, y = pos_over.get(iid, xy)
        items[iid] = dict({"id": iid, "kind": kind, "name": name, "x": float(x), "y": float(y),
                           "rot": float(rot_over.get(iid, kw.pop("rot", 0.0))), "info": ""}, **kw)
        return items[iid]

    add("host", "host", host_name, (0.0, 0.0))
    if host_uid:
        item_of[host_uid] = "host"
    # risers end at the host (the touch-down is not drawn: the riser goes into the platform)
    for uid, u in units.items():
        if u["type"] == "riser":
            item_of[uid] = "host"
    # templates and their slots; satellites
    sat_wells = set()
    for uid, u in units.items():
        if u["type"] != "template":
            continue
        try:
            n_slots = int(surf.item("template", u["params"].get("template")).get("slots") or 4)
        except Exception:                                        # noqa: BLE001
            n_slots = 4
        slots = []
        for sid in _ins(model, uid):
            w, L, chain = _well_upstream(model, _src(model, sid))
            if w is None:
                continue
            if L > 0.5:
                sat_wells.add(w)
                continue
            wu = units[w]
            lift = bool(port_edges(model, w, "in").get("lift"))
            for k in range(surf.n_wells(wu)):
                slots.append({"name": wu["name"] + (f"-{k + 1}" if surf.n_wells(wu) > 1 else ""),
                              "kind": "producer", "gl": lift})
        for _ in range(max(n_slots - len(slots), 0)):
            slots.append({"name": "", "kind": "spare", "gl": False})
        r = res.get(uid) or {}
        add(uid, "template", u["name"], npos.get(u["name"], (0.0, 0.0)), slots=slots, n_slots=n_slots,
            info=f"{sum(1 for s in slots if s['kind'] != 'spare')}/{n_slots} slots"
                 + (f" · {qf('Outlet P [bar(a)]', r['Outlet P [bar(a)]'])}" if r.get("Outlet P [bar(a)]") else ""))
        item_of[uid] = uid
    for w in sat_wells:
        wu = units[w]
        xt = next((_dst(model, s) for s in _outs(model, w) if units[_dst(model, s)]["type"] == "xmas_tree"), None)
        anchor = xt or w
        name = units[anchor]["name"]
        xy = npos.get(name) or next((v for k, v in npos.items() if wu["name"] in k), (0.0, 0.0))
        r = res.get(w) or {}
        add(anchor, "satellite", f"{wu['name']}", xy, gl=bool(port_edges(model, w, "in").get("lift")),
            info=(f"WH {qf('Wellhead P [bar(a)]', r['Wellhead P [bar(a)]'])}" if r.get("Wellhead P [bar(a)]") else ""))
        item_of[anchor] = anchor
        item_of[w] = anchor
    # PLEMs / PLETs and subsea stations
    for uid, u in units.items():
        if uid in item_of:
            continue
        t = u["type"]
        if _is_plem(u):
            # the jumper is an edge in field_layout: put the PLEM at the downstream end of its upstream neighbour
            up = _ins(model, uid)
            src = _src(model, up[0]) if up else None
            xy = npos.get(units[src]["name"]) if src else None
            add(uid, "plem", u["name"], xy or (0.0, 0.0))
            item_of[uid] = uid
        elif t in STATION and u["name"] in npos:
            r = res.get(uid) or {}
            info = ""
            if r.get("Electrical power [kW]"):
                info = f"{r['Electrical power [kW]'] / 1000.0:.1f} MW"
            elif r.get("Vessel ID [mm]"):
                info = f"ID {qf('Vessel ID [mm]', r['Vessel ID [mm]'])}"
            add(uid, STATION[t], u["name"], npos[u["name"]], info=info)
            item_of[uid] = uid
    # injectors (not on the production path): beside the first template, away from the host
    tmpl = [it for it in items.values() if it["kind"] == "template"]
    ref = tmpl[0] if tmpl else next(iter(items.values()))
    k_inj = 0
    for uid, u in units.items():
        if u["type"] != "injection_well":
            continue
        d = math.hypot(ref["x"], ref["y"]) or 5.0
        ang = math.atan2(ref["y"], ref["x"]) + math.radians(55 + 25 * k_inj)
        r = res.get(uid) or {}
        add(uid, "injector", u["name"], (0.85 * d * math.cos(ang), 0.85 * d * math.sin(ang)),
            slots=[{"name": u["name"].split(" ")[0] + (f"-{k + 1}" if surf.n_wells(u) > 1 else ""), "kind": "injector",
                    "gl": False} for k in range(surf.n_wells(u))]
            + [{"name": "", "kind": "spare", "gl": False}] * max(0, 4 - surf.n_wells(u)),
            n_slots=max(4, surf.n_wells(u)),
            info=(qf("Injection rate [Sm³/d]", r["Injection rate [Sm³/d]"]) if r.get("Injection rate [Sm³/d]") else ""))
        item_of[uid] = uid
        k_inj += 1
    # a subsea station feeding an injector (seawater lift pump) sits next to it, on the host side
    for uid, u in units.items():
        if u["type"] != "injection_well" or uid not in items:
            continue
        cur = uid
        for _ in range(10):
            ins = _ins(model, cur)
            if not ins:
                break
            cur = _src(model, ins[0])
            t = units[cur]["type"]
            if t == "feed":
                break
            if t in STATION and cur not in items:
                iw = items[uid]
                d = math.hypot(iw["x"], iw["y"]) or 1.0
                ext1 = max([math.hypot(i["x"], i["y"]) for i in items.values()] + [1.0])
                f1 = max(0.0, 1.0 - 0.15 * ext1 / d)
                r = res.get(cur) or {}
                add(cur, STATION[t], units[cur]["name"], (iw["x"] * f1, iw["y"] * f1),
                    info=(f"{r['Electrical power [kW]'] / 1000.0:.1f} MW" if r.get("Electrical power [kW]") else ""))
                item_of[cur] = cur

    # ---- lines ----------------------------------------------------------------------------------------------
    lines = []

    def downstream(a_uid, cur):
        """(item id reached, length km, heated?, flowline names) following the outlets from unit cur."""
        L, heated, names = 0.0, False, []
        for _ in range(30):
            if cur is None:
                return None, L, heated, names
            if cur in item_of and cur != a_uid:
                return item_of[cur], L, heated, names
            u = units[cur]
            if u["type"] in ("flowline", "jumper"):
                L += _len_km(u)
                names.append(u["name"])
                if u["type"] == "flowline" and surf.heating_system(u["params"])[0] != surf.HEAT_NONE:
                    heated = True
            outs = _outs(model, cur)
            if not outs:
                return None, L, heated, names
            cur = _dst(model, outs[0])
        return None, L, heated, names

    starts = [u for u in item_of if units.get(u) and units[u]["type"] not in ("well", "riser")]
    done = set()
    for a in starts:
        ia = item_of[a]
        if ia == "host" or a in done or items[ia]["kind"] == "injector":
            continue
        done.add(a)
        for sid in _outs(model, a):
            b, L, heated, names = downstream(a, _dst(model, sid))
            if b is None or b == ia:
                continue
            twin = next((ln for ln in lines if ln["service"] == "production" and names and ln["names"] == names), None)
            if twin is not None:      # commingles into a line already drawn (e.g. pump and compressor outlets)
                b, L, heated, names = twin["a"], 0.0, False, []
            svc = "water" if items[b]["kind"] == "injector" else "production"
            if any(ln["a"] == ia and ln["b"] == b and ln["service"] == svc for ln in lines):
                continue
            lines.append({"service": svc, "a": ia, "b": b, "length_km": L,
                          "label": f"~{L * lf:.1f} {lu}" if L >= 0.05 else "", "names": names, "heated": heated})
            if heated:
                lines.append({"service": "heating", "a": ia, "b": b, "length_km": L, "label": "", "names": names})
    _place_stations(items, lines, pos_over)
    # water injection: from the subsea station feeding the injector, or from the host
    for uid, u in units.items():
        if u["type"] != "injection_well" or any(ln["b"] == uid for ln in lines):
            continue
        src_item = "host"
        cur = uid
        for _ in range(10):
            ins = _ins(model, cur)
            if not ins:
                break
            cur = _src(model, ins[0])
            if cur in item_of and item_of[cur] != uid and units[cur]["type"] != "subsea_pump":
                src_item = item_of[cur]
                break
            if units[cur]["type"] == "subsea_pump" and cur in item_of:
                src_item = item_of[cur]
                break
        lines.append({"service": "water", "a": src_item, "b": uid, "length_km": None, "label": "", "names": []})
    # services from the host. The umbilical (chemicals) and the DC-FO cable (power + fibre) run from the DUTA to
    # the nearest structure of each cluster and are daisy-chained on to the units next to it; gas lift and water
    # injection come straight from the host.
    wet = [it for it in items.values() if it["kind"] in ("template", "satellite", "booster", "compressor", "separator",
                                                         "cooler", "injector", "ssiv", "cimv", "intensifier")]
    if wet:
        ext0 = max([math.hypot(i["x"], i["y"]) for i in items.values()] + [1.0])
        hx, hy = items["host"]["x"], items["host"]["y"]
        add("duta", "duta", "DUTA", (hx - 0.04 * ext0, hy - 0.10 * ext0))
        _declutter(items, pos_over, 0.075 * ext0)
        served = [items["duta"]]
        order = sorted(wet, key=lambda it: (it["kind"] not in ("template", "satellite", "injector"),
                                            math.hypot(it["x"] - hx, it["y"] - hy)))
        for it in order:
            if it["kind"] in ("template", "satellite", "injector"):
                src = items["duta"]
            else:          # daisy-chained from the nearest structure already served
                src = min(served[1:] or served, key=lambda o: math.hypot(o["x"] - it["x"], o["y"] - it["y"]))
            if it["kind"] != "ssiv":
                lines.append({"service": "chemical", "a": src["id"], "b": it["id"], "length_km": None, "label": "",
                              "names": []})
            lines.append({"service": "power", "a": src["id"], "b": it["id"], "length_km": None, "label": "",
                          "names": []})
            served.append(it)
            if (it["kind"] == "template" and any(s["gl"] for s in it["slots"])) or (it["kind"] == "satellite" and it.get("gl")):
                lines.append({"service": "gaslift", "a": "host", "b": it["id"], "length_km": None, "label": "",
                              "names": []})
    used = {s for ln in lines for s in (ln["service"],)}
    ext = max([abs(i["x"]) for i in items.values()] + [abs(i["y"]) for i in items.values()] + [1.0])
    return {"items": list(items.values()), "lines": lines, "host": host_name, "extent_km": ext,
            "units": {"len": lu, "f": lf},
            "bends": {k: float(v) for k, v in (lset.get("bend") or {}).items()},
            "services": {k: {"label": v[0], "color": v[1], "dashed": v[2]} for k, v in SERVICES.items() if k in used}}
