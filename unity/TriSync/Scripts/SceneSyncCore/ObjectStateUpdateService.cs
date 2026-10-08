using System;
using System.Collections.Generic;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class ObjectStateUpdateService
    {
        public void Apply(SceneSyncObjectStateUpdateMessage msg, Func<string, Transform> resolveTarget)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("object_state_update_message_missing");
                return;
            }

            var applied = 0;
            var missing = 0;
            var parentMissing = 0;
            var invalid = 0;
            var resolved = new Dictionary<string, Transform>();

            foreach (var item in msg.objects ?? Array.Empty<SceneSyncObjectStateItem>())
            {
                if (item == null || string.IsNullOrWhiteSpace(item.pairId))
                {
                    invalid++;
                    continue;
                }

                var target = resolveTarget?.Invoke(item.pairId);
                if (target != null)
                    resolved[item.pairId] = target;
                else
                    missing++;
            }

            foreach (var item in msg.objects ?? Array.Empty<SceneSyncObjectStateItem>())
            {
                if (item == null || string.IsNullOrWhiteSpace(item.pairId))
                    continue;
                if (!resolved.TryGetValue(item.pairId, out var target) || target == null)
                    continue;

                // Object-state payloads apply parent-relative local TRS directly.
                // Cancel any older world-space smoothing target before changing hierarchy.
                SceneSyncTransformSmoother.RemoveFrom(target, completeTarget: false);

                if (item.clearParent)
                {
                    // object_state_update_v1 uses hierarchy + local TRS semantics.
                    // After unparenting, local TRS is world TRS, so apply it below.
                    target.SetParent(null, false);
                }
                else if (!string.IsNullOrWhiteSpace(item.parentPairId))
                {
                    if (!resolved.TryGetValue(item.parentPairId, out var parent) || parent == null)
                        parent = resolveTarget?.Invoke(item.parentPairId);

                    if (parent != null && parent != target && !parent.IsChildOf(target))
                    {
                        // Do not preserve world transform here. The payload below is Blender parent-relative local TRS.
                        target.SetParent(parent, false);
                    }
                    else if (parent == null)
                        parentMissing++;
                    else
                        invalid++;
                }

                if (!string.IsNullOrWhiteSpace(item.objectName))
                    target.name = item.objectName;

                var parentVisualOffset = target.parent != null
                    ? SceneSyncTransformMapper.GetObjectTypeLocalAxisOffset(GetBoundObjectType(target.parent))
                    : Quaternion.identity;
                var inverseParentVisualOffset = Quaternion.Inverse(parentVisualOffset);

                if (item.position != null && item.position.Length == 3)
                    target.localPosition = inverseParentVisualOffset * SceneSyncTransformMapper.MapPosition(item.position);
                if (item.rotation != null && item.rotation.Length == 4)
                {
                    var mappedRotation = SceneSyncTransformMapper.MapRotation(item.rotation);
                    if (!string.IsNullOrWhiteSpace(item.objectType))
                        mappedRotation = SceneSyncTransformMapper.MapRotationForObjectType(item.rotation, item.objectType);
                    target.localRotation = inverseParentVisualOffset * mappedRotation;
                }
                if (item.scale != null && item.scale.Length == 3)
                    target.localScale = SceneSyncTransformMapper.MapScale(item.scale);

                target.gameObject.SetActive(item.visible);
#if UNITY_EDITOR
                EditorUtility.SetDirty(target.gameObject);
                EditorUtility.SetDirty(target);
#endif
                SceneSyncStateStore.MarkApplied(item.pairId);
                applied++;
            }

            var objectCount = msg.objects != null ? msg.objects.Length : 0;
            BlenderSyncReportStore.Add(
                "Object State Update",
                missing > 0 || parentMissing > 0 || invalid > 0 ? "WARN" : "OK",
                $"applied={applied} objects={objectCount} missing={missing} parentMissing={parentMissing} invalid={invalid}",
                new Dictionary<string, object>
                {
                    { "applied", applied },
                    { "objects", objectCount },
                    { "missing", missing },
                    { "parentMissing", parentMissing },
                    { "invalid", invalid },
                });
            RequestEditorRepaint();
        }

        private static string GetBoundObjectType(Transform target)
        {
            if (target == null)
                return null;
            if (target.GetComponent<Camera>() != null)
                return "camera";
            if (target.GetComponent<Light>() != null)
                return "light";
            return null;
        }

        private static void RequestEditorRepaint()
        {
#if UNITY_EDITOR
            EditorApplication.QueuePlayerLoopUpdate();
            SceneView.RepaintAll();
            EditorApplication.RepaintHierarchyWindow();
            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
#endif
        }
    }
}
