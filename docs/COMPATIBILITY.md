# Compatibility Matrix

This document records the supported range, the endpoint versions used
to qualify it, and the interoperability coverage used for release decisions.
Future profiles do not expand the support statement until the promotion gate at
the end of this document passes.

## Supported Range

The current public support statement is:

| Product | Supported versions |
| --- | --- |
| Blender | `4.2.x` through `5.2.x` |
| Unity | `6000.0.x` through `6000.5.x` |
| Universal Render Pipeline | `17.0.x` through `17.5.x` |

Unity and URP are supported as matching minor pairs: Unity `6000.n` uses URP
`17.n`. The Blender add-on metadata declares Blender `4.2.0` as its minimum.
The upper bounds are explicit; future Blender, Unity, or URP minors are not
implicitly supported by these ranges.

## Verified Version Matrix

The source of truth is `tools/compatibility-matrix.json`.

| Profile | Role | Version pair | Status |
| --- | --- | --- | --- |
| `blender-4.2` | Minimum-version floor / LTS anchor | Blender `4.2.x` | Endpoint verified |
| `blender-4.3` | Minor-version coverage | Blender `4.3.x` | Endpoint verified |
| `blender-4.4` | Minor-version coverage | Blender `4.4.x` | Endpoint verified |
| `blender-4.5` | Late 4.x / LTS anchor | Blender `4.5.x` | Endpoint verified |
| `blender-5.0` | Current Blender baseline | Blender `5.0.x` | Current baseline |
| `blender-5.1` | Newer minor coverage | Blender `5.1.x` | Endpoint verified |
| `blender-5.2` | Newer LTS coverage | Blender `5.2.x` | Endpoint verified |
| `unity-6000.0` | Unity 6 minimum/LTS anchor | Unity `6000.0.x` + URP `17.0.x` | Endpoint verified |
| `unity-6000.1` | Minor-version coverage | Unity `6000.1.x` + URP `17.1.x` | Endpoint verified |
| `unity-6000.2` | Minor-version coverage | Unity `6000.2.x` + URP `17.2.x` | Endpoint verified |
| `unity-6000.3` | Current Unity baseline | Unity `6000.3.x` + URP `17.3.x` | Current baseline |
| `unity-6000.4` | Newer minor coverage | Unity `6000.4.x` + URP `17.4.x` | Endpoint verified |
| `unity-6000.5` | Newer minor coverage | Unity `6000.5.x` + URP `17.5.x` | Endpoint verified |

Blender `4.2` is the support floor because it is an LTS release and bundles
Python 3.11, matching the tracked `pyo3` `abi3-py311` native extension. Blender
`4.0` and `4.1` bundle Python 3.10 and therefore cannot load that artifact;
they are outside the supported range rather than untested profiles.

The Blender endpoint matrix was run on Windows on `2026-07-26`:

| Profile | Tested Blender | Python | Action storage | Native helper |
| --- | --- | --- | --- | --- |
| `blender-4.2` | `4.2.23` | `3.11.7` | Legacy | Available |
| `blender-4.3` | `4.3.2` | `3.11.9` | Legacy | Available |
| `blender-4.4` | `4.4.3` | `3.11.11` | Layered | Available |
| `blender-4.5` | `4.5.6` | `3.11.11` | Layered | Available |
| `blender-5.0` | `5.0.1` | `3.11.13` | Layered | Available |
| `blender-5.1` | `5.1.2` | `3.13.9` | Layered | Available via `abi3-py311` fallback |
| `blender-5.2` | `5.2.0` | `3.13.13` | Layered | Available via `abi3-py311` fallback |

The Unity endpoint matrix was run on Windows on `2026-07-27`:

| Profile | Tested Unity | URP | EditMode |
| --- | --- | --- | --- |
| `unity-6000.0` | `6000.0.80f1` | `17.0.4` | `116/116` passed |
| `unity-6000.1` | `6000.1.17f1` | `17.1.0` | `116/116` passed |
| `unity-6000.2` | `6000.2.15f1` | `17.2.0` | `116/116` passed |
| `unity-6000.3` | `6000.3.11f1` | `17.3.0` | `116/116` passed |
| `unity-6000.4` | `6000.4.12f1` | `17.4.0` | `116/116` passed |
| `unity-6000.5` | `6000.5.5f1` | `17.5.0` | `116/116` passed |

