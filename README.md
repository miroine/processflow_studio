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
| `procsim/` | Engine, no Streamlit dependency: component library, PR EOS + flashes (`thermo.py`), streams, unit operations, subsea equipment (`surf.py` + `data/surf_catalogue.csv`), subsea CAPEX / umbilical / tie-back screening (`subsea_design.py`), slugging / turndown / field layout (`subsea_ops.py`), field life — reservoir tank, deliverability, profiles, NPV, well count (`fieldlife.py`), flowsheet solver with recycles and adjusts, examples |
| `pfd_canvas/` | Bidirectional Streamlit component — vanilla JavaScript + SVG, no build step (`frontend/pfd.js`) |
| `fieldmap_canvas/` | Field-layout drawing component — vanilla JavaScript + SVG (`frontend/fieldmap.js`), fed by `procsim/fieldmap.py` |
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
design presets (wet insulation, pipe-in-pipe, flexible, bundle, buried) and flowline heating (direct electrical
heating, heat-traced pipe-in-pipe, hot-water bundle; fixed W/m or hold-a-minimum-temperature control), risers
(vertical, SCR, lazy-wave, free-hanging, hybrid tower) with a riser-base severe-slugging screen (Bøe), SSIV and
HIPPS valves, and subsea boosters (helico-axial / hybrid multiphase pumps, wet-gas compressor, liquid pump, with
GVF window, boost and power-rating checks), dedicated subsea pumps and compressors (dry-gas centrifugal, water
injection), subsea processing — gas-liquid, liquid-liquid and inline separators with vessel sizing, a passive
seawater cooler — and chemical-injection hardware (pressure intensifier, CIMV with dosage check). A **Subsea (SURF)** tab summarises wells and equipment, draws a
reservoir-to-arrival pressure budget, builds an equipment list with a class 5 CAPEX roll-up, sizes the umbilical
(chemical-injection tubes, topside pump pressures) and the booster power cable, and screens tie-back distance vs
rate (arrival pressure, hydrate margin, maximum distance). Operability: a field-layout plan view, a turndown
envelope (operating window, limiting constraints, ramp-up liquid sweep-out) and a transient-lite slug assessment
(hydrodynamic slug statistics, severe-slug volume and build-up time, design surge volume), and a heated-lines
view (power flowing and to hold at shut-in, heat-up time, annual energy). The printable report
gains a subsea section. Wells can solve their own rate for a wellhead pressure (with a deliverability
curve); boosters take a performance curve and machines in parallel / series; a cool-down view gives the
no-touch time after shutdown.

**Field life (v6):** the production profile over the life of the field from the solved flowsheet — an EOS
compositional tank (rock and water compressibility, pot aquifer, water injection, condensate drop-out and free-gas
GOR rise through Corey relative permeabilities), deliverability from flowsheet solves with the chokes fully open
against a minimum delivery pressure, plateau / water / liquid capacity and booster rating limits, water-cut rise
after breakthrough, drilling ramp-up, boosting timing (fixed year or automatic when the plateau falls off), and
the economics (CAPEX from the equipment list, OPEX, energy and CO₂ cost per year, NPV, IRR, payback, economic
limit). It reports the drainage strategy, plateau length and recovery factor, compares well counts and boosting
options (in parallel on several cores) and recommends the case with the highest NPV, which can be written back
into the flowsheet. Well units carry *identical wells in parallel*; boosters can be *bypassed*.

**v6.1–6.2 additions:** flow assurance — shut-in natural-convection U for cool-down, depressurisation below the
hydrate pressure (blowdown time, liquid-head check), wax appearance temperature (Won multi-solid) and wax deposition
with pigging interval, DNV-RP-O501 sand erosion of bends; subsea — gas lift in the well model, a water injection well
unit, seabed-route flowlines with low points, choke opening (Cv) specification, subsea transformer and a power-from-
shore vs gas-turbine comparison; new example *Gas lift and water injection (SURF)*.

**v6.3 additions:** TEG dehydration contactor (water dew point, Kremser stages, reboiler duty), traced phase
envelope with cricondenbar/cricondentherm, column free-water draw, undo for property edits, and a validation harness
(`tests/validate_against.py`, `docs/VALIDATION.md`) for comparing with private reference cases.

