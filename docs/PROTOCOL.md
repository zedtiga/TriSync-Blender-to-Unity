# TriSync Protocol

License: MIT. See `LICENSES/MIT.txt` in the repository root.

This document summarizes the current protocol contracts between the Blender
add-on and the Unity editor package.

## Transport Model

Blender sends JSON envelopes to Unity over the session transport. Large payloads
may be split into chunk envelopes and reassembled by Unity before normal
routing.

```text
Blender payload builder
  -> session.send_auto
  -> direct JSON or large_payload.begin/chunk/end
  -> Unity SessionCore
  -> route by envelope type
```

Unity can also send selected one-shot messages back to Blender, such as
Unity-to-Blender mesh import payloads and mesh content fingerprint requests.

## Session Handshake

The current session has an explicit multi-stage handshake and send-result
semantics. The important product rule is:

```text
Do not treat a WebSocket connection as ready until the handshake is confirmed.
```

The successful handshake remains:

```text
session_hello
  -> session_ack
  -> session.handshake_ack
  -> session.handshake_confirmed
```

Versioned peers include integer `protocolVersion = 1`. `session_hello` advertises
`features`; the remaining success messages echo the exact
`negotiatedFeatures` intersection. Unknown features are ignored. The integer is
only bumped for incompatible wire changes; optional capabilities use feature
names instead.

The `unity_mesh_import_result_v1` feature gates the one-shot Unity mesh
creation result contract below. When it is not negotiated, Blender accepts the
creation request but does not send result messages; Unity reports that the
request was sent without confirmation instead of waiting indefinitely.

Blender may include an optional informational `blenderVersion` string in
`session_ack` and `session.handshake_confirmed`. Unity may display it as peer
application metadata. It is not a protocol version, does not participate in
feature negotiation or echo validation, and its absence remains valid for
legacy or older peers.

Peers that omit `protocolVersion` use legacy compatibility mode with no
negotiated features. An explicitly different version is not silently
downgraded: either endpoint sends `session.handshake_reject`, reports the local
and peer versions in its UI, and closes the current connection. A versioned
peer also rejects inconsistent final/confirmed echoes with
`negotiation_echo_mismatch`. Echo validation does not apply to legacy peers.

Session protocol negotiation is separate from AssetBridge package
`contractVersion`. The current session protocol is integer `1`; the current
AssetBridge envelope identifier is `asset-bridge-v1`. Neither follows the
product release tag, and each changes only for an incompatible change to its
own wire contract. Negotiation state is reset on disconnect and every new
handshake id. Handshake messages remain on the WebSocket thread and do not
enter Blender's main-thread dispatcher.

## Large Payloads

Large messages use:

```text
large_payload.begin
large_payload.chunk
large_payload.end
```

Unity validates:

- transfer id
- chunk count
- byte counts
- original message type
- base64 decoding
- pending transfer limits and TTL
- the negotiated whole-payload checksum

When `large_payload_crc32_v1` is negotiated, Blender adds these fields to
`large_payload.end`:

```text
checksumAlgorithm: "crc32-ieee"
checksum: eight lowercase hexadecimal characters
```

The CRC covers the original UTF-8 JSON bytes before Base64 encoding. Unity
checks it after all chunks are Base64-decoded and the byte count is validated,
but before UTF-8 decoding and message routing. Missing, malformed, unsupported,
or mismatched checksum metadata rejects the transfer. Legacy sessions do not
require or emit checksum fields.

All pending assemblies are discarded when the Unity session connects, resets,
disconnects, or fails. An assembly also records the checksum requirement from
its `large_payload.begin`; it cannot continue under a different negotiated
session state.

## AssetBridge Packages

AssetBridge is the persistent import/update path for generated resources and
scene object assembly.

Typical order for mesh object import:

```text
asset_bridge package
embedded or related MaterialContentV1 payloads
object assembly payload
```

Ordering matters. Object assembly may depend on mesh and material records that
must already exist or be pending in Unity.

The package envelope currently includes:

```text
contractVersion: "asset-bridge-v1"
package
```

Mesh package data may include binary buffer references for large arrays.

## Binary Mesh Semantics

Canonical mesh binary semantics:

