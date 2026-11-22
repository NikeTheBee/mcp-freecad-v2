"""The installer's download-size preflight (install/atelier_install.py): it must print sizes and
ask before fetching anything, and never hang when there is no one to answer. Pure Python, no
network, no FreeCAD. Sentinel: PREFLIGHT_OK
"""
import io
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
import atelier_install as ai  # noqa: E402

HOME = Path(__file__).resolve().parent.parent / "install"  # any existing dir; nothing is written


def say_capture():
    out = []
    return out, (lambda msg: out.append(msg))


def test_dry_run_never_asks():
    out, fn = say_capture()
    with mock.patch.object(ai, "say", fn), mock.patch("builtins.input", side_effect=AssertionError("asked")):
        ai.preflight(["socle"], ["sim"], ["freecad"], HOME, assume_yes=False, dry=True)
    assert any("total new download" in m for m in out), out
    print("dry run prints the banner and never calls input() OK")


def test_yes_flag_never_asks():
    with mock.patch("builtins.input", side_effect=AssertionError("asked")):
        ai.preflight(["socle"], ["sim"], ["freecad"], HOME, assume_yes=True, dry=False)
    print("--yes skips the question OK")


def test_non_interactive_proceeds_without_hanging():
    """Piped stdin (CI, `... | tool`): isatty() is False, so it must proceed rather than block
    forever on input() with nobody to answer."""
    with mock.patch("sys.stdin", io.StringIO("")), mock.patch("builtins.input",
                                                               side_effect=AssertionError("asked")):
        ai.preflight(["socle"], ["sim"], ["freecad"], HOME, assume_yes=False, dry=False)
    print("non-interactive (piped) stdin proceeds without asking OK")


def test_interactive_no_cancels():
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch("sys.stdout.isatty", return_value=True), \
         mock.patch("builtins.input", return_value="n"):
        try:
            ai.preflight(["socle"], ["sim"], ["freecad"], HOME, assume_yes=False, dry=False)
        except SystemExit as e:
            assert "cancelled" in str(e), e
        else:
            raise AssertionError("expected SystemExit on 'n'")
    print("interactive 'n' cancels (SystemExit, no download attempted) OK")


def test_interactive_yes_proceeds():
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch("sys.stdout.isatty", return_value=True), \
         mock.patch("builtins.input", return_value="y"):
        ai.preflight(["socle"], ["sim"], ["freecad"], HOME, assume_yes=False, dry=False)
    print("interactive 'y' proceeds OK")


def test_sizes_are_accounted():
    sizes = {t: v.get("size") for t, v in ai.LOCK.items() if not t.startswith("_")}
    assert sizes.get("pixi") and sizes.get("freecad"), "pixi/freecad need a known size for an honest total"
    assert set(ai.ENV_SIZE_GB) >= {e for k, p in ai.PACKS.items() if not k.startswith("_") for e in p["envs"]}, \
        "every environment any pack can select must have an approximate size"
    print("every tool/env the installer can select has a size for the preflight total OK")


for fn in (test_dry_run_never_asks, test_yes_flag_never_asks, test_non_interactive_proceeds_without_hanging,
          test_interactive_no_cancels, test_interactive_yes_proceeds, test_sizes_are_accounted):
    fn()
print("PREFLIGHT_OK")
