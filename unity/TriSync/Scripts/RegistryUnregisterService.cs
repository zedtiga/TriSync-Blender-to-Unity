using System;
using System.Collections.Generic;
using System.Linq;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.SceneSyncCore;
using BlenderSyncVNext.SessionCore;

namespace BlenderSyncVNext
{
    public sealed class RegistryUnregisterResult
    {
        public int removedResources;
        public int removedObjects;
        public int removedRiggedObjects;
    }

    public sealed class RegistryUnregisterService
    {
        public RegistryUnregisterResult Unregister(IEnumerable<string> assetIds, IEnumerable<string> pairIds)
        {
            using (RegistryFileStore.BeginUnityAssetWriteBatch())
            {
                var selectedAssetIds = NormalizeSet(assetIds);
                var selectedPairIds = NormalizeSet(pairIds);
                var result = new RegistryUnregisterResult();

                if (selectedAssetIds.Count > 0)
                    RemoveResourceRecords(selectedAssetIds, result);
                if (selectedPairIds.Count > 0)
                    RemoveObjectRecords(selectedPairIds, result);
                if (selectedPairIds.Count > 0)
                    RemoveRiggedRecords(selectedPairIds, result);

                return result;
            }
        }

        private static void RemoveResourceRecords(HashSet<string> selectedAssetIds, RegistryUnregisterResult result)
        {
            var repository = new AptRepository();
            var db = repository.Load();
            var kept = new List<AptRecord>();
            foreach (var record in db.records ?? Array.Empty<AptRecord>())
            {
                var assetId = NormalizeId(record?.assetId);
                if (string.IsNullOrWhiteSpace(assetId) || !selectedAssetIds.Contains(assetId))
                {
                    if (record != null)
                        kept.Add(record);
                    continue;
                }

                result.removedResources++;
                ResourceRuntimeMapping.Remove(assetId);
            }

            db.records = kept.ToArray();
            repository.Save(db);
        }

        private static void RemoveObjectRecords(HashSet<string> selectedPairIds, RegistryUnregisterResult result)
        {
            var bindingRegistry = new ObjectBindingRegistry();
            var objectDb = bindingRegistry.Load();
            var kept = new List<ObjectBindingEntry>();
            foreach (var record in objectDb.records ?? Array.Empty<ObjectBindingEntry>())
            {
                var pairId = NormalizeId(record?.pairId);
                if (string.IsNullOrWhiteSpace(pairId) || !selectedPairIds.Contains(pairId))
                {
                    if (record != null)
                        kept.Add(record);
                    continue;
                }

                result.removedObjects++;
                SessionClient.UnregisterMappedTarget(pairId);
            }

            objectDb.records = kept.ToArray();
            bindingRegistry.SaveImmediate(objectDb);
            RemoveObjectReferencesFromResources(selectedPairIds);
        }

        private static void RemoveRiggedRecords(HashSet<string> selectedPairIds, RegistryUnregisterResult result)
        {
            var registry = new RiggedObjectRegistry();
            var db = registry.Load();
            var kept = new List<RiggedObjectRecord>();
            foreach (var record in db.records ?? Array.Empty<RiggedObjectRecord>())
            {
                var riggedObjectId = NormalizeId(record?.riggedObjectId);
                var managedPairId = NormalizeId(record?.managedInstance?.pairId);
                var implicitPairId = string.IsNullOrWhiteSpace(riggedObjectId) ? null : "rigpair-" + riggedObjectId;
                var remove = (!string.IsNullOrWhiteSpace(managedPairId) && selectedPairIds.Contains(managedPairId))
                    || (!string.IsNullOrWhiteSpace(implicitPairId) && selectedPairIds.Contains(implicitPairId));
                if (!remove)
                {
                    if (record != null)
                        kept.Add(record);
                    continue;
                }

                result.removedRiggedObjects++;
            }

            db.records = kept.ToArray();
            registry.Save(db);
        }

        private static void RemoveObjectReferencesFromResources(HashSet<string> pairIds)
        {
            if (pairIds == null || pairIds.Count == 0)
                return;

            var repository = new AptRepository();
            var db = repository.Load();
            var changed = false;
            foreach (var record in db.records ?? Array.Empty<AptRecord>())
            {
                if (record == null || record.referencedBy == null || record.referencedBy.Length == 0)
                    continue;
                var filtered = record.referencedBy
                    .Where(r => r != null && !pairIds.Contains(NormalizeId(r.pairId)))
                    .ToArray();
                if (filtered.Length == record.referencedBy.Length)
                    continue;
                record.referencedBy = filtered;
                changed = true;
            }

            if (changed)
                repository.Save(db);
        }

        private static HashSet<string> NormalizeSet(IEnumerable<string> values)
        {
            return new HashSet<string>((values ?? Array.Empty<string>())
                .Select(NormalizeId)
                .Where(v => !string.IsNullOrWhiteSpace(v)), StringComparer.Ordinal);
        }

        private static string NormalizeId(string id)
        {
            var normalized = id?.Trim();
            return string.IsNullOrWhiteSpace(normalized) ? null : normalized;
        }
    }
}
