using System;
using System.Collections.Generic;
using System.Reflection;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.SessionCore;
using NUnit.Framework;

namespace BlenderSyncVNext.Tests
{
    public sealed class DiagnosticsSnapshotTests
    {
        private Type _registryDataType;
        private MethodInfo _buildJson;
        private MethodInfo _redactText;
        private MethodInfo _normalizeReportStatus;
        private MethodInfo _firstVisibleEntryIndex;
        private MethodInfo _setVerboseOverride;
        private MethodInfo _buildRecentLogs;
        private Type _reportViewType;
        private FieldInfo _showRecentActivity;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            ProductApi.RequireType("BlenderSyncVNext.UI.UnityDiagnosticsSnapshotBuilder");
            _registryDataType = ProductApi.RequireType("BlenderSyncVNext.UI.RegistryDiagnosticsData");
            _buildJson = ProductApi.RequireStaticMethod(
                "BlenderSyncVNext.UI.UnityDiagnosticsSnapshotBuilder", "BuildJson", 5);
            _redactText = ProductApi.RequireStaticMethod(
                "BlenderSyncVNext.UI.UnityDiagnosticsSnapshotBuilder", "RedactDiagnosticsText", 1);
            _reportViewType = ProductApi.RequireType("BlenderSyncVNext.UI.BlenderSyncReportView");
            _normalizeReportStatus = ProductApi.RequireStaticMethod(
                "BlenderSyncVNext.UI.BlenderSyncReportView", "NormalizeStatus", 1);
            _firstVisibleEntryIndex = ProductApi.RequireStaticMethod(
                "BlenderSyncVNext.UI.BlenderSyncReportView", "FirstVisibleEntryIndex", 1);
            _buildRecentLogs = ProductApi.RequireStaticMethod(
                "BlenderSyncVNext.UI.UnityDiagnosticsSnapshotBuilder", "BuildRecentLogs", 0);
            _setVerboseOverride = typeof(BlenderSyncLog).GetMethod(
                "SetVerboseOverride",
                BindingFlags.Static | BindingFlags.NonPublic);
            _showRecentActivity = _reportViewType.GetField(
                "_showRecentActivity",
                BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(_setVerboseOverride, Is.Not.Null);
            Assert.That(_showRecentActivity, Is.Not.Null);
        }

        [SetUp]
        public void SetUp()
        {
            BlenderSyncLog.Clear();
            _setVerboseOverride.Invoke(null, new object[] { (bool?)false });
        }

        [TearDown]
        public void TearDown()
        {
            BlenderSyncLog.Clear();
            _setVerboseOverride.Invoke(null, new object[] { null });
        }

        [Test]
        public void RecentActivityNormalizesStatusAndLimitsTheDefaultView()
        {
            Assert.That(_normalizeReportStatus.Invoke(null, new object[] { null }), Is.EqualTo("INFO"));
            Assert.That(_normalizeReportStatus.Invoke(null, new object[] { " warning " }), Is.EqualTo("WARN"));
            Assert.That(_normalizeReportStatus.Invoke(null, new object[] { "ok" }), Is.EqualTo("OK"));

            Assert.That(_firstVisibleEntryIndex.Invoke(null, new object[] { 0 }), Is.EqualTo(0));
            Assert.That(_firstVisibleEntryIndex.Invoke(null, new object[] { 5 }), Is.EqualTo(0));
            Assert.That(_firstVisibleEntryIndex.Invoke(null, new object[] { 6 }), Is.EqualTo(1));
            Assert.That(_firstVisibleEntryIndex.Invoke(null, new object[] { 80 }), Is.EqualTo(75));
        }

        [Test]
        public void RecentActivitySectionStartsCollapsed()
        {
            var view = Activator.CreateInstance(_reportViewType, true);

            Assert.That(_showRecentActivity.GetValue(view), Is.False);
        }

        [Test]
        public void DiagnosticsTextRedactsLocalPathsButPreservesEndpointText()
        {
            var redacted = (string)_redactText.Invoke(
                null,
                new object[] { "failed at C:\\Users\\alice\\My Project\\file.json endpoint=ws://127.0.0.1:8765/ws/" });

            Assert.That(redacted, Does.Contain("<path>"));
            Assert.That(redacted, Does.Not.Contain("C:\\Users\\alice"));
            Assert.That(redacted, Does.Contain("ws://127.0.0.1:8765/ws/"));
        }

        [Test]
        public void CopyDiagnosticsJsonUsesSharedSchemaAndOmitsPayloadSecrets()
        {
            BlenderSyncLog.Info(
                "Import",
                "failed",
                "failed at C:\\Users\\alice\\project\\asset.fbx",
                new Dictionary<string, object>
                {
                    { "path", "/home/alice/asset.fbx" },
                    { "payload", "secret" },
                });
            var registry = Activator.CreateInstance(_registryDataType, true);
            SetField(registry, "loaded", true);
            SetField(registry, "resourceCount", 3);
            SetField(registry, "objectCount", 2);
            SetField(registry, "riggedObjectCount", 1);
            SetField(registry, "objectIssueCount", 1);
            SetField(registry, "refreshStatus", "failed at C:\\Users\\alice\\project");

            var report = new BlenderSyncReportEntry
            {
                category = "Import",
                status = "ERROR",
                summary = "failed at C:\\Users\\alice\\project\\asset.fbx",
                time = DateTime.UtcNow,
            };
            report.fields["payload"] = "{\"vertices\":[1,2,3]}";
            report.fields["path"] = "C:\\Users\\alice\\project\\asset.fbx";

            var json = (string)_buildJson.Invoke(
                null,
                new object[]
                {
                    new SessionClient(),
                    "ws://127.0.0.1:8765/ws/",
                    registry,
                    report,
                    DateTime.UtcNow,
                });

            Assert.That(json, Does.Contain("blendersync-diagnostics-v2"));
            Assert.That(json, Does.Contain("<path>"));
            Assert.That(json, Does.Contain("ws://127.0.0.1:8765/ws/"));
            Assert.That(json, Does.Not.Contain("C:\\Users\\alice"));
            Assert.That(json, Does.Not.Contain("vertices"));
            Assert.That(json, Does.Not.Contain("handshake_id"));
            Assert.That(json, Does.Contain("lastOperation"));
            Assert.That(json, Does.Contain("recentLogs"));
            Assert.That(json, Does.Not.Contain("secret"));
            Assert.That(json, Does.Contain("\"riggedObjectCount\": 1"));
            Assert.That(json, Does.Contain("\"objectIssueCount\": 1"));
        }

        [Test]
        public void CopyDiagnosticsLimitsRecentLogsToLatestFifty()
        {
            for (var index = 0; index < 55; index++)
                BlenderSyncLog.Info("Loop", "event-" + index);

            var logs = (Array)_buildRecentLogs.Invoke(null, null);

            Assert.That(logs.Length, Is.EqualTo(50));
            Assert.That(GetField(logs.GetValue(0), "eventName"), Is.EqualTo("event-5"));
            Assert.That(GetField(logs.GetValue(49), "eventName"), Is.EqualTo("event-54"));
        }

        private static void SetField(object target, string name, object value)
        {
            var field = target.GetType().GetField(
                name,
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            Assert.That(field, Is.Not.Null, $"Missing diagnostics field: {name}");
            field.SetValue(target, value);
        }

        private static object GetField(object target, string name)
        {
            var field = target.GetType().GetField(
                name,
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            Assert.That(field, Is.Not.Null, $"Missing diagnostics field: {name}");
            return field.GetValue(target);
        }
    }
}
