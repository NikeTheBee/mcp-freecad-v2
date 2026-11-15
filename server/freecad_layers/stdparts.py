"""Standard purchased parts (FEUILLE_DE_ROUTE_ATELIER.md §7.1): screws, nuts, washers, bearings.

In-house, simplified geometry from the standards' nominal dimensions (no thread, no chamfers):
right for fits, clearances, assembly checks, mass estimates and the BOM; never a manufacturing
drawing of the part (it is bought). Each part carries:
- `StdPart` = its designation ("ISO 4762 M6x20", "Bearing 608 (8x22x7)"), used by the BOM;
- `Source` = "buy" (the BOM separates purchased from made parts).

Tables (nominal values, mm):
- ISO 4762 socket head cap screw: head diameter dk, head height k, hexagon socket s.
- ISO 4017 hexagon head screw: across flats s, head height k.
- ISO 4032 hexagon nut: across flats s, height m.
- ISO 7089 plain washer: inner d1, outer d2, thickness h.
- ISO 273 clearance holes: fine / medium / coarse.
- ISO 15 deep groove ball bearings (series 6xx, 60xx, 62xx, 68xx): bore d, outside D, width B.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

import FreeCAD as App
import Part

ISO4762 = {"M2": (3.8, 2, 1.5), "M2.5": (4.5, 2.5, 2), "M3": (5.5, 3, 2.5), "M4": (7, 4, 3),
           "M5": (8.5, 5, 4), "M6": (10, 6, 5), "M8": (13, 8, 6), "M10": (16, 10, 8),
           "M12": (18, 12, 10), "M16": (24, 16, 14), "M20": (30, 20, 17)}
ISO4017 = {"M3": (5.5, 2), "M4": (7, 2.8), "M5": (8, 3.5), "M6": (10, 4), "M8": (13, 5.3),
           "M10": (16, 6.4), "M12": (18, 7.5), "M16": (24, 10), "M20": (30, 12.5)}
ISO4032 = {"M2": (4, 1.6), "M2.5": (5, 2), "M3": (5.5, 2.4), "M4": (7, 3.2), "M5": (8, 4.7),
           "M6": (10, 5.2), "M8": (13, 6.8), "M10": (16, 8.4), "M12": (18, 10.8),
           "M16": (24, 14.8), "M20": (30, 18)}
ISO7089 = {"M2": (2.2, 5, 0.3), "M2.5": (2.7, 6, 0.5), "M3": (3.2, 7, 0.5), "M4": (4.3, 9, 0.8),
           "M5": (5.3, 10, 1), "M6": (6.4, 12, 1.6), "M8": (8.4, 16, 1.6), "M10": (10.5, 20, 2),
           "M12": (13, 24, 2.5), "M16": (17, 30, 3), "M20": (21, 37, 3)}
ISO273 = {"M2": (2.2, 2.4, 2.6), "M2.5": (2.7, 2.9, 3.1), "M3": (3.2, 3.4, 3.6),
          "M4": (4.3, 4.5, 4.8), "M5": (5.3, 5.5, 5.8), "M6": (6.4, 6.6, 7), "M8": (8.4, 9, 10),
          "M10": (10.5, 11, 12), "M12": (13, 13.5, 14.5), "M16": (17, 17.5, 18.5),
          "M20": (21, 22, 24)}
BEARINGS = {"623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6),
            "608": (8, 22, 7), "609": (9, 24, 7), "688": (8, 16, 5), "6800": (10, 19, 5),
            "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9), "6003": (17, 35, 10),
            "6004": (20, 42, 12), "6005": (25, 47, 12), "6200": (10, 30, 9), "6201": (12, 32, 10),
            "6202": (15, 35, 11), "6203": (17, 40, 12), "6204": (20, 47, 14), "6205": (25, 52, 15)}
STEEL_KG_MM3 = 7.85e-6
_AXES = {"x": App.Vector(1, 0, 0), "y": App.Vector(0, 1, 0), "z": App.Vector(0, 0, 1),
         "-x": App.Vector(-1, 0, 0), "-y": App.Vector(0, -1, 0), "-z": App.Vector(0, 0, -1)}


def _d(size: str) -> float:
    return float(size.upper().lstrip("M"))


def _size(size: str, table: Dict[str, Any], what: str) -> str:
    s = str(size).strip().upper()
    s = s if s.startswith("M") else "M" + s
    if s not in table:
        raise ValueError(f"{what}: size {size!r} not in the table; one of {', '.join(table)}")
    return s


def _hexagon(across_flats: float, height: float):
    r = across_flats / math.sqrt(3)                   # circumradius of the hexagon
    pts = [App.Vector(r * math.cos(math.radians(30 + 60 * i)), r * math.sin(math.radians(30 + 60 * i)), 0)
           for i in range(6)]
    return Part.Face(Part.makePolygon(pts + [pts[0]])).extrude(App.Vector(0, 0, height))


def shape(kind: str, size: str, length: float = 0.0):
    """(shape along +Z from z=0 = bearing face / under-head plane, designation)."""
    k = kind.lower()
    if k in ("iso4762", "shcs", "socket_head"):
        s = _size(size, ISO4762, "ISO 4762")
        dk, kh, hs = ISO4762[s]
        if length <= 0:
            raise ValueError("a screw needs its length (mm, under the head)")
        head = Part.makeCylinder(dk / 2, kh, App.Vector(0, 0, 0))
        head = head.cut(_hexagon(hs, kh * 0.6).translate(App.Vector(0, 0, kh * 0.4 + 1e-3)))
        sh = head.translate(App.Vector(0, 0, 0)).fuse(Part.makeCylinder(_d(s) / 2, length, App.Vector(0, 0, -length)))
        return sh.removeSplitter(), f"ISO 4762 {s}x{length:g}"
    if k in ("iso4017", "hex_screw"):
        s = _size(size, ISO4017, "ISO 4017")
        af, kh = ISO4017[s]
        if length <= 0:
            raise ValueError("a screw needs its length (mm, under the head)")
        sh = _hexagon(af, kh).fuse(Part.makeCylinder(_d(s) / 2, length, App.Vector(0, 0, -length)))
        return sh.removeSplitter(), f"ISO 4017 {s}x{length:g}"
    if k in ("iso4032", "nut"):
        s = _size(size, ISO4032, "ISO 4032")
        af, m = ISO4032[s]
        return _hexagon(af, m).cut(Part.makeCylinder(_d(s) / 2, m + 2, App.Vector(0, 0, -1))), f"ISO 4032 {s}"
    if k in ("iso7089", "washer"):
        s = _size(size, ISO7089, "ISO 7089")
        d1, d2, h = ISO7089[s]
        return (Part.makeCylinder(d2 / 2, h).cut(Part.makeCylinder(d1 / 2, h + 2, App.Vector(0, 0, -1))),
                f"ISO 7089 {s}")
    if k in ("bearing", "iso15"):
        b = str(size).strip()
        if b not in BEARINGS:
            raise ValueError(f"bearing {size!r} not in the table; one of {', '.join(BEARINGS)}")
        d, D, B = BEARINGS[b]
        ring = Part.makeCylinder(D / 2, B).cut(Part.makeCylinder(d / 2, B + 2, App.Vector(0, 0, -1)))
        return ring, f"Bearing {b} ({d:g}x{D:g}x{B:g})"
    raise ValueError("kind: iso4762 | iso4017 | iso4032 | iso7089 | bearing")


def clearance(size: str, fit: str = "medium") -> float:
    """ISO 273 clearance hole diameter for a metric screw."""
    s = _size(size, ISO273, "ISO 273")
    return ISO273[s][("fine", "medium", "coarse").index(fit)]


def make(doc, kind: str, size: str, length: float = 0.0, name: str = "",
         at: Optional[Sequence[float]] = None, axis: str = "z") -> Dict[str, Any]:
    """Add the part to `doc` at `at`, its axis along `axis` (the screw shank points to -axis)."""
    shp, designation = shape(kind, size, float(length or 0))
    if axis not in _AXES:
        raise ValueError(f"axis: one of {', '.join(_AXES)}")
    rot = App.Rotation(App.Vector(0, 0, 1), _AXES[axis])
    shp.Placement = App.Placement(App.Vector(*(at or (0, 0, 0))), rot)
    o = doc.addObject("Part::Feature", name or designation.replace(" ", "_").replace(".", "_").replace("(", "").replace(")", ""))
    o.Shape = shp
    o.Label = designation
    o.addProperty("App::PropertyString", "StdPart", "Standard part", "designation for the BOM")
    o.addProperty("App::PropertyString", "Source", "Standard part", "buy (purchased) or make")
    o.StdPart, o.Source = designation, "buy"
    doc.recompute()
    return {"name": o.Name, "designation": designation, "volume_mm3": round(o.Shape.Volume, 3),
            "mass_g_steel": round(o.Shape.Volume * STEEL_KG_MM3 * 1000, 3),
            "valid": bool(o.Shape.isValid())}
