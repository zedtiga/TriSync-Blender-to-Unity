using System;
using System.Collections.Generic;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public static class ResourceRuntimeMapping
    {
        private static readonly Dictionary<string, ResourceRuntimeEntry> Entries = new Dictionary<string, ResourceRuntimeEntry>();
        private static readonly object EntriesLock = new object();

        public static bool TryGet(string assetId, out ResourceRuntimeEntry entry)
        {
            entry = null;
            var key = NormalizeAssetId(assetId);
            if (string.IsNullOrWhiteSpace(key))
                return false;

            lock (EntriesLock)
            {
                if (!Entries.TryGetValue(key, out var cached))
                    return false;
                entry = Clone(cached);
                return true;
            }
        }

        public static void PutResolved(string assetId, string resourceType, string assetGuid, string assetPath, string sourceFingerprint, UnityEngine.Object loadedObject)
        {
            var key = NormalizeAssetId(assetId);
            if (string.IsNullOrWhiteSpace(key) || loadedObject == null)
                return;

            var entry = new ResourceRuntimeEntry
            {
                assetId = key,
                resourceType = resourceType,
                assetGuid = assetGuid,
                assetPath = assetPath,
                sourceFingerprint = sourceFingerprint,
                loadedObject = loadedObject,
                state = "resolved",
                resolvedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            };
            lock (EntriesLock)
            {
                Entries[key] = entry;
            }
        }

        public static void MarkStale(string assetId)
        {
            var key = NormalizeAssetId(assetId);
            if (string.IsNullOrWhiteSpace(key)) return;
            lock (EntriesLock)
            {
                if (!Entries.TryGetValue(key, out var entry)) return;
                entry.state = "stale";
                entry.resolvedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                Entries[key] = entry;
            }
        }

        public static void MarkMissing(string assetId)
        {
            var key = NormalizeAssetId(assetId);
            if (string.IsNullOrWhiteSpace(key)) return;
            lock (EntriesLock)
            {
                if (!Entries.TryGetValue(key, out var entry))
                    entry = new ResourceRuntimeEntry { assetId = key };
                entry.state = "missing";
                entry.loadedObject = null;
                entry.resolvedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                Entries[key] = entry;
            }
        }

        public static void Remove(string assetId)
        {
            var key = NormalizeAssetId(assetId);
            if (string.IsNullOrWhiteSpace(key)) return;
            lock (EntriesLock)
            {
                Entries.Remove(key);
            }
        }

        private static string NormalizeAssetId(string assetId)
        {
            return (assetId ?? string.Empty).Trim();
        }

        private static ResourceRuntimeEntry Clone(ResourceRuntimeEntry entry)
        {
            if (entry == null)
                return null;
            return new ResourceRuntimeEntry
            {
                assetId = entry.assetId,
                resourceType = entry.resourceType,
                assetGuid = entry.assetGuid,
                assetPath = entry.assetPath,
                sourceFingerprint = entry.sourceFingerprint,
                loadedObject = entry.loadedObject,
                state = entry.state,
                resolvedAt = entry.resolvedAt,
            };
        }
    }

    public sealed class ResourceRuntimeEntry
    {
        public string assetId;
        public string resourceType;
        public string assetGuid;
        public string assetPath;
        public string sourceFingerprint;
        public UnityEngine.Object loadedObject;
        public string state;
        public long resolvedAt;
    }
}
