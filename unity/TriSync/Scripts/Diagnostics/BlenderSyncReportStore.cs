using System;
using System.Collections.Generic;
using System.Linq;

namespace BlenderSyncVNext.Diagnostics
{
    [Serializable]
    public sealed class BlenderSyncReportEntry
    {
        public DateTime time;
        public string category;
        public string status;
        public string summary;
        public readonly Dictionary<string, string> fields = new Dictionary<string, string>();
    }

    public static class BlenderSyncReportStore
    {
        private static readonly List<BlenderSyncReportEntry> Entries = new List<BlenderSyncReportEntry>();
        private static readonly object EntriesLock = new object();
        private const int MaxEntries = 80;

        public static event Action Changed;

        public static IReadOnlyList<BlenderSyncReportEntry> Recent
        {
            get
            {
                lock (EntriesLock)
                {
                    return Entries.Select(CloneEntry).ToArray();
                }
            }
        }

        public static BlenderSyncReportEntry Last
        {
            get
            {
                lock (EntriesLock)
                {
                    return Entries.Count > 0 ? CloneEntry(Entries[Entries.Count - 1]) : null;
                }
            }
        }

        public static void Clear()
        {
            lock (EntriesLock)
            {
                Entries.Clear();
            }
            NotifyChanged();
        }

        public static void Add(string category, string status, string summary, IDictionary<string, object> fields = null)
        {
            var entry = new BlenderSyncReportEntry
            {
                time = DateTime.Now,
                category = category ?? string.Empty,
                status = status ?? string.Empty,
                summary = summary ?? string.Empty,
            };

            if (fields != null)
            {
                foreach (var kv in fields)
                    entry.fields[kv.Key] = kv.Value != null ? kv.Value.ToString() : string.Empty;
            }

            lock (EntriesLock)
            {
                Entries.Add(entry);
                while (Entries.Count > MaxEntries)
                    Entries.RemoveAt(0);
            }
            NotifyChanged();
        }

        private static void NotifyChanged()
        {
            var handlers = Changed;
            if (handlers == null)
                return;

            foreach (Action handler in handlers.GetInvocationList())
            {
                try
                {
                    handler();
                }
                catch (Exception ex)
                {
                    BlenderSyncLog.Exception(
                        "Diagnostics",
                        "report_listener_failed",
                        ex,
                        "A diagnostics report listener failed.");
                }
            }
        }

        private static BlenderSyncReportEntry CloneEntry(BlenderSyncReportEntry source)
        {
            if (source == null)
                return null;
            var clone = new BlenderSyncReportEntry
            {
                time = source.time,
                category = source.category,
                status = source.status,
                summary = source.summary,
            };
            foreach (var kv in source.fields)
                clone.fields[kv.Key] = kv.Value;
            return clone;
        }
    }
}
