using System.Collections.Generic;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class ObjectAssemblyService
    {
        private readonly MeshReferenceApplyService _meshApply = new MeshReferenceApplyService();
        private readonly MaterialReferenceApplyService _materialApply = new MaterialReferenceApplyService();
        private readonly ObjectBindingRegistry _bindingRegistry = new ObjectBindingRegistry();
        private readonly ObjectResourceLinkRegistry _linkRegistry = new ObjectResourceLinkRegistry();
        private readonly ResourceResolver _resolver = new ResourceResolver();

#if UNITY_EDITOR
        private static readonly object PendingLock = new object();
        private const int MaxPendingAttempts = 300;
        private const int MaxPendingPerPump = 16;
        private const double MinPendingPumpIntervalSeconds = 0.05d;
        private static readonly Queue<PendingAssembly> Pending = new Queue<PendingAssembly>();
        private static readonly HashSet<string> PendingPairIds = new HashSet<string>(System.StringComparer.Ordinal);
        private static readonly Dictionary<string, SceneSyncObjectAssemblyMessage> LatestByPairId = new Dictionary<string, SceneSyncObjectAssemblyMessage>(System.StringComparer.Ordinal);
        private static readonly Dictionary<string, int> AttemptsByPairId = new Dictionary<string, int>(System.StringComparer.Ordinal);
        private static bool _hookInstalled;
        private static bool _pumpProcessing;
        private static double _nextPumpAt;
        private static SceneSyncCoordinator _pumpCoordinator;
        private static int _queueEnqueuedCount;
        private static int _queueAssembledCount;
        private static int _queueTimeoutCount;
        private static int _queueFailedCount;
        private static string _queueLastPairId;
        private static string _queueLastObjectName;
        private static string _queueLastWaitReason;
        private static string _queueLastError;
#endif

        public bool TryAssemble(SceneSyncCoordinator coordinator, SceneSyncObjectAssemblyMessage msg, out string error)
        {
            error = null;

            if (coordinator == null)
            {
                error = "coordinator_missing";
                return false;
            }
            if (msg == null)
            {
                error = "message_missing";
                return false;
            }
            if (string.IsNullOrWhiteSpace(msg.pairId))
            {
                error = "pair_id_missing";
                return false;
            }

            var objectType = GetObjectType(msg);
            if (objectType == "mesh" && string.IsNullOrWhiteSpace(msg.meshRef))
            {
                error = "mesh_ref_missing";
                return false;
            }

#if UNITY_EDITOR
            EnsureEditorHook(coordinator);
            if (objectType == "mesh" && msg.materialRefs == null)
            {
                error = "material_refs_missing";
                return false;
            }

            lock (PendingLock)
            {
                LatestByPairId[msg.pairId] = msg;
                AttemptsByPairId[msg.pairId] = 0;
                if (!PendingPairIds.Add(msg.pairId))
                {
                    BlenderSyncLog.Trace(
                        "ObjectAssembly",
                        "pending_updated",
                        () => "Updated an already-pending object assembly request.",
                        () => new Dictionary<string, object>
                        {
                            { "pairId", msg.pairId },
                            { "pendingCount", Pending.Count },
                        });
                    return true;
                }

                Pending.Enqueue(new PendingAssembly { pairId = msg.pairId, msg = msg });
                _queueEnqueuedCount++;
                _queueLastPairId = msg.pairId;
                _queueLastObjectName = msg.objectName;
            }
            return true;
#else
            return TryAssembleNow(coordinator, msg, out error);
#endif
        }

        private bool TryAssembleNow(SceneSyncCoordinator coordinator, SceneSyncObjectAssemblyMessage msg, out string error)
        {
            error = null;

            var objectType = GetObjectType(msg);
            var slotMaterialRefs = GetSlotMaterialRefs(msg);
            var effectiveMaterialRefs = GetNonEmptyMaterialRefs(slotMaterialRefs);

            GameObject target;
            if (coordinator.TryGetMappedTarget(msg.pairId, out var existing) && existing != null)
            {
                target = existing.gameObject;
                BlenderSyncLog.Trace(
                    "ObjectAssembly",
                    "target_reused",
                    () => "Reused the runtime object binding.",
                    () => new Dictionary<string, object> { { "pairId", msg.pairId } });
            }
            else if (TryRebindFromRegistry(coordinator, msg.pairId, out target))
            {
                BlenderSyncLog.Trace(
                    "ObjectAssembly",
                    "target_rebound",
                    () => "Restored the object binding from the registry.",
                    () => new Dictionary<string, object> { { "pairId", msg.pairId } });
            }
            else
            {
                target = new GameObject(string.IsNullOrWhiteSpace(msg.objectName) ? msg.pairId : msg.objectName);
                BlenderSyncLog.Trace(
                    "ObjectAssembly",
                    "target_created",
                    () => "Created a Unity object for the incoming assembly.",
                    () => new Dictionary<string, object> { { "pairId", msg.pairId } });
            }

            ApplyObjectName(target, msg);
            coordinator.RegisterMappedTarget(msg.pairId, target.transform);
            PersistBinding(msg.pairId, target);

            if (objectType == "camera")
            {
                EnsureNoLight(target);
                EnsureNoMeshComponents(target);
                ApplyCamera(target, msg.camera);
                target.SetActive(msg.active);
            }
            else if (objectType == "light")
            {
                EnsureNoCamera(target);
                EnsureNoMeshComponents(target);
                ApplyLight(target, msg.light);
                target.SetActive(msg.active);
            }
            else if (objectType == "empty")
            {
                EnsureNoCamera(target);
                EnsureNoLight(target);
                EnsureNoMeshComponents(target);
                target.SetActive(msg.active);
            }
            else
            {
                EnsureNoCamera(target);
                EnsureNoLight(target);

                if (!_meshApply.TryApply(target, msg.pairId, msg.meshRef, out var meshErr))
                {
                    error = meshErr ?? "mesh_reference_apply_failed";
                    return false;
                }

                if (msg.materialRefs == null)
                {
                    error = "material_refs_missing";
                    return false;
                }

                if (msg.materialRefs != null)
                {
                    if (!_materialApply.TryApplyMany(target, slotMaterialRefs, out var matErr))
                        BlenderSyncLog.Warn(
                            "ObjectAssembly",
                            "material_mapping_incomplete",
                            matErr,
                            new Dictionary<string, object> { { "pairId", msg.pairId } });
                }
            }

            if (msg.position != null && msg.position.Length >= 3)
                target.transform.position = SceneSyncTransformMapper.MapPosition(msg.position);
            if (msg.rotation != null && msg.rotation.Length >= 4)
                target.transform.rotation = SceneSyncTransformMapper.MapRotationForObjectType(msg.rotation, objectType);
            if (msg.scale != null && msg.scale.Length >= 3)
                target.transform.localScale = SceneSyncTransformMapper.MapScale(msg.scale);

            _linkRegistry.UpsertFromAssembly(msg.pairId, target, objectType == "mesh" ? msg.meshRef : null, objectType == "mesh" ? slotMaterialRefs : new List<string>());

            BlenderSyncReportStore.Add(
                "Object Assembly",
                "OK",
                $"pair={msg.pairId} type={objectType} name={target.name}",
                new Dictionary<string, object>
                {
                    { "pairId", msg.pairId },
                    { "objectName", msg.objectName },
                    { "target", target.name },
                    { "objectType", objectType },
                    { "meshRef", msg.meshRef },
                    { "materialSlots", msg.materialRefs != null ? msg.materialRefs.Length : 0 },
                    { "materialCount", effectiveMaterialRefs.Count },
                    { "active", msg.active },
                });
            return true;
        }

        private static string GetObjectType(SceneSyncObjectAssemblyMessage msg)
        {
            var objectType = msg != null ? msg.objectType : null;
            if (string.IsNullOrWhiteSpace(objectType))
                return "mesh";
            return objectType.Trim().ToLowerInvariant();
        }

        private static void ApplyObjectName(GameObject target, SceneSyncObjectAssemblyMessage msg)
        {
            if (target == null || msg == null || string.IsNullOrWhiteSpace(msg.objectName))
                return;
            var desiredName = msg.objectName.Trim();
            if (!string.Equals(target.name, desiredName, System.StringComparison.Ordinal))
                target.name = desiredName;
        }

        private static List<string> GetSlotMaterialRefs(SceneSyncObjectAssemblyMessage msg)
        {
            var refs = new List<string>();
            if (msg?.materialRefs == null || msg.materialRefs.Length == 0)
                return refs;

            foreach (var r in msg.materialRefs)
                refs.Add(string.IsNullOrWhiteSpace(r) ? null : r.Trim());

            return refs;
        }

        private static List<string> GetNonEmptyMaterialRefs(IReadOnlyList<string> materialRefs)
        {
            var refs = new List<string>();
            if (materialRefs == null || materialRefs.Count == 0)
                return refs;

            foreach (var r in materialRefs)
            {
                if (!string.IsNullOrWhiteSpace(r))
                    refs.Add(r.Trim());
            }

            return refs;
        }

        private static void EnsureNoMeshComponents(GameObject target)
        {
            var meshFilter = target.GetComponent<MeshFilter>();
            if (meshFilter != null)
                Object.DestroyImmediate(meshFilter);

            var meshRenderer = target.GetComponent<MeshRenderer>();
            if (meshRenderer != null)
                Object.DestroyImmediate(meshRenderer);

            var skinned = target.GetComponent<SkinnedMeshRenderer>();
            if (skinned != null)
                Object.DestroyImmediate(skinned);
        }

        private static void EnsureNoCamera(GameObject target)
        {
            var camera = target.GetComponent<Camera>();
            if (camera != null)
                Object.DestroyImmediate(camera);
        }

        private static void EnsureNoLight(GameObject target)
        {
            var light = target.GetComponent<Light>();
            if (light != null)
                Object.DestroyImmediate(light);
        }

        private static void ApplyCamera(GameObject target, SceneSyncCameraPayload payload)
        {
            var camera = target.GetComponent<Camera>();
            if (camera == null)
                camera = target.AddComponent<Camera>();

            if (payload == null)
                return;

            if (!float.IsNaN(payload.fov) && payload.fov > 0f)
                camera.fieldOfView = payload.fov * Mathf.Rad2Deg;
            if (!float.IsNaN(payload.near) && payload.near > 0f)
                camera.nearClipPlane = payload.near;
            if (!float.IsNaN(payload.far) && payload.far > 0f)
                camera.farClipPlane = payload.far;
        }

        private static void ApplyLight(GameObject target, SceneSyncLightPayload payload)
        {
            var light = target.GetComponent<Light>();
            if (light == null)
                light = target.AddComponent<Light>();

            if (payload == null)
                return;

            var type = string.IsNullOrWhiteSpace(payload.lightType) ? "POINT" : payload.lightType.Trim().ToUpperInvariant();
            if (type == "SPOT")
                light.type = LightType.Spot;
            else if (type == "SUN" || type == "DIRECTIONAL")
                light.type = LightType.Directional;
            else
                light.type = LightType.Point;

            if (payload.color != null && payload.color.Length >= 3)
                light.color = new Color(payload.color[0], payload.color[1], payload.color[2], 1f);
            if (!float.IsNaN(payload.intensity) && payload.intensity >= 0f)
                light.intensity = payload.intensity;
            if (!float.IsNaN(payload.range) && payload.range > 0f)
                light.range = payload.range;
            if (!float.IsNaN(payload.spotAngle) && payload.spotAngle > 0f)
                light.spotAngle = payload.spotAngle * Mathf.Rad2Deg;
        }

#if UNITY_EDITOR
        private void EnsureEditorHook(SceneSyncCoordinator coordinator)
        {
            lock (PendingLock)
            {
                _pumpCoordinator = coordinator;
                if (_hookInstalled) return;
                EditorApplication.update += PumpPendingStatic;
                _hookInstalled = true;
            }
        }

        private static void PumpPendingStatic()
        {
            SceneSyncCoordinator coordinator;
            lock (PendingLock)
            {
                coordinator = _pumpCoordinator;
            }
            if (coordinator == null) return;
            new ObjectAssemblyService().PumpPending(coordinator);
        }

        public static int TryProcessPendingNow(SceneSyncCoordinator coordinator)
        {
            if (coordinator == null)
                return 0;

            return new ObjectAssemblyService().PumpPending(coordinator);
        }

        private int PumpPending(SceneSyncCoordinator coordinator)
        {
            var assetQueue = AssetImportCoordinator.GetQueueSnapshot();
            if (assetQueue.processing || assetQueue.pendingCount > 0)
            {
                lock (PendingLock)
                {
                    if (Pending.Count > 0)
                    {
                        _queueLastWaitReason = $"asset_package_queue_pending pending={assetQueue.pendingCount} processing={assetQueue.processing}";
                        _nextPumpAt = EditorApplication.timeSinceStartup + MinPendingPumpIntervalSeconds;
                    }
                }
                return 0;
            }

            int attempts;
            lock (PendingLock)
            {
                if (_pumpProcessing)
                    return 0;
                if (EditorApplication.timeSinceStartup < _nextPumpAt)
                    return 0;
                if (Pending.Count == 0)
                    return 0;
                attempts = Pending.Count;
                if (attempts > MaxPendingPerPump)
                    attempts = MaxPendingPerPump;
                _pumpProcessing = true;
            }

            var assembled = 0;
            try
            {
                for (var i = 0; i < attempts; i++)
                {
                    var pending = DequeuePending();
                    if (pending == null)
                        break;
                    var msg = pending != null ? pending.msg : null;
                    if (msg == null)
                    {
                        ClearPendingPairId(pending.pairId);
                        continue;
                    }

                    if (!RefsReady(msg))
                    {
                        pending.attempts++;
                        if (pending.attempts == 1 || pending.attempts % 30 == 0)
                        {
                            var waitReason = BuildPendingWaitReason(msg);
                            lock (PendingLock)
                            {
                                _queueLastPairId = msg.pairId;
                                _queueLastObjectName = msg.objectName;
                                _queueLastWaitReason = waitReason;
                            }
                        }
                        if (pending.attempts >= MaxPendingAttempts)
                        {
                            var reason = BuildPendingWaitReason(msg);
                            BlenderSyncLog.Error(
                                "ObjectAssembly",
                                "pending_references_timed_out",
                                reason,
                                new Dictionary<string, object>
                                {
                                    { "pairId", msg.pairId },
                                    { "attempts", pending.attempts },
                                });
                            SceneSyncStateStore.MarkError("object_assembly_refs_timeout: " + reason, msg.pairId);
                            lock (PendingLock)
                            {
                                _queueTimeoutCount++;
                                _queueLastPairId = msg.pairId;
                                _queueLastObjectName = msg.objectName;
                                _queueLastWaitReason = reason;
                                _queueLastError = "pending_refs_timeout";
                            }
                            ClearPendingPairId(msg.pairId);
                            continue;
                        }

                        lock (PendingLock)
                        {
                            AttemptsByPairId[msg.pairId] = pending.attempts;
                            Pending.Enqueue(pending);
                        }
                        continue;
                    }

                    if (TryAssembleNow(coordinator, msg, out var err))
                    {
                        ClearPendingPairId(msg.pairId);
                        lock (PendingLock)
                        {
                            _queueAssembledCount++;
                            _queueLastPairId = msg.pairId;
                            _queueLastObjectName = msg.objectName;
                        }
                        assembled++;
                    }
                    else
                    {
                        ClearPendingPairId(msg.pairId);
                        lock (PendingLock)
                        {
                            _queueFailedCount++;
                            _queueLastPairId = msg.pairId;
                            _queueLastObjectName = msg.objectName;
                            _queueLastError = err;
                        }
                        BlenderSyncLog.Error(
                            "ObjectAssembly",
                            "deferred_apply_failed",
                            err,
                            new Dictionary<string, object> { { "pairId", msg.pairId } });
                    }
                }
            }
            finally
            {
                lock (PendingLock)
                {
                    _pumpProcessing = false;
                    _nextPumpAt = Pending.Count > 0 ? EditorApplication.timeSinceStartup + MinPendingPumpIntervalSeconds : 0.0d;
                }
            }

            return assembled;
        }
#else
        public static int TryProcessPendingNow(SceneSyncCoordinator coordinator)
        {
            return 0;
        }
#endif

        public static void CancelPending(string pairId)
        {
#if UNITY_EDITOR
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
                return;

            lock (PendingLock)
            {
                PendingPairIds.Remove(pairId);
                LatestByPairId.Remove(pairId);
                AttemptsByPairId.Remove(pairId);
            }
#endif
        }

        public static ObjectAssemblyQueueSnapshot GetQueueSnapshot()
        {
#if UNITY_EDITOR
            lock (PendingLock)
            {
                var maxAttempts = 0;
                foreach (var attempts in AttemptsByPairId.Values)
                {
                    if (attempts > maxAttempts)
                        maxAttempts = attempts;
                }

                var nextPairId = "";
                var nextObjectName = "";
                if (Pending.Count > 0)
                {
                    var next = Pending.Peek();
                    nextPairId = next?.pairId;
                    if (!string.IsNullOrWhiteSpace(nextPairId) && LatestByPairId.TryGetValue(nextPairId, out var latest) && latest != null)
                        nextObjectName = latest.objectName;
                }

                return new ObjectAssemblyQueueSnapshot
                {
                    pendingCount = Pending.Count,
                    trackedPairCount = PendingPairIds.Count,
                    enqueuedCount = _queueEnqueuedCount,
                    assembledCount = _queueAssembledCount,
                    timeoutCount = _queueTimeoutCount,
                    failedCount = _queueFailedCount,
                    processing = _pumpProcessing,
                    nextPumpInSeconds = Pending.Count > 0 ? System.Math.Max(0.0d, _nextPumpAt - EditorApplication.timeSinceStartup) : 0.0d,
                    maxAttempts = maxAttempts,
                    nextPairId = nextPairId,
                    nextObjectName = nextObjectName,
                    lastPairId = _queueLastPairId,
                    lastObjectName = _queueLastObjectName,
                    lastWaitReason = _queueLastWaitReason,
                    lastError = _queueLastError,
                };
            }
#else
            return new ObjectAssemblyQueueSnapshot();
#endif
        }

#if UNITY_EDITOR
        private sealed class PendingAssembly
        {
            public string pairId;
            public SceneSyncObjectAssemblyMessage msg;
            public int attempts;
        }

        private static PendingAssembly DequeuePending()
        {
            lock (PendingLock)
            {
                if (Pending.Count == 0)
                    return null;

                var pending = Pending.Dequeue();
                var pairId = NormalizePairId(pending?.pairId);
                if (pending != null && !string.IsNullOrWhiteSpace(pairId))
                {
                    pending.pairId = pairId;
                    if (!PendingPairIds.Contains(pairId))
                    {
                        pending.msg = null;
                        pending.attempts = 0;
                        return pending;
                    }
                }
                if (pending != null && LatestByPairId.TryGetValue(pairId ?? string.Empty, out var latest) && latest != null)
                {
                    pending.msg = latest;
                    pending.attempts = AttemptsByPairId.TryGetValue(pairId ?? string.Empty, out var attempts) ? attempts : pending.attempts;
                }
                return pending;
            }
        }

        private static void ClearPendingPairId(string pairId)
        {
            if (string.IsNullOrWhiteSpace(pairId))
                return;

            lock (PendingLock)
            {
                PendingPairIds.Remove(pairId);
                LatestByPairId.Remove(pairId);
                AttemptsByPairId.Remove(pairId);
            }
        }

        private static string NormalizePairId(string pairId)
        {
            var normalized = (pairId ?? string.Empty).Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

        private string BuildPendingWaitReason(SceneSyncObjectAssemblyMessage msg)
        {
            if (msg == null)
                return "message_missing";
            if (GetObjectType(msg) != "mesh")
                return "not_mesh";

            var missing = new List<string>();
            if (!_resolver.TryResolveMesh(msg.meshRef, out var _, out var meshErr))
                missing.Add("mesh=" + (string.IsNullOrWhiteSpace(msg.meshRef) ? "<none>" : msg.meshRef) + ":" + (string.IsNullOrWhiteSpace(meshErr) ? "not_resolved" : meshErr));

            if (msg.materialRefs == null)
            {
                missing.Add("materialRefs=<null>");
            }
            else
            {
                foreach (var materialRef in GetNonEmptyMaterialRefs(GetSlotMaterialRefs(msg)))
                {
                    if (!_resolver.TryResolveMaterial(materialRef, out var _, out var matErr))
                        missing.Add("material=" + materialRef + ":" + (string.IsNullOrWhiteSpace(matErr) ? "not_resolved" : matErr));
                    if (missing.Count >= 8)
                        break;
                }
            }

            return missing.Count > 0 ? string.Join(",", missing) : "refs_not_ready";
        }
#endif

        [System.Serializable]
        public sealed class ObjectAssemblyQueueSnapshot
        {
            public int pendingCount;
            public int trackedPairCount;
            public int enqueuedCount;
            public int assembledCount;
            public int timeoutCount;
            public int failedCount;
            public bool processing;
            public double nextPumpInSeconds;
            public int maxAttempts;
            public string nextPairId;
            public string nextObjectName;
            public string lastPairId;
            public string lastObjectName;
            public string lastWaitReason;
            public string lastError;
        }

        private bool TryRebindFromRegistry(SceneSyncCoordinator coordinator, string pairId, out GameObject target)
        {
            target = null;
#if UNITY_EDITOR
            if (!_bindingRegistry.TryResolveBoundObject(pairId, out target, out var entry) || target == null)
            {
                if (entry != null)
                    _bindingRegistry.MarkMissing(pairId);
                return false;
            }

            coordinator.RegisterMappedTarget(pairId, target.transform);
            return true;
#else
            return false;
#endif
        }

        private void PersistBinding(string pairId, GameObject target)
        {
#if UNITY_EDITOR
            if (!_bindingRegistry.UpsertCurrentBinding(pairId, target))
                BlenderSyncLog.Warn(
                    "ObjectAssembly",
                    "binding_save_skipped",
                    "The object was assembled, but its persistent binding could not be saved.",
                    new Dictionary<string, object> { { "pairId", pairId } });
#endif
        }

        private bool RefsReady(SceneSyncObjectAssemblyMessage msg)
        {
            if (GetObjectType(msg) != "mesh")
                return true;

            if (!_resolver.TryResolveMesh(msg.meshRef, out var _, out var _))
                return false;

            if (msg.materialRefs == null)
                return false;

            var materialRefs = GetNonEmptyMaterialRefs(GetSlotMaterialRefs(msg));
            foreach (var materialRef in materialRefs)
            {
                if (string.IsNullOrWhiteSpace(materialRef))
                    continue;

                if (!_resolver.TryResolveMaterial(materialRef, out var _, out var _))
                    return false;
            }

            return true;
        }
    }
}
