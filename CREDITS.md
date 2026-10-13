# Credits & third-party projects

This project builds on the following open-source work. All trademarks and copyrights belong to
their respective authors. Only **freecad-mcp** is included in this repository: a modified copy
(git subtree) in [`server/freecad-mcp/`](server/freecad-mcp/), kept under its own LGPL licence.
Its changes are listed in [`CHANGES-NikeTheBee.md`](server/freecad-mcp/CHANGES-NikeTheBee.md).
The other projects are installed separately (FreeCAD by the user, the workbenches by
`install/bootstrap.py --with-grafts`) and driven at runtime.

| Project | Author(s) | License | Role here |
|---|---|---|---|
| [FreeCAD](https://www.freecad.org) | FreeCAD contributors | LGPL-2.1-or-later | CAD kernel & application being driven (v1.1.1) |
| [freecad-mcp](https://github.com/blwfish/freecad-mcp) | Brian L. Wong (blwfish) | LGPL-2.1-or-later | Base MCP server bridging an AI client to FreeCAD — vendored v8.3.0, modified (hole in Body, sketch DoF, oversized replies, listener proof, Windows crash tracking) |
| [Rocket Workbench](https://github.com/davesrocketshop/Rocket) | David Carter (davesrocketshop) | LGPL-2.1-or-later · MIT (PyAtmos) | Rocketry domain graft (nose/body/fins) |
| [AirPlaneDesign Workbench](https://github.com/FredsFactory/FreeCAD_AirPlaneDesign) | FredsFactory | LGPL-2.1 | Aero domain graft (NACA airfoils, fuselage profiles) |
| [CROSS Workbench](https://github.com/galou/freecad.cross) | Gaël Écorchard (galou) | LGPL-2.1-or-later | Robotics domain graft (URDF/xacro generation) |
| [Model Context Protocol](https://modelcontextprotocol.io) / `mcp` Python SDK | Anthropic & MCP contributors | MIT | The protocol and SDK used by the bridge |

The original design specification for this system is in [`docs/CAHIER_DES_CHARGES.md`](docs/CAHIER_DES_CHARGES.md).

This project's own code is under the **PolyForm Noncommercial License 1.0.0**
([`LICENSE`](LICENSE), [`NOTICE`](NOTICE)). You may use, modify and share it for any
noncommercial purpose. Keep the `Required Notice:` line and this credits file. Selling it, or
any other commercial use, needs prior written permission from **NikeTheBee**.
`server/freecad-mcp/` is excluded and stays LGPL-2.1-or-later.
