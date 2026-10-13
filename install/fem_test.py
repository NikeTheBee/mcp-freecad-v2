"""FEM exactness test: CalculiX results against closed-form values (headless).

Run with:  A:\\FreeCAD\\bin\\freecadcmd.exe install\\fem_test.py

Known values (linear elasticity), steel E = 210000 MPa unless said otherwise:
- cantilever 100x10x10 mm, fixed at x = 0, 100 N down at the tip:
  delta = F*L^3 / (3*E*I) = 0.1905 mm; the same in aluminium scales by E;
- bar in tension, 1000 N on a 10x10 mm section: sigma = 10 MPa, delta = F*L / (E*A);
- the load direction is honoured (up = +z displacement).
Traps: limits that must FAIL, bad inputs that must be refused, a non-solid
that must be UNKNOWN. Prints FEM_OK on success.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))

import shutil  # noqa: E402

import FreeCAD  # noqa: E402
import Part  # noqa: E402
from freecad_layers import fem, verify  # noqa: E402


def _tool(name):
    exe = name + (".exe" if os.name == "nt" else "")
    bundled = os.path.join(FreeCAD.getHomePath(), "bin", exe)
    return shutil.which(name) or (bundled if os.path.isfile(bundled) else None)


missing = [n for n in ("ccx", "gmsh") if not _tool(n)]
if missing:
    print(f"FEM_SKIP: {', '.join(missing)} not found (FreeCAD's Windows build bundles both; "
          "on conda install calculix + gmsh)")
    sys.exit(0)

doc = FreeCAD.newDocument("FemTest")
beam = doc.addObject("Part::Box", "Beam")
beam.Length, beam.Width, beam.Height = 100, 10, 10
doc.recompute()

F, L, b, h = 100.0, 100.0, 10.0, 10.0
I = b * h ** 3 / 12.0


def close(got, want, rel, what):
    err = abs(got - want) / want
    print(f"{what}: got {got:.5g}, closed form {want:.5g}, error {err * 100:.2f} %")
    assert err <= rel, f"{what} off by {err * 100:.2f} % (> {rel * 100:.0f} %)"


# 1. cantilever, steel
r = fem.static("Beam", ["xmin"], [{"faces": "xmax", "force_N": F, "direction": [0, 0, -1]}])
print(fem.summary(r))
assert r["status"] == verify.OK, r
close(r["measures"]["max_disp_mm"], F * L ** 3 / (3 * 210000 * I), 0.02, "cantilever steel delta")
close(r["measures"]["vm_away_from_supports_MPa"], F * L * (h / 2) / I, 0.10, "root bending stress")

# direction honoured: the tip goes UP for an upward load
r_up = fem.static("Beam", ["xmin"], [{"faces": "xmax", "force_N": F, "direction": [0, 0, 1]}])
res = [o for o in doc.getObject("FEM_Beam_Analysis").Group if o.isDerivedFrom("Fem::FemResultObject")][0]
dz = [v.z for v in res.DisplacementVectors]
assert max(dz) > 0.18 and min(dz) > -1e-3, (max(dz), min(dz))
print("upward load -> upward tip: OK")

# 2. cantilever, aluminium (delta scales with 1/E)
r_al = fem.static("Beam", ["xmin"], [{"faces": "xmax", "force_N": F, "direction": [0, 0, -1]}],
                  material="aluminium")
close(r_al["measures"]["max_disp_mm"], F * L ** 3 / (3 * 69000 * I), 0.02, "cantilever aluminium delta")

# 3. bar in tension: uniform 10 MPa
bar = doc.addObject("Part::Box", "Bar")
bar.Length, bar.Width, bar.Height = 100, 10, 10
doc.recompute()
r_bar = fem.static("Bar", ["xmin"], [{"faces": "xmax", "force_N": 1000, "direction": [1, 0, 0]}])
print(fem.summary(r_bar))
close(r_bar["measures"]["vm_away_from_supports_MPa"], 1000 / 100.0, 0.05, "tension stress")
close(r_bar["measures"]["max_disp_mm"], 1000 * 100 / (210000 * 100.0), 0.05, "tension elongation")

# 4. traps: limits that must FAIL
r_f = fem.static("Beam", ["xmin"], [{"faces": "xmax", "force_N": F, "direction": [0, 0, -1]}],
                 max_displacement_mm=0.1)
assert r_f["status"] == verify.FAIL and "displacement" in r_f["issues"][0], r_f
r_sf = fem.static("Beam", ["xmin"], [{"faces": "xmax", "force_N": F, "direction": [0, 0, -1]}],
                  min_safety_factor=10)
assert r_sf["status"] == verify.FAIL and "safety factor" in r_sf["issues"][0], r_sf
print("limits exceeded -> FAIL: OK")

# 5. bad inputs are refused, a non-solid is UNKNOWN (never OK)
for bad in (dict(fixed=[], loads=[{"faces": "xmax", "force_N": 1}]),
            dict(fixed=["top"], loads=[{"faces": "xmax", "force_N": 1}]),
            dict(fixed=["xmin"], loads=[{"faces": "xmax", "force_N": 1, "direction": [0, 0, 0]}]),
            dict(fixed=["xmin"], loads=[{"faces": "xmax", "force_N": 1}], material="unobtainium")):
    try:
        fem.static("Beam", **bad)
    except ValueError as e:
        print("refused:", str(e)[:70])
    else:
        raise AssertionError(f"accepted bad input {bad}")
# a hopeless mesh is refused BEFORE meshing (Gmsh used to grind first), with a size that works
import time  # noqa: E402
t0 = time.time()
try:
    fem.static("Beam", ["xmin"], [{"faces": "xmax", "force_N": 1}], mesh_size_mm=0.01)
except ValueError as e:
    msg = str(e)
    print("refused:", msg)
    assert "nodes" in msg and "mm or more" in msg and time.time() - t0 < 5, (msg, time.time() - t0)
    hint = float(msg.split("use ")[1].split(" mm")[0])
    assert fem.estimated_nodes(doc.Beam.Shape, hint) <= fem.MAX_NODES, hint
else:
    raise AssertionError("a 0.01 mm mesh was accepted")
# the estimate stays BELOW what Gmsh really made (never refuses a mesh that would fit)
est = fem.estimated_nodes(doc.Beam.Shape, r["measures"]["mesh_size_mm"])
assert est <= r["measures"]["nodes"], (est, r["measures"])
print(f"hopeless mesh refused before meshing, with a usable size "
      f"(estimate {est} <= {r['measures']['nodes']} real nodes): OK")
sheet = doc.addObject("Part::Feature", "Sheet")
sheet.Shape = Part.makePlane(10, 10)
doc.recompute()
r_u = fem.static("Sheet", ["xmin"], [{"faces": "xmax", "force_N": 1}])
assert r_u["status"] == verify.UNKNOWN, r_u
print("non-solid -> UNKNOWN: OK")

# 6. reruns replace the previous analysis instead of piling up results (the Beam was
#    solved 6 times above, the Bar once): one result object per analysis, no orphans
analyses = [o for o in doc.Objects if o.Name.startswith("FEM_") and o.Name.endswith("_Analysis")]
results = [o for o in doc.Objects if o.isDerivedFrom("Fem::FemResultObject")]
assert len(analyses) == 2 and len(results) == 2, ([o.Name for o in analyses], [o.Name for o in results])
print(f"{len(doc.Objects)} objects after 7 solves, one result per analysis: OK")
print("FEM_OK")
