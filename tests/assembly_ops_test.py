"""The base's assembly joints against known answers, called directly under
freecadcmd (no FreeCAD instance, no socket).

Run with:  A:\\FreeCAD\\bin\\freecadcmd.exe install\\assembly_ops_test.py
Each joint is built between boxes/cylinders whose landing place is known, then
the LINK (what solve moves) is measured: Placement and optimalBoundingBox.
Also: replies that claimed success for joints that did not hold (conflicts,
limits), a vertex off its element, the reverse option (audit 2026-11-13).
ASSEMBLY_OPS_AICOPILOT=<dir> / ASSEMBLY_OPS_SERVER=<dir> run it against other
copies of the AICopilot addon / of server/ (freecad_layers).
Sentinel: ASSEMBLY_OPS_OK
"""
import math
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.environ.get("ASSEMBLY_OPS_AICOPILOT")
                or str(REPO / "server" / "freecad-mcp" / "AICopilot"))
sys.path.insert(0, os.environ.get("ASSEMBLY_OPS_SERVER") or str(REPO / "server"))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

V = App.Vector
FAILS = []


def check(what, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + (f"  [{detail}]" if detail else ""), flush=True)
    if not ok:
        FAILS.append(what)


def fresh(name):
    for d in list(App.listDocuments()):
        App.closeDocument(d)
    doc = App.newDocument(name)
    App.setActiveDocument(doc.Name)
    return doc


def bbox(obj):
    b = Part.getShape(obj).optimalBoundingBox(False, False)
    return (b.XMin, b.XMax, b.YMin, b.YMax, b.ZMin, b.ZMax)


def at(obj, want, tol=1e-6):
    got = bbox(obj)
    return all(abs(g - w) <= tol for g, w in zip(got, want)), "bbox " + ", ".join(f"{g:.4f}" for g in got)


def handler():
    from handlers.assembly_ops import AssemblyOpsHandler
    return AssemblyOpsHandler()


def boxes(h, name, top_rot=None, ground=True):
    """Base 20x20x10 at the origin (grounded), Top 10x10x5; links Base001/Top001."""
    doc = fresh(name)
    for n, d in (("Base", (20, 20, 10)), ("Top", (10, 10, 5))):
        b = doc.addObject("Part::Box", n)
        b.Length, b.Width, b.Height = d
    doc.recompute()
    h.create_assembly({"name": "Asm"})
    h.add_component({"object_name": "Base"})
    h.add_component({"object_name": "Top"})
    if ground:
        h.ground_part({"object_name": "Base"})
    top = doc.getObject("Top001")
    if top_rot is not None:
        top.Placement = App.Placement(V(40, 0, 0), top_rot)
        doc.recompute()
    return doc, top


ON_TOP = (5, 15, 5, 15, 10, 15)          # Top centred on Base's top face
F6_F5 = {"joint_type": "Fixed", "ref1_object": "Base", "ref1_element": "Face6",
         "ref2_object": "Top", "ref2_element": "Face5"}


