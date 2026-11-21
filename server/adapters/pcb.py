"""Printed circuit board and wiring checks (env `elec`): what a board designer computes by hand.

Kinds:
- trace_width  IPC-2221 current capacity: minimum width for a current and a temperature rise, or
               the capacity of a given width; resistance, voltage drop and loss over a length
- via          plated through-hole: barrel section, IPC-2221 current, resistance, drop
- impedance    controlled impedance: microstrip and symmetric stripline (Pozar's closed forms),
               analysis (width -> Z0, effective permittivity, delay) or synthesis (Z0 -> width)
- erc          electrical rules check of a declared netlist (parts, pin types, nets): unconnected
               pins, single-pin nets, driver conflicts, undriven inputs and power inputs
- harness      a WireViz harness (YAML or dict): diagram SVG/HTML + BOM, wire count and length,
               and the voltage drop of every wire given a current

Units: mm, A, degC, ohm, V, W; copper weight in oz/ft^2 (1 oz = 35 um); impedance in ohm.

Limits, restated in every result:
- IPC-2221's curves are 1950s data and conservative for internal layers. IPC-2152 (planes, board
  material, neighbouring copper) is the modern reference; the result says when it is outside the
  charted range (0.5-3 oz, 10-100 degC rise, <= 35 A, <= 400 mil wide).
- The impedance formulas are quasi-static (no dispersion, no solder mask, no roughness), good to a
  few percent. A field solver or the fab's stack-up calculator settles a tight tolerance.
- The ERC sees the pin types you declare: a wrong pin type gives a wrong verdict.

Exactness: REFERENCES below. Pozar, Microwave Engineering, Examples 3.5 (50 ohm stripline,
b = 0.32 cm, er = 2.2 -> W = 0.266 cm) and 3.7 (50 ohm microstrip, d = 0.127 cm, er = 2.2 ->
W/d = 3.081, eps_eff = 1.87); IPC-2221 1 A / 10 degC / 1 oz: 0.30 mm outer, 0.78 mm inner, the
values published PCB calculators give; copper resistance by rho = 1.724e-8 ohm m at 20 degC.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from _common import result

ENV = "elec"
KINDS = {
    "trace_width": {"geometry": False, "timeout": 60,
                    "doc": "IPC-2221 trace current: min width for I and dT, or capacity of a width; R, drop, loss"},
    "via": {"geometry": False, "timeout": 60,
            "doc": "plated via: barrel section, IPC-2221 current for a dT, resistance, voltage drop"},
    "impedance": {"geometry": False, "timeout": 60,
                  "doc": "microstrip / stripline Z0 and eps_eff from width, or width for a target Z0"},
    "erc": {"geometry": False, "timeout": 60,
            "doc": "electrical rules check of a declared netlist: unconnected, conflicts, undriven"},
    "harness": {"geometry": False, "timeout": 300,
                "doc": "WireViz harness -> SVG/HTML + BOM, wire count/length, drop per wire"},
}

OZ_UM = 35.0                  # 1 oz/ft^2 copper, the 35 um (1.378 mil) PCB calculators use
MIL_MM = 0.0254
RHO_CU = 1.724e-8             # ohm m at 20 degC (annealed copper, IEC 60028)
ALPHA_CU = 0.00393            # 1/K
C0 = 299792458.0


def versions() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for mod in ("wireviz", "yaml", "numpy"):
        try:
            out[mod] = str(getattr(__import__(mod), "__version__", "?"))
        except Exception as e:  # noqa: BLE001
            out[mod] = f"missing ({type(e).__name__})"
    return out


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fn = {"trace_width": _trace_width, "via": _via, "impedance": _impedance,
          "erc": _erc, "harness": _harness}.get(kind)
    if fn is None:
        raise ValueError(f"unknown kind {kind!r}; one of {sorted(KINDS)}")
    return fn(inp, Path(work))


def _f(inp: Dict[str, Any], key: str, default=None, lo=None, hi=None) -> float:
    v = inp.get(key, default)
    if v is None:
        raise ValueError(f"{key} is required")
    v = float(v)
    if not math.isfinite(v) or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(f"{key}={v} outside [{lo}, {hi}]")
    return v


def _ipc_area_mil2(current: float, rise: float, k: float) -> float:
    return (current / (k * rise ** 0.44)) ** (1.0 / 0.725)


def _ipc_current(area_mil2: float, rise: float, k: float) -> float:
    return k * rise ** 0.44 * area_mil2 ** 0.725


def _copper_r(length_m: float, area_m2: float, temp_c: float) -> float:
    return RHO_CU * (1.0 + ALPHA_CU * (temp_c - 20.0)) * length_m / area_m2


def _trace_width(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    rise = _f(inp, "temp_rise_C", 10.0, 0.5, 200.0)
    oz = _f(inp, "copper_oz", 1.0, 0.1, 10.0)
    layer = str(inp.get("layer", "external")).lower()
    if layer not in ("external", "internal"):
        raise ValueError("layer: external or internal")
    k = 0.048 if layer == "external" else 0.024
    t_mil = oz * OZ_UM / 1000.0 / MIL_MM
    ambient = _f(inp, "ambient_C", 25.0, -60.0, 150.0)
    vals: Dict[str, Any] = {"thickness_um": round(oz * OZ_UM, 3)}
    notes: List[str] = []
    if inp.get("width_mm") is not None:
        w_mm = _f(inp, "width_mm", None, 0.01, 100.0)
        area = w_mm / MIL_MM * t_mil
        vals["capacity_A"] = round(_ipc_current(area, rise, k), 5)
        if inp.get("current_A") is not None:
            vals["current_to_capacity"] = round(_f(inp, "current_A", None, 0, 500) / vals["capacity_A"], 5)
    else:
        w_mm = None
    if inp.get("current_A") is not None:
        cur = _f(inp, "current_A", None, 1e-4, 500.0)
        area = _ipc_area_mil2(cur, rise, k)
        vals["area_mil2"] = round(area, 4)
        vals["min_width_mm"] = round(area / t_mil * MIL_MM, 5)
        vals["min_width_mil"] = round(area / t_mil, 4)
        if w_mm is None:
            w_mm = vals["min_width_mm"]
        if cur > 35:
            notes.append("current above IPC-2221's 35 A chart: extrapolated")
    if "capacity_A" not in vals and "min_width_mm" not in vals:
        raise ValueError("give current_A (-> min width) or width_mm (-> capacity), or both")
    if not (0.5 <= oz <= 3.0) or not (10 <= rise <= 100) or (w_mm and w_mm / MIL_MM > 400):
        notes.append("outside IPC-2221's charted range (0.5-3 oz, 10-100 degC, <= 400 mil): extrapolated")
    if inp.get("length_mm") is not None and w_mm:
        length = _f(inp, "length_mm", None, 0.0, 1e5) / 1000.0
        r = _copper_r(length, w_mm * 1e-3 * oz * OZ_UM * 1e-6, ambient + rise)
        vals["R_ohm"] = round(r, 7)
        if inp.get("current_A") is not None:
            cur = float(inp["current_A"])
            vals["drop_V"] = round(r * cur, 6)
            vals["loss_W"] = round(r * cur * cur, 6)
            if inp.get("voltage_V"):
                vals["drop_pct"] = round(100 * r * cur / float(inp["voltage_V"]), 4)
    notes.append(f"IPC-2221 {layer} (k={k}); IPC-2152 is the modern reference, less conservative inside")
    head = f"trace {layer} {oz:g} oz, dT {rise:g} degC: " + ", ".join(
        f"{k_}={v}" for k_, v in vals.items() if k_ in ("min_width_mm", "capacity_A", "R_ohm"))
    return result(vals, inp.get("limits"), head, notes=notes)


def _via(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    d = _f(inp, "drill_mm", 0.3, 0.05, 10.0)
    plating = _f(inp, "plating_um", 25.0, 5.0, 200.0) / 1000.0
    length = _f(inp, "length_mm", 1.6, 0.1, 20.0)
    rise = _f(inp, "temp_rise_C", 10.0, 0.5, 200.0)
    ambient = _f(inp, "ambient_C", 25.0, -60.0, 150.0)
    r_in = d / 2.0
    area_mm2 = math.pi * ((r_in + plating) ** 2 - r_in ** 2)
    area_mil2 = area_mm2 / MIL_MM ** 2
    r20 = _copper_r(length / 1000.0, area_mm2 * 1e-6, 20.0)
    r_hot = _copper_r(length / 1000.0, area_mm2 * 1e-6, ambient + rise)
    vals: Dict[str, Any] = {"barrel_area_mm2": round(area_mm2, 7),
                            "capacity_A": round(_ipc_current(area_mil2, rise, 0.048), 5),
                            "R20_ohm": round(r20, 8), "R_ohm": round(r_hot, 8)}
    if inp.get("current_A") is not None:
        cur = _f(inp, "current_A", None, 0, 500)
        vals["drop_V"] = round(r_hot * cur, 7)
        vals["vias_needed"] = max(1, math.ceil(cur / vals["capacity_A"] - 1e-12))
    head = f"via {d:g} mm, {plating * 1000:g} um plating: {vals['capacity_A']} A at dT {rise:g}, R {vals['R_ohm']} ohm"
    return result(vals, inp.get("limits"), head,
                  notes=["IPC-2221 external-layer constant on the barrel section (common practice); "
                         "thermal relief, pad and plane spreading not modelled"])


# ---------------------------------------------------------------- impedance (Pozar 3.7, 3.8)
def _ms_analysis(w_d: float, er: float) -> Tuple[float, float]:
    ee = (er + 1) / 2 + (er - 1) / 2 / math.sqrt(1 + 12 / w_d)
    if w_d <= 1:
        z0 = 60 / math.sqrt(ee) * math.log(8 / w_d + w_d / 4)
    else:
        z0 = 120 * math.pi / (math.sqrt(ee) * (w_d + 1.393 + 0.667 * math.log(w_d + 1.444)))
    return z0, ee


def _ms_synthesis(z0: float, er: float) -> float:
    a = z0 / 60 * math.sqrt((er + 1) / 2) + (er - 1) / (er + 1) * (0.23 + 0.11 / er)
    b = 377 * math.pi / (2 * z0 * math.sqrt(er))
    w_d = 8 * math.exp(a) / (math.exp(2 * a) - 2)
    if w_d > 2:
        w_d = 2 / math.pi * (b - 1 - math.log(2 * b - 1)
                             + (er - 1) / (2 * er) * (math.log(b - 1) + 0.39 - 0.61 / er))
    return w_d


def _sl_analysis(w_b: float, er: float) -> float:
    we_b = w_b if w_b > 0.35 else w_b - (0.35 - w_b) ** 2
    return 30 * math.pi / math.sqrt(er) / (we_b + 0.441)


def _sl_synthesis(z0: float, er: float) -> float:
    x = 30 * math.pi / (math.sqrt(er) * z0) - 0.441
    if math.sqrt(er) * z0 > 120:
        x = 0.85 - math.sqrt(0.6 - x)
    return x


def _impedance(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    line = str(inp.get("line", "microstrip")).lower()
    er = _f(inp, "er", None, 1.0, 100.0)
    vals: Dict[str, Any] = {}
    if line == "microstrip":
        h = _f(inp, "height_mm", None, 0.01, 50.0)
        if inp.get("width_mm") is not None:
            w = _f(inp, "width_mm", None, 1e-3, 100.0)
        else:
            w = _ms_synthesis(_f(inp, "z0_ohm", None, 5.0, 300.0), er) * h
        z0, ee = _ms_analysis(w / h, er)
        vals.update({"width_mm": round(w, 5), "w_over_h": round(w / h, 5), "z0_ohm": round(z0, 4),
                     "eps_eff": round(ee, 5)})
    elif line == "stripline":
        b = _f(inp, "spacing_mm", None, 0.01, 50.0)      # ground-to-ground
        if inp.get("width_mm") is not None:
            w = _f(inp, "width_mm", None, 1e-3, 100.0)
        else:
            w = _sl_synthesis(_f(inp, "z0_ohm", None, 5.0, 300.0), er) * b
            if w <= 0:
                raise ValueError("no positive width gives that impedance for this stack-up")
        ee = er
        vals.update({"width_mm": round(w, 5), "w_over_b": round(w / b, 5),
                     "z0_ohm": round(_sl_analysis(w / b, er), 4), "eps_eff": round(ee, 5)})
    else:
        raise ValueError("line: microstrip or stripline")
    vals["delay_ps_per_mm"] = round(math.sqrt(vals["eps_eff"]) / C0 * 1e9, 5)
    if inp.get("z0_ohm") is not None:
        vals["z0_error_rel"] = round(abs(vals["z0_ohm"] - float(inp["z0_ohm"])) / float(inp["z0_ohm"]), 6)
    head = f"{line} er {er:g}: W {vals['width_mm']} mm, Z0 {vals['z0_ohm']} ohm, eps_eff {vals['eps_eff']}"
    return result(vals, inp.get("limits"), head,
                  notes=["quasi-static closed forms (Pozar; its synthesis and analysis agree to ~0.6 %), "
                         "zero-thickness strip, no solder mask: "
                         "a few % -- confirm a tight tolerance with the fab's stack-up"])


# ---------------------------------------------------------------- ERC
PIN_TYPES = {"in", "out", "bidir", "tristate", "passive", "power_in", "power_out",
             "open_collector", "open_emitter", "nc"}
_DRIVERS = {"out", "bidir", "tristate", "passive", "power_out", "open_collector", "open_emitter"}
_REF = re.compile(r"^[A-Za-z_][A-Za-z0-9_+\-]{0,40}$")


def _erc(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    parts = inp.get("parts") or {}
    nets = inp.get("nets") or {}
    power_flags = set(inp.get("power_flags") or [])     # nets driven from off-board (connector, bench)
    if not isinstance(parts, dict) or not isinstance(nets, dict) or not parts or not nets:
        raise ValueError("parts {ref: {pin: type}} and nets {name: ['REF.pin', ...]} are required")
    if len(parts) > 2000 or len(nets) > 5000:
        raise ValueError("netlist too large for this check (2000 parts / 5000 nets)")
    pins: Dict[str, str] = {}
    errors: List[str] = []
    warns: List[str] = []
    for ref, pmap in parts.items():
        if not _REF.match(str(ref)) or not isinstance(pmap, dict):
            raise ValueError(f"bad part {ref!r}")
        for pin, ptype in pmap.items():
            pt = str(ptype).lower()
            if pt not in PIN_TYPES:
                raise ValueError(f"{ref}.{pin}: pin type {ptype!r} not in {sorted(PIN_TYPES)}")
            pins[f"{ref}.{pin}"] = pt
    seen: Dict[str, str] = {}
    for net, members in nets.items():
        if not isinstance(members, list):
            raise ValueError(f"net {net!r}: a list of 'REF.pin'")
        types = []
        for m in members:
            m = str(m)
            if m not in pins:
                errors.append(f"{net}: {m} is not a declared pin")
                continue
            if m in seen and seen[m] != net:
                errors.append(f"{m} is on two nets ({seen[m]}, {net})")
            seen[m] = net
            types.append(pins[m])
        if pins and "nc" in types:
            warns.append(f"{net}: a no-connect pin is connected")
        live = [t for t in types if t != "nc"]
        if len(members) == 1 and net not in power_flags:
            errors.append(f"{net}: single-pin net ({members[0]})")
        if live.count("out") + live.count("power_out") > 1 and "out" in live:
            errors.append(f"{net}: several push-pull outputs drive the same net")
        if live.count("power_out") > 1:
            warns.append(f"{net}: several power outputs in parallel")
        if "power_in" in live and "power_out" not in live and net not in power_flags:
            errors.append(f"{net}: power input not driven (no power output, no power flag)")
        if "in" in live and not any(t in _DRIVERS for t in live) and net not in power_flags:
            errors.append(f"{net}: input not driven")
    for p, t in pins.items():
        if p not in seen and t != "nc":
            errors.append(f"{p}: unconnected pin ({t})")
    vals = {"errors": len(errors), "warnings": len(warns), "parts": len(parts), "nets": len(nets),
            "pins": len(pins)}
    path = work / "erc_report.txt"
    path.write_text("\n".join(["ERRORS"] + errors + ["", "WARNINGS"] + warns), encoding="utf-8")
    head = f"ERC: {len(errors)} error(s), {len(warns)} warning(s) on {len(pins)} pins / {len(nets)} nets"
    limits = inp.get("limits") or {"errors": {"max": 0}}
    res = result(vals, limits, head, files=[path.name],
                 notes=errors[:8] + (["..."] if len(errors) > 8 else []) + warns[:3])
    return res


# ---------------------------------------------------------------- harness (WireViz)
_HARNESS_KEYS = {"connectors", "cables", "connections", "options", "metadata", "additional_bom_items"}


def _clean_harness(h: Dict[str, Any]) -> Dict[str, Any]:
    bad = set(h) - _HARNESS_KEYS
    if bad:
        raise ValueError(f"harness keys refused: {sorted(bad)} (tweak/templates can inject files)")

    def walk(o, where):
        if isinstance(o, dict):
            for k, v in o.items():
                if str(k) in ("image", "template", "tweak", "src"):
                    raise ValueError(f"{where}.{k}: images, templates and tweaks are refused "
                                     "(they read files from disk)")
                walk(v, f"{where}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{where}[{i}]")
    walk(h, "harness")
    return h


def _harness(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import yaml
    from elec import awg_area_mm2

    h = inp.get("harness")
    if isinstance(h, str):
        if len(h) > 200_000:
            raise ValueError("harness text too long")
        h = yaml.safe_load(h)
    if not isinstance(h, dict):
        raise ValueError("harness: WireViz YAML text or a dict")
    h = _clean_harness(h)
    cables = h.get("cables") or {}
    currents = inp.get("currents_A") or {}         # {"W1": 2.0} or {"W1:1": 2.0}
    volt = float(inp.get("voltage_V", 0) or 0)
    temp = float(inp.get("temperature_C", 20.0))
    n_wires = 0
    total_len = 0.0
    drops: Dict[str, float] = {}
    worst = 0.0
    for name, c in cables.items():
        c = c or {}
        count = int(c.get("wirecount") or len(c.get("colors") or []) or 1)
        length = float(c.get("length", 0) or 0)
        n_wires += count
        total_len += count * length
        gauge = c.get("gauge")
        unit = str(c.get("gauge_unit", "mm2")).lower()
        area = None
        if isinstance(gauge, str):
            m = re.match(r"\s*([\d.]+)\s*(mm2|awg)?", gauge.lower())
            if m:
                unit = m.group(2) or unit
                gauge = float(m.group(1))
        if gauge is not None:
            area = awg_area_mm2(int(gauge)) if "awg" in unit else float(gauge)
        for k in range(1, count + 1):
            cur = currents.get(f"{name}:{k}", currents.get(str(name)))
            if cur is None or area is None or length <= 0:
                continue
            r = _copper_r(2 * length, area * 1e-6, temp)   # out and back
            dv = r * float(cur)
            drops[f"{name}:{k}"] = dv
            if volt > 0:
                worst = max(worst, 100 * dv / volt)
    from wireviz import wireviz
    out_name = "harness"
    wireviz.parse(h, output_formats=("svg", "html", "tsv"), output_dir=str(work), output_name=out_name)
    files = [p.name for p in sorted(work.glob(out_name + ".*"))]
    vals: Dict[str, Any] = {"wires": n_wires, "cables": len(cables), "connectors": len(h.get("connectors") or {}),
                            "wire_length_total_m": round(total_len, 4),
                            "svg_written": 1.0 if (work / f"{out_name}.svg").exists() else 0.0}
    if drops:
        vals["max_drop_V"] = round(max(drops.values()), 6)
        if volt > 0:
            vals["max_drop_pct"] = round(worst, 5)
    head = f"harness: {n_wires} wires in {len(cables)} cable(s), {vals['connectors']} connectors, BOM + diagram"
    return result(vals, inp.get("limits"), head, files=files,
                  notes=["drop: copper out and back over the cable length, at temperature_C; "
                         "current rating per wire comes from `elec wire_sizing`"])


_ERC_OK = {
    "parts": {"J1": {"1": "power_out", "2": "power_out"},
              "U1": {"VCC": "power_in", "GND": "power_in", "IN": "in", "OUT": "out"},
              "R1": {"1": "passive", "2": "passive"}, "R2": {"1": "passive", "2": "passive"},
              "D1": {"A": "passive", "K": "passive"}},
    "nets": {"VCC": ["J1.1", "U1.VCC", "R1.1"], "GND": ["J1.2", "U1.GND", "D1.K", "R2.2"],
             "SIG": ["R1.2", "R2.1", "U1.IN"], "LED": ["U1.OUT", "D1.A"]},
}
_ERC_BAD = {   # three planted faults: SIG has no driver, U2.OUT fights U1.OUT, U2.VCC unconnected
    "parts": {"J1": {"1": "power_out", "2": "power_out"},
              "U1": {"VCC": "power_in", "GND": "power_in", "IN": "in", "OUT": "out"},
              "U2": {"VCC": "power_in", "IN": "in", "OUT": "out"}, "D1": {"A": "passive", "K": "passive"}},
    "nets": {"VCC": ["J1.1", "U1.VCC"], "GND": ["J1.2", "U1.GND", "D1.K"], "SIG": ["U1.IN", "U2.IN"],
             "LED": ["U1.OUT", "U2.OUT", "D1.A"]},
}

REFERENCES = [
    {"name": "pozar_ex3_7_microstrip_50ohm", "kind": "impedance",
     "inp": {"line": "microstrip", "er": 2.2, "height_mm": 1.27, "z0_ohm": 50.0},
     "expect": {"w_over_h": [3.081, 1e-3], "eps_eff": [1.87, 2e-3], "z0_error_rel": [0.0, 1e-2]}},
    {"name": "pozar_ex3_5_stripline_50ohm", "kind": "impedance",
     "inp": {"line": "stripline", "er": 2.2, "spacing_mm": 3.2, "z0_ohm": 50.0},
     "expect": {"width_mm": [2.66, 2e-3], "z0_error_rel": [0.0, 1e-6]}},
    {"name": "ipc2221_1A_10C_1oz_external", "kind": "trace_width",
     "inp": {"current_A": 1.0, "temp_rise_C": 10, "copper_oz": 1.0, "layer": "external"},
     "expect": {"min_width_mm": [0.300, 5e-3]}},
    {"name": "ipc2221_1A_10C_1oz_internal", "kind": "trace_width",
     "inp": {"current_A": 1.0, "temp_rise_C": 10, "copper_oz": 1.0, "layer": "internal"},
     "expect": {"min_width_mm": [0.781, 5e-3]}},
    {"name": "trace_resistance_copper_20C", "kind": "trace_width",   # rho L / (w t) at 20 degC
     "inp": {"width_mm": 0.3, "copper_oz": 1.0, "length_mm": 100.0, "ambient_C": 20.0, "temp_rise_C": 0.5},
     "expect": {"R_ohm": [RHO_CU * (1 + ALPHA_CU * 0.5) * 0.1 / (0.3e-3 * OZ_UM * 1e-6), 1e-5]}},
    {"name": "via_barrel_resistance_20C", "kind": "via",
     "inp": {"drill_mm": 0.3, "plating_um": 25.0, "length_mm": 1.6},
     "expect": {"barrel_area_mm2": [math.pi * (0.175 ** 2 - 0.15 ** 2), 1e-5],
                "R20_ohm": [RHO_CU * 1.6e-3 / (math.pi * (0.175 ** 2 - 0.15 ** 2) * 1e-6), 1e-5]}},
    {"name": "erc_clean_board", "kind": "erc", "inp": _ERC_OK,
     "expect": {"errors": [0, 0], "warnings": [0, 0], "pins": [12, 0]}},
    {"name": "erc_three_planted_faults", "kind": "erc", "inp": _ERC_BAD,
     "expect": {"errors": [3, 0]}},
    {"name": "harness_two_connectors_drop", "kind": "harness",
     "inp": {"harness": {
         "connectors": {"X1": {"pincount": 3, "type": "JST XH"}, "X2": {"pincount": 3, "type": "JST XH"}},
         "cables": {"W1": {"wirecount": 3, "length": 0.5, "gauge": "0.25 mm2",
                           "colors": ["RD", "BK", "YE"]}},
         "connections": [[{"X1": [1, 2, 3]}, {"W1": [1, 2, 3]}, {"X2": [1, 2, 3]}]]},
         "currents_A": {"W1:1": 2.0}, "voltage_V": 12.0, "temperature_C": 20.0},
     "expect": {"wires": [3, 0], "svg_written": [1.0, 0],
                "max_drop_V": [RHO_CU * 1.0 / 0.25e-6 * 2.0, 1e-5]}},
]

