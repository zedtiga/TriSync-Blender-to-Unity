# TriSync Release Checklist

This checklist is for stable release preparation.

## Supported Target

- Blender `4.2.x` through `5.2.x`
- Unity `6000.0.x` through `6000.5.x`
- Universal Render Pipeline `17.0.x` through `17.5.x`

Unity and URP minors are released as matching `6000.n` / `17.n` pairs. Versions
outside these ranges require a new compatibility promotion gate.

## Compatibility Gate

`tools/compatibility-matrix.json` defines every supported Blender and Unity/URP
minor profile. Before publishing a release that claims the complete range,
configure every profile and run the strict matrix without `-AllowMissing`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-CompatibilityMatrix.ps1
```

Every perimeter endpoint pair listed in `COMPATIBILITY.md` must also complete
the Level 1 smoke pass in `TESTING.md`, together with the current-current
interior baseline. A future version is added to the support statement only after
the same endpoint, interoperability, metadata, and documentation gates pass.

## Scope Freeze

During stabilization:

- Do not add new features unless they fix a clear release blocker.
- Accept confirmed bug fixes, documentation corrections, validation/tooling
  fixes, and low-risk cleanup.
- Defer speculative UI, protocol, or workflow expansion until after the stable
  handoff.

## Automated Validation

Run from the repository root with explicit paths to your dedicated Unity test
project and Blender add-on installation. Adjust the Blender version in this
example to your installation:

```powershell
$unityProjectRoot = 'C:\Projects\TriSync-Test'
$blenderAddonRoot = Join-Path $env:APPDATA 'Blender Foundation\Blender\5.0\scripts\addons\blendersync_vnext'
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-Project.ps1 -UnityProjectRoot $unityProjectRoot -BlenderRuntimeRoot $blenderAddonRoot -SyncBlenderAddon -RemoveStaleBlenderAddonFiles -RemoveStaleUnityRuntimeFiles
```

Expected:

- Process argument escaping and child-process timeout helper tests pass.
- Compatibility matrix definition validation passes.
- Blender Python validation passes.
- Blender runtime mirror has no stale files or mirror diffs.
- Protocol type check passes.
- Rust native `cargo check` passes.
- Unity Player assembly graph excludes all TriSync C# assemblies.
- Unity runtime mirror has no stale files or mirror diffs.
- Unity C# compile has 0 errors and no unexplained warnings.
- Product/native/protocol metadata is consistent.
- Two package builds produce identical archives and valid SHA256 manifests.

Also run:

```powershell
git diff --check
git status --short
```

Expected:

- `git diff --check` has no output.
- `git status --short` only shows intentional release/documentation edits.

## Manual Smoke Pass

Run the Level 1 checks in `docs/TESTING.md`.

Minimum release smoke coverage:

- English and Simplified Chinese UI switching on both endpoints, including
  Unity's user-level `System` fallback behavior.
- Session connect/reconnect.
- Ordinary mesh import.
- Same-name asset identity.
- Material and texture update.
- Object transform/state sync.
- Edit Mode mesh preview and commit.
- Manual UV preview.
- Shared mesh copy-on-write.
- Shape key add/remove/update and weight sync.
- Rigged object import/update.
- Animation clip import.
- Registry tab unregister.
- Report/diagnostic visibility after success and failure cases.

## Focused High-Risk Pass

Run focused tests when the latest changes touched:

- Protocol DTOs or routing.
- Generated asset naming.
- Runtime sync scripts.
- Mesh binary/native builder.
- Shape keys / BlendShapes.
- Rigged objects or rig axis mode.
- Animation clip export/import.
- Registry behavior.
- Humanoid/root-motion tools.

## Source And Runtime Cleanup

Before final commit/tag:

- Source tree has no generated diagnostics, dump files, caches, or temporary
  payload files.
- Unity runtime copy matches source after stale cleanup.
- Blender runtime add-on matches source after stale cleanup.
- No stale `.meta` files exist for deleted Unity source files.
- `Assets/TriSync/DebugSnapshots` and other diagnostic dump folders are not
  included in source.
- Python `__pycache__`, Rust `target`, Unity `Temp`, and generated runtime
  state remain ignored by Git.

Useful checks:

```powershell
git status --short
rg -n "TODO|HACK|temporary|debug probe|probe only" README.md docs blender_addon unity tools
```

Review matches manually. Do not remove useful diagnostics solely because they
contain words such as `diagnostic` or `probe`.

## Documentation Gate

Confirm:

- `README.md` describes current stable workflows.
- `docs/ARCHITECTURE.md` matches current module responsibilities.
- `docs/PROTOCOL.md` matches active message behavior.
- `docs/TESTING.md` matches current UI and feature behavior.
- `docs/HUMANOID.md` clearly states Humanoid limitations.
- `docs/RIG_AXIS.md` clearly states default vs preserved axis behavior.
- Removed features are not presented as active workflows.

## Version Gate

`VERSION` is the product-version source of truth. Before tagging:

- Blender `bl_info["version"]` matches the numeric product core version.
- The native helper reports Cargo's `CARGO_PKG_VERSION`; native capabilities
  are exposed separately and are not appended to the version string.
- Blender and Unity session protocol integers match.
- The AssetBridge contract uses the stable `asset-bridge-v1` identifier.
- Protocol and contract identifiers are not changed solely for a product tag.

## License Gate

Run the structural license validation during normal development:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-LicenseBundle.ps1
```

