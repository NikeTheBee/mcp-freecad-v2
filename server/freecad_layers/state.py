"""Project memory — persistent design state (cahier des charges §7.1).

Goal: never re-describe the project across sessions. The AI reads a compact
state at session start and updates it after each *validated* step. Stored as a
single JSON file under /project_state so it is versionable and diff-friendly.

Robustness (audit 2026-10-11, §3.6):
- every read-modify-write holds an exclusive file lock, so two sessions
  (Claude Code + Claude Desktop, two tabs) cannot lose each other's updates;
- writes are atomic (temp file + replace) and keep the previous good version
  as state.json.bak; a corrupt state.json is recovered from it instead of
  breaking the session start;
- older/partial files are migrated (missing keys get their defaults);
- summary() is bounded, whatever the size of the project.

Pure Python (no FreeCAD import): usable from freecadcmd or system Python.
"""
import contextlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from . import project_dir

STATE_VERSION = 2
LOCK_TIMEOUT_S = 30.0


class StateCorrupted(RuntimeError):
    """state.json and its backup are both unreadable."""


def _state_path() -> Path:
    d = project_dir() / "project_state"
    d.mkdir(parents=True, exist_ok=True)
    return d / "state.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _empty(project: str = "untitled") -> Dict[str, Any]:
    return {
        "state_version": STATE_VERSION,
        "project": project,
        "updated": _now(),
        "intent": "",
        "constraints": [],
        "parameters": {},
        "features": [],     # [{name, type, params, at}]
        "decisions": [],    # [{text, at}]
        "checkpoints": [],  # [{name, file, at, note}] — maintained by checkpoint.py
        "specs": [],        # design contract — maintained by spec.py
    }


def _migrate(s: Dict[str, Any]) -> Dict[str, Any]:
    """Fill keys missing from older or hand-edited files with their defaults."""
    if not isinstance(s, dict):
        raise ValueError("state root is not an object")
    for k, v in _empty(s.get("project") or "untitled").items():
        if k not in s or not isinstance(s[k], type(v)):
            s[k] = v
    s["state_version"] = STATE_VERSION
    return s


# -- locking ----------------------------------------------------------------

@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """Exclusive inter-process lock on project_state/state.json.lock."""
    lock_path = _state_path().with_suffix(".json.lock")
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"project state locked for more than "
                                       f"{LOCK_TIMEOUT_S:.0f}s: {lock_path}")
                time.sleep(0.01)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


# -- read / write -----------------------------------------------------------

def _read(p: Path) -> Dict[str, Any]:
    with p.open("r", encoding="utf-8") as f:
        return _migrate(json.load(f))


def load() -> Dict[str, Any]:
    """Load state (migrated), a fresh empty state if none exists yet, or the
    last good backup if state.json is corrupt (flagged in `_recovered`).
    Takes the lock: on Windows an open reader would make a writer's atomic
    replace fail."""
    with _locked():
        return _load_nolock()


def _load_nolock() -> Dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return _empty()
    try:
        return _read(p)
    except (ValueError, OSError) as e:
        bak = p.with_suffix(".json.bak")
        try:
            s = _read(bak)
        except (ValueError, OSError):
            raise StateCorrupted(f"{p} is unreadable ({e}) and no usable backup "
                                 f"exists at {bak}") from e
        s["_recovered"] = f"state.json was corrupt ({type(e).__name__}); loaded backup"
        return s


def _write(state: Dict[str, Any]) -> None:
    p = _state_path()
    data = {k: v for k, v in state.items() if not k.startswith("_")}
    data["updated"] = _now()
    tmp = p.with_name(f"state.json.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    if p.exists():
        try:  # keep the previous version only if it is itself readable
            _read(p)
            shutil.copyfile(p, p.with_suffix(".json.bak"))
        except (ValueError, OSError):
            pass
    os.replace(tmp, p)
    state["updated"] = data["updated"]


def save(state: Dict[str, Any]) -> None:
    """Atomic, locked write: a crash mid-write must never corrupt the project
    memory (NF2 — the memory IS the recovery mechanism)."""
    with _locked():
        _write(state)


def update(fn: Callable[[Dict[str, Any]], Any]) -> Any:
    """Locked read-modify-write: `fn(state)` mutates the state in place."""
    with _locked():
        s = _load_nolock()
        out = fn(s)
        _write(s)
        return out


# -- convenience mutators (all locked) ----------------------------------------

def init_project(name: str, intent: str = "") -> Dict[str, Any]:
    s = _empty(name)
    s["intent"] = intent
    save(s)
    return s


def set_intent(intent: str) -> None:
    update(lambda s: s.__setitem__("intent", intent))


def add_constraint(text: str) -> None:
    def fn(s):
        if text not in s["constraints"]:
            s["constraints"].append(text)
    update(fn)


def set_parameter(key: str, value: Any) -> None:
    update(lambda s: s["parameters"].__setitem__(key, value))


def record_feature(name: str, ftype: str, params: Optional[Dict[str, Any]] = None) -> None:
    """Record a validated feature. Replaces any prior entry with the same name."""
    def fn(s):
        s["features"] = [f for f in s["features"] if f.get("name") != name]
        s["features"].append({"name": name, "type": ftype, "params": params or {},
                              "at": _now()})
    update(fn)


def add_decision(text: str) -> None:
    update(lambda s: s["decisions"].append({"text": text, "at": _now()}))


# -- token-efficient read ---------------------------------------------------

def _tail(items: List[str], n: int) -> str:
    if len(items) <= n:
        return ", ".join(items)
    return f"... {len(items) - n} earlier, " + ", ".join(items[-n:])


def summary(max_items: int = 15) -> str:
    """Compact text digest for reading at session start (bounded size)."""
    s = load()
    lines: List[str] = [f"Project: {s['project']} (updated {s['updated']})"]
    if s.get("_recovered"):
        lines.append(f"WARNING: {s['_recovered']}")
    if s.get("intent"):
        lines.append(f"Intent: {s['intent'][:300]}")
    if s.get("constraints"):
        lines.append("Constraints: " + "; ".join(s["constraints"][-max_items:]))
    if s.get("specs"):
        targets = list(dict.fromkeys(str(x.get("target")) for x in s["specs"]))
        lines.append(f"Design contract: {len(s['specs'])} requirement(s) on "
                     + ", ".join(targets[:max_items]) + " - check(<target>) evaluates them")
    if s.get("parameters"):
        items = [f"{k}={v}" for k, v in s["parameters"].items()]
        lines.append("Params: " + _tail(items, max_items))
    if s.get("features"):
        feats = [f"{f['name']}({f['type']})" for f in s["features"]]
        lines.append(f"Features ({len(feats)}): " + _tail(feats, max_items))
    if s.get("checkpoints"):
        lines.append("Checkpoints: " + _tail([c["name"] for c in s["checkpoints"]], max_items))
    if s.get("decisions"):
        lines.append(f"Decisions logged: {len(s['decisions'])} "
                     f"(latest: {s['decisions'][-1]['text'][:200]})")
    return "\n".join(lines)
