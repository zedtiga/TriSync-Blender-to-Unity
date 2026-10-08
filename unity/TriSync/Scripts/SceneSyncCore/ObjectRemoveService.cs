using System;
using System.Collections.Generic;
using BlenderSyncVNext;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class ObjectRemoveService
    {
        public bool TryRemove(SceneSyncCoordinator coordinator, SceneSyncObjectRemoveMessage msg, out string error)
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
            var pairIds = NormalizePairIds(msg.removedPairIds);
            if (pairIds.Length == 0)
            {
                error = "removed_pair_ids_empty";
                return false;
            }
            msg.removedPairIds = pairIds;

            var requested = pairIds.Length;
            var removed = 0;
            var missing = 0;
            var unregisterPairIds = new List<string>();
            foreach (var pairId in pairIds)
            {
                ObjectAssemblyService.CancelPending(pairId);

                if (coordinator.TryGetOrRebindMappedTarget(pairId, out var target) && target != null)
                {
#if UNITY_EDITOR
                    UnityEngine.Object.DestroyImmediate(target.gameObject);
#else
                    UnityEngine.Object.Destroy(target.gameObject);
#endif
                    removed++;
                }
                else
                {
                    missing++;
                }

                MeshApplyService.ClearPairMeshBinding(pairId);
                unregisterPairIds.Add(pairId);
            }

            var unregisterResult = new RegistryUnregisterService().Unregister(Array.Empty<string>(), unregisterPairIds);
            BlenderSyncReportStore.Add(
                "Object Remove",
                missing > 0 ? "WARN" : "OK",
                $"removed={removed} missing={missing} requested={requested} unregistered={unregisterResult.removedObjects} rigged={unregisterResult.removedRiggedObjects}",
                new Dictionary<string, object>
                {
                    { "removed", removed },
                    { "missing", missing },
                    { "requested", requested },
                    { "unregisteredObjects", unregisterResult.removedObjects },
                    { "unregisteredRiggedObjects", unregisterResult.removedRiggedObjects },
                });
            return true;
        }

        private static string[] NormalizePairIds(string[] pairIds)
        {
            if (pairIds == null || pairIds.Length == 0)
                return Array.Empty<string>();

            var normalized = new List<string>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var pairId in pairIds)
            {
                var value = pairId?.Trim();
                if (string.IsNullOrEmpty(value) || !seen.Add(value))
                    continue;
                normalized.Add(value);
            }
            return normalized.ToArray();
        }
    }
}
