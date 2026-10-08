using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.SessionCore;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    /// <summary>
    /// Owns the preview/review mesh lifecycle used by automatic mesh sync.
    /// This service deliberately keeps state in memory instead of adding components to scene objects.
    /// Manual import/update remains responsible for persistent Mesh assets.
    /// </summary>
    public sealed class PreviewMeshService
    {
        private sealed class RuntimePreviewState
        {
            public string pairId;
            public string meshRef;
            public string previewHash;
            public string committedAssetGuid;
            public string committedAssetPath;
            public bool hasUncommittedPreview;
            public long lastPreviewAt;
            public Mesh previewMesh;
            public GameObject target;
            public string meshContentFingerprint;
            public string meshContentFingerprintNoUv;
            public string meshContentFingerprintScope;
            public bool meshContentFingerprintRequestSent;
            public long lastMeshContentFingerprintRequestAt;
        }

        private const double DefaultPreviewWatchdogCommitTimeoutSeconds = 10.0;
        private static double _previewWatchdogCommitTimeoutSeconds = DefaultPreviewWatchdogCommitTimeoutSeconds;
        private static readonly object StateByPairIdLock = new object();
        private static readonly Dictionary<string, RuntimePreviewState> StateByPairId = new Dictionary<string, RuntimePreviewState>();
        private readonly AptRepository _aptRepository = new AptRepository();
        private readonly ObjectBindingRegistry _bindingRegistry = new ObjectBindingRegistry();
        private readonly ObjectResourceLinkRegistry _linkRegistry = new ObjectResourceLinkRegistry();

#if UNITY_EDITOR
        static PreviewMeshService()
        {
            EditorApplication.update -= PumpPreviewWatchdogStatic;
            EditorApplication.update += PumpPreviewWatchdogStatic;
        }
#endif

        public static void SetPreviewWatchdogCommitTimeoutSeconds(float seconds)
        {
            if (float.IsNaN(seconds) || float.IsInfinity(seconds) || seconds <= 0f)
                seconds = (float)DefaultPreviewWatchdogCommitTimeoutSeconds;
            _previewWatchdogCommitTimeoutSeconds = Mathf.Clamp(seconds, 1f, 60f);
        }

        public void SetMeshContentFingerprint(string pairId, string meshContentFingerprint)
        {
            SetMeshContentFingerprints(pairId, meshContentFingerprint, null, null);
        }

        public void SetMeshContentFingerprints(string pairId, string meshContentFingerprint, string meshContentFingerprintNoUv, string meshContentFingerprintScope)
        {
            SetMeshContentFingerprints(pairId, meshContentFingerprint, meshContentFingerprintNoUv, meshContentFingerprintScope, commitAfterSet: false);
        }

        public void SetMeshContentFingerprints(string pairId, string meshContentFingerprint, string meshContentFingerprintNoUv, string meshContentFingerprintScope, bool commitAfterSet)
        {
            pairId = NormalizePairId(pairId);
            meshContentFingerprint = NormalizeFingerprint(meshContentFingerprint);
            meshContentFingerprintNoUv = NormalizeFingerprint(meshContentFingerprintNoUv);
            meshContentFingerprintScope = NormalizeFingerprint(meshContentFingerprintScope);
            if (string.IsNullOrWhiteSpace(pairId) || (string.IsNullOrWhiteSpace(meshContentFingerprint) && string.IsNullOrWhiteSpace(meshContentFingerprintNoUv)))
                return;
            GameObject commitTarget = null;
            lock (StateByPairIdLock)
            {
                if (StateByPairId.TryGetValue(pairId, out var state) && state != null)
                {
                    if (!string.IsNullOrWhiteSpace(meshContentFingerprint))
                        state.meshContentFingerprint = meshContentFingerprint;
                    if (!string.IsNullOrWhiteSpace(meshContentFingerprintNoUv))
                        state.meshContentFingerprintNoUv = meshContentFingerprintNoUv;
                    state.meshContentFingerprintScope = meshContentFingerprintScope;
                    state.meshContentFingerprintRequestSent = false;
                    if (commitAfterSet && state.hasUncommittedPreview && state.previewMesh != null)
                        commitTarget = state.target;
                }
            }
            if (commitTarget != null)
            {
                if (TryCommitPreview(commitTarget, out var error))
                    BlenderSyncLog.Trace(
                        "PreviewMesh",
                        "commit_after_fingerprint",
                        () => "Committed preview after receiving the mesh fingerprint.",
                        () => new Dictionary<string, object> { { "pairId", pairId } });
                else
                    BlenderSyncLog.Warn(
                        "PreviewMesh",
                        "commit_after_fingerprint_failed",
                        error,
                        new Dictionary<string, object> { { "pairId", pairId } });
            }
        }

        public bool TryApplyPreview(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] vertices,
            int[] triangles,
            Vector3[] normals,
            Vector2[] uv0,
            string previewHash,
            out string error)
        {
            return TryApplyPreview(pairId, target, meshRef, vertices, triangles, normals, uv0, null, null, previewHash, out error);
        }

        public bool TryApplyPreview(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] vertices,
            int[] triangles,
            Vector3[] normals,
            Vector2[] uv0,
            Dictionary<int, Vector2[]> explicitUvChannels,
            Color32[] colors,
            string previewHash,
            out string error)
        {
            return TryApplyPreview(pairId, target, meshRef, vertices, triangles, normals, uv0, explicitUvChannels, colors, null, previewHash, out error);
        }

        public bool TryApplyPreview(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] vertices,
            int[] triangles,
            Vector3[] normals,
            Vector2[] uv0,
            Dictionary<int, Vector2[]> explicitUvChannels,
            Color32[] colors,
            SceneSyncMeshSubMesh[] subMeshes,
            string previewHash,
            out string error)
        {
            return TryApplyPreview(pairId, target, meshRef, vertices, triangles, normals, uv0, explicitUvChannels, colors, subMeshes, null, null, previewHash, out error);
        }

        public bool TryApplyPreview(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] vertices,
            int[] triangles,
            Vector3[] normals,
            Vector2[] uv0,
            Dictionary<int, Vector2[]> explicitUvChannels,
            Color32[] colors,
            SceneSyncMeshSubMesh[] subMeshes,
            SceneSyncBinaryBlendShape[] binaryBlendShapes,
            SceneSyncMeshBlendShape[] blendShapes,
            string previewHash,
            out string error)
        {
            error = null;
            if (string.IsNullOrWhiteSpace(pairId))
            {
                error = "pairId is missing";
                return false;
            }
            pairId = NormalizePairId(pairId);
            if (target == null)
            {
                error = "target is null";
                return false;
            }
            if (vertices == null || triangles == null)
            {
                error = "vertices/triangles missing";
                return false;
            }

            var sourceMeshForUvFallback = GetCurrentRendererMesh(target);
            var meshFilter = target.GetComponent<MeshFilter>();
            if (meshFilter == null) meshFilter = target.AddComponent<MeshFilter>();
            if (target.GetComponent<MeshRenderer>() == null && target.GetComponent<SkinnedMeshRenderer>() == null) target.AddComponent<MeshRenderer>();

            var state = EnsureRuntimeState(pairId, target, meshRef);
            if (sourceMeshForUvFallback == null)
                sourceMeshForUvFallback = meshFilter.sharedMesh;
            CaptureCommittedMeshIfNeeded(target, state);

            var incomingHasUsableUv = uv0 != null && uv0.Length == vertices.Length;
            var fallbackUv = sourceMeshForUvFallback != null ? sourceMeshForUvFallback.uv : null;
            var fallbackHasUsableUv = fallbackUv != null && fallbackUv.Length == vertices.Length;
            if (!incomingHasUsableUv && !fallbackHasUsableUv)
            {
                BlenderSyncLog.Trace(
                    "PreviewMesh",
                    "uv_pending",
                    () => "Preview has no usable UV data yet; waiting for a UV-only update.",
                    () => new Dictionary<string, object>
                    {
                        { "pairId", pairId },
                        { "incomingUvCount", uv0 != null ? uv0.Length : 0 },
                        { "fallbackUvCount", fallbackUv != null ? fallbackUv.Length : 0 },
                        { "vertexCount", vertices.Length },
                    });
            }

            var uvChannels = explicitUvChannels != null && explicitUvChannels.Count > 0
                ? new Dictionary<int, Vector2[]>(explicitUvChannels)
                : new Dictionary<int, Vector2[]>();
            if (uvChannels.Count == 0 && incomingHasUsableUv)
                uvChannels[0] = uv0;
            else if (uvChannels.Count == 0 && fallbackHasUsableUv)
                uvChannels[0] = fallbackUv;

            var previewMesh = state.previewMesh;
            if (previewMesh == null)
            {
                previewMesh = new Mesh { name = string.IsNullOrWhiteSpace(meshRef) ? $"PreviewMesh_{pairId}" : $"Preview_{meshRef}" };
                previewMesh.hideFlags = HideFlags.HideAndDontSave;
                state.previewMesh = previewMesh;
            }

            previewMesh.Clear();
            previewMesh.indexFormat = vertices.Length > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16;
            previewMesh.vertices = vertices;
            if (!MeshApplyService.ApplySubMeshes(previewMesh, subMeshes, "triangles", mapTriangleWinding: false))
                previewMesh.triangles = triangles;
            if (normals != null && normals.Length == vertices.Length)
                previewMesh.normals = normals;
            else
                previewMesh.RecalculateNormals();
            MeshApplyService.ApplyUvChannels(previewMesh, uvChannels, null, vertices.Length);
            MeshApplyService.ApplyColors(previewMesh, colors, vertices.Length);
            if (binaryBlendShapes != null)
                MeshApplyService.ApplyBinaryBlendShapes(previewMesh, binaryBlendShapes, out _);
            else if (blendShapes != null)
                MeshApplyService.ApplyBlendShapes(previewMesh, blendShapes, out _);
            else
                CopyBlendShapesIfCompatible(sourceMeshForUvFallback, previewMesh, "preview");
            MeshApplyService.RecalculateTangentsIfPossible(previewMesh);
            previewMesh.RecalculateBounds();

            ApplyMeshToTargetRenderer(target, previewMesh);
            state.pairId = pairId;
            state.meshRef = meshRef;
            state.previewHash = previewHash;
            state.meshContentFingerprint = null;
            state.meshContentFingerprintNoUv = null;
            state.meshContentFingerprintScope = null;
            state.meshContentFingerprintRequestSent = false;
            state.lastMeshContentFingerprintRequestAt = 0;
            state.hasUncommittedPreview = true;
            state.lastPreviewAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            return true;
        }

        public bool TryApplyPreviewMapped(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] blenderVertices,
            int[] blenderTriangles,
            Vector3[] blenderNormals,
            Vector2[] uv0,
            string previewHash,
            out string error)
        {
            return TryApplyPreviewMapped(pairId, target, meshRef, blenderVertices, blenderTriangles, blenderNormals, uv0, null, null, previewHash, out error);
        }

        public bool TryApplyPreviewMapped(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] blenderVertices,
            int[] blenderTriangles,
            Vector3[] blenderNormals,
            Vector2[] uv0,
            Dictionary<int, Vector2[]> uvChannels,
            Color32[] colors,
            string previewHash,
            out string error)
        {
            return TryApplyPreviewMapped(pairId, target, meshRef, blenderVertices, blenderTriangles, blenderNormals, uv0, uvChannels, colors, null, previewHash, out error);
        }

        public bool TryApplyPreviewMapped(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] blenderVertices,
            int[] blenderTriangles,
            Vector3[] blenderNormals,
            Vector2[] uv0,
            Dictionary<int, Vector2[]> uvChannels,
            Color32[] colors,
            SceneSyncMeshSubMesh[] subMeshes,
            string previewHash,
            out string error)
        {
            return TryApplyPreviewMapped(pairId, target, meshRef, blenderVertices, blenderTriangles, blenderNormals, uv0, uvChannels, colors, subMeshes, null, null, previewHash, out error);
        }

        public bool TryApplyPreviewMapped(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] blenderVertices,
            int[] blenderTriangles,
            Vector3[] blenderNormals,
            Vector2[] uv0,
            Dictionary<int, Vector2[]> uvChannels,
            Color32[] colors,
            SceneSyncMeshSubMesh[] subMeshes,
            SceneSyncBinaryBlendShape[] binaryBlendShapes,
            SceneSyncMeshBlendShape[] blendShapes,
            string previewHash,
            out string error)
        {
            error = null;
            if (blenderVertices == null || blenderTriangles == null)
            {
                error = "vertices/triangles missing";
                return false;
            }
            if (!ValidateTriangleIndices(blenderTriangles, blenderVertices.Length, out error))
                return false;

            var vertices = new Vector3[blenderVertices.Length];
            for (var i = 0; i < blenderVertices.Length; i++)
                vertices[i] = SceneSyncTransformMapper.MapPoint(blenderVertices[i]);

            var normals = blenderNormals != null && blenderNormals.Length == blenderVertices.Length
                ? new Vector3[blenderNormals.Length]
                : null;
            if (normals != null)
            {
                for (var i = 0; i < blenderNormals.Length; i++)
                    normals[i] = SceneSyncTransformMapper.MapDirection(blenderNormals[i]).normalized;
            }

            var triangles = SceneSyncTransformMapper.MapTriangles(blenderTriangles);
            var mappedSubMeshes = MapSubMeshTriangleWinding(subMeshes);
            return TryApplyPreview(pairId, target, meshRef, vertices, triangles, normals, uv0, uvChannels, colors, mappedSubMeshes, binaryBlendShapes, blendShapes, previewHash, out error);
        }

        public bool TryApplyPreviewBinary(
            string pairId,
            GameObject target,
            string meshRef,
            SceneSyncBinaryBuffer[] buffers,
            string previewHash,
            out string error)
        {
            return TryApplyPreviewBinary(pairId, target, meshRef, buffers, null, null, previewHash, out error);
        }

        public bool TryApplyPreviewBinary(
            string pairId,
            GameObject target,
            string meshRef,
            SceneSyncBinaryBuffer[] buffers,
            SceneSyncBinaryUvChannel[] uvChannelPayloads,
            SceneSyncBinaryBuffer color0Buffer,
            string previewHash,
            out string error)
        {
            return TryApplyPreviewBinary(pairId, target, meshRef, buffers, uvChannelPayloads, color0Buffer, null, previewHash, out error);
        }

        public bool TryApplyPreviewBinary(
            string pairId,
            GameObject target,
            string meshRef,
            SceneSyncBinaryBuffer[] buffers,
            SceneSyncBinaryUvChannel[] uvChannelPayloads,
            SceneSyncBinaryBuffer color0Buffer,
            SceneSyncMeshSubMesh[] subMeshes,
            string previewHash,
            out string error)
        {
            return TryApplyPreviewBinary(pairId, target, meshRef, buffers, uvChannelPayloads, color0Buffer, subMeshes, null, previewHash, out error);
        }

        public bool TryApplyPreviewBinary(
            string pairId,
            GameObject target,
            string meshRef,
            SceneSyncBinaryBuffer[] buffers,
            SceneSyncBinaryUvChannel[] uvChannelPayloads,
            SceneSyncBinaryBuffer color0Buffer,
            SceneSyncMeshSubMesh[] subMeshes,
            SceneSyncBinaryBlendShape[] blendShapes,
            string previewHash,
            out string error)
        {
            error = null;
            if (buffers == null || buffers.Length == 0)
            {
                error = "buffers missing";
                return false;
            }

            if (!TryReadBinaryBuffers(buffers, out var blenderVertices, out var blenderTriangles, out var blenderNormals, out var uv0, out error))
                return false;

            var uvChannels = MeshApplyService.ReadBinaryUvChannels(uvChannelPayloads, blenderVertices != null ? blenderVertices.Length : 0, out var uvChannelError);
            var colors = MeshApplyService.ReadColor32Buffer(color0Buffer, blenderVertices != null ? blenderVertices.Length : 0, out var colorError);
#if UNITY_EDITOR
            if (!string.IsNullOrWhiteSpace(uvChannelError))
                BlenderSyncLog.Warn(
                    "PreviewMesh",
                    "uv_channel_read_failed",
                    uvChannelError,
                    new Dictionary<string, object> { { "pairId", pairId } });
            if (!string.IsNullOrWhiteSpace(colorError))
                BlenderSyncLog.Warn(
                    "PreviewMesh",
                    "color_read_failed",
                    colorError,
                    new Dictionary<string, object> { { "pairId", pairId } });
#endif
            return TryApplyPreviewMapped(pairId, target, meshRef, blenderVertices, blenderTriangles, blenderNormals, uv0, uvChannels, colors, subMeshes, blendShapes, null, previewHash, out error);
        }

        public bool TryApplyPreviewUvBinary(
            string pairId,
            GameObject target,
            SceneSyncBinaryBuffer buffer,
            string previewHash,
            out string error)
        {
            error = null;
            if (string.IsNullOrWhiteSpace(pairId))
            {
                error = "pairId is missing";
                return false;
            }
            pairId = NormalizePairId(pairId);
            if (target == null)
            {
                error = "target is null";
                return false;
            }
            if (buffer == null)
            {
                error = "uv buffer missing";
                return false;
            }
            var uv = ReadVector2Buffer(buffer, out error);
            if (!string.IsNullOrWhiteSpace(error) || uv == null)
                return false;

            if (!TryGetState(target, out var state) || state == null || state.previewMesh == null)
            {
                if (!TryCreatePreviewStateFromCurrentMesh(pairId, target, uv.Length, "uv_update", out state, out error))
                    return false;
            }
            if (uv.Length != state.previewMesh.vertexCount)
            {
                error = $"uv_count_mismatch payload={uv.Length} preview={state.previewMesh.vertexCount}";
                return false;
            }

            state.previewMesh.uv = uv;
            MeshApplyService.RecalculateTangentsIfPossible(state.previewMesh);
            state.previewMesh.RecalculateBounds();
            state.previewHash = previewHash;
            state.meshContentFingerprint = null;
            state.meshContentFingerprintNoUv = null;
            state.meshContentFingerprintScope = null;
            state.meshContentFingerprintRequestSent = false;
            state.lastMeshContentFingerprintRequestAt = 0;
            state.hasUncommittedPreview = true;
            state.lastPreviewAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            return true;
        }

        public bool TryApplyPreviewPositionsBinary(
            string pairId,
            GameObject target,
            SceneSyncBinaryBuffer buffer,
            string previewHash,
            out string error)
        {
            error = null;
            if (string.IsNullOrWhiteSpace(pairId))
            {
                error = "pairId is missing";
                return false;
            }
            pairId = NormalizePairId(pairId);
            if (target == null)
            {
                error = "target is null";
                return false;
            }
            if (buffer == null)
            {
                error = "position buffer missing";
                return false;
            }
            var blenderVertices = ReadVector3Buffer(buffer, out error);
            if (!string.IsNullOrWhiteSpace(error) || blenderVertices == null)
                return false;

            if (!TryGetState(target, out var state) || state == null || state.previewMesh == null)
            {
                if (!TryCreatePreviewStateFromCurrentMesh(pairId, target, blenderVertices.Length, "positions_update", out state, out error))
                    return false;
            }
            if (blenderVertices.Length != state.previewMesh.vertexCount)
            {
                error = $"vertex_count_mismatch payload={blenderVertices.Length} preview={state.previewMesh.vertexCount}";
                return false;
            }

            var vertices = new Vector3[blenderVertices.Length];
            for (var i = 0; i < blenderVertices.Length; i++)
                vertices[i] = SceneSyncTransformMapper.MapPoint(blenderVertices[i]);

            var sourceMesh = LoadCommittedMesh(state);
            CopyBlendShapesIfCompatible(sourceMesh, state.previewMesh, "positions_preview");
            state.previewMesh.vertices = vertices;
            state.previewMesh.RecalculateBounds();
            state.previewMesh.RecalculateNormals();
            MeshApplyService.RecalculateTangentsIfPossible(state.previewMesh);
            ApplyMeshToTargetRenderer(target, state.previewMesh);
            state.previewHash = previewHash;
            state.meshContentFingerprint = null;
            state.meshContentFingerprintNoUv = null;
            state.meshContentFingerprintScope = null;
            state.meshContentFingerprintRequestSent = false;
            state.lastMeshContentFingerprintRequestAt = 0;
            state.hasUncommittedPreview = true;
            state.lastPreviewAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            return true;
        }

        public bool TryCommitPreview(GameObject target, out string error)
        {
            error = null;
            if (target == null)
            {
                error = "target is null";
                return false;
            }
            if (!TryGetState(target, out var state) || state == null || !state.hasUncommittedPreview)
                return true;
            if (state.previewMesh == null)
            {
                error = "preview mesh missing";
                return false;
            }
            if (GetCurrentRendererMesh(target) != state.previewMesh)
            {
                DestroyPreviewMesh(state.previewMesh);
                RemoveRuntimeState(state.pairId);
                BlenderSyncLog.Trace(
                    "PreviewMesh",
                    "commit_skipped_stale",
                    () => "Discarded a stale preview instead of committing it.",
                    () => new Dictionary<string, object> { { "pairId", state.pairId } });
                return true;
            }

#if !UNITY_EDITOR
            error = "commit_preview_requires_editor";
            return false;
#else
            return TryCommitMeshData(target, state.previewMesh, state, "preview", out error);
#endif
        }

        public bool TryCommitMeshMapped(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] blenderVertices,
            int[] blenderTriangles,
            Vector3[] blenderNormals,
            Vector2[] uv0,
            string commitHash,
            out string error)
        {
            return TryCommitMeshMapped(pairId, target, meshRef, blenderVertices, blenderTriangles, blenderNormals, uv0, null, null, null, null, null, null, null, commitHash, out error);
        }

        public bool TryCommitMeshMapped(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] blenderVertices,
            int[] blenderTriangles,
            Vector3[] blenderNormals,
            Vector2[] uv0,
            Dictionary<int, Vector2[]> explicitUvChannels,
            Color32[] colors,
            string commitHash,
            out string error)
        {
            return TryCommitMeshMapped(pairId, target, meshRef, blenderVertices, blenderTriangles, blenderNormals, uv0, explicitUvChannels, colors, null, null, null, null, null, commitHash, out error);
        }

        public bool TryCommitMeshMapped(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] blenderVertices,
            int[] blenderTriangles,
            Vector3[] blenderNormals,
            Vector2[] uv0,
            Dictionary<int, Vector2[]> explicitUvChannels,
            Color32[] colors,
            SceneSyncMeshSubMesh[] subMeshes,
            string commitHash,
            out string error)
        {
            return TryCommitMeshMapped(pairId, target, meshRef, blenderVertices, blenderTriangles, blenderNormals, uv0, explicitUvChannels, colors, subMeshes, null, null, null, null, commitHash, out error);
        }

        public bool TryCommitMeshMapped(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] blenderVertices,
            int[] blenderTriangles,
            Vector3[] blenderNormals,
            Vector2[] uv0,
            Dictionary<int, Vector2[]> explicitUvChannels,
            Color32[] colors,
            SceneSyncMeshSubMesh[] subMeshes,
            string meshContentFingerprint,
            string commitHash,
            out string error)
        {
            return TryCommitMeshMapped(pairId, target, meshRef, blenderVertices, blenderTriangles, blenderNormals, uv0, explicitUvChannels, colors, subMeshes, null, meshContentFingerprint, null, null, commitHash, out error);
        }

        public bool TryCommitMeshMapped(
            string pairId,
            GameObject target,
            string meshRef,
            Vector3[] blenderVertices,
            int[] blenderTriangles,
            Vector3[] blenderNormals,
            Vector2[] uv0,
            Dictionary<int, Vector2[]> explicitUvChannels,
            Color32[] colors,
            SceneSyncMeshSubMesh[] subMeshes,
            SceneSyncBinaryBlendShape[] blendShapes,
            string meshContentFingerprint,
            string meshContentFingerprintNoUv,
            string meshContentFingerprintScope,
            string commitHash,
            out string error)
        {
            error = null;
            if (string.IsNullOrWhiteSpace(pairId))
            {
                error = "pairId is missing";
                return false;
            }
            pairId = NormalizePairId(pairId);
            if (target == null)
            {
                error = "target is null";
                return false;
            }
            if (blenderVertices == null || blenderTriangles == null)
            {
                error = "vertices/triangles missing";
                return false;
            }
            if (!ValidateTriangleIndices(blenderTriangles, blenderVertices.Length, out error))
                return false;

            var vertices = new Vector3[blenderVertices.Length];
            for (var i = 0; i < blenderVertices.Length; i++)
                vertices[i] = SceneSyncTransformMapper.MapPoint(blenderVertices[i]);

            var normals = blenderNormals != null && blenderNormals.Length == blenderVertices.Length
                ? new Vector3[blenderNormals.Length]
                : null;
            if (normals != null)
            {
                for (var i = 0; i < blenderNormals.Length; i++)
                    normals[i] = SceneSyncTransformMapper.MapDirection(blenderNormals[i]).normalized;
            }

            var triangles = SceneSyncTransformMapper.MapTriangles(blenderTriangles);
            var finalMesh = new Mesh { name = string.IsNullOrWhiteSpace(meshRef) ? $"FinalCommit_{pairId}" : $"FinalCommit_{meshRef}" };
            finalMesh.hideFlags = HideFlags.HideAndDontSave;
            finalMesh.indexFormat = vertices.Length > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16;
            finalMesh.vertices = vertices;
            if (!MeshApplyService.ApplySubMeshes(finalMesh, subMeshes, "triangles", mapTriangleWinding: true))
                finalMesh.triangles = triangles;
            if (normals != null && normals.Length == vertices.Length)
                finalMesh.normals = normals;
            else
                finalMesh.RecalculateNormals();
            MeshApplyService.ApplyUvChannels(finalMesh, explicitUvChannels, uv0, vertices.Length);
            MeshApplyService.ApplyColors(finalMesh, colors, vertices.Length);

            var state = EnsureRuntimeState(pairId, target, meshRef);
            state.previewHash = commitHash;
            state.meshContentFingerprint = NormalizeFingerprint(meshContentFingerprint);
            state.meshContentFingerprintNoUv = NormalizeFingerprint(meshContentFingerprintNoUv);
            state.meshContentFingerprintScope = NormalizeFingerprint(meshContentFingerprintScope);
            CaptureCommittedMeshIfNeeded(target, state);
            if (blendShapes != null)
                MeshApplyService.ApplyBinaryBlendShapes(finalMesh, blendShapes, out _);
            else
            {
                var committedForBlendShapes = LoadCommittedMesh(state) ?? GetCurrentRendererMesh(target);
                CopyBlendShapesIfCompatible(committedForBlendShapes, finalMesh, "final_snapshot");
            }
            MeshApplyService.RecalculateTangentsIfPossible(finalMesh);
            finalMesh.RecalculateBounds();

#if !UNITY_EDITOR
            error = "commit_preview_requires_editor";
            DestroyPreviewMesh(finalMesh);
            return false;
#else
            return TryCommitMeshData(target, finalMesh, state, "final_snapshot", out error, destroySourceAfterCommit: true);
#endif
        }

        private static bool ValidateTriangleIndices(int[] triangles, int vertexCount, out string error)
        {
            error = null;
            if (triangles == null)
            {
                error = "triangles missing";
                return false;
            }
            if (triangles.Length % 3 != 0)
            {
                error = "invalid triangles layout";
                return false;
            }
            for (var i = 0; i < triangles.Length; i++)
            {
                var index = triangles[i];
                if (index < 0 || index >= vertexCount)
                {
                    error = $"triangle index out of range vertexCount={vertexCount}";
                    return false;
                }
            }
            return true;
        }

        public bool TryCommitMeshBinary(
            string pairId,
            GameObject target,
            string meshRef,
            SceneSyncBinaryBuffer[] buffers,
            string commitHash,
            out string error)
        {
            return TryCommitMeshBinary(pairId, target, meshRef, buffers, null, null, commitHash, out error);
        }

        public bool TryCommitMeshBinary(
            string pairId,
            GameObject target,
            string meshRef,
            SceneSyncBinaryBuffer[] buffers,
            SceneSyncBinaryUvChannel[] uvChannelPayloads,
            SceneSyncBinaryBuffer color0Buffer,
            string commitHash,
            out string error)
        {
            return TryCommitMeshBinary(pairId, target, meshRef, buffers, uvChannelPayloads, color0Buffer, null, commitHash, out error);
        }

        public bool TryCommitMeshBinary(
            string pairId,
            GameObject target,
            string meshRef,
            SceneSyncBinaryBuffer[] buffers,
            SceneSyncBinaryUvChannel[] uvChannelPayloads,
            SceneSyncBinaryBuffer color0Buffer,
            SceneSyncMeshSubMesh[] subMeshes,
            string commitHash,
            out string error)
        {
            return TryCommitMeshBinary(pairId, target, meshRef, buffers, uvChannelPayloads, color0Buffer, subMeshes, null, null, null, null, commitHash, out error);
        }

        public bool TryCommitMeshBinary(
            string pairId,
            GameObject target,
            string meshRef,
            SceneSyncBinaryBuffer[] buffers,
            SceneSyncBinaryUvChannel[] uvChannelPayloads,
            SceneSyncBinaryBuffer color0Buffer,
            SceneSyncMeshSubMesh[] subMeshes,
            string meshContentFingerprint,
            string commitHash,
            out string error)
        {
            return TryCommitMeshBinary(pairId, target, meshRef, buffers, uvChannelPayloads, color0Buffer, subMeshes, null, meshContentFingerprint, null, null, commitHash, out error);
        }

        public bool TryCommitMeshBinary(
            string pairId,
            GameObject target,
            string meshRef,
            SceneSyncBinaryBuffer[] buffers,
            SceneSyncBinaryUvChannel[] uvChannelPayloads,
            SceneSyncBinaryBuffer color0Buffer,
            SceneSyncMeshSubMesh[] subMeshes,
            SceneSyncBinaryBlendShape[] blendShapes,
            string meshContentFingerprint,
            string meshContentFingerprintNoUv,
            string meshContentFingerprintScope,
            string commitHash,
            out string error)
        {
            error = null;
            if (buffers == null || buffers.Length == 0)
            {
                error = "buffers missing";
                return false;
            }
            if (!TryReadBinaryBuffers(buffers, out var blenderVertices, out var blenderTriangles, out var blenderNormals, out var uv0, out error))
                return false;
            var uvChannels = MeshApplyService.ReadBinaryUvChannels(uvChannelPayloads, blenderVertices != null ? blenderVertices.Length : 0, out var uvChannelError);
            var colors = MeshApplyService.ReadColor32Buffer(color0Buffer, blenderVertices != null ? blenderVertices.Length : 0, out var colorError);
#if UNITY_EDITOR
            if (!string.IsNullOrWhiteSpace(uvChannelError))
                BlenderSyncLog.Warn(
                    "PreviewMesh",
                    "commit_uv_channel_read_failed",
                    uvChannelError,
                    new Dictionary<string, object> { { "pairId", pairId } });
            if (!string.IsNullOrWhiteSpace(colorError))
                BlenderSyncLog.Warn(
                    "PreviewMesh",
                    "commit_color_read_failed",
                    colorError,
                    new Dictionary<string, object> { { "pairId", pairId } });
#endif
            return TryCommitMeshMapped(pairId, target, meshRef, blenderVertices, blenderTriangles, blenderNormals, uv0, uvChannels, colors, subMeshes, blendShapes, meshContentFingerprint, meshContentFingerprintNoUv, meshContentFingerprintScope, commitHash, out error);
        }

        public bool TryDiscardPreview(GameObject target, out string error)
        {
            error = null;
            if (target == null)
            {
                error = "target is null";
                return false;
            }

            if (!TryGetState(target, out var state) || state == null || !state.hasUncommittedPreview)
                return true;

            RestoreCommittedMesh(target, state);

            DestroyPreviewMesh(state.previewMesh);
            RemoveRuntimeState(state.pairId);
            return true;
        }

        public static bool HasPreview(GameObject target)
        {
            return TryGetState(target, out var state)
                && state != null
                && state.hasUncommittedPreview
                && state.previewMesh != null
                && GetCurrentRendererMesh(target) == state.previewMesh;
        }

