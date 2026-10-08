# Rig Axis Mode

This document defines the current rig axis import/export contract.

## Status

The default rig import behavior is:

```text
BakedJointAxes
```

Optional preserved rest-bone axes are available behind an explicit mode:

```text
PreserveRestBoneAxes
```

Preserved axes are an advanced/export-style representation option. They are not
the default and are not a retargeting solution.

## Default: BakedJointAxes

This is the stable TriSync `unity_rig_v1` joint-axis contract.

Rig import:

```text
Unity bone localPosition = mapped joint offset
Unity bone localRotation = identity
Unity bone localScale    = one
```

Animation export uses the same identity/rest-baked Unity bone basis.

Benefits:

- Simple and stable.
- Avoids depending on Blender bone roll quality.
- Works well for TriSync skeletons animated by TriSync curves.
- Keeps bind pose and animation conventions aligned.

Limitations:

- Blender bone roll is not directly exposed as Unity bone local rotations.
- Tools that depend on explicit rest axes may need calibration or preserved
  axes mode.

## Optional: PreserveRestBoneAxes

PreserveRestBoneAxes writes the mapped Blender rest-local bone transform into
Unity local TRS:

```text
Unity bone local TRS = mapped Blender rest local matrix
```

That means Unity bone `localRotation` can encode converted rest axis and roll
instead of identity.

The intended mental model is:

```text
TriSync PreserveRestBoneAxes ~= Blender FBX-style source rig data
```

This mode gives downstream tools more source material. Those tools are still
responsible for their own retargeting, IK, twist, or calibration behavior.

## Axis Settings

The mode exposes primary and secondary axis settings. They are defined on the
generated Unity skeleton representation, not as a promise of exact Blender FBX
exporter parity.

Current default pair for preserved axes:

```text
Primary   +Z
Secondary +X
```

This default preserves the behavior validated during the first stable
implementation of the axis extension.

## Payload Fields

Rigged payloads carry fields like:

```json
{
  "spaceSemantic": "unity_rig_v1",
  "rigAxisMode": "baked_joint_axes",
  "primaryBoneAxis": "Z",
  "secondaryBoneAxis": "X"
}
```

Supported mode values:

```text
baked_joint_axes
preserve_rest_bone_axes
```

Bone payload matrix fields used by preserved axes include:

```text
localMatrix
restLocalMatrix
tailLocalMatrix
localRotation
localScale
```

Matrix arrays are row-major.

## Animation Contract

Animation export must match the rig axis mode.

For `BakedJointAxes`, animation writes curves in the identity/rest-baked bone
basis used by the default rig import.

For `PreserveRestBoneAxes`, animation writes full local TRS curves in the
preserved Unity basis:

```text
localPosition
localRotation
localScale
```

For animated bones in preserved-axis mode, writing full TRS avoids sparse curve
defaults accidentally inheriting the baked-axis contract.

## Product Guidance

Use default `BakedJointAxes` for normal TriSync-imported skeletons and
TriSync-authored animations.

Use `PreserveRestBoneAxes` only when:

- a downstream Unity tool needs explicit rest local rotations,
- an IK/twist/retargeting tool expects meaningful bone local axes,
- you need export-style skeleton source data,
- you are validating an advanced pipeline that understands the axis mode.

Do not enable preserved axes expecting TriSync to automatically solve
retargeting or Unity native FBX Humanoid normalization.

## Regression Rule

```text
BakedJointAxes must not change when PreserveRestBoneAxes changes.
```

Any future axis-mode work must test:

- default import visual rest pose,
- default same-source animation playback,
- preserved-axis rest pose,
- preserved-axis same-source animation playback,
- skinned mesh deformation,
- switching axis mode without mutating unrelated imported rigs.
