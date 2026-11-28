"""ArduPilot SITL (software in the loop) flight tests, purely simulated (env `aero`).

The real autopilot firmware (ArduCopter / ArduPlane / ArduSub / ArduRover 4.7, Windows Cygwin builds,
install/tools.lock.json) runs with its BUILT-IN physics model; this adapter talks to it with
pymavlink over a loopback TCP link, flies a scripted test and measures it.

Kinds (no geometry):
- sitl_copter  arm in GUIDED, take off to `alt_m`, hover `hover_s`, optional waypoints, LAND
- sitl_sub     ArduSub `vectored` model: arm, depth hold (ALT_HOLD) at `depth_m`, heading hold
- sitl_rover   ArduRover: arm in GUIDED, drive to a point `north_m`/`east_m` away, stop
- sitl_plane   ArduPlane AUTO mission: runway take-off, rectangle circuit, final approach, land;
               the aircraft goes in by dynamic similarity (see the plane section)

Times are SIMULATED seconds (autopilot boot clock), not wall time. Altitudes/depths are the
autopilot's own EKF estimate (GLOBAL_POSITION_INT), relative to home.

Vehicle parameters SITL really uses (copter, `frame`, written as the model's JSON file, see
ArduPilot libraries/SITL/SIM_Frame.h): mass [kg], diagonal_size [m], hoverThrOut (throttle
output at hover, 0..1), refCurrent [A] and refVoltage [V] (hover power), maxVoltage [V],
battCapacityAh (0 = unlimited), propExpo (MOT_THST_EXPO), refRotRate [deg/s yaw], disc_area
[m^2], mdrag_coef, refSpd/refAngle (drag test point), spin_min/spin_max, pwmMin/pwmMax,
slew_max, refAlt [m AMSL]. Motor thrust is NOT given as a curve: SITL scales the motors so that
the vehicle hovers at hoverThrOut; `thrust_to_weight` sets hoverThrOut = 1 / T/W (the throttle
output is linear in thrust once MOT_THST_EXPO linearises it). Any autopilot parameter can be set
with `params` {"NAME": number}.

SECURITY (FEUILLE_DE_ROUTE_ATELIER §10.1.4, §10.2):
- simulation only: no connection string is accepted from the caller; the only link is built
  here, TCP 127.0.0.1 (SITL `--serial0 tcpclient:127.0.0.1:<port>` to our loopback listener);
  serial ports are never opened (SERIAL1/2 disabled, no uart: device);
- SITL 4.7.3 binds its RC-input UDP socket on 0.0.0.0 with no option to change it
  (AP_RCProtocol_UDP). The adapter first holds that port with an exclusive socket (connected to a
  loopback peer, so it receives nothing from the network): SITL's bind fails and it runs without
  that input (the sticks come over the loopback MAVLink link instead);
- every few seconds psutil lists SITL's sockets: any address that is not loopback stops the run
  at once (UNKNOWN, nothing measured); the whole process tree started here is killed in `finally`;
- binaries are checked against the SHA256 recorded at install (.mcpfc_sha256.<tool>);
- eeprom, logs and terrain files stay in the study folder (`work`).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _common import result, tool

ENV = "aero"
KINDS = {
    "sitl_copter": {"timeout": 600,
                    "doc": "ArduCopter SITL: GUIDED takeoff, hover, waypoints, land: time to alt, "
                           "overshoot, hover error, hover throttle, tilt, battery, landing"},
    "sitl_sub": {"timeout": 600,
                 "doc": "ArduSub SITL (vectored): depth hold at depth_m: time to depth, depth and "
                        "heading hold error"},
    "sitl_rover": {"timeout": 600,
                   "doc": "ArduRover SITL: GUIDED drive to a point: time, cross-track error, stop error"},
    "sitl_plane": {"timeout": 900,
                   "doc": "ArduPlane SITL AUTO: take-off, circuit, approach, land; aircraft by "
                          "similarity (mass, area, span, CL/Cm slopes, CD0, T/W): ground roll, "
                          "cruise speed and thrust, altitude error, bank, touchdown"},
}

COPTER_FRAMES = {            # SITL model -> (FRAME_CLASS, FRAME_TYPE, motors)
    "quad": (1, 0, 4), "+": (1, 0, 4), "x": (1, 1, 4), "hexa": (2, 0, 6), "hexax": (2, 1, 6),
    "octa": (3, 0, 8), "octa-quad": (4, 0, 8), "y6": (5, 10, 6), "dodeca-hexa": (12, 0, 12),
}
FRAME_KEYS = ("mass", "diagonal_size", "refSpd", "refAngle", "refVoltage", "refCurrent", "refAlt",
              "refTempC", "maxVoltage", "battCapacityAh", "refBatRes", "propExpo", "refRotRate",
              "hoverThrOut", "pwmMin", "pwmMax", "spin_min", "spin_max", "slew_max", "disc_area",
              "mdrag_coef")
HOME = (-35.363261, 149.165230, 584.0, 353.0)   # ArduPilot's standard SITL site (CMAC)
_PARAM = re.compile(r"[A-Z][A-Z0-9_]{1,15}")
_LOOP_CONN = re.compile(r"(tcp|tcpin|tcpclient|udp|udpin|udpout|udpclient):127\.0\.0\.1:\d{1,5}")
_STRICT_SEV = 4                                  # MAV_SEVERITY_WARNING and worse
_ALERT = re.compile(r"fail|crash|error|lane switch|variance|glitch|unhealthy|leak|thrust loss|"
                    r"vibration|compass inconsistent|ekf.*(reset|stopped)", re.I)

# simulated sensor calibration: the SITL IMU/compass carry these offsets, so the autopilot needs
# them to call itself calibrated (ArduPilot Tools/autotest/default_params, same values)
_CAL = {**{f"COMPASS_OFS{s}_{a}": v for s in ("", "2", "3")
           for a, v in (("X", 5), ("Y", 13), ("Z", -18))},
        **{f"INS_ACC{s}OFFS_{a}": 0.001 for s in ("", "2") for a in "XYZ"},
        **{f"INS_ACC{s}SCAL_{a}": 1.001 for s in ("", "2") for a in "XYZ"},
        **{f"INS_ACC3OFFS_{a}": 0.0 for a in "XYZ"}, **{f"INS_ACC3SCAL_{a}": 1.0 for a in "XYZ"}}
_COMMON = {"SERIAL1_PROTOCOL": -1, "SERIAL2_PROTOCOL": -1,     # no second/third MAVLink port
           "TERRAIN_ENABLE": 0, "LOG_DISARMED": 0, "BATT_MONITOR": 4, "SIM_BARO_RND": 0,
           **{f"RC{i}_{k}": v for i in range(1, 9)
              for k, v in (("MIN", 1000), ("MAX", 2000), ("TRIM", 1500))}}


# ------------------------------------------------------------------ versions
def _binary(kind: str) -> Path:
    return Path(tool(kind))          # sitl_copter / sitl_sub / sitl_rover / sitl_plane


def _verify_hashes(exe: Path) -> List[str]:
    """SHA256 of the binary and its Cygwin DLLs against the ones recorded at install."""
    bad = []
    for f in [exe, *sorted(exe.parent.glob("cyg*.dll"))]:
        rec = None
        for marker in exe.parent.glob(".mcpfc_sha256.*"):
            try:
                tools = json.loads(os.environ.get("MCPFC_TOOLS") or "{}")
                name = marker.name.split(".mcpfc_sha256.", 1)[1]
                if Path(tools.get(name, "")).resolve() == f.resolve():
                    rec = marker.read_text(encoding="ascii").split()[0].lower()
                    break
            except (OSError, IndexError):
                continue
        if rec is None:
            bad.append(f"{f.name}: no recorded SHA256")
            continue
        if hashlib.sha256(f.read_bytes()).hexdigest() != rec:
            bad.append(f"{f.name}: SHA256 differs from the one recorded at install")
    return bad


def versions() -> Dict[str, str]:
    import pymavlink
    out = {"pymavlink": getattr(pymavlink, "__version__", "?")}
    try:
        from importlib.metadata import version
        out["pymavlink"] = version("pymavlink")
    except Exception:  # noqa: BLE001
        pass
    for k in ("sitl_copter", "sitl_plane", "sitl_sub", "sitl_rover"):
        try:
            data = _binary(k).read_bytes()
            m = re.search(rb"(Ardu\w+) V(\d+\.\d+\.\d+)", data)
            out[k] = m.group(2).decode() if m else "?"
        except (FileNotFoundError, OSError):
            out[k] = "not installed"
    return out


# ------------------------------------------------------------------ inputs
def _num(inp: Dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    v = float(inp.get(key, default))
    if not (lo <= v <= hi) or not math.isfinite(v):
        raise ValueError(f"{key} = {v:g} out of [{lo:g}, {hi:g}]")
    return v


def _refuse_links(inp: Dict[str, Any]) -> None:
    """No caller-chosen link: SITL on loopback is the only target, ever (§10.2)."""
    for key in ("connection", "connect", "device", "serial", "port", "address", "url", "mavlink",
                "uart", "baud"):
        # `device` cpu/gpu/both is the compute choice the runner adds to every input, not a link
        if key == "device" and inp.get(key) in ("cpu", "gpu", "both"):
            continue
        if key in inp:
            raise PermissionError(f"{key!r} refused: this adapter only talks to the SITL it starts "
                                  "itself on 127.0.0.1 (no link to a real vehicle, no serial port)")


def _user_params(inp: Dict[str, Any]) -> Dict[str, float]:
    out = {}
    for k, v in (inp.get("params") or {}).items():
        if not _PARAM.fullmatch(str(k)):
            raise ValueError(f"param name {k!r}: ArduPilot names are A-Z, 0-9, _ (max 16)")
        if str(k).startswith(("SERIAL", "NET_", "SCR_")):
            raise PermissionError(f"param {k} refused: ports, networking and scripting stay as set "
                                  "by the adapter")
        v = float(v)
        if not math.isfinite(v):
            raise ValueError(f"param {k}: not a finite number")
        out[str(k)] = v
    return out


def _home(inp: Dict[str, Any], default=HOME) -> str:
    h = inp.get("home") or default
    if len(h) != 4:
        raise ValueError("home: [lat_deg, lon_deg, alt_m_AMSL, heading_deg]")
    lat, lon, alt, hdg = (float(x) for x in h)
    if not (-85 <= lat <= 85 and -180 <= lon <= 180 and -500 <= alt <= 9000 and 0 <= hdg < 360):
        raise ValueError("home out of range")
    return f"{lat:.7f},{lon:.7f},{alt:.2f},{hdg:.1f}"


def _write_defaults(work: Path, params: Dict[str, float]) -> str:
    lines = [f"{k} {v:.9g}" for k, v in params.items()]
    (work / "defaults.parm").write_text("\n".join(lines) + "\n", encoding="ascii")
    return "defaults.parm"


# ------------------------------------------------------------------ the SITL process
def _is_loopback(ip: str) -> bool:
    return ip.startswith("127.") or ip in ("::1", "localhost")


class Sitl:
    """One SITL process on loopback: start, link, socket audit, kill (context manager)."""

    def __init__(self, kind: str, work: Path, model: str, params: Dict[str, float], home: str,
                 speedup: float):
        self.kind, self.work, self.model = kind, work, model
        self.params, self.home, self.speedup = params, home, speedup
        self.proc: Optional[subprocess.Popen] = None
        self.blocker: Optional[socket.socket] = None
        self.mav = None
        self.violation: Optional[str] = None
        self._last_audit = 0.0
        self._log = None
        self.session: Optional["Session"] = None

    def __enter__(self) -> "Sitl":
        exe = _binary(self.kind)
        bad = _verify_hashes(exe)
        if bad:
            raise PermissionError("SITL binary not trusted: " + "; ".join(bad))
        from pymavlink import mavutil
        # a free instance: its RC-input port (5501 + 10 I) is taken here, exclusively, BEFORE
        # SITL starts, so SITL's own 0.0.0.0 bind of it fails (see module doc)
        rng = random.SystemRandom()
        for _ in range(40):
            inst = rng.randint(1, 250)          # SITL keeps the instance in 8 bits
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
                s.bind(("0.0.0.0", 5501 + 10 * inst))
                s.connect(("127.0.0.1", 9))      # a connected UDP socket drops every other sender
                self.blocker = s
                break
            except OSError:
                s.close()
        if self.blocker is None:
            raise RuntimeError("no free SITL instance number (RC-input ports all busy)")
        self.instance = inst
        for _ in range(10):
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            conn = f"tcpin:127.0.0.1:{port}"
            if not _LOOP_CONN.fullmatch(conn):
                raise PermissionError(f"link {conn} is not loopback")
            try:
                self.mav = mavutil.mavlink_connection(conn, source_system=255, source_component=190)
                break
            except OSError:
                continue
        if self.mav is None:
            raise RuntimeError("could not open the loopback MAVLink listener")
        device = f"tcpclient:127.0.0.1:{port}"
        if not _LOOP_CONN.fullmatch(device):
            raise PermissionError(f"SITL device {device} is not loopback")
        args = [str(exe), "-w", "--model", self.model, "--instance", str(inst),
                "--speedup", f"{self.speedup:g}", "--home", self.home,
                "--defaults", _write_defaults(self.work, self.params), "--serial0", device]
        (self.work / "sitl_command.json").write_text(json.dumps(args, indent=1), encoding="utf-8")
        tmp = self.work / "tmp"
        tmp.mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items()
               if k.upper() in ("SYSTEMROOT", "WINDIR", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS")}
        env.update(PATH=os.pathsep.join([str(exe.parent), os.path.join(env.get("SYSTEMROOT", r"C:\Windows"), "System32")]),
                   TEMP=str(tmp), TMP=str(tmp), HOME=str(self.work), CYGWIN="nodosfilewarning")
        self._log = open(self.work / "sitl.log", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
        flags = (0x08000000 | 0x00000200) if os.name == "nt" else 0   # NO_WINDOW | NEW_PROCESS_GROUP
        self.proc = subprocess.Popen(args, cwd=str(self.work), env=env, stdin=subprocess.DEVNULL,
                                     stdout=self._log, stderr=subprocess.STDOUT, creationflags=flags)
        return self

    def audit(self, force: bool = False) -> Optional[str]:
        """Every socket of the SITL tree must be on loopback; else stop (returns the reason)."""
        if self.violation or (not force and time.time() - self._last_audit < 2.0):
            return self.violation
        self._last_audit = time.time()
        import psutil
        try:
            root = psutil.Process(self.proc.pid)
            for p in [root, *root.children(recursive=True)]:
                for c in p.net_connections(kind="inet"):
                    ips = [c.laddr.ip] + ([c.raddr.ip] if c.raddr else [])
                    if not all(_is_loopback(ip) for ip in ips):
                        self.violation = (f"SITL ({p.name()}) opened {c.laddr.ip}:{c.laddr.port}"
                                          f"{' -> ' + c.raddr.ip if c.raddr else ''}: not loopback, "
                                          "run stopped")
                        self.kill()
                        return self.violation
        except psutil.NoSuchProcess:
            pass
        return None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def kill(self) -> None:
        if self.proc is None:
            return
        try:
            import psutil
            root = psutil.Process(self.proc.pid)
            for c in root.children(recursive=True):
                try:
                    c.kill()
                except psutil.Error:
                    pass
            root.kill()
        except Exception:  # noqa: BLE001 - already gone
            pass
        try:
            self.proc.wait(10)
        except Exception:  # noqa: BLE001
            pass

    def __exit__(self, *exc) -> None:
        try:
            if self.session is not None:          # what happened, kept for the human
                lines = [f"{t:9.2f} sev{sev} {txt}" for t, sev, txt in self.session.texts]
                (self.work / "statustext.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
                if self.session.samples and not (self.work / "telemetry.csv").exists():
                    _save_samples(self.work, self.session.samples, [])
        except OSError:
            pass
        try:
            if self.mav is not None:
                self.mav.close()
        finally:
            self.kill()
            if self.blocker is not None:
                self.blocker.close()
            if self._log is not None:
                self._log.close()


# ------------------------------------------------------------------ MAVLink session
class Session:
    """Reads the telemetry, keeps the sticks/heartbeat alive, records samples (sim time)."""

    def __init__(self, sitl: Sitl, wall_budget_s: float, sticks: str):
        self.sitl, self.m = sitl, sitl.mav
        self.t_end = time.time() + wall_budget_s
        self.sticks = sticks              # "rc" (copter/rover override) or "manual" (ArduSub)
        self.throttle_pwm = 1000
        self.t_ms = 0                     # autopilot boot time (sim), ms
        self.armed = False
        self.mode = None
        self.alt = math.nan               # m above home (EKF)
        self.north = self.east = math.nan
        self.vz = 0.0
        self.roll = self.pitch = self.yaw = 0.0
        self.throttle = math.nan
        self.airspeed = self.groundspeed = math.nan
        self.mission_seq = -1
        self.mission_req = -1
        self.mission_ack: Optional[int] = None
        self.mah = math.nan
        self.energy_hj = math.nan
        self.voltage = math.nan
        self.texts: List[Tuple[float, int, str]] = []
        self.samples: List[Dict[str, float]] = []
        self.recording = False
        self._last_tx = 0.0
        self.boot_seen = False
        self.acks: Dict[int, int] = {}
        self.t_armed = math.inf           # sim time of arming: alerts are counted from then
        self.ekf_flags = 0
        self.repeat = None                # (period_s, fn): a set-point streamed like a GCS does
        self._last_repeat = -1e9

    # ---- low level
    def _tx(self) -> None:
        now = time.time()
        if now - self._last_tx < 0.05:
            return
        self._last_tx = now
        mav = self.m.mav
        mav.heartbeat_send(6, 8, 0, 0, 0)                    # GCS heartbeat
        if self.sticks == "rc":
            mav.rc_channels_override_send(1, 0, 1500, 1500, self.throttle_pwm, 1500,
                                          *([65535] * 4))
        else:
            mav.manual_control_send(1, 0, 0, 500, 0, 0)       # neutral: hold

    def pump(self, seconds_wall: float = 0.05) -> None:
        if time.time() > self.t_end:
            raise TimeoutError("wall-clock budget of the run exhausted")
        if not self.sitl.alive():
            raise RuntimeError("SITL exited: " + _tail(self.sitl.work / "sitl.log"))
        if self.sitl.audit():
            raise PermissionError(self.sitl.violation)
        self._tx()
        if self.repeat and self.t_ms / 1000.0 - self._last_repeat >= self.repeat[0]:
            self._last_repeat = self.t_ms / 1000.0
            self.repeat[1]()
        t_stop = time.time() + seconds_wall
        while True:
            msg = self.m.recv_match(blocking=True, timeout=0.02)
            if msg is not None:
                self._handle(msg)
            if time.time() >= t_stop:
                break

    def _handle(self, msg) -> None:
        t = msg.get_type()
        if t == "BAD_DATA":
            return
        if msg.get_srcSystem() != 1:
            return
        tb = getattr(msg, "time_boot_ms", None)
        if tb is not None:
            self.t_ms = max(self.t_ms, int(tb))
        if t == "HEARTBEAT" and msg.get_srcComponent() == 1:
            self.boot_seen = True
            self.armed = bool(msg.base_mode & 128)
            self.mode = int(msg.custom_mode)
        elif t == "GLOBAL_POSITION_INT":
            self.alt = msg.relative_alt / 1000.0
            self.vz = msg.vz / 100.0
            if self.recording:
                self.samples.append({"t": self.t_ms / 1000.0, "alt": self.alt,
                                     "roll": self.roll, "pitch": self.pitch, "yaw": self.yaw,
                                     "thr": self.throttle, "n": self.north, "e": self.east,
                                     "v": self.voltage, "as": self.airspeed, "vz": self.vz,
                                     "seq": self.mission_seq})
        elif t == "LOCAL_POSITION_NED":
            self.north, self.east = msg.x, msg.y
        elif t == "ATTITUDE":
            self.roll, self.pitch, self.yaw = msg.roll, msg.pitch, msg.yaw
        elif t == "VFR_HUD":
            self.throttle = msg.throttle / 100.0
            self.airspeed, self.groundspeed = float(msg.airspeed), float(msg.groundspeed)
        elif t in ("MISSION_REQUEST_INT", "MISSION_REQUEST"):
            self.mission_req = int(msg.seq)
        elif t == "MISSION_ACK":
            self.mission_ack = int(msg.type)
        elif t == "MISSION_CURRENT":
            self.mission_seq = int(msg.seq)
        elif t == "BATTERY_STATUS":
            if msg.current_consumed >= 0:
                self.mah = float(msg.current_consumed)
            if msg.energy_consumed >= 0:
                self.energy_hj = float(msg.energy_consumed)
            if msg.voltages and msg.voltages[0] not in (0, 65535):
                self.voltage = msg.voltages[0] / 1000.0
        elif t == "STATUSTEXT":
            self.texts.append((self.t_ms / 1000.0, int(msg.severity), str(msg.text)[:120]))
        elif t == "EKF_STATUS_REPORT":
            self.ekf_flags = int(msg.flags)
        elif t == "COMMAND_ACK":
            self.acks[int(msg.command)] = int(msg.result)

    def wait(self, cond, sim_s: float, what: str) -> bool:
        """Pump until cond() or sim_s simulated seconds pass (False on timeout)."""
        t0 = self.t_ms
        while not cond():
            self.pump()
            if self.t_ms - t0 > sim_s * 1000:
                return False
        return True

    def sleep_sim(self, sim_s: float) -> None:
        t0 = self.t_ms
        while self.t_ms - t0 < sim_s * 1000:
            self.pump()

    # ---- commands
    def command(self, cmd: int, *p: float) -> None:
        p = list(p) + [0.0] * (7 - len(p))
        self.acks.pop(cmd, None)
        self.m.mav.command_long_send(1, 1, cmd, 0, *p)

    def command_ack(self, cmd: int, *p: float, sim_s: float = 5.0) -> Optional[int]:
        self.command(cmd, *p)
        self.wait(lambda: cmd in self.acks, sim_s, "ack")
        return self.acks.get(cmd)

    def set_mode(self, mode: int, sim_s: float = 10.0) -> bool:
        for _ in range(5):
            self.command(176, 1, mode)                          # MAV_CMD_DO_SET_MODE, custom
            if self.wait(lambda: self.mode == mode, sim_s / 5, "mode"):
                return True
        return False

    def upload_mission(self, items: List[Dict[str, Any]], sim_s: float = 30.0) -> bool:
        """MAVLink mission protocol: COUNT, answer each MISSION_REQUEST(_INT), wait for the ACK."""
        self.mission_ack, self.mission_req = None, -1
        t0, last_count = self.t_ms, -1e9
        while self.mission_ack is None:
            if self.mission_req < 0 and self.t_ms - last_count > 3000:
                self.m.mav.mission_count_send(1, 1, len(items), 0)
                last_count = self.t_ms
            self.pump()
            if 0 <= self.mission_req < len(items):
                q, it = self.mission_req, items[self.mission_req]
                self.mission_req = -1
                self.m.mav.mission_item_int_send(1, 1, q, it["frame"], it["cmd"], 0, 1, *it["p"],
                                                 it["lat"], it["lon"], it["alt"], 0)
            if self.t_ms - t0 > sim_s * 1000:
                return False
        return self.mission_ack == 0

    def streams(self) -> None:
        mav = self.m.mav
        mav.request_data_stream_send(1, 1, 0, 10, 1)            # all streams 10 Hz
        for msg_id, hz in ((33, 25), (30, 25), (32, 10), (74, 10), (147, 2)):
            mav.command_long_send(1, 1, 511, 0, msg_id, 1e6 / hz, 0, 0, 0, 0, 0)

    def wait_ekf(self, need: int, sim_s: float = 90.0) -> bool:
        """EKF_STATUS_REPORT flags: 1 attitude, 2 horiz vel, 4 vert vel, 16 horiz pos abs,
        32 vert pos abs; 128 = constant position mode (no GPS yet), must be clear."""
        return self.wait(lambda: (self.ekf_flags & need) == need and not self.ekf_flags & 128,
                         sim_s, "ekf")

    def arm(self, sim_s: float = 90.0) -> bool:
        """Retry arming until the pre-arm checks pass (EKF, GPS lock...)."""
        t0 = self.t_ms
        while self.t_ms - t0 < sim_s * 1000:
            self.command(400, 1)                                # MAV_CMD_COMPONENT_ARM_DISARM
            if self.wait(lambda: self.armed, 2.0, "arm"):
                self.t_armed = self.t_ms / 1000.0
                return True
        return False

    def alerts(self) -> List[str]:
        """Warnings and worse once armed (failsafe, EKF, crash...), as reported by the autopilot."""
        return [f"t={t:.1f}s {txt}" for t, sev, txt in self.texts
                if t >= self.t_armed and (sev <= _STRICT_SEV or _ALERT.search(txt))]

    def prearm(self) -> List[str]:
        """Pre-arm refusals (why arming waited or failed)."""
        seen, out = set(), []
        for t, sev, txt in self.texts:
            low = txt.lower()
            if (t < self.t_armed and (sev <= _STRICT_SEV or low.startswith(("prearm", "arm:")))
                    and not low.startswith(("arming motors", "throttle armed")) and txt not in seen):
                seen.add(txt)
                out.append(txt)
        return out


def _tail(p: Path, n: int = 300) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[-n:].replace("\n", " | ")
    except OSError:
        return ""


def _startup(sitl: Sitl, budget: float, sticks: str) -> Session:
    s = Session(sitl, budget, sticks)
    sitl.session = s
    # SITL connects to our listener, then boots
    t0 = time.time()
    while not s.boot_seen:
        s.pump(0.1)
        if time.time() - t0 > 60:
            raise RuntimeError("no heartbeat from SITL in 60 s: " + _tail(sitl.work / "sitl.log"))
    if sitl.audit(force=True):
        raise PermissionError(sitl.violation)
    s.streams()
    return s


def _tilt_deg(roll: float, pitch: float) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, math.cos(roll) * math.cos(pitch)))))


def _wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _stats(xs: List[float]) -> Tuple[float, float, float]:
    if not xs:
        return math.nan, math.nan, math.nan
    mean = sum(xs) / len(xs)
    rms = math.sqrt(sum(x * x for x in xs) / len(xs))
    return mean, rms, max(abs(x) for x in xs)


def _unknown(head: str, notes: List[str], values: Dict[str, Any], files: List[str]) -> Dict[str, Any]:
    return {"verdict": "UNKNOWN", "summary": "; ".join([head, *notes]), "values": values,
            "files": files}


# ------------------------------------------------------------------ copter
def _copter_params(inp: Dict[str, Any], model: str, work: Path) -> Tuple[Dict[str, float], str, List[str]]:
    base = model.split(":")[0]
    if base not in COPTER_FRAMES:
        raise ValueError(f"model {model!r}: one of {sorted(COPTER_FRAMES)}")
    fc, ft, n_motors = COPTER_FRAMES[base]
    notes = []
    frame = dict(inp.get("frame") or {})
    unknown = sorted(set(frame) - set(FRAME_KEYS))
    if unknown:
        raise ValueError(f"frame keys not used by SITL: {unknown}; known: {list(FRAME_KEYS)}")
    if "thrust_to_weight" in inp:
        twr = _num(inp, "thrust_to_weight", 2.0, 1.1, 10.0)
        frame["hoverThrOut"] = 1.0 / twr
        notes.append(f"hoverThrOut = 1/T/W = {1 / twr:.3f}")
    for k, v in frame.items():
        if not isinstance(v, (int, float)) or not math.isfinite(float(v)) or float(v) < 0:
            raise ValueError(f"frame.{k}: a non-negative number")
    if "mass" in frame and not 0.05 <= float(frame["mass"]) <= 100:
        raise ValueError("frame.mass: 0.05 .. 100 kg")
    if "hoverThrOut" in frame and not 0.1 <= float(frame["hoverThrOut"]) <= 0.9:
        raise ValueError("frame.hoverThrOut: 0.1 .. 0.9 (thrust-to-weight 1.1 .. 10)")
    params = dict(_CAL, **_COMMON)
    params.update(FRAME_CLASS=fc, FRAME_TYPE=ft, FS_THR_ENABLE=0, FS_GCS_ENABLE=0,
                  RTL_ALT=1500, DISARM_DELAY=10, MOT_HOVER_LEARN=2,
                  MOT_THST_EXPO=float(frame.get("propExpo", 0.65)),
                  MOT_THST_HOVER=float(frame.get("hoverThrOut", 0.39)),
                  MOT_SPIN_MIN=float(frame.get("spin_min", 0.15)),
                  MOT_SPIN_MAX=float(frame.get("spin_max", 0.95)),
                  MOT_PWM_MIN=float(frame.get("pwmMin", 1000)),
                  MOT_PWM_MAX=float(frame.get("pwmMax", 2000)))
    cap = float(frame.get("battCapacityAh", 0.0))
    params["SIM_BATT_CAP_AH"] = cap                   # SITL re-reads the capacity from this one
    if cap > 0:
        params["BATT_CAPACITY"] = cap * 1000
    if "maxVoltage" in frame:
        params["SIM_BATT_VOLTAGE"] = float(frame["maxVoltage"])
    if inp.get("wind_mps"):
        params["SIM_WIND_SPD"] = _num(inp, "wind_mps", 0, 0, 30)
        params["SIM_WIND_DIR"] = _num(inp, "wind_dir_deg", 0, 0, 360)
    params.update(_user_params(inp))
    if frame:
        frame_file = {k: float(v) for k, v in frame.items()}
        frame_file["num_motors"] = float(n_motors)
        (work / "frame.json").write_text(json.dumps(frame_file, indent=1), encoding="utf-8")
        model = f"{base}:frame.json"
    return params, model, notes


def _run_copter(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    alt = _num(inp, "alt_m", 10, 1, 120)
    hover_s = _num(inp, "hover_s", 15, 1, 120)
    settle_s = _num(inp, "settle_s", 3, 0, 30)
    speedup = _num(inp, "speedup", 5, 1, 20)
    wps = inp.get("waypoints") or []
    if len(wps) > 10:
        raise ValueError("waypoints: at most 10")
    for w in wps:
        if len(w) != 3 or abs(float(w[0])) > 500 or abs(float(w[1])) > 500 or not 1 <= float(w[2]) <= 120:
            raise ValueError("waypoint: [north_m, east_m, alt_m] within 500 m, alt 1..120 m")
    model = str(inp.get("model", "quad"))
    params, model, notes = _copter_params(inp, model, work)
    values: Dict[str, Any] = {"alt_target_m": alt, "speedup": speedup}
    files = ["defaults.parm", "sitl.log", "sitl_command.json", "statustext.log"] + (["frame.json"] if (work / "frame.json").exists() else [])
    head = f"ArduCopter SITL {model}"
    with Sitl("sitl_copter", work, model, params, _home(inp), speedup) as sitl:
        try:
            s = _startup(sitl, float(inp.get("wall_budget_s", 420)), "rc")
            if not s.wait_ekf(1 | 2 | 4 | 16 | 32):
                return _unknown(head, ["EKF never reported a GPS position (90 s)", *s.prearm()[-5:]],
                                values, files)
            if not s.set_mode(4):                                  # GUIDED
                return _unknown(head, ["GUIDED refused", *s.alerts()[-5:]], values, files)
            if not s.arm():
                return _unknown(head, ["could not arm (pre-arm checks)", *s.prearm()[-6:]], values, files)
            mah0, e0 = s.mah, s.energy_hj
            s.recording = True
            t_cmd = s.t_ms / 1000.0
            for _ in range(5):                                     # MAV_CMD_NAV_TAKEOFF
                ack = s.command_ack(22, 0, 0, 0, 0, 0, 0, alt)
                if ack in (0, None) or not s.armed:
                    break
                s.sleep_sim(1)
            if ack not in (0, None):
                return _unknown(head, [f"takeoff refused (MAV_RESULT {ack})", *s.alerts()[-5:]], values, files)
            climb_limit = alt / 0.5 + 30                           # > 0.5 m/s mean, else stuck
            if not s.wait(lambda: s.alt >= 0.95 * alt, climb_limit, "climb"):
                values["alt_reached_m"] = max((x["alt"] for x in s.samples), default=math.nan)
                return _unknown(head, [f"altitude {alt:g} m not reached in {climb_limit:.0f} s",
                                       *s.alerts()[-5:]], values, files)
            t_reach = s.t_ms / 1000.0
            values["time_to_alt_s"] = t_reach - t_cmd
            s.sleep_sim(settle_s + hover_s)
            t_hover0, t_hover1 = t_reach + settle_s, s.t_ms / 1000.0
            hov = [x for x in s.samples if t_hover0 <= x["t"] <= t_hover1]
            err = [x["alt"] - alt for x in hov]
            values["alt_overshoot_m"] = max(0.0, max(x["alt"] for x in s.samples) - alt)
            mean, rms, mx = _stats(err)
            values.update(hover_mean_error_m=mean, hover_rms_error_m=rms, hover_max_error_m=mx,
                          hover_window_s=t_hover1 - t_hover0)
            thr = [x["thr"] for x in hov if math.isfinite(x["thr"])]
            values["hover_throttle"] = sum(thr) / len(thr) if thr else math.nan
            vv = [x["v"] for x in hov if math.isfinite(x["v"])]
            if vv:
                values["hover_voltage_V"] = sum(vv) / len(vv)
            # waypoints (relative to the takeoff point, north/east/up)
            n0 = hov[0]["n"] if hov and math.isfinite(hov[0]["n"]) else 0.0
            e0_ = hov[0]["e"] if hov and math.isfinite(hov[0]["e"]) else 0.0
            reached = 0
            wp_times = []
            for (wn, we, wa) in wps:
                tn, te, ta = n0 + float(wn), e0_ + float(we), float(wa)
                t_w = s.t_ms
                s.m.mav.set_position_target_local_ned_send(0, 1, 1, 1, 0b0000111111111000,
                                                           tn, te, -ta, 0, 0, 0, 0, 0, 0, 0, 0)
                dist0 = math.dist((s.north, s.east, s.alt), (tn, te, ta))
                ok = s.wait(lambda: math.dist((s.north, s.east, s.alt), (tn, te, ta)) < 1.0,
                            dist0 / 1.0 + 30, "waypoint")
                if not ok:
                    notes.append(f"waypoint {[wn, we, wa]} not reached")
                    break
                reached += 1
                wp_times.append(round((s.t_ms - t_w) / 1000.0, 2))
            if wps:
                values.update(waypoints_reached=reached, waypoints_total=len(wps), waypoint_times_s=wp_times)
            # land where it is
            t_land = s.t_ms / 1000.0
            if not s.set_mode(9):                                  # LAND
                return _unknown(head, ["LAND refused", *s.alerts()[-5:]], values, files)
            landed = s.wait(lambda: not s.armed, max(s.alt, alt) / 0.3 + 30, "land")
            values["land_time_s"] = s.t_ms / 1000.0 - t_land
            air = [x for x in s.samples if x["t"] >= t_cmd]
            values["max_tilt_deg"] = max((_tilt_deg(x["roll"], x["pitch"]) for x in air), default=math.nan)
            values["landed_disarmed"] = 1.0 if (landed and abs(s.alt) < 0.5) else 0.0
            values["final_alt_m"] = s.alt
            if math.isfinite(s.mah) and math.isfinite(mah0):
                values["battery_consumed_mAh"] = s.mah - mah0
            if math.isfinite(s.energy_hj) and math.isfinite(e0):
                values["battery_consumed_Wh"] = (s.energy_hj - e0) / 36.0
            if math.isfinite(s.voltage):
                values["battery_voltage_end_V"] = s.voltage
            alerts = s.alerts()
            values["prearm_messages"] = s.prearm()[-8:]
            values["n_alerts"] = len(alerts)
            values["alerts"] = alerts[-15:]
            values["flight_time_s"] = s.t_ms / 1000.0 - t_cmd
            _save_samples(work, s.samples, files)
            sitl.audit(force=True)
            if sitl.violation:
                raise PermissionError(sitl.violation)
        except PermissionError as e:
            return _unknown(head, [str(e)], {}, files)
    head += (f": to {alt:g} m in {values['time_to_alt_s']:.1f} s, overshoot {values['alt_overshoot_m']:.2f} m,"
             f" hover error mean {values['hover_mean_error_m']:+.2f} m rms {values['hover_rms_error_m']:.2f} m,"
             f" throttle {values['hover_throttle']:.3f}, max tilt {values['max_tilt_deg']:.1f} deg, "
             f"{'landed and disarmed' if values['landed_disarmed'] else 'LANDING NOT CONFIRMED'}")
    if values["n_alerts"]:
        notes.append(f"{values['n_alerts']} autopilot warnings (values.alerts)")
    notes.append("SITL built-in physics, EKF altitude; simulated seconds")
    return result(values, inp.get("limits"), head, files, notes)


def _save_samples(work: Path, samples: List[Dict[str, float]], files: List[str]) -> None:
    keys = ["t", "alt", "n", "e", "roll", "pitch", "yaw", "thr", "v", "as", "vz", "seq"]
    lines = [",".join(keys)] + [",".join(f"{float(x.get(k, math.nan)):.4f}" for k in keys)
                                for x in samples]
    (work / "telemetry.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files.append("telemetry.csv")


# ------------------------------------------------------------------ sub
def _run_sub(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    depth = _num(inp, "depth_m", 5, 0.5, 90)
    hold_s = _num(inp, "hold_s", 20, 1, 120)
    settle_s = _num(inp, "settle_s", 3, 0, 30)
    speedup = _num(inp, "speedup", 5, 1, 20)
    model = str(inp.get("model", "vectored"))
    if model not in ("vectored", "vectored_6dof"):
        raise ValueError("model: vectored | vectored_6dof")
    params = dict(_CAL, **_COMMON)
    params.update(FRAME_CONFIG=2 if model == "vectored_6dof" else 1,
                  FS_GCS_ENABLE=0, SIM_BARO_RND=0.02, PILOT_SPEED_UP=200, PILOT_ACCEL_Z=200,
                  # position-controller gains of ArduPilot's SITL sub defaults
                  PSC_D_ACC_P=0.2, PSC_D_ACC_I=0.4, PSC_D_ACC_FF=0.075, PSC_D_VEL_P=8,
                  PSC_D_POS_P=3, PSC_D_JERK=8)
    params.update(_user_params(inp))
    values: Dict[str, Any] = {"depth_target_m": depth, "speedup": speedup}
    files = ["defaults.parm", "sitl.log", "sitl_command.json", "statustext.log"]
    head = f"ArduSub SITL {model}"
    with Sitl("sitl_sub", work, model, params, _home(inp, (HOME[0], HOME[1], 0.0, HOME[3])), speedup) as sitl:
        try:
            s = _startup(sitl, float(inp.get("wall_budget_s", 420)), "manual")
            if not s.wait_ekf(1 | 4 | 32, 60):
                return _unknown(head, ["EKF attitude/depth never ready (60 s)", *s.prearm()[-5:]],
                                values, files)
            s.sleep_sim(2)
            if not s.set_mode(19):                                 # MANUAL
                return _unknown(head, ["MANUAL refused", *s.alerts()[-5:]], values, files)
            if not s.arm():
                return _unknown(head, ["could not arm (pre-arm checks)", *s.prearm()[-6:]], values, files)
            if not s.set_mode(2):                                  # ALT_HOLD = depth hold
                return _unknown(head, ["ALT_HOLD (depth hold) refused", *s.alerts()[-5:]], values, files)
            s.sleep_sim(1)
            yaw0 = math.degrees(s.yaw)
            mah0 = s.mah
            s.recording = True
            t_cmd = s.t_ms / 1000.0
            # depth target (ArduSub: ALT_HOLD takes only z of SET_POSITION_TARGET_GLOBAL_INT)
            # depth set-point streamed at 1 Hz (ArduSub ALT_HOLD takes only z of
            # SET_POSITION_TARGET_GLOBAL_INT; one far set-point is clipped to the controller's
            # error limit, a GCS keeps sending it). type_mask: everything but z ignored.
            s.repeat = (1.0, lambda: s.m.mav.set_position_target_global_int_send(
                0, 1, 1, 6, 0b0000111111111011, 0, 0, -depth, 0, 0, 0, 0, 0, 0, 0, 0))
            lim = depth / 0.25 + 30
            if not s.wait(lambda: -s.alt >= 0.95 * depth, lim, "descent"):
                values["depth_reached_m"] = max((-x["alt"] for x in s.samples), default=math.nan)
                return _unknown(head, [f"depth {depth:g} m not reached in {lim:.0f} s", *s.alerts()[-5:]],
                                values, files)
            t_reach = s.t_ms / 1000.0
            values["time_to_depth_s"] = t_reach - t_cmd
            s.sleep_sim(settle_s + hold_s)
            t0, t1 = t_reach + settle_s, s.t_ms / 1000.0
            hold = [x for x in s.samples if t0 <= x["t"] <= t1]
            mean, rms, mx = _stats([(-x["alt"]) - depth for x in hold])
            values.update(depth_mean_error_m=mean, depth_rms_error_m=rms, depth_max_error_m=mx,
                          hold_window_s=t1 - t0,
                          depth_overshoot_m=max(0.0, max(-x["alt"] for x in s.samples) - depth))
            hmean, hrms, hmax = _stats([_wrap_deg(math.degrees(x["yaw"]) - yaw0) for x in s.samples])
            values.update(heading_mean_error_deg=hmean, heading_rms_error_deg=hrms,
                          heading_max_error_deg=hmax)
            values["max_tilt_deg"] = max((_tilt_deg(x["roll"], x["pitch"]) for x in s.samples), default=math.nan)
            if math.isfinite(s.mah) and math.isfinite(mah0):
                values["battery_consumed_mAh"] = s.mah - mah0
            s.command(400, 0)                                      # disarm
            s.wait(lambda: not s.armed, 5, "disarm")
            alerts = s.alerts()
            values["prearm_messages"] = s.prearm()[-8:]
            values["n_alerts"] = len(alerts)
            values["alerts"] = alerts[-15:]
            _save_samples(work, s.samples, files)
            sitl.audit(force=True)
            if sitl.violation:
                raise PermissionError(sitl.violation)
        except PermissionError as e:
            return _unknown(head, [str(e)], {}, files)
    head += (f": to {depth:g} m in {values['time_to_depth_s']:.1f} s, depth error mean "
             f"{values['depth_mean_error_m']:+.3f} m rms {values['depth_rms_error_m']:.3f} m, heading error "
             f"max {values['heading_max_error_deg']:.2f} deg")
    notes = ["SITL built-in physics, EKF depth; simulated seconds"]
    if values["n_alerts"]:
        notes.append(f"{values['n_alerts']} autopilot warnings (values.alerts)")
    return result(values, inp.get("limits"), head, files, notes)


# ------------------------------------------------------------------ rover
def _run_rover(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    north = _num(inp, "north_m", 50, -500, 500)
    east = _num(inp, "east_m", 0, -500, 500)
    if math.hypot(north, east) < 5:
        raise ValueError("target closer than 5 m")
    speed = _num(inp, "speed_mps", 5, 0.5, 20)
    speedup = _num(inp, "speedup", 5, 1, 20)
    model = str(inp.get("model", "rover"))
    if model not in ("rover", "rover-skid"):
        raise ValueError("model: rover | rover-skid")
    params = dict(_CAL, **_COMMON)
    params.update(FS_THR_ENABLE=0, FS_GCS_ENABLE=0, CRUISE_SPEED=speed,
                  WP_SPEED=speed, WP_RADIUS=2, CRUISE_THROTTLE=30, ATC_SPEED_P=0.1,
                  ATC_STR_RAT_FF=0.75)
    if model == "rover-skid":
        params.update(SERVO1_FUNCTION=73, SERVO3_FUNCTION=74)
    params.update(_user_params(inp))
    values: Dict[str, Any] = {"target_distance_m": math.hypot(north, east), "speedup": speedup}
    files = ["defaults.parm", "sitl.log", "sitl_command.json", "statustext.log"]
    head = f"ArduRover SITL {model}"
    with Sitl("sitl_rover", work, model, params, _home(inp), speedup) as sitl:
        try:
            s = _startup(sitl, float(inp.get("wall_budget_s", 420)), "rc")
            s.throttle_pwm = 1500                                  # rover: neutral throttle stick
            if not s.wait_ekf(1 | 2 | 16) or not s.wait(lambda: math.isfinite(s.north), 10, "pos"):
                return _unknown(head, ["EKF never reported a GPS position (90 s)", *s.prearm()[-5:]],
                                values, files)
            if not s.set_mode(15):                                 # GUIDED
                return _unknown(head, ["GUIDED refused", *s.alerts()[-5:]], values, files)
            if not s.arm():
                return _unknown(head, ["could not arm (pre-arm checks)", *s.prearm()[-6:]], values, files)
            s.sleep_sim(1)
            n0, e0 = s.north, s.east
            tn, te = n0 + north, e0 + east
            s.recording = True
            t_cmd = s.t_ms / 1000.0
            s.m.mav.set_position_target_local_ned_send(0, 1, 1, 1, 0b0000111111111100,
                                                       tn, te, 0, 0, 0, 0, 0, 0, 0, 0, 0)
            lim = math.hypot(north, east) / (0.3 * speed) + 30
            ok = s.wait(lambda: math.hypot(s.north - tn, s.east - te) < 2.5, lim, "drive")
            if not ok:
                return _unknown(head, ["target not reached", *s.alerts()[-5:]], values, files)
            values["time_to_target_s"] = s.t_ms / 1000.0 - t_cmd
            s.sleep_sim(5)
            values["stop_error_m"] = math.hypot(s.north - tn, s.east - te)
            # cross-track error: distance of the path to the straight line start -> target
            L = math.hypot(north, east)
            ux, uy = north / L, east / L
            xt = [abs(-(x["n"] - n0) * uy + (x["e"] - e0) * ux) for x in s.samples if math.isfinite(x["n"])]
            values["max_cross_track_m"] = max(xt) if xt else math.nan
            values["mean_speed_mps"] = L / values["time_to_target_s"]
            s.command(400, 0)
            alerts = s.alerts()
            values["prearm_messages"] = s.prearm()[-8:]
            values["n_alerts"] = len(alerts)
            values["alerts"] = alerts[-15:]
            _save_samples(work, s.samples, files)
            sitl.audit(force=True)
            if sitl.violation:
                raise PermissionError(sitl.violation)
        except PermissionError as e:
            return _unknown(head, [str(e)], {}, files)
    head += (f": {values['target_distance_m']:.0f} m in {values['time_to_target_s']:.1f} s, cross-track max "
             f"{values['max_cross_track_m']:.2f} m, stop error {values['stop_error_m']:.2f} m")
    return result(values, inp.get("limits"), head, files, ["SITL built-in physics; simulated seconds"])


# ------------------------------------------------------------------ plane
# SITL's built-in fixed-wing model (libraries/SITL/SIM_Plane): mass 2 kg, full-throttle thrust =
# m g / 0.7 (thrust-to-weight 1.43), aerodynamic coefficients from a JSON file
# (`--model plane:<file>.json`, keys PLANE_AERO_KEYS). Mass and thrust are not in that file, so an
# aircraft is flown in DYNAMIC SIMILARITY: same wing loading (area scaled by 2 kg / mass, span and
# chord by its square root: same aspect ratio), same thrust-to-weight (THR_MAX). SITL's default
# coefficients are a cambered Skywalker (c_lift_0 0.56, c_drag_p 0.1): give CL_0 for another wing.
PLANE_MASS_KG, PLANE_HOVER_THR = 2.0, 0.7
PLANE_AERO_KEYS = (
    "s", "b", "c", "c_lift_0", "c_lift_deltae", "c_lift_a", "c_lift_q", "mcoeff", "oswald",
    "alpha_stall", "c_drag_q", "c_drag_deltae", "c_drag_p", "c_y_0", "c_y_b", "c_y_p", "c_y_r",
    "c_y_deltaa", "c_y_deltar", "c_l_0", "c_l_p", "c_l_b", "c_l_r", "c_l_deltaa", "c_l_deltar",
    "c_m_0", "c_m_a", "c_m_q", "c_m_deltae", "c_n_0", "c_n_b", "c_n_p", "c_n_r", "c_n_deltaa",
    "c_n_deltar", "deltaa_max", "deltae_max", "deltar_max")
_R_EARTH = 6378137.0


def _plane_model(inp: Dict[str, Any], work: Path) -> Tuple[str, Optional[float], List[str], Dict[str, Any]]:
    """Model string, THR_MAX (or None), notes, values. `aircraft` (similarity) and/or `aero`
    (raw SIM_Plane coefficients, they win) write plane.json."""
    model = str(inp.get("model", "plane"))
    if model not in ("plane", "plane-elevon", "plane-vtail"):
        raise ValueError("model: plane | plane-elevon | plane-vtail")
    coeffs: Dict[str, float] = {}
    notes: List[str] = []
    values: Dict[str, Any] = {}
    thr_max = None
    ac = dict(inp.get("aircraft") or {})
    if ac:
        known = {"mass_kg", "wing_area_m2", "span_m", "mac_m", "CL_alpha", "CL_0", "Cm_alpha",
                 "Cm_0", "CD0", "oswald", "alpha_stall_deg", "thrust_to_weight"}
        bad = sorted(set(ac) - known)
        if bad:
            raise ValueError(f"aircraft keys {bad}; known: {sorted(known)}")
        m = float(ac.get("mass_kg", PLANE_MASS_KG))
        if not 0.2 <= m <= 25:
            raise ValueError("aircraft.mass_kg: 0.2 .. 25")
        k = PLANE_MASS_KG / m
        if "wing_area_m2" in ac:
            coeffs["s"] = float(ac["wing_area_m2"]) * k
        if "span_m" in ac:
            coeffs["b"] = float(ac["span_m"]) * math.sqrt(k)
        if "mac_m" in ac:
            coeffs["c"] = float(ac["mac_m"]) * math.sqrt(k)
        for src, dst in (("CL_alpha", "c_lift_a"), ("CL_0", "c_lift_0"), ("Cm_alpha", "c_m_a"),
                         ("Cm_0", "c_m_0"), ("CD0", "c_drag_p"), ("oswald", "oswald")):
            if src in ac:
                coeffs[dst] = float(ac[src])
        if "alpha_stall_deg" in ac:
            coeffs["alpha_stall"] = math.radians(float(ac["alpha_stall_deg"]))
        if "thrust_to_weight" in ac:
            twr = float(ac["thrust_to_weight"])
            if not 0.3 <= twr <= 1.0 / PLANE_HOVER_THR:
                raise ValueError(f"aircraft.thrust_to_weight: 0.3 .. {1 / PLANE_HOVER_THR:.2f} "
                                 "(SITL's plane tops out there)")
            thr_max = round(100.0 * twr * PLANE_HOVER_THR, 1)
            values["thr_max_pct"] = thr_max
        values["similarity_scale"] = k
        values["_mass_kg"] = m
        notes.append(f"dynamic similarity with SITL's {PLANE_MASS_KG:g} kg plane: area x{k:.3f}, "
                     f"span and chord x{math.sqrt(k):.3f} (same wing loading and aspect ratio)"
                     + (f", THR_MAX {thr_max:g} % (same thrust-to-weight)" if thr_max else ""))
    for key, v in (inp.get("aero") or {}).items():
        if key not in PLANE_AERO_KEYS:
            raise ValueError(f"aero.{key}: not a SIM_Plane coefficient; known: {list(PLANE_AERO_KEYS)}")
        coeffs[key] = float(v)
    for key, v in coeffs.items():
        if not math.isfinite(v):
            raise ValueError(f"{key}: not a finite number")
    if coeffs:
        if coeffs.get("s", 1) <= 0 or coeffs.get("b", 1) <= 0 or coeffs.get("c", 1) <= 0:
            raise ValueError("wing area, span and chord must be > 0")
        (work / "plane.json").write_text(json.dumps(coeffs, indent=1), encoding="utf-8")
        model = f"{model}:plane.json"
        values["aero_coefficients"] = {k: round(v, 5) for k, v in coeffs.items()}
    return model, thr_max, notes, values


def _offset(home: str, along: float, right: float, hdg_deg: float) -> Tuple[int, int]:
    lat0, lon0 = (float(x) for x in home.split(",")[:2])
    h = math.radians(hdg_deg)
    n = along * math.cos(h) - right * math.sin(h)
    e = along * math.sin(h) + right * math.cos(h)
    lat = lat0 + math.degrees(n / _R_EARTH)
    lon = lon0 + math.degrees(e / (_R_EARTH * math.cos(math.radians(lat0))))
    return int(round(lat * 1e7)), int(round(lon * 1e7))


def _run_plane(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    alt = _num(inp, "alt_m", 40, 15, 150)
    length = _num(inp, "circuit_length_m", 400, 150, 1500)
    width = _num(inp, "circuit_width_m", 180, 80, 800)
    laps = int(_num(inp, "laps", 1, 1, 5))
    approach = _num(inp, "approach_m", 280, 120, 800)
    speedup = _num(inp, "speedup", 5, 1, 20)
    cruise = _num(inp, "cruise_mps", 18, 6, 60)
    vmin = _num(inp, "min_mps", min(12.0, cruise - 2), 4, 50)
    vmax = _num(inp, "max_mps", max(30.0, cruise + 5), 8, 80)
    if not vmin < cruise < vmax:
        raise ValueError("min_mps < cruise_mps < max_mps")
    model, thr_max, notes, values = _plane_model(inp, work)
    ac_mass = values.pop("_mass_kg", None)
    home = _home(inp)
    hdg = float(home.split(",")[3])
    params = dict(_CAL, **_COMMON)
    params.update(THR_FAILSAFE=0, FS_GCS_ENABL=0, FS_LONG_ACTN=0, FS_SHORT_ACTN=3,
                  TKOFF_THR_MINSPD=0, TKOFF_THR_MINACC=0, TKOFF_ROTATE_SPD=0.8 * vmin,
                  AIRSPEED_CRUISE=cruise, AIRSPEED_MIN=vmin, AIRSPEED_MAX=vmax,
                  ARSPD_TYPE=100, ARSPD_USE=1, WP_RADIUS=30, WP_LOITER_RAD=60,
                  LAND_DISARMDELAY=3, RTL_AUTOLAND=0, SIM_BATT_VOLTAGE=16.8,
                  # SIM_Plane reads each output over 1000..2000 us; ArduPlane's default range is
                  # 1100..1900, which left 10 % thrust at idle and cut 10 % at full throttle
                  **{f"SERVO{i}_{k}": v for i in (1, 2, 3, 4)
                     for k, v in (("MIN", 1000), ("MAX", 2000), ("TRIM", 1500))})
    if thr_max is not None:
        params["THR_MAX"] = thr_max
    cap = _num(inp, "battery_Ah", 0, 0, 50)
    params["SIM_BATT_CAP_AH"] = cap
    if cap > 0:
        params["BATT_CAPACITY"] = cap * 1000
    if inp.get("wind_mps"):
        params["SIM_WIND_SPD"] = _num(inp, "wind_mps", 0, 0, 20)
        params["SIM_WIND_DIR"] = _num(inp, "wind_dir_deg", 0, 0, 360)
    params.update(_user_params(inp))
    thr_cap = float(params.get("THR_MAX", 100))

    # mission: home, takeoff along the runway heading, rectangle circuit (right-hand), final
    # approach on the runway axis, land on the start point
    lat0, lon0 = _offset(home, 0, 0, hdg)
    items = [dict(cmd=16, frame=0, p=(0, 0, 0, 0), lat=lat0, lon=lon0, alt=0.0),
             dict(cmd=22, frame=6, p=(12, 0, 0, 0), lat=0, lon=0, alt=alt)]
    corners = [(length, 0), (length, width), (-approach, width)]
    for _ in range(laps):
        for a, r in corners:
            la, lo = _offset(home, a, r, hdg)
            items.append(dict(cmd=16, frame=6, p=(0, 0, 0, 0), lat=la, lon=lo, alt=alt))
    first_wp, last_cruise = 2, len(items) - 1
    land_alt = min(alt, max(15.0, approach * math.tan(math.radians(5))))
    la, lo = _offset(home, -approach, 0, hdg)
    items.append(dict(cmd=16, frame=6, p=(0, 0, 0, 0), lat=la, lon=lo, alt=land_alt))
    items.append(dict(cmd=21, frame=6, p=(0, 0, 0, 0), lat=lat0, lon=lon0, alt=0.0))
    land_seq = len(items) - 1
    (work / "mission.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    values.update(alt_target_m=alt, cruise_target_mps=cruise, speedup=speedup,
                  waypoints_total=len(items) - 2)
    files = ["defaults.parm", "sitl.log", "sitl_command.json", "statustext.log", "mission.json"] + \
        (["plane.json"] if (work / "plane.json").exists() else [])
    head = f"ArduPlane SITL {model}"
    with Sitl("sitl_plane", work, model, params, home, speedup) as sitl:
        try:
            s = _startup(sitl, float(inp.get("wall_budget_s", 600)), "rc")
            log_txt = _tail(work / "sitl.log", 4000)
            values["coeffs_loaded"] = 1.0 if "Loaded plane aero coefficients" in log_txt else 0.0
            if (work / "plane.json").exists() and not values["coeffs_loaded"]:
                return _unknown(head, ["SITL did not report loading plane.json"], values, files)
            if not s.wait_ekf(1 | 2 | 4 | 16 | 32):
                return _unknown(head, ["EKF never reported a GPS position (90 s)", *s.prearm()[-5:]],
                                values, files)
            if not s.upload_mission(items):
                return _unknown(head, ["mission upload refused", *s.alerts()[-5:]], values, files)
            if not s.set_mode(10):                                 # AUTO
                return _unknown(head, ["AUTO refused", *s.alerts()[-5:]], values, files)
            if not s.wait(lambda: math.isfinite(s.north), 10, "pos"):
                return _unknown(head, ["no local position"], values, files)
            n0, e0 = s.north, s.east
            if not s.arm():
                return _unknown(head, ["could not arm (pre-arm checks)", *s.prearm()[-6:]], values, files)
            mah0, ej0 = s.mah, s.energy_hj
            s.recording = True
            t_arm = s.t_ms / 1000.0
            if not s.wait(lambda: s.alt > 1.5, 60, "liftoff"):
                return _unknown(head, ["no lift-off in 60 s (thrust or rotation speed too low)",
                                       *s.alerts()[-5:]], values, files)
            values["ground_roll_m"] = math.hypot(s.north - n0, s.east - e0)
            values["liftoff_time_s"] = s.t_ms / 1000.0 - t_arm
            values["liftoff_airspeed_mps"] = s.airspeed
            if not s.wait(lambda: s.mission_seq >= first_wp, alt / 1.0 + 60, "climb"):
                return _unknown(head, [f"takeoff to {alt:g} m not completed",
                                       *s.alerts()[-5:]], values, files)
            values["time_to_alt_s"] = s.t_ms / 1000.0 - t_arm
            leg_s = (2 * (length + approach) * laps + 2 * width * laps + approach) / max(cruise * 0.5, 3)
            if not s.wait(lambda: s.mission_seq >= land_seq, leg_s + 120, "circuit"):
                values["waypoints_reached"] = max(0, s.mission_seq - 1)
                return _unknown(head, ["circuit not completed", *s.alerts()[-5:]], values, files)
            t_land = s.t_ms / 1000.0
            n_txt = len(s.texts)

            def hit() -> Optional[float]:
                for _, _, txt in s.texts[n_txt:]:
                    mm = re.search(r"SIM Hit ground at ([0-9.]+) m/s", txt)
                    if mm:
                        return float(mm.group(1))
                return None
            touched = s.wait(lambda: hit() is not None or not s.armed, 150, "touchdown")
            values["waypoints_reached"] = len(items) - 2
            values["landing_time_s"] = s.t_ms / 1000.0 - t_land
            if touched:
                values["touchdown_sink_mps"] = hit() if hit() is not None else math.nan
                h = math.radians(hdg)
                dn, de = s.north - n0, s.east - e0
                values["touchdown_distance_m"] = math.hypot(dn, de)
                values["touchdown_along_m"] = dn * math.cos(h) + de * math.sin(h)
                values["touchdown_lateral_m"] = -dn * math.sin(h) + de * math.cos(h)
                tn, te = s.north, s.east
                # SITL's plane has no brakes: it rolls on at a few m/s. Rollout = until it is
                # slower than 5 m/s (taxi speed), then the pilot disarms on the ground
                s.wait(lambda: s.groundspeed < 5.0 or not s.armed, 60, "rollout")
                values["rollout_m"] = math.hypot(s.north - tn, s.east - te)
                if s.armed and abs(s.alt) < 1.5:
                    s.command(400, 0, 21196)                    # disarm (forced: on the ground)
                    s.wait(lambda: not s.armed, 5, "disarm")
            s.sleep_sim(1)
            values["landed_disarmed"] = 1.0 if (touched and not s.armed and abs(s.alt) < 1.5) else 0.0
            # the climb-out ends with a zoom above the take-off altitude: reported apart, the
            # cruise window starts on the second leg
            climb = [x["alt"] for x in s.samples if x["seq"] <= first_wp]
            values["takeoff_overshoot_m"] = max(0.0, max(climb, default=alt) - alt)
            cr = [x for x in s.samples if first_wp < x["seq"] <= last_cruise and x["t"] > t_arm]
            err = [x["alt"] - alt for x in cr]
            mean, rms, mx = _stats(err)
            values.update(cruise_alt_mean_error_m=mean, cruise_alt_rms_error_m=rms,
                          cruise_alt_max_error_m=mx)
            asp = [x["as"] for x in cr if math.isfinite(x["as"])]
            values["cruise_airspeed_mps"] = sum(asp) / len(asp) if asp else math.nan
            values["min_airspeed_airborne_mps"] = min((x["as"] for x in s.samples
                                                       if x["alt"] > 3 and math.isfinite(x["as"])),
                                                      default=math.nan)
            thr = [x["thr"] for x in cr if math.isfinite(x["thr"])]
            thr_m = sum(thr) / len(thr) if thr else math.nan
            values["cruise_throttle"] = thr_m                 # output 0..1 of full SITL throttle
            values["cruise_thrust_fraction"] = thr_m * 100.0 / thr_cap if thr_cap else math.nan
            air = [x for x in s.samples if x["alt"] > 1.5]
            values["max_bank_deg"] = max((abs(math.degrees(x["roll"])) for x in air), default=math.nan)
            values["max_pitch_deg"] = max((abs(math.degrees(x["pitch"])) for x in air), default=math.nan)
            values["flight_time_s"] = t_land - t_arm + values["landing_time_s"]
            if math.isfinite(s.mah) and math.isfinite(mah0):
                values["sitl_battery_consumed_mAh"] = s.mah - mah0
            # the aircraft's own thrust and power at cruise. Similarity: SITL thrust is
            # throttle * m_sitl g / 0.7, the real one the same over the real mass. Power of a ducted
            # fan at flight speed V (momentum theory): T = rho A Ve (Ve - V),
            # P = rho A Ve (Ve^2 - V^2) / (2 eta); inlet losses not included (see the `edf` kind)
            m_real = float(ac_mass) if ac_mass else PLANE_MASS_KG
            if math.isfinite(thr_m):
                t_c = thr_m * m_real * 9.80665 / PLANE_HOVER_THR
                values["cruise_thrust_N"] = t_c
                a_e = _num(inp, "edf_exit_area_m2", 0, 0, 1)
                eta = _num(inp, "edf_efficiency", 0.55, 0.1, 1)
                v = values["cruise_airspeed_mps"]
                if a_e and math.isfinite(v) and t_c > 0:
                    rho = 1.225
                    ve = (v + math.sqrt(v * v + 4 * t_c / (rho * a_e))) / 2
                    p_c = rho * a_e * ve * (ve * ve - v * v) / (2 * eta)
                    values["cruise_exit_velocity_mps"] = ve
                    values["cruise_power_W_est"] = p_c
                    wh = _num(inp, "battery_Wh", 0, 0, 5000)
                    if wh:
                        values["cruise_endurance_min_est"] = 0.8 * wh / p_c * 60.0
            alerts = s.alerts()
            values["prearm_messages"] = s.prearm()[-8:]
            values["n_alerts"] = len(alerts)
            values["alerts"] = alerts[-15:]
            _save_samples(work, s.samples, files)
            sitl.audit(force=True)
            if sitl.violation:
                raise PermissionError(sitl.violation)
        except PermissionError as e:
            return _unknown(head, [str(e)], {}, files)
    head += (f": lift-off after {values['ground_roll_m']:.0f} m at {values['liftoff_airspeed_mps']:.1f} m/s, "
             f"{values['waypoints_reached']}/{values['waypoints_total']} waypoints, cruise "
             f"{values['cruise_airspeed_mps']:.1f} m/s at {values['cruise_thrust_fraction'] * 100:.0f} % thrust, "
             f"altitude error rms {values['cruise_alt_rms_error_m']:.1f} m, bank max "
             f"{values['max_bank_deg']:.0f} deg, "
             + (f"touchdown {values['touchdown_along_m']:+.0f} m along / {values['touchdown_lateral_m']:+.1f} m "
                f"across the start point at {values['touchdown_sink_mps']:.2f} m/s sink, "
                if "touchdown_along_m" in values else "")
             + ("landed and disarmed" if values["landed_disarmed"] else "LANDING NOT CONFIRMED"))
    if values["n_alerts"]:
        notes.append(f"{values['n_alerts']} autopilot warnings (values.alerts)")
    notes.append("SITL built-in plane physics (runway take-off, constant thrust with speed: an EDF "
                 "loses thrust as it speeds up, so climb and top speed are optimistic); "
                 "control derivatives are SITL's, not the aircraft's; simulated seconds")
    return result(values, inp.get("limits"), head, files, notes)


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    _refuse_links(inp)
    work = Path(work).resolve()
    if kind == "sitl_copter":
        return _run_copter(inp, work)
    if kind == "sitl_sub":
        return _run_sub(inp, work)
    if kind == "sitl_rover":
        return _run_rover(inp, work)
    if kind == "sitl_plane":
        return _run_plane(inp, work)
    raise ValueError(f"unknown kind {kind!r}")


# ------------------------------------------------------------------ reference cases (§9)
# Each expected value is a requirement of the test or a calibration point of SITL's own model:
# - hover error / depth error: the acceptance bands of the test plan (copter |mean| < 0.5 m at
#   10 m, sub |mean| < 0.3 m at 5 m); close() with expected 0 checks |got| <= tolerance;
# - hover throttle 0.39: SIM_Frame.h default hoverThrOut, defined as the throttle output (CTUN.ThO,
#   = VFR_HUD throttle) at hover at refAlt 593 m and refVoltage 12.09 V; the pack is set to that
#   voltage (SIM_BATT_VOLTAGE; motor thrust in SITL grows with voltage) and home is at 584 m
#   (CMAC), 10 % band;
# - a 5 kg hexa-X frame file with hoverThrOut 0.5: the generated frame.json must reach the model
#   (same definition, same 10 % band);
# - landed_disarmed 1: LAND must end on the ground with the motors disarmed (exact);
# - rover stop error < 2 m: WP_RADIUS = 2 m is the autopilot's own "target reached" radius;
# - plane: the AUTO mission must end landed and disarmed (exact), every waypoint flown (exact),
#   cruise altitude mean error within 3 m (acceptance band of the test plan, TECS holds +/- 1..2 m
#   in still air); with a coefficient file SITL must say it loaded it (exact).
REFERENCES = [
    {"name": "copter_hover_10m", "kind": "sitl_copter",
     "inp": {"model": "quad", "alt_m": 10, "hover_s": 15, "speedup": 5,
             "params": {"SIM_BATT_VOLTAGE": 12.09}},
     "expect": {"hover_mean_error_m": [0.0, 0.5], "landed_disarmed": [1.0, 0.0],
                "hover_throttle": [0.39, 0.10]}},
    {"name": "copter_hexa_frame_file", "kind": "sitl_copter",
     "inp": {"model": "hexax", "alt_m": 10, "hover_s": 15, "speedup": 5,
             "frame": {"mass": 5.0, "hoverThrOut": 0.5, "diagonal_size": 0.6, "disc_area": 0.9,
                       "maxVoltage": 12.09}},
     "expect": {"hover_mean_error_m": [0.0, 0.5], "landed_disarmed": [1.0, 0.0],
                "hover_throttle": [0.5, 0.10]}},
    {"name": "sub_depth_hold_5m", "kind": "sitl_sub",
     "inp": {"depth_m": 5, "hold_s": 20, "speedup": 5},
     "expect": {"depth_mean_error_m": [0.0, 0.3]}},
    {"name": "rover_guided_50m", "kind": "sitl_rover",
     "inp": {"north_m": 50, "east_m": 0, "speed_mps": 5, "speedup": 5},
     "expect": {"stop_error_m": [0.0, 2.0]}},
    {"name": "plane_circuit_stock", "kind": "sitl_plane",
     "inp": {"alt_m": 40, "cruise_mps": 20, "speedup": 5},
     "expect": {"landed_disarmed": [1.0, 0.0], "waypoints_reached": [5.0, 0.0],
                "cruise_alt_mean_error_m": [0.0, 3.0]}},
    {"name": "plane_coefficient_file", "kind": "sitl_plane",
     "inp": {"alt_m": 40, "cruise_mps": 20, "speedup": 5, "aero": {"c_drag_p": 0.02}},
     "expect": {"coeffs_loaded": [1.0, 0.0], "landed_disarmed": [1.0, 0.0]}},
]
