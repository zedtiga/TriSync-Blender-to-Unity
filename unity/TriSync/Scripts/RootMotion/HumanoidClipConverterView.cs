#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using BlenderSyncVNext.Diagnostics;
using Unity.Collections;
using UnityEditor;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;

namespace BlenderSyncVNext.RootMotion
{
    internal sealed class HumanoidClipConverterView
    {
        private const int HumanoidGoalCount = 4;

        private static readonly GoalSpec[] GoalSpecs =
        {
            new GoalSpec("LeftFoot", AvatarIKGoal.LeftFoot),
            new GoalSpec("RightFoot", AvatarIKGoal.RightFoot),
            new GoalSpec("LeftHand", AvatarIKGoal.LeftHand),
            new GoalSpec("RightHand", AvatarIKGoal.RightHand),
        };

        private Animator _sourceAnimator;
        private GameObject _bindingRoot;
        private AnimationClip _sourceClip;
        private Avatar _sourceAvatar;
        private string _outputFolder = "Assets/TriSync/Resources/AnimationClips";
        private string _suffix = "_Humanoid";
        private HumanoidCurveReductionPreset _keyReduction = HumanoidCurveReductionPreset.Light;
        private HumanoidStaticCurveMode _staticCurves = HumanoidStaticCurveMode.Off;
        private bool _showOutput;
        private Vector2 _scroll;
        private string _lastResult;
        private string _bindingRootWarning;
        private bool _initialized;

        internal void Activate()
        {
            if (_initialized)
                return;
            _initialized = true;
            AssignSelectedAnimatorOnActivate();
        }

        internal void Draw()
        {
            DrawCharacterSection();

            _scroll = EditorGUILayout.BeginScrollView(_scroll, GUILayout.ExpandHeight(true));
            try
            {
                DrawSourceClipsSection();
                EditorGUILayout.Space(6f);
                DrawOutputFoldout();
            }
            finally
            {
                EditorGUILayout.EndScrollView();
            }

            DrawConvertFooter();
        }

        private void DrawCharacterSection()
        {
            EditorGUILayout.LabelField(Tr("Character"), EditorStyles.boldLabel);
            using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
            {
                EditorGUI.BeginChangeCheck();
                var animator = (Animator)EditorGUILayout.ObjectField(
                    new GUIContent(
                        Tr("Animator"),
                        Tr("Humanoid Animator used to sample the source clip.")),
                    _sourceAnimator,
                    typeof(Animator),
                    true);
                if (EditorGUI.EndChangeCheck())
                    AssignSourceAnimator(animator);

                EditorGUI.BeginChangeCheck();
                var avatar = (Avatar)EditorGUILayout.ObjectField(
                    new GUIContent(
                        Tr("Avatar"),
                        Tr("Humanoid Avatar used to interpret the source clip. It is filled from the selected Animator, but can be changed manually.")),
                    _sourceAvatar,
                    typeof(Avatar),
                    false);
                if (EditorGUI.EndChangeCheck())
                {
                    _sourceAvatar = avatar;
                    ClearLastResult();
                }

                EditorGUI.BeginChangeCheck();
                var bindingRoot = (GameObject)EditorGUILayout.ObjectField(
                    new GUIContent(
                        Tr("Root Node"),
                        Tr("Animator GameObject or descendant used to resolve and sample source clip Transform bindings.")),
                    _bindingRoot,
                    typeof(GameObject),
                    true);
                if (EditorGUI.EndChangeCheck())
                {
                    _bindingRoot = bindingRoot;
                    RefreshBindingRootWarning();
                    ClearLastResult();
                }
            }
        }

        private void DrawSourceClipsSection()
        {
            EditorGUILayout.LabelField(Tr("Source Clip"), EditorStyles.boldLabel);
            using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
            {
                EditorGUI.BeginChangeCheck();
                var clip = (AnimationClip)EditorGUILayout.ObjectField(
                    new GUIContent(
                        Tr("Clip"),
                        Tr("Generic or raw AnimationClip to convert with the selected Animator and Avatar.")),
                    _sourceClip,
                    typeof(AnimationClip),
                    false);
                if (EditorGUI.EndChangeCheck())
                    AssignSourceClip(clip);

                if (_sourceClip == null)
                    EditorGUILayout.LabelField(Tr("Assign one source clip to convert."));
                else if (IsBindingRootValid() && !string.IsNullOrEmpty(_bindingRootWarning))
                    EditorGUILayout.HelpBox(LocalizedBindingRootWarning(_bindingRoot, _sourceClip), MessageType.Warning);
            }
        }

        private void DrawOutputFoldout()
        {
            _showOutput = EditorGUILayout.Foldout(
                _showOutput,
                Tr("Output"),
                true,
                EditorStyles.foldoutHeader);
            if (!_showOutput)
                return;

            EditorGUI.BeginChangeCheck();
            _outputFolder = DrawFolderField(Tr("Clip Folder"), _outputFolder);
            _suffix = EditorGUILayout.TextField(Tr("Output Suffix"), _suffix);
            _keyReduction = (HumanoidCurveReductionPreset)EditorGUILayout.Popup(
                new GUIContent(
                    Tr("Key Reduction"),
                    Tr("Reduce baked Humanoid curve keys within a bounded error after sampling.")),
                (int)_keyReduction,
                new[]
                {
                    Tr("Off"),
                    Tr("Light"),
                    Tr("Medium"),
                    Tr("Aggressive"),
                });
            _staticCurves = (HumanoidStaticCurveMode)EditorGUILayout.Popup(
                new GUIContent(
                    Tr("Static Curves"),
                    Tr("Collapse numerically constant Animator curves to one key while keeping every binding.")),
                (int)_staticCurves,
                new[]
                {
                    Tr("Off"),
                    Tr("Collapse Constant"),
                });
            if (EditorGUI.EndChangeCheck())
                ClearLastResult();
        }

        private void DrawConvertFooter()
        {
            var avatar = ResolveAvatar();
            var status = ConversionStatusLabel(
                _sourceAnimator != null,
                _bindingRoot != null,
                IsBindingRootValid(),
                avatar != null,
                avatar != null && avatar.isValid,
                avatar != null && avatar.isHuman,
                _sourceClip != null);

            EditorGUILayout.Space(4f);
            EditorGUILayout.LabelField(
                Tr(string.IsNullOrEmpty(_lastResult) ? status : _lastResult),
                EditorStyles.label);
            using (new EditorGUI.DisabledScope(!CanConvert()))
            {
                if (GUILayout.Button(
                        Tr(ConversionActionLabel()),
                        GUILayout.Height(30f)))
                    RootMotionEditorActions.Execute(
                        "Generic To Humanoid",
                        "Convert to Humanoid",
                        "Generic To Humanoid",
                        Convert);
            }
        }

