"""Project-memory robustness (audit 2026-10-11, §2.2 S1/S2) — system Python.

Concurrent writers lose nothing, a corrupt state.json is recovered from its
backup, old files are migrated, and the summary stays bounded.
Runs without FreeCAD. Sentinel: STATE_OK
"""
import json
import multiprocessing as mp
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))

N_PER_WRITER = 150


def _writer(tag: str, project_dir: str) -> None:
    os.environ["MCP_FREECAD_PROJECT_DIR"] = project_dir
    sys.path.insert(0, str(REPO / "server"))
    from freecad_layers import state
    for i in range(N_PER_WRITER):
        state.record_feature(f"{tag}_{i:03d}", "Test")


def _reader(project_dir: str) -> None:
    """Reads continuously while the writers write (Windows: an open reader
    used to make the writer's atomic replace fail)."""
    os.environ["MCP_FREECAD_PROJECT_DIR"] = project_dir
    sys.path.insert(0, str(REPO / "server"))
    from freecad_layers import state
    for _ in range(300):
        assert state.summary().startswith("Project: Concurrent")


def main() -> int:
    tmp = tempfile.TemporaryDirectory(prefix="mcpfc_state_")
    os.environ["MCP_FREECAD_PROJECT_DIR"] = tmp.name
    from freecad_layers import state

    # 1. two processes writing at the same time: no lost update
    state.init_project("Concurrent")
    procs = [mp.Process(target=_writer, args=(t, tmp.name)) for t in ("A", "B")]
    procs.append(mp.Process(target=_reader, args=(tmp.name,)))
    [p.start() for p in procs]
    [p.join(120) for p in procs]
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
    n = len(state.load()["features"])
    print(f"concurrent writers: {n}/{2 * N_PER_WRITER} features kept")
    assert n == 2 * N_PER_WRITER, n

    # 2. truncated state.json -> recovered from the backup, session still starts
    p = Path(tmp.name) / "project_state" / "state.json"
    p.write_text('{"project": "Concurrent", "features": [', encoding="utf-8")
    s = state.summary()
    print(s.splitlines()[1])
    assert "WARNING" in s and "Concurrent" in s, s
    state.add_decision("written after recovery")  # rewrites a clean file
    assert json.loads(p.read_text(encoding="utf-8"))["decisions"][-1]["text"] \
        == "written after recovery"
    print("corrupt state recovered from backup: OK")

    # 3. both copies corrupt -> clear error, never a silent empty project
    p.write_text("{", encoding="utf-8")
    p.with_suffix(".json.bak").write_text("{", encoding="utf-8")
    try:
        state.load()
        print("FAIL: corrupt state and backup accepted silently")
        return 1
    except state.StateCorrupted as e:
        print("unrecoverable state reported clearly: OK")

    # 4. old / partial schema is migrated
    p.write_text('{"project": "old"}', encoding="utf-8")
    state.record_feature("A", "x")
    s = state.load()
    assert s["project"] == "old" and s["features"][0]["name"] == "A" and s["specs"] == []
    print("old schema migrated: OK")

    # 5. bounded summary on a big project
    state.init_project("Big")
    big = state.load()
    big["features"] = [{"name": f"F{i:04d}", "type": "PartDesign::Pad", "params": {},
                        "at": "x"} for i in range(1000)]
    big["decisions"] = [{"text": "d" * 500, "at": "x"} for _ in range(200)]
    state.save(big)
    sm = state.summary()
    print(f"summary for 1000 features: {len(sm)} chars")
    assert len(sm) < 1500, len(sm)

    tmp.cleanup()
    print("STATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
