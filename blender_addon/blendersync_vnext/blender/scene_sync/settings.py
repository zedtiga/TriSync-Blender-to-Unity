"""
Scene-level workflow settings and user-level add-on preferences.

Scene properties hold workflow and asset-pipeline state that belongs to a
.blend file. Connection and performance tuning values live in AddonPreferences
so they follow the Blender user and machine instead.
"""

from __future__ import annotations

from blender.common.constants import DEFAULT_WS_PORT

try:
    import bpy  # type: ignore
    from bpy.props import BoolProperty, FloatProperty, IntProperty, EnumProperty, StringProperty  # type: ignore
except ImportError:
    bpy = None
    BoolProperty = None
    FloatProperty = None
    IntProperty = None
    EnumProperty = None
    StringProperty = None


ADDON_PACKAGE = "blendersync_vnext"
DEFAULT_OBJECT_STATE_SYNC_HZ = 30.0
DEFAULT_MESH_UPDATE_SYNC_HZ = 30.0
DEFAULT_VIEW_SYNC_HZ = 10.0
DEFAULT_VIEW_SYNC_SCALE = 0.8
DEFAULT_LIFECYCLE_RECONCILE_HZ = 1.5
DEFAULT_EVALUATED_MESH_PREVIEW_DEBOUNCE_MS = 250
DEFAULT_PREVIEW_IDLE_COMMIT_SECONDS = 10.0
PERFORMANCE_TUNING_DEFAULTS = (
    ("object_state_sync_hz", DEFAULT_OBJECT_STATE_SYNC_HZ),
    ("mesh_update_sync_hz", DEFAULT_MESH_UPDATE_SYNC_HZ),
    ("evaluated_mesh_preview_debounce_ms", DEFAULT_EVALUATED_MESH_PREVIEW_DEBOUNCE_MS),
    ("preview_idle_commit_seconds", DEFAULT_PREVIEW_IDLE_COMMIT_SECONDS),
    ("lifecycle_reconcile_hz", DEFAULT_LIFECYCLE_RECONCILE_HZ),
    ("view_sync_hz", DEFAULT_VIEW_SYNC_HZ),
    ("view_sync_scale", DEFAULT_VIEW_SYNC_SCALE),
)


def _update_verbose_logging(preferences, _context) -> None:
    from blender.common.log import set_verbose_preference

    set_verbose_preference(bool(getattr(preferences, "verbose_logging", False)))


