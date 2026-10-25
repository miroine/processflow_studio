"""Scene description for the 3D plant view (v7.10).

``build_scene(model, sol)`` turns the flowsheet (as it stands in the selected phase stage) and its solution into plain JSON that
the Three.js component ``pfd_3d`` draws: equipment with a description and the key results, and pipes with service, nominal
size and an indicative velocity that drive the flow animation.

Layout: the 2D drawing is read as a plot plan - one drawing pixel is 8 cm on the ground.  Pipe sizes are *indicative*: the
smallest standard size that keeps the velocity near a typical design value for the service (gas 15 m/s, liquid 2.5 m/s,
two-phase 8 m/s); they are not a line-sizing result.  No Streamlit dependency."""
from __future__ import annotations

import math

from . import phasing as PH
from .streams import stream_properties

M_PER_PX = 0.06
STD_NPS_IN = [1, 1.5, 2, 3, 4, 6, 8, 10, 12, 14, 16, 20, 24, 30, 36, 42, 48]
LOGICAL = ("recycle", "adjust")

# type: (title, what it is, what the model does)
DESCR = {
    "feed": ("Feed / battery limit", "Where a stream enters the flowsheet: a well-stream header, an import line or a tie-in.", "Fixes composition, temperature, pressure and rate of the incoming stream."),
    "product": ("Product / export", "Where a stream leaves the flowsheet: an export pipeline, storage or a disposal line.", "Collects the stream; its rate and conditions are results of the process upstream."),
    "separator": ("Two-phase separator", "A vertical gravity vessel with an inlet device and a mist pad: gas leaves at the top, liquid at the bottom.", "Isothermal / isobaric PT flash of the feed; Souders-Brown gas velocity and the required diameter are reported."),
    "scrubber": ("Scrubber / knock-out drum", "A vertical vessel that removes liquid carry-over from a gas stream upstream of a compressor or a burner.", "Flash of the feed with a Souders-Brown check of the gas load against the selected diameter."),
    "separator3": ("Three-phase separator", "A horizontal vessel with a weir: gas, oil and water leave through separate nozzles after gravity settling.", "Three-phase flash; reports phase flows, vessel conditions and the gas-capacity diameter."),
    "phase_splitter": ("Inlet phase splitter", "An inlet separator that divides a well stream into gas, oil and produced water.", "Three-phase flash of the feed with the three product streams."),
    "deliquifier": ("Deliquifier / coalescing filter", "A filter-separator that removes free hydrocarbon and water droplets from a gas stream.", "Removal efficiency or carry-over in mg/Sm³, liquid load and Souders-Brown velocity."),
    "comp_splitter": ("Component splitter", "A simplified separation block that sends chosen components to the top or bottom stream.", "Specified recoveries per component - no internal hydraulics."),
    "column": ("Distillation / stabiliser column", "A trayed column with a reboiler (and condenser): light ends leave overhead, heavy ends at the bottom.", "Rigorous stage-by-stage solution with the reboiler duty, top and bottom temperatures and product rates."),
    "teg_contactor": ("TEG dehydration contactor", "A tall absorber where lean triethylene glycol meets the wet gas and takes up its water.", "Absorption-factor model with equilibrium water over the lean glycol; reports outlet water content and dew point."),
    "amine_contactor": ("Amine contactor", "A tall absorber where an amine solution removes CO₂ and H₂S from the gas.", "Equilibrium-limited absorption with rich / lean loading, circulation rate and regenerator duty."),
    "mol_sieve": ("Molecular-sieve dehydrator", "Two or more adsorbent beds: one dries the gas to a very low dew point while another is regenerated with hot gas.", "Bed sizing from a dynamic loading, regeneration gas and heater duty."),
    "membrane": ("Membrane unit", "Skid of hollow-fibre or spiral-wound elements where fast-permeating CO₂ leaves with the permeate.", "Permeance model: stage cut, CO₂ in retentate and permeate, methane loss, membrane area."),
    "valve": ("Control / letdown valve", "A throttling valve; the pressure is lowered at constant enthalpy.", "Isenthalpic flash with Joule-Thomson cooling and the required Cv / Kv."),
    "relief_valve": ("Pressure safety valve", "A spring-loaded valve that opens at the set pressure and discharges to the flare header.", "Relieving load and API 520/526 orifice selection with the capacity margin."),
    "mixer": ("Mixer / header", "A pipe junction where several streams are blended.", "Mass and energy balance; the outlet is flashed at the lowest inlet pressure."),
    "splitter": ("Splitter / tee", "A pipe tee that divides a stream into branches of identical composition.", "Fixed split fractions; no change in composition, T or P."),
    "pipe": ("Pipeline segment", "A section of pipe or flowline with elevation change and optional heat loss.", "Beggs & Brill multiphase hydraulics: pressure drop, holdup, flow regime and erosional velocity."),
    "compressor": ("Centrifugal compressor", "A motor- or turbine-driven centrifugal machine that raises the gas pressure.", "Polytropic / adiabatic efficiency, outlet temperature, head, power and the operating point on the map."),
    "expander": ("Turbo-expander", "A turbine that lets the gas down while recovering shaft power.", "Isentropic efficiency, outlet temperature (and possible condensation) and the recovered power."),
    "pump": ("Centrifugal pump", "A motor-driven pump that raises the pressure of a liquid.", "Hydraulic power from head and flow, efficiency, temperature rise."),
    "gas_turbine": ("Gas-turbine generator", "A package that burns fuel gas to drive a generator or compressor; the exhaust heat can be recovered.", "Fuel use, efficiency at part load, ambient de-rating, exhaust heat and CO₂."),
    "heater": ("Heater", "A heat exchanger or fired heater that raises the temperature of the stream.", "Duty or outlet temperature specification, pressure drop."),
    "cooler": ("Cooler", "A shell-and-tube exchanger that cools a stream with cooling water or seawater.", "Duty or outlet temperature specification, pressure drop."),
    "hx": ("Shell-and-tube exchanger", "A gas/gas or process/process exchanger: the hot stream heats the cold stream through the tube wall.", "Energy balance with terminal approaches, LMTD and UA."),
    "aircooler": ("Air cooler", "A bank of finned tube bundles cooled by fans on an elevated structure.", "Duty removed, outlet temperature, fan power estimate."),
    "chiller": ("Chiller", "A heat exchanger where a refrigerant evaporates to cool the process stream below ambient.", "Duty, outlet temperature, refrigeration power by Carnot fraction or an explicit cycle."),
    "conv_reactor": ("Conversion reactor", "A vessel where a specified conversion of a key component takes place.", "Stoichiometric reaction with conversion and heat of reaction."),
    "eq_reactor": ("Equilibrium reactor", "A vessel where a reaction reaches chemical equilibrium at the outlet temperature.", "Equilibrium constant, extent of reaction and duty."),
    "flare": ("Flare stack", "An elevated stack that burns relief and vent gas safely.", "Heat release, flame length and height, radiation at the receptor, tip Mach number, CO₂ emitted."),
    "platform": ("Platform / frame", "An outline that groups equipment on a deck or a module.", "Drawing aid only; it is not solved."),
    "well": ("Production well", "A well from the reservoir to the wellhead.", "Inflow performance and tubing hydraulics: bottom-hole pressure, rates, water cut, wellhead conditions."),
    "xmas_tree": ("Subsea Christmas tree", "The valve assembly on the wellhead; its choke sets the flow.", "Tree and choke pressure drop, Joule-Thomson cooling and the choke flow regime."),
    "template": ("Subsea template / manifold frame", "A steel frame with several well slots and a common header.", "Slot use, header pressure drop and erosional velocity."),
    "jumper": ("Jumper", "A short rigid or flexible pipe that connects subsea components.", "Friction, elevation and fittings pressure drop."),
    "flowline": ("Subsea flowline", "A pipeline on the seabed, bare, buried or pipe-in-pipe.", "Multiphase hydraulics with heat loss to the sea: arrival temperature, holdup, liquid inventory."),
    "riser": ("Riser", "The pipe that lifts the fluids from the seabed to the host platform.", "Static head, friction, riser-base slugging indicator and heat loss."),
    "subsea_booster": ("Subsea multiphase booster", "A helico-axial or twin-screw machine that boosts pressure on the seabed.", "Pressure boost from GVF-dependent efficiency, shaft power and temperature rise."),
    "subsea_pump": ("Subsea pump", "A liquid pump station on the seabed.", "Hydraulic power, efficiency and temperature rise."),
    "subsea_compressor": ("Subsea wet-gas compressor", "A compressor station on the seabed that tolerates liquid carry-over.", "Pressure boost, shaft power and outlet temperature."),
    "subsea_separator": ("Subsea separator", "A compact separator station on the seabed.", "Phase split, residence time and vessel size."),
    "subsea_cooler": ("Subsea cooler", "A seawater-cooled heat exchanger on the seabed.", "Duty to the sea, required area and hydrate margin."),
    "intensifier": ("Chemical intensifier", "A hydraulic pressure intensifier that injects chemicals against the line pressure.", "Pressure area ratio, hydraulic consumption and power."),
    "cimv": ("Chemical injection metering valve", "A valve that meters inhibitor (MEG, methanol) into the flow.", "Dosage, inhibited hydrate temperature and margin."),
    "injection_well": ("Injection well", "A well that injects water or gas into the reservoir.", "Required bottom-hole pressure, injection margin and the rate this wellhead pressure can take."),
    "subsea_valve": ("Subsea isolation valve", "A valve that can isolate a branch of the subsea system.", "Position and pressure drop."),
    "subsea_choke": ("Subsea choke module", "A choke valve on the seabed that controls the well rate.", "Cv at the opening, critical flow, Joule-Thomson cooling and hydrate margin."),
    "subsea_manifold": ("Subsea manifold", "A header that gathers the flow of several branches.", "Header velocity against the erosional limit and hydrate margin."),
    "mpfm": ("Multiphase flow meter", "An inline meter that measures gas, oil and water rates without separation.", "Rates at standard conditions, GVF, water-liquid ratio and measurement uncertainty."),
    "pig_station": ("Pig launcher / receiver", "A trap where a pig is loaded into or taken out of the pipeline.", "Transit time, liquid swept ahead of the pig and receiver slug capacity."),
    "subsea_hydrocyclone": ("Subsea hydrocyclone", "A compact cyclone that separates oil from water by centrifugal force.", "Oil removal, reject ratio and oil-in-water content."),
    "subsea_heater": ("Subsea electric heater", "An electric heater that keeps the flowline above the hydrate temperature.", "Duty, electrical power as a separate energy stream, outlet temperature."),
    "recycle": ("Recycle (logical)", "A calculation block that closes a recycle loop; it has no hardware.", "Iterates the tear stream until it stops changing."),
    "adjust": ("Adjust (logical)", "A controller block that moves one variable to meet a target.", "Secant iteration."),
}

