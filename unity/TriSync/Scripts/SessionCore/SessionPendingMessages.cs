using System;
using System.Collections.Generic;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.Protocol;
using BlenderSyncVNext.SceneSyncCore;
using UnityEngine;

namespace BlenderSyncVNext.SessionCore
{
    internal static class SessionPendingMessages
    {
        private static readonly LatestByKeyBuffer PendingTransformRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingHierarchyRawByChild = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingObjectNameRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingVisibilityRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingAutoSyncStateRaw = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingViewStateRaw = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingMeshForkRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingMeshRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingMeshBinaryRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingMeshPositionsRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingMeshUvRawByPair = new LatestByKeyBuffer();
        private static readonly RawQueueBuffer PendingObjectStateRaw = new RawQueueBuffer();
        private static readonly RawQueueBuffer PendingObjectStateAfterMeshRaw = new RawQueueBuffer();
        private static readonly LatestByKeyBuffer PendingReferenceRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingBlendShapeWeightsRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingPreviewCommitRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingPreviewCommitMeshRawByPair = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingMaterialContentV1RawByRef = new LatestByKeyBuffer();
        private static readonly RawQueueBuffer PendingObjectRemoveRaw = new RawQueueBuffer();
        private static readonly RawQueueBuffer PendingObjectAssemblyRaw = new RawQueueBuffer();
        private static readonly RawQueueBuffer PendingAssetEnvelopeRaw = new RawQueueBuffer();
        private static readonly LatestByKeyBuffer PendingRiggedObjectRawById = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingRiggedPoseRawById = new LatestByKeyBuffer();
        private static readonly LatestByKeyBuffer PendingRiggedBlendShapeWeightsRawById = new LatestByKeyBuffer();

        public static void EnqueueTransformRaw(string rawJson) =>
            PendingTransformRawByPair.Enqueue<SceneSyncTransformMessage>(rawJson, env => env.pairId);

        public static void EnqueueHierarchyRaw(string rawJson) =>
            PendingHierarchyRawByChild.Enqueue<SceneSyncHierarchyMessage>(rawJson, env => env.childPairId);

        public static void EnqueueObjectNameRaw(string rawJson) =>
            PendingObjectNameRawByPair.Enqueue<SceneSyncObjectNameMessage>(rawJson, env => env.pairId);

        public static void EnqueueVisibilityRaw(string rawJson) =>
            PendingVisibilityRawByPair.Enqueue<SceneSyncVisibilityMessage>(rawJson, env => env.pairId);

        public static void EnqueueAutoSyncStateRaw(string rawJson) =>
            PendingAutoSyncStateRaw.Enqueue<SceneSyncAutoSyncStateMessage>(rawJson, _ => "auto_sync_state");

        public static void EnqueueViewStateRaw(string rawJson) =>
            PendingViewStateRaw.Enqueue<SceneSyncViewStateMessage>(rawJson, _ => "view_state");

        public static void EnqueueMeshForkRaw(string rawJson) =>
            PendingMeshForkRawByPair.Enqueue<SceneSyncMeshForkMessage>(rawJson, env => env.pairId);

        public static void EnqueueMeshRaw(string rawJson) =>
            PendingMeshRawByPair.Enqueue<SceneSyncMeshUpdateMessage>(rawJson, env => env.pairId);

        public static void EnqueueMeshBinaryRaw(string rawJson) =>
            PendingMeshBinaryRawByPair.Enqueue<SceneSyncMeshUpdateBinaryMessage>(rawJson, env => env.pairId);

        public static void EnqueueMeshPositionsRaw(string rawJson) =>
            PendingMeshPositionsRawByPair.Enqueue<SceneSyncMeshPositionsUpdateMessage>(rawJson, env => env.pairId);

        public static void EnqueueMeshUvRaw(string rawJson) =>
            PendingMeshUvRawByPair.Enqueue<SceneSyncMeshUvUpdateMessage>(rawJson, env => env.pairId);

        public static void EnqueueObjectStateRaw(string rawJson)
        {
            if (IsAfterMeshObjectState(rawJson))
                PendingObjectStateAfterMeshRaw.Enqueue(rawJson);
            else
                PendingObjectStateRaw.Enqueue(rawJson);
        }

        public static void EnqueueReferenceRaw(string rawJson) =>
            PendingReferenceRawByPair.Enqueue<SceneSyncReferenceChangeMessage>(rawJson, BuildReferenceChangeKey);

        public static void EnqueueBlendShapeWeightsRaw(string rawJson) =>
            PendingBlendShapeWeightsRawByPair.Enqueue<SceneSyncBlendShapeWeightsMessage>(rawJson, env => env.pairId);

        public static void EnqueuePreviewCommitRaw(string rawJson) =>
            PendingPreviewCommitRawByPair.Enqueue<SceneSyncPreviewCommitMessage>(rawJson, env => env.pairId);

        public static void EnqueuePreviewCommitMeshRaw(string rawJson) =>
            PendingPreviewCommitMeshRawByPair.Enqueue<SceneSyncPreviewCommitMeshMessage>(rawJson, env => env.pairId);

