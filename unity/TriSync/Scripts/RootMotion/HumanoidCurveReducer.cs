#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.RootMotion
{
    internal enum HumanoidCurveReductionPreset
    {
        Off = 0,
        Light = 1,
        Medium = 2,
        Aggressive = 3,
    }

    internal enum HumanoidStaticCurveMode
    {
        Off = 0,
        CollapseConstant = 1,
    }

    internal readonly struct HumanoidCurveReductionResult
    {
        public readonly int KeysBefore;
        public readonly int KeysAfter;
        public readonly int CollapsedStaticTrackCount;

        public HumanoidCurveReductionResult(int keysBefore, int keysAfter)
            : this(keysBefore, keysAfter, 0)
        {
        }

        public HumanoidCurveReductionResult(
            int keysBefore,
            int keysAfter,
            int collapsedStaticTrackCount)
        {
            KeysBefore = keysBefore;
            KeysAfter = keysAfter;
            CollapsedStaticTrackCount = collapsedStaticTrackCount;
        }
    }

    internal static class HumanoidCurveReducer
    {
        private const float StaticValueEpsilon = 1e-6f;
        private const float StaticRotationEpsilonDegrees = 1e-4f;

        private static readonly string[] GoalPrefixes =
        {
            "LeftFoot",
            "RightFoot",
            "LeftHand",
            "RightHand",
        };

        internal static HumanoidCurveReductionResult Reduce(
            AnimationClip clip,
            HumanoidCurveReductionPreset preset)
        {
            return Reduce(clip, preset, HumanoidStaticCurveMode.Off);
        }

        internal static HumanoidCurveReductionResult Reduce(
            AnimationClip clip,
            HumanoidCurveReductionPreset preset,
            HumanoidStaticCurveMode staticCurveMode)
        {
            var entries = ReadAnimatorCurves(clip);
            var keysBefore = CountKeys(entries);
            if (clip == null || keysBefore == 0)
                return new HumanoidCurveReductionResult(keysBefore, keysBefore);

            if (preset != HumanoidCurveReductionPreset.Off)
            {
                var settings = SettingsFor(preset);
                var processed = new HashSet<string>(StringComparer.Ordinal);
                ReduceVectorGroup(clip, entries, "RootT", settings.PositionError, processed);
                ReduceQuaternionGroup(clip, entries, "RootQ", settings.RotationErrorDegrees, processed);
                foreach (var prefix in GoalPrefixes)
                {
                    ReduceVectorGroup(clip, entries, prefix + "T", settings.PositionError, processed);
                    ReduceQuaternionGroup(clip, entries, prefix + "Q", settings.RotationErrorDegrees, processed);
                }

                foreach (var pair in entries)
                {
                    if (processed.Contains(pair.Key))
                        continue;
                    ReduceScalarCurve(clip, pair.Value, settings.ScalarError);
                }
            }

            var collapsed = staticCurveMode == HumanoidStaticCurveMode.CollapseConstant
                ? CollapseStaticCurvesPreservingTimeRange(clip, CollapseAnimatorStaticCurves)
                : 0;
            return new HumanoidCurveReductionResult(
                keysBefore,
                CountKeys(ReadAnimatorCurves(clip)),
                collapsed);
        }

        internal static HumanoidCurveReductionResult ReduceAllFloatCurves(
            AnimationClip clip,
            HumanoidCurveReductionPreset preset)
        {
            return ReduceAllFloatCurves(clip, preset, HumanoidStaticCurveMode.Off);
        }

        internal static HumanoidCurveReductionResult ReduceAllFloatCurves(
            AnimationClip clip,
            HumanoidCurveReductionPreset preset,
            HumanoidStaticCurveMode staticCurveMode)
        {
            var entries = ReadAllFloatCurves(clip);
            var keysBefore = CountKeys(entries);
            if (clip == null || keysBefore == 0)
                return new HumanoidCurveReductionResult(keysBefore, keysBefore);

            if (preset != HumanoidCurveReductionPreset.Off)
            {
                var settings = SettingsFor(preset);
                foreach (var scope in entries.GroupBy(entry => new CurveScope(
                             entry.Binding.type,
                             entry.Binding.path)))
                {
                    var scopedEntries = scope.ToDictionary(
                        entry => entry.Binding.propertyName,
                        entry => entry,
                        StringComparer.Ordinal);
                    var processed = new HashSet<string>(StringComparer.Ordinal);

                    if (scope.Key.Type == typeof(Animator) && string.IsNullOrEmpty(scope.Key.Path))
                        ReduceAnimatorGroups(clip, scopedEntries, settings, processed);
                    else if (scope.Key.Type == typeof(Transform))
                        ReduceTransformGroups(clip, scopedEntries, settings, processed);

                    foreach (var pair in scopedEntries)
                    {
                        if (processed.Contains(pair.Key) || pair.Value.Binding.isDiscreteCurve)
                            continue;
                        ReduceScalarCurve(clip, pair.Value, settings.ScalarError);
                    }
                }
            }

            var collapsed = staticCurveMode == HumanoidStaticCurveMode.CollapseConstant
                ? CollapseStaticCurvesPreservingTimeRange(clip, CollapseStaticTransformCurves)
                : 0;
            return new HumanoidCurveReductionResult(
                keysBefore,
                CountKeys(ReadAllFloatCurves(clip)),
                collapsed);
        }

        private static Dictionary<string, CurveEntry> ReadAnimatorCurves(AnimationClip clip)
        {
            var result = new Dictionary<string, CurveEntry>(StringComparer.Ordinal);
            if (clip == null)
                return result;

            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                if (binding.type != typeof(Animator) || !string.IsNullOrEmpty(binding.path))
                    continue;
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                if (curve == null)
                    continue;
                result[binding.propertyName] = new CurveEntry(binding, curve);
            }
            return result;
        }

        private static List<CurveEntry> ReadAllFloatCurves(AnimationClip clip)
        {
            var result = new List<CurveEntry>();
            if (clip == null)
                return result;

            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                if (curve != null)
                    result.Add(new CurveEntry(binding, curve));
            }
            return result;
        }

        private static int CountKeys(IReadOnlyDictionary<string, CurveEntry> entries)
        {
            return entries.Values.Sum(entry => entry.Curve != null ? entry.Curve.length : 0);
        }

        private static int CountKeys(IEnumerable<CurveEntry> entries)
        {
            return entries.Sum(entry => entry.Curve != null ? entry.Curve.length : 0);
        }

        private static void ReduceAnimatorGroups(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            ReductionSettings settings,
            ISet<string> processed)
        {
            ReduceReservedVectorGroup(clip, entries, "RootT", settings.PositionError, processed);
            ReduceReservedQuaternionGroup(clip, entries, "RootQ", settings.RotationErrorDegrees, processed);
            foreach (var prefix in GoalPrefixes)
            {
                ReduceReservedVectorGroup(clip, entries, prefix + "T", settings.PositionError, processed);
                ReduceReservedQuaternionGroup(clip, entries, prefix + "Q", settings.RotationErrorDegrees, processed);
            }
        }

        private static void ReduceTransformGroups(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            ReductionSettings settings,
            ISet<string> processed)
        {
            ReduceReservedVectorGroup(
                clip,
                entries,
                "m_LocalPosition",
                settings.PositionError,
                processed);
            ReduceReservedQuaternionGroup(
                clip,
                entries,
                "m_LocalRotation",
                settings.RotationErrorDegrees,
                processed);
            ReduceReservedVectorGroup(
                clip,
                entries,
                "m_LocalScale",
                settings.ScaleError,
                processed);

            foreach (var prefix in new[]
                     {
                         "localEulerAnglesRaw",
                         "localEulerAnglesBaked",
                         "localEulerAngles",
                         "m_LocalEulerAngles",
                         "m_LocalEulerAnglesHint",
                     })
                ReduceReservedEulerGroup(
                    clip,
                    entries,
                    prefix,
                    settings.RotationErrorDegrees,
                    processed);
        }

        private static int CollapseAnimatorStaticCurves(AnimationClip clip)
        {
            var entries = ReadAnimatorCurves(clip);
            var processed = new HashSet<string>(StringComparer.Ordinal);
            var collapsed = 0;
            collapsed += CollapseStaticVectorGroup(clip, entries, "RootT", processed);
            collapsed += CollapseStaticQuaternionGroup(clip, entries, "RootQ", processed);
            foreach (var prefix in GoalPrefixes)
            {
                collapsed += CollapseStaticVectorGroup(clip, entries, prefix + "T", processed);
                collapsed += CollapseStaticQuaternionGroup(clip, entries, prefix + "Q", processed);
            }

            foreach (var pair in entries)
            {
                if (processed.Contains(pair.Key) || pair.Value.Binding.isDiscreteCurve)
                    continue;
                collapsed += CollapseStaticScalarCurve(clip, pair.Value);
            }
            return collapsed;
        }

        private static int CollapseStaticTransformCurves(AnimationClip clip)
        {
            var entries = ReadAllFloatCurves(clip);
            var collapsed = 0;
            foreach (var scope in entries.GroupBy(entry => new CurveScope(
                         entry.Binding.type,
                         entry.Binding.path)))
            {
                if (scope.Key.Type != typeof(Animator) && scope.Key.Type != typeof(Transform))
                    continue;
                if (scope.Key.Type == typeof(Animator) && !string.IsNullOrEmpty(scope.Key.Path))
                    continue;

                var scopedEntries = scope.ToDictionary(
                    entry => entry.Binding.propertyName,
                    entry => entry,
                    StringComparer.Ordinal);
                var processed = new HashSet<string>(StringComparer.Ordinal);
                if (scope.Key.Type == typeof(Animator))
                {
                    collapsed += CollapseStaticVectorGroup(clip, scopedEntries, "RootT", processed);
                    collapsed += CollapseStaticQuaternionGroup(clip, scopedEntries, "RootQ", processed);
                    foreach (var prefix in GoalPrefixes)
                    {
                        collapsed += CollapseStaticVectorGroup(clip, scopedEntries, prefix + "T", processed);
                        collapsed += CollapseStaticQuaternionGroup(clip, scopedEntries, prefix + "Q", processed);
                    }
                    foreach (var pair in scopedEntries)
                    {
                        if (processed.Contains(pair.Key) || pair.Value.Binding.isDiscreteCurve)
                            continue;
                        collapsed += CollapseStaticScalarCurve(clip, pair.Value);
                    }
                }
                else
                {
                    collapsed += CollapseStaticVectorGroup(clip, scopedEntries, "m_LocalPosition", processed);
                    collapsed += CollapseStaticQuaternionGroup(clip, scopedEntries, "m_LocalRotation", processed);
                    collapsed += CollapseStaticVectorGroup(clip, scopedEntries, "m_LocalScale", processed);
                    foreach (var prefix in new[]
                             {
                                 "localEulerAnglesRaw",
                                 "localEulerAnglesBaked",
                                 "localEulerAngles",
                                 "m_LocalEulerAngles",
                                 "m_LocalEulerAnglesHint",
                             })
                        collapsed += CollapseStaticEulerGroup(clip, scopedEntries, prefix, processed);
                }
            }
            return collapsed;
        }

        private static int CollapseStaticCurvesPreservingTimeRange(
            AnimationClip clip,
            Func<AnimationClip, int> collapse)
        {
            var timeRange = CaptureClipTimeRange(clip);
            try
            {
                var collapsed = collapse(clip);
                EnsureDurationAnchor(clip, timeRange.StopTime);
                return collapsed;
            }
            finally
            {
                RestoreClipTimeRange(clip, timeRange);
            }
        }

        private static ClipTimeRange CaptureClipTimeRange(AnimationClip clip)
        {
            var settings = AnimationUtility.GetAnimationClipSettings(clip);
            var startTime = IsFinite(settings.startTime) ? settings.startTime : 0f;
            var length = clip != null && IsFinite(clip.length) ? Mathf.Max(0f, clip.length) : 0f;
            var derivedStopTime = startTime + length;
            var stopTime = IsFinite(settings.stopTime) && settings.stopTime > startTime + StaticValueEpsilon
                ? settings.stopTime
                : derivedStopTime;
            if (!IsFinite(stopTime) || stopTime < startTime)
                stopTime = startTime;
            return new ClipTimeRange(startTime, stopTime);
        }

        private static void RestoreClipTimeRange(AnimationClip clip, ClipTimeRange timeRange)
        {
            if (clip == null)
                return;
            var settings = AnimationUtility.GetAnimationClipSettings(clip);
            settings.startTime = timeRange.StartTime;
            settings.stopTime = timeRange.StopTime;
            AnimationUtility.SetAnimationClipSettings(clip, settings);
        }

        private static void EnsureDurationAnchor(AnimationClip clip, float stopTime)
        {
            if (clip == null || !IsFinite(stopTime) || stopTime <= 0f)
                return;

            var anchorBinding = default(EditorCurveBinding);
            var anchorCurve = (AnimationCurve)null;
            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                if (binding.type != typeof(Animator) && binding.type != typeof(Transform))
                    continue;
                if (binding.isDiscreteCurve)
                    continue;
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                if (curve == null || curve.length == 0)
                    continue;
                if (curve.length > 1)
                    return;
                var keys = curve.keys;
                if (anchorCurve == null)
                {
                    anchorBinding = binding;
                    anchorCurve = curve;
                }
            }

            if (anchorCurve == null)
                return;
            var anchorKeys = anchorCurve.keys;
            anchorCurve.AddKey(new Keyframe(stopTime, anchorKeys[anchorKeys.Length - 1].value));
            AnimationUtility.SetEditorCurve(clip, anchorBinding, anchorCurve);
        }

        private static int CollapseStaticVectorGroup(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            string prefix,
            ISet<string> processed)
        {
            var names = new[] { prefix + ".x", prefix + ".y", prefix + ".z" };
            if (!TryReadAlignedGroup(entries, names, out var group))
                return 0;
            foreach (var name in names)
                processed.Add(name);
            var keys = group.Select(entry => entry.Curve.keys).ToArray();
            if (keys[0].Length <= 1 || !IsConstantVector(keys))
                return 0;
            return CollapseGroupToFirstKey(clip, group, component => keys[component][0].value);
        }

        private static int CollapseStaticQuaternionGroup(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            string prefix,
            ISet<string> processed)
        {
            var names = new[] { prefix + ".x", prefix + ".y", prefix + ".z", prefix + ".w" };
            if (!TryReadAlignedGroup(entries, names, out var group))
                return 0;
            foreach (var name in names)
                processed.Add(name);
            var keys = group.Select(entry => entry.Curve.keys).ToArray();
            if (keys[0].Length <= 1)
                return 0;
            for (var component = 0; component < keys.Length; component++)
                for (var index = 0; index < keys[component].Length; index++)
                    if (!IsFinite(keys[component][index].value))
                        return 0;
            for (var index = 0; index < keys[0].Length; index++)
            {
                var magnitudeSquared =
                    keys[0][index].value * keys[0][index].value +
                    keys[1][index].value * keys[1][index].value +
                    keys[2][index].value * keys[2][index].value +
                    keys[3][index].value * keys[3][index].value;
                if (magnitudeSquared <= 1e-12f)
                    return 0;
            }
            var values = ReadContinuousQuaternions(keys, out _);
            for (var i = 1; i < values.Length; i++)
            {
                if (QuaternionAngleDegrees(values[0], values[i]) > StaticRotationEpsilonDegrees)
                    return 0;
            }
            return CollapseGroupToFirstKey(
                clip,
                group,
                component => QuaternionComponent(values[0], component));
        }

        private static int CollapseStaticEulerGroup(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            string prefix,
            ISet<string> processed)
        {
            var names = new[] { prefix + ".x", prefix + ".y", prefix + ".z" };
            if (!TryReadAlignedGroup(entries, names, out var group))
                return 0;
            foreach (var name in names)
                processed.Add(name);
            var keys = group.Select(entry => entry.Curve.keys).ToArray();
            if (keys[0].Length <= 1)
                return 0;
            var values = ReadContinuousEulerAngles(keys, out _);
            for (var i = 0; i < values.Length; i++)
                if (!IsFinite(values[i]))
                    return 0;
            var first = Quaternion.Euler(values[0]);
            for (var i = 1; i < values.Length; i++)
            {
                if (QuaternionAngleDegrees(first, Quaternion.Euler(values[i])) > StaticRotationEpsilonDegrees)
                    return 0;
            }
            return CollapseGroupToFirstKey(
                clip,
                group,
                component => VectorComponent(values[0], component));
        }

        private static int CollapseStaticScalarCurve(AnimationClip clip, CurveEntry entry)
        {
            if (entry.Curve == null || entry.Curve.length <= 1 || entry.Binding.isDiscreteCurve)
                return 0;
            var keys = entry.Curve.keys;
            var first = keys[0].value;
            if (!IsFinite(first))
                return 0;
            for (var i = 1; i < keys.Length; i++)
            {
                if (!IsFinite(keys[i].value) || Mathf.Abs(keys[i].value - first) > StaticValueEpsilon)
                    return 0;
            }
            AnimationUtility.SetEditorCurve(clip, entry.Binding, BuildSingleKeyCurve(entry.Curve, first));
            return 1;
        }

        private static int CollapseGroupToFirstKey(
            AnimationClip clip,
            IReadOnlyList<CurveEntry> group,
            Func<int, float> valueAt)
        {
            var bindings = new EditorCurveBinding[group.Count];
            var curves = new AnimationCurve[group.Count];
            for (var component = 0; component < group.Count; component++)
            {
                bindings[component] = group[component].Binding;
                curves[component] = BuildSingleKeyCurve(
                    group[component].Curve,
                    valueAt(component));
            }
            AnimationUtility.SetEditorCurves(clip, bindings, curves);
            return group.Count;
        }

        private static bool IsConstantVector(Keyframe[][] keys)
        {
            var first = ReadVector(keys, 0);
            if (!IsFinite(first))
                return false;
            for (var i = 1; i < keys[0].Length; i++)
            {
                var current = ReadVector(keys, i);
                if (!IsFinite(current) || Vector3.Distance(first, current) > StaticValueEpsilon)
                    return false;
            }
            return true;
        }

        private static bool IsFinite(Vector3 value)
        {
            return IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

        private static void ReduceReservedVectorGroup(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            string prefix,
            float tolerance,
            ISet<string> processed)
        {
            ReservePresentGroupMembers(entries, prefix, new[] { "x", "y", "z" }, processed);
            ReduceVectorGroup(clip, entries, prefix, tolerance, processed);
        }

        private static void ReduceReservedQuaternionGroup(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            string prefix,
            float toleranceDegrees,
            ISet<string> processed)
        {
            ReservePresentGroupMembers(entries, prefix, new[] { "x", "y", "z", "w" }, processed);
            ReduceQuaternionGroup(clip, entries, prefix, toleranceDegrees, processed);
        }

        private static void ReduceReservedEulerGroup(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            string prefix,
            float toleranceDegrees,
            ISet<string> processed)
        {
            ReservePresentGroupMembers(entries, prefix, new[] { "x", "y", "z" }, processed);
            ReduceEulerGroup(clip, entries, prefix, toleranceDegrees, processed);
        }

        private static void ReservePresentGroupMembers(
            IReadOnlyDictionary<string, CurveEntry> entries,
            string prefix,
            IEnumerable<string> components,
            ISet<string> processed)
        {
            foreach (var component in components)
            {
                var name = prefix + "." + component;
                if (entries.ContainsKey(name))
                    processed.Add(name);
            }
        }

        private static void ReduceScalarCurve(
            AnimationClip clip,
            CurveEntry entry,
            float tolerance)
        {
            var keys = entry.Curve.keys;
            if (keys.Length <= 2)
                return;
            var keep = BuildKeepMask(
                keys.Length,
                tolerance,
                (start, index, end) => ScalarError(keys, start, index, end));
            if (CountKept(keep) == keys.Length)
                return;
            AnimationUtility.SetEditorCurve(
                clip,
                entry.Binding,
                BuildReducedCurve(entry.Curve, keep, index => keys[index].value));
        }

        private static void ReduceVectorGroup(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            string prefix,
            float tolerance,
            ISet<string> processed)
        {
            var names = new[] { prefix + ".x", prefix + ".y", prefix + ".z" };
            if (!TryReadAlignedGroup(entries, names, out var group))
                return;
            foreach (var name in names)
                processed.Add(name);

            var keys = group.Select(entry => entry.Curve.keys).ToArray();
            if (keys[0].Length <= 2)
                return;
            var keep = BuildKeepMask(
                keys[0].Length,
                tolerance,
                (start, index, end) => VectorError(keys, start, index, end));
            if (CountKept(keep) == keys[0].Length)
                return;

            var reducedCurves = new AnimationCurve[group.Length];
            var bindings = new EditorCurveBinding[group.Length];
            for (var component = 0; component < group.Length; component++)
            {
                var componentIndex = component;
                bindings[component] = group[component].Binding;
                reducedCurves[component] = BuildReducedCurve(
                    group[component].Curve,
                    keep,
                    index => keys[componentIndex][index].value);
            }
            AnimationUtility.SetEditorCurves(clip, bindings, reducedCurves);
        }

        private static void ReduceQuaternionGroup(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            string prefix,
            float toleranceDegrees,
            ISet<string> processed)
        {
            var names = new[] { prefix + ".x", prefix + ".y", prefix + ".z", prefix + ".w" };
            if (!TryReadAlignedGroup(entries, names, out var group))
                return;
            foreach (var name in names)
                processed.Add(name);

            var keys = group.Select(entry => entry.Curve.keys).ToArray();
            var values = ReadContinuousQuaternions(keys, out var continuityChanged);
            if (values.Length <= 2 && !continuityChanged)
                return;
            var keep = values.Length <= 2
                ? KeepAll(values.Length)
                : BuildKeepMask(
                    values.Length,
                    toleranceDegrees,
                    (start, index, end) => QuaternionError(keys[0], values, start, index, end));
            if (CountKept(keep) == values.Length && !continuityChanged)
                return;

            var reducedCurves = new AnimationCurve[group.Length];
            var bindings = new EditorCurveBinding[group.Length];
            for (var component = 0; component < group.Length; component++)
            {
                var componentIndex = component;
                bindings[component] = group[component].Binding;
                reducedCurves[component] = BuildReducedCurve(
                    group[component].Curve,
                    keep,
                    index => QuaternionComponent(values[index], componentIndex));
            }
            AnimationUtility.SetEditorCurves(clip, bindings, reducedCurves);
        }

        private static void ReduceEulerGroup(
            AnimationClip clip,
            IReadOnlyDictionary<string, CurveEntry> entries,
            string prefix,
            float toleranceDegrees,
            ISet<string> processed)
        {
            var names = new[] { prefix + ".x", prefix + ".y", prefix + ".z" };
            if (!TryReadAlignedGroup(entries, names, out var group))
                return;
            foreach (var name in names)
                processed.Add(name);

            var keys = group.Select(entry => entry.Curve.keys).ToArray();
            var values = ReadContinuousEulerAngles(keys, out var continuityChanged);
            if (values.Length <= 2 && !continuityChanged)
                return;
            var keep = values.Length <= 2
                ? KeepAll(values.Length)
                : BuildKeepMask(
                    values.Length,
                    toleranceDegrees,
                    (start, index, end) => EulerRotationError(keys[0], values, start, index, end));
            if (CountKept(keep) == values.Length && !continuityChanged)
                return;

            var reducedCurves = new AnimationCurve[group.Length];
            var bindings = new EditorCurveBinding[group.Length];
            for (var component = 0; component < group.Length; component++)
            {
                var componentIndex = component;
                bindings[component] = group[component].Binding;
                reducedCurves[component] = BuildReducedCurve(
                    group[component].Curve,
                    keep,
                    index => VectorComponent(values[index], componentIndex));
            }
            AnimationUtility.SetEditorCurves(clip, bindings, reducedCurves);
        }

        private static bool TryReadAlignedGroup(
            IReadOnlyDictionary<string, CurveEntry> entries,
            IReadOnlyList<string> names,
            out CurveEntry[] group)
        {
            group = new CurveEntry[names.Count];
            for (var i = 0; i < names.Count; i++)
            {
                if (
                    !entries.TryGetValue(names[i], out group[i]) ||
                    group[i].Curve == null ||
                    group[i].Binding.isDiscreteCurve)
                    return false;
            }

            var reference = group[0].Curve.keys;
            for (var i = 0; i < reference.Length; i++)
                if (!IsFinite(reference[i].time))
                    return false;
            for (var component = 1; component < group.Length; component++)
            {
                var candidate = group[component].Curve.keys;
                if (candidate.Length != reference.Length)
                    return false;
                for (var i = 0; i < reference.Length; i++)
                    if (
                        !IsFinite(candidate[i].time) ||
                        Mathf.Abs(candidate[i].time - reference[i].time) > 1e-6f)
                        return false;
            }
            return true;
        }

        private static bool[] BuildKeepMask(
            int count,
            float tolerance,
            Func<int, int, int, float> errorAt)
        {
            var keep = new bool[count];
            if (count == 0)
                return keep;
            keep[0] = true;
            if (count == 1)
                return keep;
            keep[count - 1] = true;

            var ranges = new Stack<IndexRange>();
            ranges.Push(new IndexRange(0, count - 1));
            while (ranges.Count > 0)
            {
                var range = ranges.Pop();
                var maxError = tolerance;
                var maxIndex = -1;
                for (var index = range.Start + 1; index < range.End; index++)
                {
                    var error = errorAt(range.Start, index, range.End);
                    if (float.IsNaN(error) || float.IsInfinity(error))
                        error = float.PositiveInfinity;
                    if (error <= maxError)
                        continue;
                    maxError = error;
                    maxIndex = index;
                }
                if (maxIndex < 0)
                    continue;

                keep[maxIndex] = true;
                if (maxIndex - range.Start > 1)
                    ranges.Push(new IndexRange(range.Start, maxIndex));
                if (range.End - maxIndex > 1)
                    ranges.Push(new IndexRange(maxIndex, range.End));
            }
            return keep;
        }

        private static float ScalarError(Keyframe[] keys, int start, int index, int end)
        {
            var alpha = InterpolationFactor(keys[start].time, keys[index].time, keys[end].time);
            if (float.IsNaN(alpha))
                return float.PositiveInfinity;
            var expected = Mathf.LerpUnclamped(keys[start].value, keys[end].value, alpha);
            return Mathf.Abs(keys[index].value - expected);
        }

        private static float VectorError(Keyframe[][] keys, int start, int index, int end)
        {
            var alpha = InterpolationFactor(keys[0][start].time, keys[0][index].time, keys[0][end].time);
            if (float.IsNaN(alpha))
                return float.PositiveInfinity;
            var from = ReadVector(keys, start);
            var to = ReadVector(keys, end);
            return Vector3.Distance(ReadVector(keys, index), Vector3.LerpUnclamped(from, to, alpha));
        }

        private static float QuaternionError(
            Keyframe[] timeKeys,
            Quaternion[] values,
            int start,
            int index,
            int end)
        {
            var alpha = InterpolationFactor(timeKeys[start].time, timeKeys[index].time, timeKeys[end].time);
            if (float.IsNaN(alpha))
                return float.PositiveInfinity;
            var expected = Normalize(new Quaternion(
                Mathf.LerpUnclamped(values[start].x, values[end].x, alpha),
                Mathf.LerpUnclamped(values[start].y, values[end].y, alpha),
                Mathf.LerpUnclamped(values[start].z, values[end].z, alpha),
                Mathf.LerpUnclamped(values[start].w, values[end].w, alpha)));
            return QuaternionAngleDegrees(values[index], expected);
        }

        private static float EulerRotationError(
            Keyframe[] timeKeys,
            Vector3[] values,
            int start,
            int index,
            int end)
        {
            var alpha = InterpolationFactor(timeKeys[start].time, timeKeys[index].time, timeKeys[end].time);
            if (float.IsNaN(alpha))
                return float.PositiveInfinity;
            var expected = Vector3.LerpUnclamped(values[start], values[end], alpha);
            return QuaternionAngleDegrees(
                Quaternion.Euler(values[index]),
                Quaternion.Euler(expected));
        }

        private static float InterpolationFactor(float startTime, float time, float endTime)
        {
            var duration = endTime - startTime;
            return Mathf.Abs(duration) > 1e-8f ? (time - startTime) / duration : float.NaN;
        }

        private static Vector3 ReadVector(Keyframe[][] keys, int index)
        {
            return new Vector3(
                keys[0][index].value,
                keys[1][index].value,
                keys[2][index].value);
        }

        private static Quaternion[] ReadContinuousQuaternions(
            Keyframe[][] keys,
            out bool continuityChanged)
        {
            continuityChanged = false;
            var values = new Quaternion[keys[0].Length];
            for (var i = 0; i < values.Length; i++)
            {
                var raw = new Quaternion(
                    keys[0][i].value,
                    keys[1][i].value,
                    keys[2][i].value,
                    keys[3][i].value);
                var value = Normalize(raw);
                if (i > 0 && Quaternion.Dot(values[i - 1], value) < 0f)
                {
                    value = new Quaternion(-value.x, -value.y, -value.z, -value.w);
                    continuityChanged = true;
                }
                if (Mathf.Abs(raw.x - value.x) > 1e-6f ||
                    Mathf.Abs(raw.y - value.y) > 1e-6f ||
                    Mathf.Abs(raw.z - value.z) > 1e-6f ||
                    Mathf.Abs(raw.w - value.w) > 1e-6f)
                    continuityChanged = true;
                values[i] = value;
            }
            return values;
        }

        private static Vector3[] ReadContinuousEulerAngles(
            Keyframe[][] keys,
            out bool continuityChanged)
        {
            continuityChanged = false;
            var values = new Vector3[keys[0].Length];
            if (values.Length == 0)
                return values;

            values[0] = ReadVector(keys, 0);
            for (var i = 1; i < values.Length; i++)
            {
                var raw = ReadVector(keys, i);
                var previousRaw = ReadVector(keys, i - 1);
                var value = new Vector3(
                    values[i - 1].x + Mathf.DeltaAngle(previousRaw.x, raw.x),
                    values[i - 1].y + Mathf.DeltaAngle(previousRaw.y, raw.y),
                    values[i - 1].z + Mathf.DeltaAngle(previousRaw.z, raw.z));
                if (Vector3.SqrMagnitude(raw - value) > 1e-12f)
                    continuityChanged = true;
                values[i] = value;
            }
            return values;
        }

        private static float QuaternionAngleDegrees(Quaternion from, Quaternion to)
        {
            var dot =
                (double)from.x * to.x +
                (double)from.y * to.y +
                (double)from.z * to.z +
                (double)from.w * to.w;
            var fromLengthSquared =
                (double)from.x * from.x +
                (double)from.y * from.y +
                (double)from.z * from.z +
                (double)from.w * from.w;
            var toLengthSquared =
                (double)to.x * to.x +
                (double)to.y * to.y +
                (double)to.z * to.z +
                (double)to.w * to.w;
            var lengthProduct = Math.Sqrt(fromLengthSquared * toLengthSquared);
            if (lengthProduct <= 1e-12d)
                return 180f;
            dot /= lengthProduct;
            dot = Math.Min(1d, Math.Abs(dot));
            return (float)(2d * Math.Acos(dot) * 180d / Math.PI);
        }

        private static AnimationCurve BuildReducedCurve(
            AnimationCurve source,
            IReadOnlyList<bool> keep,
            Func<int, float> valueAt)
        {
            var sourceKeys = source.keys;
            var reducedKeys = new List<Keyframe>(CountKept(keep));
            for (var i = 0; i < sourceKeys.Length; i++)
            {
                if (keep[i])
                    reducedKeys.Add(new Keyframe(sourceKeys[i].time, valueAt(i)));
            }
            var curve = new AnimationCurve(reducedKeys.ToArray())
            {
                preWrapMode = source.preWrapMode,
                postWrapMode = source.postWrapMode,
            };
            SetLinearTangents(curve);
            return curve;
        }

        private static AnimationCurve BuildSingleKeyCurve(AnimationCurve source, float value)
        {
            var sourceKeys = source.keys;
            var time = sourceKeys.Length > 0 ? sourceKeys[0].time : 0f;
            var curve = new AnimationCurve(new Keyframe(time, value))
            {
                preWrapMode = source.preWrapMode,
                postWrapMode = source.postWrapMode,
            };
            SetLinearTangents(curve);
            return curve;
        }

        private static void SetLinearTangents(AnimationCurve curve)
        {
            var keys = curve.keys;
            for (var i = 0; i < keys.Length; i++)
            {
                var key = keys[i];
                key.inTangent = i > 0
                    ? SegmentSlope(keys[i - 1], keys[i])
                    : i + 1 < keys.Length ? SegmentSlope(keys[i], keys[i + 1]) : 0f;
                key.outTangent = i + 1 < keys.Length
                    ? SegmentSlope(keys[i], keys[i + 1])
                    : i > 0 ? SegmentSlope(keys[i - 1], keys[i]) : 0f;
                key.weightedMode = WeightedMode.None;
                keys[i] = key;
            }
            curve.keys = keys;
        }

        private static float SegmentSlope(Keyframe from, Keyframe to)
        {
            var duration = to.time - from.time;
            return Mathf.Abs(duration) > 1e-8f ? (to.value - from.value) / duration : 0f;
        }

        private static Quaternion Normalize(Quaternion value)
        {
            var magnitude = Mathf.Sqrt(
                value.x * value.x +
                value.y * value.y +
                value.z * value.z +
                value.w * value.w);
            return magnitude > 1e-6f
                ? new Quaternion(
                    value.x / magnitude,
                    value.y / magnitude,
                    value.z / magnitude,
                    value.w / magnitude)
                : Quaternion.identity;
        }

        private static float QuaternionComponent(Quaternion value, int component)
        {
            switch (component)
            {
                case 0: return value.x;
                case 1: return value.y;
                case 2: return value.z;
                default: return value.w;
            }
        }

        private static float VectorComponent(Vector3 value, int component)
        {
            switch (component)
            {
                case 0: return value.x;
                case 1: return value.y;
                default: return value.z;
            }
        }

        private static int CountKept(IReadOnlyList<bool> keep)
        {
            var count = 0;
            for (var i = 0; i < keep.Count; i++)
                if (keep[i])
                    count++;
            return count;
        }

        private static bool[] KeepAll(int count)
        {
            var keep = new bool[count];
            for (var i = 0; i < keep.Length; i++)
                keep[i] = true;
            return keep;
        }

        private static ReductionSettings SettingsFor(HumanoidCurveReductionPreset preset)
        {
            switch (preset)
            {
                case HumanoidCurveReductionPreset.Light:
                    return new ReductionSettings(0.0001f, 0.05f, 0.0001f, 0.0005f);
                case HumanoidCurveReductionPreset.Medium:
                    return new ReductionSettings(0.001f, 0.25f, 0.001f, 0.0025f);
                case HumanoidCurveReductionPreset.Aggressive:
                    return new ReductionSettings(0.01f, 1f, 0.01f, 0.01f);
                default:
                    return new ReductionSettings(0f, 0f, 0f, 0f);
            }
        }

        private readonly struct ReductionSettings
        {
            public readonly float PositionError;
            public readonly float RotationErrorDegrees;
            public readonly float ScaleError;
            public readonly float ScalarError;

            public ReductionSettings(
                float positionError,
                float rotationErrorDegrees,
                float scaleError,
                float scalarError)
            {
                PositionError = positionError;
                RotationErrorDegrees = rotationErrorDegrees;
                ScaleError = scaleError;
                ScalarError = scalarError;
            }
        }

        private readonly struct ClipTimeRange
        {
            public readonly float StartTime;
            public readonly float StopTime;

            public ClipTimeRange(float startTime, float stopTime)
            {
                StartTime = startTime;
                StopTime = stopTime;
            }
        }

        private readonly struct CurveScope : IEquatable<CurveScope>
        {
            public readonly Type Type;
            public readonly string Path;

            public CurveScope(Type type, string path)
            {
                Type = type;
                Path = path ?? string.Empty;
            }

            public bool Equals(CurveScope other)
            {
                return Type == other.Type && string.Equals(Path, other.Path, StringComparison.Ordinal);
            }

            public override bool Equals(object obj)
            {
                return obj is CurveScope other && Equals(other);
            }

            public override int GetHashCode()
            {
                unchecked
                {
                    return ((Type != null ? Type.GetHashCode() : 0) * 397) ^ Path.GetHashCode();
                }
            }
        }

        private readonly struct CurveEntry
        {
            public readonly EditorCurveBinding Binding;
            public readonly AnimationCurve Curve;

            public CurveEntry(EditorCurveBinding binding, AnimationCurve curve)
            {
                Binding = binding;
                Curve = curve;
            }
        }

        private readonly struct IndexRange
        {
            public readonly int Start;
            public readonly int End;

            public IndexRange(int start, int end)
            {
                Start = start;
                End = end;
            }
        }
    }
}
#endif
