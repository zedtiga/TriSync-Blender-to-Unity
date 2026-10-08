using System;
using System.Collections.Generic;
using System.Linq;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.SessionCore;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class ObjectResourceLinkRegistry
    {
        private readonly ObjectBindingRegistry _bindingRegistry = new ObjectBindingRegistry();
        private readonly AptRepository _aptRepository = new AptRepository();

        public void UpsertFromAssembly(string pairId, GameObject target, string meshRef, IReadOnlyList<string> materialRefs)
        {
#if UNITY_EDITOR
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId) || target == null)
                return;

            var objectDb = _bindingRegistry.Load();
            var existing = _bindingRegistry.FindByPairId(objectDb, pairId);
            var oldMeshRef = existing?.meshRef;
            var oldMaterialRefs = existing?.materialRefs ?? Array.Empty<string>();

            if (!_bindingRegistry.TryCaptureBinding(pairId, target, out var entry) || entry == null)
                return;

            var aptDb = _aptRepository.Load();
            var slotMaterialRefs = (materialRefs ?? Array.Empty<string>())
                .Select(r => (r ?? string.Empty).Trim())
                .ToArray();

            entry.meshRef = string.IsNullOrWhiteSpace(meshRef) ? null : meshRef.Trim();
            entry.materialRefs = slotMaterialRefs;
            entry.meshAssetGuid = ResolveAssetGuid(aptDb, entry.meshRef);
            entry.materialAssetGuids = ResolveAssetGuids(aptDb, slotMaterialRefs);
            entry.bindingState = "bound";

            RemoveObjectRefsFromResources(aptDb, pairId, entry.sceneObjectId, oldMeshRef, oldMaterialRefs);
            AddObjectRefsToResources(aptDb, pairId, entry.sceneObjectId, entry.meshRef, entry.materialRefs);
            PushMeshUsageUpdates(aptDb, oldMeshRef, entry.meshRef);

            _bindingRegistry.UpsertBinding(objectDb, entry);
            _bindingRegistry.Save(objectDb);
            _aptRepository.Save(aptDb);
#endif
        }

        public void UpdateReference(string pairId, GameObject target, string resourceKind, string resourceRef, IReadOnlyList<string> resourceRefs = null)
        {
#if UNITY_EDITOR
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId) || target == null || string.IsNullOrWhiteSpace(resourceKind))
                return;

            var objectDb = _bindingRegistry.Load();
            var entry = _bindingRegistry.FindByPairId(objectDb, pairId);
            if (entry == null)
            {
                if (!_bindingRegistry.TryCaptureBinding(pairId, target, out entry) || entry == null)
                    return;
            }
            else if (_bindingRegistry.TryCaptureBinding(pairId, target, out var refreshedEntry) && refreshedEntry != null)
            {
                entry.sceneObjectId = refreshedEntry.sceneObjectId;
                entry.bindingState = refreshedEntry.bindingState;
            }

            var aptDb = _aptRepository.Load();
            var sceneObjectId = entry.sceneObjectId;

            var normalizedKind = (resourceKind ?? string.Empty).Trim();
            if (string.Equals(normalizedKind, "mesh", StringComparison.OrdinalIgnoreCase))
            {
                if (string.IsNullOrWhiteSpace(resourceRef))
                    return;
                var oldMeshRef = entry.meshRef;
                RemoveReferencedBy(aptDb, entry.meshRef, pairId);
                entry.meshRef = resourceRef.Trim();
                entry.meshAssetGuid = ResolveAssetGuid(aptDb, entry.meshRef);
                AddReferencedBy(aptDb, entry.meshRef, pairId, sceneObjectId);
                PushMeshUsageUpdates(aptDb, oldMeshRef, entry.meshRef);
            }
            else if (string.Equals(normalizedKind, "material", StringComparison.OrdinalIgnoreCase))
            {
                var newMaterialRefs = (resourceRefs ?? Array.Empty<string>())
                    .Select(r => (r ?? string.Empty).Trim())
                    .ToArray();
                if (newMaterialRefs.Length == 0 && !string.IsNullOrWhiteSpace(resourceRef))
                    newMaterialRefs = new[] { resourceRef.Trim() };
                if (newMaterialRefs.Length == 0 && resourceRefs == null)
                    return;

                foreach (var oldRef in entry.materialRefs ?? Array.Empty<string>())
                    RemoveReferencedBy(aptDb, oldRef, pairId);

                entry.materialRefs = newMaterialRefs;
                entry.materialAssetGuids = ResolveAssetGuids(aptDb, entry.materialRefs);
                foreach (var materialRef in entry.materialRefs)
                    AddReferencedBy(aptDb, materialRef, pairId, sceneObjectId);
            }
            else
            {
                return;
            }

            entry.bindingState = "bound";
            _bindingRegistry.UpsertBinding(objectDb, entry);
            _bindingRegistry.Save(objectDb);
            _aptRepository.Save(aptDb);