```text
POSITION
INDEX
NORMAL
UV0 ... UV7
COLOR0
BLENDSHAPE_DELTA_POSITION
SUBMESH_INDEX
```

Rules:

- `INDEX` is canonical. Do not introduce a parallel `INDICES` semantic.
- Triangle index winding is converted when mapping Blender mesh data into
  Unity space.
- Line and point topology must not be treated as triangle data.
- BlendShape delta positions are directions, not points.
- `TANGENT` is derived Unity-side rather than transported. Unity recalculates
  base tangents from final positions, normals, UV0, and triangle indices after
  full imports, preview commits, and position/UV preview updates. Missing
  prerequisites clear the tangent channel instead of retaining stale data.
- BlendShape tangent deltas are not part of the current contract.

## Unity Mesh Creation To Blender

`unity_mesh.import_v1`, `unity_mesh.import_file_v1`, and
`unity_mesh.import_binary_file_v1` are Unity-to-Blender, one-shot creation
requests. They create new Blender mesh/object data only; they do not create
SceneSync pairs, registry records, or a reverse-sync binding.

The Unity editor sends at most one creation request in flight. When
`unity_mesh_import_result_v1` is negotiated, Blender returns
`unity_mesh.import_result_v1` on the same session with this shape:

```json
{
  "type": "unity_mesh.import_result_v1",
  "status": "queued|imported|partial|failed",
  "message": "...",
  "error": null,
  "created": 1,
  "skipped": 0,
  "warnings": [],
  "timestamp": 0
}
```

The result has no `transferId`; the UI treats the latest result as the result
of the current one-shot operation. `partial` is used when one or more mesh
objects were created but a selection, submesh, or feature was omitted.
Unity waits for a result for 30 seconds using its local monotonic clock. A
timeout is shown as a warning because Blender may still finish the queued
operation. Older or legacy peers are handled by the negotiated-feature rule
above and do not leave the Unity panel in a permanent pending state.

Mesh creation supports UV0 through UV7 and single-frame BlendShapes. A
multi-frame Unity BlendShape is omitted with a warning because Blender Shape
Keys do not provide the same multi-frame asset representation. The request is
validated before Blender creates any object; malformed vertex, index, buffer,
or path data fails the mesh item instead of being truncated.

A queued `SkinnedMeshRenderer` contributes its undeformed `sharedMesh`,
material slots, and supported BlendShape definitions. Bones, skin weights,
current BlendShape weights, and the renderer's currently deformed geometry are
outside this one-shot Mesh creation contract.

Staged binary requests must use matching `import-*.manifest.json` and
`import-*.bin` files under the Unity staging tree, declare their byte sizes,
and carry `checksumAlgorithm: "crc32-ieee"` plus an eight-character lowercase
CRC32 checksum for the binary file. Blender verifies all of these before
materializing buffers and removes the files only after the queued import has
finished.

## SceneSync Messages

SceneSync is the live update path.

Major message families:

```text
scene_sync.transform
scene_sync.object_state_update
scene_sync.view_state_v1
scene_sync.preview_mesh_v1
scene_sync.preview_commit_mesh_v1
scene_sync.material_content_v1
scene_sync.blendshape_weights_v1
scene_sync.mesh_content_fingerprint_request_v1
```

Important rules:

- Unity API mutation happens on the Unity editor main thread.
- Blender API mutation happens on the Blender main thread.
- Preview mesh updates are runtime previews until committed.
- Object state changes should only affect mapped/selected targets intended by
  the sender.

### Scene View State

`scene_sync.view_state_v1` carries Blender's raw view pivot, rotation,
distance, lens, orthographic scale, and clipping range. `viewScale` is an
optional user-level framing multiplier. Unity applies it after calculating the
normal perspective or orthographic SceneView size: `1.0` preserves the baseline,
values below `1.0` zoom in, and values above `1.0` zoom out. Missing, non-finite,
or non-positive values fall back to `1.0`; supported values are clamped to
`0.1` through `5.0`.

## MaterialContentV1

MaterialContentV1 is the active material sync protocol.

Target shader:

```text
TriSync/Principled Lit URP
```

Fallback shader:

```text
Universal Render Pipeline/Lit
```

