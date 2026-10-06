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

### Subsea equipment (SURF)

The **Subsea (SURF)** palette group builds a tie-back from the reservoir to the topside. Every unit reuses the
tested pipe, valve and flash models; equipment data come from a catalogue (see below).

| Unit | Specification | Model |
|---|---|---|
| Well | rate from the feed **or solved for a wellhead pressure**; inflow model: gas back-pressure *q = C(Pr² − Pwf²)ⁿ*, liquid productivity index *q = PI(Pr − Pwf)* or Vogel; tubing MD, TVD, ID, roughness, U to formation, ambient at the wellhead | connect a **feed at reservoir P and T**; its flow is the well rate. Rates at standard conditions (15 °C, 1.01325 bar flash) give Pwf from the IPR (isothermal inflow, the reservoir heat shows as an energy stream), then the tubing is marched with Beggs & Brill against a linear geothermal ambient → wellhead P and T, AOF, GOR, water cut. Use an **Adjust** on the feed flow to find the rate for a wellhead or choke pressure. *Identical wells*: one unit stands for several parallel wells (rate split equally). **Gas lift**: connect a lift-gas stream to the *lift* port; it is injected at the valve depth (checked against the tubing pressure there, with the gas column in the annulus) and the tubing above carries the lightened mixture |
| Xmas tree + choke | tree type (valve ΔP from the catalogue), choke outlet P, choke ΔP, fully open, or **choke opening** (Cv_max, opening, equal-percentage rangeability: ΔP from the flow, homogeneous mixture with a gas expansion factor and choking) | isenthalpic; critical-flow check from the gas Cp/Cv (ideal-gas critical ratio, flagged as an estimate for multiphase flow); hydrate margin just downstream of the choke |
| Template / manifold | template (slots, header ID, length, fittings K from the catalogue) | more wells than slots is an error; mixes at the lowest slot pressure (the spread tells you how much the tree chokes must balance), then header friction + fittings loss |
| Jumper / spool / PLET / PLEM | component, length, ID, bends (−1 = catalogue), roughness | short Beggs & Brill pipe + K·ρv²/2 for bends and valves |
| Flowline | design preset (bare steel, wet insulation, pipe-in-pipe, flexible, bundle, buried, DEH-ready), length, ID, elevation, seabed T, **heating system** (none, direct electrical heating, heat-traced pipe-in-pipe, hot-water bundle) with a **fixed W/m** or **hold a minimum fluid temperature** (with a maximum W/m) | pipe model with the preset's U and roughness (0 = use the catalogue); heating adds heat along the line — in hold mode only where the fluid would fall below the set temperature, capped at the installed W/m (warned when the cap binds). Reports heat into the fluid, heated length, peak W/m and the electrical power (heat ÷ system efficiency) or the topside heater duty for a hot-water bundle |
| Riser | type (vertical, SCR, lazy-wave, free-hanging flexible, hybrid tower), water depth, ID | geometry built from the water depth and the catalogue length factor: catenaries as a two-section profile, lazy-wave as up → hog bend → sag bend → up. **Riser-base slugging screen** (Bøe 1981): stratified flow at the riser base with a downhill flowline and u_sg0 < ρ_l g α L u_sl / P0 is flagged |
| SSIV / HIPPS | valve, open/closed, open ΔP, optional HIPPS trip pressure | isenthalpic ΔP; closed or tripped gives no flow downstream (a trip is a warning) |
| Subsea booster / subsea pump / subsea compressor | type (helico-axial multiphase pump, hybrid pump, wet-gas compressor, single-phase pump), boost ΔP, outlet P or **performance curve** + speed; machines in parallel / series; efficiency (0 = catalogue or curve) | isentropic compression of the whole mixture (PS flash) ÷ efficiency; shaft and electrical power (motor efficiency), hydraulic power, inlet gas volume fraction checked against the machine's GVF window, boost against its maximum per machine and power against its rating |
| Water injection well | injectivity index, reservoir P and T, identical wells, tubing | water flows down the tubing from the wellhead (hydrostatic gain, friction, heat to the formation); required bottomhole P = P_res + q/II and the injection margin; a warning when the wellhead pressure is too low. Its outlet carries the injected water (connect a product) |
| Subsea separator | type (vertical gas-liquid, horizontal liquid-liquid, inline pipe separator), ΔP, liquid residence time, design pressure | flash at the outlet pressure; gas-liquid types send all liquid to the oil outlet, the liquid-liquid separator removes the free water (for reinjection). Sizing: Souders-Brown gas diameter, liquid volume for the residence time (horizontal L/D 4, inline L/D 20), ASME wall thickness and weight; thick walls (> 150 mm) are flagged |
| Subsea cooler | outlet T, approach to the sea temperature, or cooler area; U, sea T, ΔP | passive seawater cooler: duty to the sea (costs no utility), required area from the LMTD, hydrate check at the outlet |
| Pressure intensifier | area ratio or outlet pressure; hydraulic supply pressure; efficiency | hydraulic piston booster for chemical injection: P_out = P_hyd · ratio · η, work V·ΔP/η, hydraulic-fluid return flow |
| Chemical injection valve (CIMV) | minimum ΔP across the valve | mixes the chemical into the production stream; error if the chemical arrives below production P + ΔP_min; reports dosage (ppm, wt % inhibitor in water) and hydrate margin after injection |

