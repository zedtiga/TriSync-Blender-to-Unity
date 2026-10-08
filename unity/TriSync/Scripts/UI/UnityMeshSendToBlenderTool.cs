#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.SessionCore;
using UnityEditor;
using UnityEngine;
#if UNITY_6000_4_OR_NEWER
using MeshObjectId = UnityEngine.EntityId;
#else
using MeshObjectId = System.Int32;
#endif

namespace BlenderSyncVNext.UI
{
    public static class UnityMeshSendToBlenderTool
    {
        public const int DirectBinaryEstimateThresholdBytes = 128 * 1024;
        private const int StagingCleanupAgeHours = 24;
        private const int BinaryWriteChunkBytes = 64 * 1024;
        private const double ImportResultTimeoutSeconds = 30.0;
        private static readonly object ImportResultLock = new object();
        private static CreateInBlenderResult _latestResult = CreateInBlenderResult.Idle();
        private static bool _awaitingNegotiatedResult;
        private static double _importResultDeadlineSeconds;

        static UnityMeshSendToBlenderTool()
        {
            SessionClient.UnityMeshImportResultReceived -= HandleImportResult;
            SessionClient.UnityMeshImportResultReceived += HandleImportResult;
            EditorApplication.update -= ReconcileConnectionState;
            EditorApplication.update += ReconcileConnectionState;
        }

        public static bool SendMeshSources(IReadOnlyList<MeshSource> sources)
        {
            if (IsCreatePending)
                return false;
            if (!CanSendToBlender)
            {
                SetLocalFailure("Connect to Blender before creating a mesh.", "blender_not_connected");
                return false;
            }

            SetLocalStatus("preparing", "Preparing queued mesh data...");
            var warnings = new List<string>();
            var meshes = BuildMeshSnapshots(sources, warnings);
            if (meshes.Count == 0)
            {
                var message = warnings.Count > 0
                    ? "No compatible mesh could be exported."
                    : "Add at least one Mesh before creating it in Blender.";
                SetLocalWarning(message, "no_exportable_mesh", warnings);
                return false;
            }

            var binaryEstimate = EstimateBinaryBytes(meshes);
            var expectsImportResult = SessionClient.SharedTransport.IsFeatureNegotiated(
                SessionClient.UnityMeshImportResultFeature);
            SetLocalStatus("creating", "Waiting for Blender to create the mesh...", warnings);
            BeginImportResultWait(expectsImportResult);
            if (binaryEstimate > DirectBinaryEstimateThresholdBytes)
            {
                if (!TrySendBinaryStagedPayload(meshes, warnings, binaryEstimate, out var error))
                {
                    BlenderSyncLog.Error(
                        "UnityMeshImport",
                        "binary_stage_failed",
                        error,
                        new Dictionary<string, object>
                        {
                            { "meshCount", meshes.Count },
                            { "estimatedBytes", binaryEstimate },
                        });
                    SetLocalFailure("The mesh snapshot could not be sent to Blender.", error, warnings);
                    return false;
                }
                CompleteSendWithoutResult(expectsImportResult, warnings);
                return true;
            }

            var payload = new UnityMeshImportEnvelope
            {
                type = "unity_mesh.import_v1",
                timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                source = "unity_editor_mesh_queue",
                meshes = meshes.ToArray(),
                warnings = warnings.ToArray(),
            };
            var json = JsonUtility.ToJson(payload);
            var bytes = Encoding.UTF8.GetByteCount(json);

            if (!SessionClient.TrySendToBlender(json, error =>
                    SetLocalFailure("The mesh snapshot could not be sent to Blender.", error, warnings)))
                return false;
            BlenderSyncLog.Trace(
                "UnityMeshImport",
                "sent_direct_json",
                () => "Sent a direct JSON mesh snapshot to Blender.",
                () => new Dictionary<string, object>
                {
                    { "meshCount", meshes.Count },
                    { "jsonBytes", bytes },
                    { "binaryEstimate", binaryEstimate },
                    { "warningCount", warnings.Count },
                });
            CompleteSendWithoutResult(expectsImportResult, warnings);
            return true;
        }

        public static bool CanSendToBlender =>
            SessionClient.SharedTransport.IsRunning &&
            string.Equals(SessionClient.LastLifecycleState, "handshake_confirmed", StringComparison.Ordinal);

        public static bool IsCreatePending
        {
            get
            {
                lock (ImportResultLock)
                    return IsPendingStatus(_latestResult.status);
            }
        }

        public static CreateInBlenderResult GetLatestResult()
        {
            lock (ImportResultLock)
                return _latestResult.Clone();
        }

        public static void ReconcileConnectionState()
        {
            if (!CanSendToBlender)
            {
                if (TryFinishPendingResult(
                        "failed",
                        "The Blender connection closed before creation completed.",
                        "connection_closed"))
                    BlenderSyncLog.Warn(
                        "UnityMeshImport",
                        "result_wait_ended",
                        "The Blender connection closed before mesh creation completed.",
                        new Dictionary<string, object> { { "reason", "connection_closed" } });
                return;
            }

            if (TryExpirePendingResult(MonotonicSeconds()))
            {
                BlenderSyncLog.Warn(
                    "UnityMeshImport",
                    "result_wait_timed_out",
                    "Blender may still finish creating the mesh and report a late result.",
                    new Dictionary<string, object>
                    {
                        { "timeoutSeconds", ImportResultTimeoutSeconds },
                    });
            }
        }

