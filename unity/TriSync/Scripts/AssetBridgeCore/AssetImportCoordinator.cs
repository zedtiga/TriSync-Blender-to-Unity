using System;
using System.Collections.Generic;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.Protocol;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.AssetBridgeCore
{
    public sealed class AssetImportCoordinator
    {
        private readonly AptRepository _repository = new AptRepository();
        private readonly AssetContainerService _containerService = new AssetContainerService();

        private static readonly List<string> StateTimeline = new List<string>();
        private static readonly object StateLock = new object();
        private static AssetImportPackageSnapshot _lastPackageSnapshot;

        public static IReadOnlyList<string> GetStateTimeline()
        {
            lock (StateLock)
            {
                return StateTimeline.ToArray();
            }
        }

        public static AssetImportPackageSnapshot GetLastPackageSnapshot()
        {
            lock (StateLock)
            {
                return CloneSnapshot(_lastPackageSnapshot);
            }
        }

#if UNITY_EDITOR
        private const double MinPackageProcessIntervalSeconds = 0.20d;
        private static readonly object PendingLock = new object();
        private static readonly Queue<QueuedEnvelope> Pending = new Queue<QueuedEnvelope>();
        private static bool _editorHookInstalled;
        private static int _queueEnqueuedCount;
        private static int _queueProcessedCount;
        private static int _queueFailedCount;
        private static bool _queueProcessing;
        private static double _nextProcessAt;
        private static string _queueCurrentPackageId;
        private static string _queueLastEnqueuedPackageId;
        private static string _queueLastError;
#endif

        public void HandleEnvelope(string rawJson)
        {
            AssetBridgeEnvelope env;
            try
            {
                env = JsonUtility.FromJson<AssetBridgeEnvelope>(rawJson);
            }
            catch (Exception ex)
            {
                BlenderSyncLog.Exception(
                    "AssetImport",
                    "envelope_parse_failed",
                    ex,
                    "Could not parse an incoming asset package.",
                    new Dictionary<string, object>
                    {
                        { "rawLength", string.IsNullOrEmpty(rawJson) ? 0 : rawJson.Length },
                    });
                ReportEnvelopeError("envelope_parse_failed", rawJson, ex);
                return;
            }

            if (env == null || env.package == null || env.package.resources == null)
            {
                BlenderSyncLog.Error(
                    "AssetImport",
                    "invalid_envelope",
                    "The incoming asset package was missing required data.");
                ReportEnvelopeError("invalid_envelope_payload", rawJson);
                return;
            }

#if UNITY_EDITOR
            EnsureEditorHook();
            lock (PendingLock)
            {
                Pending.Enqueue(new QueuedEnvelope { env = env });
                _queueEnqueuedCount++;
                _queueLastEnqueuedPackageId = NormalizeOptional(env.package.packageId) ?? "(unknown)";
            }
#else
            ProcessEnvelope(env);
#endif
        }

        private void ProcessEnvelope(AssetBridgeEnvelope env)
        {
            var packageSw = System.Diagnostics.Stopwatch.StartNew();
            var packageId = NormalizeOptional(env?.package?.packageId) ?? "(unknown)";
            var resources = env?.package?.resources ?? Array.Empty<AssetBridgeResourceEntry>();
            var db = _repository.Load();
            var packageResult = new AssetImportPackageResult
            {
                packageId = packageId,
                resourceCount = resources.Length,
            };


#if UNITY_EDITOR
            var previousDeferSave = AssetContainerService.DeferAssetDatabaseSave;
            AssetContainerService.DeferAssetDatabaseSave = true;
            var needsAssetSave = false;
            try
            {
#endif
            foreach (var res in resources)
            {
                NormalizeResourceEntry(res);
                if (res == null || string.IsNullOrWhiteSpace(res.assetId))
                {
                    var invalidResult = new AssetImportResourceResult
                    {
                        assetId = res?.assetId,
                        resourceType = res?.type,
                        success = false,
                        operation = "error",
                        error = res == null ? "resource_entry_missing" : "asset_id_missing",
                        unityAssetPath = null,
                    };
                    packageResult.results.Add(invalidResult);
                    CountResourceResult(packageResult, invalidResult);
                    continue;
                }

                var existing = _repository.FindByAssetId(db, res.assetId);
                var priorFingerprint = existing != null ? existing.sourceFingerprint : null;
                var record = existing ?? new AptRecord();
                var previousTarget = CloneTarget(record.target);
                var previousSource = CloneSource(record.source);
                var previousSourceFingerprint = record.sourceFingerprint;
                var previousMeshContentFingerprint = record.meshContentFingerprint;
                var previousMeshContentFingerprintNoUv = record.meshContentFingerprintNoUv;
                record.assetId = res.assetId;
                record.sourceFingerprint = res.sourceFingerprint;
                if (string.Equals(res.type, "mesh", StringComparison.OrdinalIgnoreCase))
                {
                    record.meshContentFingerprint = res.meshContentFingerprint;
                    record.meshContentFingerprintNoUv = res.meshContentFingerprintNoUv;
                }
                record.source = new AptSource
                {
                    sourceUri = res.source != null ? res.source.sourceUri : null,
                    sourceFile = null,
                    sourceObjectPath = null,
                };
                record.target = record.target ?? new AptTarget();
                record.target.resourceType = res.type;
                record.mappingState = "missing_target";
                record.updateState = "needs_import";
                record.lastImportedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                record.lastError = null;
                TraceTransition(record.assetId, record.mappingState, record.updateState, "start");

                var resourceResult = new AssetImportResourceResult
                {
                    assetId = res.assetId,
                    resourceType = res.type,
                    success = false,
                    operation = "error",
                    error = null,
                    unityAssetPath = null,
                };

                try
                {
                    var result = _containerService.ImportOrCreateContainer(res, priorFingerprint, existing?.target?.unityAssetPath);
                    if (result.success)
                    {
#if UNITY_EDITOR
                        if (result.operation == "create" || result.operation == "update")
                            needsAssetSave = true;
#endif
                        record.target.unityAssetPath = result.unityAssetPath;
                        record.target.assetGuid = result.assetGuid;
                        record.target.localFileId = result.localFileId;
                        record.target.isSkinned = res.mesh != null && res.mesh.skin != null && res.mesh.skin.isSkinned;
                        record.target.boneCount = (res.mesh != null && res.mesh.skin != null) ? res.mesh.skin.boneCount : 0;
                        record.target.skinEncoding = (res.mesh != null && res.mesh.skin != null) ? res.mesh.skin.skinEncoding : null;
                        record.mappingState = "mapped";
                        record.updateState = "up_to_date";
                        BlenderSyncVNext.SceneSyncCore.ResourceRuntimeMapping.MarkStale(record.assetId);
                        TraceTransition(record.assetId, record.mappingState, record.updateState, result.operation ?? "apply_success");

                        resourceResult.success = true;
                        resourceResult.operation = string.IsNullOrWhiteSpace(result.operation) ? "create" : result.operation;
                        resourceResult.error = null;
                        resourceResult.unityAssetPath = result.unityAssetPath;
                    }
                    else
                    {
                        RestoreRecordAfterFailure(record, previousTarget, previousSource, previousSourceFingerprint, previousMeshContentFingerprint, previousMeshContentFingerprintNoUv);
                        record.mappingState = HasTarget(record.target) ? "mapped" : "missing_target";
                        record.updateState = "error";
                        record.lastError = result.error ?? "import failed";
                        TraceTransition(record.assetId, record.mappingState, record.updateState, record.lastError);

                        resourceResult.success = false;
                        resourceResult.operation = "error";
                        resourceResult.error = record.lastError;
                    }
                }
                catch (Exception ex)
                {
                    RestoreRecordAfterFailure(record, previousTarget, previousSource, previousSourceFingerprint, previousMeshContentFingerprint, previousMeshContentFingerprintNoUv);
                    record.mappingState = HasTarget(record.target) ? "mapped" : "missing_target";
                    record.updateState = "error";
                    record.lastError = ex.Message;
                    TraceTransition(record.assetId, record.mappingState, record.updateState, record.lastError);

                    resourceResult.success = false;
                    resourceResult.operation = "error";
                    resourceResult.error = ex.Message;
                }

                packageResult.results.Add(resourceResult);
                CountResourceResult(packageResult, resourceResult);
                _repository.Upsert(db, record);
            }
#if UNITY_EDITOR
            }
            finally
            {
                AssetContainerService.DeferAssetDatabaseSave = previousDeferSave;
            }

            double packageSaveMs = 0;
            double packageRefreshMs = 0;
            if (needsAssetSave)
            {
                var saveStart = packageSw.Elapsed.TotalMilliseconds;
                AssetDatabase.SaveAssets();
                packageSaveMs = packageSw.Elapsed.TotalMilliseconds - saveStart;
                if (ShouldRefreshAfterPackageSave())
                {
                    var refreshStart = packageSw.Elapsed.TotalMilliseconds;
                    AssetDatabase.Refresh();
                    packageRefreshMs = packageSw.Elapsed.TotalMilliseconds - refreshStart;
                }
            }
#endif

            var repositorySaveStart = packageSw.Elapsed.TotalMilliseconds;
            _repository.Save(db);
            var repositorySaveMs = packageSw.Elapsed.TotalMilliseconds - repositorySaveStart;
#if UNITY_EDITOR
            var rigPendingStart = packageSw.Elapsed.TotalMilliseconds;
            RiggedObjectImportCoordinator.TryProcessPendingNow();
            var rigPendingMs = packageSw.Elapsed.TotalMilliseconds - rigPendingStart;
#endif
            packageResult.status = ComputePackageStatus(packageResult);
            SetLastPackageSnapshot(ToSnapshot(packageResult));
            BlenderSyncReportStore.Add(
                "Asset Package",
                packageResult.errorCount > 0 ? "ERROR" : "OK",
                $"package={packageResult.packageId} status={packageResult.status} resources={packageResult.resourceCount} success={packageResult.successCount} failed={packageResult.errorCount}",
                new Dictionary<string, object>
                {
                    { "packageId", packageResult.packageId },
                    { "status", packageResult.status },
                    { "resources", packageResult.resourceCount },
                    { "success", packageResult.successCount },
                    { "failed", packageResult.errorCount },
                    { "created", packageResult.createdCount },
                    { "updated", packageResult.updatedCount },
                    { "skipped", packageResult.skippedCount },
                    { "saveMs", packageSaveMs.ToString("F2") },
                    { "refreshMs", packageRefreshMs.ToString("F2") },
                    { "repositorySaveMs", repositorySaveMs.ToString("F2") },
                    { "rigPendingMs", rigPendingMs.ToString("F2") },
                    { "totalMs", packageSw.Elapsed.TotalMilliseconds.ToString("F2") },
                });
            BlenderSyncLog.Trace(
                "AssetImport",
                "package_profile",
                () => "Completed an asset package import.",
                () => new Dictionary<string, object>
                {
                    { "packageId", packageResult.packageId },
                    { "status", packageResult.status },
                    { "resources", packageResult.resourceCount },
                    { "success", packageResult.successCount },
                    { "failed", packageResult.errorCount },
                    { "created", packageResult.createdCount },
                    { "updated", packageResult.updatedCount },
                    { "skipped", packageResult.skippedCount },
                    { "saveMs", packageSaveMs.ToString("F2") },
                    { "refreshMs", packageRefreshMs.ToString("F2") },
                    { "repositorySaveMs", repositorySaveMs.ToString("F2") },
                    { "rigPendingMs", rigPendingMs.ToString("F2") },
                    { "totalMs", packageSw.Elapsed.TotalMilliseconds.ToString("F2") },
                });
        }

#if UNITY_EDITOR
        private static void EnsureEditorHook()
        {
            lock (PendingLock)
            {
                if (_editorHookInstalled) return;
                EditorApplication.update += PumpEditorQueue;
                _editorHookInstalled = true;
            }
        }

        private static void PumpEditorQueue()
        {
            TryProcessPendingNow();
        }

        public static bool TryProcessPendingNow()
        {
            QueuedEnvelope item = null;
            lock (PendingLock)
            {
                if (_queueProcessing)
                    return false;
                if (EditorApplication.timeSinceStartup < _nextProcessAt)
                    return false;
                if (Pending.Count > 0)
                {
                    item = Pending.Dequeue();
                    _queueProcessing = true;
                    _queueCurrentPackageId = NormalizeOptional(item.env?.package?.packageId) ?? "(unknown)";
                }
            }

            if (item == null || item.env == null)
            {
                lock (PendingLock)
                {
                    _queueProcessing = false;
                    _queueCurrentPackageId = null;
                }
                return false;
            }

            var failed = false;
            try
            {
                new AssetImportCoordinator().ProcessEnvelope(item.env);
                return true;
            }
            catch (Exception ex)
            {
                failed = true;
                BlenderSyncLog.Exception(
                    "AssetImport",
                    "queued_import_failed",
                    ex,
                    "A queued asset package import failed.",
                    new Dictionary<string, object>
                    {
                        { "packageId", _queueCurrentPackageId },
                    });
                BlenderSyncReportStore.Add("Asset Package", "ERROR", "queued_import_failed", new Dictionary<string, object>
                {
                    { "exceptionType", ex.GetType().Name },
                    { "error", ex.Message },
                });
                return true;
            }
            finally
            {
                lock (PendingLock)
                {
                    _queueProcessedCount++;
                    if (failed)
                    {
                        _queueFailedCount++;
                        _queueLastError = $"package={_queueCurrentPackageId} queued_import_failed";
                    }
                    _queueProcessing = false;
                    _queueCurrentPackageId = null;
                    _nextProcessAt = EditorApplication.timeSinceStartup + MinPackageProcessIntervalSeconds;
                }
            }
        }

        private sealed class QueuedEnvelope
        {
            public AssetBridgeEnvelope env;
        }
#else
        public static bool TryProcessPendingNow()
        {
            return false;
        }
#endif

        public static AssetImportQueueSnapshot GetQueueSnapshot()
        {
#if UNITY_EDITOR
            lock (PendingLock)
            {
                var pendingCount = Pending.Count;
                return new AssetImportQueueSnapshot
                {
                    pendingCount = pendingCount,
                    enqueuedCount = _queueEnqueuedCount,
                    processedCount = _queueProcessedCount,
                    failedCount = _queueFailedCount,
                    processing = _queueProcessing,
                    nextProcessInSeconds = pendingCount > 0 ? Math.Max(0.0d, _nextProcessAt - EditorApplication.timeSinceStartup) : 0.0d,
                    currentPackageId = _queueCurrentPackageId,
                    lastEnqueuedPackageId = _queueLastEnqueuedPackageId,
                    lastError = _queueLastError,
                };
            }
#else
            return new AssetImportQueueSnapshot();
#endif
        }

#if UNITY_EDITOR
        private static bool ShouldRefreshAfterPackageSave()
        {
            return IsEnvEnabled("BLENDERSYNC_UNITY_REFRESH_AFTER_SAVE");
        }
#endif

        private static bool IsEnvEnabled(string name)
        {
            var value = Environment.GetEnvironmentVariable(name);
            if (string.IsNullOrWhiteSpace(value))
                return false;
            value = value.Trim().ToLowerInvariant();
            return value == "1" || value == "true" || value == "yes" || value == "on";
        }

        private static void NormalizeResourceEntry(AssetBridgeResourceEntry resource)
        {
            if (resource == null)
                return;

            resource.assetId = NormalizeOptional(resource.assetId);
            resource.sourceFingerprint = NormalizeOptional(resource.sourceFingerprint);
            resource.meshContentFingerprint = NormalizeOptional(resource.meshContentFingerprint);
            resource.meshContentFingerprintNoUv = NormalizeOptional(resource.meshContentFingerprintNoUv);
            var type = NormalizeOptional(resource.type);
            resource.type = string.IsNullOrWhiteSpace(type) ? null : type.ToLowerInvariant();
            if (resource.source != null)
                resource.source.sourceUri = NormalizeOptional(resource.source.sourceUri);
            NormalizeMeshPayload(resource.mesh);
        }

        private static void NormalizeMeshPayload(MeshPayload mesh)
        {
            if (mesh == null)
                return;

            mesh.topology = NormalizeOptional(mesh.topology);
            mesh.spaceSemantic = NormalizeOptional(mesh.spaceSemantic);
            mesh.colorAttributeName = NormalizeOptional(mesh.colorAttributeName);
            mesh.meshSource = NormalizeOptional(mesh.meshSource);
            mesh.meshSourceRequested = NormalizeOptional(mesh.meshSourceRequested);
            mesh.meshSourceResolved = NormalizeOptional(mesh.meshSourceResolved);
            mesh.meshSourceReason = NormalizeOptional(mesh.meshSourceReason);
            mesh.modifierSummary = NormalizeStringArray(mesh.modifierSummary);
            mesh.blendShapeSkipReason = NormalizeOptional(mesh.blendShapeSkipReason);
            mesh.skinSkipReason = NormalizeOptional(mesh.skinSkipReason);
            NormalizeSubMeshes(mesh.subMeshes);
            NormalizeBuffer(mesh.color0Buffer);
            NormalizeBuffers(mesh.binaryBuffers);
            NormalizeUvChannels(mesh.uvChannels);
            NormalizeBlendShapes(mesh.blendShapes);
            NormalizeSkin(mesh.skin);
        }

        private static void NormalizeSubMeshes(MeshSubMeshPayload[] subMeshes)
        {
            if (subMeshes == null)
                return;
            foreach (var subMesh in subMeshes)
            {
                if (subMesh == null)
                    continue;
                subMesh.topology = NormalizeOptional(subMesh.topology);
                if (subMesh.materialSlot < 0)
                    subMesh.materialSlot = 0;
            }
        }

        private static void NormalizeUvChannels(MeshUvChannelPayload[] channels)
        {
            if (channels == null)
                return;
            foreach (var channel in channels)
            {
                if (channel == null)
                    continue;
                channel.name = NormalizeOptional(channel.name);
                NormalizeBuffer(channel.buffer);
            }
        }

        private static void NormalizeBlendShapes(MeshBlendShapePayload[] blendShapes)
        {
            if (blendShapes == null)
                return;
            foreach (var blendShape in blendShapes)
            {
                if (blendShape == null)
                    continue;
                blendShape.name = NormalizeOptional(blendShape.name);
                NormalizeBuffer(blendShape.deltaPositionsBuffer);
            }
        }

        private static void NormalizeSkin(MeshSkinPayload skin)
        {
            if (skin == null)
                return;
            skin.skinEncoding = NormalizeOptional(skin.skinEncoding);
            skin.spaceSemantic = NormalizeOptional(skin.spaceSemantic);
        }

        private static void NormalizeBuffers(MeshBinaryBuffer[] buffers)
        {
            if (buffers == null)
                return;
            foreach (var buffer in buffers)
                NormalizeBuffer(buffer);
        }

        private static void NormalizeBuffer(MeshBinaryBuffer buffer)
        {
            if (buffer == null)
                return;
            buffer.semantic = NormalizeOptional(buffer.semantic);
            buffer.format = NormalizeOptional(buffer.format);
            buffer.path = NormalizeOptional(buffer.path);
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

        private static string NormalizeOptional(string value)
        {
            var normalized = value?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

        private static void ReportEnvelopeError(string summary, string rawJson, Exception ex = null)
        {
            BlenderSyncReportStore.Add("Asset Package", "ERROR", summary, new Dictionary<string, object>
            {
                { "rawLength", string.IsNullOrEmpty(rawJson) ? 0 : rawJson.Length },
                { "exceptionType", ex?.GetType().Name ?? string.Empty },
                { "error", ex?.Message ?? string.Empty },
            });
        }

        private static void CountResourceResult(AssetImportPackageResult packageResult, AssetImportResourceResult resourceResult)
        {
            if (packageResult == null || resourceResult == null)
                return;

            if (resourceResult.success)
                packageResult.successCount++;
            else
                packageResult.errorCount++;

            switch (resourceResult.operation)
            {
                case "create": packageResult.createdCount++; break;
                case "update": packageResult.updatedCount++; break;
                case "skip": packageResult.skippedCount++; break;
            }
        }

        private static string ComputePackageStatus(AssetImportPackageResult packageResult)
        {
            if (packageResult == null)
                return "failed";
            if (packageResult.successCount == 0)
                return "failed";
            if (packageResult.errorCount > 0)
                return "partial";
            return "success";
        }

        private static AssetImportPackageSnapshot ToSnapshot(AssetImportPackageResult packageResult)
        {
            if (packageResult == null)
                return null;
            return new AssetImportPackageSnapshot
            {
                packageId = packageResult.packageId,
                resourceCount = packageResult.resourceCount,
                successCount = packageResult.successCount,
                errorCount = packageResult.errorCount,
                createdCount = packageResult.createdCount,
                updatedCount = packageResult.updatedCount,
                skippedCount = packageResult.skippedCount,
                status = packageResult.status,
            };
        }

        private static void SetLastPackageSnapshot(AssetImportPackageSnapshot snapshot)
        {
            lock (StateLock)
            {
                _lastPackageSnapshot = CloneSnapshot(snapshot);
            }
        }

        private static AssetImportPackageSnapshot CloneSnapshot(AssetImportPackageSnapshot snapshot)
        {
            if (snapshot == null)
                return null;
            return new AssetImportPackageSnapshot
            {
                packageId = snapshot.packageId,
                resourceCount = snapshot.resourceCount,
                successCount = snapshot.successCount,
                errorCount = snapshot.errorCount,
                createdCount = snapshot.createdCount,
                updatedCount = snapshot.updatedCount,
                skippedCount = snapshot.skippedCount,
                status = snapshot.status,
            };
        }

        private static void TraceTransition(string assetId, string mappingState, string updateState, string detail)
        {
            var line = $"[vNext][AssetState] asset={assetId} map={mappingState} upd={updateState} detail={detail}";
            lock (StateLock)
            {
                StateTimeline.Add(line);
                if (StateTimeline.Count > 30) StateTimeline.RemoveAt(0);
            }
        }

        private static AptTarget CloneTarget(AptTarget target)
        {
            if (target == null)
                return null;
            return new AptTarget
            {
                unityAssetPath = target.unityAssetPath,
                resourceType = target.resourceType,
                assetGuid = target.assetGuid,
                localFileId = target.localFileId,
                isSkinned = target.isSkinned,
                boneCount = target.boneCount,
                skinEncoding = target.skinEncoding,
            };
        }

        private static AptSource CloneSource(AptSource source)
        {
            if (source == null)
                return null;
            return new AptSource
            {
                sourceUri = source.sourceUri,
                sourceFile = source.sourceFile,
                sourceObjectPath = source.sourceObjectPath,
            };
        }

        private static void RestoreRecordAfterFailure(AptRecord record, AptTarget previousTarget, AptSource previousSource, string previousSourceFingerprint, string previousMeshContentFingerprint, string previousMeshContentFingerprintNoUv)
        {
            if (record == null)
                return;
            record.source = previousSource;
            record.sourceFingerprint = previousSourceFingerprint;
            record.meshContentFingerprint = previousMeshContentFingerprint;
            record.meshContentFingerprintNoUv = previousMeshContentFingerprintNoUv;
            if (HasTarget(previousTarget))
                record.target = previousTarget;
        }

        private static bool HasTarget(AptTarget target)
        {
            return target != null && (!string.IsNullOrWhiteSpace(target.unityAssetPath) || !string.IsNullOrWhiteSpace(target.assetGuid));
        }

        [Serializable]
        public sealed class AssetImportPackageSnapshot
        {
            public string packageId;
            public int resourceCount;
            public int successCount;
            public int errorCount;
            public int createdCount;
            public int updatedCount;
            public int skippedCount;
            public string status;
        }

        [Serializable]
        public sealed class AssetImportQueueSnapshot
        {
            public int pendingCount;
            public int enqueuedCount;
            public int processedCount;
            public int failedCount;
            public bool processing;
            public double nextProcessInSeconds;
            public string currentPackageId;
            public string lastEnqueuedPackageId;
            public string lastError;
        }

        private sealed class AssetImportResourceResult
        {
            public string assetId;
            public string resourceType;
            public bool success;
            public string operation;
            public string error;
            public string unityAssetPath;
        }

        private sealed class AssetImportPackageResult
        {
            public string packageId;
            public int resourceCount;
            public int successCount;
            public int errorCount;
            public int createdCount;
            public int updatedCount;
            public int skippedCount;
            public string status;
            public List<AssetImportResourceResult> results = new List<AssetImportResourceResult>();
        }
    }
}
