"""Create a comparison sheet for validate_against.py from a flowsheet (built-in example name or saved .json).

    python tests/make_reference_template.py "<example name or flowsheet.json>" my_cases.csv [tolerance_percent]

The CSV is pre-filled with every numeric unit result and stream property the app computes (column `app_value`, for
information) and an EMPTY `reference` column.  Type your HYSYS / Aspen / PIPESIM / field values into `reference`
(same units as the label) for the rows you want to check, delete the other rows, then run

    python tests/validate_against.py my_cases.csv report.csv

`tolerance` is pre-set to the given percentage (default 2 %) of the app value, so only change it where you have a reason.
Keep the filled sheet OUTSIDE the public repository.
"""
from __future__ import annotations

import csv
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from validate_against import load                         # noqa: E402
from procsim.flowsheet import solve                      # noqa: E402
from procsim.streams import stream_properties            # noqa: E402


def rows_for(flowsheet, tol_pct=2.0):
    m = load(flowsheet, os.getcwd())
    sol = solve(m)
    out = []
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)           # noqa: E731
    for uid, u in m["units"].items():
        for q, v in (sol.results.get(uid) or {}).items():
            if num(v):
                out.append((u["name"], q, v))
    for sid, s in m["streams"].items():
        if sid in sol.streams:
            for q, v in stream_properties(sol.streams[sid], sol.fp).items():
                if num(v):
                    out.append((s["name"], q, v))
    return [{"case": os.path.basename(str(flowsheet))[:40], "flowsheet": flowsheet, "object": o, "quantity": q,
             "app_value": v, "reference": "", "tolerance": f"{abs(v) * tol_pct / 100:.6g}"} for o, q, v in out]


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    rows = rows_for(sys.argv[1], float(sys.argv[3]) if len(sys.argv) > 3 else 2.0)
    with open(sys.argv[2], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["case", "flowsheet", "object", "quantity", "app_value", "reference", "tolerance"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows written to {sys.argv[2]}")