# (result keys tried in order, reference size) for the visual scale of the equipment
SIZE_REF = {
    "compressor": (("Power [kW]",), 3000.0), "expander": (("Power [kW]",), 1500.0), "pump": (("Power [kW]",), 200.0),
    "gas_turbine": (("Shaft power [kW]",), 12000.0), "subsea_booster": (("Shaft power [kW]",), 1500.0),
    "subsea_pump": (("Shaft power [kW]",), 1500.0), "subsea_compressor": (("Shaft power [kW]",), 3000.0),
    "heater": (("Duty [kW]",), 2000.0), "cooler": (("Duty removed [kW]",), 2000.0), "hx": (("Duty [kW]",), 2000.0),
    "aircooler": (("Duty removed [kW]", "Duty [kW]"), 2000.0), "chiller": (("Duty removed [kW]",), 1500.0),
    "membrane": (("Membrane area [m²]",), 3000.0), "mol_sieve": (("Bed volume [m³]",), 15.0),
    "teg_contactor": (("Gas flow [MSm³/d]",), 5.0), "amine_contactor": (("Amine circulation [m³/h]",), 40.0),
    "flare": (("Flared gas [kg/h]",), 20000.0),
}
DIAMETER_KEYS = ("Selected diameter [mm]", "Required diameter [mm]", "Vessel ID [mm]")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def visual_scale(utype, res):
    """Factor on the 3D model of a unit from its result (vessel diameter, power, duty ...); 1.0 when unknown."""
    if utype in ("separator", "scrubber", "separator3", "phase_splitter", "subsea_separator", "deliquifier"):
        for k in DIAMETER_KEYS:
            d = _num(res.get(k))
            if d and d > 0:
                return max(0.65, min(1.8, d / 1000.0 / 2.0))
        return 1.0
    spec = SIZE_REF.get(utype)
    if spec:
        for k in spec[0]:
            q = _num(res.get(k))
            if q and q > 0:
                return max(0.7, min(1.7, (q / spec[1]) ** 0.3))
    return 1.0