def landings():
    h = handler()
    doc, top = boxes(h, "Fixed")
    h.create_joint(F6_F5)
    r = h.solve({})
    ok, d = at(top, ON_TOP)
    check("Fixed Face6<->Face5 lands Top centred on Base, solve success", ok and "success" in r, d + " " + r)
    r = h.set_joint_offset({"joint_name": "FixedJoint", "connector": 1, "z": 5})
    ok, d = at(top, (5, 15, 5, 15, 15, 20))
    check("set_joint_offset z=5 on connector 1 lifts Top by exactly 5", ok, d + " " + r)

    for jt in ("Revolute", "Cylindrical", "Slider", "Distance"):
        doc, top = boxes(h, jt, top_rot=App.Rotation(V(0, 0, 1), 0))
        h.create_joint(dict(F6_F5, joint_type=jt, distance=5) if jt == "Distance" else dict(F6_F5, joint_type=jt))
        if jt == "Distance":
            got = bbox(top)
            check("Distance 5 Face6<->Face5 leaves a 5 mm gap", abs(got[4] - 15) < 1e-6, f"z {got[4]:.4f}")
        else:
            ok, d = at(top, ON_TOP)
            check(f"{jt} Face6<->Face5 seats Top on Base's face centre", ok, d)

    doc, top = boxes(h, "Vertex")
    r = h.create_joint(dict(F6_F5, ref1_vertex="Vertex7", ref2_vertex="Vertex2"))
    ok, d = at(top, (20, 30, 20, 30, 10, 15))
    check("Fixed with vertexes: Top corner (0,0,0) lands on Base corner (20,20,10)", ok, d + " " + r)

    doc, top = boxes(h, "Ball", top_rot=App.Rotation())
    h.create_joint({"joint_type": "Ball", "ref1_object": "Base", "ref1_element": "Vertex7",
                    "ref2_object": "Top", "ref2_element": "Vertex2"})
    p = Part.getShape(top).Vertexes[1].Point
    check("Ball Vertex7<->Vertex2: the two corners coincide", (p - V(20, 20, 10)).Length < 1e-6, str(p))

    # parts in a moved assembly: the joint is relative, the links stay put inside it
    doc, top = boxes(h, "MovedAsm")
    doc.Asm.Placement = App.Placement(V(0, 0, 100), App.Rotation(V(0, 0, 1), 30))
    doc.recompute()
    h.create_joint(F6_F5)
    ok, d = at(top, ON_TOP)
    check("Fixed inside a moved/rotated assembly lands the same (assembly frame)", ok, d)

    # orientation-only joints, Wheel tilted at the start
    for jt, extra, want in (("Parallel", {}, 0.0), ("Perpendicular", {}, 90.0), ("Angle", {"angle": 60}, 60.0)):
        doc = fresh(jt)
        s = doc.addObject("Part::Cylinder", "Shaft"); s.Radius, s.Height = 5, 10
        w = doc.addObject("Part::Cylinder", "Wheel"); w.Radius, w.Height = 12, 3
        doc.recompute()
        h.create_assembly({}); h.add_component({"object_name": "Shaft"}); h.add_component({"object_name": "Wheel"})
        h.ground_part({"object_name": "Shaft"})
        wl = doc.getObject("Wheel001")
        wl.Placement = App.Placement(V(30, 20, 40), App.Rotation(V(1, 1, 0), 20))
        doc.recompute()
        r = h.create_joint(dict({"joint_type": jt, "ref1_object": "Shaft", "ref1_element": "Edge3",
                                 "ref2_object": "Wheel", "ref2_element": "Edge2"}, **extra))
        axis = wl.Placement.Rotation.multVec(V(0, 0, 1))
        got = math.degrees(axis.getAngle(V(0, 0, 1)))
        check(f"{jt}: wheel axis at {want} deg from the shaft axis", abs(got - want) < 1e-6, f"{got:.6f} {r}")


def false_success():
    h = handler()
    # two Fixed joints of Top to two different faces of Base cannot both hold
    doc, top = boxes(h, "Conflict")
    h.create_joint(dict(F6_F5, name="J1"))
    r2 = h.create_joint(dict(F6_F5, name="J2", ref1_element="Face1"))
    r = h.solve({})
    check("conflicting Fixed joints: solve does not report success",
          "success" not in r and "J1" in r and "J2" in r, r)
    check("conflicting Fixed joints: create_joint says the joints do not hold", "NOT SATISFIED" in r2, r2)

    # limits: a headless solve does not enforce them -- solve must say so
    doc = fresh("RevLimit")
    s = doc.addObject("Part::Cylinder", "Shaft"); s.Radius, s.Height = 5, 10
    w = doc.addObject("Part::Cylinder", "Wheel"); w.Radius, w.Height = 12, 3
    doc.recompute()
    h.create_assembly({}); h.add_component({"object_name": "Shaft"}); h.add_component({"object_name": "Wheel"})
    h.ground_part({"object_name": "Shaft"})
    h.create_joint({"joint_type": "Revolute", "ref1_object": "Shaft", "ref1_element": "Edge3",
                    "ref2_object": "Wheel", "ref2_element": "Edge2"})
    h.set_joint_limits({"joint_name": "RevoluteJoint", "angle_min": -10, "angle_max": 10})
    wl = doc.getObject("Wheel001")
    wl.Placement = App.Placement(wl.Placement.Base, App.Rotation(V(0, 0, 1), 90))
    r = h.solve({})
    check("Revolute turned to 90 deg with AngleMax=10: solve reports the limit",
          "success" not in r and "AngleMax" in r, r)
    r = h.set_joint_limits({"joint_name": "RevoluteJoint", "length_min": 0})
    check("length limit on a Revolute is refused", r.startswith("Error") and "Revolute" in r, r)

    doc, top = boxes(h, "SlideLimit")
    h.create_joint(dict(F6_F5, joint_type="Slider"))
    top.Placement = App.Placement(V(5, 5, 40), App.Rotation())
    doc.recompute()
    h.set_joint_limits({"joint_name": "SliderJoint", "length_min": 0, "length_max": 5})
    r = h.solve({})
    check("Slider 30 mm out with LengthMax=5: solve reports the limit",
          "success" not in r and "LengthMax" in r, r)

    try:
        from freecad_layers import kinematics as kin
        doc, top = boxes(h, "NativeConflict")
        h.create_joint(dict(F6_F5, name="J1"))
        h.create_joint(dict(F6_F5, name="J2", ref1_element="Face1"))
        res = kin.native_solve(doc.Asm)
        check("kinematics.native_solve: conflicting joints are not ok", res.get("ok") is False, str(res))
        doc, top = boxes(h, "NativeOk")
        h.create_joint(F6_F5)
        res = kin.native_solve(doc.Asm)
        check("kinematics.native_solve: a satisfied Fixed joint is ok", res.get("ok") is True, str(res))
    except ImportError as e:
        check("freecad_layers importable", False, str(e))