if bpy is not None:
    class BlenderSyncAddonPreferences(bpy.types.AddonPreferences):
        bl_idname = ADDON_PACKAGE


    # Future annotations are enabled in this module, so populate Blender's RNA
    # annotation with the evaluated property descriptor explicitly.
    BlenderSyncAddonPreferences.__annotations__ = {
        "session_port": IntProperty(
            name="Session Port",
            description="Local WebSocket port used to connect TriSync to Unity",
            default=DEFAULT_WS_PORT,
            min=1,
            max=65535,
        ),
        "verbose_logging": BoolProperty(
            name="Verbose Logging",
            description="Write informational and trace diagnostics to the console; trace diagnostics are not collected while disabled",
            default=False,
            update=_update_verbose_logging,
        ),
        "object_state_sync_hz": FloatProperty(
            name="Object Check Max Hz",
            description="Advanced throttle: maximum Object Mode change-check/pump rate. Actual transform sends are signature/change gated; idle objects are skipped",
            default=DEFAULT_OBJECT_STATE_SYNC_HZ,
            min=0.1,
            max=120.0,
            soft_min=0.5,
            soft_max=60.0,
        ),
        "mesh_update_sync_hz": FloatProperty(
            name="Edit Preview Max Hz",
            description="Maximum rate for processing dirty Edit/Sculpt mesh previews. Unchanged meshes are skipped",
            default=DEFAULT_MESH_UPDATE_SYNC_HZ,
            min=0.1,
            max=60.0,
            soft_min=0.5,
            soft_max=30.0,
        ),
        "evaluated_mesh_preview_debounce_ms": IntProperty(
            name="Preview Send Delay",
            description="Milliseconds to wait after the latest mesh, modifier, or Geometry Nodes change before sending a preview. Repeated changes restart the delay",
            default=DEFAULT_EVALUATED_MESH_PREVIEW_DEBOUNCE_MS,
            min=50,
            max=5000,
            soft_min=100,
            soft_max=1000,
        ),
        "preview_idle_commit_seconds": FloatProperty(
            name="Preview Idle Commit",
            description="Seconds Unity waits after the last mesh preview before committing it to the mesh asset",
            default=DEFAULT_PREVIEW_IDLE_COMMIT_SECONDS,
            min=1.0,
            max=60.0,
            soft_min=2.0,
            soft_max=30.0,
        ),
        "view_sync_hz": FloatProperty(
            name="View Sync Hz",
            description="Scene view sync frequency in Hz (0.5 to 30)",
            default=DEFAULT_VIEW_SYNC_HZ,
            min=0.5,
            max=30.0,
            soft_min=1.0,
            soft_max=10.0,
        ),
        "view_sync_scale": FloatProperty(
            name="View Scale",
            description="Unity Scene view scale multiplier; values below 1 zoom in and values above 1 zoom out",
            default=DEFAULT_VIEW_SYNC_SCALE,
            min=0.1,
            max=5.0,
            soft_min=0.5,
            soft_max=2.0,
        ),
        "lifecycle_reconcile_hz": FloatProperty(
            name="Reconcile Hz",
            description="Full-scene lifecycle reconcile frequency in Hz (0.05 to 5)",
            default=DEFAULT_LIFECYCLE_RECONCILE_HZ,
            min=0.05,
            max=5.0,
            soft_min=0.1,
            soft_max=2.0,
        ),
    }
else:
    class BlenderSyncAddonPreferences:  # type: ignore[no-redef]
        bl_idname = ADDON_PACKAGE


UI_TAB_ITEMS = (
    ("objects", "Obj", "Import, repair, and update selected Blender objects", 0, 0),
    ("materials", "Mats", "Inspect material payloads and send active material updates", 0, 1),
    ("rig_pose", "Rig", "Sync current rig pose or restore imported static pose", 0, 2),
    ("animation", "Anim", "Export animation clips to Unity", 0, 3),
    ("sync", "Sync (Legacy)", "Compatibility value for older files saved before the workflow tab redesign", 0, 4),
    ("diagnostics", "Diag", "Reports and debug state", 0, 5),
)


def _get_animation_clip_loop(scene) -> bool:
    value = getattr(scene, "blendersync_animation_clip_loop_hint", "loop")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "loop"


def _set_animation_clip_loop(scene, enabled: bool) -> None:
    scene.blendersync_animation_clip_loop_hint = "loop" if bool(enabled) else "none"


def get_addon_preferences(context=None):
    active_context = context
    if active_context is None:
        if bpy is None:
            return None
        active_context = getattr(bpy, "context", None)
    preferences = getattr(active_context, "preferences", None)
    addons = getattr(preferences, "addons", None)
    if addons is None:
        return None
    try:
        addon = addons.get(ADDON_PACKAGE)
    except (AttributeError, TypeError):
        try:
            addon = addons[ADDON_PACKAGE]
        except (KeyError, TypeError):
            addon = None
    return getattr(addon, "preferences", None) if addon is not None else None


def get_session_port(context=None) -> int:
    preferences = get_addon_preferences(context)
    try:
        return int(getattr(preferences, "session_port"))
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_WS_PORT


def get_verbose_logging(context=None) -> bool:
    preferences = get_addon_preferences(context)
    try:
        return bool(getattr(preferences, "verbose_logging"))
    except (AttributeError, TypeError, ValueError):
        return False


def _get_numeric_preference(name: str, default, context=None):
    preferences = get_addon_preferences(context)
    try:
        return type(default)(getattr(preferences, name))
    except (AttributeError, TypeError, ValueError):
        return default


def get_object_state_sync_hz(context=None) -> float:
    return float(_get_numeric_preference("object_state_sync_hz", DEFAULT_OBJECT_STATE_SYNC_HZ, context))