        private void AssignSelectedAnimatorOnActivate()
        {
            var selected = Selection.activeGameObject;
            if (selected == null)
                return;

            var animator = selected.GetComponentInParent<Animator>() ?? selected.GetComponentInChildren<Animator>();
            if (animator == null)
                return;
            AssignSourceAnimator(animator);
        }

        private void AssignSourceAnimator(Animator animator)
        {
            _sourceAnimator = animator;
            _sourceAvatar = animator != null ? animator.avatar : null;
            _bindingRoot = null;
            RefreshBindingRootWarning();
            ClearLastResult();
        }

        private void AssignSourceClip(AnimationClip clip)
        {
            _sourceClip = clip;
            RefreshBindingRootWarning();
            ClearLastResult();
        }

        private static string DrawFolderField(string label, string value)
        {
            var lineHeight = EditorGUIUtility.singleLineHeight;
            using (new EditorGUILayout.HorizontalScope(GUILayout.Height(lineHeight)))
            {
                value = EditorGUILayout.TextField(label, value, GUILayout.Height(lineHeight));
                var folderIcon = EditorGUIUtility.IconContent("Folder Icon");
                if (GUILayout.Button(
                        new GUIContent(folderIcon.image, Tr("Choose an output folder under Assets.")),
                        GUILayout.Width(28f),
                        GUILayout.Height(lineHeight)))
                    value = ChooseOutputFolder(value);
            }
            return value;
        }

        private static string ChooseOutputFolder(string current)
        {
            var selected = EditorUtility.OpenFolderPanel(
                Tr("Select Humanoid Clip Folder"),
                Application.dataPath,
                string.Empty);
            if (string.IsNullOrWhiteSpace(selected))
                return current;

            var relative = FileUtil.GetProjectRelativePath(selected).Replace('\\', '/').TrimEnd('/');
            if (relative == "Assets" || relative.StartsWith("Assets/", System.StringComparison.Ordinal))
                return relative;

            EditorUtility.DisplayDialog(
                Tr("Generic -> Humanoid"),
                Tr("Choose a folder inside this project's Assets directory."),
                Tr("OK"));
            return current;
        }

        private void ClearLastResult()
        {
            _lastResult = null;
        }

        private static string ConversionStatusLabel(
            bool hasAnimator,
            bool hasBindingRoot,
            bool bindingRootValid,
            bool hasAvatar,
            bool avatarValid,
            bool avatarHuman,
            bool hasSourceClip)
        {
            if (!hasAnimator)
                return "Assign a Humanoid Animator to begin.";
            if (!hasAvatar)
                return "Assign a Humanoid Avatar.";
            if (!avatarValid || !avatarHuman)
                return "The selected Avatar must be valid and Humanoid.";
            if (!hasBindingRoot)
                return "Assign a Root Node.";
            if (!bindingRootValid)
                return "Root Node must be under the Animator hierarchy.";
            if (!hasSourceClip)
                return "Assign a source AnimationClip.";
            return "Ready to convert.";
        }

        private static string ConversionActionLabel()
        {
            return "Convert to Humanoid";
        }

        private static string ConversionResultLabel(bool failed, bool warning)
        {
            if (failed)
                return "Humanoid conversion failed; see Diagnostics.";
            if (warning)
                return "Converted Humanoid clip with a warning; see Diagnostics.";
            return "Converted Humanoid clip.";
        }

        private Avatar ResolveAvatar()
        {
            return _sourceAvatar;
        }

        private void RefreshBindingRootWarning()
        {
            _bindingRootWarning = null;
            if (_sourceAnimator == null ||
                _bindingRoot == null ||
                _sourceClip == null ||
                !IsTransformUnderRoot(_sourceAnimator.transform, _bindingRoot.transform))
                return;

            _bindingRootWarning = BindingRootWarning(_bindingRoot.transform, _sourceClip);
        }

        private static string BindingRootWarning(Transform root, AnimationClip clip)
        {
            if (root == null || clip == null)
                return null;

            var paths = AnimationUtility.GetCurveBindings(clip)
                .Where(b => b.type == typeof(Transform) && !string.IsNullOrEmpty(b.path))
                .Select(b => b.path)
                .Distinct()
                .ToArray();
            if (paths.Length == 0)
                return null;

            var unresolved = paths.Count(path => ResolveBindingTransform(root, path) == null);
            return unresolved > 0
                ? $"Root Node cannot resolve {unresolved} of {paths.Length} Transform paths in the Source Clip. Unresolved curves will be omitted."
                : null;
        }

        private static string LocalizedBindingRootWarning(GameObject root, AnimationClip clip)
        {
            if (root == null || clip == null)
                return null;
            var paths = AnimationUtility.GetCurveBindings(clip)
                .Where(b => b.type == typeof(Transform) && !string.IsNullOrEmpty(b.path))
                .Select(b => b.path)
                .Distinct()
                .ToArray();
            var unresolved = paths.Count(path => ResolveBindingTransform(root.transform, path) == null);
            return unresolved > 0
                ? Format(
                    "Root Node cannot resolve {0} of {1} Transform paths in the Source Clip. Unresolved curves will be omitted.",
                    unresolved,
                    paths.Length)
                : null;
        }

        private GameObject ResolveSampleRoot()
        {
            return _bindingRoot;
        }

        private bool CanConvert()
        {
            var avatar = ResolveAvatar();
            return _sourceAnimator != null &&
                   _bindingRoot != null &&
                   IsBindingRootValid() &&
                   _sourceClip != null &&
                   avatar != null &&
                   avatar.isValid &&
                   avatar.isHuman;
        }

        private bool IsBindingRootValid()
        {
            return _sourceAnimator != null &&
                   _bindingRoot != null &&
                   IsTransformUnderRoot(_sourceAnimator.transform, _bindingRoot.transform);
        }

        private static bool IsTransformUnderRoot(Transform root, Transform target)
        {
            if (root == null || target == null)
                return false;
            for (var current = target; current != null; current = current.parent)
                if (current == root)
                    return true;
            return false;
        }

