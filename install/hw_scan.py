"""Hardware scan, ONLY with the owner's consent: CPU, RAM, GPU, free disk. From it, the install
picks what to download (the CUDA build of the AI environment only for a suitable NVIDIA GPU)
and the default compute profile: cpu, gpu or both (neural networks on the GPU while the physics
runs on the CPU cores).

    python install/hw_scan.py                 # asks first, then scans, prints the plan
    python install/hw_scan.py --yes --home D:/Atelier --write   # consent given on the command line

Privacy: nothing leaves the machine. The scan reads Windows' own counters (memory status, CIM
processor and video controller) and `nvidia-smi` when NVIDIA's driver installed it. The result
is written only to <workshop home>/hardware.json and atelier.json (compute profile), and only
with --write. No serial numbers, no user names, no network.

Without consent (or a non-interactive shell without --yes) nothing is read: the install then
stays on the CPU profile, which works everywhere.
Sentinels: HWSCAN_OK, HWSCAN_DECLINED.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# NVIDIA driver needed by the CUDA 12.8+ PyTorch builds (Blackwell RTX 50xx needs 12.8)
MIN_DRIVER = 570.0
MIN_VRAM_GB = 6.0
QUESTION = ("Analyser la configuration matérielle (processeur, mémoire, carte graphique, disque) "
            "pour choisir quoi télécharger ? Rien ne quitte cette machine. [o/N] ")


def ask(question: str = QUESTION, yes: bool = False, stream=None) -> bool:
    """True only with an explicit yes. A non-interactive shell without --yes is a no."""
    if yes:
        return True
    stream = stream or sys.stdin
    if not (hasattr(stream, "isatty") and stream.isatty()):
        return False
    try:
        return input(question).strip().lower() in ("o", "oui", "y", "yes")
    except EOFError:
        return False


# ------------------------------------------------------------------ probes (replaceable in tests)
def _run(args: List[str], timeout: float = 20) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def probe_ram_gb() -> float:
    if os.name != "nt":
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
        except (ValueError, OSError, AttributeError):
            return 0.0

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullTotalPhys / 2 ** 30


def probe_cpu() -> Dict[str, Any]:
    logical = os.cpu_count() or 1
    name, physical = "", 0
    ps = shutil.which("powershell")
    if ps:
        out = _run([ps, "-NoProfile", "-NonInteractive", "-Command",
                    "Get-CimInstance Win32_Processor | ForEach-Object { $_.Name + '|' + $_.NumberOfCores }"])
        for ln in out.splitlines():
            if "|" in ln:
                n, c = ln.rsplit("|", 1)
                name = name or n.strip()
                physical += int(c) if c.strip().isdigit() else 0
    return {"name": name or "unknown", "physical_cores": physical or max(1, logical // 2),
            "logical_cores": logical}


def probe_gpus() -> List[Dict[str, Any]]:
    gpus: List[Dict[str, Any]] = []
    smi = shutil.which("nvidia-smi")
    if smi:
        out = _run([smi, "--query-gpu=name,memory.total,driver_version,compute_cap",
                    "--format=csv,noheader,nounits"])
        for ln in out.splitlines():
            f = [x.strip() for x in ln.split(",")]
            if len(f) >= 3:
                gpus.append({"vendor": "NVIDIA", "name": f[0],
                             "vram_gb": round(float(f[1]) / 1024, 1) if f[1].replace(".", "").isdigit() else 0.0,
                             "driver": f[2], "compute_capability": f[3] if len(f) > 3 else ""})
    ps = shutil.which("powershell")
    if ps:
        out = _run([ps, "-NoProfile", "-NonInteractive", "-Command",
                    "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"])
        for n in (x.strip() for x in out.splitlines()):
            if n and not any(n == g["name"] for g in gpus) and "nvidia" not in n.lower():
                vendor = "AMD" if ("amd" in n.lower() or "radeon" in n.lower()) else \
                         "Intel" if "intel" in n.lower() else "other"
                gpus.append({"vendor": vendor, "name": n, "vram_gb": 0.0, "driver": "", "compute_capability": ""})
    return gpus


def probe_disk_free_gb(path: Path) -> float:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / 2 ** 30
    except OSError:
        return 0.0


def scan(home: Path, probes: Optional[Dict[str, Callable]] = None) -> Dict[str, Any]:
    pr = {"ram": probe_ram_gb, "cpu": probe_cpu, "gpus": probe_gpus, "disk": probe_disk_free_gb}
    pr.update(probes or {})
    return {"cpu": pr["cpu"](), "ram_gb": round(pr["ram"](), 1), "gpus": pr["gpus"](),
            "disk_free_gb": round(pr["disk"](home), 1), "home": str(home)}


# ------------------------------------------------------------------ the plan
def _driver(v: str) -> float:
    try:
        parts = v.split(".")
        return float(parts[0] + "." + (parts[1] if len(parts) > 1 else "0"))
    except ValueError:
        return 0.0


def plan(hw: Dict[str, Any], packs: List[str]) -> Dict[str, Any]:
    """What to download and the default compute profile, with the reasons."""
    notes: List[str] = []
    cuda = [g for g in hw["gpus"] if g["vendor"] == "NVIDIA" and _driver(g["driver"]) >= MIN_DRIVER
            and g["vram_gb"] >= MIN_VRAM_GB]
    for g in hw["gpus"]:
        if g["vendor"] == "NVIDIA" and g not in cuda:
            notes.append(f"{g['name']}: driver {g['driver'] or '?'} / {g['vram_gb']} GB -- CUDA builds need "
                         f"driver >= {MIN_DRIVER:g} and >= {MIN_VRAM_GB:g} GB: update the NVIDIA driver to use it")
        elif g["vendor"] in ("AMD", "Intel"):
            notes.append(f"{g['name']}: no CUDA; PyTorch's GPU builds for it are not on Windows conda: CPU profile")
    wants_ai = any(p in packs for p in ("ia", "tout", "ia-gpu"))
    add: List[str] = []
    if cuda and wants_ai and "ia-gpu" not in packs:
        add.append("ia-gpu")
    gpu = max(cuda, key=lambda g: g["vram_gb"]) if cuda else None
    phys = int(hw["cpu"]["physical_cores"])
    workers = max(1, phys - 1)                     # one core left for the UI and the MCP servers
    ram = float(hw["ram_gb"])
    if ram < 16:
        notes.append(f"{ram:g} GB RAM: keep 3-D meshes small (CFD ~< 4 M cells, FEM ~< 1 M nodes); "
                     "skip the ros pack (9.5 GB, heavy)")
    elif ram < 48:
        notes.append(f"{ram:g} GB RAM: comfortable; the ceiling is large 3-D CFD/FEM "
                     f"(OpenFOAM ~{int(ram / 1.5 * 0.8)} M cells, CalculiX ~{max(1, int(ram / 12))} M nodes)")
    else:
        notes.append(f"{ram:g} GB RAM: large 3-D CFD/FEM fit")
    profile = {"device": "both" if gpu else "cpu", "cpu_workers": workers,
               "omp_threads": workers, "mpi_ranks": max(1, min(phys, 64)),
               "gpu": {"name": gpu["name"], "vram_gb": gpu["vram_gb"], "driver": gpu["driver"]} if gpu else None,
               "gpu_envs": {"ai": "ai-gpu"} if gpu else {}}
    return {"add_packs": add, "compute": profile, "notes": notes}


def write(home: Path, hw: Dict[str, Any], pl: Dict[str, Any]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "hardware.json").write_text(json.dumps(hw, indent=1), encoding="utf-8")
    cfg_p = home / "atelier.json"
    cfg = json.loads(cfg_p.read_text(encoding="utf-8")) if cfg_p.is_file() else {}
    cfg["compute"] = pl["compute"]
    tmp = cfg_p.with_name("atelier.json.tmp")
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    os.replace(tmp, cfg_p)


def describe(hw: Dict[str, Any], pl: Dict[str, Any]) -> str:
    c = hw["cpu"]
    lines = [f"CPU   {c['name']} -- {c['physical_cores']} cores / {c['logical_cores']} threads",
             f"RAM   {hw['ram_gb']} GB", f"disk  {hw['disk_free_gb']} GB free under {hw['home']}"]
    for g in hw["gpus"] or [{"vendor": "-", "name": "no GPU found", "vram_gb": 0, "driver": ""}]:
        lines.append(f"GPU   {g['name']} {g['vram_gb'] or ''}{' GB' if g['vram_gb'] else ''}"
                     f"{' driver ' + g['driver'] if g['driver'] else ''}")
    cp = pl["compute"]
    lines.append(f"=> profile {cp['device']}: {cp['cpu_workers']} CPU workers"
                 + (f", GPU {cp['gpu']['name']}" if cp["gpu"] else ""))
    if pl["add_packs"]:
        lines.append(f"=> also download: {', '.join(pl['add_packs'])}")
    lines += [f"   - {n}" for n in pl["notes"]]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--yes", action="store_true", help="consent given: scan without asking")
    ap.add_argument("--home", default=os.environ.get("MCPFC_HOME", str(Path.home() / "mcpfc-atelier")))
    ap.add_argument("--packs", default="socle")
    ap.add_argument("--write", action="store_true", help="store hardware.json + compute profile")
    a = ap.parse_args()
    if not ask(yes=a.yes):
        print("HWSCAN_DECLINED nothing was read; CPU profile (works on every machine)")
        return 0
    home = Path(a.home)
    hw = scan(home)
    pl = plan(hw, [p.strip() for p in a.packs.split(",")])
    print(describe(hw, pl))
    if a.write:
        write(home, hw, pl)
    print("HWSCAN_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
