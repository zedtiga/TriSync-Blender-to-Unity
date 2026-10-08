using System;
using System.Collections.Generic;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.Protocol;
using BlenderSyncVNext.SceneSyncCore;
using UnityEngine;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;
#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.SceneManagement;
#endif

namespace BlenderSyncVNext.AssetBridgeCore
{
    public sealed class RiggedBlendShapeWeightsApplyService
    {
        public bool HandleEnvelope(string rawJson)
        {
            try
            {
                var env = JsonUtility.FromJson<RiggedBlendShapeWeightsEnvelope>(rawJson);
                if (env == null || !string.Equals(env.type, "asset_bridge.rigged_blendshape_weights_v1", StringComparison.Ordinal))
                {
                    BlenderSyncLog.Warn(
                        "RiggedBlendShape",
                        "invalid_envelope",
                        "Ignored an invalid rigged BlendShape weights payload.");
                    return false;
                }

                return Apply(env, out _);
            }
            catch (Exception ex)
            {
                BlenderSyncLog.Exception(
                    "RiggedBlendShape",
                    "parse_failed",
                    ex,
                    "Could not parse an incoming rigged BlendShape weights payload.",
                    new Dictionary<string, object>
                    {
                        { "rawLength", string.IsNullOrEmpty(rawJson) ? 0 : rawJson.Length },
                    });
                BlenderSyncReportStore.Add("Rigged Shape Keys", "ERROR", "parse_failed", new Dictionary<string, object>
                {
                    { "error", ex.Message },
                    { "rawLength", string.IsNullOrEmpty(rawJson) ? 0 : rawJson.Length },
                });
                return false;
            }
        }

        public bool Apply(RiggedBlendShapeWeightsEnvelope payload, out string error)
        {
            error = null;
#if UNITY_EDITOR
            if (Application.isPlaying)
            {
                error = "rigged_blendshape_weights_edit_mode_only";
                BlenderSyncLog.Warn(
                    "RiggedBlendShape",
                    "edit_mode_required",
                    "Rigged BlendShape weight updates are available only in Edit Mode.");
                return false;
            }

            if (payload == null)
            {
                error = "rigged_blendshape_weights_payload_missing";
                return false;
            }

            var riggedObjectId = Normalize(payload.riggedObjectId);
            if (string.IsNullOrWhiteSpace(riggedObjectId))
            {
                error = "rigged_blendshape_weights_rigged_object_id_missing";
                BlenderSyncLog.Warn(
                    "RiggedBlendShape",
                    "object_id_missing",
                    "The rigged BlendShape weights payload had no object identifier.");
                return false;
            }

            if (!TryResolveManagedInstance(riggedObjectId, out var root))
            {
                error = "rigged_blendshape_weights_instance_missing";
                BlenderSyncLog.Warn(
                    "RiggedBlendShape",
                    "instance_missing",
                    "Could not find the managed rigged object instance.",
                    new Dictionary<string, object>
                    {
                        { "riggedObjectId", riggedObjectId },
                    });
                return false;
            }

            return TryApplyToRoot(root, payload, out error);
#else
            error = "rigged_blendshape_weights_apply_requires_editor";
            return false;
#endif
        }

#if UNITY_EDITOR
        public bool TryApplyToRoot(GameObject root, RiggedBlendShapeWeightsEnvelope payload, out string error)
        {
            error = null;
            if (root == null)
            {
                error = "rigged_blendshape_weights_root_missing";
                return false;
            }
            if (payload == null || payload.parts == null || payload.parts.Length == 0)
            {
                error = "rigged_blendshape_weights_parts_empty";
                return false;
            }

            var renderers = root.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            var resolved = new List<ResolvedWeight>();
            var targets = new List<SkinnedMeshRenderer>();
            var missingParts = 0;
            var missingWeights = 0;

            foreach (var part in payload.parts)
            {
                if (part == null || part.weights == null || part.weights.Length == 0)
                    continue;

                var renderer = ResolveRenderer(renderers, part);
                if (renderer == null || renderer.sharedMesh == null)
                {
                    missingParts++;
                    continue;
                }
                if (!targets.Contains(renderer))
                    targets.Add(renderer);

                foreach (var item in part.weights)
                {
                    if (item == null || float.IsNaN(item.weight) || float.IsInfinity(item.weight))
                    {
                        missingWeights++;
                        continue;
                    }
                    var index = BlendShapeWeightApplyService.ResolveBlendShapeIndex(
                        renderer.sharedMesh,
                        item.name,
                        item.index);
                    if (index < 0 || index >= renderer.sharedMesh.blendShapeCount)
                    {
                        missingWeights++;
                        continue;
                    }
                    resolved.Add(new ResolvedWeight
                    {
                        renderer = renderer,
                        index = index,
                        weight = item.weight,
                    });
                }
            }

            if (resolved.Count == 0)
            {
                error = "rigged_blendshape_weights_none_resolved";
                BlenderSyncLog.Warn(
                    "RiggedBlendShape",
                    "weights_unresolved",
                    "None of the incoming rigged BlendShape weights matched a renderer.",
                    new Dictionary<string, object>
                    {
                        { "riggedObjectId", payload.riggedObjectId },
                        { "parts", payload.parts.Length },
                        { "missingParts", missingParts },
                        { "missingWeights", missingWeights },
                    });
                return false;
            }

            // Record every renderer before mutating it. Unity's AnimationWindow
            // uses this editor modification path to create blendShape curves.
            var undoTargets = new UnityEngine.Object[targets.Count];
            for (var i = 0; i < targets.Count; i++)
                undoTargets[i] = targets[i];
            Undo.RecordObjects(undoTargets, Tr("TriSync Sync Shape Keys"));

            foreach (var entry in resolved)
                entry.renderer.SetBlendShapeWeight(entry.index, entry.weight);

            foreach (var renderer in targets)
            {
                EditorUtility.SetDirty(renderer);
                if (PrefabUtility.IsPartOfPrefabInstance(renderer))
                    PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
            }
            EditorUtility.SetDirty(root);
            if (PrefabUtility.IsPartOfPrefabInstance(root))
                PrefabUtility.RecordPrefabInstancePropertyModifications(root);
            if (root.scene.IsValid())
                EditorSceneManager.MarkSceneDirty(root.scene);
            BlendShapeWeightEditorRepaintPump.Request(0.25);

            var partial = missingParts > 0 || missingWeights > 0;
            if (partial)
            {
                BlenderSyncLog.Warn(
                    "RiggedBlendShape",
                    "weights_partial",
                    "Some rigged BlendShape weights could not be applied.",
                    new Dictionary<string, object>
                    {
                        { "riggedObjectId", payload.riggedObjectId },
                        { "renderers", targets.Count },
                        { "applied", resolved.Count },
                        { "missingParts", missingParts },
                        { "missingWeights", missingWeights },
                    });
            }
            BlenderSyncReportStore.Add("Rigged Shape Keys", partial ? "WARN" : "OK", "weights_applied", new Dictionary<string, object>
            {
                { "riggedObjectId", payload.riggedObjectId },
                { "renderers", targets.Count },
                { "applied", resolved.Count },
                { "missingParts", missingParts },
                { "missingWeights", missingWeights },
                { "animationMode", AnimationMode.InAnimationMode() },
            });
            return true;
        }

