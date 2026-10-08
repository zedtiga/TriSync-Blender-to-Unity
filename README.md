# TriSync — Blender to Unity

**Live sync between Blender and Unity.**

TriSync helps you bring Blender meshes, materials, rigs, and animations into
Unity, then preview and update your work as you iterate. It includes a Blender
add-on and a Unity Editor companion, with English and Simplified Chinese UI.

Source code and installation packages are available free of charge.

[Download](https://github.com/zedtiga/TriSync-Blender-to-Unity/releases/latest) ·
[Quick start](docs/QUICK_START.md) ·
[Documentation](docs/README.md) ·
[Report an issue](https://github.com/zedtiga/TriSync-Blender-to-Unity/issues)

## What You Can Do

- Import meshes and generate Unity scene objects, meshes, materials, and textures.
- Sync object transforms, hierarchy, and object state with Auto Sync.
- Preview Edit Mode geometry changes, then commit them when leaving Edit Mode.
- Preview and transfer UV0–UV7, vertex colors, normals, and mesh tangents.
- Sync Shape Key weights and update Unity BlendShape definitions.
- Import rigged meshes and create AnimationClips from object, armature,
  Shape Key, or combined animation sources.
- Use Humanoid Avatar, animation conversion, and Root Motion tools.
- Inspect and unregister managed objects and resources in Unity.
- Extract a Unity mesh into a new Blender object with the one-shot
  Unity-to-Blender import tool.

## Download And Install

1. Open [Releases](https://github.com/zedtiga/TriSync-Blender-to-Unity/releases/latest)
   and download the complete bundle, `TriSync-1.0.0.zip`.
2. Extract the bundle. In Blender, use
   `Edit > Preferences > Add-ons > Install from Disk` to install
   `blender/TriSync-Blender-1.0.0.zip`, then enable **TriSync**.
3. Copy the extracted `unity/TriSync` folder into your Unity project's
   `Assets` folder. Wait for Unity to finish compiling.
4. Open `TriSync/Open TriSync` in Unity and the **TriSync** sidebar in Blender.
   Follow the [first connection and import steps](docs/QUICK_START.md#first-connection).

Standalone Blender and Unity ZIPs are also provided. The complete bundle includes
the documentation and license notices. The release's SHA256 checksum list
covers all three ZIPs and the package manifest.

GitHub's automatically generated **Source code** archives contain the repository
source. For installation, choose the named TriSync release assets above.

## Supported Environment

| Component | Supported versions |
| --- | --- |
| Blender | `4.2.x` through `5.2.x` |
| Unity | `6000.0.x` through `6000.5.x` |
| Universal Render Pipeline | `17.0.x` through `17.5.x` |

Use matching Unity and URP minors: Unity `6000.n` with URP `17.n`.

Windows x86_64 has the full recorded endpoint and interoperability coverage.
Linux x86_64 and macOS native helpers are included, but their CI build/import
checks do not establish complete product support on those platforms.
See [Compatibility](docs/COMPATIBILITY.md) for the tested versions and coverage.

## Workflow Boundaries

- Material edits use the material update workflow; a mesh update does not
  necessarily refresh material content.
- Meshes with Shape Keys use the original mesh. Evaluated modifier results are
  omitted to preserve vertex order and BlendShape mapping.
- For Geometry Nodes, placing `Realize Instances` after instance-producing
  nodes makes exported topology more predictable. TriSync can also realize
  remaining instances on a temporary export copy.
- Humanoid reuse expects a clean Unity-style T pose, orientation, and
  proportions. See [Humanoid](docs/HUMANOID.md) and [rig axes](docs/RIG_AXIS.md).
- Animation imports create new clips; they do not overwrite registry-managed
  clips automatically.
- Unity-to-Blender mesh extraction creates a new mesh/object. It is a one-shot
  import, and does not provide continuous reverse synchronization.

## Build From Source

The release packaging workflow uses Windows PowerShell, Git,
[uv](https://docs.astral.sh/uv/) with Python 3.11 or newer, and a stable
[Rust toolchain](https://rustup.rs/). Cargo verifies the bundled dependency
licenses against the lockfile. Fetch the locked dependencies once before the
offline license check. The checked-in native helpers are included in the
package, so rebuilding them is optional.

```powershell
git clone https://github.com/zedtiga/TriSync-Blender-to-Unity.git
Set-Location TriSync-Blender-to-Unity
cargo fetch --locked --manifest-path .\blender_addon\blendersync_vnext\native\blendersync_native\Cargo.toml
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Build-ReleasePackage.ps1 -OutputDirectory .\tmp\release
```

Build release packages from a clean Git checkout. Generated packages appear in
`tmp/release`. See [Contributing](CONTRIBUTING.md) for local testing and
[Release preparation](docs/RELEASE.md) for the full release checks.

## Documentation And Feedback

- [Quick start](docs/QUICK_START.md): installation, connection, first import,
  updates, and troubleshooting.
- [Architecture](docs/ARCHITECTURE.md): source layout and module boundaries.
- [Asset identity](docs/ASSET_IDENTITY.md): generated resources and registries.
- [Protocol](docs/PROTOCOL.md): Blender/Unity message contracts.
- [Testing](docs/TESTING.md): automated and manual validation.
- [Contributing](CONTRIBUTING.md): development setup and focused contributions.

When [reporting an issue](https://github.com/zedtiga/TriSync-Blender-to-Unity/issues),
include your TriSync, Blender, Unity, and URP versions, operating system,
reproduction steps, and relevant diagnostics. A small example project is useful
when the problem depends on mesh, rig, or animation data.

## Licensing

TriSync has component-specific licenses:

| Component | License |
| --- | --- |
| Project-authored Blender add-on and Rust native helper | GPL-3.0-or-later |
| Project-authored Unity companion, except the derived files below | MIT |
| URP-derived Shader/HLSL and Shader GUI files | Unity Companion License |
| Protocol specification | MIT |
| Project-authored documentation, tools, CI, and Unity tests | MIT |

See [LICENSE.md](LICENSE.md) for the complete scope, including documentation and
development files, and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for
attribution. The Unity-derived files are excluded from the MIT and GPL grants.

TriSync is an independent project. It is not affiliated with, endorsed by, or
sponsored by Blender Foundation or Unity Technologies. Blender and Unity are
referenced descriptively; this project does not use their logos.