Every SURF line unit has a **Profile** tab (P, T, holdup, velocity, elevation and hydrate margin along the
line). The **🌊 Subsea (SURF)** tab summarises the wells (Pwf, drawdown, wellhead conditions, rates, rate/AOF)
and the subsea equipment, draws a **pressure budget** from the reservoir to the arrival for any well, and holds
the catalogue.

The SURF tab is split into eleven views:

* **System** — wells, subsea equipment, pressure budget and riser geometry.
* **Equipment & CAPEX** — every subsea item with quantity and cost, rolled up into a class 5 estimate:
  equipment and lines (catalogue *cost_MUSD*; flowlines and risers per km at a 10-inch bore, scaled by
  (ID/254 mm)^0.7), installation %, wells (drilling & completion per well), umbilical and power cable per km,
  topside power per MW of subsea load, engineering % and contingency %. The cost basis is saved with the
  flowsheet; editing it never re-solves. Download the equipment list as CSV.
* **Umbilical & power** — every chemical-injection service (editable table: fluid, flow, delivery pressure;
  0 = highest wellhead pressure + 10 bar) gets the smallest standard tube meeting the allowable friction ΔP
  and velocity, with the topside pump pressure (delivery + friction − hydrostatic head) and pump power. Subsea
  boosters get a 3-phase AC power cable: the lowest voltage (6.6–33 kV) and smallest conductor meeting
  ampacity, voltage drop and a charging-current limit; long step-outs that fail are flagged.
* **Tie-back screening** — flowline + riser recomputed over a grid of distances and rate factors from the solved
  flowline inlet, with an optional ideal boost: arrival pressure, hydrate margin, and the maximum tie-back
  distance for a minimum arrival pressure (interpolated, or a lower bound when the next distance cannot
  deliver the rate).
* **Field layout** — a plan view built from the flowsheet: host at the origin, each riser's touch-down at its
  horizontal footprint, flowlines and jumpers to scale along a bearing you set, well slots on a ring around their
  template (not to scale), satellites on their own bearing, and the umbilical route.
* **Turndown** — flowline + riser recomputed over a range of rates from the solved flowline inlet: arrival
  pressure, hydrate margin, liquid inventory and erosional ratio. The operating window is the continuous range
  around the current rate that meets every limit (minimum arrival pressure, hydrate margin ≥ 0, erosional ratio
  ≤ 1, no severe riser-base slugging), with the constraint that sets each end, the turndown ratio and the liquid
  swept out when ramping from minimum to maximum rate.
* **Slugging** (transient-lite) — hydrodynamic slugs at each riser base: Gregory & Scott (1969) frequency,
  Norris (1982) mean slug length with a log-normal 1-in-1000 maximum (σ = 0.5), Gregory et al. (1978) slug-body
  holdup. The severe slug a blocked riser would form: riser fill plus the liquid pushed back into the flowline
  from a gas balance, (P0 + ΔP)(V_g − x) = P0 (V_g + Q_g0 (V_r + x)/Q_l), and its build-up time. The arrival surge
  volume to design for is the larger of the governing slug and the ramp-up sweep-out, with a margin. Confirm with
  a dynamic multiphase model.
