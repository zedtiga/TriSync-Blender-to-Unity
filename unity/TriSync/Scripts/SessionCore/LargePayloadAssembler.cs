using System;
using System.Collections.Generic;
using System.Text;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;

namespace BlenderSyncVNext.SessionCore
{
    internal static class LargePayloadAssembler
    {
        private const int MaxChunkCount = 65536;
        private const int MaxTotalBytes = 512 * 1024 * 1024;
        private const int MaxPendingAssemblies = 32;
        private const double PendingAssemblyTtlSeconds = 15 * 60.0;
        private const double PendingCleanupIntervalSeconds = 30.0;
        private static readonly Dictionary<string, LargePayloadAssembly> Pending = new Dictionary<string, LargePayloadAssembly>();
        private static readonly object PendingLock = new object();
        private static DateTime _lastPendingCleanupUtc = DateTime.MinValue;

        public static string HandleEnvelope(string rawJson, string envelopeType, bool checksumRequired)
        {
            try
            {
                if (envelopeType == "large_payload.begin")
                {
                    var begin = JsonUtility.FromJson<LargePayloadBeginEnvelope>(rawJson);
                    var transferId = NormalizeTransferId(begin?.transferId);
                    if (begin == null || string.IsNullOrWhiteSpace(transferId) || begin.chunkCount <= 0)
                        return null;
                    if (begin.chunkCount > MaxChunkCount || begin.totalBytes < 0 || begin.totalBytes > MaxTotalBytes)
                    {
                        Warn(
                            "begin_rejected",
                            $"[vNext][LargePayload] begin_rejected id={transferId} bytes={begin.totalBytes} chunks={begin.chunkCount}");
                        return null;
                    }

                    lock (PendingLock)
                    {
                        var nowUtc = DateTime.UtcNow;
                        CleanupExpiredPendingLocked(nowUtc);
                        if (!Pending.ContainsKey(transferId) && Pending.Count >= MaxPendingAssemblies)
                        {
                            Warn(
                                "begin_rejected",
                                $"[vNext][LargePayload] begin_rejected id={transferId} reason=pending_limit count={Pending.Count}");
                            return null;
                        }
                        Pending[transferId] = new LargePayloadAssembly
                        {
                            transferId = transferId,
                            originalType = NormalizeOptional(begin.originalType),
                            totalBytes = begin.totalBytes,
                            chunkCount = begin.chunkCount,
                            chunks = new string[begin.chunkCount],
                            startedUtcTicks = nowUtc.Ticks,
                            checksumRequired = checksumRequired,
                        };
                    }

                    BlenderSyncLog.Trace(
                        "LargePayload",
                        "begin",
                        () => $"[vNext][LargePayload] begin id={transferId} type={begin.originalType} bytes={begin.totalBytes} chunks={begin.chunkCount}");
                    return null;
                }

                if (envelopeType == "large_payload.chunk")
                {
                    var chunk = JsonUtility.FromJson<LargePayloadChunkEnvelope>(rawJson);
                    var transferId = NormalizeTransferId(chunk?.transferId);
                    if (chunk == null || string.IsNullOrWhiteSpace(transferId) || chunk.index < 0)
                        return null;

                    lock (PendingLock)
                    {
                        CleanupExpiredPendingLocked(DateTime.UtcNow);
                        if (!Pending.TryGetValue(transferId, out var assembly) || assembly.chunks == null)
                            return null;
                        if (assembly.checksumRequired != checksumRequired)
                        {
                            Warn(
                                "chunk_rejected",
                                $"[vNext][LargePayload] chunk_rejected id={transferId} reason=negotiation_changed");
                            Pending.Remove(transferId);
                            return null;
                        }
                        if (chunk.chunkCount < 0)
                        {
                            Warn(
                                "chunk_rejected",
                                $"[vNext][LargePayload] chunk_rejected id={transferId} invalidChunks={chunk.chunkCount}");
                            Pending.Remove(transferId);
                            return null;
                        }
                        if (chunk.chunkCount > 0 && chunk.chunkCount != assembly.chunkCount)
                        {
                            Pending.Remove(transferId);
                            return null;
                        }
                        if (chunk.index >= assembly.chunks.Length)
                        {
                            Pending.Remove(transferId);
                            return null;
                        }
                        if (assembly.chunks[chunk.index] == null)
                        {
                            assembly.chunks[chunk.index] = chunk.data ?? string.Empty;
                            assembly.receivedCount++;
                        }
                    }

                    return null;
                }

                if (envelopeType == "large_payload.end")
                {
                    var end = JsonUtility.FromJson<LargePayloadEndEnvelope>(rawJson);
                    var transferId = NormalizeTransferId(end?.transferId);
                    if (end == null || string.IsNullOrWhiteSpace(transferId))
                        return null;

                    LargePayloadAssembly assembly;
                    lock (PendingLock)
                    {
                        CleanupExpiredPendingLocked(DateTime.UtcNow);
                        if (!Pending.TryGetValue(transferId, out assembly))
                            return null;
                        if (assembly.checksumRequired != checksumRequired)
                        {
                            Warn(
                                "end_rejected",
                                $"[vNext][LargePayload] end_rejected id={transferId} reason=negotiation_changed");
                            Pending.Remove(transferId);
                            return null;
                        }
                        if (end.chunkCount < 0 || end.totalBytes < 0 || end.sentBytes < 0)
                        {
                            Warn(
                                "end_rejected",
                                $"[vNext][LargePayload] end_rejected id={transferId} chunks={end.chunkCount} totalBytes={end.totalBytes} sentBytes={end.sentBytes}");
                            Pending.Remove(transferId);
                            return null;
                        }
                        if (end.chunkCount > 0 && end.chunkCount != assembly.chunkCount)
                        {
                            Warn(
                                "end_rejected",
                                $"[vNext][LargePayload] end_rejected id={transferId} expectedChunks={assembly.chunkCount} actualChunks={end.chunkCount}");
                            Pending.Remove(transferId);
                            return null;
                        }
                        var endOriginalType = NormalizeOptional(end.originalType);
                        if (!string.IsNullOrWhiteSpace(endOriginalType) && !string.Equals(endOriginalType, assembly.originalType, StringComparison.Ordinal))
                        {
                            Warn(
                                "end_rejected",
                                $"[vNext][LargePayload] end_rejected id={transferId} expectedType={assembly.originalType} actualType={endOriginalType}");
                            Pending.Remove(transferId);
                            return null;
                        }
                        if (end.totalBytes > 0 && end.totalBytes != assembly.totalBytes)
                        {
                            Warn(
                                "end_rejected",
                                $"[vNext][LargePayload] end_rejected id={transferId} expectedBytes={assembly.totalBytes} actualBytes={end.totalBytes}");
                            Pending.Remove(transferId);
                            return null;
                        }
                        if (end.sentBytes > 0 && end.totalBytes > 0 && end.sentBytes != end.totalBytes)
                        {
                            Warn(
                                "end_rejected",
                                $"[vNext][LargePayload] end_rejected id={transferId} sentBytes={end.sentBytes} totalBytes={end.totalBytes}");
                            Pending.Remove(transferId);
                            return null;
                        }
                        if (assembly.receivedCount < assembly.chunkCount)
                        {
                            Warn(
                                "incomplete",
                                $"[vNext][LargePayload] incomplete id={transferId} received={assembly.receivedCount}/{assembly.chunkCount}");
                            Pending.Remove(transferId);
                            return null;
                        }

                        Pending.Remove(transferId);
                    }

                    var bytes = new List<byte>();
                    for (var i = 0; i < assembly.chunks.Length; i++)
                    {
                        if (assembly.chunks[i] == null)
                            return null;
                        try
                        {
                            var decoded = Convert.FromBase64String(assembly.chunks[i]);
                            if (decoded.Length > MaxTotalBytes || bytes.Count > MaxTotalBytes - decoded.Length)
                            {
                                Warn(
                                    "decode_rejected",
                                    $"[vNext][LargePayload] decode_rejected id={transferId} reason=max_bytes_exceeded");
                                return null;
                            }
                            bytes.AddRange(decoded);
                        }
                        catch (FormatException ex)
                        {
                            Warn(
                                "decode_failed",
                                $"[vNext][LargePayload] decode_failed id={transferId} chunk={i} error={ex.Message}");
                            return null;
                        }
                    }

                    var actualBytes = bytes.Count;
                    if (assembly.totalBytes > 0 && actualBytes != assembly.totalBytes)
                    {
                        Warn(
                            "size_mismatch",
                            $"[vNext][LargePayload] size_mismatch id={transferId} expected={assembly.totalBytes} actual={actualBytes}");
                        return null;
                    }
                    var payloadBytes = bytes.ToArray();
                    if (assembly.checksumRequired && !ValidateChecksum(transferId, end, payloadBytes))
                        return null;
                    var raw = Encoding.UTF8.GetString(payloadBytes);
                    return raw;
                }
            }
            catch (Exception ex)
            {
                Warn(
                    "envelope_failed",
                    $"[vNext][LargePayload] envelope_failed type={envelopeType} error={ex.Message}");
                return null;
            }

            return null;
        }

