using System;
using System.Collections.Generic;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.AnimationClipImport
{
    public sealed class AnimationClipImportCoordinator
    {
        public static AnimationClipPayload LastReceived { get; private set; }
        public static string LastError { get; private set; }

#if UNITY_EDITOR
        private static readonly object PendingLock = new object();
        private static readonly Queue<AnimationClipEnvelope> Pending = new Queue<AnimationClipEnvelope>();
        private static bool _editorHookInstalled;
#endif

        public bool HandleEnvelope(string rawJson)
        {
            LastError = null;
            LastReceived = null;

            try
            {
                var env = JsonUtility.FromJson<AnimationClipEnvelope>(rawJson);
                if (env == null)
                {
                    LastError = "animation_clip_envelope_invalid";
                    LogValidationError("envelope_invalid", "The incoming animation clip envelope was invalid.");
                    ReportImportError(LastError, rawJson);
                    return false;
                }

                NormalizeEnvelope(env);
                if (!string.Equals(env.kind, "animation_clip", StringComparison.Ordinal))
                {
                    LastError = "animation_clip_kind_invalid";
                    LogValidationError("kind_invalid", "The incoming animation payload had an unsupported kind.");
                    ReportImportError(LastError, rawJson);
                    return false;
                }

                if (env.clip == null)
                {
                    LastError = "animation_clip_payload_missing";
                    LogValidationError("payload_missing", "The incoming animation envelope had no clip payload.");
                    ReportImportError(LastError, rawJson);
                    return false;
                }

                if (string.IsNullOrWhiteSpace(env.clip.assetId))
                {
                    LastError = "animation_clip_asset_id_missing";
                    LogValidationError("asset_id_missing", "The incoming animation clip had no asset identifier.");
                    ReportImportError(LastError, rawJson);
                    return false;
                }

                LastReceived = env.clip;
#if UNITY_EDITOR
                EnsureEditorHook();
                lock (PendingLock)
                {
                    Pending.Enqueue(env);
                }
                return true;
#else
                LastError = "animation_clip_import_requires_editor";
                return false;
#endif
            }
            catch (Exception ex)
            {
                LastError = ex.Message;
                BlenderSyncLog.Exception(
                    "AnimationClip",
                    "parse_failed",
                    ex,
                    "Could not parse an incoming animation clip payload.",
                    new Dictionary<string, object>
                    {
                        { "rawLength", string.IsNullOrEmpty(rawJson) ? 0 : rawJson.Length },
                    });
                ReportImportError("parse_failed", rawJson, ex);
                return false;
            }
        }

        private static void LogValidationError(string eventName, string summary)
        {
            BlenderSyncLog.Error("AnimationClip", eventName, summary);
        }

        private static void NormalizeEnvelope(AnimationClipEnvelope env)
        {
            if (env == null)
                return;

            env.kind = NormalizeOptional(env.kind);
            if (env.clip == null)
                return;

            env.clip.assetId = NormalizeOptional(env.clip.assetId);
            env.clip.bindingSpace = NormalizeOptional(env.clip.bindingSpace);
            env.clip.channelSemantic = NormalizeOptional(env.clip.channelSemantic);
            env.clip.wrapModeHint = NormalizeOptional(env.clip.wrapModeHint);
        }

        private static string NormalizeOptional(string value)
        {
            var normalized = value?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

#if UNITY_EDITOR
        private static void EnsureEditorHook()
        {
            lock (PendingLock)
            {
                if (_editorHookInstalled)
                    return;
                EditorApplication.update += PumpEditorQueue;
                _editorHookInstalled = true;
            }
        }

        private static void PumpEditorQueue()
        {
            AnimationClipEnvelope env = null;
            lock (PendingLock)
            {
                if (Pending.Count > 0)
                    env = Pending.Dequeue();
            }

            if (env == null || env.clip == null)
                return;

            try
            {
                var build = new AnimationClipBuilder().BuildOrUpdateClip(env.clip);
                var clipName = string.IsNullOrWhiteSpace(build.clipName) ? env.clip.name : build.clipName;

                if (!build.success)
                {
                    LastError = build.error ?? "animation_clip_build_failed";
                    BlenderSyncLog.Error(
                        "AnimationClip",
                        "build_failed",
                        LastError,
                        new Dictionary<string, object>
                        {
                            { "assetId", env.clip.assetId },
                            { "clipName", clipName },
                        });
                    ReportClipBuildFailure(env.clip.assetId, clipName, build.assetPath, LastError);
                    return;
                }

                LastError = null;
                var report = env.clip.exportReport;
                var rootMotion = env.clip.rootMotion;
                BlenderSyncReportStore.Add(
                    "Animation Clip",
                    "OK",
                    $"{build.operation} clip={clipName} tracks={build.trackCount} path={build.assetPath}",
                    new Dictionary<string, object>
                    {
                        { "operation", build.operation },
                        { "assetId", env.clip.assetId },
                        { "clipName", clipName },
                        { "path", build.assetPath },
                        { "tracks", build.trackCount },
                        { "frameRate", env.clip.frameRate },
                        { "animationSource", env.clip.exportSettings != null ? env.clip.exportSettings.animationSource : "" },
                        { "startFrame", env.clip.startFrame },
                        { "endFrame", env.clip.endFrame },
                        { "bindingSpace", env.clip.bindingSpace },
                        { "channelSemantic", env.clip.channelSemantic },
                        { "keyCountBeforeSimplify", report != null ? report.keyCountBeforeSimplify : 0 },
                        { "keyCountAfterSimplify", report != null ? report.keyCountAfterSimplify : 0 },
                        { "keys", report != null ? report.keyCount : 0 },
                        { "samples", report != null ? report.sampleCount : 0 },
                        { "simplify", report != null ? report.simplify : "" },
                        { "simplifyRemovedKeys", report != null ? report.simplifyRemovedKeyCount : 0 },
                        { "staticCurves", report != null ? report.staticCurves : "" },
                        { "staticCurveRemovedKeys", report != null ? report.staticCurveRemovedKeyCount : 0 },
                        { "collapsedStaticTracks", report != null ? report.collapsedStaticTrackCount : 0 },
                        { "removedKeys", report != null ? report.removedKeyCount : 0 },
                        { "rootMotionEnabled", rootMotion != null && rootMotion.enabled },
                        { "rootMotionSource", rootMotion != null ? rootMotion.source : "" },
                        { "rootMotionBone", rootMotion != null ? rootMotion.sourceBone : "" },
                        { "rootMotionSamples", rootMotion != null ? rootMotion.sampleCount : 0 },
                        { "rootMotionDistance", rootMotion != null ? rootMotion.totalDistance.ToString("F3") : "0" },
                        { "rootMotionYawDegrees", rootMotion != null ? rootMotion.totalYawDegrees.ToString("F3") : "0" },
                        { "rootMotionPolicy", rootMotion != null ? rootMotion.applyPolicy : "" },
                        { "rootMotionError", rootMotion != null ? rootMotion.error : "" },
                    });
            }
            catch (Exception ex)
            {
                LastError = ex.Message;
                BlenderSyncLog.Exception(
                    "AnimationClip",
                    "queued_import_failed",
                    ex,
                    "A queued animation clip import failed.");
                BlenderSyncReportStore.Add("Animation Clip", "ERROR", "queued_import_failed", new Dictionary<string, object>
                {
                    { "exceptionType", ex.GetType().Name },
                    { "error", ex.Message },
                });
            }
        }

        private static void ReportImportError(string summary, string rawJson, Exception ex = null)
        {
            BlenderSyncReportStore.Add("Animation Clip", "ERROR", summary, new Dictionary<string, object>
            {
                { "rawLength", string.IsNullOrEmpty(rawJson) ? 0 : rawJson.Length },
                { "exceptionType", ex?.GetType().Name ?? string.Empty },
                { "error", ex?.Message ?? string.Empty },
            });
        }

        private static void ReportClipBuildFailure(string assetId, string clipName, string assetPath, string error)
        {
            BlenderSyncReportStore.Add("Animation Clip", "ERROR", "animation_clip_build_failed", new Dictionary<string, object>
            {
                { "assetId", assetId ?? string.Empty },
                { "clipName", clipName ?? string.Empty },
                { "path", assetPath ?? string.Empty },
                { "error", error ?? string.Empty },
            });
        }
#endif
    }
}
