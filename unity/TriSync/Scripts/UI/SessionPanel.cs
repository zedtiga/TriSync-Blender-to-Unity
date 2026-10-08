#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.SceneSyncCore;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;
using BlenderSyncVNext.SessionCore;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;

namespace BlenderSyncVNext.UI
{
    public sealed class SessionPanel : EditorWindow
    {
        private const string MenuPath = "TriSync/Open TriSync";
        private const string MainTabEditorPrefKey = "BlenderSyncVNext.MainTab";
        private const string PortEditorPrefKey = "BlenderSyncVNext.SessionPort";
        private const int DefaultSessionPort = 8765;

        private enum MainTab
        {
            Session = 0,
            Registry = 1,
            Diagnostics = 2,
        }

        private enum ConnectionVisualState
        {
            Disconnected,
            Connecting,
            Connected,
            Error,
            ProtocolMismatch,
        }

        private static readonly string[] MainTabLabels = { "Session", "Registry", "Diagnostics" };

        private Vector2 _panelScroll;
        private bool _showMeshImportDetails;
        private bool _showSettings;
        private bool _showSendToBlender = true;
        private bool _repaintScheduled;
        private readonly List<UnityMeshSendToBlenderTool.MeshSource> _meshQueue =
            new List<UnityMeshSendToBlenderTool.MeshSource>();
        private ReorderableList _meshQueueList;
        private bool _meshQueueAwaitingResult;
        private string _meshQueueNotice;
        private MessageType _meshQueueNoticeType = MessageType.Info;
        private int _mainTab;
        private int _port = DefaultSessionPort;
        private SessionClient _session;
        private RegistryPanelView _registryView;
        private DiagnosticsPanelView _diagnosticsView;

        [MenuItem(MenuPath)]
        public static void Open()
        {
            OpenAtTab(MainTab.Session);
        }

        private static void OpenAtTab(MainTab tab)
        {
            var window = GetWindow<SessionPanel>(false, "TriSync", true);
            window.minSize = new Vector2(820, 480);
            window.EnsureViews();
            window.SelectMainTab(tab);
            window.Show();
        }

        private void EnsureViews()
        {
            if (_session == null)
                _session = new SessionClient();
            if (_registryView == null)
                _registryView = new RegistryPanelView();
            if (_diagnosticsView == null)
                _diagnosticsView = new DiagnosticsPanelView();
        }

        private void OnEnable()
        {
            titleContent = new GUIContent(Tr("TriSync"));
            minSize = new Vector2(820, 480);
            _port = NormalizePort(EditorPrefs.GetInt(PortEditorPrefKey, DefaultSessionPort));
            _mainTab = NormalizeMainTab(EditorPrefs.GetInt(MainTabEditorPrefKey, (int)MainTab.Session));
            EnsureViews();
            EnsureMeshQueueList();
            BlenderSyncReportStore.Changed -= ScheduleRepaint;
            BlenderSyncReportStore.Changed += ScheduleRepaint;
        }

        private void OnDisable()
        {
            BlenderSyncReportStore.Changed -= ScheduleRepaint;
            EditorApplication.delayCall -= RepaintFromDelayCall;
            _repaintScheduled = false;
        }

        private void OnInspectorUpdate()
        {
            Repaint();
        }

        private void Update()
        {
            if (!ShouldTickRegistry(_mainTab) || _registryView == null)
                return;
            if (_registryView.TickAutoRefresh())
                Repaint();
        }

        private void OnGUI()
        {
            EnsureViews();
            DrawMainTabs();
            EditorGUILayout.Space(6);

            switch ((MainTab)NormalizeMainTab(_mainTab))
            {
                case MainTab.Registry:
                    _registryView.EnsureLoaded();
                    _registryView.DrawRegistry();
                    break;
                case MainTab.Diagnostics:
                    _diagnosticsView.Draw(this, _registryView);
                    break;
                default:
                    DrawSessionTab();
                    break;
            }
        }

