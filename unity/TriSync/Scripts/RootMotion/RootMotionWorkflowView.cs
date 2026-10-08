#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using BlenderSyncVNext.Diagnostics;
using UnityEditor;
using UnityEngine;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;

namespace BlenderSyncVNext.RootMotion
{
    internal sealed class RootMotionWorkflowView
    {
        private Animator _targetAnimator;
        private AnimationClip _sourceClip;
        private Transform _rootMotionTransform;
        private string _avatarFolder = "Assets/TriSync/Resources/Avatars";
        private string _clipFolder = "Assets/TriSync/Resources/AnimationClips";
        private string _variantSuffix = "_RootMotion";
        private HumanoidCurveReductionPreset _keyReduction = HumanoidCurveReductionPreset.Off;
        private HumanoidStaticCurveMode _staticCurves = HumanoidStaticCurveMode.Off;
        private Vector2 _scroll;
        private bool _showOutput;
        private Animator _curveSummaryAnimator;
        private AnimationClip _curveSummaryClip;
        private Transform _curveSummaryRoot;
        private string _curveSummaryRootPath;
        private int _positionCurveCount;
        private int _rotationCurveCount;

        internal void Draw()
        {
            DrawCharacterSection();

            _scroll = EditorGUILayout.BeginScrollView(_scroll, GUILayout.ExpandHeight(true));
            try
            {
                DrawSourceClipSection();
                EditorGUILayout.Space(6f);
                DrawOutputFoldout();
            }
            finally
            {
                EditorGUILayout.EndScrollView();
            }

            DrawCreateFooter();
        }

        private void DrawCharacterSection()
        {
            EditorGUILayout.LabelField(Tr("Character"), EditorStyles.boldLabel);
            using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
            {
                EditorGUI.BeginChangeCheck();
                _targetAnimator = (Animator)EditorGUILayout.ObjectField(
                    new GUIContent(
                        Tr("Animator"),
                        Tr("Character Animator used to build and receive the Generic Avatar.")),
                    _targetAnimator,
                    typeof(Animator),
                    true);
                if (EditorGUI.EndChangeCheck())
                    InvalidateCurveSummary();

                DrawAvatarRow();

                EditorGUI.BeginChangeCheck();
                _rootMotionTransform = (Transform)EditorGUILayout.ObjectField(
                    new GUIContent(
                        Tr("Root Node"),
                        Tr("Transform whose local position and rotation curves become Animator RootT and RootQ curves.")),
                    _rootMotionTransform,
                    typeof(Transform),
                    true);
                if (EditorGUI.EndChangeCheck())
                    InvalidateCurveSummary();

                DrawMotionRootWarning();
            }
        }

        private void DrawAvatarRow()
        {
            var avatar = _targetAnimator != null ? _targetAnimator.avatar : null;
            using (new EditorGUILayout.HorizontalScope())
            {
                using (new EditorGUI.DisabledScope(_targetAnimator == null))
                {
                    EditorGUI.BeginChangeCheck();
                    avatar = (Avatar)EditorGUILayout.ObjectField(
                        new GUIContent(
                            Tr("Avatar"),
                            Tr("Avatar assigned to the selected Animator.")),
                        avatar,
                        typeof(Avatar),
                        false);
                    if (EditorGUI.EndChangeCheck())
                        AssignAnimatorAvatar(avatar);
                }
                using (new EditorGUI.DisabledScope(!CanGenerateAvatar()))
                {
                    if (GUILayout.Button(
                            new GUIContent(
                                Tr(GenericAvatarActionLabel(avatar != null)),
                                Tr("Create and assign a Generic Avatar using the selected Root Node.")),
                            GUILayout.Width(190f)))
                        CreateGenericAvatarFromUi();
                }
            }
        }

        private void DrawMotionRootWarning()
        {
            if (_targetAnimator == null || _rootMotionTransform == null)
                return;

            if (IsUnderRoot(_targetAnimator.transform, _rootMotionTransform))
                return;

            EditorGUILayout.HelpBox(
                Tr("Root Node must be the Animator transform or one of its children."),
                MessageType.Warning);
        }

