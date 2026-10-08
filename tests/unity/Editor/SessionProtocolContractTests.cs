using System;
using System.Reflection;
using BlenderSyncVNext.SessionCore;
using NUnit.Framework;

namespace BlenderSyncVNext.Tests
{
    public sealed class SessionProtocolContractTests
    {
        private const string TypeName = "BlenderSyncVNext.SessionCore.SessionProtocolContract";
        private MethodInfo _negotiateOffer;
        private MethodInfo _acceptReply;
        private MethodInfo _validateEcho;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _negotiateOffer = ProductApi.RequireStaticMethod(TypeName, "NegotiateOffer", 3);
            _acceptReply = ProductApi.RequireStaticMethod(TypeName, "AcceptNegotiatedReply", 3);
            _validateEcho = ProductApi.RequireStaticMethod(TypeName, "ValidateEcho", 5);
        }

        [Test]
        public void MatchingVersionIgnoresUnknownFeatures()
        {
            var result = _negotiateOffer.Invoke(null, new object[]
            {
                true,
                1,
                new[] { "unknown_feature", "unity_mesh_import_result_v1", "large_payload_crc32_v1" },
            });

            Assert.That(Field<bool>(result, "ok"), Is.True);
            Assert.That(Field<bool>(result, "legacy"), Is.False);
            Assert.That(
                Field<string[]>(result, "negotiatedFeatures"),
                Is.EqualTo(new[] { "large_payload_crc32_v1", "unity_mesh_import_result_v1" }));
        }

        [Test]
        public void MissingVersionUsesLegacyModeWithoutFeatures()
        {
            var result = _negotiateOffer.Invoke(null, new object[]
            {
                false,
                0,
                new[] { "large_payload_crc32_v1", "unity_mesh_import_result_v1" },
            });

            Assert.That(Field<bool>(result, "ok"), Is.True);
            Assert.That(Field<bool>(result, "legacy"), Is.True);
            Assert.That(Field<string[]>(result, "negotiatedFeatures"), Is.Empty);
            Assert.That(
                (bool)_validateEcho.Invoke(null, new object[] { true, false, 0, null, Array.Empty<string>() }),
                Is.True);
        }

        [Test]
        public void ExplicitVersionMismatchIsRejected()
        {
            var result = _negotiateOffer.Invoke(null, new object[] { true, 2, Array.Empty<string>() });

            Assert.That(Field<bool>(result, "ok"), Is.False);
            Assert.That(Field<string>(result, "reason"), Is.EqualTo("protocol_version_mismatch"));
        }

        [Test]
        public void VersionedEchoMustMatchNegotiatedFeatures()
        {
            var accepted = _acceptReply.Invoke(null, new object[]
            {
                true,
                1,
                new[] { "large_payload_crc32_v1", "unity_mesh_import_result_v1" },
            });
            Assert.That(Field<bool>(accepted, "ok"), Is.True);

            var unsupported = _acceptReply.Invoke(null, new object[]
            {
                true,
                1,
                new[] { "unexpected_feature" },
            });
            Assert.That(Field<bool>(unsupported, "ok"), Is.False);
            Assert.That(Field<string>(unsupported, "reason"), Is.EqualTo("negotiation_echo_mismatch"));

            Assert.That(
                (bool)_validateEcho.Invoke(null, new object[]
                {
                    false,
                    true,
                    1,
                    new[] { "unexpected_feature" },
                    new[] { "large_payload_crc32_v1", "unity_mesh_import_result_v1" },
                }),
                Is.False);
        }

        [Test]
        public void SessionAckCapturesOptionalBlenderApplicationVersion()
        {
            var client = new SessionClient();
            client.HandleIncoming(
                "{\"type\":\"session_ack\",\"timestamp\":1,\"handshakeId\":\"hs-version\"," +
                "\"protocolVersion\":1,\"negotiatedFeatures\":[],\"blenderVersion\":\"5.0.1\"}");

            Assert.That(client.PeerApplicationVersion, Is.EqualTo("5.0.1"));

            var legacy = new SessionClient();
            legacy.HandleIncoming(
                "{\"type\":\"session_ack\",\"timestamp\":1,\"handshakeId\":\"hs-legacy\"}");
            Assert.That(legacy.PeerApplicationVersion, Is.Null);
        }

        private static T Field<T>(object target, string name)
        {
            Assert.That(target, Is.Not.Null);
            var field = target.GetType().GetField(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            Assert.That(field, Is.Not.Null, "Missing field " + name);
            return (T)field.GetValue(target);
        }
    }
}
