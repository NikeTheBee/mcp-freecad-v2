"""Gas-turbine cycle analysis with NASA's pyCycle (env `opt`).

The class of tool NASA uses NPSS for: a thermodynamic cycle model of a jet engine, solved with
real-gas thermodynamics, component maps and a Newton solver. pyCycle (NASA Glenn, Apache-2.0)
runs on OpenMDAO.

Kind:
- turbojet   single-spool turbojet (inlet, axial compressor map AXI5, burner, turbine map
             LPT2269, convergent-divergent nozzle, shaft). One DESIGN point sized for a net thrust
             and a turbine inlet temperature, then OFF-DESIGN points at the same hardware
             (nozzle throat fixed) for other altitudes / Mach / thrusts.

Inputs (SI): thrust_N, T4_K, comp_PR, comp_eff, turb_eff, alt_m (0), mach (1e-6),
Nmech_rpm (8070), burner_dPqP (0.03), nozzle_Cv (0.99), off_design [{alt_m, mach, thrust_N}].
Outputs per point (prefix `od<k>_` off-design): W_kg_s (air mass flow), FAR, OPR, Fg_N, Fn_N,
TSFC_g_kN_s and TSFC_lbm_lbf_h, Tt3_K, Tt4_K, turb_PR (design), Nmech_rpm (off-design).

Limits, restated in every result: the component maps are pyCycle's generic ones (AXI5, LPT2269),
scaled at design -- not your machine's maps; no cooling flows, no bleed, no power offtake; an
off-design point outside the map extrapolates (map_extrap) and says so. A cycle number is a
thermodynamic estimate, not a certified engine performance.

Exactness: REFERENCES reproduce the pyCycle team's regression values for its simple turbojet
(example_cycles/tests/benchmark_simple_turbojet.py: W 147.333 lbm/s, FAR 0.01776487, TSFC
0.7985165 lbm/(lbf h) at design; Nmech 7943.933 rpm, TSFC 0.7836368 off-design), the
cycle pyCycle checks against NPSS.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")
os.environ.setdefault("OPENMDAO_REPORTS", "0")

import math  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

from _common import result  # noqa: E402

ENV = "opt"
KINDS = {
    "turbojet": {"timeout": 600,
                 "doc": "pyCycle single-spool turbojet: design point sized for thrust and T4, then "
                        "off-design points (mass flow, FAR, OPR, TSFC, Tt3, shaft speed)"},
}

LBF = 4.4482216152605          # N
LBM = 0.45359237               # kg
_TSFC_SI = LBM * 1000.0 / (3600.0 * LBF / 1000.0)   # lbm/(lbf h) -> g/(kN s) (28.3245)


def versions() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for mod in ("pycycle", "openmdao", "numpy"):
        try:
            out[mod] = str(__import__(mod).__version__)
        except Exception as e:  # noqa: BLE001
            out[mod] = f"missing ({type(e).__name__})"
    return out


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    if kind != "turbojet":
        raise ValueError(f"unknown kind {kind!r}; one of {sorted(KINDS)}")
    return _turbojet(inp, Path(work))


def _num(inp: Dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    v = float(inp.get(key, default))
    if not (math.isfinite(v) and lo <= v <= hi):
        raise ValueError(f"{key}={v} outside [{lo}, {hi}]")
    return v


class _Scalars:
    """The inputs of pyCycle's tabular ThermoAdd, size-1 scalars handed over as 0-d.

    pyCycle 4.4 writes a (1,) array into one element (`W_other_out[i] += W_other_mix`), which
    numpy >= 2.5 refuses ("setting an array element with a sequence"). Same numbers, 0-d shape.
    """

    def __init__(self, inputs):
        self._i = inputs

    def __getitem__(self, key):
        v = self._i[key]
        if (key.endswith(":ratio") or key == "Fl_I:stat:W") and getattr(v, "size", 0) == 1:
            return v.reshape(())
        return v


def _patch_numpy25() -> None:
    from pycycle.thermo.tabular import thermo_add as ta
    if getattr(ta.ThermoAdd, "_mcpfc_np25", False):
        return
    orig = ta.ThermoAdd.compute

    def compute(self, inputs, outputs, *a, **k):
        return orig(self, _Scalars(inputs), outputs, *a, **k)

    ta.ThermoAdd.compute = compute
    ta.ThermoAdd._mcpfc_np25 = True


def _build(od: List[Dict[str, float]], nmech: float, dpqp: float, cv: float):
    import openmdao.api as om
    import pycycle.api as pyc
    _patch_numpy25()

    class Turbojet(pyc.Cycle):
        def setup(self):
            self.options["thermo_method"] = "TABULAR"
            self.options["thermo_data"] = pyc.AIR_JETA_TAB_SPEC
            design = self.options["design"]
            self.add_subsystem("fc", pyc.FlightConditions())
            self.add_subsystem("inlet", pyc.Inlet())
            self.add_subsystem("comp", pyc.Compressor(map_data=pyc.AXI5, map_extrap=True),
                               promotes_inputs=["Nmech"])
            self.add_subsystem("burner", pyc.Combustor(fuel_type="FAR"))
            self.add_subsystem("turb", pyc.Turbine(map_data=pyc.LPT2269), promotes_inputs=["Nmech"])
            self.add_subsystem("nozz", pyc.Nozzle(nozzType="CD", lossCoef="Cv"))
            self.add_subsystem("shaft", pyc.Shaft(num_ports=2), promotes_inputs=["Nmech"])
            self.add_subsystem("perf", pyc.Performance(num_nozzles=1, num_burners=1))
            self.pyc_connect_flow("fc.Fl_O", "inlet.Fl_I", connect_w=False)
            self.pyc_connect_flow("inlet.Fl_O", "comp.Fl_I")
            self.pyc_connect_flow("comp.Fl_O", "burner.Fl_I")
            self.pyc_connect_flow("burner.Fl_O", "turb.Fl_I")
            self.pyc_connect_flow("turb.Fl_O", "nozz.Fl_I")
            self.connect("comp.trq", "shaft.trq_0")
            self.connect("turb.trq", "shaft.trq_1")
            self.connect("fc.Fl_O:stat:P", "nozz.Ps_exhaust")
            self.connect("inlet.Fl_O:tot:P", "perf.Pt2")
            self.connect("comp.Fl_O:tot:P", "perf.Pt3")
            self.connect("burner.Wfuel", "perf.Wfuel_0")
            self.connect("inlet.F_ram", "perf.ram_drag")
            self.connect("nozz.Fg", "perf.Fg_0")
            bal = self.add_subsystem("balance", om.BalanceComp())
            if design:
                bal.add_balance("W", units="lbm/s", eq_units="lbf", rhs_name="Fn_target")
                self.connect("balance.W", "inlet.Fl_I:stat:W")
                self.connect("perf.Fn", "balance.lhs:W")
                bal.add_balance("FAR", eq_units="degR", lower=1e-4, val=.017, rhs_name="T4_target")
                self.connect("balance.FAR", "burner.Fl_I:FAR")
                self.connect("burner.Fl_O:tot:T", "balance.lhs:FAR")
                bal.add_balance("turb_PR", val=1.5, lower=1.001, upper=8, eq_units="hp", rhs_val=0.)
                self.connect("balance.turb_PR", "turb.PR")
                self.connect("shaft.pwr_net", "balance.lhs:turb_PR")
            else:
                bal.add_balance("FAR", eq_units="lbf", lower=1e-4, val=.3, rhs_name="Fn_target")
                self.connect("balance.FAR", "burner.Fl_I:FAR")
                self.connect("perf.Fn", "balance.lhs:FAR")
                bal.add_balance("Nmech", val=1.5, units="rpm", lower=500., eq_units="hp", rhs_val=0.)
                self.connect("balance.Nmech", "Nmech")
                self.connect("shaft.pwr_net", "balance.lhs:Nmech")
                bal.add_balance("W", val=168.0, units="lbm/s", eq_units="inch**2")
                self.connect("balance.W", "inlet.Fl_I:stat:W")
                self.connect("nozz.Throat:stat:area", "balance.lhs:W")
            newton = self.nonlinear_solver = om.NewtonSolver()
            for key, val in (("atol", 1e-6), ("rtol", 1e-6), ("iprint", -1), ("maxiter", 30),
                             ("solve_subsystems", True), ("max_sub_solves", 100),
                             ("reraise_child_analysiserror", False)):
                newton.options[key] = val
            newton.linesearch = om.ArmijoGoldsteinLS(maxiter=3, iprint=-1)
            self.linear_solver = om.DirectSolver()
            super().setup()

    class MP(pyc.MPCycle):
        def setup(self):
            self.pyc_add_pnt("DESIGN", Turbojet())
            self.set_input_defaults("DESIGN.Nmech", nmech, units="rpm")
            self.set_input_defaults("DESIGN.inlet.MN", 0.60)
            self.set_input_defaults("DESIGN.comp.MN", 0.020)
            self.set_input_defaults("DESIGN.burner.MN", 0.020)
            self.set_input_defaults("DESIGN.turb.MN", 0.4)
            self.pyc_add_cycle_param("burner.dPqP", dpqp)
            self.pyc_add_cycle_param("nozz.Cv", cv)
            for k, p in enumerate(od):
                pt = f"OD{k}"
                self.pyc_add_pnt(pt, Turbojet(design=False))
                self.set_input_defaults(pt + ".fc.MN", val=p["mach"])
                self.set_input_defaults(pt + ".fc.alt", p["alt_m"], units="m")
                self.set_input_defaults(pt + ".balance.Fn_target", p["thrust_N"], units="N")
            self.pyc_use_default_des_od_conns()
            self.pyc_connect_des_od("nozz.Throat:stat:area", "balance.rhs:W")
            super().setup()

    prob = om.Problem(reports=False)
    prob.model = MP()
    prob.setup(check=False)
    prob.set_solver_print(level=-1)
    return prob


def _turbojet(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    thrust = _num(inp, "thrust_N", 52488.9, 10.0, 1e6)
    t4 = _num(inp, "T4_K", 1316.7, 800.0, 2300.0)
    pr = _num(inp, "comp_PR", 13.5, 1.5, 60.0)
    ce = _num(inp, "comp_eff", 0.83, 0.5, 0.99)
    te = _num(inp, "turb_eff", 0.86, 0.5, 0.99)
    alt = _num(inp, "alt_m", 0.0, -500.0, 25000.0)
    mach = max(_num(inp, "mach", 1e-6, 0.0, 3.0), 1e-6)
    nmech = _num(inp, "Nmech_rpm", 8070.0, 100.0, 1e6)
    dpqp = _num(inp, "burner_dPqP", 0.03, 0.0, 0.2)
    cv = _num(inp, "nozzle_Cv", 0.99, 0.8, 1.0)
    od: List[Dict[str, float]] = []
    for p in list(inp.get("off_design") or [])[:8]:
        od.append({"alt_m": _num(p, "alt_m", 0.0, -500.0, 25000.0),
                   "mach": max(_num(p, "mach", 1e-6, 0.0, 3.0), 1e-6),
                   "thrust_N": _num(p, "thrust_N", thrust, 10.0, 1e6)})

    prob = _build(od, nmech, dpqp, cv)
    prob.set_val("DESIGN.fc.alt", alt, units="m")
    prob.set_val("DESIGN.fc.MN", mach)
    prob.set_val("DESIGN.balance.Fn_target", thrust, units="N")
    prob.set_val("DESIGN.balance.T4_target", t4, units="K")
    prob.set_val("DESIGN.comp.PR", pr)
    prob.set_val("DESIGN.comp.eff", ce)
    prob.set_val("DESIGN.turb.eff", te)
    # the published starting point of pyCycle's example (any thrust: W scales from it)
    prob["DESIGN.balance.FAR"] = 0.0175506829934
    prob["DESIGN.balance.W"] = 168.453135137 * thrust / 52488.9
    prob["DESIGN.balance.turb_PR"] = 4.46138725662
    prob["DESIGN.fc.balance.Pt"] = 14.6955113159
    prob["DESIGN.fc.balance.Tt"] = 518.665288153
    for k, p in enumerate(od):
        pt = f"OD{k}"
        prob[pt + ".balance.W"] = 166.073 * thrust / 52488.9
        prob[pt + ".balance.FAR"] = 0.01680
        prob[pt + ".balance.Nmech"] = nmech * 1.0158
        prob[pt + ".fc.balance.Pt"] = 15.703
        prob[pt + ".fc.balance.Tt"] = 558.31
        prob[pt + ".turb.PR"] = 4.6690

    import numpy as np
    old = np.seterr(all="ignore")
    try:
        prob.run_model()
    finally:
        np.seterr(**old)

    def g(name, units=None):
        return float(prob.get_val(name, units=units)[0]) if units else float(prob[name][0])

    vals: Dict[str, Any] = {}
    conv = True
    for k, pt in enumerate(["DESIGN"] + [f"OD{i}" for i in range(len(od))]):
        pre = "" if k == 0 else f"od{k - 1}_"
        tsfc = g(pt + ".perf.TSFC")
        fn = g(pt + ".perf.Fn", "N")
        target = thrust if k == 0 else od[k - 1]["thrust_N"]
        ok = math.isfinite(fn) and abs(fn - target) <= 1e-3 * target
        conv = conv and ok
        vals.update({
            pre + "W_kg_s": round(g(pt + ".inlet.Fl_O:stat:W", "kg/s"), 5),
            pre + "FAR": round(g(pt + ".balance.FAR"), 8),
            pre + "OPR": round(g(pt + ".perf.OPR"), 5),
            pre + "Fg_N": round(g(pt + ".perf.Fg", "N"), 2),
            pre + "Fn_N": round(fn, 2),
            pre + "TSFC_lbm_lbf_h": round(tsfc, 7),
            pre + "TSFC_g_kN_s": round(tsfc * _TSFC_SI, 5),
            pre + "Tt3_K": round(g(pt + ".comp.Fl_O:tot:T", "K"), 3),
            pre + "Tt4_K": round(g(pt + ".burner.Fl_O:tot:T", "K"), 3),
            pre + "converged": 1.0 if ok else 0.0,
        })
        if k == 0:
            vals["turb_PR"] = round(g(pt + ".balance.turb_PR"), 6)
        else:
            vals[pre + "Nmech_rpm"] = round(g(pt + ".balance.Nmech", "rpm"), 3)
            vals[pre + "Wc_map_ratio"] = round(g(pt + ".comp.Wc") / max(g("DESIGN.comp.Wc"), 1e-12), 4)
    vals["converged"] = 1.0 if conv else 0.0
    notes = ["generic AXI5/LPT2269 maps scaled at design, no bleed/cooling/offtake: a cycle "
             "estimate, not your engine's certified performance"]
    if not conv:
        notes.insert(0, "NOT CONVERGED: a thrust balance is off by more than 0.1 %")
    head = (f"turbojet: W {vals['W_kg_s']} kg/s, TSFC {vals['TSFC_g_kN_s']} g/(kN s), "
            f"OPR {vals['OPR']}, Tt3 {vals['Tt3_K']} K, {len(od)} off-design point(s)")
    res = result(vals, inp.get("limits"), head, notes=notes)
    if not conv:
        res["verdict"] = "FAIL"
    return res


_K = 5.0 / 9.0
REFERENCES = [
    {   # pyCycle benchmark_simple_turbojet.py, DESIGN and OD0 (SLS, 11000 lbf), converted to SI
        "name": "pycycle_simple_turbojet_benchmark", "kind": "turbojet",
        "inp": {"thrust_N": 11800.0 * LBF, "T4_K": 2370.0 * _K, "comp_PR": 13.5,
                "comp_eff": 0.83, "turb_eff": 0.86, "alt_m": 0.0, "mach": 1e-6,
                "off_design": [{"alt_m": 0.0, "mach": 1e-6, "thrust_N": 11000.0 * LBF}]},
        "expect": {"converged": [1.0, 1e-9],
                   "W_kg_s": [147.333 * LBM, 1e-4], "OPR": [13.5, 1e-5],
                   "FAR": [0.01776487, 1e-4], "turb_PR": [3.8591364, 1e-4],
                   "TSFC_lbm_lbf_h": [0.7985165, 1e-4], "Tt3_K": [1187.7610184 * _K, 1e-4],
                   "od0_W_kg_s": [142.786698 * LBM, 1e-4], "od0_OPR": [12.8588424, 1e-4],
                   "od0_FAR": [0.01676938946, 1e-4], "od0_Nmech_rpm": [7943.9331210, 1e-4],
                   "od0_TSFC_lbm_lbf_h": [0.783636794, 1e-4],
                   "od0_Tt3_K": [1168.067147267 * _K, 1e-4]},
    },
]
