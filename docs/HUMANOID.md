# TriSync Humanoid Workflow

This document defines the current Humanoid feature boundary.

## Product Position

TriSync can:

- Generate a Unity Humanoid Avatar from a TriSync-imported character.
- Convert TriSync Generic/raw Transform clips into Unity Humanoid clips.

TriSync does not claim exact Unity native FBX Humanoid importer parity.

For Unity Humanoid animation reuse, prepare the character before import:

- clean Unity-style T pose
- correct forward/up orientation
- clean humanoid proportions
- valid skeleton hierarchy

TriSync does not currently normalize arbitrary A poses, unusual rest poses,
or Blender bone-roll conventions into Unity native FBX Humanoid importer space.

## Animation Tools Window

Open the Unity-side animation workflows from one window:

```text
TriSync/Open Animation Tools
```

The window has three independent tabs:

```text
Humanoid Avatar
Humanoid Clips
Root Motion
```

`Humanoid Avatar` and `Humanoid Clips` form the normal Humanoid preparation and
conversion flow. `Root Motion` is a separate Generic workflow; it is not a third
Humanoid setup step and does not implicitly share or replace another tab's
Animator selection.

## Humanoid Avatar Setup

Basic workflow:

1. Open Animation Tools and select `Humanoid Avatar`.
2. Select or assign the target character Animator.
3. Click `Use Selection` if the character is selected.
4. Click `Auto Map`.
5. Review the Mapping tab.
6. Manually fix missing or wrong mappings.
7. Adjust additional settings if needed.
8. Click `Generate Humanoid Avatar`.

Auto Map is a TriSync-owned heuristic based on public `HumanBodyBones`
roles, hierarchy constraints, and common Generic, Mixamo, Rigify, and Unreal
style naming conventions. 3ds Max Biped-style names are handled by the same
general token and hierarchy rules rather than a separate mapper. It is
intentionally conservative: ambiguous or low-confidence roles remain unmapped
for manual review instead of being silently assigned. It does not call or
reproduce Unity's internal Avatar auto-mapping implementation.

Required mapping groups include:

```text
Hips
Spine
Head
LeftUpperLeg / LeftLowerLeg / LeftFoot
RightUpperLeg / RightLowerLeg / RightFoot
LeftUpperArm / LeftLowerArm / LeftHand
RightUpperArm / RightLowerArm / RightHand
```

Optional mappings include:

```text
Chest
UpperChest
Neck
Toes
Eyes
Jaw
Finger bones
```

## Additional Humanoid Settings

The tool exposes Unity Humanoid `HumanDescription` settings:

```text
Upper Arm Twist
Lower Arm Twist
Upper Leg Twist
Lower Leg Twist
Arm Stretch
Leg Stretch
Feet Spacing
Translation DoF
```

Default values follow Unity's common importer defaults:

```text
Upper Arm Twist = 0.5
Lower Arm Twist = 0.5
Upper Leg Twist = 0.5
Lower Leg Twist = 0.5
Arm Stretch = 0.05
Leg Stretch = 0.05
Feet Spacing = 0
Translation DoF = false
```

## Generic To Humanoid Clip Conversion

The converter samples a source Generic/raw Transform clip against a source
Humanoid Animator and writes:

```text
RootT.x/y/z
RootQ.x/y/z/w
HumanTrait muscle curves
Left/Right Hand/Foot T and Q goal curves
```

Internally it uses Unity Humanoid APIs, including `HumanPoseHandler`, so the
result depends on the source Avatar's Humanoid interpretation.

Prerequisites:

- Source Animator has a valid Humanoid Avatar.
- Source Generic/raw clip matches the source hierarchy.
- Clip binding paths resolve under the selected Root Node.

Choosing an Animator fills Avatar from `Animator.avatar`, while Avatar remains
editable. Root Node is deliberately not inferred: drag the Animator GameObject
or one of its descendants into the field. It is required and must remain under
the Animator hierarchy. Changing Animator clears Root Node; changing the
source Clip preserves it. If the chosen root cannot resolve every non-empty
Transform binding path, the UI warns that unresolved curves will be omitted.

Open Animation Tools and select `Humanoid Clips` to run this conversion.

## Generic Root Motion

The `Root Motion` tab creates a Generic Avatar for a selected hierarchy and can
generate a clip variant containing Animator `RootT` and `RootQ` curves extracted
from a chosen transform. This is an alternative Generic workflow. Generating a
Generic Avatar assigns it to the target Animator, so do not treat this tab as a
required follow-up to Humanoid Avatar generation. Its Avatar field directly edits
the selected Animator's Avatar reference; `Create Generic Avatar` remains the
explicit asset-generation action.

## Known Limitations

### No exact native FBX parity

Generated Humanoid Avatars and clips are intended to be practical, but they are
not guaranteed to match Unity native FBX-imported Humanoid assets exactly.
Auto Map is a starting point rather than an authoritative mapping. Always
review required bones, left/right assignment, helper/twist exclusion, and the
validation report before generating the Avatar.

### Rest bone axes and Blender bone roll

The default TriSync rig import path is a joint hierarchy plus skinning
workflow. Unity bone `localRotation` may be identity and may not expose Blender
rest bone roll as explicit Unity rest-axis data.

This is usually acceptable for same-source TriSync skeletons and
same-source TriSync animation curves because both use the same convention.

For third-party tools, IK, twist solving, external animation workflows, or
advanced Humanoid experiments, optional preserved rest-bone axes may provide
more source material. See `docs/RIG_AXIS.md`.

### Hand/Foot IK endpoint curves

Unity native FBX Humanoid clips may include endpoint curves such as:

```text
LeftFootT / LeftFootQ
RightFootT / RightFootQ
LeftHandT / LeftHandQ
RightHandT / RightHandQ
```

The Humanoid Clips tab always writes these curves from Unity `HumanPose` IK
goals. Goal T is direct, while Goal Q applies the project-validated Unity hand and
foot goal basis. A matching Mixamo comparison measured average Goal Q angular
differences of about `0.37-0.45` degrees and maxima around `1` degree, with Goal T
average position differences around `0.003` scene units. These results support
regular use but do not promise exact parity for every skeleton or Unity importer
version. These endpoint curves are required conversion output: omitting them can
leave incorrect Humanoid IK goal information in the generated clip. The converter
therefore always writes the complete 12 Goal T and 16 Goal Q bindings.

### RootT offset may differ

The relative motion trajectory may be close even when absolute RootT has a
constant offset from Unity-native output. Compare relative motion before
assuming the motion shape is wrong.

## Recommended Workflow

1. Import or assemble the character through TriSync.
2. Prepare/verify a correct T pose before relying on Unity Humanoid reuse.
3. Open Animation Tools and select `Humanoid Avatar`.
4. Auto-map and correct mappings manually.
5. Generate a Humanoid Avatar.
6. Convert Generic/raw clips only after the Avatar is valid.
7. Test the generated Humanoid clip on the source character and a second
   Humanoid character if retargeting is the goal.

## Troubleshooting

If the Avatar is invalid:

- Check required bone mappings.
- Check skeleton hierarchy and duplicate bone names.
- Check orientation and pose quality.

If converted clips appear static:

- Check that the source Animator has a valid Humanoid Avatar.
- Check that clip binding paths resolve under the selected Root Node.
- Select a different Root Node when the source paths are relative to another
  child skeleton root.

If root motion differs:

- Compare relative trajectory after subtracting first-frame offset.
- Verify source root curves and Animator setup.
