using System;
using System.Collections.Generic;
using System.IO;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;

namespace BlenderSyncVNext.SessionCore
{
    internal sealed class SessionHandshakeController
    {
        private static readonly List<string> AckTrace = new List<string>();
        private static readonly object AckTraceLock = new object();
        private static string _lastAckPayload;
        private static string _lastProtocolError;

        private readonly Action<string> _emitPayload;
        private readonly Action<string> _markCounterpartObserved;
        private readonly Action<string> _markHandshakeConfirmed;
        private readonly Action<string> _reportProtocolError;

        private string _currentHandshakeId;
        private string _targetEndpoint;
        private bool _legacyProtocol;
        private int? _peerProtocolVersion;
        private string _peerApplicationVersion;
        private string[] _negotiatedFeatures = Array.Empty<string>();
        private bool _legacyProtocolReported;

        public SessionHandshakeController(
            Action<string> emitPayload,
            Action<string> markCounterpartObserved,
            Action<string> markHandshakeConfirmed,
            Action<string> reportProtocolError)
        {
            _emitPayload = emitPayload;
            _markCounterpartObserved = markCounterpartObserved;
            _markHandshakeConfirmed = markHandshakeConfirmed;
            _reportProtocolError = reportProtocolError;
        }

        public static string HandshakeCriterion => "strict handshake with explicit final confirmation: hello -> ack -> final-ack -> confirmed";
        public static string LastAckPayload
        {
            get
            {
                lock (AckTraceLock)
                {
                    return _lastAckPayload;
                }
            }
        }
        public static string LastProtocolError
        {
            get
            {
                lock (AckTraceLock)
                {
                    return _lastProtocolError;
                }
            }
        }

        public static IReadOnlyList<string> GetAckTrace()
        {
            lock (AckTraceLock)
            {
                return AckTrace.ToArray();
            }
        }
        public string CurrentHandshakeId => _currentHandshakeId;
        public bool LegacyProtocol => _legacyProtocol;
        public int? PeerProtocolVersion => _peerProtocolVersion;
        public string PeerApplicationVersion => _peerApplicationVersion;
        public string[] NegotiatedFeatures => (string[])_negotiatedFeatures.Clone();

        public bool IsFeatureNegotiated(string feature)
        {
            var expected = (feature ?? string.Empty).Trim();
            if (expected.Length == 0)
                return false;
            var features = _negotiatedFeatures;
            for (var i = 0; i < features.Length; i++)
            {
                if (string.Equals(features[i], expected, StringComparison.Ordinal))
                    return true;
            }
            return false;
        }

        public void ResetForConnect(string endpoint)
        {
            _targetEndpoint = NormalizeOptional(endpoint);
            _currentHandshakeId = null;
            lock (AckTraceLock)
            {
                _lastAckPayload = null;
                _lastProtocolError = null;
            }
            _legacyProtocol = false;
            _peerProtocolVersion = null;
            _peerApplicationVersion = null;
            _negotiatedFeatures = Array.Empty<string>();
            _legacyProtocolReported = false;
        }

        public void SetEndpoint(string endpoint)
        {
            _targetEndpoint = NormalizeOptional(endpoint);
        }

        public void HandleSessionHello(string rawJson)
        {
            if (!TryParse(rawJson, "hello", out SessionHelloEnvelope hello))
                return;

            var handshakeId = NormalizeHandshakeId(hello.handshakeId);
            if (string.IsNullOrWhiteSpace(handshakeId))
            {
                BlenderSyncLog.Warn(
                    "Session",
                    "hello_invalid",
                    "Ignored a session hello without a handshake identifier.");
                return;
            }

            _currentHandshakeId = handshakeId;
            var negotiation = SessionProtocolContract.NegotiateOffer(
                SessionProtocolContract.HasJsonField(rawJson, "protocolVersion"),
                hello.protocolVersion,
                hello.features);
            if (!ApplyNegotiationOrReject(negotiation, _currentHandshakeId))
                return;
            BlenderSyncLog.Trace(
                "Session",
                "hello_received",
                () => "Received the Blender session hello.");
            _markCounterpartObserved?.Invoke("hello_received");
            EmitSessionAck(_currentHandshakeId, "hello_observed");
        }