**v6.4 additions:** the SURF *Field layout* view is now a field-layout drawing (JavaScript/SVG component): host
platform, templates with their well slots (producer, gas-lifted, injector, spare), satellites, PLEM, subsea stations
and the DUTA, joined by colour-coded production, gas-lift, water-injection, chemical and power / DC-FO lines with
their lengths, legend, north arrow, scale bar and title block. Drag structures, bend routes, rotate templates, hide
services from the legend and export to SVG; edits are saved with the flowsheet and never re-solve. New example
*Subsea hub (SURF)*: two templates, a satellite, a PLEM, gas lift and water injection.

**v6.5 additions (HYSYS-style process units):** amine sweetening contactor (MEA / DEA / MDEA / activated MDEA:
CO₂ and H₂S specifications, circulation, rich loading, reboiler and pump duty as energy streams); relief valve with
API 520/526 orifice selection (vapour, liquid, two-phase, API 521 fire case); flare (heat release, flame length,
radiation, tip size, CO₂ and SO₂); component splitter; conversion and equilibrium reactors (reaction strings such as
`C1 + 2 O2 -> CO2 + 2 H2O`, formation enthalpies included, ΔCp-corrected equilibrium constants); valve Cv / Kv,
choking and opening; compressor drivers (electric motor or gas turbine: fuel, CO₂, ambient derating, margin). New
examples: *Amine sweetening*, *Pressure relief*, *Steam reforming*.

**v6.6 additions (OLGA-style flow assurance, SURF tab *Regime & corrosion*):** mechanistic flow regime and holdup
(Taitel-Dukler, Taitel-Barnea-Dukler, Bendiksen drift flux) beside Beggs & Brill; CO₂ corrosion (de Waard-Lotz-Milliams)
with allowance, pH and inhibitor targets; oil-water emulsion viscosity and inversion; liquid loading (Turner / Coleman)
and minimum stable rate; pigging (run time, liquid swept, receiver size); line pack and draw-down; insulation sized
for a target cool-down time; PVT table export (CSV).

**v6.7 additions (network and well design, *Design* tab):** flowline diameter sweep with a recommended size (arrival
pressure, erosion, hydrate margin, slugging, steel mass), gas-lift allocation (performance curves from the well
model, equal-marginal-gain split of the available gas), ESP sizing (head, free gas, generic pump catalogue, motor)
and a looped / branched pipe-network solver for gas or liquid.

**v7.0 additions (production prognosis, *Prognosis* tab):** on top of the Field life model, a drainage-strategy
comparison (oil: water-injection voidage replacement and facility size; gas: facility size with the 0.6-power cost
rule; subsea boosting timing), each option at its highest-NPV well count, and an uncertainty analysis (Latin-hypercube
samples of in-place volume, aquifer, water breakthrough and price) giving P90 / P50 / P10 recovery factor and NPV per
well count, the chance of a negative NPV, and the best-on-average and the most robust well count. The deliverability
tables of a well count are shared by all samples. A recommended development summary and a report section are included.

**v7.1 additions (topside templates and debottlenecking):** six topside example flowsheets (HP compressor bypass, debottlenecking,
two parallel trains with recycle loops, produced-water handling and reinjection, gas blending to a Wobbe-index specification,
HP / LP flare system), gas-quality properties on gas streams (GCV, Wobbe index, relative density, CO₂), a vessel diameter on
separators for a gas-load check, and a *Debottlenecking* panel on the Design tab (utilisation of every unit and a throughput sweep
that ranks the limits).

**v7.2 additions (profile simulation, *Profile* tab):** the process flowsheet can be run for a time profile as well as for a single
step. A table with one row per time step holds the feed rate, pressure and temperature (and, optionally, any other numeric unit
parameter); every row is solved as a full steady-state flowsheet (recycles and Adjust loops included) and the results are charted
against time with cumulative gas, liquid, power and CO₂. A blank cell keeps the flowsheet value; a step that does not solve is
marked and does not stop the run. Tables can be generated (linear or exponential decline), edited, uploaded and downloaded as CSV.
It is a quasi-steady profile: nothing carries over between steps (no hold-up, no dynamics). An example table for the *Oil stabilisation*
example is in `docs/example_profile_oil_stabilisation.csv` (upload it on the *Profile* tab).

