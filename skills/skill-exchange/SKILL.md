# skill-exchange

**Load when:** exporting/importing CAD interchange formats — STEP, IGES, BREP (for sending parts to
other CAD, suppliers, or simulation tools).

## What this is
CAD interchange via **bundled FreeCAD** (`Part`, `Import`) — no install. STL is separate (`skill-print3d`).

## Export
Via the MCP tools: **`export(target, format="step")`** (`stp`, `brep`, `iges` too) — refused unless
`check` is OK, then the file is read back: same solids, same volume, same place. The Python below is
for `execute_python` only.
```python
obj.Shape.exportStep(r"part.step")            # STEP (preferred for solids, preserves topology)
obj.Shape.exportBrep(r"part.brep")            # native OCCT
import Import
Import.export([objA, objB], r"asm.iges")      # extension-driven: .step/.stp/.iges/.igs
```

## Import / round-trip
```python
import Import
Import.insert(r"part.step", doc.Name)         # into existing doc
doc.recompute()
# verify a solid came through:
solids = sum(len(o.Shape.Solids) for o in doc.Objects if hasattr(o, "Shape"))
```

## Notes
- STEP is the safe default for solids (keeps B-rep topology); IGES is surface-oriented and lossier.
- Units are mm. Verify after import with `skill-verify` (`verify.verdict`) before continuing.

Runnable example (export + round-trip): `install/exchange_test.py`.
