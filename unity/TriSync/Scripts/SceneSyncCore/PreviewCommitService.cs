using System.Collections.Generic;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class PreviewCommitService
    {
        private readonly PreviewMeshService _previewMesh;
        private readonly MaterialReferenceApplyService _materialApply;
        private readonly ObjectResourceLinkRegistry _linkRegistry;

        public PreviewCommitService(
            PreviewMeshService previewMesh,
            MaterialReferenceApplyService materialApply,
            ObjectResourceLinkRegistry linkRegistry)
        {
            _previewMesh = previewMesh;
            _materialApply = materialApply;
            _linkRegistry = linkRegistry;
        }

        public void CommitPreview(GameObject target, SceneSyncPreviewCommitMessage msg)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("preview_commit_message_missing");
                return;
            }

            if (_previewMesh.TryCommitPreview(target, out var err))
            {
                BlenderSyncReportStore.Add(
                    "Preview Commit",
                    "OK",
                    $"pair={msg.pairId} meshRef={msg.meshRef} reason={msg.reason}",
                    new Dictionary<string, object>
                    {
                        { "pairId", msg.pairId },
                        { "meshRef", msg.meshRef },
                        { "reason", msg.reason },
                    });
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            if (err == "shared_mesh_content_fingerprint_missing")
                return;

            SceneSyncStateStore.MarkError(err ?? "preview_commit_failed", msg.pairId);
        }

        public void CommitMesh(GameObject target, SceneSyncPreviewCommitMeshMessage msg)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("preview_commit_mesh_message_missing");
                return;
            }

            var commitHash = $"commit_mesh_v1:{msg.vertexCount}:{msg.indexCount}:{msg.timestamp}";
            if (_previewMesh.TryCommitMeshBinary(
                msg.pairId,
                target,
                msg.meshRef,
                msg.buffers,
                msg.uvChannels,
                msg.color0,
                msg.subMeshes,
                msg.blendShapes,
                msg.meshContentFingerprint,
                msg.meshContentFingerprintNoUv,
                msg.meshContentFingerprintScope,
                commitHash,
                out var err))
            {
                ApplyMaterialRefsIfPresent(target, msg);
                BlenderSyncReportStore.Add(
                    "Preview Commit Mesh",
                    "OK",
                    $"pair={msg.pairId} v={msg.vertexCount} i={msg.indexCount} meshRef={msg.meshRef}",
                    new Dictionary<string, object>
                    {
                        { "pairId", msg.pairId },
                        { "meshRef", msg.meshRef },
                        { "vertices", msg.vertexCount },
                        { "indices", msg.indexCount },
                        { "reason", msg.reason },
                        { "buffers", msg.buffers != null ? msg.buffers.Length : 0 },
                        { "blendShapes", msg.blendShapes != null ? msg.blendShapes.Length : 0 },
                    });
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            if (err == "shared_mesh_content_fingerprint_missing")
                return;

            SceneSyncStateStore.MarkError(err ?? "preview_commit_mesh_failed", msg.pairId);
        }

        private void ApplyMaterialRefsIfPresent(GameObject target, SceneSyncPreviewCommitMeshMessage msg)
        {
            if (msg == null || msg.materialRefs == null)
                return;
            if (!_materialApply.TryApplyMany(target, msg.materialRefs, out var matErr, out var unresolvedRefs, warnOnUnresolved: false))
            {
                var waitForRefs = unresolvedRefs != null && unresolvedRefs.Length > 0;
                if (!waitForRefs)
                    BlenderSyncLog.Warn(
                        "PreviewCommit",
                        "material_references_failed",
                        matErr,
                        new Dictionary<string, object> { { "pairId", msg.pairId } });
                MaterialReferenceApplyService.RegisterPending(target, msg.pairId, msg.materialRefs, unresolvedRefs, "preview_commit_failed");
                return;
            }
            if (unresolvedRefs != null && unresolvedRefs.Length > 0)
                MaterialReferenceApplyService.RegisterPending(target, msg.pairId, msg.materialRefs, unresolvedRefs, "preview_commit_partial");
            _linkRegistry.UpdateReference(msg.pairId, target, "material", FirstNonEmpty(msg.materialRefs), msg.materialRefs);
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
