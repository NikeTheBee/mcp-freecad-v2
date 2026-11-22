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
    # a bare string iterated as characters (8 joints "s","h","o"...); an empty
    # list wrote a null `joints:` -- both must be refused
    for call in (lambda: urdf_aug.add_ros2_control(et.Element("robot"), "shoulder"),
                 lambda: urdf_aug.controllers_yaml("shoulder"),
                 lambda: urdf_aug.controllers_yaml([]),
                 lambda: urdf_aug.add_ros2_control(et.Element("robot"), [])):
        try:
            call()
            print("FAIL: string / empty joint list accepted")
            return 1
        except ValueError as e:
            print(f"refused: {e}")

    # verdict cross-checks: unknown joint, fixed joint, unknown link, no inertial
    v = urdf_aug.verdict(reparsed)
    assert "links without <inertial>" in v and "not movable" not in v \
        and "unknown links" not in v, v
    bad = et.fromstring(xml)
    urdf_aug.add_ros2_control(bad, ["elbow"], name="second")
    urdf_aug.add_imu(bad, "head_link", name="imu2")
    v = urdf_aug.verdict(bad)
    assert "['elbow']" in v and "['head_link']" in v, v

    rc_inertial = inertial_checks()
    if rc_inertial:
        return rc_inertial
    print("URDF_CONTROL_OK")
    return 0


def inertial_checks() -> int:
    """<inertial> from a FreeCAD solid against analytic values (needs FreeCAD)."""
    try:
        import FreeCAD as fc
        import Part
    except ImportError:
        print("inertial checks not run: FreeCAD not importable (run under freecadcmd)")
        return 0
    import math

    def close(a, b, rel=1e-9, abs_=1e-15):
        assert abs(a - b) <= max(abs_, rel * abs(b)), (a, b)

    def check_box(p, m):
        # 100 x 50 x 20 mm steel box, in its own frame (corner at the origin)
        close(p["mass"], m)
        for got, want in zip(p["com"], (0.05, 0.025, 0.01)):
            close(got, want, abs_=1e-12)
        close(p["ixx"], m * (0.05 ** 2 + 0.02 ** 2) / 12)
        close(p["iyy"], m * (0.10 ** 2 + 0.02 ** 2) / 12)
        close(p["izz"], m * (0.10 ** 2 + 0.05 ** 2) / 12)
        for k in ("ixy", "ixz", "iyz"):
            assert abs(p[k]) < 1e-12, (k, p[k])

    doc = fc.newDocument("UrdfInertial")
    box = doc.addObject("Part::Box", "b")
    box.Length, box.Width, box.Height = 100, 50, 20
    doc.recompute()
    m = 7850.0 * 0.1 * 0.05 * 0.02                       # 0.785 kg
    check_box(urdf_aug.inertial_from_shape(box.Shape, 7850.0), m)

    # box moved and turned 30° about Z in the document
    pl = fc.Placement(fc.Vector(200, 30, 10), fc.Rotation(fc.Vector(0, 0, 1), 30))
    box.Placement = pl
    doc.recompute()
    # ... link frame = the box frame: same numbers as the unmoved box
    check_box(urdf_aug.inertial_from_shape(box.Shape, 7850.0, pl), m)
    # ... link frame = document frame: rotated tensor, COM in the document
    p = urdf_aug.inertial_from_shape(box.Shape, 7850.0)
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    i1, i2 = m * (0.05 ** 2 + 0.02 ** 2) / 12, m * (0.10 ** 2 + 0.02 ** 2) / 12
    close(p["ixx"], i1 * c * c + i2 * s * s)
    close(p["iyy"], i1 * s * s + i2 * c * c)
    close(p["ixy"], (i1 - i2) * s * c)
    close(p["com"][0], 0.2 + 0.05 * c - 0.025 * s)
    close(p["com"][1], 0.03 + 0.05 * s + 0.025 * c)
    close(p["com"][2], 0.02)

    # the same box inside a container turned 90° about X and lifted 500 mm:
    # obj.Shape ignores the container, Part.getShape(container, "b.") does not
    cont = doc.addObject("App::Part", "cont")
    cont.addObject(box)
    cont.Placement = fc.Placement(fc.Vector(0, 0, 500), fc.Rotation(fc.Vector(1, 0, 0), 90))
    doc.recompute()
    glob = Part.getShape(cont, "b.")
    check_box(urdf_aug.inertial_from_shape(glob, 7850.0, cont.Placement * pl), m)

    # aluminium cylinder r=10 h=60 along Z
    cyl = doc.addObject("Part::Cylinder", "c")
    cyl.Radius, cyl.Height = 10, 60
    doc.recompute()
    p = urdf_aug.inertial_from_shape(cyl.Shape, 2700.0)
    mc = 2700.0 * math.pi * 0.01 ** 2 * 0.06
    close(p["mass"], mc)
    close(p["izz"], mc * 0.01 ** 2 / 2)
    close(p["ixx"], mc * (3 * 0.01 ** 2 + 0.06 ** 2) / 12)
    close(p["iyy"], p["ixx"])
    close(p["com"][2], 0.03)

    # two separated boxes in one compound: parallel-axis theorem
    b1 = Part.makeBox(10, 10, 10)
    b2 = Part.makeBox(10, 10, 10, fc.Vector(90, 0, 0))
    p = urdf_aug.inertial_from_shape(Part.makeCompound([b1, b2]), 1000.0)
    m1 = 1000.0 * 1e-6
    close(p["mass"], 2 * m1)
    close(p["com"][0], 0.05)
    close(p["ixx"], 2 * m1 * (0.01 ** 2 * 2) / 12)
    close(p["iyy"], 2 * (m1 * (0.01 ** 2 * 2) / 12 + m1 * 0.045 ** 2))
    assert p["ixx"] + p["iyy"] >= p["izz"] and p["iyy"] + p["izz"] >= p["ixx"] \
        and p["ixx"] + p["izz"] >= p["iyy"], p

    # the XML element: SI values, replaced not duplicated, parsable
    link = et.Element("link", {"name": "base_link"})
    urdf_aug.add_inertial(link, cyl.Shape, 2700.0)
    urdf_aug.add_inertial(link, glob, 7850.0, cont.Placement * pl)
    again = et.fromstring(et.tostring(link, encoding="unicode"))
    assert len(again.findall("inertial")) == 1
    close(float(again.find("inertial/mass").get("value")), m)
    xyz = [float(x) for x in again.find("inertial/origin").get("xyz").split()]
    close(xyz[0], 0.05, abs_=1e-12)
    close(float(again.find("inertial/inertia").get("izz")), m * (0.1 ** 2 + 0.05 ** 2) / 12)

    # not a solid / bad density -> refused
    for call in (lambda: urdf_aug.inertial_from_shape(Part.makePlane(10, 10), 7850.0),
                 lambda: urdf_aug.inertial_from_shape(cyl.Shape, 0),
                 lambda: urdf_aug.inertial_from_shape(cyl.Shape, float("nan"))):
        try:
            call()
            print("FAIL: inertial of a non-solid / bad density accepted")
            return 1
        except ValueError as e:
            print(f"refused: {e}")
    fc.closeDocument(doc.Name)
    print("inertial: box, moved box, box in container, cylinder, compound OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.stdout.flush()  # freecadcmd drops unflushed output on sys.exit
    sys.exit(rc)