        public static void EnqueueMaterialContentV1Raw(string rawJson) =>
            PendingMaterialContentV1RawByRef.Enqueue<SceneSyncMaterialContentV1Message>(rawJson, env => env.materialRef);

        public static void EnqueueObjectRemoveRaw(string rawJson) =>
            PendingObjectRemoveRaw.Enqueue(rawJson);

        public static void EnqueueObjectAssemblyRaw(string rawJson) =>
            PendingObjectAssemblyRaw.Enqueue(rawJson);

        public static void EnqueueAssetEnvelopeRaw(string rawJson) =>
            PendingAssetEnvelopeRaw.Enqueue(rawJson);

        public static void EnqueueRiggedObjectRaw(string rawJson) =>
            PendingRiggedObjectRawById.Enqueue<RiggedObjectEnvelope>(rawJson, env => env.riggedObject?.riggedObjectId);

        public static void EnqueueRiggedPoseRaw(string rawJson) =>
            PendingRiggedPoseRawById.Enqueue<RiggedPoseEnvelope>(rawJson, env => env.riggedObjectId);

        public static void EnqueueRiggedBlendShapeWeightsRaw(string rawJson) =>
            PendingRiggedBlendShapeWeightsRawById.Enqueue<RiggedBlendShapeWeightsEnvelope>(rawJson, env => env.riggedObjectId);

        public static void Clear()
        {
            PendingTransformRawByPair.Clear();
            PendingHierarchyRawByChild.Clear();
            PendingObjectNameRawByPair.Clear();
            PendingVisibilityRawByPair.Clear();
            PendingAutoSyncStateRaw.Clear();
            PendingViewStateRaw.Clear();
            PendingMeshForkRawByPair.Clear();
            PendingMeshRawByPair.Clear();
            PendingMeshBinaryRawByPair.Clear();
            PendingMeshPositionsRawByPair.Clear();
            PendingMeshUvRawByPair.Clear();
            PendingObjectStateRaw.Clear();
            PendingObjectStateAfterMeshRaw.Clear();
            PendingReferenceRawByPair.Clear();
            PendingBlendShapeWeightsRawByPair.Clear();
            PendingPreviewCommitRawByPair.Clear();
            PendingPreviewCommitMeshRawByPair.Clear();
            PendingMaterialContentV1RawByRef.Clear();
            PendingObjectRemoveRaw.Clear();
            PendingObjectAssemblyRaw.Clear();
            PendingAssetEnvelopeRaw.Clear();
            PendingRiggedObjectRawById.Clear();
            PendingRiggedPoseRawById.Clear();
            PendingRiggedBlendShapeWeightsRawById.Clear();
        }

        public static bool Pump(SceneSyncCoordinator coordinator)
        {
            if (coordinator == null)
                return false;

            var handledAny = false;

            handledAny |= DrainAndHandle(PendingAssetEnvelopeRaw.Drain(), raw => new AssetImportCoordinator().HandleEnvelope(raw));
            handledAny |= AssetImportCoordinator.TryProcessPendingNow();
            var handledMaterialContent = DrainAndHandle(PendingMaterialContentV1RawByRef.Drain(), raw => coordinator.HandleMaterialContentV1Raw(raw, sessionActive: true));
            handledAny |= handledMaterialContent;
            if (handledMaterialContent)
                RiggedObjectImportCoordinator.TryProcessPendingNow();
            handledAny |= DrainAndHandle(PendingRiggedObjectRawById.Drain(), raw => new RiggedObjectImportCoordinator().HandleEnvelope(raw));
            handledAny |= DrainAndHandle(PendingRiggedPoseRawById.Drain(), raw => new RiggedPoseApplyService().HandleEnvelope(raw));
            handledAny |= DrainAndHandle(PendingRiggedBlendShapeWeightsRawById.Drain(), raw => new RiggedBlendShapeWeightsApplyService().HandleEnvelope(raw));
            handledAny |= DrainAndHandle(PendingObjectAssemblyRaw.Drain(), raw => coordinator.HandleObjectAssemblyRaw(raw, sessionActive: true));
            handledAny |= ObjectAssemblyService.TryProcessPendingNow(coordinator) > 0;
            handledAny |= DrainAndHandle(PendingHierarchyRawByChild.Drain(), raw => coordinator.HandleHierarchyRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingObjectNameRawByPair.Drain(), raw => coordinator.HandleObjectNameRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingVisibilityRawByPair.Drain(), raw => coordinator.HandleVisibilityRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingAutoSyncStateRaw.Drain(), raw => coordinator.HandleAutoSyncStateRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingViewStateRaw.Drain(), raw => coordinator.HandleViewStateRaw(raw, sessionActive: true));

            handledAny |= DrainAndHandle(PendingObjectStateRaw.Drain(), raw => coordinator.HandleObjectStateUpdateRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingTransformRawByPair.Drain(), raw => coordinator.HandleTransformRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingMeshForkRawByPair.Drain(), raw => coordinator.HandleMeshForkRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingMeshRawByPair.Drain(), raw => coordinator.HandleMeshUpdateRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingMeshBinaryRawByPair.Drain(), raw => coordinator.HandleMeshUpdateBinaryRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingMeshPositionsRawByPair.Drain(), raw => coordinator.HandleMeshPositionsUpdateRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingMeshUvRawByPair.Drain(), raw => coordinator.HandleMeshUvUpdateRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingObjectStateAfterMeshRaw.Drain(), raw => coordinator.HandleObjectStateUpdateRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingPreviewCommitRawByPair.Drain(), raw => coordinator.HandlePreviewCommitRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingPreviewCommitMeshRawByPair.Drain(), raw => coordinator.HandlePreviewCommitMeshRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingReferenceRawByPair.Drain(), raw => coordinator.HandleReferenceChangeRaw(raw, sessionActive: true));
            handledAny |= DrainAndHandle(PendingBlendShapeWeightsRawByPair.Drain(), raw => coordinator.HandleBlendShapeWeightsRaw(raw, sessionActive: true));

            handledAny |= DrainAndHandle(PendingObjectRemoveRaw.Drain(), raw => coordinator.HandleObjectRemoveRaw(raw, sessionActive: true));

            return handledAny;
        }

