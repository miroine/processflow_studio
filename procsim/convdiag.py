"""Feedback on recycle convergence from Solution.recycle_log (v7.9): for each recycle the last errors, the contraction
per iteration and a plain-language verdict with a suggestion."""
from __future__ import annotations


def summarise(sol):
    """[{recycle, iterations, flow_error, T_error, rate, verdict, hint}] - one row per recycle in the last solve."""
    rows = []
    by = {}
    for r in sol.recycle_log or []:
        by.setdefault(r["recycle"], []).append(r)
    for name, log in by.items():
        log = log[-60:]
        last = log[-1]
        e = [x["flow error"] for x in log]
        rate = None
        if len(e) >= 4 and e[-2] > 1e-14 and e[-4] > 1e-14:
            rate = (e[-1] / e[-4]) ** (1.0 / 3.0) if e[-1] > 0 else 0.0
        bad = not sol.converged
        if not bad:
            verdict, hint = "converged", ""
        elif rate is not None and rate >= 0.98:
            verdict = "stalled or diverging"
            hint = ("The error is not shrinking: check that the recycle stream cannot be pulled out of the loop by a specification (a separator "
                    "or splitter that sends everything one way), switch the method to Direct substitution or loosen the tolerance.")
        elif rate is not None and rate < 0.98:
            verdict = f"converging slowly (factor {rate:.2f} per iteration)"
            hint = "Raise the maximum number of iterations or keep the Wegstein method; a tighter initial guess of the recycle also helps."
        else:
            verdict, hint = "too few iterations to judge", "Raise the maximum number of iterations."
        rows.append({"recycle": name, "iterations": last["iteration"], "flow_error": last["flow error"], "T_error": last["T error [°C]"],
                     "rate": rate, "verdict": verdict, "hint": hint})
    return rows
