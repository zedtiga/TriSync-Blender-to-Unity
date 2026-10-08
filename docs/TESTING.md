# TriSync Testing

This document is the current validation plan for TriSync.

## Supported Test Environment

The current public support range is:

- Blender `4.2.x` through `5.2.x`
- Unity `6000.0.x` through `6000.5.x`
- Universal Render Pipeline `17.0.x` through `17.5.x`

Unity `6000.n` is tested with URP `17.n`; arbitrary cross-minor Unity/URP pairs
are not implied. Blender `5.0.x`, Unity `6000.3.x`, and URP `17.3.x` remain the
current day-to-day baseline, while every supported minor has an automated
endpoint profile and the required perimeter interoperability smoke coverage.
The local Python environment is managed with `uv`.

## Test Levels

```text
Level 0: automated repository validation
Level 1: smoke regression
Level 2: focused/full functional pass
Level 3: pure logic unit tests
```

Level 3 covers the current pure-logic and state-machine regression surface.
Editor API and end-to-end asset workflows still require the focused smoke tests
below.

## Level 0: Automated Validation

The `Validate source` GitHub Actions workflow runs the Python regression suite,
release metadata validation, Windows native import smoke, protocol checks, and
Unity player assembly boundary checks for pushes to `main` and pull requests.
These source checks do not replace Blender/Unity Editor or interoperability
testing.

Run from the repository root after setting the paths for your dedicated Unity
test project and installed Blender version. The examples below use Blender 5.0;
adjust the add-on path to your installation. Source-only checks that do not need
an Editor installation are listed in [Contributing](../CONTRIBUTING.md).

```powershell
$unityProjectRoot = 'C:\Projects\TriSync-Test'
$blenderAddonRoot = Join-Path $env:APPDATA 'Blender Foundation\Blender\5.0\scripts\addons\blendersync_vnext'
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-Project.ps1 -UnityProjectRoot $unityProjectRoot -BlenderRuntimeRoot $blenderAddonRoot -SyncBlenderAddon -RemoveStaleBlenderAddonFiles -RemoveStaleUnityRuntimeFiles
```

Expected:

- Windows process argument escaping, redirected log capture, and process-tree
  timeout helper tests pass.
- Compatibility matrix definition validation passes without requiring external
  editor installations.
- Blender Python syntax validation passes.
- Blender Python unit tests pass.
- Blender add-on runtime mirror has `stale_files=0` and `mirror_diffs=0`.
- Protocol type consistency validation passes.
- Logging policy validation matches the checked-in direct-console debt exactly;
  new `print()` or `Debug.Log*` calls outside the two logging facades fail.
- Rust native crate validation passes.
- Product, Blender add-on, native component, session protocol, and AssetBridge
  contract metadata remain internally consistent.
- Release packaging is reproducible, contains only tracked product files, and
  verifies its package manifest and SHA256 checksums.
- Unity Player assembly graph contains no TriSync C# assemblies.
- Unity runtime package mirror has `stale_files=0` and `mirror_diffs=0`.
- Unity C# compile has 0 errors.

