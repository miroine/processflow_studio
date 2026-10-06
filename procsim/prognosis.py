"""Production prognosis: drainage-strategy comparison and uncertainty on top of the field-life engine (v7.0).

``fieldlife`` marches ONE case through time.  A prognosis has to answer two more questions:

* **Which drainage strategy?**  The options that are a real choice are compared, each with its own best well count:
  pressure support by water injection (voidage replacement 0 / 0.5 / 1.0) for an oil field, the facility size (plateau
  rate, with the facility cost scaled by the six-tenths rule) and, when the flowsheet has subsea boosting, when it starts.  Each strategy is swept over the well counts and the highest-NPV count is
  kept.
* **How sure are we?**  The reservoir inputs nobody knows (in-place volume, aquifer size, water breakthrough) and the
  price are sampled (Latin hypercube, triangular low / base / high) and every sample is run for every well count.
  The result is P90 / P50 / P10 ranges of recovery factor and NPV per well count, the probability of a negative NPV,
  and the well count that is best on average and the one that is most robust (best P90 NPV).

The expensive part of a field-life run is the deliverability tables of the network, which depend on the well count, the
boosting mode and the plateau rate but not on the reservoir volumes or the prices; every sample therefore reuses the
tables of its well count, and each extra sample costs seconds.  Well counts run in parallel worker processes.

Screening accuracy only: the same one-tank model, the same placeholder economics as the Field life tab.
"""
from __future__ import annotations

import math
import os

import numpy as np

from . import fieldlife as FL
from .flowsheet import solve

PCTS = (10.0, 50.0, 90.0)          # percentiles of the outputs: 10th = low case (P90 in oil-field language)

# uncertain inputs: key -> (label, unit)
UNC_LABELS = {
    "in_place": ("In-place volume", "× base"),
    "aquifer": ("Aquifer size", "× pore volume"),
    "RF_bt": ("Water breakthrough at recovery factor", "-"),
    "price": ("Hydrocarbon price", "× base"),
}
DEFAULT_RANGES = {            # (low, base, high); base values are filled from the settings
    "in_place": (0.65, 1.0, 1.5),
    "aquifer": (None, None, None),
    "RF_bt": (None, None, None),
    "price": (0.7, 1.0, 1.3),
}


class PrognosisError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------- helpers
def _clean(v):
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def _unit_in_place(S):
    """Divisor from Sm³ to the units of the ``in_place`` setting (GSm³ gas / MSm³ oil)."""
    return 1e9 if S["kind"] == FL.GAS else 1e6


def _trim(r):
    """The parts of a run result worth sending back from a worker."""
    keep = ("Year", "Gas [MSm³/d]", "Oil/condensate [Sm³/d]", "Water cut / WGR", "Reservoir P [bar(a)]",
            "Delivery P [bar(a)]", "Cash flow [MUSD]")
    ann = [{k: row.get(k) for k in keep if k in row} for row in r["annual"] if row["Year"] >= 1]
    return _clean({"summary": r["summary"], "annual": ann, "N": r["N"], "boost_mode": r["boost_mode"],
                   "capex": r["capex"]})


def _batch_worker(args):
    """All scenarios of one well count in one process, sharing the network deliverability tables."""
    model, p_base, N, scen = args
    sol = solve(model)
    pools = {}
    out = []
    for label, over in scen:
        p = dict(p_base, **over)
        S = FL.setup(model, sol, p)
        # the deliverability nodes are checked against the plateau and the water grid they were built for, so only
        # scenarios with the same plateau and water grid share tables (reservoir volume, prices, VRR do not matter)
        key = (round(float(S["q_plat"]), 9), tuple(round(float(x), 9) for x in FL._w_grid(S)))
        r = FL.run(model, sol, p=p, N=N, S=S, networks=pools.setdefault(key, {}))
        out.append((label, N, _trim(r)))
    return out


def batch(model, sol, scenarios, counts, p=None, workers=None, progress=None):
    """Run every scenario (label, overrides) for every well count.  Returns {(label, N): trimmed run}.

    Well counts run in parallel (fork); a failure of the pool falls back to running here."""
    p_base = dict(FL.params(model) if p is None else p)
    jobs = [(model, p_base, int(N), list(scenarios)) for N in counts]
    results = {}
    workers = workers or min(len(jobs), os.cpu_count() or 1)
    if workers > 1 and len(jobs) > 1:
        try:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor, as_completed
            ctx = mp.get_context("fork")
            with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
                futs = [ex.submit(_batch_worker, j) for j in jobs]
                for f in as_completed(futs):
                    for label, N, r in f.result():
                        results[(label, N)] = r
                    if progress:
                        progress(len({k[1] for k in results}), len(jobs))
        except Exception:                                          # noqa: BLE001  (no fork, pickling, pool died)
            results = {}
    done = {k[1] for k in results}
    for j in jobs:
        if j[2] in done:
            continue
        for label, N, r in _batch_worker(j):
            results[(label, N)] = r
        if progress:
            progress(len({k[1] for k in results}), len(jobs))
    return results


