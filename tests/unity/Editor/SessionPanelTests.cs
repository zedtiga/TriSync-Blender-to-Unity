using System.Collections.Generic;
using System.Reflection;
using BlenderSyncVNext.UI;
using NUnit.Framework;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class SessionPanelTests
    {
        private const string TypeName = "BlenderSyncVNext.UI.SessionPanel";
        private MethodInfo _resolveState;
        private MethodInfo _statusColor;
        private MethodInfo _connectionTitle;
        private MethodInfo _connectionContext;
        private MethodInfo _connectionActionLabel;
        private MethodInfo _blenderVersionLabel;
        private MethodInfo _canCreateMesh;
        private MethodInfo _countValidMeshSources;
        private MethodInfo _containsMeshAtOtherIndex;
        private MethodInfo _createInBlenderTooltip;
        private MethodInfo _createResultSummary;
        private MethodInfo _defaultPort;
        private MethodInfo _normalizePort;
        private MethodInfo _buildEndpoint;
        private MethodInfo _portEditable;
        private MethodInfo _normalizeMainTab;
        private MethodInfo _mainTabLabel;
        private MethodInfo _shouldTickRegistry;
        private MethodInfo _canonicalMenuPath;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _resolveState = ProductApi.RequireStaticMethod(TypeName, "ResolveConnectionVisualState", 2);
            _statusColor = ProductApi.RequireStaticMethod(TypeName, "StatusColor", 2);
            _connectionTitle = ProductApi.RequireStaticMethod(TypeName, "ConnectionTitle", 2);
            _connectionContext = ProductApi.RequireStaticMethod(TypeName, "ConnectionContext", 2);
            _connectionActionLabel = ProductApi.RequireStaticMethod(TypeName, "ConnectionActionLabel", 1);
            _blenderVersionLabel = ProductApi.RequireStaticMethod(TypeName, "BlenderVersionLabel", 1);
            _canCreateMesh = ProductApi.RequireStaticMethod(TypeName, "CanCreateMesh", 3);
            _countValidMeshSources = ProductApi.RequireStaticMethod(TypeName, "CountValidMeshSources", 1);
            _containsMeshAtOtherIndex = ProductApi.RequireStaticMethod(TypeName, "ContainsMeshAtOtherIndex", 3);
            _createInBlenderTooltip = ProductApi.RequireStaticMethod(TypeName, "CreateInBlenderTooltip", 0);
            _createResultSummary = ProductApi.RequireStaticMethod(TypeName, "CreateResultSummary", 3);
            _defaultPort = ProductApi.RequireStaticMethod(TypeName, "DefaultPort", 0);
            _normalizePort = ProductApi.RequireStaticMethod(TypeName, "NormalizePort", 1);
            _buildEndpoint = ProductApi.RequireStaticMethod(TypeName, "BuildEndpointForPort", 1);
            _portEditable = ProductApi.RequireStaticMethod(TypeName, "PortEditable", 1);
            _normalizeMainTab = ProductApi.RequireStaticMethod(TypeName, "NormalizeMainTab", 1);
            _mainTabLabel = ProductApi.RequireStaticMethod(TypeName, "MainTabLabel", 1);
            _shouldTickRegistry = ProductApi.RequireStaticMethod(TypeName, "ShouldTickRegistry", 1);
            _canonicalMenuPath = ProductApi.RequireStaticMethod(TypeName, "CanonicalMenuPath", 0);
        }

        [Test]
        public void ConnectionStatesMapToUserFacingPresentation()
        {
            Assert.That(Resolve("disconnected", false), Is.EqualTo("Disconnected"));
            Assert.That(Title("disconnected", false), Is.EqualTo("Disconnected"));

            Assert.That(Resolve("transport_connected", true), Is.EqualTo("Connecting"));
            Assert.That(Title("transport_connected", true), Is.EqualTo("Connecting"));

            Assert.That(Resolve("handshake_confirmed", true), Is.EqualTo("Connected"));
            Assert.That(Title("handshake_confirmed", true), Is.EqualTo("Connected"));

            Assert.That(Context("disconnected", false), Is.EqualTo("Ready to connect"));
            Assert.That(Context("transport_connected", true), Is.EqualTo("Waiting for handshake"));
            Assert.That(Context("handshake_confirmed", true), Is.EqualTo("Session ready"));
        }

        [Test]
        public void ErrorStateTakesPriorityOverRunningTransport()
        {
            Assert.That(Resolve("error", true), Is.EqualTo("Error"));
            Assert.That(Resolve("protocol_mismatch", true), Is.EqualTo("ProtocolMismatch"));
            Assert.That(Title("protocol_mismatch", true), Is.EqualTo("Protocol mismatch"));
            Assert.That(Context("protocol_mismatch", true), Is.EqualTo("Update the other endpoint"));
            Assert.That(Context("error", true), Is.EqualTo("See Diagnostics"));

            var errorColor = (Color)_statusColor.Invoke(null, new object[] { "error", true });
            Assert.That(errorColor.r, Is.GreaterThan(errorColor.g));
        }

        [Test]
        public void ConnectionActionIsSingleAndTransportAware()
        {
            Assert.That(_connectionActionLabel.Invoke(null, new object[] { false }), Is.EqualTo("Connect"));
            Assert.That(_connectionActionLabel.Invoke(null, new object[] { true }), Is.EqualTo("Disconnect"));
        }

        [Test]
        public void BlenderVersionIsSecondaryAndHandlesLegacyPeers()
        {
            Assert.That(_blenderVersionLabel.Invoke(null, new object[] { "5.0.1" }), Is.EqualTo("Blender 5.0.1"));
            Assert.That(_blenderVersionLabel.Invoke(null, new object[] { null }), Is.EqualTo("Blender --"));
            Assert.That(_blenderVersionLabel.Invoke(null, new object[] { "  " }), Is.EqualTo("Blender --"));
        }

        [Test]
        public void CreateActionRequiresConnectionQueueContentAndNoPendingOperation()
        {
            Assert.That(_canCreateMesh.Invoke(null, new object[] { false, false, 1 }), Is.EqualTo(false));
            Assert.That(_canCreateMesh.Invoke(null, new object[] { true, false, 0 }), Is.EqualTo(false));
            Assert.That(_canCreateMesh.Invoke(null, new object[] { true, false, 1 }), Is.EqualTo(true));
            Assert.That(_canCreateMesh.Invoke(null, new object[] { true, true, 1 }), Is.EqualTo(false));
        }

        [Test]
        public void SendListOnlyCountsAssignedMeshFields()
        {
            var mesh = new Mesh { name = "QueuedMesh" };
            try
            {
                Assert.That(
                    UnityMeshSendToBlenderTool.TryCreateMeshSource(mesh, out var source, out var warning),
                    Is.True);
                var queue = new List<UnityMeshSendToBlenderTool.MeshSource> { null, source };

                Assert.That(warning, Is.Null);
                Assert.That(_countValidMeshSources.Invoke(null, new object[] { queue }), Is.EqualTo(1));
                Assert.That(
                    _containsMeshAtOtherIndex.Invoke(null, new object[] { queue, 1, mesh }),
                    Is.EqualTo(false));
                Assert.That(
                    _containsMeshAtOtherIndex.Invoke(null, new object[] { queue, 0, mesh }),
                    Is.EqualTo(true));
            }
            finally
            {
                Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void CreateActionTooltipKeepsTheUserFacingImportBoundary()
        {
            var tooltip = (string)_createInBlenderTooltip.Invoke(null, null);

            StringAssert.Contains("UV0-UV7", tooltip);
            StringAssert.Contains("single-frame BlendShapes", tooltip);
            StringAssert.Contains("Rigging", tooltip);
            StringAssert.Contains("reverse sync", tooltip);
            StringAssert.DoesNotContain("staged binary", tooltip);
        }

        [Test]
        public void CreateResultSummaryUsesObjectCounts()
        {
            Assert.That(
                _createResultSummary.Invoke(null, new object[] { "imported", 1, 0 }),
                Is.EqualTo("Created 1 object in Blender."));
            Assert.That(
                _createResultSummary.Invoke(null, new object[] { "partial", 2, 1 }),
                Is.EqualTo("Created 2 objects; 1 skipped."));
        }

        [Test]
        public void CreateResultSummaryUsesAWarningForAnEmptyQueue()
        {
            Assert.That(
                _createResultSummary.Invoke(null, new object[] { "warning", 0, 0 }),
                Is.EqualTo("Add a compatible Mesh to create in Blender."));
        }

        [Test]
        public void ConnectionSettingsUseTheExpectedDefaultPort()
        {
            Assert.That(_defaultPort.Invoke(null, null), Is.EqualTo(8765));
        }

        [Test]
        public void PortSettingBuildsAValidatedLocalEndpoint()
        {
            Assert.That(_normalizePort.Invoke(null, new object[] { 0 }), Is.EqualTo(1));
            Assert.That(_normalizePort.Invoke(null, new object[] { 70000 }), Is.EqualTo(65535));
            Assert.That(
                _buildEndpoint.Invoke(null, new object[] { 9123 }),
                Is.EqualTo("ws://127.0.0.1:9123/ws/"));
            Assert.That(_portEditable.Invoke(null, new object[] { false }), Is.True);
            Assert.That(_portEditable.Invoke(null, new object[] { true }), Is.False);
        }

        [Test]
        public void MainTabsUseStableValuesAndNormalizeInvalidStoredValues()
        {
            Assert.That(_normalizeMainTab.Invoke(null, new object[] { 0 }), Is.EqualTo(0));
            Assert.That(_normalizeMainTab.Invoke(null, new object[] { 1 }), Is.EqualTo(1));
            Assert.That(_normalizeMainTab.Invoke(null, new object[] { 2 }), Is.EqualTo(2));
            Assert.That(_normalizeMainTab.Invoke(null, new object[] { -1 }), Is.EqualTo(0));
            Assert.That(_normalizeMainTab.Invoke(null, new object[] { 3 }), Is.EqualTo(0));
            Assert.That(_mainTabLabel.Invoke(null, new object[] { 0 }), Is.EqualTo("Session"));
            Assert.That(_mainTabLabel.Invoke(null, new object[] { 1 }), Is.EqualTo("Registry"));
            Assert.That(_mainTabLabel.Invoke(null, new object[] { 2 }), Is.EqualTo("Diagnostics"));
        }

        [Test]
        public void RegistryAutoRefreshIsGatedByTheActiveTopLevelTab()
        {
            Assert.That(_shouldTickRegistry.Invoke(null, new object[] { 0 }), Is.False);
            Assert.That(_shouldTickRegistry.Invoke(null, new object[] { 1 }), Is.True);
            Assert.That(_shouldTickRegistry.Invoke(null, new object[] { 2 }), Is.False);
            Assert.That(_shouldTickRegistry.Invoke(null, new object[] { 99 }), Is.False);
        }

        [Test]
        public void DiagnosticsUiDoesNotRetainRawHandshakeControls()
        {
            var type = ProductApi.RequireType(TypeName);
            const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
            Assert.That(type.GetField("_showHandshakeDetails", flags), Is.Null);
            Assert.That(type.GetField("_showLifecycleTrace", flags), Is.Null);
            Assert.That(type.GetField("_showAckTrace", flags), Is.Null);
            Assert.That(type.GetMethod("DrawTrace", flags), Is.Null);
        }

        [Test]
        public void CanonicalMenuUsesTheUnifiedWindowName()
        {
            Assert.That(_canonicalMenuPath.Invoke(null, null), Is.EqualTo("TriSync/Open TriSync"));
        }

        private string Resolve(string state, bool running)
        {
            return _resolveState.Invoke(null, new object[] { state, running }).ToString();
        }

        private string Title(string state, bool running)
        {
            return (string)_connectionTitle.Invoke(null, new object[] { state, running });
        }

        private string Context(string state, bool running)
        {
            return (string)_connectionContext.Invoke(null, new object[] { state, running });
        }

    }
}
