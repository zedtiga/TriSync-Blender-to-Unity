#if UNITY_EDITOR
using System.Collections.Generic;
using System.Linq;
using BlenderSyncVNext.Diagnostics;
using UnityEditor;
using UnityEngine;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;

namespace BlenderSyncVNext.RootMotion
{
    internal sealed class HumanoidAvatarSetupView
    {
        private Animator _targetAnimator;
        private string _avatarFolder = "Assets/TriSync/Resources/Avatars";
        private HumanoidBoneMapper.MappingResult _mapping;
        private Vector2 _scroll;
        private int _mappingTab;
        private bool _showMappingDetails;
        private bool _showHumanoidSettings;
        private bool _showOutput;
        private float _upperArmTwist = 0.5f;
        private float _lowerArmTwist = 0.5f;
        private float _upperLegTwist = 0.5f;
        private float _lowerLegTwist = 0.5f;
        private float _armStretch = 0.05f;
        private float _legStretch = 0.05f;
        private float _feetSpacing = 0f;
        private bool _hasTranslationDoF;
        private bool _initialized;

        private static readonly HumanBodyBones[] BodyBones =
        {
            HumanBodyBones.Hips,
            HumanBodyBones.Spine,
            HumanBodyBones.Chest,
            HumanBodyBones.UpperChest,
            HumanBodyBones.Neck,
            HumanBodyBones.Head,
            HumanBodyBones.LeftShoulder,
            HumanBodyBones.LeftUpperArm,
            HumanBodyBones.LeftLowerArm,
            HumanBodyBones.LeftHand,
            HumanBodyBones.RightShoulder,
            HumanBodyBones.RightUpperArm,
            HumanBodyBones.RightLowerArm,
            HumanBodyBones.RightHand,
            HumanBodyBones.LeftUpperLeg,
            HumanBodyBones.LeftLowerLeg,
            HumanBodyBones.LeftFoot,
            HumanBodyBones.LeftToes,
            HumanBodyBones.RightUpperLeg,
            HumanBodyBones.RightLowerLeg,
            HumanBodyBones.RightFoot,
            HumanBodyBones.RightToes,
        };

        private static readonly HumanBodyBones[] HeadBones =
        {
            HumanBodyBones.Neck,
            HumanBodyBones.Head,
            HumanBodyBones.LeftEye,
            HumanBodyBones.RightEye,
            HumanBodyBones.Jaw,
        };

        private static readonly HumanBodyBones[] LeftHandBones =
        {
            HumanBodyBones.LeftHand,
            HumanBodyBones.LeftThumbProximal,
            HumanBodyBones.LeftThumbIntermediate,
            HumanBodyBones.LeftThumbDistal,
            HumanBodyBones.LeftIndexProximal,
            HumanBodyBones.LeftIndexIntermediate,
            HumanBodyBones.LeftIndexDistal,
            HumanBodyBones.LeftMiddleProximal,
            HumanBodyBones.LeftMiddleIntermediate,
            HumanBodyBones.LeftMiddleDistal,
            HumanBodyBones.LeftRingProximal,
            HumanBodyBones.LeftRingIntermediate,
            HumanBodyBones.LeftRingDistal,
            HumanBodyBones.LeftLittleProximal,
            HumanBodyBones.LeftLittleIntermediate,
            HumanBodyBones.LeftLittleDistal,
        };

        private static readonly HumanBodyBones[] RightHandBones =
        {
            HumanBodyBones.RightHand,
            HumanBodyBones.RightThumbProximal,
            HumanBodyBones.RightThumbIntermediate,
            HumanBodyBones.RightThumbDistal,
            HumanBodyBones.RightIndexProximal,
            HumanBodyBones.RightIndexIntermediate,
            HumanBodyBones.RightIndexDistal,
            HumanBodyBones.RightMiddleProximal,
            HumanBodyBones.RightMiddleIntermediate,
            HumanBodyBones.RightMiddleDistal,
            HumanBodyBones.RightRingProximal,
            HumanBodyBones.RightRingIntermediate,
            HumanBodyBones.RightRingDistal,
            HumanBodyBones.RightLittleProximal,
            HumanBodyBones.RightLittleIntermediate,
            HumanBodyBones.RightLittleDistal,
        };