* **Cool-down** — after a shutdown each point of the operating profile cools exponentially to the sea
  temperature, τ = C′/(U·πD), with C′ the heat stored per metre in the contents (in-situ holdup), the steel wall
  and part of the insulation (basis editable). The no-touch time is how long until the fluid reaches the hydrate
  temperature at the shut-in (settle-out) pressure — the time available before depressurising or inhibiting.
  *U after shut-in*: the flowing U (conservative) or natural convection (1/U = 1/U_flowing − 1/h_forced + 1/h_natural,
  h_forced from Dittus-Boelter, h_natural 10–60 W/m²K). **Depressurisation**: the hydrate pressure at the seabed
  temperature (selected hydrate model, inhibitor depression applied), the isothermal blowdown time of the gas
  inventory through a vent to below it (per line and for the whole system), and the low-point pressure the liquid
  left in the line keeps — when that is above the hydrate pressure, depressurisation alone cannot protect the line.
* **Heated lines** — every heated flowline: set temperature (hydrate T at settle-out or wax appearance T, plus a
  margin), heat and power while flowing, power to **hold** the shut-in line (U·πD·(T_set − T_sea) per metre)
  and whether the installed heating can, the **heat-up time** from sea temperature at the installed power, the
  no-touch time the line would have without heating, and the **annual energy** (continuous heating, or heating
  only through shutdowns). The chart overlays the heated and unheated temperature profiles and the heat input
  along the line. Electrical heating is charged as power and a hot-water bundle as topside heating in the
  economics; CAPEX adds the heating system per heated km.
* **Wax & sand** — the wax appearance temperature (lab value, or estimated with a multi-solid ideal model on Won's
  n-paraffin melting points and heats of fusion, counting a share of each heavy cut as n-paraffin) and the wax
  deposit growth along each line by molecular diffusion where the wall is colder than the WAT (Wilke-Chang
  diffusivity, linear solubility below the WAT, deposit porosity; heating delivered at the wall is subtracted; no shear
  removal or deposit insulation, so an upper bound) with the pigging interval; sand erosion of a bend
  at each line's highest mixture velocity with DNV-RP-O501 (smooth bend, particle-size factor 1), the years to use
  the erosion allowance and the velocity that erodes 0.1 mm/y.
* **Catalogue** — view, download, upload or reset.

**Seabed route.** A flowline's *Route* tab takes distance [km] / water depth [m] points (or a CSV): the line is
marched section by section along the seabed, its length becomes the route length, and the low points where liquid
collects are counted. **Power.** The subsea cable carries the boosters and the electrical heating; above 6.6 kV a
subsea step-down transformer is added (CAPEX allowance). *Power supply* compares power from shore with local gas
turbines for the flowsheet's power demand: CAPEX, energy and CO₂ cost, the discounted cost over a period and the CO₂
abatement cost of power from shore.

**Catalogue.** The built-in catalogue holds *generic, illustrative* values only (no vendor or company data).
Download it as CSV, edit it or write your own with the same columns, and upload it on the SURF tab: the
catalogue is then stored **in the flowsheet file** (never in the app), and the property views offer your items.
The load examples *Subsea field (SURF)* (four wells on a template, a pipe-in-pipe flowline and a lazy-wave riser)
*Subsea boosting (SURF)* (late life, a wet-gas compressor and a 45 km step-out), *Heated flowline (SURF)* (an oil
tie-back held at 25 °C by DEH, methanol through an intensifier and a CIMV), *Subsea compression station (SURF)*
(cooler, gas-liquid separator, dry-gas compressor and liquid pump) and *Subsea separation (SURF)* (water removed
and reinjected subsea, a multiphase pump for oil and gas) show the workflow.

### Well deliverability and subsea boosters

* **Wellhead-pressure specification** — set a well's *Rate* to *Wellhead pressure*: the well finds the rate at
  which inflow and tubing lift deliver that wellhead pressure (bracketing + false position, then a polish on the
  full tubing increments) and writes the rate back to its feed stream; the feed shows "Rate set by". The
  **Compute deliverability curve** button in the well's Profile tab draws wellhead pressure vs rate with the
  operating point — the nodal-analysis view.
