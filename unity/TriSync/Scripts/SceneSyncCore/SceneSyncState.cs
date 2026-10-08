using System;

namespace BlenderSyncVNext.SceneSyncCore
{
    [Serializable]
    public sealed class SceneSyncState
    {
        public bool active;
        public string lastResult;      // applied | skipped | error
        public string lastSkipReason;  // session_inactive | not_mapped | child_not_mapped | parent_not_mapped | invalid_payload | below_threshold | rate_limited
        public string lastError;
        public string lastPairId;
        public string lastReferenceKind;
        public string lastReferenceRef;
        public long lastHandledAt;
        public int applyCount;
        public int skipCount;
        public int errorCount;
    }

    public static class SceneSyncStateStore
    {
        private const int MaxDetailLength = 512;
        private static readonly SceneSyncState _current = new SceneSyncState();
        private static readonly object CurrentLock = new object();

        public static SceneSyncState Current
        {
            get
            {
                lock (CurrentLock)
                {
                    return Clone(_current);
                }
            }
        }

        public static void MarkApplied(string pairId)
        {
            lock (CurrentLock)
            {
                _current.active = true;
                _current.lastResult = "applied";
                _current.lastSkipReason = null;
                _current.lastError = null;
                _current.lastPairId = NormalizeOptional(pairId);
                ClearReference();
                _current.lastHandledAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                _current.applyCount++;
            }
        }

        public static void MarkReferenceApplied(string pairId, string resourceKind, string resourceRef)
        {
            lock (CurrentLock)
            {
                _current.active = true;
                _current.lastResult = "applied";
                _current.lastSkipReason = null;
                _current.lastError = null;
                _current.lastPairId = NormalizeOptional(pairId);
                _current.lastReferenceKind = NormalizeOptional(resourceKind);
                _current.lastReferenceRef = NormalizeDetail(resourceRef);
                _current.lastHandledAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                _current.applyCount++;
            }
        }

        public static void MarkSkipped(string reason, string pairId = null)
        {
            lock (CurrentLock)
            {
                _current.active = true;
                _current.lastResult = "skipped";
                _current.lastSkipReason = NormalizeDetail(reason);
                _current.lastError = null;
                _current.lastPairId = NormalizeOptional(pairId);
                ClearReference();
                _current.lastHandledAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                _current.skipCount++;
            }
        }

        public static void MarkError(string error, string pairId = null)
        {
            lock (CurrentLock)
            {
                _current.active = true;
                _current.lastResult = "error";
                _current.lastSkipReason = null;
                _current.lastError = NormalizeDetail(error);
                _current.lastPairId = NormalizeOptional(pairId);
                ClearReference();
                _current.lastHandledAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                _current.errorCount++;
            }
        }

        private static void ClearReference()
        {
            _current.lastReferenceKind = null;
            _current.lastReferenceRef = null;
        }

        private static SceneSyncState Clone(SceneSyncState state)
        {
            return new SceneSyncState
            {
                active = state.active,
                lastResult = state.lastResult,
                lastSkipReason = state.lastSkipReason,
                lastError = state.lastError,
                lastPairId = state.lastPairId,
                lastReferenceKind = state.lastReferenceKind,
                lastReferenceRef = state.lastReferenceRef,
                lastHandledAt = state.lastHandledAt,
                applyCount = state.applyCount,
                skipCount = state.skipCount,
                errorCount = state.errorCount,
            };
        }

        private static string NormalizeOptional(string value)
        {
            var normalized = value?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

        private static string NormalizeDetail(string value)
        {
            var normalized = NormalizeOptional(value);
            if (string.IsNullOrEmpty(normalized))
                return null;

            normalized = normalized
                .Replace('\r', ' ')
                .Replace('\n', ' ');

            return normalized.Length <= MaxDetailLength
                ? normalized
                : normalized.Substring(0, MaxDetailLength) + "...";
        }
    }
}