Rules:

- Texture roles are isolated.
- Replacing one texture role should not overwrite unrelated roles.
- Material content update is separate from mesh update.
- `properties.baseColor` uses Blender scene-linear channel values from
  `NodeSocketColor`. In a Linear Unity project, the Editor import path converts
  this value to Unity's gamma-encoded material color representation, matching
  Unity's native FBX material importer; Gamma projects keep the value unchanged.
- Shader Graph generation is retired.

## Rigged Object Protocol

Rigged import uses:

```text
spaceSemantic = unity_rig_v1
```

It carries:

- skeleton hierarchy
- mesh parts
- skin weights
- bindpose inputs
- material references/content
- rig axis mode fields

Supported rig axis modes:

```text
baked_joint_axes
preserve_rest_bone_axes
```

See `docs/RIG_AXIS.md` for the product contract.

Manual rig state snapshots use separate lightweight messages:

```text
asset_bridge.rigged_pose_v1
asset_bridge.rigged_blendshape_weights_v1
```

`asset_bridge.rigged_blendshape_weights_v1` carries only the current
non-Basis Shape Key weights for each bound skin part, together with its
`objectName` and `meshRef`. It never carries mesh geometry, materials, or
BlendShape deltas. Unity resolves the managed rig by `riggedObjectId` and
records the target `SkinnedMeshRenderer` objects with Undo before applying the
snapshot, so an active Animation recording can create `blendShape.*` curves.

## Animation Clip Protocol

Animation clips are generated output assets.

Rules:

- Clips are imported as new generated Unity `AnimationClip` assets under
  `Assets/TriSync/Resources/AnimationClips`.
- Clips are not registry-updated resources.
- The user decides whether to keep or delete older clips.
- Object/Armature, Shape Key, Driver-only, and combined export modes are
  selected on the Blender side.
- `exportSettings.staticCurves` is `off` or `collapse_constant`. The latter
  folds complete, time-aligned constant Transform groups to one key while
  retaining every binding. It is a serialization optimization, not a request
  to remove curves whose values match a static pose.
- `exportReport.staticCurveRemovedKeyCount` and
  `exportReport.collapsedStaticTrackCount` report the optimization result.

Clip naming — Unity resolves the generated clip name in this order:

1. User-provided `Clip Name`.
2. Source object or armature name.
3. Source Action name.
4. `AnimationClip`.

Internal semantic suffixes such as `_unity_rig_v1` and `_unity_object_v1` are
stripped from user-visible asset names. Unity then uses
`AssetDatabase.GenerateUniqueAssetPath(...)`, so an existing clip name produces
a unique new file instead of overwriting.

Clip update policy — no animation clip registry, no automatic update of an old
`.anim`, no Auto Bind Animator, and no auto-generated preview
AnimatorController. If the animation source changes, import a new clip.

Shape Key animation mapping:

```text
Blender key_blocks["Smile"].value -> Unity blendShape.Smile
Unity weight = Blender value * 100
```

For `unity_rig_v1`, the exporter enumerates all Mesh objects bound to the active
Armature. Each Shape Key track uses that mesh object's renderer-child path, so a
single clip can bind BlendShapes on multiple `SkinnedMeshRenderer` children.
Combined mode uses the union of the Armature and Shape Key Action ranges;
Shape Keys Only does not emit Armature Transform tracks.

Live Object Mode Shape Key weight sync is a separate state path and does not
create or modify `.anim` assets.

## Registry Behavior

Unity registries map Blender stable IDs to Unity assets and scene objects.

Unregister semantics:

- local Unity registry cleanup only
- no Unity asset deletion
- no Unity scene object deletion
- no Blender notification
- no tombstone/blocklist

If Blender sends the same asset/object after unregister, Unity treats it as a
normal import/update and can recreate registry records.

## Compatibility Rules

When changing protocol behavior:

1. Update both Blender and Unity DTO/routes in the same change.
2. Run `tools/Test-ProtocolTypes.ps1`.
3. Run full `tools/Test-Project.ps1` validation.
4. Add or update targeted tests for pure protocol logic when possible.
5. Update this document if message ordering, payload fields, or compatibility
   behavior changes.