        private sealed class LatestByKeyBuffer
        {
            private const string InvalidPayloadKey = "__invalid_payload__";
            private readonly Dictionary<string, string> _pending = new Dictionary<string, string>();
            private readonly object _gate = new object();

            public void Enqueue<TEnvelope>(string rawJson, Func<TEnvelope, string> getKey) where TEnvelope : class
            {
                if (string.IsNullOrWhiteSpace(rawJson))
                    return;

                TEnvelope env;
                try
                {
                    env = JsonUtility.FromJson<TEnvelope>(rawJson);
                }
                catch
                {
                    EnqueueWithKey(InvalidPayloadKey, rawJson);
                    return;
                }
                var key = env == null ? null : getKey(env)?.Trim();
                if (string.IsNullOrWhiteSpace(key))
                {
                    EnqueueWithKey(InvalidPayloadKey, rawJson);
                    return;
                }

                EnqueueWithKey(key, rawJson);
            }

            private void EnqueueWithKey(string key, string rawJson)
            {
                lock (_gate)
                {
                    _pending[key] = rawJson;
                }
            }

            public string[] Drain()
            {
                lock (_gate)
                {
                    var batch = new string[_pending.Count];
                    var index = 0;
                    foreach (var rawJson in _pending.Values)
                        batch[index++] = rawJson;
                    _pending.Clear();
                    return batch;
                }
            }

            public void Clear()
            {
                lock (_gate)
                    _pending.Clear();
            }
        }

        private sealed class RawQueueBuffer
        {
            private readonly Queue<string> _pending = new Queue<string>();
            private readonly object _gate = new object();

            public void Enqueue(string rawJson)
            {
                if (string.IsNullOrWhiteSpace(rawJson))
                    return;

                lock (_gate)
                {
                    _pending.Enqueue(rawJson);
                }
            }

            public string[] Drain()
            {
                lock (_gate)
                {
                    var batch = _pending.ToArray();
                    _pending.Clear();
                    return batch;
                }
            }

            public void Clear()
            {
                lock (_gate)
                    _pending.Clear();
            }
        }

        private static bool Handle(string rawJson, Action<string> handler)
        {
            if (string.IsNullOrWhiteSpace(rawJson))
                return false;

            try
            {
                handler(rawJson);
                return true;
            }
            catch (Exception ex)
            {
                BlenderSyncLog.Exception(
                    "Session",
                    "pending_message_failed",
                    ex,
                    "A queued TriSync message failed during main-thread processing.");
                return false;
            }
        }

        private static bool DrainAndHandle(string[] batch, Action<string> handler)
        {
            var handledAny = false;
            foreach (var rawJson in batch)
                handledAny |= Handle(rawJson, handler);
            return handledAny;
        }

        private static bool IsAfterMeshObjectState(string rawJson)
        {
            if (string.IsNullOrWhiteSpace(rawJson))
                return false;

            try
            {
                var env = JsonUtility.FromJson<SceneSyncObjectStateUpdateMessage>(rawJson);
                return env != null
                    && !string.IsNullOrWhiteSpace(env.sourceHint)
                    && env.sourceHint.Trim().StartsWith("after_mesh_preview:", StringComparison.OrdinalIgnoreCase);
            }
            catch
            {
                return false;
            }
        }

        private static string BuildReferenceChangeKey(SceneSyncReferenceChangeMessage env)
        {
            var pairId = env != null ? env.pairId?.Trim() : null;
            if (string.IsNullOrWhiteSpace(pairId))
                return null;

            var resourceKind = env.resourceKind?.Trim();
            return string.IsNullOrWhiteSpace(resourceKind) ? pairId : pairId + "|" + resourceKind;
        }
    }
}