# ------------------------------------------------------------------------------------------------- strategies
FAC_EXP = 0.6                      # facility cost ~ (plateau rate)^0.6 (six-tenths rule) in the facility-size options


def strategy_options(S):
    """The drainage options that make sense for this field: list of (label, overrides).

    * oil: pressure support by water injection (VRR 0 / 0.5 / 1) and a larger facility with full support;
    * gas: facility size (plateau rate 75 / 100 / 125 % of today's, facility cost scaled with the 0.6 power);
    * both, when the flowsheet has subsea boosting: when the boosting starts."""
    opts = []
    boost = S["has_boost"]
    q0 = float(S["q_plat"])
    ip = S["in_place"] / _unit_in_place(S)          # held fixed: an automatic volume would follow the plateau rate
    if S["kind"] == FL.OIL:
        for v, nm in ((0.0, "Depletion (no injection)"), (0.5, "Partial pressure support (VRR 0.5)"),
                      (1.0, "Full pressure support (VRR 1.0)")):
            opts.append((nm, {"VRR": v, "in_place": ip}))
        opts.append(("Full support, plateau 125 % (larger facility)", {"VRR": 1.0, "q_plat": 1.25 * q0, "fac_exp": FAC_EXP, "in_place": ip}))
        if boost:
            opts.append(("Full support + automatic boosting", {"VRR": 1.0, "boost_mode": FL.B_AUTO, "in_place": ip}))
    else:
        for f, nm in ((0.75, "Plateau 75 % (smaller facility)"), (1.0, "Plateau 100 % (as in the flowsheet)"),
                      (1.25, "Plateau 125 % (larger facility)")):
            opts.append((nm, {"q_plat": f * q0, "fac_exp": FAC_EXP, "in_place": ip}))
        if boost:
            opts.append(("Plateau 100 % + compression when the plateau falls off",
                         {"q_plat": q0, "fac_exp": FAC_EXP, "boost_mode": FL.B_AUTO, "in_place": ip}))
            opts.append(("Plateau 100 % + compression from year 1",
                         {"q_plat": q0, "fac_exp": FAC_EXP, "boost_mode": FL.B_YEAR, "boost_year": 1.0, "in_place": ip}))
    return opts


def compare_strategies(model, sol, counts=None, p=None, options=None, workers=None, progress=None):
    """Sweep every drainage option over the well counts.  Returns (rows, results, best) where ``rows`` has one row per
    option with its best well count (highest NPV), ``results`` is {(label, N): run} and best is the winning key."""
    S = FL.setup(model, sol, p)
    options = options or strategy_options(S)
    counts = sorted({int(n) for n in (counts or FL.default_counts(S))})
    res = batch(model, sol, options, counts, p=p if p is not None else None, workers=workers, progress=progress)
    rows = []
    for label, _ in options:
        cand = [(N, res[(label, N)]) for N in counts if (label, N) in res]
        if not cand:
            continue
        N, r = max(cand, key=lambda t: t[1]["summary"]["NPV [MUSD]"])
        sm = r["summary"]
        rows.append({"Strategy": label, "Best wells": N, "Recovery factor [%]": round(float(sm["Recovery factor [%]"]), 2),
                     "Plateau [years]": sm["Plateau length [years]"], "Production years": sm["Production years"],
                     "NPV [MUSD]": round(float(sm["NPV [MUSD]"]), 1), "CAPEX [MUSD]": round(float(sm["CAPEX [MUSD]"]), 1),
                     "Unit cost [USD/boe]": sm["Unit cost [USD/boe]"], "Boosting starts (year)": sm["Boosting starts (year)"]})
    if not rows:
        raise PrognosisError("no case could be run")
    best_label = max(rows, key=lambda r: r["NPV [MUSD]"])["Strategy"]
    best_N = next(r["Best wells"] for r in rows if r["Strategy"] == best_label)
    return rows, res, (best_label, best_N)


# ------------------------------------------------------------------------------------------------- uncertainty
def default_ranges(S):
    """Low / base / high of the uncertain inputs, from the field-life settings."""
    p = S["p"]
    base_aq = float(p["aquifer"])
    base_bt = float(p["RF_bt"])
    r = dict(DEFAULT_RANGES)
    r["aquifer"] = (0.0, base_aq, max(2.0 * base_aq, 1.0))
    r["RF_bt"] = (max(0.01, 0.5 * base_bt), base_bt, min(1.0, 1.6 * base_bt))
    return r


