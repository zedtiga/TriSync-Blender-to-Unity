using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using BlenderSyncVNext.Diagnostics;
using NUnit.Framework;

namespace BlenderSyncVNext.Tests
{
    public sealed class LargePayloadAssemblerTests
    {
        private const string TypeName = "BlenderSyncVNext.SessionCore.LargePayloadAssembler";
        private const string CrcTypeName = "BlenderSyncVNext.SessionCore.Crc32Ieee";
        private MethodInfo _handleEnvelope;
        private MethodInfo _resetPending;
        private MethodInfo _computeCrc;
        private MethodInfo _computeStreamCrc;
        private MethodInfo _setVerboseOverride;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _handleEnvelope = ProductApi.RequireStaticMethod(TypeName, "HandleEnvelope", 3);
            _resetPending = ProductApi.RequireStaticMethod(TypeName, "ResetPendingAssemblies", 0);
            _computeCrc = ProductApi.RequireStaticMethod(CrcTypeName, "ComputeHex", 1);
            _computeStreamCrc = ProductApi.RequireStaticMethod(CrcTypeName, "ComputeStreamHex", 1);
            _setVerboseOverride = typeof(BlenderSyncLog).GetMethod(
                "SetVerboseOverride",
                BindingFlags.Static | BindingFlags.NonPublic);
            Assert.That(_setVerboseOverride, Is.Not.Null);
        }

        [SetUp]
        public void SetUp()
        {
            ResetPending();
            BlenderSyncLog.Clear();
            _setVerboseOverride.Invoke(null, new object[] { (bool?)false });
        }

        [TearDown]
        public void TearDown()
        {
            ResetPending();
            BlenderSyncLog.Clear();
            _setVerboseOverride.Invoke(null, new object[] { null });
        }

        [Test]
        public void LegacyBeginChunkEndRestoresOriginalJsonWithoutChecksum()
        {
            var payload = "{\"kind\":\"mesh\",\"value\":42}";
            var chunks = SplitPayload(payload, 12);
            var transferId = NewTransferId();

            Begin(transferId, "mesh.preview", payload, chunks.Length);
            for (var index = 0; index < chunks.Length; index++)
                Chunk(transferId, index, chunks.Length, chunks[index]);

            Assert.That(End(transferId, "mesh.preview", payload, chunks.Length), Is.EqualTo(payload));
        }

        [Test]
        public void ChunksCanArriveOutOfOrder()
        {
            var payload = "{\"message\":\"out-of-order\"}";
            var chunks = SplitPayload(payload, 9);
            var transferId = NewTransferId();

            Begin(transferId, "object.state", payload, chunks.Length);
            for (var index = chunks.Length - 1; index >= 0; index--)
                Chunk(transferId, index, chunks.Length, chunks[index]);

            Assert.That(End(transferId, "object.state", payload, chunks.Length), Is.EqualTo(payload));
        }

        [Test]
        public void DuplicateChunkDoesNotCorruptTransfer()
        {
            var payload = "{\"message\":\"duplicate\"}";
            var chunks = SplitPayload(payload, 10);
            var transferId = NewTransferId();

            Begin(transferId, "mesh.update", payload, chunks.Length);
            Chunk(transferId, 0, chunks.Length, chunks[0]);
            Chunk(transferId, 0, chunks.Length, Convert.ToBase64String(Encoding.UTF8.GetBytes("ignored")));
            for (var index = 1; index < chunks.Length; index++)
                Chunk(transferId, index, chunks.Length, chunks[index]);

            Assert.That(End(transferId, "mesh.update", payload, chunks.Length), Is.EqualTo(payload));
        }

        [Test]
        public void ChunkCountMismatchRejectsAndCleansTransfer()
        {
            var payload = "{\"message\":\"mismatch\"}";
            var transferId = NewTransferId();

            Begin(transferId, "mesh.update", payload, 2);
            Chunk(transferId, 0, 3, Convert.ToBase64String(Encoding.UTF8.GetBytes(payload)));

            Assert.That(End(transferId, "mesh.update", payload, 2), Is.Null);
        }

        [Test]
        public void ByteCountMismatchIsRejected()
        {
            var payload = "{\"message\":\"wrong-size\"}";
            var bytes = Encoding.UTF8.GetBytes(payload);
            var transferId = NewTransferId();

            Begin(transferId, "mesh.update", bytes.Length + 1, 1);
            Chunk(transferId, 0, 1, Convert.ToBase64String(bytes));

            Assert.That(End(transferId, "mesh.update", bytes.Length + 1, 1), Is.Null);
            AssertLatestWarning(
                "size_mismatch",
                $"[vNext][LargePayload] size_mismatch id={transferId} expected={bytes.Length + 1} actual={bytes.Length}");
        }

