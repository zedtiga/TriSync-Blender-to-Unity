using System;
using System.IO;
using System.Linq;
using System.Text;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
using UnityEngine.SceneManagement;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    [Serializable]
    public sealed class ObjectBindingDatabase
    {
        public ObjectBindingEntry[] records = Array.Empty<ObjectBindingEntry>();
    }

    [Serializable]
    public sealed class ObjectBindingEntry
    {
        public string pairId;
        public string sceneObjectId;
        public string bindingState;
        public string meshRef;
        public string[] materialRefs = Array.Empty<string>();
        public string meshAssetGuid;
        public string[] materialAssetGuids = Array.Empty<string>();
    }

    public sealed class ObjectBindingRegistry
    {
        private static readonly Encoding JsonEncoding = new UTF8Encoding(false);
#if UNITY_EDITOR
        private const double DeferredSaveDelaySeconds = 0.5d;
        private static readonly object DeferredSaveLock = new object();
        private static ObjectBindingDatabase _pendingSaveDb;
        private static string _pendingSavePath;
        private static int _pendingSaveVersion;
        private static double _nextFlushAt;
        private static bool _updateHookInstalled;
        private static string _lastDeferredSaveError;

        static ObjectBindingRegistry()
        {
            EditorApplication.quitting -= FlushPendingForShutdown;
            EditorApplication.quitting += FlushPendingForShutdown;
            AssemblyReloadEvents.beforeAssemblyReload -= FlushPendingForShutdown;
            AssemblyReloadEvents.beforeAssemblyReload += FlushPendingForShutdown;
        }
#endif

        public string StoragePath => GetStoragePath();

        public ObjectBindingDatabase Load()
        {
#if UNITY_EDITOR
            var pending = TryGetPendingDatabase();
            if (pending != null)
                return pending;
#endif
            try
            {
                if (!File.Exists(StoragePath))
                {
                    RegistryFileStore.ClearReadFailure(StoragePath);
                    return new ObjectBindingDatabase();
                }

                var json = File.ReadAllText(StoragePath, JsonEncoding);
                var db = JsonUtility.FromJson<ObjectBindingDatabase>(json);
                RegistryFileStore.ClearReadFailure(StoragePath);
                return db ?? new ObjectBindingDatabase();
            }
            catch (Exception ex)
            {
                RegistryFileStore.ReportReadFailure(StoragePath, ex);
                return new ObjectBindingDatabase();
            }
        }

        public void Save(ObjectBindingDatabase db)
        {
#if UNITY_EDITOR
            QueueDeferredSave(db);
#else
            WriteDatabase(StoragePath, db);
#endif
        }

        public void SaveImmediate(ObjectBindingDatabase db)
        {
#if UNITY_EDITOR
            lock (DeferredSaveLock)
            {
                _pendingSaveDb = null;
                _pendingSavePath = null;
                _pendingSaveVersion++;
                RemoveUpdateHook();
            }
#endif
            WriteDatabase(StoragePath, db);
        }

        private static string GetStoragePath()
        {
            return Path.Combine(Directory.GetCurrentDirectory(), "Assets", "TriSync", "Registry", "object-bindings.json");
        }

        private static void WriteDatabase(string path, ObjectBindingDatabase db)
        {
            if (RegistryFileStore.ShouldSkipWriteAfterReadFailure(path))
                return;

            var dir = Path.GetDirectoryName(path);
            if (!Directory.Exists(dir))
                Directory.CreateDirectory(dir);

            var json = JsonUtility.ToJson(db ?? new ObjectBindingDatabase(), true);
            if (File.Exists(path))
            {
                try
                {
                    if (string.Equals(File.ReadAllText(path, JsonEncoding), json, StringComparison.Ordinal))
                    {
                        RegistryFileStore.ClearReadFailure(path);
                        RegistryFileStore.ImportUnityAssetIfNotBatched(path);
                        return;
                    }
                }
                catch
                {
                    // If comparison fails, fall through to the atomic write path.
                }
            }
            RegistryFileStore.WriteAllText(path, json, JsonEncoding);
            RegistryFileStore.ImportUnityAssetIfNotBatched(path);
        }

        public ObjectBindingEntry FindByPairId(ObjectBindingDatabase db, string pairId)
        {
            var normalizedPairId = NormalizePairId(pairId);
            if (db == null || db.records == null || string.IsNullOrWhiteSpace(normalizedPairId))
                return null;
            return db.records.FirstOrDefault(r => r != null && NormalizePairId(r.pairId) == normalizedPairId);
        }

        public void UpsertBinding(ObjectBindingDatabase db, ObjectBindingEntry record)
        {
            if (db == null || record == null)
                return;

            record.pairId = NormalizePairId(record.pairId);
            if (string.IsNullOrWhiteSpace(record.pairId))
                return;

            var list = (db.records ?? Array.Empty<ObjectBindingEntry>()).ToList();
            var index = list.FindIndex(r => r != null && NormalizePairId(r.pairId) == record.pairId);
            if (index >= 0)
                list[index] = record;
            else
                list.Add(record);
            db.records = list.ToArray();
        }

        public void MarkMissing(string pairId)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
                return;

            var db = Load();
            var rec = FindByPairId(db, pairId);
            if (rec == null) return;
            rec.bindingState = "missing";
            Save(db);
        }

