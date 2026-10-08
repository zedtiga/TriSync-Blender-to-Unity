using System;
using System.Collections.Generic;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class SceneSyncCoordinator
    {
        private readonly object _mappingLock = new object();
        private readonly Dictionary<string, Transform> _mappedTargets = new Dictionary<string, Transform>();
        private readonly HashSet<string> _rebindObservedPairs = new HashSet<string>();
        private readonly TransformApplyService _apply = new TransformApplyService();
        private readonly TransformUpdateService _transformUpdate;
        private readonly MeshApplyService _meshApply = new MeshApplyService();
        private readonly MeshForkApplyService _meshForkApply = new MeshForkApplyService();
        private readonly PreviewMeshService _previewMesh = new PreviewMeshService();
        private readonly MeshPreviewUpdateService _meshPreviewUpdate;
        private readonly MeshUpdateService _meshUpdate;
        private readonly PreviewCommitService _previewCommit;
        private readonly MaterialReferenceApplyService _materialApply = new MaterialReferenceApplyService();
        private readonly MeshReferenceApplyService _meshRefApply = new MeshReferenceApplyService();
        private readonly ReferenceChangeService _referenceChange;
        private readonly MaterialContentV1DryRunService _materialContentV1DryRun = new MaterialContentV1DryRunService();
        private readonly MaterialContentV1ApplyService _materialContentV1Apply = new MaterialContentV1ApplyService();
        private readonly MaterialContentV1UpdateService _materialContentV1Update;
        private readonly ObjectAssemblyService _objectAssembly = new ObjectAssemblyService();
        private readonly ObjectRemoveService _objectRemove = new ObjectRemoveService();
        private readonly ObjectStateUpdateService _objectStateUpdate = new ObjectStateUpdateService();
        private readonly BlendShapeWeightApplyService _blendShapeWeights = new BlendShapeWeightApplyService();
        private readonly BlendShapeWeightsUpdateService _blendShapeWeightsUpdate;
        private readonly SceneViewStateService _viewState = new SceneViewStateService();
        private readonly SceneSyncPendingSceneOpsService _pendingSceneOps = new SceneSyncPendingSceneOpsService();
        private readonly ObjectBindingRegistry _bindingRegistry = new ObjectBindingRegistry();
        private readonly ObjectResourceLinkRegistry _linkRegistry = new ObjectResourceLinkRegistry();

        public SceneSyncCoordinator()
        {
            _transformUpdate = new TransformUpdateService(_apply);
            _meshPreviewUpdate = new MeshPreviewUpdateService(_previewMesh);
            _meshUpdate = new MeshUpdateService(_meshApply, _meshForkApply, _meshPreviewUpdate, _previewMesh, _materialApply, _linkRegistry);
            _previewCommit = new PreviewCommitService(_previewMesh, _materialApply, _linkRegistry);
            _referenceChange = new ReferenceChangeService(_materialApply, _meshRefApply, _linkRegistry);
            _materialContentV1Update = new MaterialContentV1UpdateService(_materialContentV1DryRun, _materialContentV1Apply);
            _blendShapeWeightsUpdate = new BlendShapeWeightsUpdateService(_blendShapeWeights);
        }

        public void RegisterMappedTarget(string pairId, Transform target)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId) || target == null) return;
            lock (_mappingLock)
            {
                _mappedTargets[pairId] = target;
            }
        }

        public bool TryGetMappedTarget(string pairId, out Transform target)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
            {
                target = null;
                return false;
            }

            return TryGetMappedTargetOnly(pairId, out target);
        }

        public bool TryGetOrRebindMappedTarget(string pairId, out Transform target)
        {
            return TryGetOrRebindTarget(pairId, out target, out _);
        }

        public void UnregisterMappedTarget(string pairId)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
                return;
            lock (_mappingLock)
            {
                _mappedTargets.Remove(pairId);
            }
            _transformUpdate.RemovePair(pairId);
        }

        public void HandleObjectAssemblyRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncObjectAssemblyMessage msg))
                return;

            NormalizeObjectAssemblyIds(msg);
            if (msg == null || msg.type != "scene_sync.object_assembly" || string.IsNullOrWhiteSpace(msg.pairId))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (_objectAssembly.TryAssemble(this, msg, out var err))
            {
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            SceneSyncStateStore.MarkError(err ?? "object_assembly_failed", msg.pairId);
        }

        public void HandleObjectRemoveRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncObjectRemoveMessage msg))
                return;

            if (msg == null || msg.type != "scene_sync.object_remove" || !HasAnyNonEmpty(msg.removedPairIds))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload");
                return;
            }

            if (_objectRemove.TryRemove(this, msg, out var err))
            {
                _pendingSceneOps.ClearForRemovedPairs(msg.removedPairIds);
                foreach (var pairId in msg.removedPairIds)
                {
                    if (!string.IsNullOrWhiteSpace(pairId))
                        SceneSyncStateStore.MarkApplied(pairId);
                }
                return;
            }

            SceneSyncStateStore.MarkError(err ?? "object_remove_failed");
        }

        public void HandleTransformRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncTransformMessage msg))
                return;

            NormalizePairIdField(msg);
            if (msg == null || msg.type != "scene_sync.transform" || string.IsNullOrWhiteSpace(msg.pairId) || !HasTransformArrays(msg))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (!TryGetOrRebindTarget(msg.pairId, out var target, out var reboundFromRegistry))
            {
                SceneSyncStateStore.MarkSkipped("not_mapped", msg.pairId);
                return;
            }

            if (reboundFromRegistry && TryMarkRebindObserved(msg.pairId))
                BlenderSyncLog.Trace(
                    "Transform",
                    "binding_restored",
                    () => "Restored the transform target from the registry.",
                    () => new Dictionary<string, object> { { "pairId", msg.pairId } });

            _transformUpdate.Apply(target, msg);
        }

        public void HandleHierarchyRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncHierarchyMessage msg))
                return;

            NormalizeHierarchyPairIds(msg);
            if (msg == null || msg.type != "scene_sync.hierarchy" || string.IsNullOrWhiteSpace(msg.childPairId) || (!msg.clearParent && string.IsNullOrWhiteSpace(msg.parentPairId)))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload");
                return;
            }

            _pendingSceneOps.EnqueueHierarchy(msg, TryApplyHierarchy);
        }

        public void HandleObjectNameRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncObjectNameMessage msg))
                return;

            NormalizePairIdField(msg);
            if (msg == null || msg.type != "scene_sync.object_name" || string.IsNullOrWhiteSpace(msg.pairId))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            _pendingSceneOps.EnqueueObjectName(msg, TryApplyObjectName);
        }

        public void HandleVisibilityRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncVisibilityMessage msg))
                return;

            NormalizePairIdField(msg);
            if (msg == null || msg.type != "scene_sync.visibility" || string.IsNullOrWhiteSpace(msg.pairId))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            _pendingSceneOps.EnqueueVisibility(msg, TryApplyVisibility);
        }

        public void HandleViewStateRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncViewStateMessage msg))
                return;

            if (msg == null || msg.type != "scene_sync.view_state_v1" || msg.pivot == null || msg.pivot.Length != 3 || msg.rotation == null || msg.rotation.Length != 4)
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload");
                return;
            }

            _viewState.ApplyAsync(msg);
        }

        public void HandleObjectStateUpdateRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncObjectStateUpdateMessage msg))
                return;

            NormalizeObjectStatePairIds(msg);
            if (msg == null || msg.type != "scene_sync.object_state_update_v1" || !HasAnyObjectStatePairId(msg.objects))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload");
                return;
            }

            _objectStateUpdate.Apply(msg, ResolveTarget);
        }

        private Transform ResolveTarget(string pairId)
        {
            return TryGetOrRebindTarget(pairId, out var target, out _) ? target : null;
        }

        public void HandleAutoSyncStateRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncAutoSyncStateMessage msg))
                return;

            if (msg == null || msg.type != "scene_sync.auto_sync_state")
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload");
                return;
            }

            PreviewMeshService.SetPreviewWatchdogCommitTimeoutSeconds(msg.previewIdleCommitSeconds);

