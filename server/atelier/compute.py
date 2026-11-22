"""Where a computation runs: CPU, GPU, or both (network on the GPU, physics on the CPU cores).

The default profile comes from atelier.json "compute" (written by the hardware scan,
install/hw_scan.py, only with the owner's consent); without it: CPU, every machine. A run picks
its own with params["device"] = "cpu" | "gpu" | "both" | "auto".

An adapter whose env has a GPU twin (gpu_envs, e.g. ai -> ai-gpu) runs in the twin when the
device is gpu/both and the twin is installed; otherwise it falls back to the CPU env and says so.
The twin passes its own reference cases (validation is per environment).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

DEVICES = ("auto", "cpu", "gpu", "both")
# environments that have a CUDA twin (pixi.toml); the scan's profile may add or override
GPU_TWINS = {"ai": "ai-gpu"}


def profile() -> Dict[str, Any]:
    from . import config
    c = dict(config().get("compute") or {})
    workers = int(c.get("cpu_workers") or max(1, (os.cpu_count() or 2) // 2 - 1))
    return {"device": c.get("device", "cpu"), "cpu_workers": workers,
            "omp_threads": int(c.get("omp_threads") or workers),
            "mpi_ranks": int(c.get("mpi_ranks") or max(1, workers)),
            "gpu": c.get("gpu"), "gpu_envs": {**GPU_TWINS, **dict(c.get("gpu_envs") or {})}}


def resolve(env: str, requested: Optional[str]) -> Tuple[str, str, str]:
    """(env to launch, device, note) for an adapter env and the requested device."""
    from . import env_python
    p = profile()
    dev = (requested or "auto").lower()
    if dev not in DEVICES:
        raise ValueError(f"device {requested!r}: one of {DEVICES}")
    if dev == "auto":
        dev = p["device"]
    if dev == "cpu":
        return env, "cpu", ""
    twin = p["gpu_envs"].get(env)
    if twin and env_python(twin) is not None:
        return twin, dev, ""
    if twin or p["gpu"]:
        return env, "cpu", f"GPU asked but the `{twin or env}-gpu` environment is not installed: ran on CPU"
    return env, "cpu", "" if requested in (None, "auto") else "no GPU in the compute profile: ran on CPU"


def env_vars(device: str) -> Dict[str, str]:
    """Threads and device for the adapter process (solvers read OMP_NUM_THREADS, the AI adapter
    reads MCPFC_DEVICE); on CPU the GPU is hidden so nothing lands on it by accident."""
    p = profile()
    v = {"MCPFC_DEVICE": device, "MCPFC_CPU_WORKERS": str(p["cpu_workers"]),
         "MCPFC_MPI_RANKS": str(p["mpi_ranks"]), "OMP_NUM_THREADS": str(p["omp_threads"])}
    if device == "cpu":
        v["CUDA_VISIBLE_DEVICES"] = ""
    return v
