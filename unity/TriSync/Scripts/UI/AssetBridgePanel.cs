#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.SceneSyncCore;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.UI
{
    internal sealed class RegistryDiagnosticsData
    {
        public bool loaded;
        public bool autoRefresh;
        public string lastRefreshUtc;
        public string refreshStatus;
        public int resourceCount;
        public int mappedResourceCount;
        public int resourceErrorCount;
        public int objectCount;
        public int riggedObjectCount;
        public int boundObjectCount;
        public int missingOrRemovedObjectCount;
        public int objectIssueCount;
    }

    internal sealed class RegistryPanelView
    {
        private const int MaxRefreshStatusLength = 512;

        private Vector2 _windowScroll;
        private AptDatabase _assetDb;
        private ObjectBindingDatabase _objectDb;
        private RiggedObjectDatabase _riggedDb;
        private DateTime _lastRefreshAt;
        private bool _autoRefresh;
        private double _nextAutoRefreshAt;
        private string _registryFileSignature = string.Empty;
        private bool _hasRegistryFileSignature;
        private bool _loaded;
        private string _refreshStatus = "(not loaded)";
        private int _mainTab;
        private int _assetTypeFilterIndex = 0;
        private int _objectTypeFilterIndex = 0;

        private string _assetSearch = string.Empty;
        private string _objectSearch = string.Empty;
        private bool _objectBoundOnly = true;
        private bool _unregisterMode;
        private readonly HashSet<string> _expandedAssetRows = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> _expandedObjectRows = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> _expandedReferenceGroups = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> _unregisterAssetIds = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> _unregisterObjectPairIds = new HashSet<string>(StringComparer.Ordinal);
        private readonly Dictionary<string, string[]> _riggedMeshRefsCache = new Dictionary<string, string[]>(StringComparer.Ordinal);

        private static readonly string[] MainTabs = { "Resources", "Objects" };
        public void DrawRegistry()
        {
            DrawToolbar();

            if (_mainTab < 0 || _mainTab >= MainTabs.Length)
                _mainTab = 0;
            _mainTab = GUILayout.Toolbar(_mainTab, new[] { Tr(MainTabs[0]), Tr(MainTabs[1]) });
            EditorGUILayout.Space(6);

            _windowScroll = EditorGUILayout.BeginScrollView(_windowScroll);
            try
            {
                DrawCurrentTab();
            }
            finally
            {
                EditorGUILayout.EndScrollView();
            }
        }

        private void DrawCurrentTab()
        {
            switch (_mainTab)
            {
                case 1:
                    DrawObjectRecords();
                    break;
                default:
                    DrawAssetRecords();
                    break;
            }
        }

        public bool TickAutoRefresh()
        {
            if (!_autoRefresh)
                return false;
            if (EditorApplication.timeSinceStartup < _nextAutoRefreshAt)
                return false;

            _nextAutoRefreshAt = EditorApplication.timeSinceStartup + 1.0d;
            var currentSignature = BuildRegistryFileSignature(GetRegistryFilePaths());
            if (_hasRegistryFileSignature
                && string.Equals(currentSignature, _registryFileSignature, StringComparison.Ordinal))
                return false;

            Refresh(currentSignature);
            return true;
        }

        public void EnsureLoaded()
        {
            if (!_loaded)
                Refresh();
        }

        public RegistryDiagnosticsData GetDiagnosticsData()
        {
            var assetRecords = _assetDb?.records ?? Array.Empty<AptRecord>();
            var objectRecords = _objectDb?.records ?? Array.Empty<ObjectBindingEntry>();
            var riggedRecords = _riggedDb?.records ?? Array.Empty<RiggedObjectRecord>();
            var missingOrRemoved = objectRecords.Count(r =>
                r != null && (r.bindingState == "missing" || r.bindingState == "removed"));
            var riggedIssues = riggedRecords.Count(r =>
                r != null
                && (r.updateState == "error"
                    || (!string.IsNullOrWhiteSpace(r.mappingState) && r.mappingState != "mapped")));
            return new RegistryDiagnosticsData
            {
                loaded = _loaded,
                autoRefresh = _autoRefresh,
                lastRefreshUtc = _lastRefreshAt == default
                    ? null
                    : _lastRefreshAt.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"),
                refreshStatus = _refreshStatus,
                resourceCount = assetRecords.Length,
                mappedResourceCount = assetRecords.Count(r => r != null && r.mappingState == "mapped"),
                resourceErrorCount = assetRecords.Count(r => r != null && r.updateState == "error"),
                objectCount = objectRecords.Length + riggedRecords.Length,
                riggedObjectCount = riggedRecords.Length,
                boundObjectCount = objectRecords.Count(r => r != null && r.bindingState == "bound")
                    + riggedRecords.Count(IsRiggedObjectBound),
                missingOrRemovedObjectCount = missingOrRemoved,
                objectIssueCount = missingOrRemoved + riggedIssues,
            };
        }

        private void DrawToolbar()
        {
            using (new EditorGUILayout.HorizontalScope(EditorStyles.toolbar))
            {
                GUILayout.Label(Tr("Registry"), GUILayout.ExpandWidth(true));
                var autoRefresh = EditorGUILayout.ToggleLeft(Tr("Auto Refresh"), _autoRefresh, GUILayout.Width(100));
                if (autoRefresh != _autoRefresh)
                {
                    _autoRefresh = autoRefresh;
                    _nextAutoRefreshAt = 0d;
                }
                if (GUILayout.Button(Tr("Refresh"), EditorStyles.toolbarButton, GUILayout.Width(100)))
                    Refresh();
            }
        }

        private static string[] GetRegistryFilePaths()
        {
            var aptRepository = new AptRepository();
            return new[]
            {
                aptRepository.GetShardPath("mesh"),
                aptRepository.GetShardPath("material"),
                aptRepository.GetShardPath("texture"),
                new ObjectBindingRegistry().StoragePath,
                new RiggedObjectRegistry().StoragePath,
            };
        }

        private static string BuildRegistryFileSignature(IEnumerable<string> paths)
        {
            var entries = new List<string>();
            foreach (var sourcePath in paths ?? Enumerable.Empty<string>())
            {
                var path = string.IsNullOrWhiteSpace(sourcePath)
                    ? string.Empty
                    : Path.GetFullPath(sourcePath);
                var prefix = path.Length + ":" + path;
                if (path.Length == 0)
                {
                    entries.Add(prefix + "|missing");
                    continue;
                }

                try
                {
                    var info = new FileInfo(path);
                    info.Refresh();
                    entries.Add(info.Exists
                        ? prefix + "|exists|" + info.LastWriteTimeUtc.Ticks + "|" + info.Length
                        : prefix + "|missing");
                }
                catch (Exception ex)
                {
                    entries.Add(prefix + "|error|" + ex.GetType().FullName);
                }
            }

            return string.Join("\n", entries);
        }

        public void DrawDiagnosticsOverview()
        {
            var data = GetDiagnosticsData();
            EditorGUILayout.LabelField(Tr("Last refresh"), Tr(FormatLastRefresh()));
            if (!string.IsNullOrWhiteSpace(data.refreshStatus)
                && !string.Equals(data.refreshStatus, "OK", StringComparison.OrdinalIgnoreCase))
            {
                EditorGUILayout.LabelField(Tr("Refresh error"), data.refreshStatus, EditorStyles.wordWrappedLabel);
            }
            EditorGUILayout.LabelField(
                Tr("Resources"),
                Format("{0} total, {1} mapped, {2} issues", data.resourceCount, data.mappedResourceCount, data.resourceErrorCount));
            EditorGUILayout.LabelField(
                Tr("Objects"),
                Format("{0} total, {1} bound, {2} issues", data.objectCount, data.boundObjectCount, data.objectIssueCount));
        }

        public void DrawPipelineDiagnostics()
        {
            var queue = AssetBridgeCore.AssetImportCoordinator.GetQueueSnapshot();
            var assemblyQueue = ObjectAssemblyService.GetQueueSnapshot();
            EditorGUILayout.LabelField(
                Tr("Asset queue"),
                LocalizedPipelineQueue(queue.processing, queue.pendingCount, queue.failedCount));
            if (!string.IsNullOrWhiteSpace(queue.lastError))
                EditorGUILayout.LabelField(Tr("Asset error"), queue.lastError, EditorStyles.wordWrappedLabel);

            EditorGUILayout.LabelField(
                Tr("Object queue"),
                LocalizedPipelineQueue(
                    assemblyQueue.processing,
                    assemblyQueue.pendingCount,
                    assemblyQueue.failedCount + assemblyQueue.timeoutCount));
            if (!string.IsNullOrWhiteSpace(assemblyQueue.lastError))
                EditorGUILayout.LabelField(Tr("Object error"), assemblyQueue.lastError, EditorStyles.wordWrappedLabel);
        }

        private static string FormatPipelineQueue(bool processing, int pendingCount, int failedCount)
        {
            var parts = new List<string>
            {
                processing ? "Processing" : pendingCount > 0 ? "Pending" : "Idle",
            };
            if (pendingCount > 0)
                parts.Add($"{pendingCount} pending");
            if (failedCount > 0)
                parts.Add($"{failedCount} failed");
            return string.Join(", ", parts);
        }

        private static string LocalizedPipelineQueue(bool processing, int pendingCount, int failedCount)
        {
            var parts = new List<string>
            {
                Tr(processing ? "Processing" : pendingCount > 0 ? "Pending" : "Idle"),
            };
            if (pendingCount > 0)
                parts.Add(Format("{0} pending", pendingCount));
            if (failedCount > 0)
                parts.Add(Format("{0} failed", failedCount));
            return string.Join(", ", parts);
        }

        private void DrawAssetRecords()
        {
            var records = _assetDb?.records ?? Array.Empty<AptRecord>();
            if (records.Length == 0)
            {
                EditorGUILayout.HelpBox(Tr("No resource records yet. Import objects from Blender first."), MessageType.Info);
                return;
            }

            DrawAssetRecordFilters();

            var filtered = records.Where(ShouldIncludeAssetRecord).ToArray();
            DrawUnregisterControls();
            if (!TryBeginRecordsList(filtered.Length, records.Length, "No resource records found for current filter."))
                return;

            foreach (var r in filtered)
                DrawAssetRecordRow(r);
        }

        private void DrawObjectRecords()
        {
            var objectRecords = _objectDb?.records ?? Array.Empty<ObjectBindingEntry>();
            var riggedRecords = _riggedDb?.records ?? Array.Empty<RiggedObjectRecord>();
            var totalCount = objectRecords.Length + riggedRecords.Length;
            if (totalCount == 0)
            {
                EditorGUILayout.HelpBox(Tr("No object binding records yet. Import objects from Blender first."), MessageType.Info);
                return;
            }

            DrawObjectRecordFilters();

            var filteredObjects = objectRecords.Where(ShouldIncludeObjectRecord).ToArray();
            var filteredRigged = riggedRecords.Where(ShouldIncludeRiggedObjectRecord).ToArray();
            DrawUnregisterControls();
            if (!TryBeginRecordsList(
                    filteredObjects.Length + filteredRigged.Length,
                    totalCount,
                    "No object records found for current filter."))
                return;

            foreach (var r in filteredObjects)
                DrawObjectRecordRow(r);
            foreach (var r in filteredRigged)
                DrawRiggedObjectRecordRow(r);
        }

        private bool TryBeginRecordsList(int filteredCount, int totalCount, string emptyMessage)
        {
            EditorGUILayout.LabelField(Format("Showing {0} / {1}", filteredCount, totalCount));
            if (filteredCount != 0)
                return true;

            EditorGUILayout.HelpBox(Tr(emptyMessage), MessageType.Info);
            return false;
        }

        private void DrawAssetRecordFilters()
        {
            var filterOptions = new[] { Tr("All"), Tr("Mesh"), Tr("Material"), Tr("Texture") };
            if (_assetTypeFilterIndex < 0 || _assetTypeFilterIndex >= filterOptions.Length)
                _assetTypeFilterIndex = 0;
            using (new EditorGUILayout.HorizontalScope())
            {
                EditorGUILayout.LabelField(Tr("Type"), GUILayout.Width(34));
                _assetTypeFilterIndex = EditorGUILayout.Popup(_assetTypeFilterIndex, filterOptions, GUILayout.Width(120));
                EditorGUILayout.LabelField(Tr("Search"), GUILayout.Width(44));
                _assetSearch = EditorGUILayout.TextField(_assetSearch ?? string.Empty);
            }
        }

        private void DrawObjectRecordFilters()
        {
            var filterOptions = new[] { Tr("All"), Tr("Object"), Tr("Rigged Object") };
            if (_objectTypeFilterIndex < 0 || _objectTypeFilterIndex >= filterOptions.Length)
                _objectTypeFilterIndex = 0;
            using (new EditorGUILayout.HorizontalScope())
            {
                EditorGUILayout.LabelField(Tr("Type"), GUILayout.Width(34));
                _objectTypeFilterIndex = EditorGUILayout.Popup(_objectTypeFilterIndex, filterOptions, GUILayout.Width(120));
                _objectBoundOnly = EditorGUILayout.ToggleLeft(Tr("Bound only"), _objectBoundOnly, GUILayout.Width(100));
                EditorGUILayout.LabelField(Tr("Search"), GUILayout.Width(44));
                _objectSearch = EditorGUILayout.TextField(_objectSearch ?? string.Empty);
            }
        }

        private void DrawUnregisterControls()
        {
            var selectedResources = _unregisterAssetIds.Count;
            var selectedObjects = _unregisterObjectPairIds.Count;
            var selectedTotal = selectedResources + selectedObjects;

            if (!_unregisterMode)
            {
                using (new EditorGUILayout.HorizontalScope())
                {
                    GUILayout.FlexibleSpace();
                    if (GUILayout.Button(Tr("Unregister..."), GUILayout.Width(120)))
                        EnterUnregisterMode();
                }
                return;
            }

            EditorGUILayout.BeginVertical("box");
            using (new EditorGUILayout.HorizontalScope())
            {
                EditorGUILayout.LabelField(Tr("Unregister Mode"), EditorStyles.boldLabel, GUILayout.Width(130));
                EditorGUILayout.LabelField(Format("Selected: resources={0} objects={1}", selectedResources, selectedObjects), GUILayout.Width(230));
            }

            using (new EditorGUILayout.HorizontalScope())
            {
                GUILayout.FlexibleSpace();
                using (new EditorGUI.DisabledScope(selectedTotal == 0))
                {
                    if (GUILayout.Button(Tr("Unregister Selected"), GUILayout.Width(150)))
                        UnregisterSelected();
                }

                if (GUILayout.Button(Tr("Cancel"), GUILayout.Width(80)))
                    ExitUnregisterMode();
            }
            EditorGUILayout.EndVertical();
        }

        private void EnterUnregisterMode()
        {
            _unregisterMode = true;
            ClearUnregisterSelection();
            SetRefreshStatus("unregister mode enabled");
        }

        private void ExitUnregisterMode()
        {
            _unregisterMode = false;
            ClearUnregisterSelection();
            SetRefreshStatus("unregister mode cancelled");
        }

        private void ClearUnregisterSelection()
        {
            _unregisterAssetIds.Clear();
            _unregisterObjectPairIds.Clear();
        }

        private static void SetUnregisterSelection(HashSet<string> selection, string id, bool selected)
        {
            if (selection == null || string.IsNullOrWhiteSpace(id))
                return;
            if (selected)
                selection.Add(id);
            else
                selection.Remove(id);
        }

        private void UnregisterSelected()
        {
            var selectedResources = _unregisterAssetIds.Count;
            var selectedObjects = _unregisterObjectPairIds.Count;
            if (selectedResources + selectedObjects == 0)
                return;

            ExecutePanelAction("unregister selected", () =>
            {
                var result = new RegistryUnregisterService().Unregister(_unregisterAssetIds.ToArray(), _unregisterObjectPairIds.ToArray());
                _unregisterMode = false;
                ClearUnregisterSelection();
                Refresh();
                SetRefreshStatus(
                    $"unregister ok; resources={result.removedResources} objects={result.removedObjects} rigged={result.removedRiggedObjects}",
                    updateRefreshTime: true);
            });
        }

        private void DrawAssetRecordRow(AptRecord r)
        {
            var type = r.target?.resourceType ?? "";
            var path = r.target?.unityAssetPath ?? "";
            var assetObj = ResolveAssetObject(r);
            var assetId = r.assetId ?? string.Empty;

            EditorGUILayout.BeginVertical("box");
            var expanded = DrawRegistryCardHeader(
                _expandedAssetRows.Contains(assetId),
                FormatUnityObjectName(assetObj, path),
                Tr(FormatResourceType(type)),
                assetObj,
                typeof(UnityEngine.Object),
                allowSceneObjects: false,
                _unregisterMode,
                _unregisterAssetIds.Contains(assetId),
                selected => SetUnregisterSelection(_unregisterAssetIds, assetId, selected));
            SetExpanded(_expandedAssetRows, r.assetId, expanded);

            if (expanded)
                DrawAssetUsageRows(r);
            EditorGUILayout.EndVertical();
        }

        private void DrawObjectRecordRow(ObjectBindingEntry r)
        {
            var sceneObj = ResolveSceneObject(r);
            var pairId = r.pairId ?? string.Empty;

            EditorGUILayout.BeginVertical("box");
            var expanded = DrawRegistryCardHeader(
                _expandedObjectRows.Contains(pairId),
                FormatSceneObjectName(sceneObj, r.sceneObjectId),
                Tr("Object"),
                sceneObj,
                typeof(GameObject),
                allowSceneObjects: true,
                _unregisterMode,
                _unregisterObjectPairIds.Contains(pairId),
                selected => SetUnregisterSelection(_unregisterObjectPairIds, pairId, selected));
            SetExpanded(_expandedObjectRows, r.pairId, expanded);

            var meshAssetObj = ResolveAssetObjectByGuid(r.meshAssetGuid);
            var materialAssetObjs = ResolveAssetObjectsByGuids(r.materialAssetGuids);

            if (expanded)
                DrawObjectResourceRows(r, meshAssetObj, materialAssetObjs);
            EditorGUILayout.EndVertical();
        }

        private void DrawRiggedObjectRecordRow(RiggedObjectRecord r)
        {
            var sceneObj = ResolveSceneObject(r?.managedInstance?.sceneObjectId);
            var prefab = ResolveRiggedPrefab(r);
            var target = sceneObj != null ? (UnityEngine.Object)sceneObj : prefab;
            var pairId = ResolveRiggedPairId(r);
            var rowKey = "rigged:" + (r?.riggedObjectId ?? pairId);
            var displayName = string.IsNullOrWhiteSpace(r?.objectName)
                ? FormatUnityObjectName(target, r?.prefabPath)
                : r.objectName;

            EditorGUILayout.BeginVertical("box");
            var expanded = DrawRegistryCardHeader(
                _expandedObjectRows.Contains(rowKey),
                displayName,
                Tr("Rigged Object"),
                target,
                typeof(GameObject),
                allowSceneObjects: true,
                _unregisterMode,
                _unregisterObjectPairIds.Contains(pairId),
                selected => SetUnregisterSelection(_unregisterObjectPairIds, pairId, selected));
            SetExpanded(_expandedObjectRows, rowKey, expanded);

            if (expanded)
                DrawRiggedObjectResourceRows(r, sceneObj, prefab);
            EditorGUILayout.EndVertical();
        }

        private static bool DrawRegistryCardHeader(
            bool expanded,
            string name,
            string typeLabel,
            UnityEngine.Object target,
            Type objectType,
            bool allowSceneObjects,
            bool unregisterMode,
            bool unregisterSelected,
            Action<bool> onUnregisterSelectionChanged)
        {
            const float headerHeight = 56f;
            const float leftWidth = 54f;
            const float thumbnailSize = 32f;
            const float controlGap = 4f;

            var rect = GUILayoutUtility.GetRect(1f, headerHeight, GUILayout.ExpandWidth(true), GUILayout.Height(headerHeight));
            var lineHeight = EditorGUIUtility.singleLineHeight;
            var titleY = rect.y + 6f;
            var fieldY = rect.y + 28f;

            var thumbnailRect = new Rect(rect.x + 18f, rect.y + 7f, thumbnailSize, thumbnailSize);
            var foldoutRect = new Rect(rect.x + 1f, fieldY + lineHeight * 0.5f, 14f, lineHeight);
            var checkboxRect = unregisterMode
                ? new Rect(rect.x + 1f, rect.y + 7f, 16f, lineHeight)
                : Rect.zero;
            if (unregisterMode)
            {
                var newSelected = EditorGUI.Toggle(checkboxRect, unregisterSelected);
                if (newSelected != unregisterSelected)
                    onUnregisterSelectionChanged?.Invoke(newSelected);
            }
            DrawPreviewThumbnail(thumbnailRect, target);
            expanded = EditorGUI.Foldout(foldoutRect, expanded, GUIContent.none, true);

            var contentX = rect.x + leftWidth;
            var contentWidth = Mathf.Max(0f, rect.width - leftWidth);
            var titleRect = new Rect(contentX, titleY, contentWidth, lineHeight);
            EditorGUI.LabelField(titleRect, name ?? string.Empty, EditorStyles.boldLabel);

            var typeLabelWidth = Mathf.Max(
                54f,
                EditorStyles.label.CalcSize(new GUIContent(typeLabel ?? string.Empty)).x + 4f);
            var labelRect = new Rect(contentX, fieldY, typeLabelWidth, lineHeight);
            var fieldRect = new Rect(
                labelRect.xMax + controlGap,
                fieldY,
                Mathf.Max(40f, rect.xMax - labelRect.xMax - controlGap),
                lineHeight);

            EditorGUI.LabelField(labelRect, typeLabel ?? string.Empty);
            using (new EditorGUI.DisabledScope(true))
            {
                EditorGUI.ObjectField(fieldRect, target, objectType, allowSceneObjects);
            }

            expanded = ToggleCardHeaderOnClick(expanded, rect, fieldRect, checkboxRect);
            return expanded;
        }

        private static bool ToggleCardHeaderOnClick(bool expanded, Rect headerRect, Rect fieldRect, Rect checkboxRect)
        {
            var evt = Event.current;
            if (evt == null || evt.type != EventType.MouseDown || evt.button != 0)
                return expanded;
            if (!headerRect.Contains(evt.mousePosition))
                return expanded;
            if (fieldRect.Contains(evt.mousePosition)
                || checkboxRect.Contains(evt.mousePosition))
                return expanded;

            evt.Use();
            return !expanded;
        }

        private static void SetExpanded(HashSet<string> expandedRows, string key, bool expanded)
        {
            if (expandedRows == null || string.IsNullOrWhiteSpace(key))
                return;
            if (expanded)
                expandedRows.Add(key);
            else
                expandedRows.Remove(key);
        }

        private void DrawAssetUsageRows(AptRecord record)
        {
            var refs = (record?.referencedBy ?? Array.Empty<AptReferenceRef>())
                .Where(r => r != null)
                .ToArray();
            var textureConsumers = FindMaterialTextureConsumers(record).ToArray();
            var riggedConsumers = FindRiggedResourceConsumers(record?.assetId).ToArray();
            var textureSlots = (record?.materialTextureSlots ?? Array.Empty<AptMaterialTextureSlotFingerprint>())
                .Where(s => s != null && !string.IsNullOrWhiteSpace(s.assetPath))
                .ToArray();
            var recordKey = string.IsNullOrWhiteSpace(record?.assetId) ? "asset:unknown" : "asset:" + record.assetId;

            EditorGUILayout.Space(3);
            if (DrawReferenceGroupHeader(recordKey + ":references", Tr("References"), textureSlots.Length))
            {
                using (new EditorGUI.IndentLevelScope())
                {
                    if (textureSlots.Length == 0)
                        EditorGUILayout.LabelField(Tr("No referenced assets recorded."), EditorStyles.miniLabel);
                    else
                        DrawMaterialTextureSlotRows(textureSlots);
                }
            }

            var referencedByCount = refs.Length + textureConsumers.Length + riggedConsumers.Length;
            if (DrawReferenceGroupHeader(recordKey + ":referencedBy", Tr("Referenced By"), referencedByCount))
            {
                using (new EditorGUI.IndentLevelScope())
                {
                    if (referencedByCount == 0)
                    {
                        EditorGUILayout.LabelField(Tr("No object or material references recorded."), EditorStyles.miniLabel);
                        return;
                    }

                    foreach (var refEntry in refs)
                    {
                        var objectRecord = FindObjectRecordByPairId(refEntry.pairId);
                        var referencedObj = ResolveSceneObject(refEntry.sceneObjectId) ?? ResolveSceneObject(objectRecord);
                        var slotText = DescribeObjectResourceSlots(objectRecord, record.assetId);
                        DrawReferenceRow(slotText, referencedObj, typeof(GameObject), true);
                    }

                    foreach (var usage in textureConsumers)
                    {
                        var materialObj = ResolveAssetObject(usage.materialRecord);
                        DrawReferenceRow(Format("Material Texture: {0}", usage.slot), materialObj, typeof(UnityEngine.Object), false);
                    }

                    foreach (var usage in riggedConsumers)
                    {
                        var riggedObj = ResolveSceneObject(usage.record?.managedInstance?.sceneObjectId)
                            ?? ResolveRiggedPrefab(usage.record);
                        DrawReferenceRow(Format("Rigged: {0}", usage.slots), riggedObj, typeof(GameObject), true);
                    }
                }
            }
        }

        private void DrawObjectResourceRows(ObjectBindingEntry record, UnityEngine.Object meshAssetObj, UnityEngine.Object[] materialAssetObjs)
        {
            EditorGUILayout.Space(3);
            var materialRefs = record?.materialRefs ?? Array.Empty<string>();
            var materialCount = Math.Max(materialRefs.Length, materialAssetObjs?.Length ?? 0);
            var slotCount = 1 + materialCount;
            var recordKey = string.IsNullOrWhiteSpace(record?.pairId) ? "object:unknown" : "object:" + record.pairId;
            if (!DrawReferenceGroupHeader(recordKey + ":assets", Tr("Referenced Assets"), slotCount))
                return;

            using (new EditorGUI.IndentLevelScope())
            {
                DrawReferenceRow(Tr("Mesh"), meshAssetObj, typeof(UnityEngine.Object), false);
                if (materialCount == 0)
                {
                    EditorGUILayout.LabelField(Tr("No material slots recorded."), EditorStyles.miniLabel);
                    return;
                }

                for (var i = 0; i < materialCount; i++)
                {
                    var materialObj = materialAssetObjs != null && i < materialAssetObjs.Length ? materialAssetObjs[i] : null;
                    DrawReferenceRow(Format("Material[{0}]", i), materialObj, typeof(UnityEngine.Object), false);
                }
            }
        }

        private void DrawRiggedObjectResourceRows(RiggedObjectRecord record, GameObject sceneObj, GameObject prefab)
        {
            var meshRefs = ResolveRiggedMeshRefs(record);
            var materialRefs = record?.materialRefs ?? Array.Empty<string>();
            var referenceCount = 2 + meshRefs.Length + materialRefs.Length;
            var recordKey = string.IsNullOrWhiteSpace(record?.riggedObjectId)
                ? "rigged:unknown"
                : "rigged:" + record.riggedObjectId;
            if (!DrawReferenceGroupHeader(recordKey + ":references", Tr("References"), referenceCount))
                return;

            using (new EditorGUI.IndentLevelScope())
            {
                DrawReferenceRow(Tr("Prefab"), prefab, typeof(GameObject), false);
                DrawReferenceRow(Tr("Managed Instance"), sceneObj, typeof(GameObject), true);
                for (var i = 0; i < meshRefs.Length; i++)
                {
                    var label = meshRefs.Length == 1 ? Tr("Mesh") : Format("Mesh[{0}]", i);
                    DrawReferenceRow(label, ResolveAssetObjectById(meshRefs[i]), typeof(UnityEngine.Object), false);
                }

                if (materialRefs.Length == 0)
                {
                    EditorGUILayout.LabelField(Tr("No material references recorded."), EditorStyles.miniLabel);
                    return;
                }

                for (var i = 0; i < materialRefs.Length; i++)
                {
                    DrawReferenceRow(
                        Format("Material[{0}]", i),
                        ResolveAssetObjectById(materialRefs[i]),
                        typeof(UnityEngine.Object),
                        false);
                }
            }
        }

        private bool DrawReferenceGroupHeader(string key, string label, int count)
        {
            var expanded = !string.IsNullOrWhiteSpace(key) && _expandedReferenceGroups.Contains(key);
            using (new EditorGUILayout.HorizontalScope())
            {
                expanded = EditorGUILayout.Foldout(expanded, label, true);
                EditorGUILayout.LabelField(count.ToString(), EditorStyles.miniTextField, GUILayout.Width(48));
            }

            SetExpanded(_expandedReferenceGroups, key, expanded);
            return expanded;
        }

        private void DrawMaterialTextureSlotRows(AptMaterialTextureSlotFingerprint[] slots)
        {
            if (slots == null || slots.Length == 0)
                return;

            foreach (var slot in slots)
            {
                var textureObj = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(slot.assetPath);
                DrawReferenceRow(Format("Texture: {0}", slot.slot ?? Tr("(slot)")), textureObj, typeof(UnityEngine.Object), false);
            }
        }

        private void DrawReferenceRow(string label, UnityEngine.Object target, Type objectType, bool allowSceneObjects)
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                EditorGUILayout.LabelField(label, GUILayout.Width(128));
                using (new EditorGUI.DisabledScope(true))
                {
                    EditorGUILayout.ObjectField(target, objectType, allowSceneObjects);
                }
            }
        }

        private ObjectBindingEntry FindObjectRecordByPairId(string pairId)
        {
            if (string.IsNullOrWhiteSpace(pairId))
                return null;
            return (_objectDb?.records ?? Array.Empty<ObjectBindingEntry>())
                .FirstOrDefault(r => r != null && string.Equals(r.pairId, pairId, StringComparison.Ordinal));
        }

        private IEnumerable<(AptRecord materialRecord, string slot)> FindMaterialTextureConsumers(AptRecord textureRecord)
        {
            var texturePath = textureRecord?.target?.unityAssetPath;
            if (string.IsNullOrWhiteSpace(texturePath))
                yield break;

            foreach (var materialRecord in _assetDb?.records ?? Array.Empty<AptRecord>())
            {
                if (materialRecord == null)
                    continue;
                var type = materialRecord.target?.resourceType ?? string.Empty;
                if (!string.Equals(type, "material", StringComparison.OrdinalIgnoreCase))
                    continue;
                foreach (var slot in materialRecord.materialTextureSlots ?? Array.Empty<AptMaterialTextureSlotFingerprint>())
                {
                    if (slot == null || string.IsNullOrWhiteSpace(slot.assetPath))
                        continue;
                    if (PathsEqual(slot.assetPath, texturePath))
                        yield return (materialRecord, slot.slot ?? "(slot)");
                }
            }
        }

        private IEnumerable<(RiggedObjectRecord record, string slots)> FindRiggedResourceConsumers(string assetId)
        {
            if (string.IsNullOrWhiteSpace(assetId))
                yield break;

            foreach (var record in _riggedDb?.records ?? Array.Empty<RiggedObjectRecord>())
            {
                var slots = DescribeRiggedResourceSlotsCore(record, assetId, ResolveRiggedMeshRefs(record));
                if (!string.IsNullOrWhiteSpace(slots))
                    yield return (record, slots);
            }
        }

        private static string DescribeRiggedResourceSlots(RiggedObjectRecord record, string assetId)
        {
            return DescribeRiggedResourceSlotsCore(record, assetId, CollectStoredRiggedMeshRefs(record));
        }

        private static string DescribeRiggedResourceSlotsCore(
            RiggedObjectRecord record,
            string assetId,
            string[] meshRefs)
        {
            if (record == null || string.IsNullOrWhiteSpace(assetId))
                return string.Empty;

            var normalizedAssetId = assetId.Trim();
            var slots = new List<string>();
            meshRefs = meshRefs ?? Array.Empty<string>();
            for (var i = 0; i < meshRefs.Length; i++)
            {
                if (!string.Equals(meshRefs[i]?.Trim(), normalizedAssetId, StringComparison.Ordinal))
                    continue;
                slots.Add(meshRefs.Length == 1 ? "Mesh" : $"Mesh[{i}]");
            }

            var materialRefs = record.materialRefs ?? Array.Empty<string>();
            for (var i = 0; i < materialRefs.Length; i++)
            {
                if (string.Equals(materialRefs[i]?.Trim(), normalizedAssetId, StringComparison.Ordinal))
                    slots.Add($"Material[{i}]");
            }

            return slots.Count == 0 ? string.Empty : string.Join(", ", slots);
        }

        private string[] ResolveRiggedMeshRefs(RiggedObjectRecord record)
        {
            if (record == null)
                return Array.Empty<string>();

            var cacheKey = !string.IsNullOrWhiteSpace(record.riggedObjectId)
                ? "id:" + record.riggedObjectId.Trim()
                : !string.IsNullOrWhiteSpace(record.prefabPath)
                    ? "prefab:" + record.prefabPath.Trim()
                    : null;
            if (cacheKey != null && _riggedMeshRefsCache.TryGetValue(cacheKey, out var cached))
                return cached;

            var refs = CollectStoredRiggedMeshRefs(record).ToList();
            var hasPersistedMeshRefs = record.meshRefs != null
                && record.meshRefs.Any(v => !string.IsNullOrWhiteSpace(v));
            if (!hasPersistedMeshRefs)
            {
                var prefab = ResolveRiggedPrefab(record);
                foreach (var renderer in prefab != null
                             ? prefab.GetComponentsInChildren<SkinnedMeshRenderer>(true)
                             : Array.Empty<SkinnedMeshRenderer>())
                {
                    var assetId = FindAssetIdForObject(renderer != null ? renderer.sharedMesh : null);
                    AddUniqueNormalizedRef(refs, assetId);
                }
            }

            var resolved = refs.ToArray();
            if (cacheKey != null)
                _riggedMeshRefsCache[cacheKey] = resolved;
            return resolved;
        }

        private static string[] CollectStoredRiggedMeshRefs(RiggedObjectRecord record)
        {
            var refs = new List<string>();
            foreach (var meshRef in record?.meshRefs ?? Array.Empty<string>())
                AddUniqueNormalizedRef(refs, meshRef);
            AddUniqueNormalizedRef(refs, record?.meshRef);
            return refs.ToArray();
        }

        private static void AddUniqueNormalizedRef(List<string> refs, string value)
        {
            if (refs == null)
                return;
            var normalized = value?.Trim();
            if (string.IsNullOrWhiteSpace(normalized) || refs.Contains(normalized))
                return;
            refs.Add(normalized);
        }

        private static string DescribeObjectResourceSlots(ObjectBindingEntry objectRecord, string assetId)
        {
            if (objectRecord == null || string.IsNullOrWhiteSpace(assetId))
                return "Object";

            var slots = new List<string>();
            if (string.Equals(objectRecord.meshRef, assetId, StringComparison.Ordinal))
                slots.Add("Mesh");

            var materialRefs = objectRecord.materialRefs ?? Array.Empty<string>();
            for (var i = 0; i < materialRefs.Length; i++)
            {
                if (string.Equals(materialRefs[i], assetId, StringComparison.Ordinal))
                    slots.Add($"Material[{i}]");
            }

            return slots.Count == 0 ? "Object" : string.Join(", ", slots);
        }

        private bool ShouldIncludeAssetRecord(AptRecord r)
        {
            if (r == null)
                return false;

            if (_assetTypeFilterIndex != 0)
            {
                var type = r.target != null ? (r.target.resourceType ?? string.Empty).Trim() : string.Empty;
                switch (_assetTypeFilterIndex)
                {
                    case 1: if (!string.Equals(type, "mesh", StringComparison.OrdinalIgnoreCase)) return false; break;
                    case 2: if (!string.Equals(type, "material", StringComparison.OrdinalIgnoreCase)) return false; break;
                    case 3: if (!string.Equals(type, "texture", StringComparison.OrdinalIgnoreCase)) return false; break;
                }
            }

            if (!string.IsNullOrWhiteSpace(_assetSearch))
            {
                var q = _assetSearch.Trim();
                var type = r.target?.resourceType ?? string.Empty;
                var id = r.assetId ?? string.Empty;
                var path = r.target?.unityAssetPath ?? string.Empty;
                var name = FormatUnityObjectName(ResolveAssetObject(r), path);
                if (!MatchesSearch(q, id, path, type, name))
                    return false;
            }

            return true;
        }

        private bool ShouldIncludeObjectRecord(ObjectBindingEntry r)
        {
            if (r == null)
                return false;

            if (_objectTypeFilterIndex == 2)
                return false;

            if (_objectBoundOnly && !string.Equals(r.bindingState, "bound", StringComparison.Ordinal))
                return false;

            if (!string.IsNullOrWhiteSpace(_objectSearch))
            {
                var q = _objectSearch.Trim();
                var pair = r.pairId ?? string.Empty;
                var meshRef = r.meshRef ?? string.Empty;
                var meshGuid = r.meshAssetGuid ?? string.Empty;
                var sceneObj = ResolveSceneObject(r);
                if (!MatchesSearch(q, pair, meshRef, meshGuid, FormatSceneObjectName(sceneObj, r.sceneObjectId), FormatSceneObjectPath(sceneObj)))
                    return false;
            }

            return true;
        }

        private bool ShouldIncludeRiggedObjectRecord(RiggedObjectRecord r)
        {
            if (r == null)
                return false;

            if (_objectTypeFilterIndex == 1)
                return false;

            if (_objectBoundOnly && !IsRiggedObjectBound(r))
                return false;

            if (!string.IsNullOrWhiteSpace(_objectSearch))
            {
                var q = _objectSearch.Trim();
                var sceneObj = ResolveSceneObject(r.managedInstance?.sceneObjectId);
                var prefab = ResolveRiggedPrefab(r);
                var pairId = ResolveRiggedPairId(r);
                var meshRefs = string.Join(" ", ResolveRiggedMeshRefs(r));
                var materialRefs = r.materialRefs ?? Array.Empty<string>();
                var materialText = string.Join(" ", materialRefs.Where(v => !string.IsNullOrWhiteSpace(v)));
                if (!MatchesSearch(
                        q,
                        r.riggedObjectId,
                        r.objectName,
                        pairId,
                        r.prefabPath,
                        r.meshRef,
                        meshRefs,
                        materialText,
                        r.mappingState,
                        r.updateState,
                        r.lastError,
                        r.managedInstance?.scenePath,
                        FormatSceneObjectName(sceneObj, r.managedInstance?.sceneObjectId),
                        FormatUnityObjectName(prefab, r.prefabPath)))
                    return false;
            }

            return true;
        }

        private static bool IsRiggedObjectBound(RiggedObjectRecord record)
        {
            return record?.managedInstance != null
                && !string.IsNullOrWhiteSpace(record.managedInstance.pairId);
        }

        private static string ResolveRiggedPairId(RiggedObjectRecord record)
        {
            var managedPairId = record?.managedInstance?.pairId?.Trim();
            if (!string.IsNullOrWhiteSpace(managedPairId))
                return managedPairId;

            var riggedObjectId = record?.riggedObjectId?.Trim();
            return string.IsNullOrWhiteSpace(riggedObjectId)
                ? string.Empty
                : "rigpair-" + riggedObjectId;
        }

        private void SetRefreshStatus(string status, bool updateRefreshTime = false)
        {
            _refreshStatus = NormalizeRefreshStatus(status);
            if (updateRefreshTime)
                _lastRefreshAt = DateTime.Now;
        }

        private static string NormalizeRefreshStatus(string status)
        {
            var normalized = status?.Trim();
            if (string.IsNullOrEmpty(normalized))
                return "(none)";

            normalized = normalized
                .Replace('\r', ' ')
                .Replace('\n', ' ');

            return normalized.Length <= MaxRefreshStatusLength
                ? normalized
                : normalized.Substring(0, MaxRefreshStatusLength) + "...";
        }

        private void ExecutePanelAction(string actionName, Action action)
        {
            if (action == null)
                return;

            try
            {
                action();
            }
            catch (Exception ex)
            {
                var message = $"{actionName} failed: {ex.Message}";
                SetRefreshStatus(message);
                BlenderSyncReportStore.Add("Asset Bridge Panel", "ERROR", message, new Dictionary<string, object>
                {
                    { "action", actionName },
                    { "exceptionType", ex.GetType().Name },
                });
                BlenderSyncLog.Exception(
                    "RegistryPanel",
                    "action_failed",
                    ex,
                    message,
                    new Dictionary<string, object> { { "action", actionName } });
            }
        }

        private string FormatLastRefresh()
        {
            return _lastRefreshAt == default ? "(never)" : _lastRefreshAt.ToString("yyyy-MM-dd HH:mm:ss");
        }

        private static string FormatOptional(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? "(none)" : value;
        }

        private static string FormatUnityObjectName(UnityEngine.Object obj, string assetPath)
        {
            if (obj != null && !string.IsNullOrWhiteSpace(obj.name))
                return obj.name;
            if (!string.IsNullOrWhiteSpace(assetPath))
                return Path.GetFileNameWithoutExtension(assetPath);
            return "(missing asset)";
        }

        private static string FormatSceneObjectName(GameObject obj, string sceneObjectId)
        {
            if (obj != null && !string.IsNullOrWhiteSpace(obj.name))
                return obj.name;
            return string.IsNullOrWhiteSpace(sceneObjectId) ? "(missing object)" : "(unresolved object)";
        }

        private static string FormatSceneObjectPath(GameObject obj)
        {
            if (obj == null)
                return "(missing scene object)";

            var names = new List<string>();
            var current = obj.transform;
            while (current != null)
            {
                names.Add(current.name);
                current = current.parent;
            }

            names.Reverse();
            var objectPath = string.Join("/", names);
            var sceneName = obj.scene.IsValid() ? obj.scene.name : "(scene)";
            return string.IsNullOrWhiteSpace(objectPath) ? sceneName : sceneName + "/" + objectPath;
        }

        private static string FormatResourceType(string type)
        {
            if (string.IsNullOrWhiteSpace(type))
                return "Resource";
            var normalized = type.Trim();
            return char.ToUpperInvariant(normalized[0]) + normalized.Substring(1);
        }

        private static bool MatchesSearch(string query, params string[] values)
        {
            if (string.IsNullOrWhiteSpace(query))
                return true;
            if (values == null || values.Length == 0)
                return false;
            return values.Any(v => !string.IsNullOrEmpty(v) && v.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0);
        }

        private static void DrawPreviewThumbnail(Rect rect, UnityEngine.Object target)
        {
            var texture = target != null ? AssetPreview.GetAssetPreview(target) ?? AssetPreview.GetMiniThumbnail(target) : null;
            if (texture != null)
            {
                GUI.DrawTexture(rect, texture, ScaleMode.ScaleToFit);
                return;
            }

            EditorGUI.DrawRect(rect, new Color(0.18f, 0.18f, 0.18f, 1.0f));
        }

        private static bool PathsEqual(string left, string right)
        {
            return string.Equals(
                (left ?? string.Empty).Replace('\\', '/'),
                (right ?? string.Empty).Replace('\\', '/'),
                StringComparison.OrdinalIgnoreCase);
        }

        private static UnityEngine.Object ResolveAssetObject(AptRecord record)
        {
            var path = record?.target?.unityAssetPath;
            if (string.IsNullOrWhiteSpace(path))
                return null;
            return AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
        }

        private static UnityEngine.Object ResolveAssetObjectByGuid(string assetGuid)
        {
            if (string.IsNullOrWhiteSpace(assetGuid))
                return null;
            var path = AssetDatabase.GUIDToAssetPath(assetGuid);
            if (string.IsNullOrWhiteSpace(path))
                return null;
            return AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
        }

        private static UnityEngine.Object[] ResolveAssetObjectsByGuids(string[] assetGuids)
        {
            if (assetGuids == null || assetGuids.Length == 0)
                return Array.Empty<UnityEngine.Object>();
            return assetGuids
                .Where(g => !string.IsNullOrWhiteSpace(g))
                .Select(ResolveAssetObjectByGuid)
                .Where(o => o != null)
                .ToArray();
        }

        private UnityEngine.Object ResolveAssetObjectById(string assetId)
        {
            if (string.IsNullOrWhiteSpace(assetId))
                return null;

            var record = (_assetDb?.records ?? Array.Empty<AptRecord>())
                .FirstOrDefault(r => r != null && string.Equals(r.assetId, assetId, StringComparison.Ordinal));
            return ResolveAssetObject(record);
        }

        private string FindAssetIdForObject(UnityEngine.Object assetObject)
        {
            if (assetObject == null)
                return null;
            var path = AssetDatabase.GetAssetPath(assetObject);
            if (string.IsNullOrWhiteSpace(path))
                return null;

            return (_assetDb?.records ?? Array.Empty<AptRecord>())
                .FirstOrDefault(r => r != null && PathsEqual(r.target?.unityAssetPath, path))
                ?.assetId;
        }

        private static GameObject ResolveRiggedPrefab(RiggedObjectRecord record)
        {
            var path = record?.prefabPath;
            if (string.IsNullOrWhiteSpace(path))
                return null;
            return AssetDatabase.LoadAssetAtPath<GameObject>(path);
        }

        private static GameObject ResolveSceneObject(ObjectBindingEntry record)
        {
            return ResolveSceneObject(record?.sceneObjectId);
        }

        private static GameObject ResolveSceneObject(string sceneObjectId)
        {
            var id = sceneObjectId;
            if (string.IsNullOrWhiteSpace(id))
                return null;

            if (!GlobalObjectId.TryParse(id, out var gid))
                return null;

            return GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid) as GameObject;
        }

        public void Refresh()
        {
            Refresh(BuildRegistryFileSignature(GetRegistryFilePaths()));
        }

        private void Refresh(string observedFileSignature)
        {
            _riggedMeshRefsCache.Clear();
            try
            {
                _assetDb = new AptRepository().Load();
                _objectDb = new ObjectBindingRegistry().Load();
                _riggedDb = new RiggedObjectRegistry().Load();
                SetRefreshStatus("OK");
            }
            catch (Exception ex)
            {
                _assetDb = new AptDatabase();
                _objectDb = new ObjectBindingDatabase();
                _riggedDb = new RiggedObjectDatabase();
                SetRefreshStatus("refresh_failed: " + ex.Message);
            }

            _loaded = true;
            _lastRefreshAt = DateTime.Now;
            _registryFileSignature = observedFileSignature ?? string.Empty;
            _hasRegistryFileSignature = true;
        }
    }

}
#endif
