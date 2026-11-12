"""URDF augmentation — ros2_control, sensors, Gazebo plugins (§4.2 completion).

Takes the bare URDF built with CROSS's `urdf_utils` (see skill-robotics-ros)
and adds what a ROS2/Gazebo consumer needs, WITHOUT requiring a ROS2 runtime:
everything here is plain `xml.etree.ElementTree` on the `<robot>` element.

Targets ROS 2 Jazzy (Iron and later) with modern Gazebo Sim ("gz sim", not
Gazebo Classic): plugin/system names follow `gz_ros2_control`. On Humble the same
package is still named `ign_ros2_control` (`ign_ros2_control/IgnitionSystem`,
`ign_ros2_control-system`, `ign_ros2_control::IgnitionROS2ControlPlugin`): the
names written here do not load there. Generated files are consumed
later in a ROS2 workspace (`colcon build`), never executed here.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as et
from typing import Iterable

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_./-]*$")


def _valid_name(name: str) -> str:
    """ROS-style identifier gate. Joint/link names may come from imported
    documents (external data); reject anything that could restructure the
    generated YAML/URDF (newlines, quotes, colons...)."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(f"invalid ROS name: {name!r} "
                         "(allowed: letters, digits, _ . / -)")
    return name


def _joint_list(joint_names) -> list:
    """A bare string iterates as characters: add_ros2_control(robot, "shoulder")
    wrote 8 joints named s, h, o, u, l, d, e, r; an empty list wrote a YAML
    `joints:` with a null value, which the controller refuses to load."""
    if isinstance(joint_names, (str, bytes)):
        raise ValueError(f"joint_names must be a list of names, not the string "
                         f"{joint_names!r}")
    joints = [_valid_name(j) for j in joint_names]
    if not joints:
        raise ValueError("joint_names is empty: nothing to control")
    if len(set(joints)) != len(joints):
        raise ValueError(f"duplicate joint names: {joints}")
    return joints


# ── inertial (mass, centre of mass, inertia tensor) ──────────────────────────
def inertial_from_shape(shape, density_kg_m3: float, link_placement=None) -> dict:
    """Mass properties of a FreeCAD solid for a URDF `<inertial>`, in SI.

    shape: a `Part.Shape` with solids, expressed in the SAME frame as
    `link_placement` (document frame: `obj.Shape` for a top-level object,
    `Part.getShape(container, "obj.")` for an object inside a moved container —
    `obj.Shape` alone ignores the container's placement).
    link_placement: `FreeCAD.Placement` of the URDF link frame in that frame
    (None = the link frame is the shape's frame).

    Measured on FreeCAD 1.1: `Shape.Volume` is mm³, `Shape.CenterOfMass` mm,
    and `Shape.MatrixOfInertia` is the tensor of a unit-density body in mm⁵,
    taken ABOUT THE CENTRE OF MASS, in the shape frame's axes (off-diagonal
    terms already carry the tensor's minus sign). Hence:
      mass     = density · V · 1e-9                      [kg]
      com      = link⁻¹ · CenterOfMass · 1e-3            [m, link frame]
      inertia  = Rᵀ · (density · 1e-9 · M) · R · 1e-6    [kg·m², link axes, at the COM]
    Returns {"mass", "com": (x, y, z), "ixx", "ixy", "ixz", "iyy", "iyz", "izz"}.
    """
    if (not isinstance(density_kg_m3, (int, float)) or isinstance(density_kg_m3, bool)
            or not math.isfinite(density_kg_m3) or density_kg_m3 <= 0):
        raise ValueError(f"density_kg_m3 must be a finite number > 0 (got "
                         f"{density_kg_m3!r}); steel 7850, aluminium 2700, PLA 1240")
    # Per solid, then the parallel-axis theorem: a Part.Compound (several parts in
    # one link) has no CenterOfMass attribute at all.
    solids = [s for s in shape.Solids if s.Volume > 0]
    if not solids:
        raise ValueError("shape has no solid of positive volume: not a solid part")
    vol = sum(s.Volume for s in solids)
    mass = density_kg_m3 * vol * 1e-9
    vec = type(solids[0].CenterOfMass)
    cx = sum(s.Volume * s.CenterOfMass.x for s in solids) / vol
    cy = sum(s.Volume * s.CenterOfMass.y for s in solids) / vol
    cz = sum(s.Volume * s.CenterOfMass.z for s in solids) / vol
    com = vec(cx, cy, cz)
    tensor = [[0.0] * 3 for _ in range(3)]
    for s in solids:
        m = s.MatrixOfInertia
        own = [[m.A11, m.A12, m.A13], [m.A21, m.A22, m.A23], [m.A31, m.A32, m.A33]]
        c = s.CenterOfMass
        d = (c.x - cx, c.y - cy, c.z - cz)
        d2 = d[0] ** 2 + d[1] ** 2 + d[2] ** 2
        for i in range(3):
            for j in range(3):
                tensor[i][j] += own[i][j] + s.Volume * ((d2 if i == j else 0.0)
                                                        - d[i] * d[j])
    if link_placement is None:
        axes = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    else:
        com = link_placement.inverse().multVec(com)
        rot = link_placement.Rotation
        axes = []
        for unit in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
            a = rot.multVec(vec(*unit))      # link axis, in the shape frame
            axes.append((a.x, a.y, a.z))
    k = density_kg_m3 * 1e-9 * 1e-6          # mm⁵ (unit density) -> kg·m²

    def comp(i: int, j: int) -> float:
        return k * sum(axes[i][r] * tensor[r][c] * axes[j][c]
                       for r in range(3) for c in range(3))

    return {"mass": mass, "com": (com.x * 1e-3, com.y * 1e-3, com.z * 1e-3),
            "ixx": comp(0, 0), "ixy": comp(0, 1), "ixz": comp(0, 2),
            "iyy": comp(1, 1), "iyz": comp(1, 2), "izz": comp(2, 2)}