#if UNITY_EDITOR
        public static void ClearAllInOpenScenes()
        {
            var restored = 0;
            var restoredPairs = new HashSet<string>();
            var sceneCount = SceneManager.sceneCount;
            for (var i = 0; i < sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (!scene.IsValid() || !scene.isLoaded)
                    continue;

                foreach (var root in scene.GetRootGameObjects())
                {
                    if (root == null)
                        continue;
                    foreach (var rendererTarget in root.GetComponentsInChildren<Transform>(true))
                    {
                        if (rendererTarget == null || rendererTarget.gameObject == null)
                            continue;
                        var currentMesh = GetCurrentRendererMesh(rendererTarget.gameObject);
                        if (currentMesh == null)
                            continue;
                        if (TryFindStateByPreviewMesh(currentMesh, out var state, out var statePairId))
                        {
                            RestoreCommittedMesh(rendererTarget.gameObject, state);
                            restoredPairs.Add(statePairId);
                            restored++;
                        }
                    }
                }
            }

            foreach (var state in SnapshotRuntimeStates())
                DestroyPreviewMesh(state?.previewMesh);
            ClearRuntimeStates();

            BlenderSyncLog.Trace(
                "PreviewMesh",
                "cleared",
                () => "Cleared all runtime preview meshes.",
                () => new Dictionary<string, object>
                {
                    { "restored", restored },
                    { "stateCount", restoredPairs.Count },
                });
        }
