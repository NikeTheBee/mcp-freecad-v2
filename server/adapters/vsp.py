"""OpenVSP: conceptual aircraft geometry and vortex-lattice aerodynamics (env `aero`).

OpenVSP (NASA, Vehicle Sketch Pad) builds a parametric aircraft from a handful of numbers and
VSPAERO gives its forces and stability derivatives. Where `aero wing` (AeroSandbox) answers in
milliseconds on a simplified planform, this carries the real lofted geometry: it is the step
between "a wing roughly this size" and a surface FreeCAD can take over as STEP.

Everything is headless and file-based: the adapter writes an AngelScript file, `vspscript.exe`
runs it, and the results come back as CSV. The bundled Python API (built for 3.13) is never
imported.

Kinds:
- vsp_wing   build a parametric wing (span, chord, taper, sweep, dihedral, twist, NACA section),
             run a VSPAERO alpha sweep, and optionally export STEP and STL for FreeCAD
- vsp_model  the same sweep on YOUR OpenVSP model (`params.files = ["aircraft.vsp3"]`)
- vsp_aircraft  a whole small aircraft from a few numbers: lofted fuselage (radius law, nose
             ogive, open nozzle end), swept wing, horizontal tail, vertical fin, optional
             dorsal intake fairing; STEP / STL / .vsp3 for FreeCAD and the OpenVSP GUI, and a
             VSPAERO sweep with the centre of gravity: neutral point and static margin

Units: SI. Lengths m, areas m^2, angles in degrees (`sweep_deg`, `alpha_deg`...). `span_m` is the
FULL span of the mirrored wing; OpenVSP's own section parameter is the half span, which the
adapter converts. Coefficients are referred to `Sref = span x mean chord`, `bref = span`,
`cref = mean chord` unless you give your own.

Limits, restated in every result:
- VSPAERO's vortex lattice is INVISCID and LINEAR: no stall, no separation, no
  compressibility beyond the Prandtl-Glauert correction. `CDi` is the induced drag it computes;
  `CDo` is its own parasite ESTIMATE at the default Reynolds number (reported as
  `Re_millions`), not a validated drag count, so judge `CDi` and treat `CDtot` as indicative;
- this build of VSPAERO exposes the vortex-lattice solver only (no panel mode);
- a lifting surface at a high angle, with separation or a propeller in the way, is outside it:
  that is what `cfd` (SU2) is for;
- the geometry is a loft, not a stress model: thickness and spars are yours to add in FreeCAD.

Exactness: REFERENCES below. The lift slope of a rectangular wing against Helmbold's formula
(the same reference the other lifting-surface adapters are held to), the induced-drag identity
CDi = CL^2 / (pi AR e) against VSPAERO's own span efficiency, zero lift at zero angle for a
symmetric section, and the exported STL's bounding box against the wing that was asked for.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

import csv  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
import struct  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

from _common import result, run_exe, tool  # noqa: E402

ENV = "aero"
KINDS = {
    "vsp_wing": {"timeout": 900,
                 "doc": "parametric wing in OpenVSP + VSPAERO alpha sweep: CL, CDi, CMy, lift "
                        "slope, span efficiency; optional STEP/STL export for FreeCAD"},
    "vsp_model": {"timeout": 900,
                  "doc": "VSPAERO alpha sweep on your own .vsp3 model (params.files)"},
    "vsp_aircraft": {"timeout": 1200,
                     "doc": "fuselage + swept wing + tails (+ intake) from a few numbers: STEP/STL/"
                            ".vsp3, VSPAERO sweep, neutral point and static margin for a CG"},
}


def versions() -> Dict[str, str]:
    out = {}
    try:
        exe = Path(tool("openvsp"))
        out["openvsp"] = exe.parent.name
        ver = exe.parent / "CHANGELOG.md"
        if ver.is_file():
            m = re.search(r"(\d+\.\d+\.\d+)", ver.read_text(encoding="utf-8",
                                                             errors="replace")[:4000])
            if m:
                out["openvsp"] = m.group(1)
    except Exception as e:  # noqa: BLE001
        out["openvsp"] = f"missing ({e})"
    from importlib.metadata import PackageNotFoundError, version
    for p in ("numpy",):
        try:
            out[p] = version(p)
        except PackageNotFoundError:
            out[p] = "missing"
    return out


def _f(x) -> float:
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f"not a finite number: {x!r}")
    return v


def _pos(inp: Dict[str, Any], key: str, default=None) -> float:
    if key in inp:
        v = _f(inp[key])
    elif default is not None:
        v = _f(default)
    else:
        raise ValueError(f"missing input `{key}`")
    if v <= 0:
        raise ValueError(f"`{key}` must be > 0, got {v}")
    return v


# --------------------------------------------------------------------------- script generation
def _sweep_block(inp: Dict[str, Any], sref: float, bref: float, cref: float) -> str:
    """The VSPAERO part of the script: geometry pass, then the alpha sweep."""
    a0 = _f(inp.get("alpha_start_deg", 0.0))
    a1 = _f(inp.get("alpha_end_deg", 8.0))
    n = int(inp.get("alpha_points", 5))
    if n < 1 or n > 40:
        raise ValueError("`alpha_points` must be between 1 and 40")
    if n > 1 and a1 <= a0:
        raise ValueError("`alpha_end_deg` must be greater than `alpha_start_deg`")
    mach = _f(inp.get("mach", 0.0))
    if not 0.0 <= mach < 0.95:
        raise ValueError("`mach` must be in [0, 0.95): VSPAERO is subsonic here")
    if inp.get("panel_method"):
        # the example scripts shipped with OpenVSP still set an `AnalysisMethod` input, but
        # VSPAERO 3.56 no longer exposes it: this build runs the vortex-lattice solver only
        raise ValueError("`panel_method` is not available in this VSPAERO build: it exposes the "
                         "vortex-lattice solver only. Use the `cfd` adapter (SU2) for thickness "
                         "and viscous effects.")
    ncpu = max(1, min(8, int(inp.get("cpu", 2))))
    return f"""
  string compGeom = "VSPAEROComputeGeometry";
  SetAnalysisInputDefaults( compGeom );
  ExecAnalysis( compGeom );

  string sweep = "VSPAEROSweep";
  SetAnalysisInputDefaults( sweep );
  array<double> srefv(1, {sref:.9g});   SetDoubleAnalysisInput( sweep, "Sref", srefv );
  array<double> brefv(1, {bref:.9g});   SetDoubleAnalysisInput( sweep, "bref", brefv );
  array<double> crefv(1, {cref:.9g});   SetDoubleAnalysisInput( sweep, "cref", crefv );
  array<int> refFlag(1, 0);             SetIntAnalysisInput( sweep, "RefFlag", refFlag );
  array<double> a0v(1, {a0:.9g});       SetDoubleAnalysisInput( sweep, "AlphaStart", a0v );
  array<double> a1v(1, {a1:.9g});       SetDoubleAnalysisInput( sweep, "AlphaEnd", a1v );
  array<int> anp(1, {n});               SetIntAnalysisInput( sweep, "AlphaNpts", anp );
  array<double> machv(1, {mach:.9g});   SetDoubleAnalysisInput( sweep, "MachStart", machv );
  array<int> mnp(1, 1);                 SetIntAnalysisInput( sweep, "MachNpts", mnp );
  array<int> cpuv(1, {ncpu});           SetIntAnalysisInput( sweep, "NCPU", cpuv );
  string res = ExecAnalysis( sweep );
  WriteResultsCSVFile( res, "results.csv" );

  while ( GetNumTotalErrors() > 0 )
  {{
    ErrorObj err = PopLastError();
    Print( "VSPERROR " + err.GetErrorString() );
  }}
