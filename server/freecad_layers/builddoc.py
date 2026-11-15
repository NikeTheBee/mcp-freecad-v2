"""Assembly instructions (FEUILLE_DE_ROUTE_ATELIER.md §7.3): one self-contained HTML file.

build(doc, steps, path):
- a parts list (made / bought, from bom.collect);
- one section per step: the parts added (bought ones by their standard designation), the
  human's note, the tightening torque ONLY when the user gave it (a torque is never invented:
  without one, the screw or insert maker's value applies and the sheet says so), and an iso
  picture of everything assembled so far (render.views, headless);
- steps = [{"parts": [names], "note": "...", "torque_Nm": 4.5}], or None: one step per made
  part in document order, then one step per standard designation.

Labels come from documents (external data): everything is HTML-escaped. Pictures are embedded
(base64): the file can be mailed or printed alone.
"""
from __future__ import annotations

import base64
import html
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import bom, project_dir, render, safe_out_path


def _auto_steps(doc) -> List[Dict[str, Any]]:
    made, bought = [], {}
    for inst, part in bom._parts(doc, False):
        if (getattr(part, "Source", "") or "make") == "buy":
            bought.setdefault(getattr(part, "StdPart", "") or part.Label, []).append(inst.Name)
        else:
            made.append(inst.Name)
    steps = [{"parts": [m], "note": ""} for m in made]
    steps += [{"parts": names, "note": ""} for names in bought.values()]
    if not steps:
        raise ValueError("no part to assemble in the active document")
    return steps


def build(doc, steps: Optional[List[Dict[str, Any]]] = None, path: Optional[str] = None,
          title: str = "", overwrite: bool = False) -> Dict[str, Any]:
    steps = steps or _auto_steps(doc)
    for i, st in enumerate(steps, 1):
        if not st.get("parts"):
            raise ValueError(f"step {i}: list the parts it adds")
        for n in st["parts"]:
            if doc.getObject(n) is None:
                raise ValueError(f"step {i}: no object {n!r}")
    rows = bom.collect(doc, include_hidden=False)
    out = safe_out_path(path or f"builddoc/{doc.Name}_assembly.html", {".html"}, overwrite=overwrite)
    shots = project_dir() / "renders"
    so_far: List[str] = []
    parts_html: List[str] = []
    for i, st in enumerate(steps, 1):
        so_far += [n for n in st["parts"] if n not in so_far]
        png = render.views(list(so_far), path=str(shots / f"builddoc_{doc.Name}_{i:02d}.png"),
                           which=("iso",), size_px=700, overwrite=True)["path"]
        img = base64.b64encode(Path(png).read_bytes()).decode("ascii")
        items = []
        for n in st["parts"]:
            o = doc.getObject(n)
            name = getattr(o, "StdPart", "") or o.Label
            src = "bought" if (getattr(o, "Source", "") or "make") == "buy" else "made"
            items.append(f"<li>{html.escape(name)} <small>({src}, {html.escape(o.Name)})</small></li>")
        tq = st.get("torque_Nm")
        torque = (f"<p><b>Tightening torque:</b> {float(tq):g} N&middot;m (given by the user)</p>" if tq
                  else ("<p><b>Tightening torque:</b> the screw / insert maker's value (not given here)</p>"
                        if any((getattr(doc.getObject(n), "Source", "") or "") == "buy" for n in st["parts"])
                        else ""))
        note = f"<p>{html.escape(str(st.get('note', '')))}</p>" if st.get("note") else ""
        parts_html.append(f"<section><h2>Step {i}</h2><ul>{''.join(items)}</ul>{note}{torque}"
                          f"<img alt='assembly after step {i}' src='data:image/png;base64,{img}'></section>")
    table = "".join(
        f"<tr><td>{r['item']}</td><td>{html.escape(str(r['designation']))}</td><td>{r['qty']}</td>"
        f"<td>{'bought' if r.get('source') == 'buy' else 'made'}</td>"
        f"<td>{' x '.join(f'{d:g}' for d in r['bbox_mm'])}</td></tr>" for r in rows)
    head = html.escape(title or f"{doc.Label}: assembly")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{head}</title>
<style>body{{font-family:sans-serif;max-width:900px;margin:auto;padding:16px}}
table{{border-collapse:collapse}}td,th{{border:1px solid #999;padding:4px 8px}}
img{{max-width:100%;border:1px solid #ccc}}section{{page-break-inside:avoid;margin-top:24px}}</style>
</head><body><h1>{head}</h1><p>Generated {time.strftime('%Y-%m-%d %H:%M')} from the FreeCAD model.
Check every part against its drawing before assembly.</p>
<h2>Parts</h2><table><tr><th>#</th><th>Designation</th><th>Qty</th><th>Source</th><th>Size (mm)</th></tr>
{table}</table>{''.join(parts_html)}</body></html>"""
    out.write_text(page, encoding="utf-8")
    return {"path": str(out), "steps": len(steps), "lines": len(rows)}
