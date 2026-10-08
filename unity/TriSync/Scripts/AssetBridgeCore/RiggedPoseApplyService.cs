using System;
using System.Collections.Generic;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.Protocol;
using UnityEngine;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;
#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.SceneManagement;
#endif

namespace BlenderSyncVNext.AssetBridgeCore
{
    public sealed class RiggedPoseApplyService
    {
        public bool HandleEnvelope(string rawJson)
        {
            try
            {
                var env = JsonUtility.FromJson<RiggedPoseEnvelope>(rawJson);
                if (env == null || !string.Equals(env.type, "asset_bridge.rigged_pose_v1", StringComparison.Ordinal))
                {
                    BlenderSyncLog.Warn(
                        "RiggedPose",
                        "invalid_envelope",
                        "Ignored an invalid rigged pose payload.");
                    return false;
                }

                return Apply(env, out _);
            }
            catch (Exception ex)
            {
                BlenderSyncLog.Exception(
                    "RiggedPose",
                    "parse_failed",
                    ex,
                    "Could not parse an incoming rigged pose payload.",
                    new Dictionary<string, object>
                    {
                        { "rawLength", string.IsNullOrEmpty(rawJson) ? 0 : rawJson.Length },
                    });
                BlenderSyncReportStore.Add("Rigged Pose", "ERROR", "parse_failed", new Dictionary<string, object>
                {
                    { "error", ex.Message },
                    { "rawLength", string.IsNullOrEmpty(rawJson) ? 0 : rawJson.Length },
                });
                return false;
            }
        }

        public bool Apply(RiggedPoseEnvelope payload, out string error)
        {
            error = null;
#if UNITY_EDITOR
            if (Application.isPlaying)
            {
                error = "rigged_pose_edit_mode_only";
                BlenderSyncLog.Warn(
                    "RiggedPose",
                    "edit_mode_required",
                    "Rigged pose updates are available only in Edit Mode.");
                return false;
            }

            if (payload == null)
            {
                error = "rigged_pose_payload_missing";
                return false;
            }

            var riggedObjectId = Normalize(payload.riggedObjectId);
            if (string.IsNullOrWhiteSpace(riggedObjectId))
            {
                error = "rigged_pose_rigged_object_id_missing";
                BlenderSyncLog.Warn(
                    "RiggedPose",
                    "object_id_missing",
                    "The rigged pose payload had no object identifier.");
                return false;
            }

            if (!TryResolveManagedInstance(riggedObjectId, out var root, out var record))
            {
                error = "rigged_pose_instance_missing";
                BlenderSyncLog.Warn(
                    "RiggedPose",
                    "instance_missing",
                    "Could not find the managed rigged object instance.",
                    new Dictionary<string, object>
                    {
                        { "riggedObjectId", riggedObjectId },
                    });
                return false;
            }

            var mode = Normalize(payload.mode) ?? "current_pose";
            if (string.Equals(mode, "restore_static_pose", StringComparison.Ordinal))
                return RestoreStaticPose(root, riggedObjectId, record, out error);
            if (string.Equals(mode, "current_pose", StringComparison.Ordinal))
                return ApplyCurrentPose(root, riggedObjectId, payload, out error);

            error = "rigged_pose_mode_unsupported";
            BlenderSyncLog.Warn(
                "RiggedPose",
                "mode_unsupported",
                "The requested rigged pose mode is unsupported.",
                new Dictionary<string, object>
                {
                    { "mode", mode },
                });
            return false;
#else
            error = "rigged_pose_apply_requires_editor";
            return false;
#endif
        }

#if UNITY_EDITOR
        private static bool ApplyCurrentPose(GameObject root, string riggedObjectId, RiggedPoseEnvelope payload, out string error)
        {
            error = null;
            var bones = payload.bones ?? Array.Empty<RiggedPoseBonePayload>();
            if (bones.Length == 0)
            {
                error = "rigged_pose_bones_empty";
                BlenderSyncLog.Warn(
                    "RiggedPose",
                    "bones_empty",
                    "The rigged pose payload contained no bones.",
                    new Dictionary<string, object>
                    {
                        { "riggedObjectId", riggedObjectId },
                    });
                return false;
            }

            var boneMap = BuildBoneMap(root);
            var targets = new List<Transform>();
            var resolved = new List<ResolvedPoseBone>();
            var missing = 0;

            foreach (var bone in bones)
            {
                if (!TryResolveBoneTransform(root, boneMap, bone, out var target) || target == null)
                {
                    missing++;
                    continue;
                }
                resolved.Add(new ResolvedPoseBone { transform = target, bone = bone });
                if (!targets.Contains(target))
                    targets.Add(target);
            }

            if (resolved.Count == 0)
            {
                error = "rigged_pose_no_bones_resolved";
                BlenderSyncLog.Warn(
                    "RiggedPose",
                    "bones_unresolved",
                    "None of the incoming pose bones matched the managed rig.",
                    new Dictionary<string, object>
                    {
                        { "riggedObjectId", riggedObjectId },
                        { "requested", bones.Length },
                    });
                return false;
            }

            Undo.RecordObjects(targets.ToArray(), Tr("TriSync Sync Current Pose"));
            foreach (var entry in resolved)
                ApplyBoneTransform(entry.transform, entry.bone);

            MarkTargetsDirty(root, targets);
            var rigAxisMode = Normalize(payload.rigAxisMode) ?? "(none)";
            var primary = Normalize(payload.primaryBoneAxis) ?? "(none)";
            var secondary = Normalize(payload.secondaryBoneAxis) ?? "(none)";
            BlenderSyncReportStore.Add("Rigged Pose", "OK", "current_pose_applied", new Dictionary<string, object>
            {
                { "riggedObjectId", riggedObjectId },
                { "rigAxisMode", rigAxisMode },
                { "primaryBoneAxis", primary },
                { "secondaryBoneAxis", secondary },
                { "bones", resolved.Count },
                { "missing", missing },
                { "animationMode", AnimationMode.InAnimationMode() },
            });
            return true;
        }

