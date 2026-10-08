using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext
{
    internal static class RegistryFileStore
    {
        private static readonly HashSet<string> ReportedReadFailures = new HashSet<string>(StringComparer.Ordinal);
        private static readonly HashSet<string> ReportedSkippedWrites = new HashSet<string>(StringComparer.Ordinal);
        private static readonly object ReportedReadFailuresLock = new object();
#if UNITY_EDITOR
        private static int _unityAssetWriteBatchDepth;

        public static bool IsUnityAssetWriteBatchActive => _unityAssetWriteBatchDepth > 0;
#endif

        public static void WriteAllText(string path, string contents, Encoding encoding)
        {
            var dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                Directory.CreateDirectory(dir);

            if (File.Exists(path))
            {
                try
                {
                    if (string.Equals(File.ReadAllText(path, encoding), contents, StringComparison.Ordinal))
                        return;
                }
                catch
                {
                    // If comparison fails, fall through to the atomic write path.
                }
            }

            var tmp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            File.WriteAllText(tmp, contents, encoding);
            try
            {
                if (File.Exists(path))
                    File.Replace(tmp, path, null);
                else
                    File.Move(tmp, path);
            }
            catch
            {
                TryDelete(tmp);
                throw;
            }
        }

        public static IDisposable BeginUnityAssetWriteBatch()
        {
#if UNITY_EDITOR
            return new UnityAssetWriteBatch();
#else
            return NoopDisposable.Instance;
#endif
        }

        public static void ImportUnityAssetIfNotBatched(string fullPath)
        {
#if UNITY_EDITOR
            if (IsUnityAssetWriteBatchActive)
                return;

            var assetPath = ToUnityAssetPath(fullPath);
            if (string.IsNullOrWhiteSpace(assetPath))
                return;

            try
            {
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            }
            catch (Exception ex)
            {
                BlenderSyncLog.Warn(
                    "Registry",
                    "asset_import_failed",
                    ex.Message,
                    new Dictionary<string, object>
                    {
                        { "assetPath", assetPath },
                        { "exceptionType", ex.GetType().Name },
                    });
            }
#endif
        }

        private static void TryDelete(string path)
        {
            try
            {
                if (File.Exists(path))
                    File.Delete(path);
            }
            catch
            {
                // Best effort cleanup; preserve the original write failure.
            }
        }

        public static void ClearReadFailure(string path)
        {
            var normalizedPath = NormalizePath(path);
            lock (ReportedReadFailuresLock)
            {
                ReportedReadFailures.RemoveWhere(key => key.StartsWith(normalizedPath + "|", StringComparison.Ordinal));
                ReportedSkippedWrites.Remove(normalizedPath);
            }
        }

        public static bool ShouldSkipWriteAfterReadFailure(string path)
        {
            var normalizedPath = NormalizePath(path);
            lock (ReportedReadFailuresLock)
            {
                var hasReadFailure = false;
                foreach (var key in ReportedReadFailures)
                {
                    if (!key.StartsWith(normalizedPath + "|", StringComparison.Ordinal))
                        continue;
                    hasReadFailure = true;
                    break;
                }

                if (!hasReadFailure)
                    return false;

                if (!ReportedSkippedWrites.Add(normalizedPath))
                    return true;
            }

            BlenderSyncReportStore.Add("Registry", "WARN", "Registry write skipped after read failure", new Dictionary<string, object>
            {
                { "path", normalizedPath },
            });
            return true;
        }

        public static void ReportReadFailure(string path, Exception ex)
        {
            var normalizedPath = NormalizePath(path);
            var message = ex?.Message ?? "unknown registry read failure";
            var key = normalizedPath + "|" + ex?.GetType().FullName + "|" + message;
            lock (ReportedReadFailuresLock)
            {
                if (!ReportedReadFailures.Add(key))
                    return;
            }

            BlenderSyncReportStore.Add("Registry", "WARN", "Registry read failed", new Dictionary<string, object>
            {
                { "path", normalizedPath },
                { "exceptionType", ex?.GetType().Name ?? string.Empty },
                { "error", message },
            });
        }

        private static string NormalizePath(string path)
        {
            return string.IsNullOrWhiteSpace(path) ? "(unknown)" : path.Replace('\\', '/');
        }

#if UNITY_EDITOR
        private static string ToUnityAssetPath(string fullPath)
        {
            if (string.IsNullOrWhiteSpace(fullPath))
                return null;

            var projectRoot = Path.GetFullPath(Directory.GetCurrentDirectory()).Replace('\\', '/').TrimEnd('/');
            var normalized = Path.GetFullPath(fullPath).Replace('\\', '/');
            var prefix = projectRoot + "/";
            if (!normalized.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                return null;

            var assetPath = normalized.Substring(prefix.Length);
            return assetPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase) ? assetPath : null;
        }
#endif

        private sealed class NoopDisposable : IDisposable
        {
            public static readonly NoopDisposable Instance = new NoopDisposable();
            public void Dispose()
            {
            }
        }

#if UNITY_EDITOR
        private sealed class UnityAssetWriteBatch : IDisposable
        {
            private bool _disposed;

            public UnityAssetWriteBatch()
            {
                if (_unityAssetWriteBatchDepth++ == 0)
                    AssetDatabase.DisallowAutoRefresh();
            }

            public void Dispose()
            {
                if (_disposed)
                    return;
                _disposed = true;

                _unityAssetWriteBatchDepth = Math.Max(0, _unityAssetWriteBatchDepth - 1);
                if (_unityAssetWriteBatchDepth != 0)
                    return;

                AssetDatabase.AllowAutoRefresh();
                AssetDatabase.Refresh(ImportAssetOptions.ForceUpdate);
            }
        }
#endif
    }
}
