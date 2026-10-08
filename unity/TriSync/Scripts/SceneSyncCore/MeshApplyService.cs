using System;
using System.Collections.Generic;
using System.IO;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.Protocol;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
using UnityEngine.Rendering;
#if UNITY_EDITOR
using UnityEditor;
#endif
#if UNITY_6000_4_OR_NEWER
using MeshObjectId = UnityEngine.EntityId;
#else
using MeshObjectId = System.Int32;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class MeshApplyService
    {
        private readonly ResourceResolver _resolver = new ResourceResolver();
        private readonly AptRepository _aptRepository = new AptRepository();

        // Unity-side decision indexes (minimal in-memory form for this slice)
        private static readonly object MeshBindingLock = new object();
        // pairId -> version-appropriate Unity object identity
        private static readonly Dictionary<string, MeshObjectId> PairToMesh = new Dictionary<string, MeshObjectId>();
        // Unity object identity -> referenced pairIds
        private static readonly Dictionary<MeshObjectId, HashSet<string>> MeshRefs = new Dictionary<MeshObjectId, HashSet<string>>();

        public bool TryApplyBinary(string pairId, GameObject target, SceneSyncMeshUpdateBinaryMessage msg, out string error)
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
            if (msg == null)
            {
                error = "message is null";
                return false;
            }
            if (msg.buffers == null || msg.buffers.Length == 0)
            {
                error = "buffers missing";
                return false;
            }

            var sw = System.Diagnostics.Stopwatch.StartNew();
            if (!TryReadBinaryBuffers(msg, out var vertices, out var triangles, out var normals, out var uvs, out error))
                return false;
            var uvChannels = ReadBinaryUvChannels(msg.uvChannels, vertices != null ? vertices.Length : 0, out var uvChannelError);
            var colors = ReadColor32Buffer(msg.color0, vertices != null ? vertices.Length : 0, out var colorError);
#if UNITY_EDITOR
            if (!string.IsNullOrWhiteSpace(uvChannelError))
                LogMeshWarning("uv_channel_read_failed", uvChannelError, pairId, msg.meshRef);
            if (!string.IsNullOrWhiteSpace(colorError))
                LogMeshWarning("color_read_failed", colorError, pairId, msg.meshRef);
#endif
            var readMs = sw.Elapsed.TotalMilliseconds;

            if (vertices == null || triangles == null)
            {
                error = "POSITION/INDEX buffers missing";
                return false;
            }
            if (!IndicesInRange(triangles, vertices.Length))
            {
                error = $"triangle index out of range vertexCount={vertices.Length}";
                return false;
            }

            var meshFilter = target.GetComponent<MeshFilter>();
            if (meshFilter == null) meshFilter = target.AddComponent<MeshFilter>();
            var renderer = target.GetComponent<MeshRenderer>();
            if (renderer == null) renderer = target.AddComponent<MeshRenderer>();

            string preferredErr = null;
            if (!string.IsNullOrWhiteSpace(msg.meshRef) && TryBindPreferredMeshRef(pairId, meshFilter, msg.meshRef, out preferredErr))
            {
                // preferred mesh resource resolved and bound successfully
            }
            else if (!string.IsNullOrWhiteSpace(msg.meshRef) && !string.IsNullOrWhiteSpace(preferredErr))
            {
#if UNITY_EDITOR
                LogMeshWarning("preferred_mesh_bind_failed", preferredErr, pairId, msg.meshRef);
#endif
            }

            var mesh = EnsureMeshForPairDecision(pairId, meshFilter);
            if (mesh == null)
            {
                error = "unable to allocate mesh";
                return false;
            }

            var applyStart = sw.Elapsed.TotalMilliseconds;
            mesh.Clear();
            mesh.ClearBlendShapes();
            mesh.indexFormat = vertices.Length > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16;
            mesh.vertices = vertices;
            if (!ApplySubMeshes(mesh, msg.subMeshes, "triangles", mapTriangleWinding: true))
                mesh.triangles = SceneSyncTransformMapper.MapTriangles(triangles);
            if (normals != null && normals.Length == vertices.Length)
                mesh.normals = normals;
            else
                mesh.RecalculateNormals();
            ApplyUvChannels(mesh, uvChannels, uvs, vertices.Length);
            ApplyColors(mesh, colors, vertices.Length);
            var blendShapeStartMs = sw.Elapsed.TotalMilliseconds;
            ApplyBinaryBlendShapes(mesh, msg.blendShapes, out var blendShapeCount);
            var blendShapeMs = sw.Elapsed.TotalMilliseconds - blendShapeStartMs;
            RecalculateTangentsIfPossible(mesh);
            mesh.RecalculateBounds();
            var applyMs = sw.Elapsed.TotalMilliseconds - applyStart;

#if UNITY_EDITOR
            var prof = msg.profile;
            BlenderSyncReportStore.Add(
                "Mesh Apply Binary",
                "OK",
                $"pair={pairId} v={vertices.Length} i={triangles.Length} blendShapes={blendShapeCount} totalMs={sw.Elapsed.TotalMilliseconds:F2}",
                new Dictionary<string, object>
                {
                    { "pairId", pairId },
                    { "meshRef", msg.meshRef },
                    { "vertices", vertices.Length },
                    { "indices", triangles.Length },
                    { "readMs", readMs.ToString("F2") },
                    { "applyMs", applyMs.ToString("F2") },
                    { "blendShapes", blendShapeCount },
                    { "blendShapeMs", blendShapeMs.ToString("F2") },
                    { "totalMs", sw.Elapsed.TotalMilliseconds.ToString("F2") },
                    { "binaryBytes", prof != null ? prof.binaryBytes : 0 },
                    { "blendShapeBinaryBytes", prof != null ? prof.blendShapeBinaryBytes : 0 },
                    { "jsonBytes", prof != null ? prof.fallbackJsonBytes : 0 },
                });
            BlenderSyncLog.Trace(
                "MeshApply",
                "binary_profile",
                () => "Applied a binary mesh update.",
                () => new Dictionary<string, object>
                {
                    { "pairId", pairId },
                    { "meshRef", msg.meshRef },
                    { "vertices", vertices.Length },
                    { "indices", triangles.Length },
                    { "blendShapes", blendShapeCount },
                    { "readMs", readMs.ToString("F2") },
                    { "applyMs", applyMs.ToString("F2") },
                    { "totalMs", sw.Elapsed.TotalMilliseconds.ToString("F2") },
                    { "binaryBytes", prof != null ? prof.binaryBytes : 0 },
                });
            UpdateMeshRecordContentFingerprint(msg.meshRef, msg.meshContentFingerprint, msg.meshContentFingerprintNoUv);
#endif
            return true;
        }

        public bool TryApply(string pairId, GameObject target, SceneSyncMeshUpdateMessage msg, out string error)
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
            if (msg == null)
            {
                error = "message is null";
                return false;
            }
            if (msg.vertices == null || msg.triangles == null)
            {
                error = "vertices/triangles missing";
                return false;
            }
            if (msg.vertices.Length % 3 != 0)
            {
                error = "invalid vertices layout";
                return false;
            }
            if (msg.triangles.Length % 3 != 0)
            {
                error = "invalid triangles layout";
                return false;
            }
            var vCount = msg.vertices.Length / 3;
            if (!IndicesInRange(msg.triangles, vCount))
            {
                error = $"triangle index out of range vertexCount={vCount}";
                return false;
            }

            var meshFilter = target.GetComponent<MeshFilter>();
            if (meshFilter == null) meshFilter = target.AddComponent<MeshFilter>();
            var renderer = target.GetComponent<MeshRenderer>();
            if (renderer == null) renderer = target.AddComponent<MeshRenderer>();

            string preferredErr = null;
            if (!string.IsNullOrWhiteSpace(msg.meshRef) && TryBindPreferredMeshRef(pairId, meshFilter, msg.meshRef, out preferredErr))
            {
                // preferred mesh resource resolved and bound successfully
            }
            else if (!string.IsNullOrWhiteSpace(msg.meshRef) && !string.IsNullOrWhiteSpace(preferredErr))
            {
#if UNITY_EDITOR
                LogMeshWarning("preferred_mesh_bind_failed", preferredErr, pairId, msg.meshRef);
#endif
            }

            var mesh = EnsureMeshForPairDecision(pairId, meshFilter);
            if (mesh == null)
            {
                error = "unable to allocate mesh";
                return false;
            }

            // Keep scene object identity: update MeshFilter.sharedMesh content only.
            mesh.Clear();
            mesh.ClearBlendShapes();

            var vertices = new Vector3[vCount];
            for (int i = 0, j = 0; i < vCount; i++, j += 3)
                vertices[i] = SceneSyncTransformMapper.MapPoint(new Vector3(msg.vertices[j], msg.vertices[j + 1], msg.vertices[j + 2]));

            mesh.indexFormat = vCount > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16;
            mesh.vertices = vertices;
            if (!ApplySubMeshes(mesh, msg.subMeshes, "triangles", mapTriangleWinding: true))
                mesh.triangles = SceneSyncTransformMapper.MapTriangles(msg.triangles);

            if (msg.normals != null && msg.normals.Length == msg.vertices.Length)
            {
                var normals = new Vector3[vCount];
                for (int i = 0, j = 0; i < vCount; i++, j += 3)
                    normals[i] = SceneSyncTransformMapper.MapDirection(new Vector3(msg.normals[j], msg.normals[j + 1], msg.normals[j + 2])).normalized;
                mesh.normals = normals;
            }
            else
            {
                mesh.RecalculateNormals();
            }

            if (msg.uv != null && msg.uv.Length == vCount * 2)
            {
                var uvs = new Vector2[vCount];
                for (int i = 0, j = 0; i < vCount; i++, j += 2)
                    uvs[i] = new Vector2(msg.uv[j], msg.uv[j + 1]);
                mesh.uv = uvs;
            }

            ApplyBlendShapes(mesh, msg.blendShapes, out _);
            RecalculateTangentsIfPossible(mesh);
            mesh.RecalculateBounds();
            UpdateMeshRecordContentFingerprint(msg.meshRef, msg.meshContentFingerprint, msg.meshContentFingerprintNoUv);
            return true;
        }

        private void UpdateMeshRecordContentFingerprint(string meshRef, string meshContentFingerprint, string meshContentFingerprintNoUv)
        {
#if UNITY_EDITOR
            meshRef = NormalizeOptional(meshRef);
            meshContentFingerprint = NormalizeOptional(meshContentFingerprint);
            meshContentFingerprintNoUv = NormalizeOptional(meshContentFingerprintNoUv);
            if (string.IsNullOrWhiteSpace(meshRef) || (string.IsNullOrWhiteSpace(meshContentFingerprint) && string.IsNullOrWhiteSpace(meshContentFingerprintNoUv)))
                return;

            var db = _aptRepository.Load();
            var record = _aptRepository.FindByAssetId(db, meshRef);
            if (record == null)
                return;
            if (string.Equals(record.meshContentFingerprint, meshContentFingerprint, StringComparison.Ordinal)
                && string.Equals(record.meshContentFingerprintNoUv, meshContentFingerprintNoUv, StringComparison.Ordinal))
                return;

            if (!string.IsNullOrWhiteSpace(meshContentFingerprint))
                record.meshContentFingerprint = meshContentFingerprint;
            if (!string.IsNullOrWhiteSpace(meshContentFingerprintNoUv))
                record.meshContentFingerprintNoUv = meshContentFingerprintNoUv;
            _aptRepository.Upsert(db, record);
            _aptRepository.Save(db);
#endif
        }

        private static string NormalizeOptional(string value)
        {
            var normalized = value?.Trim();
            return string.IsNullOrWhiteSpace(normalized) ? null : normalized;
        }

        public static bool ApplySubMeshes(Mesh mesh, SceneSyncMeshSubMesh[] subMeshes, string fallbackTopology, bool mapTriangleWinding)
        {
            if (mesh == null || subMeshes == null || subMeshes.Length == 0)
                return false;

            var maxMaterialSlot = -1;
            var prepared = new List<(SceneSyncMeshSubMesh Payload, int[] Indices)>();
            foreach (var subMesh in subMeshes)
            {
                if (subMesh == null)
                    continue;
                var sourceIndices = ReadSubMeshIndices(subMesh);
                if (sourceIndices == null || sourceIndices.Length == 0)
                    continue;
                prepared.Add((subMesh, sourceIndices));
                maxMaterialSlot = Math.Max(maxMaterialSlot, Math.Max(0, subMesh.materialSlot));
            }

            if (maxMaterialSlot < 0)
                return false;

            mesh.subMeshCount = maxMaterialSlot + 1;
            for (var slot = 0; slot < mesh.subMeshCount; slot++)
                mesh.SetIndices(Array.Empty<int>(), MeshTopology.Triangles, slot);

            var applied = false;
            foreach (var item in prepared)
            {
                var subMesh = item.Payload;
                var sourceIndices = item.Indices;
                var materialSlot = Math.Max(0, subMesh.materialSlot);
                if (materialSlot >= mesh.subMeshCount)
                    continue;
                var topology = ToMeshTopology(string.IsNullOrWhiteSpace(subMesh.topology) ? fallbackTopology : subMesh.topology);
                if (topology == MeshTopology.Triangles && sourceIndices.Length % 3 != 0)
                    continue;
                if (!IndicesInRange(sourceIndices, mesh.vertexCount))
                    continue;
                var indices = mapTriangleWinding && topology == MeshTopology.Triangles
                    ? SceneSyncTransformMapper.MapTriangles(sourceIndices)
                    : sourceIndices;
                if (indices == null || indices.Length == 0)
                    continue;
                mesh.SetIndices(indices, topology, materialSlot);
                applied = true;
            }

            return applied;
        }

        private static int[] ReadSubMeshIndices(SceneSyncMeshSubMesh subMesh)
        {
            if (subMesh == null)
                return null;
            if (subMesh.indicesBuffer != null)
            {
                var values = ReadIntBuffer(subMesh.indicesBuffer, out var error);
                if (string.IsNullOrWhiteSpace(error) && values != null)
                    return values;
            }
            return subMesh.indices;
        }

        public static void ApplyUvChannels(Mesh mesh, Dictionary<int, Vector2[]> uvChannels, Vector2[] fallbackUv0, int vertexCount)
        {
            if (mesh == null)
                return;
            for (var channel = 0; channel < 8; channel++)
                mesh.SetUVs(channel, new List<Vector2>());
            var applied = false;
            if (uvChannels != null)
            {
                foreach (var kv in uvChannels)
                {
                    var channel = kv.Key;
                    var values = kv.Value;
                    if (channel < 0 || channel > 7 || values == null || values.Length != vertexCount)
                        continue;
                    mesh.SetUVs(channel, new List<Vector2>(values));
                    applied = true;
                }
            }
            if (!applied && fallbackUv0 != null && fallbackUv0.Length == vertexCount)
            {
                mesh.SetUVs(0, new List<Vector2>(fallbackUv0));
                applied = true;
            }
        }

        public static void ApplyColors(Mesh mesh, Color32[] colors, int vertexCount)
        {
            if (mesh == null)
                return;
            if (colors != null && colors.Length == vertexCount)
                mesh.colors32 = colors;
            else
                mesh.colors32 = Array.Empty<Color32>();
        }

        public static bool RecalculateTangentsIfPossible(Mesh mesh)
        {
            if (mesh == null || mesh.vertexCount <= 0)
                return false;

            var vertexCount = mesh.vertexCount;
            var normals = mesh.normals;
            var uv0 = new List<Vector2>();
            mesh.GetUVs(0, uv0);
            if (normals == null || normals.Length != vertexCount || uv0.Count != vertexCount)
            {
                mesh.tangents = Array.Empty<Vector4>();
                return false;
            }

            var indexCount = 0L;
            if (mesh.subMeshCount <= 0)
            {
                mesh.tangents = Array.Empty<Vector4>();
                return false;
            }
            for (var subMesh = 0; subMesh < mesh.subMeshCount; subMesh++)
            {
                if (mesh.GetTopology(subMesh) != MeshTopology.Triangles)
                {
                    mesh.tangents = Array.Empty<Vector4>();
                    return false;
                }
                indexCount += mesh.GetIndexCount(subMesh);
            }
            if (indexCount < 3)
            {
                mesh.tangents = Array.Empty<Vector4>();
                return false;
            }

            mesh.RecalculateTangents();
            if (mesh.tangents == null || mesh.tangents.Length != vertexCount)
            {
                mesh.tangents = Array.Empty<Vector4>();
                return false;
            }
            return true;
        }

        private static MeshTopology ToMeshTopology(string topology)
        {
            return string.Equals(topology, "lines", StringComparison.OrdinalIgnoreCase) ? MeshTopology.Lines : MeshTopology.Triangles;
        }

        private static bool IndicesInRange(int[] indices, int vertexCount)
        {
            if (indices == null)
                return false;
            for (var i = 0; i < indices.Length; i++)
            {
                var index = indices[i];
                if (index < 0 || index >= vertexCount)
                    return false;
            }
            return true;
        }

        public static Color32[] ReadColor32Buffer(SceneSyncBinaryBuffer buffer, int vertexCount, out string error)
        {
            error = null;
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path))
                return null;
            var values = ReadVector4Buffer(buffer, out error);
            if (!string.IsNullOrWhiteSpace(error) || values == null)
                return null;
            if (values.Length != vertexCount)
            {
                error = $"color_count_mismatch expected={vertexCount} actual={values.Length}";
                return null;
            }
            var colors = new Color32[values.Length];
            for (var i = 0; i < values.Length; i++)
            {
                var v = values[i];
                colors[i] = new Color32(
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(v.x) * 255f),
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(v.y) * 255f),
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(v.z) * 255f),
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(v.w) * 255f));
            }
            return colors;
        }

        public static Dictionary<int, Vector2[]> ReadBinaryUvChannels(SceneSyncBinaryUvChannel[] channels, int vertexCount, out string error)
        {
            error = null;
            var result = new Dictionary<int, Vector2[]>();
            if (channels == null || channels.Length == 0)
                return result;
            foreach (var channel in channels)
            {
                if (channel == null || channel.buffer == null)
                    continue;
                var index = channel.index;
                if (index < 0 || index > 7)
                    continue;
                var values = ReadVector2Buffer(channel.buffer, out var readError);
                if (!string.IsNullOrWhiteSpace(readError))
                {
                    error = readError;
                    continue;
                }
                if (values == null || values.Length != vertexCount)
                {
                    error = $"uv_channel_count_mismatch channel={index} expected={vertexCount} actual={(values != null ? values.Length : 0)}";
                    continue;
                }
                result[index] = values;
            }
            return result;
        }

        private static bool TryReadBinaryBuffers(SceneSyncMeshUpdateBinaryMessage msg, out Vector3[] vertices, out int[] triangles, out Vector3[] normals, out Vector2[] uvs, out string error)
        {
            vertices = null;
            triangles = null;
            normals = null;
            uvs = null;
            error = null;

            foreach (var buffer in msg.buffers)
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
                    vertices = ReadVector3Buffer(buffer, mapDirection: false, out error);
                else if (semantic == "NORMAL")
                    normals = ReadVector3Buffer(buffer, mapDirection: true, out error);
                else if (semantic == "UV0")
                    uvs = ReadVector2Buffer(buffer, out error);
                else if (semantic == "INDEX")
                    triangles = ReadIntBuffer(buffer, out error);

                if (!string.IsNullOrWhiteSpace(error))
                    return false;
            }

            return true;
        }

        private static Vector3[] ReadVector3Buffer(SceneSyncBinaryBuffer buffer, bool mapDirection, out string error)
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
                var v = new Vector3(BitConverter.ToSingle(bytes, o), BitConverter.ToSingle(bytes, o + 4), BitConverter.ToSingle(bytes, o + 8));
                values[i] = mapDirection ? SceneSyncTransformMapper.MapDirection(v).normalized : SceneSyncTransformMapper.MapPoint(v);
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

        private static Vector4[] ReadVector4Buffer(SceneSyncBinaryBuffer buffer, out string error)
        {
            error = null;
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path))
            {
                error = $"buffer missing: {buffer?.semantic} path={buffer?.path}";
                return null;
            }

            var bytes = File.ReadAllBytes(buffer.path);
            var components = buffer.components > 0 ? buffer.components : 4;
            if (!IsFloat32(buffer.format) || components < 4 || bytes.Length % 4 != 0)
            {
                error = $"invalid float4 buffer semantic={buffer.semantic} format={buffer.format} components={buffer.components} bytes={bytes.Length}";
                return null;
            }
            var floatCount = bytes.Length / 4;
            var count = buffer.count > 0 ? Math.Min(buffer.count, floatCount / components) : floatCount / components;
            var values = new Vector4[count];
            for (var i = 0; i < count; i++)
            {
                var o = i * components * 4;
                values[i] = new Vector4(BitConverter.ToSingle(bytes, o), BitConverter.ToSingle(bytes, o + 4), BitConverter.ToSingle(bytes, o + 8), BitConverter.ToSingle(bytes, o + 12));
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

        public static void ApplyBlendShapes(Mesh mesh, MeshBlendShapePayload[] blendShapes, out int applied)
        {
            applied = 0;
            if (mesh == null || blendShapes == null || blendShapes.Length == 0)
                return;
            var vertexCount = mesh.vertexCount;
            foreach (var shape in blendShapes)
            {
                if (shape == null || string.IsNullOrWhiteSpace(shape.name))
                    continue;
                Vector3[] deltas = null;
                if (shape.deltaPositionsBuffer != null)
                {
                    deltas = ReadBlendShapeDeltaBuffer(shape.deltaPositionsBuffer, vertexCount, out var binaryError);
#if UNITY_EDITOR
                    if (!string.IsNullOrWhiteSpace(binaryError))
                        BlenderSyncLog.Warn(
                            "MeshApply",
                            "blend_shape_binary_fallback",
                            binaryError,
                            new Dictionary<string, object> { { "shapeName", shape.name } });
#endif
                }
                if (deltas == null && shape.deltaPositions != null && shape.deltaPositions.Length == vertexCount * 3)
                    deltas = MapBlendShapeDeltas(shape.deltaPositions, vertexCount);
                if (deltas == null)
                    continue;
                var frameWeight = shape.frameWeight > 0.0001f ? shape.frameWeight : 100.0f;
                mesh.AddBlendShapeFrame(shape.name, frameWeight, deltas, null, null);
                applied++;
            }
        }

        public static void ApplyBinaryBlendShapes(Mesh mesh, SceneSyncBinaryBlendShape[] blendShapes, out int applied)
        {
            applied = 0;
            if (mesh == null || blendShapes == null || blendShapes.Length == 0)
                return;
            var vertexCount = mesh.vertexCount;
            foreach (var shape in blendShapes)
            {
                if (shape == null || string.IsNullOrWhiteSpace(shape.name) || shape.deltaPositionsBuffer == null)
                    continue;
                var deltas = ReadBlendShapeDeltaBuffer(shape.deltaPositionsBuffer, vertexCount, out var error);
#if UNITY_EDITOR
                if (!string.IsNullOrWhiteSpace(error))
                    BlenderSyncLog.Warn(
                        "MeshApply",
                        "blend_shape_binary_read_failed",
                        error,
                        new Dictionary<string, object> { { "shapeName", shape.name } });
#endif
                if (deltas == null)
                    continue;
                var frameWeight = shape.frameWeight > 0.0001f ? shape.frameWeight : 100.0f;
                mesh.AddBlendShapeFrame(shape.name, frameWeight, deltas, null, null);
                applied++;
            }
        }

        public static void ApplyBlendShapes(Mesh mesh, SceneSyncMeshBlendShape[] blendShapes, out int applied)
        {
            applied = 0;
            if (mesh == null || blendShapes == null || blendShapes.Length == 0)
                return;
            var vertexCount = mesh.vertexCount;
            foreach (var shape in blendShapes)
            {
                if (shape == null || string.IsNullOrWhiteSpace(shape.name))
                    continue;
                var deltas = shape.deltaPositions != null && shape.deltaPositions.Length == vertexCount * 3
                    ? MapBlendShapeDeltas(shape.deltaPositions, vertexCount)
                    : null;
                if (deltas == null)
                    continue;
                var frameWeight = shape.frameWeight > 0.0001f ? shape.frameWeight : 100.0f;
                mesh.AddBlendShapeFrame(shape.name, frameWeight, deltas, null, null);
                applied++;
            }
        }

        private static Vector3[] ReadBlendShapeDeltaBuffer(SceneSyncBinaryBuffer buffer, int vertexCount, out string error)
        {
            error = null;
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path))
            {
                error = $"blend shape buffer missing path={buffer?.path}";
                return null;
            }
            var bytes = File.ReadAllBytes(buffer.path);
            var components = buffer.components > 0 ? buffer.components : 3;
            if (!IsFloat32(buffer.format) || components < 3 || bytes.Length % 4 != 0)
            {
                error = $"invalid blend shape float3 buffer format={buffer.format} components={buffer.components} bytes={bytes.Length}";
                return null;
            }
            var floatCount = bytes.Length / 4;
            var count = buffer.count > 0 ? Math.Min(buffer.count, floatCount / components) : floatCount / components;
            if (count != vertexCount)
            {
                error = $"blend shape vertex count mismatch expected={vertexCount} actual={count}";
                return null;
            }
            var values = new Vector3[count];
            for (var i = 0; i < count; i++)
            {
                var o = i * components * 4;
                var v = new Vector3(BitConverter.ToSingle(bytes, o), BitConverter.ToSingle(bytes, o + 4), BitConverter.ToSingle(bytes, o + 8));
                values[i] = SceneSyncTransformMapper.MapDirection(v);
            }
            return values;
        }

        private static Vector3[] ReadBlendShapeDeltaBuffer(MeshBinaryBuffer buffer, int vertexCount, out string error)
        {
            error = null;
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path))
            {
                error = $"blend shape buffer missing path={buffer?.path}";
                return null;
            }
            var bytes = File.ReadAllBytes(buffer.path);
            var components = buffer.components > 0 ? buffer.components : 3;
            if (!IsFloat32(buffer.format) || components < 3 || bytes.Length % 4 != 0)
            {
                error = $"invalid blend shape float3 buffer format={buffer.format} components={buffer.components} bytes={bytes.Length}";
                return null;
            }
            var floatCount = bytes.Length / 4;
            var count = buffer.count > 0 ? Math.Min(buffer.count, floatCount / components) : floatCount / components;
            if (count != vertexCount)
            {
                error = $"blend shape vertex count mismatch expected={vertexCount} actual={count}";
                return null;
            }
            var values = new Vector3[count];
            for (var i = 0; i < count; i++)
            {
                var o = i * components * 4;
                var v = new Vector3(BitConverter.ToSingle(bytes, o), BitConverter.ToSingle(bytes, o + 4), BitConverter.ToSingle(bytes, o + 8));
                values[i] = SceneSyncTransformMapper.MapDirection(v);
            }
            return values;
        }

        private static Vector3[] MapBlendShapeDeltas(float[] deltaPositions, int vertexCount)
        {
            var deltas = new Vector3[vertexCount];
            for (var i = 0; i < vertexCount; i++)
            {
                var baseIndex = i * 3;
                deltas[i] = SceneSyncTransformMapper.MapDirection(new Vector3(
                    deltaPositions[baseIndex],
                    deltaPositions[baseIndex + 1],
                    deltaPositions[baseIndex + 2]));
            }
            return deltas;
        }

        private bool TryBindPreferredMeshRef(string pairId, MeshFilter meshFilter, string meshRef, out string error)
        {
            error = null;
            if (meshFilter == null)
            {
                error = "meshFilter is null";
                return false;
            }
            if (string.IsNullOrWhiteSpace(meshRef))
            {
                error = "meshRef is missing";
                return false;
            }
            if (!_resolver.TryResolveMesh(meshRef, out var resolvedMesh, out error) || resolvedMesh == null)
                return false;

            meshFilter.sharedMesh = resolvedMesh;
            RegisterPairMeshBinding(pairId, resolvedMesh);
            return true;
        }

        private static Mesh EnsureMeshForPairDecision(string pairId, MeshFilter meshFilter)
        {
            var current = meshFilter.sharedMesh;
            if (current == null)
            {
                var created = new Mesh { name = "SceneSyncMesh" };
                meshFilter.sharedMesh = created;
                BindPairToMesh(pairId, GetMeshObjectId(created));
                return created;
            }

            var currentId = GetMeshObjectId(current);
            BindPairToMesh(pairId, currentId);

            // Fallback only: persistent forks should happen through PreviewMeshService commit.
            // Keep this runtime split as a safety net to avoid mutating a mesh currently shared by multiple pairs.
            if (GetMeshReferenceCount(currentId) > 1)
            {
                var split = UnityEngine.Object.Instantiate(current);
                split.name = current.name + "_SceneSyncSplit";
                meshFilter.sharedMesh = split;

                UnbindPairFromMesh(pairId, currentId);
                BindPairToMesh(pairId, GetMeshObjectId(split));
#if UNITY_EDITOR
                BlenderSyncLog.Trace(
                    "MeshApply",
                    "runtime_mesh_split",
                    () => "Split a shared runtime mesh before applying an update.",
                    () => new Dictionary<string, object> { { "pairId", pairId } });
#endif
                return split;
            }

            return current;
        }

