"""Launch the real usdview UI, capture its viewport, then close it cleanly."""
import json
import os
from pathlib import Path
from pxr.Usdviewq import Launcher
from pxr.Usdviewq.qt import QtCore


class CheckedLauncher(Launcher):
    def LaunchPreamble(self, options):
        app, controller = super().LaunchPreamble(options)

        def capture():
            root = Path(os.environ['MOONRAY_LOCAL_ROOT']) / 'validation'
            root.mkdir(exist_ok=True)
            view = controller._stageView
            renderer = view.GetCurrentRendererId()
            viewport = controller.GrabViewportShot()
            assert not viewport.isNull(), 'Empty usdview framebuffer'
            assert viewport.save(str(root / f'usdview-{renderer}.png'))
            controller.GrabWindowShot().save(str(root / f'usdview-{renderer}-window.png'))
            report = {'renderer': str(renderer),
                      'plugins': [str(p) for p in view.GetRendererPlugins()],
                      'width': viewport.width(), 'height': viewport.height(),
                      'stage': str(controller._dataModel.stage.GetRootLayer().identifier)}
            (root / f'usdview-{renderer}.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report), flush=True)
            app.closeAllWindows()

        QtCore.QTimer.singleShot(12000, capture)
        return app, controller


CheckedLauncher().Run()
