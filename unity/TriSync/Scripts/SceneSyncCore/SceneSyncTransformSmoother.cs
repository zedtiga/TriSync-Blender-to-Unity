using System;
using System.Collections.Generic;
using System.Runtime.CompilerServices;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public static class SceneSyncTransformSmoother
    {
        private sealed class TargetState
        {
            internal readonly Transform Target;

            private Vector3 _startPosition;
            private Quaternion _startRotation = Quaternion.identity;
            private Vector3 _startScale = Vector3.one;
            private Vector3 _targetPosition;
            private Quaternion _targetRotation = Quaternion.identity;
            private Vector3 _targetScale = Vector3.one;
            private float _smoothingTime;
            private float _elapsed;
            private float _lastProgress;
            private AnimationCurve _followCurve;

            internal TargetState(Transform target)
            {
                Target = target;
            }

            internal bool HasTarget { get; private set; }

            internal void SetTarget(
                Vector3 position,
                Quaternion rotation,
                Vector3 scale,
                float smoothingTime,
                AnimationCurve followCurve)
            {
                _startPosition = Target.position;
                _startRotation = Target.rotation;
                _startScale = Target.localScale;
                _targetPosition = position;
                _targetRotation = rotation;
                _targetScale = scale;
                _smoothingTime = Mathf.Max(0.0001f, smoothingTime);
                _followCurve = followCurve;
                _elapsed = 0f;
                _lastProgress = 0f;
                HasTarget = true;
            }

            internal bool TickOnce(float dt)
            {
                if (!HasTarget || Target == null)
                    return false;

                if (dt <= 0f)
                    dt = 1f / 60f;

                _elapsed = Mathf.Min(_smoothingTime, _elapsed + dt);
                var normalizedTime = Mathf.Clamp01(_elapsed / _smoothingTime);
                var curveProgress = _followCurve != null
                    ? _followCurve.Evaluate(normalizedTime)
                    : normalizedTime;
                var progress = Mathf.Max(_lastProgress, Mathf.Clamp01(curveProgress));
                _lastProgress = progress;

                Target.position = Vector3.Lerp(_startPosition, _targetPosition, progress);
                Target.rotation = Quaternion.Slerp(_startRotation, _targetRotation, progress);
                Target.localScale = Vector3.Lerp(_startScale, _targetScale, progress);

                if (normalizedTime < 1f)
                    return true;

                SnapToTarget();
                return false;
            }

            internal void SnapToTarget()
            {
                if (HasTarget && Target != null)
                {
                    Target.position = _targetPosition;
                    Target.rotation = _targetRotation;
                    Target.localScale = _targetScale;
                }
                HasTarget = false;
            }
        }

        private sealed class TransformReferenceComparer : IEqualityComparer<Transform>
        {
            internal static readonly TransformReferenceComparer Instance =
                new TransformReferenceComparer();

            public bool Equals(Transform x, Transform y)
            {
                return ReferenceEquals(x, y);
            }

            public int GetHashCode(Transform obj)
            {
                return ReferenceEquals(obj, null) ? 0 : RuntimeHelpers.GetHashCode(obj);
            }
        }

        private const float MaxEditorDeltaTime = 0.1f;
        private static readonly object TargetsLock = new object();
        private static readonly Dictionary<Transform, TargetState> Targets =
            new Dictionary<Transform, TargetState>(TransformReferenceComparer.Instance);
        private static bool _pumpInstalled;
        private static double _lastTickAt = -1d;

        public static void SmoothTo(
            Transform target,
            Vector3 position,
            Quaternion rotation,
            Vector3 scale,
            float smoothingTime,
            AnimationCurve followCurve)
        {
            if (target == null || !EditModeGuard.IsAvailable)
                return;

            TargetState state;
            lock (TargetsLock)
            {
                if (!Targets.TryGetValue(target, out state) ||
                    !ReferenceEquals(state.Target, target))
                {
                    state = new TargetState(target);
                    Targets[target] = state;
                }
            }

            state.SetTarget(position, rotation, scale, smoothingTime, followCurve);
            EnsurePump();
        }

        public static void RemoveFrom(Transform target, bool completeTarget)
        {
            if (target == null)
                return;

            TargetState state = null;
            lock (TargetsLock)
            {
                if (Targets.TryGetValue(target, out state))
                    Targets.Remove(target);
            }

            if (completeTarget)
                state?.SnapToTarget();
            UninstallPumpIfIdle();
        }

        public static void CompleteAll()
        {
            List<TargetState> states;
            lock (TargetsLock)
            {
                states = new List<TargetState>(Targets.Values);
                Targets.Clear();
            }

            foreach (var state in states)
                state.SnapToTarget();
            UninstallPumpIfIdle();
        }

        private static void EnsurePump()
        {
            if (_pumpInstalled)
                return;
            _lastTickAt = EditorApplication.timeSinceStartup;
            EditorApplication.update += TickAll;
            AssemblyReloadEvents.beforeAssemblyReload += CompleteAll;
            EditorApplication.quitting += CompleteAll;
            _pumpInstalled = true;
        }

        private static void UninstallPumpIfIdle()
        {
            lock (TargetsLock)
            {
                if (Targets.Count > 0 || !_pumpInstalled)
                    return;
            }

            EditorApplication.update -= TickAll;
            AssemblyReloadEvents.beforeAssemblyReload -= CompleteAll;
            EditorApplication.quitting -= CompleteAll;
            _pumpInstalled = false;
            _lastTickAt = -1d;
        }

        private static void TickAll()
        {
            if (!EditModeGuard.IsAvailable)
            {
                CompleteAll();
                return;
            }

            var active = SnapshotTargets();
            if (active == null)
                return;

            var now = EditorApplication.timeSinceStartup;
            var elapsed = _lastTickAt >= 0d ? now - _lastTickAt : (1d / 60d);
            _lastTickAt = now;
            var dt = elapsed > 0d
                ? Mathf.Min((float)elapsed, MaxEditorDeltaTime)
                : (1f / 60f);
            var needsRepaint = false;
            foreach (var state in active)
            {
                if (state.Target == null)
                {
                    RemoveState(state);
                    continue;
                }

                var hadTarget = state.HasTarget;
                if (!state.TickOnce(dt))
                    RemoveState(state);
                needsRepaint |= hadTarget;
            }

            if (needsRepaint)
            {
                EditorApplication.QueuePlayerLoopUpdate();
                SceneView.RepaintAll();
            }
        }

        private static List<TargetState> SnapshotTargets()
        {
            lock (TargetsLock)
            {
                return Targets.Count > 0
                    ? new List<TargetState>(Targets.Values)
                    : null;
            }
        }

        private static void RemoveState(TargetState state)
        {
            lock (TargetsLock)
            {
                if (Targets.TryGetValue(state.Target, out var current) &&
                    ReferenceEquals(current, state))
                {
                    Targets.Remove(state.Target);
                }
            }
            UninstallPumpIfIdle();
        }
    }
}
