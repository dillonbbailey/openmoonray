# Plan: MoonRay Hydra render delegate inside Maya 2027 (macOS, Apple Silicon)

Status: **verified draft.** Both Stage 0 gates from the first draft are now answered, and both
came back favourable. One new top risk was found. The Rosetta question is resolved — Maya
runs native arm64 (Risk 2). **M0–M2 are done**: the delegate builds against Maya's USD 25.11,
and "Hydra Moonray" appears in Maya's viewport Renderer menu (2026-10-05). Next: M3.

Target branch: `macos-local-usdview`, or a new `maya2027-hydra` branch off it.

---

## 1. Context

### Why

MoonRay already builds a working Hydra render delegate (`hd_moonray`), verified rendering in
usdview on this machine. The goal is to load it **inside Maya 2027** so `Moonray` can be
selected in Maya's Hydra viewport and render interactively.

### Why it is not just "copy the dylib in"

| | Existing build | Maya 2027 |
| --- | --- | --- |
| USD | 22.11 | **25.11** (`PXR_VERSION 2511`) |
| USD C++ namespace | `pxrInternal_v0_22__pxrReserved__` | `pxrInternal_v0_25_11__pxrReserved__` |
| Python | 3.9.6 | **3.13.9** |
| Boost.Python | external `libboost_python39` | vendored `pxr_boost` (`libusd_python.dylib`) |
| TBB | 2020.3 (`libtbb.dylib`) | oneTBB 2022 (`libtbb.12.14.dylib`) |
| Architecture | arm64 | universal (x86_64 + arm64) |

The namespace token alone mismatches every USD symbol. The Hydra layer must be rebuilt
against Maya's own USD.

### Settled decisions

1. **Milestone = interactive Hydra viewport delegate.** Not batch.
2. **Delegate-only rebuild**, reusing the MoonRay core libraries already in
   `local-build/install`.
3. **Out-of-process Arras delegate** (`hd_moonray.dylib` / `HdMoonrayRendererPlugin`), not
   the in-process `hd_moonray_debug`.

---

## 2. Findings that changed the plan

These supersede the first draft. Each was verified by direct inspection.

**① No pxr shim needs to be hand-written.** `mayausd/USD/pxrConfig.cmake` is a *real,
complete* USD config: it does `include("${PXR_CMAKE_DIR}/cmake/pxrTargets.cmake")` and sets
`PXR_INCLUDE_DIRS "${PXR_CMAKE_DIR}/include"`. `devkit.tgz` ships `./cmake/pxrTargets.cmake`
and `./cmake/pxrTargets-relwithdebinfo.cmake`, whose `_IMPORT_PREFIX` is derived from the
parent of `cmake/`. So a **directory assembled in the workspace** from those pieces is a
fully working `pxr_DIR`. The ~2000-line `pxr-houdini/pxrTargets.cmake` fabrication is
**not needed**, and the `libusd_<lib>.dylib` name mapping comes free — it is already encoded
in the devkit's `pxrTargets-relwithdebinfo.cmake` for all 73 targets, under the bare target
names hdMoonray links.

**② Library validation is not a blocker.**
`codesign -d --entitlements - /Applications/Autodesk/maya2027/Maya.app` reports
`com.apple.security.cs.disable-library-validation = true` **and**
`com.apple.security.cs.allow-dyld-environment-variables = true`. Unsigned locally-built
dylibs load, `DYLD_*` is honoured, and child-process spawn is unrestricted. Maya is not
sandboxed. The first draft's risk #1 is **eliminated**.

**③ The Ndr → Sdr port already exists and needs no edits for 25.11.**
`dillonbbailey/moonray_sdr_plugins` branch `ubuntu24-local-usdview-xpu`, commit `0f90564`
*"Support USD 25.08 shader discovery and parsing through Sdr"* (13 files, +179/−58). It adds
`sdrCompat.h` with a `moonray_sdr::` alias namespace switching on `PXR_VERSION >= 2508`, plus
a CMake switch templatising the `plugInfo.json.in` bases. **2511 satisfies `>= 2508`**, so
this is a merge-and-recompile task, not a port. The first draft's risk #2 drops from
"a day to a week" to roughly an afternoon.

**④ hdMoonray needed only two commits for USD 25.08.**
`dillonbbailey/hdMoonray` branch `ubuntu24-local-usdview-xpu`:
- `eca8b47` — `if(PXR_VERSION VERSION_GREATER_EQUAL 2505)` → `if(TARGET hgiVulkan)` for the
  Vulkan find. Exactly right for Maya, which ships **no `hgiVulkan`**.
- `5352005` — two `VtValue::Get<TfToken>()` → `GetWithDefault<TfToken>()` in
  `lib/hydramoonray/Light.cc`, for absent lightLink/shadowLink. This is the same
  `empty VtValue` error we already saw in usdview.

