HELP = r"""
### Using the diagram

| Action | How |
|---|---|
| Add equipment | Drag an icon from the palette onto the canvas (or tap it, then tap the canvas) |
| Connect | Drag from a port (small circle that appears on hover) to a compatible port on another unit |
| Feed / product stream | Drag from an **inlet** port to empty space → new feed; from an **outlet** port → new product |
| Property view | Double-click (or double-tap) a unit or stream; the view opens under the diagram |
| Move / select | Drag icons; Shift-click adds to the selection; Shift-drag on the background draws a selection box |
| Pan / zoom | Drag the background; mouse wheel or the + / − / Fit buttons |
| Delete / flip / undo | `Del`, `F`, `Ctrl+Z` — or the toolbar buttons |
| Duplicate | `Ctrl+D` or **Duplicate** (also `Ctrl+C` / `Ctrl+V`): copies keep their specifications and internal connections; a copied Adjust starts inactive |

Colours follow HYSYS conventions: **dark blue** streams are solved, **light blue** are not; unit outlines turn
**yellow** when a connection or specification is missing and **red** on an error. Red arrows are energy streams
(sign convention in the workbook: positive into the process, negative out).

### Unit operations

| Unit | Specification | Model |
|---|---|---|
| Valve | outlet P or ΔP | isenthalpic (PH flash) — Joule-Thomson effect |
| Mixer | lowest inlet P (or specified) | adiabatic enthalpy balance |
| Tee | split fractions (last outlet takes the remainder) | same state on every outlet |
| 2-/3-phase separator | ΔP, optional heat input | PH flash; 3-phase splits vapour / hydrocarbon liquid / aqueous; indicative Souders-Brown gas-handling diameter |
| Gas scrubber | ΔP, internal (mesh pad / vane pack / axial cyclones / none), optional K override, ID (0 = size it), design margin | 2-phase flash + Souders-Brown sizing: K-factor (GPSA, mesh pad corrected for pressure), max and actual gas velocity, required and selected diameter, gas load % — overload warns of liquid carry-over |
| Column | trays, feed tray, top/bottom P; condenser none / partial / total with reflux ratio; reboiler with duty, bottoms rate, boil-up ratio, reboiler T or distillate rate | equilibrium stages, inside-out: rigorous PR K-values and enthalpies (outer loop) + Newton on the MESH equations (inner loop); stage profiles, duties, bottoms TVP @ 37.8 °C. Configurations: absorber (top + bottom feed), reboiled absorber / stabiliser, distillation. VLE only — remove free water upstream |
| Compressor | outlet P or ratio with polytropic or adiabatic η — or **performance curve** + speed | isentropic via PS flash; polytropic by stepwise path at η_p (4- and 8-step, Richardson-extrapolated). With a curve: head and η_p read at the actual inlet flow (fan laws for speed), outlet P solved to match the head; surge / stonewall margins, map with speed lines |
| Expander | outlet P, adiabatic η | PS flash to the isentropic outlet, then PH |
| Pump | outlet P or ΔP, adiabatic η | ideal work V·ΔP with the volume-shifted inlet liquid volume, then PH flash |
| Heater / cooler | outlet T, duty or outlet vapour fraction; ΔP | PT / PH / P-VF flash |
| Shell & tube | tube or shell outlet T, duty, minimum approach, or UA (rating); ΔP each side | counter-current; zoned heat curve, UA, internal pinch |
| Pipe segment | length, ID, roughness, elevation change, increments; adiabatic or overall U to ambient | Beggs & Brill (1973) with Payne corrections, or homogeneous no-slip; Churchill friction factor; Heun predictor–corrector along the line; exact exponential heat loss per increment; holdup, flow regime, liquid inventory, API RP 14E erosional ratio |
| Air cooler | process outlet T, air ΔT, fan ΔP and η | as cooler + air-side balance for a fan-power estimate |
| Recycle | tolerances, Wegstein / direct substitution | tear stream starts at zero flow, bounded Wegstein (q ∈ [−5, 0]) |
| Adjust | any numeric unit/feed parameter → stream property or unit result | bounded secant with bisection fallback; converged value is written back |

### Compressor performance curves

Enter the design-speed curve on the compressor's **Performance curve** tab (actual inlet flow, polytropic head,
polytropic efficiency — first row = surge point, last row = stonewall), or press **Generate a typical curve** to
create a realistic centrifugal curve through the current operating point. Then either keep a fixed specification
(the map shows where the duty sits and the speed needed to deliver it), or choose **Performance curve** as the
specification so the curve and the speed set the discharge pressure. Surge margin is Q/Q_surge − 1 at the
operating speed; below 10 % is flagged. Pair it with an **Adjust** on *Speed* to hit a discharge pressure.

### Anti-surge control

Switch **Anti-surge control** on for a compressor with a performance curve and set the minimum surge margin
(the control line, e.g. 10 %). When the process flow falls below the control line the compressor recycles
cooled discharge gas to suction: the machine flow is held on the control line, the extra power is charged to the
compressor, and the heat removed by the anti-surge cooler appears as a separate energy stream. With a fixed
discharge pressure the speed follows the fan laws, so at turndown the power stops falling once recycle starts —
the classic turndown penalty. The map shows the control line and the process → machine flow step.

### Display units

**Display units** in the sidebar switches every table, input, chart and label between SI and Field units
(°F, psia, psi, MMscf/d, bbl/d, lb/h, lbmol/h, hp for power, MMBtu/h for heat, ft, in, lb/ft³, Btu/lb …).
Temperature differences (approaches, margins, rises) convert without the 32 °F offset. The engine always
calculates in SI, and saved flowsheets are stored in SI. Feeds also accept lbmol/h, lb/h, MMscf/d and bbl/d.

### Economics & CO₂ (Analysis tab)

Every energy stream is priced: shaft power from the grid (price, grid emission factor) or gas-turbine drivers
(fuel from the turbine efficiency and fuel LHV, CO₂ per Sm³ of fuel); heating from a gas-fired heater, electric
heating or free waste heat; seawater cooling charged as pumping power. Results: energy cost, CO₂ emissions and
CO₂ cost per year, CO₂ intensity (kg/boe) and cost per boe of product, fuel gas. All prices and factors are
**illustrative placeholders — enter your own**; they are saved with the flowsheet and can be swept in the case
study (e.g. CO₂ intensity vs. export pressure).

### Hydrate inhibition (MEG / methanol)

Add **MEG** and/or **MeOH** to the component list and inject them through a feed stream (the tie-back example
injects 90 wt% lean MEG). The flash carries the inhibitor into the aqueous phase; the hydrate temperature is then
depressed by Hammerschmidt (MEG, K = 1297 °C·g/mol) or Nielsen–Bucklin (methanol). **Analysis → Flow assurance**
plots the process P–T path against the uninhibited and inhibited hydrate curves (optionally over the phase
envelope) and sizes the lean-inhibitor injection for a design margin.

### Analysis tab

KPIs (power, heating, cooling, gas and liquid products, minimum surge margin, hydrate risks), a mass-flow
Sankey diagram, the flow-assurance P–T plot and dosing calculator, equipment charts (energy streams,
compressors and maps, heat curves, column and pipe profiles, convergence) and grouped stream compositions.

### Case study and report

* **Case study** (tab): sweep any numeric specification over a range and record stream properties or unit
  results — e.g. compressor power vs. suction pressure, or arrival temperature vs. flowline U. Each case is solved
  on a copy; the base flowsheet is unchanged. Results as charts, table and CSV.
* **Printable report** (Workbook tab): a self-contained HTML report — KPIs, attention items, the PFD (after
  **Export SVG**), stream tables, unit results, energy streams and the fluid package. Open it and print to PDF.

### Thermodynamics

* **Peng-Robinson (1978)** with the ω > 0.49 α correction, van der Waals mixing and editable kij.
* **Flash:** successive substitution on K-values, Michelsen's multiphase Rachford–Rice (so up to three phases —
  vapour, hydrocarbon liquid, aqueous — with vanishing phases handled cleanly), Wilson and free-water
  initialisation, Michelsen tangent-plane stability test for single-phase results, and warm starts.
  PH/PS/P-VF flashes are secant on T with a bracketed Brent fallback; pure components get an exact
  saturation treatment inside the dome.
* **Caloric:** ideal-gas Cp polynomials (Reid, Prausnitz & Poling) + PR departure functions; Cp and Cv from
  analytic EOS derivatives. Enthalpy reference: ideal gas at 25 °C = 0 (no heats of formation — no reactions).
* **Density:** Peneloux volume shift (PR form, Rackett Z_RA); water/methanol shifts calibrated to 15 °C density.
* **Viscosity:** Lohrenz–Bray–Clark (Stiel–Thodos dilute gas, Herning–Zipperer mixing) for vapour and
  hydrocarbon liquid; Vogel equation for the aqueous phase. Gas within ~1–3 % of NIST; untuned LBC liquid
  viscosities are typically 15–40 % low — tune against lab data for pipeline design.
* **Hydrate screening:** Motiee (1991) gas-gravity correlation (3.5–280 bar, SG 0.55–1.0), shown as hydrate
  temperature and margin on every vapour-bearing stream; streams that carry water and run colder are flagged ❄.
* **Inhibitors:** MEG (ethylene glycol) and methanol in the library, with water/inhibitor kij (−0.063 / −0.07) and a
  high MEG–hydrocarbon kij (0.20) so glycol stays with the water.
* **Standard conditions:** 15 °C, 1.01325 bar (23.645 Sm³/kmol).
* **Hypothetical components:** Kesler–Lee (1976) Tc, Pc, ω; Riazi–Daubert (1980) MW; ideal-gas Cp from the
  mass-specific n-paraffin series scaled by MW.

### Limitations

* Steady state only; no reactions, no electrolytes, no hydrate or wax prediction.
* PR water solubility and aqueous-phase density are approximate (liquid water ≈ 3 % light at 100 °C);
  glycol/amine systems are not modelled.
* No thermal conductivity; no columns (distillation/absorption) yet; the pipe model neglects acceleration pressure
  drop and uses a single inclination per segment (chain segments for a profile).
* Hydrate temperatures come from a gas-gravity correlation; salts and H₂S/CO₂ effects are ignored, and the
  inhibitor correlations are screening tools (check high MEG concentrations with a rigorous hydrate model).
* Columns are VLE-only (no free-water draw or liquid–liquid split on the trays); no side draws or pump-arounds yet.
* The phase envelope is contoured from a P–T grid of flashes rather than traced, so the critical region is
  approximate.
* Results are for screening — check critical numbers against a commercial simulator.

### About

ProcessFlow Studio — made by **Merouane Hamdani**. For educational purposes only: an independent teaching and
screening tool, not an official product of, affiliated with, or endorsed by Equinor or AspenTech. The colour theme
is inspired by the Equinor Design System palette; no logos or trademarks are used.
"""