* **Booster performance curves** — on the booster's *Performance curve* tab enter (or generate through the
  current point) the design-speed curve of one machine: flow, boost ΔP and efficiency. With a curve, a boost or
  outlet-pressure spec reports the speed to meet it; the *Performance curve* spec lets curve and speed set the
  boost. Machines in parallel split the flow, machines in series share the boost; power, rating and the maximum
  boost are checked per machine.

### Field life: production profile, recovery factor, wells and boosting

**📅 Field life** marches the solved flowsheet through the life of the field and answers the concept questions:
how long the plateau lasts, what recovery factor the development reaches, how many wells to drill, and when (or
whether) to install subsea boosting.

* **Reservoir** — one compositional tank on the Peng-Robinson EOS for all the wells: the pressure solves
  *n·v(p, z) + (W_e + W_inj − W_p)·B_w = HCPV_i·(1 − c_e (p_i − p))*, with c_e = (c_f + c_w S_wc)/(1 − S_wc). A pot
  aquifer adds *W_e = W_aq (c_w + c_f)(p_i − p)*; oil fields can inject water at a voidage-replacement ratio. Below the
  dew or bubble point the tank produces the mobility-weighted mix of its vapour and liquid (Corey relative
  permeabilities with critical saturations), so condensate drops out and the CGR falls, or free gas raises the GOR.
  In place: GIIP (separator gas) or STOIIP; the default is an automatic estimate — enter your own.
* **Water** — the water cut (oil) or water-gas ratio (gas) rises from its current value after breakthrough at a
  recovery factor you set, to a maximum over a recovery interval.
* **Deliverability** — the flowsheet itself, solved with every Xmas-tree choke fully open, wells on their feed
  rate and the well feeds at the tank pressure (each well keeps its share of the initial pressure). The deliverable
  rate is the largest scale on the hydrocarbon rate of all well feeds at which every unit solves, the delivery stream
  arrives above the **minimum delivery pressure** and boosters stay within their rating. These points are solved only
  where the run needs them (a grid of reservoir-pressure ratio × water cut) and interpolated.
* **Constraints** — plateau (facility) rate, minimum delivery pressure, water and liquid handling capacity, booster
  rating; the binding one is reported every year.
* **Wells** — well units carry *Identical wells* (parallel wells with the same inflow and tubing); a well count is
  spread over the well units. A drilling rate ramps the wells in (deliverability scaled with the wells online).
* **Boosting** — as in the flowsheet, never (bypassed), from a given year, or automatically in the step where the
  plateau would otherwise fall off. Booster units have a *Status: Online / Bypassed* switch for the same purpose.
* **Economics** — revenue at the gas and oil prices, CAPEX from the SURF equipment list (wells and trees scale with
  the well count; extra templates when the wells exceed the slots; boosting paid the year before it starts when
  timed), fixed OPEX as % of CAPEX plus the energy and CO₂ cost of each year from the Analysis economics (and the
  water-injection pumping power), NPV (mid-year discounting to first production), IRR, payback, unit cost. Production
  stops at the **economic limit** (operating cash flow negative), where the recovery factor is reported.

**Well count and boosting** runs the field life for several well counts (and boosting options) — in parallel on
machines with several cores — and recommends the case with the highest NPV; **Apply** writes that well count into
the flowsheet with the feed rates set to the plateau. Results are stored with the flowsheet and printed in the
report. Screening only: one tank, lift tables with the initial reservoir fluid (GOR / CGR changes alter the volumes,
not the hydraulics), quarterly steps, illustrative prices.

### HYSYS-style process units (v6.5)

* **Amine contactor** (*Separation*): MEA 15 wt%, DEA 25 wt%, MDEA 40 wt% or activated MDEA 45 wt% against CO₂ and
  H₂S specifications. The removals come from a 2×2 balance on the specifications; circulation follows from the
  acid gas removed and the design rich-minus-lean loading; the heat of absorption warms the solvent; the regenerator
  reboiler (heat) and rich-amine pump (work) are energy streams. A screening model: no tray-by-tray kinetics, no
  degradation or foaming. Outlets: *sweet* and *acid*.
