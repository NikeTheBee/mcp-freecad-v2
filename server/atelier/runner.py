"""Run an adapter as a subprocess: inputs written as JSON, result read back, cached, supervised.

- Which kinds exist: read from each adapter's literal KINDS dict (ast, no import of tool code).
- A kind runs only if its adapter passed its reference cases for the current adapter code and
  environment lock (validation/<adapter>.json in MCPFC_HOME); a stale or missing validation runs
  the reference cases first, and a FAIL makes the kind unusable (§3.7, step 5).
- Cache: same inputs_hash (kind + params + geometry bytes + adapter code + pixi.lock) → the stored
  result, marked "cached".
- Supervision: argument list (no shell), python -I, timeout, memory ceiling, whole process tree
  killed on overrun; works the same for a foreground run and a background job.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import ADAPTERS, REPO, config, env_python, home

_NAME = re.compile(r"[a-z][a-z0-9_]{0,40}")
_STUDY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
MEM_FRACTION = 0.8           # kill a run that takes more than this share of the machine's RAM
FOREGROUND_MAX_S = 110.0     # longer than this: the call returns a job id instead of blocking


# ------------------------------------------------------------------ registry
def _literal(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                                for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def registry() -> Dict[str, Dict[str, Any]]:
    """kind -> {adapter, env, geometry, timeout, doc}, from the adapters' source."""
    kinds: Dict[str, Dict[str, Any]] = {}
    for f in sorted(ADAPTERS.glob("*.py")):
        if f.name.startswith("_") or not _NAME.fullmatch(f.stem):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            env, ks = _literal(tree, "ENV"), _literal(tree, "KINDS") or {}
        except (SyntaxError, ValueError, OSError):
            continue
        for k, meta in ks.items():
            kinds[k] = {"adapter": f.stem, "env": meta.get("env", env),
                        "geometry": bool(meta.get("geometry")), "timeout": float(meta.get("timeout", 300)),
                        "doc": str(meta.get("doc", ""))}
    return kinds


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def adapter_hash(adapter: str) -> str:
    h = hashlib.sha256()
    for f in (ADAPTERS / f"{adapter}.py", ADAPTERS / "_common.py", ADAPTERS / "_entry.py"):
        h.update(f.read_bytes())
    return h.hexdigest()


def lock_hash() -> str:
    try:
        return _sha((REPO / "pixi.lock").read_bytes())
    except OSError:
        return "no-lock"


# ------------------------------------------------------------------ process supervision
def _kill_tree(pid: int) -> None:
    try:
        import psutil
        p = psutil.Process(pid)
        for c in p.children(recursive=True):
            c.kill()
        p.kill()
    except Exception:  # noqa: BLE001 — already gone
        pass


