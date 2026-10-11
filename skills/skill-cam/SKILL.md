# skill-cam — DISABLED (safety)

**Load when:** the user asks for machining / CNC — toolpaths, G-code, drilling/milling, "Path/CAM job".

## Status: CAM is switched off in this project
The former `freecad_layers/cam.py` was **removed** (audit 2026-10-11, §2.2 K1/K2): it treated every
cylindrical face (fillets, bosses, the outside of a round bar) as a hole, used a default Ø5 end mill
whatever the hole size, and posted G-code with **no spindle start (`M3`) and a zero feed (`F0`)**.
That output can break a tool or a machine.

## What to do instead
1. **Never** produce G-code meant for a real machine — not with `execute_python`, not with the base
   server's `cam_operations` tool.
2. Export the part as **STEP** (`skill-exchange`) and tell the user to program the toolpaths in a CAM
   tool they control (FreeCAD CAM workbench in the GUI, or their usual CAM software), with a
   simulation before cutting.
3. If the user explicitly insists on a draft toolpath, say clearly that it is **not machine-safe**
   and must be reviewed and simulated by a person who knows the machine.

## When it may come back
Only with a G-code validator that refuses any program without `M3`/`S>0` before the first cut, with
an `F0` move, with a cut below the stock, with a tool larger than the hole, or drilling anything but
a **concave, through** cylindrical hole — and that re-checks drilled positions against the measured
hole centres (audit §7.4 N7).