        private void DrawMainTabs()
        {
            using (new EditorGUILayout.HorizontalScope(EditorStyles.toolbar))
            {
                var selected = GUILayout.Toolbar(
                    NormalizeMainTab(_mainTab),
                    LocalizedMainTabLabels(),
                    EditorStyles.toolbarButton,
                    GUILayout.ExpandWidth(true));
                if (selected != _mainTab)
                    SelectMainTab((MainTab)NormalizeMainTab(selected));
            }
        }

        private void SelectMainTab(MainTab tab)
        {
            _mainTab = NormalizeMainTab((int)tab);
            EditorPrefs.SetInt(MainTabEditorPrefKey, _mainTab);
        }

        private void DrawSessionTab()
        {
            _panelScroll = EditorGUILayout.BeginScrollView(_panelScroll);
            try
            {
                DrawConnectionCard();

                if (!string.IsNullOrWhiteSpace(SessionClient.LastProtocolError))
                    EditorGUILayout.HelpBox(Tr(SessionClient.LastProtocolError), MessageType.Error);

                EditorGUILayout.Space(6);
                DrawSettingsSection();
                EditorGUILayout.Space(6);
                DrawSendToBlenderSection();
            }
            finally
            {
                EditorGUILayout.EndScrollView();
            }
        }

        private void DrawSettingsSection()
        {
            _showSettings = EditorGUILayout.Foldout(
                _showSettings,
                Tr("Settings"),
                true,
                EditorStyles.foldoutHeader);
            if (!_showSettings)
                return;

            using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
                BlenderSyncUserSettingsGUI.DrawControls();
        }

        private void DrawConnectionCard()
        {
            var state = SessionClient.LastLifecycleState ?? "disconnected";
            var running = SessionClient.SharedTransport.IsRunning;
            var editModeAvailable = EditModeGuard.IsAvailable;

            using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
            {
                var statusRow = EditorGUILayout.GetControlRect(false, 24f);
                var detailsRow = EditorGUILayout.GetControlRect(false, 22f);
                var statusColor = StatusColor(state, running);
                var accentRect = new Rect(
                    statusRow.x - 5f,
                    statusRow.y,
                    3f,
                    detailsRow.yMax - statusRow.y);
                var dotRect = new Rect(statusRow.x + 2f, statusRow.y + 6f, 10f, 10f);
                var versionWidth = Math.Min(120f, Math.Max(80f, statusRow.width * 0.25f));
                var versionRect = new Rect(
                    statusRow.xMax - versionWidth,
                    statusRow.y + 1f,
                    versionWidth,
                    20f);
                var title = Tr(ConnectionTitle(state, running));
                var titleX = dotRect.xMax + 8f;
                var titleWidth = Math.Min(
                    EditorStyles.boldLabel.CalcSize(new GUIContent(title)).x + 4f,
                    Math.Max(40f, versionRect.x - titleX - 8f));
                var titleRect = new Rect(
                    titleX,
                    statusRow.y + 1f,
                    titleWidth,
                    20f);
                var contextRect = new Rect(
                    titleRect.xMax + 8f,
                    statusRow.y + 1f,
                    Math.Max(0f, versionRect.x - titleRect.xMax - 16f),
                    20f);

                EditorGUI.DrawRect(accentRect, statusColor);
                EditorGUI.DrawRect(dotRect, statusColor);
                EditorGUI.LabelField(titleRect, title, EditorStyles.boldLabel);
                if (contextRect.width >= 60f)
                    EditorGUI.LabelField(contextRect, Tr(ConnectionContext(state, running)));
                EditorGUI.LabelField(
                    versionRect,
                    new GUIContent(
                        BlenderVersionLabel(running ? _session.PeerApplicationVersion : null),
                        Tr("Blender application version reported by the connected endpoint.")),
                    EditorStyles.label);

                var portLabelRect = new Rect(titleX, detailsRow.y + 1f, 28f, 20f);
                var portValueRect = new Rect(portLabelRect.xMax + 4f, detailsRow.y + 1f, 72f, 20f);
                var actionRect = new Rect(portValueRect.xMax + 8f, detailsRow.y, 110f, 22f);
                EditorGUI.LabelField(
                    portLabelRect,
                    new GUIContent(Tr("Port"), Tr("Must match the Blender listener port.")),
                    EditorStyles.label);
                using (new EditorGUI.DisabledScope(!PortEditable(running) || !editModeAvailable))
                {
                    EditorGUI.BeginChangeCheck();
                    var nextPort = EditorGUI.DelayedIntField(portValueRect, _port);
                    if (EditorGUI.EndChangeCheck())
                    {
                        _port = NormalizePort(nextPort);
                        EditorPrefs.SetInt(PortEditorPrefKey, _port);
                    }
                }
                using (new EditorGUI.DisabledScope(!running && !editModeAvailable))
                {
                    if (GUI.Button(actionRect, Tr(ConnectionActionLabel(running))))
                    {
                        if (running)
                            _session.Disconnect();
                        else
                            _session.Connect(BuildEndpointForPort(_port));
                    }
                }

                if (!editModeAvailable)
                    EditorGUILayout.HelpBox(Tr("TriSync is available only in Edit Mode."), MessageType.Info);
            }
        }