**⑤ hdMoonray itself contains zero Ndr/Sdr usage.** The Ndr problem is entirely confined to
`moonray_sdr_plugins`. Combined with ③, materials stop being the critical path.

**⑥ Every pxr header hdMoonray includes exists in 25.11.** All 72 distinct `#include <pxr/...>`
lines were diffed against the devkit manifest. The sole miss is
`pxr/usd/usdGeom/procedural.h`, which is DWA-internal and already gated by
`if ("$ENV{STUDIO}" STREQUAL "GLD")`. Strong evidence that 25.11 breakage is small.

**⑦ The in-process footprint is much larger than the first draft claimed.** See §5. In
particular, without one extra line of code the "out-of-process" delegate still loads
Embree 4, OIIO 2.3 and OpenVDB 9.1 into Maya.

**⑧ New top risk: a compile-time TBB header collision.** See §7, Risk 1. This, not Ndr and
not code signing, is now the thing most likely to sink the effort.

---

## 3. Layout

### In the repo

```
openmoonray/building/macOSMaya/
├── PLAN.md                    this file
├── README.md
├── env.sh                     sources ../macOSLocal/env.sh, adds MAYA_* / PXR_* vars
├── unpack-usd-devkit.sh       extract devkit.tgz; assemble the pxr shim dir
├── tbb-probe/                 Risk 1 gate: layout diff, TBB 2020.3 vs oneTBB headers
├── build-delegate.sh          configure + build + install the delegate only
├── check-delegate-linkage.sh  M1 checks: otool/nm/codesign + mayapy load test
├── check-delegate.py          mayapy smoke test (what dyld actually loads)
├── make-maya-module.sh        generate the Maya module tree (absolute paths)
├── run-maya.sh                launch Maya with MAYA_MODULE_PATH only
├── check-maya-module.py       headless M2 check (maya.standalone + mayaHydra)
├── pxr-maya/pxrConfig.cmake.in   patched copy of Maya's config (3 guards only)
├── hydra-only/CMakeLists.txt     tiny superproject: hdMoonray + moonray_sdr_plugins
├── hydra-only/cmake/             FindLibatomic override, PinMayaUsd install step
└── module/{moonray.mod.in, mayaUsdPlugInfo.json, bundle-plugInfo.json}
```

Plus `macos-maya-environment` (hidden) and `macos-maya-release` presets in
`CMakeMacOSPresets.json`.

### In the workspace

```
local-build/
├── deps/  install/           UNTOUCHED, reused as-is
└── maya/
    ├── usd-devkit/           devkit.tgz extracted verbatim
    ├── pxr/                  the shim "pxr_DIR"
    │   ├── pxrConfig.cmake   generated from pxrConfig.cmake.in
    │   ├── cmake/            -> ../usd-devkit/cmake
    │   ├── include/pxr       -> ../../usd-devkit/include/pxr     (ONLY pxr exposed)
    │   └── lib/              -> <mayausd>/USD/lib
    ├── build/  install/
    ├── module/moonray/2027/
    └── logs/  validation/
```

**The curated `include/` directory is the crux.** It exposes **only `pxr/`**, preventing the
devkit's bundled `boost/` 1.88, oneTBB `tbb/`, `OpenEXR/`, `Imath/` and `OpenImageIO/` from
shadowing the deps' boost 1.78 / TBB 2020.3 / OIIO 2.3 that the prebuilt MoonRay core was
compiled against. Nothing is written outside the workspace; nothing in `/Applications` is
modified.

---

## 4. The pxr shim (three edits, not a rewrite)

`unpack-usd-devkit.sh` extracts `devkit.tgz` (~20,522 entries, 79 MB) and symlinks the shim
together. `pxr-maya/pxrConfig.cmake.in` is Maya's own `pxrConfig.cmake` with exactly three
changes:

1. **Guard the unconditional `add_library(TBB::tbb SHARED IMPORTED)`** in `if(NOT TARGET
   TBB::tbb) ... endif()`. Maya's version calls it unguarded, which is a hard `FATAL_ERROR`
   once MoonRay's `FindTBB.cmake` has already created the target. This guard is what lets
   `TBB::tbb` stay bound to deps TBB 2020.3.
2. **Make `find_dependency(MaterialX)` / `find_dependency(Imath)` conditional** (default OFF).
   hdMoonray uses neither, and skipping them avoids dragging their include dirs in.
3. Leave the `pxrTargets.cmake` include and `PXR_INCLUDE_DIRS` untouched — they now resolve
   through the curated shim.