#if UNITY_EDITOR
            if (!msg.enabled)
            {
                EditorApplication.delayCall += () =>
                {
                    try
                    {
                        SceneSyncTransformSmoother.CompleteAll();
                    }
                    catch (Exception ex)
                    {
                        BlenderSyncLog.Exception(
                            "AutoSync",
                            "smoother_clear_failed",
                            ex,
                            "Could not clear runtime transform smoothing state.");
                    }
                };
            }
#endif
            SceneSyncStateStore.MarkApplied("auto_sync_state");
        }

        public void HandleMeshForkRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncMeshForkMessage msg))
                return;

            NormalizeMeshForkIds(msg);
            if (msg == null || msg.type != "scene_sync.mesh_fork" || string.IsNullOrWhiteSpace(msg.pairId) || string.IsNullOrWhiteSpace(msg.targetMeshRef))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (!TryResolveMappedTarget(msg.pairId, out var target))
                return;

            _meshUpdate.ApplyFork(target.gameObject, msg);
        }

        public void HandleMeshUpdateRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncMeshUpdateMessage msg))
                return;

            NormalizePairIdField(msg);
            if (msg == null || msg.type != "scene_sync.mesh_update" || string.IsNullOrWhiteSpace(msg.pairId) || !HasMeshArrays(msg))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (!TryResolveMappedTarget(msg.pairId, out var target))
                return;

            _meshUpdate.Apply(target.gameObject, msg);
        }

        public void HandleMeshUpdateBinaryRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncMeshUpdateBinaryMessage msg))
                return;

            NormalizePairIdField(msg);
            if (msg == null || msg.type != "scene_sync.mesh_update_binary_v1" || string.IsNullOrWhiteSpace(msg.pairId) || !HasBuffers(msg.buffers))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (!TryResolveMappedTarget(msg.pairId, out var target))
                return;

            _meshUpdate.ApplyBinary(target.gameObject, msg);
        }

        public void HandleMeshPositionsUpdateRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncMeshPositionsUpdateMessage msg))
                return;

            NormalizePairIdField(msg);
            if (msg == null || msg.type != "scene_sync.mesh_positions_update_v1" || string.IsNullOrWhiteSpace(msg.pairId) || msg.buffer == null)
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (!TryResolveMappedTarget(msg.pairId, out var target))
                return;

            _meshUpdate.ApplyPreviewPositions(target.gameObject, msg);
        }

        public void HandleMeshUvUpdateRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncMeshUvUpdateMessage msg))
                return;

            NormalizePairIdField(msg);
            if (msg == null || msg.type != "scene_sync.mesh_uv_update_v1" || string.IsNullOrWhiteSpace(msg.pairId) || msg.buffer == null)
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (!TryResolveMappedTarget(msg.pairId, out var target))
                return;

            _meshUpdate.ApplyPreviewUv(target.gameObject, msg);
        }

        public void HandlePreviewCommitRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncPreviewCommitMessage msg))
                return;

            NormalizePairIdField(msg);
            if (msg == null || msg.type != "scene_sync.preview_commit" || string.IsNullOrWhiteSpace(msg.pairId))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (!TryResolveMappedTarget(msg.pairId, out var target))
                return;

            _previewCommit.CommitPreview(target.gameObject, msg);
        }

        public void HandlePreviewCommitMeshRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncPreviewCommitMeshMessage msg))
                return;

            NormalizePairIdField(msg);
            if (msg == null || msg.type != "scene_sync.preview_commit_mesh_v1" || string.IsNullOrWhiteSpace(msg.pairId) || !HasBuffers(msg.buffers))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (!TryResolveMappedTarget(msg.pairId, out var target))
                return;

            _previewCommit.CommitMesh(target.gameObject, msg);
        }

        public void HandleBlendShapeWeightsRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncBlendShapeWeightsMessage msg))
                return;

            NormalizePairIdField(msg);
            if (msg == null || msg.type != "scene_sync.blendshape_weights_v1" || string.IsNullOrWhiteSpace(msg.pairId))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (!TryResolveMappedTarget(msg.pairId, out var target))
                return;

            _blendShapeWeightsUpdate.Apply(target.gameObject, msg);
        }

        public void HandleReferenceChangeRaw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncReferenceChangeMessage msg))
                return;

            NormalizeReferenceChangeIds(msg);
            if (msg == null || msg.type != "scene_sync.reference_change" || string.IsNullOrWhiteSpace(msg.pairId) || !HasReferenceTarget(msg))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.pairId : null);
                return;
            }

            if (!TryResolveMappedTarget(msg.pairId, out var target))
                return;

            _referenceChange.Apply(msg, target.gameObject);
        }

        public void HandleMaterialContentV1Raw(string rawJson, bool sessionActive = true)
        {
            if (!TryParseActiveMessage(rawJson, sessionActive, out SceneSyncMaterialContentV1Message msg))
                return;

            NormalizeMaterialContentV1(msg);
            if (msg == null || msg.type != "scene_sync.material_content_v1" || msg.schema != "material_content_v1" || string.IsNullOrWhiteSpace(msg.materialRef))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload", msg != null ? msg.materialRef : null);
                return;
            }

            _materialContentV1Update.Apply(msg);
        }

        private static bool TryParseActiveMessage<T>(string rawJson, bool sessionActive, out T msg) where T : class
        {
            msg = null;
            if (!sessionActive)
            {
                SceneSyncStateStore.MarkSkipped("session_inactive");
                return false;
            }
            if (string.IsNullOrWhiteSpace(rawJson))
            {
                SceneSyncStateStore.MarkSkipped("invalid_payload");
                return false;
            }

            try
            {
                msg = JsonUtility.FromJson<T>(rawJson);
                return true;
            }
            catch (Exception ex)
            {
                SceneSyncStateStore.MarkError("parse_failed: " + ex.Message);
                return false;
            }
        }

        private static bool HasTransformArrays(SceneSyncTransformMessage msg)
        {
            return msg.position != null && msg.position.Length == 3
                   && msg.rotation != null && msg.rotation.Length == 4
                   && msg.scale != null && msg.scale.Length == 3;
        }

        private static bool HasMeshArrays(SceneSyncMeshUpdateMessage msg)
        {
            return msg.vertices != null && msg.vertices.Length % 3 == 0
                   && msg.triangles != null && msg.triangles.Length % 3 == 0;
        }

        private static bool HasBuffers(SceneSyncBinaryBuffer[] buffers)
        {
            return buffers != null && buffers.Length > 0;
        }

        private static bool HasReferenceTarget(SceneSyncReferenceChangeMessage msg)
        {
            var kind = (msg.resourceKind ?? string.Empty).Trim();
            if (string.Equals(kind, "mesh", StringComparison.OrdinalIgnoreCase))
                return !string.IsNullOrWhiteSpace(msg.resourceRef);
            if (!string.Equals(kind, "material", StringComparison.OrdinalIgnoreCase))
                return false;

            if (!string.IsNullOrWhiteSpace(msg.resourceRef))
                return true;

            var refs = msg.resourceRefs;
            if (refs != null)
                return true;
            return false;
        }

        private static bool HasAnyNonEmpty(string[] values)
        {
            if (values == null || values.Length == 0)
                return false;
            for (var i = 0; i < values.Length; i++)
            {
                if (!string.IsNullOrWhiteSpace(values[i]))
                    return true;
            }
            return false;
        }

        private static bool HasAnyObjectStatePairId(SceneSyncObjectStateItem[] objects)
        {
            if (objects == null || objects.Length == 0)
                return false;
            for (var i = 0; i < objects.Length; i++)
            {
                if (!string.IsNullOrWhiteSpace(objects[i]?.pairId))
                    return true;
            }
            return false;
        }

        private bool TryResolveMappedTarget(string pairId, out Transform target)
        {
            if (TryGetOrRebindTarget(pairId, out target, out _))
                return true;

            SceneSyncStateStore.MarkSkipped("not_mapped", pairId);
            return false;
        }

        private bool TryGetOrRebindTarget(string pairId, out Transform target, out bool reboundFromRegistry)
        {
            reboundFromRegistry = false;
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
            {
                target = null;
                return false;
            }

            if (TryGetMappedTargetOnly(pairId, out target) && target != null)
                return true;

#if UNITY_EDITOR
            if (_bindingRegistry.TryResolveBoundObject(pairId, out var rebound, out var entry) && rebound != null)
            {
                target = rebound.transform;
                RegisterMappedTarget(pairId, target);
                reboundFromRegistry = true;
                return true;
            }

            if (entry != null)
                _bindingRegistry.MarkMissing(pairId);

            if (TryResolveRiggedManagedInstance(pairId, out var riggedRoot) && riggedRoot != null)
            {
                target = riggedRoot.transform;
                RegisterMappedTarget(pairId, target);
                reboundFromRegistry = true;
                return true;
            }
#endif

            target = null;
            return false;
        }

