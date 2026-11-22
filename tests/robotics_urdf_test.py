"""Validate robotics URDF export via the CROSS workbench, headless (cahier §4.2).

Run with:  A:\FreeCAD\bin\freecadcmd.exe tests\robotics_urdf_test.py

CROSS (galou/freecad.cross, cloned in addons/) generates URDF/xacro from FreeCAD
geometry. This needs NO ROS2 runtime. CROSS lives under the `freecad` namespace
package and pulls FreeCADGui in a util import, so we stub Gui and extend the
namespace path. Builds a one-link robot URDF from a box and checks the XML.
Prints ROBOTICS_URDF_OK on success.
"""
import os
import sys
import types
import tempfile
import xml.etree.ElementTree as et

# CROSS imports FreeCADGui transitively; stub it for headless use.
sys.modules.setdefault("FreeCADGui", types.ModuleType("FreeCADGui"))

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cross = os.path.join(_repo, "addons", "freecad.cross")
if not os.path.isdir(_cross):
    sys.exit(f"CROSS not found at {_cross} — clone galou/freecad.cross there")

import FreeCAD
import freecad  # FreeCAD's own namespace package
freecad.__path__.append(os.path.join(_cross, "freecad"))  # let it find freecad.cross

from freecad.cross import urdf_utils

doc = FreeCAD.newDocument("RoboticsTest")
box = doc.addObject("Part::Box", "base")
box.Length, box.Width, box.Height = 100, 60, 40
doc.recompute()

# Assemble a minimal one-link robot URDF
robot = et.Element("robot", {"name": "demo_bot"})
link = et.SubElement(robot, "link", {"name": "base_link"})
link.append(urdf_utils.urdf_visual_from_box(box))
link.append(urdf_utils.urdf_collision_from_box(box))
xml_str = et.tostring(robot, encoding="unicode")

for token in ("<robot", 'name="demo_bot"', "base_link", "<visual", "<collision", "<box size="):
    assert token in xml_str, f"URDF missing {token!r}\n{xml_str}"

out = os.path.join(tempfile.mkdtemp(prefix="mcpfc_urdf_"), "demo_bot.urdf")
with open(out, "w", encoding="utf-8") as f:
    f.write(xml_str)
assert os.path.getsize(out) > 0
print("URDF written:", os.path.basename(out), f"({os.path.getsize(out)} bytes)")
print("ROBOTICS_URDF_OK")