        public static void HandleImportResult(string rawJson)
        {
            UnityMeshImportResultEnvelope incoming;
            try
            {
                incoming = JsonUtility.FromJson<UnityMeshImportResultEnvelope>(rawJson);
            }
            catch (Exception ex)
            {
                BlenderSyncLog.Warn(
                    "UnityMeshImport",
                    "result_rejected_invalid_json",
                    ex.Message);
                return;
            }

            var status = (incoming?.status ?? string.Empty).Trim().ToLowerInvariant();
            if (incoming == null || !IsAcceptedResultStatus(status))
            {
                BlenderSyncLog.Warn(
                    "UnityMeshImport",
                    "result_rejected_invalid_status",
                    "Ignored a mesh creation result with an unsupported status.");
                return;
            }

            var rejectedQueued = false;
            lock (ImportResultLock)
            {
                if (string.Equals(status, "queued", StringComparison.Ordinal))
                {
                    if (!_awaitingNegotiatedResult || !IsPendingStatus(_latestResult.status))
                    {
                        rejectedQueued = true;
                    }
                    else
                    {
                        _importResultDeadlineSeconds = MonotonicSeconds() + ImportResultTimeoutSeconds;
                    }
                }
                else
                {
                    _awaitingNegotiatedResult = false;
                    _importResultDeadlineSeconds = 0.0;
                }
                if (!rejectedQueued)
                {
                    _latestResult = new CreateInBlenderResult
                    {
                        status = status,
                        message = incoming.message,
                        error = incoming.error,
                        created = Math.Max(0, incoming.created),
                        skipped = Math.Max(0, incoming.skipped),
                        warnings = incoming.warnings ?? Array.Empty<string>(),
                        updatedAt = incoming.timestamp > 0
                            ? incoming.timestamp
                            : DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                    };
                }
            }

            if (rejectedQueued)
            {
                BlenderSyncLog.Warn(
                    "UnityMeshImport",
                    "result_rejected_unexpected_queued",
                    "Ignored a late queued result because no creation request is awaiting it.");
                return;
            }

            var fields = new Dictionary<string, object>
            {
                { "status", status },
                { "created", incoming.created },
                { "skipped", incoming.skipped },
                { "warningCount", incoming.warnings?.Length ?? 0 },
            };
            if (status == "failed")
                BlenderSyncLog.Error(
                    "UnityMeshImport",
                    "result_failed",
                    incoming.error ?? incoming.message,
                    fields);
            else if (status == "partial")
                BlenderSyncLog.Warn(
                    "UnityMeshImport",
                    "result_partial",
                    incoming.message,
                    fields);
            else if (status == "queued")
                BlenderSyncLog.Trace(
                    "UnityMeshImport",
                    "result_queued",
                    () => incoming.message,
                    () => fields);
            else
                BlenderSyncLog.Info(
                    "UnityMeshImport",
                    "result_imported",
                    incoming.message,
                    fields);
        }

        public static bool TryCreateMeshSource(
            UnityEngine.Object value,
            out MeshSource source,
            out string warning)
        {
            source = null;
            warning = null;
            if (value == null)
            {
                warning = "Select a Mesh or a GameObject with a MeshFilter or SkinnedMeshRenderer.";
                return false;
            }

            if (value is Mesh mesh)
            {
                source = new MeshSource(mesh, null, "mesh_asset");
                return true;
            }

            var owner = value as GameObject;
            if (owner == null && value is Component component)
                owner = component.gameObject;
            if (owner == null)
            {
                warning = "Only Mesh assets and mesh-bearing GameObjects can be added.";
                return false;
            }

            var sharedMesh = ResolveSharedMesh(owner, out var sourceKind);
            if (sharedMesh == null)
            {
                warning = $"GameObject '{owner.name}' has no MeshFilter or SkinnedMeshRenderer shared Mesh.";
                return false;
            }

            source = new MeshSource(sharedMesh, owner, sourceKind);
            return true;
        }

        private static List<UnityMeshSnapshot> BuildMeshSnapshots(IReadOnlyList<MeshSource> sources, List<string> warnings)
        {
            var result = new List<UnityMeshSnapshot>();
            var seenAssets = new HashSet<MeshObjectId>();
            if (sources == null)
                return result;

            foreach (var source in sources)
            {
                var mesh = source?.Mesh;
                if (mesh == null)
                {
                    warnings.Add("SKIP mesh=(missing) reason=queued_mesh_missing");
                    continue;
                }
                if (!seenAssets.Add(GetMeshObjectId(mesh)))
                    continue;

                var owner = source.Owner;
                var objectName = owner != null ? owner.name : mesh.name;
                TryAddMesh(result, warnings, mesh, objectName, source.SourceKind, owner);
            }
            return result;
        }

        private static MeshObjectId GetMeshObjectId(Mesh mesh)
        {
#if UNITY_6000_4_OR_NEWER
            return mesh.GetEntityId();
#else
            return mesh.GetInstanceID();
#endif
        }