Useful focused commands:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-BlenderPython.ps1 -VerboseFiles
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-BlenderUnit.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-CompatibilityMatrix.ps1 -List
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-UnityPlayerBoundary.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-UnityEditMode.ps1 -UnityProjectRoot $unityProjectRoot
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-ProtocolTypes.ps1 -VerboseTypes
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-LoggingPolicy.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-ReleasePackage.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Sync-UnityRuntime.ps1 -UnityProjectRoot $unityProjectRoot -VerboseFiles
```

### Cross-Platform Native Build

Native artifacts for the four tracked host targets are built by the manual
GitHub Actions workflow `.github/workflows/build-native.yml`. Trigger **Build
native artifacts** from the Actions page; it has no push, pull request, or
scheduled trigger. The Windows and Linux jobs upload one platform directory
each. The macOS job uploads both `macos_arm64` and `macos_x86_64` directories.
Every job runs `tools/native_import_smoke.py` before upload and checks the Cargo
native version and capability list against repository metadata. The macOS job
imports the runner-native architecture and uses `file` to verify both produced
Mach-O architectures; the non-native architecture still requires a matching
host import smoke before it can be described as load-verified.

After downloading the artifacts, merge the contents into
`blender_addon/blendersync_vnext/native/artifacts/`, preserving the platform and
`cpython-311` directories. Do not rename the module files or flatten the
directory tree. Verify the staged files with:

```powershell
uv run python tools/native_import_smoke.py --repo-root . --module-root .\blender_addon\blendersync_vnext\native\artifacts\win_amd64\cpython-311
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Test-ReleasePackage.ps1
```

Use the corresponding `linux_x86_64`, `macos_arm64`, or `macos_x86_64`
directory when running the smoke on those hosts. Once all four downloaded
files are reviewed, tracked, and committed, release packaging should report
`nativeArtifacts=4/4` without missing-artifact warnings.

Run the Blender matrix on the corresponding host after staging. A successful
Actions build and import smoke prove that the artifact loads on its runner;
they do not replace a Blender endpoint or interoperability run and do not
alone expand the public platform support statement.

Unity EditMode tests require the Unity test project to be closed. They can also
be included in the aggregate validation with
`Test-Project.ps1 -RunUnityEditModeTests`. Test sources live under `tests/unity`
and are deployed to a separate `Assets/BlenderSyncVNextTests` folder, so they
are not part of the public Unity companion package. The runner treats missing
or malformed result XML, a missing `test-run` root, zero discovered tests, and
any result other than `Passed` as validation failures.

`Test-CompatibilityMatrix.ps1` is the multi-version entry point. Normal
`Test-Project.ps1` runs only `-ValidateOnly`; it does not silently skip or claim
external editor versions. Configure paths through the environment variables in
`COMPATIBILITY.md`, use `-Profile <id>` for one version, `-AllowMissing` for an
explicit partial local run, and no skip switch for the strict full matrix.
Unity matrix runs write independent results under
`tmp/compatibility-matrix/<profile-id>/`.

`Test-UnityPlayerBoundary.ps1` requires every public companion C# source file
to belong to one of the two declared Editor-only TriSync assemblies. The Unity
EditMode suite also checks that the actual Player assembly graph contains no
TriSync C# assembly.

## Level 1: Smoke Regression

Run after normal feature edits and before stable handoff.

### Session

Steps:

1. Open your dedicated Unity test project with the matching URP version.
2. Start Unity bridge/session panel.
3. Connect from Blender.
4. Send one small payload.

Expected:

- Session reaches confirmed/ready state.
- No handshake mismatch, parse failure, or transport error appears.

Automated protocol negotiation coverage must include:

1. Same explicit protocol version with feature intersection.
2. Missing version accepted as legacy with no features.
3. Explicit version mismatch rejected with a user-visible error.
4. Versioned final/confirmed echo mismatch rejected, while legacy peers are
   exempt from echo validation.

### English And Simplified Chinese UI

Steps:

1. In Blender, enable interface translation under `Edit > Preferences >
   Interface > Translation`. Switch the global language between English and
   Simplified Chinese, reopening the TriSync N-panel after each change.
2. In each Blender language, inspect all TriSync workflow tabs, disclosures,
   tooltips, enum choices, operator labels, and one validation or completion
   report. Perform one connect/disconnect and one simple object operation.
3. In Unity, open `Edit > Preferences > TriSync`. Select English, then
   Simplified Chinese, reopening the TriSync and Animation Tools windows
   after each change.
4. In each Unity language, inspect all top-level tabs, Diagnostics disclosures,
   the Send To Blender workflow, all three Animation Tools views, the custom
   Principled Lit material inspector, tooltips, validation messages, and one
   confirmation dialog.
5. Select `System` in Unity, restart or repaint the editor as needed, and confirm
   it resolves to Simplified Chinese only on a Simplified Chinese system. On any
   other system language it resolves to English.
6. On every supported Unity minor, confirm selecting Simplified Chinese renders
   the same translated TriSync UI. On Unity 6000.1, record any
   `DontSaveInEditor` assertion separately and compare it with the same editor
   state before opening TriSync; Unity issue `UUM-85059` is a known editor
   defect and is not a TriSync localization failure when the baseline also
   reproduces it.
7. Copy Diagnostics on both endpoints and inspect recent log entries, protocol
   identifiers, filesystem paths, object/asset names, and structured field
   values.

Expected:

- Blender follows its global interface language immediately and does not expose
  a separate TriSync language selector.
- Unity's language preference is user-level, persists across projects and window
  reopenings, and does not modify project assets or scene files.
- Static and dynamic UI labels, tooltips, dialogs, validation text, Undo labels,
  and Blender operator reports use the active language. Missing catalog entries
  fall back to their stable English source text on every supported Unity minor.
- Logs, log event names/summaries, protocol identifiers, asset/object names,
  paths, and diagnostic field values remain untranslated.
- Unity's `TriSync/Open TriSync` and `TriSync/Open Animation Tools`
  menu paths remain stable English because `MenuItem` paths are compile-time
  constants; content inside the windows is localized.

### Blender Session Panel

Steps:

1. Open the TriSync N-panel while disconnected. Change Port from its
   default `8765`, match Unity to the same port, connect, and wait for the
   handshake to complete.
2. Observe the connection row during the three connection states. Reopen the
   panel and another `.blend` to confirm Port remains a user-level preference.
3. Toggle Auto View, then enable Auto Sync and run the full-width Import /
   Repair and Update actions without changing tabs.
4. Select a synced Mesh and verify Commit Preview becomes available; select no
   ready Mesh or disconnect and verify it becomes unavailable.
5. Use the Sync View button adjacent to Auto View once. In Performance, confirm
   the defaults are View Hz `10.0` and View Scale `0.8`. Set View Scale to
   `0.4` and confirm Unity zooms further in, then to `1.2` and confirm Unity
   zooms out. Repeat once in perspective and orthographic view, then reset to
   the default `0.8`.
6. Expand Object Options in the Obj tab and inspect the rig import controls.
7. Select one Mesh, then a second Mesh while keeping the first active.
8. Open Mats with and without an active material, both disconnected and after
   the handshake completes.
9. Import a rig, enter Pose Mode on its Armature, change a bone transform, and
   use Sync Pose to Unity. With Unity Animation recording enabled, repeat the
   same controller-driven pose on several frames: unchanged unit bone scales
   must remain exactly `1`, unchanged local positions must not drift, and an
   intentional position or scale change must still be recorded. Return to
   Object Mode. Use Sync Shape Keys to Unity on a rig with one or more bound
   skin meshes, and confirm only current BlendShape weights are sent. With
   Unity Animation recording enabled, repeat different facial weights on
   several frames and verify curves are created on each renderer child. Use
   Restore Imported Pose afterward and confirm it does not reset Shape Keys.
10. Open Anim, expand Clip Export, and verify Quality and Last Export are
    independent disclosures. Export once with an active animated object.
11. Open a `.blend` last saved with the legacy Sync tab selected.
12. With no supported object selected, inspect Import / Repair, Update, and
    Update State. Then select an unimported Mesh and inspect them again.
13. Produce or simulate a terminal Bulk Job result and dismiss it with the
    close icon.
14. Open Diag before and after an operation. Expand Recent Activity, then
    expand Active Object, Runtime, Performance, and Developer independently.
15. Click Copy Diagnostics, paste the clipboard into a text editor, and inspect
    the JSON snapshot.

Expected:

- The connection-row status is gray when disconnected, yellow while
  handshaking, and green after confirmation without enabling Debug.
- Expected offline `session_not_connected` state is quiet; actionable
  connection and protocol errors remain visible.
- Only the valid Connect or Disconnect action is drawn for the current state.
- Connection status and Port share the first row, followed by a full-width
  Connect or Disconnect action with the same row height as the primary controls.
- Port accepts `1` through `65535`, defaults to `8765`, persists in Blender's
  add-on preferences, and is disabled while listening, handshaking, or connected.
- Import / Repair, Update, and Update State are disabled while disconnected or
  when no suitable object is selected. An unimported supported object enables
  Import / Repair but keeps both update actions disabled until import succeeds.
- Precondition races and direct operator invocation report a warning without
  creating a persistent error Bulk Job. Actual transport or pipeline failures
  remain errors.
- Auto View, Auto Sync, and the three primary actions remain above the workflow
  tabs with consistent row heights; Import / Repair and Update each occupy a
  full-width row.
- Auto View uses the same pressed toggle language as Auto Sync, sits one row
  above it, and shares its row with the one-time Sync View action.
- Obj presents Update State followed by Reuse Active Mesh Asset without an
  Object Settings heading. Object Options contains the full-width Rig Import
  mode; legacy Mesh Source and evaluated-watch controls are not exposed.
- With Auto Sync enabled, modifier and Geometry Nodes updates use Blender's
  depsgraph events, evaluated-mesh debounce, and fingerprint suppression. Idle
  background ticks do not build or hash an evaluated mesh solely as a safety
  poll. Manual Import / Update actions remain independent.
- Reuse Active Mesh Asset is disabled until the active object and at least one
  additional selected object both have Mesh data.
- Mats shows the active material, a read-only shader/unique-texture summary,
  and Sync to Unity action. It does not build or inspect material payloads
  while drawing, and Sync to Unity is available only with an active material
  after handshake confirmation.
- Rig shows only the resolved Armature name and three vertically prioritized
  actions. Sync Pose to Unity is available only for an imported active
  Armature in Pose Mode; Sync Shape Keys to Unity is available in Object and
  Pose modes and sends no mesh/resource payload; Restore Imported Pose remains
  available in Object and Pose modes and changes only the Unity instance.
- Anim labels the optional name as Clip Name, uses Export to Unity for the
  Blender-to-Unity action, and keeps Quality collapsed independently. Last
  Export is absent before the first real report and collapsed when available;
  export requires an active object and confirmed handshake. Loop Animation is
  a checkbox that defaults on and can be disabled for one-shot clips. Quality
  exposes Sampling, Interpolation, Simplify, and Static Curves; Static Curves
  defaults to Off and Collapse Constant reduces complete constant Transform
  groups to one key without removing their bindings. An entirely static clip
  may retain one same-value duration endpoint on an existing curve so Unity's
  `AnimationClip.length` remains nonzero. Quaternion continuity remains an
  internal, enabled-by-default compatibility setting.
- The legacy Sync tab is not visible and normalizes to Obj without an enum
  warning or accidental remap to Diag.
- Running Bulk Jobs expose Cancel. Completed, cancelled, and failed jobs expose
  a close icon that returns the panel state to idle.
- Fast-path failures use a compact operation result rather than a Bulk Job
  diagnostic dump. True bulk jobs show a progress bar while running and a
  user-facing completed/skipped/failed summary when finished; internal mode,
  operation identifiers, elapsed time, and raw failure reasons remain in Diag.
- Diag keeps only Session health and Recent Activity visible by default. Active
  Object, Runtime, Performance, and Developer are independent disclosures.
  Developer shows only the verbose logging toggle, compact
  phase/protocol/endpoint state, negotiated features when present, and current
  WS errors. Trigger Monitor and raw handshake fields are not exposed, while
  normal baseline state is aggregated instead of listing every clean field.
- Performance distinguishes the Edit Preview maximum processing rate from the
  post-change Preview Send Delay; neither label implies unconditional sends.
  Its seven tuning values are Blender add-on preferences that follow the user and
  machine rather than being serialized into each `.blend` file. Legacy Scene
  values are ignored. Reset to Defaults restores only these seven values and does
  not change the session port or scene-level Auto Sync settings.
- Recent Activity merges bounded operation-report history with logging-facade
  entries, orders them by time, and shows at most the latest 5. Every entry can
  be expanded independently. The section is collapsed by default while its
  textual Clear action remains available when records exist. There is no
  separate Last Operation disclosure; Clear removes operation history,
  last-operation state, and diagnostic logs. Developer contains the user-level
  Verbose Logging setting.
- Disabling Blender Verbose Logging keeps Info and Warn under Recent Activity,
  sends Error to the console, and neither builds nor retains Trace. Enabling it
  also sends Info, Warn, and Trace to the console. Product logging has no
  remaining direct-print debt. Only the logging facade and
  `common/exception_boundary.py` may write
  directly to the console; the latter is an independent last-resort sink that
  cannot safely re-enter the facade when the facade itself fails. Blender
  background sessions enable verbose facade logging automatically for runner diagnostics; set
  `BLENDERSYNC_VERBOSE_LOGS=0` to disable it explicitly for another batch
  workflow.
- Copy Diagnostics uses `blendersync-diagnostics-v2` and writes sanitized JSON
  containing session, last-operation, runtime-version, timestamp, and at most
  50 bounded recent log entries. It excludes payload contents, the raw
  handshake ID, and local filesystem paths. The legacy Trigger Monitor is not
  registered or available.

### Unity TriSync Window

Steps:

1. Open `TriSync/Open TriSync` and switch among Session, Registry, and
   Diagnostics.
2. In Session while disconnected, change the port. Connect to Blender and
   observe the connecting and confirmed states. Confirm the status row contains
   the concise state context and Blender application version, while the
   connection action follows the disabled port on the secondary row.
3. Disconnect, confirm the port becomes editable again, then reproduce a
   protocol or transport error if available.
4. Change the Unity selection between unrelated objects while the Session tab is
   open. Confirm the panel does not inspect or display current-selection details
   during repaint. Click the list `+` and confirm it creates an empty object field
   without reading the current Selection.
5. Assign or drag a Mesh asset, MeshFilter GameObject, and SkinnedMeshRenderer
   GameObject into separate fields. Confirm invalid objects and duplicate shared
   Mesh references are rejected with a warning, the selected row can be removed
   with `-`, rows are numbered without source-type labels, and Send To Blender
   can be collapsed.
6. Expand Settings. Change Language, toggle Transform Smoothing, adjust
   Smoothing Time, and edit Follow Curve. Confirm the same values appear under
   `Edit > Preferences > TriSync`, then use Reset Smoothing.
7. With smoothing enabled, move an imported object continuously in Blender and
   confirm Unity follows according to the curve and reaches the exact final
   transform after the configured time. Disable smoothing and confirm the next
   automatic transform applies immediately. Re-enable it for the remaining
   checks.
8. While connected, enter Play Mode. Confirm the session disconnects before
   Play Mode starts, Connect is disabled, synchronized objects receive no
   updates or temporary components, and imported assets remain usable by the
   running game. Return to Edit Mode, reconnect, and confirm smoothing resumes.
9. Hover Create Mesh in Blender and confirm the concise import boundary tooltip
   is visible. Create a readable triangle Mesh and observe the `Creating...`,
   queued, and terminal imported/partial result states. Confirm list controls are
   disabled while in flight and a successful import clears the list.
10. Repeat with a mesh containing UV7 and a single-frame BlendShape. Confirm both
   arrive in Blender. A multi-frame BlendShape should be omitted and reported
   as a warning rather than silently treated as a complete import.
11. In Registry, switch between Resources and Objects, enable Auto Refresh, and
    use Refresh manually. Confirm Resources has Type and Search filters without a
    Mapped only checkbox, and that mapped and unmapped records remain visible.
    In Objects, use the Type filter to switch between Object and Rigged Object;
    expand a rigged card and inspect its prefab, managed instance, mesh, and
    material references. Expand the `Referenced By` section of the corresponding
    Mesh and Material resources and confirm the rigged object and its slots are
    counted there as well. Multi-part rigs must reference every part Mesh, not
    only the primary meshRef.
12. With Auto Refresh enabled, leave the registry files unchanged and confirm the
    view does not perform repeated reloads. Change, create, or delete one registry
    file and confirm the view refreshes on the next check. Switch away from
    Registry for several seconds, return, and verify no polling occurred while
    the tab was inactive.
13. In Diagnostics, inspect Recent Activity. Confirm an empty report uses one
    quiet text row rather than an info HelpBox. Run an operation, expand its
    activity row, inspect its fields, then clear it.
14. Expand Technical Details. Confirm Session contains only last activity,
    protocol, features, and an error when present; both pipeline queues use a
    concise Idle/Processing/Pending/Failed summary; and Registry object totals
    include both regular and Rigged Object records. Raw handshake state, IDs,
    payloads, traces, timelines, and asset paths must not be displayed.
15. Click Copy Diagnostics, paste the clipboard into a text editor, and inspect
    the JSON snapshot.

Expected:

- One `TriSync` window hosts all three top-level tabs. The TriSync menu
  has one main-window command and one Animation Tools command, with no separate
  Registry Inspector or sync report window commands. It has no legacy
  current-Selection creation command or development comparison commands.
- The connection card shows status, concise context, and the optional Blender
  application version on its first row. The persistent port and Connect or
  Disconnect action share the secondary row.
- Disconnected is gray, connecting is yellow, confirmed is green, and protocol
  or transport errors remain red even while the transport reports running.
- The Session tab contains connection status, a collapsed Settings section, and
  then the temporary Send To Blender Mesh list. Settings contains the shared
  user-level Language and Transform Smoothing controls. Protocol, negotiated
  features, and last activity are shown only under Diagnostics > Technical
  Details; raw handshake data and traces are not exposed in the UI.
- Port defaults to `8765`, persists between window openings, drives the Unity
  connection endpoint, and is disabled while the transport is running. It must
  match the Blender listener port.
- Transform Smoothing defaults on and remains a user-level preference. Its time
  and normalized curve are shared with `Edit > Preferences > TriSync`, never
  serialized into scene objects, and Reset Smoothing restores product defaults.
  Edit Mode smoothing must not add a component to synchronized scene objects.
  Managed targets disappear after settling, when smoothing is disabled, when
  Auto Sync stops, or when the session disconnects. Entering Play Mode must
  disconnect the session, clear pending work, and disable Connect until Edit
  Mode resumes. Edit Mode smoothing must not add a hidden component or mark a
  synchronized scene object as `DontSaveInEditor`. Unity 6000.1 may still emit
  the editor-level `DontSaveInEditor` assertion tracked as `UUM-85059`; compare
  it with a baseline project before attributing it to TriSync.
- The panel does not inspect or summarize the continuously changing Unity
  selection during repaint or menu validation. The list contains explicit object
  fields for Mesh assets or mesh-bearing GameObjects; detailed mesh data is read
  only by Create Mesh in Blender. Incompatible fields are user-facing warnings.
- Import results and omitted-feature warnings are shown after the one-shot
  operation. Imported clears the temporary list; failed or partial keeps it for
  retry.
- Registry Auto Refresh checks file metadata only while Registry is active and
  reloads registry data only after a registry file changes.
  Diagnostics reads the same registry snapshot rather than loading independent
  copies of the registry databases.
- Bound only and Search apply to both regular and rigged object records.
  Unregistering a Rigged Object removes only its registry record and leaves its
  prefab, scene instance, and referenced assets intact.
- Recent Activity is collapsed by default and shows at most the latest 5 entries
  when expanded, with each entry's details independently collapsed. Its textual
  Clear action remains available while the section is folded and removes both
  operation reports and diagnostic logs. Technical Details contains the
  user-level Verbose Logging setting plus concise Session, Pipeline, and
  Registry summaries. Rigged Object records contribute to object Total, Bound,
  and Issues.
- Copy Diagnostics uses the same `blendersync-diagnostics-v2` schema identifier
  as Blender. It includes session, registry summary, the latest operation, and
  at most 50 bounded recent log entries while excluding payload data, raw
  handshake IDs, object names, and local filesystem paths.
- Disabling Verbose Logging keeps Info and Warn under Recent Activity, sends
  Error to the console, and neither builds nor retains Trace. Enabling it also
  sends Info, Warn, and Trace to the console. Unity product code has no
  remaining direct-console logging debt.

### Geometry Nodes Instance Output

Steps:

1. Create a mesh object with a Geometry Nodes modifier whose output contains
   instances and does not contain a `Realize Instances` node.
2. Import or repair the object, enable Auto Sync, and move the object.
3. Change a Geometry Nodes input while Auto Sync is enabled.
4. Repeat with an explicit `Realize Instances` node on the final output path
   for comparison.
5. Add multiple `Set Material` branches whose materials exist only on the
   evaluated Geometry Nodes output. Include an unused empty object material
   slot, import it, then replace one assigned material while Auto Sync is
   enabled.

Expected:

- Blender realizes generated instances on a temporary export-only duplicate;
  the user's node tree, object, and modifier stack are unchanged.
- Object movement sends object state without building an evaluated mesh or
  repeatedly reporting `no_mesh_change`; real node-input changes still build
  and send the updated mesh.
- Manual import and automatic mesh preview produce a non-empty mesh asset and
  object assembly in Unity.
- Unity creates each evaluated Geometry Nodes material slot used by exported
  geometry in source order. Unused empty evaluated slots are compacted, so a
  GN-only material does not leave a leading empty Unity slot. Replacing a
  GN-assigned material updates the corresponding Unity slot without requiring
  a geometry change.
- When the final graph output already realizes all instances, TriSync uses
  the normal evaluated-mesh path and does not add a second temporary realization
  pass.
- Blender diagnostics may report `evaluated_realized_instances` and the
  realized instance count.
- If the evaluated result has no exportable vertices or indices, Blender
  reports a warning (`mesh_has_no_exportable_geometry`) and sends no empty mesh
  package, so Unity does not receive a dangling object assembly.

### Ordinary Mesh Import

Steps:

1. Select a simple Blender mesh with a material.
2. Import/update selected.
3. Inspect Unity scene object and generated resources.

Expected:

- Object appears with correct transform mapping.
- Mesh orientation is correct.
- A triangle mesh with complete normals and UV0 has one finite, unit tangent
  per vertex. Tangent-space normal maps remain correctly oriented across
  regular and mirrored UV islands.
- Position-only and UV-only previews regenerate tangents; removing UV0 clears
  them instead of preserving stale values.
- Material slot assignment is present.
- Registry/report entries are successful.

### Same-Name Asset Identity

Steps:

1. Import two different meshes with the same display name.
2. Import two different materials/textures with the same display name.
3. Duplicate a Blender object that intentionally shares the same mesh asset id.

Expected:

- Different stable IDs generate different Unity asset paths.
- Intentional same mesh asset id shares the Unity mesh resource.
- Resource errors remain zero.

### Material Content V1

Steps:

1. Send a material with base color, texture, roughness/metallic, normal, and
   emission where available.
2. Modify a material value or texture role and send material update.

Expected:

- Unity material uses `TriSync/Principled Lit URP` when available.
- Texture roles are isolated.
- Material report entries show success or clear warnings.
- Generated material and texture assets use `Resources/Materials` and
  `Resources/Textures`, without a version suffix in either directory name.

### Live Transform And Object State

Steps:

1. Import a mesh object.
2. Enable Auto Sync.
3. Move, rotate, scale, rename, toggle visibility, and change parent.

Expected:

- Transform updates apply after threshold filtering.
- Name, visibility, and hierarchy apply to the mapped Unity object.
- Selected-only operations do not mutate unrelated objects.

### Blender File Switch

Steps:

1. Connect Blender and Unity.
2. Open another `.blend` file.
3. Enable Auto Sync in the loaded file when its saved setting is off.
4. Modify an imported object.

Expected:

- Controller timers and inbound dispatch recover after the file switch.
- Auto Sync follows the loaded file's scene setting; it is not inherited from
  the previously open file.
- After enabling Auto Sync, subsequent changes continue to reach Unity.

### Mesh Preview And Commit

Steps:

1. Import an ordinary mesh.
2. Enter Edit Mode.
3. Move vertices and inspect Unity preview.
4. Edit UVs and use manual UV Preview.
5. Exit Edit Mode.

Expected:

- Preview updates the intended object.
- Manual UV Preview carries current evaluated mesh UV data.
- Mode exit commits to the correct mesh asset or forks when shared.

### Shape Keys / BlendShapes

Steps:

1. Import a mesh with shape keys.
2. Add, remove, and modify shape keys.
3. Change Object Mode shape key weights.
4. Disable Auto Sync, change only a shape key weight, and use Update Selected.
5. Add a visible modifier without removing the shape keys.

Expected:

- Unity BlendShape definitions update after mesh update/import.
- Shape key weight sync is stable in Object Mode.
- Manual weight-only updates do not send a mesh preview or commit.
- Delta magnitude matches Blender data after coordinate conversion.
- The modifier viewport result is not applied; Unity keeps the original mesh
  topology and valid BlendShape mappings.

### Rigged Object Import

Steps:

1. Import an armature with skinned mesh children.
2. Test default axis mode.
3. Test preserved rest-bone axis mode only if that workflow is being changed.

Expected:

- Skeleton hierarchy, mesh parts, bindposes, and weights are valid.
- Axis mode changes affect only the selected/current import target.
- Updating one rigged object does not mutate unrelated imports.

### Animation Clip Import

Steps:

1. Import Object/Armature animation.
2. On an Armature with one or more bound skin meshes, key Shape Keys on the
   mesh data. Import with Combined and confirm the clip contains both bone and
   `blendShape.*` curves on the matching renderer-child paths.
3. Import the same rig with Shape Keys Only and confirm it succeeds without an
   Armature Action, contains the skin-mesh `blendShape.*` curves, and contains no
   Armature Transform curves.
4. Use actions with different ranges on the Armature and Shape Keys; confirm
   Combined covers their union. Repeat with Shape Key drivers where available.
5. Import Driver-only animation where available.

Expected:

- Clip assets are generated.
- Combined and Shape Keys Only include Shape Key animation from every mesh bound
  to the active Armature, including multiple skinned mesh parts.
- No AnimationController auto-bind/auto-preview assets are created.
- Clips are not treated as registry-updated resources.

### Unity Animation Tools Window

Steps:

1. Open `TriSync/Open Animation Tools`.
2. Switch among Humanoid Avatar, Humanoid Clips, and Root Motion, then close and
   reopen the window and confirm the selected tab is restored.
3. In Humanoid Avatar, confirm the empty state is neutral and that `Humanoid
   Settings`, `Output`, and the disabled generate button remain visible. Then
   assign an Animator and verify mapping runs automatically. Use `Remap`, review
   required mappings, and generate a Humanoid Avatar. Confirm generation does
   not open a completion modal; the assigned Avatar field, selected/pinged asset,
   Recent Activity, and Console provide the result.
4. Collapse and reopen `Mapping Details`, `Humanoid Settings`, and `Output`.
   Scroll the bone mapping list and confirm the generate status and button remain
   visible at the bottom of the view.
5. In Humanoid Clips, assign a valid Humanoid Animator and confirm Avatar is
   filled but remains editable, while Root Node stays empty and conversion is
   disabled. Drag the Animator GameObject or one of its descendants into Root
   Node, assign one Generic/raw clip, and convert. Expand `Output`, confirm Key
   Reduction offers Off, Light, Medium, and Aggressive and defaults to Light.
   Confirm the output always contains all 12 Goal T and 16 Goal Q bindings,
   with no UI option to disable them.
6. Replace the source clip and convert it again when a different animation is
   needed. Confirm each conversion uses the source clip's frame rate and preserves
   Root Node. Inspect Recent Activity and confirm it records Key Reduction,
   Static Curves, collapsed static track count, keysBefore, keysAfter, and
   reductionPercent. Repeat once with Off and confirm keysBefore equals keysAfter.
   Enable Collapse Constant and confirm constant Animator curves become one-key
   curves while Root/Goal bindings remain present; varying curves are unchanged.
   For an entirely static clip, confirm duration and `AnimationClip.length`
   remain unchanged even if one existing curve retains a same-value endpoint.
   Use a clip with an unresolved Transform path and confirm a warning explains
   that the curve will be omitted. Choose an object outside the Animator
   hierarchy and confirm the action is disabled with a clear status. Change
   Animator and confirm Root Node is cleared with no automatic fallback.
7. In Root Motion, independently assign an Animator, editable Avatar, Root Node,
   and Clip; confirm no target is implicitly copied from a Humanoid tab. Confirm
   the section is named `Source Clip`, its field is named `Clip`, and the Generic
   Avatar creation action remains available. Confirm valid Root Nodes do not add
   a redundant resolved-path row, invalid hierarchy choices still show a warning,
   and Position/Rotation curve counts remain visible. Expand `Output`, confirm
   Key Reduction offers the same four presets and defaults to Off, and Static
   Curves offers Off and Collapse Constant and defaults to Off.
8. Create or replace the character's Generic Avatar, then create Root Motion Clips
   from two different source clips without recreating the Avatar. For one dense
   recorded clip, create Off, Light, and Aggressive variants. Confirm pose and
   Root Motion remain correct, the reduced variants are smaller, and Recent
   Activity reports keysBefore, keysAfter, and reductionPercent. Enable Collapse
   Constant once and confirm constant Root Transform groups reduce to one key,
   Root Motion bindings remain present, and discrete, object-reference, and
   BlendShape curves are unchanged. For an entirely static clip, confirm its
   duration remains unchanged and at most one existing curve retains a
   same-value endpoint. Confirm replacing a Humanoid Avatar requires
   explicit confirmation. Successful clip creation must not open a completion
   modal; the generated clip is selected and the result is available through
   Recent Activity and the Console.
9. Collapse and reopen `Output`; verify both folder pickers accept only folders
   under `Assets`, and confirm the create button remains fixed below the scroll area.

Expected:

- The TriSync menu exposes one Animation Tools command, not separate Avatar,
  Clip Converter, Root Motion, or Humanoid Muscle Compare commands.
- Each tab retains its own state while switching tabs.
- Humanoid Avatar keeps the target and generate controls fixed while only the
  mapping and optional settings area scrolls.
- Humanoid Clips uses one explicit source clip field and one conversion action.
  Avatar is editable and auto-filled from Animator; Root Node is an explicit,
  required Object Field with hierarchy validation and no automatic inference or
  fallback. Bounded key reduction preserves the first and last samples, keeps
  Root and Goal vector/quaternion component times synchronized, and can be
  disabled without changing dense curve data. Static Curve Collapse Constant
  folds only complete, time-aligned constant Animator groups and preserves all
  bindings; an entirely static clip may retain one same-value duration endpoint
  on an existing curve. It does not remove a curve because its value happens to
  match a static pose. Detailed reports remain in Diagnostics, while the
  generated clip is selected automatically after a successful conversion.
- Root Motion separates character-level Generic Avatar setup from repeatable clip
  creation. Its Avatar is an editable Animator reference, while the explicit
  Create/Replace Generic Avatar action remains available. Missing Root Node
  channels are reported before constant fallback curves are written. Optional
  reduction handles Transform vectors, quaternions, wrapped Euler angles, and
  continuous scalar curves while preserving discrete and object-reference curves;
  Off leaves copied float curves untouched. Static Curve Collapse Constant folds
  complete, time-aligned constant Transform groups while preserving their
  bindings and leaves BlendShape, discrete, and object-reference curves alone.
  An entirely static clip may retain one same-value duration endpoint on an
  existing curve. Successful clip creation does not interrupt the workflow with
  a modal dialog.
- Humanoid Avatar and Humanoid Clips preserve their existing asset generation
  behavior. Root Motion remains a separate Generic workflow.
- Generated assets and failures continue to appear in the shared sync report.

### Registry Unregister

Steps:

1. Open Unity `TriSync/Open TriSync` and select Registry.
2. Inspect Resources and Objects, then inspect Registry under Diagnostics.
3. Unregister one disposable record.

Expected:

- Inspector does not mutate data unless explicitly requested.
- Unregister removes registry records only.
- It does not delete Unity assets, scene objects, or Blender data.
- Sending the same Blender object/resource again recreates normal records.

## Level 2: Focused Functional Pass

Run the relevant section when a change touches that area:

- Session and transport.
- Large payload chunking.
- Asset naming or stable ID strategy.
- Material content and textures.
- Mesh binary/native extraction.
- Edit Mode preview and preview commit.
- Shared mesh copy-on-write.
- Shape keys / BlendShapes.
- Rigged object import and rig axis mode.
- Animation clip import/export.
- Registry behavior.
- Unity-to-Blender mesh import.
- Runtime sync scripts.
- Humanoid/root-motion tools.

For release candidates, run all Level 1 tests plus all Level 2 sections touched
since the last stable branch.

## Level 3: Pure Logic Unit Test Targets

Current pure-logic and characterization coverage includes:

### Unity EditMode Tests

`LargePayloadAssembler`:

- begin/chunk/end restores direct payload JSON.
- chunks can arrive out of order.
- duplicate chunks do not corrupt the transfer.
- chunk count mismatch is rejected.
- byte count mismatch is rejected.
- invalid base64 is rejected and cleaned up.
- IEEE CRC-32 matches the standard `123456789 -> cbf43926` vector.
- a negotiated valid checksum restores the original payload.
- checksum mismatch and missing checksum are rejected.
- malformed checksum and unsupported algorithms are rejected.
- legacy transfers remain valid without checksum fields.
- session reset drops every in-flight assembly.
- a transfer cannot cross negotiated checksum states.

`SessionProtocolContract` and one-shot mesh creation:

- `unity_mesh_import_result_v1` is negotiated only when both endpoints offer it.
- legacy or feature-missing peers do not produce a result wait state.
- a negotiated result wait expires from a local monotonic deadline and becomes
  a warning rather than an indefinite Pending state.
- `SkinnedMeshRenderer` snapshots preserve the shared mesh and BlendShapes while
  reporting omitted bones, skin weights, current BlendShape weights, and current
  deformation in the returned warnings.

Unity logging migration:

- handshake Trace entries never retain the raw acknowledgement payload or
  handshake identifier, and legacy-protocol negotiation records one warning per
  handshake rather than one per phase;
- material payload warnings aggregate by severity instead of emitting one entry
  per warning item;
- mesh-creation imported/partial terminal results map to Info/Warn entries;
- asset-package, mesh-asset, rigged-object, rigged-pose, material-reference,
  object-state/removal, and BlendShape update paths use the same facade; routine
  high-frequency work is silent unless verbose logging is enabled;
- animation import, Humanoid/Generic Avatar creation, Generic-to-Humanoid
  conversion, and root-motion variant generation retain one operation report
  without duplicating successful work into the Console;
- the migrated preview, material, object-assembly, mesh-send, session, and
  smoke-observed adjacent files contain no direct `Debug.Log*` calls. The
  logging debt gate pins this boundary.
- the Unity long tail follows the same policy: mesh application profiles and
  normal registry/rebinding activity are Trace, recoverable degradation is
  buffered Warn, report-backed success paths are not duplicated, and repeated
  deferred registry-save failures are deduplicated until a successful save;
- `LargePayloadAssembler` preserves its established rejection strings in the
  buffered Warn summaries, records normal transfer starts only as Trace, and
  its tests assert the diagnostics buffer instead of depending on Console
  output. Unity product code has no remaining direct-log debt.

Blender logging migration:

- transport entry points record successful and high-frequency sends only as
  lazy Trace, aggregate loop activity, buffer expected session/build failures
  as Warn, and retain unexpected exception tracebacks as Error;
- object-context mesh profiling is lazy Trace, while recoverable mesh/material
  fallback conditions are buffered Warn. Neither migrated file contains direct
  `print()` calls, and the logging debt gate pins that boundary.
- scene-sync controller and preview paths keep routine dirty-probe, baseline,
  cache, BlendShape, preview, and commit activity as lazy Trace; expected send
  rejection or fallback is buffered Warn, while unexpected exceptions retain
  Error tracebacks. Repeated background-pump exceptions are rate-limited by
  exception signature so a timer failure cannot flood diagnostics.
- the Blender closeout migrates Unity mesh import, material/lifecycle, session
  worker, dispatcher, native fallback, package/resource, rig, identity, and UI
  long-tail logging. Background-thread paths call only the lock-protected facade;
  successful and profiling activity is Info or lazy Trace, expected degradation
  is Warn, and unexpected exceptions retain their existing boundary traceback.
  Legacy `BLENDERSYNC_PROFILE_LOGS` and `BLENDERSYNC_LOG_PREVIEW_COMPARE` switches
  are removed in favor of the single Verbose Logging preference.

`AssetPathUtility`:

- same display name plus different stable IDs creates different paths.
- same stable ID creates stable path.
- known prefixes such as `mesh-`, `mat-`, and `tex-` are stripped for suffixes.
- short suffix length is stable.
- invalid file name characters are sanitized.

`SceneSyncTransformMapper`:

- point and direction mapping match the current Blender-to-Unity basis.
- triangle winding swaps the second and third indices.
- camera/light object type offset is applied.
- composed transform matches manual matrix composition.

`PreviewMeshService` runtime fingerprints:

- identical mesh data produces a stable fingerprint.
- vertex, UV, color, and index changes alter the fingerprint.
- BlendShape frame delta changes alter the fingerprint.

`MaterialContentV1ApplyService` apply fingerprints:

- identical material content produces a stable fingerprint.
- content hashes and texture dependency fields affect the fingerprint.

`TriSync/Principled Lit URP` shader compatibility:

- the shader asset resolves and imports without compiler errors in each Unity
  compatibility fixture.

`UnityMeshSendToBlenderTool` workflow state:

- direct and staged sends use the same result-capability gate.
- a queued result clears only on imported/partial/failed confirmation; a
  no-result peer remains retryable and is labeled as sent.
- queued confirmation starts a fresh processing deadline; timeout becomes a
  non-pending warning, ignores a late queued message, and remains replaceable by
  a late terminal result.

`SessionPanel`:

- connection state, error precedence, Blender version, action labels, default
  port, and validated endpoint map to stable user-facing presentation.
- mesh-list counts, create-action gating, import-boundary tooltip, and terminal
  result summaries do not depend on repaint-time Selection inspection.
- top-level tab values remain stable, Registry auto refresh is active-tab
  gated, and the unified window keeps one canonical menu command.

`RegistryPanelView`:

- Resources retain mapped and unmapped records, while Object/Rigged Object
  filters, search, bound state, unregister identity, and reference slots remain
  distinct.
- multi-part rigs contribute every distinct Mesh and Material reference to
  resource reverse-reference and diagnostics counts.
- auto refresh fingerprints all registry files and changes only for file
  content, creation, or deletion; pipeline diagnostics stay concise.

`AnimationToolsWindow` and its three workflow views:

- tab values are stable, invalid stored values normalize, one canonical menu
  opens the window, and each workflow retains independent view state.
- Humanoid Avatar readiness, validation, and required/optional mapping coverage
  are characterized independently of IMGUI drawing.
- Humanoid Clip conversion requires Hand/Foot IK curves, uses editable character
  fields and source frame rate, validates manual root hierarchy/path coverage,
  applies bounded grouped key reduction with an exact Off mode, and describes
  one-clip actions and results.
- Root Motion keeps editable Avatar rules, motion-root curve coverage, and
  create/replace readiness behavior characterized without requiring an Avatar,
  with key reduction defaulting to Off for copied source curves.

`HumanoidBoneMapper` auto mapping:

- Generic `HumanBodyBones`-style names map deterministically.
- Mixamo prefixes, Rigify side suffixes, 3ds Max Biped tokens, and Unreal
  numbered spines map through the project-owned heuristic rules.
- finger chains map by hierarchy and segment numbering.
- helper, IK, pole, target, metacarpal, roll, and twist names are not selected
  as humanoid roles.

### Python Unit Tests

`session/crc32_ieee.py` and large-payload sending:

- IEEE CRC-32 matches the standard vector.
- negotiated `large_payload.end` includes algorithm and checksum fields.
- legacy `large_payload.end` omits checksum fields.

Runtime identity and exception boundaries:

- reused Blender pointer values cannot bypass duplicate instance-id detection.
- Scene fingerprint caches follow `session_uid`, not persistent memory addresses.
- boundary errors retain their one-line message and emit rate-limited tracebacks.

`resource_update/fingerprint.py`:

- identical mesh input gives stable hash.
- vertex/index/normal changes affect hash.
- UV changes affect full hash.
- `include_uv=False` ignores UV-only changes.
- submesh and BlendShape metadata affect hash.
- binary buffer ordering stays deterministic.

`scene_sync/policy.py`:

- `in_scope("all", ...)` passes.
- non-matching scopes fail.
- transform deltas below threshold skip.
- deltas equal to threshold trigger because the current rule is `>=`.

## Failure Triage

If a smoke test fails:

1. Re-run Level 0 validation.
2. Check Diagnostics > Recent Activity on both endpoints; enable Verbose Logging
   only when Trace detail is needed.
3. Check Unity Console for Error entries such as `parse_failed`,
   `invalid_payload`, `not_mapped`, or apply failures.
4. Check Blender console for send failures or payload builder exceptions.
5. Inspect Unity registry files under:

```text
<Unity project>/Assets/TriSync/Registry
```

Do not patch from guesswork. Capture the smallest failing action, identify the
message path, then change the narrowest code path that explains the failure.
Unexpected Blender boundary exceptions print a full traceback by default, at
most once per site every 10 seconds. Set `BLENDERSYNC_TRACEBACKS=0` before
starting Blender only when concise one-line errors are preferred.