Required at configure time: `pxr_DIR=$PXR_SHIM`, `PXR_USD_LOCATION=$MAYA_USD`,
`MAYA_LOCATION=/Applications/Autodesk/maya2027` (Maya's config derives `mayapy`,
`libpython3.13.dylib` and `include/python3.13` from it, then does
`find_dependency(Python3 "3.13.9" EXACT COMPONENTS Development)`), and `PYTHON_INCLUDE_DIR`.

**Set `CMAKE_BUILD_TYPE=RelWithDebInfo`** so the imported-config lookup matches the devkit's
`pxrTargets-relwithdebinfo.cmake` (or map `RELEASE → RelWithDebInfo`).

**No external boost_python.** `PXR_BOOST_PYTHON_NO_PY_SIGNATURES` already comes from
`pxrTargets.cmake`'s own `INTERFACE_COMPILE_DEFINITIONS`. hdMoonray's top-level
`find_package(Boost COMPONENTS python…)` still fires; satisfy it from deps with
`-DBOOST_PYTHON_COMPONENT_NAME=python39`. Nothing we build links it, because `Boost::python*`
appears only in `cmd/`, which we exclude.

### Building only the hydra subtree

Standalone configuration is viable: `hdMoonray/CMakeLists.txt` guards its own
`find_package(...)` block with `if("${PROJECT_NAME}" STREQUAL "${CMAKE_PROJECT_NAME}")`, and
all needed config packages exist in `local-build/install/lib/cmake/` (`ArrasCore-4.10.3.0`,
`SceneRdl2-16.2.0.0`, `Moonray-18.4.0.0`, …).

`hydra-only/CMakeLists.txt` is a small superproject that establishes `TBB::tbb` from deps
**before** `find_package(pxr)` runs, then adds both hydra subdirectories. Because
`PROJECT_NAME != CMAKE_PROJECT_NAME` there, hdMoonray skips its own finds and consumes ours.

`CMAKE_MODULE_PATH` must include `openmoonray/cmake_modules/cmake` — `SceneRdl2Config.cmake`
does `find_dependency` on JsonCpp, Log4cplus, CppUnit and TBB, which resolve only through
those `Find*.cmake` modules.

Exclude `cmd/`, `hats/`, `testSuite` via a new `HDMOONRAY_BUILD_CMD=OFF` option; build the
explicit target list instead.

**RPATH caveat:** hdMoonray's top-level does `set(CMAKE_INSTALL_RPATH ${GLOBAL_INSTALL_RPATH})`,
which **overwrites** any preset value. Without an absolute `$INSTALL_DIR/lib` entry the
delegate cannot find `libscene_rdl2.dylib`. Patch to `list(APPEND ...)` or set it after
`add_subdirectory`.

Also verify `-Wl,-ld_classic` (from `OMR_Platform.cmake`) is still accepted — it is removed
in newer linkers, and this machine is on clang 21 / Tahoe.

---

## 5. What actually loads inside Maya

Verified with `otool -L` on the real artefacts. **The first draft understated this.**

`libhydramoonray.dylib` pulls in `scene_rdl2`, `common_fb_util`, `fb_util_ispc`,
`render_util`, `common_math`, `math_ispc`, `common_platform`, `render_logging`,
`libtbb.dylib` (2020.3), `log4cplus`, `jsoncpp`.

`hd_moonray.dylib` adds the Arras client stack (`sdk`, `client_api`, `client_local`,
`client_receiver`, `core_messages`, `message_api`, `network`, `http`, …) **plus**
OpenImageDenoise, **OpenSSL 3**, **libcurl 4**, **libuuid**, and **OpenSubdiv 3.5.0 alongside
Maya's 3.7.0**.