#if UNITY_EDITOR
        private static ObjectBindingDatabase TryGetPendingDatabase()
        {
            lock (DeferredSaveLock)
            {
                return _pendingSaveDb != null ? CloneDatabase(_pendingSaveDb) : null;
            }
        }

        private static void QueueDeferredSave(ObjectBindingDatabase db)
        {
            lock (DeferredSaveLock)
            {
                _pendingSaveDb = CloneDatabase(db ?? new ObjectBindingDatabase());
                _pendingSavePath = GetStoragePath();
                _pendingSaveVersion++;
                _nextFlushAt = EditorApplication.timeSinceStartup + DeferredSaveDelaySeconds;
                EnsureUpdateHookLocked();
            }
        }

        private static void EnsureUpdateHookLocked()
        {
            if (_updateHookInstalled)
                return;
            EditorApplication.update += FlushPendingWhenDue;
            _updateHookInstalled = true;
        }

        private static void RemoveUpdateHook()
        {
            if (!_updateHookInstalled)
                return;
            EditorApplication.update -= FlushPendingWhenDue;
            _updateHookInstalled = false;
        }

        private static void FlushPendingWhenDue()
        {
            lock (DeferredSaveLock)
            {
                if (_pendingSaveDb == null)
                {
                    RemoveUpdateHook();
                    return;
                }
                if (EditorApplication.timeSinceStartup < _nextFlushAt)
                    return;
            }

            FlushPendingSave(force: false);
        }

        private static void FlushPendingForShutdown()
        {
            FlushPendingSave(force: true);
        }

        private static void FlushPendingSave(bool force)
        {
            ObjectBindingDatabase db;
            string path;
            int version;
            lock (DeferredSaveLock)
            {
                if (_pendingSaveDb == null)
                {
                    RemoveUpdateHook();
                    return;
                }
                if (!force && EditorApplication.timeSinceStartup < _nextFlushAt)
                    return;

                db = CloneDatabase(_pendingSaveDb);
                path = _pendingSavePath;
                version = _pendingSaveVersion;
            }

            try
            {
                WriteDatabase(path, db);
                lock (DeferredSaveLock)
                {
                    _lastDeferredSaveError = null;
                    if (version == _pendingSaveVersion)
                    {
                        _pendingSaveDb = null;
                        _pendingSavePath = null;
                        RemoveUpdateHook();
                    }
                }
            }
            catch (Exception ex)
            {
                var errorSignature = ex.GetType().FullName + ":" + ex.Message;
                var shouldLog = false;
                lock (DeferredSaveLock)
                {
                    if (!string.Equals(_lastDeferredSaveError, errorSignature, StringComparison.Ordinal))
                    {
                        _lastDeferredSaveError = errorSignature;
                        shouldLog = true;
                    }
                    _nextFlushAt = EditorApplication.timeSinceStartup + 1.0d;
                }
                if (shouldLog)
                    BlenderSyncLog.Warn(
                        "Registry",
                        "deferred_save_failed",
                        ex.Message,
                        new System.Collections.Generic.Dictionary<string, object>
                        {
                            { "exceptionType", ex.GetType().Name },
                        });
            }
        }

        private static ObjectBindingDatabase CloneDatabase(ObjectBindingDatabase db)
        {
            return new ObjectBindingDatabase
            {
                records = (db?.records ?? Array.Empty<ObjectBindingEntry>())
                    .Where(r => r != null)
                    .Select(CloneEntry)
                    .ToArray()
            };
        }

        private static ObjectBindingEntry CloneEntry(ObjectBindingEntry entry)
        {
            if (entry == null)
                return null;
            return new ObjectBindingEntry
            {
                pairId = entry.pairId,
                sceneObjectId = entry.sceneObjectId,
                bindingState = entry.bindingState,
                meshRef = entry.meshRef,
                materialRefs = entry.materialRefs != null ? entry.materialRefs.ToArray() : Array.Empty<string>(),
                meshAssetGuid = entry.meshAssetGuid,
                materialAssetGuids = entry.materialAssetGuids != null ? entry.materialAssetGuids.ToArray() : Array.Empty<string>(),
            };
        }

        public bool TryCaptureBinding(string pairId, GameObject target, out ObjectBindingEntry entry)
        {
            entry = null;
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId) || target == null)
                return false;

            var scene = target.scene;
            if (!scene.IsValid() || string.IsNullOrWhiteSpace(scene.path))
                return false;

            var gid = GlobalObjectId.GetGlobalObjectIdSlow(target);
            var gidString = gid.ToString();
            if (string.IsNullOrWhiteSpace(gidString) || gidString == "GlobalObjectId_V1-0-00000000000000000000000000000000-0-0")
                return false;

            entry = new ObjectBindingEntry
            {
                pairId = pairId,
                sceneObjectId = gidString,
                bindingState = "bound",
            };
            return true;
        }

        public bool TryResolveBoundObject(string pairId, out GameObject target, out ObjectBindingEntry entry)
        {
            target = null;
            entry = null;
            pairId = NormalizePairId(pairId);

            var db = Load();
            entry = FindByPairId(db, pairId);
            if (entry == null || string.IsNullOrWhiteSpace(entry.sceneObjectId))
                return false;
            if (!GlobalObjectId.TryParse(entry.sceneObjectId, out var gid))
                return false;

            var obj = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid);
            target = obj as GameObject;
            return target != null;
        }

        public bool UpsertCurrentBinding(string pairId, GameObject target)
        {
            pairId = NormalizePairId(pairId);
            if (!TryCaptureBinding(pairId, target, out var rec))
                return false;

            var db = Load();
            UpsertBinding(db, rec);
            Save(db);
            return true;
        }
#endif

        private static string NormalizePairId(string pairId)
        {
            var normalized = pairId?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

    }
}
