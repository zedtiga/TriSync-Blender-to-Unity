# TriSync Architecture

This document is the current high-level map of TriSync. It describes the
stable data paths, module boundaries, and important product constraints.

## One-Line Model

```text
Blender observes and packages data.
Unity receives, imports, applies, and stores data.
```

## Source Layout

```text
TriSync-OpenSource/
|-- blender_addon/blendersync_vnext/   Blender add-on source
|-- unity/TriSync/                     Unity package source mirror
|-- tools/                             validation and runtime sync scripts
|-- docs/                              current project documentation
|-- README.md                          project entry point
```

The repository is the source of truth. Runtime copies are deployment targets:

```text
<Unity project>/Assets/TriSync
<Blender user scripts>/addons/blendersync_vnext
```

Do not edit runtime copies as source. Supply your own project and add-on
locations to the sync scripts; see [Contributing](../CONTRIBUTING.md).

## Main Data Flow

```text
Blender UI / operators
  -> context builders
  -> payload builders
  -> session transport
  -> Unity SessionCore
  -> AssetBridgeCore / SceneSyncCore / AnimationClipImport / RootMotion
```

There are two main paths:

```text
Manual import/update path:
  Blender Import/Update Selected
    -> AssetBridge package or resource update
    -> Unity persistent resources and scene objects

Live update path:
  Blender Auto Sync / Preview
    -> SceneSync messages
    -> Unity runtime preview, object state, and preview commit
```

## Blender Modules

```text
blender_addon/blendersync_vnext/blender/
|-- ui/                   Panels, operators, context builders
|-- transport/            High-level send entrypoints and sequencing
|-- session/              Connection, send_auto, large payload chunking
|-- package/              AssetBridge package building
|-- resource_update/      Mesh update binary protocol and fingerprints
|-- material_resource/    MaterialContentV1 extraction
|-- rigged_object/        unity_rig_v1 armature and skinned mesh export
|-- scene_sync/           Auto Sync, preview, baselines, dirty triggers
|-- animation_clip/       Animation sampling and clip payloads
|-- native/               Rust/PyO3 mesh extraction bridge
|-- identity/             Stable IDs for Blender objects and resources
|-- asset_registry/       Known asset fingerprints and resend filtering
|-- deps/                 Dependency closure helpers
|-- translations.py       Blender translation catalog
|-- common/               Shared types, constants, errors, localization facade
```

Important notes:

- `scene_sync/controller.py` is the largest Blender-side coordination module.
  It owns much of Auto Sync, preview, dirty-state, lifecycle, material resend,
  and commit orchestration. Treat large edits there as high risk.
- Rust/PyO3 native extraction is an acceleration path. The Python path must
  remain a functional fallback.
- Blender uses a WebSocket/session worker thread plus Blender main-thread timer
  work. Blender API mutation should stay on the main thread.

## Unity Modules

```text
unity/TriSync/Scripts/
|-- SessionCore/          Session receive, dispatch, pending buffers, chunks
|-- Protocol/             Unity-side DTOs
|-- AssetBridgeCore/      Persistent resource import and rigged prefab import
|-- SceneSyncCore/        Auto Sync, preview mesh, material apply, transforms
|-- APT/                  Asset path/reference registry helpers
|-- AnimationClipImport/  AnimationClip creation
|-- RootMotion/           Humanoid avatar and conversion tools
|-- Diagnostics/          Report store and diagnostics views
|-- Localization/         Unity user-level language selection and UI catalog
|-- UI/                   Unified TriSync window and registry views
|-- Editor/               URP-specific custom Shader GUI
```

Unity C# is split into two Editor-only assembly definitions:

- `BlenderSyncVNext.Editor` contains the session, import, synchronization, UI,
  diagnostics, and Humanoid tools.
- `BlenderSyncVNext.URP.Editor` isolates the URP/Core Editor API dependency of
  the custom material inspector.

Transform smoothing is held in an Editor-only managed target table and never
attaches a component to scene objects. TriSync disconnects before entering Play
Mode and all incoming or outgoing session work is rejected outside Edit Mode.
Generated meshes, materials, Avatars, shaders, and other assets remain usable
by player builds.

Important notes:

- Unity receives WebSocket messages off the editor update path, then pumps
  pending work on the Unity editor main thread before touching Unity APIs.
- `PreviewMeshService.cs` and `MaterialContentV1ApplyService.cs` are large
  hotspot services. Prefer focused edits and pure helper tests around them.
- For Unity native resources such as `Mesh`, prefer API-level in-place updates
  over `CopySerialized` when updating existing assets.

## Active Product Paths

Active:

```text
MaterialContentV1
TriSync/Principled Lit URP
Materials
Textures
unity_rig_v1
Binary v1 POSITION/INDEX/NORMAL/UV0-UV7/COLOR0
SceneSync PreviewMeshService
Unified Registry tab local unregister
Unity-to-Blender one-shot mesh import
```

