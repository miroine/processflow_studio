# ProcessFlow Studio

*Made by Merouane Hamdani — for educational purposes only. Independent tool, not affiliated with or endorsed by
Equinor or AspenTech; the colour theme is inspired by the Equinor Design System palette (no logos or trademarks).*

A steady-state process simulator in Streamlit with a HYSYS-style, drag-and-drop process flow diagram.
Draw the flowsheet with the equipment palette, connect ports, double-click to open property views, and
the flowsheet solves with a Peng-Robinson fluid package. Results appear on the diagram (stream
conditions, duties, status colours), in a HYSYS-like workbook, and as Plotly charts.

```bash
pip install -r requirements.txt
streamlit run app.py
```

## What's inside

| Folder | Content |
|---|---|
| `procsim/` | Engine, no Streamlit dependency: component library, PR EOS + flashes (`thermo.py`), streams, unit operations, subsea equipment (`surf.py` + `data/surf_catalogue.csv`), subsea CAPEX / umbilical / tie-back screening (`subsea_design.py`), slugging / turndown / field layout (`subsea_ops.py`), flowsheet solver with recycles and adjusts, examples |
| `pfd_canvas/` | Bidirectional Streamlit component — vanilla JavaScript + SVG, no build step (`frontend/pfd.js`) |
| `ui/` | Session/model sync, property views, fluid package manager, workbook, charts, help |
| `tests/` | Verification suites — `python tests/run_all.py` |

**Unit operations:** feed/product streams, valve, mixer, tee, 2-phase and 3-phase separators, compressor
(polytropic or adiabatic), expander, pump, heater, cooler, shell-and-tube exchanger (outlet T, duty,
minimum-approach or UA rating; heat curve), air cooler (with fan-power estimate), pipe segment (Beggs & Brill
with Payne corrections or homogeneous; elevation, heat loss to ambient, holdup, regime, erosional ratio),
gas scrubber (Souders-Brown sizing: mesh pad / vane / cyclones), column (absorber, reboiled absorber /
stabiliser, distillation with partial or total condenser; inside-out solver with rigorous PR stages, bottoms TVP),
compressor performance curves (fan laws, surge / stonewall margins, performance map) with anti-surge recycle
control, recycle (Wegstein), adjust.

**Subsea (SURF):** wells (gas back-pressure, PI or Vogel inflow + Beggs & Brill tubing lift with a geothermal
ambient → wellhead P/T, AOF, GOR, water cut), subsea Xmas tree + choke (critical-flow and hydrate check),
templates / manifolds (slot count, header loss), jumpers, spools, PLET and PLEM (fitting losses), flowlines with
design presets (wet insulation, pipe-in-pipe, flexible, bundle, buried) and direct electrical heating, risers
(vertical, SCR, lazy-wave, free-hanging, hybrid tower) with a riser-base severe-slugging screen (Bøe), SSIV and
HIPPS valves, and subsea boosters (helico-axial / hybrid multiphase pumps, wet-gas compressor, liquid pump, with
GVF window, boost and power-rating checks). A **Subsea (SURF)** tab summarises wells and equipment, draws a
reservoir-to-arrival pressure budget, builds an equipment list with a class 5 CAPEX roll-up, sizes the umbilical
(chemical-injection tubes, topside pump pressures) and the booster power cable, and screens tie-back distance vs
rate (arrival pressure, hydrate margin, maximum distance). Operability: a field-layout plan view, a turndown
envelope (operating window, limiting constraints, ramp-up liquid sweep-out) and a transient-lite slug assessment
(hydrodynamic slug statistics, severe-slug volume and build-up time, design surge volume). The printable report
gains a subsea section. Wells can solve their own rate for a wellhead pressure (with a deliverability
curve); boosters take a performance curve and machines in parallel / series; a cool-down view gives the
no-touch time after shutdown.

**Scenarios:** save named cases of the flowsheet with their key results (production, power, CO₂, arrival
conditions, CAPEX, no-touch time, slug surge) and compare them side by side, as differences to a base case,
in a chart and in the report. Equipment data come from `procsim/data/surf_catalogue.csv`, which holds **generic, illustrative values
only**; upload your own CSV in the app and it is stored with the flowsheet file, never in the repository.

**Display units:** SI or Field (°F, psia, MMscf/d, bbl/d, lb/h, hp, MMBtu/h …) for every table, input, chart,
label and report; the engine always calculates in SI.

**Economics & CO₂:** energy OPEX, CO₂ emissions and CO₂ cost per year, CO₂ intensity (kg/boe) and cost per boe for
grid-powered or gas-turbine-driven facilities with fired, electric or waste-heat heating and seawater cooling —
illustrative defaults, all user-editable and sweepable in the case study.

