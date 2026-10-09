"""Linear HDR readback in the optional RTX environment; no native USD/Qt imports."""
import json
import os
from pathlib import Path
import sys
import time

from .ovrtx_settings import refinement_frames, validate_final_settings
from .ovrtx_worker import Renderer
from .ovrtx_progress import RenderProgress
from .ovrtx_outputs import validate_outputs

PREFIX = "OVRTX_FINAL_EVENT "


def main():
    import numpy as np
    # Do not leave a GPU job behind if its native parent crashes.
    if sys.platform.startswith("linux"):
        import ctypes
        import signal
        parent = os.getppid()
        ctypes.CDLL(None).prctl(1, signal.SIGKILL)
        if os.getppid() != parent:
            return
    request = Path(sys.argv[1])
    data = json.loads(request.read_text())
    data["settings"] = validate_final_settings(data["settings"])
    data["output_names"] = validate_outputs(data.get("output_names", ["HdrColor"]), data["settings"]["mode"])
    renderer = Renderer(request.parent)
    try:
        count = refinement_frames(data["settings"])
        published = time.monotonic()
        progress = RenderProgress(data["settings"], count, data.get("progress_step", 5),
                                  emit=lambda message: print(message, flush=True))
        for index in range(count):
            pixels, _ = renderer.render_frame(data, progress=progress.status, all_outputs=True)
            final = index + 1 == count
            if final or (data["live_updates"] and (index == 0 or time.monotonic() - published >= data["update_interval"])):
                temporary = request.parent / "hdr.tmp"
                with temporary.open("wb") as stream:
                    np.savez(stream, **pixels)
                # A unique file prevents a fast final frame from replacing a
                # checkpoint before the native EXR writer has consumed it.
                destination = request.parent / f"hdr-{index}.npz"
                os.replace(temporary, destination)
                print(PREFIX + json.dumps(dict(file=str(destination), final=final)), flush=True)
                published = time.monotonic()
            progress.completed_frame()
    finally:
        renderer.close()


if __name__ == "__main__":
    main()