"""


def _wants_aero(inp: Dict[str, Any]) -> bool:
    """`run_aero: false` builds and exports the geometry without calling VSPAERO: the fast path
    when all you want is a surface to take into FreeCAD."""
    return bool(inp.get("run_aero", True))


def _wing_script(inp: Dict[str, Any], work: Path) -> Tuple[str, float, float, float]:
    span = _pos(inp, "span_m")
    root = _pos(inp, "root_chord_m")
    taper = _f(inp.get("taper", 1.0))
    if not 0.05 <= taper <= 1.0:
        raise ValueError("`taper` (tip chord / root chord) must be between 0.05 and 1")
    sweep = _f(inp.get("sweep_deg", 0.0))
    if abs(sweep) > 70:
        raise ValueError("`sweep_deg` must be between -70 and 70")
    dihedral = _f(inp.get("dihedral_deg", 0.0))
    twist = _f(inp.get("twist_deg", 0.0))
    thick = _f(inp.get("thickness_ratio", 0.12))
    camber = _f(inp.get("camber", 0.0))
    if not 0.01 <= thick <= 0.3 or not -0.1 <= camber <= 0.1:
        raise ValueError("`thickness_ratio` 0.01..0.3 and `camber` -0.1..0.1")
    tess = int(inp.get("span_panels", 20))
    if not 4 <= tess <= 60:
        raise ValueError("`span_panels` must be between 4 and 60")

    tip = root * taper
    mean = 0.5 * (root + tip)
    sref = _f(inp.get("sref_m2", span * mean))
    bref = _f(inp.get("bref_m", span))
    cref = _f(inp.get("cref_m", mean))
    exports = []
    if bool(inp.get("export_step", False)):
        exports.append('  ExportFile( "wing.stp", SET_ALL, EXPORT_STEP );')
    if bool(inp.get("export_stl", False)):
        exports.append('  ExportFile( "wing.stl", SET_ALL, EXPORT_STL );')

    script = f"""// generated by the workshop: parametric wing + VSPAERO