        public void HandleSessionAck(string rawJson)
        {
            if (!TryParse(rawJson, "session_ack", out SessionAckEnvelope ack))
                return;

            var handshakeId = NormalizeHandshakeId(ack.handshakeId);
            if (string.IsNullOrWhiteSpace(handshakeId))
            {
                BlenderSyncLog.Warn(
                    "Session",
                    "session_ack_invalid",
                    "Ignored a session acknowledgement without a handshake identifier.");
                return;
            }

            if (!string.IsNullOrWhiteSpace(_currentHandshakeId) && handshakeId != _currentHandshakeId)
            {
                BlenderSyncLog.Warn(
                    "Session",
                    "session_ack_mismatch",
                    "Ignored a session acknowledgement for a different handshake.");
                return;
            }

            _currentHandshakeId = handshakeId;
            var negotiation = SessionProtocolContract.AcceptNegotiatedReply(
                SessionProtocolContract.HasJsonField(rawJson, "protocolVersion"),
                ack.protocolVersion,
                ack.negotiatedFeatures);
            if (!ApplyNegotiationOrReject(negotiation, _currentHandshakeId))
                return;
            RecordPeerApplicationVersion(ack.blenderVersion);
            _markCounterpartObserved?.Invoke("session_ack_received");
            EmitFinalHandshakeAck(_currentHandshakeId);
        }

        public void HandleFinalHandshakeAck(string rawJson)
        {
            if (!TryParse(rawJson, "final_handshake_ack", out SessionFinalAckEnvelope ack))
                return;

            var handshakeId = NormalizeHandshakeId(ack.handshakeId);
            if (string.IsNullOrWhiteSpace(handshakeId))
            {
                BlenderSyncLog.Warn(
                    "Session",
                    "final_ack_invalid",
                    "Ignored a final acknowledgement without a handshake identifier.");
                return;
            }

            if (!string.IsNullOrWhiteSpace(_currentHandshakeId) && handshakeId != _currentHandshakeId)
            {
                BlenderSyncLog.Warn(
                    "Session",
                    "final_ack_mismatch",
                    "Ignored a final acknowledgement for a different handshake.");
                return;
            }

            _currentHandshakeId = handshakeId;
            if (!ValidateEchoOrReject(rawJson, ack.protocolVersion, ack.negotiatedFeatures, _currentHandshakeId))
                return;
            RecordPeerApplicationVersion(ack.blenderVersion);
            BlenderSyncLog.Trace(
                "Session",
                "final_ack_received",
                () => "Received the final handshake acknowledgement.");
            // Keep as compatibility signal only; strict confirmed state now waits for explicit
            // `session.handshake_confirmed` from Blender side.
        }

        public void HandleHandshakeConfirmed(string rawJson)
        {
            if (!TryParse(rawJson, "handshake_confirmed", out SessionFinalAckEnvelope ack))
                return;

            var handshakeId = NormalizeHandshakeId(ack.handshakeId);
            if (string.IsNullOrWhiteSpace(handshakeId))
            {
                BlenderSyncLog.Warn(
                    "Session",
                    "confirmation_invalid",
                    "Ignored a handshake confirmation without a handshake identifier.");
                return;
            }

            if (!string.IsNullOrWhiteSpace(_currentHandshakeId) && handshakeId != _currentHandshakeId)
            {
                BlenderSyncLog.Warn(
                    "Session",
                    "confirmation_mismatch",
                    "Ignored a confirmation for a different handshake.");
                return;
            }

            _currentHandshakeId = handshakeId;
            if (!ValidateEchoOrReject(rawJson, ack.protocolVersion, ack.negotiatedFeatures, _currentHandshakeId))
                return;
            RecordPeerApplicationVersion(ack.blenderVersion);
            BlenderSyncLog.Info(
                "Session",
                "handshake_confirmed",
                "Blender confirmed the session handshake.",
                new Dictionary<string, object>
                {
                    { "legacyProtocol", _legacyProtocol },
                    { "peerProtocolVersion", _peerProtocolVersion },
                    { "peerApplicationVersion", _peerApplicationVersion },
                    { "featureCount", _negotiatedFeatures.Length },
                });
            _markHandshakeConfirmed?.Invoke("confirmed_from_blender");
        }

