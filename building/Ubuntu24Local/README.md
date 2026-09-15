# Ubuntu 24.04 workspace-local build with usdview and XPU

Build MoonRay and OpenUSD 23.08 with `usdview`, both MoonRay GUIs, and optional
CUDA/OptiX XPU support. This recipe uses local conda-forge binary dependencies
plus the rendering libraries built from source. It runs no system package
installation commands and makes no shell-profile changes.

The host must already provide Bash, Git with Git LFS, curl, tar, bzip2, and
standard Linux utilities. GUI use requires a desktop display server. GPU
rendering uses the existing NVIDIA driver; this recipe does not install one.
The compiler and its build sysroot are installed locally. Executables still
use the host's standard Linux runtime and loader.

## Clone, bootstrap, and build

Clone this branch with `--recurse-submodules`. It pins the required source fixes
in the `scene_rdl2`, `arras_render`, and `hdMoonray` submodules.

Run from the root of the checkout:

```bash
bash building/Ubuntu24Local/bootstrap.sh
bash building/Ubuntu24Local/build-deps.sh
bash building/Ubuntu24Local/build-moonray.sh
```

By default, everything generated is stored in `../local-build`, next to the
checkout. To choose a different workspace-local location, set an absolute path
before all build and setup commands:

```bash
export MOONRAY_LOCAL_ROOT="$PWD/local-build"
```

The output directory contains:

| Directory | Contents |
| --- | --- |
| `deps` | Local compiler, Python, CUDA toolkit, and dependency libraries |
| `install` | MoonRay binaries, shaders, Hydra plugins, and Python modules |
| `build/dependencies` | Dependency source and build trees |
| `build/moonray` | MoonRay build tree |
| `cache`, `config`, `share`, `tmp` | Workspace-local caches and runtime files |
| `tools`, `downloads`, `sources` | Bootstrap tools and downloaded sources |

`BUILD_JOBS` defaults to 16. `MOONRAY_USE_OPTIX=OFF` disables XPU in the MoonRay
build. The locked environment still contains CUDA, allowing either build mode
without changing the dependency environment. The full tested workspace used
approximately 11 GB, including build trees and caches.

`dependencies-explicit.txt` pins binary package URLs and checksums. The bootstrap
script creates the environment only when it is absent. To reproduce a clean
environment, select a fresh `MOONRAY_LOCAL_ROOT`; it does not overwrite an
existing environment. Source dependency versions are in
[`third-party/CMakeLists.txt`](third-party/CMakeLists.txt).

## Run

```bash
source building/Ubuntu24Local/setup.sh
usdview testdata/sphere.usd
usdview --renderer Moonray testdata/sphere.usd
moonray_gui -in testdata/rectangle.rdla
moonray_gui_v2 -in testdata/rectangle.rdla
```

The build script generates the shader JSON descriptions required by the Hydra
plugin and installs `render_profile_viewer`. Use this recipe's setup script:
the upstream setup script assumes a directory named `installs` and includes
system Python paths. usdview settings persistence is disabled to avoid writing
to `~/.usdview`. CUDA, OptiX, OpenGL, Qt, and Python cache/config paths are set
locally. Tools may still write explicitly requested render output paths.

## Validation

The original build passed these smoke checks on Ubuntu 24.04 with an NVIDIA RTX
PRO 6000 Blackwell Workstation Edition and driver 595.84:

- CPU/vector and GPU/XPU rectangle renders, each 512 × 512, with finite,
  nonblank pixels. Mean absolute CPU/XPU pixel difference was `4.7e-7`.
- OptiX reported successful GPU setup and over 99% GPU bundled intersection
  ray utilization; the XPU result was not a CPU fallback.
- `hd_render` produced a 1920 × 1080 sphere image.
- usdview rendered with both GL and Moonray; screenshots were captured.
- Both MoonRay GUI applications opened and rendered the rectangle.
- Local `scene_rdl2` and profile-viewer Python imports succeeded.
- Linkage checks found no missing libraries; external linked libraries were
  the standard Linux runtime and loader.

To repeat the rendering checks:

```bash
source building/Ubuntu24Local/setup.sh
moonray -in testdata/rectangle.rdla -out "$MOONRAY_LOCAL_ROOT/validation/cpu.exr" \
  -exec_mode vector -threads 8 -auto_affinity off -info
moonray -in testdata/rectangle.rdla -out "$MOONRAY_LOCAL_ROOT/validation/xpu.exr" \
  -exec_mode xpu -threads 8 -auto_affinity off -info
hd_render -in testdata/sphere.usd -out "$MOONRAY_LOCAL_ROOT/validation/hydra.exr"
timeout 90 python building/Ubuntu24Local/check-usdview.py \
  --defaultsettings --renderer Moonray testdata/sphere.usd
```

The usdview check opens the real UI, saves screenshots and a renderer report in
`validation`, and closes after twelve seconds. These checks do not replace the
full upstream unit and regression test suites.

## Build adaptations and known warnings

This recipe draws on the [general build guide](../general_build.md), the Rocky9
dependency recipe, and [Ubuntu PR #226](https://github.com/OpenMoonRay/openmoonray/pull/226)
at `18093fcbcdd82f44a2d6256798c70a67230d0d8e`. It uses the PR's OpenImageIO 2.5.19
update without its system package installation steps.

- OpenUSD enables Python support, USD tools, and usdview using local Python
  3.10, Boost 1.78, Qt 5.15.8, PySide2, and PyOpenGL.
- `PXR_PY_UNDEFINED_DYNAMIC_LOOKUP=OFF` explicitly links shared Python, avoiding
  unresolved Python symbols in the USD command-line tools.
- TBB 2020.3 is built before its consumers, with OpenVDB's include path pinned
  to the same installation. OpenColorIO 2.2.1 uses compatible yaml-cpp 0.7.
- UUID's `include/uuid` directory is supplied explicitly to MoonRay's finder.
- Unix Makefiles avoid duplicate JSON output rules rejected by Ninja.
- Pinned source fixes add three missing `<functional>` includes and handle
  absent Hydra light/shadow-link values without empty-`VtValue` errors.
- The profile-viewer installer generates the `_version.py` omitted by its
  CMake installation, using the version from upstream `setup.cfg`.

Remaining upstream warnings observed during successful rendering:

- The C++ shader plugins attempt to import unavailable Python modules
  `pxr.MoonrayShaderParser` and `pxr.MoonrayShaderDiscovery`.
- `DwaBaseMaterial.iridescence_colors` emits a shader metadata type warning.
- `moonray_gui_v2` prints GLFW invalid-key warnings; key handling was not audited.
- No OCIO display configuration is selected by default; set `OCIO` to a desired
  configuration to enable the GUIs' display color management.
