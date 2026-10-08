#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using BlenderSyncVNext.Diagnostics;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;

namespace BlenderSyncVNext.RootMotion
{
    internal static class RootMotionEditorActions
    {
        public static void Execute(string reportTitle, string actionName, string dialogTitle, Action action)
        {
            if (action == null)
                return;

            try
            {
                action();
            }
            catch (Exception ex)
            {
                var message = $"{actionName} failed: {ex.Message}";
                BlenderSyncReportStore.Add(reportTitle, "ERROR", message, new Dictionary<string, object>
                {
                    { "action", actionName },
                    { "exceptionType", ex.GetType().Name },
                });
                BlenderSyncLog.Exception(
                    "AnimationTools",
                    "editor_action_failed",
                    ex,
                    message,
                    new Dictionary<string, object>
                    {
                        { "action", actionName },
                    });
                EditorUtility.DisplayDialog(
                    Tr(dialogTitle),
                    Format("{0} failed: {1}", Tr(actionName), ex.Message),
                    Tr("OK"));
            }
        }

        public static void MarkAnimatorDirty(Animator animator)
        {
            if (animator == null)
                return;

            EditorUtility.SetDirty(animator);
            if (PrefabUtility.IsPartOfPrefabInstance(animator))
                PrefabUtility.RecordPrefabInstancePropertyModifications(animator);

            var target = animator.gameObject;
            if (target == null)
                return;

            EditorUtility.SetDirty(target);
            if (PrefabUtility.IsPartOfPrefabInstance(target))
                PrefabUtility.RecordPrefabInstancePropertyModifications(target);
            if (target.scene.IsValid())
                EditorSceneManager.MarkSceneDirty(target.scene);
        }
    }
}
#endif