        public void HandleHandshakeReject(string rawJson)
        {
            if (!TryParse(rawJson, "handshake_reject", out SessionRejectEnvelope reject))
                return;
            var handshakeId = NormalizeHandshakeId(reject.handshakeId);
            if (!string.IsNullOrWhiteSpace(_currentHandshakeId)
                && !string.IsNullOrWhiteSpace(handshakeId)
                && !string.Equals(handshakeId, _currentHandshakeId, StringComparison.Ordinal))
            {
                BlenderSyncLog.Warn(
                    "Session",
                    "handshake_reject_mismatch",
                    "Ignored a rejection for a different handshake.");
                return;
            }
            var error = SessionProtocolContract.BuildProtocolError(reject.reason, reject.localProtocolVersion);
            RecordProtocolError(error);
            BlenderSyncLog.Warn(
                "Session",
                "handshake_rejected",
                error);
            _reportProtocolError?.Invoke(error);
        }

        public void AnnounceImportRoot()
        {
            var root = Path.Combine(Directory.GetCurrentDirectory(), "Assets", "TriSync", "Resources").Replace('\\', '/');
            var textureExportRoot = Path.Combine(Directory.GetCurrentDirectory(), "Temp", "BlenderSyncVNext", "TextureExportsV1").Replace('\\', '/');
            var payload = "{" +
                          "\"type\":\"session.import_root\"," +
                          "\"timestamp\":" + DateTimeOffset.UtcNow.ToUnixTimeSeconds() + "," +
                          "\"assetsImportRoot\":\"" + EscapeJsonString(root) + "\"," +
                          "\"textureExportRoot\":\"" + EscapeJsonString(textureExportRoot) + "\"" +
                          "}";
            _emitPayload?.Invoke(payload);
        }

        private void EmitSessionAck(string handshakeId, string criterion)
        {
            var payload = "{" +
                          "\"type\":\"session_ack\"," +
                          "\"timestamp\":" + DateTimeOffset.UtcNow.ToUnixTimeSeconds() + "," +
                          "\"handshakeId\":\"" + EscapeJsonString(handshakeId) + "\"," +
                          "\"criterion\":\"" + EscapeJsonString(criterion) + "\"" +
                          SessionProtocolContract.ReplyJsonFields(_legacyProtocol, _negotiatedFeatures) +
                          (string.IsNullOrWhiteSpace(_targetEndpoint) ? "" : ",\"targetEndpoint\":\"" + EscapeJsonString(_targetEndpoint) + "\"") +
                          "}";
            RecordAck(payload);
            BlenderSyncLog.Trace(
                "Session",
                "session_ack_emitted",
                () => "Sent the session acknowledgement.",
                () => new Dictionary<string, object>
                {
                    { "criterion", criterion },
                    { "legacyProtocol", _legacyProtocol },
                    { "featureCount", _negotiatedFeatures.Length },
                });
            _emitPayload?.Invoke(payload);
        }

        private void EmitFinalHandshakeAck(string handshakeId)
        {
            var payload = "{" +
                          "\"type\":\"session.handshake_ack\"," +
                          "\"timestamp\":" + DateTimeOffset.UtcNow.ToUnixTimeSeconds() + "," +
                          "\"handshakeId\":\"" + EscapeJsonString(handshakeId) + "\"" +
                          SessionProtocolContract.ReplyJsonFields(_legacyProtocol, _negotiatedFeatures) +
                          (string.IsNullOrWhiteSpace(_targetEndpoint) ? "" : ",\"targetEndpoint\":\"" + EscapeJsonString(_targetEndpoint) + "\"") +
                          "}";
            RecordAck(payload);
            _emitPayload?.Invoke(payload);
        }

        private bool ApplyNegotiationOrReject(SessionProtocolContract.NegotiationResult result, string handshakeId)
        {
            if (result == null || !result.ok)
            {
                RejectHandshake(handshakeId, result?.reason ?? "protocol_negotiation_failed", result?.peerVersion);
                return false;
            }
            _legacyProtocol = result.legacy;
            _peerProtocolVersion = result.peerVersion;
            _negotiatedFeatures = result.negotiatedFeatures ?? Array.Empty<string>();
            if (_legacyProtocol && !_legacyProtocolReported)
            {
                _legacyProtocolReported = true;
                BlenderSyncLog.Warn(
                    "Session",
                    "legacy_protocol",
                    "The Blender peer did not report a protocol version; negotiated features are disabled.");
            }
            return true;
        }

        private bool ValidateEchoOrReject(string rawJson, int protocolVersion, string[] echoedFeatures, string handshakeId)
        {
            var ok = SessionProtocolContract.ValidateEcho(
                _legacyProtocol,
                SessionProtocolContract.HasJsonField(rawJson, "protocolVersion"),
                protocolVersion,
                echoedFeatures,
                _negotiatedFeatures);
            if (ok)
                return true;
            RejectHandshake(handshakeId, "negotiation_echo_mismatch", protocolVersion);
            return false;
        }