        public static int ResetPendingAssemblies()
        {
            lock (PendingLock)
            {
                var removed = Pending.Count;
                Pending.Clear();
                _lastPendingCleanupUtc = DateTime.MinValue;
                return removed;
            }
        }

        private static bool ValidateChecksum(string transferId, LargePayloadEndEnvelope end, byte[] payloadBytes)
        {
            if (!string.Equals(end.checksumAlgorithm, "crc32-ieee", StringComparison.Ordinal))
            {
                var algorithm = string.IsNullOrEmpty(end.checksumAlgorithm) ? "missing" : end.checksumAlgorithm;
                Warn(
                    "checksum_rejected",
                    $"[vNext][LargePayload] checksum_rejected id={transferId} reason=unsupported_algorithm algorithm={algorithm}");
                return false;
            }
            if (!IsLowerHexChecksum(end.checksum))
            {
                var reason = string.IsNullOrEmpty(end.checksum) ? "missing" : "malformed";
                Warn(
                    "checksum_rejected",
                    $"[vNext][LargePayload] checksum_rejected id={transferId} reason={reason}");
                return false;
            }

            var actual = Crc32Ieee.ComputeHex(payloadBytes);
            if (string.Equals(actual, end.checksum, StringComparison.Ordinal))
                return true;
            Warn(
                "checksum_mismatch",
                $"[vNext][LargePayload] checksum_mismatch id={transferId} expected={end.checksum} actual={actual}");
            return false;
        }

