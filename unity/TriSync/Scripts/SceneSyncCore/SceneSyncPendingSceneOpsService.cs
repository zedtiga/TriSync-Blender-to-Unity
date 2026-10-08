using System;
using System.Collections.Generic;
using System.Threading;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    internal sealed class SceneSyncPendingSceneOpsService
    {
        public delegate bool TryApplyHierarchyDelegate(SceneSyncHierarchyMessage msg, out string reason);
        public delegate bool TryApplyVisibilityDelegate(SceneSyncVisibilityMessage msg, out string reason);
        public delegate bool TryApplyObjectNameDelegate(SceneSyncObjectNameMessage msg, out string reason);

        private readonly Queue<SceneSyncHierarchyMessage> _pendingHierarchy = new Queue<SceneSyncHierarchyMessage>();
        private readonly Queue<SceneSyncVisibilityMessage> _pendingVisibility = new Queue<SceneSyncVisibilityMessage>();
        private readonly Queue<SceneSyncObjectNameMessage> _pendingObjectName = new Queue<SceneSyncObjectNameMessage>();
        private readonly HashSet<string> _pendingHierarchyKeys = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> _pendingVisibilityKeys = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> _pendingObjectNameKeys = new HashSet<string>(StringComparer.Ordinal);
        private readonly Dictionary<string, SceneSyncHierarchyMessage> _latestHierarchyByKey = new Dictionary<string, SceneSyncHierarchyMessage>(StringComparer.Ordinal);
        private readonly Dictionary<string, SceneSyncVisibilityMessage> _latestVisibilityByKey = new Dictionary<string, SceneSyncVisibilityMessage>(StringComparer.Ordinal);
        private readonly Dictionary<string, SceneSyncObjectNameMessage> _latestObjectNameByKey = new Dictionary<string, SceneSyncObjectNameMessage>(StringComparer.Ordinal);
        private readonly object _pendingLock = new object();

#if UNITY_EDITOR
        private bool _hierarchyHookInstalled;
        private bool _visibilityHookInstalled;
        private bool _objectNameHookInstalled;
        private SynchronizationContext _mainThreadContext;

        public SceneSyncPendingSceneOpsService()
        {
            TryCaptureMainThreadContext();
        }
#endif

        public void EnqueueHierarchy(SceneSyncHierarchyMessage msg, TryApplyHierarchyDelegate apply)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("hierarchy_message_missing");
                return;
            }

#if UNITY_EDITOR
            EnsureHierarchyPump(apply);
            EnqueueCoalescedLocked(_pendingHierarchy, _pendingHierarchyKeys, _latestHierarchyByKey, NormalizeKey(msg.childPairId), msg);
            RequestMainThreadPump(apply, null, null);
#else
            if (apply(msg, out var pendingReason))
            {
                SceneSyncStateStore.MarkApplied(msg.childPairId);
                return;
            }

            if (!string.IsNullOrWhiteSpace(pendingReason))
                SceneSyncStateStore.MarkError(pendingReason, msg.childPairId);
#endif
        }

        public void EnqueueVisibility(SceneSyncVisibilityMessage msg, TryApplyVisibilityDelegate apply)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("visibility_message_missing");
                return;
            }

#if UNITY_EDITOR
            EnsureVisibilityPump(apply);
            EnqueueCoalescedLocked(_pendingVisibility, _pendingVisibilityKeys, _latestVisibilityByKey, NormalizeKey(msg.pairId), msg);
            RequestMainThreadPump(null, apply, null);
#else
            if (apply(msg, out var reason))
            {
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            if (!string.IsNullOrWhiteSpace(reason))
                SceneSyncStateStore.MarkError(reason, msg.pairId);
#endif
        }

        public void EnqueueObjectName(SceneSyncObjectNameMessage msg, TryApplyObjectNameDelegate apply)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("object_name_message_missing");
                return;
            }

#if UNITY_EDITOR
            EnsureObjectNamePump(apply);
            EnqueueCoalescedLocked(_pendingObjectName, _pendingObjectNameKeys, _latestObjectNameByKey, NormalizeKey(msg.pairId), msg);
            RequestMainThreadPump(null, null, apply);