**Analysis tab:** KPIs, mass-flow Sankey, flow-assurance P–T path against the uninhibited and inhibited
hydrate curves (with optional phase envelope) plus an MEG / methanol dosing calculator, equipment charts
(energy, compressors + maps, heat curves, column and pipe profiles, convergence) and grouped compositions.

**Tools:** case study (sweep a specification, record any stream property or unit result, charts + CSV),
printable HTML report (print to PDF), Excel workbook, SVG export, duplicate / copy-paste with specifications.

**Thermodynamics:** Peng-Robinson 1978 with editable kij, vapour / hydrocarbon-liquid / aqueous three-phase
flash (Michelsen multiphase Rachford–Rice, stability test, missing-phase check on every split), PT / PH / PS /
P-VF flashes, Peneloux densities, LBC viscosities (Vogel for water), Motiee hydrate screening with MEG
(Hammerschmidt) and methanol (Nielsen–Bucklin) inhibition, hypothetical petroleum cuts from NBP + SG.

**Examples:** two-stage gas compression with liquid recycle; oil stabilisation (3-stage separation +
recompression); JT dew-point control with a gas/gas exchanger, recycle and adjust; subsea tie-back (choke,
25 km flowline, riser, arrival separator) with 90 wt% lean-MEG injection; condensate stabiliser column with an
Adjust on the reboiler temperature to meet a TVP of 0.80 bar; subsea field with four wells on a 4-slot
template, a spool, a 25 km pipe-in-pipe flowline, an SSIV and a lazy-wave riser to the arrival separator; its
late-life variant with a subsea wet-gas compressor and a 45 km step-out.

## Verification

`python tests/run_all.py` runs six suites (808 checks):

* **thermo** — PR against NIST WebBook (vapour pressures; methane density, Cp, Joule-Thomson coefficient,
  viscosity), thermodynamic consistency, flash round trips, iso-fugacity, three-phase monotonicity, MEG properties,
  Hammerschmidt / Nielsen–Bucklin values and their inverses, injection mass balance, unit conversions and
  their exact inverses.
* **flowsheet** — component and energy balances on every unit of every example; analytic compressor / pump /
  valve limits; pipe hydraulics vs Darcy–Weisbach/Colebrook, static head and the Beggs & Brill textbook holdup;
  HX UA round trip; compressor curves (curve through the duty point reproduces it, fan laws, surge warning);
  scrubber Souders-Brown arithmetic and the GPSA pressure factor; anti-surge control (recycle opens at turndown,
  machine held on the control line, power floor, cooler duty, energy balance); economics arithmetic (grid, gas
  turbine, fired heating, intensity); columns — every stage in PR equilibrium
  (|y − Kx| < 1e-7), distillate at its bubble point, specs met, reflux and lean-oil trends, TVP Adjust.
* **surf** — the IPRs against their formulas (back-pressure, PI, Vogel, AOF), the well's tubing against the pipe
  model, choke specs and critical ratio, template slots and header loss, jumper fitting losses, flowline presets
  and DEH, riser geometries and the Bøe number from its definition, SSIV / HIPPS trips, catalogue parsing,
  validation and swapping, and balances (incl. reservoir heat, DEH and elevation) on the SURF field example;
  boosters (power = F·ΔH, isentropic at 100 %, GVF window, rating), CAPEX arithmetic, Hagen–Poiseuille tube
  ΔP, tube and cable selection, and tie-back screening reproducing the flowsheet arrival pressure; slug
  correlations against their formulas, the severe-slug gas balance, turndown reproducing the flowsheet and its
  low-rate hydrate limit, operating-window logic, and layout geometry (distances, bearings, well ring).
* **ui** — a headless run of the Streamlit app against stubs that validate widget arguments and Plotly property
  names (and forbid dual-axis charts), driving every property view, the analysis tab, case study and report.
* **browser** — the PFD canvas in real headless Chromium (Playwright): every example drawn and fitted, palette
  drag-and-drop, port-to-port connect, drop-to-create feeds/products, double-click, move, Delete / Ctrl+Z /
  Ctrl+D / F keys, wheel zoom, SVG export rendering standalone. Screenshots go to `tests/_screens/`.
* **canvas** — Node unit tests of routing, the Streamlit message protocol and editing logic.

Not verified in the build environment: Streamlit and Plotly themselves (not installable there), so the app's
pages and Plotly charts have not been rendered; the canvas, the theme CSS and the report were rendered in Chromium.

Results are for screening; check critical numbers against a commercial simulator.