#endif

        private static RuntimePreviewState EnsureRuntimeState(string pairId, GameObject target, string meshRef)
        {
            pairId = NormalizePairId(pairId);
            RuntimePreviewState state;
            lock (StateByPairIdLock)
            {
                if (!StateByPairId.TryGetValue(pairId, out state) || state == null)
                {
                    state = new RuntimePreviewState();
                    StateByPairId[pairId] = state;
                }
            }
            state.pairId = pairId;
            state.meshRef = meshRef;
            state.target = target;
            return state;
        }

        private static bool TryGetState(GameObject target, out RuntimePreviewState state)
        {
            state = null;
            if (target == null)
                return false;
            var sharedMesh = GetCurrentRendererMesh(target);
            if (sharedMesh == null)
                return false;
            return TryFindStateByPreviewMesh(sharedMesh, out state, out _);
        }

        private static bool TryFindStateByPreviewMesh(Mesh previewMesh, out RuntimePreviewState state, out string pairId)
        {
            state = null;
            pairId = null;
            if (previewMesh == null)
                return false;

            lock (StateByPairIdLock)
            {
                foreach (var kv in StateByPairId)
                {
                    if (kv.Value != null && kv.Value.previewMesh == previewMesh)
                    {
                        state = kv.Value;
                        pairId = kv.Key;
                        return true;
                    }
                }
            }

            return false;
        }

        private static bool TryCreatePreviewStateFromCurrentMesh(
            string pairId,
            GameObject target,
            int expectedVertexCount,
            string context,
            out RuntimePreviewState state,
            out string error)
        {
            state = null;
            error = null;
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
            {
                error = "pairId is missing";
                return false;
            }
            if (target == null)
            {
                error = "target is null";
                return false;
            }

            var currentMesh = GetCurrentRendererMesh(target);
            if (currentMesh == null)
            {
                error = "preview_baseline_missing";
                return false;
            }
            if (expectedVertexCount >= 0 && currentMesh.vertexCount != expectedVertexCount)
            {
                error = $"preview_baseline_vertex_count_mismatch payload={expectedVertexCount} current={currentMesh.vertexCount}";
                return false;
            }

            state = EnsureRuntimeState(pairId, target, null);
            CaptureCommittedMeshIfNeeded(target, state);
            if (state.previewMesh != null && state.previewMesh != currentMesh)
                DestroyPreviewMesh(state.previewMesh);

            var previewMesh = new Mesh { name = $"PreviewRecovered_{pairId}" };
            previewMesh.hideFlags = HideFlags.HideAndDontSave;
            CopyMeshData(currentMesh, previewMesh);
            state.previewMesh = previewMesh;
            state.previewHash = $"recovered:{context}:{currentMesh.vertexCount}";
            state.meshContentFingerprint = null;
            state.meshContentFingerprintNoUv = null;
            state.meshContentFingerprintScope = null;
            state.meshContentFingerprintRequestSent = false;
            state.lastMeshContentFingerprintRequestAt = 0;
            state.hasUncommittedPreview = false;
            state.lastPreviewAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            ApplyMeshToTargetRenderer(target, previewMesh);
#if UNITY_EDITOR
            BlenderSyncLog.Info(
                "PreviewMesh",
                "baseline_recovered",
                "Recovered the current renderer mesh as the preview baseline.",
                new Dictionary<string, object>
                {
                    { "pairId", pairId },
                    { "context", context },
                    { "vertexCount", currentMesh.vertexCount },
                });
#endif
            return true;
        }

        private static List<RuntimePreviewState> SnapshotRuntimeStates()
        {
            lock (StateByPairIdLock)
            {
                return new List<RuntimePreviewState>(StateByPairId.Values);
            }
        }

        private static void RemoveRuntimeState(string pairId)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
                return;

            lock (StateByPairIdLock)
            {
                StateByPairId.Remove(pairId);
            }
        }

        private static void ClearRuntimeStates()
        {
            lock (StateByPairIdLock)
            {
                StateByPairId.Clear();
            }
        }

        private static string NormalizePairId(string pairId)
        {
            return (pairId ?? string.Empty).Trim();
        }

        private static bool TryReadBinaryBuffers(SceneSyncBinaryBuffer[] buffers, out Vector3[] vertices, out int[] triangles, out Vector3[] normals, out Vector2[] uvs, out string error)
        {
            vertices = null;
            triangles = null;
            normals = null;
            uvs = null;
            error = null;

            foreach (var buffer in buffers)
            {
                if (buffer == null || string.IsNullOrWhiteSpace(buffer.semantic))
                    continue;
                if (string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path))
                {
                    error = $"buffer missing: {buffer?.semantic} path={buffer?.path}";
                    return false;
                }

                var semantic = buffer.semantic.Trim().ToUpperInvariant();
                if (semantic == "POSITION")
                    vertices = ReadVector3Buffer(buffer, out error);
                else if (semantic == "NORMAL")
                    normals = ReadVector3Buffer(buffer, out error);
                else if (semantic == "UV0")
                    uvs = ReadVector2Buffer(buffer, out error);
                else if (semantic == "INDEX")
                    triangles = ReadIntBuffer(buffer, out error);

                if (!string.IsNullOrWhiteSpace(error))
                    return false;
            }

            if (vertices == null || triangles == null)
            {
                error = "POSITION/INDEX buffers missing";
                return false;
            }

            return true;
        }

        private static Vector3[] ReadVector3Buffer(SceneSyncBinaryBuffer buffer, out string error)
        {
            error = null;
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path))
            {
                error = $"buffer missing: {buffer?.semantic} path={buffer?.path}";
                return null;
            }

            var bytes = File.ReadAllBytes(buffer.path);
            var components = buffer.components > 0 ? buffer.components : 3;
            if (!IsFloat32(buffer.format) || components < 3 || bytes.Length % 4 != 0)
            {
                error = $"invalid float3 buffer semantic={buffer.semantic} format={buffer.format} components={buffer.components} bytes={bytes.Length}";
                return null;
            }
            var floatCount = bytes.Length / 4;
            var count = buffer.count > 0 ? Math.Min(buffer.count, floatCount / components) : floatCount / components;
            var values = new Vector3[count];
            for (var i = 0; i < count; i++)
            {
                var o = i * components * 4;
                values[i] = new Vector3(BitConverter.ToSingle(bytes, o), BitConverter.ToSingle(bytes, o + 4), BitConverter.ToSingle(bytes, o + 8));
            }
            return values;
        }

        private static Vector2[] ReadVector2Buffer(SceneSyncBinaryBuffer buffer, out string error)
        {
            error = null;
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path))
            {
                error = $"buffer missing: {buffer?.semantic} path={buffer?.path}";
                return null;
            }

            var bytes = File.ReadAllBytes(buffer.path);
            var components = buffer.components > 0 ? buffer.components : 2;
            if (!IsFloat32(buffer.format) || components < 2 || bytes.Length % 4 != 0)
            {
                error = $"invalid float2 buffer semantic={buffer.semantic} format={buffer.format} components={buffer.components} bytes={bytes.Length}";
                return null;
            }
            var floatCount = bytes.Length / 4;
            var count = buffer.count > 0 ? Math.Min(buffer.count, floatCount / components) : floatCount / components;
            var values = new Vector2[count];
            for (var i = 0; i < count; i++)
            {
                var o = i * components * 4;
                values[i] = new Vector2(BitConverter.ToSingle(bytes, o), BitConverter.ToSingle(bytes, o + 4));
            }
            return values;
        }

        private static int[] ReadIntBuffer(SceneSyncBinaryBuffer buffer, out string error)
        {
            error = null;
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path))
            {
                error = $"buffer missing: {buffer?.semantic} path={buffer?.path}";
                return null;
            }

            var bytes = File.ReadAllBytes(buffer.path);
            if (!IsInt32(buffer.format) || bytes.Length % 4 != 0)
            {
                error = $"invalid int buffer semantic={buffer.semantic} format={buffer.format} bytes={bytes.Length}";
                return null;
            }
            var available = bytes.Length / 4;
            var count = buffer.count > 0 ? Math.Min(buffer.count, available) : available;
            var values = new int[count];
            Buffer.BlockCopy(bytes, 0, values, 0, count * 4);
            return values;
        }

        private static bool IsFloat32(string format)
        {
            return string.IsNullOrWhiteSpace(format) || string.Equals(format, "float32", StringComparison.OrdinalIgnoreCase);
        }

        private static bool IsInt32(string format)
        {
            return string.IsNullOrWhiteSpace(format) || string.Equals(format, "int32", StringComparison.OrdinalIgnoreCase);
        }