Generated material and texture assets are written under
`Assets/TriSync/Resources/Materials` and
`Assets/TriSync/Resources/Textures`. Protocol and implementation
identifiers such as `MaterialContentV1` remain unchanged.

Retired or intentionally not active:

```text
Old blender/material_sync package
Old scene_sync.material_content / material_facts handlers
Shader Graph generation path
AnimationClip auto-bind / auto-preview controller creation
Blender-side Remove Selected in Unity UI button
Remove stale pairs UI button
```

## Current Workflows

### Ordinary Mesh Import

```text
Blender selected mesh object
  -> object context builder
  -> AssetBridge package
  -> Unity mesh/material/texture assets
  -> Unity scene object assembly
```

The import path maps Blender-space mesh data into Unity space through
`SceneSyncTransformMapper`.

Base mesh tangents are derived in Unity after positions, normals, UV0, and
triangle submeshes reach their final mapped form. They are intentionally not a
wire field or fingerprint input. Meshes without complete UV0/normals, and
non-triangle topology, keep an empty tangent channel. BlendShape tangent deltas
are not currently transported.

### Material Content

```text
Blender material
  -> MaterialContentV1 payload
  -> scene_sync.material_content_v1
  -> Unity material using TriSync/Principled Lit URP
```

Material content has its own update path. Mesh updates should not be expected to
refresh material parameter changes.

### Auto Sync And Preview

```text
Blender dirty signal / mode change / poll
  -> scene_sync.controller
  -> preview mesh or object state payload
  -> Unity SceneSyncCore
  -> runtime preview or persistent commit
```

Edit Mode mesh changes preview first, then commit on mode exit when appropriate.
Manual UV Preview sends current evaluated mesh data with UV channels.

Automatic transform messages use managed per-Transform smoothing state in Edit
Mode, so scene objects never receive a temporary component. The user-level
duration and normalized follow curve are read by the Editor assembly. Only
active targets are tracked, interpolation uses real delta time, and completed
state is removed after reaching the latest target. Direct local-TRS object-state
updates cancel any older world-space smoothing target first.

### Shape Keys / BlendShapes

- Shape key weights sync as lightweight object state.
- Imported rigs expose a separate lightweight `rigged_blendshape_weights_v1`
  snapshot channel for recording current weights on all bound skin parts.
- The rigged snapshot resolves each renderer by mesh reference and child name,
  then records Unity Undo before applying weights so Animation recording can
  create `blendShape.*` curves.
- Shape key structure and delta changes are mesh resource updates.
- Shape key Edit Mode preview is conservative by design.

### Rigged Objects

`unity_rig_v1` imports armature hierarchy, skinned mesh parts, weights,
bindposes, and materials.

Default rig axis mode is identity/rest-baked. Preserved rest-bone axes are an
explicit optional mode. See `docs/RIG_AXIS.md`.

### Animation Clips

Animation clip import creates generated Unity `AnimationClip` assets. Clips are
not registry-updated resources. If a source animation changes, import a new
clip and let the user keep or delete older clips.

For `unity_rig_v1`, animation source discovery treats the active Armature and
all Mesh objects bound to it as one rig. Combined mode samples the Armature
Action plus every bound mesh's Shape Key Action or drivers. Shape Keys Only
samples those mesh sources without adding Armature channels. BlendShape curve
paths use the corresponding rigged-prefab renderer child name.

The optional Static Curves setting is applied after sampling and ordinary key
reduction. Collapse Constant only folds complete, time-aligned constant
Animator/Transform groups to one key; it preserves every curve binding and is
off by default. When every eligible curve in a clip is static, one existing
curve may retain a same-value endpoint key so Unity's `AnimationClip.length`
continues to cover the declared duration. Removing a curve because its constant
value matches a skeleton's static pose is intentionally not part of this
low-risk path because it can change layering, Avatar Mask, retargeting, or
runtime-default semantics.

### Registry View

The Registry tab is a Unity-side ownership and inspection tool. Unregister
removes Unity-side registry records only. It does not delete assets, scene
objects, or Blender data, and it does not notify Blender.

## Known Architecture Risks

These are not necessarily release blockers, but they are the areas to treat
with extra care:

- Blender `scene_sync/controller.py` is still too large and coordinates several
  state machines. File-scoped mutable state is now collected in explicit
  runtime containers, but the orchestration code should eventually be split by
  lifecycle, object state, material, and preview responsibilities.
- Unity `PreviewMeshService.cs` and `MaterialContentV1ApplyService.cs` are large
  hotspot files.
- Protocol negotiation intentionally covers the current session version and
  optional features. Future incompatible changes must bump the protocol version
  or add a negotiated feature before the new behavior is sent.
- Large payload chunking validates structure, byte counts, and a negotiated
  whole-payload CRC32. Legacy peers remain valid without checksum metadata.
- Python tests cover the main-thread dispatcher, file lifecycle, controller
  state transitions, preview mapping, mesh fingerprints, and sync policy. Broad
  Blender/Unity integration behavior still depends on the documented manual
  smoke matrix.