        private static Mesh ResolveSharedMesh(GameObject owner, out string sourceKind)
        {
            sourceKind = null;
            if (owner == null)
                return null;

            var filter = owner.GetComponent<MeshFilter>();
            if (filter != null && filter.sharedMesh != null)
            {
                sourceKind = "mesh_filter";
                return filter.sharedMesh;
            }

            var skinned = owner.GetComponent<SkinnedMeshRenderer>();
            if (skinned != null && skinned.sharedMesh != null)
            {
                sourceKind = "skinned_mesh_renderer";
                return skinned.sharedMesh;
            }

            return null;
        }

        private static void TryAddMesh(List<UnityMeshSnapshot> result, List<string> warnings, Mesh mesh, string objectName, string sourceKind, GameObject owner)
        {
            try
            {
                var snapshot = BuildSnapshot(mesh, objectName, sourceKind, owner, warnings);
                if (snapshot != null)
                    result.Add(snapshot);
            }
            catch (Exception ex)
            {
                warnings.Add($"SKIP mesh={SafeName(mesh != null ? mesh.name : null, "Mesh")} reason={ex.Message}");
            }
        }

        private static UnityMeshSnapshot BuildSnapshot(Mesh mesh, string objectName, string sourceKind, GameObject owner, List<string> warnings)
        {
            if (mesh == null)
                return null;
            if (!mesh.isReadable)
                throw new InvalidOperationException("mesh_not_readable");

            var vertices = mesh.vertices;
            if (vertices == null || vertices.Length == 0)
                throw new InvalidOperationException("empty_vertices");

            var snapshot = new UnityMeshSnapshot
            {
                meshName = SafeName(mesh.name, "UnityMesh"),
                objectName = SafeName(objectName, mesh.name),
                sourceKind = sourceKind,
                sourceObjectPath = owner != null ? BuildHierarchyPath(owner.transform) : null,
                assetPath = AssetDatabase.GetAssetPath(mesh),
                vertexCount = vertices.Length,
                vertices = FlattenVector3(vertices, mapToBlender: true, normalize: false),
                normals = BuildNormals(mesh, vertices.Length),
                colors = BuildColors(mesh, vertices.Length),
                uvChannels = BuildUvChannels(mesh, vertices.Length),
                blendShapes = BuildBlendShapes(mesh, vertices.Length, warnings),
            };

            if (AssetDatabase.TryGetGUIDAndLocalFileIdentifier(mesh, out string guid, out long localId))
            {
                snapshot.assetGuid = guid;
                snapshot.assetLocalId = localId.ToString();
            }

            var renderer = owner != null ? owner.GetComponent<Renderer>() : null;
            snapshot.materialNames = BuildMaterialNames(renderer);
            snapshot.subMeshes = BuildSubMeshes(mesh, snapshot.materialNames, warnings);
            snapshot.indexCount = CountIndices(snapshot.subMeshes);
            if (snapshot.subMeshes.Length == 0 || snapshot.indexCount == 0)
                throw new InvalidOperationException("no_triangle_submeshes");
            if (string.Equals(sourceKind, "skinned_mesh_renderer", StringComparison.Ordinal))
            {
                warnings?.Add(
                    $"SKINNED_MESH_SHARED_ONLY mesh={snapshot.objectName} " +
                    "omitted=bones,skin_weights,current_blend_shape_weights,current_deformation");
            }
            return snapshot;
        }

        private static UnityMeshSubMesh[] BuildSubMeshes(Mesh mesh, string[] materialNames, List<string> warnings)
        {
            var count = Math.Max(1, mesh.subMeshCount);
            var subMeshes = new List<UnityMeshSubMesh>(count);
            for (var i = 0; i < count; i++)
            {
                if (i < mesh.subMeshCount && mesh.GetTopology(i) != MeshTopology.Triangles)
                {
                    warnings.Add($"SKIP_SUBMESH mesh={mesh.name} slot={i} topology={mesh.GetTopology(i)} reason=only_triangles_supported");
                    continue;
                }

                var indices = mesh.subMeshCount > 0 ? mesh.GetTriangles(i) : mesh.triangles;
                if (indices == null || indices.Length < 3)
                    continue;

                subMeshes.Add(new UnityMeshSubMesh
                {
                    materialSlot = i,
                    materialName = materialNames != null && i < materialNames.Length ? materialNames[i] : null,
                    topology = "triangles",
                    indices = MapTrianglesToBlender(indices),
                });
            }
            return subMeshes.ToArray();
        }

        private static int CountIndices(UnityMeshSubMesh[] subMeshes)
        {
            var total = 0;
            if (subMeshes == null)
                return total;
            foreach (var subMesh in subMeshes)
                total += subMesh?.indices?.Length ?? 0;
            return total;
        }

        private static string[] BuildMaterialNames(Renderer renderer)
        {
            if (renderer == null)
                return Array.Empty<string>();
            var materials = renderer.sharedMaterials ?? Array.Empty<Material>();
            var names = new string[materials.Length];
            for (var i = 0; i < materials.Length; i++)
                names[i] = materials[i] != null ? materials[i].name : string.Empty;
            return names;
        }