        private void Convert()
        {
            var avatar = ResolveAvatar();
            if (!CanConvert())
            {
                EditorUtility.DisplayDialog(
                    Tr("Generic -> Humanoid"),
                    Tr("A Humanoid Animator, a valid Humanoid Avatar, a valid Root Node, and a source AnimationClip are required."),
                    Tr("OK"));
                return;
            }

            var report = ConvertClip(_sourceClip, avatar);
            var failed = !string.IsNullOrEmpty(report) && report.Contains(" ERROR ");
            var warning = !failed && !string.IsNullOrEmpty(report) && report.Contains("outputHumanMotion=False");
            _lastResult = ConversionResultLabel(failed, warning);
        }

        private string ConvertClip(AnimationClip sourceClip, Avatar avatar)
        {
            if (!RootMotionAssetPaths.TryEnsureAssetFolder(ref _outputFolder, "Generic To Humanoid", "Generic To Humanoid"))
                return "[vNext][GenericToHumanoid] ERROR invalid_output_folder";
            var outputPath = GetOutputPath(sourceClip);
            var output = new AnimationClip
            {
                frameRate = ResolveSampleFps(sourceClip),
                name = Path.GetFileNameWithoutExtension(outputPath)
            };
            output.legacy = false;

            var sampleRoot = ResolveSampleRoot();
            var sampleFps = ResolveSampleFps(sourceClip);
            var result = SampleToHumanoidCurves(
                _sourceAnimator,
                sampleRoot,
                avatar,
                sourceClip,
                output,
                sampleFps);
            var reduction = HumanoidCurveReducer.Reduce(output, _keyReduction, _staticCurves);
            AnimationUtility.SetAnimationEvents(output, AnimationUtility.GetAnimationEvents(sourceClip));
            AssetDatabase.CreateAsset(output, outputPath);
            AssetDatabase.SaveAssets();
            AssetDatabase.ImportAsset(outputPath);
            var reloaded = AssetDatabase.LoadAssetAtPath<AnimationClip>(outputPath);
            if (reloaded == null)
            {
                var errorMessage = $"clip_asset_save_failed outputPath={outputPath}";
                BlenderSyncReportStore.Add("Generic To Humanoid", "ERROR", errorMessage, new Dictionary<string, object>
                {
                    { "sourceAnimator", _sourceAnimator.name },
                    { "sourceClip", sourceClip.name },
                    { "avatar", avatar.name },
                    { "outputPath", outputPath },
                });
                return $"[vNext][GenericToHumanoid] ERROR {errorMessage}";
            }
            var bindings = AnimationUtility.GetCurveBindings(reloaded);
            var rootT = CountProperties(bindings, "RootT");
            var rootQ = CountProperties(bindings, "RootQ");
            var goalT = CountGoalProperties(bindings, "T");
            var goalQ = CountGoalProperties(bindings, "Q");
            var muscleHits = CountHumanTraitMuscleBindings(bindings);
            var reductionPercent = reduction.KeysBefore > 0
                ? 100f * (reduction.KeysBefore - reduction.KeysAfter) / reduction.KeysBefore
                : 0f;
            var reductionName = _keyReduction.ToString().ToLowerInvariant();
            var staticCurveName = _staticCurves.ToString().ToLowerInvariant();
            var message = $"converted={outputPath} humanMotion={(reloaded != null && reloaded.humanMotion)} samples={result.sampleCount} fps={sampleFps:0.###} rootT={rootT} rootQ={rootQ} muscles={muscleHits} goalT={goalT} goalQ={goalQ} handFootIkCurves=True goalQCalibration={result.goalQCalibration} varyingMuscles={result.varyingMuscles} keyReduction={reductionName} staticCurves={staticCurveName} collapsedStaticTracks={reduction.CollapsedStaticTrackCount} keysBefore={reduction.KeysBefore} keysAfter={reduction.KeysAfter} reductionPercent={reductionPercent:0.##}";

            BlenderSyncReportStore.Add(
                "Generic To Humanoid",
                reloaded != null && reloaded.humanMotion ? "OK" : "WARN",
                message,
                new Dictionary<string, object>
                {
                    { "sourceAnimator", _sourceAnimator.name },
                    { "sampleRoot", sampleRoot != null ? sampleRoot.name : "(null)" },
                    { "sourceClip", sourceClip.name },
                    { "sourceHumanMotion", sourceClip.humanMotion },
                    { "avatar", avatar.name },
                    { "avatarValid", avatar.isValid },
                    { "avatarHuman", avatar.isHuman },
                    { "outputPath", outputPath },
                    { "outputHumanMotion", reloaded != null && reloaded.humanMotion },
                    { "sampleFps", sampleFps },
                    { "duration", sourceClip.length },
                    { "sampleCount", result.sampleCount },
                    { "keyReduction", reductionName },
                    { "staticCurves", staticCurveName },
                    { "collapsedStaticTracks", reduction.CollapsedStaticTrackCount },
                    { "keysBefore", reduction.KeysBefore },
                    { "keysAfter", reduction.KeysAfter },
                    { "reductionPercent", reductionPercent },
                    { "varyingMuscles", result.varyingMuscles },
                    { "varyingRootT", result.varyingRootT },
                    { "varyingRootQ", result.varyingRootQ },
                    { "rootT", rootT },
                    { "rootQ", rootQ },
                    { "goalT", goalT },
                    { "goalQ", goalQ },
                    { "handFootIkCurves", true },
                    { "goalQCalibration", result.goalQCalibration },
                    { "muscleCurves", muscleHits },
                    { "curveBindings", bindings.Length },
                });

            SelectGeneratedClip(reloaded);

            return
                $"[vNext][GenericToHumanoid] {message}\n" +
                $"sourceAnimator={_sourceAnimator.name}\n" +
                $"sampleRoot={(sampleRoot != null ? sampleRoot.name : "(null)")}\n" +
                $"sourceClip={sourceClip.name} sourceHumanMotion={sourceClip.humanMotion} length={sourceClip.length:0.###}\n" +
                $"avatar={avatar.name} valid={avatar.isValid} human={avatar.isHuman}\n" +
                $"outputClip={(reloaded != null ? reloaded.name : "(null)")} outputHumanMotion={(reloaded != null && reloaded.humanMotion)} curveBindings={bindings.Length}\n" +
                $"variation varyingRootT={result.varyingRootT} varyingRootQ={result.varyingRootQ} varyingMuscles={result.varyingMuscles}\n" +
                $"keyReduction={reductionName} staticCurves={staticCurveName} collapsedStaticTracks={reduction.CollapsedStaticTrackCount} keysBefore={reduction.KeysBefore} keysAfter={reduction.KeysAfter} reductionPercent={reductionPercent:0.##}\n" +
                $"handFootIkCurves required=True goalT={goalT} goalQ={goalQ} calibration={result.goalQCalibration}";
        }

