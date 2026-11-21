"""OpenFOAM 12 (blueCFD-Core, native Windows): incompressible flow, and any case you supply (env `sim`).

The workshop's SU2 and Elmer cover external aerodynamics, compressible flow and conjugate heat.
OpenFOAM brings the rest of what Fluent is bought for: free surface (VOF), cavitation, rotating
zones (MRF), six-degree-of-freedom bodies, turbulence models by the dozen. It runs here as the
native Windows build of blueCFD-Core, serial (Pstream dummy library, no MPI).

Kinds:
- foam_couette   plane Couette flow between a fixed and a sliding wall (cyclic in x): the exact
                 linear profile, the solver's own validation
- foam_channel   laminar plane channel (inlet, outlet, two walls): developed profile, maximum
                 velocity and pressure gradient against the exact Poiseuille solution
- foam_case      run YOUR case folder (params.files) with a solver module (`incompressibleFluid`,
                 `incompressibleVoF`, `multiphaseEuler`...): blockMesh if there is a blockMeshDict,
                 then foamRun; residual history and the last time step's fields come back

Security -- read this before `foam_case`. An OpenFOAM dictionary can COMPILE AND RUN C++
(`#codeStream`, `#calc`, `coded*` boundary conditions and function objects), load any DLL
(`libs (...)`), or call the shell (`systemCall`). blueCFD ships the compiler, so on this machine
those are real code execution from a text file. Every dictionary of a supplied case is scanned and
the case is REFUSED if it uses any of them, or includes a file from outside the case. That covers
almost every case people actually run; the ones it refuses need a human to read them.

Units: SI. Kinematic viscosity in m^2/s, kinematic pressure p/rho in m^2/s^2 (incompressible
OpenFOAM convention), velocities m/s.

Limits, restated in every result: serial only; the generated cases are laminar and 2-D (one cell
deep, `empty` front and back); a supplied case is run as given, so its physics, mesh quality and
convergence are its own -- the answer reports the residuals so you can judge, and says UNKNOWN
rather than OK when nothing was asked of it.

Exactness: REFERENCES below. Couette's linear profile and Poiseuille's parabola and pressure
gradient are exact solutions of the Navier-Stokes equations; and a case carrying `#codeStream`
must be refused before anything runs.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

import math  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

from _common import result, run_exe, tool  # noqa: E402

ENV = "sim"
KINDS = {
    "foam_couette": {"timeout": 600,
                     "doc": "OpenFOAM plane Couette flow: exact linear profile (solver check)"},
    "foam_channel": {"timeout": 900,
                     "doc": "OpenFOAM laminar plane channel: developed profile, u_max and dp/dx "
                            "against the exact Poiseuille solution"},
    "foam_case": {"timeout": 3600,
                  "doc": "run your own OpenFOAM 12 case (params.files) with a solver module; "
                         "dictionaries that compile code, load libraries or call the shell are "
                         "refused"},
}
PLATFORM = "mingw_w64Gcc122DPInt32Opt"
_SOLVERS = {"incompressibleFluid", "incompressibleVoF", "compressibleVoF", "fluid",
            "multiphaseEuler", "incompressibleDenseParticleFluid", "isothermalFluid",
            "shockFluid", "solid", "film", "XiFluid", "multicomponentFluid"}
# directives that turn a dictionary into code, a library load or a shell call
_FORBIDDEN = re.compile(
    r"#\s*codeStream|#\s*calc\b|\bcoded\w*|\bcodeInclude\b|\bcodeOptions\b|\bcodeLibs\b|"
    r"\bsystemCall\b|\blibs\s*\(|\bexecuteControl\b|#\s*includeFunc\b.*\bsystemCall")
_INCLUDE = re.compile(r'#\s*include(?:Etc|IfPresent)?\s+"([^"]+)"')


def versions() -> Dict[str, str]:
    out = {}
    try:
        exe = Path(tool("bluecfd"))
        out["openfoam"] = exe.parents[3].name if len(exe.parents) > 3 else "?"
    except Exception as e:  # noqa: BLE001
        out["openfoam"] = f"missing ({e})"
    return out


def _f(x) -> float:
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f"not a finite number: {x!r}")
    return v


def _pos(d: Dict[str, Any], key: str, default=None) -> float:
    v = _f(d[key]) if key in d else (_f(default) if default is not None else None)
    if v is None:
        raise ValueError(f"missing input `{key}`")
    if v <= 0:
        raise ValueError(f"`{key}` must be > 0, got {v}")
    return v


# --------------------------------------------------------------------------- the environment
def _foam_env() -> Tuple[Path, Dict[str, str]]:
    """Paths and variables that blueCFD's own setvars_OF12.bat would set, for a serial run."""
    exe = Path(tool("bluecfd"))
    proj = exe.parents[3]                     # .../OpenFOAM-12
    root = proj.parent                        # blueCFD home
    third = root / "ThirdParty-12" / "platforms"
    appbin = proj / "platforms" / PLATFORM / "bin"
    libbin = proj / "platforms" / PLATFORM / "lib"
    extlib = third / "mingw_w64Gcc122DPInt32" / "lib"
    gccbin = third / "mingw_w64x86_64-w64-mingw32" / "gcc-12.2.0" / "bin"
    win = os.environ.get("SYSTEMROOT", r"C:\Windows")
    path = os.pathsep.join(str(p) for p in (
        appbin, proj / "bin", gccbin, root / "msys64" / "mingw64" / "bin",
        libbin / "dummy", libbin, extlib, win, Path(win) / "System32"))
    env = {
        "PATH": path, "WM_PROJECT": "OpenFOAM", "WM_PROJECT_VERSION": "12",
        "WM_PROJECT_DIR": str(proj), "WM_PROJECT_INST_DIR": str(root),
        "FOAM_INST_DIR": str(root), "WM_THIRD_PARTY_DIR": str(root / "ThirdParty-12"),
        "WM_OPTIONS": PLATFORM, "WM_ARCH": "mingw_w64", "WM_COMPILER": "Gcc122",
        "WM_PRECISION_OPTION": "DP", "WM_LABEL_SIZE": "32", "WM_LABEL_OPTION": "Int32",
        "WM_COMPILE_OPTION": "Opt", "WM_OSTYPE": "MSwindows", "WM_MPLIB": "dummy",
        "FOAM_ETC": str(proj / "etc"), "FOAM_APPBIN": str(appbin), "FOAM_LIBBIN": str(libbin),
        "FOAM_EXT_LIBBIN": str(extlib), "FOAM_SIGFPE": "1", "FOAM_MPI": "dummy",
        "USER": "ofuser", "USERNAME": "ofuser",
    }
    return appbin, env