        private static UnityMeshUvChannel[] BuildUvChannels(Mesh mesh, int vertexCount)
        {
            var channels = new List<UnityMeshUvChannel>();
            for (var channel = 0; channel < 8; channel++)
            {
                var values = new List<Vector2>(vertexCount);
                mesh.GetUVs(channel, values);
                if (values.Count != vertexCount)
                    continue;
                channels.Add(new UnityMeshUvChannel
                {
                    index = channel,
                    name = "UV" + channel,
                    values = FlattenVector2(values),
                });
            }
            return channels.ToArray();
        }

        private static UnityMeshBlendShape[] BuildBlendShapes(Mesh mesh, int vertexCount, List<string> warnings)
        {
            if (mesh == null || mesh.blendShapeCount <= 0 || vertexCount <= 0)
                return Array.Empty<UnityMeshBlendShape>();

            var result = new List<UnityMeshBlendShape>(mesh.blendShapeCount);
            for (var shapeIndex = 0; shapeIndex < mesh.blendShapeCount; shapeIndex++)
            {
                var shapeName = SafeName(mesh.GetBlendShapeName(shapeIndex), "Shape " + (shapeIndex + 1));
                var frameCount = mesh.GetBlendShapeFrameCount(shapeIndex);
                if (frameCount != 1)
                {
                    warnings?.Add(
                        $"OMIT_BLEND_SHAPE mesh={SafeName(mesh.name, "Mesh")} shape={shapeName} " +
                        $"reason=multiple_frames_not_supported frames={frameCount}");
                    continue;
                }

                var deltaVertices = new Vector3[vertexCount];
                var deltaNormals = new Vector3[vertexCount];
                var deltaTangents = new Vector3[vertexCount];
                mesh.GetBlendShapeFrameVertices(
                    shapeIndex,
                    0,
                    deltaVertices,
                    deltaNormals,
                    deltaTangents);
                result.Add(new UnityMeshBlendShape
                {
                    name = shapeName,
                    frameWeight = mesh.GetBlendShapeFrameWeight(shapeIndex, 0),
                    deltaPositions = FlattenVector3(deltaVertices, mapToBlender: true, normalize: false),
                });
            }
            return result.ToArray();
        }

        private static float[] BuildNormals(Mesh mesh, int vertexCount)
        {
            var normals = mesh.normals;
            return normals != null && normals.Length == vertexCount
                ? FlattenVector3(normals, mapToBlender: true, normalize: true)
                : Array.Empty<float>();
        }

        private static float[] BuildColors(Mesh mesh, int vertexCount)
        {
            var colors = mesh.colors;
            if (colors == null || colors.Length != vertexCount)
                return Array.Empty<float>();
            var flat = new float[colors.Length * 4];
            for (var i = 0; i < colors.Length; i++)
            {
                var offset = i * 4;
                flat[offset] = colors[i].r;
                flat[offset + 1] = colors[i].g;
                flat[offset + 2] = colors[i].b;
                flat[offset + 3] = colors[i].a;
            }
            return flat;
        }

        private static float[] FlattenVector3(IReadOnlyList<Vector3> values, bool mapToBlender, bool normalize)
        {
            var flat = new float[values.Count * 3];
            for (var i = 0; i < values.Count; i++)
            {
                var value = mapToBlender ? MapVectorToBlender(values[i]) : values[i];
                if (normalize && value.sqrMagnitude > 1e-12f)
                    value.Normalize();
                var offset = i * 3;
                flat[offset] = value.x;
                flat[offset + 1] = value.y;
                flat[offset + 2] = value.z;
            }
            return flat;
        }

        private static float[] FlattenVector2(IReadOnlyList<Vector2> values)
        {
            var flat = new float[values.Count * 2];
            for (var i = 0; i < values.Count; i++)
            {
                var offset = i * 2;
                flat[offset] = values[i].x;
                flat[offset + 1] = values[i].y;
            }
            return flat;
        }

        private static Vector3 MapVectorToBlender(Vector3 value)
        {
            return new Vector3(value.x, value.z, value.y);
        }

        private static int[] MapTrianglesToBlender(int[] triangles)
        {
            var mapped = (int[])triangles.Clone();
            for (var i = 0; i + 2 < mapped.Length; i += 3)
            {
                var tmp = mapped[i + 1];
                mapped[i + 1] = mapped[i + 2];
                mapped[i + 2] = tmp;
            }
            return mapped;
        }

        private static string BuildHierarchyPath(Transform transform)
        {
            if (transform == null)
                return string.Empty;
            var parts = new List<string>();
            var current = transform;
            while (current != null)
            {
                parts.Add(current.name);
                current = current.parent;
            }
            parts.Reverse();
            return string.Join("/", parts);
        }

        private static string SafeName(string value, string fallback)
        {
            return string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
        }