        private string GetOutputPath(AnimationClip sourceClip)
        {
            var baseName = RootMotionAssetPaths.SanitizeFileName(sourceClip != null ? sourceClip.name : "HumanoidClip", "HumanoidClip");
            var suffix = RootMotionAssetPaths.SanitizeFileName(_suffix, string.Empty);
            return AssetDatabase.GenerateUniqueAssetPath($"{_outputFolder}/{baseName}{suffix}.anim");
        }

        private static float ResolveSampleFps(AnimationClip clip)
        {
            if (clip != null && clip.frameRate > 0f)
                return clip.frameRate;
            return 60f;
        }

        private static ConversionResult SampleToHumanoidCurves(
            Animator animator,
            GameObject sampleRoot,
            Avatar avatar,
            AnimationClip source,
            AnimationClip output,
            float sampleFps)
        {
            var duration = Mathf.Max(0f, source.length);
            var sampleCount = Mathf.Max(2, Mathf.CeilToInt(duration * sampleFps) + 1);
            var rootTX = new List<Keyframe>(sampleCount);
            var rootTY = new List<Keyframe>(sampleCount);
            var rootTZ = new List<Keyframe>(sampleCount);
            var rootQX = new List<Keyframe>(sampleCount);
            var rootQY = new List<Keyframe>(sampleCount);
            var rootQZ = new List<Keyframe>(sampleCount);
            var rootQW = new List<Keyframe>(sampleCount);
            var muscleKeys = new List<Keyframe>[HumanTrait.MuscleCount];
            for (var i = 0; i < muscleKeys.Length; i++)
                muscleKeys[i] = new List<Keyframe>(sampleCount);
            var goalKeys = CreateGoalKeySets(sampleCount);

            var pose = new HumanPose { muscles = new float[HumanTrait.MuscleCount] };
            var handler = new HumanPoseHandler(avatar, animator.transform);
            var sampleTransform = sampleRoot != null ? sampleRoot.transform : animator.transform;
            var curveSets = BuildTransformCurveSets(sampleTransform, source);
            var originals = CaptureHierarchyOriginals(sampleTransform);
#if !UNITY_6000_1_OR_NEWER
            LegacyGoalSampler legacyGoalSampler = null;
#endif
            try
            {
#if !UNITY_6000_1_OR_NEWER
                legacyGoalSampler = new LegacyGoalSampler(animator, avatar);
#endif
                for (var i = 0; i < sampleCount; i++)
                {
                    var time = sampleCount <= 1 ? 0f : Mathf.Min(duration, i / sampleFps);
                    if (i == sampleCount - 1)
                        time = duration;

                    RestoreOriginals(originals);
                    ApplyTransformCurveSets(curveSets, time);
                    handler.GetHumanPose(ref pose);

                    AddKey(rootTX, time, pose.bodyPosition.x);
                    AddKey(rootTY, time, pose.bodyPosition.y);
                    AddKey(rootTZ, time, pose.bodyPosition.z);
                    var q = pose.bodyRotation;
                    if (i > 0 && Quaternion.Dot(new Quaternion(rootQX[rootQX.Count - 1].value, rootQY[rootQY.Count - 1].value, rootQZ[rootQZ.Count - 1].value, rootQW[rootQW.Count - 1].value), q) < 0f)
                        q = new Quaternion(-q.x, -q.y, -q.z, -q.w);
                    AddKey(rootQX, time, q.x);
                    AddKey(rootQY, time, q.y);
                    AddKey(rootQZ, time, q.z);
                    AddKey(rootQW, time, q.w);

                    for (var m = 0; m < HumanTrait.MuscleCount; m++)
                        AddKey(muscleKeys[m], time, pose.muscles != null && m < pose.muscles.Length ? pose.muscles[m] : 0f);

#if UNITY_6000_1_OR_NEWER
                    AddGoalKeys(goalKeys, pose, time);
#else
                    legacyGoalSampler.Sample();
                    for (var goalIndex = 0; goalIndex < GoalSpecs.Length; goalIndex++)
                        AddGoalKey(
                            goalKeys[goalIndex],
                            legacyGoalSampler.GetPosition(goalIndex),
                            legacyGoalSampler.GetRotation(goalIndex),
                            time);
#endif
                }
            }
            finally
            {
#if !UNITY_6000_1_OR_NEWER
                legacyGoalSampler?.Dispose();
#endif
                RestoreOriginals(originals);
                handler.Dispose();
            }

            NormalizeRootHorizontalOrigin(rootTX, rootTZ);
            SetAnimatorCurve(output, "RootT.x", rootTX);
            SetAnimatorCurve(output, "RootT.y", rootTY);
            SetAnimatorCurve(output, "RootT.z", rootTZ);
            SetAnimatorCurve(output, "RootQ.x", rootQX);
            SetAnimatorCurve(output, "RootQ.y", rootQY);
            SetAnimatorCurve(output, "RootQ.z", rootQZ);
            SetAnimatorCurve(output, "RootQ.w", rootQW);
            for (var m = 0; m < HumanTrait.MuscleCount; m++)
                SetAnimatorCurve(output, ToHumanoidCurvePropertyName(HumanTrait.MuscleName[m]), muscleKeys[m]);
            var goalQCalibration = ApplyGoalQUnityBasisCalibration(goalKeys);
            WriteGoalCurves(output, goalKeys);

            var varyingRootT = IsVarying(rootTX) || IsVarying(rootTY) || IsVarying(rootTZ);
            var varyingRootQ = IsVarying(rootQX) || IsVarying(rootQY) || IsVarying(rootQZ) || IsVarying(rootQW);
            var varyingMuscles = 0;
            for (var m = 0; m < muscleKeys.Length; m++)
                if (IsVarying(muscleKeys[m]))
                    varyingMuscles++;
            return new ConversionResult(
                sampleCount,
                duration,
                varyingRootT,
                varyingRootQ,
                varyingMuscles,
                goalQCalibration);
        }

