# Quick Start

This guide is for installing a TriSync package and running the
first Blender-to-Unity sync.

## Supported Environment

Use the supported range:

- Blender `4.2.x` through `5.2.x`
- Unity `6000.0.x` through `6000.5.x`
- Universal Render Pipeline `17.0.x` through `17.5.x`

Use matching Unity and URP minors: Unity `6000.n` is qualified with URP `17.n`.
Versions outside these ranges may work, but are not part of the current support
statement.

## Package Contents

Download the complete `TriSync-1.0.0.zip` bundle from
[GitHub Releases](https://github.com/zedtiga/TriSync-Blender-to-Unity/releases/latest).
Source code and installation packages are free. Choose a named TriSync release
asset; GitHub's automatic **Source code** archives are repository snapshots,
not Blender installation ZIPs.

A distribution consists of two parts:

```text
blender/   Blender add-on zip (blendersync_vnext)
unity/     Unity Editor companion package folder
```

The Blender zip installs the Blender add-on. The Unity folder is the Unity
Editor companion package. The outer bundle also contains the current docs,
license files, and `manifest.json`. Both standalone ZIPs include the license
overview, third-party notices, and license texts in their package root. An
external SHA256 manifest/checksum list supports independent verification.

## Install The Blender Add-On

1. Open Blender.
2. Go to `Edit > Preferences > Add-ons`.
3. Use `Install from Disk`.
4. Select `blender/TriSync-Blender-<version>.zip`.
5. Enable `TriSync`.
6. Open the 3D View sidebar and find the `TriSync` tab.

If the tab does not appear, restart Blender and confirm the add-on is enabled.

## Install The Unity Companion

1. Close Unity or keep the project idle.
2. Copy the Unity package folder into the project:

   ```text
   Assets/TriSync
   ```

3. Open Unity and wait for compilation.
4. Confirm the menu exists:

   ```text
   TriSync/Open TriSync
   ```

The Unity companion is an Editor tool. It is not required as runtime game logic.

## Language

TriSync provides English and Simplified Chinese UI text.

- Blender follows Blender's global interface language and translation settings.
  Use `Edit > Preferences > Interface > Translation`; the add-on does not keep
  a separate language preference.
- Unity exposes the same user-level setting in the main TriSync window under
  `Session > Settings` and under `Edit > Preferences > TriSync`. Choose
  `System`, `English`, or `Simplified Chinese`. `System` selects
  Simplified Chinese only when Unity reports a Simplified Chinese system
  language; all other system languages use English.
- Unity's `TriSync/...` menu command names remain stable English because
  Unity requires `MenuItem` paths to be compile-time constants. The opened
  windows, controls, tooltips, dialogs, and user-facing operation feedback use
  the selected language.

Logs, protocol identifiers, asset names, and filesystem paths are not translated
so diagnostics remain stable and searchable.

## Transform Smoothing

Unity automatically smooths Blender Auto Sync transform updates. Open
`Session > Settings` in the main TriSync window to enable or disable smoothing,
change its duration, or edit the normalized follow curve. The same controls are
available under `Edit > Preferences > TriSync`; these are user-level settings
and are not saved into Unity scenes.

## First Connection

1. In Unity, open:

   ```text
   TriSync/Open TriSync
   ```

2. Start or connect the Unity session from the control panel.
3. In Blender, open the `TriSync` sidebar panel.
4. Connect to Unity from Blender.
5. Confirm both sides show connected state.

Warnings and errors should remain visible in Unity.

## First Import

Use a simple object first:

1. In Blender, create or select a cube.
2. Assign one material.
3. Optionally assign one texture.
4. In the TriSync panel, mark/select the object for sync.
5. Import or update the selected object.
6. In Unity, confirm:
   - A scene object appears.
   - A Mesh asset is generated.
   - A Material asset is generated.
   - A Texture asset is generated when a texture was used.
   - The Registry tab can show the object/resource relationship.

After the first import works, test camera and light sync, then more complex mesh
updates.

## Updating Content

Use the workflow that matches the changed data:

- Object transform or hierarchy: send object state/update.
- Mesh geometry: update the mesh object.
- Material values or texture slots: use the material update workflow.
- UV edits: use Preview UV when validating UV channels.
- Shape Key weights: Object Mode weight changes sync as lightweight state.
- Shape Key structure or deltas: update/import the mesh resource.
- Meshes with Shape Keys: modifier viewport results are intentionally ignored;
  Unity receives the original mesh and BlendShape data so vertex/delta mapping
  remains stable.
- Animation: import a new AnimationClip when the source animation changes.

AnimationClips are generated outputs. They are not registry-overwritten
resources; keep or delete old clips manually.

Generic-to-Humanoid conversion defaults to Light key reduction after pose
sampling. Use Off when an exact dense bake is required; Medium and Aggressive
trade progressively more precision for fewer runtime curve keys. Conversion
details in Recent Activity report the before/after key counts.

Both Humanoid conversion and Root Motion clip creation expose Static Curves.
It defaults to Off. Collapse Constant is a low-risk serialization optimization:
complete, time-aligned constant Animator or Transform groups are represented by
one key while every binding remains. It does not remove curves whose value
matches a static pose, and it does not rewrite BlendShape, discrete, or
object-reference curves. For an entirely static clip, one existing curve may
keep a same-value endpoint key so the declared clip duration remains visible
through Unity's `AnimationClip.length` API.

Root Motion clip creation also offers the four reduction presets, but defaults
to Off because it copies the source clip's curves. Light, Medium, and Aggressive
are intended for dense recorded or baked Generic clips; Transform components
remain synchronized, while discrete and object-reference curves are preserved.

### Geometry Nodes

When a Geometry Nodes result is intended to become one concrete Unity Mesh,
place `Realize Instances` on the final output path after all nodes that create
instances. This is recommended, not required: TriSync automatically
realizes any remaining final-output instances on a temporary export-only
duplicate and does not modify the user's node tree.

Explicit realization avoids the additional temporary realization pass and
makes final topology, materials, and attributes easier to reason about. Leaving
instances unrealized can keep Blender's interactive graph lighter, but import
and real Geometry Nodes content changes may pause while TriSync realizes a
large result for export. Normal object movement syncs only object state and does
not rebuild that mesh.

## Rigged Objects And Humanoid

Rigged object import is supported through `unity_rig_v1`.

Use the default identity/rest-baked axis mode unless downstream tools need
explicit rest-axis data. Preserved rest-bone axes are an advanced source-data
option.

Humanoid Avatar tools are available, but TriSync does not clone Unity's
native FBX Humanoid importer. For Humanoid animation reuse, prepare the character
before import with a clean Unity-style T pose, correct orientation, and clean
proportions.

## Registry And Diagnostics

Open:

```text
TriSync/Open TriSync
```

Select the Registry tab to inspect generated resources, scene objects, and
references. Select Diagnostics for reports and registry health information.

`Unregister` removes TriSync's Unity-side registry ownership records. It
does not delete Unity assets, scene objects, or Blender objects. If Blender sends
the same item again later, Unity can recreate registry records.

## Common Issues

### Blender Panel Is Missing

- Confirm the add-on is enabled.
- Restart Blender.
- Confirm the package was installed as `blendersync_vnext`.

### Unity Menu Is Missing

- Confirm the Unity folder is under `Assets/TriSync`.
- Wait for Unity compilation to finish.
- Check the Unity Console for compile errors.

### Connection Does Not Start

- Open Unity's TriSync control panel first.
- Confirm both tools target the same project/session.
- Check the Unity Console and Blender system console for handshake warnings.

### Material Did Not Update

Material content has its own update path. A mesh update does not necessarily
refresh material values or texture slots.

### Preview Looks Right But Commit Did Not Change The Asset

Use the final update/import path for committed mesh resources. Preview is
intended for interactive feedback before commit.

### Humanoid Retargeting Looks Wrong

Check the source rest pose first. TriSync expects a clean Unity-style T pose
for reliable Humanoid reuse. It does not normalize arbitrary A poses or unusual
bone-roll conventions into Unity's native FBX importer space.

## More Documentation

- `docs/README.md` for the index of all core documentation.
- `docs/ASSET_IDENTITY.md` for generated asset identity and registries.
- `docs/HUMANOID.md` and `docs/RIG_AXIS.md` for rigged object and Humanoid limits.