void main()
{{
  string wid = AddGeom( "WING" );
  SetGeomName( wid, "wing" );
  SetParmVal( wid, "Sym_Planar_Flag", "Sym", SYM_XZ );
  SetDriverGroup( wid, 1, SPAN_WSECT_DRIVER, TAPER_WSECT_DRIVER, ROOTC_WSECT_DRIVER );
  SetParmVal( wid, "SectTess_U", "XSec_1", {tess} );
  SetParmVal( wid, "Span", "XSec_1", {span / 2.0:.9g} );
  SetParmVal( wid, "Taper", "XSec_1", {taper:.9g} );
  SetParmVal( wid, "Root_Chord", "XSec_1", {root:.9g} );
  SetParmVal( wid, "Sweep", "XSec_1", {sweep:.9g} );
  SetParmVal( wid, "Sweep_Location", "XSec_1", 0.25 );
  SetParmVal( wid, "Dihedral", "XSec_1", {dihedral:.9g} );
  SetParmVal( wid, "Twist", "XSec_1", {twist:.9g} );
  Update();

  string xsurf = GetXSecSurf( wid, 0 );
  for ( int i = 0 ; i < 2 ; i++ )
  {{
    ChangeXSecShape( xsurf, i, XS_FOUR_SERIES );
    string xs = GetXSec( xsurf, i );
    SetParmVal( GetXSecParm( xs, "ThickChord" ), {thick:.9g} );
    SetParmVal( GetXSecParm( xs, "Camber" ), {camber:.9g} );
  }}
  Update();

  WriteVSPFile( "wing.vsp3", SET_ALL );
  ClearVSPModel();
  ReadVSPFile( "wing.vsp3" );
{chr(10).join(exports)}
{_sweep_block(inp, sref, bref, cref) if _wants_aero(inp) else ""}
}}
"""
    return script, sref, bref, cref


def _surface(name: str, p: Dict[str, Any], defaults: Dict[str, float]) -> Dict[str, float]:
    d = dict(defaults)
    d.update({k: _f(v) for k, v in (p or {}).items()})
    for k in ("span_m", "root_chord_m"):
        if not d.get(k, 0) > 0:
            raise ValueError(f"{name}.{k} must be > 0")
    if not 0.05 <= d["taper"] <= 1.0:
        raise ValueError(f"{name}.taper must be in 0.05..1")
    if abs(d["sweep_le_deg"]) > 70:
        raise ValueError(f"{name}.sweep_le_deg must be in -70..70")
    if not 0.03 <= d["thickness_ratio"] <= 0.25:
        raise ValueError(f"{name}.thickness_ratio must be in 0.03..0.25")
    return d


def _lift_surface_script(var: str, label: str, d: Dict[str, float], fin: bool = False) -> str:
    half = d["span_m"] if fin else d["span_m"] / 2.0     # a fin's span is its height
    return f"""
  string {var} = AddGeom( "WING" );
  SetGeomName( {var}, "{label}" );
  SetParmVal( {var}, "Sym_Planar_Flag", "Sym", {0 if fin else "SYM_XZ"} );
  SetDriverGroup( {var}, 1, SPAN_WSECT_DRIVER, TAPER_WSECT_DRIVER, ROOTC_WSECT_DRIVER );
  SetParmVal( {var}, "SectTess_U", "XSec_1", 14 );
  SetParmVal( {var}, "Span", "XSec_1", {half:.9g} );
  SetParmVal( {var}, "Taper", "XSec_1", {d["taper"]:.9g} );
  SetParmVal( {var}, "Root_Chord", "XSec_1", {d["root_chord_m"]:.9g} );
  SetParmVal( {var}, "Sweep", "XSec_1", {d["sweep_le_deg"]:.9g} );
  SetParmVal( {var}, "Sweep_Location", "XSec_1", 0.0 );
  SetParmVal( {var}, "Dihedral", "XSec_1", {d.get("dihedral_deg", 0.0):.9g} );
  SetParmVal( {var}, "X_Rel_Location", "XForm", {d["x_m"]:.9g} );
  SetParmVal( {var}, "Z_Rel_Location", "XForm", {d.get("z_m", 0.0):.9g} );
  {'SetParmVal( ' + var + ', "X_Rel_Rotation", "XForm", 90.0 );' if fin else ''}
  Update();
  {{
    string xsurf = GetXSecSurf( {var}, 0 );
    for ( int i = 0 ; i < 2 ; i++ )
    {{
      ChangeXSecShape( xsurf, i, XS_FOUR_SERIES );
      string xs = GetXSec( xsurf, i );
      SetParmVal( GetXSecParm( xs, "ThickChord" ), {d["thickness_ratio"]:.9g} );
      SetParmVal( GetXSecParm( xs, "Camber" ), {d.get("camber", 0.0):.9g} );
    }}
  }}
  Update();