* **Relief valve (PSV)** (*Safety*): API 520 required area for vapour (critical and sub-critical), liquid and
  two-phase relief, API 526 orifice letter (several valves above T), capacity of the chosen orifice, back-pressure
  correction. The *Fire case* takes the heat input from the wetted area of the vessel (API 521, 43.2·F·A^0.82;
  bare, insulated or water-spray) and the latent heat of the contents.
* **Flare** (*Safety*, no outlet): heat release (LHV from the formation enthalpies), Hajek-Ludwig flame length,
  radiation at a receptor (point-source model with the transmissivity and radiated fraction you set), the distance
  to the radiation limit, tip diameter at the chosen Mach number, and the CO₂ and SO₂ emitted.
* **Component splitter**: a specified fraction of each component to the top outlet, optional outlet temperatures; the
  duty is an energy stream. Use it for membranes, molecular sieves and idealised separations.
* **Conversion reactor / Equilibrium reactor** (*Reactors*): reactions written as `C1 + 2 O2 -> CO2 + 2 H2O` in
  the components of the fluid package. Conversion: the fraction of the first reactant of each reaction, limited by
  availability. Equilibrium: one reaction, K(T) from ΔG° and ΔH° at 298 K with the ideal-gas heat capacities of the
  package (Δ Cp integrated), mole-fraction equilibrium. Both with *Outlet temperature* (duty out) or *Adiabatic*.
  Enthalpies include the heats of formation, so the duty is the true heat of reaction plus sensible heat.
* **Valve**: required Cv / Kv (IEC 60534 for liquid, gas and wet gas), choked-flow check, and the opening for a rated
  Cv with an equal-percentage or linear characteristic. **Compressor**: electric motor or gas turbine driver (fuel
  gas, CO₂, ambient derating 0.7 %/K above 15 °C, margin and a warning when the driver is too small).
  Examples: *Amine sweetening*, *Pressure relief*, *Steam reforming*.

### OLGA-style flow assurance (v6.6) — SURF tab, *Regime & corrosion*

* **Flow regime**: the mechanistic regime and holdup beside Beggs & Brill at each line's inlet and outlet.
  Taitel & Dukler (1976) for near-horizontal lines (equilibrium stratified layer; stratified → intermittent / annular
  / dispersed bubble), Taitel, Barnea & Dukler (1980) for inclined and vertical lines; holdups from the stratified
  balance, Butterworth's fit of Lockhart-Martinelli (annular), Bendiksen drift flux (slug, bubble). Slug frequency
  and length are those of the *Slugging* tab.
* **CO₂ corrosion**: de Waard-Lotz-Milliams (1993) rate (kinetics in series with mass transfer, scale-temperature and
  pH corrections), allowance needed over the design life, the pH or inhibitor efficiency that would meet it, and a
  sour-service flag (pH₂S above 0.003 bar). It is the open-literature model, not NORSOK M-506.
* **Emulsion**: Brinkman viscosity of the oil-water dispersion with the inversion water cut of Arirachakaran et al.
  (1989); assumes a stable emulsion.
* **Liquid loading**: Turner and Coleman droplet velocities, margin and minimum stable gas rate for wells and
  upward lines.
* **Pigging**: run time, liquid swept ahead of the pig, arrival rate and time, pressure to push the slug, flags for
  stall, wear and receiver size.
* **Line pack**: gas inventory (real-gas law) and the volume available by drawing the line down to a minimum pressure.
* **Insulation**: the thickness that gives a target no-touch time, with the Cool-down model.
* **PVT table**: densities, viscosities, Z, Bg and enthalpy over a P-T grid of any stream, as a CSV in SI units.

### Topside templates and debottlenecking (v7.1)

* **Six topside examples** (Example selector, names end with *(topside)*): *HP compressor bypass* (a tee splits the gas between
  the compressor and a let-down bypass valve; change the split fraction), *Debottlenecking*, *Two parallel trains*
  (two compressor trains and two scrubber-liquid recycle loops from one inlet), *Water handling and reinjection*
  (degasser, overboard split, seawater make-up, pump and injection wells), *Gas mixing* (three gases let down to a
  header, an Adjust tunes the rich-gas flow to a Wobbe-index target) and *Flare system* (HP and LP headers, PSVs, header
  lines, knock-out drums, flares).