def triangular(u, low, mode, high):
    """Inverse CDF of the triangular distribution (vectorised)."""
    u = np.asarray(u, float)
    if high - low < 1e-15:
        return np.full_like(u, mode)
    c = (mode - low) / (high - low)
    lo = low + np.sqrt(np.maximum(u * (high - low) * (mode - low), 0.0))
    hi = high - np.sqrt(np.maximum((1.0 - u) * (high - low) * (high - mode), 0.0))
    return np.where(u < c, lo, hi)


def latin_hypercube(n, keys, seed=1):
    """n × len(keys) stratified uniforms; every column is a shuffled set of one point per stratum."""
    rng = np.random.default_rng(seed)
    u = np.empty((n, len(keys)))
    for j in range(len(keys)):
        u[:, j] = (rng.permutation(n) + rng.random(n)) / n
    return u


def make_samples(S, ranges, n=30, seed=1, active=None):
    """Override dicts (settings of the field-life run) for n samples; the first sample is the base case."""
    p = S["p"]
    keys = [k for k in UNC_LABELS if (active is None or k in active)]
    u = latin_hypercube(max(n - 1, 1), keys, seed)
    base_ip = S["in_place"] / _unit_in_place(S)
    out = [{"_i": 0, "_vals": {k: ranges[k][1] for k in keys}}]
    for i in range(u.shape[0]):
        vals = {k: float(triangular(u[i, j], *ranges[k])) for j, k in enumerate(keys)}
        out.append({"_i": i + 1, "_vals": vals})
    samples = []
    for s in out:
        v = s["_vals"]
        over = {}
        if "in_place" in v:
            over["in_place"] = base_ip * v["in_place"]
        if "aquifer" in v:
            over["aquifer"] = max(0.0, v["aquifer"])
        if "RF_bt" in v:
            over["RF_bt"] = min(max(v["RF_bt"], 0.001), 1.0)
        if "price" in v:
            over["gas_price"] = float(p["gas_price"]) * v["price"]
            over["oil_price"] = float(p["oil_price"]) * v["price"]
        samples.append((f"s{s['_i']}", over, v))
    return samples


def _pct(a, q):
    return float(np.percentile(np.asarray(a, float), q))


def uncertainty(model, sol, counts=None, n=30, ranges=None, p=None, seed=1, active=None, base_boost=None,
                workers=None, progress=None):
    """Monte Carlo over the uncertain inputs for every well count.

    Returns a dict: rows (one per well count: expected NPV, P90 / P50 / P10 of NPV and recovery factor, probability of
    negative NPV), samples (the input table), per_run (the NPV / RF of every sample and count), best (highest expected
    NPV), robust (highest P90 NPV), and bands (P90 / P50 / P10 of the annual production rate for the best count)."""
    S = FL.setup(model, sol, p)
    if S["in_place_auto"]:
        # allowed, but the caller should say that the base volume is only an automatic estimate
        pass
    ranges = dict(default_ranges(S), **(ranges or {}))
    counts = sorted({int(x) for x in (counts or FL.default_counts(S))})
    n = int(max(3, min(n, 200)))
    samples = make_samples(S, ranges, n, seed, active)
    base_over = {} if base_boost is None else {"boost_mode": base_boost}
    scen = [(lab, dict(over, **base_over)) for lab, over, _ in samples]
    res = batch(model, sol, scen, counts, p=p, workers=workers, progress=progress)
    gas = S["kind"] == FL.GAS
    rate_key = "Gas [MSm³/d]" if gas else "Oil/condensate [Sm³/d]"
    rows, per_run, rates = [], [], {}
    for N in counts:
        npv, rf, pl, cap = [], [], [], []
        prof = []
        for lab, _, vals in samples:
            r = res.get((lab, N))
            if r is None:
                continue
            sm = r["summary"]
            npv.append(float(sm["NPV [MUSD]"]))
            rf.append(float(sm["Recovery factor [%]"]))
            pl.append(float(sm["Plateau length [years]"]))
            cap.append(float(sm["CAPEX [MUSD]"]))
            prof.append([row.get(rate_key) or 0.0 for row in r["annual"]])
            per_run.append({"Wells": N, "Sample": lab, "NPV [MUSD]": npv[-1], "Recovery factor [%]": rf[-1],
                            "Plateau [years]": pl[-1], **{UNC_LABELS[k][0]: v for k, v in vals.items()}})
        if not npv:
            continue
        L = max(len(x) for x in prof)
        arr = np.array([x + [0.0] * (L - len(x)) for x in prof])
        rates[N] = {"P90": np.percentile(arr, 10, axis=0).tolist(), "P50": np.percentile(arr, 50, axis=0).tolist(),
                    "P10": np.percentile(arr, 90, axis=0).tolist()}
        rows.append({"Wells": N, "Expected NPV [MUSD]": round(float(np.mean(npv)), 1),
                     "NPV P90 [MUSD]": round(_pct(npv, 10), 1), "NPV P50 [MUSD]": round(_pct(npv, 50), 1),
                     "NPV P10 [MUSD]": round(_pct(npv, 90), 1),
                     "P(NPV<0) [%]": round(100.0 * float(np.mean(np.array(npv) < 0)), 0),
                     "RF P90 [%]": round(_pct(rf, 10), 1), "RF P50 [%]": round(_pct(rf, 50), 1),
                     "RF P10 [%]": round(_pct(rf, 90), 1), "Plateau P50 [years]": round(_pct(pl, 50), 1),
                     "CAPEX [MUSD]": round(float(np.mean(cap)), 1)})
    if not rows:
        raise PrognosisError("no sample could be run")
    best = max(rows, key=lambda r: r["Expected NPV [MUSD]"])["Wells"]
    robust = max(rows, key=lambda r: r["NPV P90 [MUSD]"])["Wells"]
    return _clean({"rows": rows, "samples": [{"Sample": lab, **{UNC_LABELS[k][0]: v for k, v in vals.items()}}
                                              for lab, _, vals in samples],
                   "per_run": per_run, "best": best, "robust": robust, "bands": rates.get(best, {}),
                   "bands_all": {str(k): v for k, v in rates.items()}, "kind": S["kind"], "n": n,
                   "ranges": {k: list(v) for k, v in ranges.items()}, "active": active or list(UNC_LABELS),
                   "in_place_auto": bool(S["in_place_auto"]), "rate_key": rate_key})