        private void RejectHandshake(string handshakeId, string reason, int? peerVersion)
        {
            var error = SessionProtocolContract.BuildProtocolError(reason, peerVersion);
            RecordProtocolError(error);
            var payload = "{" +
                          "\"type\":\"session.handshake_reject\"," +
                          "\"timestamp\":" + DateTimeOffset.UtcNow.ToUnixTimeSeconds() + "," +
                          "\"handshakeId\":\"" + EscapeJsonString(handshakeId) + "\"," +
                          "\"reason\":\"" + EscapeJsonString(reason) + "\"," +
                          "\"localProtocolVersion\":" + SessionProtocolContract.ProtocolVersion + "," +
                          "\"peerProtocolVersion\":" + (peerVersion.HasValue ? peerVersion.Value.ToString() : "null") +
                          (string.IsNullOrWhiteSpace(_targetEndpoint) ? "" : ",\"targetEndpoint\":\"" + EscapeJsonString(_targetEndpoint) + "\"") +
                          "}";
            RecordAck(payload);
            _emitPayload?.Invoke(payload);
            _reportProtocolError?.Invoke(error);
        }

        private static void RecordProtocolError(string error)
        {
            lock (AckTraceLock)
            {
                _lastProtocolError = string.IsNullOrWhiteSpace(error) ? null : error.Trim();
            }
        }

        private static string EscapeJsonString(string value)
        {
            if (string.IsNullOrEmpty(value))
                return string.Empty;
            return value
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\r", "\\r")
                .Replace("\n", "\\n")
                .Replace("\t", "\\t");
        }

        private static bool TryParse<TEnvelope>(string rawJson, string label, out TEnvelope envelope) where TEnvelope : class
        {
            envelope = null;
            if (string.IsNullOrWhiteSpace(rawJson))
            {
                BlenderSyncLog.Warn(
                    "Session",
                    "handshake_payload_empty",
                    "Ignored an empty handshake payload.",
                    new Dictionary<string, object> { { "messageType", label } });
                return false;
            }

            try
            {
                envelope = JsonUtility.FromJson<TEnvelope>(rawJson);
                return envelope != null;
            }
            catch (Exception ex)
            {
                BlenderSyncLog.Warn(
                    "Session",
                    "handshake_parse_failed",
                    ex.Message,
                    new Dictionary<string, object> { { "messageType", label } });
                return false;
            }
        }

        private static string NormalizeHandshakeId(string handshakeId)
        {
            return (handshakeId ?? string.Empty).Trim();
        }

        private static string NormalizeOptional(string value)
        {
            var normalized = (value ?? string.Empty).Trim();
            return string.IsNullOrWhiteSpace(normalized) ? null : normalized;
        }

        private void RecordPeerApplicationVersion(string version)
        {
            var normalized = NormalizeOptional(version);
            if (normalized != null)
                _peerApplicationVersion = normalized;
        }

        private static void RecordAck(string payload)
        {
            lock (AckTraceLock)
            {
                _lastAckPayload = payload;
                AckTrace.Add(payload);
                if (AckTrace.Count > 20)
                    AckTrace.RemoveAt(0);
            }
        }

#pragma warning disable 0649 // JsonUtility populates these DTO fields via reflection.
        [Serializable]
        private sealed class SessionHelloEnvelope
        {
            public string type;
            public long timestamp;
            public string handshakeId;
            public string targetEndpoint;
            public int protocolVersion;
            public string[] features;
        }

        [Serializable]
        private sealed class SessionAckEnvelope
        {
            public string type;
            public long timestamp;
            public string handshakeId;
            public string criterion;
            public string targetEndpoint;
            public int protocolVersion;
            public string[] negotiatedFeatures;
            public string blenderVersion;
        }

        [Serializable]
        private sealed class SessionFinalAckEnvelope
        {
            public string type;
            public long timestamp;
            public string handshakeId;
            public string targetEndpoint;
            public int protocolVersion;
            public string[] negotiatedFeatures;
            public string blenderVersion;
        }

        [Serializable]
        private sealed class SessionRejectEnvelope
        {
            public string type;
            public long timestamp;
            public string handshakeId;
            public string reason;
            public int localProtocolVersion;
            public int peerProtocolVersion;
            public string targetEndpoint;
        }
#pragma warning restore 0649
    }
}