        [Test]
        public void InvalidBase64IsRejectedAndTransferIdCanBeReused()
        {
            var payload = "{\"message\":\"valid-after-invalid\"}";
            var bytes = Encoding.UTF8.GetBytes(payload);
            var transferId = NewTransferId();

            Begin(transferId, "mesh.update", 4, 1);
            Chunk(transferId, 0, 1, "not-base64");
            Assert.That(End(transferId, "mesh.update", 4, 1), Is.Null);
            AssertLatestWarningStartsWith(
                "decode_failed",
                $"[vNext][LargePayload] decode_failed id={transferId} chunk=0 error=");

            Begin(transferId, "mesh.update", payload, 1);
            Chunk(transferId, 0, 1, Convert.ToBase64String(bytes));
            Assert.That(End(transferId, "mesh.update", payload, 1), Is.EqualTo(payload));
        }

        [Test]
        public void IeeeCrc32MatchesStandardVector()
        {
            Assert.That(ComputeCrc(Encoding.ASCII.GetBytes("123456789")), Is.EqualTo("cbf43926"));
            using (var stream = new MemoryStream(Encoding.ASCII.GetBytes("123456789")))
                Assert.That(_computeStreamCrc.Invoke(null, new object[] { stream }), Is.EqualTo("cbf43926"));
        }

        [Test]
        public void RequiredChecksumAcceptsValidPayload()
        {
            var payload = "{\"message\":\"checksummed\"}";
            var chunks = SplitPayload(payload, 8);
            var transferId = NewTransferId();

            Begin(transferId, "mesh.update", payload, chunks.Length, true);
            for (var index = 0; index < chunks.Length; index++)
                Chunk(transferId, index, chunks.Length, chunks[index], true);

            Assert.That(
                End(transferId, "mesh.update", payload, chunks.Length, true, "crc32-ieee", ComputeCrc(payload)),
                Is.EqualTo(payload));
        }

        [Test]
        public void RequiredChecksumMismatchIsRejected()
        {
            var payload = "{\"message\":\"checksum-mismatch\"}";
            var bytes = Encoding.UTF8.GetBytes(payload);
            var transferId = NewTransferId();

            Begin(transferId, "mesh.update", bytes.Length, 1, true);
            Chunk(transferId, 0, 1, Convert.ToBase64String(bytes), true);

            Assert.That(
                End(transferId, "mesh.update", bytes.Length, 1, true, "crc32-ieee", "00000000"),
                Is.Null);
            AssertLatestWarning(
                "checksum_mismatch",
                $"[vNext][LargePayload] checksum_mismatch id={transferId} expected=00000000 actual={ComputeCrc(bytes)}");
        }

        [Test]
        public void RequiredChecksumMissingIsRejected()
        {
            var payload = "{\"message\":\"checksum-missing\"}";
            var bytes = Encoding.UTF8.GetBytes(payload);
            var transferId = NewTransferId();

            Begin(transferId, "mesh.update", bytes.Length, 1, true);
            Chunk(transferId, 0, 1, Convert.ToBase64String(bytes), true);

            Assert.That(End(transferId, "mesh.update", bytes.Length, 1, true, "crc32-ieee", null), Is.Null);
            AssertLatestWarning(
                "checksum_rejected",
                $"[vNext][LargePayload] checksum_rejected id={transferId} reason=missing");
        }

        [Test]
        public void RequiredChecksumRejectsMalformedAndUnsupportedMetadata()
        {
            var payload = "{\"message\":\"checksum-metadata\"}";
            var bytes = Encoding.UTF8.GetBytes(payload);
            var malformedId = NewTransferId();

            Begin(malformedId, "mesh.update", bytes.Length, 1, true);
            Chunk(malformedId, 0, 1, Convert.ToBase64String(bytes), true);
            Assert.That(End(malformedId, "mesh.update", bytes.Length, 1, true, "crc32-ieee", "ABC123"), Is.Null);
            AssertLatestWarning(
                "checksum_rejected",
                $"[vNext][LargePayload] checksum_rejected id={malformedId} reason=malformed");

            var unsupportedId = NewTransferId();
            Begin(unsupportedId, "mesh.update", bytes.Length, 1, true);
            Chunk(unsupportedId, 0, 1, Convert.ToBase64String(bytes), true);
            Assert.That(End(unsupportedId, "mesh.update", bytes.Length, 1, true, "crc32c", ComputeCrc(bytes)), Is.Null);
            AssertLatestWarning(
                "checksum_rejected",
                $"[vNext][LargePayload] checksum_rejected id={unsupportedId} reason=unsupported_algorithm algorithm=crc32c");
        }

        [Test]
        public void ResetPendingAssembliesDropsInFlightTransfer()
        {
            var payload = "{\"message\":\"old-session\"}";
            var bytes = Encoding.UTF8.GetBytes(payload);
            var transferId = NewTransferId();

            Begin(transferId, "mesh.update", bytes.Length, 1, true);
            Chunk(transferId, 0, 1, Convert.ToBase64String(bytes), true);

            Assert.That(ResetPending(), Is.EqualTo(1));
            Assert.That(
                End(transferId, "mesh.update", bytes.Length, 1, true, "crc32-ieee", ComputeCrc(bytes)),
                Is.Null);
        }

