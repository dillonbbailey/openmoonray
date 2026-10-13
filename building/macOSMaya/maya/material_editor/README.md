# MoonRay Material Editor for Maya

A one-off copy of the Material Editor from MoonLab
(<https://github.com/dillonbbailey/MoonLab>, `moonray_editor/`, commit 0f19c81),
adapted to run inside Maya 2027. The window contents are unchanged: shader
library, material graphs, USD scene nodes, graph tabs, preview, and the
Parameters / Preview setup / Render log tabs, plus the floating editor window's
toolbar and menus.

Open it from **MoonRay ▸ Material Editor** (added by `../scripts/userSetup.py`;
`make-maya-module.sh` puts both directories on Maya's PYTHONPATH).

What changed for Maya:
- `editor.py` (MoonLab's `app.py`): only the Material Editor is kept. The
  Lunatic application around it is gone (USD Viewer, RenderView, projects, MCP,
  Python console, simulation, layouts, restart).
- `maya_host.py`: a retained, dockable workspace control. Closing the panel
  only hides it, so open graphs and running previews are kept.
- The stylesheet is set on the editor, never on Maya's QApplication. The
  app-wide appearance event filter became `themes.polish_tree`. Menu
  shortcuts only apply while the editor has focus.
- `maya_stage.py`: replaces the usdview worker for USD scene features, answering
  its requests in-process for mayaUsd stages with MoonLab's own USD helpers:
  - The current stage follows Maya's selection: the stage of selected USD prims
    or of a selected proxy shape, otherwise the only stage in the scene.
  - USD SCENE NODES lists that stage's materials, lights, cameras and textures.
  - **Tools ▸ Assign material to selected USD prims** binds the current tab;
    the same item is on a material's right-click menu in MATERIAL GRAPHS.
    As in Maya, assigning to a group reaches everything under it: if geometry
    below has its own binding (models imported from Maya do), the group's
    binding is made `strongerThanDescendants`. **Tools ▸ Override materials
    below** (also on both right-click menus, remembered) turns this off, for
    USD's default: geometry with its own material keeps it. The status bar
    says which happened.
    Material sync, *Select bound prims*, light / camera / transform links and
    the conversion dialog work as in MoonLab.
  - Edits go to the stage's edit target (Maya's Layer Editor) and into Maya's
    undo queue as one "MoonRay: …" step each, through mayaUsd's
    `UsdUndoBlock`. MoonLab's separate USD undo stack is not used.
- Background jobs (preview renders, exports, thumbnails, .tx, bakes) run through
  `run-job.sh` in the workspace's MoonRay environment (`building/macOSLocal`:
  USD 22.11, Python 3.9, OpenImageIO), started with an empty environment so
  nothing from Maya leaks in. `usd_compat.py` covers the USD 22.11 gaps.
- Settings use their own QSettings name. Caches live in
  `local-build/maya/material-editor-cache`, not in the home directory.
- MoonLab's USD schemas (`assets/usd_schemas`) are only registered in jobs.
  In Maya they would clash with hdMoonray's `MoonrayMeshLight`.
- File metadata keys (`lunatic:*`) and the `.moonraygraph` format are
  unchanged, so graphs move freely between MoonLab and Maya.

Tests, run inside Maya from `building/macOSMaya`:
- `M3_SCRIPT=material-editor-test.py bash m3-render-test.sh` checks the
  window, the preview render and that Maya's own UI is left alone.
- `M3_SCRIPT=material-editor-usd-test.py HDMOONRAY_RDLA_OUTPUT=<file>.rdla bash m3-render-test.sh`
  checks binding, Maya undo, sync, selection and the Hydra Moonray result.
- `M3_SCRIPT=material-editor-live-test.py bash m3-render-test.sh` assigns a
  material to an Xform from the right-click menu while Hydra Moonray renders;
  the sphere inside has its own material. With Override materials below off
  it keeps it; on, the viewport turns red.
- Material not showing? In Maya's Script Editor (Python):
  `from moonray_maya import diagnose; diagnose.materials()` prints what each
  piece of geometry under the selection resolves to, and why.
