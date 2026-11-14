"""Fatigue from a load or stress history (env `sim`): rainflow counting + Palmgren-Miner damage.

Kind `fatigue`:
- signal: a list of values (stress MPa, or load N with `scale` = stress per unit load), or
  `csv` = a file in the study folder with one column (or `column` = name/index);
- counting: ASTM E1049-85 §5.4.4 rainflow (half and full cycles, residue as half cycles);
- S-N curve (one of):
    {"basquin": {"Sf_MPa": ..., "b": ...}}          sigma_a = Sf (2N)^b    (Basquin)
    {"power": {"C": ..., "m": ...}}                  N = C * S_range^-m      (e.g. Eurocode 3 detail)
  optional "endurance_MPa" (amplitude below it does no damage: state the source in hypotheses);
- mean-stress correction: "goodman" with Su_MPa (sigma_a / (1 - sigma_m / Su)), or none;
- repeats: how many times the history is applied (block loading).
Outputs: cycle count, damage D (Miner sum), life in repeats of the history (1/D), max range,
damage-equivalent range at 1e6 / 2e6 cycles for the curve's slope.

Limits: uniaxial, no notch or size factors unless the S-N curve already includes them (it is the
user's curve: its source belongs in the study's hypotheses). Miner D = 1 is a mean, not a bound.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from _common import result

ENV = "sim"
KINDS = {
    "fatigue": {"geometry": False, "timeout": 300,
                "doc": "rainflow (ASTM E1049) + Miner damage on a stress/load history and an S-N curve"},
}


def versions() -> Dict[str, str]:
    import numpy
    return {"rainflow": "ASTM E1049-85 5.4.4 (in-house)", "numpy": numpy.__version__}


def turning_points(x: List[float]) -> List[float]:
    out: List[float] = []
    for v in x:
        if out and v == out[-1]:
            continue
        if len(out) >= 2 and (out[-1] - out[-2]) * (v - out[-1]) > 0:
            out[-1] = v                       # still rising / falling: extend
        else:
            out.append(v)
    return out


def rainflow(x: List[float]) -> List[Tuple[float, float, float]]:
    """(range, mean, count) with count 0.5 or 1, ASTM E1049-85 §5.4.4."""
    pts = turning_points(x)
    stack: List[float] = []
    cycles: List[Tuple[float, float, float]] = []
    for p in pts:
        stack.append(p)
        while len(stack) >= 3:
            X = abs(stack[-1] - stack[-2])
            Y = abs(stack[-2] - stack[-3])
            if X < Y:
                break
            if len(stack) == 3:               # Y contains the starting point: half cycle
                cycles.append((Y, (stack[0] + stack[1]) / 2, 0.5))
                stack.pop(0)
            else:
                cycles.append((Y, (stack[-2] + stack[-3]) / 2, 1.0))
                last = stack.pop()
                stack.pop()
                stack.pop()
                stack.append(last)
    for a, b in zip(stack, stack[1:]):        # residue: half cycles
        cycles.append((abs(b - a), (a + b) / 2, 0.5))
    return cycles


def _signal(inp: Dict[str, Any], work: Path) -> List[float]:
    if inp.get("signal") is not None:
        sig = [float(v) for v in inp["signal"]]
    elif inp.get("csv"):
        p = (work / inp["csv"]).resolve()
        if not p.is_relative_to(work.resolve()) or not p.is_file():
            raise ValueError("csv must be a file inside the study folder")
        col = inp.get("column", 0)
        with open(p, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if isinstance(col, str):
            idx = rows[0].index(col)
            rows = rows[1:]
        else:
            idx = int(col)
            if rows and not _is_num(rows[0][idx]):
                rows = rows[1:]
        sig = [float(r[idx]) for r in rows if r]
    else:
        raise ValueError("give signal (a list) or csv (a file in the study folder)")
    if len(sig) < 2:
        raise ValueError("the history needs at least 2 points")
    return [v * float(inp.get("scale", 1.0)) for v in sig]


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _life(rng: float, mean: float, sn: Dict[str, Any], mean_corr: str, su: float) -> float:
    amp = rng / 2
    if mean_corr == "goodman":
        if mean >= su:
            return 0.0
        amp = amp / (1 - mean / su) if mean > 0 else amp
    if amp <= 0:
        return math.inf
    if sn.get("endurance_MPa") is not None and amp < float(sn["endurance_MPa"]):
        return math.inf
    if "basquin" in sn:
        sf, b = float(sn["basquin"]["Sf_MPa"]), float(sn["basquin"]["b"])
        return 0.5 * (amp / sf) ** (1 / b)
    if "power" in sn:
        C, m = float(sn["power"]["C"]), float(sn["power"]["m"])
        return C * (2 * amp) ** (-m)
    raise ValueError("sn: basquin {Sf_MPa, b} or power {C, m}")


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    sig = _signal(inp, work)
    cyc = rainflow(sig)
    sn = inp.get("sn")
    values: Dict[str, Any] = {"cycles": sum(c for _, _, c in cyc),
                              "max_range": max((r for r, _, _ in cyc), default=0.0),
                              "half_cycles": sum(1 for *_, c in cyc if c == 0.5)}
    notes = []
    if sn:
        mc = str(inp.get("mean_correction", "none")).lower()
        su = float(inp.get("Su_MPa", 0) or 0)
        if mc == "goodman" and su <= 0:
            raise ValueError("goodman needs Su_MPa")
        reps = float(inp.get("repeats", 1))
        D = sum(c / _life(r, m, sn, mc, su) for r, m, c in cyc if r > 0) * reps
        values["damage"] = D
        values["life_repeats"] = (reps / D) if D > 0 else math.inf
        if "power" in sn:
            m = float(sn["power"]["m"])
            n = sum(c for r, _, c in cyc if r > 0)
            for N in (1e6, 2e6):
                values[f"eq_range_at_{N:.0e}"] = (sum(c * r ** m for r, _, c in cyc) * reps / N) ** (1 / m) if n else 0
        notes.append("Miner D = 1 is a mean failure estimate, not a guaranteed life")
    head = f"fatigue: {values['cycles']:g} cycles, max range {values['max_range']:.4g}"
    if "damage" in values:
        head += f", damage {values['damage']:.4g}, life {values['life_repeats']:.4g} × the history"
    rows = [{"range": r, "mean": m, "count": c} for r, m, c in cyc]
    (work / "cycles.csv").write_text("range,mean,count\n" + "\n".join(f"{x['range']},{x['mean']},{x['count']}" for x in rows),
                                     encoding="utf-8")
    return result(values, inp.get("limits"), head, ["cycles.csv"], notes)


REFERENCES = [
    {   # ASTM E1049-85 Fig. 6 example: ranges 3 (0.5), 4 (1.5), 6 (0.5), 8 (1.0), 9 (0.5) -> 4 cycles
        "name": "astm_e1049_example", "kind": "fatigue",
        "inp": {"signal": [-2, 1, -3, 5, -1, 3, -4, 4, -2],
                "sn": {"power": {"C": 1.0, "m": 1.0}}},
        # with N = 1/S: D = sum(count * range) = 0.5*3 + 1.5*4 + 0.5*6 + 1*8 + 0.5*9 = 23
        "expect": {"cycles": [4.0, 1e-12], "max_range": [9.0, 1e-12], "damage": [23.0, 1e-12]},
    },
    {   # constant amplitude 100 MPa (2000 reversals = 1000 cycles), Basquin Sf=900, b=-0.1: N = 0.5 (100/900)^(1/b)
        "name": "miner_constant_amplitude", "kind": "fatigue",
        "inp": {"signal": [-100, 100] * 1000 + [-100],
                "sn": {"basquin": {"Sf_MPa": 900, "b": -0.1}}},
        "expect": {"damage": [1000 / (0.5 * (100 / 900) ** (1 / -0.1)), 1e-3]},
    },
]