        private static void NormalizeRootHorizontalOrigin(List<Keyframe> rootTX, List<Keyframe> rootTZ)
        {
            if (rootTX == null || rootTZ == null || rootTX.Count == 0 || rootTZ.Count == 0)
                return;
            var offsetX = rootTX[0].value;
            var offsetZ = rootTZ[0].value;
            if (Mathf.Abs(offsetX) <= 1e-6f && Mathf.Abs(offsetZ) <= 1e-6f)
                return;
            for (var i = 0; i < rootTX.Count; i++)
                ReplaceKeyValue(rootTX, i, rootTX[i].value - offsetX);
            for (var i = 0; i < rootTZ.Count; i++)
                ReplaceKeyValue(rootTZ, i, rootTZ[i].value - offsetZ);
        }

        private readonly struct ConversionResult
        {
            public readonly int sampleCount;
            public readonly float duration;
            public readonly bool varyingRootT;
            public readonly bool varyingRootQ;
            public readonly int varyingMuscles;
            public readonly string goalQCalibration;

            public ConversionResult(
                int sampleCount,
                float duration,
                bool varyingRootT,
                bool varyingRootQ,
                int varyingMuscles,
                string goalQCalibration)
            {
                this.sampleCount = sampleCount;
                this.duration = duration;
                this.varyingRootT = varyingRootT;
                this.varyingRootQ = varyingRootQ;
                this.varyingMuscles = varyingMuscles;
                this.goalQCalibration = goalQCalibration;
            }
        }

        private static GoalKeySet[] CreateGoalKeySets(int sampleCount)
        {
            var sets = new GoalKeySet[GoalSpecs.Length];
            for (var i = 0; i < sets.Length; i++)
                sets[i] = new GoalKeySet(GoalSpecs[i].Prefix, sampleCount);
            return sets;
        }

#if UNITY_6000_1_OR_NEWER
        private static void AddGoalKeys(GoalKeySet[] goalKeys, HumanPose pose, float time)
        {
            var positions = pose.ikGoalPositions;
            var rotations = pose.ikGoalRotations;
            for (var i = 0; i < GoalSpecs.Length; i++)
            {
                var goalIndex = (int)GoalSpecs[i].Goal;
                var p = goalIndex >= 0 && goalIndex < positions.Length ? positions[goalIndex] : Vector3.zero;
                var q = goalIndex >= 0 && goalIndex < rotations.Length ? Normalize(rotations[goalIndex]) : Quaternion.identity;
                AddGoalKey(goalKeys[i], p, q, time);
            }
        }
#endif

        private static void AddGoalKey(GoalKeySet goalKeys, Vector3 position, Quaternion rotation, float time)
        {
            var q = Normalize(rotation);
            if (goalKeys.QX.Count > 0)
            {
                var previous = new Quaternion(
                    goalKeys.QX[goalKeys.QX.Count - 1].value,
                    goalKeys.QY[goalKeys.QY.Count - 1].value,
                    goalKeys.QZ[goalKeys.QZ.Count - 1].value,
                    goalKeys.QW[goalKeys.QW.Count - 1].value);
                if (Quaternion.Dot(previous, q) < 0f)
                    q = new Quaternion(-q.x, -q.y, -q.z, -q.w);
            }
            AddKey(goalKeys.TX, time, position.x);
            AddKey(goalKeys.TY, time, position.y);
            AddKey(goalKeys.TZ, time, position.z);
            AddKey(goalKeys.QX, time, q.x);
            AddKey(goalKeys.QY, time, q.y);
            AddKey(goalKeys.QZ, time, q.z);
            AddKey(goalKeys.QW, time, q.w);
        }

        private static string ApplyGoalQUnityBasisCalibration(GoalKeySet[] goalKeys)
        {
            for (var i = 0; i < goalKeys.Length; i++)
            {
                var postOffset = UnityGoalQPostOffset(GoalSpecs[i].Goal);
                for (var k = 0; k < goalKeys[i].QX.Count; k++)
                {
                    var q = Normalize(goalKeys[i].EvaluateQ(k) * postOffset);
                    if (k > 0)
                    {
                        var previous = new Quaternion(
                            goalKeys[i].QX[k - 1].value,
                            goalKeys[i].QY[k - 1].value,
                            goalKeys[i].QZ[k - 1].value,
                            goalKeys[i].QW[k - 1].value);
                        if (Quaternion.Dot(previous, q) < 0f)
                            q = new Quaternion(-q.x, -q.y, -q.z, -q.w);
                    }
                    ReplaceKeyValue(goalKeys[i].QX, k, q.x);
                    ReplaceKeyValue(goalKeys[i].QY, k, q.y);
                    ReplaceKeyValue(goalKeys[i].QZ, k, q.z);
                    ReplaceKeyValue(goalKeys[i].QW, k, q.w);
                }
            }

            return "unity_goal_basis_v1";
        }

        private static Quaternion UnityGoalQPostOffset(AvatarIKGoal goal)
        {
            switch (goal)
            {
                case AvatarIKGoal.LeftFoot:
                case AvatarIKGoal.RightFoot:
                    return Quaternion.Euler(0f, 90f, -90f);
                case AvatarIKGoal.LeftHand:
                    return Quaternion.Euler(0f, 90f, 180f);
                case AvatarIKGoal.RightHand:
                    return Quaternion.Euler(0f, -90f, 0f);
                default:
                    return Quaternion.identity;
            }
        }

        private static void WriteGoalCurves(AnimationClip output, GoalKeySet[] goalKeys)
        {
            foreach (var set in goalKeys)
            {
                SetAnimatorCurve(output, $"{set.Prefix}T.x", set.TX);
                SetAnimatorCurve(output, $"{set.Prefix}T.y", set.TY);
                SetAnimatorCurve(output, $"{set.Prefix}T.z", set.TZ);
                SetAnimatorCurve(output, $"{set.Prefix}Q.x", set.QX);
                SetAnimatorCurve(output, $"{set.Prefix}Q.y", set.QY);
                SetAnimatorCurve(output, $"{set.Prefix}Q.z", set.QZ);
                SetAnimatorCurve(output, $"{set.Prefix}Q.w", set.QW);
            }
        }

        private static Quaternion Normalize(Quaternion q)
        {
            var len = Mathf.Sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
            return len > 1e-6f ? new Quaternion(q.x / len, q.y / len, q.z / len, q.w / len) : Quaternion.identity;
        }

        private static void ReplaceKeyValue(List<Keyframe> keys, int index, float value)
        {
            var key = keys[index];
            key.value = value;
            keys[index] = key;
        }

        private readonly struct GoalSpec
        {
            public readonly string Prefix;
            public readonly AvatarIKGoal Goal;

            public GoalSpec(string prefix, AvatarIKGoal goal)
            {
                Prefix = prefix;
                Goal = goal;
            }
        }