def nominal_size(vol_m3_h, service):
    """(NPS inches, inner diameter m, velocity m/s) for an indicative line size."""
    v_t = {"gas": 15.0, "liquid": 2.5, "water": 2.5, "two-phase": 8.0}.get(service, 8.0)
    q = max(vol_m3_h, 0.01) / 3600.0
    d = math.sqrt(4.0 * q / (math.pi * v_t))
    nps = next((n for n in STD_NPS_IN if n * 0.0254 * 0.93 >= d), STD_NPS_IN[-1])
    di = nps * 0.0254 * 0.93
    return nps, di, q / (math.pi * di * di / 4.0)


def service_of(src_port, vf, phase_txt):
    p = (src_port or "").lower()
    if p in ("vapour", "gas", "dry", "sweet", "overhead", "top", "retentate", "permeate"):
        return "gas"
    if p in ("water", "reject"):
        return "water"
    if p in ("oil", "liquid", "bottoms", "bottom", "acid"):
        return "liquid"
    if vf is None:
        return "two-phase"
    if vf > 0.97:
        return "gas"
    if vf < 0.03:
        return "water" if (phase_txt or "").strip().lower() == "aqueous" else "liquid"
    return "two-phase"


def _plain(x):
    """JSON-safe copy (numpy numbers -> float)."""
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    f = _num(x)
    return f if f is not None else str(x)


