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
| Field life | tank material balance residual, p/z ∝ moles for a gas, aquifer influx, voidage replacement, IRR zeroes the NPV, deliverability root | fieldlife |
| Balances | component and energy balances on every unit of every example | flowsheet, surf |

## 2. Known limits (screening)

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
