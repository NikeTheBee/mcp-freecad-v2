"""Reference cases of the fast `sim`-environment adapters, through the real runner.

A kind only runs once its reference cases pass (FEUILLE_DE_ROUTE_ATELIER.md §3.7). Those checks
normally happen at install time or on first use; this test keeps the fast ones in the suite, so a
change to the solvers, to the lock or to the runner is caught here rather than in a design run.

Covered: `aeroelastic` (flutter, divergence, gust, fin flutter, AIC), `pogo` (feedline, closed
loop, accumulator) and `draw` (image and SVG tracing) -- all in the `sim` environment, all a few
seconds in total. The heavy adapters (space, gazebo, sitl, cfd...) stay out of the suite on
purpose: the installer runs them.

Goes through `server.atelier.runner.validate`, so the subprocess launch, the environment, the
network guard and the validation store are all exercised.

Needs the workshop installed (MCPFC_HOME or ~/.freecad-mcp/atelier_home), otherwise prints
REFCASES_SIM_SKIP. Sentinel: REFCASES_SIM_OK
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

ADAPTERS = ("aeroelastic", "pogo", "draw")


def _home() -> str | None:
    h = os.environ.get("MCPFC_HOME")
    if h:
        return h
    try:
        return (Path.home() / ".freecad-mcp" / "atelier_home").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def main() -> int:
    home = _home()
    if not home or not Path(home).is_dir():
        print("REFCASES_SIM_SKIP workshop not installed (no MCPFC_HOME): "
              "run install\\install.ps1 -Packs socle")
        return 0
    os.environ.setdefault("MCPFC_HOME", home)
    from server.atelier import env_python
    from server.atelier.runner import validate

    if env_python("sim") is None:
        print("REFCASES_SIM_SKIP the `sim` environment is not installed")
        return 0

    bad = []
    for adapter in ADAPTERS:
        res = validate(adapter, "sim")
        cases = res.get("cases") or []
        failing = [c.get("name") for c in cases if not c.get("ok")]
        print(f"[{res.get('verdict')}] {adapter}: {res.get('summary', '')}")
        for c in cases:
            if not c.get("ok"):
                print(f"    FAIL {c.get('name')}: {c.get('error', '')}")
                for ch in c.get("checks") or []:
                    if not ch.get("ok"):
                        print(f"        {ch['key']}: got {ch['got']!r}, expected "
                              f"{ch['expected']!r} +/- {ch['rel_tol']}")
        if res.get("verdict") != "OK" or failing or not cases:
            bad.append(adapter)
    if bad:
        print(f"!! reference cases failed: {', '.join(bad)}")
        return 1
    print("REFCASES_SIM_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