**v7.3 additions (dynamic simulation, *Dynamic* tab):** a lumped, time-domain simulation of the solved flowsheet, from seconds to
hours. It starts from the steady-state solution (t = 0 reproduces it) and follows a table of events - feed-rate / temperature /
pressure steps and ramps, set-point and valve changes, compressor trip / start / speed, pump trip, opening a blowdown valve or PSV,
a fire heat input, a back-pressure change. Four applications are covered: **vessel hold-up with level and pressure control**
(separators, scrubbers, mixers; PI controllers with anti-windup), **blowdown / depressurisation** (orifice flow to a flare header,
API 520 / ISO 4126 gas flow, choked and subsonic, PSV with reseat), **pipeline line-pack** (pipes become chains of isothermal cells)
and **compressor trip, coast-down and surge** (map-based flow, speed lag, check valve, anti-surge valve and controller). Vessels
are well-mixed holdups solved with a U-V flash at every step (Peng-Robinson), total moles are conserved to round-off, and the
time step adapts. Four examples (all generic data): *Dynamic: HP separator ...*, *gas vessel blowdown*, *compressor trip*,
*pipeline line-pack*. Limits: no pressure waves or momentum (no water hammer), well-mixed holdups (no stratification, no
foaming or carry-over), homogeneous flow in pipes (no slip, no slugging), valves are ideal characteristics, liquid-full vessels and
columns, heat exchangers, reactors and other unit types are not supported (the tab says which unit is the problem).
Valve, compressor and pump coefficients are calibrated at the steady state, so a flowsheet with no data for them still starts at
its steady point; vessel volumes and valve sizes default to generic values - change them for a real design. Not a replacement for
a validated dynamic simulator (HYSYS Dynamics, UniSim, OLGA, K-Spice).

**v7.4 additions (data exchange, *Data* tab):** *Import* parameters, feed compositions, profile tables and dynamic events from **CSV, Excel
or YAML** (or open a complete flowsheet in YAML). The layout of every sheet is recognised from its columns (one row per parameter, one row per
unit, compositions long or wide, `Time` + `Unit | parameter` profile, events); nothing is written until a **change plan** (old value, new value,
status, reason) has been checked - unknown units or parameters (with "did you mean"), values outside the catalogue limits, bad select options and
parameters hidden by the unit's specification are flagged, and *Undo edit* reverts an import. *Batch edit*: set / multiply / add / reset a
parameter on all units of a type or name pattern, edit a table of one unit type, or edit all feed compositions in one grid. *Export* the result
tables (streams, stream compositions, unit results, energy, overall KPIs, parameters, feed compositions, plus the last Profile and Dynamic
results) to a formatted **Excel** workbook, **CSV** (one table or a zip), or the flowsheet / parameters as **YAML** / JSON; the *Parameters* and
*Feed compositions* sheets of an export can be edited in Excel and imported again. A **Python editor** lets you adjust the export tables with a
short script before exporting (and apply an edited Parameters table back to the flowsheet). The editor runs code, so it is **off unless the
host enables it** (environment variable `PFS_PYTHON_EDITOR=on` or the Streamlit secret `python_editor = "on"`, optionally with a password
`python_editor_password`); scripts run in a separate process with CPU, memory and no-file-write limits, no file or network access and a
short allow-list of imports; modules are handed over through read-only views (no `pd.io` / `np.os` doors), private attributes are blocked and file / process / network calls are audited (v7.4.1) - defence in depth, still not a certified sandbox. Examples: `docs/example_import_parameters.csv`,
`docs/example_import_changes.yaml`. Needs `pyyaml` (added to `requirements.txt`).

**v7.4.1 (audit and bug-fix release):** closed a hole in the Python editor (`pd.io.common.os` gave a script access to the operating system) with
read-only module views, a stricter AST check and an audit hook. Dynamics: bumpless manual → auto transfer, surge margin in surge, anti-surge
controller back to auto after a restart, a second `run()` continues with the same mass-balance baseline, events and settings are validated with
readable messages (bad target, missing time, text value, negative run length, zero volume, PSV without set pressure), valve openings are clamped,
and steps never jump over a feed ramp's breakpoint. UI: loading a flowsheet clears the Profile / Dynamic / Data widget state and caches, untouched
default vessel volumes are no longer pinned into the settings, the dynamic model assembly is cached between reruns and the default Python script
works on an empty flowsheet.

**v7.6 additions (phasing and symbols):** elements and streams can be tagged *new in phase n* / *removed in phase n* and coloured freely; a stage selector
shows the design, today, or the situation after any phase, with the calculation following the stage (absent items are dropped and shown as ghosts), plus a
*Before / after* comparison tab. Separators and scrubbers can be vertical or horizontal, every element has its own zoom, and the new elements are an
offshore platform frame (fixed, floating, FPSO, subsea, onshore), a gas turbine (LHV-based power, derating, exhaust heat, CO₂, plant power balance) and
a phase splitter. A phased example flowsheet is included.

