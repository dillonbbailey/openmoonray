// Copyright 2023-2024 DreamWorks Animation LLC
// SPDX-License-Identifier: Apache-2.0
//
// Gate for PLAN.md §7 Risk 1: the delegate compiles Maya's USD 25.11 headers
// against deps TBB 2020.3, while Maya's libusd_* were built against oneTBB
// 2022. tbb-probe.sh compiles this file once per TBB header set and diffs the
// output.
//
// Several USD types holding TBB members have different sizes under the two
// header sets. That is harmless as long as the delegate only uses them through
// pointers and out-of-line calls. What must match:
//   "must"  base classes hdMoonray derives from, and the offsets of private
//           members hdMoonray reaches through inline accessors.
//   "info"  everything else, reported for context.
// Any new inline accessor hdMoonray starts using on a TBB-holding USD type
// belongs in the "must" set.
//
// Define PROBE_WITH_SCENE_RDL2 to also include scene_rdl2 in the same TU
// (only valid with TBB 2020.3, which scene_rdl2 was built against) and to
// exercise both TBB runtimes in one process.

#include <pxr/pxr.h>
#include <pxr/base/tf/diagnosticMgr.h>
#include <pxr/base/tf/token.h>
#include <pxr/imaging/hd/basisCurves.h>
#include <pxr/imaging/hd/camera.h>
#include <pxr/imaging/hd/changeTracker.h>
#include <pxr/imaging/hd/instancer.h>
#include <pxr/imaging/hd/light.h>
#include <pxr/imaging/hd/material.h>
#include <pxr/imaging/hd/mesh.h>
#include <pxr/imaging/hd/points.h>
#include <pxr/imaging/hd/renderBuffer.h>
#include <pxr/imaging/hd/renderDelegate.h>
#include <pxr/imaging/hd/renderIndex.h>
#include <pxr/imaging/hd/renderPass.h>
#include <pxr/imaging/hd/rendererPlugin.h>
#include <pxr/imaging/hd/sceneDelegate.h>
#include <pxr/imaging/hd/volume.h>
#include <pxr/usd/usd/stage.h>
#include <pxr/usdImaging/usdImaging/delegate.h>
#include <pxr/usdImaging/usdImaging/indexProxy.h>
#include <pxr/usdImaging/usdImaging/lightAdapter.h>

#ifdef PROBE_WITH_SCENE_RDL2
#include <scene_rdl2/scene/rdl2/SceneContext.h>
#endif

#include <tbb/concurrent_hash_map.h>
#include <tbb/concurrent_unordered_map.h>
#include <tbb/concurrent_vector.h>
#include <tbb/enumerable_thread_specific.h>
#include <tbb/spin_mutex.h>
#include <tbb/spin_rw_mutex.h>

#include <cstdio>
#include <iterator>

PXR_NAMESPACE_USING_DIRECTIVE

// Explicit instantiation may name private members, which is the standard way
// to take their member pointers.
template <typename Tag, typename Tag::type M>
struct PrivateMember { friend typename Tag::type get(Tag) { return M; } };

struct RenderIndexTracker { using type = HdChangeTracker HdRenderIndex::*; friend type get(RenderIndexTracker); };
struct DelegateTime       { using type = UsdTimeCode UsdImagingDelegate::*;  friend type get(DelegateTime); };
template struct PrivateMember<RenderIndexTracker, &HdRenderIndex::_tracker>;
template struct PrivateMember<DelegateTime, &UsdImagingDelegate::_time>;

template <typename C, typename M>
size_t offsetOf(M C::*member)
{
    alignas(C) static unsigned char storage[sizeof(C)];
    const C* object = reinterpret_cast<const C*>(storage);
    return reinterpret_cast<const unsigned char*>(&(object->*member)) - storage;
}

#define SIZE(KIND, T) \
    std::printf("%s  %-44s sizeof %5zu  alignof %3zu\n", KIND, #T, sizeof(T), alignof(T))
#define OFFSET(KIND, NAME, TAG) \
    std::printf("%s  %-44s offset %5zu\n", KIND, NAME, offsetOf(get(TAG())))

int main()
{
    // Base classes hdMoonray derives from: a derived layout depends on these.
    SIZE("must", HdRenderDelegate);
    SIZE("must", HdRendererPlugin);
    SIZE("must", HdSceneDelegate);
    SIZE("must", HdRenderPass);
    SIZE("must", HdRenderBuffer);
    SIZE("must", HdInstancer);
    SIZE("must", HdRprim);
    SIZE("must", HdMesh);
    SIZE("must", HdBasisCurves);
    SIZE("must", HdPoints);
    SIZE("must", HdVolume);
    SIZE("must", HdSprim);
    SIZE("must", HdCamera);
    SIZE("must", HdLight);
    SIZE("must", HdMaterial);
    SIZE("must", UsdImagingLightAdapter);

    // Private members hdMoonray reads through inline accessors.
    // HdRenderIndex::GetChangeTracker() is inline; the tracker methods it
    // then calls are all out of line.
    OFFSET("must", "HdRenderIndex::_tracker", RenderIndexTracker);

    // UsdImagingDelegate::GetTime() is inline and this offset differs, which
    // is why RenderPass.cc uses the out-of-line GetTimeWithOffset(0) instead.
    OFFSET("info", "UsdImagingDelegate::_time", DelegateTime);

    // Types holding TBB members, used by hdMoonray only through pointers.
    SIZE("info", HdChangeTracker);
    SIZE("info", HdRenderIndex);
    SIZE("info", TfDiagnosticMgr);
    SIZE("info", UsdImagingDelegate);
    SIZE("info", UsdImagingIndexProxy);
    SIZE("info", UsdStage);

    // The TBB containers themselves, for context.
    using HashMap = tbb::concurrent_hash_map<int, int>;
    using UMap    = tbb::concurrent_unordered_map<int, int>;
    using Vec     = tbb::concurrent_vector<int>;
    using Ets     = tbb::enumerable_thread_specific<int>;
    SIZE("info", HashMap);
    SIZE("info", UMap);
    SIZE("info", Vec);
    SIZE("info", Ets);
    SIZE("info", tbb::spin_mutex);
    SIZE("info", tbb::spin_rw_mutex);

#ifdef PROBE_WITH_SCENE_RDL2
    // Link and run check: a USD call (oneTBB runtime inside libusd_tf) and a
    // scene_rdl2 call (TBB 2020.3 runtime) in the same process.
    const TfToken token("moonray");
    scene_rdl2::rdl2::SceneContext context;
    std::printf("run   TfToken '%s', SceneContext with %zu SceneClasses\n",
                token.GetText(),
                static_cast<size_t>(std::distance(context.beginSceneClass(),
                                                  context.endSceneClass())));
#endif
    return 0;
}
