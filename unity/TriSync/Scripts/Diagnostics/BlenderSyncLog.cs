#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.Diagnostics
{
    [Serializable]
    public sealed class BlenderSyncLogEntry
    {
        public long sequence;
        public DateTime timeUtc;
        public string level;
        public string category;
        public string eventName;
        public string summary;
        public readonly Dictionary<string, string> fields = new Dictionary<string, string>();
    }

    public static class BlenderSyncLog
    {
        public const int MaxEntries = 500;
        public const int MaxSummaryLength = 2048;
        public const int MaxFieldCount = 16;
        public const int MaxFieldValueLength = 512;

        private const string VerboseEditorPrefKey = "BlenderSyncVNext.VerboseLogging";
        private const string VerboseEnvironmentVariable = "BLENDERSYNC_VERBOSE_LOGS";
        private const int MaxFieldKeyLength = 80;
        private static readonly List<BlenderSyncLogEntry> Entries = new List<BlenderSyncLogEntry>();
        private static readonly object EntriesLock = new object();
        private static readonly HashSet<string> SensitiveFieldNames = new HashSet<string>(
            new[] { "body", "content", "json", "payload", "raw", "rawjson", "binary", "binarydata" },
            StringComparer.OrdinalIgnoreCase);
        private static readonly bool? EnvironmentVerbose = ParseOptionalBool(
            Environment.GetEnvironmentVariable(VerboseEnvironmentVariable));

        private static long _sequence;
        private static int _verboseEnabled;
        private static int _verboseOverride = -1;

        public static bool VerboseEnabled
        {
            get
            {
                var overrideValue = Volatile.Read(ref _verboseOverride);
                if (overrideValue >= 0)
                    return overrideValue == 1;
                if (EnvironmentVerbose.HasValue)
                    return EnvironmentVerbose.Value;
                return Volatile.Read(ref _verboseEnabled) == 1;
            }
        }

        public static IReadOnlyList<BlenderSyncLogEntry> Recent
        {
            get
            {
                lock (EntriesLock)
                    return Entries.Select(CloneEntry).ToArray();
            }
        }

        public static long LatestSequence
        {
            get
            {
                lock (EntriesLock)
                    return Entries.Count > 0 ? Entries[Entries.Count - 1].sequence : 0L;
            }
        }

        [InitializeOnLoadMethod]
        private static void InitializePreference()
        {
            Volatile.Write(
                ref _verboseEnabled,
                EditorPrefs.GetBool(VerboseEditorPrefKey, false) ? 1 : 0);
        }

        public static void SetVerbose(bool enabled)
        {
            Volatile.Write(ref _verboseEnabled, enabled ? 1 : 0);
            EditorPrefs.SetBool(VerboseEditorPrefKey, enabled);
        }

        internal static void SetVerboseOverride(bool? enabled)
        {
            Volatile.Write(ref _verboseOverride, enabled.HasValue ? (enabled.Value ? 1 : 0) : -1);
        }

        public static void Clear()
        {
            lock (EntriesLock)
                Entries.Clear();
        }

        public static bool Error(
            string category,
            string eventName,
            string summary = null,
            IDictionary<string, object> fields = null)
        {
            return Write("ERROR", category, eventName, summary, fields, true);
        }

        public static bool Warn(
            string category,
            string eventName,
            string summary = null,
            IDictionary<string, object> fields = null)
        {
            return Write("WARN", category, eventName, summary, fields, VerboseEnabled);
        }

        public static bool Info(
            string category,
            string eventName,
            string summary = null,
            IDictionary<string, object> fields = null)
        {
            return Write("INFO", category, eventName, summary, fields, VerboseEnabled);
        }

        public static bool Trace(
            string category,
            string eventName,
            Func<string> summaryFactory,
            Func<IDictionary<string, object>> fieldsFactory = null)
        {
            if (!VerboseEnabled)
                return false;
            try
            {
                return Write(
                    "TRACE",
                    category,
                    eventName,
                    summaryFactory != null ? summaryFactory() : null,
                    fieldsFactory != null ? fieldsFactory() : null,
                    true);
            }
            catch (Exception ex)
            {
                return Exception("Logging", "trace_factory_failed", ex);
            }
        }

        public static bool Trace(
            string category,
            string eventName,
            string summary = null,
            IDictionary<string, object> fields = null)
        {
            if (!VerboseEnabled)
                return false;
            return Write("TRACE", category, eventName, summary, fields, true);
        }

        public static bool Exception(
            string category,
            string eventName,
            Exception exception,
            string summary = null,
            IDictionary<string, object> fields = null)
        {
            var exceptionFields = fields != null
                ? new Dictionary<string, object>(fields)
                : new Dictionary<string, object>();
            exceptionFields["exceptionType"] = exception != null ? exception.GetType().Name : "Exception";
            var entry = Append(
                "ERROR",
                category,
                eventName,
                summary ?? exception?.Message ?? "Exception",
                exceptionFields);
            Debug.LogError(FormatConsole(entry) + Environment.NewLine + (exception?.ToString() ?? "Exception"));
            return true;
        }

        private static bool Write(
            string level,
            string category,
            string eventName,
            string summary,
            IDictionary<string, object> fields,
            bool writeConsole)
        {
            var entry = Append(level, category, eventName, summary, fields);
            if (!writeConsole)
                return true;

            var message = FormatConsole(entry);
            switch (level)
            {
                case "ERROR":
                    Debug.LogError(message);
                    break;
                case "WARN":
                    Debug.LogWarning(message);
                    break;
                default:
                    Debug.Log(message);
                    break;
            }
            return true;
        }

        private static BlenderSyncLogEntry Append(
            string level,
            string category,
            string eventName,
            string summary,
            IDictionary<string, object> fields)
        {
            var entry = new BlenderSyncLogEntry
            {
                level = NormalizeText(level, 16, "INFO"),
                category = NormalizeText(category, 80, "General"),
                eventName = NormalizeText(eventName, 120, "event"),
                summary = NormalizeText(summary, MaxSummaryLength, string.Empty),
            };
            NormalizeFields(fields, entry.fields);

            lock (EntriesLock)
            {
                entry.sequence = ++_sequence;
                entry.timeUtc = DateTime.UtcNow;
                Entries.Add(entry);
                while (Entries.Count > MaxEntries)
                    Entries.RemoveAt(0);
            }
            return CloneEntry(entry);
        }

        private static void NormalizeFields(
            IDictionary<string, object> source,
            IDictionary<string, string> destination)
        {
            if (source == null)
                return;
            foreach (var pair in source)
            {
                if (destination.Count >= MaxFieldCount)
                    break;
                var key = NormalizeText(pair.Key, MaxFieldKeyLength, string.Empty).Trim();
                if (string.IsNullOrEmpty(key) || SensitiveFieldNames.Contains(key.Replace("_", string.Empty)))
                    continue;
                var value = pair.Value;
                if (value != null && !(value is string) && !(value is bool) &&
                    !(value is byte) && !(value is sbyte) && !(value is short) && !(value is ushort) &&
                    !(value is int) && !(value is uint) && !(value is long) && !(value is ulong) &&
                    !(value is float) && !(value is double) && !(value is decimal))
                    continue;
                destination[key] = NormalizeText(value, MaxFieldValueLength, string.Empty);
            }
        }

        private static string FormatConsole(BlenderSyncLogEntry entry)
        {
            var prefix = $"[BlenderSync][{entry.level}][{entry.category}] {entry.eventName}";
            var fields = entry.fields.Count > 0
                ? " " + string.Join(" ", entry.fields.Select(pair => pair.Key + "=" + pair.Value))
                : string.Empty;
            return prefix + (string.IsNullOrEmpty(entry.summary) ? string.Empty : " " + entry.summary) + fields;
        }

        private static string NormalizeText(object value, int maxLength, string fallback)
        {
            string text;
            try
            {
                text = value != null ? value.ToString() : fallback;
            }
            catch
            {
                text = fallback;
            }
            text = text ?? fallback ?? string.Empty;
            return text.Length <= maxLength
                ? text
                : text.Substring(0, Math.Max(1, maxLength - 3)) + "...";
        }

        private static bool? ParseOptionalBool(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return null;
            switch (value.Trim().ToLowerInvariant())
            {
                case "1":
                case "true":
                case "yes":
                case "on":
                    return true;
                default:
                    return false;
            }
        }

        private static BlenderSyncLogEntry CloneEntry(BlenderSyncLogEntry source)
        {
            var clone = new BlenderSyncLogEntry
            {
                sequence = source.sequence,
                timeUtc = source.timeUtc,
                level = source.level,
                category = source.category,
                eventName = source.eventName,
                summary = source.summary,
            };
            foreach (var pair in source.fields)
                clone.fields[pair.Key] = pair.Value;
            return clone;
        }
    }
}
#endif
