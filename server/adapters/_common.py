"""Shared helpers for adapters (run inside a pixi environment; standard library only).

Adapter module contract (one file per tool or family in server/adapters/):

    ENV = "sim"                         # pixi environment it runs in
    KINDS = {                           # a LITERAL dict: the MCP server reads it without importing
        "fem_static": {"geometry": True, "timeout": 900, "doc": "one line"},
    }
    def run(kind, inp, work) -> dict    # {"verdict", "summary", "values", "files"}; work = study dir
    def versions() -> dict              # {"calculix": "2.23", ...}
    REFERENCES = [                      # reference cases with an exact or published answer (§9)
        {"name": "...", "kind": "...", "inp": {...}, "expect": {"value_key": [expected, rel_tol]}},
    ]

`inp["limits"]` = {"value_key": {"max": x} | {"min": x}}: the criteria the verdict is judged on.
No criteria -> UNKNOWN ("computed, nothing to judge against"), never a silent OK.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

ENV_PREFIX = Path(sys.prefix)


def exe(name: str) -> str:
    """An executable of THIS environment (Library/bin, Scripts, bin), never one found on PATH."""
    for sub in ("Library/bin", "Scripts", "bin", ""):
        for ext in ((".exe", ".bat", ".cmd", "") if os.name == "nt" else ("",)):
            p = ENV_PREFIX / sub / (name + ext)
            if p.is_file():
                return str(p)
    raise FileNotFoundError(f"{name} is not installed in the environment {ENV_PREFIX.name}")


def tool(name: str, rel: str = "") -> str:
    """A tool installed outside conda (install/tools.lock.json), e.g. tool("su2") -> SU2_CFD.exe,
    tool("su2", "SU2_DEF.exe") -> another file beside it."""
    tools = json.loads(os.environ.get("MCPFC_TOOLS") or "{}")
    p = tools.get(name)
    if not p or not Path(p).exists():
        raise FileNotFoundError(f"tool {name!r} is not installed: run install.ps1 -Packs <its pack>")
    p = Path(p)
    if rel:
        p = p.parent / rel
        if not p.exists():
            raise FileNotFoundError(f"{rel} not found beside {name}")
    return str(p)


def env_exe(env: str, name: str) -> str:
    """An executable of ANOTHER pixi environment (e.g. java of `space` for OpenRocket)."""
    envs = json.loads(os.environ.get("MCPFC_ENVS") or "{}")
    if env not in envs:
        raise FileNotFoundError(f"environment {env!r} is not installed")
    for sub in ("Library/bin", "Scripts", "bin", ""):
        for ext in (".exe", ".bat", ".cmd", ""):
            p = Path(envs[env]) / sub / (name + ext)
            if p.is_file():
                return str(p)
    raise FileNotFoundError(f"{name} not found in environment {env}")


def run_exe(args: Sequence[str], cwd: Path, timeout: float, stdin: Optional[str] = None,
            env_extra: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """Run a known executable with an argument list (no shell), a timeout, output captured."""
    env = dict(os.environ)
    env.update(env_extra or {})
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
    return subprocess.run(list(args), cwd=str(cwd), input=stdin, capture_output=True, text=True,
                          timeout=timeout, env=env, creationflags=flags)


def judge(values: Dict[str, Any], limits: Optional[Dict[str, Dict[str, float]]]) -> tuple:
    """(verdict, lines) of the values against the caller's limits."""
    if not limits:
        return "UNKNOWN", ["no criteria given: computed, nothing judged"]
    verdict, lines = "OK", []
    for key, lim in limits.items():
        v = values.get(key)
        if not isinstance(v, (int, float)) or (isinstance(v, float) and not math.isfinite(v)):
            verdict = "UNKNOWN" if verdict == "OK" else verdict
            lines.append(f"{key}: not computed -> UNKNOWN")
            continue
        ok = True
        if "max" in lim and v > float(lim["max"]):
            ok = False
        if "min" in lim and v < float(lim["min"]):
            ok = False
        bound = " ".join(f"{k} {lim[k]:g}" for k in ("min", "max") if k in lim)
        lines.append(f"{key} = {v:.4g} ({bound}) {'OK' if ok else 'FAIL'}")
        if not ok:
            verdict = "FAIL"
    return verdict, lines


def result(values: Dict[str, Any], limits, head: str, files: Iterable[str] = (),
           notes: Iterable[str] = ()) -> Dict[str, Any]:
    verdict, lines = judge(values, limits)
    summary = "; ".join([head, *lines, *notes])
    return {"verdict": verdict, "summary": summary, "values": values, "files": list(files)}


def close(got: float, expected: float, rel_tol: float) -> bool:
    if expected == 0:
        return abs(got) <= rel_tol
    return abs(got - expected) <= rel_tol * abs(expected)


def run_references(mod, out_dir: Path) -> Dict[str, Any]:
    """Run every reference case of an adapter; OK only if all match."""
    cases: List[Dict[str, Any]] = []
    all_ok = True
    for ref in getattr(mod, "REFERENCES", []):
        work = out_dir / ("ref_" + ref["name"])
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        t0 = time.time()
        try:
            inp = dict(ref["inp"], kind=ref["kind"])
            if "setup" in ref:                      # a callable name of the module (geometry...)
                inp = getattr(mod, ref["setup"])(inp, work)
            res = mod.run(ref["kind"], inp, work)
            checks = []
            ok = True
            for key, (expected, tol) in ref["expect"].items():
                got = (res.get("values") or {}).get(key)
                good = isinstance(got, (int, float)) and close(float(got), float(expected), float(tol))
                ok &= good
                checks.append({"key": key, "expected": expected, "got": got, "rel_tol": tol,
                               "ok": good})
            cases.append({"name": ref["name"], "ok": ok, "checks": checks,
                          "elapsed_s": round(time.time() - t0, 2)})
        except Exception as e:  # noqa: BLE001
            ok = False
            cases.append({"name": ref["name"], "ok": False, "error": f"{type(e).__name__}: {e}"[:400]})
        all_ok &= ok
    if not cases:
        return {"verdict": "UNKNOWN", "summary": "no reference case", "cases": []}
    n_ok = sum(c["ok"] for c in cases)
    return {"verdict": "OK" if all_ok else "FAIL", "cases": cases,
            "summary": f"{n_ok}/{len(cases)} reference cases match",
            "versions": mod.versions() if hasattr(mod, "versions") else {}}


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def geometry_path(inp: Dict[str, Any], work: Path) -> Path:
    """The geometry file handed by the server (always inside the study folder)."""
    g = inp.get("geometry")
    if not g:
        raise ValueError("this kind needs a geometry (target) but none was given")
    p = (work / g).resolve() if not Path(g).is_absolute() else Path(g).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"geometry file missing: {p.name}")
    return p
