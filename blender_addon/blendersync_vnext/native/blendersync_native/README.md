# TriSync Native Extractor

Rust + PyO3 native extension for high-frequency accurate mesh extraction.

## Build prerequisites

- Rust toolchain: https://rustup.rs/
- maturin installed for Blender's Python ABI, for example:
  - `uv tool install maturin`, or
  - `<blender-python> -m pip install maturin`

## Target

The tracked builds use PyO3 `abi3-py311`. Place the platform-specific module
under the matching directory:

| Host target | Directory | Module file |
| --- | --- | --- |
| Windows x86_64 | `../artifacts/win_amd64/cpython-311/` | `blendersync_native.pyd` |
| Linux x86_64 | `../artifacts/linux_x86_64/cpython-311/` | `blendersync_native.so` |
| macOS arm64 | `../artifacts/macos_arm64/cpython-311/` | `blendersync_native.so` |
| macOS x86_64 | `../artifacts/macos_x86_64/cpython-311/` | `blendersync_native.so` |

The Blender addon searches:

1. `native/artifacts/<platform>/<sys.implementation.cache_tag>/`
2. `native/artifacts/<platform>/cpython-311/` on CPython 3.11 or newer
3. `native/artifacts/<platform>/`
4. `native/artifacts/`

The current interpreter tag remains first so a version-specific build can
override the stable-ABI artifact. The `cpython-311` fallback is not used on
older CPython versions or other Python implementations.

The repository package may contain only a subset of the four platform
artifacts while builds are being staged. The Python fallback remains available
when the host-specific module is missing or cannot be imported.

## GitHub Actions Build

The repository contains a manual-only workflow at
`.github/workflows/build-native.yml`. In GitHub, open **Actions**, select
**Build native artifacts**, choose **Run workflow**, and wait for all three jobs
to finish. The workflow does not run automatically on pushes or pull requests.
Each job runs an import smoke that checks `version()` and `capabilities()`
against the checked-in release metadata before uploading its artifact. The
macOS job imports the runner-native module and verifies both outputs' Mach-O
architectures; the other architecture is not import-smoked on that runner.

Download the three workflow artifacts and merge their contents into
`native/artifacts/` in the add-on package. The downloaded files already retain
the platform directory, so the resulting paths must be:

```text
native/artifacts/win_amd64/cpython-311/blendersync_native.pyd
native/artifacts/linux_x86_64/cpython-311/blendersync_native.so
native/artifacts/macos_arm64/cpython-311/blendersync_native.so
native/artifacts/macos_x86_64/cpython-311/blendersync_native.so
```

The macOS artifact contains both architectures. Keep the binary extensions and
directory names unchanged. After staging artifacts locally, run the import
smoke again on the matching host with its `cpython-311` directory as
`--module-root`, then run the Blender endpoint matrix there.
The workflow build and import smoke do not by themselves expand the project's
public platform support statement.

## Contract

The native module must be pure-buffer only:

- no `bpy`
- no Unity state
- no global Blender object access
- input: foreach_get buffers from Python
- output: accurate split vertices/indices/profile or binary_v1 files

Python fallback remains authoritative until native output hashes match the reference.

`version()` reports only the Cargo package version through
`CARGO_PKG_VERSION`. `capabilities()` returns the supported native operation
identifiers separately; callers must not infer capabilities from a formatted
version string.