Unity `6000.0` uses the public animation-stream fallback because its
`HumanPose` API does not expose IK goal arrays. Unity `6000.4` and newer use
`EntityId` and the replacement unsorted object-query overload; earlier minors
retain the legacy object identity APIs.

Unity `6000.1` has a known Editor defect tracked as `UUM-85059`. It can emit a
`DontSaveInEditor` persistence assertion during ordinary editor operations,
including scene object creation and GUI repaint. The behavior can be reproduced
against the editor baseline without relying on TriSync, so it is not used as a
localization or interoperability failure unless TriSync changes the baseline
behavior. TriSync does not use a 6000.1-specific language or layout path.

The EditMode total includes an imported-shader error gate for
`TriSync/Principled Lit URP`. URP `17.0` uses its legacy fog and GBuffer
interfaces, while URP `17.1` and newer use the replacement interfaces.

`Endpoint verified` records a passing automated Blender or Unity profile.
Manual cross-endpoint coverage remains a separate result below; support for the
range does not mean every one of the 42 Cartesian combinations was run.

Every released Blender minor from `4.2` through `5.2` has its own endpoint
profile. Passing neighboring minors does not substitute for running that
profile. Future Blender minors are not implicitly covered by `5.0+`; add a new
candidate profile when a stable release is available.

Unity and URP are tested as a pair. Passing a Unity editor version with a
different URP major/minor does not satisfy that profile. Every listed Unity
minor requires a separate pinned project; the runner verifies the actual
manifest instead of inferring it from the editor version. Future Unity 6 minors
are added only after their intended URP fixture pair is defined.

The endpoint matrix uses a perimeter-plus-current interoperability strategy.
Every Blender profile runs against both the Unity floor and ceiling, every Unity
profile runs against both the Blender floor and ceiling, and the current
`blender-5.0` + `unity-6000.3` baseline remains an interior anchor. This covers
all version endpoints and both compatibility boundaries without requiring the
full Cartesian product.

The perimeter was manually exercised on Windows on `2026-08-02`. The smoke
covered ordinary meshes, materials, automatic transform and mesh updates,
BlendShapes, multiple UV channels, rigged objects, animation, and Geometry
Nodes. No product errors or functional regressions were observed.

| Blender / Unity | `6000.0` | `6000.1` | `6000.2` | `6000.3` | `6000.4` | `6000.5` |
| --- | --- | --- | --- | --- | --- | --- |
| `4.2` | Verified | Verified | Verified | Verified | Verified | Verified |
| `4.3` | Verified | - | - | - | - | Verified |
| `4.4` | Verified | - | - | - | - | Verified |
| `4.5` | Verified | - | - | - | - | Verified |
| `5.0` | Verified | - | - | Current baseline | - | Verified |
| `5.1` | Verified | - | - | - | - | Verified |
| `5.2` | Verified | Verified | Verified | Verified | Verified | Verified |

The 22 perimeter combinations are recorded as `verified` in
`tools/compatibility-matrix.json`; `current-current` retains
`current-baseline`. These pairs use the Level 1 workflow smoke and remain manual.
Independent endpoint tests do not replace cross-endpoint verification.

## Runner

Validate only the tracked matrix definition during normal repository checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-CompatibilityMatrix.ps1 -ValidateOnly
```

List every profile and its local configuration state:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-CompatibilityMatrix.ps1 -List
```

The list also prints all required manual interoperability pairs.

Run one configured profile:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-CompatibilityMatrix.ps1 -Profile unity-6000.3
```

Run every configured profile while explicitly skipping versions not installed
on the current machine:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-CompatibilityMatrix.ps1 -AllowMissing
```

