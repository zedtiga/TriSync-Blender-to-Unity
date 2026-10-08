"""Small translation facade for dynamic TriSync UI text."""

from __future__ import annotations

from blender.translations import blender_translation_dictionary

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


TRANSLATION_OWNER = "blendersync_vnext"


def _translations_api():
    app = getattr(bpy, "app", None) if bpy is not None else None
    return getattr(app, "translations", None)


def register_translations() -> None:
    translations = _translations_api()
    if translations is None:
        return
    try:
        translations.unregister(TRANSLATION_OWNER)
    except (KeyError, RuntimeError, ValueError):
        pass
    translations.register(TRANSLATION_OWNER, blender_translation_dictionary())


def unregister_translations() -> None:
    translations = _translations_api()
    if translations is None:
        return
    try:
        translations.unregister(TRANSLATION_OWNER)
    except (KeyError, RuntimeError, ValueError):
        pass


def iface(message: str, context: str = "*") -> str:
    translations = _translations_api()
    translate = getattr(translations, "pgettext_iface", None)
    if not callable(translate):
        return str(message or "")
    return str(translate(str(message or ""), context))


def report(message: str, context: str = "*") -> str:
    translations = _translations_api()
    translate = getattr(translations, "pgettext_rpt", None)
    if not callable(translate):
        return iface(message, context)
    return str(translate(str(message or ""), context))


def format_iface(message: str, /, **values) -> str:
    return iface(message).format(**values)


def format_report(message: str, /, **values) -> str:
    return report(message).format(**values)