def _clean_env(env: str, device: str = "cpu") -> Dict[str, str]:
    """Environment of an adapter: the pixi env first on PATH, no proxies, temp inside home."""
    from . import env_prefix
    prefix = env_prefix(env)
    keep = {k: v for k, v in os.environ.items()
            if k.upper() in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
                             "PROCESSOR_ARCHITECTURE", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
                             "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "OS", "USERNAME")}
    paths = []
    if prefix:
        paths += [str(prefix), str(prefix / "Library" / "bin"), str(prefix / "Scripts"),
                  str(prefix / "Library" / "mingw-w64" / "bin")]
    paths.append(os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32"))
    keep["PATH"] = os.pathsep.join(paths)
    tmp = (home() or Path.home()) / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    keep.update(TEMP=str(tmp), TMP=str(tmp), MCPFC_NO_NET="1", PYTHONUTF8="1",
                MPLBACKEND="Agg", OMP_NUM_THREADS=os.environ.get("OMP_NUM_THREADS", "8"),
                # OpenBLAS 0.3.34's auto-selected kernels crash (access violation) on some small
                # matrix products on Zen 4 CPUs; the Sandybridge kernels are correct everywhere
                OPENBLAS_CORETYPE=os.environ.get("OPENBLAS_CORETYPE", "Sandybridge"),
                # The socle environments resolve to the MKL build of libblas (ipopt, mumps and
                # casadi pull it in). MKL's default threading layer loads a second OpenMP
                # runtime next to the llvm-openmp already loaded by gmsh/vtk, and every LAPACK
                # call (inv, solve, svd, eig) then kills the process with no message. The
                # sequential layer is correct, deterministic -- which is what reference cases
                # want -- and here as fast as the threaded one. `TBB` also works if a future
                # solver needs threaded BLAS.
                MKL_THREADING_LAYER=os.environ.get("MKL_THREADING_LAYER", "SEQUENTIAL"),
                NO_PROXY="*", HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9")
    from .compute import env_vars
    keep.update(env_vars(device))            # threads from the compute profile, the device
    if prefix:
        keep["CONDA_PREFIX"] = str(prefix)
    # tools installed outside conda (tools.lock.json) and the other envs' prefixes: an adapter
    # finds SU2, Elmer, SITL, GMAT... or another environment's java through _common.tool()
    keep["MCPFC_TOOLS"] = json.dumps(config().get("tools") or {})
    keep["MCPFC_ENVS"] = json.dumps(config().get("envs") or {})
    return keep


def _launch(env: str, args: List[str], cwd: Path, device: str = "cpu") -> subprocess.Popen:
    py = env_python(env)
    if py is None:
        raise RuntimeError(f"environment {env!r} is not installed: run install\\install.ps1 "
                           f"(tools_status() says what is missing)")
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
    log = open(cwd / "adapter.log", "w", encoding="utf-8")  # noqa: SIM115 — closed by watcher
    proc = subprocess.Popen([str(py), "-I", str(ADAPTERS / "_entry.py"), *args], cwd=str(cwd),
                            env=_clean_env(env, device), stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, creationflags=flags)
    proc._mcpfc_log = log  # type: ignore[attr-defined]
    return proc


def _watch(proc: subprocess.Popen, timeout: float) -> Optional[str]:
    """Wait for the process; None when it ended, else why it was killed."""
    try:
        import psutil
        ceiling = psutil.virtual_memory().total * MEM_FRACTION
    except Exception:  # noqa: BLE001
        psutil, ceiling = None, None
    t_end = time.time() + timeout
    reason = None
    while proc.poll() is None:
        if time.time() > t_end:
            reason = f"timeout after {timeout:.0f} s"
            break
        if psutil is not None:
            try:
                p = psutil.Process(proc.pid)
                rss = sum(c.memory_info().rss for c in [p, *p.children(recursive=True)])
                if rss > ceiling:
                    reason = f"memory ceiling exceeded ({rss / 2**30:.1f} GiB)"
                    break
            except psutil.Error:
                pass
        time.sleep(0.2)
    if reason:
        _kill_tree(proc.pid)
        proc.wait(10)
    getattr(proc, "_mcpfc_log").close()
    return reason


def _read_output(out: Path, killed: Optional[str], cwd: Path) -> Dict[str, Any]:
    if killed:
        return {"verdict": "UNKNOWN", "summary": f"stopped: {killed}"}
    try:
        return json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tail = ""
        try:
            tail = (cwd / "adapter.log").read_text(encoding="utf-8", errors="replace")[-400:]
        except OSError:
            pass
        return {"verdict": "UNKNOWN", "summary": f"the adapter wrote no result. log tail: {tail}"}


# ------------------------------------------------------------------ validation (reference cases)
def validation_dir() -> Path:
    h = home()
    if h is None:
        raise RuntimeError("the workshop is not installed (no MCPFC_HOME): run install\\install.ps1")
    d = h / "validation"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _vkey(adapter: str, env: Optional[str]) -> str:
    own = (registry_adapters().get(adapter) or {}).get("env")
    return adapter if env in (None, own) else f"{adapter}@{env}"


def registry_adapters() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for meta in registry().values():
        out.setdefault(meta["adapter"], {"env": meta["env"]})
    return out


def validation(adapter: str, env: Optional[str] = None) -> Dict[str, Any]:
    """Stored reference-case result, with 'stale' when the code or the lock changed since."""
    try:
        v = json.loads((validation_dir() / f"{_vkey(adapter, env)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return {"verdict": "NOT_RUN"}
    v["stale"] = (v.get("adapter_hash") != adapter_hash(adapter) or v.get("lock_hash") != lock_hash())
    return v


def validate(adapter: str, env: str, timeout: float = 1800, device: str = "cpu") -> Dict[str, Any]:
    key = _vkey(adapter, env)
    if key != adapter and device == "cpu":
        device = "gpu"                     # a GPU twin is validated on its GPU
    d = validation_dir() / f"run_{key.replace('@', '_at_')}"
    d.mkdir(parents=True, exist_ok=True)
    out = d / "reference.json"
    out.unlink(missing_ok=True)
    proc = _launch(env, [adapter, "--reference", str(out)], d, device)
    res = _read_output(out, _watch(proc, timeout), d)
    res.update(adapter_hash=adapter_hash(adapter), lock_hash=lock_hash(), env=env,
               when=time.strftime("%Y-%m-%d %H:%M:%S"))
    (validation_dir() / f"{key}.json").write_text(json.dumps(res, indent=1, default=str),
                                                  encoding="utf-8")
    return res


def ensure_validated(adapter: str, env: str) -> Tuple[bool, str]:
    v = validation(adapter, env)
    if v.get("verdict") != "OK" or v.get("stale"):
        v = validate(adapter, env)
    if v.get("verdict") == "OK":
        return True, v.get("summary", "")
    bad = [c["name"] for c in v.get("cases", []) if not c.get("ok")]
    return False, (f"adapter {adapter} failed its reference cases ({v.get('summary', '')}; "
                   f"failing: {', '.join(bad) or '-'}): not usable until fixed")


# ------------------------------------------------------------------ runs and jobs
def input_files(params: Dict[str, Any], project_dir: Path) -> List[Tuple[str, Path]]:
    """params["files"]: project-relative paths (a URDF and its meshes, a CSV log, a netlist
    include...) handed to the adapter: (name inside the study folder, source). A path outside
    the project, absolute, or with a drive / stream is refused."""
    out: List[Tuple[str, Path]] = []
    root = project_dir.resolve()
    for f in params.get("files") or []:
        rel = str(f).replace("\\", "/")
        if not rel or rel.startswith("/") or ":" in rel or ".." in rel.split("/"):
            raise ValueError(f"files: {f!r} must be a path relative to the project folder")
        src = (root / rel).resolve()
        if not src.is_relative_to(root) or not src.is_file():
            raise ValueError(f"files: {f!r} is not a file of the project folder")
        out.append((rel, src))
    return out


def inputs_hash(kind: str, params: Dict[str, Any], geometry: Optional[Path], adapter: str,
                files: Optional[List[Tuple[str, Path]]] = None) -> str:
    h = hashlib.sha256()
    for rel, src in files or []:            # the bytes, not only the names
        h.update(rel.encode())
        h.update(hashlib.sha256(src.read_bytes()).digest())
    h.update(kind.encode())
    h.update(json.dumps(params, sort_keys=True, default=str).encode())
    if geometry is not None:
        data = geometry.read_bytes()
        if geometry.suffix.lower() in (".step", ".stp"):
            # the HEADER holds the export date and file name: only DATA is the geometry
            i = data.find(b"DATA;")
            data = data[i:] if i >= 0 else data
            # OpenCascade numbers each export of a session ("STEP translator 7.8 2")
            data = re.sub(rb"STEP translator [\d.]+ \d+", b"STEP translator", data)
        h.update(data)
    h.update(adapter_hash(adapter).encode())
    h.update(lock_hash().encode())
    return h.hexdigest()


_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def jobs_dir(project_dir: Path) -> Path:
    d = project_dir / "studies" / "_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run(kind: str, params: Dict[str, Any], project_dir: Path, study: str = "default",
        geometry: Optional[Path] = None, background: Optional[bool] = None,
        timeout: Optional[float] = None) -> Dict[str, Any]:
    """Run one simulation. Returns the result, or {"job": id} for a long one."""
    reg = registry()
    if kind not in reg:
        raise ValueError(f"unknown kind {kind!r}; known: {', '.join(sorted(reg))}")
    if not _STUDY.fullmatch(study):
        raise ValueError(f"study name {study!r}: letters, digits, _ and - only (max 40)")
    meta = reg[kind]
    if meta["geometry"] and geometry is None:
        raise ValueError(f"{kind} needs a geometry: pass target=<FreeCAD object>")
    from .compute import resolve
    run_env, device, dev_note = resolve(meta["env"], params.get("device"))
    ok, why = ensure_validated(meta["adapter"], run_env)
    if not ok:
        return {"verdict": "UNKNOWN", "summary": why}
    files = input_files(params, project_dir)
    ih = inputs_hash(kind, dict(params, device=device), geometry, meta["adapter"], files)
    work = project_dir / "studies" / study / f"{kind}-{ih[:10]}"
    out = work / "output.json"
    if out.is_file():
        try:
            res = json.loads(out.read_text(encoding="utf-8"))
            if res.get("inputs_hash") == ih:
                res["cached"] = True
                return res
        except ValueError:
            pass
    work.mkdir(parents=True, exist_ok=True)
    for rel, src in files:
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    inp = dict(params, kind=kind, device=device)
    if geometry is not None:
        target = work / ("geometry" + geometry.suffix.lower())
        if geometry.resolve() != target.resolve():
            target.write_bytes(geometry.read_bytes())
        inp["geometry"] = target.name
    (work / "input.json").write_text(json.dumps(inp, indent=1, default=str), encoding="utf-8")
    limit = float(timeout or meta["timeout"])

    def finish(proc) -> Dict[str, Any]:
        res = _read_output(out, _watch(proc, limit), work)
        res.update(inputs_hash=ih, kind=kind, tool_env=run_env, device=device, work=str(work),
                   reference=validation(meta["adapter"], run_env).get("summary", ""))
        if dev_note:
            res["summary"] = f"{res.get('summary', '')}; {dev_note}"
        res["files"] = [str(Path(f)) if Path(f).is_absolute() else str(work / f)
                        for f in res.get("files", [])]
        if res.get("verdict") in ("OK", "FAIL"):         # only real answers are cached
            out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        else:
            out.unlink(missing_ok=True)
            (work / "last_failure.json").write_text(json.dumps(res, indent=1, default=str),
                                                    encoding="utf-8")
        return res

    out.unlink(missing_ok=True)
    proc = _launch(run_env, [meta["adapter"], str(work / "input.json"), str(out)], work, device)
    jid = uuid.uuid4().hex[:8]
    rec = {"id": jid, "kind": kind, "study": study, "work": str(work), "pid": proc.pid,
           "started": time.time(), "timeout": limit, "state": "running"}
    (jobs_dir(project_dir) / f"{jid}.json").write_text(json.dumps(rec), encoding="utf-8")
    box: Dict[str, Any] = {}

    def bg():
        box["res"] = res = finish(proc)
        rec.update(state="done", ended=time.time(), verdict=res.get("verdict"))
        (jobs_dir(project_dir) / f"{jid}.json").write_text(json.dumps(rec), encoding="utf-8")

    th = threading.Thread(target=bg, daemon=True)
    with _LOCK:
        _JOBS[jid] = {"proc": proc, "thread": th}
    th.start()
    # a short run answers at once; a long one (or background=True) becomes a job to poll
    th.join(0 if background else (limit if background is False else FOREGROUND_MAX_S))
    if "res" in box:
        return box["res"]
    return {"job": jid, "verdict": "RUNNING",
            "summary": f"{kind} still running as job {jid} (limit {limit:.0f} s): job('{jid}')"}


def job(project_dir: Path, jid: str, action: str = "status") -> Dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{8}", jid or ""):
        raise ValueError("job id: 8 hex characters")
    f = jobs_dir(project_dir) / f"{jid}.json"
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"no job {jid} in this project") from None
    if action == "cancel":
        if rec.get("state") == "running":
            _kill_tree(int(rec["pid"]))
            rec["state"] = "cancelled"
            f.write_text(json.dumps(rec), encoding="utf-8")
        return {"job": jid, "state": rec["state"]}
    work = Path(rec["work"])
    if rec.get("state") == "running":
        alive = jid in _JOBS and _JOBS[jid]["proc"].poll() is None
        if not alive and jid not in _JOBS:
            try:
                import psutil
                alive = psutil.pid_exists(int(rec["pid"]))
            except Exception:  # noqa: BLE001
                alive = False
            if not alive:
                rec["state"] = "done" if (work / "output.json").is_file() else "lost"
        if alive:
            return {"job": jid, "state": "running",
                    "elapsed_s": round(time.time() - rec["started"]), "limit_s": rec["timeout"]}
    for name in ("output.json", "last_failure.json"):
        if (work / name).is_file():
            res = json.loads((work / name).read_text(encoding="utf-8"))
            res["job"] = jid
            return res
    return {"job": jid, "state": rec.get("state", "unknown"), "verdict": "UNKNOWN",
            "summary": "the job left no result (FreeCAD/MCP restart while it ran?)"}


def status(refresh: bool = False) -> Dict[str, Any]:
    from .compute import profile
    """What is installed and validated, per environment and adapter."""
    cfg = config()
    reg = registry()
    adapters: Dict[str, Dict[str, Any]] = {}
    for kind, meta in reg.items():
        a = adapters.setdefault(meta["adapter"], {"env": meta["env"], "kinds": []})
        a["kinds"].append(kind)
    for name, a in adapters.items():
        if env_python(a["env"]) is None:
            a["state"] = "env missing"
            continue
        v = validate(name, a["env"]) if refresh else validation(name)
        a["state"] = ("validated" if v.get("verdict") == "OK" and not v.get("stale")
                      else "stale" if v.get("stale") else v.get("verdict", "NOT_RUN").lower())
        a["reference"] = v.get("summary", "")
        a["versions"] = v.get("versions", {})
        twin = profile()["gpu_envs"].get(a["env"])
        if twin and env_python(twin) is not None:
            g = validate(name, twin) if refresh else validation(name, twin)
            a["gpu"] = (f"{twin} " + ("validated" if g.get("verdict") == "OK" and not g.get("stale")
                                      else "stale" if g.get("stale") else g.get("verdict", "NOT_RUN").lower()))
    return {"home": str(home()) if home() else None, "compute": profile(),
            "envs": {k: bool(env_python(k)) for k in (cfg.get("envs") or {})},
            "tools": {k: Path(v).exists() for k, v in (cfg.get("tools") or {}).items()},
            "adapters": adapters}
