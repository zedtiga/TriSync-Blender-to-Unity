using System;
using System.Collections.Generic;
using System.IO;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.Protocol;
using BlenderSyncVNext.SceneSyncCore;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
using UnityEngine.Rendering;
#if UNITY_EDITOR
using Unity.Collections;
using UnityEditor;
#endif

namespace BlenderSyncVNext.AssetBridgeCore
{
    public sealed class AssetContainerService
    {
        public static MeshSkinPayload LastImportedMeshSkinPayload { get; private set; }
        public static string LastImportedMeshAssetId { get; private set; }
        public static string LastImportedMeshAssetPath { get; private set; }
        public static string LastImportedMeshOperation { get; private set; }
        public static string LastImportedMeshFingerprint { get; private set; }
        public static long LastImportedMeshAtUnixMs { get; private set; }
        public static bool LastImportedMeshUsedBinary { get; private set; }
        public static long LastImportedMeshBinaryBytes { get; private set; }
        public static bool DeferAssetDatabaseSave { get; set; }

        public ImportResult ImportOrCreateContainer(AssetBridgeResourceEntry entry, string priorFingerprint = null, string existingUnityAssetPath = null)
        {
            if (entry == null || string.IsNullOrWhiteSpace(entry.assetId))
            {
                return new ImportResult { success = false, error = "invalid resource entry" };
            }

            if (entry.type == "mesh" && entry.mesh != null)
            {
                var root = Path.Combine(Directory.GetCurrentDirectory(), "Assets", "TriSync", "Resources", "Meshes");
                Directory.CreateDirectory(root);
                return ImportOrUpdateMesh(entry, priorFingerprint, existingUnityAssetPath);
            }


            return new ImportResult
            {
                success = false,
                unityAssetPath = null,
                error = "unsupported_resource_type",
                operation = "skip",
            };
        }

