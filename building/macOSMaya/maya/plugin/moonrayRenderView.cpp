// Copyright 2023-2024 DreamWorks Animation LLC
// SPDX-License-Identifier: Apache-2.0

// moonrayRenderView: progressive MoonRay images in Maya's Render View.
//
// Render View frames are rendered by mayaHydra's hydraRender, which blocks
// Maya's main thread until MoonRay converges, so nothing in Maya can update the
// window meanwhile. hdMoonray calls a host hook (hdMoonraySetFrameCallback,
// lib/hydramoonray/FrameCallback.h) from inside that render loop each time a new
// color image arrives; this plug-in registers it for the duration of a frame and
// copies each image into Render View.
//
//   moonrayRenderView -begin -width 960 -height 540;   // before hydraRender
//   moonrayRenderView -finish;                         // after it
//
// moonray_maya/render_view.py wraps hydraRender with these.

#include <maya/MArgDatabase.h>
#include <maya/MFnPlugin.h>
#include <maya/MGlobal.h>
#include <maya/MPxCommand.h>
#include <maya/MRenderView.h>
#include <maya/MSyntax.h>

#include <chrono>
#include <cstdlib>
#include <dlfcn.h>
#include <pthread.h>
#include <string>
#include <vector>

namespace {

typedef void (*FrameCallback)(const float* rgba, int width, int height, float progress, void* user);
typedef void (*SetFrameCallback)(FrameCallback callback, void* user);

struct Frame {
    unsigned width = 0;
    unsigned height = 0;
    bool active = false;
    std::vector<RV_PIXEL> pixels;
    std::chrono::steady_clock::time_point lastUpdate;
    unsigned updates = 0;
};
Frame sFrame;

// Updating Render View costs a little; MoonRay may deliver images faster than that
// is worth. The last image is always shown by the caller loading the final EXR.
constexpr auto kMinInterval = std::chrono::milliseconds(200);

SetFrameCallback findSetFrameCallback(bool load)
{
    // Present once the Moonray delegate is loaded; otherwise (load) load it: the
    // same library the delegate links, located by the module (HDMOONRAY_LIBRARY).
    if (void* sym = dlsym(RTLD_DEFAULT, "hdMoonraySetFrameCallback")) {
        return reinterpret_cast<SetFrameCallback>(sym);
    }
    if (!load) {
        return nullptr;
    }
    const char* path = std::getenv("HDMOONRAY_LIBRARY");
    if (!path || !*path) {
        return nullptr;
    }
    void* lib = dlopen(path, RTLD_NOW | RTLD_GLOBAL);
    if (!lib) {
        MGlobal::displayWarning(MString("moonrayRenderView: ") + dlerror());
        return nullptr;
    }
    return reinterpret_cast<SetFrameCallback>(dlsym(lib, "hdMoonraySetFrameCallback"));
}

void onFrame(const float* rgba, int width, int height, float /*progress*/, void* /*user*/)
{
    // Only on Maya's main thread (hydraRender's loop), and only frames of the
    // size Render View was started with.
    if (!sFrame.active || !pthread_main_np() || width != int(sFrame.width) || height != int(sFrame.height)) {
        return;
    }
    const auto now = std::chrono::steady_clock::now();
    if (sFrame.updates > 0 && now - sFrame.lastUpdate < kMinInterval) {
        return;
    }
    sFrame.lastUpdate = now;
    sFrame.updates++;

    // Hydra's rows run bottom to top, as Render View's do.
    const size_t count = size_t(width) * size_t(height);
    sFrame.pixels.resize(count);
    for (size_t i = 0; i < count; ++i) {
        const float* p = rgba + 4 * i;
        sFrame.pixels[i] = RV_PIXEL{p[0], p[1], p[2], p[3]};
    }
    const unsigned right = sFrame.width - 1;
    const unsigned top = sFrame.height - 1;
    MRenderView::updatePixels(0, right, 0, top, sFrame.pixels.data(), /*isHdr=*/true);
    MRenderView::refresh(0, right, 0, top);
}

class MoonrayRenderViewCmd : public MPxCommand {
public:
    static void* creator() { return new MoonrayRenderViewCmd; }

    static MSyntax newSyntax()
    {
        MSyntax syntax;
        // Not -e / -h: Maya reserves them (edit, help).
        syntax.addFlag("-b", "-begin");
        syntax.addFlag("-f", "-finish");
        syntax.addFlag("-wd", "-width", MSyntax::kUnsigned);
        syntax.addFlag("-ht", "-height", MSyntax::kUnsigned);
        syntax.enableQuery(false);
        syntax.enableEdit(false);
        return syntax;
    }

    MStatus doIt(const MArgList& args) override
    {
        MStatus status;
        MArgDatabase db(syntax(), args, &status);
        if (!status) return status;

        if (db.isFlagSet("-begin")) {
            unsigned width = 0, height = 0;
            db.getFlagArgument("-width", 0, width);
            db.getFlagArgument("-height", 0, height);
            if (width == 0 || height == 0) {
                displayError("moonrayRenderView -begin needs -width and -height");
                return MS::kFailure;
            }
            SetFrameCallback setCallback = findSetFrameCallback(true);
            if (!setCallback) {
                // Not fatal: the frame still appears in Render View when it is done.
                displayWarning("moonrayRenderView: Moonray delegate not found; no progressive display");
                setResult(false);
                return MS::kSuccess;
            }
            sFrame.width = width;
            sFrame.height = height;
            sFrame.updates = 0;
            sFrame.active = MRenderView::doesRenderEditorExist() &&
                            MRenderView::startRender(width, height, false, true) == MS::kSuccess;
            setCallback(sFrame.active ? onFrame : nullptr, nullptr);
            setResult(sFrame.active);
            return MS::kSuccess;
        }
        if (db.isFlagSet("-finish")) {
            if (SetFrameCallback setCallback = findSetFrameCallback(false)) {
                setCallback(nullptr, nullptr);
            }
            if (sFrame.active) {
                MRenderView::endRender();
            }
            sFrame.active = false;
            setResult(int(sFrame.updates));
            sFrame.pixels.clear();
            sFrame.pixels.shrink_to_fit();
            return MS::kSuccess;
        }
        displayError("moonrayRenderView: use -begin or -finish");
        return MS::kFailure;
    }
};

} // namespace

MStatus initializePlugin(MObject obj)
{
    MFnPlugin plugin(obj, "OpenMoonRay", "1.0", "Any");
    return plugin.registerCommand("moonrayRenderView", MoonrayRenderViewCmd::creator,
                                  MoonrayRenderViewCmd::newSyntax);
}

MStatus uninitializePlugin(MObject obj)
{
    if (SetFrameCallback setCallback = findSetFrameCallback(false)) {
        setCallback(nullptr, nullptr);
    }
    MFnPlugin plugin(obj);
    return plugin.deregisterCommand("moonrayRenderView");
}
