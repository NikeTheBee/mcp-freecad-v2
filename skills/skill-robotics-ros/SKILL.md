# skill-robotics-ros

**Load when:** robots / ROS — defining links & joints, generating **URDF/xacro** robot descriptions.

## What this is
The [CROSS workbench](https://github.com/galou/freecad.cross) (galou, cloned in `addons/freecad.cross`).
URDF **generation/export needs no ROS2 runtime**. Gazebo simulation / `ros2_control` *do* need ROS2 and
are out of scope here (see `docs/CAHIER_DES_CHARGES.md` §12).

## Headless setup (the gotchas)
CROSS is a `freecad.cross` **namespace package** and pulls `FreeCADGui` via a util import. To use its URDF
core under `freecadcmd`:
```python
import sys, types, os
sys.modules.setdefault("FreeCADGui", types.ModuleType("FreeCADGui"))   # stub Gui
import freecad
freecad.__path__.append(r"<repo>\addons\freecad.cross\freecad")        # extend the namespace
from freecad.cross import urdf_utils
```
The high-level CROSS `Robot`/`Link`/`Joint` document objects are GUI-proxy-coupled; for headless pipelines
build the URDF from the `urdf_utils` primitives below (which operate on plain FreeCAD objects).

## Generate URDF from geometry
`urdf_utils` returns `xml.etree.ElementTree` elements (URDF is in **metres** — CROSS converts from mm):
```python
import xml.etree.ElementTree as et
robot = et.Element("robot", {"name": "demo_bot"})
link  = et.SubElement(robot, "link", {"name": "base_link"})
link.append(urdf_utils.urdf_visual_from_box(box))       # also _cylinder / _sphere / _from_object (mesh)
link.append(urdf_utils.urdf_collision_from_box(box))
urdf_aug.add_inertial(link, box.Shape, 7850.0)          # freecad_layers.urdf_aug; density kg/m³ -> kg, m, kg·m² (see below)
xml_str = et.tostring(robot, encoding="unicode")        # write to <name>.urdf
```
Helpers: `urdf_geometry_box/cylinder/sphere`, `urdf_visual_from_*`, `urdf_collision_from_*`,
`urdf_origin_from_placement` (mm → m, rpy = fixed-axis XYZ in radians), `urdf_*_mesh`.
- `urdf_visual_mesh/urdf_collision_mesh` only **reference** `package://<pkg>/meshes/<file>`: they do
  not export the mesh and write no `scale`. Export it yourself into `meshes/`; an STL written by
  FreeCAD is in **mm**, so add `scale="0.001 0.001 0.001"` on the `<mesh>` element (otherwise the
  part is 1000× too big in RViz/Gazebo).

## Inertial (mass, centre of mass, inertia) — never by hand
`urdf_aug.add_inertial(link, shape, density_kg_m3, link_placement=None)` (or
`inertial_from_shape` for the numbers) converts FreeCAD's mm units: `Shape.Volume` mm³,
`Shape.CenterOfMass` mm, `Shape.MatrixOfInertia` = unit-density tensor in **mm⁵ about the centre of
mass** (not about the origin). Passing those raw to URDF is wrong by 1e9 / 1e15.
- `shape` and `link_placement` must be in the same frame. Part inside a moved container:
  `Part.getShape(container, "obj.")` (`obj.Shape` ignores the container's placement).
- `link_placement` = the link frame (the placement used for the joint origin); the result is the COM
  in the link frame and the tensor in link axes, at the COM.
- CROSS's own `urdf_utils.urdf_inertial(mass, com_placement, ixx, ixy, ixz, iyy, iyz, izz)` takes six
  separate terms already in kg / kg·m² (placement in mm) and does no conversion.

## Joints
Add `<joint>` elements between links manually:
```python
j = et.SubElement(robot, "joint", {"name": "j1", "type": "revolute"})  # or fixed/prismatic/continuous
et.SubElement(j, "parent", {"link": "base_link"})
et.SubElement(j, "child",  {"link": "arm_link"})
# origin = child link frame seen from the PARENT link frame (not the document placement)
j.append(urdf_utils.urdf_origin_from_placement(base_frame.inverse() * arm_frame))
et.SubElement(j, "axis", {"xyz": "0 0 1"})              # unit vector, in the CHILD link frame
et.SubElement(j, "limit", {"lower": "-1.57", "upper": "1.57", "effort": "10", "velocity": "1"})
```
`base_frame` / `arm_frame` are the global placements chosen as link frames (e.g. the joint axis
location). Everything inside a link is then expressed in ITS frame: for the child,
`urdf_utils.urdf_visual_from_box(arm, placement=arm_frame.inverse())` (same for collision) and
`urdf_aug.add_inertial(arm_link, arm.Shape, rho, arm_frame)`. Without `placement=` the visual keeps
the document placement on top of the joint origin: a part at z = 300 mm is drawn at 600 mm.
Limits are radians (revolute) or metres (prismatic).

## Full ROS2 package (not just the URDF string)
To hand off to a ROS2 workspace, emit an `ament_cmake` package
(`write_ros2_package` in `install/urdf_package_test.py`): `package.xml` (format 3, `ament_cmake`,
`robot_state_publisher`/`rviz2` deps) + `CMakeLists.txt` (installs `urdf/` + `launch/`) +
`urdf/<pkg>.urdf`. No ROS2 runtime needed to *generate* it; `colcon build` it later in a workspace.

## Inverse kinematics — not supported here
CROSS's IK needs the Pinocchio binary package, which is not available in FreeCAD's or the bridge's
Python on Windows; the former wrapper could never solve anything on the target OS and was removed
(audit 2026-10-11, X4). Say so plainly. For IK, export the URDF and use MoveIt / KDL in a ROS 2
workspace. Planar linkages (grippers, four-bars) are solved exactly by `skill-kinematics`.

## ros2_control · sensors · Gazebo (generation only — §4.2)
`server/freecad_layers/urdf_aug.py` augments the bare `<robot>` element for ROS 2 Jazzy (Iron+) /
modern **Gazebo Sim** (`gz_ros2_control`, not Gazebo Classic; on Humble the plugin names are
`ign_ros2_control…` and these do not load). Joint names are a **list**, never a bare string:
```python
from freecad_layers import urdf_aug
urdf_aug.add_ros2_control(robot, ["shoulder", "elbow"])          # command/state interfaces
urdf_aug.add_gazebo_ros2_control_plugin(robot, "$(find my_bot)/config/controllers.yaml")
urdf_aug.add_imu(robot, "base_link"); urdf_aug.add_camera(robot, "head_link")
urdf_aug.add_lidar(robot, "base_link")
yaml_text = urdf_aug.controllers_yaml(["shoulder", "elbow"])     # ros2_controllers config
print(urdf_aug.verdict(robot))                                   # compact check
```
`verdict` appends `| WARN …` for a ros2_control joint that is not a movable `<joint>`, a sensor on
an unknown link, or links without `<inertial>` — fix them, Gazebo fails or drops them silently.
Ship the YAML as `config/controllers.yaml` inside the ROS2 package (§ above). *Running* the
sim still needs a ROS2+Gazebo install — we generate, the workspace consumes (§12).

## Verify
Assert the output contains `<robot`, each `<link>`, `<visual>/<collision>`, and the expected `<box size=>`
/ `<joint>`; and that the package files are valid XML. Runnable examples:
`install/robotics_urdf_test.py` (URDF), `install/urdf_package_test.py` (full package),
`install/urdf_control_test.py` (ros2_control/sensors/Gazebo augmentation).