        private ImportResult ImportOrUpdateMesh(AssetBridgeResourceEntry entry, string priorFingerprint, string existingUnityAssetPath)
        {
            var totalSw = System.Diagnostics.Stopwatch.StartNew();
            var assetPath = ChooseMeshAssetPath(entry, existingUnityAssetPath);
            var operation = string.IsNullOrWhiteSpace(priorFingerprint)
                ? "create"
                : (priorFingerprint == entry.sourceFingerprint ? "skip" : "update");

#if UNITY_EDITOR
            var assetDir = Path.GetDirectoryName(assetPath);
            if (!string.IsNullOrWhiteSpace(assetDir))
                Directory.CreateDirectory(assetDir);

            var meshAsset = AssetDatabase.LoadAssetAtPath<Mesh>(assetPath);
            var assetExists = meshAsset != null;
            if (operation == "skip" && !assetExists)
                operation = "create";
            else if (operation == "create" && assetExists)
                operation = "update";

            if (operation != "skip")
            {
                var applySw = System.Diagnostics.Stopwatch.StartNew();
                MeshPayloadApplyProfile applyProfile;
                if (meshAsset == null)
                {
                    meshAsset = new Mesh { name = entry.assetId };
                    applyProfile = ApplyMeshPayload(meshAsset, entry.mesh);
                    AssetDatabase.CreateAsset(meshAsset, assetPath);
                }
                else
                {
                    applyProfile = ApplyMeshPayload(meshAsset, entry.mesh);
                    if (string.IsNullOrWhiteSpace(meshAsset.name))
                        meshAsset.name = entry.assetId;
                    EditorUtility.SetDirty(meshAsset);
                }
                double saveMs = 0;
                double refreshMs = 0;
                if (!DeferAssetDatabaseSave)
                {
                    var saveStart = totalSw.Elapsed.TotalMilliseconds;
                    AssetDatabase.SaveAssets();
                    saveMs = totalSw.Elapsed.TotalMilliseconds - saveStart;
                    var refreshStart = totalSw.Elapsed.TotalMilliseconds;
                    AssetDatabase.Refresh();
                    refreshMs = totalSw.Elapsed.TotalMilliseconds - refreshStart;
                }
                BlenderSyncLog.Trace(
                    "AssetImport",
                    "mesh_asset_apply_profile",
                    () => "Applied a mesh payload to its Unity asset.",
                    () => new Dictionary<string, object>
                    {
                        { "assetId", entry.assetId },
                        { "operation", operation },
                        { "vertices", applyProfile.vertexCount },
                        { "indices", applyProfile.indexCount },
                        { "subMeshes", applyProfile.subMeshCount },
                        { "clearMs", applyProfile.clearMs.ToString("F2") },
                        { "readVerticesMs", applyProfile.readVerticesMs.ToString("F2") },
                        { "readIndicesMs", applyProfile.readIndicesMs.ToString("F2") },
                        { "setVerticesMs", applyProfile.setVerticesMs.ToString("F2") },
                        { "setNormalsMs", applyProfile.setNormalsMs.ToString("F2") },
                        { "uvChannelsMs", applyProfile.uvChannelsMs.ToString("F2") },
                        { "skinMs", applyProfile.skinMs.ToString("F2") },
                        { "blendShapeMs", applyProfile.blendShapeMs.ToString("F2") },
                        { "recalcTangentsMs", applyProfile.recalcTangentsMs.ToString("F2") },
                        { "applyBlockMs", applySw.Elapsed.TotalMilliseconds.ToString("F2") },
                        { "saveMs", saveMs.ToString("F2") },
                        { "refreshMs", refreshMs.ToString("F2") },
                    });
            }
#endif

            if (entry.mesh != null && entry.mesh.skin != null && entry.mesh.skin.isSkinned)
            {
                LastImportedMeshSkinPayload = entry.mesh.skin;
                LastImportedMeshAssetId = entry.assetId;
                LastImportedMeshAssetPath = assetPath;
                LastImportedMeshOperation = operation;
                LastImportedMeshFingerprint = entry.sourceFingerprint;
                LastImportedMeshAtUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                LastImportedMeshUsedBinary = entry.mesh.binaryBuffers != null && entry.mesh.binaryBuffers.Length > 0;
                LastImportedMeshBinaryBytes = entry.mesh.binaryProfile != null ? entry.mesh.binaryProfile.binaryBytes : 0;
            }

            var meshGuid = CaptureAssetGuid(assetPath);
            BlenderSyncLog.Trace(
                "AssetImport",
                "mesh_asset_imported",
                () => "Imported a mesh resource into its Unity asset.",
                () => new Dictionary<string, object>
                {
                    { "assetId", entry.assetId },
                    { "operation", operation },
                    { "vertices", entry.mesh.vertexCount },
                    { "skinned", entry.mesh.skin != null && entry.mesh.skin.isSkinned },
                    { "usedBinary", LastImportedMeshAssetId == entry.assetId && LastImportedMeshUsedBinary },
                    { "binaryBytes", LastImportedMeshAssetId == entry.assetId ? LastImportedMeshBinaryBytes : 0L },
                    { "totalMs", totalSw.Elapsed.TotalMilliseconds.ToString("F2") },
                });
            return new ImportResult
            {
                success = true,
                unityAssetPath = assetPath,
                assetGuid = meshGuid,
                localFileId = 0,
                error = null,
                operation = operation,
            };
        }

#if UNITY_EDITOR
        private static MeshPayloadApplyProfile ApplyMeshPayload(Mesh mesh, MeshPayload payload)
        {
            var profile = new MeshPayloadApplyProfile();
            var sw = System.Diagnostics.Stopwatch.StartNew();
            if (mesh == null || payload == null)
                return profile;

            var segmentStart = sw.Elapsed.TotalMilliseconds;
            mesh.Clear();
            mesh.ClearBlendShapes();
            profile.clearMs = sw.Elapsed.TotalMilliseconds - segmentStart;

            segmentStart = sw.Elapsed.TotalMilliseconds;
            var vertices = MapPointsToUnity(ReadVector3Payload(payload.vertices, GetBinaryBuffer(payload, "POSITION")));
            if (vertices.Length == 0)
                throw new InvalidOperationException("mesh_vertices_missing");
            profile.readVerticesMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            var normals = MapDirectionsToUnity(ReadVector3Payload(payload.normals, GetBinaryBuffer(payload, "NORMAL")));
            profile.readNormalsMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            var uv0 = ReadVector2Payload(payload.uv0, GetBinaryBuffer(payload, "UV0"));
            profile.readUv0Ms = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            var fallbackTopology = ToMeshTopology(payload.topology);
            var indices = ReadIntPayload(payload.indices, GetBinaryBuffer(payload, "INDEX"));
            if (indices.Length == 0)
                throw new InvalidOperationException("mesh_indices_missing");
            ValidateIndices(indices, vertices.Length, fallbackTopology, "mesh_indices");
            indices = MapIndicesForTopology(indices, fallbackTopology);
            profile.readIndicesMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            var colors = ReadColor32Payload(payload.color0, payload.color0Buffer, vertices.Length);
            profile.readColorMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            profile.vertexCount = vertices.Length;
            profile.indexCount = indices.Length;
            profile.subMeshCount = payload.subMeshes != null ? payload.subMeshes.Length : 0;

            segmentStart = sw.Elapsed.TotalMilliseconds;
            mesh.indexFormat = vertices.Length > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16;
            profile.setFormatMs = sw.Elapsed.TotalMilliseconds - segmentStart;

            segmentStart = sw.Elapsed.TotalMilliseconds;
            if (vertices.Length > 0) mesh.vertices = vertices;
            profile.setVerticesMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            if (normals.Length == vertices.Length) mesh.normals = normals;
            profile.setNormalsMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            if (uv0.Length == vertices.Length) mesh.uv = uv0;
            profile.setUv0Ms = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            if (colors != null && colors.Length == vertices.Length) mesh.colors32 = colors;
            else mesh.colors32 = null;
            profile.setColorsMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            ApplyUvChannels(mesh, payload.uvChannels, vertices.Length);
            profile.uvChannelsMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            if (!ApplySubMeshes(mesh, payload))
            {
                profile.subMeshesMs = sw.Elapsed.TotalMilliseconds - segmentStart;
                segmentStart = sw.Elapsed.TotalMilliseconds;
                if (indices.Length > 0)
                    mesh.SetIndices(indices, fallbackTopology, 0);
                profile.fallbackIndicesMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            }
            else
            {
                profile.subMeshesMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            }
            segmentStart = sw.Elapsed.TotalMilliseconds;
            ApplyMeshSkinPayload(mesh, payload, vertices.Length);
            profile.skinMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            ApplyAssetBridgeBlendShapes(mesh, payload.blendShapes, vertices.Length);
            profile.blendShapeMs = sw.Elapsed.TotalMilliseconds - segmentStart;

            segmentStart = sw.Elapsed.TotalMilliseconds;
            mesh.RecalculateBounds();
            profile.boundsMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            if (normals.Length != vertices.Length) mesh.RecalculateNormals();
            profile.recalcNormalsMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            segmentStart = sw.Elapsed.TotalMilliseconds;
            MeshApplyService.RecalculateTangentsIfPossible(mesh);
            profile.recalcTangentsMs = sw.Elapsed.TotalMilliseconds - segmentStart;
            profile.totalMs = sw.Elapsed.TotalMilliseconds;
            return profile;
        }

