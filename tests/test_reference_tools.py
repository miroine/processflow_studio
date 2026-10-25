"""v7.10.1 reference-comparison workflow: template generator + validate_against round trip.

Run:  python tests/test_reference_tools.py
"""
import csv
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from check import Checker                                    # noqa: E402
from procsim.examples import EXAMPLES                        # noqa: E402
import make_reference_template as MT                         # noqa: E402
import validate_against as VA                                # noqa: E402

c = Checker("reference_tools")
name = list(EXAMPLES)[0]
rows = MT.rows_for(name, 1.0)
c.check("template lists unit results and stream properties", len(rows) > 50 and {"object", "quantity", "app_value", "reference", "tolerance"} <= set(rows[0]), str(len(rows)))
c.check("reference column starts empty", all(r["reference"] == "" for r in rows), "")
c.check("tolerance is the requested percentage of the app value", all(abs(float(r["tolerance"]) - abs(r["app_value"]) * 0.01) < 1e-5 * (1 + abs(r["app_value"])) for r in rows), "")
d = tempfile.mkdtemp()
p = os.path.join(d, "t.csv")
pick = [r for r in rows if isinstance(r["app_value"], (int, float)) and abs(r["app_value"]) > 1][:4]
for i, r in enumerate(pick):
    r["reference"] = repr(float(r["app_value"]) * (1.0 if i < 3 else 1.2))          # three equal, one 20 % off
with open(p, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["case", "flowsheet", "object", "quantity", "app_value", "reference", "tolerance"])
    w.writeheader()
    w.writerows(rows)                                        # most rows keep an empty reference -> skipped
rep = os.path.join(d, "r.csv")
import io, contextlib                                         # noqa: E401,E402
with contextlib.redirect_stdout(io.StringIO()):
    rc = VA.main(p, rep)
out = list(csv.DictReader(open(rep, encoding="utf-8")))
c.check("rows without a reference are skipped", len(out) == 4, str(len(out)))
c.check("equal values pass, the 20 % deviation fails", [r["status"] for r in out] == ["pass", "pass", "pass", "FAIL"] and rc == 1, str([r["status"] for r in out]))
c.check("report carries the relative difference", abs(float(out[3]["relative [%]"]) - (100 * (1 / 1.2 - 1))) < 1e-6 or abs(float(out[3]["relative [%]"])) > 10, out[3]["relative [%]"])
sys.exit(c.report())
