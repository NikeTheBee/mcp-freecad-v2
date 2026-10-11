"""URDF augmentation — ros2_control, sensors, Gazebo plugins (§4.2 completion).

Takes the bare URDF built with CROSS's `urdf_utils` (see skill-robotics-ros)
and adds what a ROS2/Gazebo consumer needs, WITHOUT requiring a ROS2 runtime:
everything here is plain `xml.etree.ElementTree` on the `<robot>` element.

Targets ROS 2 Humble+ with modern Gazebo Sim ("gz sim", not Gazebo Classic):
plugin/system names follow `gz_ros2_control`. Generated files are consumed
later in a ROS2 workspace (`colcon build`), never executed here.
"""
from __future__ import annotations

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
    joints = [_valid_name(j) for j in joint_names]
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
    return (f"URDF '{robot.get('name')}': ros2_control joints={n_joints_rc} "
            f"gz_plugin={'yes' if has_plugin else 'no'} sensors={sensors or 'none'}")