"""


def _fuse_profile(length: float, dmax: float, d_exit: float, nose_frac: float, tail_frac: float):
    """(x fraction, diameter) stations: ogive nose, straight centre, smooth taper to the nozzle."""
    st = []
    # few, well spaced stations: OpenVSP skins between circles with tangent continuity, and
    # closely spaced ones (or equal diameters right next to each other) make it ripple
    for f in (0.0, nose_frac * 0.35, nose_frac, 1.0 - tail_frac, 1.0):
        if f <= nose_frac:
            u = 1.0 - f / nose_frac
            dia = dmax * math.sqrt(max(0.0, 1.0 - u ** 2.2))
        elif f <= 1.0 - tail_frac:
            dia = dmax
        else:
            u = (f - (1.0 - tail_frac)) / tail_frac
            dia = dmax - (dmax - d_exit) * (u * u * (3 - 2 * u))
        st.append((f, dia))
    clean = []
    for f, dia in st:                      # strictly increasing stations
        if not clean or f > clean[-1][0] + 1e-6:
            clean.append((f, dia))
    return clean


def _aircraft_script(inp: Dict[str, Any], work: Path) -> Tuple[str, float, float, float, Dict[str, Any]]:
    fu = dict(inp.get("fuselage") or {})
    L = _pos(fu, "length_m", 0.98)
    dmax = _pos(fu, "max_diameter_m", 0.11)
    d_exit = _pos(fu, "exit_diameter_m", 0.062)
    nose_frac = _f(fu.get("nose_fraction", 0.22))
    tail_frac = _f(fu.get("tail_fraction", 0.35))
    if not (0.05 <= nose_frac <= 0.5 and 0.05 <= tail_frac <= 0.6 and nose_frac + tail_frac < 0.95):
        raise ValueError("fuselage nose_fraction 0.05..0.5, tail_fraction 0.05..0.6, sum < 0.95")
    if d_exit >= dmax:
        raise ValueError("fuselage.exit_diameter_m must be below max_diameter_m")
    wing = _surface("wing", inp.get("wing"), {"taper": 0.46, "sweep_le_deg": 28.0, "thickness_ratio": 0.09,
                                              "dihedral_deg": 1.5, "x_m": 0.33, "z_m": -0.006})
    htail = _surface("htail", inp.get("htail"), {"taper": 0.54, "sweep_le_deg": 35.0, "thickness_ratio": 0.08,
                                                 "x_m": 0.805, "z_m": 0.0})
    vfin = _surface("vfin", inp.get("vfin"), {"taper": 0.44, "sweep_le_deg": 40.0, "thickness_ratio": 0.08,
                                              "x_m": 0.735, "z_m": 0.04})
    intake = inp.get("intake")
    prof = _fuse_profile(L, dmax, d_exit, nose_frac, tail_frac)
    fuse = [f"""
  string fid = AddGeom( "FUSELAGE" );
  SetGeomName( fid, "fuselage" );
  SetParmVal( fid, "Length", "Design", {L:.9g} );
  Update();
  string fsurf = GetXSecSurf( fid, 0 );
  while ( GetNumXSec( fsurf ) < {len(prof)} ) {{ InsertXSec( fid, 1, XS_CIRCLE ); Update(); }}
  while ( GetNumXSec( fsurf ) > {len(prof)} ) {{ CutXSec( fid, 1 ); Update(); }}
"""]
    # positions first from the back so every station stays ordered while it moves
    for i in range(len(prof) - 1, -1, -1):
        f, dia = prof[i]
        shape = "XS_POINT" if i == 0 else "XS_CIRCLE"
        fuse.append(f"""  ChangeXSecShape( fsurf, {i}, {shape} );
  {{ string xs = GetXSec( fsurf, {i} );
    SetParmVal( GetXSecParm( xs, "XLocPercent" ), {f:.9g} );
    {'' if i == 0 else f'SetParmVal( GetXSecParm( xs, "Circle_Diameter" ), {dia:.9g} );'} }}
  Update();
""")
    pod = ""
    if intake:
        il = _pos(intake, "length_m", 0.16)
        ifr = _pos(intake, "fineness", 2.6)
        ix = _f(intake.get("x_m", 0.47))
        iz = _f(intake.get("z_m", dmax / 2.0 + 0.012))
        pod = f"""
  string pid = AddGeom( "POD" );
  SetGeomName( pid, "intake_fairing" );
  SetParmVal( pid, "Length", "Design", {il:.9g} );
  SetParmVal( pid, "FineRatio", "Design", {ifr:.9g} );
  SetParmVal( pid, "X_Rel_Location", "XForm", {ix:.9g} );
  SetParmVal( pid, "Z_Rel_Location", "XForm", {iz:.9g} );
  Update();
