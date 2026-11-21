"""Cyber gate of the workshop: the rules every adapter and every pinned dependency must obey.

`security_scan.py` looks for secrets, sensitive files and a non-local bind. This one checks the
*structure* of the code and of the supply chain, so that a future adapter cannot quietly
reintroduce a class of problem that has already been reasoned about:

A. every adapter (`server/adapters/*.py`) -- read with `ast`, never imported:
   A1 no shell: no `shell=True`, `os.system`, `os.popen`, `subprocess.getoutput`;
   A2 no dynamic code or unsafe deserialisation: `eval`, `exec`, `pickle`, `marshal`, `shelve`,
      `yaml.load` without a safe loader;
   A3 no network of its own: an adapter talks to files and to loopback only, and the network
      guard in `_entry.py` is what enforces that at runtime -- here we check it never even
      imports an HTTP client;
   A4 no XML parser on untrusted input (an imported STEP, SVG or log is untrusted): either
      nothing, or `defusedxml`;
   A5 the contract: `ENV`, a literal `KINDS`, `run`, `versions`, and a NON-EMPTY `REFERENCES`
      -- a kind with no reference case can never be trusted, so it must not exist;
   A6 writes stay in the job folder: no absolute path literal, no `tempfile` (the runner gives
      every run its own folder and cleans it up);
   A7 only the known tuning environment variables are set, so that no adapter can switch off
      the network guard or the deterministic BLAS by assigning to `os.environ`.

B. `install/tools.lock.json`: https only, a real SHA256, a licence, a known archive type, a
   destination under `tools/`, a size, and where the hash came from.

C. `pixi.lock`: every package carries a SHA256, and every download host is one we declared.

D. `packs/packs.json`: every tool it names exists in the lock, every environment in pixi.toml.

E. the FreeCAD-side rule of CLAUDE.md: a layer that writes files must go through the shared
   gates (`safe_out_path` / `safe_under`), never build a path from caller text by itself.

Offline, no dependency, reads only. Prints CYBER_SCAN_OK when clean; exit 1 and a precise list
otherwise. Wired into run_all_tests.py. Sentinel: CYBER_SCAN_OK
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADAPTERS = REPO / "server" / "adapters"
LAYERS = REPO / "server" / "freecad_layers"

# A1 / A2 / A3 / A4: names that must not appear in an adapter, and why
BANNED_CALLS = {
    "os.system": "runs a shell",
    "os.popen": "runs a shell",
    "subprocess.getoutput": "runs a shell",
    "subprocess.getstatusoutput": "runs a shell",
    "eval": "evaluates code built at run time",
    "exec": "executes code built at run time",
    "pickle.load": "deserialises code",
    "pickle.loads": "deserialises code",
    "marshal.loads": "deserialises code",
}
BANNED_IMPORTS = {
    "pickle": "deserialisation of code; adapters exchange JSON, CSV, VTU and STEP",
    "marshal": "deserialisation of code",
    "shelve": "pickle under another name",
    "requests": "an adapter must not reach the network itself",
    "urllib.request": "an adapter must not reach the network itself",
    "http.client": "an adapter must not reach the network itself",
    "ftplib": "an adapter must not reach the network itself",
    "smtplib": "an adapter must not reach the network itself",
    "telnetlib": "an adapter must not reach the network itself",
    "xmlrpc.client": "an adapter must not reach the network itself",
    "xml.etree.ElementTree": "an XML parser on untrusted input: use defusedxml",
    "xml.dom.minidom": "an XML parser on untrusted input: use defusedxml",
    "xml.sax": "an XML parser on untrusted input: use defusedxml",
    "lxml": "an XML parser on untrusted input: use defusedxml",
    "tempfile": "the runner gives every run its own folder: write there, not in the system temp",
}
# A7: the only environment variables an adapter may set, with the reason each one exists
ALLOWED_ENV_WRITES = {
    "OPENBLAS_CORETYPE",      # OpenBLAS 0.3.34 faults on Zen 4 in its AVX2 kernels
    "MKL_THREADING_LAYER",    # MKL's default layer loads a second OpenMP runtime and crashes
    "OPENMDAO_REPORTS",       # OpenMDAO would scatter HTML reports in the job folders
    "PYBAMM_DISABLE_TELEMETRY",   # switches telemetry OFF; exactly what we want set
    "JAVA_HOME",              # points at the JDK pinned inside the `space` environment
    "OMP_NUM_THREADS",
    "MPLBACKEND",
    "PYTHONUTF8",
    "SU2_RUN", "SU2_HOME",    # SU2 needs its own installation to be announced
    "ELMER_HOME", "ELMER_LIB",
    "GMSH_HOME",
}

# Exceptions that were examined and accepted, each with the reason. An exception is listed here
# on purpose: it stays visible in review, and a NEW adapter does not inherit it.
ACCEPTED = {
    ("dynamics.py", "xml.etree.ElementTree"):
        "reads URDF written by our own exporter or supplied by the user. The stdlib "
        "ElementTree does not resolve external entities, so there is no file read or SSRF; "
        "the residual risk is entity expansion, i.e. a slow parse of the user's own file.",
    ("gazebo.py", "xml.etree.ElementTree"):
        "same: URDF / SDF / MJCF conversion checks, stdlib ElementTree, no external entities.",
    ("firmware.py", "tempfile"):
        "Renode is given a short path of its own; the runner redirects TEMP and TMP into the "
        "workshop folder, so this stays inside MCPFC_HOME. Hygiene item, not an escape: "
        "see docs/AUDIT_CYBER_2026-11-21.md finding 2.",
}
# Section E: layers whose writes were checked and do not need the path gates, with the reason.
ACCEPTED_LAYERS = {
    "state.py": "writes only project_state/state.json, its temp file and a 0600 lock, all "
                "derived from the project folder, never from caller text.",
}
# Section B: destinations outside tools/, with the reason.
ACCEPTED_DEST = {"pixi": "pixi is the package manager itself, not one of the tools it installs"}
ARCHIVES = {"zip", "7z", "inno", "file"}
ALLOWED_HOSTS = {"conda.anaconda.org", "files.pythonhosted.org", "pypi.org"}
ABS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/(?:usr|etc|var|tmp|home|bin|opt)/)")


def dotted(node: ast.AST) -> str:
    """`a.b.c` for an Attribute/Name chain, else ''."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def adapter_problems(path: Path) -> list:
    """Every rule of section A, by reading the file. The module is never imported."""
    rel = path.relative_to(REPO).as_posix()
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"{rel}: does not parse ({e})"]
    bad = []
    internal = path.name.startswith("_")
    # a literal path that is only the fallback of os.environ.get (SYSTEMROOT, ...) is read, not
    # written; collect those first so the A6 pass does not flag them
    # ... including `env.get("SYSTEMROOT", r"C:\Windows")` on a scrubbed local copy of the
    # environment: a `.get()` fallback is a value that is READ, never a path written to
    env_defaults = {a.value for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and dotted(node.func).endswith(".get")
                    for a in node.args[1:]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)}

    for node in ast.walk(tree):
        # A1 / A2: forbidden calls, and shell=True on any call
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name in BANNED_CALLS:
                bad.append(f"{rel}:{node.lineno}: A1/A2 `{name}` {BANNED_CALLS[name]}")
            if name.endswith("yaml.load") or name == "load":
                if isinstance(node.func, ast.Attribute) and node.func.attr == "load" \
                        and dotted(node.func).startswith("yaml"):
                    kw = {k.arg for k in node.keywords}
                    if "Loader" not in kw:
                        bad.append(f"{rel}:{node.lineno}: A2 yaml.load without a safe Loader")
            for kw in node.keywords:
                if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant)
                                              and kw.value.value is False):
                    bad.append(f"{rel}:{node.lineno}: A1 shell= must be absent or False")
        # A3 / A4 / A6: forbidden imports
        if isinstance(node, ast.Import):
            for a in node.names:
                for banned, why in BANNED_IMPORTS.items():
                    if (a.name == banned or a.name.startswith(banned + ".")) \
                            and (path.name, banned) not in ACCEPTED:
                        bad.append(f"{rel}:{node.lineno}: A3/A4/A6 imports `{a.name}`: {why}")
        if isinstance(node, ast.ImportFrom) and node.module:
            for banned, why in BANNED_IMPORTS.items():
                if (node.module == banned or node.module.startswith(banned + ".")) \
                        and (path.name, banned) not in ACCEPTED:
                    bad.append(f"{rel}:{node.lineno}: A3/A4/A6 imports from `{node.module}`: {why}")
        # A7: assignments into os.environ
        if isinstance(node, ast.Call) and dotted(node.func).endswith("environ.setdefault"):
            key = node.args[0] if node.args else None
            if not (isinstance(key, ast.Constant) and key.value in ALLOWED_ENV_WRITES):
                shown = getattr(key, "value", "<computed>")
                bad.append(f"{rel}:{node.lineno}: A7 sets the environment variable {shown!r}, "
                           f"which is not in the reviewed list")
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) \
                and dotted(node.value).endswith("os.environ"):
            key = node.slice
            if not (isinstance(key, ast.Constant) and key.value in ALLOWED_ENV_WRITES):
                shown = getattr(key, "value", "<computed>")
                bad.append(f"{rel}:{node.lineno}: A7 assigns os.environ[{shown!r}], "
                           f"which is not in the reviewed list")
        # A6: absolute path literals
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and ABS_PATH.match(node.value) and node.value not in env_defaults:
            bad.append(f"{rel}:{node.lineno}: A6 absolute path literal {node.value!r}: "
                       f"an adapter writes in the folder the runner gives it")

    if internal:
        return bad
    # A5: the contract. A kind without a reference case must not exist.
    top = {t.targets[0].id for t in tree.body
           if isinstance(t, ast.Assign) and isinstance(t.targets[0], ast.Name)}
    funcs = {f.name for f in tree.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for need in ("ENV", "KINDS", "REFERENCES"):
        if need not in top:
            bad.append(f"{rel}: A5 no module-level `{need}`")
    for need in ("run", "versions"):
        if need not in funcs:
            bad.append(f"{rel}: A5 no `{need}()`")
    for t in tree.body:
        if isinstance(t, ast.Assign) and isinstance(t.targets[0], ast.Name):
            if t.targets[0].id == "REFERENCES":
                if not isinstance(t.value, ast.List) or not t.value.elts:
                    bad.append(f"{rel}: A5 `REFERENCES` must be a non-empty list: a kind with no "
                               f"reference case can never be reported as OK")
            if t.targets[0].id == "KINDS" and not isinstance(t.value, ast.Dict):
                bad.append(f"{rel}: A5 `KINDS` must be a literal dict (the runner reads it "
                           f"without importing the module)")
    return bad


def lock_problems() -> list:
    """Section B: the pinned tools."""
    bad = []
    lock = json.loads((REPO / "install" / "tools.lock.json").read_text(encoding="utf-8"))
    seen_dest = {}
    for name, t in lock.items():
        if name.startswith("_"):
            continue
        where = f"tools.lock.json[{name}]"
        url = t.get("url", "")
        if not url.startswith("https://"):
            bad.append(f"{where}: B url is not https: {url!r}")
        host = url.split("/")[2] if url.count("/") > 2 else ""
        if not host:
            bad.append(f"{where}: B url has no host")
        if not re.fullmatch(r"[0-9a-f]{64}", str(t.get("sha256", ""))):
            bad.append(f"{where}: B sha256 is not 64 lowercase hex characters")
        for key in ("license", "sha_source", "bin", "version"):
            if not str(t.get(key, "")).strip():
                bad.append(f"{where}: B `{key}` is empty")
        if t.get("archive") not in ARCHIVES:
            bad.append(f"{where}: B unknown archive type {t.get('archive')!r}")
        if not str(t.get("dest", "")).startswith("tools/") and name not in ACCEPTED_DEST:
            bad.append(f"{where}: B dest must be under tools/, got {t.get('dest')!r}")
        if not isinstance(t.get("size"), int) or t.get("size", 0) <= 0:
            bad.append(f"{where}: B size must be a positive integer (the preflight announces it)")
        if not isinstance(t.get("packs"), list) or not t["packs"]:
            bad.append(f"{where}: B `packs` must say which pack installs it")
        dest = t.get("dest")
        if dest in seen_dest and "file" not in (t.get("archive"), lock[seen_dest[dest]].get("archive")):
            bad.append(f"{where}: B shares its destination with {seen_dest[dest]} although "
                       f"neither is a single-file tool")
        seen_dest.setdefault(dest, name)
    return bad


def pixi_problems() -> list:
    """Section C: every package hashed, every host declared."""
    bad = []
    text = (REPO / "pixi.lock").read_text(encoding="utf-8")
    hosts = set(re.findall(r"https://([A-Za-z0-9.\-]+)/", text))
    for h in sorted(hosts - ALLOWED_HOSTS):
        bad.append(f"pixi.lock: C downloads from an undeclared host {h!r}")
    # in the `packages:` section every entry is `- conda:|pypi: <url>` followed by its hashes
    pkgs = re.split(r"\npackages:\n", text, maxsplit=1)
    if len(pkgs) != 2:
        return bad + ["pixi.lock: C no `packages:` section"]
    unhashed = []
    for block in re.split(r"\n- (?=conda:|pypi:)", pkgs[1]):
        m = re.match(r"(conda|pypi):\s*(\S+)", block.strip())
        if not m:
            continue
        if not re.search(r"\bsha256:\s*[0-9a-f]{64}", block):
            unhashed.append(m.group(2))
    if unhashed:
        bad.append(f"pixi.lock: C {len(unhashed)} package(s) without a sha256, first: "
                   f"{unhashed[0]}")
    return bad


def packs_problems() -> list:
    """Section D: packs name tools and environments that exist."""
    bad = []
    packs = json.loads((REPO / "packs" / "packs.json").read_text(encoding="utf-8"))
    lock = json.loads((REPO / "install" / "tools.lock.json").read_text(encoding="utf-8"))
    toml = (REPO / "pixi.toml").read_text(encoding="utf-8")
    envs = set(re.findall(r"(?m)^(\w[\w\-]*)\s*=\s*\{\s*features", toml.split("[environments]")[-1]))
    for pack, spec in packs.items():
        if pack.startswith("_"):
            continue
        for t in spec.get("tools", []):
            if t not in lock:
                bad.append(f"packs.json[{pack}]: D tool {t!r} is not in tools.lock.json")
        for e in spec.get("envs", []):
            if envs and e not in envs:
                bad.append(f"packs.json[{pack}]: D environment {e!r} is not in pixi.toml")
        for s in spec.get("skills", []):
            if not (REPO / "skills" / s).is_dir():
                bad.append(f"packs.json[{pack}]: D skill {s!r} has no folder in skills/")
    return bad


def layer_problems() -> list:
    """Section E: a FreeCAD-side layer that writes files must use the shared path gates."""
    bad = []
    writes = re.compile(r"\.write_text\(|\.write_bytes\(|\bopen\([^)]*['\"][wa]")
    for p in sorted(LAYERS.glob("*.py")):
        if p.name == "__init__.py":
            continue
        src = p.read_text(encoding="utf-8")
        if writes.search(src) and not re.search(r"safe_out_path|safe_under", src) \
                and p.name not in ACCEPTED_LAYERS:
            bad.append(f"{p.relative_to(REPO).as_posix()}: E writes files without going through "
                       f"safe_out_path / safe_under (SECURITY.md, CLAUDE.md)")
    return bad


def main() -> int:
    problems = []
    adapters = sorted(ADAPTERS.glob("*.py"))
    for p in adapters:
        problems += adapter_problems(p)
    problems += lock_problems() + pixi_problems() + packs_problems() + layer_problems()
    n_ad = len([p for p in adapters if not p.name.startswith("_")])
    if problems:
        print(f"!! cyber gate: {len(problems)} problem(s)")
        for p in problems:
            print("  " + p)
        return 1
    print(f"checked {n_ad} adapters, the pinned tools, pixi.lock, the packs and the FreeCAD "
          f"layers - no violation")
    print("CYBER_SCAN_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