#if UNITY_EDITOR
        private static void LogMeshWarning(string eventName, string summary, string pairId, string meshRef)
        {
            BlenderSyncLog.Warn(
                "MeshApply",
                eventName,
                summary,
                new Dictionary<string, object>
                {
                    { "pairId", pairId },
                    { "meshRef", meshRef },
                });
        }
#endif

        public static void RegisterPairMeshBinding(string pairId, Mesh mesh)
        {
            var normalizedPairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(normalizedPairId) || mesh == null)
                return;
            BindPairToMesh(normalizedPairId, GetMeshObjectId(mesh));
        }

        public static void ClearPairMeshBinding(string pairId)
        {
            var normalizedPairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(normalizedPairId))
                return;
            if (TryGetBoundMeshId(normalizedPairId, out var meshId))
                UnbindPairFromMesh(normalizedPairId, meshId);
        }

        public static bool IsPairBoundToMesh(string pairId, Mesh mesh)
        {
            var normalizedPairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(normalizedPairId) || mesh == null)
                return false;
            return TryGetBoundMeshId(normalizedPairId, out var meshId) && meshId == GetMeshObjectId(mesh);
        }

        private static MeshObjectId GetMeshObjectId(Mesh mesh)
        {
#if UNITY_6000_4_OR_NEWER
            return mesh.GetEntityId();
#else
            return mesh.GetInstanceID();
#endif
        }

        private static void BindPairToMesh(string pairId, MeshObjectId meshId)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
                return;

            lock (MeshBindingLock)
            {
                if (PairToMesh.TryGetValue(pairId, out var prevMeshId) && prevMeshId != meshId)
                    UnbindPairFromMeshLocked(pairId, prevMeshId);

                PairToMesh[pairId] = meshId;
                if (!MeshRefs.TryGetValue(meshId, out var refs))
                {
                    refs = new HashSet<string>();
                    MeshRefs[meshId] = refs;
                }
                refs.Add(pairId);
            }
        }

        private static void UnbindPairFromMesh(string pairId, MeshObjectId meshId)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
                return;

            lock (MeshBindingLock)
            {
                UnbindPairFromMeshLocked(pairId, meshId);
            }
        }

        private static void UnbindPairFromMeshLocked(string pairId, MeshObjectId meshId)
        {
            if (MeshRefs.TryGetValue(meshId, out var refs))
            {
                refs.Remove(pairId);
                if (refs.Count == 0)
                    MeshRefs.Remove(meshId);
            }

            if (PairToMesh.TryGetValue(pairId, out var mapped) && mapped == meshId)
                PairToMesh.Remove(pairId);
        }

        private static bool TryGetBoundMeshId(string pairId, out MeshObjectId meshId)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
            {
                meshId = default;
                return false;
            }

            lock (MeshBindingLock)
            {
                return PairToMesh.TryGetValue(pairId, out meshId);
            }
        }

        private static int GetMeshReferenceCount(MeshObjectId meshId)
        {
            lock (MeshBindingLock)
            {
                return MeshRefs.TryGetValue(meshId, out var refs) ? refs.Count : 0;
            }
        }

        private static string NormalizePairId(string pairId)
        {
            return (pairId ?? string.Empty).Trim();
        }
    }
}
