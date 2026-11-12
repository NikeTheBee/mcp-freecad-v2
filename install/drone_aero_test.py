"""Drone analytical aero test (§4.3 — drone counterpart of rocket_cp_test).

Validates the momentum-theory sizing helpers in server/freecad_layers/aero.py
against a realistic 450 mm quadcopter and a small fixed wing. Pure Python —
runs under freecadcmd or system Python. Sentinel: DRONE_AERO_OK
"""
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))

from freecad_layers import aero  # noqa: E402


def main() -> int:
    # 450 mm quad: 1.2 kg AUW, 4 motors, 10" (0.254 m) props, motors 8 N max
    # each, 3S 5000 mAh ≈ 55.5 Wh.
    r = aero.check_multirotor(mass_kg=1.2, n_motors=4, prop_diameter_m=0.254,
                              max_thrust_per_motor_n=8.0, battery_wh=55.5)
    print(r)
    assert r["ok"], f"quad should be viable: {r['issues']}"
    assert abs(r["hover_thrust_per_motor_n"] - 2.94) < 0.05, r
    assert 2.5 < r["thrust_to_weight"] < 3.0, r        # 32 N / 11.77 N ≈ 2.72
    assert 50 < r["disk_loading_n_m2"] < 100, r        # ≈ 58 N/m² (efficient)
    assert 60 < r["hover_power_w"] < 120, r            # ≈ 82 W mech
    assert 15 < r["flight_time_min"] < 40, r           # ≈ 28 min

    v = aero.verdict_multirotor(1.2, 4, 0.254, 8.0, 55.5)
    print(v)
    assert v.startswith("OK"), v

    # Undersized check must fail: 2 kg on the same setup with weak 4 N motors.
    bad = aero.check_multirotor(2.0, 4, 0.254, 4.0)
    assert not bad["ok"] and any("cannot hover" in i for i in bad["issues"]), bad

    # Tip Mach: 10" prop at 12000 rpm ≈ 0.47 — subsonic, sane.
    m = aero.tip_mach(12000, 0.254)
    assert 0.4 < m < 0.55, m

    # Fixed wing: 1.5 kg, 0.30 m² wing → V_stall ≈ 7.8 m/s; loading ≈ 49 N/m².
    vs = aero.stall_speed(1.5, 0.30)
    assert 7.0 < vs < 8.5, vs
    wl = aero.wing_loading(1.5, 0.30)
    assert 45 < wl < 55, wl
    print(f"fixed wing: V_stall={vs:.1f} m/s, loading={wl:.0f} N/m²")

    # audit A1: meaningless inputs are refused with a clear ValueError
    for bad in [(1.0, 0, 0.25, 10), (1.0, 4, 0.0, 10), (-2.0, 4, 0.25, 10)]:
        try:
            aero.verdict_multirotor(*bad)
            print(f"FAIL: accepted {bad}")
            return 1
        except ValueError as e:
            print(f"refused {bad}: {e}")
    # exact values (hand computation), not only plausibility windows
    t = 1.2 * 9.81 / 4
    a = math.pi * 0.127 ** 2
    p_exact = 4 * t ** 1.5 / math.sqrt(2 * 1.225 * a) / 0.7
    assert abs(aero.hover_power(1.2, 0.254, 4) - p_exact) < 1e-9, p_exact
    assert abs(aero.flight_time_min(55.5, p_exact) - 55.5 * 0.8 * 0.85 / p_exact * 60) < 1e-9
    assert abs(aero.disk_loading(1.2, 0.254, 4) - 1.2 * 9.81 / (4 * a)) < 1e-9
    assert abs(vs - math.sqrt(2 * 1.5 * 9.81 / (1.225 * 0.30 * 1.3))) < 1e-12, vs
    assert abs(m - math.pi * 0.254 * 200 / 340.3) < 1e-12, m

    # units trap (the CAD model is in mm): a prop in mm or inches, a wing area in
    # mm² used to give "OK ... hover=0.1W" and a 0.008 m/s stall speed.
    for d in (254.0, 10.0):
        v = aero.verdict_multirotor(1.2, 4, d, 8.0)
        assert v.startswith("FAIL") and "metres" in v, v
    for call in (lambda: aero.stall_speed(1.5, 300000.0),
                 lambda: aero.wing_loading(1.5, 300000.0)):
        try:
            call()
            print("FAIL: wing area in mm² accepted")
            return 1
        except ValueError as e:
            print(f"refused: {e}")
    print("DRONE_AERO_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.stdout.flush()  # freecadcmd drops unflushed output on sys.exit
    sys.exit(rc)
