"""Gazebo Jetty (gz-sim 10, sdformat 15) headless, loopback only (env `ros`).

Kinds:
- gz_underwater  server-only gz sim (`gz sim -s -r`, no GUI) of an SDF world generated here: one
                 vehicle (box / cylinder / sphere hull, or a model file of `work`), Buoyancy
                 (uniform fluid density, no free surface), Hydrodynamics (linear and quadratic
                 damping, current), added mass (SDF <fluid_added_mass>), optional Thrusters with a
                 fixed thrust. The pose is recorded by gz's own log recorder into `work`
                 (state.tlog, SQLite) and read back: final depth, vertical velocity, terminal
                 velocity, time constant, attitude drift, horizontal drift.
- convert_check  URDF -> SDF with sdformat (`gz sdf -p`), SDF re-checked (`gz sdf -k`) and re-printed
                 (SDF -> SDF); compared with the URDF read directly: total mass, each link's mass,
                 centre of mass and inertia, joint count and types. Fixed links lumped by sdformat
                 are listed and their lumped inertia is checked against the exact composite.
                 Optional third loader: MuJoCo (env `robot`, through files only), which also fuses
                 fixed links silently. FAIL when any mass/inertia differs beyond `tol`.

Units: SI (m, kg, s, N, kg m^2, rad; angles in params ending in _deg are degrees).
Depth is positive downwards (z = -depth); velocities are world frame, vz positive UP.
Added mass and damping are given POSITIVE (physical size); they are written with the SNAME
signs gz expects (X_u = -c). Vector order [surge, sway, heave, roll, pitch, yaw] = x y z p q r.

Network (SECURITY.md, roadmap §10.1.4): gz-transport is used with GZ_IP=127.0.0.1, a unique
GZ_PARTITION, the zenoh backend restricted to tcp/127.0.0.1 with multicast scouting off (the
ZeroMQ backend aborts on this machine: wepoll "Bad file descriptor"), and the legacy discovery
ports pointed at ports Windows refuses to bind (excluded port ranges), so that discovery, which
always binds 0.0.0.0, stays closed. Every socket of the started process tree is sampled with
psutil during the run: any non-loopback address -> the run is killed and the answer is UNKNOWN.
Remote model URIs (http, fuel) are refused. The process tree started here is killed in `finally`.

Exactness: REFERENCES below (closed-form answers). Limits: rigid bodies; buoyancy from the
collision volume in an unbounded uniform fluid (no surface, no waves); Fossen-type lumped
hydrodynamics; DART integrator (semi-implicit, error O(dt)).
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import socket
import sqlite3
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from _common import ENV_PREFIX, env_exe, result

ENV = "ros"
KINDS = {
    "gz_underwater": {"geometry": False, "timeout": 900,
                      "doc": "headless gz sim of a vehicle in water (buoyancy, damping, added mass, thrusters): depth, terminal velocity, attitude drift"},
    "convert_check": {"geometry": False, "timeout": 300,
                      "doc": "URDF -> SDF (gz sdf) [+ MuJoCo]: masses, inertias, joints compared; lumped fixed links listed"},
}
LIB = ENV_PREFIX / "Library"
MAX_ITER = 2_000_000
_AXES = ("x", "y", "z", "p", "q", "r")
_HYD_LIN = ("xU", "yV", "zW", "kP", "mQ", "nR")
_HYD_QUAD = ("xUabsU", "yVabsV", "zWabsW", "kPabsP", "mQabsQ", "nRabsR")


def _gz_bin(rel: str) -> str:
    """A gz binary of THIS environment (what `gz sim` / `gz sdf` dispatch to), never from PATH.
    The `gz` front-end is a Ruby script that re-execs these: called directly, the process tree
    stays supervisable and no shell is involved."""
    p = LIB / "libexec" / "gz" / rel
    if not p.is_file():
        raise FileNotFoundError(f"{rel} is not installed in the environment {ENV_PREFIX.name}")
    return str(p)


def _sim_exe() -> str:
    return _gz_bin("sim10/gz-sim-main.exe" if os.name == "nt" else "sim10/gz-sim-main")


def _sdf_exe() -> str:
    return _gz_bin("sdformat15/gz-sdformat-sdf.exe" if os.name == "nt" else "sdformat15/gz-sdformat-sdf")


def _flags() -> int:
    return 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW


def _base_env(home: Path) -> Dict[str, str]:
    env = dict(os.environ)
    env.pop("GZ_RELAY", None)
    (home / "fuel").mkdir(parents=True, exist_ok=True)
    blocked = "http://127.0.0.1:9"
    env.update(
        PATH=os.pathsep.join([str(LIB / "bin"), str(ENV_PREFIX), env.get("PATH", "")]),
        GZ_CONFIG_PATH=str(LIB / "share" / "gz"),
        GZ_SIM_SYSTEM_PLUGIN_PATH=str(LIB / "lib" / "gz-sim-10" / "plugins"),
        GZ_SIM_PHYSICS_ENGINE_PATH=str(LIB / "lib" / "gz-physics-9" / "engine-plugins"),
        GZ_TRANSPORT_LOG_SQL_PATH=str(LIB / "share" / "gz" / "gz-transport" / "sql"),
        AMENT_PREFIX_PATH=str(LIB), CONDA_PREFIX=str(ENV_PREFIX),
        GZ_HOMEDIR=str(home), HOME=str(home), GZ_FUEL_CACHE_PATH=str(home / "fuel"),
        # libcurl (Fuel downloads) reads the lower-case names; "*" in NO_PROXY would bypass them
        http_proxy=blocked, https_proxy=blocked, all_proxy=blocked, HTTP_PROXY=blocked,
        HTTPS_PROXY=blocked, ALL_PROXY=blocked, NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost")
    return env


def _version_of(exe: str) -> str:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30,
                             env=_base_env(Path(os.environ.get("TEMP", ".")) / "mcpfc_gz_home"),
                             creationflags=_flags())
        m = re.findall(r"^\s*(\d+\.\d+\.\d+)\s*$", out.stdout, re.M)
        return m[-1] if m else "?"
    except Exception as e:  # noqa: BLE001
        return f"missing ({type(e).__name__})"


def versions() -> Dict[str, str]:
    out = {"gz-sim": _version_of(_sim_exe()), "sdformat": _version_of(_sdf_exe())}
    try:
        import google.protobuf
        out["protobuf"] = google.protobuf.__version__
    except Exception:  # noqa: BLE001
        pass
    out["numpy"] = np.__version__
    return out


# ------------------------------------------------------------------ loopback-only transport
def _refused_udp(port: int) -> bool:
    """True when Windows refuses ANY bind of this UDP port (excluded port range, WSAEACCES).
    Probed on 127.0.0.1 only: nothing is ever bound on another interface here."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError as e:
        return getattr(e, "winerror", None) == 10013 or e.errno == 13
    finally:
        s.close()


def _closed_discovery_ports() -> List[int]:
    """Two UDP ports inside an excluded range (runs of >= 8 refused ports, so not a port that
    one other program merely holds exclusively)."""
    if os.name != "nt":
        return []
    runs: List[List[int]] = []
    for p in range(49152, 65536):
        if _refused_udp(p):
            if runs and runs[-1][1] == p - 1:
                runs[-1][1] = p
            else:
                runs.append([p, p])
    for lo, hi in sorted(runs, key=lambda r: r[0] - r[1]):
        if hi - lo + 1 >= 8:
            mid = (lo + hi) // 2
            return [mid, mid + 1]
    return []


