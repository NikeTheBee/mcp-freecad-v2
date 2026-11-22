"""Multi-project test (pure Python, no FreeCAD). Sentinel: PROJECTS_OK

- the original single-project layout keeps working as "default";
- two named projects keep separate memory (and checkpoint/recovery folders);
- creating a project never overwrites one; switching never deletes anything;
- bad names (traversal, device names, empty) and unknown projects are refused.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))

ws = tempfile.mkdtemp(prefix="mcpfc_ws_")
os.environ["MCP_FREECAD_PROJECT_DIR"] = ws
try:
    from freecad_layers import current_project, project_dir, projects, state

    # default = the workspace root, as before
    assert current_project() == "default" and project_dir() == Path(ws)
    state.init_project("Legacy", "the original project")
    state.add_constraint("legacy constraint")
    print("default project = workspace root:", projects.listing())

    # two named projects, separate memory
    projects.create("Bracket", "L bracket 80x30")
    assert current_project() == "Bracket"
    assert project_dir() == Path(ws) / "projects" / "Bracket"
    state.add_constraint("wall >= 3 mm")
    projects.create("Gear-2", "spur gear m1 z20")
    state.add_constraint("module 1")
    assert state.load()["constraints"] == ["module 1"], state.load()["constraints"]
    projects.switch("Bracket")
    s = state.load()
    assert s["project"] == "Bracket" and s["constraints"] == ["wall >= 3 mm"], s
    print("separate memories:", projects.listing())
    assert projects.listing() == "Projects: default, Bracket*, Gear-2", projects.listing()

    # no overwrite, nothing deleted by a switch, default still intact
    try:
        projects.create("Bracket")
    except ValueError as e:
        print("refused:", e)
    else:
        raise AssertionError("existing project overwritten")
    assert state.load()["constraints"] == ["wall >= 3 mm"]
    projects.switch("default")
    assert state.load()["project"] == "Legacy" and state.load()["constraints"] == ["legacy constraint"]
    projects.switch("Gear-2")
    assert state.load()["constraints"] == ["module 1"]
    print("no overwrite, switching keeps every project: OK")

    # bad names / unknown projects
    for bad in ("../evil", "a/b", "", " ", "con", "LPT1", "-x", "x" * 41, "default"):
        try:
            projects.create(bad)
        except ValueError as e:
            print("refused name", repr(bad), "->", str(e)[:50])
        else:
            raise AssertionError(f"accepted project name {bad!r}")
    for bad in ("nope", "../.."):
        try:
            projects.switch(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"switched to {bad!r}")
    assert not (Path(ws).parent / "evil").exists()

    # a tampered pointer to a missing folder falls back to default, not a crash
    outside = Path(ws) / "outside"
    outside.mkdir()                                  # exists, but is not under /projects
    for evil in ('{"name": "../outside"}', '{"name": "../../etc"}', '{"name": 3}', "not json"):
        (Path(ws) / "projects" / "current.json").write_text(evil, encoding="utf-8")
        assert current_project() == "default", evil
    print("bad names refused, tampered pointer falls back to default: OK")
finally:
    shutil.rmtree(ws, ignore_errors=True)
print("PROJECTS_OK")