Run the strict release matrix by omitting `-AllowMissing`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-CompatibilityMatrix.ps1
```

Strict mode fails for missing configuration, invalid paths, version/URP
mismatches, compilation failures, zero Unity tests, or failed smoke tests. An
`-AllowMissing` run still fails if it does not execute at least one profile.
The runner reports the required manual interop pair count but does not claim to
execute those interactive workflows.

Blender smoke processes time out after 300 seconds and Unity EditMode processes
after 600 seconds by default. Override those limits with
`-BlenderTimeoutSeconds` and `-UnityTimeoutSeconds` when a known slow fixture
needs more time. A timeout terminates only the child process started for that
profile and its descendants, then fails the profile with the root process ID.

## Local Configuration

Paths are supplied with process or machine environment variables so the tracked
matrix never contains workstation-specific locations.

| Profile | Required environment variables |
| --- | --- |
| `blender-4.2` | `BLENDERSYNC_BLENDER_42_EXE` |
| `blender-4.3` | `BLENDERSYNC_BLENDER_43_EXE` |
| `blender-4.4` | `BLENDERSYNC_BLENDER_44_EXE` |
| `blender-4.5` | `BLENDERSYNC_BLENDER_45_EXE` |
| `blender-5.0` | `BLENDERSYNC_BLENDER_50_EXE` |
| `blender-5.1` | `BLENDERSYNC_BLENDER_51_EXE` |
| `blender-5.2` | `BLENDERSYNC_BLENDER_52_EXE` |
| `unity-6000.0` | `BLENDERSYNC_UNITY_6000_0_EDITOR`, `BLENDERSYNC_UNITY_6000_0_PROJECT` |
| `unity-6000.1` | `BLENDERSYNC_UNITY_6000_1_EDITOR`, `BLENDERSYNC_UNITY_6000_1_PROJECT` |
| `unity-6000.2` | `BLENDERSYNC_UNITY_6000_2_EDITOR`, `BLENDERSYNC_UNITY_6000_2_PROJECT` |
| `unity-6000.3` | `BLENDERSYNC_UNITY_6000_3_EDITOR`, `BLENDERSYNC_UNITY_6000_3_PROJECT` |
| `unity-6000.4` | `BLENDERSYNC_UNITY_6000_4_EDITOR`, `BLENDERSYNC_UNITY_6000_4_PROJECT` |
| `unity-6000.5` | `BLENDERSYNC_UNITY_6000_5_EDITOR`, `BLENDERSYNC_UNITY_6000_5_PROJECT` |

Each Unity profile requires a separate minimal test project pinned to the
declared editor and URP versions. Do not point two profiles at one project or
allow a newer editor to upgrade an older fixture project.

For local manual interoperability work, the configured Unity projects may be
normal projects that are convenient to open and inspect; they do not need to
live under a particular fixture directory. Keep one project per Unity minor
and pin its editor and URP versions as declared by the matrix.

Synchronize every configured manual workspace from the repository before
starting an interoperability run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Sync-CompatibilityWorkspaces.ps1
```

For each Blender executable, this mirrors the add-on into the application's
version-local `scripts/addons/blendersync_vnext` directory. Because Blender's
third-party add-on discovery uses the version-specific user scripts directory,
the command places a directory junction there that targets the application-local
copy. An existing standalone copy is moved outside the scanned add-on directory
to `blendersync-managed-backups` before the junction is created. The command then
uses that exact Blender executable to persist and verify the enabled preference.
Normal manual launches through `blender.exe` therefore load the synchronized
application-local code.

`BlenderSync Test.cmd` remains beside `blender.exe` as an explicit launcher that
also requests the add-on on the command line, but it no longer selects a separate
test profile. This avoids a manual Blender session silently loading a stale
add-on from a hidden fixture or an unmanaged user add-on copy.

The same command synchronizes `Assets/TriSync` in every configured
Unity project. The workspace command prefers persistent user environment
variables so a long-running shell cannot silently reuse older fixture paths;
pass `-PreferProcessEnvironment` for an intentional one-off override. Use
`-VerifyOnly` to perform mirror checks without copying source.

Per-profile logs and results are written under
`tmp/compatibility-matrix/<profile-id>/` by default. Override the root with
`-ArtifactRoot` when CI needs a dedicated artifact directory. Blender profiles
retain `blender-smoke.json`, `blender-smoke.stdout.log`, and
`blender-smoke.stderr.log`; failed runs print the captured log tails before the
profile failure summary.