def _transport_env(gzdir: Path) -> Tuple[Dict[str, str], Optional[str]]:
    ports = _closed_discovery_ports()
    if len(ports) < 2:
        return {}, ("gz-transport's discovery always binds UDP on 0.0.0.0 and no unbindable "
                    "port was found to park it on: refused to start gz (non-loopback socket)")
    cfg = gzdir / "zenoh.json5"
    cfg.write_text('{\n mode: "peer",\n listen: { endpoints: ["tcp/127.0.0.1:0"] },\n'
                   ' connect: { endpoints: [] },\n'
                   ' scouting: { multicast: { enabled: false }, gossip: { enabled: false } },\n}\n',
                   encoding="utf-8")
    env = _base_env(gzdir / "home")
    env.update(GZ_IP="127.0.0.1", GZ_PARTITION="mcpfc_" + uuid.uuid4().hex[:12],
               GZ_TRANSPORT_IMPLEMENTATION="zenoh", ZENOH_CONFIG=str(cfg),
               GZ_DISCOVERY_MSG_PORT=str(ports[0]), GZ_DISCOVERY_SRV_PORT=str(ports[1]))
    return env, None


def _loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_loopback
    except ValueError:
        return False


def _sockets(pid: int) -> Tuple[List[str], List[str], bool]:
    """(all endpoints, non-loopback endpoints, inspected) of the process tree rooted at pid."""
    import psutil
    seen, bad = [], []
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        return seen, bad, False
    ok = False
    for q in procs:
        try:
            conns = (q.net_connections if hasattr(q, "net_connections") else q.connections)(kind="inet")
            ok = True
        except psutil.NoSuchProcess:
            continue
        except psutil.Error:
            return seen, bad + [f"{q.name()}: sockets not inspectable"], True
        for c in conns:
            proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"
            for a in (c.laddr, c.raddr):
                if a and a.ip:
                    tag = f"{proto} {a.ip}:{a.port}"
                    seen.append(tag)
                    if not _loopback(a.ip):
                        bad.append(tag)
    return seen, bad, ok


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process started here and its children (only those)."""
    try:
        import psutil
        try:
            root = psutil.Process(proc.pid)
            kids = root.children(recursive=True)
        except psutil.Error:
            kids = []
        for c in kids:
            try:
                c.kill()
            except psutil.Error:
                pass
    except Exception:  # noqa: BLE001
        pass
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(10)
    except Exception:  # noqa: BLE001
        pass


def _run_supervised(args: List[str], cwd: Path, env: Dict[str, str], timeout: float,
                    log: Path) -> Dict[str, Any]:
    """Run gz, sampling its sockets; stop at the first non-loopback one."""
    sockets_seen: set = set()
    violation: List[str] = []
    samples = 0
    reason = None
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(args, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=fh,
                                stderr=subprocess.STDOUT, creationflags=_flags())
        t_end = time.time() + timeout
        try:
            while proc.poll() is None:
                seen, bad, ok = _sockets(proc.pid)
                if ok:
                    samples += 1
                sockets_seen.update(seen)
                if bad:
                    violation = bad
                    break
                if time.time() > t_end:
                    reason = f"timeout after {timeout:.0f} s"
                    break
                time.sleep(0.05)
        finally:
            _kill_tree(proc)
    return {"rc": proc.returncode, "violation": violation, "samples": samples,
            "sockets": sorted(sockets_seen), "timeout": reason}


# ------------------------------------------------------------------ files inside work
def _inside(p: Path, work: Path, what: str) -> Path:
    r, w = Path(p).resolve(), Path(work).resolve()
    if r != w and w not in r.parents:
        raise ValueError(f"{what} is outside the study folder: refused")
    return r


def _parse_xml(path: Path) -> ET.Element:
    text = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        raise ValueError(f"{path.name}: DOCTYPE/ENTITY declarations refused")
    return ET.fromstring(text)


def _check_refs(path: Path, work: Path) -> ET.Element:
    """Every file a model references resolves inside work; no remote URI."""
    root = _parse_xml(path)
    refs = [el.text or "" for el in root.iter("uri")] + \
           [el.get("filename", "") for el in root.iter() if el.get("filename")]
    for ref in refs:
        ref = ref.strip()
        if not ref:
            continue
        if re.match(r"^(https?|ftp|fuel)://", ref, re.I) or "fuel.gazebosim.org" in ref:
            raise ValueError(f"remote model reference {ref[:60]!r} refused (no network)")
        local = re.sub(r"^(file|model|package)://", "", ref)
        if Path(local).is_absolute():
            _inside(Path(local), work, f"reference {ref[:60]}")
        else:
            _inside(path.parent / local, work, f"reference {ref[:60]}")
    return root


def _rel_model(spec: str, work: Path, what: str) -> Path:
    if Path(spec).is_absolute() or ":" in spec:
        raise ValueError(f"{what}: a path relative to the study folder, not an absolute one")
    p = _inside(work / spec, work, what)
    if not p.is_file():
        raise FileNotFoundError(f"{what} file missing in the study folder: {spec}")
    return p


# ------------------------------------------------------------------ gz_underwater
def _vec(v, n: int, what: str, default: float = 0.0) -> List[float]:
    if v is None:
        return [default] * n
    if isinstance(v, (int, float)):
        return [float(v)] * n
    v = [float(x) for x in v]
    if len(v) != n:
        raise ValueError(f"{what}: {n} values expected")
    return v


def _hull(inp: Dict[str, Any]) -> Dict[str, Any]:
    h = dict(inp.get("hull") or {"shape": "box", "size": [0.5, 0.5, 0.4]})
    shape = str(h.get("shape", "box")).lower()
    if shape == "box":
        a, b, c = _vec(h.get("size"), 3, "hull.size")
        vol = a * b * c
        geom = f"<box><size>{a!r} {b!r} {c!r}</size></box>"
        rpy = "0 0 0"
        k = lambda m: (m * (b * b + c * c) / 12, m * (a * a + c * c) / 12, m * (a * a + b * b) / 12)
    elif shape == "cylinder":
        r, L = float(h["radius"]), float(h["length"])
        axis = str(h.get("axis", "x")).lower()
        vol = math.pi * r * r * L
        geom = f"<cylinder><radius>{r!r}</radius><length>{L!r}</length></cylinder>"
        ia, it = 0.5 * r * r, (3 * r * r + L * L) / 12
        if axis == "x":
            rpy, k = "0 1.5707963267948966 0", lambda m: (m * ia, m * it, m * it)
        elif axis == "y":
            rpy, k = "1.5707963267948966 0 0", lambda m: (m * it, m * ia, m * it)
        else:
            rpy, k = "0 0 0", lambda m: (m * it, m * it, m * ia)
    elif shape == "sphere":
        r = float(h["radius"])
        vol = 4 / 3 * math.pi * r ** 3
        geom = f"<sphere><radius>{r!r}</radius></sphere>"
        rpy, k = "0 0 0", lambda m: (0.4 * m * r * r,) * 3
    else:
        raise ValueError("hull.shape: box, cylinder or sphere")
    if vol <= 0:
        raise ValueError("hull volume must be > 0")
    return {"shape": shape, "volume": vol, "geom": geom, "rpy": rpy, "inertia_of": k}


def _added_mass(spec) -> np.ndarray:
    """6x6 added mass (surge..yaw, positive): 6 diagonal values, a 6x6 matrix, or the marine
    adapter's output ({"added_mass_6x6": ...} or {"A11_kg", ..., "A66_kg_m2"})."""
    if spec is None:
        return np.zeros((6, 6))
    if isinstance(spec, dict):
        if "added_mass_6x6" in spec:
            spec = spec["added_mass_6x6"]
        else:
            keys = ("A11_kg", "A22_kg", "A33_kg", "A44_kg_m2", "A55_kg_m2", "A66_kg_m2")
            spec = [float(spec.get(k, 0.0)) for k in keys]
    a = np.asarray(spec, dtype=float)
    if a.shape == (6,):
        a = np.diag(a)
    if a.shape != (6, 6) or not np.all(np.isfinite(a)):
        raise ValueError("added_mass: 6 values, a 6x6 matrix, or the marine added_mass output")
    return 0.5 * (a + a.T)


