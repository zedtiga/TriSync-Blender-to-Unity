from __future__ import annotations

import ast
import re
import string
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
ADDON_ROOT = ROOT / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.translations import ZH_HANS, blender_translation_dictionary
from blender.common import localization


BLENDER_UI_FILES = (
    ADDON_ROOT / "blender" / "ui" / "session_panel.py",
    ADDON_ROOT / "blender" / "ui" / "operators.py",
    ADDON_ROOT / "blender" / "scene_sync" / "settings.py",
)
BLENDER_VISIBLE_ASSIGNMENTS = {
    "_VISIBLE_WORKFLOW_TABS",
    "_LAST_OPERATION_LABELS",
    "_BULK_OPERATION_LABELS",
    "UI_TAB_ITEMS",
}
BLENDER_UI_CALLS = {"label", "operator", "prop", "prop_enum"}
BLENDER_PROPERTY_CALLS = {
    "BoolProperty",
    "EnumProperty",
    "FloatProperty",
    "IntProperty",
    "StringProperty",
}
UNITY_LOCALIZATION_FILE = (
    ROOT
    / "unity"
    / "TriSync"
    / "Scripts"
    / "Localization"
    / "BlenderSyncLocalization.cs"
)
UNITY_SOURCE_ROOT = ROOT / "unity" / "TriSync" / "Scripts"
UNITY_CATALOG_ENTRY = re.compile(
    r'^\s*\{\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\},',
    re.MULTILINE,
)
UNITY_LOOKUP = re.compile(r'\b(?:Tr|Format)\(\s*"((?:[^"\\]|\\.)*)"')
UNITY_UNDO_LITERAL = re.compile(
    r'\bUndo\.(?:(?:RecordObject|RecordObjects|RegisterCreatedObjectUndo)\([^,\n]+,\s*"|SetCurrentGroupName\(\s*")'
)


def _decode_csharp_string(value: str) -> str:
    return value.replace(r"\n", "\n").replace(r'\"', '"').replace(r"\\", "\\")


def _placeholder_names(value: str) -> list[str]:
    return sorted(
        str(field_name).split(".", 1)[0].split("[", 1)[0]
        for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(value)
        if field_name is not None
    )


def _literal_strings(value) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        strings = set()
        for item in value.values():
            strings.update(_literal_strings(item))
        return strings
    if isinstance(value, (tuple, list, set)):
        strings = set()
        for item in value:
            strings.update(_literal_strings(item))
        return strings
    return set()


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _keyword_literal(node: ast.Call, name: str):
    for keyword in node.keywords:
        if keyword.arg == name:
            try:
                return ast.literal_eval(keyword.value)
            except (TypeError, ValueError):
                return None
    return None


def _collect_blender_ui_strings() -> set[str]:
    strings = set()
    for path in BLENDER_UI_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value_node = node.value
                for target in targets:
                    name = target.id if isinstance(target, ast.Name) else ""
                    attr = target.attr if isinstance(target, ast.Attribute) else ""
                    if name in BLENDER_VISIBLE_ASSIGNMENTS or attr in {"bl_label", "bl_description"}:
                        try:
                            value = ast.literal_eval(value_node)
                            if name in {"UI_TAB_ITEMS", "_VISIBLE_WORKFLOW_TABS"}:
                                for item in value:
                                    strings.update(str(text) for text in item[1:3] if text)
                            else:
                                strings.update(_literal_strings(value))
                        except (TypeError, ValueError):
                            pass
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name in BLENDER_UI_CALLS:
                text = _keyword_literal(node, "text")
                if isinstance(text, str) and text:
                    strings.add(text)
            if name == "_draw_disclosure" and len(node.args) >= 4:
                try:
                    label = ast.literal_eval(node.args[3])
                except (TypeError, ValueError):
                    label = None
                if isinstance(label, str) and label:
                    strings.add(label)
            if name in BLENDER_PROPERTY_CALLS:
                for keyword in ("name", "description"):
                    text = _keyword_literal(node, keyword)
                    if isinstance(text, str) and text:
                        strings.add(text)
                items = _keyword_literal(node, "items")
                if isinstance(items, (tuple, list)):
                    for item in items:
                        if isinstance(item, (tuple, list)):
                            strings.update(str(value) for value in item[1:3] if value)
    return strings