        private static bool ApplySubMeshes(Mesh mesh, MeshPayload payload)
        {
            if (mesh == null || payload == null || payload.subMeshes == null || payload.subMeshes.Length == 0)
                return false;

            var prepared = new List<(MeshSubMeshPayload Payload, int[] Indices)>();
            foreach (var subMesh in payload.subMeshes)
            {
                if (subMesh == null)
                    continue;
                var sourceIndices = ReadIntPayload(subMesh.indices, subMesh.indicesBuffer);
                if (sourceIndices == null || sourceIndices.Length == 0)
                    continue;
                prepared.Add((subMesh, sourceIndices));
            }

            var validSubMeshes = new List<(int MaterialSlot, MeshTopology Topology, int[] Indices)>();
            var maxMaterialSlot = -1;
            foreach (var item in prepared)
            {
                var subMesh = item.Payload;
                var materialSlot = Math.Max(0, subMesh.materialSlot);
                var topology = ToMeshTopology(string.IsNullOrWhiteSpace(subMesh.topology) ? payload.topology : subMesh.topology);
                if (!AreIndicesValid(item.Indices, mesh.vertexCount, topology))
                    continue;
                var indices = MapIndicesForTopology(item.Indices, topology);
                if (indices == null || indices.Length == 0)
                    continue;

                validSubMeshes.Add((materialSlot, topology, indices));
                maxMaterialSlot = Math.Max(maxMaterialSlot, materialSlot);
            }

            if (validSubMeshes.Count == 0 || maxMaterialSlot < 0)
                return false;

            mesh.subMeshCount = maxMaterialSlot + 1;
            for (var slot = 0; slot < mesh.subMeshCount; slot++)
                mesh.SetIndices(Array.Empty<int>(), MeshTopology.Triangles, slot);

            foreach (var item in validSubMeshes)
            {
                if (item.MaterialSlot >= mesh.subMeshCount)
                    continue;
                mesh.SetIndices(item.Indices, item.Topology, item.MaterialSlot);
            }

            return true;
        }