"""
    span = wing["span_m"]
    mean = wing["root_chord_m"] * (1 + wing["taper"]) / 2.0
    sref = _f(inp.get("sref_m2", span * mean))
    bref = _f(inp.get("bref_m", span))
    cref = _f(inp.get("cref_m", mean))
    # STEP through the surface-intersection analysis: trimmed, intersected B-Rep (wing-body
    # junction cut), in metres -- a plain ExportFile STEP is a set of untrimmed surfaces in feet
    exports = ['  ExportFile( "aircraft.stl", SET_ALL, EXPORT_STL );',
               '  {',
               '    string si = "SurfaceIntersection";',
               '    SetAnalysisInputDefaults( si );',
               '    array<string> stp(1, "aircraft.stp"); SetStringAnalysisInput( si, "STEPFileName", stp );',
               '    array<int> one(1, 1); SetIntAnalysisInput( si, "STEPFileFlag", one );',
               '    array<int> zero(1, 0); SetIntAnalysisInput( si, "IGESFileFlag", zero );',
               '    array<int> rep(1, STEP_BREP); SetIntAnalysisInput( si, "STEPRepresentation", rep );',
               '    array<int> unit(1, LEN_M); SetIntAnalysisInput( si, "CADLenUnit", unit );',
               '    array<int> allset(1, SET_ALL); SetIntAnalysisInput( si, "SelectedSetIndex", allset );',
               '    ExecAnalysis( si );',
               '  }']
    script = ("// generated by the workshop: whole aircraft + VSPAERO\nvoid main()\n{\n"
              + "".join(fuse) + pod
              + _lift_surface_script("wid", "wing", wing)
              + _lift_surface_script("hid", "htail", htail)
              + _lift_surface_script("vid", "vfin", vfin, fin=True)
              + '  WriteVSPFile( "aircraft.vsp3", SET_ALL );\n  ClearVSPModel();\n  ReadVSPFile( "aircraft.vsp3" );\n'
              + "\n".join(exports) + "\n"
              + "  while ( GetNumTotalErrors() > 0 ) { ErrorObj err = PopLastError(); Print( \"VSPERROR \" + err.GetErrorString() ); }\n"
              + "}\n")
    geo = {"length_m": L, "span_m": span, "wing": wing, "htail": htail, "vfin": vfin, "mac_m": cref}
    return script, sref, bref, cref, geo


def _cg_sweep_script(model: str, inp: Dict[str, Any], sref: float, bref: float, cref: float,
                     xcg: float, tag: str) -> str:
    block = _sweep_block(inp, sref, bref, cref).replace(
        'string res = ExecAnalysis( sweep );',
        f'array<double> xcgv(1, {xcg:.9g}); SetDoubleAnalysisInput( sweep, "Xcg", xcgv );\n'
        '  string res = ExecAnalysis( sweep );').replace('"results.csv"', f'"results_{tag}.csv"')
    return f"""// VSPAERO on the aircraft, moments about x = {xcg:.6g} m