        private static bool RestoreStaticPose(GameObject root, string riggedObjectId, RiggedObjectRecord record, out string error)
        {
            error = null;
            var bones = CollectRendererBones(root);
            if (bones.Count == 0)
            {
                error = "rigged_pose_renderer_bones_empty";
                BlenderSyncLog.Warn(
                    "RiggedPose",
                    "renderer_bones_empty",
                    "The managed rig has no renderer bones to restore.",
                    new Dictionary<string, object>
                    {
                        { "riggedObjectId", riggedObjectId },
                    });
                return false;
            }

            var restored = new List<Transform>();
            foreach (var bone in bones)
            {
                var source = ResolveStaticPoseSource(root, record, bone);
                if (source == null)
                    continue;
                restored.Add(bone);
            }

            if (restored.Count == 0)
            {
                error = "rigged_pose_static_sources_missing";
                BlenderSyncLog.Warn(
                    "RiggedPose",
                    "static_sources_missing",
                    "No static-pose sources could be resolved for the managed rig.",
                    new Dictionary<string, object>
                    {
                        { "riggedObjectId", riggedObjectId },
                    });
                return false;
            }

            Undo.RecordObjects(restored.ToArray(), Tr("TriSync Restore Static Pose"));
            foreach (var bone in restored)
            {
                var source = ResolveStaticPoseSource(root, record, bone);
                if (source == null)
                    continue;
                bone.localPosition = source.localPosition;
                bone.localRotation = source.localRotation;
                bone.localScale = source.localScale;
            }

            MarkTargetsDirty(root, restored);
            BlenderSyncReportStore.Add("Rigged Pose", "OK", "static_pose_restored", new Dictionary<string, object>
            {
                { "riggedObjectId", riggedObjectId },
                { "bones", restored.Count },
                { "animationMode", AnimationMode.InAnimationMode() },
            });
            return true;
        }

        private sealed class ResolvedPoseBone
        {
            public Transform transform;
            public RiggedPoseBonePayload bone;
        }

        private static bool TryResolveManagedInstance(string riggedObjectId, out GameObject root, out RiggedObjectRecord record)
        {
            root = null;
            record = null;
            var registry = new RiggedObjectRegistry();
            var db = registry.Load();
            record = registry.FindByRiggedObjectId(db, riggedObjectId);
            var sceneObjectId = record?.managedInstance?.sceneObjectId;
            if (string.IsNullOrWhiteSpace(sceneObjectId))
                return false;
            if (!GlobalObjectId.TryParse(sceneObjectId, out var gid))
                return false;
            root = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid) as GameObject;
            return root != null;
        }

        private static Dictionary<string, Transform> BuildBoneMap(GameObject root)
        {
            var map = new Dictionary<string, Transform>(StringComparer.Ordinal);
            foreach (var bone in CollectRendererBones(root))
            {
                if (bone == null)
                    continue;
                AddBoneMapEntry(map, "bone-" + bone.name, bone);
            }

            foreach (var t in root.GetComponentsInChildren<Transform>(true))
            {
                if (t == null)
                    continue;
                AddBoneMapEntry(map, "bone-" + t.name, t);
            }
            return map;
        }

        private static List<Transform> CollectRendererBones(GameObject root)
        {
            var result = new List<Transform>();
            if (root == null)
                return result;
            foreach (var renderer in root.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                if (renderer == null || renderer.bones == null)
                    continue;
                foreach (var bone in renderer.bones)
                {
                    if (bone != null && !result.Contains(bone))
                        result.Add(bone);
                }
            }
            return result;
        }