* **Gas quality** on every gas stream (vapour fraction ≥ 0.5): gross calorific value (dry, 15 °C, ideal gas, from the heats of
  combustion of the library components), relative density, Wobbe index and CO₂ mol%. They can be Adjust targets.
* **Separators** have an optional vessel diameter: the gas load against the Souders-Brown limit is then reported (and warned
  about above 100 %), as scrubbers already did.
* **Debottlenecking** (Design tab): the utilisation of every unit with a capacity - scrubber / separator gas load, compressor
  flow against stonewall, driver power against rating, valve opening against 85 %, erosional ratio of lines - and a throughput
  sweep that re-solves the flowsheet at several rates, finds the factor at which each reaches 100 % and ranks them. Limits within
  3 % of the first are reported together. Units without a capacity (coolers, heaters, a separator without a diameter) are not
  checked; surge (turndown) is not part of it.

### Prognosis (v7.0) — *Prognosis* tab

Built on the Field life model; it answers "which strategy, how many wells, what recovery factor, how sure?".

* **Drainage strategy**: oil — depletion, partial and full pressure support by water injection (voidage replacement
  0 / 0.5 / 1) and a larger facility; gas — facility size (plateau 75 / 100 / 125 % of today's rate, facility cost scaled
  with the 0.6 power, the "six-tenths rule"); when the flowsheet has subsea boosting, when it starts. Each option is swept
  over the well counts and kept at its highest-NPV count.
* **Uncertainty**: in-place volume, aquifer size, water breakthrough and price are sampled (Latin hypercube,
  triangular low / base / high) and every sample is run for every well count. Results: P90 (low case, 10th percentile),
  P50 and P10 (high case) of recovery factor and NPV, the chance of a negative NPV, the well count with the highest
  expected NPV and the most robust one (best P90 NPV).
* The deliverability tables of a well count are reused by all its samples; the first run per well count takes about
  two minutes, further samples a few seconds each. Recovery factor of a gas field is mostly set by the abandonment
  pressure, so its range is narrow; the in-place volume mainly moves the plateau length and the NPV.
* Limits: one tank, the same placeholder economics as the Field life tab, independent inputs, facility cost scaling
  by a simple exponent. Treat the numbers as ranking and ranges, not as a reserves estimate.

### Design tools (v6.7) — *Design* tab

* **Pipe sizing**: the flowline (and its riser) re-solved at a list of inside diameters at the present rate: arrival
  pressure, erosional ratio, hydrate margin, riser-base slugging and liquid inventory, with the wall (thin-wall Barlow
  with a design factor and a corrosion allowance) and the steel mass per km. The recommended diameter is the smallest
  that meets every limit.
* **Gas-lift allocation**: the oil rate of each lifted well at its present wellhead pressure for several lift-gas
  rates (the inflow and tubing model is re-solved at every point), the upper concave envelope of each curve and the
  split of a limited gas supply that maximises the total oil (equal marginal gain). Wells that cannot flow at a
  rate leave that point out.
* **ESP sizing**: total dynamic head from the intake and wellhead pressures, mixture density and tubing friction;
  free gas at the intake and a gas-handling verdict; pump options from a small catalogue of **generic illustrative**
  curves at 50 and 60 Hz (affinity laws) with stages, shaft power and motor size. Replace the catalogue with vendor
  data for a real design; viscosity derating is not applied.
* **Pipe network**: any topology, with loops and meshes solved together — nodes with a fixed pressure (P), a supply or
  withdrawal (Q) or none (J); pipes with length, diameter and roughness. Single-phase gas (isothermal, constant Z,
  horizontal) or liquid (with elevation); Churchill friction at each pipe's Reynolds number; flows, node pressures,
  velocities and the erosional ratio. The text is saved with the flowsheet. Multiphase lines belong in the flowsheet.

### Process additions (v6.3)

* **TEG dehydration contactor** (*Separation* palette): inlet scrubber + N theoretical stages of lean triethylene
  glycol. Equilibrium water over the lean glycol from its purity (water activity, γ = 1.0 — within ~3 K of the GPSA
  equilibrium dew-point chart), Kremser absorption with the circulation rate (L TEG per kg water), the water dew point
  of the dried gas from the EOS, rich-glycol purity and the regenerator reboiler duty (charged as heating). An
  optional dew-point specification warns when missed. Example: *Gas dehydration: TEG contactor*.
