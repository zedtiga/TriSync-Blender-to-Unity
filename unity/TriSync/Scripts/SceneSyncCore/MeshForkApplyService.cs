using System;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Protocol;
using UnityEngine;
#if UNITY_EDITOR
using BlenderSyncVNext.Diagnostics;
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class MeshForkApplyService
    {
        private readonly ObjectResourceLinkRegistry _linkRegistry = new ObjectResourceLinkRegistry();
        private readonly AptRepository _aptRepository = new AptRepository();
        private readonly AssetContainerService _assetContainerService = new AssetContainerService();

        public bool TryApply(string pairId, GameObject target, SceneSyncMeshForkMessage msg, out string error)
        {
            error = null;
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
            if (msg == null)
            {
                error = "message is null";
                return false;
            }
            if (string.IsNullOrWhiteSpace(msg.targetMeshRef))
            {
                error = "targetMeshRef is missing";
                return false;
            }

#if !UNITY_EDITOR
            error = "mesh_fork_requires_editor";
            return false;
#else
            var meshFilter = target.GetComponent<MeshFilter>();
            if (meshFilter == null)
            {
                meshFilter = target.AddComponent<MeshFilter>();
            }
            if (target.GetComponent<MeshRenderer>() == null)
            {
                target.AddComponent<MeshRenderer>();
            }

            var currentMesh = meshFilter.sharedMesh;
            var entry = BuildMeshEntry(msg.targetMeshRef, currentMesh);
            var importResult = _assetContainerService.ImportOrCreateContainer(entry, priorFingerprint: null);
            if (!importResult.success || string.IsNullOrWhiteSpace(importResult.unityAssetPath))
            {
                error = importResult.error ?? "mesh_fork_asset_create_failed";
                return false;
            }

            var forkedAsset = AssetDatabase.LoadAssetAtPath<Mesh>(importResult.unityAssetPath);
            if (forkedAsset == null)
            {
                error = "mesh_fork_asset_load_failed";
                return false;
            }

            var aptDb = _aptRepository.Load();
            var record = _aptRepository.FindByAssetId(aptDb, msg.targetMeshRef) ?? new AptRecord { assetId = msg.targetMeshRef };
            record.sourceFingerprint = entry.sourceFingerprint;
            record.meshContentFingerprint = entry.meshContentFingerprint;
            record.meshContentFingerprintNoUv = entry.meshContentFingerprintNoUv;
            record.source = new AptSource
            {
                sourceUri = string.IsNullOrWhiteSpace(msg.sourceMeshRef) ? $"scene_sync://mesh_fork/{pairId}" : $"scene_sync://mesh_fork/{msg.sourceMeshRef}",
                sourceObjectPath = string.IsNullOrWhiteSpace(msg.objectName) ? target.name : msg.objectName,
            };
            record.target = new AptTarget
            {
                unityAssetPath = importResult.unityAssetPath,
                resourceType = "mesh",
                assetGuid = importResult.assetGuid,
                localFileId = importResult.localFileId,
                isSkinned = false,
                boneCount = 0,
                skinEncoding = null,
            };
            record.mappingState = "mapped";
            record.updateState = "up_to_date";
            record.lastImportedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            record.lastError = null;
            _aptRepository.Upsert(aptDb, record);
            _aptRepository.Save(aptDb);

            meshFilter.sharedMesh = forkedAsset;
            _linkRegistry.UpdateReference(pairId, target, "mesh", msg.targetMeshRef);
            MeshApplyService.RegisterPairMeshBinding(pairId, forkedAsset);

            BlenderSyncLog.Trace(
                "MeshFork",
                "applied",
                () => "Applied a dedicated mesh fork to the object.",
                () => new System.Collections.Generic.Dictionary<string, object>
                {
                    { "pairId", pairId },
                    { "sourceMeshRef", msg.sourceMeshRef },
                    { "targetMeshRef", msg.targetMeshRef },
                });
            return true;
#endif
        }

        private static AssetBridgeResourceEntry BuildMeshEntry(string assetId, Mesh currentMesh)
        {
            return new AssetBridgeResourceEntry
            {
                assetId = assetId,
                sourceFingerprint = BuildFingerprint(currentMesh),
                type = "mesh",
                source = new AssetBridgeSource
                {
                    sourceUri = string.IsNullOrWhiteSpace(assetId) ? "scene_sync://mesh_fork" : $"scene_sync://mesh_fork/{assetId}"
                },
                mesh = BuildMeshPayload(currentMesh),
            };
        }

        private static MeshPayload BuildMeshPayload(Mesh mesh)
        {
            if (mesh == null)
            {
                return new MeshPayload
                {
                    topology = "triangles",
                    vertexCount = 0,
                    vertices = Array.Empty<float>(),
                    indices = Array.Empty<int>(),
                    normals = Array.Empty<float>(),
                    uv0 = Array.Empty<float>(),
                };
            }

            var vertices = mesh.vertices ?? Array.Empty<Vector3>();
            var normals = mesh.normals ?? Array.Empty<Vector3>();
            var uv = mesh.uv ?? Array.Empty<Vector2>();
            var triangles = mesh.triangles ?? Array.Empty<int>();

            var vertexData = new float[vertices.Length * 3];
            for (var i = 0; i < vertices.Length; i++)
            {
                var v = SceneSyncTransformMapper.MapPoint(vertices[i]);
                var o = i * 3;
                vertexData[o] = v.x;
                vertexData[o + 1] = v.y;
                vertexData[o + 2] = v.z;
            }

            float[] normalData = Array.Empty<float>();
            if (normals.Length == vertices.Length && normals.Length > 0)
            {
                normalData = new float[normals.Length * 3];
                for (var i = 0; i < normals.Length; i++)
                {
                    var n = SceneSyncTransformMapper.MapDirection(normals[i]);
                    var o = i * 3;
                    normalData[o] = n.x;
                    normalData[o + 1] = n.y;
                    normalData[o + 2] = n.z;
                }
            }

            float[] uvData = Array.Empty<float>();
            if (uv.Length == vertices.Length && uv.Length > 0)
            {
                uvData = new float[uv.Length * 2];
                for (var i = 0; i < uv.Length; i++)
                {
                    var u = uv[i];
                    var o = i * 2;
                    uvData[o] = u.x;
                    uvData[o + 1] = u.y;
                }
            }

            return new MeshPayload
            {
                topology = "triangles",
                vertexCount = vertices.Length,
                vertices = vertexData,
                indices = SceneSyncTransformMapper.MapTriangles((int[])triangles.Clone()),
                normals = normalData,
                uv0 = uvData,
            };
        }

        private static string BuildFingerprint(Mesh mesh)
        {
            if (mesh == null)
                return "mesh-fork-empty";
            return $"mesh-fork:{mesh.vertexCount}:{mesh.triangles?.Length ?? 0}:{mesh.name}";
        }
    }
}
