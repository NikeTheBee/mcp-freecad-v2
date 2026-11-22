"""MCP FREECAD v2: compact tool schemas, the same tools for fewer tokens (any MCP client pays
the tool list at every session start).

MCPFC_TOOLS=compact (default):
- a tool description keeps its first sentences up to ~240 characters; a parameter description
  keeps its first sentence up to ~90 characters; types, enums, defaults, required and
  annotations are untouched, so every call that worked still works;
- developer/debug tools (DORMANT) are not listed but stay callable by name;
- the full text of any tool, dormant or not: get_usage_guidance(operation="schema",
  topic="<tool name>").
MCPFC_TOOLS=full: the original schemas, every tool listed.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

DORMANT = frozenset({"test_echo", "reload_modules", "manage_connection", "get_debug_logs",
                     "run_inspector", "list_freecad_instances", "fixture_operations",
                     "continue_selection"})
TOOL_MAX, PARAM_MAX = 240, 70
_SCHEMA = "input_schema"         # mcp 2.x field name (the JSON alias stays inputSchema)
_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(`\"'])|\s+--\s+|;\s+")


def compact_mode() -> bool:
    return os.environ.get("MCPFC_TOOLS", "compact").lower() != "full"


def shorten(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    out = ""
    for m in _END.finditer(text):
        if m.start() > limit:
            break
        out = text[:m.start()].rstrip(" ;-")
    if len(out) >= min(25, limit // 3):
        return out
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:(-")
    return cut + "…"


def _schema(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "examples":
                continue
            if k == "description" and isinstance(v, str):
                out[k] = shorten(v, PARAM_MAX)
            else:
                out[k] = _schema(v)
        return out
    if isinstance(node, list):
        return [_schema(x) for x in node]
    return node


def compact_tools(tools: List[Any]) -> List[Any]:
    """Copies of the mcp `types.Tool` objects with compact text; dormant ones left out."""
    out = []
    for t in tools:
        if t.name in DORMANT:
            continue
        out.append(t.model_copy(update={"description": shorten(t.description or "", TOOL_MAX),
                                        _SCHEMA: _schema(getattr(t, _SCHEMA))}))
    return out


def full_schema(tools: List[Any], name: str) -> str:
    for t in tools:
        if t.name == name:
            return json.dumps({"name": t.name, "description": t.description,
                               "inputSchema": getattr(t, _SCHEMA)}, indent=1)
    names = sorted(t.name for t in tools)
    return json.dumps({"error": f"no tool {name!r}", "tools": names})
