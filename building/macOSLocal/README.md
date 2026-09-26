# macOS workspace-local build with usdview

Builds MoonRay and its dependencies entirely inside a single workspace
directory, and builds OpenUSD 22.11 with `usdview` and the USD command line
tools enabled.

This recipe writes nothing outside the workspace. It runs no installer and no
package manager, modifies no shell profile, and does not use the
`/Applications/MoonRay` layout that [the upstream macOS
recipe](../macOS/macOS_build.md) assumes.

## Requirements

Everything here is read from the system, never written to:

- Apple silicon Mac, macOS 14.6 or newer
- Xcode, including the Command Line Tools
- The Metal Toolchain, *only if you want XPU mode* — see below
- `/Library/Developer/CommandLineTools/.../Python3.framework/Versions/3.9` —
  the Python 3.9 that Boost.Python, OpenImageIO and USD are all compiled
  against. Present with a standard Command Line Tools install
- CMake 3.26.5 or newer, Git, and Git LFS
- Roughly 25 GB free disk space

Homebrew may be installed; it is deliberately excluded from package discovery
(`CMAKE_IGNORE_PATH`) so a stray `/opt/homebrew` library can never be linked in.
A Homebrew `cmake` binary is fine to build with.

## Build

Clone with `--recurse-submodules`, then run from the root of the checkout:

```bash
bash building/macOSLocal/bootstrap.sh
bash building/macOSLocal/build-deps.sh
bash building/macOSLocal/build-moonray.sh
```

Everything generated goes to `../local-build`, next to the checkout. To put it
elsewhere, export an absolute path before every command above:

```bash
export MOONRAY_LOCAL_ROOT="$PWD/local-build"
```

| Directory | Contents |
| --- | --- |
| `deps` | Every third-party dependency, including USD |
| `venv` | Python environment holding PySide6 and PyOpenGL |
| `install` | MoonRay binaries, shaders, Hydra plugins and Python modules |
| `build/deps` | Dependency source and build trees |
| `build/moonray` | MoonRay build tree |
| `cache`, `config`, `share`, `tmp` | Workspace-local caches and temp files |

`BUILD_JOBS` defaults to the CPU count. The dependency build is the long pole;
Qt 5.12, Boost and USD dominate it.

## XPU mode and the Metal Toolchain

MoonRay's XPU mode compiles `MetalGPUPrograms.metal`, which requires Xcode's
Metal Toolchain. On Tahoe that toolchain is a separately downloaded component,
and it installs into Xcode — it is the one piece this recipe cannot keep inside
the workspace. `bootstrap.sh` warns if it is missing.

Either install it:

```bash
xcodebuild -downloadComponent MetalToolchain
```

or build without XPU, which needs nothing outside the workspace:

```bash
bash building/macOSLocal/build-moonray.sh -DMOONRAY_USE_METAL=NO
```

`MOONRAY_USE_METAL` defaults to YES on Apple platforms
(`cmake_modules/cmake/OMR_Platform.cmake`). Setting it to NO drops XPU mode and
OIDN Metal denoising; scalar and vector CPU rendering, both GUIs, the Hydra
delegate and usdview are unaffected.

## Run

```bash
source building/macOSLocal/setup.sh
usdview testdata/sphere.usd
usdview --renderer Moonray testdata/sphere.usd
moonray_gui -exec_mode xpu -info -in testdata/curves.rdla
```

## How usdview is enabled

The upstream macOS recipe builds USD with `PXR_BUILD_USDVIEW=OFF`. Turning it on
requires PySide and PyOpenGL at configure time, which is the reason this recipe
exists: USD's `FindPySide` runs `import PySide6` under `PYTHON_EXECUTABLE`, so
those modules must be importable by the interpreter USD is built with — and
installing them into the system Python is exactly what this recipe must avoid.

`bootstrap.sh` resolves that with a venv created from the Command Line Tools
Python 3.9. The venv shares that interpreter's ABI, so USD still compiles and
links against the same `libpython3.9.dylib` as Boost.Python and OpenImageIO,
while `PySide6` and `PyOpenGL` resolve out of the workspace.

Versions are pinned to what USD 22.11 was released against (see its
`VERSIONS.md`):

- **PySide6 6.3.1** — the version USD 22.11 lists as tested on macOS. It ships a
  `cp36-abi3` universal2 wheel, so it loads on Python 3.9 on Apple silicon.
- **PyOpenGL 3.1.7** rather than the tested 3.1.5. 3.1.5 cannot locate the
  OpenGL framework through the dyld shared cache on macOS 11 and newer.

USD then bakes the venv interpreter into the installed `usdview` shebang
(`PXR_PYTHON_SHEBANG` defaults to `PYTHON_EXECUTABLE`), so `usdview` finds
PySide6 with no `PYTHONPATH` set up by the caller.

