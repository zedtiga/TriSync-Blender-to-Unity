using System;
using System.Collections.Generic;

namespace BlenderSyncVNext.SessionCore
{
    internal static class SessionProtocolContract
    {
        public const int ProtocolVersion = 1;
        public const string LargePayloadCrc32Feature = "large_payload_crc32_v1";
        public const string UnityMeshImportResultFeature = "unity_mesh_import_result_v1";

        private static readonly string[] Supported =
        {
            LargePayloadCrc32Feature,
            UnityMeshImportResultFeature,
        };

        internal sealed class NegotiationResult
        {
            public bool ok;
            public bool legacy;
            public int? peerVersion;
            public string[] negotiatedFeatures = Array.Empty<string>();
            public string reason;
        }

        public static string[] SupportedFeatures => (string[])Supported.Clone();

        public static NegotiationResult NegotiateOffer(bool hasProtocolVersion, int peerVersion, string[] peerFeatures)
        {
            if (!hasProtocolVersion)
                return new NegotiationResult { ok = true, legacy = true };
            if (peerVersion != ProtocolVersion)
            {
                return new NegotiationResult
                {
                    ok = false,
                    peerVersion = peerVersion,
                    reason = "protocol_version_mismatch",
                };
            }

            var offered = new HashSet<string>(NormalizeFeatures(peerFeatures), StringComparer.Ordinal);
            var negotiated = new List<string>();
            foreach (var feature in Supported)
            {
                if (offered.Contains(feature))
                    negotiated.Add(feature);
            }
            negotiated.Sort(StringComparer.Ordinal);
            return new NegotiationResult
            {
                ok = true,
                peerVersion = peerVersion,
                negotiatedFeatures = negotiated.ToArray(),
            };
        }

        public static NegotiationResult AcceptNegotiatedReply(bool hasProtocolVersion, int peerVersion, string[] negotiatedFeatures)
        {
            if (!hasProtocolVersion)
                return new NegotiationResult { ok = true, legacy = true };
            if (peerVersion != ProtocolVersion)
            {
                return new NegotiationResult
                {
                    ok = false,
                    peerVersion = peerVersion,
                    reason = "protocol_version_mismatch",
                };
            }

            var normalized = NormalizeFeatures(negotiatedFeatures);
            var supported = new HashSet<string>(Supported, StringComparer.Ordinal);
            foreach (var feature in normalized)
            {
                if (!supported.Contains(feature))
                {
                    return new NegotiationResult
                    {
                        ok = false,
                        peerVersion = peerVersion,
                        reason = "negotiation_echo_mismatch",
                    };
                }
            }
            return new NegotiationResult
            {
                ok = true,
                peerVersion = peerVersion,
                negotiatedFeatures = normalized,
            };
        }

        public static bool ValidateEcho(
            bool legacy,
            bool hasProtocolVersion,
            int peerVersion,
            string[] echoedFeatures,
            string[] expectedFeatures)
        {
            if (legacy)
                return true;
            if (!hasProtocolVersion || peerVersion != ProtocolVersion)
                return false;
            var echoed = NormalizeFeatures(echoedFeatures);
            var expected = NormalizeFeatures(expectedFeatures);
            if (echoed.Length != expected.Length)
                return false;
            for (var i = 0; i < echoed.Length; i++)
            {
                if (!string.Equals(echoed[i], expected[i], StringComparison.Ordinal))
                    return false;
            }
            return true;
        }

        public static string BuildProtocolError(string reason, int? peerVersion = null)
        {
            if (string.Equals(reason, "protocol_version_mismatch", StringComparison.Ordinal))
            {
                var peer = peerVersion.HasValue ? peerVersion.Value.ToString() : "unknown";
                return $"Protocol version mismatch (local {ProtocolVersion} / peer {peer}). Update the other endpoint.";
            }
            if (string.Equals(reason, "negotiation_echo_mismatch", StringComparison.Ordinal))
                return $"Protocol negotiation echo mismatch (local {ProtocolVersion}). Update the other endpoint.";
            return string.IsNullOrWhiteSpace(reason) ? "Protocol negotiation failed." : reason.Trim();
        }

        public static bool HasJsonField(string rawJson, string fieldName)
        {
            if (string.IsNullOrWhiteSpace(rawJson) || string.IsNullOrWhiteSpace(fieldName))
                return false;
            return rawJson.IndexOf("\"" + fieldName.Trim() + "\"", StringComparison.Ordinal) >= 0;
        }

        public static string OfferJsonFields()
        {
            return $",\"protocolVersion\":{ProtocolVersion},\"features\":[{QuoteFeatures(Supported)}]";
        }

        public static string ReplyJsonFields(bool legacy, string[] negotiatedFeatures)
        {
            if (legacy)
                return string.Empty;
            var features = NormalizeFeatures(negotiatedFeatures);
            return $",\"protocolVersion\":{ProtocolVersion},\"negotiatedFeatures\":[{QuoteFeatures(features)}]";
        }

        private static string[] NormalizeFeatures(string[] values)
        {
            if (values == null || values.Length == 0)
                return Array.Empty<string>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            var normalized = new List<string>();
            foreach (var value in values)
            {
                var feature = (value ?? string.Empty).Trim();
                if (feature.Length == 0 || !seen.Add(feature))
                    continue;
                normalized.Add(feature);
            }
            normalized.Sort(StringComparer.Ordinal);
            return normalized.ToArray();
        }

        private static string QuoteFeatures(string[] features)
        {
            if (features == null || features.Length == 0)
                return string.Empty;
            var quoted = new string[features.Length];
            for (var i = 0; i < features.Length; i++)
                quoted[i] = "\"" + features[i].Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
            return string.Join(",", quoted);
        }
    }
}