        private static int[] MapIndicesForTopology(int[] indices, MeshTopology topology)
        {
            if (indices == null)
                return Array.Empty<int>();
            return topology == MeshTopology.Triangles
                ? SceneSyncTransformMapper.MapTriangles(indices)
                : indices;
        }

        private static void ValidateIndices(int[] indices, int vertexCount, MeshTopology topology, string label)
        {
            if (!AreIndicesValid(indices, vertexCount, topology))
                throw new InvalidOperationException($"{label}_invalid vertexCount={vertexCount} indexCount={(indices == null ? -1 : indices.Length)} topology={topology}");
        }

        private static bool AreIndicesValid(int[] indices, int vertexCount, MeshTopology topology)
        {
            if (indices == null)
                return false;
            if (topology == MeshTopology.Triangles && indices.Length % 3 != 0)
                return false;
            if (vertexCount < 0)
                return false;
            for (var i = 0; i < indices.Length; i++)
            {
                var index = indices[i];
                if (index < 0 || index >= vertexCount)
                    return false;
            }
            return true;
        }

        private static void ApplyMeshSkinPayload(Mesh mesh, MeshPayload payload, int vertexCount)
        {
            if (mesh == null || payload == null)
                return;
            if (payload.skin == null || !payload.skin.isSkinned)
            {
                mesh.bindposes = Array.Empty<Matrix4x4>();
                using var emptyBonesPerVertex = new NativeArray<byte>(0, Allocator.Temp);
                using var emptyWeights = new NativeArray<BoneWeight1>(0, Allocator.Temp);
                mesh.SetBoneWeights(emptyBonesPerVertex, emptyWeights);
                return;
            }

            var skin = payload.skin;
            if (!string.Equals(skin.skinEncoding ?? "variable", "variable", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException($"mesh_skin_encoding_unsupported: {skin.skinEncoding}");

            if (skin.boneCount <= 0)
                throw new InvalidOperationException("mesh_skin_bone_count_invalid");

            mesh.bindposes = BuildBindPoses(skin);
            ApplyVariableInfluenceSkin(mesh, skin, vertexCount);
        }

        private static Matrix4x4[] BuildBindPoses(MeshSkinPayload skin)
        {
            if (skin.bindPoses == null || skin.bindPoses.Length != skin.boneCount * 16)
                throw new InvalidOperationException("mesh_skin_bindposes_invalid");

            var bindposes = new Matrix4x4[skin.boneCount];
            for (var i = 0; i < skin.boneCount; i++)
            {
                bindposes[i] = SceneSyncTransformMapper.MapRowMajorMatrix(skin.bindPoses, i * 16);
            }
            return bindposes;
        }

        private static void ApplyVariableInfluenceSkin(Mesh mesh, MeshSkinPayload skin, int vertexCount)
        {
            if (skin.bonesPerVertex == null || skin.bonesPerVertex.Length != vertexCount)
                throw new InvalidOperationException($"mesh_skin_bones_per_vertex_invalid expected={vertexCount} actual={(skin.bonesPerVertex == null ? -1 : skin.bonesPerVertex.Length)}");
            if (skin.boneIndices == null || skin.boneWeights == null)
                throw new InvalidOperationException("mesh_skin_influences_missing");
            if (skin.boneIndices.Length != skin.boneWeights.Length)
                throw new InvalidOperationException("mesh_skin_influence_array_length_mismatch");

            var influenceCount = 0;
            for (var i = 0; i < skin.bonesPerVertex.Length; i++)
            {
                if (skin.bonesPerVertex[i] < 0 || skin.bonesPerVertex[i] > byte.MaxValue)
                    throw new InvalidOperationException($"mesh_skin_bones_per_vertex_value_invalid vertex={i} value={skin.bonesPerVertex[i]}");
                influenceCount += skin.bonesPerVertex[i];
            }
            if (influenceCount != skin.boneIndices.Length)
                throw new InvalidOperationException($"mesh_skin_influence_count_mismatch expected={influenceCount} actual={skin.boneIndices.Length}");

            var bonesPerVertex = new NativeArray<byte>(vertexCount, Allocator.Temp);
            var weights = new NativeArray<BoneWeight1>(influenceCount, Allocator.Temp);
            try
            {
                var cursor = 0;
                for (var v = 0; v < vertexCount; v++)
                {
                    var count = skin.bonesPerVertex[v];
                    bonesPerVertex[v] = (byte)count;

                    var weightSum = 0f;
                    for (var i = 0; i < count; i++)
                    {
                        var sourceIndex = cursor + i;
                        var boneIndex = skin.boneIndices[sourceIndex];
                        if (boneIndex < 0 || boneIndex >= skin.boneCount)
                            throw new InvalidOperationException($"mesh_skin_bone_index_out_of_range vertex={v} boneIndex={boneIndex} boneCount={skin.boneCount}");

                        weightSum += skin.boneWeights[sourceIndex];
                    }

                    if (count > 0 && weightSum <= 0f)
                        throw new InvalidOperationException($"mesh_skin_vertex_weight_sum_invalid vertex={v}");

                    for (var i = 0; i < count; i++)
                    {
                        var sourceIndex = cursor + i;
                        weights[sourceIndex] = new BoneWeight1
                        {
                            boneIndex = skin.boneIndices[sourceIndex],
                            weight = skin.boneWeights[sourceIndex] / weightSum,
                        };
                    }
                    cursor += count;
                }

                mesh.SetBoneWeights(bonesPerVertex, weights);
            }
            finally
            {
                if (weights.IsCreated)
                    weights.Dispose();
                if (bonesPerVertex.IsCreated)
                    bonesPerVertex.Dispose();
            }
        }

        private static void ApplyAssetBridgeBlendShapes(Mesh mesh, MeshBlendShapePayload[] blendShapes, int vertexCount)
        {
            if (mesh == null || blendShapes == null || vertexCount <= 0)
                return;

            mesh.ClearBlendShapes();
            foreach (var shape in blendShapes)
            {
                if (shape == null || string.IsNullOrWhiteSpace(shape.name))
                    continue;
                var deltaPositions = MapVectorsToUnity(ReadVector3Payload(shape.deltaPositions, shape.deltaPositionsBuffer));
                if (deltaPositions.Length != vertexCount)
                    continue;
                var deltaNormals = new Vector3[vertexCount];
                var deltaTangents = new Vector3[vertexCount];
                var weight = shape.frameWeight != 0f ? shape.frameWeight : 100f;
                mesh.AddBlendShapeFrame(shape.name, weight, deltaPositions, deltaNormals, deltaTangents);
            }
        }

        private static MeshTopology ToMeshTopology(string topology)
        {
            return string.Equals(topology, "lines", StringComparison.OrdinalIgnoreCase) ? MeshTopology.Lines : MeshTopology.Triangles;
        }

        private static MeshBinaryBuffer GetBinaryBuffer(MeshPayload payload, string semantic)
        {
            if (payload?.binaryBuffers == null || string.IsNullOrWhiteSpace(semantic)) return null;
            foreach (var buffer in payload.binaryBuffers)
            {
                if (buffer != null && string.Equals(buffer.semantic, semantic, StringComparison.OrdinalIgnoreCase))
                    return buffer;
            }
            return null;
        }

        private static Vector3[] MapPointsToUnity(Vector3[] values)
        {
            if (values == null || values.Length == 0) return Array.Empty<Vector3>();
            var result = new Vector3[values.Length];
            for (var i = 0; i < values.Length; i++) result[i] = SceneSyncTransformMapper.MapPoint(values[i]);
            return result;
        }

        private static Vector3[] MapDirectionsToUnity(Vector3[] values)
        {
            if (values == null || values.Length == 0) return Array.Empty<Vector3>();
            var result = new Vector3[values.Length];
            for (var i = 0; i < values.Length; i++) result[i] = SceneSyncTransformMapper.MapDirection(values[i]).normalized;
            return result;
        }

        private static Vector3[] MapVectorsToUnity(Vector3[] values)
        {
            if (values == null || values.Length == 0) return Array.Empty<Vector3>();
            var result = new Vector3[values.Length];
            for (var i = 0; i < values.Length; i++) result[i] = SceneSyncTransformMapper.MapDirection(values[i]);
            return result;
        }

        private static Vector3[] ReadVector3Payload(float[] data, MeshBinaryBuffer buffer)
        {
            if (buffer != null)
            {
                var values = ReadVector3Buffer(buffer);
                if (values != null)
                    return values;
            }
            return ToVector3Array(data);
        }

        private static Vector2[] ReadVector2Payload(float[] data, MeshBinaryBuffer buffer)
        {
            if (buffer != null)
            {
                var values = ReadVector2Buffer(buffer);
                if (values != null)
                    return values;
            }
            return ToVector2Array(data);
        }

        private static int[] ReadIntPayload(int[] data, MeshBinaryBuffer buffer)
        {
            if (buffer != null)
            {
                var values = ReadIntBuffer(buffer);
                if (values != null) return values;
            }
            return data ?? Array.Empty<int>();
        }

        private static float[] ReadFloatBuffer(MeshBinaryBuffer buffer)
        {
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path)) return null;
            var bytes = File.ReadAllBytes(buffer.path);
            if (bytes.Length % 4 != 0) return null;
            var values = new float[bytes.Length / 4];
            Buffer.BlockCopy(bytes, 0, values, 0, bytes.Length);
            return values;
        }

