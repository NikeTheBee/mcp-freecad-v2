"""Validate full ROS2 package generation (cahier §4.2: package URDF/xacro pour ROS2).

Run with:  A:/FreeCAD/bin/freecadcmd.exe tests/urdf_package_test.py

Beyond a bare URDF string (skill-robotics-ros), produce an installable ament_cmake
package directory: package.xml + CMakeLists.txt + urdf/ + launch/. No ROS2 runtime
needed to GENERATE it (you build/run it later in a ROS2 workspace).
Prints URDF_PACKAGE_OK on success.
"""
import os
import tempfile
import xml.etree.ElementTree as et

URDF = (
    '<?xml version="1.0"?>\n'
    '<robot name="demo_bot">\n'
    '  <link name="base_link">\n'
    '    <visual><geometry><box size="0.1 0.06 0.04"/></geometry></visual>\n'
    '    <collision><geometry><box size="0.1 0.06 0.04"/></geometry></collision>\n'
    '  </link>\n'
    '</robot>\n'
)


def write_ros2_package(root: str, pkg: str, urdf_xml: str) -> str:
    """Write an ament_cmake ROS2 package that installs a URDF. Returns pkg dir."""
    pkg_dir = os.path.join(root, pkg)
    os.makedirs(os.path.join(pkg_dir, "urdf"), exist_ok=True)
    os.makedirs(os.path.join(pkg_dir, "launch"), exist_ok=True)

    with open(os.path.join(pkg_dir, "urdf", f"{pkg}.urdf"), "w", encoding="utf-8") as f:
        f.write(urdf_xml)

    with open(os.path.join(pkg_dir, "package.xml"), "w", encoding="utf-8") as f:
        f.write(
            '<?xml version="1.0"?>\n'
            '<package format="3">\n'
            f'  <name>{pkg}</name>\n'
            '  <version>0.0.1</version>\n'
            f'  <description>URDF description for {pkg} (generated from FreeCAD).</description>\n'
            '  <maintainer email="user@example.com">user</maintainer>\n'
            '  <license>MIT</license>\n'
            '  <buildtool_depend>ament_cmake</buildtool_depend>\n'
            '  <exec_depend>robot_state_publisher</exec_depend>\n'
            '  <exec_depend>rviz2</exec_depend>\n'
            '  <export><build_type>ament_cmake</build_type></export>\n'
            '</package>\n'
        )

    with open(os.path.join(pkg_dir, "CMakeLists.txt"), "w", encoding="utf-8") as f:
        f.write(
            "cmake_minimum_required(VERSION 3.8)\n"
            f"project({pkg})\n"
            "find_package(ament_cmake REQUIRED)\n"
            "install(DIRECTORY urdf launch DESTINATION share/${PROJECT_NAME})\n"
            "ament_package()\n"
        )
    return pkg_dir


root = tempfile.mkdtemp(prefix="mcpfc_ros2_")
pkg_dir = write_ros2_package(root, "demo_bot_description", URDF)

required = ["package.xml", "CMakeLists.txt", os.path.join("urdf", "demo_bot_description.urdf")]
for rel in required:
    p = os.path.join(pkg_dir, rel)
    assert os.path.isfile(p) and os.path.getsize(p) > 0, f"missing {rel}"
    print("wrote", rel)

# package.xml and the URDF must be valid XML
et.parse(os.path.join(pkg_dir, "package.xml"))
et.parse(os.path.join(pkg_dir, "urdf", "demo_bot_description.urdf"))
print("URDF_PACKAGE_OK")