def _run_case(case: Path, solver: str, timeout: float, mesh: bool = True) -> Dict[str, Any]:
    appbin, env = _foam_env()
    env.update(HOME=str(case), WM_PROJECT_USER_DIR=str(case))
    logs = []
    if mesh and (case / "system" / "blockMeshDict").is_file():
        p = run_exe([str(appbin / "blockMesh.exe")], case, timeout, env_extra=env)
        (case / "log.blockMesh").write_text((p.stdout or "") + (p.stderr or ""), encoding="utf-8")
        logs.append("log.blockMesh")
        if p.returncode != 0:
            raise ValueError(f"blockMesh failed: {((p.stdout or '') + (p.stderr or ''))[-500:]}")
    p = run_exe([str(appbin / "foamRun.exe"), "-solver", solver], case, timeout, env_extra=env)
    out = (p.stdout or "") + (p.stderr or "")
    (case / "log.foamRun").write_text(out, encoding="utf-8")
    logs.append("log.foamRun")
    if p.returncode != 0 or "FOAM FATAL" in out:
        raise ValueError(f"foamRun failed: {out[-700:]}")
    return {"log": out, "logs": logs}


def _residuals(log: str) -> Dict[str, float]:
    """Last initial residual of each solved field, from the solver log."""
    res: Dict[str, float] = {}
    for m in re.finditer(r"Solving for (\w+), Initial residual = ([0-9.eE+-]+)", log):
        res[m.group(1)] = float(m.group(2))
    return res


