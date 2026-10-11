# Adversarial tests of first-party layers — run under freecadcmd. Prints RESULT lines.
import os, sys, tempfile, json, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "server"))
_tmp = tempfile.mkdtemp(prefix="adv_layers_")
os.environ["MCP_FREECAD_PROJECT_DIR"] = _tmp
import FreeCAD as App, Part
from freecad_layers import state, verify, checkpoint, bom, cam, safe_out_path, safe_under

def R(tag, msg): print(f"RESULT[{tag}] {msg}", flush=True)
def guard(tag, fn):
    try: fn()
    except Exception as e: R(tag, f"EXCEPTION {type(e).__name__}: {str(e)[:200]}")

doc = App.newDocument("Adv")

# V1: verify on a non-solid (wire) -> does verdict say OK?
def v1():
    w = doc.addObject("Part::Feature", "Wire")
    w.Shape = Part.makePolygon([App.Vector(0,0,0), App.Vector(10,0,0), App.Vector(10,10,0)])
    doc.recompute()
    R("V1 wire verdict", verify.verdict("Wire"))
guard("V1", v1)

# V2: self-intersecting (bow-tie) prism -> is it caught?
def v2():
    pts = [App.Vector(0,0,0), App.Vector(10,10,0), App.Vector(10,0,0), App.Vector(0,10,0), App.Vector(0,0,0)]
    wire = Part.makePolygon(pts)
    try:
        face = Part.Face(wire)
        solid = face.extrude(App.Vector(0,0,5))
    except Exception as e:
        R("V2", f"could not build bowtie: {e}"); return
    o = doc.addObject("Part::Feature", "Bowtie"); o.Shape = solid; doc.recompute()
    R("V2 bowtie verdict", verify.verdict("Bowtie"))
    try:
        r = solid.check(True); R("V2 raw shape.check(True) returned", repr(r)[:150])
    except Exception as e:
        R("V2 raw shape.check(True) RAISED", f"{type(e).__name__}: {str(e)[:150]}")
guard("V2", v2)

# V2b: two overlapping solids glued in one compound (interference) -> caught?
def v2b():
    a = Part.makeBox(10,10,10); b = Part.makeBox(10,10,10, App.Vector(5,5,5))
    o = doc.addObject("Part::Feature", "Overlap"); o.Shape = Part.makeCompound([a,b]); doc.recompute()
    R("V2b overlapping compound verdict", verify.verdict("Overlap"))
    try:
        r = o.Shape.check(True); R("V2b raw check", repr(r)[:150])
    except Exception as e:
        R("V2b raw check RAISED", f"{type(e).__name__}: {str(e)[:150]}")
guard("V2b", v2b)

# V3: failed recompute keeps stale shape -> verify still says OK?
def v3():
    b = doc.addObject("Part::Box", "StaleBox"); b.Length = 10; doc.recompute()
    b.Length = 0  # invalid
    doc.recompute()
    R("V3 StaleBox state", f"State={b.State} isValid(obj)={b.isValid()} shapeVol={b.Shape.Volume}")
    R("V3 StaleBox verdict", verify.verdict("StaleBox"))
guard("V3", v3)

# V3b: PartDesign Pad on an open sketch -> verify?
def v3b():
    import Sketcher
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sk")
    sk.addGeometry(Part.LineSegment(App.Vector(0,0,0), App.Vector(10,0,0)))
    sk.addGeometry(Part.LineSegment(App.Vector(10,0,0), App.Vector(10,10,0)))
    pad = body.newObject("PartDesign::Pad", "Pad"); pad.Profile = sk; pad.Length = 5
    doc.recompute()
    R("V3b Pad(open sketch) state", f"State={pad.State}")
    R("V3b Pad verdict", verify.verdict("Pad"))
    R("V3b Body verdict", verify.verdict("Body"))
guard("V3b", v3b)

# C1: checkpoint name collisions
def c1():
    b = doc.addObject("Part::Box", "CkBox"); doc.recompute()
    checkpoint.save("v1/fins", doc=doc)
    b.Length = 99; doc.recompute()
    checkpoint.save("v1 fins", doc=doc)   # different name, same sanitized file
    r = checkpoint.restore("v1/fins")
    L = r.getObject("CkBox").Length.Value
    R("C1 collision", f"restored 'v1/fins' -> CkBox.Length={L} (expected 10 if isolated); entries={[c['name'] for c in checkpoint.list_checkpoints()]}")
    R("C1 open docs after restore", f"{list(App.listDocuments().keys())} active={App.ActiveDocument.Name}")
    try:
        r2 = checkpoint.restore("v1/fins"); R("C1 restore twice", f"ok -> {r2.Name}; docs={list(App.listDocuments().keys())}")
    except Exception as e:
        R("C1 restore twice RAISED", f"{type(e).__name__}: {e}")
guard("C1", c1)

# S1: corrupt state.json -> summary()
def s1():
    p = os.path.join(_tmp, "project_state", "state.json")
    open(p, "w").write('{"project": "x", "features": [')
    try:
        state.summary(); R("S1 corrupt state", "summary OK (unexpected)")
    except Exception as e:
        R("S1 corrupt state", f"summary() CRASHES: {type(e).__name__}: {str(e)[:100]}")
    open(p, "w").write('{"project": "old"}')
    try:
        state.summary(); state.record_feature("A", "x"); R("S1b old schema", "ok")
    except Exception as e:
        R("S1b old-schema state", f"CRASH {type(e).__name__}: {e}")
