"""Compare ProcessFlow Studio against your own reference results (PIPESIM, OLGA, HYSYS, field data …).

Keep the reference file OUTSIDE the public repository. Run:

    python tests/validate_against.py my_cases.csv [report.csv]

CSV columns (header row required):
    case        free text
    flowsheet   a saved flowsheet .json (path relative to the CSV) or the name of a built-in example
    object      unit or stream name in that flowsheet
    quantity    a result label exactly as shown in the app (e.g. "Outlet P [bar(a)]", "Wellhead P [bar(a)]") or,
                for streams, a property label (e.g. "Temperature [°C]", "Std gas flow [MSm³/d]")
    reference   the reference value (SI units as in the label)
    tolerance   absolute tolerance in the same unit (optional; blank = report only)

The report lists app value, reference, difference, relative difference and pass / fail, and a summary per case.
"""
from __future__ import annotations

import csv
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from procsim.examples import EXAMPLES                     # noqa: E402
from procsim.flowsheet import solve                       # noqa: E402
from procsim.streams import stream_properties             # noqa: E402


def load(flowsheet, base):
    if flowsheet in EXAMPLES:
        return EXAMPLES[flowsheet]()
    path = flowsheet if os.path.isabs(flowsheet) else os.path.join(base, flowsheet)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def value(model, sol, obj, quantity):
    for uid, u in model["units"].items():
        if u["name"] == obj:
            return (sol.results.get(uid) or {}).get(quantity)
    for sid, s in model["streams"].items():
        if s["name"] == obj and sid in sol.streams:
            return stream_properties(sol.streams[sid], sol.fp).get(quantity)
    raise KeyError(f"no unit or stream named '{obj}'")


def main(csv_path, out_path=None):
    base = os.path.dirname(os.path.abspath(csv_path))
    with open(csv_path, newline="", encoding="utf-8") as f:
        cases = list(csv.DictReader(f))
    cache, rows = {}, []
    for c in cases:
        fs = c["flowsheet"].strip()
        if fs not in cache:
            m = load(fs, base)
            cache[fs] = (m, solve(m))
        m, sol = cache[fs]
        try:
            v = value(m, sol, c["object"].strip(), c["quantity"].strip())
            err = None
        except KeyError as e:
            v, err = None, str(e)
        ref = float(c["reference"])
        tol = float(c["tolerance"]) if (c.get("tolerance") or "").strip() else None
        d = (v - ref) if isinstance(v, (int, float)) else None
        rows.append({"case": c["case"], "object": c["object"], "quantity": c["quantity"], "app": v, "reference": ref,
                     "difference": d, "relative [%]": (100.0 * d / ref) if (d is not None and ref) else None,
                     "status": err or ("no value" if d is None else ("—" if tol is None else
                                                                    ("pass" if abs(d) <= tol else "FAIL")))})
    w = max(len(r["quantity"]) for r in rows) if rows else 10
    for r in rows:
        print(f"{r['case'][:24]:24s} {r['object'][:22]:22s} {r['quantity'][:w]:{w}s} app {r['app']!s:>12.12} "
              f"ref {r['reference']:>10.4g} {r['status']}")
    n_f = sum(1 for r in rows if r["status"] == "FAIL")
    print(f"{len(rows)} comparisons, {n_f} outside tolerance")
    if out_path:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)
    return 1 if n_f else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
