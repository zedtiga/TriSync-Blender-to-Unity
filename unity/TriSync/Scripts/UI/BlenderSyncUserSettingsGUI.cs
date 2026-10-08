#if UNITY_EDITOR
using System.Collections.Generic;
using BlenderSyncVNext.Localization;
using BlenderSyncVNext.SceneSyncCore;
using UnityEditor;
using UnityEngine;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;

namespace BlenderSyncVNext.UI
{
    internal static class BlenderSyncUserSettingsGUI
    {
        internal static void DrawControls()
        {
            var language = BlenderSyncLocalization.Language;
            var selectedLanguage = (BlenderSyncLanguage)EditorGUILayout.Popup(
                Tr("Language"),
                (int)language,
                BlenderSyncLocalization.LanguageLabels());
            if (selectedLanguage != language)
                BlenderSyncLocalization.Language = selectedLanguage;

            EditorGUILayout.Space(4f);
            var smoothingEnabled = TransformSmoothingPreferences.Enabled;
            var nextSmoothingEnabled = EditorGUILayout.Toggle(
                new GUIContent(
                    Tr("Transform Smoothing"),
                    Tr("Smooth automatically synchronized object transforms in the Unity editor.")),
                smoothingEnabled);
            if (nextSmoothingEnabled != smoothingEnabled)
                TransformSmoothingPreferences.Enabled = nextSmoothingEnabled;

            if (nextSmoothingEnabled)
            {
                EditorGUI.indentLevel++;
                var smoothingTime = EditorGUILayout.Slider(
                    new GUIContent(
                        Tr("Smoothing Time"),
                        Tr("Time in seconds used to settle on the latest synchronized transform.")),
                    TransformSmoothingPreferences.SmoothingTime,
                    TransformSmoothingPreferences.MinSmoothingTime,
                    TransformSmoothingPreferences.MaxSmoothingTime);
                if (!Mathf.Approximately(smoothingTime, TransformSmoothingPreferences.SmoothingTime))
                    TransformSmoothingPreferences.SmoothingTime = smoothingTime;

                var followCurve = TransformSmoothingPreferences.FollowCurve;
                EditorGUI.BeginChangeCheck();
                followCurve = EditorGUILayout.CurveField(
                    new GUIContent(
                        Tr("Follow Curve"),
                        Tr("Normalized follow progress over the smoothing time.")),
                    followCurve,
                    Color.green,
                    new Rect(0f, 0f, 1f, 1f),
                    GUILayout.Height(72f));
                if (EditorGUI.EndChangeCheck())
                    TransformSmoothingPreferences.FollowCurve = followCurve;
                EditorGUI.indentLevel--;
            }

            using (new EditorGUILayout.HorizontalScope())
            {
                GUILayout.FlexibleSpace();
                if (GUILayout.Button(Tr("Reset Smoothing"), GUILayout.Width(120f)))
                    TransformSmoothingPreferences.ResetSmoothing();
            }
        }

        [SettingsProvider]
        private static SettingsProvider CreateSettingsProvider()
        {
            return new SettingsProvider("Preferences/TriSync", SettingsScope.User)
            {
                label = "TriSync",
                guiHandler = _ => DrawControls(),
                keywords = new HashSet<string>(new[]
                {
                    "TriSync",
                    "Language",
                    "Transform Smoothing",
                    "Smoothing Time",
                    "Follow Curve",
                    "语言",
                    "变换平滑",
                }),
            };
        }
    }
}
#endif