        private void DrawSendToBlenderSection()
        {
            UnityMeshSendToBlenderTool.ReconcileConnectionState();
            ReconcileMeshQueueResult();

            _showSendToBlender = EditorGUILayout.Foldout(
                _showSendToBlender,
                Tr("Send To Blender"),
                true,
                EditorStyles.foldoutHeader);
            if (!_showSendToBlender)
                return;

            using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
            {
                EnsureMeshQueueList();
                var canSendToBlender = UnityMeshSendToBlenderTool.CanSendToBlender;
                var createPending = UnityMeshSendToBlenderTool.IsCreatePending;
                _meshQueueList.DoLayoutList();

                if (!string.IsNullOrWhiteSpace(_meshQueueNotice))
                    EditorGUILayout.HelpBox(_meshQueueNotice, _meshQueueNoticeType);

                EditorGUILayout.Space(3f);
                var canCreate = CanCreateMesh(
                    canSendToBlender,
                    createPending,
                    CountValidMeshSources(_meshQueue));
                using (new EditorGUI.DisabledScope(!canCreate))
                {
                    if (GUILayout.Button(
                            new GUIContent(
                                Tr(createPending ? "Creating..." : "Create Mesh in Blender"),
                                Tr(CreateInBlenderTooltip())),
                            GUILayout.Height(26f)))
                    {
                        if (UnityMeshSendToBlenderTool.SendMeshSources(_meshQueue))
                            _meshQueueAwaitingResult = true;
                    }
                }

                DrawCreateInBlenderResult(UnityMeshSendToBlenderTool.GetLatestResult());
            }
        }

        private void EnsureMeshQueueList()
        {
            if (_meshQueueList != null)
                return;

            _meshQueueList = new ReorderableList(
                _meshQueue,
                typeof(UnityMeshSendToBlenderTool.MeshSource),
                false,
                true,
                true,
                true)
            {
                elementHeight = EditorGUIUtility.singleLineHeight + 4f,
                drawHeaderCallback = rect => EditorGUI.LabelField(
                    rect,
                    new GUIContent(Tr("Meshes"), Tr("Use + to add a Mesh asset or mesh-bearing GameObject."))),
                drawElementCallback = (rect, index, active, focused) =>
                {
                    if (index < 0 || index >= _meshQueue.Count)
                        return;
                    var source = _meshQueue[index];
                    rect.y += 2f;
                    rect.height = EditorGUIUtility.singleLineHeight;
                    const float indexWidth = 24f;
                    var indexRect = new Rect(rect.x, rect.y, indexWidth, rect.height);
                    var fieldRect = new Rect(
                        indexRect.xMax + 4f,
                        rect.y,
                        Math.Max(80f, rect.xMax - indexRect.xMax - 4f),
                        rect.height);
                    EditorGUI.LabelField(indexRect, $"{index + 1}.");
                    var current = source?.FieldObject;
                    var selected = EditorGUI.ObjectField(
                        fieldRect,
                        current,
                        typeof(UnityEngine.Object),
                        true);
                    if (selected != current)
                    {
                        if (selected == null)
                        {
                            _meshQueue[index] = null;
                            _meshQueueNotice = null;
                        }
                        else if (UnityMeshSendToBlenderTool.TryCreateMeshSource(selected, out var replacement, out var warning))
                        {
                            if (ContainsMeshAtOtherIndex(_meshQueue, index, replacement.Mesh))
                            {
                                _meshQueue[index] = null;
                                _meshQueueNotice = Tr("That Mesh is already in the list.");
                                _meshQueueNoticeType = MessageType.Warning;
                            }
                            else
                            {
                                _meshQueue[index] = replacement;
                                _meshQueueNotice = null;
                            }
                        }
                        else
                        {
                            _meshQueue[index] = null;
                            _meshQueueNotice = LocalizeMeshQueueWarning(warning);
                            _meshQueueNoticeType = MessageType.Warning;
                        }
                    }
                },
                onCanAddCallback = _ => !UnityMeshSendToBlenderTool.IsCreatePending,
                onAddCallback = list =>
                {
                    _meshQueue.Add(null);
                    list.index = _meshQueue.Count - 1;
                    _meshQueueNotice = null;
                },
                onCanRemoveCallback = list =>
                    !UnityMeshSendToBlenderTool.IsCreatePending &&
                    list.index >= 0 &&
                    list.index < _meshQueue.Count,
                onRemoveCallback = list =>
                {
                    if (list.index < 0 || list.index >= _meshQueue.Count)
                        return;
                    _meshQueue.RemoveAt(list.index);
                    list.index = Math.Min(list.index, _meshQueue.Count - 1);
                    _meshQueueNotice = null;
                },
            };
        }

