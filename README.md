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
| `procsim/` | Engine, no Streamlit dependency: component library, PR EOS + flashes (`thermo.py`), streams, unit operations, flowsheet solver with recycles and adjusts, examples |
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
Adjust on the reboiler temperature to meet a TVP of 0.80 bar.

## Verification

`python tests/run_all.py` runs five suites (451 checks):

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
* **ui** — a headless run of the Streamlit app against stubs that validate widget arguments and Plotly property
  names (and forbid dual-axis charts), driving every property view, the analysis tab, case study and report.
* **browser** — the PFD canvas in real headless Chromium (Playwright): every example drawn and fitted, palette
  drag-and-drop, port-to-port connect, drop-to-create feeds/products, double-click, move, Delete / Ctrl+Z /
  Ctrl+D / F keys, wheel zoom, SVG export rendering standalone. Screenshots go to `tests/_screens/`.
* **canvas** — Node unit tests of routing, the Streamlit message protocol and editing logic.

Not verified in the build environment: Streamlit and Plotly themselves (not installable there), so the app's
pages and Plotly charts have not been rendered; the canvas, the theme CSS and the report were rendered in Chromium.

Results are for screening; check critical numbers against a commercial simulator.