def _f(x: float) -> str:
    return repr(float(x))


def _world(inp: Dict[str, Any], work: Path) -> Tuple[str, Dict[str, Any]]:
    g = float(inp.get("gravity", 9.81))
    rho = float(inp.get("fluid_density", 1000.0))
    fluid = bool(inp.get("fluid", True))
    dt = float(inp.get("dt", 0.001))
    depth0 = float(inp.get("initial_depth", 10.0))
    rpy0 = [math.radians(a) for a in _vec(inp.get("initial_rpy_deg"), 3, "initial_rpy_deg")]
    lin = _vec(inp.get("damping", {}).get("linear") if isinstance(inp.get("damping"), dict) else None,
               6, "damping.linear")
    quad = _vec(inp.get("damping", {}).get("quadratic") if isinstance(inp.get("damping"), dict) else None,
                6, "damping.quadratic")
    A = _added_mass(inp.get("added_mass"))
    am = [float(A[i, i]) for i in range(6)]
    if any(v < 0 for v in lin + quad + am):
        raise ValueError("damping and added_mass are given positive (physical size)")
    current = _vec(inp.get("current"), 3, "current")
    info: Dict[str, Any] = {"g": g, "rho": rho, "fluid": fluid, "depth0": depth0, "rpy0": rpy0,
                            "lin": lin, "quad": quad, "added_mass": am}
    thrusters = list(inp.get("thrusters") or [])
    hyd = ""
    if fluid and (any(lin) or any(quad) or any(current)):
        hyd = ("<plugin filename=\"gz-sim-hydrodynamics-system\" name=\"gz::sim::systems::Hydrodynamics\">"
               "<link_name>{link}</link_name>"
               f"<water_density>{_f(rho)}</water_density>"
               + "".join(f"<{n}>{_f(-v)}</{n}>" for n, v in zip(_HYD_LIN, lin))
               + "".join(f"<{n}>{_f(-v)}</{n}>" for n, v in zip(_HYD_QUAD, quad))
               + "".join(f"<{n}>0</{n}>" for n in ("xDotU", "yDotV", "zDotW", "kDotP", "mDotQ", "nDotR"))
               + f"<default_current>{' '.join(_f(c) for c in current)}</default_current>"
               "</plugin>")
    pose0 = f"0 0 {_f(-depth0)} {' '.join(_f(a) for a in rpy0)}"
    if inp.get("model"):
        path = _rel_model(str(inp["model"]), work, "model")
        _check_refs(path, work)
        link = str(inp.get("link") or "")
        if not link:
            root = _parse_xml(path)
            links = [el.get("name") for el in root.iter("link")]
            if not links:
                raise ValueError("model: no link found; give params['link']")
            link = links[0]
        if any(am):
            raise ValueError("added_mass with a model file: put <fluid_added_mass> in its <inertial>")
        if thrusters:
            raise ValueError("thrusters with a model file: add the propeller joints to the model "
                             "and its Thruster plugins")
        uri = path.relative_to(work.resolve()).as_posix()
        body = (f"<include><uri>{uri}</uri><name>vehicle</name><pose>{pose0}</pose>"
                + hyd.replace("{link}", link) + _posepub() + "</include>")
        info.update(link=link, volume=None, mass=None)
        timeline = ""
    else:
        hull = _hull(inp)
        vol = hull["volume"]
        if inp.get("mass") is not None:
            m = float(inp["mass"])
        elif inp.get("net_buoyancy_N") is not None:
            m = (rho * vol * g - float(inp["net_buoyancy_N"])) / g
        else:
            m = rho * vol                                    # neutral by default
        if m <= 0:
            raise ValueError("mass must be > 0")
        if inp.get("inertia") is not None:
            iv = [float(x) for x in inp["inertia"]]
            if len(iv) == 3:
                iv += [0.0, 0.0, 0.0]
            if len(iv) != 6:
                raise ValueError("inertia: [ixx, iyy, izz] or [ixx, iyy, izz, ixy, ixz, iyz]")
        else:
            iv = [*hull["inertia_of"](m), 0.0, 0.0, 0.0]
        com = _vec(inp.get("com"), 3, "com")
        fam = ""
        if fluid and np.any(A):
            fam = "<fluid_added_mass>" + "".join(
                f"<{_AXES[i]}{_AXES[j]}>{_f(A[i, j])}</{_AXES[i]}{_AXES[j]}>"
                for i in range(6) for j in range(i, 6)) + "</fluid_added_mass>"
        links = [f"""<link name="hull">
    <inertial><pose>{' '.join(_f(c) for c in com)} 0 0 0</pose><mass>{_f(m)}</mass>
     <inertia><ixx>{_f(iv[0])}</ixx><ixy>{_f(iv[3])}</ixy><ixz>{_f(iv[4])}</ixz><iyy>{_f(iv[1])}</iyy><iyz>{_f(iv[5])}</iyz><izz>{_f(iv[2])}</izz></inertia>{fam}</inertial>
    <collision name="hull_collision"><pose>0 0 0 {hull['rpy']}</pose><geometry>{hull['geom']}</geometry></collision>
   </link>"""]
        plugins, keys = [], []
        for i, th in enumerate(thrusters):
            pos = _vec(th.get("pos"), 3, f"thrusters[{i}].pos")
            ax = np.asarray(_vec(th.get("axis", [1, 0, 0]), 3, f"thrusters[{i}].axis"))
            if np.linalg.norm(ax) == 0:
                raise ValueError(f"thrusters[{i}].axis is zero")
            ax = ax / np.linalg.norm(ax)
            F = float(th.get("thrust_N", 0.0))
            d = float(th.get("diameter", 0.1))
            links.append(f"""<link name="prop{i}"><pose>{' '.join(_f(p) for p in pos)} 0 0 0</pose>
    <inertial><mass>0.01</mass><inertia><ixx>1e-4</ixx><iyy>1e-4</iyy><izz>1e-4</izz><ixy>0</ixy><ixz>0</ixz><iyz>0</iyz></inertia></inertial></link>
   <joint name="prop{i}_joint" type="revolute"><parent>hull</parent><child>prop{i}</child>
    <axis><xyz>{' '.join(_f(a) for a in ax)}</xyz><limit><lower>-1e16</lower><upper>1e16</upper></limit></axis></joint>""")
            plugins.append(f"""<plugin filename="gz-sim-thruster-system" name="gz::sim::systems::Thruster">
    <namespace>vehicle</namespace><joint_name>prop{i}_joint</joint_name>
    <thrust_coefficient>{_f(th.get('thrust_coefficient', 0.004))}</thrust_coefficient>
    <fluid_density>{_f(rho)}</fluid_density><propeller_diameter>{_f(d)}</propeller_diameter>
    <max_thrust_cmd>{_f(max(1000.0, 2 * abs(F)))}</max_thrust_cmd><min_thrust_cmd>{_f(-max(1000.0, 2 * abs(F)))}</min_thrust_cmd>
   </plugin>""")
            keys.append(f'<output topic="/model/vehicle/joint/prop{i}_joint/cmd_thrust" '
                        f'type="gz.msgs.Double">data: {_f(F)}</output>')
        # the fixed command is re-sent every 0.05 s so that a first message lost at start-up
        # cannot leave a thruster at zero
        timeline = ("" if not keys else
                    "<plugin filename=\"gz-sim-timeline-publisher-system\" "
                    "name=\"gz::sim::systems::TimelinePublisher\"><loop>true</loop>"
                    "<period>0.05</period><keyframe time=\"0\">" + "".join(keys)
                    + "</keyframe></plugin>")
        body = (f"<model name=\"vehicle\"><pose>{pose0}</pose>" + "".join(links)
                + hyd.replace("{link}", "hull") + "".join(plugins) + _posepub() + "</model>")
        info.update(link="hull", volume=vol, mass=m, inertia=iv, com=com,
                    thrust=[float(t.get("thrust_N", 0.0)) for t in thrusters])
    buoy = ("<plugin filename=\"gz-sim-buoyancy-system\" name=\"gz::sim::systems::Buoyancy\">"
            f"<uniform_fluid_density>{_f(rho)}</uniform_fluid_density></plugin>") if fluid else ""
    world = f"""<?xml version="1.0"?>
<sdf version="1.11">
 <world name="mcpfc">
  <physics name="fixed_step" type="dart"><max_step_size>{_f(dt)}</max_step_size><real_time_factor>0</real_time_factor><real_time_update_rate>0</real_time_update_rate></physics>
  <gravity>0 0 {_f(-g)}</gravity>
  <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
  {buoy}
  {timeline}
  {body}
 </world>
</sdf>
"""
    return world, info


