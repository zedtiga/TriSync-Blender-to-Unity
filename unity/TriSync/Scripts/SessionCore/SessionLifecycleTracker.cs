using System;
using System.Collections.Generic;

namespace BlenderSyncVNext.SessionCore
{
    internal sealed class SessionLifecycleTracker
    {
        private const int MaxTraceDetailLength = 512;
        private static readonly List<string> LifecycleTrace = new List<string>();
        private static readonly object LifecycleLock = new object();
        private static string _lastLifecycleState = "disconnected";
        private static long _lastLifecycleAt;

        public string Status { get; private set; } = "disconnected";

        public static IReadOnlyList<string> GetLifecycleTrace()
        {
            lock (LifecycleLock)
            {
                return LifecycleTrace.ToArray();
            }
        }

        public static string LastLifecycleState
        {
            get
            {
                lock (LifecycleLock)
                {
                    return _lastLifecycleState;
                }
            }
        }

        public static long LastLifecycleAt
        {
            get
            {
                lock (LifecycleLock)
                {
                    return _lastLifecycleAt;
                }
            }
        }

        public void TraceOnly(string state, string detail = null)
        {
            Trace(state, detail);
        }

        public void SetStatus(string state, string detail, Action<string> notifyStatusChanged)
        {
            Status = NormalizeState(state);
            TraceNormalized(Status, NormalizeDetail(detail));
            notifyStatusChanged?.Invoke(Status);
        }

        private static void Trace(string state, string detail)
        {
            TraceNormalized(NormalizeState(state), NormalizeDetail(detail));
        }

        private static void TraceNormalized(string state, string detail)
        {
            lock (LifecycleLock)
            {
                _lastLifecycleState = state;
                _lastLifecycleAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                var line = string.IsNullOrWhiteSpace(detail)
                    ? $"[vNext][Session] state={state} at={_lastLifecycleAt}"
                    : $"[vNext][Session] state={state} at={_lastLifecycleAt} detail={detail}";
                LifecycleTrace.Add(line);
                if (LifecycleTrace.Count > 20)
                    LifecycleTrace.RemoveAt(0);
            }
        }

        private static string NormalizeState(string state)
        {
            var normalized = state?.Trim();
            return string.IsNullOrEmpty(normalized) ? "unknown" : normalized;
        }

        private static string NormalizeDetail(string detail)
        {
            var normalized = detail?.Trim();
            if (string.IsNullOrEmpty(normalized))
                return null;

            normalized = normalized
                .Replace('\r', ' ')
                .Replace('\n', ' ');

            return normalized.Length <= MaxTraceDetailLength
                ? normalized
                : normalized.Substring(0, MaxTraceDetailLength) + "...";
        }
    }
}
