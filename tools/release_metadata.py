from __future__ import annotations

import argparse
import ast
import json
import re
import tomllib
from pathlib import Path


PRODUCT_VERSION_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<label>[0-9A-Za-z-]+)\.(?P<iteration>[1-9]\d*))?$"
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(_read_text(path), filename=str(path), feature_version=(3, 11))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"missing_assignment:{path}:{name}")


def _product_version_parts(product_version: str) -> tuple[int, int, int]:
    match = PRODUCT_VERSION_PATTERN.fullmatch(product_version)
    if match is None:
        raise ValueError(f"invalid_product_version:{product_version}")
    return tuple(int(match.group(name)) for name in ("major", "minor", "patch"))


def _csharp_protocol_version(path: Path) -> int:
    match = re.search(r"\bpublic\s+const\s+int\s+ProtocolVersion\s*=\s*(\d+)\s*;", _read_text(path))
    if match is None:
        raise ValueError(f"missing_csharp_protocol_version:{path}")
    return int(match.group(1))


def _native_capabilities(source: str) -> list[str]:
    match = re.search(
        r"const\s+NATIVE_CAPABILITIES\s*:\s*&\[&str\]\s*=\s*&\[(?P<body>.*?)\];",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise ValueError("missing_native_capabilities")
    capabilities = re.findall(r'"([a-z0-9_]+)"', match.group("body"))
    if not capabilities or len(capabilities) != len(set(capabilities)):
        raise ValueError("invalid_native_capabilities")
    return capabilities


def load_release_metadata(repo_root: Path) -> dict:
    root = repo_root.resolve()
    product_version = _read_text(root / "VERSION").strip()
    product_core = _product_version_parts(product_version)

    addon_init = root / "blender_addon" / "blendersync_vnext" / "__init__.py"
    bl_info = _literal_assignment(addon_init, "bl_info")
    blender_version = tuple(bl_info.get("version") or ())
    if blender_version != product_core:
        raise ValueError(
            "blender_product_version_mismatch:"
            f"product={'.'.join(str(value) for value in product_core)}:"
            f"bl_info={'.'.join(str(value) for value in blender_version)}"
        )

    cargo_path = root / "blender_addon" / "blendersync_vnext" / "native" / "blendersync_native" / "Cargo.toml"
    cargo = tomllib.loads(_read_text(cargo_path))
    native_version = str(cargo.get("package", {}).get("version") or "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", native_version):
        raise ValueError(f"invalid_native_version:{native_version}")

    native_source_path = cargo_path.parent / "src" / "lib.rs"
    native_source = _read_text(native_source_path)
    if 'env!("CARGO_PKG_VERSION")' not in native_source:
        raise ValueError("native_version_not_derived_from_cargo")
    if re.search(r'blendersync_native\s+\d+\.\d+\.\d+', native_source):
        raise ValueError("hardcoded_native_version")

    python_protocol_path = root / "blender_addon" / "blendersync_vnext" / "blender" / "session" / "protocol_contract.py"
    unity_protocol_path = root / "unity" / "TriSync" / "Scripts" / "SessionCore" / "SessionProtocolContract.cs"
    python_protocol = int(_literal_assignment(python_protocol_path, "PROTOCOL_VERSION"))
    unity_protocol = _csharp_protocol_version(unity_protocol_path)
    if python_protocol != unity_protocol:
        raise ValueError(
            f"session_protocol_version_mismatch:blender={python_protocol}:unity={unity_protocol}"
        )

    constants_path = root / "blender_addon" / "blendersync_vnext" / "blender" / "common" / "constants.py"
    asset_bridge_contract = str(
        _literal_assignment(constants_path, "ASSET_BRIDGE_CONTRACT_VERSION")
    ).strip()
    if not re.fullmatch(r"asset-bridge-v[1-9]\d*", asset_bridge_contract):
        raise ValueError(f"invalid_asset_bridge_contract:{asset_bridge_contract}")

    return {
        "schemaVersion": 1,
        "productVersion": product_version,
        "blenderAddonVersion": ".".join(str(value) for value in blender_version),
        "nativeVersion": native_version,
        "nativeCapabilities": _native_capabilities(native_source),
        "sessionProtocolVersion": python_protocol,
        "assetBridgeContractVersion": asset_bridge_contract,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and print TriSync release metadata.")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    metadata = load_release_metadata(args.repo_root)
    print(json.dumps(metadata, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
