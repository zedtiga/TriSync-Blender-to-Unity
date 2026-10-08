using System;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using BlenderSyncVNext.AnimationClipImport;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.SceneSyncCore;
using BlenderSyncVNext.SessionCore;
using BlenderSyncVNext.UI;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace BlenderSyncVNext.Tests
{
    public sealed class UnityLoggingMigrationTests
    {
        private MethodInfo _setVerboseOverride;
        private MethodInfo _logPayloadWarnings;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _setVerboseOverride = typeof(BlenderSyncLog).GetMethod(
                "SetVerboseOverride",
                BindingFlags.Static | BindingFlags.NonPublic);
            _logPayloadWarnings = ProductApi.RequireStaticMethod(
                "BlenderSyncVNext.SceneSyncCore.MaterialContentV1ApplyService",
                "LogPayloadWarnings",
                1);
            Assert.That(_setVerboseOverride, Is.Not.Null);
        }

        [SetUp]
        public void SetUp()
        {
            BlenderSyncLog.Clear();
            BlenderSyncReportStore.Clear();
            _setVerboseOverride.Invoke(null, new object[] { (bool?)false });
        }

        [TearDown]
        public void TearDown()
        {
            BlenderSyncLog.Clear();
            BlenderSyncReportStore.Clear();
            _setVerboseOverride.Invoke(null, new object[] { null });
        }

        [Test]
        public void HandshakeTraceDoesNotExposePayloadOrHandshakeId()
        {
            _setVerboseOverride.Invoke(null, new object[] { (bool?)true });
            LogAssert.Expect(
                LogType.Log,
                new Regex(@"\[BlenderSync\]\[TRACE\]\[Session\] hello_received"));
            LogAssert.Expect(
                LogType.Log,
                new Regex(@"\[BlenderSync\]\[TRACE\]\[Session\] session_ack_emitted"));

            var client = new SessionClient();
            client.HandleIncoming(
                "{\"type\":\"session_hello\",\"timestamp\":1," +
                "\"handshakeId\":\"hs-secret-value\",\"protocolVersion\":1,\"features\":[]}");

            var encoded = string.Join(
                "\n",
                BlenderSyncLog.Recent.Select(entry =>
                    entry.eventName + " " + entry.summary + " " +
                    string.Join(" ", entry.fields.Select(pair => pair.Key + "=" + pair.Value))));
            Assert.That(encoded, Does.Not.Contain("hs-secret-value"));
            Assert.That(encoded, Does.Not.Contain("handshakeId"));
            Assert.That(encoded, Does.Not.Contain("{\"type\""));
        }

        [Test]
        public void LegacyProtocolWarningIsRecordedOnlyOncePerHandshake()
        {
            var client = new SessionClient();
            client.HandleIncoming(
                "{\"type\":\"session_hello\",\"timestamp\":1,\"handshakeId\":\"hs-legacy\"}");
            client.HandleIncoming(
                "{\"type\":\"session_ack\",\"timestamp\":2,\"handshakeId\":\"hs-legacy\"}");

            var entries = BlenderSyncLog.Recent
                .Where(entry => entry.eventName == "legacy_protocol")
                .ToArray();
            Assert.That(entries, Has.Length.EqualTo(1));
            Assert.That(entries[0].level, Is.EqualTo("WARN"));
        }

        [Test]
        public void MaterialPayloadWarningsAreAggregatedBySeverity()
        {
            var message = new SceneSyncMaterialContentV1Message
            {
                materialRef = "material-1",
                warnings = new[]
                {
                    new MaterialContentV1Warning
                    {
                        code = "image_packed_exported",
                        slot = "baseColor",
                        message = "Packed image exported.",
                    },
                    new MaterialContentV1Warning
                    {
                        code = "image_buffer_exported",
                        slot = "normal",
                        message = "Image buffer exported.",
                    },
                    new MaterialContentV1Warning
                    {
                        code = "normal_space_unsupported",
                        slot = "normal",
                        message = "Unsupported normal space.",
                    },
                },
            };

            _logPayloadWarnings.Invoke(null, new object[] { message });

            var entries = BlenderSyncLog.Recent.ToArray();
            Assert.That(entries, Has.Length.EqualTo(2));
            Assert.That(entries[0].eventName, Is.EqualTo("payload_information"));
            Assert.That(entries[0].level, Is.EqualTo("INFO"));
            Assert.That(entries[0].fields["count"], Is.EqualTo("2"));
            Assert.That(entries[1].eventName, Is.EqualTo("payload_warning"));
            Assert.That(entries[1].level, Is.EqualTo("WARN"));
            Assert.That(entries[1].fields["count"], Is.EqualTo("1"));
        }

        [Test]
        public void MeshImportTerminalResultsUseUserFacingSeverities()
        {
            UnityMeshSendToBlenderTool.HandleImportResult(
                "{\"type\":\"unity_mesh.import_result_v1\",\"status\":\"imported\"," +
                "\"message\":\"Created\",\"created\":1,\"skipped\":0,\"warnings\":[],\"timestamp\":1}");
            UnityMeshSendToBlenderTool.HandleImportResult(
                "{\"type\":\"unity_mesh.import_result_v1\",\"status\":\"partial\"," +
                "\"message\":\"Created with warnings\",\"created\":1,\"skipped\":0," +
                "\"warnings\":[\"degraded\"],\"timestamp\":2}");

            var entries = BlenderSyncLog.Recent
                .Where(entry => entry.category == "UnityMeshImport")
                .ToArray();
            Assert.That(entries.Select(entry => entry.eventName), Is.EqualTo(new[]
            {
                "result_imported",
                "result_partial",
            }));
            Assert.That(entries.Select(entry => entry.level), Is.EqualTo(new[] { "INFO", "WARN" }));
        }

        [Test]
        public void RoutineBlendShapeUpdateIsSilentWhenVerboseLoggingIsDisabled()
        {
            var target = new GameObject("LoggingMigrationBlendShapeTarget");
            try
            {
                var service = new BlendShapeWeightsUpdateService(new BlendShapeWeightApplyService());
                service.Apply(target, new SceneSyncBlendShapeWeightsMessage
                {
                    pairId = "pair-logging-test",
                    weights = Array.Empty<SceneSyncBlendShapeWeightItem>(),
                });

                Assert.That(BlenderSyncLog.Recent, Is.Empty);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        [Test]
        public void RiggedPoseValidationUsesBufferedWarning()
        {
            var handled = new RiggedPoseApplyService().HandleEnvelope("{\"type\":\"invalid\"}");

            Assert.That(handled, Is.False);
            var entry = BlenderSyncLog.Recent.Single();
            Assert.That(entry.category, Is.EqualTo("RiggedPose"));
            Assert.That(entry.eventName, Is.EqualTo("invalid_envelope"));
            Assert.That(entry.level, Is.EqualTo("WARN"));
        }

        [Test]
        public void AnimationEnvelopeValidationUsesErrorFacade()
        {
            LogAssert.Expect(
                LogType.Error,
                new Regex(@"\[BlenderSync\]\[ERROR\]\[AnimationClip\] kind_invalid"));

            var handled = new AnimationClipImportCoordinator().HandleEnvelope("{}");

            Assert.That(handled, Is.False);
            var entry = BlenderSyncLog.Recent.Single();
            Assert.That(entry.category, Is.EqualTo("AnimationClip"));
            Assert.That(entry.eventName, Is.EqualTo("kind_invalid"));
            Assert.That(entry.level, Is.EqualTo("ERROR"));
        }

        [Test]
        public void ReportListenerFailureUsesErrorFacadeAndDoesNotStopOtherListeners()
        {
            var successfulListenerCalls = 0;
            Action failingListener = () => throw new InvalidOperationException("listener failed");
            Action successfulListener = () => successfulListenerCalls++;
            BlenderSyncReportStore.Changed += failingListener;
            BlenderSyncReportStore.Changed += successfulListener;
            LogAssert.Expect(
                LogType.Error,
                new Regex(@"\[BlenderSync\]\[ERROR\]\[Diagnostics\] report_listener_failed"));

            try
            {
                BlenderSyncReportStore.Add("Test", "OK", "listener isolation");
            }
            finally
            {
                BlenderSyncReportStore.Changed -= failingListener;
                BlenderSyncReportStore.Changed -= successfulListener;
            }

            Assert.That(successfulListenerCalls, Is.EqualTo(1));
            var entry = BlenderSyncLog.Recent.Single();
            Assert.That(entry.eventName, Is.EqualTo("report_listener_failed"));
            Assert.That(entry.level, Is.EqualTo("ERROR"));
        }
    }
}