`PXR_ENABLE_METAL_SUPPORT` defaults on for Apple platforms in USD 22.11, so
Storm renders through Metal; OpenGL support stays enabled alongside it.

## Differences from `building/macOS`

`third-party/CMakeLists.txt` is a copy of `../macOS/CMakeLists.txt`. Diff the
two to see the whole change; it is small, and it reuses that directory's
patches and toolset config rather than duplicating them.

- `InstallRoot`, the Python interpreter, the Python framework and the PySide bin
  directory are cache variables supplied by `build-deps.sh`, replacing paths
  that were hardcoded or derived from the `/Applications/MoonRay` symlink layout.
- Boost's `user-config.jam` is generated so its interpreter matches the rest of
  the build, instead of being read verbatim with a hardcoded path.
- `BOOST_ARCH` is given a default of `arm64`; upstream references it without
  ever setting it.
- USD sets `PXR_BUILD_USDVIEW=ON`, `PXR_BUILD_USD_TOOLS=ON`,
  `PXR_BUILD_IMAGING=ON`, `PXR_BUILD_USD_IMAGING=ON` and `PYSIDE_BIN_DIR`.
- OpenImageIO builds against the workspace interpreter rather than
  `/usr/bin/python3`.

The MoonRay build itself uses a new `macos-local-release` preset in
`CMakeMacOSPresets.json`, which reads `DEPS_ROOT`, `BUILD_DIR`, `INSTALL_DIR`
and `MOONRAY_PYTHON` from the environment `env.sh` sets. Configuring that preset
without sourcing `env.sh` first will fail with empty paths; `build-moonray.sh`
sources it for you.

Submodule URLs in `.gitmodules` are absolute `OpenMoonRay` URLs on this branch.
The relative URLs used upstream only resolve for a fork that also forks all
twenty sibling repositories.

## Validation

Validated on macOS 26.7 Tahoe, Xcode clang 21, Apple M5 Pro, CMake 4.3.4:

- All 27 dependencies built, including Qt 5.12 and USD 22.11 with usdview.
- Complete MoonRay build with XPU/Metal enabled and zero build failures.
- CPU vector and XPU renders of `rectangle.rdla`, both 512 x 512, finite and
  nonblank. Mean absolute CPU/XPU pixel difference was `9.7e-9`.
- The XPU run loaded `install/shaders/default.metallib`, reported
  `Using GPU: Apple M5 Pro` and 100% GPU occlusion ray utilization, so it was
  not a CPU fallback.
- `hd_render` produced a 1920 x 1080 sphere image through the Hydra delegate.
- The real usdview window rendered with both Storm/Metal and Moonray;
  screenshots are in `$MOONRAY_LOCAL_ROOT/validation`.
- The Sdr registry resolved 201 shader nodes including `DwaBaseMaterial`.

To repeat:

```bash
source building/macOSLocal/setup.sh
moonray -in testdata/rectangle.rdla -out "$MOONRAY_LOCAL_ROOT/validation/cpu.exr" \
  -exec_mode vector -threads 8 -info
moonray -in testdata/rectangle.rdla -out "$MOONRAY_LOCAL_ROOT/validation/xpu.exr" \
  -exec_mode xpu -threads 8 -info
hd_render -in testdata/sphere.usd -out "$MOONRAY_LOCAL_ROOT/validation/hydra.exr"
"$VENV_ROOT/bin/python3" building/macOSLocal/check-usdview.py --defaultsettings \
  --renderer Moonray testdata/sphere.usd
```

`check-usdview.py` opens the real UI, writes screenshots and a renderer report
to `$MOONRAY_LOCAL_ROOT/validation`, and closes after twelve seconds. These are
smoke checks; they do not replace the upstream unit and regression suites.

## Known warnings

Observed during successful renders, all nonblocking:

- The shader plugins try to import `pxr.MoonrayShaderParser` and
  `pxr.MoonrayShaderDiscovery`. Those packages ship an `__init__.py` whose only
  stated purpose is to silence this warning, but
  `moonray_sdr_plugins/*/CMakeLists.txt` never installs them.
- `DwaBaseMaterial.iridescence_colors` emits a shader metadata type warning
  (`GfVec3f` from `SdfType` versus `VtArray<GfVec3f>` from the default value).
- usdview with the Moonray delegate logs `Attempted to get value of type
  'TfToken' from empty VtValue` while still rendering correctly. The
  `ubuntu24-local-usdview-xpu` branch pins an `hdMoonray` fix for absent Hydra
  light/shadow-link values; it is not applied here.
- `hd_render` exits with `SocketPeer::receive: Bad file descriptor` after
  writing its output. This is Arras shutdown noise, not a render failure.