class LocalizationCatalogTests(unittest.TestCase):
    def test_blender_catalog_covers_static_user_interface_strings(self) -> None:
        missing = sorted(_collect_blender_ui_strings() - set(ZH_HANS))
        self.assertEqual([], missing)

    def test_blender_catalog_has_matching_placeholders_and_locale_aliases(self) -> None:
        for source, translated in ZH_HANS.items():
            self.assertTrue(translated, source)
            self.assertEqual(_placeholder_names(source), _placeholder_names(translated), source)
        catalog = blender_translation_dictionary()
        self.assertEqual(catalog["zh_HANS"], catalog["zh_CN"])
        self.assertIn(("*", "Session"), catalog["zh_HANS"])
        self.assertIn(("Operator", "Connect Session"), catalog["zh_HANS"])

    def test_blender_translation_facade_registers_and_routes_contexts(self) -> None:
        calls = []
        translations = SimpleNamespace(
            register=lambda owner, catalog: calls.append(("register", owner, catalog)),
            unregister=lambda owner: calls.append(("unregister", owner)),
            pgettext_iface=lambda message, context: f"iface:{context}:{message}",
            pgettext_rpt=lambda message, context: f"report:{context}:{message}",
        )
        with mock.patch.object(localization, "bpy", SimpleNamespace(app=SimpleNamespace(translations=translations))):
            localization.register_translations()
            self.assertEqual("iface:*:Session", localization.iface("Session"))
            self.assertEqual("report:Operator:Failed", localization.report("Failed", "Operator"))
            localization.unregister_translations()

        self.assertEqual("unregister", calls[0][0])
        self.assertEqual(("register", localization.TRANSLATION_OWNER), calls[1][:2])
        self.assertEqual("unregister", calls[2][0])

    def test_blender_static_english_is_not_hidden_from_translation(self) -> None:
        for path in BLENDER_UI_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            violations = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_name(node) not in BLENDER_UI_CALLS:
                    continue
                text = _keyword_literal(node, "text")
                translate = _keyword_literal(node, "translate")
                if translate is False and isinstance(text, str) and re.search(r"[A-Za-z]", text):
                    violations.append((node.lineno, text))
            self.assertEqual([], violations, str(path))

    def test_unity_catalog_covers_all_literal_lookups(self) -> None:
        catalog_source = UNITY_LOCALIZATION_FILE.read_text(encoding="utf-8-sig")
        catalog_pairs = [
            (_decode_csharp_string(match.group(1)), _decode_csharp_string(match.group(2)))
            for match in UNITY_CATALOG_ENTRY.finditer(catalog_source)
        ]
        catalog_keys = {source for source, _translated in catalog_pairs}
        self.assertEqual(len(catalog_keys), len(catalog_pairs))
        for source, translated in catalog_pairs:
            self.assertTrue(translated, source)
            self.assertEqual(_placeholder_names(source), _placeholder_names(translated), source)

        missing = []
        for path in UNITY_SOURCE_ROOT.rglob("*.cs"):
            source = path.read_text(encoding="utf-8-sig")
            for match in UNITY_LOOKUP.finditer(source):
                key = _decode_csharp_string(match.group(1))
                if key not in catalog_keys:
                    missing.append(f"{path.relative_to(ROOT)}:{source.count(chr(10), 0, match.start()) + 1}:{key}")
        self.assertEqual([], missing)

    def test_log_facades_do_not_depend_on_localization(self) -> None:
        unity_log = (
            UNITY_SOURCE_ROOT / "Diagnostics" / "BlenderSyncLog.cs"
        ).read_text(encoding="utf-8-sig")
        blender_log = (
            ADDON_ROOT / "blender" / "common" / "log.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn("BlenderSyncLocalization", unity_log)
        self.assertNotIn("localization", blender_log)

    def test_unity_undo_labels_route_through_localization(self) -> None:
        violations = []
        for path in UNITY_SOURCE_ROOT.rglob("*.cs"):
            source = path.read_text(encoding="utf-8-sig")
            for match in UNITY_UNDO_LITERAL.finditer(source):
                violations.append(
                    f"{path.relative_to(ROOT)}:{source.count(chr(10), 0, match.start()) + 1}"
                )
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
