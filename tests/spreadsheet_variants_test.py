"""Validate spreadsheet-driven parametric variants (cahier des charges §4.1).

Run with:  A:\FreeCAD\bin\freecadcmd.exe tests\spreadsheet_variants_test.py

A Spreadsheet with named aliases drives object dimensions via expressions, so a
whole design family comes from editing one cell. Bundled FreeCAD. Prints
SPREADSHEET_OK on success.
"""
import FreeCAD

doc = FreeCAD.newDocument("VariantsTest")

sheet = doc.addObject("Spreadsheet::Sheet", "Params")
sheet.set("A1", "length"); sheet.set("B1", "40"); sheet.setAlias("B1", "length")
sheet.set("A2", "width");  sheet.set("B2", "20"); sheet.setAlias("B2", "width")
sheet.set("A3", "height"); sheet.set("B3", "10"); sheet.setAlias("B3", "height")
doc.recompute()

box = doc.addObject("Part::Box", "Box")
box.setExpression("Length", "Params.length")
box.setExpression("Width", "Params.width")
box.setExpression("Height", "Params.height")
doc.recompute()
print(f"VARIANT A: {box.Length} x {box.Width} x {box.Height}  vol={box.Shape.Volume:.0f}")
assert abs(box.Shape.Volume - 40 * 20 * 10) < 1e-6, "variant A wrong"

# Change ONE cell -> a new variant of the whole part
sheet.set("B1", "60")
doc.recompute()
print(f"VARIANT B: {box.Length} x {box.Width} x {box.Height}  vol={box.Shape.Volume:.0f}")
assert abs(box.Shape.Volume - 60 * 20 * 10) < 1e-6, "variant B did not update from spreadsheet"

print("SPREADSHEET_OK")
