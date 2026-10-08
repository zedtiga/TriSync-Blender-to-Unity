from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from release_metadata import load_release_metadata


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load and validate the TriSync native extension."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--module-root", type=Path, required=True)
    args = parser.parse_args()

    metadata = load_release_metadata(args.repo_root)
    module_root = args.module_root.resolve()
    if not module_root.is_dir():
        raise RuntimeError(f"native_module_root_missing:{module_root}")
    sys.path.insert(0, str(module_root))
    module = importlib.import_module("blendersync_native")
    module_file = Path(str(getattr(module, "__file__", ""))).resolve()
    if module_file.parent != module_root:
        raise RuntimeError(
            f"native_module_loaded_from_unexpected_path:expected={module_root}:actual={module_file}"
        )
    version = str(module.version())
    capabilities = [str(value) for value in module.capabilities()]
    expected_version = str(metadata["nativeVersion"])
    expected_capabilities = [str(value) for value in metadata["nativeCapabilities"]]

    if version != expected_version:
        raise RuntimeError(
            f"native_version_mismatch:expected={expected_version}:actual={version}"
        )
    if capabilities != expected_capabilities:
        raise RuntimeError(
            "native_capabilities_mismatch:"
            f"expected={expected_capabilities}:actual={capabilities}"
        )

    print(
        f"native_import_smoke_ok module={module_file} "
        f"version={version} capabilities={capabilities}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