def get_mesh_update_sync_hz(context=None) -> float:
    return float(_get_numeric_preference("mesh_update_sync_hz", DEFAULT_MESH_UPDATE_SYNC_HZ, context))


def get_evaluated_mesh_preview_debounce_ms(context=None) -> int:
    return int(
        _get_numeric_preference(
            "evaluated_mesh_preview_debounce_ms",
            DEFAULT_EVALUATED_MESH_PREVIEW_DEBOUNCE_MS,
            context,
        )
    )


def get_preview_idle_commit_seconds(context=None) -> float:
    return float(
        _get_numeric_preference(
            "preview_idle_commit_seconds",
            DEFAULT_PREVIEW_IDLE_COMMIT_SECONDS,
            context,
        )
    )


def get_view_sync_hz(context=None) -> float:
    return float(_get_numeric_preference("view_sync_hz", DEFAULT_VIEW_SYNC_HZ, context))


def get_view_sync_scale(context=None) -> float:
    return float(_get_numeric_preference("view_sync_scale", DEFAULT_VIEW_SYNC_SCALE, context))


def get_lifecycle_reconcile_hz(context=None) -> float:
    return float(
        _get_numeric_preference(
            "lifecycle_reconcile_hz",
            DEFAULT_LIFECYCLE_RECONCILE_HZ,
            context,
        )
    )


def reset_performance_tuning(context=None) -> bool:
    preferences = get_addon_preferences(context)
    if preferences is None:
        return False
    for name, default in PERFORMANCE_TUNING_DEFAULTS:
        setattr(preferences, name, default)
    return True


def _safe_unregister_class(cls) -> None:
    if bpy is None:
        return
    candidates = [cls]
    existing = getattr(bpy.types, getattr(cls, "__name__", ""), None)
    if existing is not None and existing is not cls:
        candidates.insert(0, existing)
    for candidate in candidates:
        try:
            bpy.utils.unregister_class(candidate)
            return
        except Exception:
            continue


def _safe_register_class(cls) -> None:
    if bpy is None:
        return
    try:
        bpy.utils.register_class(cls)
        return
    except Exception as exc:
        if "already registered" not in str(exc):
            raise
    _safe_unregister_class(cls)
    bpy.utils.register_class(cls)