        private sealed class GoalKeySet
        {
            public readonly string Prefix;
            public readonly List<Keyframe> TX;
            public readonly List<Keyframe> TY;
            public readonly List<Keyframe> TZ;
            public readonly List<Keyframe> QX;
            public readonly List<Keyframe> QY;
            public readonly List<Keyframe> QZ;
            public readonly List<Keyframe> QW;

            public GoalKeySet(string prefix, int capacity)
            {
                Prefix = prefix;
                TX = new List<Keyframe>(capacity);
                TY = new List<Keyframe>(capacity);
                TZ = new List<Keyframe>(capacity);
                QX = new List<Keyframe>(capacity);
                QY = new List<Keyframe>(capacity);
                QZ = new List<Keyframe>(capacity);
                QW = new List<Keyframe>(capacity);
            }

            public Quaternion EvaluateQ(int index)
            {
                return Normalize(new Quaternion(QX[index].value, QY[index].value, QZ[index].value, QW[index].value));
            }
        }

        private sealed class LegacyGoalSampler : IDisposable
        {
            private readonly Animator _animator;
            private readonly LegacyAnimatorState _animatorState;
            private NativeArray<TransformSceneHandle> _sceneHandles;
            private NativeArray<TransformStreamHandle> _streamHandles;
            private NativeArray<Vector3> _positions;
            private NativeArray<Quaternion> _rotations;
            private NativeArray<int> _status;
            private PlayableGraph _graph;

            public LegacyGoalSampler(Animator animator, Avatar avatar)
            {
                _animator = animator ?? throw new ArgumentNullException(nameof(animator));
                if (avatar == null || !avatar.isValid || !avatar.isHuman)
                    throw new ArgumentException("A valid Humanoid Avatar is required.", nameof(avatar));
                try
                {
                    _animatorState = new LegacyAnimatorState(animator, avatar);
                    var transforms = animator.transform.GetComponentsInChildren<Transform>(true);
                    _sceneHandles = new NativeArray<TransformSceneHandle>(transforms.Length, Allocator.Persistent);
                    _streamHandles = new NativeArray<TransformStreamHandle>(transforms.Length, Allocator.Persistent);
                    for (var i = 0; i < transforms.Length; i++)
                    {
                        _sceneHandles[i] = animator.BindSceneTransform(transforms[i]);
                        _streamHandles[i] = animator.BindStreamTransform(transforms[i]);
                    }
                    _positions = new NativeArray<Vector3>(HumanoidGoalCount, Allocator.Persistent);
                    _rotations = new NativeArray<Quaternion>(HumanoidGoalCount, Allocator.Persistent);
                    _status = new NativeArray<int>(1, Allocator.Persistent);
                    _graph = PlayableGraph.Create("BlenderSync Legacy Humanoid Goals");
                    _graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
                    var scriptPlayable = AnimationScriptPlayable.Create(
                        _graph,
                        new LegacyGoalJob
                        {
                            SceneHandles = _sceneHandles,
                            StreamHandles = _streamHandles,
                            Positions = _positions,
                            Rotations = _rotations,
                            Status = _status,
                        },
                        0);
                    scriptPlayable.SetProcessInputs(false);
                    var output = AnimationPlayableOutput.Create(_graph, "BlenderSync Legacy Humanoid Goals", animator);
                    output.SetSourcePlayable(scriptPlayable);
                    output.SetWeight(1f);
                    _graph.Play();
                }
                catch
                {
                    Dispose();
                    throw;
                }
            }

            public void Sample()
            {
                var root = _animator.transform;
                var localPosition = root.localPosition;
                var localRotation = root.localRotation;
                var localScale = root.localScale;
                _status[0] = 0;
                try
                {
                    _graph.Evaluate(0f);
                }
                finally
                {
                    root.localPosition = localPosition;
                    root.localRotation = localRotation;
                    root.localScale = localScale;
                }

                if (_status[0] != 1)
                    throw new InvalidOperationException("Unity 6000.0 did not provide a Humanoid animation stream for IK goal sampling.");
            }

            public Vector3 GetPosition(int index)
            {
                return _positions[index];
            }

            public Quaternion GetRotation(int index)
            {
                return _rotations[index];
            }

            public void Dispose()
            {
                if (_graph.IsValid())
                    _graph.Destroy();
                if (_sceneHandles.IsCreated)
                    _sceneHandles.Dispose();
                if (_streamHandles.IsCreated)
                    _streamHandles.Dispose();
                if (_positions.IsCreated)
                    _positions.Dispose();
                if (_rotations.IsCreated)
                    _rotations.Dispose();
                if (_status.IsCreated)
                    _status.Dispose();
                _animatorState?.Dispose();
            }
        }

        private sealed class LegacyAnimatorState : IDisposable
        {
            private readonly Animator _animator;
            private readonly Avatar _previousAvatar;
            private readonly bool _previousEnabled;
            private readonly AnimatorCullingMode _previousCullingMode;
            private bool _disposed;

            public LegacyAnimatorState(Animator animator, Avatar avatar)
            {
                _animator = animator ?? throw new ArgumentNullException(nameof(animator));
                _previousAvatar = animator.avatar;
                _previousEnabled = animator.enabled;
                _previousCullingMode = animator.cullingMode;
                animator.avatar = avatar;
                animator.enabled = true;
                animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            }

            public void Dispose()
            {
                if (_disposed)
                    return;
                _disposed = true;
                if (_animator == null)
                    return;
                _animator.avatar = _previousAvatar;
                _animator.cullingMode = _previousCullingMode;
                _animator.enabled = _previousEnabled;
            }
        }

        private struct LegacyGoalJob : IAnimationJob
        {
            public NativeArray<TransformSceneHandle> SceneHandles;
            public NativeArray<TransformStreamHandle> StreamHandles;
            public NativeArray<Vector3> Positions;
            public NativeArray<Quaternion> Rotations;
            public NativeArray<int> Status;