#if UNITY_EDITOR
        private static bool TryResolveRiggedManagedInstance(string pairId, out GameObject target)
        {
            target = null;
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId) || !pairId.StartsWith("rigpair-", StringComparison.Ordinal))
                return false;

            var registry = new RiggedObjectRegistry();
            var db = registry.Load();
            var record = registry.FindByManagedPairId(db, pairId);
            if (record == null)
            {
                var riggedObjectId = pairId.Substring("rigpair-".Length);
                record = registry.FindByRiggedObjectId(db, riggedObjectId);
            }

            var sceneObjectId = record?.managedInstance?.sceneObjectId;
            if (string.IsNullOrWhiteSpace(sceneObjectId))
                return false;
            if (!GlobalObjectId.TryParse(sceneObjectId, out var gid))
                return false;

            target = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid) as GameObject;
            return target != null;
        }
#endif

        private bool TryApplyHierarchy(SceneSyncHierarchyMessage msg, out string reason)
        {
            reason = null;
            NormalizeHierarchyPairIds(msg);
            if (msg == null || msg.type != "scene_sync.hierarchy" || string.IsNullOrWhiteSpace(msg.childPairId) || (!msg.clearParent && string.IsNullOrWhiteSpace(msg.parentPairId)))
            {
                reason = "invalid_payload";
                return false;
            }

            if (!TryGetOrRebindTarget(msg.childPairId, out var child, out _) || child == null)
            {
                reason = "child_not_mapped";
                return false;
            }

            if (msg.clearParent)
            {
                child.SetParent(null, true);
                return true;
            }

            if (!TryGetOrRebindTarget(msg.parentPairId, out var parent, out _) || parent == null)
            {
                reason = "parent_not_mapped";
                return false;
            }

            if (child == parent || parent.IsChildOf(child))
            {
                reason = "invalid_hierarchy_relation";
                return false;
            }

            child.SetParent(parent, true);
            return true;
        }

        private bool TryApplyVisibility(SceneSyncVisibilityMessage msg, out string reason)
        {
            reason = null;
            if (msg == null || msg.type != "scene_sync.visibility" || string.IsNullOrWhiteSpace(msg.pairId))
            {
                reason = "invalid_payload";
                return false;
            }

            if (!TryGetOrRebindTarget(msg.pairId, out var target, out _))
            {
                reason = "not_mapped";
                return false;
            }

            target.gameObject.SetActive(msg.visible);
            return true;
        }

        private bool TryApplyObjectName(SceneSyncObjectNameMessage msg, out string reason)
        {
            reason = null;
            if (msg == null || msg.type != "scene_sync.object_name" || string.IsNullOrWhiteSpace(msg.pairId))
            {
                reason = "invalid_payload";
                return false;
            }

            if (!TryGetOrRebindTarget(msg.pairId, out var target, out _))
            {
                reason = "not_mapped";
                return false;
            }

            target.name = string.IsNullOrWhiteSpace(msg.objectName) ? msg.pairId : msg.objectName;
            return true;
        }

        private bool TryGetMappedTargetOnly(string pairId, out Transform target)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
            {
                target = null;
                return false;
            }

            lock (_mappingLock)
            {
                return _mappedTargets.TryGetValue(pairId, out target);
            }
        }

        private bool TryMarkRebindObserved(string pairId)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
                return false;

            lock (_mappingLock)
            {
                return _rebindObservedPairs.Add(pairId);
            }
        }

        private static void NormalizePairIdField(SceneSyncTransformMessage msg)
        {
            if (msg != null) msg.pairId = NormalizePairId(msg.pairId);
        }

        private static void NormalizePairIdField(SceneSyncObjectNameMessage msg)
        {
            if (msg != null) msg.pairId = NormalizePairId(msg.pairId);
        }

        private static void NormalizePairIdField(SceneSyncVisibilityMessage msg)
        {
            if (msg != null) msg.pairId = NormalizePairId(msg.pairId);
        }

        private static void NormalizePairIdField(SceneSyncMeshUpdateMessage msg)
        {
            if (msg == null)
                return;
            msg.pairId = NormalizePairId(msg.pairId);
            msg.sourceHint = NormalizeOptional(msg.sourceHint);
            msg.rebuildReason = NormalizeOptional(msg.rebuildReason);
            msg.meshRef = NormalizeOptional(msg.meshRef);
            msg.meshContentFingerprint = NormalizeOptional(msg.meshContentFingerprint);
            msg.meshContentFingerprintNoUv = NormalizeOptional(msg.meshContentFingerprintNoUv);
            msg.meshContentFingerprintScope = NormalizeOptional(msg.meshContentFingerprintScope);
            msg.materialRefs = NormalizeStringArray(msg.materialRefs);
            NormalizeSubMeshes(msg.subMeshes);
        }

        private static void NormalizePairIdField(SceneSyncMeshUpdateBinaryMessage msg)
        {
            if (msg == null)
                return;
            msg.pairId = NormalizePairId(msg.pairId);
            msg.sourceHint = NormalizeOptional(msg.sourceHint);
            msg.rebuildReason = NormalizeOptional(msg.rebuildReason);
            msg.meshRef = NormalizeOptional(msg.meshRef);
            msg.meshContentFingerprint = NormalizeOptional(msg.meshContentFingerprint);
            msg.meshContentFingerprintNoUv = NormalizeOptional(msg.meshContentFingerprintNoUv);
            msg.meshContentFingerprintScope = NormalizeOptional(msg.meshContentFingerprintScope);
            msg.materialRefs = NormalizeStringArray(msg.materialRefs);
            NormalizeBuffers(msg.buffers);
            NormalizeSubMeshes(msg.subMeshes);
            NormalizeUvChannels(msg.uvChannels);
            NormalizeBuffer(msg.color0);
            NormalizeBlendShapeBuffers(msg.blendShapes);
        }

        private static void NormalizePairIdField(SceneSyncMeshPositionsUpdateMessage msg)
        {
            if (msg == null)
                return;
            msg.pairId = NormalizePairId(msg.pairId);
            msg.sourceHint = NormalizeOptional(msg.sourceHint);
            msg.meshRef = NormalizeOptional(msg.meshRef);
            NormalizeBuffer(msg.buffer);
        }

        private static void NormalizePairIdField(SceneSyncMeshUvUpdateMessage msg)
        {
            if (msg == null)
                return;
            msg.pairId = NormalizePairId(msg.pairId);
            msg.sourceHint = NormalizeOptional(msg.sourceHint);
            msg.meshRef = NormalizeOptional(msg.meshRef);
            NormalizeBuffer(msg.buffer);
        }

        private static void NormalizePairIdField(SceneSyncPreviewCommitMessage msg)
        {
            if (msg == null)
                return;
            msg.pairId = NormalizePairId(msg.pairId);
            msg.meshRef = NormalizeOptional(msg.meshRef);
        }

        private static void NormalizePairIdField(SceneSyncPreviewCommitMeshMessage msg)
        {
            if (msg == null)
                return;
            msg.pairId = NormalizePairId(msg.pairId);
            msg.meshRef = NormalizeOptional(msg.meshRef);
            msg.meshContentFingerprint = NormalizeOptional(msg.meshContentFingerprint);
            msg.meshContentFingerprintNoUv = NormalizeOptional(msg.meshContentFingerprintNoUv);
            msg.meshContentFingerprintScope = NormalizeOptional(msg.meshContentFingerprintScope);
            msg.materialRefs = NormalizeStringArray(msg.materialRefs);
            NormalizeBuffers(msg.buffers);
            NormalizeSubMeshes(msg.subMeshes);
            NormalizeBlendShapeBuffers(msg.blendShapes);
            NormalizeUvChannels(msg.uvChannels);
            NormalizeBuffer(msg.color0);
        }

        private static void NormalizePairIdField(SceneSyncBlendShapeWeightsMessage msg)
        {
            if (msg != null) msg.pairId = NormalizePairId(msg.pairId);
        }

        private static void NormalizeObjectAssemblyIds(SceneSyncObjectAssemblyMessage msg)
        {
            if (msg == null)
                return;
            msg.pairId = NormalizePairId(msg.pairId);
            msg.meshRef = NormalizeOptional(msg.meshRef);
            msg.materialRefs = NormalizeStringArray(msg.materialRefs);
        }

        private static void NormalizeMeshForkIds(SceneSyncMeshForkMessage msg)
        {
            if (msg == null)
                return;
            msg.pairId = NormalizePairId(msg.pairId);
            msg.sourceMeshRef = NormalizeOptional(msg.sourceMeshRef);
            msg.targetMeshRef = NormalizeOptional(msg.targetMeshRef);
        }

        private static void NormalizeReferenceChangeIds(SceneSyncReferenceChangeMessage msg)
        {
            if (msg == null)
                return;
            msg.pairId = NormalizePairId(msg.pairId);
            msg.resourceKind = NormalizeOptional(msg.resourceKind);
            msg.resourceRef = NormalizeOptional(msg.resourceRef);
            msg.resourceRefs = NormalizeStringArray(msg.resourceRefs);
        }

        private static void NormalizeObjectStatePairIds(SceneSyncObjectStateUpdateMessage msg)
        {
            if (msg?.objects == null)
                return;
            foreach (var obj in msg.objects)
            {
                if (obj == null)
                    continue;
                obj.pairId = NormalizePairId(obj.pairId);
                obj.parentPairId = NormalizePairId(obj.parentPairId);
            }
        }

        private static void NormalizeBuffers(SceneSyncBinaryBuffer[] buffers)
        {
            if (buffers == null)
                return;
            foreach (var buffer in buffers)
                NormalizeBuffer(buffer);
        }

        private static void NormalizeUvChannels(SceneSyncBinaryUvChannel[] channels)
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

        private static void NormalizeSubMeshes(SceneSyncMeshSubMesh[] subMeshes)
        {
            if (subMeshes == null)
                return;
            foreach (var subMesh in subMeshes)
            {
                if (subMesh == null)
                    continue;
                subMesh.topology = NormalizeOptional(subMesh.topology);
            }
        }

        private static void NormalizeBlendShapeBuffers(SceneSyncBinaryBlendShape[] blendShapes)
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

        private static void NormalizeBuffer(SceneSyncBinaryBuffer buffer)
        {
            if (buffer == null)
                return;
            buffer.semantic = NormalizeOptional(buffer.semantic);
            buffer.format = NormalizeOptional(buffer.format);
            buffer.path = NormalizeOptional(buffer.path);
        }

        private static void NormalizeMaterialContentV1(SceneSyncMaterialContentV1Message msg)
        {
            if (msg == null)
                return;

            msg.schema = NormalizeOptional(msg.schema);
            msg.materialRef = NormalizeOptional(msg.materialRef);
            if (msg.source != null)
            {
                msg.source.name = NormalizeOptional(msg.source.name);
                msg.source.blenderMaterialName = NormalizeOptional(msg.source.blenderMaterialName);
            }
            if (msg.shader != null)
            {
                msg.shader.policy = NormalizeOptional(msg.shader.policy);
                msg.shader.target = NormalizeOptional(msg.shader.target);
            }
            if (msg.properties != null)
                msg.properties.alphaModeHint = NormalizeOptional(msg.properties.alphaModeHint);
            NormalizeMaterialTextures(msg.textures);
            if (msg.fingerprint != null)
            {
                msg.fingerprint.contentHash = NormalizeOptional(msg.fingerprint.contentHash);
                msg.fingerprint.textureDependencyHash = NormalizeOptional(msg.fingerprint.textureDependencyHash);
            }
            NormalizeMaterialWarnings(msg.warnings);
        }

        private static void NormalizeMaterialTextures(MaterialContentV1Textures textures)
        {
            if (textures == null)
                return;
            NormalizeMaterialTexture(textures.baseColor);
            NormalizeMaterialTexture(textures.normal);
            NormalizeMaterialTexture(textures.metallic);
            NormalizeMaterialTexture(textures.roughness);
            NormalizeMaterialTexture(textures.occlusion);
            NormalizeMaterialTexture(textures.height);
            NormalizeMaterialTexture(textures.alpha);
            NormalizeMaterialTexture(textures.emission);
        }

        private static void NormalizeMaterialTexture(MaterialContentV1Texture texture)
        {
            if (texture == null)
                return;
            texture.textureRef = NormalizeOptional(texture.textureRef);
            texture.sourcePath = NormalizeOptional(texture.sourcePath);
            texture.imageName = NormalizeOptional(texture.imageName);
            texture.fileName = NormalizeOptional(texture.fileName);
            texture.colorSpace = NormalizeOptional(texture.colorSpace);
            texture.blenderColorSpace = NormalizeOptional(texture.blenderColorSpace);
            texture.usage = NormalizeOptional(texture.usage);
            texture.sourceKind = NormalizeOptional(texture.sourceKind);
            texture.sourceChannel = NormalizeUpperOptional(texture.sourceChannel);
            texture.extension = NormalizeUpperOptional(texture.extension);
            texture.interpolation = NormalizeUpperOptional(texture.interpolation);
            texture.projection = NormalizeUpperOptional(texture.projection);
            texture.normalSpace = NormalizeUpperOptional(texture.normalSpace);
        }

        private static void NormalizeMaterialWarnings(MaterialContentV1Warning[] warnings)
        {
            if (warnings == null)
                return;
            foreach (var warning in warnings)
            {
                if (warning == null)
                    continue;
                warning.code = NormalizeOptional(warning.code);
                warning.slot = NormalizeOptional(warning.slot);
                warning.imageName = NormalizeOptional(warning.imageName);
                warning.message = NormalizeOptional(warning.message);
            }
        }

        private static void NormalizeHierarchyPairIds(SceneSyncHierarchyMessage msg)
        {
            if (msg == null)
                return;
            msg.childPairId = NormalizePairId(msg.childPairId);
            msg.parentPairId = NormalizePairId(msg.parentPairId);
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

        private static string NormalizeUpperOptional(string value)
        {
            var normalized = NormalizeOptional(value);
            return normalized != null ? normalized.ToUpperInvariant() : null;
        }

        private static string NormalizePairId(string pairId)
        {
            var normalized = pairId?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

    }
}
