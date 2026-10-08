using System;
using System.Collections.Generic;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.Protocol;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.AssetBridgeCore
{
    public sealed class RiggedObjectImportCoordinator
    {
        public static RiggedObjectPayload LastReceived { get; private set; }
        public static string LastError { get; private set; }

#if UNITY_EDITOR
        private static readonly Queue<RiggedObjectPayload> Pending = new Queue<RiggedObjectPayload>();
        private static readonly HashSet<string> PendingIds = new HashSet<string>(StringComparer.Ordinal);
        private static readonly Dictionary<string, RiggedObjectPayload> LatestById = new Dictionary<string, RiggedObjectPayload>(StringComparer.Ordinal);
        private static readonly Dictionary<string, double> RetryAfterById = new Dictionary<string, double>(StringComparer.Ordinal);
        private static readonly object PendingLock = new object();
        private static bool _editorHookInstalled;
        private const double MissingDependencyRetryDelaySeconds = 0.25d;
#endif

        public bool HandleEnvelope(string rawJson)
        {
            LastError = null;
            LastReceived = null;

            try
            {
                var env = JsonUtility.FromJson<RiggedObjectEnvelope>(rawJson);
                if (env == null || env.riggedObject == null)
                {
                    LastError = "rigged_object_invalid";
                    BlenderSyncLog.Error(
                        "RiggedObject",
                        "invalid_payload",
                        "The incoming rigged object payload was invalid.");
                    ReportImportError(LastError, rawJson);
                    return false;
                }

                var rig = env.riggedObject;
                NormalizeRigPayload(rig);
                if (string.IsNullOrWhiteSpace(rig.riggedObjectId))
                {
                    LastError = "rigged_object_id_missing";
                    BlenderSyncLog.Error(
                        "RiggedObject",
                        "object_id_missing",
                        "The incoming rigged object had no identifier.");
                    ReportImportError(LastError, rawJson);
                    return false;
                }
                if (!HasRigMeshReference(rig))
                {
                    LastError = "rigged_object_mesh_ref_missing";
                    BlenderSyncLog.Error(
                        "RiggedObject",
                        "mesh_reference_missing",
                        "The incoming rigged object had no mesh reference.",
                        new Dictionary<string, object>
                        {
                            { "riggedObjectId", rig.riggedObjectId },
                        });
                    ReportImportError(LastError, rawJson);
                    return false;
                }

                LastReceived = rig;
                BlenderSyncLog.Trace(
                    "RiggedObject",
                    "payload_received",
                    () => "Received a rigged object payload.",
                    () => new Dictionary<string, object>
                    {
                        { "riggedObjectId", rig.riggedObjectId },
                        { "spaceSemantic", rig.spaceSemantic },
                        { "rigAxisMode", rig.rigAxisMode },
                        { "primaryBoneAxis", rig.primaryBoneAxis },
                        { "secondaryBoneAxis", rig.secondaryBoneAxis },
                        { "boneCount", rig.orderedBoneIds != null ? rig.orderedBoneIds.Length : 0 },
                    });
#if UNITY_EDITOR
                EnsureEditorHook();
                Enqueue(rig);
                return true;
#else
                LastError = "rigged_prefab_builder_requires_editor";
                return false;
#endif
            }
            catch (Exception ex)
            {
                LastError = ex.Message;
                BlenderSyncLog.Exception(
                    "RiggedObject",
                    "parse_failed",
                    ex,
                    "Could not parse an incoming rigged object payload.",
                    new Dictionary<string, object>
                    {
                        { "rawLength", string.IsNullOrEmpty(rawJson) ? 0 : rawJson.Length },
                    });
                ReportImportError("parse_failed", rawJson, ex);
                return false;
            }
        }

        private static void ReportImportError(string summary, string rawJson, Exception ex = null)
        {
            BlenderSyncReportStore.Add("Rigged Object", "ERROR", summary, new Dictionary<string, object>
            {
                { "rawLength", string.IsNullOrEmpty(rawJson) ? 0 : rawJson.Length },
                { "exceptionType", ex?.GetType().Name ?? string.Empty },
                { "error", ex?.Message ?? string.Empty },
            });
        }

        private static void NormalizeRigPayload(RiggedObjectPayload rig)
        {
            if (rig == null)
                return;

            rig.riggedObjectId = NormalizeOptional(rig.riggedObjectId);
            rig.spaceSemantic = NormalizeOptional(rig.spaceSemantic);
            rig.rigAxisMode = NormalizeOptional(rig.rigAxisMode);
            rig.primaryBoneAxis = NormalizeOptional(rig.primaryBoneAxis);
            rig.secondaryBoneAxis = NormalizeOptional(rig.secondaryBoneAxis);
            rig.meshRef = NormalizeOptional(rig.meshRef);
            rig.visibilityState = NormalizeOptional(rig.visibilityState);
            rig.materialRefs = NormalizeStringArray(rig.materialRefs);
            rig.rootBoneId = NormalizeOptional(rig.rootBoneId);
            rig.orderedBoneIds = NormalizeStringArray(rig.orderedBoneIds);

            if (rig.meshParts != null)
            {
                foreach (var part in rig.meshParts)
                {
                    if (part == null)
                        continue;
                    part.meshRef = NormalizeOptional(part.meshRef);
                    part.visibilityState = NormalizeOptional(part.visibilityState);
                    part.materialRefs = NormalizeStringArray(part.materialRefs);
                    part.orderedBoneIds = NormalizeStringArray(part.orderedBoneIds);
                }
            }

            if (rig.skeleton?.bones == null)
                return;

            foreach (var bone in rig.skeleton.bones)
            {
                if (bone == null)
                    continue;
                bone.boneId = NormalizeOptional(bone.boneId);
                bone.parentBoneId = NormalizeOptional(bone.parentBoneId);
            }
        }

        private static bool HasRigMeshReference(RiggedObjectPayload rig)
        {
            if (!string.IsNullOrWhiteSpace(rig?.meshRef))
                return true;

            if (rig?.meshParts == null)
                return false;

            foreach (var part in rig.meshParts)
            {
                if (!string.IsNullOrWhiteSpace(part?.meshRef))
                    return true;
            }
            return false;
        }

        private static string NormalizeOptional(string value)
        {
            var normalized = value?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

        private static string[] NormalizeStringArray(string[] values)
        {
            if (values == null || values.Length == 0)
                return values;

            var normalized = new string[values.Length];
            for (var i = 0; i < values.Length; i++)
                normalized[i] = NormalizeOptional(values[i]);
            return normalized;
        }

#if UNITY_EDITOR
        private static void EnsureEditorHook()
        {
            lock (PendingLock)
            {
                if (_editorHookInstalled)
                    return;
                EditorApplication.update += PumpEditorQueue;
                _editorHookInstalled = true;
            }
        }

        private static void PumpEditorQueue()
        {
            TryProcessPendingNow();
        }

        private static void Enqueue(RiggedObjectPayload rig)
        {
            if (rig == null || string.IsNullOrWhiteSpace(rig.riggedObjectId))
                return;
            LastReceived = rig;
            lock (PendingLock)
            {
                LatestById[rig.riggedObjectId] = rig;
                if (!PendingIds.Add(rig.riggedObjectId))
                {
                    RetryAfterById.Remove(rig.riggedObjectId);
                    BlenderSyncLog.Trace(
                        "RiggedObject",
                        "pending_updated",
                        () => "Updated an already queued rigged object.",
                        () => new Dictionary<string, object>
                        {
                            { "riggedObjectId", rig.riggedObjectId },
                            { "pending", Pending.Count },
                        });
                    return;
                }

                RetryAfterById.Remove(rig.riggedObjectId);
                Pending.Enqueue(rig);
                BlenderSyncLog.Trace(
                    "RiggedObject",
                    "queued",
                    () => "Queued a rigged object for prefab creation.",
                    () => new Dictionary<string, object>
                    {
                        { "riggedObjectId", rig.riggedObjectId },
                        { "pending", Pending.Count },
                    });
            }
        }

        public static void TryProcessPendingNow()
        {
            PumpPending();
        }

        private static void PumpPending()
        {
            int attempts;
            lock (PendingLock)
            {
                if (Pending.Count == 0)
                    return;

                if (!HasRetryReadyLocked(EditorApplication.timeSinceStartup))
                    return;

                attempts = Pending.Count;
                BlenderSyncLog.Trace(
                    "RiggedObject",
                    "queue_pump",
                    () => "Processing queued rigged objects.",
                    () => new Dictionary<string, object>
                    {
                        { "attempts", attempts },
                        { "pending", Pending.Count },
                    });
            }

            for (var i = 0; i < attempts; i++)
            {
                var rig = DequeuePending();
                if (rig == null || string.IsNullOrWhiteSpace(rig.riggedObjectId))
                    continue;

                if (!CanRetryNow(rig.riggedObjectId))
                {
                    RequeuePending(rig, log: false);
                    continue;
                }

                var buildResult = new RiggedPrefabBuilder().BuildOrUpdate(rig);
                if (!buildResult.success)
                {
                    LastError = buildResult.error ?? "rigged_prefab_build_failed";
                    if (IsRetryableDependencyError(buildResult.error))
                    {
                        RequeuePending(rig, log: true);
                        continue;
                    }

                    ClearPendingId(rig.riggedObjectId);
                    SaveBuildFailure(rig, LastError);
                    BlenderSyncLog.Error(
                        "RiggedObject",
                        "prefab_build_failed",
                        LastError,
                        new Dictionary<string, object>
                        {
                            { "riggedObjectId", rig.riggedObjectId },
                        });
                    continue;
                }

                ClearPendingId(rig.riggedObjectId);
                var registry = new RiggedObjectRegistry();
                var db = registry.Load();
                var existingRecord = registry.FindByRiggedObjectId(db, rig.riggedObjectId);
                var record = existingRecord ?? new RiggedObjectRecord { riggedObjectId = rig.riggedObjectId };
                record.riggedObjectId = rig.riggedObjectId;
                record.objectName = rig.objectName;
                record.prefabPath = buildResult.prefabPath;
                record.meshRef = rig.meshRef;
                record.meshRefs = CollectRiggedMeshRefs(rig);
                record.materialRefs = CollectRiggedMaterialRefs(rig);
                record.mappingState = "mapped";
                record.updateState = "up_to_date";
                record.lastImportedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                record.lastError = null;
                var instanceOperation = "none";

                if (rig.prefabPolicy != null && rig.prefabPolicy.autoInstantiate)
                {
                    var instanceService = new RiggedInstanceService();
                    var instance = existingRecord != null && existingRecord.managedInstance != null
                        ? instanceService.ReplaceManagedInstance(rig, buildResult.prefabPath, existingRecord.managedInstance, out var instanceError)
                        : instanceService.CreateManagedInstance(rig, buildResult.prefabPath, out instanceError);
                    if (instance == null)
                    {
                        LastError = instanceError ?? "rigged_instance_create_failed";
                        record.lastError = LastError;
                        record.updateState = "error";
                        registry.Upsert(db, record);
                        registry.Save(db);
                        BlenderSyncLog.Error(
                            "RiggedObject",
                            "instance_create_failed",
                            LastError,
                            new Dictionary<string, object>
                            {
                                { "riggedObjectId", rig.riggedObjectId },
                            });
                        ReportBuildFailure(rig.riggedObjectId, record.objectName, record.prefabPath, LastError);
                        continue;
                    }

                    record.managedInstance = new RiggedManagedInstanceRecord
                    {
                        pairId = existingRecord != null && existingRecord.managedInstance != null && !string.IsNullOrWhiteSpace(existingRecord.managedInstance.pairId)
                            ? existingRecord.managedInstance.pairId
                            : $"rigpair-{rig.riggedObjectId}",
                        sceneObjectId = RiggedObjectRegistry.TryCaptureSceneObjectId(instance),
                        scenePath = RiggedObjectRegistry.TryCaptureScenePath(instance),
                    };
                    instanceOperation = existingRecord != null && existingRecord.managedInstance != null
                        ? "replace"
                        : "create";
                }

                LastError = null;
                registry.Upsert(db, record);
                registry.Save(db);
                BlenderSyncLog.Info(
                    "RiggedObject",
                    "import_completed",
                    "Created or updated the rigged object prefab.",
                    new Dictionary<string, object>
                    {
                        { "riggedObjectId", rig.riggedObjectId },
                        { "prefabOperation", buildResult.operation },
                        { "instanceOperation", instanceOperation },
                    });
            }
        }

        private static RiggedObjectPayload DequeuePending()
        {
            lock (PendingLock)
            {
                if (Pending.Count == 0)
                    return null;

                var rig = Pending.Dequeue();
                if (rig != null && LatestById.TryGetValue(rig.riggedObjectId ?? string.Empty, out var latest) && latest != null)
                    rig = latest;
                return rig;
            }
        }

        private static bool CanRetryNow(string riggedObjectId)
        {
            if (string.IsNullOrWhiteSpace(riggedObjectId))
                return false;

            lock (PendingLock)
            {
                return IsRetryReady(riggedObjectId, EditorApplication.timeSinceStartup);
            }
        }

        private static bool HasRetryReadyLocked(double now)
        {
            foreach (var rig in Pending)
            {
                if (rig != null && IsRetryReady(rig.riggedObjectId, now))
                    return true;
            }
            return false;
        }

        private static bool IsRetryReady(string riggedObjectId, double now)
        {
            return !string.IsNullOrWhiteSpace(riggedObjectId)
                && (!RetryAfterById.TryGetValue(riggedObjectId, out var retryAfter) || now >= retryAfter);
        }

        private static void RequeuePending(RiggedObjectPayload rig, bool log)
        {
            if (rig == null || string.IsNullOrWhiteSpace(rig.riggedObjectId))
                return;

            lock (PendingLock)
            {
                if (log)
                    RetryAfterById[rig.riggedObjectId] = EditorApplication.timeSinceStartup + MissingDependencyRetryDelaySeconds;
                Pending.Enqueue(rig);
                if (log)
                    BlenderSyncLog.Trace(
                        "RiggedObject",
                        "dependency_retry_scheduled",
                        () => "Deferred rigged object creation until referenced assets are available.",
                        () => new Dictionary<string, object>
                        {
                            { "riggedObjectId", rig.riggedObjectId },
                            { "reason", LastError },
                            { "pending", Pending.Count },
                        });
            }
        }

        private static bool IsRetryableDependencyError(string error)
        {
            return string.Equals(error, "mesh_asset_not_mapped", StringComparison.Ordinal)
                || string.Equals(error, "mesh_asset_not_found", StringComparison.Ordinal)
                || string.Equals(error, "material_asset_not_mapped", StringComparison.Ordinal)
                || string.Equals(error, "material_asset_not_found", StringComparison.Ordinal);
        }

        private static void ClearPendingId(string riggedObjectId)
        {
            if (string.IsNullOrWhiteSpace(riggedObjectId))
                return;

            lock (PendingLock)
            {
                PendingIds.Remove(riggedObjectId);
                LatestById.Remove(riggedObjectId);
                RetryAfterById.Remove(riggedObjectId);
            }
        }

        private static void SaveBuildFailure(RiggedObjectPayload rig, string error)
        {
            if (rig == null || string.IsNullOrWhiteSpace(rig.riggedObjectId))
                return;

            var registry = new RiggedObjectRegistry();
            var db = registry.Load();
            var existing = registry.FindByRiggedObjectId(db, rig.riggedObjectId);
            var record = existing ?? new RiggedObjectRecord { riggedObjectId = rig.riggedObjectId };
            record.riggedObjectId = rig.riggedObjectId;

            record.objectName = rig.objectName;
            record.prefabPath = existing?.prefabPath;
            record.meshRef = rig.meshRef;
            record.meshRefs = CollectRiggedMeshRefs(rig);
            record.materialRefs = CollectRiggedMaterialRefs(rig);
            record.mappingState = string.IsNullOrWhiteSpace(record.prefabPath) ? "missing_target" : "mapped";
            record.updateState = "error";
            record.lastImportedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            record.lastError = error ?? "rigged_prefab_build_failed";

            registry.Upsert(db, record);
            registry.Save(db);
            ReportBuildFailure(rig.riggedObjectId, record.objectName, record.prefabPath, record.lastError);
        }

        private static void ReportBuildFailure(string riggedObjectId, string objectName, string prefabPath, string error)
        {
            BlenderSyncReportStore.Add("Rigged Object", "ERROR", "rigged_object_build_failed", new Dictionary<string, object>
            {
                { "riggedObjectId", riggedObjectId ?? string.Empty },
                { "objectName", objectName ?? string.Empty },
                { "prefabPath", prefabPath ?? string.Empty },
                { "error", error ?? string.Empty },
            });
        }

        private static string[] CollectRiggedMaterialRefs(RiggedObjectPayload rig)
        {
            var refs = new List<string>();
            AddMaterialRefs(refs, rig?.materialRefs);
            if (rig?.meshParts != null)
            {
                foreach (var part in rig.meshParts)
                    AddMaterialRefs(refs, part?.materialRefs);
            }
            return refs.ToArray();
        }

        private static string[] CollectRiggedMeshRefs(RiggedObjectPayload rig)
        {
            var refs = new List<string>();
            AddUniqueRef(refs, rig?.meshRef);
            if (rig?.meshParts != null)
            {
                foreach (var part in rig.meshParts)
                    AddUniqueRef(refs, part?.meshRef);
            }
            return refs.ToArray();
        }

        private static void AddUniqueRef(List<string> refs, string value)
        {
            if (refs == null)
                return;

            var normalized = NormalizeOptional(value);
            if (string.IsNullOrWhiteSpace(normalized) || refs.Contains(normalized))
                return;
            refs.Add(normalized);
        }

        private static void AddMaterialRefs(List<string> refs, string[] materialRefs)
        {
            if (refs == null || materialRefs == null)
                return;

            foreach (var materialRef in materialRefs)
            {
                var normalized = NormalizeOptional(materialRef);
                if (string.IsNullOrWhiteSpace(normalized) || refs.Contains(normalized))
                    continue;
                refs.Add(normalized);
            }
        }
#endif
    }
}
