"""Field life: production profile, recovery factor, well count and boosting over the life of the field (v6).

The solved flowsheet describes the production system at one moment.  This module marches it through time:

* **Reservoir** - a compositional tank on the Peng-Robinson EOS (one tank for all the wells): fixed pore volume
  with rock and connate-water compressibility, an optional pot aquifer and, for oil, water injection at a
  voidage-replacement ratio.  Each step the tank pressure solves  n·v(p, z) + W_net·Bw = HCPV_i (1 − c_e (p_i − p)).
  Below the dew / bubble point the produced composition is the mobility-weighted mix of the tank's vapour and
  liquid (Corey relative permeabilities), so a gas condensate drops liquid (CGR falls) and an oil produces free gas
  (GOR rises) - the material balance of the tank stays exact in moles.
* **Water** - water cut (oil) or water-gas ratio (gas) rises with recovery after breakthrough (an input shape).
* **Network** - the flowsheet itself, solved with every Xmas-tree choke fully open, wells on their feed rate and the
  well feeds at the tank pressure: the deliverable rate is the largest rate scale *s* (on the hydrocarbon molar
  rate of every well feed) at which the delivery stream still arrives above the minimum pressure, every unit solves
  and boosters stay within their rating.  These "deliverability tables" are built lazily on a grid of reservoir
  pressure ratio and water cut (only the nodes the run visits are solved) and interpolated.
* **Constraints** - plateau (facility) rate, water and liquid handling capacity, delivery pressure, booster rating.
* **Wells** - the well units carry *identical wells in parallel* (``n_par``); a well count N is spread over them.
  Optional drilling rate (ramp-up, approximated by scaling the deliverability with the wells online).
* **Boosting** - as in the flowsheet, never, from a given year, or automatically when the plateau falls off.
* **Economics** - revenue at gas / oil prices, CAPEX from the equipment list (wells, extra templates, boosters
  when they are installed), OPEX as % of CAPEX plus the energy and CO₂ cost of each year, NPV, IRR, payback, an
  economic cut-off (production stops when the operating cash flow turns negative) and the recovery factor there.

Screening accuracy: one tank, lift tables built with the initial reservoir fluid (GOR / CGR changes alter the volumes
produced, not the hydraulics), quarterly steps.  All economic defaults are illustrative placeholders.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from . import surf
from .flowsheet import feed_composition, port_edges, solve, build_fluid
from .streams import make_stream
from .thermo import FlashError, V_STD_GAS
from .transport import phase_viscosity_cP

K0 = 273.15
GAS, OIL = "Gas", "Oil"
KINDS = ("Auto", GAS, OIL)
B_AS_IS, B_NEVER, B_YEAR, B_AUTO = ("As in the flowsheet", "Never (bypassed)", "Start in a given year",
                                    "Automatic: when the plateau falls off")
BOOST_MODES = (B_AS_IS, B_NEVER, B_YEAR, B_AUTO)
R_GRID = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.42, 0.34, 0.27, 0.2, 0.14, 0.09)
BW = 1.01                     # water formation volume factor (screening)
POLAR = ("H2O", "MeOH", "MEG")
FAILED = ("error", "missing", "unsolved")

DEFAULTS = {
    # reservoir
    "kind": "Auto", "in_place": 0.0, "Swc": 0.20, "cf": 6e-5, "cw": 4.5e-5, "aquifer": 0.0, "VRR": -1.0,
    "Sgc": 0.05, "Slc": 0.20, "ng": 2.0, "nl": 2.0,
    # water
    "RF_bt": -1.0, "RF_rise": -1.0, "w_max": -1.0,
    # facility, wells, boosting
    "q_plat": 0.0, "water_cap": 0.0, "liq_cap": 0.0, "P_min": 0.0, "deliv": "", "uptime": 95.0, "years": 30.0,
    "n_wells": 0.0, "rig": 0.0, "boost_mode": B_AS_IS, "boost_year": 5.0, "rating": True,
    # economics
    "gas_price": 0.25, "oil_price": 70.0, "fx": 10.5, "opex_pct": 4.0, "disc": 8.0, "capex_years": 2.0,
    "fac_exp": 0.0,
}
# defaults that depend on the reservoir kind (used where the stored value is negative = automatic)
KIND_DEFAULTS = {GAS: {"VRR": 0.0, "RF_bt": 1.0, "RF_rise": 0.2, "w_max": 100.0},
                 OIL: {"VRR": 1.0, "RF_bt": 0.10, "RF_rise": 0.35, "w_max": 90.0}}
LABELS = {
    "kind": ("Reservoir fluid", ""), "in_place": ("In place (0 = automatic)", "GSm³ gas / MSm³ oil"),
    "Swc": ("Connate water saturation", "-"), "cf": ("Pore compressibility", "1/bar"),
    "cw": ("Water compressibility", "1/bar"), "aquifer": ("Pot aquifer (× reservoir pore volume)", "-"),
    "VRR": ("Water injection, voidage replacement (oil)", "-"),
    "Sgc": ("Critical gas saturation", "-"), "Slc": ("Critical liquid saturation (oil / condensate)", "-"),
    "ng": ("Corey exponent, gas", "-"), "nl": ("Corey exponent, liquid", "-"),
    "RF_bt": ("Water breakthrough at recovery factor", "-"), "RF_rise": ("Water rise over recovery", "-"),
    "w_max": ("Maximum water cut [%] (oil) / WGR [Sm³/MSm³] (gas)", ""),
    "q_plat": ("Plateau rate (0 = current rate)", "MSm³/d gas / Sm³/d oil"),
    "water_cap": ("Water handling capacity (0 = none)", "Sm³/d"), "liq_cap": ("Liquid capacity (0 = none)", "Sm³/d"),
    "P_min": ("Minimum delivery pressure (0 = automatic)", "bar(a)"), "uptime": ("Production efficiency", "%"),
    "years": ("Maximum field life", "years"), "n_wells": ("Producing wells (0 = as in the flowsheet)", "-"),
    "rig": ("Drilling rate (0 = all wells ready at start)", "wells/year"),
    "boost_mode": ("Subsea boosting", ""), "boost_year": ("Boosting starts in year", "-"),
    "gas_price": ("Gas price", "USD/Sm³"), "oil_price": ("Oil / condensate price", "USD/bbl"),
    "fx": ("Energy-cost currency per USD", "-"), "opex_pct": ("Fixed OPEX", "% of CAPEX per year"),
    "disc": ("Discount rate", "%/year"), "capex_years": ("Construction before first production", "years"),
    "fac_exp": ("Facility cost scaling with plateau rate (exponent, 0 = fixed)", "-"),
}


class FieldLifeError(RuntimeError):
    pass


def params(model):
    p = dict(DEFAULTS)
    p.update({k: v for k, v in (model.get("fieldlife") or {}).items() if k in DEFAULTS})
    return p


def resolved(p, kind):
    """Settings with the kind-dependent automatic values filled in."""
    q = dict(p)
    for k, v in KIND_DEFAULTS[kind].items():
        if q.get(k) is None or float(q[k]) < 0:
            q[k] = v
    return q


# ---------------------------------------------------------------------------------------------- the flowsheet side

def _polar_mask(fp):
    return np.array([k in POLAR for k in fp.keys])


def delivery_stream(model, sol):
    """Default delivery point: the stream leaving the first riser (topside arrival), else the stream into the
    first product."""
    for uid, u in sorted(model["units"].items(), key=lambda kv: kv[1]["name"]):
        if u["type"] == "riser":
            outs = [s for lst in port_edges(model, uid, "out").values() for s in lst]
            if outs:
                return outs[0]
    for sid, s in sorted(model["streams"].items(), key=lambda kv: kv[1]["name"]):
        if model["units"][s["dst"][0]]["type"] == "product":
            return sid
    raise FieldLifeError("no delivery stream: add a riser or a product")


def well_feeds(model, sol, fp):
    """[(well uid, feed uid, feed stream id, base data)] for every well fed directly by a feed."""
    out = []
    pol = _polar_mask(fp)
    iw = fp.keys.index("H2O") if "H2O" in fp.keys else -1
    for uid, u in sorted(model["units"].items(), key=lambda kv: kv[1]["name"]):
        if u["type"] != "well":
            continue
        ins = port_edges(model, uid, "in").get("in") or []
        if not ins:
            continue
        sid = ins[0]
        fid = model["streams"][sid]["src"][0]
        if model["units"][fid]["type"] != "feed":
            raise FieldLifeError(f"{u['name']}: field life needs each well fed directly by a feed at reservoir conditions")
        st = sol.streams.get(sid)
        if st is None or st.empty:
            continue
        z = st.z.copy()
        hc = np.where(pol, 0.0, z)
        F_hc = st.F * hc.sum()
        z_hc = hc / hc.sum()
        g, o, _ = surf.standard_rates(make_stream("", fp, F_hc, z_hc, None), fp)
        w_std = 0.0
        if iw >= 0:
            w_std = st.F * z[iw] * fp.MW[iw] / fp.rho_std[iw] * 24.0          # Sm³/d of water
        out.append((uid, fid, sid, {"F": st.F, "F_hc": F_hc, "z_hc": z_hc, "z": z, "P": st.P, "T": st.T,
                                     "gas": g, "oil": o, "wat": w_std, "n0": surf.n_wells(u),
                                     "others": np.where(pol, z, 0.0) * st.F}))
    if not out:
        raise FieldLifeError("no wells with a feed: field life needs well units (Subsea palette) fed at reservoir P, T")
    return out


def distribute(n_total, base):
    """Spread n_total wells over the well units in proportion to their current counts (largest remainders)."""
    tot = sum(base) or 1
    raw = [n_total * b / tot for b in base]
    n = [int(math.floor(x)) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - n[i], reverse=True)[:n_total - sum(n)]:
        n[i] += 1
    return n


def kpis(model, sol, deliv):
    from . import economics, subsea_design as sd
    st = sol.streams.get(deliv)
    k = {"Delivery P [bar(a)]": st.P if st is not None and not st.empty else None,
         "Delivery T [°C]": (st.T - K0) if st is not None and not st.empty else None}
    try:
        t = economics.compute(model, sol)
        hours = float(t["params"]["hours"]) or 8760.0
        k["Power demand [kW]"] = t["totals"]["Power demand [kW]"]
        k["Energy + CO₂ cost [cur/h]"] = t["totals"]["Energy + CO₂ cost [cur/y]"] / hours
        k["CO₂ [t/h]"] = t["totals"]["CO₂ emissions [t/y]"] / hours
    except Exception:                                           # noqa: BLE001
        k["Power demand [kW]"] = k["Energy + CO₂ cost [cur/h]"] = k["CO₂ [t/h]"] = 0.0
    k["Booster power [kW]"] = sd.booster_power_kW(model, sol)[1]
    k["Heating power [kW]"] = sd.heating_power_kW(model, sol)
    util, hm, er = [0.0], [], [0.0]
    for uid, r in sol.results.items():
        if not isinstance(r, dict):
            continue
        if r.get("Power utilisation [%]") is not None:
            util.append(float(r["Power utilisation [%]"]))
        for key in ("Min. hydrate margin along line [°C]", "Downstream hydrate margin [°C]"):
            if r.get(key) is not None:
                hm.append(float(r[key]))
        for key, v in r.items():
            if isinstance(key, str) and key.startswith("Erosional velocity ratio") and v is not None:
                er.append(float(v))
    k["Booster utilisation [%]"] = max(util)
    k["Min. hydrate margin [°C]"] = min(hm) if hm else None
    k["Max. erosional ratio [-]"] = max(er)
    return k


class Network:
    """Deliverability of one configuration (wells per unit, boosters on/off) from flowsheet solves."""

    def __init__(self, base_model, fp, feeds, deliv, P_min, n_cfg, boost_on, kind, rating=True, progress=None):
        self.fp, self.feeds, self.deliv, self.P_min, self.kind, self.rating = fp, feeds, deliv, P_min, kind, rating
        self.n_cfg, self.boost_on, self.progress = list(n_cfg), boost_on, progress
        m = copy.deepcopy(base_model)
        m.pop("scenarios", None)
        for u in m["units"].values():
            if u["type"] == "xmas_tree":
                u["params"]["spec"] = "Choke fully open"
            elif u["type"] == "well":
                u["params"]["rate_spec"] = surf.RATE_FEED
            elif u["type"] == "adjust":
                u["params"]["active"] = False
            elif u["type"] in surf.BOOSTER_TYPES and boost_on is not None:
                u["params"]["online"] = surf.ONLINE if boost_on else surf.BYPASSED
        for (wid, fid, sid, d), n in zip(feeds, self.n_cfg):
            m["units"][wid]["params"]["n_par"] = max(1, n)
            fpar = m["units"][fid]["params"]
            fpar["flow_basis"], fpar["comp_basis"], fpar["spec"] = "kmol/h", "Mole fractions", "T & P"
        self.model = m
        self.scale = [n / d["n0"] for (_, _, _, d), n in zip(feeds, self.n_cfg)]
        self.q_unit = sum(sc * (d["gas"] if kind == GAS else d["oil"]) for (_, _, _, d), sc in zip(feeds, self.scale))
        self.other_unit = sum(sc * (d["oil"] if kind == GAS else d["gas"]) for (_, _, _, d), sc in zip(feeds, self.scale))
        self.cache = {}
        self.nodes = {}
        self.n_solves = 0
        self.iw = fp.keys.index("H2O") if "H2O" in fp.keys else -1

    # -- one flowsheet solve
    def evaluate(self, r, s, w):
        key = (round(r, 5), round(s, 5), round(w, 5))
        if key in self.cache:
            return self.cache[key]
        m = self.model
        fp = self.fp
        for (wid, fid, sid, d), sc in zip(self.feeds, self.scale):
            fpar = m["units"][fid]["params"]
            F_hc = s * d["F_hc"] * sc
            comp = d["z_hc"] * F_hc + d["others"] * s * sc
            if self.iw >= 0:
                comp[self.iw] = 0.0
                prim = (d["oil"] if self.kind == OIL else d["gas"]) * s * sc
                if self.kind == OIL:
                    wstd = w / max(1.0 - w, 1e-6) * prim if w < 0.999 else 1e3 * prim
                else:
                    wstd = w * prim                                  # w = WGR Sm³/MSm³, prim in MSm³/d
                comp[self.iw] = wstd * fp.rho_std[self.iw] / fp.MW[self.iw] / 24.0
            F = float(comp.sum())
            fpar["flow"] = F
            fpar["composition"] = {k: float(comp[i] / F) if F > 0 else float(d["z"][i]) for i, k in enumerate(fp.keys)}
            fpar["P_bar"] = max(d["P"] * r, 1.0)
            fpar["T_C"] = d["T"] - K0
        try:
            sol = solve(m, fp)
            self.n_solves += 1
            if self.progress:
                self.progress(self.n_solves)
        except Exception as e:                                    # noqa: BLE001
            res = {"ok": False, "why": f"solve failed: {e}", "k": None}
            self.cache[key] = res
            return res
        bad = [u for u, v in sol.status.items() if v in FAILED]
        k = kpis(m, sol, self.deliv)
        Pd = k["Delivery P [bar(a)]"]
        if bad:
            first = next((u for u in bad if sol.status[u] == "error"), bad[0])
            msg = sol.errors.get(first) or sol.status[first]
            why = f"{m['units'][first]['name']}: {msg}"
            ok = False
        elif Pd is None:
            why, ok = "no flow at the delivery point", False
        elif Pd < self.P_min:
            why, ok = f"delivery pressure {Pd:.1f} bar below the minimum {self.P_min:.1f} bar", False
        elif self.rating and k["Booster utilisation [%]"] > 100.0 + 1e-6:
            why, ok = f"booster above its rating ({k['Booster utilisation [%]']:.0f} %)", False
        else:
            why, ok = "", True
        res = {"ok": ok, "why": why, "k": k, "Pd": Pd}
        self.cache[key] = res
        return res

    # -- deliverable rate scale at a grid node
    def _g(self, e):
        """Delivery-pressure margin of an evaluation (None when a unit failed or the booster rating limits)."""
        if e.get("Pd") is None or e["k"] is None or not (e["ok"] or "below the minimum" in e["why"]):
            return None
        return e["Pd"] - self.P_min

    def _predict(self, ri, wi):
        """s* extrapolated from the two nearest exact nodes at higher reservoir pressure (same water level)."""
        ex = sorted((k[0], nd["s_star"]) for k, nd in self.nodes.items()
                    if k[1] == wi and nd["exact"] and nd["s_star"] and k[0] < ri)
        if not ex:
            return None
        if len(ex) == 1:
            return ex[-1][1] * (R_GRID[ri] / R_GRID[ex[-1][0]]) ** 1.5
        (i1, s1), (i2, s2) = ex[-2], ex[-1]
        r1, r2, r = R_GRID[i1], R_GRID[i2], R_GRID[ri]
        return max(s2 + (s2 - s1) * (r - r2) / (r2 - r1), 0.0)

    def node(self, ri, wi, w, s_plat, need_star=False):
        key = (ri, wi)
        nd = self.nodes.get(key)
        r = R_GRID[ri]
        if nd is None:
            nd = {"pts": [], "s_star": None, "exact": False, "why": ""}
            self.nodes[key] = nd
            e = self.evaluate(r, s_plat, w)
            nd["pts"].append((s_plat, e))
            if e["ok"]:
                nd["s_star"] = s_plat                      # at least the plateau
            else:
                nd["why"] = e["why"]
                self._root(nd, ri, wi, w, s_plat, hi=s_plat)
        if need_star and not nd["exact"] and nd["s_star"] is not None and nd["s_star"] >= s_plat:
            s, last_ok = s_plat, s_plat
            for _ in range(5):
                s *= 1.12
                e = self.evaluate(r, s, w)
                nd["pts"].append((s, e))
                if not e["ok"]:
                    nd["why"] = e["why"]
                    self._root(nd, ri, wi, w, s_plat, lo=last_ok, hi=s)
                    break
                last_ok = s
            else:
                nd["s_star"], nd["exact"] = last_ok, True
        return nd

    def _root(self, nd, ri, wi, w, s_plat, lo=None, hi=None):
        """Largest feasible rate scale between lo (feasible) and hi (infeasible). Without lo, search downwards from
        a prediction; a delivery pressure that falls as the rate falls means the left (liquid-loading) branch of the
        U-shaped system curve: nothing below is feasible either."""
        r = R_GRID[ri]
        e_hi = self.evaluate(r, hi, w)
        if lo is None:
            pred = self._predict(ri, wi)
            s = min(hi * 0.9, pred * 1.02) if pred else hi * 0.7
            s = max(s, 0.02 * s_plat)
            g_prev = self._g(e_hi)
            while True:
                e = self.evaluate(r, s, w)
                nd["pts"].append((s, e))
                if e["ok"]:
                    lo = s
                    break
                gs = self._g(e)
                nd["why"] = e["why"] or nd["why"]
                if gs is not None and g_prev is not None and gs < g_prev - 0.2:
                    nd["s_star"], nd["exact"] = 0.0, True          # left branch: lower rates arrive even lower
                    return
                if gs is not None and g_prev is not None and g_prev != gs:
                    s_new = s - gs * (hi - s) / (g_prev - gs) if hi != s else s * 0.7   # secant towards g = 0
                    s_new = min(max(s_new, s * 0.4), s * 0.9)
                else:
                    s_new = s * 0.6
                hi, e_hi, g_prev = s, e, gs
                s = s_new
                if s < 0.01 * s_plat:
                    nd["s_star"], nd["exact"] = 0.0, True
                    return
        e_lo = self.evaluate(r, lo, w)
        g_lo, g_hi = self._g(e_lo), self._g(e_hi)
        side = 0
        for _ in range(8):
            if (hi - lo) / hi < 0.03 or (g_lo is not None and g_lo < 0.3):
                break
            if g_lo is not None and g_hi is not None and g_lo != g_hi:
                s = hi - g_hi * (hi - lo) / (g_hi - g_lo)
                s = min(max(s, lo + 0.05 * (hi - lo)), hi - 0.05 * (hi - lo))
            else:
                s = 0.5 * (lo + hi)
            e = self.evaluate(r, s, w)
            nd["pts"].append((s, e))
            gs = self._g(e)
            if e["ok"]:
                lo, g_lo = s, gs
                if side == 1 and g_hi is not None:
                    g_hi *= 0.5
                side = 1
            else:
                hi, g_hi = s, gs
                nd["why"] = e["why"]
                if side == -1 and g_lo is not None:
                    g_lo *= 0.5
                side = -1
        nd["s_star"], nd["exact"] = lo, True

    def deliverable(self, r, w, w_grid, s_plat):
        """(s_max, limiting reason, kpis at s_max-ish nodes) at reservoir ratio r and water level w."""
        r = min(max(r, R_GRID[-1]), 1.0)
        i = next(k for k in range(len(R_GRID) - 1) if R_GRID[k + 1] <= r) if r < 1.0 else 0
        j = min(i + 1, len(R_GRID) - 1)
        fr = _snap(0.0 if i == j or r >= 1.0 else (R_GRID[i] - r) / (R_GRID[i] - R_GRID[j]))
        wi, wj, fw = _bracket(w, w_grid)
        fw = _snap(fw)
        out, why = 0.0, ""
        for (ri, rf) in ((i, 1 - fr), (j, fr)):
            if rf <= 0:
                continue
            for (wk, wf) in ((wi, 1 - fw), (wj, fw)):
                if wf <= 0:
                    continue
                nd = self.node(ri, wk, w_grid[wk], s_plat)
                out += rf * wf * (nd["s_star"] or 0.0)
                if nd["why"]:
                    why = nd["why"]
        if out < s_plat * 0.999:
            # off plateau: interpolate between exact s* values (the plateau nodes need theirs too)
            out, why = 0.0, ""
            for (ri, rf) in ((i, 1 - fr), (j, fr)):
                if rf <= 0:
                    continue
                for (wk, wf) in ((wi, 1 - fw), (wj, fw)):
                    if wf <= 0:
                        continue
                    nd = self.node(ri, wk, w_grid[wk], s_plat, need_star=True)
                    out += rf * wf * (nd["s_star"] or 0.0)
                    why = nd["why"] or why
        return out, why

    def kpi_at(self, r, w, w_grid, s):
        """KPIs interpolated over the visited nodes and, within a node, linearly in s through the solved points."""
        r = min(max(r, R_GRID[-1]), 1.0)
        i = next(k for k in range(len(R_GRID) - 1) if R_GRID[k + 1] <= r) if r < 1.0 else 0
        j = min(i + 1, len(R_GRID) - 1)
        fr = _snap(0.0 if i == j or r >= 1.0 else (R_GRID[i] - r) / (R_GRID[i] - R_GRID[j]))
        wi, wj, fw = _bracket(w, w_grid)
        fw = _snap(fw)
        acc, wsum = {}, 0.0
        for (ri, rf) in ((i, 1 - fr), (j, fr)):
            for (wk, wf) in ((wi, 1 - fw), (wj, fw)):
                f = rf * wf
                nd = self.nodes.get((ri, wk))
                if f <= 0 or nd is None:
                    continue
                pts = sorted((x, e["k"]) for x, e in nd["pts"] if e["k"] is not None and e.get("Pd") is not None)
                if not pts:
                    continue
                k = _interp_k(pts, s)
                for key, v in k.items():
                    if v is not None:
                        acc[key] = acc.get(key, 0.0) + f * v
                wsum += f
        return {k: v / wsum for k, v in acc.items()} if wsum > 0 else {}


def reason(why, P_min):
    """Short name of the constraint that limits the deliverable rate."""
    if not why:
        return "network deliverability"
    if "below the minimum" in why:
        return f"minimum delivery pressure ({P_min:.0f} bar)"
    if "rating" in why:
        return "booster power rating"
    unit = why.split(":")[0]
    return f"{unit} cannot pass more flow"


def _snap(f):
    """Interpolation weight snapped to the nearer node within 1 % (avoids solving a node that hardly counts)."""
    return 0.0 if f < 0.01 else (1.0 if f > 0.99 else f)


def _bracket(w, grid):
    if len(grid) == 1 or w <= grid[0]:
        return 0, 0, 0.0
    for k in range(len(grid) - 1):
        if grid[k] <= w <= grid[k + 1]:
            return k, k + 1, (w - grid[k]) / (grid[k + 1] - grid[k]) if grid[k + 1] > grid[k] else 0.0
    return len(grid) - 1, len(grid) - 1, 0.0


_SCALES = ("Power demand [kW]", "Energy + CO₂ cost [cur/h]", "CO₂ [t/h]", "Booster power [kW]", "Heating power [kW]")


def _interp_k(pts, s):
    xs = [p[0] for p in pts]
    if s <= xs[0]:
        x0, k0 = pts[0]
        f = s / x0 if x0 > 0 else 1.0
        return {key: (v * f if key in _SCALES else v) for key, v in k0.items() if v is not None}
    if s >= xs[-1]:
        return dict(pts[-1][1])
    for (x0, k0), (x1, k1) in zip(pts[:-1], pts[1:]):
        if x0 <= s <= x1:
            f = (s - x0) / (x1 - x0) if x1 > x0 else 0.0
            return {key: (k0[key] + f * (k1[key] - k0[key]) if k0.get(key) is not None and k1.get(key) is not None
                          else k0.get(key)) for key in k0}
    return dict(pts[-1][1])


# ------------------------------------------------------------------------------------------------ the reservoir

class Tank:
    """Compositional tank (EOS) with rock/water compressibility, pot aquifer and voidage-replacement injection."""

    def __init__(self, fp, z, T, Pi, kind, in_place, p):
        self.fp, self.T, self.Pi, self.kind = fp, T, Pi, kind
        self.z = np.asarray(z, float) / np.sum(z)
        self.p = Pi
        self.Swc, self.cf, self.cw = float(p["Swc"]), float(p["cf"]), float(p["cw"])
        self.ce = (self.cf + self.cw * self.Swc) / (1.0 - self.Swc)
        self.Sgc, self.Slc, self.ng, self.nl = float(p["Sgc"]), float(p["Slc"]), float(p["ng"]), float(p["nl"])
        self.K = None
        fr = self.flash(Pi, self.z)
        self.v_i = self._vol(fr)
        g, o = self.std_per_mol(self.z)
        prim = g if kind == GAS else o
        if prim <= 0:
            raise FieldLifeError(f"the reservoir fluid gives no {'gas' if kind == GAS else 'oil'} at standard "
                                 "conditions - check the reservoir type")
        self.n = in_place / prim                     # mol
        self.n_i = self.n
        self.HCPV_i = self.n * self.v_i              # m³
        self.PV_i = self.HCPV_i / (1.0 - self.Swc)
        self.W_aq = float(p["aquifer"]) * self.PV_i
        self.Winj = self.Wp = 0.0                    # cumulative reservoir m³ (surface m³ × Bw)
        self.in_place = in_place
        self.Psat = saturation_pressure(fp, self.z, T, Pi)

    def flash(self, P, z):
        fr = self.fp.pt_flash(z, self.T, P, self.K)
        if fr.Kset is not None:
            self.K = fr.Kset
        return fr

    @staticmethod
    def _vol(fr):
        return sum(ph.beta * ph.Vs for ph in fr.phases)

    def std_per_mol(self, y):
        """(gas Sm³, oil Sm³) per mol of composition y at standard conditions."""
        g, o, w = surf.standard_rates(make_stream("", self.fp, 1.0, y, None), self.fp)    # per kmol/h
        return g * 1e6 / 24.0 / 1000.0, (o + w) / 24.0 / 1000.0

    def We(self, P):
        return self.W_aq * (self.cw + self.cf) * max(self.Pi - P, 0.0)

    def produced_composition(self, P=None):
        """Mobility-weighted composition of the fluid leaving the tank and the gas saturation (HC pore)."""
        fr = self.flash(P or self.p, self.z)
        v = fr.phase("V")
        liqs = [ph for ph in fr.phases if ph.kind != "V"]
        if v is None or not liqs:
            return self.z.copy(), (1.0 if v is not None else 0.0)
        l = max(liqs, key=lambda ph: ph.beta)
        Vg, Vl = v.beta * v.Vs, l.beta * l.Vs
        Sg = Vg / (Vg + Vl)
        span = max(1.0 - self.Sgc - self.Slc, 1e-6)
        krg = min(max((Sg - self.Sgc) / span, 0.0), 1.0) ** self.ng
        krl = min(max((1.0 - Sg - self.Slc) / span, 0.0), 1.0) ** self.nl
        mg = krg / max(phase_viscosity_cP(self.fp, v, self.T), 1e-6)
        ml = krl / max(phase_viscosity_cP(self.fp, l, self.T), 1e-6)
        if mg + ml <= 0:
            return self.z.copy(), Sg
        qg, ql = mg / (mg + ml) / v.Vs, ml / (mg + ml) / l.Vs        # mol per m³ of reservoir flow
        y = (qg * v.x + ql * l.x) / (qg + ql)
        return y / y.sum(), Sg

    def solve_pressure(self):
        """Tank pressure for the current moles and water terms."""
        W_net = lambda P: (self.We(P) + self.Winj - self.Wp) * BW          # noqa: E731
        f = lambda P: self.n * self._vol(self.flash(P, self.z)) + W_net(P) - self.HCPV_i * (1.0 - self.ce * (self.Pi - P))  # noqa: E731
        lo, hi = 1.0, max(self.p * 1.05, self.Pi * 1.05)
        f_hi = f(hi)
        while f_hi > 0 and hi < 3.0 * self.Pi:
            hi *= 1.15
            f_hi = f(hi)
        a, fa = self.p, f(self.p)
        if fa > 0:
            lo_, flo = a, fa
            b, fb = hi, f_hi
        else:
            b, fb = a, fa
            lo_ = max(lo, a * 0.85)
            flo = f(lo_)
            while flo < 0 and lo_ > lo + 1e-9:
                b, fb = lo_, flo
                lo_ = max(lo, lo_ * 0.8)
                flo = f(lo_)
            if flo < 0:
                self.p = lo
                return lo
        # Illinois on [lo_ (f > 0), b (f < 0)]
        side = 0
        for _ in range(60):
            P = b - fb * (b - lo_) / (fb - flo) if fb != flo else 0.5 * (lo_ + b)
            fP = f(P)
            if abs(fP) < 1e-7 * self.HCPV_i or abs(b - lo_) < 1e-6:
                break
            if fP > 0:
                lo_, flo = P, fP
                if side == 1:
                    fb *= 0.5
                side = 1
            else:
                b, fb = P, fP
                if side == -1:
                    flo *= 0.5
                side = -1
        self.p = P
        return P


def saturation_pressure(fp, z, T, P_hi):
    """Bubble / upper dew point at T below P_hi (None if the fluid stays single phase down to 1 bar)."""
    def two(P):
        try:
            return len([ph for ph in fp.pt_flash(z, T, P).phases if ph.kind != "W"]) > 1
        except FlashError:
            return False
    if two(P_hi):
        return P_hi
    P_prev = P_hi
    P = P_hi
    while P > 1.5:
        P = max(P * 0.9, 1.0)
        if two(P):
            lo, hi = P, P_prev
            for _ in range(25):
                mid = 0.5 * (lo + hi)
                if two(mid):
                    lo = mid
                else:
                    hi = mid
            return 0.5 * (lo + hi)
        P_prev = P
    return None


def water_level(RF, w0, p):
    """Water cut (fraction, oil) or WGR (Sm³/MSm³, gas) at recovery factor RF."""
    wmax = float(p["w_max"]) / (100.0 if p["_kind"] == OIL else 1.0)
    if RF <= float(p["RF_bt"]) or wmax <= w0:
        return w0
    x = min(1.0, (RF - float(p["RF_bt"])) / max(float(p["RF_rise"]), 1e-6))
    return w0 + (wmax - w0) * x ** 0.7


# --------------------------------------------------------------------------------------------------- the run

def setup(model, sol, p=None):
    """Everything a run needs from the flowsheet (fast; no extra solves)."""
    if sol is None:
        raise FieldLifeError("solve the flowsheet first")
    bad = [model["units"][u]["name"] for u, v in sol.status.items() if v in FAILED]
    if bad:
        raise FieldLifeError(f"the flowsheet must solve cleanly first ({', '.join(bad[:4])} not solved)")
    p = params(model) if p is None else p
    fp = sol.fp
    feeds = well_feeds(model, sol, fp)
    kind = p["kind"]
    if kind == "Auto":
        kind = GAS if any(model["units"][w]["params"].get("ipr", surf.IPR_GAS) == surf.IPR_GAS for w, *_ in feeds) else OIL
    q = resolved(p, kind)
    q["_kind"] = kind
    F_hc = sum(d["F_hc"] for *_, d in feeds)
    z_hc = sum(d["z_hc"] * d["F_hc"] for *_, d in feeds) / F_hc
    Pi = sum(d["P"] * d["F_hc"] for *_, d in feeds) / F_hc
    T = sum(d["T"] * d["F_hc"] for *_, d in feeds) / F_hc
    gas = sum(d["gas"] for *_, d in feeds)
    oil = sum(d["oil"] for *_, d in feeds)
    wat = sum(d["wat"] for *_, d in feeds)
    prim = gas if kind == GAS else oil
    if prim <= 0:
        raise FieldLifeError(f"the wells produce no {'gas' if kind == GAS else 'oil'}: set the reservoir type")
    deliv = q["deliv"] if q.get("deliv") in model["streams"] else delivery_stream(model, sol)
    st = sol.streams[deliv]
    P_min = float(q["P_min"]) or max(5.0, round(0.5 * st.P))
    n0 = [d["n0"] for *_, d in feeds]
    N = int(round(float(q["n_wells"]))) or sum(n0)
    q_plat = float(q["q_plat"]) or prim
    w0 = (wat / (oil + wat) if oil + wat > 0 else 0.0) if kind == OIL else (wat / gas if gas > 0 else 0.0)
    in_place = float(q["in_place"])
    if in_place <= 0:            # automatic: about 7 (gas) / 5 (oil) plateau years
        in_place = q_plat * (1e6 if kind == GAS else 1.0) * 365.0 * q["uptime"] / 100.0 * (7.0 / 0.45 if kind == GAS else 5.0 / 0.2)
        auto_ip = True
    else:
        in_place = in_place * (1e9 if kind == GAS else 1e6)        # GSm³ -> Sm³ ; MSm³ -> Sm³
        auto_ip = False
    has_boost = any(u["type"] in surf.BOOSTER_TYPES for u in model["units"].values())
    return {"p": q, "kind": kind, "fp": fp, "feeds": feeds, "z_hc": z_hc, "Pi": Pi, "T": T, "prim0": prim,
            "gas0": gas, "oil0": oil, "wat0": wat, "deliv": deliv, "deliv_name": model["streams"][deliv]["name"],
            "P_min": P_min, "N": N, "n0": n0, "q_plat": q_plat, "w0": w0, "in_place": in_place,
            "in_place_auto": auto_ip, "has_boost": has_boost, "model": model}


def _w_grid(S):
    p = S["p"]
    wmax = float(p["w_max"]) / (100.0 if S["kind"] == OIL else 1.0)
    if float(p["RF_bt"]) >= 1.0 or wmax <= S["w0"] + 1e-9:
        return [S["w0"]]
    fr = (0.0, 0.25, 0.45, 0.6, 0.72, 0.82, 0.9, 0.96, 1.0) if S["kind"] == OIL else (0.0, 0.25, 0.5, 0.75, 1.0)
    return [S["w0"] + f * (wmax - S["w0"]) for f in fr]


def run(model, sol, p=None, N=None, boost_mode=None, progress=None, S=None, networks=None):
    """March the field through time. Returns a result dict (yearly table, summary, events)."""
    S = S or setup(model, sol, p)
    p = S["p"]
    kind, fp = S["kind"], S["fp"]
    N = int(N or S["N"])
    mode = boost_mode or p["boost_mode"]
    if not S["has_boost"]:
        mode = B_AS_IS
    n_cfg = distribute(N, S["n0"])
    networks = networks if networks is not None else {}

    def net(on):
        key = (N, on if mode != B_AS_IS else None)
        if key not in networks:
            networks[key] = Network(model, fp, S["feeds"], S["deliv"], S["P_min"], n_cfg,
                                    None if mode == B_AS_IS else on, kind, bool(p["rating"]), progress)
        return networks[key]

    tank = Tank(fp, S["z_hc"], S["T"], S["Pi"], kind, S["in_place"], p)
    w_grid = _w_grid(S)
    dt = 0.25
    years = float(p["years"])
    up = float(p["uptime"]) / 100.0
    rig = float(p["rig"])
    boost_on = mode == B_AS_IS or (mode == B_YEAR and float(p["boost_year"]) <= 1.0)
    boost_start = 1.0 if (mode == B_YEAR and boost_on) else None
    if mode == B_AS_IS and S["has_boost"]:
        boost_start = 1.0
    steps, events = [], []
    cum_p = cum_o = cum_g = cum_w = cum_inj = 0.0
    plateau_end = None
    t = 0.0
    sat_note = False
    stop_reason = f"end of the {years:.0f}-year period"
    bt_done = False
    while t < years - 1e-9:
        year = int(t) + 1
        if mode == B_YEAR and not boost_on and year >= float(p["boost_year"]):
            boost_on, boost_start = True, float(year)
            events.append((t, f"Subsea boosting starts (year {year})"))
        RF = cum_p / S["in_place"]
        w = water_level(RF, S["w0"], p)
        if not bt_done and RF > float(p["RF_bt"]) and len(w_grid) > 1:
            bt_done = True
            events.append((t, f"Water breakthrough (recovery factor {100 * RF:.1f} %)"))
        r = tank.p / S["Pi"]
        nw = net(boost_on if mode != B_AS_IS else None)
        s_plat = S["q_plat"] / nw.q_unit
        k_online = N if rig <= 0 else min(N, max(1, int(math.ceil(rig * (t + dt) - 1e-9))))
        s_max, why = nw.deliverable(r, w, w_grid, s_plat)
        if mode == B_AUTO and not boost_on and s_max * k_online / N < s_plat * 0.999:
            nb = net(True)
            s_b, why_b = nb.deliverable(r, w, w_grid, s_plat)
            if s_b > s_max * 1.01:
                boost_on, boost_start = True, t + 1.0
                events.append((t, f"Subsea boosting starts automatically (year {year}): the plateau can no longer "
                                  f"be held without it"))
                nw, s_max, why = nb, s_b, why_b
        s_max *= k_online / N
        if s_max >= 0.995 * s_plat:
            s_max = max(s_max, s_plat)
        lim = [(s_plat, "plateau (facility) rate")]
        if s_max < s_plat:
            lim = [(s_max, reason(why, S["P_min"]))]
        wcap, lcap = float(p["water_cap"]), float(p["liq_cap"])
        if kind == OIL:
            if wcap > 0 and w > 0:
                lim.append((wcap * (1 - w) / (w * nw.q_unit), "water handling capacity"))
            if lcap > 0:
                lim.append((lcap * (1 - w) / nw.q_unit, "liquid capacity"))
        else:
            if wcap > 0 and w > 0:
                lim.append((wcap / (w * nw.q_unit), "water handling capacity"))
        s, limiter = min(lim, key=lambda x: x[0])
        if k_online < N and limiter.startswith("plateau") is False and s >= s_max - 1e-12:
            limiter = f"wells online ({k_online}/{N})"
        q = s * nw.q_unit                                     # primary product, stream-day
        y, Sg = tank.produced_composition()
        g_mol, o_mol = tank.std_per_mol(y)
        prim_mol = g_mol if kind == GAS else o_mol
        if s <= 1e-6 or prim_mol <= 1e-12:
            stop_reason = "the wells can no longer flow against the minimum delivery pressure" if s <= 1e-6 \
                else "the reservoir no longer produces the primary product"
            break
        days = 365.25 * dt * up
        prim_vol = q * (1e6 if kind == GAS else 1.0) * days      # Sm³ in the step
        dn = prim_vol / prim_mol
        if dn >= tank.n * 0.95:
            stop_reason = "reservoir exhausted"
            break
        gas_vol, oil_vol = dn * g_mol, dn * o_mol
        if kind == OIL:
            wat_vol = w / max(1 - w, 1e-6) * oil_vol
        else:
            wat_vol = w * gas_vol / 1e6
        v_res = dn * tank._vol(tank.flash(tank.p, y)) + wat_vol * BW
        inj = float(p["VRR"]) * v_res / BW if kind == OIL else 0.0
        z_new = tank.n * tank.z - dn * y
        tank.n -= dn
        tank.z = np.maximum(z_new, 0.0) / np.maximum(z_new, 0.0).sum()
        tank.Wp += wat_vol
        tank.Winj += inj
        P_new = tank.solve_pressure()
        k = nw.kpi_at(r, w, w_grid, s)
        cum_p += prim_vol
        cum_g += gas_vol
        cum_o += oil_vol
        cum_w += wat_vol
        cum_inj += inj
        if plateau_end is None and s < s_plat * 0.985 and t > 0:
            plateau_end = t
            events.append((t, f"Plateau ends (year {year}): {limiter}"))
        if not sat_note and tank.Psat and P_new < tank.Psat:
            sat_note = True
            events.append((t, f"Reservoir pressure falls below the {'dew' if kind == GAS else 'bubble'} point "
                              f"({tank.Psat:.0f} bar)"))
        hours = 8760.0 * dt * up
        inj_kW = 0.0
        if inj > 0:
            inj_kW = inj / (365.25 * dt * up * 86400.0) * (250.0 - 5.0) * 1e5 / 0.75 / 1000.0
        steps.append({"t": t, "year": year, "s": s, "s_max": s_max, "s_plat": s_plat, "limiter": limiter,
                      "P_res": tank.p, "r": r, "gas": gas_vol / (days * 1e6), "oil": oil_vol / days,
                      "water": wat_vol / days, "inj": inj / days, "w": w, "Sg": Sg, "RF": cum_p / S["in_place"],
                      "wells": k_online, "boost": bool(boost_on and S["has_boost"]),
                      "power_kW": float(k.get("Power demand [kW]", 0.0)) + inj_kW,
                      "energy_cur": float(k.get("Energy + CO₂ cost [cur/h]", 0.0)) * hours,
                      "co2_t": float(k.get("CO₂ [t/h]", 0.0)) * hours, "inj_kW": inj_kW,
                      "P_deliv": k.get("Delivery P [bar(a)]"), "hyd": k.get("Min. hydrate margin [°C]"),
                      "boost_kW": float(k.get("Booster power [kW]", 0.0)), "heat_kW": float(k.get("Heating power [kW]", 0.0)),
                      "gas_vol": gas_vol, "oil_vol": oil_vol, "wat_vol": wat_vol, "days": days})
        t += dt
    if not steps:
        raise FieldLifeError("the field cannot produce: check the minimum delivery pressure and the plateau rate")
    res = {"steps": steps, "events": events, "stop": stop_reason, "N": N, "boost_mode": mode, "boost_start": boost_start,
           "plateau_end": plateau_end, "kind": kind, "Psat": tank.Psat, "Pi": S["Pi"], "in_place": S["in_place"],
           "n_solves": sum(n.n_solves for n in networks.values()), "n_cfg": n_cfg}
    economics(res, S, model, sol)
    return res


# ----------------------------------------------------------------------------------------------- economics

def capex_split(model, sol, S, N, n_cfg, boost_later):
    """(facility MUSD, per-well MUSD, booster MUSD, extra templates) for N wells."""
    from . import subsea_design as sd
    m = copy.deepcopy(model)
    for (wid, *_), n in zip(S["feeds"], n_cfg):
        m["units"][wid]["params"]["n_par"] = max(1, n)
    items, tot = sd.equipment_list(m, sol)
    cp = sd.capex_params(m)
    mult = (1.0 + cp["eng_pct"] / 100.0) * (1.0 + cp["cont_pct"] / 100.0)
    total = tot["Total CAPEX [MUSD]"]
    wells = sum(i["Total [MUSD]"] for i in items if i["Group"] == "Wells") * (1.0 + cp["cont_pct"] / 100.0)
    boost = 0.0
    if boost_later:
        boost = sum(i["Total [MUSD]"] for i in items if i["Group"] == "Subsea boosting"
                    or i["Item"] in ("Power cable", "Topside power")) * mult
    slots = 0
    slot_unit = None
    for u in m["units"].values():
        if u["type"] == "template":
            try:
                row = surf.item("template", u["params"].get("template"))
                slots += int(row.get("slots") or 0)
                slot_unit = slot_unit or row
            except Exception:                                    # noqa: BLE001
                pass
    extra_t = 0
    extra = 0.0
    if slot_unit and N > slots:
        per = int(slot_unit.get("slots") or 4)
        extra_t = int(math.ceil((N - slots) / per))
        extra = extra_t * float(slot_unit.get("cost_MUSD") or 0.0) * (1.0 + cp["install_pct"] / 100.0) * mult
    facility = total - wells - boost + extra
    return facility, wells / max(N, 1), boost, extra_t


def economics(res, S, model, sol):
    p = S["p"]
    N = res["N"]
    boost_later = res["boost_mode"] in (B_YEAR, B_AUTO)
    facility, per_well, boost_capex, extra_t = capex_split(model, sol, S, N, res["n_cfg"], boost_later)
    fe = float(p.get("fac_exp", 0.0) or 0.0)
    if fe > 0 and S["prim0"] > 0 and S["q_plat"] > 0:      # six-tenths-rule style scaling with the plateau rate
        facility *= (S["q_plat"] / S["prim0"]) ** fe
    res["capex"] = {"Facilities [MUSD]": facility, "Wells [MUSD]": per_well * N, "Boosting (when installed) [MUSD]":
                    boost_capex if res["boost_start"] and boost_later else 0.0, "Extra templates": extra_t}
    years = sorted({s["year"] for s in res["steps"]})
    rows = []
    gp, op = float(p["gas_price"]), float(p["oil_price"])
    fx = max(float(p["fx"]), 1e-9)
    d = float(p["disc"]) / 100.0
    rig = float(p["rig"])
    ny = int(round(float(p["capex_years"])))
    # pre-production years
    for k in range(ny, 0, -1):
        cap = facility / ny if ny else 0.0
        if k == 1 and rig <= 0:
            cap += per_well * N
        rows.append({"Year": 1 - k, "CAPEX [MUSD]": cap})
    if ny == 0:
        rows.append({"Year": 0, "CAPEX [MUSD]": facility + (per_well * N if rig <= 0 else 0.0)})
    wells_prev = 0
    for y in years:
        st = [s for s in res["steps"] if s["year"] == y]
        days = sum(s["days"] for s in st)
        cal = 365.25 * len(st) * 0.25
        g = sum(s["gas_vol"] for s in st)
        o = sum(s["oil_vol"] for s in st)
        wv = sum(s["wat_vol"] for s in st)
        rev = (g * gp + o * 6.28981 * op) / 1e6
        energy = sum(s["energy_cur"] for s in st) / fx / 1e6
        co2 = sum(s["co2_t"] for s in st)
        if any(s["inj_kW"] for s in st):     # water-injection power at the grid price of the economics settings
            from . import economics as ec
            ep = ec.params(model)
            mwh = sum(s["inj_kW"] * 8760 * 0.25 * float(p["uptime"]) / 100.0 for s in st) / 1000.0
            energy += mwh * (float(ep["el_price"]) + float(ep["grid_co2"]) * float(ep["co2_tax"])) / fx / 1e6
            co2 += mwh * float(ep["grid_co2"])
        opex = float(p["opex_pct"]) / 100.0 * (facility + per_well * N) + energy
        cap = 0.0
        wells_now = max(s["wells"] for s in st)
        if rig > 0 and wells_now > wells_prev:
            cap += per_well * (wells_now - wells_prev)
        wells_prev = wells_now
        rows.append({"Year": y, "Gas [MSm³/d]": g / 1e6 / cal, "Oil/condensate [Sm³/d]": o / cal,
                     "Water [Sm³/d]": wv / cal, "Injection [Sm³/d]": sum(s["inj"] * s["days"] for s in st) / cal,
                     "Reservoir P [bar(a)]": st[-1]["P_res"], "Delivery P [bar(a)]": st[-1]["P_deliv"],
                     "Water cut / WGR": st[-1]["w"] * (100.0 if S["kind"] == OIL else 1.0),
                     "Wells online": wells_now, "Boosting": "on" if st[-1]["boost"] else "off",
                     "Limited by": st[-1]["limiter"], "Power [MW]": sum(s["power_kW"] for s in st) / len(st) / 1000.0,
                     "CO₂ [kt/y]": co2 / 1000.0, "Recovery factor [%]": 100.0 * st[-1]["RF"],
                     "Revenue [MUSD]": rev, "OPEX [MUSD]": opex, "CAPEX [MUSD]": cap, "_gas": g, "_oil": o, "_days": days})
    if boost_later and res["boost_start"]:                 # boosting installed the year before it starts
        yb = int(res["boost_start"]) - 1
        row = next((r for r in rows if r["Year"] == yb), None)
        if row is None:
            row = {"Year": yb, "CAPEX [MUSD]": 0.0}
            rows.append(row)
            rows.sort(key=lambda r: r["Year"])
        row["CAPEX [MUSD]"] = row.get("CAPEX [MUSD]", 0.0) + boost_capex
    # economic cut-off: stop before the first producing year after the plateau whose operating cash flow is negative
    cut = None
    prod_rows = [r for r in rows if r["Year"] >= 1]
    for r in prod_rows:
        if r["Revenue [MUSD]"] - r["OPEX [MUSD]"] < 0 and (res["plateau_end"] is not None or r["Year"] > 1):
            cut = r["Year"]
            break
    if cut is not None:
        rows = [r for r in rows if r["Year"] < cut]
        res["stop"] = f"economic limit in year {cut} (operating cash flow turns negative)"
    cum = disc_cum = 0.0
    for r in rows:
        r.setdefault("Revenue [MUSD]", 0.0)
        r.setdefault("OPEX [MUSD]", 0.0)
        r["Cash flow [MUSD]"] = r["Revenue [MUSD]"] - r["OPEX [MUSD]"] - r["CAPEX [MUSD]"]
        r["Discounted [MUSD]"] = r["Cash flow [MUSD]"] / (1.0 + d) ** (r["Year"] - 0.5 if r["Year"] >= 1 else r["Year"])
        cum += r["Cash flow [MUSD]"]
        disc_cum += r["Discounted [MUSD]"]
        r["Cumulative cash [MUSD]"] = cum
    prod = [r for r in rows if r["Year"] >= 1]
    last = prod[-1] if prod else None
    gp_tot = sum(r["_gas"] for r in prod)
    op_tot = sum(r["_oil"] for r in prod)
    prim_tot = gp_tot if S["kind"] == GAS else op_tot
    boe = gp_tot / 1000.0 * 6.28981 + op_tot * 6.28981
    capex_tot = sum(r["CAPEX [MUSD]"] for r in rows)
    opex_tot = sum(r["OPEX [MUSD]"] for r in rows)
    payback = next((r["Year"] for r in rows if r["Cumulative cash [MUSD]"] >= 0 and r["Year"] >= 1), None)
    plat_years = (res["plateau_end"] if res["plateau_end"] is not None else (last["Year"] if last else 0))
    res["annual"] = rows
    res["summary"] = {
        "Producing wells": N, "Plateau length [years]": plat_years,
        "Production years": last["Year"] if last else 0,
        "Recovery factor [%]": 100.0 * prim_tot / S["in_place"],
        "Cumulative gas [GSm³]": gp_tot / 1e9, "Cumulative oil/condensate [MSm³]": op_tot / 1e6,
        "CAPEX [MUSD]": capex_tot, "OPEX [MUSD]": opex_tot, "NPV [MUSD]": disc_cum,
        "IRR [%]": irr([r["Cash flow [MUSD]"] for r in rows]), "Payback year": payback,
        "Unit cost [USD/boe]": (capex_tot + opex_tot) * 1e6 / boe if boe > 0 else None,
        "CO₂ intensity [kg/boe]": (sum(r.get("CO₂ [kt/y]", 0.0) for r in prod) * 1e6 / boe) if boe > 0 else None,
        "Abandonment": res["stop"],
        "Boosting starts (year)": res["boost_start"] if res["boost_mode"] != B_NEVER else None,
    }


def irr(cash):
    """Internal rate of return [%] by bisection (None if the cash flows never change sign)."""
    def npv(rate):
        return sum(c / (1.0 + rate) ** k for k, c in enumerate(cash))
    if not cash or npv(0.0) <= 0 or min(cash) >= 0:
        return None
    lo, hi = 0.0, 5.0
    if npv(hi) > 0:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return 100.0 * 0.5 * (lo + hi)


# ------------------------------------------------------------------------------------------------ optimisation

def default_counts(S):
    N0 = S["N"]
    k = len(S["feeds"])
    c = sorted({max(1, int(round(N0 * f))) for f in (0.5, 0.75, 1.0, 1.5, 2.0)} | {max(k, 1)})
    return [n for n in c if n >= 1][:6]


def _case(args):
    """One sweep case in a worker process (re-solves the base flowsheet there)."""
    model, p, N, mode = args
    sol = solve(model)
    return (N, mode), run(model, sol, p=p, N=N, boost_mode=mode)


def sweep(model, sol, counts=None, modes=None, p=None, progress=None, case_progress=None, workers=None):
    """Run the field life for several well counts (and boosting modes). Returns (rows, results by key, best key).

    With several CPU cores the cases run in parallel worker processes (``workers``: None = all cores, 1 = serial);
    any failure of the process pool falls back to running the remaining cases here."""
    import os
    S = setup(model, sol, p)
    counts = counts or default_counts(S)
    modes = modes or [S["p"]["boost_mode"] if S["has_boost"] else B_AS_IS]
    cases = [(N, mode) for mode in modes for N in counts]
    rows, results = [], {}
    networks = {}
    workers = workers or min(len(cases), os.cpu_count() or 1)
    if workers > 1 and len(cases) > 1:
        try:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor, as_completed
            ctx = mp.get_context("fork")
            with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
                futs = [ex.submit(_case, (model, S["p"], N, mode)) for N, mode in cases]
                for f in as_completed(futs):
                    key, r = f.result()
                    results[key] = r
                    if case_progress:
                        case_progress(len(results), len(cases))
        except Exception:                                          # noqa: BLE001  (no fork, pickling, pool died)
            pass
    for N, mode in cases:
        if (N, mode) not in results:
            results[(N, mode)] = run(model, sol, N=N, boost_mode=mode, progress=progress, S=S, networks=networks)
            if case_progress:
                case_progress(len(results), len(cases))
    for N, mode in cases:
            r = results[(N, mode)]
            sm = r["summary"]
            rows.append({"Wells": N, "Boosting": mode, "Plateau [years]": sm["Plateau length [years]"],
                         "Production years": sm["Production years"], "Recovery factor [%]": sm["Recovery factor [%]"],
                         "NPV [MUSD]": sm["NPV [MUSD]"], "IRR [%]": sm["IRR [%]"], "CAPEX [MUSD]": sm["CAPEX [MUSD]"],
                         "Unit cost [USD/boe]": sm["Unit cost [USD/boe]"], "Boosting starts (year)": sm["Boosting starts (year)"]})
    best = max(results, key=lambda k: results[k]["summary"]["NPV [MUSD]"])
    return rows, results, best


def strategy_text(S, res, best_row=None):
    """A short drainage-strategy summary for the report and the tab."""
    sm = res["summary"]
    kind = S["kind"]
    p = S["p"]
    drive = ("depletion" if kind == GAS and float(p["aquifer"]) <= 0 else
             "depletion with aquifer support" if kind == GAS else
             f"water injection (voidage replacement {float(p['VRR']):.2f})" if float(p["VRR"]) > 0 else
             "depletion (solution-gas drive below the bubble point)" + (" with aquifer support" if float(p["aquifer"]) > 0 else ""))
    unit = "MSm³/d" if kind == GAS else "Sm³/d"
    txt = [f"**Drainage strategy:** {kind.lower()} reservoir produced by {drive}, {sm['Producing wells']} producers, "
           f"plateau {S['q_plat']:.3g} {unit} held for {sm['Plateau length [years]']:.1f} years, "
           f"{sm['Production years']} production years to {sm['Abandonment']}."]
    txt.append(f"**Recovery factor:** {sm['Recovery factor [%]']:.1f} % of the "
               f"{'GIIP' if kind == GAS else 'STOIIP'} ({S['in_place'] / (1e9 if kind == GAS else 1e6):.3g} "
               f"{'GSm³' if kind == GAS else 'MSm³'}{', automatic estimate - enter your own' if S['in_place_auto'] else ''}).")
    if sm.get("Boosting starts (year)"):
        txt.append(f"**Subsea boosting** from year {sm['Boosting starts (year)']:.0f}.")
    eco = f"**Economics:** NPV {sm['NPV [MUSD]']:,.0f} MUSD at {float(p['disc']):.0f} %"
    if sm["IRR [%]"] is not None:
        eco += f", IRR {sm['IRR [%]']:.0f} %"
    if sm["Unit cost [USD/boe]"]:
        eco += f", unit cost {sm['Unit cost [USD/boe]']:.1f} USD/boe"
    txt.append(eco + ".")
    if best_row:
        txt.append(f"**Recommended:** {best_row['Wells']} wells ({best_row['Boosting'].lower()}) - the highest NPV of "
                   f"the cases run ({best_row['NPV [MUSD]']:,.0f} MUSD, recovery {best_row['Recovery factor [%]']:.1f} %).")
    return txt


def apply_wells(model, sol, N, p=None):
    """A copy of the flowsheet with N producing wells spread over the well units and the feed rates set so the total
    equals the plateau rate (the design point of N wells)."""
    S = setup(model, sol, p)
    n_cfg = distribute(int(N), S["n0"])
    kind = S["kind"]
    q_unit = sum((d["gas"] if kind == GAS else d["oil"]) * n / d["n0"] for (*_, d), n in zip(S["feeds"], n_cfg))
    s = S["q_plat"] / q_unit if q_unit > 0 else 1.0
    m = copy.deepcopy(model)
    for (wid, fid, sid, d), n in zip(S["feeds"], n_cfg):
        m["units"][wid]["params"]["n_par"] = max(1, n)
        fpar = m["units"][fid]["params"]
        fpar["flow"] = float(fpar.get("flow", 0.0)) * (n / d["n0"]) * s
    return m


def compact(res, S, sweep_rows=None, best=None):
    """JSON-safe summary of a run (yearly table, summary, events, strategy) to keep with the flowsheet."""
    def clean(v):
        if isinstance(v, (np.floating,)):
            return float(v)
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        return v
    best_row = None
    if sweep_rows and best:
        best_row = next((r for r in sweep_rows if r["Wells"] == best[0] and r["Boosting"] == best[1]), None)
    return clean({"kind": S["kind"], "annual": [{k: v for k, v in r.items() if not k.startswith("_")}
                                                for r in res["annual"]],
                  "summary": res["summary"], "events": [[t, e] for t, e in res["events"]], "capex": res["capex"],
                  "N": res["N"], "boost_mode": res["boost_mode"], "Psat": res["Psat"], "Pi": res["Pi"],
                  "P_min": S["P_min"], "q_plat": S["q_plat"], "in_place": S["in_place"], "n_solves": res["n_solves"],
                  "uptime": float(S["p"]["uptime"]),
                  "strategy": strategy_text(S, res, best_row), "sweep": sweep_rows or [],
                  "best": list(best) if best else None})