# ------------------------------------------------------------------------------------------------- recommendation
def recommendation(S, strat=None, unc=None):
    """Plain-language prognosis summary (list of markdown lines): strategy, wells, recovery factor and its range."""
    lines = []
    kind = "gas" if S["kind"] == FL.GAS else "oil"
    if strat:
        rows, best = strat["rows"], strat["best"]
        r = next(x for x in rows if x["Strategy"] == best[0])
        worst = min(rows, key=lambda x: x["NPV [MUSD]"])
        lines.append(f"**Drainage strategy:** {best[0].lower()} with **{r['Best wells']} wells** gives the highest NPV "
                     f"({r['NPV [MUSD]']:,.0f} MUSD) and a recovery factor of {r['Recovery factor [%]']:.0f} %; "
                     f"the weakest option ({worst['Strategy'].lower()}) gives {worst['NPV [MUSD]']:,.0f} MUSD and "
                     f"{worst['Recovery factor [%]']:.0f} %.")
    if unc:
        rows = unc["rows"]
        b = next(x for x in rows if x["Wells"] == unc["best"])
        lines.append(f"**Under uncertainty:** {unc['best']} wells have the highest expected NPV "
                     f"({b['Expected NPV [MUSD]']:,.0f} MUSD; P90-P10 {b['NPV P90 [MUSD]']:,.0f} to "
                     f"{b['NPV P10 [MUSD]']:,.0f}); recovery factor {b['RF P50 [%]']:.0f} % (P50), "
                     f"{b['RF P90 [%]']:.0f}-{b['RF P10 [%]']:.0f} % (P90-P10); chance of a negative NPV "
                     f"{b['P(NPV<0) [%]']:.0f} %.")
        if unc["robust"] != unc["best"]:
            rb = next(x for x in rows if x["Wells"] == unc["robust"])
            lines.append(f"The most robust choice is {unc['robust']} wells (P90 NPV {rb['NPV P90 [MUSD]']:,.0f} MUSD "
                         f"against {b['NPV P90 [MUSD]']:,.0f}): fewer wells give up upside to protect the downside.")
        else:
            lines.append("The same well count is also the best on the downside (P90), so the choice is robust.")
        if unc.get("in_place_auto"):
            lines.append("The in-place volume is the automatic estimate; enter your own in the Field life tab for a "
                         "meaningful range.")
    if not lines:
        lines.append(f"Run the strategy comparison and the uncertainty analysis for this {kind} field.")
    return lines


def compact_strategy(rows, results, best):
    """JSON-safe strategy result to keep with the flowsheet (profiles of the winning case per option only)."""
    prof = {}
    for r in rows:
        run = results.get((r["Strategy"], r["Best wells"]))
        if run:
            prof[r["Strategy"]] = run["annual"]
    return _clean({"rows": rows, "best": list(best), "profiles": prof})