void main()
{{
  ReadVSPFile( "{model}" );
{block}
}}
"""


def _slope(xs, ys) -> float:
    import numpy as np
    a = np.radians(np.asarray(xs, float))
    b = np.asarray(ys, float)
    ok = np.isfinite(a) & np.isfinite(b)
    return float(np.polyfit(a[ok], b[ok], 1)[0])


def _aircraft(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    exe = Path(tool("openvsp"))
    script, sref, bref, cref, geo = _aircraft_script(inp, work)
    (work / "aircraft.vspscript").write_text(script, encoding="utf-8")
    proc = run_exe([str(exe), "-script", "aircraft.vspscript"], work, 600)
    out = (proc.stdout or "") + (proc.stderr or "")
    (work / "vsp_geometry.log").write_text(out, encoding="utf-8", errors="replace")
    errs = [ln for ln in out.splitlines() if ln.startswith("VSPERROR")]
    if not (work / "aircraft.vsp3").is_file():
        raise ValueError(f"OpenVSP built no aircraft: {out.strip()[-500:]}")
    values: Dict[str, Any] = {"sref_m2": round(sref, 6), "bref_m": bref, "cref_m": round(cref, 6),
                              "aspect_ratio": round(bref * bref / sref, 4)}
    bb = _stl_bbox(work / "aircraft.stl") if (work / "aircraft.stl").is_file() else None
    if bb:
        values.update(stl_length_m=round(bb[0], 5), stl_span_m=round(bb[1], 5), stl_height_m=round(bb[2], 5))
    files = [str(work / n) for n in ("aircraft.vsp3", "aircraft.stp", "aircraft.stl", "vsp_geometry.log")
             if (work / n).is_file()]
    notes = ["a loft for shape and aerodynamics: walls, bulkheads and print sections are made in FreeCAD"]
    if errs:
        notes.append(f"OpenVSP reported {len(errs)} API error(s), see vsp_geometry.log")
    if _wants_aero(inp):
        # leading edge of the mean aerodynamic chord of a straight-tapered wing
        w = geo["wing"]
        lam = w["taper"]
        ybar = (w["span_m"] / 2) / 3 * (1 + 2 * lam) / (1 + lam)
        x_mac_le = w["x_m"] + ybar * math.tan(math.radians(w["sweep_le_deg"]))
        if inp.get("cg_x_m") is not None:
            xcg = _f(inp["cg_x_m"])
        else:
            xcg = x_mac_le + _f(inp.get("cg_mac_fraction", 0.25)) * cref
        sp = work / "sweep_cg.vspscript"
        sp.write_text(_cg_sweep_script("aircraft.vsp3", inp, sref, bref, cref, xcg, "cg"), encoding="utf-8")
        proc = run_exe([str(exe), "-script", sp.name], work, 900)
        (work / "vsp_aero.log").write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8", errors="replace")
        (work / "results_cg.csv").replace(work / "results.csv")
        rows = _read_results(work)
        al = [r.get("Alpha", float("nan")) for r in rows]
        cl = [r.get("CLtot", float("nan")) for r in rows]
        cm = [r.get("CMytot", float("nan")) for r in rows]
        cla, cma = _slope(al, cl), _slope(al, cm)
        x_np = xcg - cma / cla * cref
        margin = (x_np - xcg) / cref
        values.update({"alpha_deg": [round(a, 4) for a in al], "CL": [round(c, 6) for c in cl],
                       "CMy": [round(c, 6) for c in cm],
                       "CDi": [round(r.get("CDi", float("nan")), 7) for r in rows],
                       "CL_alpha_per_rad": round(cla, 5), "Cm_alpha_per_rad": round(cma, 5),
                       "x_cg_m": round(xcg, 5), "x_mac_le_m": round(x_mac_le, 5),
                       "x_neutral_point_m": round(x_np, 5), "static_margin_mac": round(margin, 5),
                       "CL_at_0": round(cl[0], 6) if al and abs(al[0]) < 1e-9 else None})
        if rows and "L_D" in rows[-1]:
            values["lift_to_drag_last"] = round(rows[-1]["L_D"], 4)
        if inp.get("check_neutral_point", False):
            # the definition, checked: moments about the computed neutral point do not change with alpha
            sp2 = work / "sweep_np.vspscript"
            sp2.write_text(_cg_sweep_script("aircraft.vsp3", inp, sref, bref, cref, x_np, "np"), encoding="utf-8")
            run_exe([str(exe), "-script", sp2.name], work, 900)
            (work / "results_np.csv").replace(work / "results.csv")
            rows2 = _read_results(work)
            cma_np = _slope([r.get("Alpha") for r in rows2], [r.get("CMytot") for r in rows2])
            values["Cm_alpha_at_np_rel"] = round(abs(cma_np / cla), 6)
        files.append(str(work / "results.csv"))
        notes += ["VSPAERO vortex lattice: inviscid and linear, no stall; the fuselage carries no lift "
                  "here, so the neutral point is the lifting surfaces' (slightly aft of the real one: "
                  "a fuselage destabilises)",
                  "static margin = (x_np - x_cg) / MAC: 5 to 15 % is the usual target for a jet model"]
        head = (f"aircraft {geo['length_m']:.3f} m x {bref:.3f} m: CL_alpha {cla:.3f}/rad, neutral point "
                f"{x_np:.4f} m, CG {xcg:.4f} m, static margin {margin * 100:.1f} % MAC")
    else:
        head = "aircraft geometry only: " + ", ".join(Path(f).name for f in files)
    return result(values, inp.get("limits"), head, files=files, notes=notes)


def _model_script(inp: Dict[str, Any], work: Path) -> Tuple[str, float, float, float]:
    name = None
    for f in sorted(work.iterdir()):
        if f.suffix.lower() == ".vsp3":
            name = f.name
            break
    if name is None:
        raise ValueError("no .vsp3 file in the job folder: pass it with "
                         "`params.files = [\"path/to/model.vsp3\"]`")
    # the name is interpolated into the generated AngelScript, so it is checked rather than
    # trusted: no quote can close the string literal, no backslash can escape it. Windows
    # already forbids `"` in a file name; this is the second lock on the same door.
    if not re.fullmatch(r"[A-Za-z0-9_.\- ]{1,120}\.vsp3", name):
        raise ValueError(f"unusable model name {name!r}: letters, digits, space, dot, dash "
                         f"and underscore only")
    sref = _pos(inp, "sref_m2")
    bref = _pos(inp, "bref_m")
    cref = _pos(inp, "cref_m")
    script = f"""// generated by the workshop: VSPAERO on a supplied model
void main()
{{
  ReadVSPFile( "{name}" );
{_sweep_block(inp, sref, bref, cref)}
}}
"""
    return script, sref, bref, cref


# --------------------------------------------------------------------------- results
def _read_results(work: Path) -> List[Dict[str, float]]:
    """VSPAERO's results CSV is a stack of blocks: `Results_Name,<name>` then one line per field.
    The alpha sweep's forces live in the `VSPAERO_Polar` block as parallel columns."""
    f = work / "results.csv"
    if not f.is_file():
        raise ValueError("VSPAERO produced no results.csv (see vsp.log in the job folder)")
    blocks: Dict[str, Dict[str, List[str]]] = {}
    current = None
    with f.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh):
            if not row or not row[0].strip():
                continue
            key = row[0].strip()
            if key == "Results_Name":
                current = row[1].strip() if len(row) > 1 else ""
                blocks.setdefault(current, {})
            elif current is not None:
                blocks[current][key] = [c.strip() for c in row[1:] if c.strip() != ""]
    polar = blocks.get("VSPAERO_Polar") or {}
    if not polar:
        raise ValueError(f"no VSPAERO_Polar block in results.csv (blocks: {', '.join(blocks)})")
    # the names VSPAERO 3.56 writes, which are not the ones its own example scripts use:
    # CLtot / CMytot / L_D, not CL / CMy / L/D
    want = ("Alpha", "CLtot", "CDtot", "CDi", "CDo", "CMytot", "L_D", "E", "Mach", "Re_1e6")
    cols = {k: polar[k] for k in want if k in polar}
    n = min((len(v) for v in cols.values()), default=0)
    if n == 0:
        raise ValueError("the polar block is empty")
    rows = []
    for i in range(n):
        row = {}
        for k, v in cols.items():
            try:
                row[k] = float(v[i])
            except ValueError:
                pass
        rows.append(row)
    return rows


