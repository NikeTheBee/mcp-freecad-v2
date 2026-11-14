"""Automatic design correction (server/atelier/optimize.py) against closed-form optima.

Run with: py -3 install/optimize_test.py   (no FreeCAD needed). Sentinel: OPTIMIZE_OK
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from atelier.optimize import margins, search  # noqa: E402


def beam(x):
    # cantilever 200 mm, b 20 mm, 1 kN at the tip: sigma = 6 F L / (b t^2); S235
    t = x["t"]
    return {"safety_factor": 235 / (6 * 1000 * 200 / (20 * t * t)), "mass_kg": 7.85e-6 * 200 * 20 * t}


exact = math.sqrt(6 * 1000 * 200 * 2 / (20 * 235))           # thinnest t with SF >= 2
r = search(beam, [{"name": "t", "lo": 5, "hi": 40}], objective="mass_kg",
           constraints={"safety_factor": {"min": 2}}, max_runs=14)
t = r["best"]["x"]["t"]
assert r["feasible"] and t >= exact and abs(t - exact) / exact < 0.002, (t, exact)
print(f"lightest beam: t = {t:.4f} mm (exact {exact:.4f}) in {len(r['runs'])} runs OK")

r = search(beam, [{"name": "t", "lo": 5, "hi": 40}], constraints={"safety_factor": {"min": 2}}, max_runs=14)
assert r["feasible"] and abs(r["best"]["x"]["t"] - exact) / exact < 0.002, r["best"]
print("no objective: the thinnest feasible OK")

r = search(beam, [{"name": "t", "lo": 1, "hi": 5}], constraints={"safety_factor": {"min": 2}}, max_runs=6)
assert not r["feasible"] and r["best"]["x"]["t"] == 5, r["best"]
print("infeasible range: reported, closest design given OK")

r = search(lambda x: {"f": (x["x"] - 3) ** 2 + (x["y"] + 1) ** 2},
           [{"name": "x", "lo": -5, "hi": 5}, {"name": "y", "lo": -5, "hi": 5}], objective="f", max_runs=30)
assert r["best"]["values"]["f"] < 0.02, r["best"]
print("2 variables, interior optimum OK")

calls = []
r = search(lambda x: calls.append(x) or None, [{"name": "t", "lo": 0, "hi": 1}],
           constraints={"sf": {"min": 1}}, max_runs=4)
assert not r["feasible"] and len(calls) == 4, calls            # broken designs: never feasible
assert margins({"a": 3}, {"a": {"min": 2, "max": 4}}) == 0.0 + min((3 - 2) / 2, (4 - 3) / 4)
print("OPTIMIZE_OK")