**v7.5 additions (fluid package, *Fluid package* tab and *Phase envelope*):** the component library grows from 13 to **163** (n-alkanes to C20, branched
alkanes, cyclics, aromatics, olefins, sulfur compounds, alcohols, glycols, amines, light and noble gases) with CAS number and formula, searchable and
filterable; data from the ChemSep pure-component database (Artistic License 2.0). **EOS calibration** fits the Peng-Robinson *m* and the Peneloux
volume shift (optionally ω, Tc, Pc, with weak priors) of any component to vapour-pressure and liquid-density data, or a pair's kij to bubble
pressures, shows before/after deviations and stores the result in the flowsheet. **Plus-fraction splitting** (Pedersen distribution, Søreide density,
equal-mass pseudo-components). The **phase envelope** is now traced by continuation through the critical point (smooth, 1-3 s) with optional
vapour-fraction lines; the old grid method remains as a fallback. Tests: `tests/test_fluid75.py`.
Limits: extended-library kij are generic family rules; Cp is a cubic fit; calibrations are only as good as the data and extrapolate poorly when
ω/Tc/Pc are fitted together with m.



**Hydrate model (v6):** Motiee gas-gravity correlation or a van der Waals–Platteeuw model (sI/sII, Kihara potentials, PR
fugacities, fitted to pure-gas data), selected on the Fluid package tab.

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
late-life variant with a subsea wet-gas compressor and a 45 km step-out; an 18 km DEH oil tie-back held at 25 °C
with methanol injected through a pressure intensifier and a CIMV; a subsea compression station (cooler,
gas-liquid separator, dry-gas compressor, liquid pump, 40 km export to a lazy-wave riser); and subsea separation
with water reinjection and a multiphase pump on the oil and gas.

## Verification

`python tests/run_all.py` runs every suite (about 1,230 checks). On GitHub the workflow `.github/workflows/tests.yml`
runs them on each push with the real Streamlit, Plotly, PyArrow, Chromium and Node on Python 3.12 and 3.14 (open the
**Actions** tab of the repository to see the result; screenshots are attached to each run).

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
  low-rate hydrate limit, operating-window logic, and layout geometry (distances, bearings, well ring); heated
  flowlines (fixed-W/m energy balance, hold mode reaching the set temperature and the W/m cap, hold power and
  heat-up time from their formulas, annual energy), subsea separators (balances, Souders-Brown and wall
  thickness), cooler area / approach, intensifier pressure and work, CIMV pressure check and dosage, and the
  three new examples solving with closed balances.
* **fieldlife** — the tank material balance against its definition (p/z ∝ moles for a gas; residuals of the volume
  balance), compressibility, aquifer and voidage-replacement effects, the bubble / dew point, the produced
  composition below it (immobile gas or condensate), water-cut rise, well distribution and IRR; full runs on the SURF
  examples: plateau at the facility rate, pressure held by injection, recovery = cumulative / in place, NPV = sum of
  discounted cash flows, the deliverability root (feasible at s*, not 6 % above), a lower delivery pressure lengthening
  the plateau, more wells holding it longer, boosting recovering more than bypassed boosters.
* **ui** — a headless run of the Streamlit app against stubs that validate widget arguments and Plotly property
  names (and forbid dual-axis charts), driving every property view, the analysis tab, case study and report.
* **browser** — the PFD canvas in real headless Chromium (Playwright): every example drawn and fitted, palette
  drag-and-drop, port-to-port connect, drop-to-create feeds/products, double-click, move, Delete / Ctrl+Z /
  Ctrl+D / F keys, wheel zoom, SVG export rendering standalone. Screenshots go to `tests/_screens/`.
* **canvas** — Node unit tests of routing, the Streamlit message protocol and editing logic.
* **streamlit-real** — the app on the real Streamlit runtime (`streamlit.testing.v1.AppTest`): every example and
  property view, field units, the SURF buttons, scenarios and a blank flowsheet, with no exception and no error box.
  Skipped when Streamlit is not installed. When a real Plotly is installed the **ui** suite also runs a second time
  on it, so every chart is fully serialised and validated by Plotly.

Streamlit and Plotly cannot be installed from PyPI in the build environment; there they were built from their GitHub
sources (Streamlit with a test-only PyArrow stand-in) to run the streamlit-real and real-Plotly checks. The GitHub
workflow runs the same checks on the released packages.

Results are for screening; check critical numbers against a commercial simulator.