        private sealed class ResolvedWeight
        {
            public SkinnedMeshRenderer renderer;
            public int index;
            public float weight;
        }

        private static SkinnedMeshRenderer ResolveRenderer(
            SkinnedMeshRenderer[] renderers,
            RiggedBlendShapePartPayload part)
        {
            if (renderers == null || part == null)
                return null;

            Mesh expectedMesh = null;
            if (!string.IsNullOrWhiteSpace(part.meshRef))
            {
                var resolver = new ResourceResolver();
                resolver.TryResolveMesh(part.meshRef, out expectedMesh, out _);
            }

            if (expectedMesh != null)
            {
                foreach (var renderer in renderers)
                {
                    if (renderer != null && renderer.sharedMesh == expectedMesh &&
                        string.Equals(renderer.gameObject.name, part.objectName, StringComparison.Ordinal))
                        return renderer;
                }
                foreach (var renderer in renderers)
                {
                    if (renderer != null && renderer.sharedMesh == expectedMesh)
                        return renderer;
                }
            }

            if (!string.IsNullOrWhiteSpace(part.objectName))
            {
                foreach (var renderer in renderers)
                {
                    if (renderer != null && string.Equals(renderer.gameObject.name, part.objectName, StringComparison.Ordinal))
                        return renderer;
                }
            }
            return null;
        }

        private static bool TryResolveManagedInstance(string riggedObjectId, out GameObject root)
        {
            root = null;
            var registry = new RiggedObjectRegistry();
            var db = registry.Load();
            var record = registry.FindByRiggedObjectId(db, riggedObjectId);
            var sceneObjectId = record?.managedInstance?.sceneObjectId;
            if (string.IsNullOrWhiteSpace(sceneObjectId))
                return false;
            if (!GlobalObjectId.TryParse(sceneObjectId, out var gid))
                return false;
            root = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid) as GameObject;
            return root != null;
        }
#endif

        private static string Normalize(string value)
        {
            var normalized = value?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }
    }
}
