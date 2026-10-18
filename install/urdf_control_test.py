"""ros2_control / sensors / Gazebo URDF augmentation test (§4.2 completion).

Builds a minimal 2-link arm URDF with plain ElementTree (the CROSS-built URDF
has the same structure), augments it with server/freecad_layers/urdf_aug.py,
and checks the result parses and carries every element a ROS2/Gazebo consumer
needs. Pure Python. Sentinel: URDF_CONTROL_OK
"""
import sys
import xml.etree.ElementTree as et
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))

from freecad_layers import urdf_aug  # noqa: E402


def main() -> int:
    # Minimal 2-link, 1-revolute-joint robot (what urdf_utils would emit).
    robot = et.Element("robot", {"name": "demo_arm"})
    for ln in ("base_link", "arm_link"):
        et.SubElement(robot, "link", {"name": ln})
    j = et.SubElement(robot, "joint", {"name": "shoulder", "type": "revolute"})
    et.SubElement(j, "parent", {"link": "base_link"})
    et.SubElement(j, "child", {"link": "arm_link"})
    et.SubElement(j, "axis", {"xyz": "0 0 1"})
    et.SubElement(j, "limit", {"lower": "-1.57", "upper": "1.57",
                               "effort": "10", "velocity": "1"})

    urdf_aug.add_ros2_control(robot, ["shoulder"])
    urdf_aug.add_gazebo_ros2_control_plugin(
        robot, "$(find demo_arm)/config/controllers.yaml")
    urdf_aug.add_imu(robot, "base_link")
    urdf_aug.add_camera(robot, "arm_link")
    urdf_aug.add_lidar(robot, "base_link")

    xml = et.tostring(robot, encoding="unicode")
    reparsed = et.fromstring(xml)  # must stay well-formed XML

    assert reparsed.find("./ros2_control/hardware/plugin").text \
        == "gz_ros2_control/GazeboSimSystem"
    rc_joint = reparsed.find("./ros2_control/joint[@name='shoulder']")
    assert rc_joint is not None
    assert rc_joint.find("command_interface").get("name") == "position"
    assert len(rc_joint.findall("state_interface")) == 2

    plugins = [p for gz in reparsed.findall("gazebo") for p in gz.findall("plugin")]
    assert any(p.get("filename") == "gz_ros2_control-system" for p in plugins)

    sensor_types = sorted(s.get("type") for gz in reparsed.findall("gazebo")
                          for s in gz.findall("sensor"))
    assert sensor_types == ["camera", "gpu_lidar", "imu"], sensor_types

    yaml_text = urdf_aug.controllers_yaml(["shoulder"])
    assert "joint_state_broadcaster" in yaml_text and "- shoulder" in yaml_text
    assert "position_controllers/JointGroupPositionController" in yaml_text

    # secure-by-design: names from external documents must be validated —
    # a newline-bearing name would restructure the generated YAML.
    try:
        urdf_aug.controllers_yaml(["ok_name", "evil:\n  injected: true"])
        print("FAIL: YAML-injection name accepted")
        return 1
    except ValueError:
        print("invalid ROS name refused: OK")

    print(urdf_aug.verdict(reparsed))
    # audit U1: unknown controller / interface -> clear ValueError, not KeyError
    for call in (lambda: urdf_aug.controllers_yaml(["j1"], controller="trajectory"),
                 lambda: urdf_aug.add_ros2_control(et.Element("robot"), ["j1"],
                                                   interface="torque")):
        try:
            call()
            print("FAIL: unknown controller accepted")
            return 1
        except ValueError as e:
            print(f"refused: {e}")
    print("URDF_CONTROL_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.stdout.flush()  # freecadcmd drops unflushed output on sys.exit
    sys.exit(rc)