        private static bool TryResolveBoneTransform(GameObject root, Dictionary<string, Transform> boneMap, RiggedPoseBonePayload bone, out Transform target)
        {
            target = null;
            if (bone == null)
                return false;

            var boneId = Normalize(bone.boneId);
            if (!string.IsNullOrWhiteSpace(boneId) && boneMap.TryGetValue(boneId, out target) && target != null)
                return true;

            var name = Normalize(bone.name);
            if (string.IsNullOrWhiteSpace(name) && !string.IsNullOrWhiteSpace(boneId) && boneId.StartsWith("bone-", StringComparison.Ordinal))
                name = boneId.Substring("bone-".Length);
            if (string.IsNullOrWhiteSpace(name))
                return false;

            var sanitizedId = "bone-" + SanitizeTransformName(name);
            if (boneMap.TryGetValue(sanitizedId, out target) && target != null)
                return true;

            var sanitizedName = SanitizeTransformName(name);
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
            {
                if (t != null && string.Equals(t.name, sanitizedName, StringComparison.Ordinal))
                {
                    target = t;
                    return true;
                }
            }
            return false;
        }

        private static Transform ResolveStaticPoseSource(GameObject root, RiggedObjectRecord record, Transform bone)
        {
            if (bone == null)
                return null;

            var source = PrefabUtility.GetCorrespondingObjectFromSource(bone) as Transform;
            if (source != null)
                return source;

            var prefabPath = record?.prefabPath;
            if (string.IsNullOrWhiteSpace(prefabPath))
                return null;

            var relativePath = GetRelativePath(root != null ? root.transform : null, bone);
            source = FindPrefabBoneSourceByPath(prefabPath, relativePath);
            return source != null ? source : FindPrefabBoneSourceByName(prefabPath, bone.name);
        }

        private static string GetRelativePath(Transform root, Transform target)
        {
            if (root == null || target == null)
                return null;
            var stack = new Stack<string>();
            var current = target;
            while (current != null && current != root)
            {
                stack.Push(current.name);
                current = current.parent;
            }
            return current == root && stack.Count > 0 ? string.Join("/", stack.ToArray()) : null;
        }

        private static Transform FindPrefabBoneSourceByPath(string prefabPath, string relativePath)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            if (prefab == null || string.IsNullOrWhiteSpace(relativePath))
                return null;
            return prefab.transform.Find(relativePath);
        }

        private static Transform FindPrefabBoneSourceByName(string prefabPath, string boneName)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            if (prefab == null || string.IsNullOrWhiteSpace(boneName))
                return null;
            foreach (var t in prefab.GetComponentsInChildren<Transform>(true))
            {
                if (t != null && string.Equals(t.name, boneName, StringComparison.Ordinal))
                    return t;
            }
            return null;
        }

        private static void ApplyBoneTransform(Transform target, RiggedPoseBonePayload bone)
        {
            if (target == null || bone == null)
                return;
            if (bone.localPosition != null && bone.localPosition.Length >= 3)
                target.localPosition = new Vector3(bone.localPosition[0], bone.localPosition[1], bone.localPosition[2]);
            if (bone.localRotation != null && bone.localRotation.Length >= 4)
            {
                var q = new Quaternion(bone.localRotation[0], bone.localRotation[1], bone.localRotation[2], bone.localRotation[3]);
                var lengthSq = q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w;
                if (lengthSq > 1e-8f)
                {
                    var inv = 1.0f / Mathf.Sqrt(lengthSq);
                    q = new Quaternion(q.x * inv, q.y * inv, q.z * inv, q.w * inv);
                }
                else
                    q = Quaternion.identity;
                target.localRotation = q;
            }
            if (bone.localScale != null && bone.localScale.Length >= 3)
                target.localScale = new Vector3(bone.localScale[0], bone.localScale[1], bone.localScale[2]);
        }

        private static void MarkTargetsDirty(GameObject root, List<Transform> targets)
        {
            foreach (var target in targets)
            {
                if (target == null)
                    continue;

                EditorUtility.SetDirty(target);
                RecordPrefabInstancePropertyModifications(target);
            }
            if (root != null)
            {
                EditorUtility.SetDirty(root);
                RecordPrefabInstancePropertyModifications(root);
                if (root.scene.IsValid())
                    EditorSceneManager.MarkSceneDirty(root.scene);
            }
            SceneView.RepaintAll();
        }

        private static void RecordPrefabInstancePropertyModifications(UnityEngine.Object target)
        {
            if (target != null && PrefabUtility.IsPartOfPrefabInstance(target))
                PrefabUtility.RecordPrefabInstancePropertyModifications(target);
        }

        private static void AddBoneMapEntry(Dictionary<string, Transform> map, string key, Transform value)
        {
            key = Normalize(key);
            if (string.IsNullOrWhiteSpace(key) || value == null || map.ContainsKey(key))
                return;
            map[key] = value;
        }

        private static string SanitizeTransformName(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return "Bone";
            return value.Replace('/', '_').Replace('\\', '_').Trim();
        }
#endif

        private static string Normalize(string value)
        {
            var normalized = value?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }
    }
}
