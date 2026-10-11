import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "server"))
_tmp = tempfile.mkdtemp(prefix="adv_layers2_")
os.environ["MCP_FREECAD_PROJECT_DIR"] = _tmp
import FreeCAD as App, Part
from freecad_layers import checkpoint, bom, cam, drawing, verify

def R(tag, msg): print(f"RESULT[{tag}] {msg}", flush=True)

# B1c: truly different parts, same stem + same volume
d3 = App.newDocument("BomMirror")
a = d3.addObject("Part::Box", "Bracket"); a.Length=10; a.Width=20; a.Height=30      # 6000
b = d3.addObject("Part::Cylinder", "Bracket001"); b.Radius=(6000/(3.141592653589793*30))**0.5; b.Height=30  # 6000, a cylinder
d3.recompute()
R("B1c box vs cylinder, same stem+volume", f"{[(r['designation'], r['qty']) for r in bom.collect(d3)]}")

# C2: rollback returns the in-memory modified doc, not the disk checkpoint
doc = App.newDocument("Work")
bx = doc.addObject("Part::Box", "B"); doc.recompute()
checkpoint.save("good", doc=doc)
r1 = checkpoint.restore("good")
r1.getObject("B").Length = 777; r1.recompute()      # the AI 'goes astray' in the restored doc
r2 = checkpoint.restore("good")                     # 'revert to the checkpoint'
R("C2 second restore", f"B.Length={r2.getObject('B').Length.Value} (checkpoint on disk has 10)")

# K1: print the actual drill moves on a hole-less filleted block
d = App.newDocument("CamAdv")
bxx = d.addObject("Part::Box", "Blk"); bxx.Length=40; bxx.Width=30; bxx.Height=10; d.recompute()
fil = d.addObject("Part::Fillet", "Rounded"); fil.Base = bxx
fil.Edges = [(i+1, 3.0, 3.0) for i, e in enumerate(bxx.Shape.Edges)
             if abs(e.Vertexes[0].Point.z - e.Vertexes[1].Point.z) > 1e-6]
d.recompute()
job = cam.make_job(fil); res = cam.drill_holes(job, fil)
out = cam.post_gcode(job, os.path.join(_tmp, "danger.nc"))
lines = open(out).read().splitlines()
R("K1 G-code excerpt", " | ".join([l for l in lines if any(t in l for t in ("G81","G82","G83","G73","G98","G99"))][:8]))
tc = job.Tools.Group[0]
R("K1 tool used", f"{tc.Label} dia={getattr(tc.Tool,'Diameter','?')} -- fillet radius 3mm, no hole exists")

# D1: drawing - unknown direction silently becomes 'front'; huge part on A4 at scale 1
dd = App.newDocument("Draw")
big = dd.addObject("Part::Box", "Big"); big.Length=2000; big.Width=1000; big.Height=500; dd.recompute()
page = drawing.make_page(dd, "P")
v = drawing.add_view(page, big, "isometric")
R("D1 unknown direction 'isometric'", f"Direction={tuple(v.Direction)} (silently front)")
R("D1 2000mm part on A4 at scale 1", drawing.verdict(page))
# template fallback: nonsense name silently picks some template
R("D1 template 'A0_DoesNotExist'", os.path.basename(drawing._find_template("A0_DoesNotExist")))
R("D1 template glob metachar '*'", os.path.basename(drawing._find_template("*")))

# A1: aero input validation
from freecad_layers import aero
for args in [(1.0, 0, 0.25, 10), (1.0, 4, 0.0, 10), (-2.0, 4, 0.25, 10), (1.0, 4, 0.25, 1e9, 1e6)]:
    try:
        R(f"A1 aero{args}", aero.verdict_multirotor(*args))
    except Exception as e:
        R(f"A1 aero{args}", f"{type(e).__name__}: {e}")

# U1: urdf_aug controller name
from freecad_layers import urdf_aug
try:
    urdf_aug.controllers_yaml(["j1"], controller="trajectory")
except Exception as e:
    R("U1 controllers_yaml('trajectory')", f"{type(e).__name__}: {e}")
print("ADV2_DONE")
