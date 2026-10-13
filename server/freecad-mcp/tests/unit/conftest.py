"""
Shared fixtures and mocks for FreeCAD MCP unit tests.

These tests run WITHOUT FreeCAD installed by mocking the FreeCAD modules.
"""

import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

import pytest

# MCP FREECAD v2 (2026-10-12): the handler computes its ~/.freecad-mcp paths
# (auth token, logs, crashes) at import time, and on Windows some tests run
# start_server's real Windows branch -- they overwrote the auth token of the
# user's live FreeCAD, locking every client out of it. Point the home
# directory at a throwaway folder before any project module is imported.
_SANDBOX_HOME = tempfile.mkdtemp(prefix="freecad_mcp_unit_home_")
for _var in ("HOME", "USERPROFILE"):
    os.environ[_var] = _SANDBOX_HOME
# A fake token, as a running FreeCAD would leave: the bridge's Windows path
# reads it, so those tests no longer depend on the user's real file.
os.makedirs(os.path.join(_SANDBOX_HOME, ".freecad-mcp"), exist_ok=True)
with open(os.path.join(_SANDBOX_HOME, ".freecad-mcp", "windows_auth_token"), "w") as _f:
    _f.write("0" * 64)


@pytest.fixture(autouse=True)
def _upstream_cam_tools_enabled(monkeypatch):
    """MCP FREECAD v2 hides the CAM tools unless FREECAD_MCP_ENABLE_CAM=1. Upstream's
    own tests exercise them, so they run with CAM enabled; the default (hidden and
    refused) is covered by test_mcp_protocol.py::TestCamDisabledByDefault."""
    monkeypatch.setenv("FREECAD_MCP_ENABLE_CAM", "1")


@pytest.fixture(autouse=True)
def _bridge_trusts_mocked_freecad(monkeypatch):
    """Protocol tests stand in for FreeCAD with mocked socket replies that do
    not model the listener proof (MCP FREECAD v2) -- on Windows the bridge
    would refuse to talk to them. The proof itself is tested directly in
    test_bridge_framing.py and test_freecad_mcp_handler.py."""
    bridge = sys.modules.get("freecad_mcp_server")
    if bridge is not None and hasattr(bridge, "verify_listener"):
        monkeypatch.setattr(bridge, "verify_listener", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def mock_freecad(monkeypatch):
    """Mock the FreeCAD and related modules so freecad_mcp_handler.py can import."""
    # Create mock FreeCAD module
    fc = types.ModuleType("FreeCAD")
    fc.GuiUp = False  # Console mode — avoids PySide/FreeCADGui imports
    fc.Console = MagicMock()
    fc.ActiveDocument = None
    fc.newDocument = MagicMock()
    fc.getUserAppDataDir = MagicMock(return_value="/tmp/fake_freecad")
    fc.Document = type("Document", (), {})  # type stub for annotations

    # Preference store: an EMPTY store, so GetBool/GetString return the
    # default the caller passed. A test that needs a set preference replaces
    # fc.ParamGet on this fixture (see test_base_handler.TestAutosaveBefore).
    pref = MagicMock()
    pref.GetBool = MagicMock(side_effect=lambda key, default=False: default)
    pref.GetString = MagicMock(side_effect=lambda key, default="": default)
    fc.ParamGet = MagicMock(return_value=pref)

    # Create mock FreeCADGui
    fcgui = types.ModuleType("FreeCADGui")

    # Create mock Part module
    part = types.ModuleType("Part")

    # Install into sys.modules before any import
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fcgui)
    monkeypatch.setitem(sys.modules, "Part", part)
    monkeypatch.setitem(sys.modules, "PySide", MagicMock())
    monkeypatch.setitem(sys.modules, "PySide.QtCore", MagicMock())

    return fc
