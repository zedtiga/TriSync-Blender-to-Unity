#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    internal static class TransformSmoothingPreferences
    {
        internal const float DefaultSmoothingTime = 0.2f;
        internal const float MinSmoothingTime = 0.02f;
        internal const float MaxSmoothingTime = 1.0f;

        private const string EnabledEditorPrefKey = "BlenderSyncVNext.TransformSmoothingEnabled";
        private const string TimeEditorPrefKey = "BlenderSyncVNext.TransformSmoothingTime";
        private const string CurveEditorPrefKey = "BlenderSyncVNext.TransformSmoothingCurve";

        [Serializable]
        private sealed class CurveData
        {
            public CurveKeyData[] keys;
        }

        [Serializable]
        private struct CurveKeyData
        {
            public float time;
            public float value;
            public float inTangent;
            public float outTangent;
            public float inWeight;
            public float outWeight;
            public int weightedMode;
        }

        private static bool _loaded;
        private static bool _enabled;
        private static float _smoothingTime;
        private static AnimationCurve _followCurve;

        internal static bool Enabled
        {
            get
            {
                EnsureLoaded();
                return _enabled;
            }
            set
            {
                EnsureLoaded();
                _enabled = value;
                EditorPrefs.SetBool(EnabledEditorPrefKey, value);
                if (!value)
                    SceneSyncTransformSmoother.CompleteAll();
            }
        }

        internal static float SmoothingTime
        {
            get
            {
                EnsureLoaded();
                return _smoothingTime;
            }
            set
            {
                EnsureLoaded();
                _smoothingTime = NormalizeTime(value);
                EditorPrefs.SetFloat(TimeEditorPrefKey, _smoothingTime);
            }
        }

        internal static AnimationCurve FollowCurve
        {
            get
            {
                EnsureLoaded();
                return CloneCurve(_followCurve);
            }
            set
            {
                EnsureLoaded();
                _followCurve = NormalizeCurve(value);
                EditorPrefs.SetString(
                    CurveEditorPrefKey,
                    JsonUtility.ToJson(new CurveData { keys = SerializeKeys(_followCurve.keys) }));
            }
        }

        internal static AnimationCurve RuntimeCurve
        {
            get
            {
                EnsureLoaded();
                return _followCurve;
            }
        }

        internal static void ResetSmoothing()
        {
            EditorPrefs.DeleteKey(EnabledEditorPrefKey);
            EditorPrefs.DeleteKey(TimeEditorPrefKey);
            EditorPrefs.DeleteKey(CurveEditorPrefKey);
            _loaded = false;
            EnsureLoaded();
        }

        internal static void Reload()
        {
            _loaded = false;
            EnsureLoaded();
        }

        internal static AnimationCurve NormalizeCurve(AnimationCurve source)
        {
            var sourceKeys = source != null ? source.keys : Array.Empty<Keyframe>();
            var keys = new List<Keyframe>(sourceKeys.Length + 2);
            foreach (var sourceKey in sourceKeys)
            {
                if (!IsFinite(sourceKey.time) || !IsFinite(sourceKey.value))
                    continue;
                var key = sourceKey;
                key.time = Mathf.Clamp01(key.time);
                key.value = Mathf.Clamp01(key.value);
                keys.Add(key);
            }

            keys.Sort((left, right) => left.time.CompareTo(right.time));
            var unique = new List<Keyframe>(keys.Count + 2);
            foreach (var key in keys)
            {
                if (unique.Count > 0 && Mathf.Abs(unique[unique.Count - 1].time - key.time) <= 0.0001f)
                    unique[unique.Count - 1] = key;
                else
                    unique.Add(key);
            }

            EnsureEndpoint(unique, 0f, 0f, insertAtStart: true);
            EnsureEndpoint(unique, 1f, 1f, insertAtStart: false);

            var previousValue = 0f;
            for (var index = 0; index < unique.Count; index++)
            {
                var key = unique[index];
                key.value = Mathf.Max(previousValue, Mathf.Clamp01(key.value));
                if (index == 0)
                {
                    key.time = 0f;
                    key.value = 0f;
                }
                else if (index == unique.Count - 1)
                {
                    key.time = 1f;
                    key.value = 1f;
                }
                unique[index] = key;
                previousValue = key.value;
            }

            var normalized = new AnimationCurve(unique.ToArray())
            {
                preWrapMode = WrapMode.ClampForever,
                postWrapMode = WrapMode.ClampForever,
            };
            return normalized;
        }

        private static void EnsureLoaded()
        {
            if (_loaded)
                return;

            _enabled = EditorPrefs.GetBool(EnabledEditorPrefKey, true);
            _smoothingTime = NormalizeTime(
                EditorPrefs.GetFloat(TimeEditorPrefKey, DefaultSmoothingTime));
            _followCurve = LoadCurve();
            _loaded = true;
        }

        private static AnimationCurve LoadCurve()
        {
            var json = EditorPrefs.GetString(CurveEditorPrefKey, string.Empty);
            if (!string.IsNullOrWhiteSpace(json))
            {
                try
                {
                    var data = JsonUtility.FromJson<CurveData>(json);
                    if (data?.keys != null && data.keys.Length > 0)
                        return NormalizeCurve(new AnimationCurve(DeserializeKeys(data.keys)));
                }
                catch
                {
                    // Invalid user preferences fall back to the product default.
                }
            }
            return CreateDefaultCurve();
        }

        private static AnimationCurve CreateDefaultCurve()
        {
            return NormalizeCurve(new AnimationCurve(
                new Keyframe(0f, 0f, 0f, 2f),
                new Keyframe(1f, 1f, 0f, 0f)));
        }

        private static AnimationCurve CloneCurve(AnimationCurve source)
        {
            var clone = new AnimationCurve(source != null ? source.keys : Array.Empty<Keyframe>());
            if (source != null)
            {
                clone.preWrapMode = source.preWrapMode;
                clone.postWrapMode = source.postWrapMode;
            }
            return clone;
        }

        private static CurveKeyData[] SerializeKeys(Keyframe[] keys)
        {
            var serialized = new CurveKeyData[keys.Length];
            for (var index = 0; index < keys.Length; index++)
            {
                var key = keys[index];
                serialized[index] = new CurveKeyData
                {
                    time = key.time,
                    value = key.value,
                    inTangent = key.inTangent,
                    outTangent = key.outTangent,
                    inWeight = key.inWeight,
                    outWeight = key.outWeight,
                    weightedMode = (int)key.weightedMode,
                };
            }
            return serialized;
        }

        private static Keyframe[] DeserializeKeys(CurveKeyData[] serialized)
        {
            var keys = new Keyframe[serialized.Length];
            for (var index = 0; index < serialized.Length; index++)
            {
                var source = serialized[index];
                var key = new Keyframe(
                    source.time,
                    source.value,
                    source.inTangent,
                    source.outTangent,
                    source.inWeight,
                    source.outWeight)
                {
                    weightedMode = (WeightedMode)source.weightedMode,
                };
                keys[index] = key;
            }
            return keys;
        }

        private static void EnsureEndpoint(
            List<Keyframe> keys,
            float time,
            float value,
            bool insertAtStart)
        {
            if (keys.Count == 0)
            {
                keys.Add(new Keyframe(time, value));
                return;
            }

            var index = insertAtStart ? 0 : keys.Count - 1;
            if (Mathf.Abs(keys[index].time - time) <= 0.0001f)
            {
                var endpoint = keys[index];
                endpoint.time = time;
                endpoint.value = value;
                keys[index] = endpoint;
                return;
            }

            if (insertAtStart)
                keys.Insert(0, new Keyframe(time, value));
            else
                keys.Add(new Keyframe(time, value));
        }

        private static float NormalizeTime(float value)
        {
            return IsFinite(value)
                ? Mathf.Clamp(value, MinSmoothingTime, MaxSmoothingTime)
                : DefaultSmoothingTime;
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }
    }
}
#endif