#endif
        }

        private static string ResolveAssetGuid(AptDatabase db, string assetRef)
        {
            var normalizedRef = NormalizeAssetRef(assetRef);
            if (string.IsNullOrWhiteSpace(normalizedRef))
                return null;
            var rec = new AptRepository().FindByAssetId(db, normalizedRef);
            return rec?.target?.assetGuid;
        }

        private static string[] ResolveAssetGuids(AptDatabase db, IEnumerable<string> assetRefs)
        {
            if (assetRefs == null)
                return Array.Empty<string>();
            var guids = new List<string>();
            var repository = new AptRepository();
            foreach (var assetRef in assetRefs)
            {
                var normalizedRef = NormalizeAssetRef(assetRef);
                if (string.IsNullOrWhiteSpace(normalizedRef))
                {
                    guids.Add(string.Empty);
                    continue;
                }
                var rec = repository.FindByAssetId(db, normalizedRef);
                var guid = rec?.target?.assetGuid;
                guids.Add(guid ?? string.Empty);
            }
            return guids.ToArray();
        }

        private void RemoveObjectRefsFromResources(AptDatabase db, string pairId, string sceneObjectId, string meshRef, IEnumerable<string> materialRefs)
        {
            RemoveReferencedBy(db, meshRef, pairId);
            foreach (var materialRef in materialRefs ?? Array.Empty<string>())
                RemoveReferencedBy(db, materialRef, pairId);
        }

        private void AddObjectRefsToResources(AptDatabase db, string pairId, string sceneObjectId, string meshRef, IEnumerable<string> materialRefs)
        {
            AddReferencedBy(db, meshRef, pairId, sceneObjectId);
            foreach (var materialRef in materialRefs ?? Array.Empty<string>())
                AddReferencedBy(db, materialRef, pairId, sceneObjectId);
        }

        private void AddReferencedBy(AptDatabase db, string assetId, string pairId, string sceneObjectId)
        {
            var normalizedAssetId = NormalizeAssetRef(assetId);
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(normalizedAssetId) || string.IsNullOrWhiteSpace(pairId))
                return;

            var rec = _aptRepository.FindByAssetId(db, normalizedAssetId);
            if (rec == null)
                return;

            var refs = (rec.referencedBy ?? Array.Empty<AptReferenceRef>()).ToList();
            var changed = false;
            var existing = refs.FirstOrDefault(r => r != null && string.Equals(NormalizePairId(r.pairId), pairId, StringComparison.Ordinal));
            if (existing == null)
            {
                refs.Add(new AptReferenceRef { pairId = pairId, sceneObjectId = sceneObjectId });
                changed = true;
            }
            else if (!string.Equals(existing.sceneObjectId, sceneObjectId, StringComparison.Ordinal))
            {
                existing.sceneObjectId = sceneObjectId;
                changed = true;
            }

            if (!changed)
                return;

            rec.referencedBy = refs.Where(r => r != null).ToArray();
            _aptRepository.Upsert(db, rec);
        }

        private void RemoveReferencedBy(AptDatabase db, string assetId, string pairId)
        {
            var normalizedAssetId = NormalizeAssetRef(assetId);
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(normalizedAssetId) || string.IsNullOrWhiteSpace(pairId))
                return;

            var rec = _aptRepository.FindByAssetId(db, normalizedAssetId);
            if (rec == null)
                return;

            rec.referencedBy = (rec.referencedBy ?? Array.Empty<AptReferenceRef>())
                .Where(r => r != null && !string.Equals(NormalizePairId(r.pairId), pairId, StringComparison.Ordinal))
                .ToArray();
            _aptRepository.Upsert(db, rec);
        }

        private static string NormalizePairId(string pairId)
        {
            var normalized = pairId?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

        private static string NormalizeAssetRef(string assetRef)
        {
            return (assetRef ?? string.Empty).Trim();
        }

        private void PushMeshUsageUpdates(AptDatabase db, params string[] meshRefs)
        {
#if UNITY_EDITOR
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var rawRef in meshRefs ?? Array.Empty<string>())
            {
                var meshRef = NormalizeAssetRef(rawRef);
                if (string.IsNullOrWhiteSpace(meshRef) || !seen.Add(meshRef))
                    continue;

                var rec = _aptRepository.FindByAssetId(db, meshRef);
                var refs = (rec?.referencedBy ?? Array.Empty<AptReferenceRef>())
                    .Where(r => r != null && !string.IsNullOrWhiteSpace(r.pairId))
                    .GroupBy(r => NormalizePairId(r.pairId))
                    .Where(g => !string.IsNullOrWhiteSpace(g.Key))
                    .Select(g => g.First().pairId.Trim())
                    .ToArray();
                var payload = new MeshRefUsagePayload
                {
                    type = "scene_sync.mesh_ref_usage_v1",
                    timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                    meshRef = meshRef,
                    refCount = refs.Length,
                    isShared = refs.Length > 1,
                    pairs = refs,
                };
                SessionClient.SendToBlender(JsonUtility.ToJson(payload));
            }
#endif
        }

        [Serializable]
        private sealed class MeshRefUsagePayload
        {
            public string type;
            public long timestamp;
            public string meshRef;
            public int refCount;
            public bool isShared;
            public string[] pairs;
        }
    }
}
