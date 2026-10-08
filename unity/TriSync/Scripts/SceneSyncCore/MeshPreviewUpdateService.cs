using System;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class MeshPreviewUpdateService
    {
        private readonly PreviewMeshService _previewMesh;

        public MeshPreviewUpdateService(PreviewMeshService previewMesh)
        {
            _previewMesh = previewMesh;
        }

        public static bool IsAutoPreview(SceneSyncMeshUpdateMessage msg)
        {
            return IsPreviewSyncHint(msg?.sourceHint);
        }

        public static bool IsAutoPreview(SceneSyncMeshUpdateBinaryMessage msg)
        {
            return IsPreviewSyncHint(msg?.sourceHint);
        }

        private static bool IsPreviewSyncHint(string sourceHint)
        {
            return string.Equals(sourceHint, "auto_sync", StringComparison.OrdinalIgnoreCase)
                || string.Equals(sourceHint, "manual_sync", StringComparison.OrdinalIgnoreCase);
        }

        public bool TryApply(SceneSyncMeshUpdateMessage msg, GameObject target, out string error)
        {
            error = null;
            if (msg == null)
            {
                error = "message is null";
                return false;
            }
            if (target == null)
            {
                error = "target is null";
                return false;
            }
            if (msg.vertices == null || msg.triangles == null || msg.vertices.Length % 3 != 0)
            {
                error = "invalid mesh arrays";
                return false;
            }

            var vCount = msg.vertices.Length / 3;
            var vertices = new Vector3[vCount];
            for (int i = 0, j = 0; i < vCount; i++, j += 3)
                vertices[i] = new Vector3(msg.vertices[j], msg.vertices[j + 1], msg.vertices[j + 2]);

            Vector3[] normals = null;
            if (msg.normals != null && msg.normals.Length == msg.vertices.Length)
            {
                normals = new Vector3[vCount];
                for (int i = 0, j = 0; i < vCount; i++, j += 3)
                    normals[i] = new Vector3(msg.normals[j], msg.normals[j + 1], msg.normals[j + 2]);
            }

            Vector2[] uvs = null;
            if (msg.uv != null && msg.uv.Length == vCount * 2)
            {
                uvs = new Vector2[vCount];
                for (int i = 0, j = 0; i < vCount; i++, j += 2)
                    uvs[i] = new Vector2(msg.uv[j], msg.uv[j + 1]);
            }

            var previewHash = $"json:{vCount}:{msg.triangles.Length}:{msg.timestamp}";
            var ok = _previewMesh.TryApplyPreviewMapped(msg.pairId, target, msg.meshRef, vertices, msg.triangles, normals, uvs, null, null, msg.subMeshes, null, msg.blendShapes, previewHash, out error);
            if (ok)
                _previewMesh.SetMeshContentFingerprints(msg.pairId, msg.meshContentFingerprint, msg.meshContentFingerprintNoUv, msg.meshContentFingerprintScope, IsPreviewCommitFingerprintResponse(msg.rebuildReason));
            return ok;
        }

        public bool TryApply(SceneSyncMeshUpdateBinaryMessage msg, GameObject target, out string error)
        {
            error = null;
            if (msg == null)
            {
                error = "message is null";
                return false;
            }
            if (target == null)
            {
                error = "target is null";
                return false;
            }

            var previewHash = $"binary:{msg.vertexCount}:{msg.indexCount}:{msg.timestamp}";
            var ok = _previewMesh.TryApplyPreviewBinary(msg.pairId, target, msg.meshRef, msg.buffers, msg.uvChannels, msg.color0, msg.subMeshes, msg.blendShapes, previewHash, out error);
            if (ok)
                _previewMesh.SetMeshContentFingerprints(msg.pairId, msg.meshContentFingerprint, msg.meshContentFingerprintNoUv, msg.meshContentFingerprintScope, IsPreviewCommitFingerprintResponse(msg.rebuildReason));
            return ok;
        }

        private static bool IsPreviewCommitFingerprintResponse(string rebuildReason)
        {
            return !string.IsNullOrWhiteSpace(rebuildReason)
                && rebuildReason.StartsWith("mesh_content_fingerprint_request:preview", StringComparison.OrdinalIgnoreCase);
        }
    }
}