#if UNITY_EDITOR
        private string ResolveCurrentMeshRefForPair(RuntimePreviewState state)
        {
            if (state == null || string.IsNullOrWhiteSpace(state.pairId))
                return state?.meshRef;
            var objectDb = _bindingRegistry.Load();
            var binding = _bindingRegistry.FindByPairId(objectDb, state.pairId);
            return string.IsNullOrWhiteSpace(binding?.meshRef) ? state.meshRef : binding.meshRef;
        }

        private bool TryGetSharedMeshRecord(string effectiveMeshRef, RuntimePreviewState state, out AptRecord record)
        {
            record = null;
            if (state == null || string.IsNullOrWhiteSpace(effectiveMeshRef) || string.IsNullOrWhiteSpace(state.pairId))
                return false;

            var aptDb = _aptRepository.Load();
            record = _aptRepository.FindByAssetId(aptDb, effectiveMeshRef);
            if (record == null)
                return false;

            var refs = record.referencedBy ?? Array.Empty<AptReferenceRef>();
            return refs.Count(r => r != null && !string.IsNullOrWhiteSpace(r.pairId)) > 1;
        }

        private bool TryCommitMeshData(GameObject target, Mesh sourceMesh, RuntimePreviewState state, string mode, out string error, bool destroySourceAfterCommit = false)
        {
            error = null;
            if (target == null)
            {
                error = "target is null";
                return false;
            }
            if (sourceMesh == null)
            {
                error = "source mesh missing";
                return false;
            }

            CaptureCommittedMeshIfNeeded(target, state);
            var effectiveMeshRef = ResolveCurrentMeshRefForPair(state);
            if (TryGetSharedMeshRecord(effectiveMeshRef, state, out var sharedRecord))
            {
                if (TrySkipUnchangedSharedCommit(target, state, sharedRecord, mode))
                {
                    if (destroySourceAfterCommit)
                        DestroyPreviewMesh(sourceMesh);
                    return true;
                }
                if (string.IsNullOrWhiteSpace(state.meshContentFingerprint))
                {
                    error = "shared_mesh_content_fingerprint_missing";
                    RequestMeshContentFingerprint(state, effectiveMeshRef, mode);
                    return false;
                }
                // Fingerprint schemas can evolve, and older records may include
                // instance-only fields. Compare actual Mesh data before forking
                // so a schema-only mismatch cannot split a shared resource.
                if (TrySkipRuntimeIdenticalSharedCommit(target, sourceMesh, state, sharedRecord, mode))
                {
                    if (destroySourceAfterCommit)
                        DestroyPreviewMesh(sourceMesh);
                    return true;
                }
                BlenderSyncLog.Trace(
                    "PreviewMesh",
                    "commit_fork_decision",
                    () =>
                        $"pair={state.pairId} mode={mode} meshRef={effectiveMeshRef} " +
                        $"incoming={ShortFingerprint(state.meshContentFingerprint)} " +
                        $"existing={ShortFingerprint(sharedRecord.meshContentFingerprint)} " +
                        $"scope={NormalizeFingerprint(state.meshContentFingerprintScope) ?? "full"} " +
                        $"incomingNoUv={ShortFingerprint(state.meshContentFingerprintNoUv)} " +
                        $"existingNoUv={ShortFingerprint(sharedRecord.meshContentFingerprintNoUv)}");
                LogForkDiff(target, sourceMesh, state, mode);
                return TryCommitMeshDataAsFork(target, sourceMesh, state, effectiveMeshRef, mode, out error, destroySourceAfterCommit);
            }

            if (string.IsNullOrWhiteSpace(state.committedAssetPath))
            {
                error = "committed asset path missing";
                if (destroySourceAfterCommit) DestroyPreviewMesh(sourceMesh);
                return false;
            }
            var committed = AssetDatabase.LoadAssetAtPath<Mesh>(state.committedAssetPath);
            if (committed == null)
            {
                error = "committed mesh asset missing";
                if (destroySourceAfterCommit) DestroyPreviewMesh(sourceMesh);
                return false;
            }

            CopyMeshData(sourceMesh, committed);
            EditorUtility.SetDirty(committed);
            AssetDatabase.SaveAssets();
            UpdateMeshRecordContentFingerprint(effectiveMeshRef, state.meshContentFingerprint, state.meshContentFingerprintNoUv);
            ApplyMeshToTargetRenderer(target, committed);
            MeshApplyService.RegisterPairMeshBinding(state.pairId, committed);
            _linkRegistry.UpdateReference(state.pairId, target, "mesh", effectiveMeshRef);

            if (state.previewMesh != null && state.previewMesh != sourceMesh)
                DestroyPreviewMesh(state.previewMesh);
            if (destroySourceAfterCommit)
                DestroyPreviewMesh(sourceMesh);
            RemoveRuntimeState(state.pairId);
            BlenderSyncLog.Trace(
                "PreviewMesh",
                "committed",
                () => "Committed preview mesh data to its existing asset.",
                () => new Dictionary<string, object>
                {
                    { "pairId", state.pairId },
                    { "mode", mode },
                });
            return true;
        }

        private static void RequestMeshContentFingerprint(RuntimePreviewState state, string meshRef, string mode)
        {
            if (state == null || string.IsNullOrWhiteSpace(state.pairId))
                return;

            var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            state.lastPreviewAt = now;
            if (state.meshContentFingerprintRequestSent && now - state.lastMeshContentFingerprintRequestAt < 5)
                return;

            state.meshContentFingerprintRequestSent = true;
            state.lastMeshContentFingerprintRequestAt = now;
            var payload = new MeshContentFingerprintRequestPayload
            {
                type = "scene_sync.mesh_content_fingerprint_request_v1",
                timestamp = now,
                pairId = state.pairId,
                meshRef = string.IsNullOrWhiteSpace(meshRef) ? state.meshRef : meshRef,
                reason = string.IsNullOrWhiteSpace(mode) ? "preview_commit" : mode,
            };
            SessionClient.SendToBlender(JsonUtility.ToJson(payload));
            BlenderSyncLog.Info(
                "PreviewMesh",
                "commit_waiting_for_fingerprint",
                "Requested the mesh fingerprint before committing a shared preview.",
                new Dictionary<string, object>
                {
                    { "pairId", state.pairId },
                    { "mode", mode },
                    { "meshRef", payload.meshRef },
                });
        }

        private bool TrySkipUnchangedSharedCommit(GameObject target, RuntimePreviewState state, AptRecord sharedRecord, string mode)
        {
            var useNoUv = string.Equals(NormalizeFingerprint(state?.meshContentFingerprintScope), "no_uv", StringComparison.OrdinalIgnoreCase);
            var incomingFingerprint = useNoUv
                ? NormalizeFingerprint(state?.meshContentFingerprintNoUv) ?? NormalizeFingerprint(state?.meshContentFingerprint)
                : NormalizeFingerprint(state?.meshContentFingerprint);
            var existingFingerprint = useNoUv
                ? NormalizeFingerprint(sharedRecord?.meshContentFingerprintNoUv)
                : NormalizeFingerprint(sharedRecord?.meshContentFingerprint);
            if (string.IsNullOrWhiteSpace(incomingFingerprint) || string.IsNullOrWhiteSpace(existingFingerprint))
                return false;
            if (!string.Equals(incomingFingerprint, existingFingerprint, StringComparison.Ordinal))
                return false;

            var committed = LoadCommittedMesh(state);
            if (committed == null)
                return false;

            ApplyMeshToTargetRenderer(target, committed);
            MeshApplyService.RegisterPairMeshBinding(state.pairId, committed);
            _linkRegistry.UpdateReference(state.pairId, target, "mesh", sharedRecord.assetId);
            if (state.previewMesh != null)
                DestroyPreviewMesh(state.previewMesh);
            RemoveRuntimeState(state.pairId);
            BlenderSyncLog.Trace(
                "PreviewMesh",
                "commit_skipped_unchanged_shared",
                () => "Reused the shared mesh because its fingerprint is unchanged.",
                () => new Dictionary<string, object>
                {
                    { "pairId", state.pairId },
                    { "mode", mode },
                    { "scope", useNoUv ? "no_uv" : "full" },
                });
            return true;
        }

        private bool TrySkipRuntimeIdenticalSharedCommit(GameObject target, Mesh sourceMesh, RuntimePreviewState state, AptRecord sharedRecord, string mode)
        {
            if (target == null || sourceMesh == null || state == null || sharedRecord == null)
                return false;
            var incomingFingerprint = NormalizeFingerprint(state.meshContentFingerprint);
            if (string.IsNullOrWhiteSpace(incomingFingerprint))
                return false;

            var committed = LoadCommittedMesh(state);
            if (committed == null)
                return false;

            var sourceRuntimeFingerprint = ComputeUnityMeshRuntimeFingerprint(sourceMesh);
            var committedRuntimeFingerprint = ComputeUnityMeshRuntimeFingerprint(committed);
            if (string.IsNullOrWhiteSpace(sourceRuntimeFingerprint)
                || string.IsNullOrWhiteSpace(committedRuntimeFingerprint)
                || !string.Equals(sourceRuntimeFingerprint, committedRuntimeFingerprint, StringComparison.Ordinal))
                return false;

            UpdateMeshRecordContentFingerprint(sharedRecord.assetId, incomingFingerprint, state.meshContentFingerprintNoUv);
            ApplyMeshToTargetRenderer(target, committed);
            MeshApplyService.RegisterPairMeshBinding(state.pairId, committed);
            _linkRegistry.UpdateReference(state.pairId, target, "mesh", sharedRecord.assetId);
            if (state.previewMesh != null)
                DestroyPreviewMesh(state.previewMesh);
            RemoveRuntimeState(state.pairId);
            BlenderSyncLog.Trace(
                "PreviewMesh",
                "commit_skipped_runtime_identical_shared",
                () => "Reused the shared mesh after confirming identical runtime data.",
                () => new Dictionary<string, object>
                {
                    { "pairId", state.pairId },
                    { "mode", mode },
                });
            return true;
        }

        private void UpdateMeshRecordContentFingerprint(string meshRef, string meshContentFingerprint, string meshContentFingerprintNoUv)
        {
            meshRef = NormalizeFingerprint(meshRef);
            meshContentFingerprint = NormalizeFingerprint(meshContentFingerprint);
            meshContentFingerprintNoUv = NormalizeFingerprint(meshContentFingerprintNoUv);
            if (string.IsNullOrWhiteSpace(meshRef) || (string.IsNullOrWhiteSpace(meshContentFingerprint) && string.IsNullOrWhiteSpace(meshContentFingerprintNoUv)))
                return;

            var aptDb = _aptRepository.Load();
            var record = _aptRepository.FindByAssetId(aptDb, meshRef);
            if (record == null)
                return;
            if (string.Equals(record.meshContentFingerprint, meshContentFingerprint, StringComparison.Ordinal)
                && string.Equals(record.meshContentFingerprintNoUv, meshContentFingerprintNoUv, StringComparison.Ordinal))
                return;

            if (!string.IsNullOrWhiteSpace(meshContentFingerprint))
                record.meshContentFingerprint = meshContentFingerprint;
            if (!string.IsNullOrWhiteSpace(meshContentFingerprintNoUv))
                record.meshContentFingerprintNoUv = meshContentFingerprintNoUv;
            _aptRepository.Upsert(aptDb, record);
            _aptRepository.Save(aptDb);
        }

        private static void PumpPreviewWatchdogStatic()
        {
            new PreviewMeshService().PumpPreviewWatchdog();
        }

        private void PumpPreviewWatchdog()
        {
            var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            var commitTimeoutSeconds = _previewWatchdogCommitTimeoutSeconds;
            foreach (var state in SnapshotRuntimeStates())
            {
                if (state == null || !state.hasUncommittedPreview || state.previewMesh == null)
                    continue;
                if (now - state.lastPreviewAt < commitTimeoutSeconds)
                    continue;

                var target = state.target;
                if (target == null)
                {
                    DestroyPreviewMesh(state.previewMesh);
                    RemoveRuntimeState(state.pairId);
                    continue;
                }

                var currentMesh = GetCurrentRendererMesh(target);
                if (currentMesh != state.previewMesh)
                {
                    DestroyPreviewMesh(state.previewMesh);
                    RemoveRuntimeState(state.pairId);
                    continue;
                }

                if (!TryCommitMeshData(target, state.previewMesh, state, "preview_watchdog_timeout", out var error)
                    && !string.Equals(error, "shared_mesh_content_fingerprint_missing", StringComparison.Ordinal))
                    BlenderSyncLog.Warn(
                        "PreviewMesh",
                        "watchdog_commit_failed",
                        error,
                        new Dictionary<string, object> { { "pairId", state.pairId } });
            }
        }

        private bool TryCommitMeshDataAsFork(GameObject target, Mesh sourceMesh, RuntimePreviewState state, string sourceMeshRef, string mode, out string error, bool destroySourceAfterCommit = false)
        {
            error = null;
            var targetMeshRef = "mesh-" + Guid.NewGuid().ToString("N");
            var displayName = BuildMeshAssetDisplayName(target != null ? target.name : null);
            var assetPath = BuildMeshAssetPath(displayName, targetMeshRef);
            EnsureAssetFolder(Path.GetDirectoryName(assetPath));
            var forkedAsset = new Mesh { name = displayName };
            CopyMeshData(sourceMesh, forkedAsset);
            AssetDatabase.CreateAsset(forkedAsset, assetPath);
            EditorUtility.SetDirty(forkedAsset);
            AssetDatabase.SaveAssets();
            forkedAsset = AssetDatabase.LoadAssetAtPath<Mesh>(assetPath);
            if (forkedAsset == null)
            {
                error = "preview_mesh_fork_asset_create_failed";
                if (destroySourceAfterCommit) DestroyPreviewMesh(sourceMesh);
                return false;
            }
            var assetGuid = AssetDatabase.AssetPathToGUID(assetPath);

            var aptDb = _aptRepository.Load();
            var record = _aptRepository.FindByAssetId(aptDb, targetMeshRef) ?? new AptRecord { assetId = targetMeshRef };
            record.sourceFingerprint = BuildFingerprint(sourceMesh, state);
            record.meshContentFingerprint = NormalizeFingerprint(state.meshContentFingerprint) ?? record.sourceFingerprint;
            record.meshContentFingerprintNoUv = NormalizeFingerprint(state.meshContentFingerprintNoUv);
            record.source = new AptSource
            {
                sourceUri = string.IsNullOrWhiteSpace(sourceMeshRef) ? $"preview_commit://{state.pairId}" : $"preview_commit://{sourceMeshRef}",
                sourceObjectPath = target.name,
            };
            record.target = new AptTarget
            {
                unityAssetPath = assetPath,
                resourceType = "mesh",
                assetGuid = assetGuid,
                localFileId = 0,
                isSkinned = forkedAsset.blendShapeCount > 0,
                boneCount = 0,
                skinEncoding = null,
            };
            record.mappingState = "mapped";
            record.updateState = "up_to_date";
            record.lastImportedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            record.lastError = null;
            _aptRepository.Upsert(aptDb, record);
            _aptRepository.Save(aptDb);

            ApplyMeshToTargetRenderer(target, forkedAsset);
            _linkRegistry.UpdateReference(state.pairId, target, "mesh", targetMeshRef);
            MeshApplyService.RegisterPairMeshBinding(state.pairId, forkedAsset);

            if (state.previewMesh != null && state.previewMesh != sourceMesh)
                DestroyPreviewMesh(state.previewMesh);
            if (destroySourceAfterCommit)
                DestroyPreviewMesh(sourceMesh);
            RemoveRuntimeState(state.pairId);
            BlenderSyncLog.Info(
                "PreviewMesh",
                "shared_mesh_forked",
                "Created a dedicated mesh asset because a shared mesh changed.",
                new Dictionary<string, object>
                {
                    { "pairId", state.pairId },
                    { "mode", mode },
                    { "sourceMeshRef", sourceMeshRef },
                    { "targetMeshRef", targetMeshRef },
                });
            return true;
        }

        private static string BuildMeshAssetDisplayName(string objectName)
        {
            return AssetPathUtility.SanitizeFileName(string.IsNullOrWhiteSpace(objectName) ? "Mesh" : objectName.Trim(), "Mesh");
        }

        private static string BuildMeshAssetPath(string displayName, string assetId)
        {
            return AssetPathUtility.BuildStableAssetPath("Assets/TriSync/Resources/Meshes", displayName, assetId, ".asset");
        }

        private static void EnsureAssetFolder(string assetFolder)
        {
            if (string.IsNullOrWhiteSpace(assetFolder))
                return;
            var normalized = assetFolder.Replace('\\', '/');
            var parts = normalized.Split('/');
            if (parts.Length == 0 || parts[0] != "Assets")
                throw new InvalidOperationException("asset_path_must_start_with_assets:" + assetFolder);

            var current = "Assets";
            for (var i = 1; i < parts.Length; i++)
            {
                var next = current + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next))
                    AssetDatabase.CreateFolder(current, parts[i]);
                current = next;
            }
        }

        private static string BuildFingerprint(Mesh mesh, RuntimePreviewState state)
        {
            if (mesh == null)
                return "preview-commit-empty";
            return $"preview-commit:{state?.meshRef}:{mesh.vertexCount}:{mesh.triangles?.Length ?? 0}:blendShapes={mesh.blendShapeCount}:{state?.previewHash}";
        }

        private static string NormalizeFingerprint(string fingerprint)
        {
            var normalized = fingerprint?.Trim();
            return string.IsNullOrWhiteSpace(normalized) ? null : normalized;
        }

        private static string ShortFingerprint(string fingerprint)
        {
            var normalized = NormalizeFingerprint(fingerprint);
            if (string.IsNullOrWhiteSpace(normalized))
                return "<empty>";
            return normalized.Length <= 12 ? normalized : normalized.Substring(0, 12);
        }

        private static void LogForkDiff(GameObject target, Mesh sourceMesh, RuntimePreviewState state, string mode)
        {
            BlenderSyncLog.Trace(
                "PreviewMesh",
                "commit_fork_diff",
                () =>
                {
                    var committed = LoadCommittedMesh(state);
                    return $"pair={state?.pairId} mode={mode} " +
                           $"source=({BuildUnityMeshDebugSummary(sourceMesh)}) " +
                           $"committed=({BuildUnityMeshDebugSummary(committed)}) target={target?.name}";
                });
        }

        private static string BuildUnityMeshDebugSummary(Mesh mesh)
        {
            if (mesh == null)
                return "missing";
            return $"v={mesh.vertexCount} sub={mesh.subMeshCount} blend={mesh.blendShapeCount} " +
                $"pos={ShortHash(HashVector3Array(mesh.vertices))} " +
                $"nrm={ShortHash(HashVector3Array(mesh.normals))} " +
                $"col={ShortHash(HashColor32Array(mesh.colors32))} " +
                $"uv0={ShortHash(HashUvChannel(mesh, 0))} " +
                $"uv1={ShortHash(HashUvChannel(mesh, 1))} " +
                $"idx={ShortHash(HashSubMeshIndices(mesh))}";
        }

        private static string ShortHash(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return "empty";
            return value.Length <= 8 ? value : value.Substring(0, 8);
        }

        private static string HashVector3Array(Vector3[] values)
        {
            using (var sha1 = SHA1.Create())
            {
                AddVector3Array(sha1, values);
                sha1.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                return BitConverter.ToString(sha1.Hash).Replace("-", "").ToLowerInvariant();
            }
        }

        private static string HashColor32Array(Color32[] values)
        {
            using (var sha1 = SHA1.Create())
            {
                AddColor32Array(sha1, values);
                sha1.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                return BitConverter.ToString(sha1.Hash).Replace("-", "").ToLowerInvariant();
            }
        }

        private static string HashUvChannel(Mesh mesh, int channel)
        {
            if (mesh == null)
                return null;
            using (var sha1 = SHA1.Create())
            {
                var values = new List<Vector2>();
                mesh.GetUVs(channel, values);
                AddVector2List(sha1, values);
                sha1.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                return BitConverter.ToString(sha1.Hash).Replace("-", "").ToLowerInvariant();
            }
        }

        private static string HashSubMeshIndices(Mesh mesh)
        {
            if (mesh == null)
                return null;
            using (var sha1 = SHA1.Create())
            {
                AddInt(sha1, mesh.subMeshCount);
                for (var slot = 0; slot < mesh.subMeshCount; slot++)
                {
                    AddInt(sha1, slot);
                    AddInt(sha1, (int)mesh.GetTopology(slot));
                    AddIntArray(sha1, mesh.GetIndices(slot));
                }
                sha1.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                return BitConverter.ToString(sha1.Hash).Replace("-", "").ToLowerInvariant();
            }
        }

        private static string ComputeUnityMeshRuntimeFingerprint(Mesh mesh)
        {
            if (mesh == null)
                return null;

            using (var sha1 = SHA1.Create())
            {
                AddString(sha1, "unity_mesh_runtime_v1");
                AddInt(sha1, mesh.vertexCount);
                AddInt(sha1, mesh.subMeshCount);
                AddInt(sha1, mesh.blendShapeCount);
                AddVector3Array(sha1, mesh.vertices);
                AddVector3Array(sha1, mesh.normals);
                AddColor32Array(sha1, mesh.colors32);
                for (var channel = 0; channel < 8; channel++)
                {
                    var uvs = new List<Vector2>();
                    mesh.GetUVs(channel, uvs);
                    AddInt(sha1, channel);
                    AddVector2List(sha1, uvs);
                }
                for (var slot = 0; slot < mesh.subMeshCount; slot++)
                {
                    AddInt(sha1, slot);
                    AddInt(sha1, (int)mesh.GetTopology(slot));
                    AddIntArray(sha1, mesh.GetIndices(slot));
                }
                AddBlendShapes(sha1, mesh);
                sha1.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                return BitConverter.ToString(sha1.Hash).Replace("-", "").ToLowerInvariant();
            }
        }

        private static void AddBlendShapes(HashAlgorithm hash, Mesh mesh)
        {
            if (hash == null || mesh == null)
                return;
            var vertices = new Vector3[mesh.vertexCount];
            var normals = new Vector3[mesh.vertexCount];
            var tangents = new Vector3[mesh.vertexCount];
            for (var shape = 0; shape < mesh.blendShapeCount; shape++)
            {
                AddInt(hash, shape);
                AddString(hash, mesh.GetBlendShapeName(shape));
                var frameCount = mesh.GetBlendShapeFrameCount(shape);
                AddInt(hash, frameCount);
                for (var frame = 0; frame < frameCount; frame++)
                {
                    AddFloat(hash, mesh.GetBlendShapeFrameWeight(shape, frame));
                    Array.Clear(vertices, 0, vertices.Length);
                    Array.Clear(normals, 0, normals.Length);
                    Array.Clear(tangents, 0, tangents.Length);
                    mesh.GetBlendShapeFrameVertices(shape, frame, vertices, normals, tangents);
                    AddVector3Array(hash, vertices);
                    AddVector3Array(hash, normals);
                    AddVector3Array(hash, tangents);
                }
            }
        }

        private static void AddVector3Array(HashAlgorithm hash, Vector3[] values)
        {
            AddInt(hash, values != null ? values.Length : 0);
            if (values == null)
                return;
            for (var i = 0; i < values.Length; i++)
            {
                AddFloat(hash, values[i].x);
                AddFloat(hash, values[i].y);
                AddFloat(hash, values[i].z);
            }
        }

        private static void AddVector2List(HashAlgorithm hash, List<Vector2> values)
        {
            AddInt(hash, values != null ? values.Count : 0);
            if (values == null)
                return;
            for (var i = 0; i < values.Count; i++)
            {
                AddFloat(hash, values[i].x);
                AddFloat(hash, values[i].y);
            }
        }

        private static void AddColor32Array(HashAlgorithm hash, Color32[] values)
        {
            AddInt(hash, values != null ? values.Length : 0);
            if (values == null)
                return;
            for (var i = 0; i < values.Length; i++)
            {
                AddByte(hash, values[i].r);
                AddByte(hash, values[i].g);
                AddByte(hash, values[i].b);
                AddByte(hash, values[i].a);
            }
        }

        private static void AddIntArray(HashAlgorithm hash, int[] values)
        {
            AddInt(hash, values != null ? values.Length : 0);
            if (values == null)
                return;
            for (var i = 0; i < values.Length; i++)
                AddInt(hash, values[i]);
        }

        private static void AddString(HashAlgorithm hash, string value)
        {
            var bytes = Encoding.UTF8.GetBytes(value ?? string.Empty);
            AddInt(hash, bytes.Length);
            AddBytes(hash, bytes);
        }

        private static void AddInt(HashAlgorithm hash, int value)
        {
            AddBytes(hash, BitConverter.GetBytes(value));
        }

        private static void AddFloat(HashAlgorithm hash, float value)
        {
            AddBytes(hash, BitConverter.GetBytes(value));
        }

        private static void AddByte(HashAlgorithm hash, byte value)
        {
            AddBytes(hash, new[] { value });
        }

        private static void AddBytes(HashAlgorithm hash, byte[] bytes)
        {
            if (hash == null || bytes == null || bytes.Length == 0)
                return;
            hash.TransformBlock(bytes, 0, bytes.Length, null, 0);
        }

        [Serializable]
        private sealed class MeshContentFingerprintRequestPayload
        {
            public string type;
            public long timestamp;
            public string pairId;
            public string meshRef;
            public string reason;
        }