        private static bool TrySendBinaryStagedPayload(
            List<UnityMeshSnapshot> meshes,
            List<string> warnings,
            long estimatedBytes,
            out string error)
        {
            error = null;
            string manifestPath = null;
            string binaryPath = null;
            try
            {
                var stagingDir = GetStagingDirectory();
                Directory.CreateDirectory(stagingDir);
                CleanupStaleStagingFiles(stagingDir);

                var stem = "import-" + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + "-" + Guid.NewGuid().ToString("N");
                manifestPath = Path.Combine(stagingDir, stem + ".manifest.json");
                binaryPath = Path.Combine(stagingDir, stem + ".bin");

                UnityMeshBinarySnapshot[] binaryMeshes;
                using (var stream = new FileStream(binaryPath, FileMode.CreateNew, FileAccess.Write, FileShare.Read))
                {
                    binaryMeshes = WriteBinaryMeshes(stream, meshes);
                }

                var manifest = new UnityMeshBinaryManifest
                {
                    schema = "unity_mesh_binary_v1",
                    meshes = binaryMeshes,
                };
                var manifestJson = JsonUtility.ToJson(manifest);
                File.WriteAllText(manifestPath, manifestJson, new UTF8Encoding(false));

                var binaryBytes = new FileInfo(binaryPath).Length;
                var manifestBytes = Encoding.UTF8.GetByteCount(manifestJson);
                string checksum;
                using (var checksumStream = new FileStream(binaryPath, FileMode.Open, FileAccess.Read, FileShare.Read))
                    checksum = Crc32Ieee.ComputeStreamHex(checksumStream);

                var envelope = new UnityMeshImportBinaryFileEnvelope
                {
                    type = "unity_mesh.import_binary_file_v1",
                    timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                    source = "unity_editor_mesh_queue",
                    manifestPath = manifestPath,
                    binaryPath = binaryPath,
                    manifestBytes = manifestBytes,
                    binaryBytes = binaryBytes,
                    estimatedBinaryBytes = estimatedBytes,
                    meshCount = meshes.Count,
                    deleteAfterImport = true,
                    checksumAlgorithm = "crc32-ieee",
                    checksum = checksum,
                    warnings = warnings?.ToArray() ?? Array.Empty<string>(),
                };
                var message = JsonUtility.ToJson(envelope);
                if (!SessionClient.TrySendToBlender(message, sendError =>
                    {
                        DeleteStagingFiles(manifestPath, binaryPath);
                        SetLocalFailure("The staged mesh snapshot could not be sent to Blender.", sendError, warnings);
                    }))
                {
                    error = "send_not_accepted";
                    DeleteStagingFiles(manifestPath, binaryPath);
                    return false;
                }
                BlenderSyncLog.Trace(
                    "UnityMeshImport",
                    "sent_staged_binary",
                    () => "Sent a staged binary mesh snapshot to Blender.",
                    () => new Dictionary<string, object>
                    {
                        { "meshCount", meshes.Count },
                        { "binaryBytes", binaryBytes },
                        { "manifestBytes", manifestBytes },
                        { "estimatedBytes", estimatedBytes },
                        { "warningCount", warnings?.Count ?? 0 },
                    });
                return true;
            }
            catch (Exception ex)
            {
                error = ex.Message;
                DeleteStagingFiles(manifestPath, binaryPath);
                return false;
            }
        }

        private static void DeleteStagingFiles(params string[] paths)
        {
            foreach (var path in paths ?? Array.Empty<string>())
            {
                if (string.IsNullOrWhiteSpace(path))
                    continue;
                try
                {
                    if (File.Exists(path))
                        File.Delete(path);
                }
                catch
                {
                    // The normal stale-file cleanup remains as a fallback.
                }
            }
        }

        private static string GetStagingDirectory()
        {
            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName;
            if (string.IsNullOrWhiteSpace(projectRoot))
                projectRoot = Application.dataPath;
            return Path.Combine(projectRoot, "Temp", "BlenderSyncVNext", "UnityMeshImportsV1");
        }

        private static void CleanupStaleStagingFiles(string stagingDir)
        {
            try
            {
                if (!Directory.Exists(stagingDir))
                    return;
                var cutoff = DateTime.UtcNow.AddHours(-StagingCleanupAgeHours);
                foreach (var path in Directory.GetFiles(stagingDir, "import-*.*"))
                {
                    try
                    {
                        if (File.GetLastWriteTimeUtc(path) < cutoff)
                            File.Delete(path);
                    }
                    catch
                    {
                        // Best-effort cleanup; active imports may still be reading a file.
                    }
                }
            }
            catch
            {
                // Staging cleanup must never block the import command.
            }
        }

        private static long EstimateBinaryBytes(List<UnityMeshSnapshot> meshes)
        {
            long total = 0;
            foreach (var mesh in meshes)
            {
                total += ByteLength(mesh.vertices, sizeof(float));
                total += ByteLength(mesh.normals, sizeof(float));
                total += ByteLength(mesh.colors, sizeof(float));
                if (mesh.uvChannels != null)
                {
                    foreach (var uv in mesh.uvChannels)
                        total += ByteLength(uv?.values, sizeof(float));
                }
                if (mesh.subMeshes != null)
                {
                    foreach (var subMesh in mesh.subMeshes)
                        total += ByteLength(subMesh?.indices, sizeof(int));
                }
                if (mesh.blendShapes != null)
                {
                    foreach (var shape in mesh.blendShapes)
                        total += ByteLength(shape?.deltaPositions, sizeof(float));
                }
            }
            return total;
        }