        internal void Activate()
        {
            if (_initialized)
                return;
            _initialized = true;
            TryUseSelection(false);
        }

        internal void Draw()
        {
            DrawTargetSection();
            _scroll = EditorGUILayout.BeginScrollView(_scroll, GUILayout.ExpandHeight(true));
            try
            {
                if (_targetAnimator == null)
                {
                    EditorGUILayout.Space(6f);
                    EditorGUILayout.HelpBox(
                        Tr(GenerateStatusLabel(false, false, 0, false)),
                        MessageType.Info);
                }
                else
                {
                    DrawMappingSummary();
                }
                EditorGUILayout.Space(6f);
                DrawHumanoidSettingsFoldout();
                DrawOutputFoldout();
            }
            finally
            {
                EditorGUILayout.EndScrollView();
            }

            DrawGenerateFooter();
        }

        private void DrawTargetSection()
        {
            EditorGUILayout.LabelField(Tr("Target"), EditorStyles.boldLabel);
            using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
            {
                using (new EditorGUILayout.HorizontalScope())
                {
                    EditorGUI.BeginChangeCheck();
                    _targetAnimator = (Animator)EditorGUILayout.ObjectField(
                        Tr("Animator"),
                        _targetAnimator,
                        typeof(Animator),
                        true);
                    if (EditorGUI.EndChangeCheck())
                        AutoMap();

                    if (GUILayout.Button(Tr("Use Selected"), GUILayout.Width(110f)))
                        TryUseSelection(true);
                }

                if (_targetAnimator != null)
                {
                    var avatar = _targetAnimator.avatar;
                    EditorGUILayout.LabelField(
                        Tr("Avatar"),
                        LocalizedAvatarStatusLabel(
                            avatar != null ? avatar.name : null,
                            avatar != null,
                            avatar != null && avatar.isValid,
                            avatar != null && avatar.isHuman));
                }
            }
        }

        private void TryUseSelection(bool showDialog)
        {
            var selected = Selection.activeGameObject;
            if (selected == null)
            {
                if (showDialog)
                    EditorUtility.DisplayDialog(Tr("Humanoid Avatar Setup"), Tr("Select a character GameObject or one of its child bones."), Tr("OK"));
                return;
            }

            var animator = selected.GetComponentInParent<Animator>();
            if (animator == null)
                animator = selected.GetComponentInChildren<Animator>();

            if (animator == null)
            {
                if (showDialog)
                    EditorUtility.DisplayDialog(Tr("Humanoid Avatar Setup"), Tr("No Animator found on the selection, its parents, or its children."), Tr("OK"));
                return;
            }

            _targetAnimator = animator;
            AutoMap();
        }

        private void AutoMap()
        {
            _mapping = _targetAnimator != null
                ? HumanoidBoneMapper.AutoMap(_targetAnimator.transform)
                : null;
        }

        private bool CanGenerate()
        {
            return _targetAnimator != null && _mapping != null && _mapping.HasRequired && AllMappedBonesUnderTargetRoot();
        }

