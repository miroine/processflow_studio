# ProcessFlow Studio — validation basis

ProcessFlow Studio is a screening tool for education. This note lists what each model is checked against
in the automated suites (`python tests/run_all.py`), where the remaining uncertainty is, and how to compare
the app with your own trusted cases without putting them in the public repository.

## 1. Checked against published data or exact solutions

| Area | Reference | Check (suite) |
|---|---|---|
| Peng-Robinson EOS | NIST WebBook: C₃/nC₄/C₂/CO₂ vapour pressures; methane density, Cp, Joule-Thomson coefficient and viscosity at 300 K / 100 bar | thermo |
| Thermodynamic consistency | dH/dT = Cp, Maxwell relations, iso-fugacity at every converged split, flash round trips | thermo |
| Flash speed-ups (v6) | GDEM acceleration, two-phase warm-start seeds, scalar 3-phase Rachford–Rice, Newton PH step — identical converged results to the unaccelerated paths | thermo |
| Hydrates, Motiee | the correlation itself; Hammerschmidt / Nielsen–Bucklin inhibition and their inverses | thermo |
| Hydrates, van der Waals–Platteeuw (v6) | pure-gas equilibria (Deaton & Frost 1946; Sloan & Koh 2008): CH₄, C₂H₆, C₃H₈, CO₂, i-C₄, N₂, H₂S within 0.5 K; 0.6-gravity gas vs the Katz chart within 1 K; structure I/II selection | thermo |
| Pipe hydraulics | Darcy–Weisbach/Colebrook, static head, Beggs & Brill textbook holdup example | flowsheet |
| Rotating equipment | ideal-gas polytropic ODE, isentropic limit, fan laws | flowsheet, surf |
| Columns | every stage in PR equilibrium (‖y − Kx‖ < 1e-7), specifications met, energy closure | flowsheet |
| Wells | IPR formulas (back-pressure, PI, Vogel, AOF); tubing = pipe model; identical wells; gas lift energy and mole balance | surf |
| Riser slugging | Bøe (1981) number from its definition; severe-slug gas balance | surf |
| Cool-down | exponential cool-down and no-touch formula; shut-in U from its definition | surf, flowassure |
| Sand erosion | DNV-RP-O501 (2015) smooth-bend equation reproduced term by term | flowassure |
| Wax | Won (1986) n-paraffin melting points (C₁₀, C₂₀) | flowassure |
| TEG dehydration | Kremser equation; equilibrium water from the lean-glycol activity, within ~3 K of the GPSA equilibrium dew-point chart | process |
| Relief valve / flare | API 520 critical-flow area formula (C coefficient 327.8 at k = 1.11) reproduced term by term; API 526 orifice letters; API 521 fire heat input 43.2·F·A^0.82; Hajek-Ludwig flame length | process |
| Reactors | heat of combustion of CH₄ (802.3 kJ/mol) recovered; reforming ln K(1073 K) ≈ 5.2 with Cp(T) (constant ΔH gives 2.4); atom balances; Le Chatelier trends | process |
| Valve Cv | IEC 60534 gas Kv (N9 = 24.6) and liquid Kv = Q·√(SG/ΔP) from their definitions | process |
| Amine sweetening | component balance, specifications met, specific reboiler duty within the textbook 1-4 MJ/kg acid gas (screening model: fixed rich loading, no tray-by-tray kinetics) | process |
| Mechanistic flow regime | Taitel-Dukler geometry (π/8 at half-full, areas add to π/4) and momentum balance (layer thickens with X, thins downhill); air-water regime maps at 1 bar (5 cm horizontal, 10 cm vertical); drift-flux and Butterworth holdups from their definitions | pack B |
| CO₂ corrosion | de Waard-Lotz-Milliams 1993 reproduced term by term (60 °C, 1 bar, 3 m/s: kinetic 37 mm/y, mass transfer 8 mm/y in series); pH 3.8 of CO₂-saturated water at 25 °C | pack B |
| Emulsions / liquid loading | Arirachakaran inversion (0.5 at 1 cP, 0.28 at 100 cP); Brinkman viscosity; Coleman velocity equals the field-unit formula (≈ 2 m/s for water in 70 bar gas) | pack B |
| Pigging, line pack, insulation | run time = L/v, arrival rate × time = swept volume; pack + minimum inventory = inventory; the returned thickness reproduces the target no-touch time | pack B |
| Pipe network | single gas pipe: P1² − P2² = f L Z R T ṁ²/(D M A²) with Churchill f; liquid pipe: Darcy-Weisbach plus the static head; parallel pipes split in the ratio √(D⁵/f); mass balance at every junction and zero pressure sum around a loop | pack C |
| Pipe sizing | Barlow wall and steel mass from their formulas; the sweep reuses the flowline model of the turndown tab | pack C |
| Gas-lift allocation | greedy equal-marginal-gain split equals the brute-force optimum on random concave curves | pack C |
| ESP | stage head and efficiency at the best-efficiency flow, affinity laws, TDH = ΔP/(ρg), stages and motor cover the head and the power (generic curves, not vendor data) | pack C |
| Prognosis sampling | triangular inverse CDF (limits, mode, mean = (a+b+c)/3), Latin hypercube one point per stratum, reproducible seeds, price scaling of both products | prognosis |
| Prognosis runs | strategy at the base plateau and the uncertainty base sample reproduce a plain field-life run; facility cost scales with the 0.6 power exactly; P90 <= P50 <= P10 | prognosis |
| Field life | tank material balance residual, p/z ∝ moles for a gas, aquifer influx, voidage replacement, IRR zeroes the NPV, deliverability root | fieldlife |
| Balances | component and energy balances on every unit of every example | flowsheet, surf |

## 2. Known limits (screening)

* Prognosis: one reservoir tank, independent uncertain inputs (no correlation between volume and aquifer, say), facility cost by a simple power law, placeholder prices; ranges are for ranking options, not a reserves estimate.
* Steady state only: slugging, cool-down and blowdown are transient-lite estimates.
* Multiphase flow: Beggs & Brill (1973) with Payne corrections — no mechanistic model (OLGA / LedaFlow / PIPESIM).
* Hydrates: inhibitors applied as a temperature depression, not through the water activity.
* Field life: one tank for all wells, lift tables with the initial reservoir fluid, quarterly steps.
* Costs, prices and catalogue data are illustrative placeholders.

## 3. Comparing with your own trusted cases (kept private)

1. Save the flowsheet you want to check (**Save flowsheet (.json)**), or use a built-in example by name.
2. Write a CSV **outside the repository** with the columns
   `case, flowsheet, object, quantity, reference, tolerance`, one row per number to compare. Use the result labels
   shown in the property views (e.g. `Outlet P [bar(a)]`) or the stream property labels (e.g. `Temperature [°C]`).
3. Run `python tests/validate_against.py my_cases.csv report.csv`.

The script solves each flowsheet once, prints app value, reference and pass/fail per row, and writes the report.
Reference cases from company tools or field data therefore never enter the public code base.