def _posepub() -> str:
    return ("<plugin filename=\"gz-sim-pose-publisher-system\" name=\"gz::sim::systems::PosePublisher\">"
            "<publish_link_pose>false</publish_link_pose><publish_model_pose>true</publish_model_pose>"
            "<publish_nested_model_pose>false</publish_nested_model_pose>"
            "<use_pose_vector_msg>true</use_pose_vector_msg><update_frequency>{hz}</update_frequency>"
            "</plugin>")


def _read_poses(tlog: Path) -> np.ndarray:
    """rows t, x, y, z, qx, qy, qz, qw of the model 'vehicle' (t = sim time of recording)."""
    from gz.msgs.pose_v_pb2 import Pose_V
    con = sqlite3.connect(f"file:{tlog.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT m.time_recv, m.message FROM messages m JOIN topics t "
                           "ON m.topic_id = t.id WHERE t.name = '/model/vehicle/pose' "
                           "ORDER BY m.time_recv, m.id").fetchall()
    finally:
        con.close()
    out = []
    for tr, blob in rows:
        msg = Pose_V()
        msg.ParseFromString(blob)
        for p in msg.pose:
            if p.name == "vehicle":
                st = p.header.stamp if p.HasField("header") else None
                t = (st.sec + st.nsec * 1e-9) if st is not None and (st.sec or st.nsec) else tr * 1e-9
                o = p.orientation
                out.append([t, p.position.x, p.position.y, p.position.z, o.x, o.y, o.z, o.w])
    return np.asarray(out, dtype=float)


def _quat_rpy(q) -> Tuple[float, float, float]:
    x, y, z, w = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def _rpy_quat(r, p, y) -> np.ndarray:
    cr, sr, cp, sp, cy, sy = (math.cos(r / 2), math.sin(r / 2), math.cos(p / 2), math.sin(p / 2),
                              math.cos(y / 2), math.sin(y / 2))
    return np.array([sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy,
                     cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy])