        private void DrawMappingSummary()
        {
            if (_mapping == null)
                AutoMap();
            if (_mapping == null)
                return;

            using (new EditorGUILayout.HorizontalScope())
            {
                EditorGUILayout.LabelField(Tr("Mapping"), EditorStyles.boldLabel);
                GUILayout.FlexibleSpace();
                if (GUILayout.Button(
                        new GUIContent(Tr("Remap"), Tr("Rebuild all bone assignments from the target hierarchy.")),
                        GUILayout.Width(80f)))
                    AutoMap();
            }

            GetMappingCounts(
                out var requiredMapped,
                out var requiredTotal,
                out var optionalMapped,
                out var optionalTotal);
            var status = _mapping.HasRequired ? MessageType.Info : MessageType.Warning;
            EditorGUILayout.HelpBox(
                LocalizedMappingSummaryLabel(
                    requiredMapped,
                    requiredTotal,
                    optionalMapped,
                    optionalTotal),
                status);

            _showMappingDetails = EditorGUILayout.Foldout(
                _showMappingDetails,
                Tr("Mapping Details"),
                true);
            if (_showMappingDetails)
            {
                using (new EditorGUI.IndentLevelScope())
                {
                    EditorGUILayout.LabelField(Tr("Candidate source"), _mapping.ValidBoneSource);
                    EditorGUILayout.LabelField(Tr("Valid bones"), _mapping.ValidBoneCount.ToString());
                    EditorGUILayout.LabelField(Tr("Bridge/dummy bones"), _mapping.BridgeBoneCount.ToString());
                }
            }

            _mappingTab = GUILayout.Toolbar(
                Mathf.Clamp(_mappingTab, 0, 3),
                new[] { Tr("Body"), Tr("Head"), Tr("Left Hand"), Tr("Right Hand") });
            EditorGUILayout.Space(4);
            switch (_mappingTab)
            {
                case 0:
                    DrawBoneGroup("Body", BodyBones);
                    break;
                case 1:
                    DrawBoneGroup("Head", HeadBones);
                    break;
                case 2:
                    DrawBoneGroup("Left Hand", LeftHandBones);
                    break;
                case 3:
                    DrawBoneGroup("Right Hand", RightHandBones);
                    break;
                default:
                    DrawBoneGroup("Body", BodyBones);
                    break;
            }
        }

        private void DrawBoneGroup(string title, IReadOnlyList<HumanBodyBones> bones)
        {
            EditorGUILayout.Space(4);
            EditorGUILayout.LabelField(Tr(title), EditorStyles.boldLabel);
            foreach (var bone in bones)
                DrawBoneRow(bone);
        }

        private void DrawBoneRow(HumanBodyBones bone)
        {
            _mapping.Bones.TryGetValue(bone, out var transform);
            var required = IsRequired(bone);
            using (new EditorGUILayout.HorizontalScope())
            {
                var oldColor = GUI.color;
                GUI.color = transform != null ? new Color(0.35f, 0.9f, 0.35f) : (required ? new Color(1f, 0.45f, 0.35f) : new Color(0.65f, 0.65f, 0.65f));
                EditorGUILayout.LabelField(transform != null ? "OK" : (required ? Tr("REQ") : "-"), GUILayout.Width(34));
                GUI.color = oldColor;

                EditorGUILayout.LabelField(Tr(DisplayBoneName(bone)), GUILayout.Width(160));
                EditorGUI.BeginChangeCheck();
                var newTransform = (Transform)EditorGUILayout.ObjectField(transform, typeof(Transform), true);
                if (EditorGUI.EndChangeCheck())
                {
                    if (newTransform == null)
                    {
                        _mapping.Bones.Remove(bone);
                    }
                    else if (!IsUnderTargetRoot(newTransform))
                    {
                        EditorUtility.DisplayDialog(
                            Tr("Humanoid Avatar Setup"),
                            Tr("Mapped bones must be under the Target Animator hierarchy."),
                            Tr("OK"));
                    }
                    else
                    {
                        _mapping.Bones[bone] = newTransform;
                    }
                    HumanoidBoneMapper.RefreshMissing(_mapping);
                }

            }
        }

        private static bool IsRequired(HumanBodyBones bone)
        {
            var index = (int)bone;
            return index >= 0 && index < HumanTrait.BoneCount && HumanTrait.RequiredBone(index);
        }

        private bool AllMappedBonesUnderTargetRoot()
        {
            if (_targetAnimator == null || _mapping == null)
                return false;

            foreach (var transform in _mapping.Bones.Values)
            {
                if (transform != null && !IsUnderTargetRoot(transform))
                    return false;
            }
            return true;
        }

