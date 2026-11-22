"""TechDraw 2D drawing test (GAP G1) — runs under freecadcmd.

Builds a dimensioned multi-view sheet for a part via freecad_layers.drawing
and exports DXF headless. Also exercises the secure-by-design refusal paths
(template traversal, silent clobber, wrong extension). Sentinel: TECHDRAW_OK
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))

import FreeCAD as App  # noqa: E402
from freecad_layers import drawing, safe_out_path  # noqa: E402


def _techdraw_ready():
    """TechDraw is an optional-at-build module and its headless HLR backend
    varies by platform. Gate the geometry-dependent asserts on real capability
    so a build without it (some conda-forge headless builds) skips instead of
    false-failing — the feature is validated on the Windows 1.1.1 target."""
    try:
        import TechDraw
    except ImportError as e:
        return None, f"TechDraw module unavailable: {e}"
    import glob
    tdir = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates")
    if not glob.glob(os.path.join(tdir, "*.svg")):
        return None, f"no TechDraw templates under {tdir}"
    if not hasattr(TechDraw, "writeDXFPage"):
        return None, "TechDraw.writeDXFPage missing (GUI-only build)"
    return TechDraw, "ok"


def main() -> int:
    # conda-forge Linux headless TechDraw SEGFAULTS during view/dimension
    # recompute (native crash, no traceback possible) — see COMPATIBILITY.md
    # R11. CI sets this env; the feature is fully validated on the Windows
    # 1.1.1 target and on any build where this test runs.
    if os.environ.get("MCPFC_SKIP_TECHDRAW"):
        print("TECHDRAW_SKIP: MCPFC_SKIP_TECHDRAW set (headless TechDraw "
              "segfaults on this build — R11)")
        return 0

    td_mod, why = _techdraw_ready()
    if td_mod is None:
        print(f"TECHDRAW_SKIP: {why} — validated on the Windows 1.1.1 target")
        return 0

    doc = App.newDocument("TDTest")
    b = doc.addObject("Part::Box", "Bracket")
    b.Length, b.Width, b.Height = 40, 20, 10
    doc.recompute()

    with tempfile.TemporaryDirectory() as td:
        dxf = os.path.join(td, "bracket.dxf")
        res = drawing.sheet_for(b, dxf_path=dxf,
                                views=("front", "top", "right"), scale=2.0)
        print(res)
        # Pipeline invariants (platform-independent): 3 views created and a
        # non-empty DXF written. Exact geometry/dim counts depend on the
        # headless HLR backend, so those are reported, not hard-asserted.
        assert len(res["views"]) == 3, res
        assert Path(dxf).is_file() and Path(dxf).stat().st_size > 0, "no DXF"
        print(f"dxf bytes={Path(dxf).stat().st_size} dims={res['dims']}")
        if res["dims"] < 4 or Path(dxf).stat().st_size < 1000:
            print("note: headless HLR produced reduced geometry (backend-dependent)")

        # every overall dimension present reads the part's real size along its view axes
        # (a wrong number on a workshop plan is worse than a missing one)
        bb = b.Shape.BoundBox
        checked = 0
        for dim in (o for o in doc.Objects if o.TypeId == "TechDraw::DrawViewDimExtent"):
            view = dim.References2D[0][0]
            xd = App.Vector(view.XDirection).normalize()
            yd = App.Vector(view.Direction).cross(xd).normalize()
            axis = xd if dim.Type == "DistanceX" else yd
            want = sum(abs(c) * ln for c, ln in zip((axis.x, axis.y, axis.z),
                                                    (bb.XLength, bb.YLength, bb.ZLength)))
            assert abs(dim.getRawValue() - want) < 1e-6, (view.Name, dim.Type, dim.getRawValue(), want)
            checked += 1
        print(f"{checked} overall dimensions read the part's real size: OK")
        # an iso view first: its projected extent must not be dimensioned as a size
        iso = drawing.sheet_for(b, views=("iso", "front", "top"), scale=1.0)
        iso_dims = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawViewDimExtent"
                    and o.References2D[0][0].Name == iso["views"][0]]
        assert not iso_dims and iso["dims"] >= 1, (iso, [d.Name for d in iso_dims])
        print("iso view gets no overall-size dimension (projected length): OK")

        page = doc.getObject(res["page"])
        v = drawing.verdict(page)
        print(v)
        assert v.startswith("OK"), v

        # point-to-point dimension API
        view = doc.getObject(res["views"][0])
        d = drawing.add_distance_dim(view, (0, 0), (40, 0), "DistanceX")
        assert d is not None

        # --- secure-by-design refusal paths ---
        try:
            drawing.export_dxf(page, dxf)       # exists, no overwrite flag
            print("FAIL: clobber was allowed")
            return 1
        except FileExistsError:
            print("clobber refused without overwrite=True: OK")
        drawing.export_dxf(page, dxf, overwrite=True)   # explicit is fine
        try:
            safe_out_path(os.path.join(td, "evil.dll"), {".dxf"})
            print("FAIL: wrong extension accepted")
            return 1
        except ValueError:
            print("wrong extension refused: OK")
        try:
            from freecad_layers import safe_under
            safe_under(Path(td), "..\\..\\evil.svg")
            print("FAIL: traversal accepted")
            return 1
        except ValueError:
            print("path traversal refused: OK")

        # per-view SVG fragment (text-friendly preview)
        svg = drawing.view_svg(view)
        assert svg.strip().startswith("<") and len(svg) > 50

        # --- audit D1–D3: no silent fallbacks -----------------------------
        try:
            drawing.add_view(page, b, "isometric")
            print("FAIL: unknown direction silently accepted"); return 1
        except ValueError:
            print("unknown view direction refused: OK")
        try:
            drawing.make_page(doc, "Bad", "A0_DoesNotExist")
            print("FAIL: unknown template silently replaced"); return 1
        except FileNotFoundError:
            print("unknown template refused: OK")
        a3 = drawing._find_template("A3_Landscape_ISO5457_minimal")
        assert a3.endswith("A3_Landscape_ISO5457_minimal.svg"), a3

        # a 2 m part: auto scale makes it fit; forcing 1:1 is reported as FAIL
        big = doc.addObject("Part::Box", "Big")
        big.Length, big.Width, big.Height = 2000, 1000, 500
        doc.recompute()
        auto = drawing.sheet_for(big, dims=False)
        print("auto:", auto["scale"], auto["verdict"])
        assert auto["scale"] < 0.1 and auto["verdict"].startswith("OK"), auto
        forced = drawing.sheet_for(big, dims=False, scale=1.0)
        print("forced 1:1:", forced["verdict"])
        assert forced["verdict"].startswith("FAIL") and "exceeds" in forced["verdict"]

        # 4 views: the 4th slot (above the title block) is lower. Sized on the first,
        # the iso view of a 45 mm cube at 1:1 ran 14 mm into the title block, verdict OK
        cube = doc.addObject("Part::Box", "Cube45")
        cube.Length = cube.Width = cube.Height = 45
        doc.recompute()
        four = ("front", "top", "right", "iso")
        auto = drawing.sheet_for(cube, views=four, dims=False)
        assert auto["scale"] == 0.5 and auto["verdict"].startswith("OK"), auto
        forced = drawing.sheet_for(cube, views=four, dims=False, scale=1.0)
        assert forced["verdict"].startswith("FAIL") and "_iso001 outside its slot" \
            in forced["verdict"] and "front" not in forced["verdict"], forced
        print("4 views: each fits its own slot, an iso over the title block FAILs: OK")

        # TechDraw draws a part where it is: a 100 mm bar in an App::Part turned 90 deg
        # shows 100 mm TALL in the top view; sized on its own frame (100 wide, 10 tall)
        # the sheet stayed 1:1 and the view overflowed its slot, verdict OK
        turned = doc.addObject("App::Part", "Turned")
        turned.Placement = App.Placement(App.Vector(), App.Rotation(App.Vector(0, 0, 1), 90))
        bar = doc.addObject("Part::Box", "Bar")
        bar.Length, bar.Width, bar.Height = 100, 10, 10
        turned.addObject(bar)
        doc.recompute()
        tb = drawing.sheet_for(bar, views=("front", "top"), dims=False)
        assert tb["scale"] == 0.5 and tb["verdict"].startswith("OK"), tb
        print("a part in a turned App::Part is sized as TechDraw draws it: OK")

    print("TECHDRAW_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
# Surface any crash on stdout: some CI runners drown stderr in banner noise.
try:
    rc = main()
except Exception:
    import traceback
    print("TECHDRAW_CRASH:\n" + traceback.format_exc())
    rc = 1
if rc:
    sys.stdout.flush()  # freecadcmd drops unflushed output on sys.exit
    sys.exit(rc)