            public void ProcessAnimation(AnimationStream stream)
            {
                if (!stream.isHumanStream)
                {
                    Status[0] = -1;
                    return;
                }

                for (var i = 0; i < SceneHandles.Length; i++)
                {
                    StreamHandles[i].SetLocalPosition(stream, SceneHandles[i].GetLocalPosition(stream));
                    StreamHandles[i].SetLocalRotation(stream, SceneHandles[i].GetLocalRotation(stream));
                    StreamHandles[i].SetLocalScale(stream, SceneHandles[i].GetLocalScale(stream));
                }

                var human = stream.AsHuman();
                var bodyPosition = human.bodyPosition;
                var bodyRotation = human.bodyRotation;
                var humanScale = human.humanScale;
                for (var i = 0; i < HumanoidGoalCount; i++)
                {
                    var goal = (AvatarIKGoal)i;
                    var goalPosition = human.GetGoalPositionFromPose(goal);
                    var goalRotation = human.GetGoalRotationFromPose(goal);
                    var footBottomHeight = goal == AvatarIKGoal.LeftFoot
                        ? human.leftFootHeight
                        : goal == AvatarIKGoal.RightFoot
                            ? human.rightFootHeight
                            : 0f;
                    Positions[i] = LegacyBodyGoalPosition(
                        goalPosition,
                        goalRotation,
                        bodyPosition,
                        bodyRotation,
                        humanScale,
                        footBottomHeight);
                    Rotations[i] = LegacyBodyGoalRotation(goalRotation, bodyRotation);
                }
                Status[0] = 1;
            }

            public void ProcessRootMotion(AnimationStream stream)
            {
            }
        }

        private static Vector3 LegacyBodyGoalPosition(
            Vector3 goalPositionFromPose,
            Quaternion goalRotationFromPose,
            Vector3 bodyPosition,
            Quaternion bodyRotation,
            float humanScale,
            float footBottomHeight)
        {
            var scale = Mathf.Max(Mathf.Abs(humanScale), 1e-6f);
            var bodyGoalRotation = LegacyBodyGoalRotation(goalRotationFromPose, bodyRotation);
            var bodyGoalPosition = Quaternion.Inverse(bodyRotation) *
                                   (goalPositionFromPose - bodyPosition) / scale;
            return bodyGoalPosition +
                   bodyGoalRotation * Vector3.down * (Mathf.Max(0f, footBottomHeight) / scale);
        }

        private static Quaternion LegacyBodyGoalRotation(
            Quaternion goalRotationFromPose,
            Quaternion bodyRotation)
        {
            return Normalize(Quaternion.Inverse(bodyRotation) * goalRotationFromPose);
        }

        private sealed class TransformCurveSet
        {
            public Transform Transform;
            public AnimationCurve PosX;
            public AnimationCurve PosY;
            public AnimationCurve PosZ;
            public AnimationCurve RotX;
            public AnimationCurve RotY;
            public AnimationCurve RotZ;
            public AnimationCurve RotW;
            public AnimationCurve EulerX;
            public AnimationCurve EulerY;
            public AnimationCurve EulerZ;
            public int EulerXPriority;
            public int EulerYPriority;
            public int EulerZPriority;
            public AnimationCurve ScaleX;
            public AnimationCurve ScaleY;
            public AnimationCurve ScaleZ;
        }

        private struct TransformOriginal
        {
            public Transform Transform;
            public Vector3 LocalPosition;
            public Quaternion LocalRotation;
            public Vector3 LocalScale;
        }

        private static List<TransformCurveSet> BuildTransformCurveSets(Transform sampleRoot, AnimationClip clip)
        {
            var sets = new Dictionary<string, TransformCurveSet>();
            if (sampleRoot == null || clip == null)
                return new List<TransformCurveSet>();

            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                if (binding.type != typeof(Transform))
                    continue;
                var transform = ResolveBindingTransform(sampleRoot, binding.path);
                if (transform == null)
                    continue;
                var key = binding.path ?? string.Empty;
                if (!sets.TryGetValue(key, out var set))
                {
                    set = new TransformCurveSet { Transform = transform };
                    sets[key] = set;
                }
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                if (TryAssignEulerCurve(set, binding.propertyName, curve))
                    continue;
                switch (binding.propertyName)
                {
                    case "m_LocalPosition.x": set.PosX = curve; break;
                    case "m_LocalPosition.y": set.PosY = curve; break;
                    case "m_LocalPosition.z": set.PosZ = curve; break;
                    case "m_LocalRotation.x": set.RotX = curve; break;
                    case "m_LocalRotation.y": set.RotY = curve; break;
                    case "m_LocalRotation.z": set.RotZ = curve; break;
                    case "m_LocalRotation.w": set.RotW = curve; break;
                    case "m_LocalScale.x": set.ScaleX = curve; break;
                    case "m_LocalScale.y": set.ScaleY = curve; break;
                    case "m_LocalScale.z": set.ScaleZ = curve; break;
                }
            }
            return sets.Values.ToList();
        }

        private static bool TryAssignEulerCurve(
            TransformCurveSet set,
            string propertyName,
            AnimationCurve curve)
        {
            if (set == null || string.IsNullOrEmpty(propertyName) || curve == null)
                return false;
            var separator = propertyName.LastIndexOf('.');
            if (separator <= 0 || separator != propertyName.Length - 2)
                return false;
            var priority = EulerCurvePriority(propertyName.Substring(0, separator));
            if (priority <= 0)
                return false;

            switch (propertyName[propertyName.Length - 1])
            {
                case 'x':
                    if (priority > set.EulerXPriority)
                    {
                        set.EulerX = curve;
                        set.EulerXPriority = priority;
                    }
                    return true;
                case 'y':
                    if (priority > set.EulerYPriority)
                    {
                        set.EulerY = curve;
                        set.EulerYPriority = priority;
                    }
                    return true;
                case 'z':
                    if (priority > set.EulerZPriority)
                    {
                        set.EulerZ = curve;
                        set.EulerZPriority = priority;
                    }
                    return true;
                default:
                    return false;
            }
        }

        private static int EulerCurvePriority(string propertyName)
        {
            switch (propertyName)
            {
                case "localEulerAnglesRaw": return 4;
                case "localEulerAnglesBaked": return 3;
                case "localEulerAngles": return 2;
                case "m_LocalEulerAngles": return 1;
                default: return 0;
            }
        }

        private static Transform ResolveBindingTransform(Transform root, string path)
        {
            if (root == null)
                return null;
            if (string.IsNullOrEmpty(path))
                return root;

            var direct = root.Find(path);
            if (direct != null)
                return direct;

            var segments = path.Split('/');
            for (var i = 0; i < segments.Length; i++)
            {
                if (segments[i] != root.name)
                    continue;
                if (i == segments.Length - 1)
                    return root;
                var relativePath = string.Join("/", segments.Skip(i + 1));
                var relative = root.Find(relativePath);
                if (relative != null)
                    return relative;
            }

            return null;
        }