        private void DrawSourceClipSection()
        {
            EditorGUILayout.LabelField(Tr("Source Clip"), EditorStyles.boldLabel);
            using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
            {
                EditorGUI.BeginChangeCheck();
                _sourceClip = (AnimationClip)EditorGUILayout.ObjectField(
                    new GUIContent(
                        Tr("Clip"),
                        Tr("Raw Generic clip to copy and augment with Animator RootT and RootQ curves.")),
                    _sourceClip,
                    typeof(AnimationClip),
                    false);
                if (EditorGUI.EndChangeCheck())
                    InvalidateCurveSummary();

                DrawCurveSummary();
            }
        }

        private void DrawCurveSummary()
        {
            if (!TryEnsureCurveSummary())
                return;

            EditorGUILayout.LabelField(
                Tr("Root Curves"),
                Format("Position {0}/3 | Rotation {1}/4", _positionCurveCount, _rotationCurveCount));
            if (_positionCurveCount < 3 || _rotationCurveCount < 4)
            {
                EditorGUILayout.HelpBox(
                    Tr("The source clip is missing one or more Root Node channels. Missing channels will use constant values in the generated clip."),
                    MessageType.Warning);
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

            _avatarFolder = DrawFolderField(Tr("Avatar Folder"), _avatarFolder, Tr("Select Avatar Folder"));
            _clipFolder = DrawFolderField(Tr("Clip Folder"), _clipFolder, Tr("Select Clip Folder"));
            _variantSuffix = EditorGUILayout.TextField(Tr("Variant Suffix"), _variantSuffix);
            _keyReduction = (HumanoidCurveReductionPreset)EditorGUILayout.Popup(
                new GUIContent(
                    Tr("Key Reduction"),
                    Tr("Reduce copied baked curve keys within a bounded error. Object-reference and discrete curves are preserved.")),
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
                    Tr("Collapse numerically constant Animator and Transform curves to one key while keeping every binding.")),
                (int)_staticCurves,
                new[]
                {
                    Tr("Off"),
                    Tr("Collapse Constant"),
                });
        }