        private static long ByteLength(float[] values, int elementSize)
        {
            return values == null ? 0L : (long)values.Length * elementSize;
        }

        private static long ByteLength(int[] values, int elementSize)
        {
            return values == null ? 0L : (long)values.Length * elementSize;
        }

        private static UnityMeshBinarySnapshot[] WriteBinaryMeshes(FileStream stream, List<UnityMeshSnapshot> meshes)
        {
            var result = new UnityMeshBinarySnapshot[meshes.Count];
            for (var i = 0; i < meshes.Count; i++)
            {
                var mesh = meshes[i];
                var binary = new UnityMeshBinarySnapshot
                {
                    meshName = mesh.meshName,
                    objectName = mesh.objectName,
                    sourceKind = mesh.sourceKind,
                    sourceObjectPath = mesh.sourceObjectPath,
                    assetPath = mesh.assetPath,
                    assetGuid = mesh.assetGuid,
                    assetLocalId = mesh.assetLocalId,
                    vertexCount = mesh.vertexCount,
                    indexCount = mesh.indexCount,
                    materialNames = mesh.materialNames,
                    vertices = WriteFloatBuffer(stream, "POSITION", mesh.vertices, 3),
                    normals = WriteFloatBuffer(stream, "NORMAL", mesh.normals, 3, includeEmptyDescriptor: true),
                    colors = WriteFloatBuffer(stream, "COLOR", mesh.colors, 4, includeEmptyDescriptor: true),
                    uvChannels = WriteBinaryUvChannels(stream, mesh.uvChannels),
                    subMeshes = WriteBinarySubMeshes(stream, mesh.subMeshes),
                    blendShapes = WriteBinaryBlendShapes(stream, mesh.blendShapes),
                };
                result[i] = binary;
            }
            return result;
        }

        private static UnityMeshBinaryBlendShape[] WriteBinaryBlendShapes(FileStream stream, UnityMeshBlendShape[] blendShapes)
        {
            if (blendShapes == null || blendShapes.Length == 0)
                return Array.Empty<UnityMeshBinaryBlendShape>();
            var result = new UnityMeshBinaryBlendShape[blendShapes.Length];
            for (var i = 0; i < blendShapes.Length; i++)
            {
                var shape = blendShapes[i];
                result[i] = new UnityMeshBinaryBlendShape
                {
                    name = shape.name,
                    frameWeight = shape.frameWeight,
                    deltaPositions = WriteFloatBuffer(stream, "BLEND_SHAPE_POSITION", shape.deltaPositions, 3),
                };
            }
            return result;
        }

        private static UnityMeshBinaryUvChannel[] WriteBinaryUvChannels(FileStream stream, UnityMeshUvChannel[] channels)
        {
            if (channels == null || channels.Length == 0)
                return Array.Empty<UnityMeshBinaryUvChannel>();
            var result = new UnityMeshBinaryUvChannel[channels.Length];
            for (var i = 0; i < channels.Length; i++)
            {
                var channel = channels[i];
                result[i] = new UnityMeshBinaryUvChannel
                {
                    index = channel.index,
                    name = channel.name,
                    buffer = WriteFloatBuffer(stream, "UV" + channel.index, channel.values, 2),
                };
            }
            return result;
        }

        private static UnityMeshBinarySubMesh[] WriteBinarySubMeshes(FileStream stream, UnityMeshSubMesh[] subMeshes)
        {
            if (subMeshes == null || subMeshes.Length == 0)
                return Array.Empty<UnityMeshBinarySubMesh>();
            var result = new UnityMeshBinarySubMesh[subMeshes.Length];
            for (var i = 0; i < subMeshes.Length; i++)
            {
                var subMesh = subMeshes[i];
                result[i] = new UnityMeshBinarySubMesh
                {
                    materialSlot = subMesh.materialSlot,
                    materialName = subMesh.materialName,
                    topology = subMesh.topology,
                    indices = WriteIntBuffer(stream, "INDEX", subMesh.indices, 1),
                };
            }
            return result;
        }

        private static UnityMeshBinaryBuffer WriteFloatBuffer(
            FileStream stream,
            string semantic,
            float[] values,
            int components,
            bool includeEmptyDescriptor = false)
        {
            if (values == null || values.Length == 0)
            {
                if (!includeEmptyDescriptor)
                    return null;
                return new UnityMeshBinaryBuffer
                {
                    semantic = semantic,
                    valueType = "float32",
                    components = components,
                    count = 0,
                    offset = stream.Position,
                    byteCount = 0,
                };
            }
            var offset = stream.Position;
            var byteCount = checked(values.Length * sizeof(float));
            WritePrimitiveArray(stream, values, byteCount);
            return new UnityMeshBinaryBuffer
            {
                semantic = semantic,
                valueType = "float32",
                components = components,
                count = values.Length / Math.Max(1, components),
                offset = offset,
                byteCount = byteCount,
            };
        }

        private static UnityMeshBinaryBuffer WriteIntBuffer(FileStream stream, string semantic, int[] values, int components)
        {
            if (values == null || values.Length == 0)
                return null;
            var offset = stream.Position;
            var byteCount = checked(values.Length * sizeof(int));
            WritePrimitiveArray(stream, values, byteCount);
            return new UnityMeshBinaryBuffer
            {
                semantic = semantic,
                valueType = "int32",
                components = components,
                count = values.Length / Math.Max(1, components),
                offset = offset,
                byteCount = byteCount,
            };
        }

