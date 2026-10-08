using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.SceneSyncCore;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.AnimationClipImport
{
    public sealed class AnimationClipBuilder
    {
        public sealed class BuildResult
        {
            public bool success;
            public string operation;
            public string assetPath;
            public string clipName;
            public string error;
            public int trackCount;
        }

        public BuildResult BuildOrUpdateClip(AnimationClipPayload payload)
        {
            var result = new BuildResult
            {
                success = false,
                operation = "error",
                assetPath = null,
                error = null,
                trackCount = payload != null && payload.tracks != null ? payload.tracks.Count : 0,
            };

#if UNITY_EDITOR
            try
            {
                if (payload == null)
                {
                    result.error = "animation_clip_payload_missing";
                    return result;
                }

                if (string.IsNullOrWhiteSpace(payload.assetId))
                {
                    result.error = "animation_clip_asset_id_missing";
                    return result;
                }

                var assetPath = ResolveAssetPath(payload);
                result.assetPath = assetPath;
                var clipObjectName = ResolveClipObjectName(assetPath);
                result.clipName = clipObjectName;
                var effectiveTracks = BuildEffectiveTracks(payload);
                result.trackCount = effectiveTracks.Count;
                ValidateTrackBindings(effectiveTracks, payload.exportSettings);

                AnimationClip clip;
                try
                {
                    EnsureParentFolder(assetPath);
                    clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(assetPath);
                    result.operation = "create";
                }
                catch (Exception ex)
                {
                    result.error = $"animation_clip_asset_path_invalid path={assetPath} reason={ex.Message}";
                    return result;
                }
                if (clip != null)
                {
                    result.error = $"animation_clip_asset_path_collision path={assetPath}";
                    return result;
                }

                clip = new AnimationClip();
                ApplyClipPayload(clip, payload, effectiveTracks, clipObjectName);
                AssetDatabase.CreateAsset(clip, assetPath);

                EditorUtility.SetDirty(clip);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();

                result.success = true;
                return result;
            }
            catch (Exception ex)
            {
                result.error = ex.Message;
                return result;
            }
#else
            result.error = "animation_clip_builder_requires_editor";
            return result;
#endif
        }

#if UNITY_EDITOR
        private static void ValidateTrackBindings(List<AnimationTrackDto> tracks, AnimationExportSettings settings)
        {
            var tempClip = new AnimationClip();
            try
            {
                foreach (var track in tracks ?? new List<AnimationTrackDto>())
                {
                    if (track == null)
                        continue;
                    BindTrack(tempClip, track, settings);
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(tempClip);
            }
        }

        private static void ApplyClipPayload(AnimationClip clip, AnimationClipPayload payload, List<AnimationTrackDto> effectiveTracks, string clipObjectName)
        {
            if (clip == null || payload == null)
                return;

            clip.name = string.IsNullOrWhiteSpace(clipObjectName) ? "AnimationClip" : clipObjectName;
            ClearAllCurves(clip);
            clip.frameRate = payload.frameRate > 0f ? payload.frameRate : 60f;

            foreach (var track in effectiveTracks ?? new List<AnimationTrackDto>())
            {
                if (track == null)
                    continue;
                BindTrack(clip, track, payload.exportSettings);
            }

            ApplyWrapModeHint(clip, payload.wrapModeHint);
            ApplyDurationHint(clip, payload);
        }

        private static string ResolveClipObjectName(string assetPath)
        {
            var name = string.IsNullOrWhiteSpace(assetPath) ? null : Path.GetFileNameWithoutExtension(assetPath);
            return string.IsNullOrWhiteSpace(name) ? "AnimationClip" : name.Trim();
        }

        private static List<AnimationTrackDto> BuildEffectiveTracks(AnimationClipPayload payload)
        {
            var semantic = ResolveChannelSemantic(payload);
            if (string.Equals(semantic, "unity_rig_v1", StringComparison.Ordinal))
                return BuildEffectiveTracksUnityRigV1(payload);
            if (string.Equals(semantic, "unity_object_v1", StringComparison.Ordinal))
                return BuildEffectiveTracksUnityObjectV1(payload);
            return BuildEffectiveTracksLegacy(payload);
        }

        private static List<AnimationTrackDto> BuildEffectiveTracksUnityRigV1(AnimationClipPayload payload)
        {
            return payload != null && payload.tracks != null
                ? new List<AnimationTrackDto>(payload.tracks.Where(track => track != null))
                : new List<AnimationTrackDto>();
        }

        private static List<AnimationTrackDto> BuildEffectiveTracksUnityObjectV1(AnimationClipPayload payload)
        {
            // Blender-side unity_object_v1 already exports Transform curves in
            // Unity axes. Keep them as authored; do not apply legacy/bone remaps.
            return payload != null && payload.tracks != null
                ? new List<AnimationTrackDto>(payload.tracks.Where(track => track != null))
                : new List<AnimationTrackDto>();
        }

        private static List<AnimationTrackDto> BuildEffectiveTracksLegacy(AnimationClipPayload payload)
        {
            var source = payload != null ? payload.tracks : null;
            if (source == null || source.Count == 0)
                return new List<AnimationTrackDto>();

            var grouped = new Dictionary<string, BoneTrackGroup>(StringComparer.Ordinal);
            var passthrough = new List<AnimationTrackDto>();

            foreach (var track in source)
            {
                if (track == null)
                    continue;
                if (!string.Equals(track.targetType, "bone", StringComparison.Ordinal))
                {
                    passthrough.Add(track);
                    continue;
                }

                var key = $"{track.path}|{track.targetType}";
                if (!grouped.TryGetValue(key, out var group))
                {
                    group = new BoneTrackGroup
                    {
                        path = track.path,
                        targetType = track.targetType,
                    };
                    grouped[key] = group;
                }
                group.Absorb(track);
            }

            foreach (var group in grouped.Values)
                passthrough.AddRange(group.BuildMappedTracks());

            return passthrough;
        }

        private static string ResolveChannelSemantic(AnimationClipPayload payload)
        {
            var semantic = payload != null ? payload.channelSemantic : null;
            if (string.IsNullOrWhiteSpace(semantic))
                return "legacy";
            return semantic.Trim();
        }

        private static void BindTrack(AnimationClip clip, AnimationTrackDto track, AnimationExportSettings settings)
        {
            if (clip == null || track == null)
                return;

            if (string.IsNullOrWhiteSpace(track.property) || string.IsNullOrWhiteSpace(track.component))
                return;

            var curve = BuildCurve(track.keys, ResolveInterpolation(track, settings));
            var propertyName = MapUnityPropertyName(track.property, track.component);
            var normalizedPath = NormalizeBindingPath(track.path);
            var binding = new EditorCurveBinding
            {
                path = normalizedPath,
                type = ResolveBindingType(track),
                propertyName = propertyName,
            };
            try
            {
                AnimationUtility.SetEditorCurve(clip, binding, curve);
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException($"animation_curve_bind_failed path={normalizedPath} type={binding.type.Name} property={propertyName} reason={ex.Message}", ex);
            }
        }

        private static AnimationCurve BuildCurve(List<AnimationKeyDto> keys, string interpolation)
        {
            if (keys == null || keys.Count == 0)
                return new AnimationCurve();

            var orderedKeys = keys
                .Select((key, index) => new { key, index })
                .OrderBy(item => item.key != null ? item.key.time : 0f)
                .ThenBy(item => item.index)
                .ToArray();

            var keyframes = new Keyframe[orderedKeys.Length];
            for (var i = 0; i < orderedKeys.Length; i++)
            {
                var k = orderedKeys[i].key ?? new AnimationKeyDto();
                keyframes[i] = new Keyframe(k.time, k.value);
            }
            var curve = new AnimationCurve(keyframes);
            ApplyInterpolation(curve, interpolation);
            return curve;
        }

        private static string ResolveInterpolation(AnimationTrackDto track, AnimationExportSettings settings)
        {
            if (!string.IsNullOrWhiteSpace(track?.interpolation))
                return track.interpolation.Trim().ToLowerInvariant();
            if (!string.IsNullOrWhiteSpace(settings?.interpolation))
                return settings.interpolation.Trim().ToLowerInvariant();
            return "linear";
        }

        private static void ApplyInterpolation(AnimationCurve curve, string interpolation)
        {
            if (curve == null)
                return;
            var mode = string.IsNullOrWhiteSpace(interpolation) ? "linear" : interpolation.Trim().ToLowerInvariant();
            for (var i = 0; i < curve.length; i++)
            {
                if (mode == "constant")
                {
                    AnimationUtility.SetKeyLeftTangentMode(curve, i, AnimationUtility.TangentMode.Constant);
                    AnimationUtility.SetKeyRightTangentMode(curve, i, AnimationUtility.TangentMode.Constant);
                }
                else if (mode == "smooth")
                {
                    AnimationUtility.SetKeyLeftTangentMode(curve, i, AnimationUtility.TangentMode.Auto);
                    AnimationUtility.SetKeyRightTangentMode(curve, i, AnimationUtility.TangentMode.Auto);
                }
                else
                {
                    AnimationUtility.SetKeyLeftTangentMode(curve, i, AnimationUtility.TangentMode.Linear);
                    AnimationUtility.SetKeyRightTangentMode(curve, i, AnimationUtility.TangentMode.Linear);
                }
            }
        }

        private static Type ResolveBindingType(AnimationTrackDto track)
        {
            if (track != null && string.Equals(track.targetType, "blend_shape", StringComparison.Ordinal))
                return typeof(SkinnedMeshRenderer);
            return typeof(Transform);
        }

        private static string MapUnityPropertyName(string property, string component)
        {
            switch (property)
            {
                case "localPosition":
                    return $"m_LocalPosition.{component}";
                case "localRotation":
                    return $"m_LocalRotation.{component}";
                case "localScale":
                    return $"m_LocalScale.{component}";
                case "blendShapeWeight":
                    return $"blendShape.{component}";
                default:
                    throw new InvalidOperationException($"Unsupported animation track property: {property}/{component}");
            }
        }

        private sealed class BoneTrackGroup
        {
            public string path;
            public string targetType;
            private readonly SortedDictionary<float, BoneFrame> _frames = new SortedDictionary<float, BoneFrame>();

            public void Absorb(AnimationTrackDto track)
            {
                if (track == null || track.keys == null)
                    return;
                foreach (var key in track.keys)
                {
                    var time = key != null ? key.time : 0f;
                    if (!_frames.TryGetValue(time, out var frame))
                    {
                        frame = new BoneFrame();
                        _frames[time] = frame;
                    }
                    frame.Assign(track.property, track.component, key != null ? key.value : 0f);
                }
            }

            public List<AnimationTrackDto> BuildMappedTracks()
            {
                var result = InitTrackSet(path, targetType);
                foreach (var kv in _frames)
                {
                    var time = kv.Key;
                    var frame = kv.Value;
                    var pos = new[] { frame.px, frame.py, frame.pz };
                    var rot = new[] { frame.rx, frame.ry, frame.rz, frame.rw };
                    var scl = new[] { frame.sx, frame.sy, frame.sz };
                    SceneSyncTransformMapper.ConvertBoneTransform(pos, rot, scl, out var mappedPosition, out var mappedRotation, out var mappedScale);
                    Append(result["localPosition.x"], time, mappedPosition.x);
                    Append(result["localPosition.y"], time, mappedPosition.y);
                    Append(result["localPosition.z"], time, mappedPosition.z);
                    Append(result["localRotation.x"], time, mappedRotation.x);
                    Append(result["localRotation.y"], time, mappedRotation.y);
                    Append(result["localRotation.z"], time, mappedRotation.z);
                    Append(result["localRotation.w"], time, mappedRotation.w);
                    Append(result["localScale.x"], time, mappedScale.x);
                    Append(result["localScale.y"], time, mappedScale.y);
                    Append(result["localScale.z"], time, mappedScale.z);
                }
                return new List<AnimationTrackDto>(result.Values);
            }

        }

        private sealed class BoneFrame
        {
            public float px, py, pz;
            public float rx, ry, rz, rw = 1f;
            public float sx = 1f, sy = 1f, sz = 1f;

            public void Assign(string property, string component, float value)
            {
                if (property == "localPosition")
                {
                    if (component == "x") px = value;
                    else if (component == "y") py = value;
                    else if (component == "z") pz = value;
                    return;
                }
                if (property == "localRotation")
                {
                    if (component == "x") rx = value;
                    else if (component == "y") ry = value;
                    else if (component == "z") rz = value;
                    else if (component == "w") rw = value;
                    return;
                }
                if (property == "localScale")
                {
                    if (component == "x") sx = value;
                    else if (component == "y") sy = value;
                    else if (component == "z") sz = value;
                }
            }

        }

        private static Dictionary<string, AnimationTrackDto> InitTrackSet(string path, string targetType)
        {
            return new Dictionary<string, AnimationTrackDto>(StringComparer.Ordinal)
            {
                ["localPosition.x"] = NewTrack(path, targetType, "localPosition", "x"),
                ["localPosition.y"] = NewTrack(path, targetType, "localPosition", "y"),
                ["localPosition.z"] = NewTrack(path, targetType, "localPosition", "z"),
                ["localRotation.x"] = NewTrack(path, targetType, "localRotation", "x"),
                ["localRotation.y"] = NewTrack(path, targetType, "localRotation", "y"),
                ["localRotation.z"] = NewTrack(path, targetType, "localRotation", "z"),
                ["localRotation.w"] = NewTrack(path, targetType, "localRotation", "w"),
                ["localScale.x"] = NewTrack(path, targetType, "localScale", "x"),
                ["localScale.y"] = NewTrack(path, targetType, "localScale", "y"),
                ["localScale.z"] = NewTrack(path, targetType, "localScale", "z"),
            };
        }

        private static AnimationTrackDto NewTrack(string path, string targetType, string property, string component)
        {
            return new AnimationTrackDto
            {
                path = NormalizeBindingPath(path),
                targetType = targetType,
                property = property,
                component = component,
                interpolation = "linear",
                keys = new List<AnimationKeyDto>(),
            };
        }

        private static void Append(AnimationTrackDto track, float time, float value)
        {
            if (track.keys == null)
                track.keys = new List<AnimationKeyDto>();
            track.keys.Add(new AnimationKeyDto { time = time, value = value });
        }

        private static string NormalizeBindingPath(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
                return string.Empty;
            var segments = path
                .Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(SanitizeBindingSegment)
                .Where(v => !string.IsNullOrWhiteSpace(v));
            return string.Join("/", segments);
        }

        private static string SanitizeBindingSegment(string value)
        {
            var text = (value ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(text))
                return "unnamed";
            text = text.Replace('/', '_').Replace('\\', '_');
            return text;
        }

        private static void ClearAllCurves(AnimationClip clip)
        {
            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
                AnimationUtility.SetEditorCurve(clip, binding, null);
            foreach (var binding in AnimationUtility.GetObjectReferenceCurveBindings(clip))
                AnimationUtility.SetObjectReferenceCurve(clip, binding, null);
            AnimationUtility.SetAnimationEvents(clip, Array.Empty<AnimationEvent>());
        }

        private static string ResolveAssetPath(AnimationClipPayload payload)
        {
            var displayName = StripAnimationSemanticSuffix(payload.name);
            if (string.IsNullOrWhiteSpace(displayName))
                displayName = payload.exportContext != null && !string.IsNullOrWhiteSpace(payload.exportContext.sourceObjectName)
                    ? payload.exportContext.sourceObjectName
                    : payload.exportContext?.sourceArmatureName;
            if (string.IsNullOrWhiteSpace(displayName))
                displayName = payload.sourceActionName;
            var safeName = string.IsNullOrWhiteSpace(displayName)
                ? null
                : AssetPathUtility.SanitizeFileName(displayName, "AnimationClip");
            if (string.IsNullOrWhiteSpace(safeName))
                safeName = "AnimationClip";

            var dir = "Assets/TriSync/Resources/AnimationClips";
            EnsureParentFolder(dir + "/placeholder.anim");
            return AssetDatabase.GenerateUniqueAssetPath(Path.Combine(dir, safeName + ".anim").Replace('\\', '/'));
        }

        internal static string StripAnimationSemanticSuffix(string name)
        {
            if (string.IsNullOrWhiteSpace(name))
                return name;

            var result = name.Trim();
            var suffixes = new[]
            {
                "_unity_rig_v1",
                "_unity_object_v1",
            };
            foreach (var suffix in suffixes)
            {
                if (result.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
                    return result.Substring(0, result.Length - suffix.Length).TrimEnd('_', ' ', '-', '.');
            }
            return result;
        }

        private static void EnsureParentFolder(string assetPath)
        {
            var dir = Path.GetDirectoryName(assetPath)?.Replace('\\', '/');
            if (string.IsNullOrWhiteSpace(dir))
                return;
            if (AssetDatabase.IsValidFolder(dir))
                return;

            var parts = dir.Split('/');
            var current = parts[0];
            for (var i = 1; i < parts.Length; i++)
            {
                var next = current + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next))
                    AssetDatabase.CreateFolder(current, parts[i]);
                current = next;
            }
        }

        private static void ApplyWrapModeHint(AnimationClip clip, string wrapModeHint)
        {
            if (clip == null)
                return;

            var normalized = (wrapModeHint ?? string.Empty).Trim().ToLowerInvariant();
            if (normalized == "loop")
            {
                var settings = AnimationUtility.GetAnimationClipSettings(clip);
                settings.loopTime = true;
                AnimationUtility.SetAnimationClipSettings(clip, settings);
            }
            else if (normalized == "none" || normalized == "once" || string.IsNullOrWhiteSpace(normalized))
            {
                var settings = AnimationUtility.GetAnimationClipSettings(clip);
                settings.loopTime = false;
                AnimationUtility.SetAnimationClipSettings(clip, settings);
            }
        }

        private static void ApplyDurationHint(AnimationClip clip, AnimationClipPayload payload)
        {
            if (clip == null || payload == null)
                return;

            var duration = ResolveDurationHint(payload);
            if (!IsFinite(duration) || duration <= 0f)
                return;

            EnsureDurationAnchor(clip, duration);
            var settings = AnimationUtility.GetAnimationClipSettings(clip);
            settings.startTime = 0f;
            settings.stopTime = duration;
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

        private static float ResolveDurationHint(AnimationClipPayload payload)
        {
            var duration = 0f;
            var report = payload.exportReport;
            if (report != null)
            {
                if (IsFinite(report.durationSeconds))
                    duration = Mathf.Max(duration, report.durationSeconds);
                if (report.endFrame > report.startFrame && report.sceneFrameRate > 0f)
                    duration = Mathf.Max(
                        duration,
                        (report.endFrame - report.startFrame) / report.sceneFrameRate);
            }

            if (payload.endFrame > payload.startFrame && payload.frameRate > 0f)
                duration = Mathf.Max(
                    duration,
                    (payload.endFrame - payload.startFrame) / payload.frameRate);

            foreach (var track in payload.tracks ?? new List<AnimationTrackDto>())
            {
                if (track == null || track.keys == null)
                    continue;
                foreach (var key in track.keys)
                {
                    if (key != null && IsFinite(key.time))
                        duration = Mathf.Max(duration, key.time);
                }
            }
            return duration;
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

#endif
    }
}