        private static bool IsLowerHexChecksum(string value)
        {
            if (value == null || value.Length != 8)
                return false;
            for (var i = 0; i < value.Length; i++)
            {
                var character = value[i];
                if ((character < '0' || character > '9') && (character < 'a' || character > 'f'))
                    return false;
            }
            return true;
        }

        private static void CleanupExpiredPendingLocked(DateTime nowUtc)
        {
            if ((nowUtc - _lastPendingCleanupUtc).TotalSeconds < PendingCleanupIntervalSeconds)
                return;
            _lastPendingCleanupUtc = nowUtc;
            if (Pending.Count == 0)
                return;

            var cutoffTicks = nowUtc.AddSeconds(-PendingAssemblyTtlSeconds).Ticks;
            List<string> expired = null;
            foreach (var item in Pending)
            {
                if (item.Value == null || item.Value.startedUtcTicks <= 0 || item.Value.startedUtcTicks < cutoffTicks)
                    (expired ?? (expired = new List<string>())).Add(item.Key);
            }

            if (expired == null || expired.Count == 0)
                return;

            foreach (var key in expired)
                Pending.Remove(key);
            Warn(
                "cleanup_expired",
                $"[vNext][LargePayload] cleanup_expired pendingRemoved={expired.Count} pendingRemaining={Pending.Count}");
        }

        private static void Warn(string eventName, string message)
        {
            BlenderSyncLog.Warn("LargePayload", eventName, message);
        }

        private static string NormalizeTransferId(string transferId)
        {
            return (transferId ?? string.Empty).Trim();
        }

        private static string NormalizeOptional(string value)
        {
            var normalized = value?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

        private sealed class LargePayloadAssembly
        {
            public string transferId;
            public string originalType;
            public int totalBytes;
            public int chunkCount;
            public int receivedCount;
            public string[] chunks;
            public long startedUtcTicks;
            public bool checksumRequired;
        }

#pragma warning disable 0649 // JsonUtility populates these DTO fields via reflection.
        [Serializable]
        private sealed class LargePayloadBeginEnvelope
        {
            public string type;
            public string transferId;
            public string originalType;
            public int totalBytes;
            public int chunkCount;
            public string encoding;
            public long timestamp;
        }

        [Serializable]
        private sealed class LargePayloadChunkEnvelope
        {
            public string type;
            public string transferId;
            public int index;
            public int chunkCount;
            public string data;
        }

        [Serializable]
        private sealed class LargePayloadEndEnvelope
        {
            public string type;
            public string transferId;
            public string originalType;
            public int totalBytes;
            public int chunkCount;
            public int sentBytes;
            public string checksumAlgorithm;
            public string checksum;
            public long timestamp;
        }
#pragma warning restore 0649
    }
}