def register_settings() -> None:
    """Register add-on preferences and Scene properties."""
    if bpy is None:
        return

    _safe_register_class(BlenderSyncAddonPreferences)
    from blender.common.log import set_verbose_preference

    set_verbose_preference(get_verbose_logging())

    # Global sync on/off switch
    bpy.types.Scene.blendersync_sync_enabled = BoolProperty(
        name="Auto Sync",
        description="Enable automatic scene sync to Unity",
        default=False,
    )

    bpy.types.Scene.blendersync_view_sync_enabled = BoolProperty(
        name="Auto Sync Scene View",
        description="Automatically sync the active Blender 3D View to Unity SceneView",
        default=False,
    )

    bpy.types.Scene.blendersync_show_auto_sync_settings = BoolProperty(
        name="Show Auto Sync Settings",
        description="Show automatic sync frequency and view-sync settings",
        default=False,
    )

    bpy.types.Scene.blendersync_show_object_advanced = BoolProperty(
        name="Show Object Advanced",
        description="Show advanced remove and pre-import mesh preset tools in the Objects tab",
        default=False,
    )

    bpy.types.Scene.blendersync_show_animation_panel = BoolProperty(
        name="Show Clip Export",
        description="Show animation clip export settings",
        default=False,
    )

    bpy.types.Scene.blendersync_show_animation_quality = BoolProperty(
        name="Show Animation Quality",
        description="Show advanced animation sampling and curve quality settings",
        default=False,
    )

    bpy.types.Scene.blendersync_show_animation_report = BoolProperty(
        name="Show Last Animation Export",
        description="Show details from the last animation clip export",
        default=False,
    )

    bpy.types.Scene.blendersync_show_last_report = BoolProperty(
        name="Legacy Show Last Report",
        description="Compatibility property for older files saved before the diagnostics redesign",
        default=False,
    )

    bpy.types.Scene.blendersync_show_diagnostics_recent_activity = BoolProperty(
        name="Show Recent Activity",
        description="Show recent TriSync operation reports and diagnostic logs",
        default=False,
    )

    bpy.types.Scene.blendersync_show_diagnostics_active_object = BoolProperty(
        name="Show Active Object Diagnostics",
        description="Show sync and baseline diagnostics for the active object",
        default=False,
    )

    bpy.types.Scene.blendersync_show_diagnostics_runtime = BoolProperty(
        name="Show Runtime Diagnostics",
        description="Show compact Auto Sync runtime counters",
        default=False,
    )

    bpy.types.Scene.blendersync_show_diagnostics_developer = BoolProperty(
        name="Show Developer Diagnostics",
        description="Show raw session state and runtime counters",
        default=False,
    )

    bpy.types.Scene.blendersync_show_debug = BoolProperty(
        name="Show Debug",
        description="Show detailed TriSync diagnostic state",
        default=False,
    )

    bpy.types.Scene.blendersync_rig_export_leaf_bones = BoolProperty(
        name="Export Leaf Bones",
        description="Create Unity *_end transforms for terminal rig bones to preserve Blender bone tails",
        default=True,
    )

    bpy.types.Scene.blendersync_rig_axis_mode = EnumProperty(
        name="Rig Axis Mode",
        description="Bone-axis contract used when importing rigs and matching animation clips",
        items=(
            ("baked_joint_axes", "Baked Joint Axes", "Default stable mode: Unity bone rotations are identity and rest offsets are baked into joint positions"),
            ("preserve_rest_bone_axes", "Preserve Rest Bone Axes", "Experimental export-like mode: preserve Blender rest local bone rotation/scale and export matching full local TRS animation"),
        ),
        default="baked_joint_axes",
    )

    bone_axis_items = (
        ("X", "+X", "Positive X axis"),
        ("Y", "+Y", "Positive Y axis"),
        ("Z", "+Z", "Positive Z axis"),
        ("-X", "-X", "Negative X axis"),
        ("-Y", "-Y", "Negative Y axis"),
        ("-Z", "-Z", "Negative Z axis"),
    )
    bpy.types.Scene.blendersync_rig_primary_bone_axis = EnumProperty(
        name="Primary Bone Axis",
        description="Primary bone axis in the generated Unity skeleton; the current preserve-axis result corresponds to +Z",
        items=bone_axis_items,
        default="Z",
    )
    bpy.types.Scene.blendersync_rig_secondary_bone_axis = EnumProperty(
        name="Secondary Bone Axis",
        description="Secondary bone axis in the generated Unity skeleton; the current preserve-axis result corresponds to +X",
        items=bone_axis_items,
        default="X",
    )

    bpy.types.Scene.blendersync_ui_tab = EnumProperty(
        name="TriSync Tab",
        description="Active TriSync workflow section",
        items=UI_TAB_ITEMS,
        default="objects",
    )

    bpy.types.Scene.blendersync_mesh_source = EnumProperty(
        name="Legacy Mesh Source",
        description="Compatibility property for older files; runtime mesh source selection now always uses the automatic policy",
        items=(
            ("auto", "Auto", "Use Original Mesh normally; use Evaluated Mesh when visible modifiers or Geometry Nodes are active. Shape Keys and Skin stay Original"),
            ("original", "Original Mesh", "Always use the original mesh datablock"),
            ("evaluated", "Evaluated Mesh", "Use Blender depsgraph evaluated mesh including modifiers and Geometry Nodes mesh output"),
        ),
        default="auto",
    )

    bpy.types.Scene.blendersync_animation_clip_range_mode = EnumProperty(
        name="Range",
        description="Frame range used for animation clip export",
        items=(
            ("action", "Action Range", "Use active action range"),
            ("scene", "Scene Range", "Use current scene frame range"),
            ("custom", "Custom", "Use custom start/end frames"),
        ),
        default="action",
    )

    bpy.types.Scene.blendersync_animation_clip_source_mode = EnumProperty(
        name="Animation Source",
        description="Animation sources included in the exported Unity AnimationClip",
        items=(
            ("combined", "Combined", "Export Object and Shape Key animation together"),
            ("object", "Object Only", "Export only Object/Armature animation and Object drivers"),
            ("shape_keys", "Shape Keys Only", "Export only Shape Key animation and Shape Key drivers"),
        ),
        default="combined",
    )

    bpy.types.Scene.blendersync_animation_clip_start_frame = IntProperty(
        name="Start",
        description="Custom animation clip export start frame",
        default=1,
    )

    bpy.types.Scene.blendersync_animation_clip_end_frame = IntProperty(
        name="End",
        description="Custom animation clip export end frame",
        default=250,
    )

    bpy.types.Scene.blendersync_animation_clip_sample_mode = EnumProperty(
        name="Sample Mode",
        description="How animation is sampled before export",
        items=(
            ("scene_frames", "Scene Frames", "Sample integer Blender frames with Sample Step"),
            ("fixed_fps", "Fixed FPS", "Sample at the selected export FPS using sub-frames when needed"),
            ("keyframes", "Keyframes", "Sample source Action keyframe times plus range boundaries"),
        ),
        default="scene_frames",
    )

    bpy.types.Scene.blendersync_animation_clip_sample_rate_mode = EnumProperty(
        name="FPS",
        description="Export FPS used for clip timing and fixed-FPS sampling",
        items=(
            ("scene", "Scene FPS", "Use current scene FPS"),
            ("fps_30", "30 FPS", "Export at 30 FPS"),
            ("fps_60", "60 FPS", "Export at 60 FPS"),
            ("fps_120", "120 FPS", "Export at 120 FPS"),
            ("custom", "Custom FPS", "Use custom FPS"),
        ),
        default="scene",
    )

    bpy.types.Scene.blendersync_animation_clip_sample_rate = FloatProperty(
        name="Custom FPS",
        description="Custom export FPS for animation clip export",
        default=30.0,
        min=1.0,
        max=240.0,
        soft_min=12.0,
        soft_max=120.0,
    )

    bpy.types.Scene.blendersync_animation_clip_sample_step = IntProperty(
        name="Sample Step",
        description="Sample every N integer Blender frames when Sample Mode is Scene Frames",
        default=1,
        min=1,
        max=60,
        soft_min=1,
        soft_max=5,
    )

    bpy.types.Scene.blendersync_animation_clip_sampling_strategy = EnumProperty(
        name="Armature Sampling",
        description="How TriSync decides whether to export keyed deform-bone curves or bake evaluated deform-bone pose",
        items=(
            ("auto", "Auto", "Use evaluated deform-bone bake when constraints, drivers, or animated controller/non-deform bones are detected"),
            ("raw_fcurve", "Raw F-Curve", "Export keyed deform-bone channels only; constraints/controllers are not baked"),
            ("evaluated_deform", "Evaluated Deform Bones", "Bake final evaluated deform-bone pose; intended for IK/constraints/controllers"),
        ),
        default="auto",
    )

    bpy.types.Scene.blendersync_animation_clip_interpolation = EnumProperty(
        name="Interpolation",
        description="Unity curve interpolation/tangent mode",
        items=(
            ("linear", "Linear", "Use linear tangents"),
            ("constant", "Constant", "Use stepped/constant tangents"),
            ("smooth", "Smooth", "Use smooth automatic tangents"),
        ),
        default="linear",
    )

    bpy.types.Scene.blendersync_animation_clip_quaternion_continuity = BoolProperty(
        name="Quaternion Continuity",
        description="Keep neighboring quaternion keys on the same hemisphere to avoid rotation flips",
        default=True,
    )

    bpy.types.Scene.blendersync_animation_clip_simplify = EnumProperty(
        name="Simplify",
        description="Reduce redundant exported curve keys after sampling",
        items=(
            ("off", "Off", "Do not reduce keys"),
            ("light", "Light", "Light key reduction"),
            ("medium", "Medium", "Medium key reduction"),
            ("aggressive", "Aggressive", "Aggressive key reduction"),
        ),
        default="off",
    )

    bpy.types.Scene.blendersync_animation_clip_static_curves = EnumProperty(
        name="Static Curves",
        description="Collapse numerically constant Transform curve groups to one key without removing their bindings",
        items=(
            ("off", "Off", "Keep constant Transform curves unchanged"),
            ("collapse_constant", "Collapse Constant", "Fold constant Transform curve groups to one key and keep every binding"),
        ),
        default="off",
    )

    bpy.types.Scene.blendersync_animation_clip_loop_hint = EnumProperty(
        name="Loop",
        description="Loop hint for Unity AnimationClip",
        items=(
            ("none", "None", "Do not mark as loop"),
            ("loop", "Loop", "Mark clip as loop"),
        ),
        default="loop",
    )

    bpy.types.Scene.blendersync_animation_clip_loop = BoolProperty(
        name="Loop",
        description="Mark the exported Unity AnimationClip as looping",
        get=_get_animation_clip_loop,
        set=_set_animation_clip_loop,
    )

    bpy.types.Scene.blendersync_animation_clip_name = StringProperty(
        name="Clip Name",
        description="Optional Unity-side AnimationClip name override",
        default="",
    )
    bpy.types.Scene.blendersync_animation_clip_last_report = StringProperty(
        name="Last Export Report",
        description="Summary of the last animation clip export",
        default="",
    )