def _stl_bbox(path: Path) -> Optional[Tuple[float, float, float]]:
    """Bounding box of a binary or ASCII STL, without a mesh library."""
    import numpy as np
    b = path.read_bytes()
    pts: List[Tuple[float, float, float]] = []
    if len(b) > 84 and not b[:80].lstrip().lower().startswith(b"solid"):
        n = struct.unpack("<I", b[80:84])[0]
        if 84 + 50 * n <= len(b):
            for i in range(n):
                o = 84 + 50 * i + 12
                pts.extend(struct.unpack("<9f", b[o:o + 36])[j:j + 3] for j in (0, 3, 6))
    if not pts:
        txt = b.decode("utf-8", errors="replace")
        pts = [tuple(float(x) for x in m.groups())           # type: ignore[misc]
               for m in re.finditer(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", txt)]
    if not pts:
        return None
    a = np.asarray(pts, dtype=float)
    return tuple(float(x) for x in (a.max(axis=0) - a.min(axis=0)))   # type: ignore[return-value]


def _collect_exports(work: Path, files: List[str], values: Dict[str, Any]):
    """Add the exported geometry to the answer, and measure the STL so that the export is checked
    rather than assumed."""
    for extra in ("wing.stp", "wing.stl", "wing.vsp3"):
        p = work / extra
        if not p.is_file():
            continue
        files.append(str(p))
        if extra == "wing.stl":
            bb = _stl_bbox(p)
            if bb:
                values["stl_bbox_m"] = [round(x, 6) for x in bb]
                values["stl_span_m"] = round(max(bb), 6)
    return files, values


def _run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    exe = Path(tool("openvsp"))
    if kind == "vsp_wing":
        script, sref, bref, cref = _wing_script(inp, work)
    else:
        script, sref, bref, cref = _model_script(inp, work)
    sp = work / "case.vspscript"
    sp.write_text(script, encoding="utf-8")
    timeout = float(KINDS[kind]["timeout"])
    proc = run_exe([str(exe), "-script", sp.name], work, timeout)
    code = proc.returncode
    out = (proc.stdout or "") + (proc.stderr or "")
    (work / "vsp.log").write_text(out, encoding="utf-8", errors="replace")
    errs = [ln for ln in out.splitlines() if ln.startswith("VSPERROR")]
    if code != 0 and not (work / "results.csv").is_file():
        raise ValueError(f"vspscript failed (exit {code}): {out.strip()[-400:]}")

    if not _wants_aero(inp):
        values: Dict[str, Any] = {"sref_m2": sref, "bref_m": bref, "cref_m": cref,
                                  "aspect_ratio": round(bref * bref / sref, 6), "points": 0}
        files = [str(work / "vsp.log")]
        files, values = _collect_exports(work, files, values)
        return result(values, inp.get("limits"),
                      "geometry only (no VSPAERO run): " + ", ".join(
                          Path(f).name for f in files if not f.endswith("vsp.log")),
                      files=files,
                      notes=["`run_aero: false`: a loft and its export, nothing was computed",
                             "import the STEP into FreeCAD, then build your own sketch and "
                             "structure on it"])

    rows = _read_results(work)
    alphas = [r.get("Alpha", float("nan")) for r in rows]
    cls = [r.get("CLtot", float("nan")) for r in rows]
    values: Dict[str, Any] = {
        "points": len(rows),
        "alpha_deg": [round(a, 6) for a in alphas],
        "CL": [round(c, 6) for c in cls],
        "CDi": [round(r.get("CDi", float("nan")), 8) for r in rows],
        "CDtot": [round(r.get("CDtot", float("nan")), 8) for r in rows],
        "CMy": [round(r.get("CMytot", float("nan")), 6) for r in rows],
        "sref_m2": sref, "bref_m": bref, "cref_m": cref,
        "aspect_ratio": round(bref * bref / sref, 6),
    }
    if len(rows) >= 2:
        # least squares slope over the sweep, per radian
        import numpy as np
        a = np.asarray(alphas, dtype=float)
        c = np.asarray(cls, dtype=float)
        good = np.isfinite(a) & np.isfinite(c)
        if good.sum() >= 2:
            slope = float(np.polyfit(np.radians(a[good]), c[good], 1)[0])
            values["CL_alpha_per_rad"] = round(slope, 6)
            values["CL_alpha_per_deg"] = round(slope * math.pi / 180.0, 8)
    last = rows[-1]
    for k, name in (("E", "span_efficiency"), ("L_D", "lift_to_drag"), ("CDo", "CDo"),
                    ("Re_1e6", "Re_millions")):
        if k in last:
            values[name] = round(last[k], 6)
    if "E" in last and last.get("CDi", 0) > 0 and abs(last.get("CLtot", 0)) > 1e-6:
        ar = bref * bref / sref
        pred = last["CLtot"] ** 2 / (math.pi * ar * last["E"])
        values["cdi_vs_cl2_identity_rel"] = round(abs(pred - last["CDi"]) / last["CDi"], 6)
    files = [str(work / "results.csv"), str(work / "vsp.log")]
    files, values = _collect_exports(work, files, values)
    notes = ["VSPAERO vortex lattice: inviscid and linear, no stall and no profile drag",
             "coefficients are referred to the Sref / bref / cref printed above",
             "a loft, not a structure: thickness and spars belong in FreeCAD"]
    if errs:
        notes.append(f"OpenVSP reported {len(errs)} API error(s), see vsp.log")
    head = (f"{len(rows)} point(s), AR {values['aspect_ratio']:.2f}"
            + (f", CL_alpha {values['CL_alpha_per_rad']:.3f} /rad"
               if "CL_alpha_per_rad" in values else ""))
    return result(values, inp.get("limits"), head, files=files, notes=notes)


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; known: {', '.join(sorted(KINDS))}")
    if kind == "vsp_aircraft":
        return _aircraft(inp, Path(work))
    return _run(kind, inp, Path(work))


# --------------------------------------------------------------------------- references
# Helmbold's lifting-surface formula for a straight wing (Anderson, Fundamentals of
# Aerodynamics, eq. 5.81; an approximation good to a few per cent at AR 8), the same reference
# the AeroSandbox and PanelAero paths are held to:
_AR = 8.0
_HELMBOLD = 2.0 * math.pi * _AR / (2.0 + math.sqrt(_AR ** 2 + 4.0))
_SPAN, _CHORD = 8.0, 1.0            # AR = 8 rectangular wing, 1 m chord

REFERENCES = [
    {   # Lift slope against Helmbold, and the induced-drag identity against VSPAERO's own span
        # efficiency. Measured 2026-11-20: 4.61 /rad against Helmbold's 4.91, i.e. 6 % below --
        # Helmbold is itself an approximation good to a few per cent and the vortex lattice adds
        # its discretisation, so the tolerance is 8 %. The identity, in contrast, is arithmetic
        # and matches to better than 0.1 %.
        "name": "rectangular_wing_lift_slope", "kind": "vsp_wing",
        "inp": {"span_m": _SPAN, "root_chord_m": _CHORD, "taper": 1.0,
                "thickness_ratio": 0.12, "camber": 0.0, "span_panels": 24,
                "alpha_start_deg": 0.0, "alpha_end_deg": 6.0, "alpha_points": 4,
                "mach": 0.0},
        "expect": {"CL_alpha_per_rad": [_HELMBOLD, 0.08],
                   "aspect_ratio": [_AR, 1e-9],
                   "cdi_vs_cl2_identity_rel": [0.0, 1e-3]},
    },
    {   # a whole aircraft: the exported geometry is the one asked for (length, span), a
        # symmetric aircraft at zero incidence carries no lift, and the neutral point satisfies
        # its definition (moments about it do not change with alpha)
        "name": "aircraft_geometry_and_neutral_point", "kind": "vsp_aircraft",
        "inp": {"fuselage": {"length_m": 0.98, "max_diameter_m": 0.11, "exit_diameter_m": 0.062},
                "wing": {"span_m": 1.1, "root_chord_m": 0.26, "taper": 0.46, "sweep_le_deg": 28,
                         "dihedral_deg": 0.0, "x_m": 0.33, "z_m": 0.0},
                "htail": {"span_m": 0.38, "root_chord_m": 0.13, "taper": 0.54, "sweep_le_deg": 35, "x_m": 0.805},
                "vfin": {"span_m": 0.15, "root_chord_m": 0.16, "taper": 0.44, "sweep_le_deg": 40, "x_m": 0.735, "z_m": 0.04},
                "alpha_start_deg": 0.0, "alpha_end_deg": 6.0, "alpha_points": 4,
                "check_neutral_point": True},
        # overall length: the swept tailplane tip runs past the nozzle (0.805 + 0.19 tan 35 + 0.0702 m)
        "expect": {"stl_length_m": [0.805 + 0.19 * math.tan(math.radians(35.0)) + 0.13 * 0.54, 0.01],
                   "stl_span_m": [1.1, 0.01], "CL_at_0": [0.0, 2e-3],
                   "Cm_alpha_at_np_rel": [0.0, 0.02]},
    },
    {   # the geometry path to FreeCAD: the exported STL must be the wing that was asked for
        "name": "wing_stl_export_size", "kind": "vsp_wing",
        "inp": {"span_m": _SPAN, "root_chord_m": _CHORD, "taper": 1.0, "span_panels": 12,
                "run_aero": False, "export_stl": True, "export_step": True},
        "expect": {"stl_span_m": [_SPAN, 0.01], "aspect_ratio": [_AR, 1e-9]},
    },
]