#else
            if (apply(msg, out var reason))
            {
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            if (!string.IsNullOrWhiteSpace(reason))
                SceneSyncStateStore.MarkError(reason, msg.pairId);
#endif
        }

        public void ClearForRemovedPairs(IEnumerable<string> pairIds)
        {
#if UNITY_EDITOR
            var removed = new HashSet<string>(StringComparer.Ordinal);
            foreach (var rawPairId in pairIds ?? Array.Empty<string>())
            {
                var pairId = NormalizeKey(rawPairId);
                if (!string.IsNullOrWhiteSpace(pairId))
                    removed.Add(pairId);
            }
            if (removed.Count == 0)
                return;

            lock (_pendingLock)
            {
                foreach (var pairId in removed)
                {
                    ClearPendingLocked(_pendingVisibilityKeys, _latestVisibilityByKey, pairId);
                    ClearPendingLocked(_pendingObjectNameKeys, _latestObjectNameByKey, pairId);
                }

                var hierarchyKeys = new List<string>();
                foreach (var entry in _latestHierarchyByKey)
                {
                    var msg = entry.Value;
                    if (msg == null)
                        continue;
                    if (removed.Contains(NormalizeKey(msg.childPairId)) || removed.Contains(NormalizeKey(msg.parentPairId)))
                        hierarchyKeys.Add(entry.Key);
                }

                foreach (var key in hierarchyKeys)
                    ClearPendingLocked(_pendingHierarchyKeys, _latestHierarchyByKey, key);
            }
#endif
        }

