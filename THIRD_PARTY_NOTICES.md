# Third-Party Notices

This file records third-party code and license obligations for TriSync
source and release packaging. Third-party components remain under their
upstream licenses regardless of TriSync's component licenses.

## Provenance Guard

No Unity Reference Only Humanoid mapper source is present in the current
product tree. A public package must continue to pass the provenance scan in
`tools/Test-LicenseBundle.ps1`.

## Vendored Python Dependencies

### websockets 16.0

- Location: `blender_addon/blendersync_vnext/vendor/websockets`
- Upstream project: `https://github.com/python-websockets/websockets`
- Primary license: BSD-3-Clause
- License copies:
  `blender_addon/blendersync_vnext/vendor/websockets/LICENSE` and
  `LICENSES/THIRD_PARTY/websockets-LICENSE.txt`

The vendored file
`blender_addon/blendersync_vnext/vendor/websockets/asyncio/async_timeout.py`
also contains:

- code from `async-timeout`, licensed under Apache-2.0; and
- a compatibility backport from `typing_extensions`, licensed under the
  Python Software Foundation License 2.0.

The corresponding license texts are included at:

```text
LICENSES/THIRD_PARTY/Apache-2.0.txt
LICENSES/THIRD_PARTY/Python-Software-Foundation-LICENSE.txt
```

## Unity Universal Render Pipeline Sources

The following files contain project modifications of Unity Universal Render
Pipeline 17.3.0 source files:

```text
unity/TriSync/Shaders/BlenderSyncDepthOnlyPass.hlsl
unity/TriSync/Shaders/BlenderSyncLitDepthNormalsPass.hlsl
unity/TriSync/Shaders/BlenderSyncLitForwardPass.hlsl
unity/TriSync/Shaders/BlenderSyncLitGBufferPass.hlsl
unity/TriSync/Shaders/BlenderSyncLitInput.hlsl
unity/TriSync/Shaders/BlenderSyncLitMetaPass.hlsl
unity/TriSync/Shaders/BlenderSyncObjectMotionVectorsPass.hlsl
unity/TriSync/Shaders/BlenderSyncShadowCasterPass.hlsl
unity/TriSync/Shaders/BlenderSyncUniversal2DPass.hlsl
unity/TriSync/Shaders/TriSync_PrincipledLit_URP.shader
unity/TriSync/Scripts/Editor/BlenderSyncPrincipledLitShaderGUI.cs
```

Upstream package:

```text
com.unity.render-pipelines.universal 17.3.0
Copyright (c) 2020 Unity Technologies ApS
```

These modified files are distributed for Unity-dependent projects under the
Unity Companion License. They are not relicensed as MIT, GPL, or exclusively
under another project-authored license.

Package notice and license link:

```text
LICENSES/THIRD_PARTY/Unity-Render-Pipelines-LICENSE.md
https://unity.com/legal/licenses/unity-companion-license
```

Other TriSync Unity code calls public Unity Editor and runtime APIs. Such
API use alone is not a redistribution of Unity reference source.

## Rust Native Helper Dependencies

The native helper crate is located at:

```text
blender_addon/blendersync_vnext/native/blendersync_native
```

Its direct dependencies are `bytemuck`, `pyo3`, and `sha1`. The compiled
helper also incorporates their transitive dependencies. Exact versions and
license expressions come from `Cargo.lock` and `cargo metadata`.

A deterministic aggregate containing every locked dependency, the selected
permitted license branch, and the corresponding upstream license text is at:

```text
LICENSES/THIRD_PARTY/Rust-dependency-licenses.txt
```

Regenerate or verify it with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Update-RustThirdPartyLicenses.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Update-RustThirdPartyLicenses.ps1 -Check
```

For dependencies offering `MIT OR Apache-2.0`, the aggregate records the MIT
license branch. `target-lexicon` is recorded under Apache-2.0 with the LLVM
exception. `unicode-ident` is recorded under MIT together with Unicode-3.0.