def _latest_time(case: Path) -> Path:
    times = []
    for d in case.iterdir():
        if d.is_dir():
            try:
                times.append((float(d.name), d))
            except ValueError:
                pass
    if not times:
        raise ValueError("the run wrote no time directory")
    return max(times)[1]


def _read_internal(path: Path) -> List:
    """internalField of an ASCII field file: a list of floats or of (x y z) tuples."""
    t = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"internalField\s+nonuniform\s+List<(scalar|vector)>\s*(\d+)\s*\((.*?)\)\s*;",
                  t, re.S)
    if not m:
        u = re.search(r"internalField\s+uniform\s+(\([^)]*\)|[^;]+);", t)
        if u:
            raise ValueError(f"{path.name} is uniform ({u.group(1).strip()}): the flow did not "
                             f"develop")
        raise ValueError(f"cannot read the internalField of {path.name}")
    kind, n, body = m.group(1), int(m.group(2)), m.group(3)
    if kind == "scalar":
        vals = [float(x) for x in body.split()]
    else:
        vals = [tuple(float(v) for v in g.split()) for g in re.findall(r"\(([^()]*)\)", body)]
    if len(vals) != n:
        raise ValueError(f"{path.name}: read {len(vals)} values, the header says {n}")
    return vals


# --------------------------------------------------------------------------- case generation
_HEADER = """FoamFile
{{
    format      ascii;
    class       {cls};
    object      {obj};
}}
"""


def _w(case: Path, rel: str, cls: str, body: str) -> None:
    p = case / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_HEADER.format(cls=cls, obj=p.name) + body, encoding="utf-8")


def _common_dicts(case: Path, nu: float, end: float, dt: float) -> None:
    _w(case, "system/controlDict", "dictionary", f"""
application     foamRun;
solver          incompressibleFluid;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {end:g};
deltaT          {dt:g};
writeControl    runTime;
writeInterval   {end:g};
purgeWrite      0;
writeFormat     ascii;
writePrecision  10;
writeCompression off;
timeFormat      general;
timePrecision   8;
runTimeModifiable false;
""")
    _w(case, "system/fvSchemes", "dictionary", """
ddtSchemes      { default backward; }
gradSchemes     { default Gauss linear; }
divSchemes      { default none; div(phi,U) Gauss linear; div((nuEff*dev2(T(grad(U))))) Gauss linear; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes   { default corrected; }
""")
    _w(case, "system/fvSolution", "dictionary", """
solvers
{
    p      { solver PCG; preconditioner DIC; tolerance 1e-10; relTol 0.01; }
    pFinal { $p; relTol 0; }
    U      { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0.1; }
    UFinal { $U; relTol 0; }
}
PIMPLE
{
    momentumPredictor   yes;
    nOuterCorrectors    1;
    nCorrectors         3;
    nNonOrthogonalCorrectors 0;
    pRefCell            0;
    pRefValue           0;
}
""")
    _w(case, "constant/physicalProperties", "dictionary",
       f"\nviscosityModel  constant;\nnu              {nu:.10g};\n")
    _w(case, "constant/momentumTransport", "dictionary", "\nsimulationType laminar;\n")


def _block(case: Path, lx: float, ly: float, nx: int, ny: int, patches: str) -> None:
    lz = ly / ny
    _w(case, "system/blockMeshDict", "dictionary", f"""
convertToMeters 1;
vertices
(
    (0 0 0) ({lx:.10g} 0 0) ({lx:.10g} {ly:.10g} 0) (0 {ly:.10g} 0)
    (0 0 {lz:.10g}) ({lx:.10g} 0 {lz:.10g}) ({lx:.10g} {ly:.10g} {lz:.10g}) (0 {ly:.10g} {lz:.10g})
);
blocks ( hex (0 1 2 3 4 5 6 7) ({nx} {ny} 1) simpleGrading (1 1 1) );
boundary
(
{patches}
    frontAndBack {{ type empty; faces ((0 3 2 1) (4 5 6 7)); }}
);
""")


