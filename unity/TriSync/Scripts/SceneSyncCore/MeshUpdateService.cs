using System.Collections.Generic;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class MeshUpdateService
    {
        private readonly MeshApplyService _meshApply;
        private readonly MeshForkApplyService _meshForkApply;
        private readonly MeshPreviewUpdateService _meshPreviewUpdate;
        private readonly PreviewMeshService _previewMesh;
        private readonly MaterialReferenceApplyService _materialApply;
        private readonly ObjectResourceLinkRegistry _linkRegistry;

        public MeshUpdateService(
            MeshApplyService meshApply,
            MeshForkApplyService meshForkApply,
            MeshPreviewUpdateService meshPreviewUpdate,
            PreviewMeshService previewMesh,
            MaterialReferenceApplyService materialApply,
            ObjectResourceLinkRegistry linkRegistry)
        {
            _meshApply = meshApply;
            _meshForkApply = meshForkApply;
            _meshPreviewUpdate = meshPreviewUpdate;
            _previewMesh = previewMesh;
            _materialApply = materialApply;
            _linkRegistry = linkRegistry;
        }

        public void ApplyFork(GameObject target, SceneSyncMeshForkMessage msg)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("mesh_fork_message_missing");
                return;
            }

            if (_meshForkApply.TryApply(msg.pairId, target, msg, out var err))
            {
                BlenderSyncReportStore.Add(
                    "Mesh Fork",
                    "OK",
                    $"pair={msg.pairId} targetMeshRef={msg.targetMeshRef}",
                    new Dictionary<string, object>
                    {
                        { "pairId", msg.pairId },
                        { "sourceMeshRef", msg.sourceMeshRef },
                        { "targetMeshRef", msg.targetMeshRef },
                        { "objectName", msg.objectName },
                        { "reason", msg.reason },
                    });
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            SceneSyncStateStore.MarkError(err ?? "mesh_fork_apply_failed", msg.pairId);
        }

        public void Apply(GameObject target, SceneSyncMeshUpdateMessage msg)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("mesh_update_message_missing");
                return;
            }

            if (MeshPreviewUpdateService.IsAutoPreview(msg))
            {
                if (_meshPreviewUpdate.TryApply(msg, target, out var previewErr))
                {
                    ApplyMaterialRefsIfPresent(target, msg.pairId, msg.materialRefs);
                    SceneSyncStateStore.MarkApplied(msg.pairId);
                    return;
                }
                SceneSyncStateStore.MarkError(previewErr ?? "mesh_preview_apply_failed", msg.pairId);
                return;
            }

            if (_meshApply.TryApply(msg.pairId, target, msg, out var err))
            {
                ReconcileRendererForCurrentMesh(target);
                ApplyMeshRefIfPresent(target, msg.pairId, msg.meshRef);
                ApplyMaterialRefsIfPresent(target, msg.pairId, msg.materialRefs);
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            SceneSyncStateStore.MarkError(err ?? "mesh_apply_failed", msg.pairId);
        }

        public void ApplyBinary(GameObject target, SceneSyncMeshUpdateBinaryMessage msg)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("mesh_update_binary_message_missing");
                return;
            }

            if (MeshPreviewUpdateService.IsAutoPreview(msg))
            {
                if (_meshPreviewUpdate.TryApply(msg, target, out var previewErr))
                {
                    ApplyMaterialRefsIfPresent(target, msg.pairId, msg.materialRefs);
                    SceneSyncStateStore.MarkApplied(msg.pairId);
                    return;
                }
                SceneSyncStateStore.MarkError(previewErr ?? "mesh_preview_binary_apply_failed", msg.pairId);
                return;
            }

            if (_meshApply.TryApplyBinary(msg.pairId, target, msg, out var err))
            {
                ReconcileRendererForCurrentMesh(target);
                ApplyMeshRefIfPresent(target, msg.pairId, msg.meshRef);
                ApplyMaterialRefsIfPresent(target, msg.pairId, msg.materialRefs);
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            SceneSyncStateStore.MarkError(err ?? "mesh_apply_binary_failed", msg.pairId);
        }

        public void ApplyPreviewPositions(GameObject target, SceneSyncMeshPositionsUpdateMessage msg)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("mesh_positions_update_message_missing");
                return;
            }

            var previewHash = $"positions:{msg.exportVertexCount}:{msg.timestamp}";
            if (_previewMesh.TryApplyPreviewPositionsBinary(msg.pairId, target, msg.buffer, previewHash, out var err))
            {
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            SceneSyncStateStore.MarkError(err ?? "mesh_positions_preview_apply_failed", msg.pairId);
        }

        public void ApplyPreviewUv(GameObject target, SceneSyncMeshUvUpdateMessage msg)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("mesh_uv_update_message_missing");
                return;
            }

            var previewHash = $"uv:{msg.exportVertexCount}:{msg.timestamp}";
            if (_previewMesh.TryApplyPreviewUvBinary(msg.pairId, target, msg.buffer, previewHash, out var err))
            {
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            SceneSyncStateStore.MarkError(err ?? "mesh_uv_preview_apply_failed", msg.pairId);
        }

        private static void ReconcileRendererForCurrentMesh(GameObject target)
        {
            var meshFilter = target.GetComponent<MeshFilter>();
            var skinnedMesh = target.GetComponent<SkinnedMeshRenderer>();
            var currentMesh = meshFilter != null ? meshFilter.sharedMesh : skinnedMesh != null ? skinnedMesh.sharedMesh : null;
            if (currentMesh != null)
                MeshReferenceApplyService.ReconcileRendererForMesh(target, currentMesh);
        }

        private void ApplyMaterialRefsIfPresent(GameObject target, string pairId, string[] materialRefs)
        {
            if (materialRefs == null)
                return;

            if (!_materialApply.TryApplyMany(target, materialRefs, out var matErr, out var unresolvedRefs, warnOnUnresolved: false))
            {
                var waitForRefs = unresolvedRefs != null && unresolvedRefs.Length > 0;
                if (!waitForRefs)
                    BlenderSyncLog.Warn(
                        "MeshUpdate",
                        "material_references_failed",
                        matErr,
                        new Dictionary<string, object> { { "pairId", pairId } });
                MaterialReferenceApplyService.RegisterPending(target, pairId, materialRefs, unresolvedRefs, "mesh_update_failed");
                return;
            }
            if (unresolvedRefs != null && unresolvedRefs.Length > 0)
                MaterialReferenceApplyService.RegisterPending(target, pairId, materialRefs, unresolvedRefs, "mesh_update_partial");

            var resourceRef = FirstNonEmpty(materialRefs);
            _linkRegistry.UpdateReference(pairId, target, "material", resourceRef, materialRefs);
        }

        private void ApplyMeshRefIfPresent(GameObject target, string pairId, string meshRef)
        {
            if (string.IsNullOrWhiteSpace(meshRef))
                return;
            _linkRegistry.UpdateReference(pairId, target, "mesh", meshRef);
        }

        private static string FirstNonEmpty(string[] values)
        {
            if (values == null)
                return null;
            for (var i = 0; i < values.Length; i++)
            {
                if (!string.IsNullOrWhiteSpace(values[i]))
                    return values[i].Trim();
            }
            return null;
        }
    }
}