        private static List<TransformOriginal> CaptureHierarchyOriginals(Transform root)
        {
            var originals = new List<TransformOriginal>();
            if (root == null)
                return originals;

            foreach (var transform in root.GetComponentsInChildren<Transform>(true))
            {
                if (transform == null)
                    continue;
                originals.Add(new TransformOriginal
                {
                    Transform = transform,
                    LocalPosition = transform.localPosition,
                    LocalRotation = transform.localRotation,
                    LocalScale = transform.localScale,
                });
            }
            return originals;
        }

        private static void RestoreOriginals(List<TransformOriginal> originals)
        {
            foreach (var original in originals)
            {
                if (original.Transform == null)
                    continue;
                original.Transform.localPosition = original.LocalPosition;
                original.Transform.localRotation = original.LocalRotation;
                original.Transform.localScale = original.LocalScale;
            }
        }

        private static void ApplyTransformCurveSets(List<TransformCurveSet> sets, float time)
        {
            foreach (var set in sets)
            {
                if (set.Transform == null)
                    continue;
                if (set.PosX != null || set.PosY != null || set.PosZ != null)
                {
                    var p = set.Transform.localPosition;
                    p.x = set.PosX != null ? set.PosX.Evaluate(time) : p.x;
                    p.y = set.PosY != null ? set.PosY.Evaluate(time) : p.y;
                    p.z = set.PosZ != null ? set.PosZ.Evaluate(time) : p.z;
                    set.Transform.localPosition = p;
                }
                if (set.RotX != null || set.RotY != null || set.RotZ != null || set.RotW != null)
                {
                    var r = set.Transform.localRotation;
                    r.x = set.RotX != null ? set.RotX.Evaluate(time) : r.x;
                    r.y = set.RotY != null ? set.RotY.Evaluate(time) : r.y;
                    r.z = set.RotZ != null ? set.RotZ.Evaluate(time) : r.z;
                    r.w = set.RotW != null ? set.RotW.Evaluate(time) : r.w;
                    var len = Mathf.Sqrt(r.x * r.x + r.y * r.y + r.z * r.z + r.w * r.w);
                    if (len > 1e-6f)
                        r = new Quaternion(r.x / len, r.y / len, r.z / len, r.w / len);
                    set.Transform.localRotation = r;
                }
                else if (set.EulerX != null || set.EulerY != null || set.EulerZ != null)
                {
                    var euler = set.Transform.localEulerAngles;
                    euler.x = set.EulerX != null ? set.EulerX.Evaluate(time) : euler.x;
                    euler.y = set.EulerY != null ? set.EulerY.Evaluate(time) : euler.y;
                    euler.z = set.EulerZ != null ? set.EulerZ.Evaluate(time) : euler.z;
                    set.Transform.localRotation = Quaternion.Euler(euler);
                }
                if (set.ScaleX != null || set.ScaleY != null || set.ScaleZ != null)
                {
                    var s = set.Transform.localScale;
                    s.x = set.ScaleX != null ? set.ScaleX.Evaluate(time) : s.x;
                    s.y = set.ScaleY != null ? set.ScaleY.Evaluate(time) : s.y;
                    s.z = set.ScaleZ != null ? set.ScaleZ.Evaluate(time) : s.z;
                    set.Transform.localScale = s;
                }
            }
        }

        private static void AddKey(List<Keyframe> keys, float time, float value)
        {
            keys.Add(new Keyframe(time, value));
        }

        private static void SetAnimatorCurve(AnimationClip clip, string propertyName, List<Keyframe> keys)
        {
            var curve = new AnimationCurve(keys.ToArray());
            AnimationUtility.SetEditorCurve(clip, EditorCurveBinding.FloatCurve(string.Empty, typeof(Animator), propertyName), curve);
        }

        private static bool IsVarying(List<Keyframe> keys)
        {
            if (keys == null || keys.Count < 2)
                return false;
            var first = keys[0].value;
            for (var i = 1; i < keys.Count; i++)
                if (Mathf.Abs(keys[i].value - first) > 1e-5f)
                    return true;
            return false;
        }

        private static int CountProperties(IEnumerable<EditorCurveBinding> bindings, string prefix)
        {
            return bindings.Count(binding => !string.IsNullOrEmpty(binding.propertyName) && binding.propertyName.StartsWith(prefix));
        }

        private static int CountGoalProperties(IEnumerable<EditorCurveBinding> bindings, string suffix)
        {
            return bindings.Count(binding =>
                !string.IsNullOrEmpty(binding.propertyName) &&
                GoalSpecs.Any(goal => binding.propertyName.StartsWith(goal.Prefix + suffix)));
        }

        private static int CountHumanTraitMuscleBindings(IEnumerable<EditorCurveBinding> bindings)
        {
            var muscleNames = new HashSet<string>();
            for (var i = 0; i < HumanTrait.MuscleCount; i++)
                muscleNames.Add(ToHumanoidCurvePropertyName(HumanTrait.MuscleName[i]));
            return bindings.Count(binding => !string.IsNullOrEmpty(binding.propertyName) && muscleNames.Contains(binding.propertyName));
        }

        private static string ToHumanoidCurvePropertyName(string muscleName)
        {
            if (string.IsNullOrEmpty(muscleName))
                return muscleName;

            var leftPrefix = "Left ";
            var rightPrefix = "Right ";
            if (TryConvertFingerMuscleName(muscleName, leftPrefix, "LeftHand.", out var leftName))
                return leftName;
            if (TryConvertFingerMuscleName(muscleName, rightPrefix, "RightHand.", out var rightName))
                return rightName;
            return muscleName;
        }

        private static bool TryConvertFingerMuscleName(string muscleName, string sidePrefix, string nativePrefix, out string nativeName)
        {
            nativeName = null;
            if (!muscleName.StartsWith(sidePrefix))
                return false;
            var rest = muscleName.Substring(sidePrefix.Length);
            var parts = rest.Split(' ');
            if (parts.Length < 2)
                return false;
            var finger = parts[0];
            if (finger != "Thumb" && finger != "Index" && finger != "Middle" && finger != "Ring" && finger != "Little")
                return false;
            nativeName = parts.Length == 2
                ? nativePrefix + finger + "." + parts[1]
                : nativePrefix + finger + "." + parts[1] + " " + string.Join(" ", parts.Skip(2));
            return true;
        }

        private static void SelectGeneratedClip(AnimationClip clip)
        {
            if (clip == null)
                return;
            Selection.activeObject = clip;
            EditorGUIUtility.PingObject(clip);
        }
    }
}
#endif
