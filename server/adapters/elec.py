"""Electrical and electronic studies (env `elec`): circuits, batteries, magnets, wires, power budget.

Kinds:
- spice         a netlist run by ngspice in batch mode, sandboxed; measurements returned as numbers
- battery       PyBaMM constant-current discharge (SPM / SPMe / DFN, a named parameter set)
- magnet        Magpylib field of loops, solenoids and permanent magnets; force and torque
- wire_sizing   conductor resistance, voltage drop, loss, cross-section for a max drop, fuse rules
- power_budget  rails and loads -> battery current (mean, peak, rms bound), C-rate, autonomy

Units (SI unless the key says otherwise):
- spice: SPICE units (V, A, ohm, F, H, s, Hz); numbers accept SPICE suffixes (1k, 100n, 1meg).
- battery: A, V, Ah, Wh, degC, h.
- magnet: m, A, T (polarization J = mu0 M, close to the datasheet remanence Br), N, N m.
- wire_sizing: length m, cross-section mm^2, current A, voltage V, temperature degC, ohm, W.
- power_budget: A, V, Ah, W, h; duty is a fraction 0..1.

Safety rules applied (docs/FEUILLE_DE_ROUTE_ATELIER.md §10.2): above 50 V AC or 120 V DC the result
says "qualified person required" and the verdict cannot be OK; above 60 V DC it lists insulation,
interlock and a hardware emergency stop; a fuse must sit below the wire's sourced rating, with a
25 % derating margin; cell limits come only from the datasheet values passed in (absent: UNKNOWN).
Nothing here talks to a real instrument or board.
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _common import exe, result, run_exe

ENV = "elec"
KINDS = {
    "spice": {"geometry": False, "timeout": 300,
              "doc": "ngspice netlist (.op/.ac/.dc/.tran), sandboxed; .meas and vector measures as numbers"},
    "battery": {"geometry": False, "timeout": 300,
                "doc": "PyBaMM SPM/SPMe/DFN constant-current discharge: capacity Ah, energy Wh, temperature"},
    "magnet": {"geometry": False, "timeout": 300,
               "doc": "Magpylib: B of loops/solenoids/cuboid/cylinder/sphere magnets at points, force/torque"},
    "wire_sizing": {"geometry": False, "timeout": 60,
                    "doc": "wire resistance, voltage drop, loss, section for a max drop %, fuse vs sourced rating"},
    "power_budget": {"geometry": False, "timeout": 60,
                     "doc": "rails/loads -> battery mean/peak/rms current, C-rate vs datasheet, autonomy, margin"},
}

MU0 = 4e-7 * math.pi          # the magnitude used by Magpylib (1.25663706127e-06), SI


def versions() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        p = run_exe([exe("ngspice_con"), "-v"], Path.cwd(), 30)
        m = re.search(r"ngspice-(\d+)", p.stdout + p.stderr)
        out["ngspice"] = m.group(1) if m else "?"
    except Exception as e:  # noqa: BLE001
        out["ngspice"] = f"missing ({type(e).__name__})"
    for mod in ("pybamm", "magpylib", "numpy", "scipy"):
        try:
            if mod == "pybamm":
                os.environ.setdefault("PYBAMM_DISABLE_TELEMETRY", "true")
            out[mod] = str(__import__(mod).__version__)
        except Exception as e:  # noqa: BLE001
            out[mod] = f"missing ({type(e).__name__})"
    return out


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fn = {"spice": _spice, "battery": _battery, "magnet": _magnet,
          "wire_sizing": _wire_sizing, "power_budget": _power_budget}.get(kind)
    if fn is None:
        raise ValueError(f"unknown kind {kind!r}; one of {sorted(KINDS)}")
    return fn(inp, Path(work))


def _safety_voltage(v_dc: float = 0.0, v_ac_rms: float = 0.0) -> Tuple[bool, List[str]]:
    """(qualified person required, notes) for a circuit voltage (§10.2)."""
    notes: List[str] = []
    qualified = abs(v_dc) > 120.0 or abs(v_ac_rms) > 50.0
    if qualified:
        notes.append("MAINS/HIGH VOLTAGE (> 50 V AC or > 120 V DC): qualified person required; no "
                     "mains-side design by the AI, certified off-the-shelf supplies only")
    elif abs(v_dc) > 60.0:
        notes.append("above 60 V DC (beyond safety extra-low voltage): insulation, interlock and a "
                     "hardware emergency stop independent of the firmware")
    return qualified, notes


def _finish(values, limits, head, files=(), notes=(), qualified=False) -> Dict[str, Any]:
    res = result(values, limits, head, files, notes)
    if qualified and res["verdict"] == "OK":
        res["verdict"] = "UNKNOWN"
        res["summary"] += "; verdict held at UNKNOWN: qualified person required"
    return res


# ================================================================== spice
_SUFFIX = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "mil": 25.4e-6, "m": 1e-3, "u": 1e-6,
           "n": 1e-9, "p": 1e-12, "f": 1e-15}
_NUM = re.compile(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(meg|mil|[tgkmunpf])?[a-z]*\s*$", re.I)


def _num(s) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    m = _NUM.fullmatch(str(s))
    if not m:
        raise ValueError(f"not a number: {s!r}")
    return float(m.group(1)) * _SUFFIX.get((m.group(2) or "").lower(), 1.0)


_ANALYSES = ("op", "ac", "dc", "tran", "noise", "tf", "pz", "sens", "disto", "sp")
_DOT_OK = {".param", ".func", ".subckt", ".ends", ".model", ".ic", ".nodeset", ".global", ".save",
           ".print", ".plot", ".four", ".options", ".option", ".opt", ".temp", ".title", ".csparam",
           ".width", ".if", ".elseif", ".else", ".endif", ".endl", ".probe", ".include", ".inc",
           ".lib", ".meas", ".measure", ".control", ".endc", ".end"} | {"." + a for a in _ANALYSES}
# control-block commands allowed (nutmeg); everything else (shell, cd, source, load, codemodel,
# use, aspice, rspice, gnuplot, hardcopy, plot, edit, alias, circbyline, osdi, quit...) is refused
_CTRL_OK = {"let", "unlet", "print", "echo", "meas", "measure", "set", "unset", "if", "else", "end",
            "while", "repeat", "dowhile", "foreach", "break", "continue", "label", "goto", "alter",
            "altermod", "reset", "show", "showmod", "display", "compose", "linearize", "fft", "spec",
            "fourier", "setplot", "destroy", "run", "option", "options", "save", "write", "wrdata",
            "status", "delete", "stop", "resume", "step", "define", "undefine", "deftype",
            "settype", "strcmp", "shift", "remzerovec", "transpose", "inventory", "rusage",
            "version", "listing", "setseed", "setscale", "asciiplot", "psd"} | set(_ANALYSES)
_WRITERS = {"write", "wrdata"}
_FILE_TOKEN = re.compile(r"\b([a-z_]*file[a-z_]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s)]+)", re.I)


def _logical_lines(text: str) -> List[str]:
    out: List[str] = []
    for raw in text.replace("\r", "").split("\n"):
        s = raw.rstrip()
        if s.lstrip().startswith("+") and out:
            out[-1] += " " + s.lstrip()[1:]
        else:
            out.append(s)
    return out


def _inside(work: Path, base: Path, p: str) -> Path:
    p = p.strip().strip("\"'")
    if not p:
        raise ValueError("empty file name in the netlist")
    if "$" in p or "`" in p:
        raise ValueError(f"file name {p!r}: no variables or substitutions")
    q = Path(p)
    root = work.resolve()
    for b in {base.resolve(), root}:
        r = (q if q.is_absolute() else b / q).resolve()
        if not r.is_relative_to(root):
            raise PermissionError(f"refused: {p!r} is outside the study folder")
    return (q if q.is_absolute() else base / q).resolve()


def _check_control(cmd: str, work: Path) -> None:
    if any(c in cmd for c in "<>`"):
        raise PermissionError(f"control line {cmd.strip()!r}: redirection and backquotes are refused "
                              f"(use gt/lt in conditions, wrdata/write <file> for output)")
    for part in cmd.split(";"):
        words = part.split()
        if not words:
            continue
        w = words[0].lower()
        if w not in _CTRL_OK:
            raise PermissionError(f"control command {w!r} is refused in the sandbox")
        if w in _WRITERS and len(words) > 1:
            _inside(work, work, words[1])
        if w == "set" and re.search(r"\bsourcepath\b", part, re.I):
            raise PermissionError("set sourcepath is refused")


def _sanitize(text: str, work: Path, base: Path, depth: int = 0) -> Dict[str, List[str]]:
    """Split a netlist into body / analyses / meas / control lines; raise on anything unsafe."""
    if depth > 4:
        raise ValueError("includes nested too deep")
    if len(text) > 2_000_000:
        raise ValueError("netlist larger than 2 MB")
    if "`" in text:
        raise PermissionError("backquote (shell) substitution is refused")
    parts: Dict[str, List[str]] = {"body": [], "analyses": [], "meas": [], "control": []}
    in_ctrl = False
    for line in _logical_lines(text):
        s = line.strip()
        if not s or s.startswith("*"):
            parts["body"].append(line)
            continue
        for m in _FILE_TOKEN.finditer(s):
            f = _inside(work, base, m.group(2))
            if depth == 0 and not f.is_file():
                raise FileNotFoundError(f"{m.group(1)}={m.group(2)}: file missing in the study folder")
        low = s.lower()
        word = low.split()[0]
        if in_ctrl:
            if word == ".endc":
                in_ctrl = False
                continue
            if word in ("quit", "exit"):
                continue                                        # the adapter quits itself
            _check_control(s, work)
            parts["control"].append(s)
            continue
        if word.startswith("."):
            if word not in _DOT_OK:
                raise PermissionError(f"directive {word} is refused in the sandbox")
            if word == ".end":
                break
            if word == ".control":
                in_ctrl = True
                continue
            if word in (".include", ".inc") or (word == ".lib" and len(s.split()) >= 3):
                arg = s.split(None, 1)[1]
                arg = arg.rsplit(None, 1)[0] if word == ".lib" else arg
                f = _inside(work, base, arg)
                if not f.is_file():
                    raise FileNotFoundError(f"{word} {arg}: file missing in the study folder")
                sub = _sanitize(f.read_text(encoding="utf-8", errors="replace"), work, f.parent, depth + 1)
                if sub["control"] or (depth == 0 and sub["analyses"]):
                    raise PermissionError(f"{f.name}: an included file may not hold .control or analyses")
                parts["body"].append(f"{s.split()[0]} \"{f}\"" + (f" {s.split()[-1]}" if word == ".lib" else ""))
                continue
            if word[1:] in _ANALYSES:
                parts["analyses"].append(s)
                continue
            if word in (".meas", ".measure"):
                parts["meas"].append(s)
                continue
        parts["body"].append(line)
    if in_ctrl:
        raise ValueError(".control without .endc")
    return parts


def _read_raw(path: Path) -> List[Dict[str, Any]]:
    """ngspice ASCII rawfile -> [{plot, complex, names, types, data{name: array}}]."""
    import numpy as np
    text = path.read_text(encoding="utf-8", errors="replace")
    plots = []
    pos = 0
    while True:
        i = text.find("Plotname:", pos)
        if i < 0:
            break
        j = text.find("Values:", i)
        if j < 0:
            break
        head = text[i:j]
        name = head.split("\n", 1)[0].split(":", 1)[1].strip()
        is_c = "complex" in re.search(r"Flags:(.*)", head).group(1)
        nv = int(re.search(r"No\. Variables:\s*(\d+)", head).group(1))
        npnt = int(re.search(r"No\. Points:\s*(\d+)", head).group(1))
        var_lines = head.split("Variables:", 2)[-1].strip().split("\n")[:nv]
        names, types = [], []
        for vl in var_lines:
            f = vl.split()
            names.append(f[1].lower())
            types.append(f[2].lower() if len(f) > 2 else "")
        nxt = text.find("Title:", j)
        body = text[j + len("Values:"): nxt if nxt >= 0 else len(text)].split()
        need = npnt * (nv + 1)
        if len(body) < need:
            raise RuntimeError(f"rawfile truncated ({len(body)} < {need} tokens)")
        tok = np.array(body[:need], dtype=object).reshape(npnt, nv + 1)[:, 1:]
        data = {}
        for k, n in enumerate(names):
            col = tok[:, k]
            if is_c:
                re_im = np.array([c.split(",") for c in col], dtype=float)
                data[n] = re_im[:, 0] + 1j * re_im[:, 1]
            else:
                data[n] = col.astype(float)
        plots.append({"plot": name, "complex": is_c, "names": names, "types": types, "data": data})
        pos = j + 1
    return plots


_FUNCS = ("db", "mag", "phase_deg", "real", "imag")


def _vector(expr: str, plot: Dict[str, Any]):
    import numpy as np
    e = expr.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(vdb|vm)\((.+)\)", e)
    if m:
        e = ("db" if m.group(1) == "vdb" else "mag") + f"(v({m.group(2)}))"
    m = re.fullmatch(r"([a-z_]+)\((.+)\)", e)
    if m and m.group(1) in _FUNCS:
        y = np.asarray(_vector(m.group(2), plot))
        f = m.group(1)
        if f == "db":
            return 20 * np.log10(np.abs(y))
        if f == "mag":
            return np.abs(y)
        if f == "phase_deg":
            return np.degrees(np.unwrap(np.angle(y)))
        return y.real if f == "real" else y.imag
    d = plot["data"]
    if e in d:
        return d[e]
    m = re.fullmatch(r"v\(([^,()]+),([^,()]+)\)", e)
    if m:
        a, b = (0.0 if n == "0" else _vector(f"v({n})", plot) for n in m.groups())
        return np.asarray(a) - np.asarray(b)
    if e == "v(0)":
        return np.zeros(len(next(iter(d.values()))))
    m = re.fullmatch(r"i\(([^()]+)\)", e)
    if m and f"{m.group(1)}#branch" in d:
        return d[f"{m.group(1)}#branch"]
    raise KeyError(f"{expr}: no such vector in the {plot['plot']} (have {', '.join(plot['names'][:12])})")


def _measure(op: str, x, y) -> float:
    import numpy as np
    y = np.asarray(y)
    if np.iscomplexobj(y):
        y = np.abs(y)                     # a complex AC vector is measured on its magnitude
    x = np.asarray(x, dtype=float).real if x is not None else np.arange(len(y), dtype=float)
    o = op.strip().lower()
    win = re.fullmatch(r"(max|min|avg|rms|pp)@(.+):(.+)", o)
    if win:
        a, b = _num(win.group(2)), _num(win.group(3))
        keep = (x >= min(a, b)) & (x <= max(a, b))
        if keep.sum() < 2:
            raise ValueError(f"{op}: fewer than 2 points in the window")
        x, y, o = x[keep], y[keep], win.group(1)
    if o == "value":
        if len(y) != 1:
            raise ValueError("'value' is for a single-point result (.op); use final/at=")
        return float(y[0])
    if o in ("max", "min"):
        return float(y.max() if o == "max" else y.min())
    if o == "pp":
        return float(y.max() - y.min())
    if o in ("final", "initial"):
        return float(y[-1] if o == "final" else y[0])
    if o in ("argmax", "argmin"):
        return float(x[int(y.argmax() if o == "argmax" else y.argmin())])
    if o in ("avg", "rms"):
        span = x[-1] - x[0]
        if span == 0:
            raise ValueError(f"{op}: zero-length sweep")
        if o == "avg":
            return float(np.trapezoid(y, x) / span)
        return float(math.sqrt(np.trapezoid(y * y, x) / span))
    m = re.fullmatch(r"at=(.+)", o)
    if m:
        xv = _num(m.group(1))
        order = np.argsort(x)
        if not (x[order][0] <= xv <= x[order][-1]):
            raise ValueError(f"{op}: outside the sweep {x.min():g}..{x.max():g}")
        return float(np.interp(xv, x[order], y[order]))
    m = re.fullmatch(r"cross=(.+)", o)
    if m:
        lv = _num(m.group(1))
        d = y - lv
        idx = np.nonzero(np.signbit(d[:-1]) != np.signbit(d[1:]))[0]
        if not len(idx):
            raise ValueError(f"{op}: never crosses {lv:g}")
        k = int(idx[0])
        return float(x[k] + (x[k + 1] - x[k]) * (lv - y[k]) / (y[k + 1] - y[k]))
    raise ValueError(f"measure op {op!r}: max, min, pp, avg, rms (optional @from:to), final, "
                     f"initial, argmax, argmin, at=X, cross=L, value")


def _spice(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    netlist = inp.get("netlist")
    if not isinstance(netlist, str) or not netlist.strip():
        raise ValueError("spice: netlist (text of the circuit, no title line needed) is required")
    parts = _sanitize(netlist, work, work)
    extra = inp.get("analysis") or []
    extra = [extra] if isinstance(extra, str) else list(extra)
    for a in extra:
        p = _sanitize(a if a.strip().startswith(".") else "." + a.strip(), work, work)
        parts["analyses"] += p["analyses"]
    for m in inp.get("meas") or []:
        p = _sanitize(m if m.strip().startswith(".") else ".meas " + m.strip(), work, work)
        parts["meas"] += p["meas"]
    if not parts["analyses"]:
        raise ValueError("spice: no analysis (.op/.ac/.dc/.tran...) in netlist or 'analysis'")
    if len(parts["analyses"]) > 8:
        raise ValueError("spice: at most 8 analyses per study")
    prints = [str(p).lower() for p in inp.get("print") or []]
    for p in prints:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", p):
            raise ValueError(f"print: {p!r} is not a vector name")
    measures = inp.get("measure") or {}
    timeout = float(inp.get("timeout_s", 240))
    ng = exe("ngspice_con")
    values: Dict[str, Any] = {}
    per_key: Dict[str, List[Tuple[str, float]]] = {}
    notes: List[str] = []
    files: List[str] = []
    errors: List[str] = []
    vmax_dc, vmax_ac = 0.0, 0.0
    for n, an in enumerate(parts["analyses"]):
        atype = an.split()[0].lower()[1:]
        meas = [m for m in parts["meas"] if len(m.split()) > 2 and m.split()[1].lower() == atype]
        names = []
        for m in meas:
            nm = m.split()[2].lower()
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", nm):
                raise ValueError(f".meas name {nm!r}: letters, digits, _")
            names.append(nm)
        raw = f"out_{n}.raw"
        deck = [f"* mcpfc spice {atype}", *parts["body"], an,
                ".control", "set filetype=ascii", "set numdgt=15", "run", f"write {raw}",
                *parts["control"], *["meas " + m.split(None, 1)[1] for m in meas],
                "echo MCPFC_BEGIN", *[f"print {x}" for x in names + prints], "echo MCPFC_END",
                "quit 0", ".endc", ".end"]
        cir = work / f"circuit_{n}.cir"
        cir.write_text("\n".join(deck) + "\n", encoding="utf-8")
        log = work / f"ngspice_{n}.log"
        rp = work / raw
        rp.unlink(missing_ok=True)
        proc = run_exe([ng, "-b", "-n", "-o", log.name, cir.name], work, timeout)
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        files += [cir.name, log.name]
        errors += [ln.strip() for ln in text.splitlines()
                   if re.match(r"\s*(error|.*failed!)", ln, re.I)][:6]
        if not rp.is_file():
            tail = "; ".join(errors) or (text + proc.stdout + proc.stderr)[-400:]
            raise RuntimeError(f"ngspice produced no result for {an!r} (code {proc.returncode}): {tail}")
        if rp.stat().st_size > 300 * 2**20:
            raise RuntimeError(f"{raw} > 300 MB: add .save <vectors> or a coarser step")
        files.append(raw)
        plots = _read_raw(rp)
        if not plots:
            raise RuntimeError(f"{raw}: no plot in the rawfile")
        plot = plots[0]
        block = text.split("MCPFC_BEGIN", 1)[-1].split("MCPFC_END", 1)[0]
        for nm in names + prints:
            mm = re.search(rf"^\s*{re.escape(nm)}\s*=\s*([-+0-9.eE]+)", block, re.M)
            per_key.setdefault(nm, []).append((atype, float(mm.group(1)) if mm else None))
            if not mm:
                notes.append(f"{nm}: not computed (see {log.name})")
        sweep = plot["names"][0]
        x = plot["data"][sweep] if len(plot["data"][sweep]) > 1 else None
        if atype == "op":
            for nm in plot["names"]:
                per_key.setdefault(nm, []).append((atype, float(plot["data"][nm][0].real)))
        for key, ops in measures.items():
            restrict, expr = (key.split(":", 1) if re.match(r"(op|ac|dc|tran|noise):", key)
                              else (None, key))
            if restrict and restrict != atype:
                continue
            for op in [ops] if isinstance(ops, str) else list(ops):
                try:
                    v = _measure(op, x, _vector(expr, plot))
                except (KeyError, ValueError) as e:
                    if restrict or len(parts["analyses"]) == 1:
                        notes.append(f"{expr}:{op}: {e}")
                    v = None
                if v is not None or restrict or len(parts["analyses"]) == 1:
                    per_key.setdefault(f"{expr.lower()}:{op}", []).append((atype, v))
        if atype in ("op", "tran", "dc"):
            for nm, typ in zip(plot["names"], plot["types"]):
                if typ == "voltage":
                    y = np.abs(np.asarray(plot["data"][nm]).real)
                    vmax_dc = max(vmax_dc, float(y.max()))
                    yy = np.asarray(plot["data"][nm]).real
                    if atype == "tran" and yy.min() < -0.5 * yy.max():   # alternating: rms of the swing
                        vmax_ac = max(vmax_ac, float(np.abs(yy).max()) / math.sqrt(2))
    for key, got in per_key.items():
        if len(got) == 1 or len({a for a, _ in got}) == 1:
            values[key] = got[-1][1]
        else:
            for a, v in got:
                values[f"{a}.{key}"] = v
    values["max_abs_node_V"] = vmax_dc
    qualified, snotes = _safety_voltage(vmax_dc if vmax_ac == 0 else 0.0, vmax_ac)
    if vmax_ac and vmax_dc > 120:
        qualified = True
    notes = snotes + notes
    if errors:
        notes.append("ngspice: " + " | ".join(dict.fromkeys(errors))[:300])
    shown = [f"{k} = {v:.6g}" for k, v in values.items()
             if isinstance(v, (int, float)) and k != "max_abs_node_V"][:10]
    head = (f"spice {', '.join(a.split()[0] for a in parts['analyses'])}: " + (", ".join(shown) or "ran"))
    return _finish(values, inp.get("limits"), head, files, notes, qualified)


# ================================================================== battery (PyBaMM)
def _battery(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    os.environ.setdefault("PYBAMM_DISABLE_TELEMETRY", "true")
    import pybamm
    pset = str(inp.get("parameter_set", "Chen2020"))
    if pset not in pybamm.parameter_sets:
        raise ValueError(f"parameter_set {pset!r}: one of {sorted(pybamm.parameter_sets)}")
    models = inp.get("compare") or [inp.get("model", "SPMe")]
    thermal = str(inp.get("thermal", "lumped"))
    if thermal not in ("lumped", "isothermal"):
        raise ValueError("thermal: lumped or isothermal")
    values: Dict[str, Any] = {}
    notes: List[str] = []
    caps = []
    for k, name in enumerate(models):
        if name not in ("SPM", "SPMe", "DFN"):
            raise ValueError(f"model {name!r}: SPM, SPMe or DFN")
        pv = pybamm.ParameterValues(pset)
        if inp.get("ambient_C") is not None:
            t = float(inp["ambient_C"]) + 273.15
            pv.update({"Ambient temperature [K]": t, "Initial temperature [K]": t})
        cutoff = float(inp.get("cutoff_V", pv["Lower voltage cut-off [V]"]))
        nominal = float(pv["Nominal cell capacity [A.h]"])
        if inp.get("current_A") is not None:
            cur = float(inp["current_A"])
            step = f"Discharge at {cur:g} A until {cutoff:g} V"
            c_rate = cur / nominal
        else:
            c_rate = float(inp.get("c_rate", 1.0))
            step = f"Discharge at {c_rate:g}C until {cutoff:g} V"
        if not 0.01 <= c_rate <= 10:
            raise ValueError(f"C-rate {c_rate:g} outside 0.01..10 (runtime and model validity)")
        period = max(5.0, 3600.0 / c_rate / 1500)
        model = getattr(pybamm.lithium_ion, name)(
            options={"thermal": thermal, "calculate discharge energy": "true"})
        sim = pybamm.Simulation(model, parameter_values=pv,
                                experiment=pybamm.Experiment([step], period=f"{period:.0f} seconds"))
        sol = sim.solve(initial_soc=float(inp.get("initial_soc", 1.0)))
        v = sol["Voltage [V]"].entries
        if v[-1] > cutoff + 0.05:
            notes.append(f"{name}: stopped at {v[-1]:.3f} V before the cut-off (another limit hit)")
        cap = float(sol["Discharge capacity [A.h]"].entries[-1])
        en = float(sol["Discharge energy [W.h]"].entries[-1])
        temp = sol["Volume-averaged cell temperature [K]"].entries - 273.15
        tag = "" if len(models) == 1 else f"_{name}"
        values[f"capacity_Ah{tag}"] = cap
        values[f"energy_Wh{tag}"] = en
        values[f"duration_h{tag}"] = float(sol["Time [s]"].entries[-1] / 3600)
        values[f"mean_V{tag}"] = en / cap if cap else None
        if thermal == "lumped":
            values[f"final_T_C{tag}"] = float(temp[-1])
            values[f"max_T_C{tag}"] = float(np.max(temp))
        if k == 0:
            values.update(capacity_Ah=cap, energy_Wh=en, nominal_capacity_Ah=nominal,
                          capacity_vs_nominal=cap / nominal, c_rate=c_rate, cutoff_V=cutoff)
            if thermal == "lumped":
                values.update(final_T_C=float(temp[-1]), max_T_C=float(np.max(temp)))
        caps.append(cap)
    if len(caps) > 1:
        values["capacity_rel_diff"] = (max(caps) - min(caps)) / (sum(caps) / len(caps))
    head = (f"battery {pset} {'/'.join(models)} {thermal}: {values['c_rate']:g}C to "
            f"{values['cutoff_V']:g} V -> {values['capacity_Ah']:.4g} Ah "
            f"({values['capacity_vs_nominal'] * 100:.1f} % of nominal {values['nominal_capacity_Ah']:g} Ah), "
            f"{values['energy_Wh']:.4g} Wh"
            + (f", T end {values['final_T_C']:.1f} degC" if "final_T_C" in values else ""))
    if "capacity_rel_diff" in values:
        head += f"; models differ by {values['capacity_rel_diff'] * 100:.2f} %"
    notes.append("model of one cell from published parameters, not a datasheet: limits (max current, "
                 "temperature) come from the cell's datasheet; a lithium pack needs a BMS and a fuse "
                 "near the battery, transport per UN38.3")
    return _finish(values, inp.get("limits"), head, [], notes)


# ================================================================== magnet (Magpylib)
def _rotation(spec: Dict[str, Any]):
    import numpy as np
    from scipy.spatial.transform import Rotation
    if spec.get("rotvec_deg") is not None:
        return Rotation.from_rotvec(np.radians(np.asarray(spec["rotvec_deg"], dtype=float)))
    a = np.asarray(spec.get("axis", [0, 0, 1]), dtype=float)
    a = a / np.linalg.norm(a)
    z = np.array([0.0, 0.0, 1.0])
    c = np.cross(z, a)
    s = np.linalg.norm(c)
    if s < 1e-12:
        return Rotation.identity() if a[2] > 0 else Rotation.from_rotvec([math.pi, 0, 0])
    return Rotation.from_rotvec(c / s * math.atan2(s, float(np.dot(z, a))))


def _polarization(spec: Dict[str, Any]):
    if spec.get("polarization_T") is not None:
        return tuple(float(x) for x in spec["polarization_T"])
    if spec.get("Br_T") is not None:
        return (0.0, 0.0, float(spec["Br_T"]))       # along the magnet's local z (its axis)
    raise ValueError(f"magnet {spec.get('name', '')}: give polarization_T [x,y,z] or Br_T "
                     f"(datasheet remanence); no default grade is assumed")


def _diameter(spec: Dict[str, Any]) -> float:
    if spec.get("diameter_m") is not None:
        return float(spec["diameter_m"])
    if spec.get("radius_m") is not None:
        return 2 * float(spec["radius_m"])
    raise ValueError(f"{spec.get('type')}: diameter_m or radius_m")


def _magnet_obj(spec: Dict[str, Any]):
    import numpy as np
    import magpylib as magpy
    t = str(spec.get("type", "")).lower()
    pos = np.asarray(spec.get("position_m", [0, 0, 0]), dtype=float)
    rot = _rotation(spec)
    if t == "loop":
        return magpy.current.Circle(current=float(spec["current_A"]), diameter=_diameter(spec),
                                    position=pos, orientation=rot), 2 * _diameter(spec)
    if t == "solenoid":
        n, L = int(spec["turns"]), float(spec["length_m"])
        if not 1 <= n <= 5000:
            raise ValueError("solenoid turns: 1..5000")
        loops = [magpy.current.Circle(current=float(spec["current_A"]), diameter=_diameter(spec),
                                      position=pos + rot.apply([0, 0, -L / 2 + (k + 0.5) * L / n]),
                                      orientation=rot) for k in range(n)]
        return magpy.Collection(*loops), max(L, _diameter(spec))
    if t == "cuboid":
        dim = [float(x) for x in spec["dimension_m"]]
        return magpy.magnet.Cuboid(polarization=_polarization(spec), dimension=dim, position=pos,
                                   orientation=rot), max(dim)
    if t == "cylinder":
        d, h = _diameter(spec), float(spec["height_m"])
        return magpy.magnet.Cylinder(polarization=_polarization(spec), dimension=(d, h), position=pos,
                                     orientation=rot), max(d, h)
    if t == "sphere":
        d = _diameter(spec)
        return magpy.magnet.Sphere(polarization=_polarization(spec), diameter=d, position=pos,
                                   orientation=rot), d
    raise ValueError(f"source type {t!r}: loop, solenoid, cuboid, cylinder, sphere")


def _magnet(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    import magpylib as magpy
    specs = inp.get("sources") or []
    if not specs:
        raise ValueError("magnet: sources [{type: loop|solenoid|cuboid|cylinder|sphere, ...}]")
    objs: Dict[str, Any] = {}
    size: Dict[str, float] = {}
    for i, s in enumerate(specs):
        name = str(s.get("name", f"s{i}"))
        if not re.fullmatch(r"[A-Za-z0-9_]{1,30}", name) or name in objs:
            raise ValueError(f"source name {name!r}: unique, letters/digits/_")
        objs[name], size[name] = _magnet_obj(s)
    values: Dict[str, Any] = {}
    pts = np.asarray(inp.get("points_m") or [], dtype=float).reshape(-1, 3)
    if len(pts) > 100_000:
        raise ValueError("at most 100000 points")
    head = [f"magnet: {len(objs)} source(s)"]
    if len(pts):
        B = np.atleast_2d(magpy.getB(list(objs.values()), pts, sumup=True)).reshape(-1, 3)
        mag = np.linalg.norm(B, axis=1)
        values["B_T"] = B.tolist()
        values["B_max_T"] = float(np.nanmax(mag))
        for k in range(min(len(pts), 50)):
            for d, ax in enumerate("xyz"):
                values[f"B{ax}_T_{k}"] = float(B[k, d])
            values[f"B_T_{k}"] = float(mag[k])
        head.append(f"|B| at {len(pts)} point(s): " + ", ".join(f"{m:.4g}" for m in mag[:6]) + " T")
    notes: List[str] = []
    for tname in inp.get("force_on") or []:
        if tname not in objs:
            raise ValueError(f"force_on {tname!r}: not a source name ({', '.join(objs)})")
        others = [o for n, o in objs.items() if n != tname]
        if not others:
            raise ValueError("force_on needs at least one other source")
        tgt = objs[tname]
        mesh = inp.get("meshing")
        if mesh is None:
            mesh = 128 if isinstance(tgt, (magpy.current.Circle, magpy.Collection)) else 2000
        F, T = magpy.getFT(others, tgt, meshing=mesh, eps=1e-5 * size[tname])
        F, T = np.asarray(F, dtype=float).reshape(3), np.asarray(T, dtype=float).reshape(3)
        for d, ax in enumerate("xyz"):
            values[f"F{ax}_N_{tname}"] = float(F[d])
            values[f"T{ax}_Nm_{tname}"] = float(T[d])
        values[f"F_N_{tname}"] = float(np.linalg.norm(F))
        values[f"T_Nm_{tname}"] = float(np.linalg.norm(T))
        head.append(f"force on {tname} [{F[0]:.4g}, {F[1]:.4g}, {F[2]:.4g}] N, torque "
                    f"|{np.linalg.norm(T):.3g}| N m")
    if inp.get("force_on"):
        notes.append("force/torque by meshing the target (Magpylib getFT): pass a larger 'meshing' "
                     "and check the change to judge convergence")
    if not len(pts) and not inp.get("force_on"):
        raise ValueError("magnet: give points_m and/or force_on")
    notes.append("ideal magnets (linear, rigid polarization; no soft iron, no demagnetisation)")
    return _finish(values, inp.get("limits"), "; ".join(head), [], notes)


# ================================================================== wire_sizing
# Resistivity at 20 degC and temperature coefficient:
#   copper: IEC 60028 (International Annealed Copper Standard): 1/58 ohm mm^2/m, alpha 0.00393 /K
#   aluminium: IEC 60889 (hard-drawn Al for conductors): 0.028264 ohm mm^2/m, alpha 0.00403 /K
MATERIALS = {"copper": (1.0 / 58.0, 0.00393), "aluminium": (0.028264, 0.00403)}
STANDARD_MM2 = [0.5, 0.75, 1.0, 1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240]
# IEC 60228:2004 maximum DC resistance at 20 degC, plain annealed copper, ohm/km
IEC60228 = {
    "class2": {1.5: 12.1, 2.5: 7.41, 4: 4.61, 6: 3.08, 10: 1.83, 16: 1.15, 25: 0.727, 35: 0.524,
               50: 0.387, 70: 0.268, 95: 0.193, 120: 0.153, 150: 0.124, 185: 0.0991, 240: 0.0754},
    "class5": {0.5: 39.0, 0.75: 26.0, 1.0: 19.5, 1.5: 13.3, 2.5: 7.98, 4: 4.95, 6: 3.30, 10: 1.91,
               16: 1.21, 25: 0.780, 35: 0.554, 50: 0.386, 70: 0.272, 95: 0.206, 120: 0.161,
               150: 0.129, 185: 0.106, 240: 0.0801},
}
# IEC 60364-5-52:2009 Table B.52.2: current-carrying capacity (A), PVC insulation (70 degC conductor),
# two loaded conductors, copper, ambient 30 degC in air; installation reference methods A1, B1, C.
# (transcribed as reproduced in the Schneider Electric Electrical Installation Guide, chapter G)
IEC60364_B52_2 = {
    "A1": {1.5: 14.5, 2.5: 19.5, 4: 26, 6: 34, 10: 46, 16: 61, 25: 80, 35: 99},
    "B1": {1.5: 17.5, 2.5: 24, 4: 32, 6: 41, 10: 57, 16: 76, 25: 101, 35: 125},
    "C": {1.5: 19.5, 2.5: 27, 4: 36, 6: 46, 10: 63, 16: 85, 25: 112, 35: 138},
}
# IEC 60364-5-52 Table B.52.14, PVC, ambient air above 30 degC (no credit taken below 30 degC)
IEC60364_B52_14_PVC = {30: 1.00, 35: 0.94, 40: 0.87, 45: 0.79, 50: 0.71, 55: 0.61, 60: 0.50}


def awg_area_mm2(n: int) -> float:
    """ASTM B258: d = 0.127 mm x 92^((36 - n) / 39); 0000 = -3."""
    d = 0.127 * 92 ** ((36 - n) / 39)
    return math.pi / 4 * d * d


def _ampacity(area: float, inst: Dict[str, Any], material: str) -> Tuple[Optional[float], str]:
    std = str(inst.get("standard", "IEC 60364-5-52")).replace(" ", "").upper()
    method = str(inst.get("method", "")).upper()
    ins = str(inst.get("insulation", "PVC")).upper()
    loaded = int(inst.get("loaded_conductors", 2))
    amb = float(inst.get("ambient_C", 30))
    if std != "IEC60364-5-52" or ins != "PVC" or loaded != 2 or material != "copper" \
            or method not in IEC60364_B52_2:
        return None, ("ampacity UNKNOWN: only IEC 60364-5-52 Table B.52.2 (copper, PVC, 2 loaded "
                      "conductors, methods A1/B1/C, 1.5..35 mm^2) is tabulated here; pass ampacity_A "
                      "from the cable datasheet")
    table = IEC60364_B52_2[method]
    size = next((s for s in sorted(table) if abs(s - area) < 1e-6), None)
    if size is None:
        return None, f"ampacity UNKNOWN: {area:g} mm^2 is not a tabulated size ({sorted(table)})"
    if amb > 60:
        return None, "ampacity UNKNOWN: ambient above 60 degC is outside Table B.52.14 for PVC"
    k = next(v for a, v in sorted(IEC60364_B52_14_PVC.items()) if a >= amb) if amb > 30 else 1.0
    grp = float(inst.get("grouping_factor", 1.0))
    src = (f"IEC 60364-5-52 Table B.52.2 method {method}: {table[size]:g} A x ambient {k:g}"
           + (f" x grouping {grp:g} (given)" if grp != 1 else " (single circuit, no grouping factor)"))
    return table[size] * k * grp, src


def _wire_sizing(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    material = str(inp.get("material", "copper")).lower().replace("aluminum", "aluminium")
    if material not in MATERIALS:
        raise ValueError(f"material: {sorted(MATERIALS)}")
    rho20, alpha = MATERIALS[material]
    T = float(inp.get("temperature_C", 20.0))
    length = float(inp["length_m"])
    round_trip = bool(inp.get("round_trip", True))
    L = length * (2 if round_trip else 1)
    I = float(inp.get("current_A", 0.0))
    V = inp.get("voltage_V")
    system = str(inp.get("system", "dc")).lower()
    if system not in ("dc", "ac"):
        raise ValueError("system: dc or ac (single-phase, resistive drop only)")
    conductor = str(inp.get("conductor", "ideal")).lower()
    notes: List[str] = []
    values: Dict[str, Any] = {"length_conductor_m": L}
    rho_T = rho20 * (1 + alpha * (T - 20.0))
    area = None
    if inp.get("awg") is not None:
        area = awg_area_mm2(int(inp["awg"]))
        values["awg"] = int(inp["awg"])
    elif inp.get("area_mm2") is not None:
        area = float(inp["area_mm2"])
    src = (f"{material} rho20 {rho20:.6g} ohm mm^2/m, alpha {alpha} /K "
           f"({'IEC 60028' if material == 'copper' else 'IEC 60889'}), at {T:g} degC")
    if area is not None:
        values["area_mm2"] = area
        if conductor in ("iec60228_class2", "iec60228_class5"):
            if material != "copper":
                raise ValueError("IEC 60228 tables here are for plain copper")
            tab = IEC60228[conductor.split("_")[1]]
            size = next((s for s in tab if abs(s - area) < 1e-6), None)
            if size is None:
                raise ValueError(f"{conductor}: {area:g} mm^2 not tabulated ({sorted(tab)})")
            r_m = tab[size] / 1000 * (1 + alpha * (T - 20.0))
            src = f"IEC 60228 {conductor[-6:]} max {tab[size]:g} ohm/km at 20 degC, alpha {alpha}, at {T:g} degC"
        elif conductor == "ideal":
            r_m = rho_T / area
        else:
            raise ValueError("conductor: ideal, iec60228_class2, iec60228_class5")
        R = r_m * L
        values.update(R_ohm_per_m=r_m, R_ohm=R, drop_V=I * R, loss_W=I * I * R)
        if V:
            values["drop_pct"] = 100 * I * R / float(V)
    limits = dict(inp.get("limits") or {})
    head = [f"wire {material} {L:g} m conductor, {I:g} A"]
    if area is not None:
        head.append(f"{area:.4g} mm^2: R {values['R_ohm']:.4g} ohm, drop {values['drop_V']:.4g} V"
                    + (f" ({values['drop_pct']:.3g} %)" if "drop_pct" in values else "")
                    + f", loss {values['loss_W']:.4g} W")
    if inp.get("max_drop_pct") is not None:
        if not V:
            raise ValueError("max_drop_pct needs voltage_V")
        pct = float(inp["max_drop_pct"])
        a_min = rho_T * L * I / (float(V) * pct / 100)
        values["min_area_mm2"] = a_min
        values["suggested_area_mm2"] = next((s for s in STANDARD_MM2 if s >= a_min - 1e-12), None)
        awg = next((n for n in range(40, -4, -1) if awg_area_mm2(n) >= a_min), None)
        values["suggested_awg"] = awg
        head.append(f"for <= {pct:g} % drop: >= {a_min:.3g} mm^2 -> {values['suggested_area_mm2']} mm^2"
                    + (f" / AWG {awg}" if awg is not None else "") + " (ideal resistivity)")
        if area is not None:
            limits.setdefault("drop_pct", {"max": pct})
    # ampacity: only a sourced figure
    amp, amp_src = None, ""
    if inp.get("ampacity_A") is not None:
        amp, amp_src = float(inp["ampacity_A"]), str(inp.get("ampacity_source", "given by the caller"))
    elif inp.get("installation") and area is not None:
        amp, amp_src = _ampacity(area, inp["installation"], material)
    else:
        amp_src = "ampacity UNKNOWN: no sourced table for this case (pass ampacity_A from the datasheet " \
                  "or installation={method: A1|B1|C, ...} for IEC 60364-5-52)"
    values["ampacity_A"] = amp
    notes.append(amp_src if amp is None else f"ampacity {amp:.4g} A from {amp_src}")
    fuse = inp.get("fuse_A")
    if fuse is not None or amp is not None or inp.get("require_ampacity"):
        if I:
            values["derated_current_ratio"] = (1.25 * I / amp) if amp else None   # §10.2: >= 25 % margin
            limits.setdefault("derated_current_ratio", {"max": 1.0})
        if fuse is not None:
            values["fuse_A"] = float(fuse)
            values["fuse_to_ampacity"] = (float(fuse) / amp) if amp else None   # IEC 60364-4-43: In <= Iz
            limits.setdefault("fuse_to_ampacity", {"max": 1.0})
            if I:
                values["current_to_fuse"] = I / float(fuse)                      # Ib <= In
                limits.setdefault("current_to_fuse", {"max": 1.0})
    if "temperature_C" not in inp:
        notes.append("conductor at 20 degC: a loaded wire runs hotter (PVC up to 70 degC), pass temperature_C")
    if system == "ac":
        notes.append("AC: resistive drop only (cos phi = 1, no reactance, single-phase)")
    v = float(V or 0)
    qualified, snotes = _safety_voltage(v if system == "dc" else 0.0, v if system == "ac" else 0.0)
    values["qualified_person_required"] = int(qualified)
    if area is not None:
        notes.insert(0, src)
    return _finish(values, limits, "; ".join(head), [], snotes + notes, qualified)


# ================================================================== power_budget
def _power_budget(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    bat = inp.get("battery") or {}
    cap = float(bat["capacity_Ah"])
    vb = float(bat["voltage_V"])
    vmin = float(bat.get("voltage_min_V", vb))
    usable = float(bat.get("usable_fraction", 1.0))
    notes: List[str] = []
    if "usable_fraction" not in bat:
        notes.append("usable_fraction not given: 100 % of rated capacity assumed (no ageing/DoD margin)")
    if "voltage_min_V" not in bat:
        notes.append("voltage_min_V not given: peak current computed at the nominal voltage")
    values: Dict[str, Any] = {}
    p_mean = p_peak = p_rms = 0.0
    rows = []
    for r in inp.get("rails") or []:
        rv = float(r.get("voltage_V", vb))
        eff = r.get("efficiency")
        if eff is None:
            eff = 1.0
            if abs(rv - vb) > 1e-9:
                notes.append(f"rail {r.get('name', rv)}: converter efficiency not given, 100 % assumed (optimistic)")
        eff = float(eff)
        if not 0 < eff <= 1:
            raise ValueError("efficiency: 0 < eff <= 1")
        i_mean = i_peak = i_rms = 0.0
        for ld in r.get("loads") or []:
            a = float(ld["avg_A"])
            duty = float(ld.get("duty", 1.0))
            if not 0 <= duty <= 1:
                raise ValueError("duty: 0..1")
            i_mean += a * duty
            i_peak += float(ld.get("peak_A", a))
            i_rms += float(ld.get("rms_A", a * math.sqrt(duty)))
        name = str(r.get("name", f"{rv:g}V"))
        values[f"rail_{name}_mean_A"] = i_mean
        values[f"rail_{name}_peak_A"] = i_peak
        rows.append(f"{name} {i_mean:.3g}/{i_peak:.3g} A")
        p_mean += rv * i_mean / eff
        p_peak += rv * i_peak / eff
        p_rms += rv * i_rms / eff
    if not rows:
        raise ValueError("power_budget: rails [{name, voltage_V, efficiency, loads: [{avg_A, peak_A, duty}]}]")
    ib_mean, ib_peak, ib_rms = p_mean / vb, p_peak / vmin, p_rms / vb
    values.update(battery_power_mean_W=p_mean, battery_mean_A=ib_mean, battery_peak_A=ib_peak,
                  battery_rms_bound_A=ib_rms, c_rate_mean=ib_mean / cap, c_rate_peak=ib_peak / cap,
                  c_rate_rms_bound=ib_rms / cap,
                  autonomy_h=(cap * usable / ib_mean) if ib_mean > 0 else float("inf"))
    limits = dict(inp.get("limits") or {})
    cont, peak = bat.get("max_continuous_C"), bat.get("max_peak_C")
    values["c_rate_cont_ratio"] = values["c_rate_rms_bound"] / float(cont) if cont else None
    limits.setdefault("c_rate_cont_ratio", {"max": 1.0})
    ref_peak = peak if peak else cont
    values["c_rate_peak_ratio"] = values["c_rate_peak"] / float(ref_peak) if ref_peak else None
    limits.setdefault("c_rate_peak_ratio", {"max": 1.0})
    if not cont:
        notes.append("max_continuous_C not given: C-rate check UNKNOWN (take it from the cell datasheet)")
    elif not peak:
        notes.append("max_peak_C not given: peak compared with the continuous rating")
    if inp.get("required_autonomy_h") is not None:
        req = float(inp["required_autonomy_h"])
        values["autonomy_margin"] = values["autonomy_h"] / req - 1
        limits.setdefault("autonomy_h", {"min": req})
    qualified, snotes = _safety_voltage(max(vb, *(float(r.get("voltage_V", vb)) for r in inp["rails"])))
    if vb > 24:
        snotes.append("above about 24 V: precharge circuit and reverse-polarity protection")
    snotes.append("lithium pack: BMS (cell voltage, current, temperature) and a fuse near the battery")
    head = (f"power budget {vb:g} V {cap:g} Ah: mean {ib_mean:.4g} A, peak {ib_peak:.4g} A, rms <= "
            f"{ib_rms:.4g} A, C-rate mean {values['c_rate_mean']:.3g} / peak {values['c_rate_peak']:.3g}, "
            f"autonomy {values['autonomy_h']:.4g} h; rails " + ", ".join(rows))
    notes.append("rms is an upper bound (sum of per-load rms); peak assumes all peaks coincide")
    return _finish(values, limits, head, [], snotes + notes, qualified)


# ================================================================== reference cases (§9)
# RC low-pass: |H| = 1/sqrt(1 + (w R C)^2): -3.0103 dB (= 1/sqrt 2) and -45 deg at f = 1/(2 pi R C).
_FC = 1 / (2 * math.pi * 1e3 * 100e-9)
# Two coaxial circular loops (radii a, b, gap z): exact force with complete elliptic integrals,
# F = mu0 I1 I2 z / sqrt((a+b)^2 + z^2) * [ (a^2+b^2+z^2)/((a-b)^2+z^2) E(k) - K(k) ],
# k^2 = 4ab/((a+b)^2+z^2) (Grover, Inductance Calculations, 1946; Babic & Akyel), attractive for
# parallel currents. scipy.special.ellipk/ellipe take m = k^2.


def _loop_force(a, b, z, i1, i2):
    from scipy.special import ellipe, ellipk
    m = 4 * a * b / ((a + b) ** 2 + z * z)
    return MU0 * i1 * i2 * z / math.sqrt((a + b) ** 2 + z * z) * (
        (a * a + b * b + z * z) / ((a - b) ** 2 + z * z) * ellipe(m) - ellipk(m))


try:
    _LOOP_F = _loop_force(0.02, 0.015, 0.01, 10.0, 5.0)
except Exception:  # noqa: BLE001 — scipy missing: the reference case then fails, visibly
    _LOOP_F = float("nan")
_J, _D, _H, _Z = 1.2, 0.01, 0.005, 0.003     # cylinder magnet: J (T), diameter, height, gap (m)

REFERENCES = [
    {   # .ac of an RC low-pass: f(-3 dB) = 1/(2 pi R C) = 1591.549 Hz, phase -45 deg there
        "name": "spice_rc_lowpass_ac", "kind": "spice",
        "inp": {"netlist": "V1 in 0 DC 0 AC 1\nR1 in out 1k\nC1 out 0 100n\n",
                "analysis": ".ac dec 200 1 1meg",
                "meas": [".meas ac f3db WHEN vdb(out)=-3.010299957"],
                "measure": {"vdb(out)": "cross=-3.010299957", "phase_deg(v(out))": f"at={_FC:.9g}"}},
        "expect": {"f3db": [_FC, 1e-3], "vdb(out):cross=-3.010299957": [_FC, 1e-3],
                   f"phase_deg(v(out)):at={_FC:.9g}": [-45.0, 2e-3]},
    },
    {   # resistive divider .op: Vout = 12 x 4.7k / (10k + 4.7k); source current -12/14.7k
        "name": "spice_divider_op", "kind": "spice",
        "inp": {"netlist": "V1 in 0 DC 12\nR1 in out 10k\nR2 out 0 4.7k\n.op\n"},
        "expect": {"v(out)": [12 * 4.7 / 14.7, 1e-6], "i(v1)": [-12 / 14.7e3, 1e-6]},
    },
    {   # RC step response: v(tau) = 1 - 1/e = 0.63212 with tau = R C = 1 ms
        "name": "spice_rc_step_tau", "kind": "spice",
        "inp": {"netlist": "V1 in 0 DC 1\nR1 in out 1k\nC1 out 0 1u IC=0\n.tran 1u 5m 0 1u uic\n",
                "meas": ["tran vtau FIND v(out) AT=1m"], "measure": {"v(out)": ["at=1m", "final"]}},
        "expect": {"vtau": [1 - math.exp(-1), 2e-3], "v(out):at=1m": [1 - math.exp(-1), 2e-3],
                   "v(out):final": [1 - math.exp(-5), 2e-3]},
    },
    {   # ideal buck (square wave Vin at duty D into lossless LC + R): mean Vout = D Vin = 6 V
        "name": "spice_buck_ideal_average", "kind": "spice",
        "inp": {"netlist": ("Vsw sw 0 PULSE(0 12 0 10n 10n 4.99u 10u)\nL1 sw out 100u\n"
                            "C1 out 0 100u\nRload out 0 2\n.save v(out)\n"
                            ".tran 50n 10m 0 50n uic\n"),
                "meas": ["tran vavg AVG v(out) FROM=8m TO=10m"]},
        "expect": {"vavg": [6.0, 5e-3]},
    },
    {   # PyBaMM SPM and DFN agree at C/10 (both reduce to the same equilibrium limit) and give
        # about the nominal 5.0 Ah of the LG M50 cell (Chen et al., J. Electrochem. Soc. 167 (2020)
        # 080534); nominal is a rated figure, hence 5 %; the model-to-model check is the tight one
        "name": "battery_spm_vs_dfn_c10", "kind": "battery",
        "inp": {"parameter_set": "Chen2020", "compare": ["SPM", "DFN"], "c_rate": 0.1},
        "expect": {"capacity_rel_diff": [0.0, 0.01], "capacity_Ah": [5.0, 0.05],
                   "capacity_Ah_DFN": [5.0, 0.05]},
    },
    {   # finite solenoid (current sheet) centre field: B = mu0 N I / sqrt(L^2 + 4 R^2)
        "name": "magnet_solenoid_centre", "kind": "magnet",
        "inp": {"sources": [{"type": "solenoid", "turns": 200, "length_m": 0.1, "radius_m": 0.02,
                             "current_A": 1.0}], "points_m": [[0, 0, 0]]},
        "expect": {"Bz_T_0": [MU0 * 200 * 1.0 / math.sqrt(0.1 ** 2 + 4 * 0.02 ** 2), 2e-3]},
    },
    {   # axial field of a cylinder magnet at gap z from its face:
        # Bz = J/2 [ (H+z)/sqrt(R^2+(H+z)^2) - z/sqrt(R^2+z^2) ] (e.g. Camacho & Sosa,
        # Rev. Mex. Fis. E 59 (2013) 8)
        "name": "magnet_cylinder_axis", "kind": "magnet",
        "inp": {"sources": [{"type": "cylinder", "diameter_m": _D, "height_m": _H,
                             "polarization_T": [0, 0, _J]}], "points_m": [[0, 0, _H / 2 + _Z]]},
        "expect": {"Bz_T_0": [_J / 2 * ((_H + _Z) / math.sqrt((_D / 2) ** 2 + (_H + _Z) ** 2)
                                        - _Z / math.sqrt((_D / 2) ** 2 + _Z ** 2)), 1e-6]},
    },
    {   # force between two coaxial loops (exact elliptic formula above), target pulled down
        "name": "magnet_coaxial_loops_force", "kind": "magnet",
        "inp": {"sources": [{"name": "src", "type": "loop", "radius_m": 0.02, "current_A": 10.0},
                            {"name": "tgt", "type": "loop", "radius_m": 0.015, "current_A": 5.0,
                             "position_m": [0, 0, 0.01]}],
                "force_on": ["tgt"], "meshing": 256},
        "expect": {"Fz_N_tgt": [-_LOOP_F, 2e-3]},
    },
    {   # copper 2.5 mm^2, 10 m out and back, 10 A, 24 V: R = rho L / A with rho = 1/58 (IEC 60028)
        "name": "wire_drop_copper_20C", "kind": "wire_sizing",
        "inp": {"length_m": 10, "area_mm2": 2.5, "current_A": 10, "voltage_V": 24},
        "expect": {"drop_V": [10 * 20 / 58 / 2.5, 1e-9], "loss_W": [100 * 20 / 58 / 2.5, 1e-9],
                   "drop_pct": [100 * 10 * 20 / 58 / 2.5 / 24, 1e-9]},
    },
    {   # same at 70 degC: R(T) = R20 (1 + 0.00393 x 50); smallest section for 3 % drop at 12 V
        "name": "wire_drop_copper_70C_sizing", "kind": "wire_sizing",
        "inp": {"length_m": 5, "awg": 10, "current_A": 20, "voltage_V": 12, "temperature_C": 70,
                "max_drop_pct": 3},
        "expect": {"area_mm2": [math.pi / 4 * (0.127 * 92 ** (26 / 39)) ** 2, 1e-12],
                   "drop_V": [20 * 10 / 58 * (1 + 0.00393 * 50) / (math.pi / 4 * (0.127 * 92 ** (26 / 39)) ** 2), 1e-9],
                   "min_area_mm2": [10 / 58 * (1 + 0.00393 * 50) * 20 / (12 * 0.03), 1e-9]},
    },
    {   # power budget arithmetic: 12 V rail 1 A at 50 % duty + 5 V rail 2 A through a 90 % converter
        # on a 12 V 5 Ah battery: P = 6 + 11.11 W, I = 1.4259 A, autonomy = 5 / 1.4259 h
        "name": "power_budget_two_rails", "kind": "power_budget",
        "inp": {"battery": {"capacity_Ah": 5, "voltage_V": 12, "max_continuous_C": 2},
                "rails": [{"name": "12V", "voltage_V": 12, "loads": [{"avg_A": 1, "duty": 0.5}]},
                          {"name": "5V", "voltage_V": 5, "efficiency": 0.9, "loads": [{"avg_A": 2}]}]},
        "expect": {"battery_mean_A": [(6 + 10 / 0.9) / 12, 1e-12],
                   "autonomy_h": [5 / ((6 + 10 / 0.9) / 12), 1e-12],
                   "c_rate_mean": [(6 + 10 / 0.9) / 12 / 5, 1e-12]},
    },
]
