using System;
using System.IO;
using System.Text;

namespace BlenderSyncVNext.AssetBridgeCore
{
    public static class AssetPathUtility
    {
        private const int DefaultStableIdLength = 6;

        public static string BuildStableAssetPath(string folder, string friendlyName, string stableId, string extension)
        {
            return Path.Combine(NormalizeAssetFolder(folder), BuildStableFileName(friendlyName, stableId, extension)).Replace('\\', '/');
        }

        public static string BuildStableFileName(string friendlyName, string stableId, string extension)
        {
            var safeName = SanitizeFileName(friendlyName, "asset");
            var suffix = ShortStableId(stableId);
            var ext = NormalizeExtension(extension);
            return string.IsNullOrWhiteSpace(suffix)
                ? safeName + ext
                : safeName + "_" + suffix + ext;
        }

        public static string ShortStableId(string stableId)
        {
            if (string.IsNullOrWhiteSpace(stableId))
                return null;

            var raw = StripKnownPrefix(stableId.Trim());
            var builder = new StringBuilder(DefaultStableIdLength);
            foreach (var ch in raw)
            {
                if (!char.IsLetterOrDigit(ch))
                    continue;
                builder.Append(char.ToLowerInvariant(ch));
                if (builder.Length >= DefaultStableIdLength)
                    break;
            }
            return builder.Length > 0 ? builder.ToString() : null;
        }

        public static string SanitizeFileName(string value, string fallback)
        {
            var raw = string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
            foreach (var ch in Path.GetInvalidFileNameChars())
                raw = raw.Replace(ch, '_');
            raw = raw.Replace(':', '_').Replace('/', '_').Replace('\\', '_').Trim();
            return string.IsNullOrWhiteSpace(raw) ? fallback : raw;
        }

        private static string NormalizeAssetFolder(string folder)
        {
            return string.IsNullOrWhiteSpace(folder)
                ? "Assets"
                : folder.Replace('\\', '/').TrimEnd('/');
        }

        private static string NormalizeExtension(string extension)
        {
            if (string.IsNullOrWhiteSpace(extension))
                return string.Empty;
            return extension[0] == '.' ? extension : "." + extension;
        }

        private static string StripKnownPrefix(string stableId)
        {
            var prefixes = new[]
            {
                "mesh-", "mesh_",
                "mat-", "mat_",
                "tex-", "tex_",
                "animunityrigv1-preserveaxes-", "animunityrigv1_preserveaxes_",
                "animunityrigv1-", "animunityrigv1_",
                "animobjectv1-", "animobjectv1_",
                "anim-", "anim_",
                "clip-", "clip_",
                "rigobj-", "rigobj_",
                "rig-", "rig_",
                "object-", "object_",
                "prefab-", "prefab_",
            };

            foreach (var prefix in prefixes)
            {
                if (stableId.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                    return stableId.Substring(prefix.Length);
            }
            return stableId;
        }
    }
}