        private bool IsUnderTargetRoot(Transform transform)
        {
            if (_targetAnimator == null || transform == null)
                return false;

            var root = _targetAnimator.transform;
            for (var current = transform; current != null; current = current.parent)
            {
                if (current == root)
                    return true;
            }
            return false;
        }

        private static string DisplayBoneName(HumanBodyBones bone)
        {
            var index = (int)bone;
            if (index >= 0 && index < HumanTrait.BoneName.Length)
                return ObjectNames.NicifyVariableName(HumanTrait.BoneName[index]);
            return ObjectNames.NicifyVariableName(bone.ToString());
        }

        private void DrawHumanoidSettingsFoldout()
        {
            _showHumanoidSettings = EditorGUILayout.Foldout(
                _showHumanoidSettings,
                new GUIContent(
                    Tr("Humanoid Settings"),
                    Tr("Unity HumanDescription twist, stretch, spacing, and translation settings.")),
                true,
                EditorStyles.foldoutHeader);
            if (!_showHumanoidSettings)
                return;

            _upperArmTwist = EditorGUILayout.Slider(Tr("Upper Arm Twist"), _upperArmTwist, 0f, 1f);
            _lowerArmTwist = EditorGUILayout.Slider(Tr("Lower Arm Twist"), _lowerArmTwist, 0f, 1f);
            _upperLegTwist = EditorGUILayout.Slider(Tr("Upper Leg Twist"), _upperLegTwist, 0f, 1f);
            _lowerLegTwist = EditorGUILayout.Slider(Tr("Lower Leg Twist"), _lowerLegTwist, 0f, 1f);
            _armStretch = EditorGUILayout.Slider(Tr("Arm Stretch"), _armStretch, 0f, 0.2f);
            _legStretch = EditorGUILayout.Slider(Tr("Leg Stretch"), _legStretch, 0f, 0.2f);
            _feetSpacing = EditorGUILayout.Slider(Tr("Feet Spacing"), _feetSpacing, 0f, 1f);
            _hasTranslationDoF = EditorGUILayout.Toggle(Tr("Translation DoF"), _hasTranslationDoF);
            using (new EditorGUILayout.HorizontalScope())
            {
                GUILayout.FlexibleSpace();
                if (GUILayout.Button(Tr("Reset Defaults"), GUILayout.Width(120f)))
                {
                    _upperArmTwist = _lowerArmTwist = _upperLegTwist = _lowerLegTwist = 0.5f;
                    _armStretch = _legStretch = 0.05f;
                    _feetSpacing = 0f;
                    _hasTranslationDoF = false;
                }
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

            var lineHeight = EditorGUIUtility.singleLineHeight;
            using (new EditorGUILayout.HorizontalScope(GUILayout.Height(lineHeight)))
            {
                _avatarFolder = EditorGUILayout.TextField(
                    Tr("Avatar Folder"),
                    _avatarFolder,
                    GUILayout.Height(lineHeight));
                var folderIcon = EditorGUIUtility.IconContent("Folder Icon");
                if (GUILayout.Button(
                        new GUIContent(folderIcon.image, Tr("Choose an output folder under Assets.")),
                        GUILayout.Width(28f),
                        GUILayout.Height(lineHeight)))
                    ChooseAvatarFolder();
            }
        }

        private void ChooseAvatarFolder()
        {
            var selected = EditorUtility.OpenFolderPanel(
                Tr("Select Avatar Folder"),
                Application.dataPath,
                string.Empty);
            if (string.IsNullOrWhiteSpace(selected))
                return;

            var relative = FileUtil.GetProjectRelativePath(selected).Replace('\\', '/').TrimEnd('/');
            if (relative == "Assets" || relative.StartsWith("Assets/", System.StringComparison.Ordinal))
            {
                _avatarFolder = relative;
                return;
            }

            EditorUtility.DisplayDialog(
                Tr("Humanoid Avatar Setup"),
                Tr("Choose a folder inside this project's Assets directory."),
                Tr("OK"));
        }

        private void DrawGenerateFooter()
        {
            var canGenerate = CanGenerate();
            EditorGUILayout.Space(4f);
            if (_targetAnimator != null)
            {
                EditorGUILayout.LabelField(
                    LocalizedGenerateStatusLabel(
                        true,
                        _mapping != null,
                        _mapping != null ? _mapping.MissingRequired.Count : 0,
                        _mapping != null && AllMappedBonesUnderTargetRoot()),
                    EditorStyles.label);
            }
            using (new EditorGUI.DisabledScope(!canGenerate))
            {
                if (GUILayout.Button(Tr("Generate Humanoid Avatar"), GUILayout.Height(30f)))
                    RootMotionEditorActions.Execute(
                        "Humanoid Avatar",
                        "Generate Humanoid Avatar",
                        "Humanoid Avatar Setup",
                        () => GenerateHumanoidAvatar());
            }
        }

        private void GetMappingCounts(
            out int requiredMapped,
            out int requiredTotal,
            out int optionalMapped,
            out int optionalTotal)
        {
            requiredMapped = 0;
            requiredTotal = 0;
            optionalMapped = 0;
            optionalTotal = 0;
            for (var index = 0; index < HumanTrait.BoneCount; index++)
            {
                var bone = (HumanBodyBones)index;
                var mapped = _mapping != null &&
                             _mapping.Bones.TryGetValue(bone, out var transform) &&
                             transform != null;
                if (HumanTrait.RequiredBone(index))
                {
                    requiredTotal++;
                    if (mapped)
                        requiredMapped++;
                }
                else
                {
                    optionalTotal++;
                    if (mapped)
                        optionalMapped++;
                }
            }
        }

        private static string AvatarStatusLabel(string name, bool assigned, bool valid, bool human)
        {
            if (!assigned)
                return "Not assigned";
            return $"{name} | {(valid ? "Valid" : "Invalid")} | {(human ? "Humanoid" : "Generic")}";
        }

        private static string LocalizedAvatarStatusLabel(string name, bool assigned, bool valid, bool human)
        {
            if (!assigned)
                return Tr("Not assigned");
            return Format(
                "{0} | {1} | {2}",
                name,
                Tr(valid ? "Valid" : "Invalid"),
                Tr(human ? "Humanoid" : "Generic"));
        }

        private static string MappingSummaryLabel(
            int requiredMapped,
            int requiredTotal,
            int optionalMapped,
            int optionalTotal)
        {
            return $"Required {requiredMapped}/{requiredTotal} | Optional {optionalMapped}/{optionalTotal}";
        }

        private static string LocalizedMappingSummaryLabel(
            int requiredMapped,
            int requiredTotal,
            int optionalMapped,
            int optionalTotal)
        {
            return Format(
                "Required {0}/{1} | Optional {2}/{3}",
                requiredMapped,
                requiredTotal,
                optionalMapped,
                optionalTotal);
        }

        private static string GenerateStatusLabel(
            bool hasTarget,
            bool hasMapping,
            int missingRequired,
            bool allBonesUnderTarget)
        {
            if (!hasTarget)
                return "Assign a character Animator to begin.";
            if (!hasMapping)
                return "Humanoid mapping is unavailable.";
            if (missingRequired > 0)
                return missingRequired == 1
                    ? "1 required bone is missing."
                    : $"{missingRequired} required bones are missing.";
            if (!allBonesUnderTarget)
                return "One or more mapped bones are outside the target hierarchy.";
            return "Ready to generate and assign a Humanoid Avatar.";
        }

        private static string LocalizedGenerateStatusLabel(
            bool hasTarget,
            bool hasMapping,
            int missingRequired,
            bool allBonesUnderTarget)
        {
            if (!hasTarget)
                return Tr("Assign a character Animator to begin.");
            if (!hasMapping)
                return Tr("Humanoid mapping is unavailable.");
            if (missingRequired > 0)
                return missingRequired == 1
                    ? Format("{0} required bone is missing.", missingRequired)
                    : Format("{0} required bones are missing.", missingRequired);
            if (!allBonesUnderTarget)
                return Tr("One or more mapped bones are outside the target hierarchy.");
            return Tr("Ready to generate and assign a Humanoid Avatar.");
        }

        private Avatar GenerateHumanoidAvatar()
        {
            if (!CanGenerate())
            {
                EditorUtility.DisplayDialog(Tr("Humanoid Avatar Setup"), Tr("Target Animator and all required humanoid bones are required before generating."), Tr("OK"));
                return null;
            }

            if (!RootMotionAssetPaths.TryEnsureAssetFolder(ref _avatarFolder, "Humanoid Avatar", "Humanoid Avatar Setup"))
                return null;
            var description = HumanoidBoneMapper.BuildHumanDescription(_targetAnimator.transform, _mapping);
            description.upperArmTwist = _upperArmTwist;
            description.lowerArmTwist = _lowerArmTwist;
            description.upperLegTwist = _upperLegTwist;
            description.lowerLegTwist = _lowerLegTwist;
            description.armStretch = _armStretch;
            description.legStretch = _legStretch;
            description.feetSpacing = _feetSpacing;
            description.hasTranslationDoF = _hasTranslationDoF;

            var avatar = AvatarBuilder.BuildHumanAvatar(_targetAnimator.gameObject, description);
            if (avatar == null)
            {
                BlenderSyncReportStore.Add("Humanoid Avatar", "ERROR", "BuildHumanAvatar returned null");
                EditorUtility.DisplayDialog(Tr("Humanoid Avatar Setup"), Tr("BuildHumanAvatar returned null."), Tr("OK"));
                return null;
            }

            var safeAvatarName = RootMotionAssetPaths.SanitizeFileName(_targetAnimator.gameObject.name, "HumanoidAvatar");
            avatar.name = $"{safeAvatarName}_HumanoidAvatar";
            var avatarPath = AssetDatabase.GenerateUniqueAssetPath($"{_avatarFolder}/{avatar.name}.asset");
            AssetDatabase.CreateAsset(avatar, avatarPath);
            AssetDatabase.SaveAssets();
            var savedAvatar = AssetDatabase.LoadAssetAtPath<Avatar>(avatarPath);
            if (savedAvatar == null)
            {
                BlenderSyncReportStore.Add("Humanoid Avatar", "ERROR", "Avatar asset save failed", new Dictionary<string, object> { { "avatarPath", avatarPath } });
                EditorUtility.DisplayDialog(Tr("Humanoid Avatar Setup"), Tr("Avatar asset save failed."), Tr("OK"));
                return null;
            }
            avatar = savedAvatar;

            _targetAnimator.avatar = avatar;
            RootMotionEditorActions.MarkAnimatorDirty(_targetAnimator);
            SelectAndPing(avatar);

            var missingRequired = string.Join(",", _mapping.MissingRequired.Select(HumanoidBoneMapper.ToHumanName));
            var missingOptional = string.Join(",", _mapping.MissingOptional.Select(HumanoidBoneMapper.ToHumanName));
            var status = avatar.isValid && avatar.isHuman ? "OK" : "WARN";
            var message = $"avatar={avatar.name} valid={avatar.isValid} human={avatar.isHuman} mapped={_mapping.Bones.Count} missingRequired={_mapping.MissingRequired.Count}";
            BlenderSyncReportStore.Add(
                "Humanoid Avatar",
                status,
                message,
                new Dictionary<string, object>
                {
                    { "target", _targetAnimator.name },
                    { "avatarPath", avatarPath },
                    { "avatarValid", avatar.isValid },
                    { "avatarHuman", avatar.isHuman },
                    { "mappedCount", _mapping.Bones.Count },
                    { "missingRequired", missingRequired },
                    { "missingOptional", missingOptional },
                    { "assigned", true },
                });

            return avatar;
        }

        private static void SelectAndPing(Object target)
        {
            if (target == null)
                return;
            Selection.activeObject = target;
            EditorGUIUtility.PingObject(target);
        }
    }
}
#endif