        private static int[] ReadIntBuffer(MeshBinaryBuffer buffer)
        {
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path)) return null;
            if (!string.IsNullOrWhiteSpace(buffer.format) && !string.Equals(buffer.format, "int32", StringComparison.OrdinalIgnoreCase)) return null;
            var bytes = File.ReadAllBytes(buffer.path);
            if (bytes.Length % 4 != 0) return null;
            var values = new int[bytes.Length / 4];
            Buffer.BlockCopy(bytes, 0, values, 0, bytes.Length);
            return values;
        }

        private static Vector3[] ToVector3Array(float[] data)
        {
            if (data == null || data.Length < 3) return Array.Empty<Vector3>();
            var count = data.Length / 3;
            var result = new Vector3[count];
            for (var i = 0; i < count; i++) result[i] = new Vector3(data[i * 3], data[i * 3 + 1], data[i * 3 + 2]);
            return result;
        }

        private static Vector2[] ToVector2Array(float[] data)
        {
            if (data == null || data.Length < 2) return Array.Empty<Vector2>();
            var count = data.Length / 2;
            var result = new Vector2[count];
            for (var i = 0; i < count; i++) result[i] = new Vector2(data[i * 2], data[i * 2 + 1]);
            return result;
        }

        private static Vector4[] ReadVector4Buffer(MeshBinaryBuffer buffer)
        {
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path) || buffer.components < 4)
                return null;
            if (!string.IsNullOrWhiteSpace(buffer.format) && !string.Equals(buffer.format, "float32", StringComparison.OrdinalIgnoreCase))
                return null;
            var floats = ReadFloatBuffer(buffer);
            if (floats == null)
                return null;
            var count = buffer.count > 0 ? Math.Min(buffer.count, floats.Length / buffer.components) : floats.Length / buffer.components;
            var result = new Vector4[count];
            for (var i = 0; i < count; i++)
                result[i] = new Vector4(floats[i * buffer.components], floats[i * buffer.components + 1], floats[i * buffer.components + 2], floats[i * buffer.components + 3]);
            return result;
        }

        private static Vector3[] ReadVector3Buffer(MeshBinaryBuffer buffer)
        {
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path) || buffer.components < 3)
                return null;
            if (!string.IsNullOrWhiteSpace(buffer.format) && !string.Equals(buffer.format, "float32", StringComparison.OrdinalIgnoreCase))
                return null;
            var floats = ReadFloatBuffer(buffer);
            if (floats == null)
                return null;
            var count = buffer.count > 0 ? Math.Min(buffer.count, floats.Length / buffer.components) : floats.Length / buffer.components;
            var result = new Vector3[count];
            for (var i = 0; i < count; i++)
                result[i] = new Vector3(floats[i * buffer.components], floats[i * buffer.components + 1], floats[i * buffer.components + 2]);
            return result;
        }

        private static Vector2[] ReadVector2Buffer(MeshBinaryBuffer buffer)
        {
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || !File.Exists(buffer.path) || buffer.components < 2)
                return null;
            if (!string.IsNullOrWhiteSpace(buffer.format) && !string.Equals(buffer.format, "float32", StringComparison.OrdinalIgnoreCase))
                return null;
            var floats = ReadFloatBuffer(buffer);
            if (floats == null)
                return null;
            var count = buffer.count > 0 ? Math.Min(buffer.count, floats.Length / buffer.components) : floats.Length / buffer.components;
            var result = new Vector2[count];
            for (var i = 0; i < count; i++)
                result[i] = new Vector2(floats[i * buffer.components], floats[i * buffer.components + 1]);
            return result;
        }

        private static Color32[] ReadColor32Buffer(MeshBinaryBuffer buffer, int expectedCount, out int count)
        {
            count = 0;
            if (buffer == null || string.IsNullOrWhiteSpace(buffer.path) || expectedCount <= 0 || !File.Exists(buffer.path)) return null;

            if (!string.Equals(buffer.format, "float32", StringComparison.OrdinalIgnoreCase) || buffer.components != 4)
                return null;

            var values = ReadVector4Buffer(buffer);
            if (values == null || values.Length != expectedCount) return null;

            var colors = new Color32[expectedCount];
            for (var i = 0; i < expectedCount; i++)
            {
                var v = values[i];
                colors[i] = new Color32(
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(v.x) * 255f),
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(v.y) * 255f),
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(v.z) * 255f),
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(v.w) * 255f));
            }
            count = expectedCount;
            return colors;
        }

        private static Color32[] ReadColor32Payload(float[] data, MeshBinaryBuffer buffer, int expectedCount)
        {
            var colors = ReadColor32Buffer(buffer, expectedCount, out _);
            if (colors != null)
                return colors;

            if (data == null || expectedCount <= 0 || data.Length != expectedCount * 4)
                return null;

            colors = new Color32[expectedCount];
            for (var i = 0; i < expectedCount; i++)
            {
                var baseIndex = i * 4;
                colors[i] = new Color32(
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(data[baseIndex]) * 255f),
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(data[baseIndex + 1]) * 255f),
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(data[baseIndex + 2]) * 255f),
                    (byte)Mathf.RoundToInt(Mathf.Clamp01(data[baseIndex + 3]) * 255f));
            }
            return colors;
        }

        private static void ApplyUvChannels(Mesh mesh, MeshUvChannelPayload[] channels, int vertexCount)
        {
            if (mesh == null) return;
            for (var channel = 0; channel < 8; channel++) mesh.SetUVs(channel, (List<Vector2>)null);
            if (channels == null || vertexCount <= 0) return;
            foreach (var channel in channels)
            {
                if (channel == null || channel.index < 0 || channel.index > 7 || channel.values == null && channel.buffer == null) continue;
                var values = channel.buffer != null ? ReadVector2Buffer(channel.buffer) : ToVector2Array(channel.values);
                if (values == null || values.Length != vertexCount) continue;
                mesh.SetUVs(channel.index, new List<Vector2>(values));
            }
        }
