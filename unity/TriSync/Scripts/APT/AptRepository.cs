using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEngine;

namespace BlenderSyncVNext.APT
{
    public sealed class AptRepository
    {
        private static readonly Encoding JsonEncoding = new UTF8Encoding(false);
        private readonly HashSet<string> _failedLoadShardTypes = new HashSet<string>(StringComparer.Ordinal);

        public string StorageRoot => Path.Combine(Directory.GetCurrentDirectory(), "Assets", "TriSync", "Registry");
        public string StoragePath => StorageRoot;

        public AptDatabase Load()
        {
            _failedLoadShardTypes.Clear();
            try
            {
                var records = new List<AptRecord>();
                foreach (var type in GetSupportedTypes())
                {
                    var shard = LoadShard(type);
                    if (shard?.records == null)
                        continue;

                    foreach (var rec in shard.records)
                    {
                        if (rec != null)
                            records.Add(rec);
                    }
                }

                return new AptDatabase { records = records.ToArray() };
            }
            catch
            {
                return new AptDatabase();
            }
        }

        public void Save(AptDatabase db)
        {
            if (!Directory.Exists(StorageRoot))
                Directory.CreateDirectory(StorageRoot);

            var map = new Dictionary<string, List<AptRecord>>(StringComparer.Ordinal);
            foreach (var record in (db?.records ?? Array.Empty<AptRecord>()).Where(r => r != null))
            {
                record.assetId = NormalizeAssetId(record.assetId);
                if (string.IsNullOrWhiteSpace(record.assetId))
                    continue;

                if (!TryNormalizeResourceType(record.target?.resourceType, out var type))
                    continue;
                if (!map.TryGetValue(type, out var records))
                {
                    records = new List<AptRecord>();
                    map[type] = records;
                }
                records.Add(record);
            }

            foreach (var type in GetSupportedTypes())
            {
                if (_failedLoadShardTypes.Contains(type) && !map.ContainsKey(type))
                    continue;
                var shard = map.TryGetValue(type, out var records)
                    ? new AptDatabase { records = records.ToArray() }
                    : new AptDatabase();
                SaveShard(type, shard);
            }
        }

        public AptRecord FindByAssetId(AptDatabase db, string assetId)
        {
            assetId = NormalizeAssetId(assetId);
            if (db == null || db.records == null || string.IsNullOrWhiteSpace(assetId))
                return null;
            return db.records.FirstOrDefault(r => r != null && NormalizeAssetId(r.assetId) == assetId);
        }

        public void Upsert(AptDatabase db, AptRecord record)
        {
            if (db == null || record == null)
                return;

            record.assetId = NormalizeAssetId(record.assetId);
            if (string.IsNullOrWhiteSpace(record.assetId))
                return;

            var list = (db?.records ?? Array.Empty<AptRecord>()).ToList();
            var index = list.FindIndex(r => r != null && NormalizeAssetId(r.assetId) == record.assetId);
            if (index >= 0)
                list[index] = record;
            else
                list.Add(record);
            db.records = list.ToArray();
        }

        public void MarkState(AptDatabase db, string assetId, string mappingState, string updateState, string err = null)
        {
            var rec = FindByAssetId(db, assetId);
            if (rec == null) return;

            rec.mappingState = mappingState;
            rec.updateState = updateState;
            rec.lastError = err;
        }

        public string GetShardPath(string type)
        {
            return Path.Combine(StorageRoot, GetShardFileName(type));
        }

        private static string[] GetSupportedTypes()
        {
            return new[] { "mesh", "material", "texture" };
        }

        private static string NormalizeAssetId(string assetId)
        {
            var normalized = assetId?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

        private static string GetShardFileName(string type)
        {
            switch (NormalizeResourceType(type))
            {
                case "mesh": return "meshes.json";
                case "material": return "materials.json";
                case "texture": return "textures.json";
                default: throw new InvalidOperationException("unsupported_apt_resource_type");
            }
        }

        private static string NormalizeResourceType(string type)
        {
            if (TryNormalizeResourceType(type, out var value))
                return value;
            throw new InvalidOperationException("unsupported_apt_resource_type:" + (type ?? "(null)"));
        }

        private static bool TryNormalizeResourceType(string type, out string normalized)
        {
            normalized = (type ?? string.Empty).Trim().ToLowerInvariant();
            switch (normalized)
            {
                case "mesh":
                case "material":
                case "texture":
                    return true;
                default:
                    normalized = null;
                    return false;
            }
        }

        private AptDatabase LoadShard(string type)
        {
            var path = GetShardPath(type);
            if (!File.Exists(path))
            {
                RegistryFileStore.ClearReadFailure(path);
                return new AptDatabase();
            }

            try
            {
                var json = File.ReadAllText(path, JsonEncoding);
                var db = JsonUtility.FromJson<AptDatabase>(json);
                RegistryFileStore.ClearReadFailure(path);
                return db ?? new AptDatabase();
            }
            catch (Exception ex)
            {
                RegistryFileStore.ReportReadFailure(path, ex);
                _failedLoadShardTypes.Add(NormalizeResourceType(type));
                return new AptDatabase();
            }
        }

        private void SaveShard(string type, AptDatabase db)
        {
            var path = GetShardPath(type);
            if (RegistryFileStore.ShouldSkipWriteAfterReadFailure(path))
                return;

            var dir = Path.GetDirectoryName(path);
            if (!Directory.Exists(dir))
                Directory.CreateDirectory(dir);

            var json = JsonUtility.ToJson(db ?? new AptDatabase(), true);
            RegistryFileStore.WriteAllText(path, json, JsonEncoding);
            RegistryFileStore.ImportUnityAssetIfNotBatched(path);
        }
    }
}