        [Test]
        public void TransferCannotCrossNegotiationState()
        {
            var payload = "{\"message\":\"renegotiated\"}";
            var bytes = Encoding.UTF8.GetBytes(payload);
            var transferId = NewTransferId();

            Begin(transferId, "mesh.update", bytes.Length, 1, false);
            Chunk(transferId, 0, 1, Convert.ToBase64String(bytes), true);
            AssertLatestWarning(
                "chunk_rejected",
                $"[vNext][LargePayload] chunk_rejected id={transferId} reason=negotiation_changed");

            Assert.That(End(transferId, "mesh.update", bytes.Length, 1, true, "crc32-ieee", ComputeCrc(bytes)), Is.Null);
        }

        private string Invoke(string rawJson, string envelopeType, bool checksumRequired = false)
        {
            return (string)_handleEnvelope.Invoke(null, new object[] { rawJson, envelopeType, checksumRequired });
        }

        private void Begin(string transferId, string originalType, string payload, int chunkCount, bool checksumRequired = false)
        {
            Begin(transferId, originalType, Encoding.UTF8.GetByteCount(payload), chunkCount, checksumRequired);
        }

        private void Begin(string transferId, string originalType, int totalBytes, int chunkCount, bool checksumRequired = false)
        {
            Invoke(
                $"{{\"type\":\"large_payload.begin\",\"transferId\":\"{transferId}\",\"originalType\":\"{originalType}\",\"totalBytes\":{totalBytes},\"chunkCount\":{chunkCount}}}",
                "large_payload.begin",
                checksumRequired);
        }

        private void Chunk(string transferId, int index, int chunkCount, string data, bool checksumRequired = false)
        {
            Invoke(
                $"{{\"type\":\"large_payload.chunk\",\"transferId\":\"{transferId}\",\"index\":{index},\"chunkCount\":{chunkCount},\"data\":\"{data}\"}}",
                "large_payload.chunk",
                checksumRequired);
        }

        private string End(
            string transferId,
            string originalType,
            string payload,
            int chunkCount,
            bool checksumRequired = false,
            string checksumAlgorithm = null,
            string checksum = null)
        {
            return End(
                transferId,
                originalType,
                Encoding.UTF8.GetByteCount(payload),
                chunkCount,
                checksumRequired,
                checksumAlgorithm,
                checksum);
        }

        private string End(
            string transferId,
            string originalType,
            int totalBytes,
            int chunkCount,
            bool checksumRequired = false,
            string checksumAlgorithm = null,
            string checksum = null)
        {
            var checksumFields = string.Empty;
            if (checksumAlgorithm != null)
                checksumFields += $",\"checksumAlgorithm\":\"{checksumAlgorithm}\"";
            if (checksum != null)
                checksumFields += $",\"checksum\":\"{checksum}\"";
            return Invoke(
                $"{{\"type\":\"large_payload.end\",\"transferId\":\"{transferId}\",\"originalType\":\"{originalType}\",\"totalBytes\":{totalBytes},\"sentBytes\":{totalBytes},\"chunkCount\":{chunkCount}{checksumFields}}}",
                "large_payload.end",
                checksumRequired);
        }

        private string ComputeCrc(string payload)
        {
            return ComputeCrc(Encoding.UTF8.GetBytes(payload));
        }

        private string ComputeCrc(byte[] bytes)
        {
            return (string)_computeCrc.Invoke(null, new object[] { bytes });
        }

        private int ResetPending()
        {
            return (int)_resetPending.Invoke(null, Array.Empty<object>());
        }

        private static void AssertLatestWarning(string eventName, string summary)
        {
            var entry = BlenderSyncLog.Recent.Last();
            Assert.That(entry.category, Is.EqualTo("LargePayload"));
            Assert.That(entry.level, Is.EqualTo("WARN"));
            Assert.That(entry.eventName, Is.EqualTo(eventName));
            Assert.That(entry.summary, Is.EqualTo(summary));
        }

        private static void AssertLatestWarningStartsWith(string eventName, string summaryPrefix)
        {
            var entry = BlenderSyncLog.Recent.Last();
            Assert.That(entry.category, Is.EqualTo("LargePayload"));
            Assert.That(entry.level, Is.EqualTo("WARN"));
            Assert.That(entry.eventName, Is.EqualTo(eventName));
            Assert.That(entry.summary, Does.StartWith(summaryPrefix));
        }

        private static string[] SplitPayload(string payload, int firstChunkByteCount)
        {
            var bytes = Encoding.UTF8.GetBytes(payload);
            var split = Math.Min(firstChunkByteCount, bytes.Length - 1);
            var first = new byte[split];
            var second = new byte[bytes.Length - split];
            Buffer.BlockCopy(bytes, 0, first, 0, first.Length);
            Buffer.BlockCopy(bytes, split, second, 0, second.Length);
            return new[]
            {
                Convert.ToBase64String(first),
                Convert.ToBase64String(second),
            };
        }

        private static string NewTransferId()
        {
            return "test-" + Guid.NewGuid().ToString("N");
        }
    }
}