#endif
#if UNITY_EDITOR
        private static string CaptureAssetGuid(string assetPath)
        {
            if (string.IsNullOrWhiteSpace(assetPath))
                return null;
            return AssetDatabase.AssetPathToGUID(assetPath);
        }
#endif

        private static string BuildMeshAssetPath(AssetBridgeResourceEntry entry)
        {
            var baseName = BuildFriendlyBaseName(entry, "mesh", trimFileLikeExtension: false);
            return AssetPathUtility.BuildStableAssetPath("Assets/TriSync/Resources/Meshes", baseName, entry?.assetId, ".asset");
        }

        private static string ChooseMeshAssetPath(AssetBridgeResourceEntry entry, string existingUnityAssetPath)
        {
            var existing = NormalizeExistingAssetPath(existingUnityAssetPath);
            return string.IsNullOrWhiteSpace(existing) ? BuildMeshAssetPath(entry) : existing;
        }

        private static string NormalizeExistingAssetPath(string assetPath)
        {
            if (string.IsNullOrWhiteSpace(assetPath))
                return null;

            var normalized = assetPath.Trim().Replace('\\', '/');
            if (!normalized.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase) || normalized.Contains("/../") || normalized.EndsWith("/..", StringComparison.Ordinal))
                return null;
            if (!string.Equals(Path.GetExtension(normalized), ".asset", StringComparison.OrdinalIgnoreCase))
                return null;
            return normalized;
        }

        private static string BuildFriendlyBaseName(AssetBridgeResourceEntry entry, string fallbackPrefix, bool trimFileLikeExtension)
        {
            var sourceUri = entry?.source?.sourceUri;
            var preferred = TryExtractSourceName(sourceUri, trimFileLikeExtension);
            if (string.IsNullOrWhiteSpace(preferred))
                preferred = string.IsNullOrWhiteSpace(entry?.assetId) ? fallbackPrefix : entry.assetId;

            return AssetPathUtility.SanitizeFileName(preferred, fallbackPrefix);
        }

        private static string TryExtractSourceName(string sourceUri, bool trimFileLikeExtension)
        {
            if (string.IsNullOrWhiteSpace(sourceUri))
                return null;

            var normalized = sourceUri.Replace('\\', '/');
            var tail = normalized;
            var slash = normalized.LastIndexOf('/');
            if (slash >= 0 && slash < normalized.Length - 1)
                tail = normalized.Substring(slash + 1);

            if (string.IsNullOrWhiteSpace(tail))
                return null;

            if (trimFileLikeExtension)
            {
                var lower = tail.ToLowerInvariant();
                var exts = new[] { ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".exr", ".hdr", ".psd", ".webp", ".dds", ".ktx" };
                foreach (var ext in exts)
                {
                    if (lower.EndsWith(ext, StringComparison.Ordinal))
                    {
                        tail = tail.Substring(0, tail.Length - ext.Length);
                        break;
                    }
                }
            }

            return string.IsNullOrWhiteSpace(tail) ? null : tail;
        }

        private struct MeshPayloadApplyProfile
        {
            public double clearMs;
            public double readVerticesMs;
            public double readNormalsMs;
            public double readUv0Ms;
            public double readIndicesMs;
            public double readColorMs;
            public double setFormatMs;
            public double setVerticesMs;
            public double setNormalsMs;
            public double setUv0Ms;
            public double setColorsMs;
            public double uvChannelsMs;
            public double subMeshesMs;
            public double fallbackIndicesMs;
            public double skinMs;
            public double blendShapeMs;
            public double boundsMs;
            public double recalcNormalsMs;
            public double recalcTangentsMs;
            public double totalMs;
            public int vertexCount;
            public int indexCount;
            public int subMeshCount;
        }

    }

    public sealed class ImportResult
    {
        public bool success;
        public string unityAssetPath;
        public string assetGuid;
        public long localFileId;
        public string error;
        public string operation;
    }
}
