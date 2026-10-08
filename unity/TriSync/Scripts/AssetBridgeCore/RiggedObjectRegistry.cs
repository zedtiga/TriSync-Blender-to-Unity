using System;
using System.IO;
using System.Linq;
using System.Text;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
using UnityEngine.SceneManagement;
#endif

namespace BlenderSyncVNext.AssetBridgeCore
{
    [Serializable]
    public sealed class RiggedManagedInstanceRecord
    {
        public string pairId;
        public string sceneObjectId;
        public string scenePath;
    }

    [Serializable]
    public sealed class RiggedObjectRecord
    {
        public string riggedObjectId;
        public string objectName;
        public string prefabPath;
        public string meshRef;
        public string[] meshRefs = Array.Empty<string>();
        public string[] materialRefs = Array.Empty<string>();
        public string mappingState;
        public string updateState;
        public long lastImportedAt;
        public string lastError;
        public RiggedManagedInstanceRecord managedInstance;
    }

    [Serializable]
    public sealed class RiggedObjectDatabase
    {
        public int version = 1;
        public RiggedObjectRecord[] records = Array.Empty<RiggedObjectRecord>();
    }

    public sealed class RiggedObjectRegistry
    {
        private static readonly Encoding JsonEncoding = new UTF8Encoding(false);

        public string StoragePath => Path.Combine(Directory.GetCurrentDirectory(), "Assets", "TriSync", "Registry", "rigged-objects.json");

        public RiggedObjectDatabase Load()
        {
            try
            {
                if (!File.Exists(StoragePath))
                {
                    RegistryFileStore.ClearReadFailure(StoragePath);
                    return new RiggedObjectDatabase();
                }
                var json = File.ReadAllText(StoragePath, JsonEncoding);
                var db = JsonUtility.FromJson<RiggedObjectDatabase>(json);
                RegistryFileStore.ClearReadFailure(StoragePath);
                return db ?? new RiggedObjectDatabase();
            }
            catch (Exception ex)
            {
                RegistryFileStore.ReportReadFailure(StoragePath, ex);
                return new RiggedObjectDatabase();
            }
        }

        public void Save(RiggedObjectDatabase db)
        {
            if (RegistryFileStore.ShouldSkipWriteAfterReadFailure(StoragePath))
                return;

            var dir = Path.GetDirectoryName(StoragePath);
            if (!Directory.Exists(dir))
                Directory.CreateDirectory(dir);
            var json = JsonUtility.ToJson(db ?? new RiggedObjectDatabase(), true);
            RegistryFileStore.WriteAllText(StoragePath, json, JsonEncoding);
#if UNITY_EDITOR
            ImportRegistryAsset();
#endif
        }

        public RiggedObjectRecord FindByRiggedObjectId(RiggedObjectDatabase db, string riggedObjectId)
        {
            riggedObjectId = NormalizeRiggedObjectId(riggedObjectId);
            if (db == null || db.records == null || string.IsNullOrWhiteSpace(riggedObjectId))
                return null;
            return db.records.FirstOrDefault(r => r != null && NormalizeRiggedObjectId(r.riggedObjectId) == riggedObjectId);
        }

        public RiggedObjectRecord FindByManagedPairId(RiggedObjectDatabase db, string pairId)
        {
            pairId = NormalizeRiggedObjectId(pairId);
            if (db == null || db.records == null || string.IsNullOrWhiteSpace(pairId))
                return null;
            return db.records.FirstOrDefault(r => r != null && NormalizeRiggedObjectId(r.managedInstance?.pairId) == pairId);
        }

        public void Upsert(RiggedObjectDatabase db, RiggedObjectRecord record)
        {
            if (db == null || record == null)
                return;

            record.riggedObjectId = NormalizeRiggedObjectId(record.riggedObjectId);
            if (string.IsNullOrWhiteSpace(record.riggedObjectId))
                return;

            var list = (db?.records ?? Array.Empty<RiggedObjectRecord>()).ToList();
            var index = list.FindIndex(r => r != null && NormalizeRiggedObjectId(r.riggedObjectId) == record.riggedObjectId);
            if (index >= 0)
                list[index] = record;
            else
                list.Add(record);
            db.records = list.ToArray();
        }

        private static string NormalizeRiggedObjectId(string riggedObjectId)
        {
            var normalized = riggedObjectId?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

#if UNITY_EDITOR
        private void ImportRegistryAsset()
        {
            if (RegistryFileStore.IsUnityAssetWriteBatchActive)
                return;

            var projectRoot = Path.GetFullPath(Directory.GetCurrentDirectory()).Replace('\\', '/').TrimEnd('/');
            var fullPath = Path.GetFullPath(StoragePath).Replace('\\', '/');
            var prefix = projectRoot + "/";
            if (!fullPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                return;

            var assetPath = fullPath.Substring(prefix.Length);
            if (string.IsNullOrWhiteSpace(assetPath) || !assetPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                return;

            AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate);
        }

        public static string TryCaptureSceneObjectId(GameObject target)
        {
            if (target == null)
                return null;
            var gid = GlobalObjectId.GetGlobalObjectIdSlow(target);
            var gidString = gid.ToString();
            return string.IsNullOrWhiteSpace(gidString) ? null : gidString;
        }

        public static string TryCaptureScenePath(GameObject target)
        {
            if (target == null)
                return null;
            var scene = target.scene;
            return scene.IsValid() ? scene.path : null;
        }
#endif
    }
}