        private static bool ContainsMeshAtOtherIndex(
            IReadOnlyList<UnityMeshSendToBlenderTool.MeshSource> sources,
            int ignoredIndex,
            Mesh mesh)
        {
            if (sources == null || mesh == null)
                return false;
            for (var i = 0; i < sources.Count; i++)
            {
                if (i == ignoredIndex)
                    continue;
                if (sources[i]?.Mesh == mesh)
                    return true;
            }
            return false;
        }

        private static string LocalizeMeshQueueWarning(string warning)
        {
            const string prefix = "GameObject '";
            const string suffix = "' has no MeshFilter or SkinnedMeshRenderer shared Mesh.";
            if (!string.IsNullOrEmpty(warning) &&
                warning.StartsWith(prefix, StringComparison.Ordinal) &&
                warning.EndsWith(suffix, StringComparison.Ordinal))
            {
                var name = warning.Substring(prefix.Length, warning.Length - prefix.Length - suffix.Length);
                return Format("GameObject '{0}' has no MeshFilter or SkinnedMeshRenderer shared Mesh.", name);
            }
            return Tr(warning);
        }

        private static int CountValidMeshSources(IReadOnlyList<UnityMeshSendToBlenderTool.MeshSource> sources)
        {
            var count = 0;
            if (sources == null)
                return count;
            foreach (var source in sources)
            {
                if (source?.Mesh != null)
                    count++;
            }
            return count;
        }

        private void ReconcileMeshQueueResult()
        {
            if (!_meshQueueAwaitingResult || UnityMeshSendToBlenderTool.IsCreatePending)
                return;

            var result = UnityMeshSendToBlenderTool.GetLatestResult();
            if (string.Equals(result?.status, "imported", StringComparison.Ordinal))
            {
                _meshQueue.Clear();
                if (_meshQueueList != null)
                    _meshQueueList.index = -1;
                _meshQueueNotice = null;
            }
            _meshQueueAwaitingResult = false;
        }

        private void DrawCreateInBlenderResult(UnityMeshSendToBlenderTool.CreateInBlenderResult result)
        {
            if (result == null || string.IsNullOrWhiteSpace(result.status) || result.status == "idle")
                return;

            var text = string.IsNullOrWhiteSpace(result.message)
                ? LocalizedCreateResultSummary(result.status, result.created, result.skipped)
                : Tr(result.message);
            var messageType = MessageType.Info;
            if (result.status == "failed")
            {
                messageType = MessageType.Error;
                if (!string.IsNullOrWhiteSpace(result.error))
                    text += "\n" + Tr(result.error);
            }
            else if (result.status == "partial" || result.status == "warning")
            {
                messageType = MessageType.Warning;
            }
            if (result.status == "imported" || result.status == "partial" || result.status == "failed")
                EditorGUILayout.LabelField(Tr("Last creation"), EditorStyles.miniBoldLabel);
            EditorGUILayout.HelpBox(text, messageType);

            if (result.warnings == null || result.warnings.Length == 0)
                return;
            _showMeshImportDetails = EditorGUILayout.Foldout(
                _showMeshImportDetails,
                Format("Details ({0})", result.warnings.Length),
                true);
            if (!_showMeshImportDetails)
                return;
            foreach (var warning in result.warnings)
                EditorGUILayout.LabelField(Tr(warning), EditorStyles.wordWrappedLabel);
        }

