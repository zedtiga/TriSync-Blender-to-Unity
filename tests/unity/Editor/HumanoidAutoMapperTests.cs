using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using NUnit.Framework;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class HumanoidAutoMapperTests
    {
        private const string MapperTypeName = "BlenderSyncVNext.RootMotion.HumanoidBoneMapper";
        private readonly List<GameObject> _roots = new List<GameObject>();
        private MethodInfo _autoMap;
        private FieldInfo _bonesField;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _autoMap = ProductApi.RequireStaticMethod(MapperTypeName, "AutoMap", 1);
            _bonesField = _autoMap.ReturnType.GetField("Bones", BindingFlags.Public | BindingFlags.Instance);
            Assert.That(_bonesField, Is.Not.Null);
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var root in _roots)
            {
                if (root != null)
                    Object.DestroyImmediate(root);
            }
            _roots.Clear();
        }

        [Test]
        public void GenericHumanBodyBoneNamesMapRequiredBodyAndFingerChain()
        {
            var expected = BuildRig(GenericNames(), includeArmTwists: false);
            var leftHand = expected[HumanBodyBones.LeftHand];
            var metacarpal = Add(leftHand, "LeftThumbMetacarpal");
            var thumb1 = Add(metacarpal, "LeftHandThumb1");
            var thumb2 = Add(thumb1, "LeftHandThumb2");
            var thumb3 = Add(thumb2, "LeftHandThumb3");

            var mapped = AutoMap(expected[HumanBodyBones.Hips].root);

            AssertExpected(mapped, expected);
            AssertMapped(mapped, HumanBodyBones.LeftThumbProximal, thumb1);
            AssertMapped(mapped, HumanBodyBones.LeftThumbIntermediate, thumb2);
            AssertMapped(mapped, HumanBodyBones.LeftThumbDistal, thumb3);
        }

        [Test]
        public void MixamoPrefixAndNumberedSpineMapDeterministically()
        {
            var expected = BuildRig(MixamoNames(), includeArmTwists: false);

            var mapped = AutoMap(expected[HumanBodyBones.Hips].root);

            AssertExpected(mapped, expected);
        }

        [Test]
        public void RigifyStylePrefixesAndSideSuffixesMapDeterministically()
        {
            var expected = BuildRig(RigifyNames(), includeArmTwists: false);

            var mapped = AutoMap(expected[HumanBodyBones.Hips].root);

            AssertExpected(mapped, expected);
        }

        [Test]
        public void BipedPrefixSideTokensAndNumberedSpineMapDeterministically()
        {
            var expected = BuildRig(BipedNames(), includeArmTwists: false);

            var mapped = AutoMap(expected[HumanBodyBones.Hips].root);

            AssertExpected(mapped, expected);
        }

        [Test]
        public void UnrealStyleNamesMapWhileTwistHelpersAreIgnored()
        {
            var expected = BuildRig(UnrealNames(), includeArmTwists: true, out var helpers);

            var mapped = AutoMap(expected[HumanBodyBones.Hips].root);

            AssertExpected(mapped, expected);
            foreach (var value in mapped.Values)
                Assert.That(helpers.Contains(value as Transform), Is.False);
        }

        [Test]
        public void ControlOnlyNamesRemainUnmappedInsteadOfGuessing()
        {
            var root = NewRoot();
            var hips = Add(root.transform, "pelvis");
            Add(hips, "arm_ik_l");
            Add(hips, "arm_ik_r");
            Add(hips, "knee_pole_l");
            Add(hips, "hand_target_r");

            var mapped = AutoMap(root.transform);

            AssertMapped(mapped, HumanBodyBones.Hips, hips);
            AssertUnmapped(mapped, HumanBodyBones.LeftUpperArm);
            AssertUnmapped(mapped, HumanBodyBones.RightUpperArm);
            AssertUnmapped(mapped, HumanBodyBones.LeftLowerLeg);
            AssertUnmapped(mapped, HumanBodyBones.RightHand);
        }

        private IDictionary AutoMap(Transform root)
        {
            var result = _autoMap.Invoke(null, new object[] { root });
            return (IDictionary)_bonesField.GetValue(result);
        }

        private Dictionary<HumanBodyBones, Transform> BuildRig(
            IReadOnlyDictionary<HumanBodyBones, string> names,
            bool includeArmTwists)
        {
            return BuildRig(names, includeArmTwists, out _);
        }

        private Dictionary<HumanBodyBones, Transform> BuildRig(
            IReadOnlyDictionary<HumanBodyBones, string> names,
            bool includeArmTwists,
            out List<Transform> helpers)
        {
            helpers = new List<Transform>();
            var root = NewRoot();
            var result = new Dictionary<HumanBodyBones, Transform>();

            var hips = AddRole(root.transform, HumanBodyBones.Hips, names, result);
            var spine = AddRole(hips, HumanBodyBones.Spine, names, result);
            var chest = AddRole(spine, HumanBodyBones.Chest, names, result);
            var upperChest = AddRole(chest, HumanBodyBones.UpperChest, names, result);
            var neck = AddRole(upperChest, HumanBodyBones.Neck, names, result);
            AddRole(neck, HumanBodyBones.Head, names, result);

            BuildLeg(hips, names, result, true);
            BuildLeg(hips, names, result, false);
            BuildArm(upperChest, names, result, helpers, includeArmTwists, true);
            BuildArm(upperChest, names, result, helpers, includeArmTwists, false);
            return result;
        }

        private static void BuildLeg(
            Transform hips,
            IReadOnlyDictionary<HumanBodyBones, string> names,
            IDictionary<HumanBodyBones, Transform> result,
            bool left)
        {
            var upperRole = left ? HumanBodyBones.LeftUpperLeg : HumanBodyBones.RightUpperLeg;
            var lowerRole = left ? HumanBodyBones.LeftLowerLeg : HumanBodyBones.RightLowerLeg;
            var footRole = left ? HumanBodyBones.LeftFoot : HumanBodyBones.RightFoot;
            var toesRole = left ? HumanBodyBones.LeftToes : HumanBodyBones.RightToes;
            var upper = AddRole(hips, upperRole, names, result);
            var lower = AddRole(upper, lowerRole, names, result);
            var foot = AddRole(lower, footRole, names, result);
            AddRole(foot, toesRole, names, result);
        }

        private static void BuildArm(
            Transform chest,
            IReadOnlyDictionary<HumanBodyBones, string> names,
            IDictionary<HumanBodyBones, Transform> result,
            ICollection<Transform> helpers,
            bool includeTwist,
            bool left)
        {
            var shoulderRole = left ? HumanBodyBones.LeftShoulder : HumanBodyBones.RightShoulder;
            var upperRole = left ? HumanBodyBones.LeftUpperArm : HumanBodyBones.RightUpperArm;
            var lowerRole = left ? HumanBodyBones.LeftLowerArm : HumanBodyBones.RightLowerArm;
            var handRole = left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand;
            var shoulder = AddRole(chest, shoulderRole, names, result);
            var upper = AddRole(shoulder, upperRole, names, result);
            var lowerParent = upper;
            if (includeTwist)
            {
                lowerParent = Add(upper, left ? "upperarm_twist_01_l" : "upperarm_twist_01_r");
                helpers.Add(lowerParent);
            }
            var lower = AddRole(lowerParent, lowerRole, names, result);
            AddRole(lower, handRole, names, result);
        }

        private static Transform AddRole(
            Transform parent,
            HumanBodyBones role,
            IReadOnlyDictionary<HumanBodyBones, string> names,
            IDictionary<HumanBodyBones, Transform> result)
        {
            var transform = Add(parent, names[role]);
            result[role] = transform;
            return transform;
        }

        private GameObject NewRoot()
        {
            var root = new GameObject("CharacterRoot");
            _roots.Add(root);
            return root;
        }

        private static Transform Add(Transform parent, string name)
        {
            var child = new GameObject(name).transform;
            child.SetParent(parent, false);
            return child;
        }

        private static void AssertExpected(IDictionary mapped, IReadOnlyDictionary<HumanBodyBones, Transform> expected)
        {
            foreach (var entry in expected)
                AssertMapped(mapped, entry.Key, entry.Value);
        }

        private static void AssertMapped(IDictionary mapped, HumanBodyBones bone, Transform expected)
        {
            Assert.That(mapped.Contains(bone), Is.True, $"Expected {bone} to be mapped.");
            Assert.That(mapped[bone], Is.SameAs(expected), $"Unexpected transform mapped to {bone}.");
        }

        private static void AssertUnmapped(IDictionary mapped, HumanBodyBones bone)
        {
            Assert.That(mapped.Contains(bone), Is.False, $"Expected {bone} to remain unmapped.");
        }

        private static Dictionary<HumanBodyBones, string> GenericNames()
        {
            return Names(
                "Hips", "Spine", "Chest", "UpperChest", "Neck", "Head",
                "LeftShoulder", "LeftUpperArm", "LeftLowerArm", "LeftHand",
                "RightShoulder", "RightUpperArm", "RightLowerArm", "RightHand",
                "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "LeftToes",
                "RightUpperLeg", "RightLowerLeg", "RightFoot", "RightToes");
        }

        private static Dictionary<HumanBodyBones, string> MixamoNames()
        {
            const string prefix = "mixamorig:";
            return Names(
                prefix + "Hips", prefix + "Spine", prefix + "Spine1", prefix + "Spine2", prefix + "Neck", prefix + "Head",
                prefix + "LeftShoulder", prefix + "LeftArm", prefix + "LeftForeArm", prefix + "LeftHand",
                prefix + "RightShoulder", prefix + "RightArm", prefix + "RightForeArm", prefix + "RightHand",
                prefix + "LeftUpLeg", prefix + "LeftLeg", prefix + "LeftFoot", prefix + "LeftToeBase",
                prefix + "RightUpLeg", prefix + "RightLeg", prefix + "RightFoot", prefix + "RightToeBase");
        }

        private static Dictionary<HumanBodyBones, string> UnrealNames()
        {
            return Names(
                "pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "head",
                "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l",
                "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
                "thigh_l", "calf_l", "foot_l", "ball_l",
                "thigh_r", "calf_r", "foot_r", "ball_r");
        }

        private static Dictionary<HumanBodyBones, string> RigifyNames()
        {
            return Names(
                "DEF-pelvis", "DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-neck", "DEF-head",
                "DEF-shoulder.L", "DEF-upper_arm.L", "DEF-forearm.L", "DEF-hand.L",
                "DEF-shoulder.R", "DEF-upper_arm.R", "DEF-forearm.R", "DEF-hand.R",
                "DEF-thigh.L", "DEF-shin.L", "DEF-foot.L", "DEF-toe.L",
                "DEF-thigh.R", "DEF-shin.R", "DEF-foot.R", "DEF-toe.R");
        }

        private static Dictionary<HumanBodyBones, string> BipedNames()
        {
            const string prefix = "Bip01 ";
            return Names(
                prefix + "Pelvis", prefix + "Spine", prefix + "Spine1", prefix + "Spine2", prefix + "Neck", prefix + "Head",
                prefix + "L Clavicle", prefix + "L UpperArm", prefix + "L Forearm", prefix + "L Hand",
                prefix + "R Clavicle", prefix + "R UpperArm", prefix + "R Forearm", prefix + "R Hand",
                prefix + "L Thigh", prefix + "L Calf", prefix + "L Foot", prefix + "L Toe0",
                prefix + "R Thigh", prefix + "R Calf", prefix + "R Foot", prefix + "R Toe0");
        }

        private static Dictionary<HumanBodyBones, string> Names(
            string hips, string spine, string chest, string upperChest, string neck, string head,
            string leftShoulder, string leftUpperArm, string leftLowerArm, string leftHand,
            string rightShoulder, string rightUpperArm, string rightLowerArm, string rightHand,
            string leftUpperLeg, string leftLowerLeg, string leftFoot, string leftToes,
            string rightUpperLeg, string rightLowerLeg, string rightFoot, string rightToes)
        {
            return new Dictionary<HumanBodyBones, string>
            {
                [HumanBodyBones.Hips] = hips,
                [HumanBodyBones.Spine] = spine,
                [HumanBodyBones.Chest] = chest,
                [HumanBodyBones.UpperChest] = upperChest,
                [HumanBodyBones.Neck] = neck,
                [HumanBodyBones.Head] = head,
                [HumanBodyBones.LeftShoulder] = leftShoulder,
                [HumanBodyBones.LeftUpperArm] = leftUpperArm,
                [HumanBodyBones.LeftLowerArm] = leftLowerArm,
                [HumanBodyBones.LeftHand] = leftHand,
                [HumanBodyBones.RightShoulder] = rightShoulder,
                [HumanBodyBones.RightUpperArm] = rightUpperArm,
                [HumanBodyBones.RightLowerArm] = rightLowerArm,
                [HumanBodyBones.RightHand] = rightHand,
                [HumanBodyBones.LeftUpperLeg] = leftUpperLeg,
                [HumanBodyBones.LeftLowerLeg] = leftLowerLeg,
                [HumanBodyBones.LeftFoot] = leftFoot,
                [HumanBodyBones.LeftToes] = leftToes,
                [HumanBodyBones.RightUpperLeg] = rightUpperLeg,
                [HumanBodyBones.RightLowerLeg] = rightLowerLeg,
                [HumanBodyBones.RightFoot] = rightFoot,
                [HumanBodyBones.RightToes] = rightToes,
            };
        }
    }
}
