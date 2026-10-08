from __future__ import annotations

# User-facing operation intent.  This describes what the user asked for.
INTENT_IMPORT_OBJECTS = "import_objects"
INTENT_OBJECT_STATE = "object_state"
INTENT_VIEW_STATE = "view_state"
INTENT_ANIMATION_CLIP = "animation_clip"
INTENT_MATERIAL_UPDATE = "material_update"
INTENT_MATERIAL_RESOURCES = "material_resources"
INTENT_STRUCTURE_ONLY = "structure_only"
INTENT_PREVIEW_UPDATE = "preview_update"
INTENT_PREVIEW_COMMIT = "preview_commit"
INTENT_AUTO_SYNC = "auto_sync"

# Concrete trigger/source path.  This describes where the operation came from.
TRIGGER_IMPORT_SELECTED_OBJECTS = "import_selected_objects"
TRIGGER_UPDATE_OBJECT_STATE = "update_object_state"
TRIGGER_SYNC_SCENE_VIEW_MANUAL = "sync_scene_view_manual"
TRIGGER_ANIMATION_CLIP_EXPORT = "animation_clip_export"
TRIGGER_MATERIAL_UPDATE_CONTEXTUAL = "material_update_contextual"
TRIGGER_MATERIAL_RESOURCES_RESEND_CONTEXTUAL = "material_resources_resend_contextual"
TRIGGER_AUTO_MATERIAL_SLOT = "auto_material_slot"


def apply_update_metadata(context: dict, *, update_intent: str, trigger_type: str) -> dict:
    """Attach optional local/report metadata to a send context.

    These fields are intentionally non-breaking.  Existing transport code already
    reads triggerType for logs, and updateIntent is currently report/local metadata.
    """
    if context is None:
        return context
    context["updateIntent"] = str(update_intent or "")
    context["triggerType"] = str(trigger_type or "")
    return context