**And worse:** `ArrasRenderer.cc:23` constructs `scene_rdl2::rdl2::SceneContext()` with proxy
mode **off**, so building the rdl2 scene client-side `dlopen`s the *full* shader DSOs from
`RDL2_DSO_PATH` **inside Maya** — `DwaBaseMaterial.so` alone drags in **Embree 4.4,
OIIO 2.3** (vs Maya's 2.5.16) and **OpenVDB 9.1**.

There is a designed fix: `install/rdl2dso/` ships `*.so.proxy` siblings that link only
scene_rdl2 + fb_util + math + log4cplus + jsoncpp, selected by
`SceneContext::setProxyModeEnabled(true)`. One line at `ArrasRenderer.cc:23` collapses the
in-process footprint to roughly what the out-of-process choice was supposed to deliver. See
milestone **M3b**.

What genuinely stays out of process in `execComp`: `rendering_rndr`, `rendering_pbr`,
`rendering_rt`, `rendering_geom`, Embree, the moonshine shader implementations, XPU/Metal.

---

## 6. Maya registration

Module tree at `local-build/maya/module/moonray/2027/`:

```
moonray/2027/
├── usd/
│   ├── mayaUsdPlugInfo.json
│   └── bundle/2511/
│       ├── plugInfo.json                 {"Includes": ["*/"]}
│       ├── hd_moonray.dylib   hdMoonrayAdapters.dylib
│       ├── moonrayShaderDiscovery.dylib  moonrayShaderParser.dylib
│       └── <name>/plugInfo.json          (one per plugin)
└── renderDesc/HdMoonrayRendererPluginRenderer.xml    optional, batch only
```

`usd/mayaUsdPlugInfo.json`, byte-for-byte Arnold's shape:

```json
{ "MayaUsdIncludes": [ { "PlugPath": "bundle/2511",
    "VersionCheck": { "Python": "3", "USD": "0.25.11" } } ] }
```

`bundle/2511/plugInfo.json` is `{"Includes": ["*/"]}` — **note `*/`, not Arnold's
`*/resources/`**. This matches what hdMoonray already installs, and means the **stock
generated plugInfo files work unchanged**: `hd_moonray/plugInfo.json` carries
`"LibraryPath": "../../hd_moonray.dylib"`, which from `bundle/2511/hd_moonray/` resolves
correctly. Do *not* copy Arnold's `resources/` + `Root: ".."` indirection — it would break
the relative path.

**Prefer symlinks** when populating `bundle/2511`, so `@loader_path/../lib` still resolves to
`install/lib`; or add the absolute `$INSTALL_DIR/lib` RPATH from §4, after which either works.

`moonray.mod` (paths **must be absolute** — `libmayaUsd.dylib` literally carries the string
`Relative paths are unsupported for MAYA_PXR_PLUGINPATH_NAME`):

```
+ moonray 1.0 @MODULE_ROOT@/moonray/2027
MAYA_PXR_PLUGINPATH_NAME += @MODULE_ROOT@/moonray/2027/usd
MAYA_RENDER_DESC_PATH    += @MODULE_ROOT@/moonray/2027/renderDesc
MOONRAY_CLASS_PATH  = @INSTALL_DIR@/shader_json
RDL2_DSO_PATH       = @INSTALL_DIR@/rdl2dso
ARRAS_SESSION_PATH  = @INSTALL_DIR@/sessions
REZ_MOONRAY_ROOT    = @INSTALL_DIR@
```

`run-maya.sh` sets `MAYA_MODULE_PATH` to the workspace module dir, prepends
`$INSTALL_DIR/bin` to `PATH` (Arras finds `execComp` on the child's inherited PATH), and sets
`DYLD_FALLBACK_LIBRARY_PATH`. **This keeps everything inside the workspace — nothing is
written to `/Users/Shared/Autodesk/modules/maya/2027/`.** Dropping a `.mod` there remains a
convenience fallback for double-click launching, but then `DYLD_*` cannot be set and the
absolute RPATH entries become mandatory.

Prefer `MAYA_PXR_PLUGINPATH_NAME` over `PXR_PLUGINPATH_NAME`: it is the sanctioned path, it
is settable from `.mod`, and its `VersionCheck` means a future Maya with a different USD
skips our bundle rather than loading an ABI-incompatible dylib.

### Arras runtime specifics

- `ARRAS_SESSION_PATH` → `install/sessions` (`hd_single.sessiondef` etc., verified present).
- `LocalSession.cc` sets `program = "execComp"` and, in `current-environment` packaging,
  the child **inherits Maya's entire environment**. Reset `PYTHONHOME`/`PYTHONPATH` via an
  `environment` block in a workspace-local sessiondef so `execComp` does not inherit Maya's
  Python 3.13.
- Arras hardcodes `/tmp/exec-<name>-<uuid>` + a `.ipc` Unix socket — **`/tmp` writes are
  unavoidable**; they are ephemeral per session. No TCP ports in local mode.
- Set `workingDirectory` explicitly in the sessiondef; an `.app` launch otherwise gives the
  child cwd `/`.

---

## 7. Risk register

Reordered by the verified findings. Two risks from the first draft are gone; one new risk
tops the list.

**Risk 1 — TBB header collision in a single translation unit. HIGH / HIGH. ★ new top risk.**
Most of `lib/hydramoonray/` includes both a pxr imaging header and
`scene_rdl2/scene/rdl2/SceneContext.h`. Both pull `<tbb/...>`, and `tbb/` can resolve to only
one prefix. `pxr/imaging/hd/changeTracker.h`, `hd/renderIndex.h`,
`usdImaging/collectionCache.h`, `tf/diagnosticMgr.h` and `work/loops.h` all include TBB
headers; `SceneContext` holds `tbb::concurrent_hash_map` **members**.
*Mitigation:* choose **TBB 2020.3 for the whole delegate build** — the curated include dir
exposes only `pxr/`, and `include_directories(BEFORE SYSTEM $DEPS_ROOT/include)` supplies
`tbb/`. TBB 2020.3 provides every header name USD asks for (all verified present in
`deps/include/tbb`). scene_rdl2 — which hdMoonray manipulates intimately via templates and
inline accessors — then gets its native headers. hdMoonray never inherits from or embeds a
USD type holding a tbb member.
*Gate before any other code (M0):* compile a probe printing `sizeof(pxr::HdChangeTracker)`,
`sizeof(pxr::HdRenderIndex)`, `sizeof(pxr::TfDiagnosticMgr)` under both header sets.
Identical ⇒ risk collapses. Divergent ⇒ escalate.
*Escalation:* rebuild only the in-process MoonRay libs against oneTBB (headers available in
`usd-devkit/include/tbb`). The set is bounded, and scene_rdl2 looks oneTBB-clean —
`task_scheduler_init` appears only in a test file, and there is no `tbb::atomic` or
`tbb/task.h` usage.
*Outcome (M0, 2026-09-30): sizes diverge, but the risk is contained — staying on TBB 2020.3.*
`tbb-probe/tbb-probe.sh` found `HdChangeTracker` (3264 vs 3224), `HdRenderIndex`,
`TfDiagnosticMgr`, `UsdImagingDelegate` and `UsdStage` differ. That only matters where the
delegate's own compiled code reads their members, i.e. through **inline** accessors. Audit of
`hdMoonray/lib`:
- `HdRenderIndex::GetChangeTracker()` (inline, 6 call sites): `_tracker` is at offset **440
  under both** — the whole size delta is inside the tracker. Every tracker method called on
  it (`MarkRprimDirty`, `MarkSprimDirty`, `MarkAllRprimsDirty`, `GetInstancerDirtyBits`,
  `MarkInstancerClean`) is out of line. **Safe.**
- Every other `HdRenderIndex` call is out of line. All 16 base classes hdMoonray derives from
  are identical. **Safe.**
- `UsdImagingDelegate::GetTime()` (inline, `RenderPass.cc`): `_time` is at **1120 vs 1152**.
  **Real bug.** Patched to the out-of-line `GetTimeWithOffset(0.0f)` on hdMoonray branch
  `maya2027-hydra`.

The probe now gates on exactly those layouts ("must" lines) and passes. **Any new inline
accessor on a TBB-holding USD type must be added to the probe.** `moonray_sdr_plugins` was
not audited; it uses Sdr, not the divergent Hd/UsdImaging types.

**Risk 2 — Maya running under Rosetta. RESOLVED ✅**
Verified on a live Maya process (pid 1590): `lsappinfo` reports `LSArchitecture = arm64` and
`vmmap` reports `Code Type: ARM64`. Maya 2027 runs native arm64 on this machine, so the
arm64-only MoonRay libraries in `local-build` are compatible. No universal rebuild is needed.
(`libRosetta.dylib` appears mapped even in native processes; `Code Type` is authoritative.)

**Risk 3 — Full rdl2 shader DSOs loaded into Maya. RESOLVED ✅ (M3b).**
Embree 4.4, OIIO 2.3, OpenVDB 9.1 in Maya's address space. *Mitigation:*
`setProxyModeEnabled(true)` (M3b). *Unverified:* that `rdl2::BinaryWriter` serialises a
proxy-mode scene faithfully — test as a discrete experiment.

**Risk 4 — Python 3.9 linked into the delegate. MEDIUM / HIGH.**
The current 22.11 build links `Python3.framework/3.9` and `libboost_python39`. Two CPython
runtimes in one process is fatal if both initialise. *Gate:* `otool -L` must show zero
`Python3.framework` and zero `libboost_python` entries before the dylib is ever loaded into
Maya. Should follow automatically from excluding `cmd/`, but verify explicitly.

**Risk 5 — mayaHydra scene-index emulation gaps. MEDIUM / MEDIUM.**
hdMoonray targets the legacy `HdSceneDelegate`; mayaHydra 0.8.2 runs scene indices with
emulation. Arnold and Prman work through the same path, so the mechanism is proven.
*Mitigation:* stage geometry first, lights/instancers/volumes later. Commit `5352005` is
already exactly this class of bug.

**Risk 6 — duplicate OpenSubdiv / OIIO / OpenSSL / libcurl. MEDIUM / LOW-MEDIUM.**
Distinct install names + two-level namespace should isolate them. Risk 3's fix removes the
worst. Re-check with `otool -L` after the Maya build.

**Risk 7 — deployment target / `-ld_classic`. MEDIUM / LOW.**
MoonRay libs are `minos 26.7, sdk 27.0`; Maya's USD is `minos 14.0, sdk 13.3`. Mismatch
produces warnings, not errors. Be ready to strip `-ld_classic` for this preset.

**Risk 8 — Arras child-process environment pollution. MEDIUM / LOW.** See §6.

**Risk 9 — `TBB::tbb` double definition. HIGH / TRIVIAL.** Solved by the shim guard (§4).

**Risk 10 — Ndr removal. Demoted from "THE BIG PROBLEM" to merge-and-recompile** (finding ③).
Does not block M1–M3.

**Risk 11 — hardened runtime / library validation. ELIMINATED** (finding ②).

---

## 8. Milestones

**M0 — Bring-up, no code.** Confirm Maya launches arm64. Extract devkit; assemble the pxr
shim. Run the TBB `sizeof` probe (Risk 1). Compile one throwaway TU that includes
`pxr/imaging/hd/renderDelegate.h` **and** `scene_rdl2/scene/rdl2/SceneContext.h` together.
*Exit: that TU compiles and links.*
**✅ Done 2026-09-30.** `unpack-usd-devkit.sh` + `tbb-probe/tbb-probe.sh`. The probe TU
compiles, links and runs a `TfToken` (oneTBB runtime) alongside a `SceneContext` (TBB 2020.3
runtime) in one process. See Risk 1 outcome. Two things found for M1:
- scene_rdl2's install omits `scene_rdl2/common/arm/` (`emulation.h`, `sse2neon.h`,
  `avx2neon.h`), which `common/math/Math.h` includes on arm64. It belongs to no component, so
  no `PUBLIC_HEADER` rule installs it. Fix the install; the probe borrows it from source.
- Maya's `libpython3.13.dylib` has install name
  `@executable_path/../Frameworks/Python.framework/Versions/3.13/Python`: fine inside Maya,
  unresolvable anywhere else. Standalone tools need `DYLD_FRAMEWORK_PATH`.

**M1 — Delegate builds against 25.11.** hdMoonray: branch `maya2027-hydra` (fork's two
commits + the `GetTime` fix; no merge needed, the fork branch is upstream `986121d` + 2).
Merge the `moonray_sdr_plugins` fork branch; superproject + preset in place. *Exit:
`hd_moonray.dylib` exists; `otool -L` shows Maya's `libusd_*` only, no `libusd_ndr`, no
`Python3.framework/3.9`, no `libboost_python39`, arm64; `tbb-probe.sh` passes.*
**✅ Done 2026-09-30.** `bash building/macOSMaya/build-delegate.sh` → `local-build/maya/install`,
then `check-delegate-linkage.sh` → PASS, including a `mayapy` load test
(`check-delegate.py`) that inspects the images dyld actually loaded. What it took, beyond §4:
- **No presets, no `maya-tbb-first.cmake`.** `build-delegate.sh` configures `hydra-only/`
  directly; the superproject finds TBB before pxr itself, which is all the hook was for.
- **Libatomic.** The installed `ArrasCoreConfig.cmake` requires it on every Unix; macOS has
  none and nothing links it. `hydra-only/cmake/FindLibatomic.cmake` reports it found on Apple.
- **`PXR_USD_LOCATION` = the shim**, not Maya's USD dir: `pxrTargets.cmake` uses it for both
  `lib/` and `include/`, and Maya's USD dir has no `include/`.
- **Deps' USD 22.11 headers shadowed 25.11 — silently.** Core MoonRay targets carry
  `$DEPS_ROOT/include` (which has 22.11 `pxr/`) ahead of the shim. `libhydramoonray` built
  with 219 undefined `pxrInternal_v0_22` symbols and still linked, because
  `Python::Module` adds `-undefined dynamic_lookup`. Fix: `include_directories(BEFORE SYSTEM
  ${PXR_INCLUDE_DIRS})`; the check now fails on any `v0_22` symbol.
- **One real 25.11 API change** surfaced once the headers were right: pure virtual
  `HdRendererPlugin::IsSupported(HdRendererCreateArgs const&, std::string*)`. Added, gated
  on `__has_include(<pxr/imaging/hd/rendererCreateArgs.h>)`.
- **Library name clashes, so USD is pinned by absolute path.** Deps has USD 22.11 as
  `@rpath/libusd_*.dylib`; Maya's USD dir has boost 1.88 under the same names as deps' boost
  1.78. No RPATH order works for both. `hydra-only/cmake/PinMayaUsd.cmake` (install step)
  rewrites every Maya-only dependency to its absolute Maya path, drops Maya/shim RPATHs, and
  ad-hoc re-signs. Inside Maya that is the file Maya already loaded.
- **RPATH order.** The core install also has a 22.11 `libhydramoonray.dylib`, so
  `@loader_path` must come first. hdMoonray now prepends its entries and lets the parent turn
  off link-path RPATHs; the superproject sets `CMAKE_BUILD_WITH_INSTALL_RPATH` because
  CMake's macOS install-time RPATH rewrite keeps shared entries in build order.
- **hdMoonray options** (fork, `maya2027-hydra`): `HDMOONRAY_BUILD_CMD`,
  `HDMOONRAY_BUILD_DEBUG_PLUGIN`, `HDMOONRAY_BUILD_HOUDINI`, all default ON upstream.
- **sdr plugins** (fork, `maya2027-hydra` = `0f90564` + 1): stop building `moduleDeps.cpp`,
  which registered a nonexistent `pxr.MoonrayShader*` Python module that USD 25.x tried to
  import on every load.
- `-Wl,-ld_classic` is still accepted (Risk 7): no change needed.

Bonus check toward M4: in `mayapy` with `MOONRAY_CLASS_PATH` set, Sdr finds 1002 nodes and
`DwaBaseMaterial` (106 inputs). One Sdr warning: `DwaBaseMaterial.iridescence_colors`
declares `GfVec3f` but defaults to an array.

**M2 — 🎯 FIRST PROOF OF LIFE: "Moonray" in Maya's Hydra renderer menu.**
Module tree + `.mod` + `run-maya.sh`. *Exit: a `mayaHydraRenderOverride_HdMoonrayRendererPlugin`
entry labelled **Moonray** appears.* This proves plugin discovery, `VersionCheck` gating,
`dlopen` of an unsigned arm64 dylib into Maya, and registry registration — **without
requiring a single MoonRay pixel.** Everything before is prerequisite; everything after is
refinement.

**✅ Done 2026-10-05.** The viewport's Renderer menu shows **"(Technology Preview) Hydra
Moonray"** (mayaHydra's label for every Hydra renderer). Steps:
`make-maya-module.sh` → `local-build/maya/module/`; `run-maya.sh` launches Maya with only
`MAYA_MODULE_PATH` set (it does not source `env.sh`: build-only variables like
`PYTHONNOUSERSITE`/`TMPDIR` would change Maya's behaviour). Notes:
- `bundle/2511/<name>/plugInfo.json` are real files with **absolute** `LibraryPath`s: USD
  resolves a relative `LibraryPath` lexically, so symlinked plugInfo files would break.
- Headless check: `MAYA_MODULE_PATH=… mayapy check-maya-module.py` — `maya.standalone`,
  loads mayaUsd + mayaHydra, asserts `mayaHydra -listRenderers` has `HdMoonrayRendererPlugin`
  with display name `Moonray`. PASS.
- Live GUI: `TF_DEBUG=PLUG_LOAD` shows `hd_moonray` and both sdr plugins load; `vmmap`
  shows our `hd_moonray`/`libhydramoonray`/core `libscene_rdl2`, and no USD 22.11, Python 3.9,
  Embree, OIIO 2.3 or OpenVDB in Maya's process.
- `Error:  (mayaHydra)` and `displayRGBColor is unavailable in batch mode` appear in
  `maya.standalone` with or without our module — not ours.

**M3 — First pixels.** Delegate instantiates, `ArrasRenderer` spawns a local `execComp`, a
polygon cube appears. Materials wrong/absent — expected.

**M3b — Proxy DSOs.** Flip `setProxyModeEnabled(true)`; confirm M3 still renders and that
Embree/OIIO/OpenVDB have left Maya's process.

**✅ M3b done 2026-10-06.** `ArrasRenderer` enables `SceneContext::setProxyModeEnabled(true)`.
Measured in Maya while rendering (`images.txt` from `m3-render-test.py`, dyld's own image list):
workspace libraries mapped into Maya 78 → 57; gone: Embree 4.4, OIIO 2.3.20, OpenVDB 9.1,
OpenSubdiv 3.5 and the four full shader DSOs (now `*.so.proxy`). Left from deps: OIDN, curl,
OpenSSL — the Arras client stack `hd_moonray` links directly (§5).
Open item 2 (BinaryWriter fidelity) — **checked, and it found a bug**: in proxy mode
`rdl2::Camera::setFocalLength()` is the base class's no-op, so "focal" was silently dropped
and the render used the 30mm default instead of Maya's 50mm (FOV mismatch with the
viewport). Fixed in hdMoonray `Camera.cc` (set "focal" by name, PerspectiveCamera only).
After the fix the serialized scene is identical to non-proxy mode (16/16 objects, modulo
mayaHydra's per-session render-item numbers).

**M4 — Materials.** Sdr plugins registering; `MOONRAY_CLASS_PATH` → the **188 existing JSON
files** in `install/shader_json` (no regeneration needed). *Exit: a `DwaBaseMaterial` renders
with correct albedo — not the fully-transparent image that signals an empty Sdr registry.*

**✅ M4 done 2026-10-06.** No Sdr changes were needed to *render* materials:
- `testdata/sphere.usd` (`DwaBaseMaterial`, albedo ← `CheckerboardMap` green/blue) through
  Maya's `usdrecord`: 39% green / 39% blue pixels, no `Invalid info:id`. Through a mayaUsd
  stage in Maya: the RDL dump has the `DwaBaseMaterial` + bound `CheckerboardMap` assigned to
  the sphere; rendering that dump offline shows the checker.
- Maya's own materials: an `openPBRSurface` with base colour red arrives as
  `UsdPreviewSurface` `diffuseColor = (1,0,0)`, `roughness = 0.3`; offline render of the dump
  is a glossy red cube (`M3_CUBE_SHADER="openPBRSurface:1,0,0" m3-render-test.sh`).
- Sdr parser (`moonray_sdr_plugins`): `RgbVector` now maps to Sdr `color` + dynamic array
  (`color3f[]`), removing the default-type warnings on production materials
  (`iridescence_colors` etc.). **Remaining:** 78 such warnings when all 1002 nodes are parsed —
  `Vec3d` scalars (`Project*Map` translate/rotate/scale: float3 vs GfVec3d) and float-tuple
  vector arrays (geometry classes, `UserData`, `TestInputs*`). They affect only how inputs are
  described to authoring tools, not rendering. Candidate fix: `sdrUsdDefinitionType` metadata.

**M5 — Breadth and stability.** Adapters (mesh lights, light filters), lights, instancers,
volumes, render settings, camera navigation, repeated renderer switching, clean shutdown with
no orphaned `execComp`.

**M6 (optional) — Batch.** `renderDesc/HdMoonrayRendererPluginRenderer.xml` via
`MAYA_RENDER_DESC_PATH`.

---

## 9. Verification

| Stage | Check |
| --- | --- |
| M0 | `lipo -info` on Maya's binary; `ps -o arch` on running Maya. `sizeof` probe diff. Probe TU compiles. |
| M1 | `otool -L hd_moonray.dylib \| grep -E 'ndr\|Python3.framework\|boost_python'` → empty. `lipo -info` → arm64. `LC_RPATH` includes `$INSTALL_DIR/lib`. |
| M2 | `mayapy -c "from pxr import Plug; print([p.name for p in Plug.Registry().GetAllPlugins()])" \| grep hd_moonray`; read Maya's Renderer menu. `TF_DEBUG=PLUG_INFO_SEARCH,PLUG_LOAD` and `DYLD_PRINT_LIBRARIES=1` to see the scan and which TBB/OpenSubdiv copies load. |
| M3 | `ps -ef \| grep execComp`; `ls /tmp/exec-*` shows config + `.ipc` socket. |
| M3b | `vmmap <maya pid> \| grep -E 'embree\|OpenImageIO\|openvdb'` → empty. |
| M4 | `Sdr.Registry().GetNodeByName('DwaBaseMaterial')` non-null; compare a viewport capture against the known-good `hd_render` output in `local-build/validation`. |
| M5 | 20× renderer switch with no leak/crash; Maya exits with no orphaned `execComp`. |

Debug switches: `HDMOONRAY_DEBUG`, `HDMOONRAY_INFO`, `HDMOONRAY_LOGLEVEL`,
`HDMOONRAY_RDLA_OUTPUT` (dumps the converted scene — fastest way to separate a delegate
problem from a shading problem), and Maya's `MAYAHYDRALIB_RENDEROVERRIDE_*` `TF_DEBUG` codes.

---

## 10. Open items

1. ~~Maya under Rosetta~~ — **resolved**: verified native arm64 (see Risk 2).
2. ~~`rdl2::BinaryWriter` fidelity in proxy mode~~ — checked in M3b; found and fixed the
   `setFocalLength` no-op. Other scenes may use other virtual-setter paths: compare
   `HDMOONRAY_RDLA_OUTPUT` dumps with and without proxy mode when adding prim types.
3. ~~Sdr API drift 25.08 → 25.11~~ — none hit; the 25.08 port built and works unchanged.
4. **mayaHydra emulation vs hdMoonray's legacy scene-delegate assumptions** — unprovable
   before M3.

No pre-code unknowns remain: the TBB probe (Risk 1) ran in M0 and passes after one patch.

---

## Prior art

- `building/macOS/pxr-houdini/` — the DCC shim precedent (superseded in approach by ①, but
  the preset/env-var structure still applies)
- `scripts/macOS/setupHoudini.sh` — DCC runtime env precedent
- `building/macOSLocal/` — the workspace-local recipe this sits beside
- `dillonbbailey/moonray_sdr_plugins@0f90564`, `dillonbbailey/hdMoonray@eca8b47,5352005`
- `building/Ubuntu24Local/usd25-include-order.cmake` — the `CMAKE_PROJECT_<name>_INCLUDE`
  trick; not needed in the end (the superproject orders TBB and includes itself)
- TSC notes `tsc/meetings/2026/2026-{07-30,08-20}.md` — upstream Hydra 2.0 / Maya work
