"""dedupe: a same-version copy elsewhere is found and offered, another version is only reported,
the workshop's own copy is not counted as a duplicate. Fake folders only, the real PC is not
searched. Sentinel: DEDUPE_TEST_OK
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
import dedupe as dd  # noqa: E402


def touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def main() -> int:
    assert dd.product_version("tools/FreeCAD-1.1.4") == ("freecad", "1.1.4")
    assert dd.product_version("tools/GMAT-R2026a")[0] == "gmat"
    assert dd.product_version("pixi/bin") == ("bin", "")
    t = Path(tempfile.mkdtemp(prefix="mcpfc_dedupe_"))
    try:
        root = t / "drive"
        home = t / "home"
        entry = {"dest": "tools/FreeCAD-1.1.4", "bin": "bin/freecadcmd.exe"}
        touch(root / "Apps" / "FreeCAD 1.1.4" / "bin" / "freecadcmd.exe")
        touch(root / "FreeCAD" / "bin" / "freecadcmd.exe")                       # unknown version
        touch(home / "tools" / "FreeCAD-1.1.4" / "bin" / "freecadcmd.exe")       # the workshop's own
        found = dd.find_existing(entry, home, roots=[root, t])
        paths = [f["path"] for f in found]
        assert found and found[0]["match"] == "same" and "1.1.4" in found[0]["path"], found
        assert any(f["match"] == "other" for f in found), found
        assert not any(str(home) in p for p in paths), "the workshop copy is not a duplicate"
        print("same version found first, other version reported, own copy ignored OK")
        assert dd.find_existing({"dest": "tools/Renode-1.17.0", "bin": "renode.exe"}, home, roots=[root]) == []
        print("absent tool: nothing found OK")
        assert dd.same_volume(t / "a", t / "b")
        print("same-volume check OK")
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print("DEDUPE_TEST_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