def _underwater(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    duration = float(inp.get("duration", 10.0))
    dt = float(inp.get("dt", 0.001))
    hz = float(inp.get("record_hz", 100.0))
    if not (0 < dt <= 0.01) or duration <= 0:
        raise ValueError("dt in (0, 0.01] s and duration > 0")
    n_iter = int(round(duration / dt))
    if n_iter > MAX_ITER:
        raise ValueError(f"{n_iter} steps > {MAX_ITER}: shorten duration or enlarge dt")
    if not (1 <= hz <= 1000):
        raise ValueError("record_hz in [1, 1000]")
    gzdir = work / "gz"
    gzdir.mkdir(parents=True, exist_ok=True)
    world, info = _world(inp, work)
    world = world.replace("{hz}", _f(hz))
    wfile = gzdir / "world.sdf"
    wfile.write_text(world, encoding="utf-8")
    env, why = _transport_env(gzdir)
    if why:
        return {"verdict": "UNKNOWN", "summary": why, "values": {}, "files": ["gz/world.sdf"]}
    env["GZ_SIM_RESOURCE_PATH"] = str(work.resolve())
    logdir = gzdir / "log"
    args = [_sim_exe(), str(wfile), "-s", "-r", "-v", "2", "--iterations", str(n_iter),
            "--record-path", str(logdir), "--log-overwrite", "--record-topic", "/model/vehicle/pose"]
    timeout = float(inp.get("timeout_s", 600))
    run = _run_supervised(args, gzdir, env, timeout, gzdir / "gz_server.log")
    files = ["gz/world.sdf", "gz/gz_server.log"]
    net = {"gz_sockets": run["sockets"], "gz_partition": env["GZ_PARTITION"]}
    if run["violation"]:
        return {"verdict": "UNKNOWN", "values": net, "files": files,
                "summary": ("stopped: gz opened a non-loopback socket ("
                            + ", ".join(run["violation"][:4]) + "); nothing was simulated")}
    if run["samples"] == 0:
        return {"verdict": "UNKNOWN", "values": net, "files": files,
                "summary": "gz ended before its sockets could be inspected: binding not verified"}
    if run["timeout"]:
        return {"verdict": "UNKNOWN", "values": net, "files": files,
                "summary": f"gz sim stopped: {run['timeout']}"}
    tlog = logdir / "state.tlog"
    tail = (gzdir / "gz_server.log").read_text(encoding="utf-8", errors="replace")[-600:]
    if run["rc"] != 0 or not tlog.is_file():
        raise RuntimeError(f"gz sim failed (code {run['rc']}): {tail}")
    data = _read_poses(tlog)
    if len(data) < 5:
        raise RuntimeError(f"gz recorded {len(data)} poses (pose publisher not running?): {tail}")
    q0 = _rpy_quat(*info["rpy0"])
    data = np.vstack([[0.0, 0.0, 0.0, -info["depth0"], *q0], data])
    files.append("gz/log/state.tlog")
    np.savetxt(work / "pose.csv", data, delimiter=",", header="t_s,x_m,y_m,z_m,qx,qy,qz,qw",
               comments="")
    files.append("pose.csv")
    return _summarise(data, info, duration, inp, net, files)


def _summarise(data: np.ndarray, info: Dict[str, Any], duration: float, inp: Dict[str, Any],
               net: Dict[str, Any], files: List[str]) -> Dict[str, Any]:
    t, xyz, quat = data[:, 0], data[:, 1:4], data[:, 4:8]
    z = xyz[:, 2]
    vel = np.gradient(xyz, t, axis=0)
    vz = vel[:, 2]
    n = len(t)
    win = max(3, int(0.1 * n))                     # last 10 % of the record
    vz_tail = vz[-win:]
    v_term = float(np.mean(vz_tail))
    spread = float(np.max(np.abs(vz_tail - v_term)))
    steady = spread <= max(0.01 * abs(v_term), 1e-4)
    q0 = quat[0]
    dots = np.clip(np.abs(quat @ q0) / np.linalg.norm(quat, axis=1) / np.linalg.norm(q0), 0, 1)
    ang = np.degrees(2 * np.arccos(dots))
    r, p, y = _quat_rpy(quat[-1])
    values: Dict[str, Any] = {
        "duration_s": round(float(t[-1]), 6), "samples": int(n),
        "initial_depth_m": info["depth0"], "final_depth_m": float(-z[-1]),
        "depth_change_m": float(-(z[-1] - z[0])),              # > 0: went down
        "max_depth_drift_m": float(np.max(np.abs(z - z[0]))),
        "vertical_velocity_m_s": float(np.mean(vz[-3:])),       # + up
        "vz_terminal_m_s": v_term, "terminal_speed_m_s": abs(v_term),
        "terminal_reached": bool(steady),
        "horizontal_drift_m": float(np.hypot(*(xyz[-1, :2] - xyz[0, :2]))),
        "speed_m_s": float(np.linalg.norm(vel[-1])),
        "horizontal_speed_m_s": float(np.mean(np.hypot(vel[-win:, 0], vel[-win:, 1]))),
        "max_attitude_drift_deg": float(np.max(ang)),
        "final_roll_deg": math.degrees(r), "final_pitch_deg": math.degrees(p),
        "final_yaw_deg": math.degrees(y),
    }
    if abs(v_term) > 1e-6:
        reach = np.nonzero(np.abs(vz) >= (1 - math.exp(-1)) * abs(v_term))[0]
        if len(reach) and reach[0] > 0:
            k = reach[0]
            target = (1 - math.exp(-1)) * abs(v_term)
            a0, a1 = abs(vz[k - 1]), abs(vz[k])
            values["t63_s"] = float(t[k - 1] + (target - a0) / max(a1 - a0, 1e-12) * (t[k] - t[k - 1]))
    notes = []
    if info.get("volume") is not None:
        F = (info["rho"] * info["volume"] - info["mass"]) * info["g"] if info["fluid"] \
            else -info["mass"] * info["g"]
        values.update(volume_m3=info["volume"], mass_kg=info["mass"], net_buoyancy_N=F)
        c, k = info["lin"][2], info["quad"][2]
        if info["fluid"] and F != 0 and (c > 0 or k > 0):
            # steady heave: c v + k v^2 = |F|
            vt = (abs(F) / c if k == 0 else (-c + math.sqrt(c * c + 4 * k * abs(F))) / (2 * k))
            values["predicted_terminal_speed_m_s"] = vt
    if not steady and abs(v_term) > 1e-4:
        notes.append("vertical velocity still changing at the end: terminal value not reached, "
                     "lengthen duration")
    if info["fluid"]:
        notes.append("uniform fluid without free surface or seabed")
    values.update(net)
    head = (f"gz_underwater {duration:g} s: depth {info['depth0']:.3g} -> {-z[-1]:.4g} m, "
            f"vz {v_term:+.4g} m/s{' (terminal)' if steady else ''}, attitude drift "
            f"{values['max_attitude_drift_deg']:.3g} deg; gz sockets loopback only")
    return result(values, inp.get("limits"), head, files, notes)


# ------------------------------------------------------------------ convert_check
def _rot(r: float, p: float, y: float) -> np.ndarray:
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def _nums(s: Optional[str], n: int) -> List[float]:
    v = [float(x) for x in (s or "").split()] if s else [0.0] * n
    if len(v) != n:
        raise ValueError(f"expected {n} numbers, got {s!r}")
    return v


def _tensor(ixx, iyy, izz, ixy, ixz, iyz) -> np.ndarray:
    return np.array([[ixx, ixy, ixz], [ixy, iyy, iyz], [ixz, iyz, izz]], dtype=float)


def _urdf(path: Path) -> Dict[str, Any]:
    root = _parse_xml(path)
    if root.tag != "robot":
        raise ValueError(f"{path.name}: not a URDF (<robot>)")
    links, joints = {}, {}
    for ln in root.findall("link"):
        inert = ln.find("inertial")
        if inert is None:
            links[ln.get("name")] = {"mass": 0.0, "com": np.zeros(3), "I": np.zeros((3, 3)),
                                     "has_inertial": False}
            continue
        o = inert.find("origin")
        xyz = _nums(o.get("xyz") if o is not None else None, 3)
        rpy = _nums(o.get("rpy") if o is not None else None, 3)
        m = float(inert.find("mass").get("value"))
        i = inert.find("inertia")
        I = _tensor(*(float(i.get(k, 0)) for k in ("ixx", "iyy", "izz", "ixy", "ixz", "iyz")))
        R = _rot(*rpy)
        links[ln.get("name")] = {"mass": m, "com": np.array(xyz), "I": R @ I @ R.T,
                                 "has_inertial": True}
    for j in root.findall("joint"):
        o = j.find("origin")
        T = np.eye(4)
        T[:3, :3] = _rot(*_nums(o.get("rpy") if o is not None else None, 3))
        T[:3, 3] = _nums(o.get("xyz") if o is not None else None, 3)
        joints[j.get("name")] = {"type": j.get("type"), "parent": j.find("parent").get("link"),
                                 "child": j.find("child").get("link"), "T": T}
    return {"links": links, "joints": joints}


def _sdf_model(text: str) -> Dict[str, Any]:
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        raise ValueError("SDF: DOCTYPE/ENTITY declarations refused")
    root = ET.fromstring(text)
    model = root.find("model")
    if model is None:
        raise ValueError("SDF holds no <model>")
    links, joints, frames = {}, {}, {}
    for ln in model.findall("link"):
        inert = ln.find("inertial")
        if inert is None:                                # SDF defaults: 1 kg, identity inertia
            links[ln.get("name")] = {"mass": 1.0, "com": np.zeros(3), "I": np.eye(3),
                                     "has_inertial": False}
            continue
        pose = inert.find("pose")
        pv = _nums(pose.text if pose is not None else None, 6)
        if pose is not None and pose.get("relative_to") not in (None, "", ln.get("name")):
            raise ValueError(f"link {ln.get('name')}: inertial pose relative to another frame")
        m_el = inert.find("mass")
        m = float(m_el.text) if m_el is not None else 1.0
        i = inert.find("inertia")
        get = (lambda k, d: float(i.find(k).text) if i is not None and i.find(k) is not None else d)
        I = _tensor(get("ixx", 1.0), get("iyy", 1.0), get("izz", 1.0),
                    get("ixy", 0.0), get("ixz", 0.0), get("iyz", 0.0))
        R = _rot(*pv[3:])
        links[ln.get("name")] = {"mass": m, "com": np.array(pv[:3]), "I": R @ I @ R.T,
                                 "has_inertial": True}
    for j in model.findall("joint"):
        joints[j.get("name")] = {"type": j.get("type"), "parent": (j.findtext("parent") or "").strip(),
                                 "child": (j.findtext("child") or "").strip()}
    for f in model.findall("frame"):
        frames[f.get("name")] = f.get("attached_to")
    nested = len(model.findall("model"))
    return {"links": links, "joints": joints, "frames": frames, "nested": nested}


def _composite(urdf: Dict[str, Any], keep: str, members: List[str]) -> Dict[str, Any]:
    """Mass, COM and inertia about the COM, in the frame of link `keep`, of keep + members
    (members joined to keep by fixed joints)."""
    T_of = {keep: np.eye(4)}
    changed = True
    while changed:
        changed = False
        for j in urdf["joints"].values():
            if j["type"] == "fixed" and j["parent"] in T_of and j["child"] in members \
                    and j["child"] not in T_of:
                T_of[j["child"]] = T_of[j["parent"]] @ j["T"]
                changed = True
    m_tot, mc = 0.0, np.zeros(3)
    parts = []
    for name in [keep, *members]:
        L = urdf["links"][name]
        T = T_of[name]
        c = T[:3, :3] @ L["com"] + T[:3, 3]
        parts.append((L["mass"], c, T[:3, :3] @ L["I"] @ T[:3, :3].T))
        m_tot += L["mass"]
        mc += L["mass"] * c
    com = mc / m_tot if m_tot > 0 else np.zeros(3)
    I = np.zeros((3, 3))
    for m, c, Ic in parts:
        d = c - com
        I += Ic + m * (np.dot(d, d) * np.eye(3) - np.outer(d, d))
    return {"mass": m_tot, "com": com, "I": I}


def _groups(urdf: Dict[str, Any], kept: set) -> Dict[str, List[str]]:
    """kept link -> URDF links lumped into it (descendants through fixed joints only)."""
    parent_of = {j["child"]: (j["parent"], j["type"]) for j in urdf["joints"].values()}
    groups: Dict[str, List[str]] = {k: [] for k in kept}
    for name in urdf["links"]:
        if name in kept:
            continue
        cur = name
        while cur in parent_of and parent_of[cur][1] == "fixed" and cur not in kept:
            cur = parent_of[cur][0]
        if cur in kept:
            groups[cur].append(name)
        else:
            groups.setdefault("__unaccounted__", []).append(name)
    return groups


def _rel(a: float, b: float, scale: float) -> float:
    return abs(a - b) / max(abs(scale), 1e-300)


def _compare(ref: Dict[str, Any], got: Dict[str, Dict[str, Any]], tag: str,
             principal: bool = False) -> Tuple[Dict[str, float], List[str], List[str]]:
    """ref: URDF; got: link name -> {mass, com, I} of the other loader. Returns errors,
    dropped links, problems."""
    kept = set(got) & set(ref["links"])
    groups = _groups(ref, kept)
    dropped = sorted(n for k, v in groups.items() if k != "__unaccounted__" for n in v)
    problems: List[str] = []
    if groups.get("__unaccounted__"):
        problems.append(f"{tag}: links missing and not joined by fixed joints: "
                        + ", ".join(groups["__unaccounted__"]))
    m_err = i_err = c_err = 0.0
    for k in sorted(kept):
        comp = _composite(ref, k, groups.get(k, []))
        g = got[k]
        scale_m = max(comp["mass"], 1e-12)
        e_m = _rel(g["mass"], comp["mass"], scale_m)
        if principal:
            ev_r = np.sort(np.linalg.eigvalsh(comp["I"]))
            ev_g = np.sort(np.asarray(g["I_principal"], dtype=float))
            e_i = float(np.max(np.abs(ev_g - ev_r)) / max(np.max(np.abs(ev_r)), 1e-300))
        else:
            e_i = float(np.linalg.norm(g["I"] - comp["I"]) / max(np.linalg.norm(comp["I"]), 1e-300))
        e_c = float(np.linalg.norm(np.asarray(g["com"]) - comp["com"]))
        if comp["mass"] == 0 and g["mass"] == 0:
            e_m = e_i = 0.0
        if not ref["links"][k]["has_inertial"] and not groups.get(k) and g["mass"] > 0:
            problems.append(f"{tag}: link {k} has no <inertial> in the URDF but {g['mass']:g} kg here")
        m_err, i_err, c_err = max(m_err, e_m), max(i_err, e_i), max(c_err, e_c)
    return {"mass_rel_err": m_err, "inertia_rel_err": i_err, "com_err_m": c_err}, dropped, problems


_JOINT_EQ = {"revolute": {"revolute"}, "continuous": {"revolute", "continuous"},
             "prismatic": {"prismatic"}, "fixed": {"fixed"}, "floating": {"free", "floating"},
             "planar": {"planar"}}


def _two_link_urdf() -> str:
    """2-link arm on a base, plus a sensor link on a fixed joint (lumped by converters)."""
    return """<?xml version="1.0"?>
<robot name="two_link">
 <link name="base"><inertial><origin xyz="0 0 0.05" rpy="0 0 0"/><mass value="3.0"/>
  <inertia ixx="0.0125" ixy="0" ixz="0" iyy="0.02" iyz="0" izz="0.03"/></inertial></link>
 <joint name="shoulder" type="revolute"><parent link="base"/><child link="link1"/>
  <origin xyz="0 0 0.1" rpy="0 0 0"/><axis xyz="0 -1 0"/><limit lower="-3" upper="3" effort="50" velocity="5"/></joint>
 <link name="link1"><inertial><origin xyz="0.25 0 0" rpy="0.1 0.2 0.3"/><mass value="2.0"/>
  <inertia ixx="0.001" ixy="0.0001" ixz="0" iyy="0.0425" iyz="0" izz="0.0425"/></inertial></link>
 <joint name="elbow" type="revolute"><parent link="link1"/><child link="link2"/>
  <origin xyz="0.5 0 0" rpy="0 0 0"/><axis xyz="0 -1 0"/><limit lower="-3" upper="3" effort="50" velocity="5"/></joint>
 <link name="link2"><inertial><origin xyz="0.2 0 0" rpy="0 0 0"/><mass value="1.5"/>
  <inertia ixx="0.0005" ixy="0" ixz="0" iyy="0.02" iyz="0" izz="0.02"/></inertial></link>
 <joint name="sensor_mount" type="fixed"><parent link="link2"/><child link="sensor"/>
  <origin xyz="0.4 0 0" rpy="0 0 1.5707963267948966"/></joint>
 <link name="sensor"><inertial><origin xyz="0.01 0 0" rpy="0 0 0"/><mass value="0.1"/>
  <inertia ixx="1e-5" ixy="0" ixz="0" iyy="2e-5" iyz="0" izz="3e-5"/></inertial></link>
</robot>
"""


_MJ_SCRIPT = r'''
import json, sys
import numpy as np
import mujoco
src, out = sys.argv[1], sys.argv[2]
m = mujoco.MjModel.from_xml_path(src)
bodies = {}
for b in range(m.nbody):
    name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b) or ("world" if b == 0 else f"body{b}")
    bodies[name] = {"mass": float(m.body_mass[b]), "I_principal": [float(x) for x in m.body_inertia[b]],
                    "ipos": [float(x) for x in m.body_ipos[b]], "parent": int(m.body_parentid[b]),
                    "id": b}
types = {0: "free", 1: "ball", 2: "prismatic", 3: "revolute"}
joints = {(mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j) or f"joint{j}"): types.get(int(m.jnt_type[j]), "?")
          for j in range(m.njnt)}
mujoco.mj_saveLastXML(out.replace(".json", ".xml"), m)
json.dump({"bodies": bodies, "joints": joints, "version": mujoco.__version__}, open(out, "w"))
'''


def _mjcf_side(urdf_path: Path, work: Path, timeout: float) -> Dict[str, Any]:
    py = env_exe("robot", "python")
    d = work / "mjcf"
    d.mkdir(exist_ok=True)
    script = d / "mj_load.py"
    script.write_text(_MJ_SCRIPT, encoding="utf-8")
    out = d / "mujoco_model.json"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    r = subprocess.run([py, "-I", str(script), str(urdf_path), str(out)], cwd=str(d), env=env,
                       capture_output=True, text=True, timeout=timeout, creationflags=_flags())
    if r.returncode != 0 or not out.is_file():
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1][:300]
                           if (r.stderr or r.stdout).strip() else f"code {r.returncode}")
    return json.loads(out.read_text(encoding="utf-8"))


