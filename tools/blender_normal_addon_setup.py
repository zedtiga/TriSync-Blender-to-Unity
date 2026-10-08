from __future__ import annotations

import argparse
import os
import sys

import addon_utils
import bpy


def _arguments_after_separator() -> list[str]:
    try:
        separator = sys.argv.index("--")
    except ValueError:
        return []
    return sys.argv[separator + 1 :]


def _normalized(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _assert_loaded_from(runtime_root: str) -> str:
    import blendersync_vnext

    expected = _normalized(runtime_root)
    actual = _normalized(os.path.dirname(blendersync_vnext.__file__))
    if actual != expected:
        raise RuntimeError(f"unexpected_addon_path:{actual}:expected:{expected}")
    if not hasattr(bpy.types, "BS_PT_session_panel"):
        raise RuntimeError("session_panel_not_registered")
    return actual


def _resolve_user_addon_root() -> None:
    root = bpy.utils.user_resource("SCRIPTS", path="addons", create=True)
    if not root:
        raise RuntimeError("user_addon_root_unavailable")
    print(f"BLENDERSYNC_USER_ADDONS={root}")


def _enable(runtime_root: str) -> None:
    module = addon_utils.enable("blendersync_vnext", default_set=True)
    if module is None:
        raise RuntimeError("blendersync_vnext_enable_failed")
    actual = _assert_loaded_from(runtime_root)
    bpy.ops.wm.save_userpref()
    print(f"BLENDERSYNC_NORMAL_ENABLE_OK path={actual}")


def _verify(runtime_root: str) -> None:
    enabled, loaded = addon_utils.check("blendersync_vnext")
    if not enabled or not loaded:
        raise RuntimeError(f"addon_not_enabled_after_restart:{enabled}:{loaded}")
    actual = _assert_loaded_from(runtime_root)
    print(f"BLENDERSYNC_NORMAL_RESTART_OK path={actual}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=("resolve-user-addon", "enable", "verify"),
    )
    parser.add_argument("--runtime-root")
    args = parser.parse_args(_arguments_after_separator())

    if args.mode == "resolve-user-addon":
        _resolve_user_addon_root()
        return
    if not args.runtime_root:
        raise RuntimeError("runtime_root_required")
    if args.mode == "enable":
        _enable(args.runtime_root)
    else:
        _verify(args.runtime_root)


if __name__ == "__main__":
    main()