        private static void WritePrimitiveArray(FileStream stream, Array values, int byteCount)
        {
            var buffer = new byte[Math.Min(BinaryWriteChunkBytes, byteCount)];
            var sourceOffset = 0;
            while (sourceOffset < byteCount)
            {
                var count = Math.Min(buffer.Length, byteCount - sourceOffset);
                Buffer.BlockCopy(values, sourceOffset, buffer, 0, count);
                stream.Write(buffer, 0, count);
                sourceOffset += count;
            }
        }

        private static void SetLocalStatus(string status, string message, IEnumerable<string> warnings = null)
        {
            lock (ImportResultLock)
            {
                _awaitingNegotiatedResult = false;
                _importResultDeadlineSeconds = 0.0;
                _latestResult = new CreateInBlenderResult
                {
                    status = status,
                    message = message,
                    error = null,
                    created = 0,
                    skipped = 0,
                    warnings = warnings != null ? new List<string>(warnings).ToArray() : Array.Empty<string>(),
                    updatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                };
            }
        }

        private static void SetLocalFailure(string message, string error, IEnumerable<string> warnings = null)
        {
            lock (ImportResultLock)
            {
                _awaitingNegotiatedResult = false;
                _importResultDeadlineSeconds = 0.0;
                _latestResult = new CreateInBlenderResult
                {
                    status = "failed",
                    message = message,
                    error = string.IsNullOrWhiteSpace(error) ? "create_failed" : error,
                    created = 0,
                    skipped = 0,
                    warnings = warnings != null ? new List<string>(warnings).ToArray() : Array.Empty<string>(),
                    updatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                };
            }
        }

        private static void SetLocalWarning(string message, string reason, IEnumerable<string> warnings = null)
        {
            lock (ImportResultLock)
            {
                _awaitingNegotiatedResult = false;
                _importResultDeadlineSeconds = 0.0;
                _latestResult = new CreateInBlenderResult
                {
                    status = "warning",
                    message = message,
                    error = string.IsNullOrWhiteSpace(reason) ? null : reason,
                    created = 0,
                    skipped = 0,
                    warnings = warnings != null ? new List<string>(warnings).ToArray() : Array.Empty<string>(),
                    updatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                };
            }
        }

        private static void BeginImportResultWait(bool expectsImportResult)
        {
            lock (ImportResultLock)
            {
                _awaitingNegotiatedResult = expectsImportResult;
                _importResultDeadlineSeconds = expectsImportResult
                    ? MonotonicSeconds() + ImportResultTimeoutSeconds
                    : 0.0;
            }
        }

        private static void CompleteSendWithoutResult(bool expectsImportResult, IEnumerable<string> warnings)
        {
            if (expectsImportResult)
                return;

            SetLocalStatus(
                "sent",
                "Sent to Blender. This Blender version does not report creation results.",
                warnings);
        }

        private static bool TryFinishPendingResult(string status, string message, string error)
        {
            lock (ImportResultLock)
            {
                if (!_awaitingNegotiatedResult || !IsPendingStatus(_latestResult.status))
                    return false;

                _awaitingNegotiatedResult = false;
                _importResultDeadlineSeconds = 0.0;
                _latestResult = new CreateInBlenderResult
                {
                    status = status,
                    message = message,
                    error = error,
                    created = 0,
                    skipped = 0,
                    warnings = Array.Empty<string>(),
                    updatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                };
                return true;
            }
        }

        private static bool TryExpirePendingResult(double nowSeconds)
        {
            lock (ImportResultLock)
            {
                if (!_awaitingNegotiatedResult || !IsPendingStatus(_latestResult.status))
                    return false;
                if (!IsDeadlineExpired(nowSeconds, _importResultDeadlineSeconds))
                    return false;

                _awaitingNegotiatedResult = false;
                _importResultDeadlineSeconds = 0.0;
                _latestResult = new CreateInBlenderResult
                {
                    status = "warning",
                    message = "Blender did not confirm mesh creation within 30 seconds. It may still finish.",
                    error = "result_timeout",
                    created = 0,
                    skipped = 0,
                    warnings = Array.Empty<string>(),
                    updatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                };
                return true;
            }
        }

        private static double MonotonicSeconds()
        {
            return (double)System.Diagnostics.Stopwatch.GetTimestamp() /
                   System.Diagnostics.Stopwatch.Frequency;
        }

        private static bool IsDeadlineExpired(double nowSeconds, double deadlineSeconds)
        {
            return deadlineSeconds > 0.0 && nowSeconds >= deadlineSeconds;
        }

        private static bool IsPendingStatus(string status)
        {
            return string.Equals(status, "preparing", StringComparison.Ordinal) ||
                   string.Equals(status, "creating", StringComparison.Ordinal) ||
                   string.Equals(status, "queued", StringComparison.Ordinal);
        }

