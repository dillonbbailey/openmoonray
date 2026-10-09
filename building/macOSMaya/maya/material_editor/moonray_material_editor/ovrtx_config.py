"""Optional ovRTX runtime discovery without importing its native libraries."""
import os
from pathlib import Path

OVRTX = "MoonLabOvRtx"
OVRTX_NAME = "ovRTX"
PREFIX = "OVRTX_VIEWER_EVENT "
ROOT = Path(__file__).resolve().parents[1]


def runtime_python():
    return Path(os.environ.get("MOONLAB_OVRTX_PYTHON", ROOT / ".venv-ovrtx/bin/python"))


def require_runtime():
    python = runtime_python()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeError("ovRTX is not installed. Run ./setup-ovrtx.sh in the MoonLab folder, "
                           "or set MOONLAB_OVRTX_PYTHON to a Python environment with ovrtx and ovstage.")
    return str(python)


def runtime_environment():
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH", "QT_PLUGIN_PATH",
                "QT_QPA_PLATFORM_PLUGIN_PATH", "PXR_PLUGINPATH_NAME", "RDL2_DSO_PATH"):
        env.pop(key, None)
    env.update(PYTHONPATH=str(ROOT), PYTHONNOUSERSITE="1", PYTHONUNBUFFERED="1")
    return env
