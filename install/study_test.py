"""The study loop (server/atelier/study.py) without FreeCAD: states, history, measurements.

Run with: py -3 install/study_test.py      Sentinel: STUDY_OK
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from atelier import study as S  # noqa: E402

d = Path(tempfile.mkdtemp(prefix="mcpfc_study_"))
try:
    S.start(d, "t", "goal", criteria={"k": {"v": {"min": 1}}}, params={"a": 1},
            acceptance={"q": {"min": 0}}, agreement=0.1)
    S.record_result(d, "t", "k", {"verdict": "FAIL", "summary": "low", "values": {"v": 0.5}})
    assert S.load(d, "t")["state"] == "design"
    S.new_iteration(d, "t", "first change", {"a": 2})
    S.record_result(d, "t", "k", {"verdict": "OK", "summary": "ok", "values": {"v": 2.0, "q": 10.0}})
    assert S.load(d, "t")["state"] == "ready_to_build"
    S.new_iteration(d, "t", "second change", {"a": 3})
    s = S.load(d, "t")
    # each iteration keeps ITS parameters (a shared reference showed the newest everywhere)
    assert [h["params"]["a"] for h in s["history"]] == [1, 2] and s["params"]["a"] == 3, s["history"]
    assert s["history"][0]["results"]["k"]["verdict"] == "FAIL", s["history"][0]
    print("history keeps each iteration's own parameters and results OK")
    S.record_result(d, "t", "k", {"verdict": "OK", "summary": "ok", "values": {"v": 2.0, "q": 10.0}})
    S.mark_built(d, "t", "made")
    r = S.measure(d, "t", {"q": 10.5}, "scale", {"q": "k.q"})
    assert r["state"] == "validated", r
    r = S.measure(d, "t", {"q": 13.0}, "scale", {"q": "k.q"})
    assert r["state"] == "calibrate" and "calibrate" in r["next"], r
    print("measure: agreement -> validated, 30 % off -> calibrate OK")
    rep = S.report(d, "t").read_text(encoding="utf-8")
    assert '"a": 1' in rep.split("## Iteration 2")[0] and "## Iteration 3" in rep, rep
    print("report lists every iteration with its own parameters OK")
    lines = (d / "studies" / "t" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert all(json.loads(x)["t"] for x in lines) and len(lines) >= 9, len(lines)
finally:
    shutil.rmtree(d, ignore_errors=True)
print("STUDY_OK")
