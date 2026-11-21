"""Software bill of materials of the workshop, from the lock files alone (CycloneDX 1.5 JSON).

Answers "what is actually running here, from where, under which licence, with which hash" without
a network call and without importing anything: it reads `pixi.lock` (conda and PyPI packages, each
with the SHA256 the installer verified), `install/tools.lock.json` (the tools outside conda, with
their licence and the provenance of their hash) and the vendored MCP base.

    py -3.13 install/sbom.py [--out <file>]

Default output: <workshop home>/sbom.cdx.json if MCPFC_HOME is set, else ./sbom.cdx.json. The
file is NOT committed: it is regenerated from the locks, which are what the repository pins.

Honest about what it cannot know: a conda lock entry carries the package's URL and hash but not
its licence, so `licenses` is filled only where we record it ourselves (the external tools, the
base). Those gaps are marked, never guessed. Sentinel: SBOM_OK
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def pixi_components() -> list:
    """One component per package of the `packages:` section, with its hash and download URL."""
    text = (REPO / "pixi.lock").read_text(encoding="utf-8")
    parts = re.split(r"\npackages:\n", text, maxsplit=1)
    if len(parts) != 2:
        return []
    out = []
    for block in re.split(r"\n- (?=conda:|pypi:)", parts[1]):
        m = re.match(r"(conda|pypi):\s*(\S+)", block.strip())
        if not m:
            continue
        kind, url = m.group(1), m.group(2)
        sha = re.search(r"\bsha256:\s*([0-9a-f]{64})", block)
        name = re.search(r"\n\s*name:\s*(\S+)", block)
        ver = re.search(r"\n\s*version:\s*(\S+)", block)
        if name and ver:
            nm, vr = name.group(1), ver.group(1)
        else:   # conda entries carry the name and version in the file name
            base = url.rsplit("/", 1)[-1]
            base = re.sub(r"\.(conda|tar\.bz2|whl|tar\.gz)$", "", base)
            bits = base.split("-")
            nm = "-".join(bits[:-2]) if len(bits) >= 3 else base
            vr = bits[-2] if len(bits) >= 3 else "unknown"
        comp = {
            "type": "library",
            "name": nm,
            "version": vr,
            "purl": f"pkg:{'conda' if kind == 'conda' else 'pypi'}/{nm}@{vr}",
            "externalReferences": [{"type": "distribution", "url": url}],
            "properties": [{"name": "mcpfc:source", "value": f"{kind} (pixi.lock)"}],
        }
        if sha:
            comp["hashes"] = [{"alg": "SHA-256", "content": sha.group(1)}]
        else:
            comp["properties"].append({"name": "mcpfc:hash", "value": "none in the lock"})
        out.append(comp)
    return out


def tool_components() -> list:
    """The tools installed outside conda: these we DO know the licence of."""
    lock = json.loads((REPO / "install" / "tools.lock.json").read_text(encoding="utf-8"))
    out = []
    for name, t in lock.items():
        if name.startswith("_"):
            continue
        out.append({
            "type": "application",
            "name": name,
            "version": str(t.get("version", "unknown")),
            "purl": f"pkg:generic/{name}@{re.sub(r'[^A-Za-z0-9._-]', '_', str(t.get('version')))}",
            "licenses": [{"license": {"name": t["license"]}}] if t.get("license") else [],
            "hashes": [{"alg": "SHA-256", "content": t["sha256"]}] if t.get("sha256") else [],
            "externalReferences": [{"type": "distribution", "url": t.get("url", "")}],
            "properties": [
                {"name": "mcpfc:source", "value": "install/tools.lock.json"},
                {"name": "mcpfc:hash-provenance", "value": str(t.get("sha_source", "unrecorded"))},
                {"name": "mcpfc:packs", "value": ", ".join(t.get("packs") or [])},
            ],
        })
    return out


def base_component() -> dict:
    """The vendored MCP base, identified by the digest of its own changelog of modifications."""
    ch = REPO / "server" / "freecad-mcp" / "CHANGES-NikeTheBee.md"
    lic = REPO / "server" / "freecad-mcp" / "LICENSE"
    name = "unknown"
    if lic.is_file():
        head = lic.read_text(encoding="utf-8", errors="replace")[:400]
        for cand in ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL", "MIT", "Apache"):
            if cand in head:
                name = cand
                break
    comp = {
        "type": "library",
        "name": "freecad-mcp (vendored, modified)",
        "version": "8.3.0+NikeTheBee",
        "licenses": [{"license": {"name": name}}],
        "properties": [{"name": "mcpfc:source", "value": "git subtree server/freecad-mcp"}],
    }
    if ch.is_file():
        comp["properties"].append({
            "name": "mcpfc:modifications-digest",
            "value": hashlib.sha256(ch.read_bytes()).hexdigest(),
        })
    return comp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    home = os.environ.get("MCPFC_HOME", "")
    out = Path(a.out) if a.out else (Path(home) / "sbom.cdx.json" if home
                                     else REPO / "sbom.cdx.json")
    comps = tool_components() + [base_component()] + pixi_components()
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "component": {"type": "application", "name": "mcp-freecad-atelier",
                          "version": "v2"},
            "tools": [{"name": "install/sbom.py", "version": "1"}],
        },
        "components": comps,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    hashed = sum(1 for c in comps if c.get("hashes"))
    licensed = sum(1 for c in comps if c.get("licenses"))
    print(f"{len(comps)} components -> {out}")
    print(f"  with a SHA-256: {hashed}/{len(comps)}; with a recorded licence: {licensed} "
          f"(a conda lock entry carries no licence, so those are left empty rather than guessed)")
    print("SBOM_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