## Version-Specific Checks

Each Blender profile starts the real Blender executable with factory settings
and runs `tests/blender/compatibility_smoke.py`. The smoke verifies:

- reported Blender major/minor;
- add-on registration and unregistration;
- Scene property and panel registration;
- Mesh, UV, and color attribute APIs;
- Geometry Nodes interface and Realize Instances node creation;
- evaluated mesh acquisition and cleanup;
- legacy or layered Action FCurve traversal through the product iterator;
- verbose logging facade buffering and Trace generation;
- native helper availability as a non-blocking probe.

The native probe also records the resolved platform artifact tag in each smoke
result (`win_amd64`, `linux_x86_64`, `macos_arm64`, or `macos_x86_64`).

Blender background sessions enable verbose facade logging automatically so
matrix and batch-run artifacts retain diagnostic context. Set
`BLENDERSYNC_VERBOSE_LOGS=0` to suppress it explicitly for another background
workflow.

The runner compares the smoke result's complete `checks` set with the profile
declaration. Missing, unexpected, duplicate, or empty check names fail the
profile even when the smoke reports `passed`.

Each Unity profile verifies the project editor version and paired URP version,
synchronizes the public source into the fixture project, compiles the product
assemblies, and runs the complete EditMode suite with isolated result files.

The matrix runner is the version-specific entry point. `Test-Project.ps1` only
validates its tracked definition because normal development machines are not
required to install the full version matrix.

## Future Support Promotion Gate

A future candidate profile can become part of the public support statement only
after:

1. its automated matrix profile passes on a clean fixture;
2. its required perimeter interoperability combinations pass the Level 1
   workflow smoke in `TESTING.md`;
3. native and Python-fallback behavior are characterized separately;
4. known limitations are documented;
5. `bl_info`, QUICK_START, TESTING, and RELEASE are updated together.

For a release that claims the complete tracked range, every endpoint profile
and every listed interoperability pair must pass. One passing patch release is
used to qualify its `major.minor.x` line; a later patch is rerun when its release
notes touch Python, RNA, depsgraph, Geometry Nodes, or extension ABI behavior.

## Native Platform Status

Version compatibility and platform compatibility are separate claims. The
native loader selects a platform directory from the host OS and architecture,
then applies the same current-interpreter-tag to `cpython-311` `abi3` fallback
order used on Windows.

| Host target | Artifact directory | Module file | Repository status |
| --- | --- | --- | --- |
| Windows x86_64 | `win_amd64/cpython-311` | `blendersync_native.pyd` | Built and verified by the Windows endpoint matrix |
| Linux x86_64 | `linux_x86_64/cpython-311` | `blendersync_native.so` | CI-built and import-smoked; Blender matrix pending |
| macOS arm64 | `macos_arm64/cpython-311` | `blendersync_native.so` | CI-built and import-smoked on the current arm64 runner; Blender matrix pending |
| macOS x86_64 | `macos_x86_64/cpython-311` | `blendersync_native.so` | CI-built and Mach-O architecture-checked; x86_64 import smoke and Blender matrix pending |

Release manifests enumerate all four target slots. Any missing artifact
produces a packaging warning and remains explicitly unavailable; it is not
silently represented as supported. When a native artifact is absent or cannot
be loaded, the Python implementation remains the functional fallback.

All four artifact slots are populated. The public support statement still
claims the Windows validation only. Complete Linux or macOS support requires a
native import smoke and Blender endpoint/interoperability matrix on each target
architecture. A successful cross-platform build alone does not expand the
support statement.

The manual GitHub Actions workflow `.github/workflows/build-native.yml` is the
tracked build path for the three non-Windows artifact slots and the Windows
rebuild. It runs an import smoke against the runner's artifact before
upload. Downloaded artifacts retain the `win_amd64`, `linux_x86_64`,
`macos_arm64`, or `macos_x86_64` directory and should be merged below
`blender_addon/blendersync_vnext/native/artifacts/`; the macOS download contains
both macOS directories. This workflow is deliberately separate from the
compatibility matrix, which must still be run on the target host before a
platform is described as verified.
