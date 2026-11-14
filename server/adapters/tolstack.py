"""1-D tolerance stack-up (env `sim`): worst case, RSS and Monte-Carlo of a chain of dimensions.

Kind `tolstack`, params:
  dims: [{"name": "A", "nominal": 50.0, "plus": 0.1, "minus": 0.1, "sign": +1,
          "dist": "normal" | "uniform", "cpk": 1.0}]       (mm; sign +1 adds, -1 subtracts)
  samples: Monte-Carlo samples (default 200000), seed (default 1)
  limits: e.g. {"gap_min_mm": {"min": 0.0}} or {"reject_ppm": {"max": 100}}; spec {"min": x, "max": y}
          on the gap gives reject_ppm.
Normal distribution: the tolerance band is +/- 3 sigma at cpk 1 (sigma = half band / (3 cpk)),
centred on the middle of the band. Uniform: flat over the band.
Outputs: gap_nominal, gap_wc_min/max (worst case), gap_rss_min/max (3 sigma, centred), MC mean,
std, min, max, and reject_ppm against spec. Limits of the method: linear chain only (no angles,
no GD&T zones); the RSS assumes independent, centred processes.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict

from _common import result

ENV = "sim"
KINDS = {"tolstack": {"geometry": False, "timeout": 120,
                      "doc": "1-D stack-up: worst case, RSS, Monte-Carlo, reject rate vs spec"}}


def versions() -> Dict[str, str]:
    import numpy
    return {"numpy": numpy.__version__}


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    dims = inp.get("dims") or []
    if not dims:
        raise ValueError("dims: at least one dimension")
    n = int(inp.get("samples", 200_000))
    rng = np.random.default_rng(int(inp.get("seed", 1)))
    nom = wc_lo = wc_hi = 0.0
    var = 0.0
    mid = 0.0
    mc = np.zeros(n)
    for d in dims:
        s = int(d.get("sign", 1))
        if s not in (1, -1):
            raise ValueError(f"{d.get('name')}: sign must be +1 or -1")
        plus, minus = float(d.get("plus", 0)), float(d.get("minus", 0))
        if plus < -minus:
            raise ValueError(f"{d.get('name')}: empty tolerance band")
        lo, hi = float(d["nominal"]) - minus, float(d["nominal"]) + plus
        nom += s * float(d["nominal"])
        wc_lo += s * (lo if s > 0 else hi)
        wc_hi += s * (hi if s > 0 else lo)
        c = (lo + hi) / 2
        mid += s * c
        half = (hi - lo) / 2
        if d.get("dist", "normal") == "uniform":
            var += half ** 2 / 3
            mc += s * rng.uniform(lo, hi, n)
        else:
            sigma = half / (3 * float(d.get("cpk", 1.0))) if half > 0 else 0.0
            var += sigma ** 2
            mc += s * (rng.normal(c, sigma, n) if sigma > 0 else np.full(n, c))
    sd = math.sqrt(var)
    values: Dict[str, Any] = {"gap_nominal": nom, "gap_wc_min": wc_lo, "gap_wc_max": wc_hi,
                              "gap_rss_min": mid - 3 * sd, "gap_rss_max": mid + 3 * sd,
                              "gap_mc_mean": float(mc.mean()), "gap_mc_std": float(mc.std()),
                              "gap_mc_min": float(mc.min()), "gap_mc_max": float(mc.max()),
                              "gap_min_mm": wc_lo}
    spec = inp.get("spec") or {}
    if spec:
        bad = np.zeros(n, dtype=bool)
        if "min" in spec:
            bad |= mc < float(spec["min"])
        if "max" in spec:
            bad |= mc > float(spec["max"])
        values["reject_ppm"] = float(bad.mean() * 1e6)
    head = (f"tolstack: nominal {nom:.4g}, worst case {wc_lo:.4g}..{wc_hi:.4g}, "
            f"RSS {mid - 3 * sd:.4g}..{mid + 3 * sd:.4g} mm")
    if "reject_ppm" in values:
        head += f", rejects {values['reject_ppm']:.0f} ppm"
    return result(values, inp.get("limits"), head)


REFERENCES = [
    {   # housing 50 +/-0.1 minus 3 parts 16.5 +/-0.05: WC = 0.5 +/- 0.25, RSS = 0.5 +/- sqrt(.1^2+3*.05^2)
        "name": "wc_and_rss", "kind": "tolstack",
        "inp": {"dims": [{"name": "housing", "nominal": 50, "plus": 0.1, "minus": 0.1, "sign": 1},
                         *[{"name": f"p{i}", "nominal": 16.5, "plus": 0.05, "minus": 0.05, "sign": -1}
                           for i in range(3)]],
                "spec": {"min": 0.5 - math.sqrt(0.1 ** 2 + 3 * 0.05 ** 2)}, "samples": 2_000_000},
        "expect": {"gap_wc_min": [0.25, 1e-9], "gap_wc_max": [0.75, 1e-9],
                   "gap_rss_min": [0.5 - math.sqrt(0.1 ** 2 + 3 * 0.05 ** 2), 1e-9],
                   # spec at -3 sigma of the stack: P(Z < -3) = 1349.9 ppm (normal table);
                   # 2e6 samples -> ~2700 rejects, Monte-Carlo noise ~2 %
                   "reject_ppm": [1349.9, 0.06]},
    },
    {   # two uniform U(0,1) summed: variance 2/12, std sqrt(1/6)
        "name": "uniform_variance", "kind": "tolstack",
        "inp": {"dims": [{"nominal": 0.5, "plus": 0.5, "minus": 0.5, "dist": "uniform"},
                         {"nominal": 0.5, "plus": 0.5, "minus": 0.5, "dist": "uniform"}]},
        "expect": {"gap_mc_std": [math.sqrt(1 / 6), 0.01], "gap_mc_mean": [1.0, 0.005]},
    },
]