guard("S1", s1)

# S2: summary scale (token-minimal claim)
def s2():
    state.init_project("Big", "big project")
    for i in range(300):
        state.record_feature(f"Feature_{i:03d}", "PartDesign::Pad", {"Length": i})
    for i in range(50): state.add_decision(f"decision {i} " + "x"*80)
    sm = state.summary()
    R("S2 summary size", f"{len(sm)} chars (~{len(sm)//4} tokens) for 300 features - no truncation")
guard("S2", s2)

# P1: safe_out_path writes anywhere / ADS trick / reserved names
def p1():
    for cand in [os.path.join(os.environ.get("TEMP"), "anywhere_probe.csv"),
                 os.path.join(_tmp, "evil.exe:x.csv"),
                 "CON.csv", "nul.csv"]:
        try:
            out = safe_out_path(cand, {".csv"})
            R("P1 safe_out_path accepted", f"{cand!r} -> {out}")
        except Exception as e:
            R("P1 safe_out_path refused", f"{cand!r}: {type(e).__name__} {str(e)[:80]}")
    try:
        out = safe_out_path(os.path.join(_tmp, "evil.exe:x.csv"), {".csv"})
        with open(out, "w") as f: f.write("payload")
        R("P1 ADS", f"wrote -> files now: {os.listdir(_tmp)}")
    except Exception as e:
        R("P1 ADS write", f"{type(e).__name__}: {e}")
guard("P1", p1)

# B1: BOM on a PartDesign body with features & a boolean cut
def b1():
    d = App.newDocument("BomAdv")
    body = d.addObject("PartDesign::Body", "Body")
    box = body.newObject("PartDesign::AdditiveBox", "Box"); box.Length=20; box.Width=20; box.Height=5
    cyl = body.newObject("PartDesign::SubtractiveCylinder", "Hole"); cyl.Radius=2; cyl.Height=10
    d.recompute()
    st = d.addObject("Part::Box", "Stock"); tl = d.addObject("Part::Cylinder", "Cutter")
    cut = d.addObject("Part::Cut", "Plate"); cut.Base, cut.Tool = st, tl
    d.recompute()
    rows = bom.collect(d)
    R("B1 BOM (expected 2 real parts: Body, Plate)", f"{len(rows)} lines: {[(r['designation'], r['qty']) for r in rows]}")
    d2 = App.newDocument("BomLinks")
    s = d2.addObject("Part::Cylinder", "Screw"); s.Radius=1.5; s.Height=10
    for i in range(3):
        l = d2.addObject("App::Link", f"ScrewLink{i}"); l.LinkedObject = s
    d2.recompute()
    rows = bom.collect(d2)
    R("B1b BOM with 3 App::Link instances (expected qty 4)", f"{[(r['designation'], r['qty']) for r in rows]}")
    d3 = App.newDocument("BomMirror")
    a = d3.addObject("Part::Box", "Bracket"); a.Length=10; a.Width=20; a.Height=30
    b = d3.addObject("Part::Box", "Bracket001"); b.Length=30; b.Width=20; b.Height=10
    d3.recompute()
    rows = bom.collect(d3)
    R("B1c two DIFFERENT parts same stem+volume", f"{[(r['designation'], r['qty']) for r in rows]}")
guard("B1", b1)

# K1: CAM picks convex cylinders (fillets/bosses) as 'holes'
def k1():
    d = App.newDocument("CamAdv")
    bx = d.addObject("Part::Box", "Blk"); bx.Length=40; bx.Width=30; bx.Height=10
    d.recompute()
    fil = d.addObject("Part::Fillet", "Rounded"); fil.Base = bx
    edges = [(i+1, 3.0, 3.0) for i, e in enumerate(bx.Shape.Edges)
             if abs(e.Vertexes[0].Point.z - e.Vertexes[1].Point.z) > 1e-6]
    fil.Edges = edges; d.recompute()
    holes = cam._hole_faces(fil)
    R("K1 hole candidates on a part with ZERO holes (4 vertical fillets)", f"{holes}")
    try:
        job = cam.make_job(fil); r = cam.drill_holes(job, fil)
        R("K1 drill op generated on a hole-less part", f"{r}")
        out = cam.post_gcode(job, os.path.join(_tmp, "danger.nc"))
        txt = open(out).read()
        R("K1 G-code", f"{len(txt.splitlines())} lines, drill cycle present={('G81' in txt) or ('G83' in txt)}")
        R("K1 G-code drill lines", " | ".join([l for l in txt.splitlines() if l.startswith(("G81","G83","G73"))][:6]))
    except Exception as e:
        R("K1 cam", f"{type(e).__name__}: {str(e)[:200]}")
    d2 = App.newDocument("CamRod")
    rod = d2.addObject("Part::Cylinder", "Rod"); rod.Radius=10; rod.Height=50; d2.recompute()
    R("K1b round bar 'holes'", f"{cam._hole_faces(rod)}")
guard("K1", k1)

print("ADV_DONE", _tmp)
