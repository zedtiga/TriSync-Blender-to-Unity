# TriSync Licensing Overview

TriSync is a mixed-license project. Different components have different
licensing requirements because the product combines a Blender add-on, a Unity
Editor companion, Unity render-pipeline-derived assets, and third-party code.

This overview identifies those boundaries. It is not legal advice.

## Blender Components: GPL-3.0-or-later

The project-authored portions of the following components are licensed under
GPL-3.0-or-later:

- `blender_addon/blendersync_vnext`, excluding vendored third-party code
- `blender_addon/blendersync_vnext/native/blendersync_native`, excluding its
  Rust dependencies
- `tests/python`
- `tests/blender`

The license text is included at:

```text
LICENSES/GPL-3.0-or-later.txt
```

The compiled native helper contains third-party Rust dependencies. Their
licenses remain in force and are documented in `THIRD_PARTY_NOTICES.md`.

## Unity Companion: MIT

Project-authored Unity companion files under `unity/TriSync` are
licensed under the MIT License, except for the URP-derived Shader, HLSL, and
Shader GUI files identified below. This MIT grant includes the Editor tooling
used to import, synchronize, and convert project content. TriSync C# tooling is
Editor-only and is not included in Unity player builds.

The MIT text is included at:

```text
LICENSES/MIT.txt
```

## Unity Companion License Components

The bundled Shader/HLSL assets under `unity/TriSync/Shaders` and
`unity/TriSync/Scripts/Editor/BlenderSyncPrincipledLitShaderGUI.cs`
contain modifications of Unity Universal Render Pipeline sources. They remain
subject to the Unity Companion License for Unity-dependent projects and are
not covered by the MIT grant above or the GPL declaration.

See `THIRD_PARTY_NOTICES.md` and:

```text
LICENSES/THIRD_PARTY/Unity-Render-Pipelines-LICENSE.md
```

## Protocol Specification: MIT

The protocol specification in `docs/PROTOCOL.md` is licensed under the MIT
License so independently licensed endpoints may implement the wire contract.
This does not change the license of either endpoint's implementation code.

The MIT text is included at:

```text
LICENSES/MIT.txt
```

## Generated User Content

Meshes, materials, textures, prefabs, AnimationClips, Avatars, registries, and
other Unity assets generated from a user's content are user project output.
They do not become TriSync implementation source code or acquire GPL/MIT
licensing obligations merely because TriSync generated them.

Final Unity applications may include generated output, MIT-licensed Unity
runtime code, and runtime-capable Unity Companion License shader assets, subject
to the applicable license terms.

## Documentation And Development Files: MIT

Unless a file explicitly states otherwise, project-authored documentation,
development tools, build and CI configuration, and tests outside `tests/python`
and `tests/blender` are licensed under the MIT License. This includes the root
README and contribution guide, `docs`, `tools`, `.github`, and `tests/unity`.
The MIT text is included at `LICENSES/MIT.txt`.

This grant does not replace the component-specific or third-party licenses
identified above. Tests under `tests/python` and `tests/blender` remain
GPL-3.0-or-later.

## Branding And Media

Unless a file explicitly states otherwise, product names, branding, screenshots,
videos, and icons are copyright retained by TriSync contributors. They are not
covered by the software and documentation grants above. No trademark rights are
granted by these software licenses.

## Third-Party Code

Third-party software remains under its upstream license. Required notices and
license locations are recorded in:

```text
THIRD_PARTY_NOTICES.md
```