def _convert(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    tol = float(inp.get("tol", 1e-6))
    spec = inp.get("urdf") or inp.get("model")
    if inp.get("urdf_xml"):
        upath = work / "model.urdf"
        upath.write_text(str(inp["urdf_xml"]), encoding="utf-8")
    elif spec == "builtin:two_link":
        upath = work / "builtin_two_link.urdf"
        upath.write_text(_two_link_urdf(), encoding="utf-8")
    elif isinstance(spec, str) and spec:
        upath = _rel_model(spec, work, "urdf")
    else:
        raise ValueError("urdf: a file relative to the study folder, urdf_xml, or builtin:two_link")
    _check_refs(upath, work)
    ref = _urdf(upath)
    env = _base_env(work / "gz_home")
    sdf_exe = _sdf_exe()
    files = [upath.relative_to(work.resolve()).as_posix() if upath.resolve().is_relative_to(work.resolve())
             else upath.name]
    if inp.get("sdf"):                                      # compare with an SDF the user made
        spath = _rel_model(str(inp["sdf"]), work, "sdf")
        _check_refs(spath, work)
        how = f"URDF vs given SDF {spath.name}"
    else:
        p = subprocess.run([sdf_exe, "-p", str(upath)], capture_output=True, text=True, timeout=120,
                           env=env, cwd=str(work), creationflags=_flags())
        if p.returncode != 0 or "<sdf" not in p.stdout:
            raise RuntimeError(f"gz sdf -p failed (code {p.returncode}): {(p.stderr or p.stdout)[-400:]}")
        spath = work / (upath.stem + ".sdf")
        spath.write_text(p.stdout[p.stdout.find("<sdf"):], encoding="utf-8")
        how = "URDF -> SDF (gz sdf -p)"
    files.append(spath.name)
    k = subprocess.run([sdf_exe, "-k", str(spath)], capture_output=True, text=True, timeout=120,
                       env=env, cwd=str(work), creationflags=_flags())
    valid = k.returncode == 0 and "Valid" in k.stdout
    sdf = _sdf_model(spath.read_text(encoding="utf-8"))
    # SDF -> SDF: the converted file read back by sdformat must give the same model
    p2 = subprocess.run([sdf_exe, "-p", str(spath)], capture_output=True, text=True, timeout=120,
                        env=env, cwd=str(work), creationflags=_flags())
    problems: List[str] = []
    if not valid:
        problems.append(f"gz sdf -k: SDF not valid: {(k.stdout + k.stderr).strip()[-200:]}")
    if sdf["nested"]:
        problems.append(f"SDF has {sdf['nested']} nested model(s): not compared")
    errs, dropped, probs = _compare(ref, sdf["links"], "SDF")
    problems += probs
    rt_err = math.inf
    if p2.returncode == 0 and "<sdf" in p2.stdout:
        sdf2 = _sdf_model(p2.stdout[p2.stdout.find("<sdf"):])
        rt_err = 0.0
        if set(sdf2["links"]) != set(sdf["links"]):
            problems.append("SDF -> SDF changed the link set")
            rt_err = math.inf
        for n in set(sdf2["links"]) & set(sdf["links"]):
            a, b = sdf["links"][n], sdf2["links"][n]
            rt_err = max(rt_err, _rel(a["mass"], b["mass"], max(a["mass"], 1e-12)),
                         float(np.linalg.norm(a["I"] - b["I"]) / max(np.linalg.norm(a["I"]), 1e-300)))
    else:
        problems.append("gz sdf -p could not read back the SDF")
    # joints
    ref_moving = {n: j for n, j in ref["joints"].items() if j["type"] != "fixed"}
    for n, j in ref_moving.items():
        s = sdf["joints"].get(n)
        if s is None:
            problems.append(f"SDF: joint {n} ({j['type']}) missing")
        elif s["type"] not in _JOINT_EQ.get(j["type"], {j["type"]}):
            problems.append(f"SDF: joint {n} {j['type']} became {s['type']}")
    fixed_kept = [n for n, j in ref["joints"].items() if j["type"] == "fixed" and n in sdf["joints"]]
    total_ref = sum(L["mass"] for L in ref["links"].values())
    total_sdf = sum(L["mass"] for L in sdf["links"].values())
    values: Dict[str, Any] = {
        "links_urdf": len(ref["links"]), "links_sdf": len(sdf["links"]),
        "joints_urdf": len(ref["joints"]), "joints_sdf": len(sdf["joints"]),
        "moving_joints_urdf": len(ref_moving),
        "moving_joints_sdf": sum(1 for j in sdf["joints"].values() if j["type"] != "fixed"),
        "total_mass_urdf_kg": total_ref, "total_mass_sdf_kg": total_sdf,
        "total_mass_rel_err": _rel(total_sdf, total_ref, max(total_ref, 1e-12)),
        "mass_rel_err_max": errs["mass_rel_err"], "inertia_rel_err_max": errs["inertia_rel_err"],
        "com_err_max_m": errs["com_err_m"], "sdf_roundtrip_rel_err": rt_err,
        "dropped_fixed_links": len(dropped), "dropped_links": dropped,
        "fixed_joints_kept": fixed_kept, "sdf_valid": valid, "tol": tol,
    }
    worst = max(values["total_mass_rel_err"], errs["mass_rel_err"], errs["inertia_rel_err"], rt_err)
    com_bad = errs["com_err_m"] > tol * max(1.0, _size(ref))
    notes = []
    if dropped:
        notes.append(f"{len(dropped)} fixed link(s) lumped into their parent by sdformat: "
                     + ", ".join(dropped) + " (composite inertia checked)")
    # optional third loader: MuJoCo (env robot), through files only
    if inp.get("mjcf", True):
        try:
            mj = _mjcf_side(upath, work, float(inp.get("timeout_s", 120)))
            bodies = {n: {"mass": b["mass"], "com": b["ipos"], "I_principal": b["I_principal"]}
                      for n, b in mj["bodies"].items() if n in ref["links"]}
            # MuJoCo expresses the body inertia in its principal frame: compare eigenvalues;
            # ipos is in the body frame, which MuJoCo keeps as the URDF link frame
            merrs, mdropped, mprobs = _compare(ref, bodies, "MuJoCo", principal=True)
            mj_total = sum(b["mass"] for n, b in mj["bodies"].items() if n != "world")
            world_m = mj["bodies"].get("world", {}).get("mass", 0.0)
            lost = sorted(n for n in ref["links"] if n not in mj["bodies"])
            values.update(mujoco=mj.get("version"), mjcf_total_mass_kg=mj_total,
                          mjcf_total_mass_rel_err=_rel(mj_total, total_ref, max(total_ref, 1e-12)),
                          mjcf_mass_rel_err_max=merrs["mass_rel_err"],
                          mjcf_inertia_rel_err_max=merrs["inertia_rel_err"],
                          mjcf_com_err_max_m=merrs["com_err_m"], mjcf_dropped_links=lost,
                          mjcf_world_mass_kg=world_m)
            files.append("mjcf/mujoco_model.xml")
            if lost:
                notes.append("MuJoCo fused (silently) the fixed link(s) " + ", ".join(lost)
                             + (f"; {world_m:g} kg went into the world body" if world_m else ""))
            mworst = max(merrs["mass_rel_err"], merrs["inertia_rel_err"])
            if mworst > tol or merrs["com_err_m"] > tol * max(1.0, _size(ref)):
                problems.append(f"MuJoCo: masses/inertias differ (mass {merrs['mass_rel_err']:.2g}, "
                                f"inertia {merrs['inertia_rel_err']:.2g}, COM {merrs['com_err_m']:.2g} m)")
            if values["mjcf_total_mass_rel_err"] > tol:
                notes.append(f"MuJoCo moving-body mass {mj_total:g} kg vs URDF {total_ref:g} kg "
                             "(static links fused into the world are not simulated)")
            problems += [p for p in mprobs if "missing and not joined" not in p]
        except FileNotFoundError as e:
            notes.append(f"MuJoCo comparison skipped: {e}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"MuJoCo could not load the URDF: {type(e).__name__}: {str(e)[:200]}")
    bad = worst > tol or com_bad or bool(problems)
    head = (f"convert_check {how}: total mass {total_ref:.6g} -> {total_sdf:.6g} kg, "
            f"max rel. error mass {errs['mass_rel_err']:.2g} / inertia {errs['inertia_rel_err']:.2g} "
            f"(tol {tol:g}), joints {len(ref_moving)} moving kept "
            f"{values['moving_joints_sdf']}")
    res = result(values, inp.get("limits"), head, files, [*problems, *notes])
    if bad:
        res["verdict"] = "FAIL"
    elif res["verdict"] == "UNKNOWN":                 # the tolerance is the criterion here
        res["verdict"] = "OK"
        res["summary"] = res["summary"].replace("; no criteria given: computed, nothing judged", "")
    return res


def _size(ref: Dict[str, Any]) -> float:
    s = [float(np.linalg.norm(j["T"][:3, 3])) for j in ref["joints"].values()]
    return max(s) if s else 1.0


# ------------------------------------------------------------------ entry
def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    work = Path(work).resolve()          # gz gets absolute paths only (a missing relative world
    if kind == "gz_underwater":           # file makes it try a Fuel download)
        return _underwater(inp, work)
    if kind == "convert_check":
        return _convert(inp, work)
    raise ValueError(f"unknown kind {kind!r}")


# ------------------------------------------------------------------ reference cases (§9)
_G, _RHO = 9.81, 1000.0
_BOX = {"shape": "box", "size": [0.5, 0.5, 0.4]}            # V = 0.1 m^3, displaces 100 kg
_F_UP = (100.0 - 90.0) * _G                                  # net buoyancy of a 90 kg hull
REFERENCES = [
    {   # neutral buoyancy (m = rho V): Archimedes, the body stays at its depth
        "name": "neutral_buoyancy_hold", "kind": "gz_underwater",
        "inp": {"hull": _BOX, "mass": 100.0, "fluid_density": _RHO, "gravity": _G,
                "initial_depth": 10.0, "duration": 10.0,
                "damping": {"linear": [50, 50, 50, 5, 5, 5]}, "added_mass": [20, 60, 60, 1, 1, 1]},
        "expect": {"max_depth_drift_m": [0.0, 0.01]},      # |drift| <= 1 cm over 10 s
    },
    {   # linear damping: m_tot dv/dt = F - c v -> v_t = F / c
        "name": "terminal_velocity_linear", "kind": "gz_underwater",
        "inp": {"hull": _BOX, "mass": 90.0, "fluid_density": _RHO, "gravity": _G,
                "initial_depth": 50.0, "duration": 12.0, "damping": {"linear": [100] * 6}},
        "expect": {"terminal_speed_m_s": [_F_UP / 100.0, 0.01]},
    },
    {   # added mass slows the transient only: v = v_t (1 - exp(-c t / (m + m_a))), t63 = (m+m_a)/c
        "name": "added_mass_time_constant", "kind": "gz_underwater",
        "inp": {"hull": _BOX, "mass": 90.0, "fluid_density": _RHO, "gravity": _G,
                "initial_depth": 50.0, "duration": 12.0, "damping": {"linear": [100] * 6},
                "added_mass": [60, 60, 60, 1, 1, 1]},
        "expect": {"t63_s": [(90.0 + 60.0) / 100.0, 0.02], "terminal_speed_m_s": [_F_UP / 100.0, 0.01]},
    },
    {   # quadratic damping: k v^2 = F -> v_t = sqrt(F / k)
        "name": "terminal_velocity_quadratic", "kind": "gz_underwater",
        "inp": {"hull": _BOX, "mass": 90.0, "fluid_density": _RHO, "gravity": _G,
                "initial_depth": 50.0, "duration": 12.0, "damping": {"quadratic": [100] * 6}},
        "expect": {"terminal_speed_m_s": [math.sqrt(_F_UP / 100.0), 0.01]},
    },
    {   # no fluid: free fall, drop = g t^2 / 2
        "name": "free_fall_no_fluid", "kind": "gz_underwater",
        "inp": {"hull": _BOX, "mass": 90.0, "fluid": False, "gravity": _G, "initial_depth": 0.0,
                "duration": 2.0},
        "expect": {"depth_change_m": [_G * 2.0 ** 2 / 2, 0.002]},
    },
    {   # neutral body, surge thruster T against linear damping c: u = T / c
        "name": "thruster_surge_speed", "kind": "gz_underwater",
        "inp": {"hull": _BOX, "mass": 100.0, "fluid_density": _RHO, "gravity": _G,
                "initial_depth": 10.0, "duration": 20.0, "damping": {"linear": [50, 50, 50, 20, 20, 20]},
                "thrusters": [{"pos": [-0.3, 0, 0], "axis": [1, 0, 0], "thrust_N": 25.0}]},
        "expect": {"horizontal_speed_m_s": [25.0 / 50.0, 0.02]},
    },
    {   # URDF -> SDF keeps every mass and inertia (fixed sensor link lumped: composite exact)
        "name": "convert_two_link", "kind": "convert_check",
        "inp": {"urdf": "builtin:two_link", "tol": 1e-9},
        "expect": {"total_mass_rel_err": [0.0, 1e-12], "mass_rel_err_max": [0.0, 1e-9],
                   "inertia_rel_err_max": [0.0, 1e-9], "dropped_fixed_links": [1, 0]},
    },
]