def _bridge_logical(streams, recycles):
    """A recycle block has no hardware: the pipe that enters it continues straight to where its outlet stream goes."""
    if not recycles:
        return streams
    by_dst = {}
    out = []
    for s in streams:
        if s["dst"][0] in recycles:
            by_dst[s["dst"][0]] = s
    for s in streams:
        if s["src"][0] in recycles:
            sin = by_dst.get(s["src"][0])
            if sin is None:
                continue
            m = dict(sin)
            m["dst"] = list(s["dst"])
            m["name"] = sin["name"]
            out.append(m)
        elif s["dst"][0] in recycles:
            continue
        else:
            out.append(s)
    return out


def build_scene(model, sol=None, fmt=None, qfmt=None, solved_ok=True):
    """JSON-ready scene.  ``fmt(v)`` / ``qfmt(label, v)`` format numbers like the rest of the app (unit systems)."""
    fmt = fmt or (lambda v: f"{v:.4g}" if isinstance(v, (int, float)) else str(v))
    qfmt = qfmt or (lambda lab, v, *a: fmt(v))
    sm = PH.stage_model(model)
    stage = PH.resolve_stage(model)
    units, streams = sm["units"], sm["streams"]
    pos = [(u["x"], u["y"]) for u in units.values() if u["type"] not in LOGICAL and u["type"] != "platform"]
    cx = sum(p[0] for p in pos) / len(pos) if pos else 0.0
    cy = sum(p[1] for p in pos) / len(pos) if pos else 0.0
    cur = sol is not None and solved_ok
    out_u, out_s, plat = [], [], []
    for uid, u in units.items():
        t = u["type"]
        x, z = (u["x"] - cx) * M_PER_PX, (u["y"] - cy) * M_PER_PX
        d = DESCR.get(t, (t, "", ""))
        res = (sol.results.get(uid) or {}) if cur else {}
        if t == "platform":
            p = u.get("params", {})
            plat.append({"id": uid, "name": u["name"], "x": x + float(u.get("w", p.get("w", 520))) * M_PER_PX / 2,
                         "z": z + float(u.get("h", p.get("h", 320))) * M_PER_PX / 2,
                         "w": float(u.get("w", p.get("w", 520))) * M_PER_PX, "d": float(u.get("h", p.get("h", 320))) * M_PER_PX})
            continue
        if t in LOGICAL:
            continue
        rows = []
        for k, v in res.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)) and _num(v) is not None:
                rows.append([k, qfmt(k, v)])
            elif isinstance(v, str) and len(v) < 40:
                rows.append([k, v])
        status = (sol.status.get(uid, "unsolved") if cur else "unsolved") if t not in ("feed", "product") else "ok"
        out_u.append({"id": uid, "type": t, "name": u["name"], "x": round(x, 3), "z": round(z, 3), "flip": bool(u.get("flip")),
                      "orient": u.get("orient") or "", "scale": round(visual_scale(t, res) * float(u.get("scale", 1.0) or 1.0), 3),
                      "phase": u.get("phase") or "", "change": u.get("change") or "", "color": PH.effective_color(u, model) or "",
                      "status": status, "error": (sol.errors.get(uid, "") if cur else ""),
                      "title": d[0], "what": d[1], "model": d[2], "results": rows[:24]})
    for sid, s in streams.items():
        st = sol.streams.get(sid) if cur else None
        e = {"id": sid, "name": s["name"], "src": list(s["src"]), "dst": list(s["dst"]),
             "phase": s.get("phase") or "", "change": s.get("change") or "", "color": PH.effective_color(s, model) or "", "solved": False}
        if st is not None and not st.empty:
            p = stream_properties(st, sol.fp)
            vf = _num(p.get("Vapour fraction"))
            vol = _num(p.get("Actual vol flow [m³/h]")) or 0.0
            svc = service_of(s["src"][1], vf, p.get("Phase"))
            nps, di, vel = nominal_size(vol, svc)
            e.update({"solved": True, "T": _num(p["Temperature [°C]"]), "P": _num(p["Pressure [bar(a)]"]), "mass": _num(p["Mass flow [kg/h]"]),
                      "vol": vol, "vf": vf, "service": svc, "nps": nps, "d": round(di, 4), "vel": round(vel, 2),
                      "rho": _num(p.get("Mass density [kg/m³]")), "phaseTxt": p.get("Phase"),
                      "rows": [["Phase", str(p.get("Phase"))], ["Vapour fraction", f"{vf:.4f}" if vf is not None else ""],
                               ["Temperature", qfmt("Temperature [°C]", p["Temperature [°C]"])], ["Pressure", qfmt("Pressure [bar(a)]", p["Pressure [bar(a)]"])],
                               ["Mass flow", qfmt("Mass flow [kg/h]", p["Mass flow [kg/h]"])], ["Actual volume flow", f"{fmt(vol)} m³/h"],
                               ["Indicative line size", f"{nps:g} in, about {vel:.1f} m/s"]]})
        else:
            e.update({"service": service_of(s["src"][1], None, ""), "nps": 6, "d": 0.14, "vel": 0.0, "mass": 0.0, "rows": [["State", "no flow / not solved"]]})
        out_s.append(e)
    out_s = _bridge_logical(out_s, {uid for uid, u in units.items() if u["type"] == "recycle"})
    return _plain({"units": out_u, "streams": out_s, "platforms": plat,
                   "meta": {"stage": stage, "stageName": PH.stage_name(model, stage), "solved": bool(cur),
                            "phases": [{"id": p["id"], "name": p["name"], "color": PH.phase_color(model, p["id"])} for p in PH.phase_list(model)]}})