def references():
    h = handler()
    doc, top = boxes(h, "VertexOff")
    r = h.create_joint(dict(F6_F5, ref1_vertex="Vertex8"))   # Vertex8 = (20,20,0): not on Face6
    check("a vertex that is not on the element is refused, no joint left",
          "not a vertex of" in r and not [o for o in doc.Objects if o.Name.startswith("FixedJoint")], r)

    # Top starting upside down: the solver keeps the flipped connector -> overlap
    doc, top = boxes(h, "Flipped", top_rot=App.Rotation(V(1, 0, 0), 180))
    r = h.create_joint(F6_F5)
    check("upside-down start: the reply warns the parts overlap", "overlap" in r, r)
    doc, top = boxes(h, "Reversed", top_rot=App.Rotation(V(1, 0, 0), 180))
    r = h.create_joint(dict(F6_F5, reverse=True))
    h.solve({})
    ok, d = at(top, ON_TOP)
    check("upside-down start + reverse=true: Top sits on Base, no warning", ok and "overlap" not in r, d + " " + r)
    r = h.create_joint(dict(F6_F5, joint_type="Ball", ref1_element="Vertex7", ref2_element="Vertex2",
                            reverse=True))
    check("reverse on a Ball joint is refused", "reverse applies only" in r, r)


def components():
    h = handler()
    doc = fresh("Placed")
    asm_tr = App.Placement(V(0, 0, 7), App.Rotation(V(0, 0, 1), 15))
    top = doc.addObject("Part::Box", "Top")
    top.Length, top.Width, top.Height = 10, 6, 4
    top.Placement = App.Placement(V(100, 0, 0), App.Rotation(V(1, 0, 0), 90))
    cont = doc.addObject("App::Part", "Holder")
    inner = doc.addObject("Part::Box", "Inner")
    cont.addObject(inner)
    cont.Placement = App.Placement(V(0, 50, 0), App.Rotation(V(0, 0, 1), 90))
    doc.recompute()
    h.create_assembly({"name": "Asm"})
    doc.Asm.Placement = asm_tr
    doc.recompute()
    for name, src in (("Top", top), ("Inner", inner)):
        r = h.add_component({"object_name": name})
        link = doc.getObject(name + "001")
        doc.recompute()
        g = doc.Asm.getGlobalPlacement() * link.Placement
        check(f"add_component keeps {name} where it is (global placement of the link = the part's)",
              g.isSame(src.getGlobalPlacement(), 1e-9), f"{g} vs {src.getGlobalPlacement()} {r}")
    r = h.add_component({"object_name": "Top", "x": 1, "y": 2, "z": 3})
    link = doc.getObject("Top002")
    check("add_component x/y/z set the position, the part's rotation is kept",
          (link.Placement.Base - V(1, 2, 3)).Length < 1e-9
          and link.Placement.Rotation.isSame(asm_tr.Rotation.inverted() * top.Placement.Rotation, 1e-9),
          f"{link.Placement} {r}")


def main():
    for part in (landings, false_success, references, components):
        try:
            part()
        except Exception:
            FAILS.append(part.__name__)
            print(traceback.format_exc(), flush=True)
    if FAILS:
        print(f"FAILED: {len(FAILS)} check(s): {FAILS}", flush=True)
    else:
        print("ASSEMBLY_OPS_OK", flush=True)


main()