        internal void DrawSessionDiagnostics()
        {
            EditorGUILayout.LabelField(Tr("Last activity"), Tr(FormatLifecycleTime(SessionClient.LastLifecycleAt)));
            EditorGUILayout.LabelField(Tr("Protocol"), Tr(ProtocolSummary()));
            EditorGUILayout.LabelField(Tr("Features"), Tr(FeaturesSummary()));
            if (!string.IsNullOrWhiteSpace(SessionClient.LastProtocolError))
                EditorGUILayout.LabelField(Tr("Last error"), Tr(SessionClient.LastProtocolError), EditorStyles.wordWrappedLabel);
        }

        internal string BuildCopyDiagnosticsJson(RegistryPanelView registryView)
        {
            EnsureViews();
            return UnityDiagnosticsSnapshotBuilder.BuildJson(
                _session,
                BuildEndpointForPort(_port),
                registryView?.GetDiagnosticsData(),
                BlenderSyncReportStore.Last,
                DateTime.UtcNow);
        }

        private void ScheduleRepaint()
        {
            if (_repaintScheduled)
                return;
            _repaintScheduled = true;
            EditorApplication.delayCall += RepaintFromDelayCall;
        }

        private void RepaintFromDelayCall()
        {
            _repaintScheduled = false;
            if (this == null)
                return;
            Repaint();
        }

        private static int NormalizeMainTab(int value)
        {
            return value >= (int)MainTab.Session && value <= (int)MainTab.Diagnostics
                ? value
                : (int)MainTab.Session;
        }

        private static bool ShouldTickRegistry(int value)
        {
            return NormalizeMainTab(value) == (int)MainTab.Registry;
        }

        private static string MainTabLabel(int value)
        {
            return MainTabLabels[NormalizeMainTab(value)];
        }

        private static string[] LocalizedMainTabLabels()
        {
            return new[] { Tr(MainTabLabels[0]), Tr(MainTabLabels[1]), Tr(MainTabLabels[2]) };
        }

        private static string CanonicalMenuPath()
        {
            return MenuPath;
        }

        private static ConnectionVisualState ResolveConnectionVisualState(string state, bool running)
        {
            if (string.Equals(state, "protocol_mismatch", StringComparison.Ordinal))
                return ConnectionVisualState.ProtocolMismatch;
            if (string.Equals(state, "error", StringComparison.Ordinal))
                return ConnectionVisualState.Error;
            if (running && string.Equals(state, "handshake_confirmed", StringComparison.Ordinal))
                return ConnectionVisualState.Connected;
            if (running || IsConnectingState(state))
                return ConnectionVisualState.Connecting;
            return ConnectionVisualState.Disconnected;
        }

        private static bool IsConnectingState(string state)
        {
            return state == "connect_attempted" ||
                   state == "local_ready" ||
                   state == "transport_connected" ||
                   state == "counterpart_observed";
        }

        private static Color StatusColor(string state, bool running)
        {
            switch (ResolveConnectionVisualState(state, running))
            {
                case ConnectionVisualState.Connected:
                    return new Color(0.24f, 0.78f, 0.29f);
                case ConnectionVisualState.Connecting:
                    return new Color(0.94f, 0.72f, 0.22f);
                case ConnectionVisualState.Error:
                case ConnectionVisualState.ProtocolMismatch:
                    return new Color(0.86f, 0.28f, 0.25f);
                default:
                    return new Color(0.45f, 0.45f, 0.45f);
            }
        }

