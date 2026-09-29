"""Tiny check collector shared by the standalone test scripts."""


class Checker:
    def __init__(self, suite):
        self.suite = suite
        self.passed = 0
        self.failed = []

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed.append(f"{name}  [{detail}]")
            print(f"  FAIL  {name}  [{detail}]")

    def close(self, name, a, b, tol):
        self.check(name, a is not None and abs(a - b) <= tol, f"got {a!r}, expected {b!r} ± {tol}")

    def rel(self, name, a, b, rtol):
        self.check(name, a is not None and abs(a - b) <= rtol * abs(b), f"got {a!r}, expected {b!r} ± {rtol:.1%}")

    def within(self, name, a, lo, hi):
        self.check(name, a is not None and lo <= a <= hi, f"got {a!r}, expected in [{lo}, {hi}]")

    def eq(self, name, a, b):
        self.check(name, a == b, f"got {a!r}, expected {b!r}")

    def report(self):
        tot = self.passed + len(self.failed)
        print(f"{self.suite}: {self.passed}/{tot} passed")
        return 1 if self.failed else 0