#if UNITY_EDITOR
        private void TryCaptureMainThreadContext()
        {
            if (_mainThreadContext != null)
                return;

            var current = SynchronizationContext.Current;
            if (current == null)
                return;

            _mainThreadContext = current;
        }

        private void RequestMainThreadPump(
            TryApplyHierarchyDelegate applyHierarchy,
            TryApplyVisibilityDelegate applyVisibility,
            TryApplyObjectNameDelegate applyObjectName)
        {
            TryCaptureMainThreadContext();
            if (_mainThreadContext == null)
                return;

            _mainThreadContext.Post(_ =>
            {
                if (applyObjectName != null)
                    PumpPendingObjectName(applyObjectName);
                if (applyHierarchy != null)
                    PumpPendingHierarchy(applyHierarchy);
                if (applyVisibility != null)
                    PumpPendingVisibility(applyVisibility);
                RequestEditorRepaint();
            }, null);
        }

        private void EnsureHierarchyPump(TryApplyHierarchyDelegate apply)
        {
            lock (_pendingLock)
            {
                if (_hierarchyHookInstalled) return;
                EditorApplication.update += () => PumpPendingHierarchy(apply);
                _hierarchyHookInstalled = true;
            }
        }

        private void EnsureVisibilityPump(TryApplyVisibilityDelegate apply)
        {
            lock (_pendingLock)
            {
                if (_visibilityHookInstalled) return;
                EditorApplication.update += () => PumpPendingVisibility(apply);
                _visibilityHookInstalled = true;
            }
        }

        private void EnsureObjectNamePump(TryApplyObjectNameDelegate apply)
        {
            lock (_pendingLock)
            {
                if (_objectNameHookInstalled) return;
                EditorApplication.update += () => PumpPendingObjectName(apply);
                _objectNameHookInstalled = true;
            }
        }

        private static void RequestEditorRepaint()
        {
            EditorApplication.QueuePlayerLoopUpdate();
            SceneView.RepaintAll();
            EditorApplication.RepaintHierarchyWindow();
            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
        }

        private void PumpPendingHierarchy(TryApplyHierarchyDelegate apply)
        {
            var attempts = CountLocked(_pendingHierarchy);
            if (attempts == 0)
                return;

            for (var i = 0; i < attempts; i++)
            {
                var msg = DequeueLatestLocked(_pendingHierarchy, _pendingHierarchyKeys, _latestHierarchyByKey, m => m?.childPairId, out var key);
                if (msg == null)
                    continue;

                if (apply(msg, out var reason))
                {
                    ClearPendingLocked(_pendingHierarchyKeys, _latestHierarchyByKey, key);
                    SceneSyncStateStore.MarkApplied(msg.childPairId);
                    RequestEditorRepaint();
                    continue;
                }

                if (reason == "child_not_mapped" || reason == "parent_not_mapped")
                {
                    RequeueLocked(_pendingHierarchy, msg);
                    continue;
                }

                ClearPendingLocked(_pendingHierarchyKeys, _latestHierarchyByKey, key);
                if (!string.IsNullOrWhiteSpace(reason))
                    SceneSyncStateStore.MarkError(reason, msg.childPairId);
            }
        }

        private void PumpPendingVisibility(TryApplyVisibilityDelegate apply)
        {
            var attempts = CountLocked(_pendingVisibility);
            if (attempts == 0)
                return;

            for (var i = 0; i < attempts; i++)
            {
                var msg = DequeueLatestLocked(_pendingVisibility, _pendingVisibilityKeys, _latestVisibilityByKey, m => m?.pairId, out var key);
                if (msg == null)
                    continue;

                if (apply(msg, out var reason))
                {
                    ClearPendingLocked(_pendingVisibilityKeys, _latestVisibilityByKey, key);
                    SceneSyncStateStore.MarkApplied(msg.pairId);
                    RequestEditorRepaint();
                    continue;
                }

                if (reason == "not_mapped")
                {
                    RequeueLocked(_pendingVisibility, msg);
                    continue;
                }

                ClearPendingLocked(_pendingVisibilityKeys, _latestVisibilityByKey, key);
                if (!string.IsNullOrWhiteSpace(reason))
                    SceneSyncStateStore.MarkError(reason, msg.pairId);
            }
        }

        private void PumpPendingObjectName(TryApplyObjectNameDelegate apply)
        {
            var attempts = CountLocked(_pendingObjectName);
            if (attempts == 0)
                return;

            for (var i = 0; i < attempts; i++)
            {
                var msg = DequeueLatestLocked(_pendingObjectName, _pendingObjectNameKeys, _latestObjectNameByKey, m => m?.pairId, out var key);
                if (msg == null)
                    continue;

                if (apply(msg, out var reason))
                {
                    ClearPendingLocked(_pendingObjectNameKeys, _latestObjectNameByKey, key);
                    SceneSyncStateStore.MarkApplied(msg.pairId);
                    RequestEditorRepaint();
                    continue;
                }

                if (reason == "not_mapped")
                {
                    RequeueLocked(_pendingObjectName, msg);
                    continue;
                }

                ClearPendingLocked(_pendingObjectNameKeys, _latestObjectNameByKey, key);
                if (!string.IsNullOrWhiteSpace(reason))
                    SceneSyncStateStore.MarkError(reason, msg.pairId);
            }
        }

        private void EnqueueCoalescedLocked<TMessage>(
            Queue<TMessage> queue,
            HashSet<string> pendingKeys,
            Dictionary<string, TMessage> latestByKey,
            string key,
            TMessage msg) where TMessage : class
        {
            if (msg == null)
                return;
            lock (_pendingLock)
            {
                if (!string.IsNullOrWhiteSpace(key))
                {
                    latestByKey[key] = msg;
                    if (!pendingKeys.Add(key))
                        return;
                }
                queue.Enqueue(msg);
            }
        }

        private void RequeueLocked<TMessage>(Queue<TMessage> queue, TMessage msg) where TMessage : class
        {
            if (msg == null)
                return;
            lock (_pendingLock)
            {
                queue.Enqueue(msg);
            }
        }

        private TMessage DequeueLatestLocked<TMessage>(
            Queue<TMessage> queue,
            HashSet<string> pendingKeys,
            Dictionary<string, TMessage> latestByKey,
            Func<TMessage, string> keySelector,
            out string key) where TMessage : class
        {
            lock (_pendingLock)
            {
                key = null;
                if (queue.Count == 0)
                    return null;

                var msg = queue.Dequeue();
                key = NormalizeKey(keySelector != null ? keySelector(msg) : null);
                if (!string.IsNullOrWhiteSpace(key) && !pendingKeys.Contains(key))
                    return null;
                if (!string.IsNullOrWhiteSpace(key) && pendingKeys.Contains(key) && latestByKey.TryGetValue(key, out var latest) && latest != null)
                    return latest;
                return msg;
            }
        }

        private void ClearPendingLocked<TMessage>(HashSet<string> pendingKeys, Dictionary<string, TMessage> latestByKey, string key) where TMessage : class
        {
            if (string.IsNullOrWhiteSpace(key))
                return;
            lock (_pendingLock)
            {
                pendingKeys.Remove(key);
                latestByKey.Remove(key);
            }
        }

        private int CountLocked<TMessage>(Queue<TMessage> queue)
        {
            lock (_pendingLock)
            {
                return queue.Count;
            }
        }

        private static string NormalizeKey(string value)
        {
            var normalized = (value ?? string.Empty).Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }
#endif
    }
}
