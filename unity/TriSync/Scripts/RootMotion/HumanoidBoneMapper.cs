#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace BlenderSyncVNext.RootMotion
{
    public static class HumanoidBoneMapper
    {
        public sealed class MappingResult
        {
            public readonly Dictionary<HumanBodyBones, Transform> Bones = new Dictionary<HumanBodyBones, Transform>();
            public readonly List<HumanBodyBones> MissingRequired = new List<HumanBodyBones>();
            public readonly List<HumanBodyBones> MissingOptional = new List<HumanBodyBones>();

            public int ValidBoneCount;
            public int BridgeBoneCount;
            public string ValidBoneSource = "Unknown";

            public bool HasRequired => MissingRequired.Count == 0;
        }

        private static readonly HumanBodyBones[] RequiredBones =
        {
            HumanBodyBones.Hips,
            HumanBodyBones.Spine,
            HumanBodyBones.Head,
            HumanBodyBones.LeftUpperLeg,
            HumanBodyBones.LeftLowerLeg,
            HumanBodyBones.LeftFoot,
            HumanBodyBones.RightUpperLeg,
            HumanBodyBones.RightLowerLeg,
            HumanBodyBones.RightFoot,
            HumanBodyBones.LeftUpperArm,
            HumanBodyBones.LeftLowerArm,
            HumanBodyBones.LeftHand,
            HumanBodyBones.RightUpperArm,
            HumanBodyBones.RightLowerArm,
            HumanBodyBones.RightHand,
        };

        private static readonly HumanBodyBones[] OptionalBones =
        {
            HumanBodyBones.Chest,
            HumanBodyBones.UpperChest,
            HumanBodyBones.Neck,
            HumanBodyBones.LeftShoulder,
            HumanBodyBones.RightShoulder,
            HumanBodyBones.LeftToes,
            HumanBodyBones.RightToes,
            HumanBodyBones.LeftEye,
            HumanBodyBones.RightEye,
            HumanBodyBones.Jaw,
        };

        public static MappingResult AutoMap(Transform root)
        {
            var result = new MappingResult();
            if (root == null)
                return result;

            var validBones = BuildCandidateBones(root, out var validCount, out var bridgeCount, out var source);
            result.ValidBoneCount = validCount;
            result.BridgeBoneCount = bridgeCount;
            result.ValidBoneSource = source;

            var mappedBones = BlenderSyncHumanoidAutoMapper.MapBones(root, validBones);
            foreach (var kvp in mappedBones)
            {
                var index = (int)kvp.Key;
                if (index >= 0 && index < (int)HumanBodyBones.LastBone && kvp.Value != null)
                    result.Bones[kvp.Key] = kvp.Value;
            }

            RefreshMissing(result);
            return result;
        }

        private static Dictionary<Transform, bool> BuildCandidateBones(Transform root, out int validCount, out int bridgeCount, out string source)
        {
            var validBones = new Dictionary<Transform, bool>();
            if (root == null)
            {
                validCount = 0;
                bridgeCount = 0;
                source = "None";
                return validBones;
            }

            foreach (var smr in root.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                if (smr.rootBone != null)
                    validBones[smr.rootBone] = true;
                foreach (var bone in smr.bones)
                    if (bone != null)
                        validBones[bone] = true;
            }

            if (validBones.Count > 0)
            {
                var realBones = validBones.Keys.ToList();
                foreach (var bone in realBones)
                {
                    var t = bone.parent;
                    while (t != null)
                    {
                        if (!validBones.ContainsKey(t))
                            validBones[t] = false;
                        if (t == root)
                            break;
                        t = t.parent;
                    }
                }

                validCount = validBones.Count(kvp => kvp.Value);
                bridgeCount = validBones.Count(kvp => !kvp.Value);
                source = "SkinnedMeshRenderer.bones";
                return validBones;
            }

            foreach (var transform in root.GetComponentsInChildren<Transform>(true))
                validBones[transform] = true;
            validCount = validBones.Count;
            bridgeCount = 0;
            source = "Fallback: all transforms";
            return validBones;
        }

        public static void RefreshMissing(MappingResult result)
        {
            if (result == null)
                return;

            result.MissingRequired.Clear();
            result.MissingOptional.Clear();
            foreach (var bone in RequiredBones)
                if (!result.Bones.TryGetValue(bone, out var transform) || transform == null)
                    result.MissingRequired.Add(bone);
            foreach (var bone in OptionalBones)
                if (!result.Bones.TryGetValue(bone, out var transform) || transform == null)
                    result.MissingOptional.Add(bone);
        }

        public static MappingResult FromHumanDescription(Transform root, HumanDescription humanDescription)
        {
            var result = new MappingResult();
            if (root == null || humanDescription.human == null)
            {
                RefreshMissing(result);
                return result;
            }

            var transformsByName = root.GetComponentsInChildren<Transform>(true)
                .GroupBy(t => t.name)
                .ToDictionary(g => g.Key, g => g.First());
            var humanByName = new Dictionary<string, HumanBodyBones>();
            for (var i = 0; i < HumanTrait.BoneName.Length && i < (int)HumanBodyBones.LastBone; i++)
                humanByName[HumanTrait.BoneName[i]] = (HumanBodyBones)i;

            foreach (var humanBone in humanDescription.human ?? Array.Empty<HumanBone>())
            {
                if (string.IsNullOrEmpty(humanBone.humanName) || string.IsNullOrEmpty(humanBone.boneName))
                    continue;
                if (!humanByName.TryGetValue(humanBone.humanName, out var bodyBone))
                    continue;
                if (!transformsByName.TryGetValue(humanBone.boneName, out var transform))
                    continue;
                result.Bones[bodyBone] = transform;
            }

            RefreshMissing(result);
            return result;
        }

        public static HumanDescription BuildHumanDescription(Transform root, MappingResult mapping)
        {
            return BuildHumanDescription(root, mapping, null);
        }

        public static HumanDescription BuildHumanDescription(Transform root, MappingResult mapping, IReadOnlyDictionary<HumanBodyBones, HumanLimit> limits)
        {
            if (root == null)
                throw new ArgumentNullException(nameof(root));
            if (mapping == null)
                throw new ArgumentNullException(nameof(mapping));

            var humanBones = new List<HumanBone>();
            foreach (var kvp in mapping.Bones.OrderBy(k => (int)k.Key))
            {
                if (kvp.Value == null)
                    continue;
                var limit = limits != null && limits.TryGetValue(kvp.Key, out var customLimit)
                    ? customLimit
                    : new HumanLimit { useDefaultValues = true };
                humanBones.Add(new HumanBone
                {
                    boneName = kvp.Value.name,
                    humanName = ToUnityHumanName(kvp.Key),
                    limit = limit,
                });
            }

            var skeletonBones = root.GetComponentsInChildren<Transform>(true)
                .Select(t => new SkeletonBone
                {
                    name = t.name,
                    position = t.localPosition,
                    rotation = t.localRotation,
                    scale = t.localScale,
                })
                .ToArray();

            return new HumanDescription
            {
                human = humanBones.ToArray(),
                skeleton = skeletonBones,
                upperArmTwist = 0.5f,
                lowerArmTwist = 0.5f,
                upperLegTwist = 0.5f,
                lowerLegTwist = 0.5f,
                armStretch = 0.05f,
                legStretch = 0.05f,
                feetSpacing = 0f,
                hasTranslationDoF = false,
            };
        }

        public static IReadOnlyList<HumanBodyBones> GetRequiredBones() => RequiredBones;
        public static IReadOnlyList<HumanBodyBones> GetOptionalBones() => OptionalBones;

        public static string ToHumanName(HumanBodyBones bone)
        {
            return ToUnityHumanName(bone);
        }

        public static string ToUnityHumanName(HumanBodyBones bone)
        {
            var index = (int)bone;
            if (index >= 0 && index < HumanTrait.BoneName.Length)
                return HumanTrait.BoneName[index];
            return bone.ToString();
        }
    }
}
#endif