        private static bool IsAcceptedResultStatus(string status)
        {
            return string.Equals(status, "queued", StringComparison.Ordinal) ||
                   string.Equals(status, "imported", StringComparison.Ordinal) ||
                   string.Equals(status, "partial", StringComparison.Ordinal) ||
                   string.Equals(status, "failed", StringComparison.Ordinal);
        }

        [Serializable]
        private sealed class UnityMeshImportEnvelope
        {
            public string type;
            public long timestamp;
            public string source;
            public UnityMeshSnapshot[] meshes;
            public string[] warnings;
        }

        [Serializable]
        private sealed class UnityMeshImportBinaryFileEnvelope
        {
            public string type;
            public long timestamp;
            public string source;
            public string manifestPath;
            public string binaryPath;
            public long manifestBytes;
            public long binaryBytes;
            public long estimatedBinaryBytes;
            public int meshCount;
            public bool deleteAfterImport;
            public string checksumAlgorithm;
            public string checksum;
            public string[] warnings;
        }

        [Serializable]
        private sealed class UnityMeshSnapshot
        {
            public string meshName;
            public string objectName;
            public string sourceKind;
            public string sourceObjectPath;
            public string assetPath;
            public string assetGuid;
            public string assetLocalId;
            public int vertexCount;
            public int indexCount;
            public float[] vertices;
            public float[] normals;
            public float[] colors;
            public string[] materialNames;
            public UnityMeshSubMesh[] subMeshes;
            public UnityMeshUvChannel[] uvChannels;
            public UnityMeshBlendShape[] blendShapes;
        }

        [Serializable]
        private sealed class UnityMeshSubMesh
        {
            public int materialSlot;
            public string materialName;
            public string topology;
            public int[] indices;
        }

        [Serializable]
        private sealed class UnityMeshUvChannel
        {
            public int index;
            public string name;
            public float[] values;
        }

        [Serializable]
        private sealed class UnityMeshBlendShape
        {
            public string name;
            public float frameWeight;
            public float[] deltaPositions;
        }

        [Serializable]
        private sealed class UnityMeshBinaryManifest
        {
            public string schema;
            public UnityMeshBinarySnapshot[] meshes;
        }

        [Serializable]
        private sealed class UnityMeshBinarySnapshot
        {
            public string meshName;
            public string objectName;
            public string sourceKind;
            public string sourceObjectPath;
            public string assetPath;
            public string assetGuid;
            public string assetLocalId;
            public int vertexCount;
            public int indexCount;
            public string[] materialNames;
            public UnityMeshBinaryBuffer vertices;
            public UnityMeshBinaryBuffer normals;
            public UnityMeshBinaryBuffer colors;
            public UnityMeshBinarySubMesh[] subMeshes;
            public UnityMeshBinaryUvChannel[] uvChannels;
            public UnityMeshBinaryBlendShape[] blendShapes;
        }

        [Serializable]
        private sealed class UnityMeshBinarySubMesh
        {
            public int materialSlot;
            public string materialName;
            public string topology;
            public UnityMeshBinaryBuffer indices;
        }

        [Serializable]
        private sealed class UnityMeshBinaryUvChannel
        {
            public int index;
            public string name;
            public UnityMeshBinaryBuffer buffer;
        }

        [Serializable]
        private sealed class UnityMeshBinaryBlendShape
        {
            public string name;
            public float frameWeight;
            public UnityMeshBinaryBuffer deltaPositions;
        }

        [Serializable]
        private sealed class UnityMeshBinaryBuffer
        {
            public string semantic;
            public string valueType;
            public int components;
            public int count;
            public long offset;
            public long byteCount;
        }

#pragma warning disable 0649 // JsonUtility populates this DTO via reflection.
        [Serializable]
        private sealed class UnityMeshImportResultEnvelope
        {
            public string type;
            public string status;
            public string message;
            public string error;
            public int created;
            public int skipped;
            public string[] warnings;
            public long timestamp;
        }
#pragma warning restore 0649

        public sealed class CreateInBlenderResult
        {
            public string status;
            public string message;
            public string error;
            public int created;
            public int skipped;
            public string[] warnings;
            public long updatedAt;

            internal static CreateInBlenderResult Idle()
            {
                return new CreateInBlenderResult
                {
                    status = "idle",
                    message = null,
                    warnings = Array.Empty<string>(),
                };
            }

            internal CreateInBlenderResult Clone()
            {
                return new CreateInBlenderResult
                {
                    status = status,
                    message = message,
                    error = error,
                    created = created,
                    skipped = skipped,
                    warnings = warnings != null ? (string[])warnings.Clone() : Array.Empty<string>(),
                    updatedAt = updatedAt,
                };
            }
        }

        public sealed class MeshSource
        {
            private readonly Mesh _mesh;
            private readonly GameObject _owner;
            private readonly string _sourceKind;

            internal MeshSource(Mesh mesh, GameObject owner, string sourceKind)
            {
                _mesh = mesh;
                _owner = owner;
                _sourceKind = sourceKind ?? "mesh_asset";
            }

            public Mesh Mesh => _mesh;
            public GameObject Owner => _owner;
            public string SourceKind => _sourceKind;
            public UnityEngine.Object FieldObject => _owner != null ? (UnityEngine.Object)_owner : _mesh;
        }

    }
}
#endif