def _centres(lx: float, ly: float, nx: int, ny: int) -> List[Tuple[float, float]]:
    """blockMesh numbers the cells of one block x fastest, then y."""
    return [((i + 0.5) * lx / nx, (j + 0.5) * ly / ny) for j in range(ny) for i in range(nx)]


def _run_couette(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    h = _pos(inp, "height_m", 0.1)
    uw = _pos(inp, "wall_velocity_m_s", 0.1)
    nu = _pos(inp, "nu_m2_s", 1e-3)
    ny = int(inp.get("cells_across", 20))
    if not 4 <= ny <= 400:
        raise ValueError("`cells_across` must be between 4 and 400")
    nx, lx = 4, h
    tau = h * h / nu
    end, dt = 6.0 * tau, tau / 40.0
    case = work / "case"
    if case.exists():
        shutil.rmtree(case)
    _common_dicts(case, nu, end, dt)
    _block(case, lx, h, nx, ny, """
    movingWall  { type wall; faces ((3 7 6 2)); }
    fixedWall   { type wall; faces ((1 5 4 0)); }
    left        { type cyclic; neighbourPatch right; faces ((0 4 7 3)); }
    right       { type cyclic; neighbourPatch left;  faces ((2 6 5 1)); }""")
    _w(case, "0/U", "volVectorField", f"""
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (0 0 0);
boundaryField
{{
    movingWall {{ type fixedValue; value uniform ({uw:.10g} 0 0); }}
    fixedWall  {{ type noSlip; }}
    #includeEtc "caseDicts/setConstraintTypes"
}}
""")
    _w(case, "0/p", "volScalarField", """
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    movingWall { type zeroGradient; }
    fixedWall  { type zeroGradient; }
    #includeEtc "caseDicts/setConstraintTypes"
}
""")
    run = _run_case(case, "incompressibleFluid", float(KINDS["foam_couette"]["timeout"]))
    u = _read_internal(_latest_time(case) / "U")
    cs = _centres(lx, h, nx, ny)
    err = max(abs(ux[0] - uw * y / h) for ux, (_x, y) in zip(u, cs)) / uw
    mid = [ux[0] for ux, (_x, y) in zip(u, cs) if abs(y - h / 2) <= h / ny]
    values = {
        "cells": len(u), "profile_max_error_rel": round(err, 9),
        "u_mid_m_s": round(sum(mid) / len(mid), 9) if mid else None,
        "u_mid_exact_m_s": uw / 2.0,
        "wall_shear_kinematic_m2_s2": nu * uw / h,
        "reynolds": uw * h / nu, "residuals": _residuals(run["log"]),
    }
    return result(values, inp.get("limits"),
                  f"Couette, {ny} cells across: profile within {err * 100:.4f} % of the exact "
                  f"linear one", files=[str(case / x) for x in run["logs"]],
                  notes=["laminar 2-D, serial; the exact solution is u = U y / h"])


def _run_channel(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    h = _pos(inp, "height_m", 0.1)
    ub = _pos(inp, "mean_velocity_m_s", 0.01)
    nu = _pos(inp, "nu_m2_s", 1e-3)
    ratio = _pos(inp, "length_to_height", 20.0)
    ny = int(inp.get("cells_across", 20))
    nx = int(inp.get("cells_along", 100))
    if not (8 <= ny <= 200 and 20 <= nx <= 2000):
        raise ValueError("`cells_across` 8..200 and `cells_along` 20..2000")
    re_ = ub * h / nu
    if re_ > 1500:
        raise ValueError(f"Re = {re_:.0f}: above about 2000 a channel is no longer laminar; this "
                         f"kind is the laminar check, use `foam_case` with a turbulence model")
    lx = ratio * h
    tau = max(h * h / nu, lx / ub)
    end = 3.0 * tau
    dt = min(0.5 * (lx / nx) / ub, h * h / nu / 20.0)
    steps = end / dt
    if steps > 20000:
        raise ValueError(f"{steps:.0f} time steps needed: lower length_to_height or raise the "
                         f"velocity")
    case = work / "case"
    if case.exists():
        shutil.rmtree(case)
    _common_dicts(case, nu, end, dt)
    _block(case, lx, h, nx, ny, """
    inlet  { type patch; faces ((0 4 7 3)); }
    outlet { type patch; faces ((2 6 5 1)); }
    walls  { type wall;  faces ((3 7 6 2) (1 5 4 0)); }""")
    _w(case, "0/U", "volVectorField", f"""
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (0 0 0);
boundaryField
{{
    inlet  {{ type fixedValue; value uniform ({ub:.10g} 0 0); }}
    outlet {{ type zeroGradient; }}
    walls  {{ type noSlip; }}
    #includeEtc "caseDicts/setConstraintTypes"
}}
""")
    _w(case, "0/p", "volScalarField", """
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    inlet  { type zeroGradient; }
    outlet { type fixedValue; value uniform 0; }
    walls  { type zeroGradient; }
    #includeEtc "caseDicts/setConstraintTypes"
}
""")
    run = _run_case(case, "incompressibleFluid", float(KINDS["foam_channel"]["timeout"]))
    last = _latest_time(case)
    u = _read_internal(last / "U")
    p = _read_internal(last / "p")
    cs = _centres(lx, h, nx, ny)
    # developed region: the columns between 50 % and 75 % of the length
    i1, i2 = int(0.50 * nx), int(0.75 * nx)
    col = [u[i2 + nx * j][0] for j in range(ny)]
    ys = [cs[i2 + nx * j][1] for j in range(ny)]
    exact = [1.5 * ub * (1.0 - ((y - h / 2) / (h / 2)) ** 2) for y in ys]
    prof_err = max(abs(a - b) for a, b in zip(col, exact)) / (1.5 * ub)
    pbar = [sum(p[i + nx * j] for j in range(ny)) / ny for i in (i1, i2)]
    dpdx = (pbar[1] - pbar[0]) / (cs[i2][0] - cs[i1][0])
    dpdx_exact = -12.0 * nu * ub / (h * h)
    values = {
        "cells": len(u), "reynolds": re_,
        "u_max_m_s": round(max(col), 9), "u_max_exact_m_s": 1.5 * ub,
        "u_max_over_mean": round(max(col) / ub, 9),
        "profile_max_error_rel": round(prof_err, 9),
        "dpdx_kinematic": round(dpdx, 12), "dpdx_exact_kinematic": dpdx_exact,
        "dpdx_error_rel": round(abs(dpdx - dpdx_exact) / abs(dpdx_exact), 9),
        "residuals": _residuals(run["log"]),
    }
    return result(values, inp.get("limits"),
                  f"laminar channel Re {re_:.1f}: u_max/U {max(col) / ub:.4f} (exact 1.5), dp/dx "
                  f"within {values['dpdx_error_rel'] * 100:.2f} % of 12 nu U / h^2",
                  files=[str(case / x) for x in run["logs"]],
                  notes=["laminar 2-D, serial; the cell-centre maximum sits half a cell from the "
                         "axis, so u_max reads a touch below 1.5 U on a coarse mesh",
                         "kinematic pressure (p / rho), the incompressible OpenFOAM convention"])


# --------------------------------------------------------------------------- user cases
def scan_case(case: Path) -> List[str]:
    """Every reason to refuse a supplied case: code, libraries, shell, or files from outside."""
    bad = []
    for f in sorted(case.rglob("*")):
        if not f.is_file() or f.stat().st_size > 50_000_000:
            continue
        rel = f.relative_to(case).as_posix()
        if rel.split("/")[0] not in ("system", "constant", "0", "0.orig") or rel.startswith("constant/polyMesh/"):
            continue
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _FORBIDDEN.finditer(t):
            bad.append(f"{rel}: `{m.group(0).strip()}` compiles code, loads a library or runs a "
                       f"command")
        for m in _INCLUDE.finditer(t):
            target = m.group(1)
            if "#includeEtc" in m.group(0).replace(" ", ""):
                continue                                   # OpenFOAM's own etc files
            if (Path(target).is_absolute() or target.startswith(("~", "$")) or ".." in target
                    or ":" in target):
                bad.append(f"{rel}: includes {target!r}, outside the case")
    return bad


def _run_user_case(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    solver = str(inp.get("solver", "incompressibleFluid"))
    if solver not in _SOLVERS:
        raise ValueError(f"unknown solver module {solver!r}; known: {', '.join(sorted(_SOLVERS))}")
    # the runner copies params.files into the job folder; the case is the folder holding system/
    roots = [d for d in [work] + [p for p in work.iterdir() if p.is_dir()]
             if (d / "system" / "controlDict").is_file()]
    if not roots:
        raise ValueError("no case found: pass its files with params.files, keeping the "
                         "system/, constant/ and 0/ folders")
    case = roots[0]
    bad = scan_case(case)
    if bad:
        values = {"refused": 1.0, "reasons": bad[:20]}
        return result(values, inp.get("limits"),
                      f"case REFUSED before anything ran: {len(bad)} directive(s) that would "
                      f"compile code, load a library, run a command or read outside the case",
                      notes=bad[:5] + ["a human must read such a case; the workshop will not "
                                       "run it"])
    run = _run_case(case, solver, float(inp.get("timeout_s", KINDS["foam_case"]["timeout"])))
    res = _residuals(run["log"])
    last = _latest_time(case)
    values = {"refused": 0.0, "solver": solver, "last_time": last.name,
              "fields_written": sorted(p.name for p in last.iterdir() if p.is_file()),
              "residuals": res, "max_final_residual": max(res.values()) if res else None}
    return result(values, inp.get("limits"),
                  f"{solver}: ran to t = {last.name}, worst final residual "
                  f"{values['max_final_residual']:.2e}" if res else f"{solver}: ran to t = {last.name}",
                  files=[str(case / x) for x in run["logs"]],
                  notes=["the case is run as given: its physics, mesh and convergence are its own",
                         "open the time folders in ParaView (env `post`) to look at the fields"])


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fns = {"foam_couette": _run_couette, "foam_channel": _run_channel, "foam_case": _run_user_case}
    if kind not in fns:
        raise ValueError(f"unknown kind {kind!r}; known: {', '.join(sorted(fns))}")
    return fns[kind](inp, Path(work))


# --------------------------------------------------------------------------- references
def _evil_case(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """A case whose controlDict hides a #codeStream: it must be refused before anything runs."""
    (work / "system").mkdir(parents=True, exist_ok=True)
    (work / "system" / "controlDict").write_text(
        'FoamFile { format ascii; class dictionary; object controlDict; }\n'
        'application foamRun;\nsolver incompressibleFluid;\n'
        'endTime #codeStream { code #{ os << 1; #}; };\n', encoding="utf-8")
    return dict(inp)


_H, _UB, _NU = 0.1, 0.01, 1e-3

REFERENCES = [
    {   # exact: u = U y / h. A linear profile is represented exactly by a second-order scheme.
        "name": "couette_linear_profile", "kind": "foam_couette",
        "inp": {"height_m": 0.1, "wall_velocity_m_s": 0.1, "nu_m2_s": 1e-3, "cells_across": 20},
        "expect": {"profile_max_error_rel": [0.0, 1e-3], "u_mid_m_s": [0.05, 1e-3]},
    },
    {   # exact Poiseuille: u_max = 1.5 U and dp/dx = -12 nu U / h^2, developed region
        "name": "poiseuille_channel", "kind": "foam_channel",
        "inp": {"height_m": _H, "mean_velocity_m_s": _UB, "nu_m2_s": _NU,
                "length_to_height": 20.0, "cells_across": 20, "cells_along": 100},
        "expect": {"u_max_over_mean": [1.5, 0.01],
                   "dpdx_kinematic": [-12.0 * _NU * _UB / _H ** 2, 0.02],
                   "profile_max_error_rel": [0.0, 0.01]},
    },
    {   # a dictionary that would compile C++ is refused before OpenFOAM is even started
        "name": "codestream_case_refused", "kind": "foam_case", "setup": "_evil_case",
        "inp": {"solver": "incompressibleFluid"},
        "expect": {"refused": [1.0, 1e-9]},
    },
]