Before any public package is built, run the release-ready mode:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-LicenseBundle.ps1 -RequireReleaseReady
```

Release-ready mode verifies all of the following:

- Project-authored Unity companion Editor code is declared MIT licensed.
- `LICENSES/MIT.txt` contains the TriSync contributors copyright notice and
  standard MIT grant.
- The retired Unity commercial license file and references are
  absent.
- `THIRD_PARTY_NOTICES.md` contains no active release-blocker status.
- The Rust dependency license aggregate matches the locked crate versions.
- Every URP-derived file remains listed under the Unity Companion License.
- The Unity product source contains no Unity Reference Only provenance marker.

Preview packaging runs the structural license gate and records
`preview-structural` in its manifest. Stable product versions automatically
invoke release-ready mode. Every bundle includes `LICENSE.md`,
`THIRD_PARTY_NOTICES.md`, and the complete `LICENSES` directory.

## Superhive Source-Only Submission

Superhive Blender submissions use the dedicated source-only builder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Build-SuperhiveBlenderPackage.ps1 -OutputDirectory .\tmp\superhive -SourceCommit v1.0.0
```

The resulting `TriSync-Blender-<version>-source-only.zip` contains the complete
Python add-on only. It excludes compiled modules, native build source, build
caches, and native acquisition instructions, so the submission presents the
plugin functionality without exposing a repository or alternate download path.
Run the dedicated package test before uploading:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-SuperhiveBlenderPackage.ps1
```

Keep this channel-specific package separate from the full GitHub distribution.
GitHub releases use the complete bundle and both standalone installation ZIPs.

## Cross-Platform Native Artifacts

The native build workflow is intentionally manual: run
`.github/workflows/build-native.yml` from the GitHub Actions page when a new
platform artifact is needed. It builds Windows x86_64, Linux x86_64, and both
macOS arm64 and x86_64 targets. Each job imports the resulting PyO3 module and
checks its Cargo-derived version and capability list before uploading. The
macOS job imports only the runner-native artifact and separately verifies the
Mach-O architecture of both outputs.

Download the workflow artifacts and merge their platform directories under
`blender_addon/blendersync_vnext/native/artifacts/`. The expected layout is:

```text
native/artifacts/<platform>/cpython-311/blendersync_native.pyd  # Windows
native/artifacts/<platform>/cpython-311/blendersync_native.so  # Linux/macOS
```

The macOS download contains two platform directories. Preserve all directory
names and binary extensions. Review and commit all four binaries before the
release package is built, then run the package structure/checksum tests and
confirm the builder reports `nativeArtifacts=4/4`.
Missing platform artifacts remain a warning in preview packaging, but a stable
package should be built only after the intended artifact set is present. A
workflow import smoke is not a substitute for a Blender endpoint matrix or
interoperability validation on that platform.

## Package Build

The packaging license gate requires Cargo and the locked Rust dependencies in
the local cache. Follow [Build from source](../README.md#build-from-source) to
install the prerequisites and fetch dependencies before the first package build.

Build from a clean worktree:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Build-ReleasePackage.ps1 -OutputDirectory .\tmp\release
```

The builder only reads source metadata and tracked product files. It does not
modify source. Metadata mismatch, untracked release inputs, license-gate
failure, or an invalid package structure fails the build. `-AllowDirty` exists
only for local smoke packages; such packages record `source.dirty = true`.

Outputs include the complete bundle, standalone Blender and Unity archives, a
JSON manifest with product/native/protocol versions, the four native platform
artifact slots, and every bundled payload file's SHA256, plus
`SHA256SUMS.txt`. Missing native artifacts are reported as package warnings
while their manifest slots remain explicitly unavailable. The manifest cannot
hash itself, so the external checksum list covers the manifest and all three
archives. Archive entry order and timestamps are fixed so repeated builds from
the same source are byte-for-byte reproducible.

## Tagging

Commit and push the release-preparation changes first. Then build from that
clean commit, install the generated artifacts for the manual smoke pass, and
tag the exact commit that produced them:

```powershell
$version = (Get-Content -LiteralPath .\VERSION -Raw).Trim()
git status --short
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Build-ReleasePackage.ps1 -OutputDirectory .\tmp\release
git tag "v$version"
git push origin "v$version"
```

`git status --short` must produce no output before packaging and tagging.

Use a new version tag when the stable baseline changes.

## GitHub Release Assets

Create a release for the tested tag and attach these outputs from the same clean
commit:

- `TriSync-<version>.zip`: the complete bundle, recommended for first installation.
- `TriSync-Blender-<version>.zip`: the standalone Blender add-on.
- `TriSync-Unity-<version>.zip`: the standalone Unity companion.
- `TriSync-<version>-manifest.json`: bundled file hashes and source commit.
- `TriSync-<version>-SHA256SUMS.txt`: archive and manifest checksums.

Include the quick-start link, supported version range, platform qualification,
and component-license boundaries in the release description. Verify that the
release and its assets can be read without authentication after publishing.

## Release Record Template

```text
Date:
Unity version:
Blender version:
Repository commit:
Branch:
Runtime sync command:
Native module available:

Automated validation:
Smoke tests run:
Focused sections run:
Failures:
Workarounds:
Follow-up issues:
Release result:
```