* **Traced phase envelope**: the stream *Phase envelope* tab adds traced bubble and dew points (bisection on the
  phase count) with the **cricondenbar** (refined by bisection on pressure) and **cricondentherm**.
* **Column free water**: *Free water* = decant from the feeds (feed knock-out) or also from the condenser (reflux-drum
  water boot) to the column's optional *water* outlet; the stage model stays VLE-only.
* **↶ Undo edit** (sidebar) reverses the last change made in a property view (the canvas keeps Ctrl+Z).
* **Validation against your own cases**: `tests/validate_against.py` compares any results with a private CSV of
  reference values (see `docs/VALIDATION.md`), so company or field data never enter the repository.

### Hydrate model (v6)

The **Fluid package** tab selects the hydrate model for the whole flowsheet: the Motiee (1991) gas-gravity
correlation (default) or **van der Waals–Platteeuw**: sI and sII hydrates, Langmuir constants from Kihara cell
potentials, guest fugacities from the Peng-Robinson EOS, reference properties of Dharmawardhana et al. (1980). The
Kihara σ and ε/k were fitted to pure-gas equilibria (CH₄, C₂H₆, C₃H₈, CO₂, i-C₄H₁₀, N₂, H₂S; rms ≤ 0.3 K); a 0.6-gravity
natural gas lands within 1 K of the Katz chart. It responds to composition (CO₂, H₂S, N₂, propane switching the gas to
structure II) where a gravity correlation cannot. MEG and methanol are applied as a temperature depression in both.

### Solver speed (v6)

Two-phase results in systems that can form three phases (gas + free water) now seed the next flash, successive
substitution is accelerated (GDEM, Michelsen) while every phase is established, the three-phase Rachford–Rice runs
on scalar arithmetic, and PH/PS flashes start with a Newton step from the heat capacity: subsea examples solve
2.5–3.5× faster than in v5.4 with identical converged results.

### Scenarios

**⚖️ Scenarios** saves the solved flowsheet as a named case — a copy of every specification plus its key results
(products, power and CO₂, wells and wellhead pressure, arrival P/T, hydrate margin, booster power, CAPEX,
no-touch time, slug surge volume). Change the design, save again and compare in one table (optionally as
differences to a base case) and one chart; load a scenario back onto the canvas, delete it, or download the
comparison. Scenarios are stored in the flowsheet file and printed in the report.

### Solver speed

Recent solutions are kept by flowsheet content, so undo or switching a specification back is instant. If a solve
takes longer than the **Auto-solve time limit** (sidebar, default 10 s), auto-solve pauses until you press
**▶ Solve** — useful on large subsea flowsheets. Equation-of-state mixing terms are cached per temperature.

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
* No thermal conductivity; the pipe model neglects acceleration pressure drop and uses a single inclination per
  segment (chain segments, or use the riser's built-in geometries, for a profile).
* Hydrate temperatures come from a gas-gravity correlation; salts and H₂S/CO₂ effects are ignored, and the
  inhibitor correlations are screening tools (check high MEG concentrations with a rigorous hydrate model).
* Columns are VLE-only (no free-water draw or liquid–liquid split on the trays); no side draws or pump-arounds yet.
* The phase envelope is contoured from a P–T grid of flashes rather than traced, so the critical region is
  approximate.
* SURF: the well model is steady-state nodal analysis (no transient, no gas lift yet); the choke uses a gas-based
  critical ratio; the slugging check is a screening criterion, not a transient simulation. Boosters have no
  performance map (fixed efficiency); CAPEX, umbilical and cable data are generic allowances (class 5). Slugging and
  turndown are steady-state ("transient-lite"); the layout's well slots and bearings are schematic.
* Results are for screening — check critical numbers against a commercial simulator.

### About

ProcessFlow Studio — made by **Merouane Hamdani**. For educational purposes only: an independent teaching and
screening tool, not an official product of, affiliated with, or endorsed by Equinor or AspenTech. The colour theme
is inspired by the Equinor Design System palette; no logos or trademarks are used.
"""
