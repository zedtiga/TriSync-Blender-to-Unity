using System;
using System.Threading;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    internal sealed class SceneViewStateService
    {
#if UNITY_EDITOR
        private static readonly object RepaintPumpLock = new object();
        private static SynchronizationContext _mainThreadContext;
        private static int _mainThreadId;
        private static bool _repaintPumpInstalled;
        private static double _repaintUntil;
        private static ViewStateSmoothingState _smoothing;
#endif

        public SceneViewStateService()
        {
#if UNITY_EDITOR
            TryCaptureMainThreadContext();
#endif
        }

        public void ApplyAsync(SceneSyncViewStateMessage msg)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("view_state_message_missing");
                return;
            }

#if UNITY_EDITOR
            TryCaptureMainThreadContext();
            if (_mainThreadId != 0 && Thread.CurrentThread.ManagedThreadId == _mainThreadId)
            {
                ApplyViewState(msg);
            }
            else if (_mainThreadContext != null)
            {
                _mainThreadContext.Post(_ => ApplyViewState(msg), null);
            }
            else
            {
                EditorApplication.delayCall += () => ApplyViewState(msg);
            }
#else
            SceneSyncStateStore.MarkSkipped("view_state_requires_editor");
#endif
        }

#if UNITY_EDITOR
        private static void TryCaptureMainThreadContext()
        {
            if (_mainThreadContext != null)
                return;

            var current = SynchronizationContext.Current;
            if (current == null)
                return;

            _mainThreadContext = current;
            _mainThreadId = Thread.CurrentThread.ManagedThreadId;
        }

        private static void ApplyViewState(SceneSyncViewStateMessage msg)
        {
            var sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null && SceneView.sceneViews != null && SceneView.sceneViews.Count > 0)
                sceneView = SceneView.sceneViews[0] as SceneView;
            if (sceneView == null)
            {
                SceneSyncStateStore.MarkSkipped("scene_view_missing");
                return;
            }

            var pivot = SceneSyncTransformMapper.MapPosition(msg.pivot);
            Quaternion rotation;
            if (msg.forward != null && msg.forward.Length == 3 && msg.up != null && msg.up.Length == 3)
            {
                var forward = SceneSyncTransformMapper.MapDirection(new Vector3(msg.forward[0], msg.forward[1], msg.forward[2])).normalized;
                var up = SceneSyncTransformMapper.MapDirection(new Vector3(msg.up[0], msg.up[1], msg.up[2])).normalized;
                rotation = forward.sqrMagnitude > 1e-8f && up.sqrMagnitude > 1e-8f
                    ? Quaternion.LookRotation(forward, up)
                    : SceneSyncTransformMapper.MapRotation(msg.rotation);
            }
            else
            {
                rotation = SceneSyncTransformMapper.MapRotation(msg.rotation);
            }
            var size = CalculateSceneViewSize(msg);

            QueueSmoothing(sceneView, pivot, rotation, size, msg.isOrthographic);

            SceneSyncStateStore.MarkApplied("view_state");
        }

        private sealed class ViewStateSmoothingState
        {
            public SceneView sceneView;
            public Vector3 currentPivot;
            public Quaternion currentRotation;
            public float currentSize;
            public Vector3 targetPivot;
            public Quaternion targetRotation;
            public float targetSize;
            public bool orthographic;
            public double lastUpdateTime;
        }

        private static void QueueSmoothing(SceneView sceneView, Vector3 pivot, Quaternion rotation, float size, bool orthographic)
        {
            if (sceneView == null)
                return;
            if (_smoothing == null || _smoothing.sceneView != sceneView)
            {
                _smoothing = new ViewStateSmoothingState
                {
                    sceneView = sceneView,
                    currentPivot = sceneView.pivot,
                    currentRotation = sceneView.rotation,
                    currentSize = Mathf.Max(0.01f, sceneView.size),
                    targetPivot = pivot,
                    targetRotation = rotation,
                    targetSize = Mathf.Max(0.01f, size),
                    orthographic = orthographic,
                    lastUpdateTime = EditorApplication.timeSinceStartup,
                };
            }
            else
            {
                _smoothing.targetPivot = pivot;
                _smoothing.targetRotation = rotation;
                _smoothing.targetSize = Mathf.Max(0.01f, size);
                _smoothing.orthographic = orthographic;
            }
            RequestRepaintPump(1.0);
        }

        private static float EstimateSceneViewSizeFromBlenderDistance(float distance, float lensMm)
        {
            var safeDistance = Mathf.Max(0.01f, distance);
            // Blender viewport lens is expressed like a camera lens in mm. Use the common 32mm sensor width
            // to estimate vertical-ish framing for Unity SceneView.size, which is a framing radius rather than
            // the raw camera-to-pivot distance.
            var safeLens = Mathf.Max(1.0f, lensMm > 0.0001f ? lensMm : 50.0f);
            var fovRad = 2.0f * Mathf.Atan(32.0f / (2.0f * safeLens));
            const float sceneViewPerspectiveFramingFactor = 2.0f;
            return Mathf.Max(0.01f, safeDistance * Mathf.Tan(fovRad * 0.5f) * sceneViewPerspectiveFramingFactor);
        }

        private static float CalculateSceneViewSize(SceneSyncViewStateMessage msg)
        {
            if (msg == null)
                return 0.01f;
            var baseSize = msg.isOrthographic && msg.orthographicScale > 0.0001f
                ? msg.orthographicScale
                : EstimateSceneViewSizeFromBlenderDistance(msg.distance, msg.lens);
            return Mathf.Max(0.01f, baseSize * NormalizeViewScale(msg.viewScale));
        }

        private static float NormalizeViewScale(float value)
        {
            if (float.IsNaN(value) || float.IsInfinity(value) || value <= 0.0f)
                return 1.0f;
            return Mathf.Clamp(value, 0.1f, 5.0f);
        }

        private static void RequestRepaintPump(double seconds)
        {
            lock (RepaintPumpLock)
            {
                _repaintUntil = Math.Max(_repaintUntil, EditorApplication.timeSinceStartup + Math.Max(0.05, seconds));
                if (_repaintPumpInstalled)
                    return;
                EditorApplication.update += PumpRepaint;
                _repaintPumpInstalled = true;
            }
        }

        private static void PumpRepaint()
        {
            var now = EditorApplication.timeSinceStartup;
            var hasSmoothing = false;
            if (_smoothing != null && _smoothing.sceneView != null)
            {
                var state = _smoothing;
                var dt = Mathf.Clamp((float)(now - state.lastUpdateTime), 0.0f, 0.1f);
                state.lastUpdateTime = now;
                const float smoothingSpeed = 12.0f;
                var t = 1.0f - Mathf.Exp(-smoothingSpeed * dt);
                state.currentPivot = Vector3.Lerp(state.currentPivot, state.targetPivot, t);
                state.currentRotation = Quaternion.Slerp(state.currentRotation, state.targetRotation, t);
                state.currentSize = Mathf.Lerp(state.currentSize, state.targetSize, t);
                state.sceneView.orthographic = state.orthographic;
                state.sceneView.LookAt(state.currentPivot, state.currentRotation, state.currentSize, state.orthographic, false);
                hasSmoothing = Vector3.Distance(state.currentPivot, state.targetPivot) > 0.0005f
                    || Quaternion.Angle(state.currentRotation, state.targetRotation) > 0.05f
                    || Mathf.Abs(state.currentSize - state.targetSize) > 0.0005f;
                if (!hasSmoothing)
                {
                    state.currentPivot = state.targetPivot;
                    state.currentRotation = state.targetRotation;
                    state.currentSize = state.targetSize;
                    state.sceneView.LookAt(state.currentPivot, state.currentRotation, state.currentSize, state.orthographic, false);
                    _smoothing = null;
                }
            }

            SceneView.RepaintAll();
            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            EditorApplication.QueuePlayerLoopUpdate();
            lock (RepaintPumpLock)
            {
                if (hasSmoothing || EditorApplication.timeSinceStartup < _repaintUntil)
                    return;
                EditorApplication.update -= PumpRepaint;
                _repaintPumpInstalled = false;
            }
        }
#endif
    }
}
