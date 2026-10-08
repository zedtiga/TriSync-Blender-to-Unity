#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.SessionCore;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.UI
{
    internal sealed class DiagnosticsPanelView
    {
        private readonly BlenderSyncReportView _reportView = new BlenderSyncReportView();
        private Vector2 _scroll;
        private bool _showTechnicalDetails;
        private double _copiedUntil;

        public void Draw(SessionPanel sessionPanel, RegistryPanelView registryView)
        {
            registryView.EnsureLoaded();
            DrawToolbar(sessionPanel, registryView);
            EditorGUILayout.Space(6);

            _scroll = EditorGUILayout.BeginScrollView(_scroll);
            try
            {
                _reportView.DrawRecentActivity();
                EditorGUILayout.Space(8);
                DrawTechnicalDetails(sessionPanel, registryView);
            }
            finally
            {
                EditorGUILayout.EndScrollView();
            }
        }

        private void DrawTechnicalDetails(SessionPanel sessionPanel, RegistryPanelView registryView)
        {
            _showTechnicalDetails = EditorGUILayout.Foldout(_showTechnicalDetails, Tr("Technical Details"), true);
            if (!_showTechnicalDetails)
                return;

            EditorGUILayout.Space(3);
            using (new EditorGUILayout.VerticalScope("box"))
            {
                EditorGUILayout.LabelField(Tr("Logging"), EditorStyles.boldLabel);
                var verboseLogging = BlenderSyncLog.VerboseEnabled;
                EditorGUI.BeginChangeCheck();
                verboseLogging = EditorGUILayout.Toggle(Tr("Verbose Logging"), verboseLogging);
                if (EditorGUI.EndChangeCheck())
                    BlenderSyncLog.SetVerbose(verboseLogging);
                EditorGUILayout.Space(6);

                EditorGUILayout.LabelField(Tr("Session"), EditorStyles.boldLabel);
                sessionPanel.DrawSessionDiagnostics();
                EditorGUILayout.Space(6);

                EditorGUILayout.LabelField(Tr("Pipeline"), EditorStyles.boldLabel);
                registryView.DrawPipelineDiagnostics();
                EditorGUILayout.Space(6);

                EditorGUILayout.LabelField(Tr("Registry"), EditorStyles.boldLabel);
                registryView.DrawDiagnosticsOverview();
            }
        }

        private void DrawToolbar(SessionPanel sessionPanel, RegistryPanelView registryView)
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                GUILayout.FlexibleSpace();
                var recentlyCopied = EditorApplication.timeSinceStartup < _copiedUntil;
                if (GUILayout.Button(
                        Tr(recentlyCopied ? "Copied" : "Copy Diagnostics"),
                        GUILayout.Width(122)))
                {
                    GUIUtility.systemCopyBuffer = sessionPanel.BuildCopyDiagnosticsJson(registryView);
                    _copiedUntil = EditorApplication.timeSinceStartup + 1.5d;
                }
            }
        }

    }

    internal sealed class BlenderSyncReportView
    {
        private const int MaxVisibleEntries = 5;
        private readonly HashSet<string> _expandedEntries = new HashSet<string>(StringComparer.Ordinal);
        private bool _showRecentActivity;

        public void DrawRecentActivity()
        {
            var hasEntries = BlenderSyncReportStore.Last != null || BlenderSyncLog.LatestSequence > 0L;
            using (new EditorGUILayout.HorizontalScope())
            {
                _showRecentActivity = EditorGUILayout.Foldout(
                    _showRecentActivity,
                    Tr("Recent Activity"),
                    true);
                GUILayout.FlexibleSpace();
                if (hasEntries && GUILayout.Button(Tr("Clear"), GUILayout.Width(72)))
                {
                    BlenderSyncReportStore.Clear();
                    BlenderSyncLog.Clear();
                    _expandedEntries.Clear();
                    GUIUtility.ExitGUI();
                }
            }

            if (!_showRecentActivity)
                return;

            var entries = BuildEntries();
            if (entries == null || entries.Count == 0)
            {
                EditorGUILayout.LabelField(Tr("No activity recorded."), EditorStyles.miniLabel);
                return;
            }

            var first = FirstVisibleEntryIndex(entries.Count);
            if (first > 0)
                EditorGUILayout.LabelField(Format("Showing latest {0} of {1}.", MaxVisibleEntries, entries.Count), EditorStyles.miniLabel);

            for (var i = entries.Count - 1; i >= first; i--)
                DrawEntry(entries[i], i);
        }

        private static List<ActivityEntry> BuildEntries()
        {
            var entries = new List<ActivityEntry>();
            var reports = BlenderSyncReportStore.Recent;
            for (var index = 0; index < reports.Count; index++)
            {
                var report = reports[index];
                entries.Add(new ActivityEntry
                {
                    key = $"report:{index}:{report.time.Ticks}",
                    time = report.time.ToUniversalTime(),
                    status = report.status,
                    category = report.category,
                    summary = report.summary,
                    fields = report.fields,
                });
            }
            foreach (var log in BlenderSyncLog.Recent)
            {
                entries.Add(new ActivityEntry
                {
                    key = $"log:{log.sequence}",
                    time = log.timeUtc,
                    status = log.level,
                    category = string.IsNullOrWhiteSpace(log.eventName)
                        ? log.category
                        : $"{log.category} / {log.eventName}",
                    summary = log.summary,
                    fields = log.fields,
                });
            }
            entries.Sort((left, right) =>
            {
                var timeComparison = left.time.CompareTo(right.time);
                return timeComparison != 0
                    ? timeComparison
                    : string.CompareOrdinal(left.key, right.key);
            });
            return entries;
        }

        private void DrawEntry(ActivityEntry entry, int index)
        {
            EditorGUILayout.BeginVertical("box");
            var key = entry.key ?? $"entry:{index}";
            var expanded = _expandedEntries.Contains(key);
            var status = NormalizeStatus(entry.status);
            var header = $"{entry.time.ToLocalTime():HH:mm:ss}  [{status}] {entry.category}";
            var nextExpanded = EditorGUILayout.Foldout(expanded, header, true);
            if (nextExpanded)
                _expandedEntries.Add(key);
            else
                _expandedEntries.Remove(key);

            if (!nextExpanded && !string.IsNullOrWhiteSpace(entry.summary))
                EditorGUILayout.LabelField(Truncate(entry.summary, 140), EditorStyles.miniLabel);

            if (!nextExpanded)
            {
                EditorGUILayout.EndVertical();
                return;
            }

            if (!string.IsNullOrEmpty(entry.summary))
                EditorGUILayout.LabelField(entry.summary, EditorStyles.wordWrappedLabel);

            if (entry.fields != null && entry.fields.Count > 0)
            {
                EditorGUILayout.Space(2);
                foreach (KeyValuePair<string, string> kv in entry.fields)
                    EditorGUILayout.LabelField(kv.Key, kv.Value ?? string.Empty);
            }
            EditorGUILayout.EndVertical();
        }

        internal static string NormalizeStatus(string status)
        {
            var normalized = string.IsNullOrWhiteSpace(status) ? "INFO" : status.Trim().ToUpperInvariant();
            return normalized == "WARNING" ? "WARN" : normalized;
        }

        internal static int FirstVisibleEntryIndex(int entryCount)
        {
            return Math.Max(0, entryCount - MaxVisibleEntries);
        }

        private static string Truncate(string value, int maxLength)
        {
            if (string.IsNullOrEmpty(value) || value.Length <= maxLength)
                return value ?? string.Empty;
            return value.Substring(0, Math.Max(1, maxLength - 3)) + "...";
        }

        private sealed class ActivityEntry
        {
            public string key;
            public DateTime time;
            public string status;
            public string category;
            public string summary;
            public IDictionary<string, string> fields;
        }
    }

    internal static class UnityDiagnosticsSnapshotBuilder
    {
        private const string SchemaVersion = "blendersync-diagnostics-v2";
        private const int MaxReportFields = 16;
        private const int MaxRecentLogs = 50;
        private const int MaxTextLength = 2048;
        private static readonly Regex DiagnosticsPathRegex = new Regex(
            @"(?<![A-Za-z0-9])(?:""(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|private|mnt|opt)/)[^""]*""|'(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|private|mnt|opt)/)[^']*'|(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|private|mnt|opt)/)(?:(?!\s+(?:endpoint=)?(?:wss?|https?)://)[^,;\r\n])+)",
            RegexOptions.Compiled | RegexOptions.IgnoreCase);

        public static string BuildJson(
            SessionClient session,
            string endpoint,
            RegistryDiagnosticsData registry,
            BlenderSyncReportEntry lastReport,
            DateTime timestampUtc)
        {
            var state = SessionClient.LastLifecycleState ?? "disconnected";
            var running = SessionClient.SharedTransport.IsRunning;
            var snapshot = new DiagnosticsSnapshot
            {
                schemaVersion = SchemaVersion,
                timestampUtc = timestampUtc.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"),
                unityVersion = Application.unityVersion,
                session = new SessionSnapshot
                {
                    transportConnected = running,
                    handshakeConfirmed = running && state == "handshake_confirmed",
                    handshakePhase = NormalizeHandshakePhase(state),
                    protocolVersion = session != null && session.PeerProtocolVersion.HasValue
                        ? session.PeerProtocolVersion.Value.ToString()
                        : null,
                    legacyProtocol = session != null && session.LegacyProtocol,
                    negotiatedFeatures = session?.NegotiatedFeatures ?? Array.Empty<string>(),
                    endpoint = RedactDiagnosticsText(endpoint),
                    lastError = RedactDiagnosticsText(SessionClient.LastProtocolError),
                    lastActivityUtc = FormatUnixTimestamp(SessionClient.LastLifecycleAt),
                },
                registry = BuildRegistrySnapshot(registry),
                lastOperation = BuildLastOperation(lastReport),
                recentLogs = BuildRecentLogs(),
            };
            return JsonUtility.ToJson(snapshot, true);
        }

        private static RegistrySnapshot BuildRegistrySnapshot(RegistryDiagnosticsData source)
        {
            source = source ?? new RegistryDiagnosticsData();
            return new RegistrySnapshot
            {
                loaded = source.loaded,
                autoRefresh = source.autoRefresh,
                lastRefreshUtc = source.lastRefreshUtc,
                refreshStatus = RedactDiagnosticsText(source.refreshStatus),
                resourceCount = source.resourceCount,
                mappedResourceCount = source.mappedResourceCount,
                resourceErrorCount = source.resourceErrorCount,
                objectCount = source.objectCount,
                riggedObjectCount = source.riggedObjectCount,
                boundObjectCount = source.boundObjectCount,
                missingOrRemovedObjectCount = source.missingOrRemovedObjectCount,
                objectIssueCount = source.objectIssueCount,
            };
        }

        private static LastOperationSnapshot BuildLastOperation(BlenderSyncReportEntry entry)
        {
            if (entry == null)
                return new LastOperationSnapshot { available = false, fields = Array.Empty<DiagnosticsField>() };

            var fields = (entry.fields ?? new Dictionary<string, string>())
                .OrderBy(kv => kv.Key, StringComparer.Ordinal)
                .Where(kv => IsSafeReportField(kv.Key))
                .Take(MaxReportFields)
                .Select(kv => new DiagnosticsField
                {
                    key = RedactDiagnosticsText(kv.Key),
                    value = RedactDiagnosticsText(kv.Value),
                })
                .ToArray();
            return new LastOperationSnapshot
            {
                available = true,
                timestampUtc = entry.time.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"),
                category = RedactDiagnosticsText(entry.category),
                status = RedactDiagnosticsText(entry.status),
                summary = RedactDiagnosticsText(entry.summary),
                fields = fields,
            };
        }

        private static LogSnapshot[] BuildRecentLogs()
        {
            var logs = BlenderSyncLog.Recent;
            return logs
                .Skip(Math.Max(0, logs.Count - MaxRecentLogs))
                .Select(entry => new LogSnapshot
                {
                    timestampUtc = entry.timeUtc.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"),
                    level = RedactDiagnosticsText(entry.level),
                    category = RedactDiagnosticsText(entry.category),
                    eventName = RedactDiagnosticsText(entry.eventName),
                    summary = RedactDiagnosticsText(entry.summary),
                    fields = entry.fields
                        .OrderBy(pair => pair.Key, StringComparer.Ordinal)
                        .Take(MaxReportFields)
                        .Select(pair => new DiagnosticsField
                        {
                            key = RedactDiagnosticsText(pair.Key),
                            value = RedactDiagnosticsText(pair.Value),
                        })
                        .ToArray(),
                })
                .ToArray();
        }

        private static bool IsSafeReportField(string key)
        {
            switch (key)
            {
                case "updateIntent":
                case "triggerType":
                case "selectedObjectCount":
                case "supportedSelectedObjectCount":
                case "unsupportedSelectedObjectCount":
                case "resourceCountEstimate":
                case "objectAssemblyCount":
                case "riggedObjectCount":
                case "objectStateCount":
                case "resourceCount":
                case "successCount":
                case "failedCount":
                case "createdCount":
                case "updatedCount":
                case "skippedCount":
                    return true;
                default:
                    return false;
            }
        }

        private static string RedactDiagnosticsText(string value)
        {
            if (value == null)
                return null;
            var redacted = DiagnosticsPathRegex.Replace(value, "<path>");
            return redacted.Length <= MaxTextLength
                ? redacted
                : redacted.Substring(0, MaxTextLength) + "...";
        }

        private static string NormalizeHandshakePhase(string state)
        {
            switch (state)
            {
                case "handshake_confirmed": return "confirmed";
                case "protocol_mismatch": return "rejected";
                case "error": return "error";
                case null:
                case "": return "disconnected";
                default: return state;
            }
        }

        private static string FormatUnixTimestamp(long value)
        {
            if (value <= 0)
                return null;
            try
            {
                return DateTimeOffset.FromUnixTimeSeconds(value).UtcDateTime.ToString("yyyy-MM-ddTHH:mm:ssZ");
            }
            catch
            {
                return null;
            }
        }

        [Serializable]
        private sealed class DiagnosticsSnapshot
        {
            public string schemaVersion;
            public string timestampUtc;
            public string unityVersion;
            public SessionSnapshot session;
            public RegistrySnapshot registry;
            public LastOperationSnapshot lastOperation;
            public LogSnapshot[] recentLogs;
        }

        [Serializable]
        private sealed class SessionSnapshot
        {
            public bool transportConnected;
            public bool handshakeConfirmed;
            public string handshakePhase;
            public string protocolVersion;
            public bool legacyProtocol;
            public string[] negotiatedFeatures;
            public string endpoint;
            public string lastError;
            public string lastActivityUtc;
        }

        [Serializable]
        private sealed class RegistrySnapshot
        {
            public bool loaded;
            public bool autoRefresh;
            public string lastRefreshUtc;
            public string refreshStatus;
            public int resourceCount;
            public int mappedResourceCount;
            public int resourceErrorCount;
            public int objectCount;
            public int riggedObjectCount;
            public int boundObjectCount;
            public int missingOrRemovedObjectCount;
            public int objectIssueCount;
        }

        [Serializable]
        private sealed class LastOperationSnapshot
        {
            public bool available;
            public string timestampUtc;
            public string category;
            public string status;
            public string summary;
            public DiagnosticsField[] fields;
        }

        [Serializable]
        private sealed class LogSnapshot
        {
            public string timestampUtc;
            public string level;
            public string category;
            public string eventName;
            public string summary;
            public DiagnosticsField[] fields;
        }

        [Serializable]
        private sealed class DiagnosticsField
        {
            public string key;
            public string value;
        }
    }
}
#endif