def unregister_settings() -> None:
    """Unregister Scene properties and add-on preferences."""
    if bpy is None:
        return

    del bpy.types.Scene.blendersync_sync_enabled
    del bpy.types.Scene.blendersync_view_sync_enabled
    del bpy.types.Scene.blendersync_show_auto_sync_settings
    del bpy.types.Scene.blendersync_show_object_advanced
    del bpy.types.Scene.blendersync_show_animation_panel
    del bpy.types.Scene.blendersync_show_animation_quality
    del bpy.types.Scene.blendersync_show_animation_report
    del bpy.types.Scene.blendersync_show_last_report
    del bpy.types.Scene.blendersync_show_diagnostics_recent_activity
    del bpy.types.Scene.blendersync_show_diagnostics_active_object
    del bpy.types.Scene.blendersync_show_diagnostics_runtime
    del bpy.types.Scene.blendersync_show_diagnostics_developer
    del bpy.types.Scene.blendersync_show_debug
    del bpy.types.Scene.blendersync_rig_export_leaf_bones
    del bpy.types.Scene.blendersync_rig_axis_mode
    del bpy.types.Scene.blendersync_rig_primary_bone_axis
    del bpy.types.Scene.blendersync_rig_secondary_bone_axis
    del bpy.types.Scene.blendersync_ui_tab
    del bpy.types.Scene.blendersync_mesh_source
    del bpy.types.Scene.blendersync_animation_clip_range_mode
    del bpy.types.Scene.blendersync_animation_clip_source_mode
    del bpy.types.Scene.blendersync_animation_clip_start_frame
    del bpy.types.Scene.blendersync_animation_clip_end_frame
    del bpy.types.Scene.blendersync_animation_clip_sample_mode
    del bpy.types.Scene.blendersync_animation_clip_sample_rate_mode
    del bpy.types.Scene.blendersync_animation_clip_sample_rate
    del bpy.types.Scene.blendersync_animation_clip_sample_step
    del bpy.types.Scene.blendersync_animation_clip_sampling_strategy
    del bpy.types.Scene.blendersync_animation_clip_interpolation
    del bpy.types.Scene.blendersync_animation_clip_quaternion_continuity
    del bpy.types.Scene.blendersync_animation_clip_simplify
    del bpy.types.Scene.blendersync_animation_clip_static_curves
    del bpy.types.Scene.blendersync_animation_clip_loop
    del bpy.types.Scene.blendersync_animation_clip_loop_hint
    del bpy.types.Scene.blendersync_animation_clip_name
    del bpy.types.Scene.blendersync_animation_clip_last_report
    _safe_unregister_class(BlenderSyncAddonPreferences)
    from blender.common.log import set_verbose_preference

    set_verbose_preference(False)