        private static string ConnectionTitle(string state, bool running)
        {
            switch (ResolveConnectionVisualState(state, running))
            {
                case ConnectionVisualState.Connected: return "Connected";
                case ConnectionVisualState.Connecting: return "Connecting";
                case ConnectionVisualState.Error: return "Connection error";
                case ConnectionVisualState.ProtocolMismatch: return "Protocol mismatch";
                default: return "Disconnected";
            }
        }

        private static string ConnectionContext(string state, bool running)
        {
            switch (ResolveConnectionVisualState(state, running))
            {
                case ConnectionVisualState.Connected: return "Session ready";
                case ConnectionVisualState.Connecting: return "Waiting for handshake";
                case ConnectionVisualState.Error: return "See Diagnostics";
                case ConnectionVisualState.ProtocolMismatch: return "Update the other endpoint";
                default: return "Ready to connect";
            }
        }

        private static string ConnectionActionLabel(bool running)
        {
            return running ? "Disconnect" : "Connect";
        }

        private static string BlenderVersionLabel(string version)
        {
            var normalized = (version ?? string.Empty).Trim();
            return normalized.Length > 0 ? "Blender " + normalized : "Blender --";
        }

        private string ProtocolSummary()
        {
            if (_session.LegacyProtocol)
                return "Legacy";
            return _session.PeerProtocolVersion.HasValue
                ? $"Version {_session.PeerProtocolVersion.Value}"
                : "Not negotiated";
        }

        private string FeaturesSummary()
        {
            var features = _session.NegotiatedFeatures;
            return features == null || features.Length == 0
                ? "None"
                : string.Join(", ", features);
        }

        private static int DefaultPort()
        {
            return DefaultSessionPort;
        }

        private static int NormalizePort(int port)
        {
            return Mathf.Clamp(port, 1, 65535);
        }

        private static bool PortEditable(bool running)
        {
            return !running;
        }

        private static string BuildEndpointForPort(int port)
        {
            return $"ws://127.0.0.1:{NormalizePort(port)}/ws/";
        }

        private static bool CanCreateMesh(bool connected, bool pending, int meshCount)
        {
            return connected && !pending && meshCount > 0;
        }

        private static string CreateInBlenderTooltip()
        {
            return "Creates new Blender Mesh objects from the queued readable Meshes.\n" +
                   "Supports UV0-UV7 and single-frame BlendShapes.\n" +
                   "Rigging, current deformation, multi-frame BlendShapes, and reverse sync are not included.";
        }

        private static string CreateResultSummary(string status, int created, int skipped)
        {
            switch (status)
            {
                case "preparing": return "Preparing queued mesh data...";
                case "creating": return "Waiting for Blender to create the mesh...";
                case "queued": return "Queued in Blender.";
                case "imported": return created == 1 ? "Created 1 object in Blender." : $"Created {created} objects in Blender.";
                case "partial": return $"Created {created} objects; {skipped} skipped.";
                case "warning": return "Add a compatible Mesh to create in Blender.";
                case "failed": return "Blender could not create the queued mesh.";
                default: return "No creation result is available.";
            }
        }

        private static string LocalizedCreateResultSummary(string status, int created, int skipped)
        {
            switch (status)
            {
                case "preparing": return Tr("Preparing queued mesh data...");
                case "creating": return Tr("Waiting for Blender to create the mesh...");
                case "queued": return Tr("Queued in Blender.");
                case "imported":
                    return created == 1
                        ? Format("Created {0} object in Blender.", created)
                        : Format("Created {0} objects in Blender.", created);
                case "partial": return Format("Created {0} objects; {1} skipped.", created, skipped);
                case "warning": return Tr("Add a compatible Mesh to create in Blender.");
                case "failed": return Tr("Blender could not create the queued mesh.");
                default: return Tr("No creation result is available.");
            }
        }

        private static string FormatLifecycleTime(long value)
        {
            if (value == 0)
                return "No activity";
            try
            {
                return DateTimeOffset.FromUnixTimeSeconds(value).LocalDateTime.ToString("yyyy-MM-dd HH:mm:ss");
            }
            catch
            {
                return value.ToString();
            }
        }

        private static string FormatOptional(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? "(none)" : value;
        }

    }
}
#endif
