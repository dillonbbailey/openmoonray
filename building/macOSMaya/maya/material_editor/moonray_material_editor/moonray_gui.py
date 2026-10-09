"""Export a persistent material scene and open the standalone renderer GUI."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile

from .model import GraphError


def scene_snapshot(graph, base_dir):
    """Keep every scene node, anchoring assets before leaving the document folder."""
    result = graph.clone()
    base = Path(base_dir).resolve()

    def anchor(value):
        if isinstance(value, list):
            return [anchor(item) for item in value]
        if isinstance(value, str) and value and "://" not in value:
            path = Path(os.path.expandvars(value)).expanduser()
            return str(path if path.is_absolute() else base / path)
        return value

    for node in result.data["nodes"]:
        for name, attr in result.catalog.attributes(node["shader"]).items():
            if attr.get("filename"):
                value = result.value(node, name)
                if value:
                    node["values"][name] = anchor(value)
    result.data["preview"]["vdb_file"] = anchor(result.data["preview"]["vdb_file"])
    result.validate(require_surface=True)
    return result


def launch_scene(scene, mode, executable=None, *, log_directory=None):
    executable = executable or shutil.which("moonray_gui")
    if not executable:
        raise GraphError("moonray_gui was not found in the MoonRay runtime.")
    scene = Path(scene).resolve()
    log_path = Path(log_directory or scene.parent) / "moonray_gui.log"
    environment = os.environ.copy()
    # The standalone GUI uses Qt5; the editor's Qt6 plugin paths are incompatible.
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(key, None)
    environment["QT_QPA_PLATFORM"] = "xcb"
    with log_path.open("wb") as log:
        process = subprocess.Popen([str(executable), "-in", str(scene), "-exec_mode", mode],
                                   cwd=scene.parent, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    previous = signal.getsignal(signal.SIGTERM)

    def cancel(signum, _frame):
        # Cancel during startup must also close the newly detached GUI.
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, cancel)
    try:
        try:
            code = process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            return process.pid
        if code != 0:
            detail = log_path.read_text(errors="replace")[-3000:].strip()
            raise GraphError(f"moonray_gui exited during startup (code {code}).\n{detail}\nLog: {log_path}")
        return process.pid
    finally:
        signal.signal(signal.SIGTERM, previous)


def render_material_scene(graph, directory, base_dir, size=512, mode="vectorized"):
    executable = shutil.which("moonray_gui")
    if not executable:
        raise GraphError("moonray_gui was not found in the MoonRay runtime.")
    from .native import create_scene
    snapshot = scene_snapshot(graph, base_dir)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    images = directory / "images"
    images.mkdir(exist_ok=True)
    scene = directory / (snapshot.material_export_name() + "_preview.rdla")
    snapshot.save(directory / "graph.moonraygraph")
    create_scene(snapshot, scene, images, size)
    print(f"Prepared material scene: {scene}", flush=True)
    pid = launch_scene(scene, mode, executable)
    print(f"Opened moonray_gui (PID {pid}). Log: {directory / 'moonray_gui.log'}", flush=True)
    return scene, pid


def export_scene(target, prepare):
    """Publish the RDLA atomically, keeping its generated dependencies beside it."""
    target = Path(target).resolve()
    assets = Path(tempfile.mkdtemp(prefix=target.stem + "_assets-", dir=target.parent))
    try:
        scene = prepare(assets)
        scene.replace(target)
    except BaseException:
        shutil.rmtree(assets)
        raise
    print(f"Exported native scene: {target}\nScene assets: {assets}", flush=True)
    return dict(scene=str(target), assets=str(assets))


def export_and_launch(target, mode, prepare):
    executable = shutil.which("moonray_gui")
    if not executable:
        raise GraphError("moonray_gui was not found in the MoonRay runtime.")
    result = export_scene(target, prepare)
    target, assets = Path(result["scene"]), Path(result["assets"])
    pid = launch_scene(target, mode, executable, log_directory=assets)
    print(f"Opened moonray_gui (PID {pid}). Log: {assets / 'moonray_gui.log'}", flush=True)
    return dict(scene=str(target), assets=str(assets), pid=pid)


def export_material_scene(graph, target, base_dir, size=512, mode="vectorized", *, launch=True):
    from .native import create_scene
    snapshot = scene_snapshot(graph, base_dir)

    def prepare(assets):
        scene = assets / "scene.rdla"
        create_scene(snapshot, scene, assets, size)
        return scene

    return export_and_launch(target, mode, prepare) if launch else export_scene(target, prepare)