#endif

        private static Mesh GetCurrentRendererMesh(GameObject target)
        {
            if (target == null)
                return null;
            var skinned = target.GetComponent<SkinnedMeshRenderer>();
            if (skinned != null && skinned.sharedMesh != null)
                return skinned.sharedMesh;
            var meshFilter = target.GetComponent<MeshFilter>();
            return meshFilter != null ? meshFilter.sharedMesh : null;
        }

        private static void ApplyMeshToTargetRenderer(GameObject target, Mesh mesh)
        {
            if (target == null || mesh == null)
                return;
            MeshReferenceApplyService.ReconcileRendererForMesh(target, mesh);
        }

        private static Mesh LoadCommittedMesh(RuntimePreviewState state)
        {
#if UNITY_EDITOR
            if (state != null && !string.IsNullOrWhiteSpace(state.committedAssetPath))
                return AssetDatabase.LoadAssetAtPath<Mesh>(state.committedAssetPath);
#endif
            return null;
        }

        private static void CopyMeshData(Mesh source, Mesh target)
        {
            target.Clear();
            target.indexFormat = source.indexFormat;
            target.vertices = source.vertices;
            CopySubMeshes(source, target);
            var normals = source.normals;
            if (normals != null && normals.Length == source.vertexCount)
                target.normals = normals;
            else
                target.RecalculateNormals();
            for (var channel = 0; channel < 8; channel++)
            {
                var uv = new List<Vector2>();
                source.GetUVs(channel, uv);
                target.SetUVs(channel, uv != null && uv.Count == source.vertexCount ? uv : new List<Vector2>());
            }
            var colors = source.colors32;
            target.colors32 = colors != null && colors.Length == source.vertexCount ? colors : Array.Empty<Color32>();
            CopyBlendShapesIfCompatible(source, target, "copy");
            MeshApplyService.RecalculateTangentsIfPossible(target);
            target.RecalculateBounds();
        }

        private static void CopySubMeshes(Mesh source, Mesh target)
        {
            if (source == null || target == null)
                return;
            var subMeshCount = Math.Max(1, source.subMeshCount);
            target.subMeshCount = subMeshCount;
            for (var slot = 0; slot < subMeshCount; slot++)
                target.SetIndices(source.GetIndices(slot), source.GetTopology(slot), slot);
        }

        private static SceneSyncMeshSubMesh[] MapSubMeshTriangleWinding(SceneSyncMeshSubMesh[] subMeshes)
        {
            if (subMeshes == null || subMeshes.Length == 0)
                return subMeshes;
            var mapped = new SceneSyncMeshSubMesh[subMeshes.Length];
            for (var i = 0; i < subMeshes.Length; i++)
            {
                var source = subMeshes[i];
                if (source == null)
                    continue;
                var indices = source.indices;
                var indicesBuffer = source.indicesBuffer;
                if (indicesBuffer != null)
                {
                    var bufferIndices = ReadIntBuffer(indicesBuffer, out var error);
                    if (string.IsNullOrWhiteSpace(error) && bufferIndices != null)
                    {
                        indices = bufferIndices;
                        indicesBuffer = null;
                    }
                }
                if (IsTriangleTopology(source.topology))
                    indices = SceneSyncTransformMapper.MapTriangles(indices);
                mapped[i] = new SceneSyncMeshSubMesh
                {
                    materialSlot = source.materialSlot,
                    topology = source.topology,
                    indices = indices,
                    indicesBuffer = indicesBuffer,
                };
            }
            return mapped;
        }

        private static bool IsTriangleTopology(string topology)
        {
            return !string.Equals(topology, "lines", StringComparison.OrdinalIgnoreCase);
        }

        private static void CopyBlendShapesIfCompatible(Mesh source, Mesh target, string context)
        {
            if (target == null)
                return;
            target.ClearBlendShapes();
            if (source == null || source.blendShapeCount <= 0)
                return;
            if (source.vertexCount != target.vertexCount)
            {
                BlenderSyncLog.Warn(
                    "PreviewMesh",
                    "blend_shapes_skipped",
                    "Blend shapes were skipped because source and target vertex counts differ.",
                    new Dictionary<string, object>
                    {
                        { "context", context },
                        { "sourceVertexCount", source.vertexCount },
                        { "targetVertexCount", target.vertexCount },
                        { "shapeCount", source.blendShapeCount },
                    });
                return;
            }

            var deltaVertices = new Vector3[source.vertexCount];
            var deltaNormals = new Vector3[source.vertexCount];
            var deltaTangents = new Vector3[source.vertexCount];
            for (var shapeIndex = 0; shapeIndex < source.blendShapeCount; shapeIndex++)
            {
                var shapeName = source.GetBlendShapeName(shapeIndex);
                var frameCount = source.GetBlendShapeFrameCount(shapeIndex);
                for (var frameIndex = 0; frameIndex < frameCount; frameIndex++)
                {
                    Array.Clear(deltaVertices, 0, deltaVertices.Length);
                    Array.Clear(deltaNormals, 0, deltaNormals.Length);
                    Array.Clear(deltaTangents, 0, deltaTangents.Length);
                    source.GetBlendShapeFrameVertices(shapeIndex, frameIndex, deltaVertices, deltaNormals, deltaTangents);
                    var weight = source.GetBlendShapeFrameWeight(shapeIndex, frameIndex);
                    target.AddBlendShapeFrame(shapeName, weight, deltaVertices, deltaNormals, deltaTangents);
                }
            }
        }

        private static void RestoreCommittedMesh(GameObject target, RuntimePreviewState state)
        {
            if (target == null || state == null)
                return;
#if UNITY_EDITOR
            if (!string.IsNullOrWhiteSpace(state.committedAssetPath))
            {
                var committed = AssetDatabase.LoadAssetAtPath<Mesh>(state.committedAssetPath);
                if (committed != null)
                {
                    ApplyMeshToTargetRenderer(target, committed);
                    MeshApplyService.RegisterPairMeshBinding(state.pairId, committed);
                }
            }
#endif
        }

        private static void CaptureCommittedMeshIfNeeded(GameObject target, RuntimePreviewState state)
        {
            if (target == null || state == null || !string.IsNullOrWhiteSpace(state.committedAssetPath))
                return;

#if UNITY_EDITOR
            var current = GetCurrentRendererMesh(target);
            if (current == null)
                return;
            var path = AssetDatabase.GetAssetPath(current);
            if (string.IsNullOrWhiteSpace(path))
                return;
            state.committedAssetPath = path;
            state.committedAssetGuid = AssetDatabase.AssetPathToGUID(path);
#endif
        }

        private static void DestroyPreviewMesh(Mesh mesh)
        {
            if (mesh == null)
                return;
#if UNITY_EDITOR
            UnityEngine.Object.DestroyImmediate(mesh);
#else
            UnityEngine.Object.Destroy(mesh);
#endif
        }
    }
}
