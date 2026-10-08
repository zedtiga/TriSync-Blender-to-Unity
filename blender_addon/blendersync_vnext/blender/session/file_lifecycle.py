from __future__ import annotations

try:
    import bpy  # type: ignore
    from bpy.app.handlers import persistent  # type: ignore
except ImportError:
    bpy = None

    def persistent(fn):
        return fn


from blender.scene_sync import controller
from blender.asset_registry import reset_runtime_asset_registry_cache
from blender.common.exception_boundary import report_boundary_exception
from blender.identity import reset_runtime_identity_cache
from blender.session import main_thread_dispatcher
from blender import unity_mesh_import


@persistent
def _on_load_pre(_unused) -> None:
    main_thread_dispatcher.begin_file_load()
    reset_runtime_identity_cache()
    reset_runtime_asset_registry_cache()
    unity_mesh_import.reset_file_runtime_state(reason="load_pre")
    controller.reset_file_runtime_state(reason="load_pre")


@persistent
def _on_load_post(_unused) -> None:
    _finish_file_load("load_post")


@persistent
def _on_load_post_fail(_unused) -> None:
    _finish_file_load("load_post_fail")


def _finish_file_load(reason: str) -> None:
    try:
        _run_recovery_step(
            reason,
            "controller_reset",
            lambda: controller.reset_file_runtime_state(reason=reason),
        )
        _run_recovery_step(reason, "controller_register", controller.register_controller)
        _run_recovery_step(
            reason,
            "publish_auto_sync_state",
            controller.publish_current_auto_sync_state,
        )
    finally:
        _run_recovery_step(
            reason,
            "dispatcher_complete",
            main_thread_dispatcher.complete_file_load,
        )


def _run_recovery_step(reason: str, step: str, action) -> bool:
    try:
        action()
        return True
    except Exception as exc:
        report_boundary_exception(
            f"file_lifecycle:{step}",
            exc,
            message=(
                f"[vNext][FileLifecycle] recovery_step_failed "
                f"reason={reason} step={step} error={exc}"
            ),
        )
        return False


def register_file_lifecycle() -> None:
    if bpy is None:
        return
    if _on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_on_load_pre)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    load_post_fail = getattr(bpy.app.handlers, "load_post_fail", None)
    if load_post_fail is not None and _on_load_post_fail not in load_post_fail:
        load_post_fail.append(_on_load_post_fail)


def unregister_file_lifecycle() -> None:
    if bpy is not None:
        if _on_load_pre in bpy.app.handlers.load_pre:
            bpy.app.handlers.load_pre.remove(_on_load_pre)
        if _on_load_post in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(_on_load_post)
        load_post_fail = getattr(bpy.app.handlers, "load_post_fail", None)
        if load_post_fail is not None and _on_load_post_fail in load_post_fail:
            load_post_fail.remove(_on_load_post_fail)
    unity_mesh_import.reset_file_runtime_state(reason="addon_unregister")
    reset_runtime_identity_cache()
    reset_runtime_asset_registry_cache()
