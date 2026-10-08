#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using BlenderSyncVNext.Diagnostics;
using UnityEditor;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;

namespace BlenderSyncVNext.RootMotion
{
    internal static class RootMotionAssetPaths
    {
        public static bool TryEnsureAssetFolder(ref string folder, string reportTitle, string dialogTitle)
        {
            var original = folder;
            try
            {
                folder = EnsureAssetFolder(folder);
                return true;
            }
            catch (InvalidOperationException ex)
            {
                const string message = "Invalid output folder. Use a Unity asset path under Assets.";
                BlenderSyncReportStore.Add(reportTitle, "ERROR", message, new Dictionary<string, object>
                {
                    { "folder", original ?? string.Empty },
                    { "error", ex.Message },
                });
                EditorUtility.DisplayDialog(
                    Tr(dialogTitle),
                    Tr(message) + $"\n\n{original}",
                    Tr("OK"));
                return false;
            }
        }

        public static string EnsureAssetFolder(string folder)
        {
            var normalized = NormalizeAssetFolder(folder);
            if (normalized == "Assets")
                return normalized;

            var parts = normalized.Split('/');
            var current = "Assets";
            for (var i = 1; i < parts.Length; i++)
            {
                var next = current + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next))
                    AssetDatabase.CreateFolder(current, parts[i]);
                if (!AssetDatabase.IsValidFolder(next))
                    throw new InvalidOperationException("asset_folder_create_failed:" + next);
                current = next;
            }

            return normalized;
        }

        public static string SanitizeFileName(string value, string fallback)
        {
            var text = string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
            if (text == null)
                return string.Empty;

            foreach (var ch in Path.GetInvalidFileNameChars())
                text = text.Replace(ch, '_');

            text = text.Replace(':', '_').Replace('/', '_').Replace('\\', '_').Trim();
            return string.IsNullOrWhiteSpace(text) ? (fallback ?? string.Empty) : text;
        }

        private static string NormalizeAssetFolder(string folder)
        {
            if (string.IsNullOrWhiteSpace(folder))
                throw new InvalidOperationException("asset_folder_empty");

            var rawParts = folder.Replace('\\', '/').Trim().Trim('/').Split('/');
            var parts = new List<string>();
            foreach (var rawPart in rawParts)
            {
                var part = rawPart.Trim();
                if (part.Length == 0)
                    continue;
                if (part == "." || part == "..")
                    throw new InvalidOperationException("asset_folder_invalid_segment:" + part);
                foreach (var ch in Path.GetInvalidFileNameChars())
                {
                    if (part.IndexOf(ch) >= 0)
                        throw new InvalidOperationException("asset_folder_invalid_segment:" + part);
                }
                parts.Add(part);
            }

            if (parts.Count == 0 || !string.Equals(parts[0], "Assets", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("asset_path_must_start_with_assets:" + folder);

            parts[0] = "Assets";
            return string.Join("/", parts);
        }
    }
}
#endif