def add_inertial(link: et.Element, shape, density_kg_m3: float,
                 link_placement=None) -> et.Element:
    """Set the `<inertial>` of a URDF `<link>` from a FreeCAD solid (replaces an
    existing one). See `inertial_from_shape` for frames and units. The origin
    carries the centre of mass with rpy 0: the tensor is given in link axes."""
    p = inertial_from_shape(shape, density_kg_m3, link_placement)
    for old in link.findall("inertial"):
        link.remove(old)
    inertial = et.SubElement(link, "inertial")
    et.SubElement(inertial, "origin", {
        "xyz": " ".join(repr(float(v)) for v in p["com"]), "rpy": "0 0 0"})
    et.SubElement(inertial, "mass", {"value": repr(float(p["mass"]))})
    et.SubElement(inertial, "inertia", {
        key: repr(float(p[key])) for key in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")})
    return inertial


# ── ros2_control ─────────────────────────────────────────────────────────────
def add_ros2_control(robot: et.Element, joint_names: Iterable[str],
                     name: str = "GazeboSimSystem",
                     interface: str = "position") -> et.Element:
    """Add a `<ros2_control type="system">` block exposing each joint.

    interface: "position" | "velocity" | "effort" — the command interface;
    state interfaces (position+velocity) are always exposed.
    """
    if interface not in _CONTROLLERS:
        raise ValueError(f"unknown command interface {interface!r}; "
                         f"use one of {sorted(_CONTROLLERS)}")
    joint_names = _joint_list(joint_names)
    rc = et.SubElement(robot, "ros2_control",
                       {"name": _valid_name(name), "type": "system"})
    hw = et.SubElement(rc, "hardware")
    et.SubElement(hw, "plugin").text = "gz_ros2_control/GazeboSimSystem"
    for jn in joint_names:
        j = et.SubElement(rc, "joint", {"name": _valid_name(jn)})
        et.SubElement(j, "command_interface", {"name": interface})
        et.SubElement(j, "state_interface", {"name": "position"})
        et.SubElement(j, "state_interface", {"name": "velocity"})
    return rc


def add_gazebo_ros2_control_plugin(robot: et.Element,
                                   controllers_yaml_pkg_path: str) -> et.Element:
    """Add the Gazebo Sim plugin that drives ros2_control from the sim loop.

    controllers_yaml_pkg_path: package-relative path baked into the plugin,
    e.g. "$(find my_bot)/config/controllers.yaml" (resolved by ROS2 launch).
    """
    gz = et.SubElement(robot, "gazebo")
    plugin = et.SubElement(gz, "plugin", {
        "filename": "gz_ros2_control-system",
        "name": "gz_ros2_control::GazeboSimROS2ControlPlugin",
    })
    et.SubElement(plugin, "parameters").text = controllers_yaml_pkg_path
    return gz


_CONTROLLERS = {"position": "position_controllers/JointGroupPositionController",
                "velocity": "velocity_controllers/JointGroupVelocityController",
                "effort": "effort_controllers/JointGroupEffortController"}


def controllers_yaml(joint_names: Iterable[str],
                     controller: str = "position") -> str:
    """ros2_controllers YAML: joint_state_broadcaster + one trajectory controller."""
    if controller not in _CONTROLLERS:
        raise ValueError(f"unknown controller {controller!r}; use one of {sorted(_CONTROLLERS)}")
    joints = _joint_list(joint_names)
    joint_lines = "\n".join(f"      - {j}" for j in joints)
    ctrl_type = _CONTROLLERS[controller]
    return f"""controller_manager:
  ros__parameters:
    update_rate: 100
    joint_state_broadcaster:
      type: joint_state_broadcaster/JointStateBroadcaster
    {controller}_controller:
      type: {ctrl_type}

{controller}_controller:
  ros__parameters:
    joints:
{joint_lines}
"""


# ── sensors (Gazebo Sim `<gazebo reference=...><sensor>`) ────────────────────
def add_imu(robot: et.Element, link_name: str, name: str = "imu",
            update_rate: float = 100.0, topic: str | None = None) -> et.Element:
    gz = et.SubElement(robot, "gazebo", {"reference": link_name})
    s = et.SubElement(gz, "sensor", {"name": name, "type": "imu"})
    et.SubElement(s, "update_rate").text = str(update_rate)
    et.SubElement(s, "always_on").text = "true"
    et.SubElement(s, "topic").text = topic or f"{name}/data"
    return gz


def add_camera(robot: et.Element, link_name: str, name: str = "camera",
               hfov_rad: float = 1.396, width: int = 640, height: int = 480,
               update_rate: float = 30.0) -> et.Element:
    gz = et.SubElement(robot, "gazebo", {"reference": link_name})
    s = et.SubElement(gz, "sensor", {"name": name, "type": "camera"})
    et.SubElement(s, "update_rate").text = str(update_rate)
    cam = et.SubElement(s, "camera")
    et.SubElement(cam, "horizontal_fov").text = str(hfov_rad)
    img = et.SubElement(cam, "image")
    et.SubElement(img, "width").text = str(width)
    et.SubElement(img, "height").text = str(height)
    et.SubElement(s, "topic").text = f"{name}/image_raw"
    return gz


def add_lidar(robot: et.Element, link_name: str, name: str = "lidar",
              samples: int = 360, min_range: float = 0.12,
              max_range: float = 12.0, update_rate: float = 10.0) -> et.Element:
    gz = et.SubElement(robot, "gazebo", {"reference": link_name})
    s = et.SubElement(gz, "sensor", {"name": name, "type": "gpu_lidar"})
    et.SubElement(s, "update_rate").text = str(update_rate)
    et.SubElement(s, "topic").text = f"{name}/scan"
    ray = et.SubElement(s, "ray")
    scan = et.SubElement(ray, "scan")
    h = et.SubElement(scan, "horizontal")
    et.SubElement(h, "samples").text = str(samples)
    et.SubElement(h, "min_angle").text = "-3.14159"
    et.SubElement(h, "max_angle").text = "3.14159"
    rng = et.SubElement(ray, "range")
    et.SubElement(rng, "min").text = str(min_range)
    et.SubElement(rng, "max").text = str(max_range)
    return gz


# ── verification ─────────────────────────────────────────────────────────────
def verdict(robot: et.Element) -> str:
    """Token-minimal summary of what the augmented URDF now carries."""
    n_joints_rc = len(robot.findall("./ros2_control/joint"))
    sensors = [s.get("type") for gz in robot.findall("gazebo")
               for s in gz.findall("sensor")]
    has_plugin = any(p.get("filename") == "gz_ros2_control-system"
                     for gz in robot.findall("gazebo")
                     for p in gz.findall("plugin"))
    out = (f"URDF '{robot.get('name')}': ros2_control joints={n_joints_rc} "
           f"gz_plugin={'yes' if has_plugin else 'no'} sensors={sensors or 'none'}")
    # A ros2_control joint that is not a movable <joint> of the robot makes
    # gz_ros2_control fail at load; a sensor on an unknown link is dropped by
    # Gazebo without a word; a link without <inertial> is ignored by the physics.
    # The summary used to say nothing of the three.
    movable = {j.get("name") for j in robot.findall("joint")
               if j.get("type") != "fixed"}
    links = {ln.get("name") for ln in robot.findall("link")}
    problems = []
    bad_joints = sorted({j.get("name") for j in robot.findall("./ros2_control/joint")}
                        - movable)
    if bad_joints:
        problems.append(f"ros2_control joints not movable joints of the URDF: {bad_joints}")
    bad_refs = sorted({gz.get("reference") for gz in robot.findall("gazebo")
                       if gz.get("reference") is not None} - links)
    if bad_refs:
        problems.append(f"gazebo references to unknown links: {bad_refs}")
    no_inertia = sorted(ln.get("name") for ln in robot.findall("link")
                        if ln.find("inertial") is None)
    if no_inertia and (has_plugin or n_joints_rc):
        problems.append(f"links without <inertial> (no dynamics in Gazebo): {no_inertia}")
    if problems:
        out += " | WARN " + "; ".join(problems)
    return out
