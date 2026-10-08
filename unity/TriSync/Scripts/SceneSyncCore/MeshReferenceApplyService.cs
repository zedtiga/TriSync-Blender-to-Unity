using System;
using System.Collections.Generic;
using BlenderSyncVNext.APT;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    // Mesh reference apply helper for the current minimal slice.
    // Keeps the instance/reference layer generic: resourceKind/resourceRef remain the protocol shape;
    // this service only handles kind=mesh.
    public sealed class MeshReferenceApplyService
    {
        private readonly ResourceResolver _resolver = new ResourceResolver();

        public static void ReconcileRendererForMesh(GameObject target, Mesh mesh)
        {
            if (target == null || mesh == null)
                return;
            var existingSkinned = target.GetComponent<SkinnedMeshRenderer>();
            var preservedBlendShapeWeights = mesh.blendShapeCount > 0
                ? CaptureBlendShapeWeights(existingSkinned)
                : null;
            if (mesh.blendShapeCount > 0)
            {
                var existingRenderer = target.GetComponent<MeshRenderer>();
                var preservedMaterials = existingRenderer != null ? existingRenderer.sharedMaterials : null;
                if ((preservedMaterials == null || preservedMaterials.Length == 0) && existingSkinned != null)
                    preservedMaterials = existingSkinned.sharedMaterials;
                var existingMeshFilter = target.GetComponent<MeshFilter>();
                if (existingMeshFilter != null) UnityEngine.Object.DestroyImmediate(existingMeshFilter);
                if (existingRenderer != null) UnityEngine.Object.DestroyImmediate(existingRenderer);
                var skinned = target.GetComponent<SkinnedMeshRenderer>();
                if (skinned == null) skinned = target.AddComponent<SkinnedMeshRenderer>();
                skinned.sharedMesh = mesh;
                SkinnedMeshBoundsUtility.ApplySharedMeshBounds(skinned);
                if (preservedMaterials != null && preservedMaterials.Length > 0)
                    skinned.sharedMaterials = preservedMaterials;
                RestoreBlendShapeWeights(skinned, preservedBlendShapeWeights);
                return;
            }

            var oldSkinned = target.GetComponent<SkinnedMeshRenderer>();
            var preservedSkinnedMaterials = oldSkinned != null ? oldSkinned.sharedMaterials : null;
            if (oldSkinned != null) UnityEngine.Object.DestroyImmediate(oldSkinned);
            var meshFilter = target.GetComponent<MeshFilter>();
            if (meshFilter == null) meshFilter = target.AddComponent<MeshFilter>();
            var renderer = target.GetComponent<MeshRenderer>();
            if (renderer == null) renderer = target.AddComponent<MeshRenderer>();
            meshFilter.sharedMesh = mesh;
            if (preservedSkinnedMaterials != null && preservedSkinnedMaterials.Length > 0)
                renderer.sharedMaterials = preservedSkinnedMaterials;
        }

        private static Dictionary<string, float> CaptureBlendShapeWeights(SkinnedMeshRenderer renderer)
        {
            var weights = new Dictionary<string, float>(StringComparer.Ordinal);
            if (renderer == null || renderer.sharedMesh == null)
                return weights;

            var mesh = renderer.sharedMesh;
            for (var index = 0; index < mesh.blendShapeCount; index++)
            {
                var name = mesh.GetBlendShapeName(index);
                if (string.IsNullOrWhiteSpace(name) || weights.ContainsKey(name))
                    continue;
                weights[name] = renderer.GetBlendShapeWeight(index);
            }
            return weights;
        }

        private static void RestoreBlendShapeWeights(
            SkinnedMeshRenderer renderer,
            Dictionary<string, float> weights)
        {
            if (renderer == null || renderer.sharedMesh == null || weights == null || weights.Count == 0)
                return;

            var mesh = renderer.sharedMesh;
            foreach (var item in weights)
            {
                var index = mesh.GetBlendShapeIndex(item.Key);
                if (index >= 0)
                    renderer.SetBlendShapeWeight(index, item.Value);
            }
        }

        public static int ReconcileAllRenderersForMesh(Mesh mesh)
        {
            if (mesh == null)
                return 0;
            var count = 0;
            foreach (var filter in FindAllObjects<MeshFilter>())
            {
                if (filter != null && filter.sharedMesh == mesh)
                {
                    ReconcileRendererForMesh(filter.gameObject, mesh);
                    count++;
                }
            }
            foreach (var skinned in FindAllObjects<SkinnedMeshRenderer>())
            {
                if (skinned != null && skinned.sharedMesh == mesh)
                {
                    ReconcileRendererForMesh(skinned.gameObject, mesh);
                    count++;
                }
            }
            return count;
        }

        private static T[] FindAllObjects<T>() where T : UnityEngine.Object
        {
#if UNITY_6000_4_OR_NEWER
            return UnityEngine.Object.FindObjectsByType<T>(FindObjectsInactive.Include);
#else
            return UnityEngine.Object.FindObjectsByType<T>(FindObjectsInactive.Include, FindObjectsSortMode.None);
#endif
        }

        public bool TryApply(GameObject target, string resourceRef, out string error)
            => TryApply(target, pairId: null, resourceRef, out error);

        public bool TryApply(GameObject target, string pairId, string resourceRef, out string error)
        {
            error = null;
            if (target == null)
            {
                error = "target is null";
                return false;
            }
            var normalizedRef = (resourceRef ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(normalizedRef))
            {
                error = "resourceRef is missing";
                return false;
            }

            if (!_resolver.TryResolveMesh(normalizedRef, out var mesh, out error) || mesh == null)
                return false;

            ReconcileRendererForMesh(target, mesh);
#if UNITY_EDITOR
            MeshApplyService.RegisterPairMeshBinding(pairId, mesh);
#endif
            return true;
        }
    }
}