        private static string DrawFolderField(string label, string value, string dialogTitle)
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
                    value = ChooseAssetFolder(value, dialogTitle);
            }
            return value;
        }

        private static string ChooseAssetFolder(string current, string dialogTitle)
        {
            var selected = EditorUtility.OpenFolderPanel(
                dialogTitle,
                Application.dataPath,
                string.Empty);
            if (string.IsNullOrWhiteSpace(selected))
                return current;

            var relative = FileUtil.GetProjectRelativePath(selected).Replace('\\', '/').TrimEnd('/');
            if (relative == "Assets" || relative.StartsWith("Assets/", System.StringComparison.Ordinal))
                return relative;

            EditorUtility.DisplayDialog(
                Tr("Animation Root Motion"),
                Tr("Choose a folder inside this project's Assets directory."),
                Tr("OK"));
            return current;
        }

        private void DrawCreateFooter()
        {
            var canGenerate = CanGenerateVariant();
            EditorGUILayout.Space(4f);
            EditorGUILayout.LabelField(
                Tr(CreateStatusLabel(
                    _targetAnimator != null,
                    _rootMotionTransform != null,
                    _targetAnimator != null &&
                    _rootMotionTransform != null &&
                    IsUnderRoot(_targetAnimator.transform, _rootMotionTransform),
                    _sourceClip != null,
                    _positionCurveCount,
                    _rotationCurveCount)),
                EditorStyles.label);
            using (new EditorGUI.DisabledScope(!canGenerate))
            {
                if (GUILayout.Button(
                        new GUIContent(
                            Tr("Create Root Motion Clip"),
                            Tr("Create a copy of Source Clip with Animator RootT and RootQ curves. Root Transform clip settings are not changed automatically.")),
                        GUILayout.Height(30f)))
                    RootMotionEditorActions.Execute(
                        "Root Motion Variant",
                        "Create Root Motion Clip",
                        "Animation Root Motion",
                        () => GenerateRootMotionVariant());
            }
        }

        private void CreateGenericAvatarFromUi()
        {
            var currentAvatar = _targetAnimator != null ? _targetAnimator.avatar : null;
            if (NeedsHumanoidReplacementConfirmation(
                    currentAvatar != null,
                    currentAvatar != null && currentAvatar.isHuman) &&
                !EditorUtility.DisplayDialog(
                    Tr("Replace Humanoid Avatar?"),
                    Tr("This Animator currently uses a Humanoid Avatar. Creating a Generic Avatar will replace that assignment."),
                    Tr("Replace"),
                    Tr("Cancel")))
                return;

            RootMotionEditorActions.Execute(
                "Generic Avatar",
                GenericAvatarActionLabel(currentAvatar != null),
                "Animation Root Motion",
                () => GenerateGenericAvatar());
        }

        private bool TryEnsureCurveSummary()
        {
            if (_targetAnimator == null ||
                _sourceClip == null ||
                _rootMotionTransform == null ||
                !IsUnderRoot(_targetAnimator.transform, _rootMotionTransform))
                return false;

            var rootPath = GetRelativePath(_targetAnimator.transform, _rootMotionTransform);
            if (_curveSummaryAnimator == _targetAnimator &&
                _curveSummaryClip == _sourceClip &&
                _curveSummaryRoot == _rootMotionTransform &&
                _curveSummaryRootPath == rootPath)
                return true;

            var bindings = AnimationUtility.GetCurveBindings(_sourceClip);
            _positionCurveCount = CountTransformCurves(bindings, rootPath, "m_LocalPosition");
            _rotationCurveCount = CountTransformCurves(bindings, rootPath, "m_LocalRotation");
            _curveSummaryAnimator = _targetAnimator;
            _curveSummaryClip = _sourceClip;
            _curveSummaryRoot = _rootMotionTransform;
            _curveSummaryRootPath = rootPath;
            return true;
        }

        private void InvalidateCurveSummary()
        {
            _curveSummaryAnimator = null;
            _curveSummaryClip = null;
            _curveSummaryRoot = null;
            _curveSummaryRootPath = null;
            _positionCurveCount = 0;
            _rotationCurveCount = 0;
        }

        private void AssignAnimatorAvatar(Avatar avatar)
        {
            if (_targetAnimator == null || _targetAnimator.avatar == avatar)
                return;

            Undo.RecordObject(_targetAnimator, Tr("Assign Avatar"));
            _targetAnimator.avatar = avatar;
            RootMotionEditorActions.MarkAnimatorDirty(_targetAnimator);
        }

        private static string GenericAvatarActionLabel(bool hasAvatar)
        {
            return hasAvatar ? "Replace with Generic Avatar" : "Create Generic Avatar";
        }

        private static bool NeedsHumanoidReplacementConfirmation(bool assigned, bool human)
        {
            return assigned && human;
        }

        private static string CurveSummaryLabel(int positionCurves, int rotationCurves)
        {
            return $"Position {positionCurves}/3 | Rotation {rotationCurves}/4";
        }

        private static string CreateStatusLabel(
            bool hasAnimator,
            bool hasMotionRoot,
            bool motionRootUnderAnimator,
            bool hasSourceClip,
            int positionCurves,
            int rotationCurves)
        {
            if (!hasAnimator)
                return "Assign a character Animator to begin.";
            if (!hasMotionRoot)
                return "Assign a Root Node.";
            if (!motionRootUnderAnimator)
                return "Root Node must be under the Animator hierarchy.";
            if (!hasSourceClip)
                return "Assign a Source Clip.";
            if (positionCurves < 3 || rotationCurves < 4)
                return "Ready with constant fallbacks for missing root channels.";
            return "Ready to create a Root Motion Clip.";
        }

        private bool CanGenerateAvatar()
        {
            return _targetAnimator != null && _rootMotionTransform != null && IsUnderRoot(_targetAnimator.transform, _rootMotionTransform);
        }

        private bool CanGenerateVariant()
        {
            return _targetAnimator != null && _sourceClip != null && _rootMotionTransform != null && IsUnderRoot(_targetAnimator.transform, _rootMotionTransform);
        }

        private Avatar GenerateGenericAvatar()
        {
            if (!CanGenerateAvatar())
            {
                EditorUtility.DisplayDialog(Tr("Animation Root Motion"), Tr("Target Animator and a Root Motion Transform under that Animator are required."), Tr("OK"));
                return null;
            }

            if (!RootMotionAssetPaths.TryEnsureAssetFolder(ref _avatarFolder, "Generic Avatar", "Animation Root Motion"))
                return null;
            var rootName = _rootMotionTransform.name;
            var avatar = AvatarBuilder.BuildGenericAvatar(_targetAnimator.gameObject, rootName);
            if (avatar == null)
            {
                BlenderSyncReportStore.Add("Generic Avatar", "ERROR", "BuildGenericAvatar returned null");
                EditorUtility.DisplayDialog(Tr("Animation Root Motion"), Tr("BuildGenericAvatar returned null."), Tr("OK"));
                return null;
            }

            var safeAvatarName = RootMotionAssetPaths.SanitizeFileName(_targetAnimator.gameObject.name, "GenericAvatar");
            avatar.name = $"{safeAvatarName}_GenericAvatar";
            var avatarPath = AssetDatabase.GenerateUniqueAssetPath($"{_avatarFolder}/{avatar.name}.asset");
            AssetDatabase.CreateAsset(avatar, avatarPath);
            AssetDatabase.SaveAssets();
            var savedAvatar = AssetDatabase.LoadAssetAtPath<Avatar>(avatarPath);
            if (savedAvatar == null)
            {
                BlenderSyncReportStore.Add("Generic Avatar", "ERROR", "Avatar asset save failed", new Dictionary<string, object> { { "avatarPath", avatarPath } });
                EditorUtility.DisplayDialog(Tr("Animation Root Motion"), Tr("Avatar asset save failed."), Tr("OK"));
                return null;
            }
            avatar = savedAvatar;

            _targetAnimator.avatar = avatar;
            RootMotionEditorActions.MarkAnimatorDirty(_targetAnimator);
            SelectGeneratedAsset(avatar);

            BlenderSyncReportStore.Add(
                "Generic Avatar",
                avatar.isValid ? "OK" : "WARN",
                $"avatar={avatar.name} valid={avatar.isValid} human={avatar.isHuman} root={rootName}",
                new Dictionary<string, object>
                {
                    { "target", _targetAnimator.name },
                    { "avatarPath", avatarPath },
                    { "rootMotionTransformName", rootName },
                    { "rootMotionRelativePath", GetRelativePath(_targetAnimator.transform, _rootMotionTransform) },
                    { "transformCount", _targetAnimator.GetComponentsInChildren<Transform>(true).Length },
                    { "avatarValid", avatar.isValid },
                    { "avatarHuman", avatar.isHuman },
                    { "assigned", true },
                });

            return avatar;
        }

        private AnimationClip GenerateRootMotionVariant()
        {
            if (!CanGenerateVariant())
            {
                EditorUtility.DisplayDialog(Tr("Animation Root Motion"), Tr("Source Clip, Target Animator, and a Root Motion Transform under that Animator are required."), Tr("OK"));
                return null;
            }

            if (!RootMotionAssetPaths.TryEnsureAssetFolder(ref _clipFolder, "Root Motion Variant", "Animation Root Motion"))
                return null;
            var rootPath = GetRelativePath(_targetAnimator.transform, _rootMotionTransform);
            var safeClipName = RootMotionAssetPaths.SanitizeFileName(_sourceClip.name, "RootMotionClip");
            var safeSuffix = RootMotionAssetPaths.SanitizeFileName(_variantSuffix, string.Empty);
            var variantPath = AssetDatabase.GenerateUniqueAssetPath($"{_clipFolder}/{safeClipName}{safeSuffix}.anim");
            var variant = new AnimationClip
            {
                name = Path.GetFileNameWithoutExtension(variantPath),
                frameRate = _sourceClip.frameRate > 0 ? _sourceClip.frameRate : 60f,
            };

            var copied = CopyCurves(_sourceClip, variant);
            var bindings = AnimationUtility.GetCurveBindings(_sourceClip);
            var rootT = ExtractAndWriteRootT(_sourceClip, variant, bindings, rootPath);
            var rootQ = ExtractAndWriteRootQ(_sourceClip, variant, bindings, rootPath);
            var reduction = HumanoidCurveReducer.ReduceAllFloatCurves(
                variant,
                _keyReduction,
                _staticCurves);
            var reductionPercent = reduction.KeysBefore > 0
                ? 100f * (reduction.KeysBefore - reduction.KeysAfter) / reduction.KeysBefore
                : 0f;
            var reductionName = _keyReduction.ToString().ToLowerInvariant();
            var staticCurveName = _staticCurves.ToString().ToLowerInvariant();

            AssetDatabase.CreateAsset(variant, variantPath);
            AssetDatabase.SaveAssets();
            var savedVariant = AssetDatabase.LoadAssetAtPath<AnimationClip>(variantPath);
            if (savedVariant == null)
            {
                BlenderSyncReportStore.Add("Root Motion Variant", "ERROR", "Variant clip asset save failed", new Dictionary<string, object> { { "variantPath", variantPath } });
                EditorUtility.DisplayDialog(Tr("Animation Root Motion"), Tr("Variant clip asset save failed."), Tr("OK"));
                return null;
            }
            variant = savedVariant;
            SelectGeneratedAsset(variant);

            BlenderSyncReportStore.Add(
                "Root Motion Variant",
                rootT.extracted == 3 && rootQ.extracted == 4 ? "OK" : "WARN",
                $"variant={variantPath} rootPath={(string.IsNullOrEmpty(rootPath) ? "(Animator root)" : rootPath)} RootT={rootT.extracted}/3 RootQ={rootQ.extracted}/4 keyReduction={reductionName} staticCurves={staticCurveName} collapsedStaticTracks={reduction.CollapsedStaticTrackCount} keysBefore={reduction.KeysBefore} keysAfter={reduction.KeysAfter} reductionPercent={reductionPercent:0.##}",
                new Dictionary<string, object>
                {
                    { "sourceClip", _sourceClip.name },
                    { "variantPath", variantPath },
                    { "targetAnimator", _targetAnimator.name },
                    { "rootTransform", _rootMotionTransform.name },
                    { "rootPath", string.IsNullOrEmpty(rootPath) ? "(Animator root)" : rootPath },
                    { "copiedCurves", copied },
                    { "rootTExtracted", rootT.extracted },
                    { "rootQExtracted", rootQ.extracted },
                    { "rootTFallbacks", rootT.missing.Count },
                    { "rootQFallbacks", rootQ.missing.Count },
                    { "missingPosition", string.Join(",", rootT.missing) },
                    { "missingRotation", string.Join(",", rootQ.missing) },
                    { "keyReduction", reductionName },
                    { "staticCurves", staticCurveName },
                    { "collapsedStaticTracks", reduction.CollapsedStaticTrackCount },
                    { "keysBefore", reduction.KeysBefore },
                    { "keysAfter", reduction.KeysAfter },
                    { "reductionPercent", reductionPercent },
                });

            return variant;
        }

        private static int CopyCurves(AnimationClip source, AnimationClip target)
        {
            var count = 0;
            foreach (var binding in AnimationUtility.GetCurveBindings(source))
            {
                AnimationUtility.SetEditorCurve(target, binding, AnimationUtility.GetEditorCurve(source, binding));
                count++;
            }
            foreach (var binding in AnimationUtility.GetObjectReferenceCurveBindings(source))
                AnimationUtility.SetObjectReferenceCurve(target, binding, AnimationUtility.GetObjectReferenceCurve(source, binding));
            AnimationUtility.SetAnimationEvents(target, AnimationUtility.GetAnimationEvents(source));
            return count;
        }

        private static (int extracted, List<string> missing) ExtractAndWriteRootT(AnimationClip source, AnimationClip target, EditorCurveBinding[] bindings, string rootPath)
        {
            var missing = new List<string>();
            var extracted = 0;
            foreach (var axis in new[] { "x", "y", "z" })
            {
                var curve = FindCurve(source, bindings, rootPath, $"m_LocalPosition.{axis}");
                if (curve == null)
                {
                    missing.Add(axis);
                    curve = ConstantCurve(0f, source.length);
                }
                else
                {
                    extracted++;
                }
                SetRootCurve(target, $"RootT.{axis}", curve);
            }
            return (extracted, missing);
        }

        private static (int extracted, List<string> missing) ExtractAndWriteRootQ(AnimationClip source, AnimationClip target, EditorCurveBinding[] bindings, string rootPath)
        {
            var missing = new List<string>();
            var extracted = 0;
            foreach (var axis in new[] { "x", "y", "z", "w" })
            {
                var curve = FindCurve(source, bindings, rootPath, $"m_LocalRotation.{axis}");
                if (curve == null)
                {
                    missing.Add(axis);
                    curve = ConstantCurve(axis == "w" ? 1f : 0f, source.length);
                }
                else
                {
                    extracted++;
                }
                SetRootCurve(target, $"RootQ.{axis}", curve);
            }
            return (extracted, missing);
        }

        private static AnimationCurve FindCurve(AnimationClip clip, EditorCurveBinding[] bindings, string path, string property)
        {
            foreach (var binding in bindings)
            {
                if (binding.path == path && binding.propertyName == property)
                    return AnimationUtility.GetEditorCurve(clip, binding);
            }
            return null;
        }

        private static void SetRootCurve(AnimationClip clip, string propertyName, AnimationCurve curve)
        {
            var binding = new EditorCurveBinding
            {
                path = string.Empty,
                type = typeof(Animator),
                propertyName = propertyName,
            };
            AnimationUtility.SetEditorCurve(clip, binding, curve);
        }

        private static AnimationCurve ConstantCurve(float value, float duration)
        {
            return new AnimationCurve(new Keyframe(0f, value), new Keyframe(Mathf.Max(0.033f, duration), value));
        }

        private static void SelectGeneratedAsset(Object asset)
        {
            if (asset == null)
                return;
            Selection.activeObject = asset;
            EditorGUIUtility.PingObject(asset);
        }

        private static int CountTransformCurves(EditorCurveBinding[] bindings, string path, string prefix)
        {
            var count = 0;
            foreach (var binding in bindings)
            {
                if (binding.path == path && binding.propertyName != null && binding.propertyName.StartsWith(prefix))
                    count++;
            }
            return count;
        }

        private static string GetRelativePath(Transform root, Transform target)
        {
            if (root == null || target == null)
                return string.Empty;
            if (root == target)
                return string.Empty;
            var names = new List<string>();
            var current = target;
            while (current != null && current != root)
            {
                names.Add(current.name);
                current = current.parent;
            }
            if (current != root)
                return target.name;
            names.Reverse();
            return string.Join("/", names);
        }

        private static bool IsUnderRoot(Transform root, Transform target)
        {
            if (root == null || target == null)
                return false;
            for (var current = target; current != null; current = current.parent)
                if (current == root)
                    return true;
            return false;
        }
    }
}
#endif
