from __future__ import annotations

import os
import sys

bl_info = {
    "name": "TriSync",
    "author": "TriSync Team",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > TriSync",
    "description": "TriSync live preview integration between Blender and Unity",
    "category": "3D View",
}

_ADDON_DIR = os.path.dirname(__file__)
if _ADDON_DIR not in sys.path:
    sys.path.insert(0, _ADDON_DIR)

from blender.scene_sync import controller, settings  # noqa: E402
from blender.common.localization import register_translations, unregister_translations  # noqa: E402
from blender.session import file_lifecycle, main_thread_dispatcher  # noqa: E402
from blender.ui import operators, session_panel  # noqa: E402


def register() -> None:
    register_translations()
    settings.register_settings()
    operators.register()
    session_panel.register()
    main_thread_dispatcher.register_main_thread_dispatcher()
    controller.register_controller()
    file_lifecycle.register_file_lifecycle()


def unregister() -> None:
    file_lifecycle.unregister_file_lifecycle()
    controller.unregister_controller()
    main_thread_dispatcher.unregister_main_thread_dispatcher()
    session_panel.unregister()
    operators.unregister()
    settings.unregister_settings()
    unregister_translations()
