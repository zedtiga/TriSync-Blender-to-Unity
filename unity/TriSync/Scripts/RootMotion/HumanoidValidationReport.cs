#if UNITY_EDITOR
using System.Collections.Generic;
using UnityEngine;

namespace BlenderSyncVNext.RootMotion
{
    public sealed class HumanoidValidationReport
    {
        public sealed class Item
        {
            public readonly string Message;
            public readonly HumanBodyBones? PrimaryBone;
            public readonly HumanBodyBones? SecondaryBone;

            public Item(string message, HumanBodyBones? primaryBone = null, HumanBodyBones? secondaryBone = null)
            {
                Message = message;
                PrimaryBone = primaryBone;
                SecondaryBone = secondaryBone;
            }
        }

        public readonly List<Item> Errors = new List<Item>();
        public readonly List<Item> Warnings = new List<Item>();
        public readonly List<Item> Infos = new List<Item>();

        public bool HasErrors => Errors.Count > 0;
        public bool HasWarnings => Warnings.Count > 0;

        public static HumanoidValidationReport Run(HumanoidBoneMapper.MappingResult mapping)
        {
            var report = new HumanoidValidationReport();
            if (mapping == null)
            {
                report.Errors.Add(new Item("No humanoid mapping available. Run Auto Map first."));
                return report;
            }

            if (mapping.MissingRequired.Count == 0)
                report.Infos.Add(new Item("Required bones complete."));
            else
                foreach (var bone in mapping.MissingRequired)
                    report.Errors.Add(new Item($"Required bone missing: {HumanoidBoneMapper.ToHumanName(bone)}", bone));

            if (mapping.MissingOptional.Count > 0)
                report.Warnings.Add(new Item($"Optional bones missing: {string.Join(", ", mapping.MissingOptional.ConvertAll(HumanoidBoneMapper.ToHumanName))}"));

            CheckChain(report, mapping, "Body", HumanBodyBones.Hips, HumanBodyBones.Spine, HumanBodyBones.Chest, HumanBodyBones.UpperChest, HumanBodyBones.Neck, HumanBodyBones.Head);
            CheckChain(report, mapping, "Left arm", HumanBodyBones.LeftShoulder, HumanBodyBones.LeftUpperArm, HumanBodyBones.LeftLowerArm, HumanBodyBones.LeftHand);
            CheckChain(report, mapping, "Right arm", HumanBodyBones.RightShoulder, HumanBodyBones.RightUpperArm, HumanBodyBones.RightLowerArm, HumanBodyBones.RightHand);
            CheckChain(report, mapping, "Left leg", HumanBodyBones.LeftUpperLeg, HumanBodyBones.LeftLowerLeg, HumanBodyBones.LeftFoot, HumanBodyBones.LeftToes);
            CheckChain(report, mapping, "Right leg", HumanBodyBones.RightUpperLeg, HumanBodyBones.RightLowerLeg, HumanBodyBones.RightFoot, HumanBodyBones.RightToes);
            CheckDuplicates(report, mapping);
            CheckLengths(report, mapping);
            CheckSymmetry(report, mapping);
            GuessPose(report, mapping);

            return report;
        }

        private static void CheckChain(HumanoidValidationReport report, HumanoidBoneMapper.MappingResult mapping, string label, params HumanBodyBones[] chain)
        {
            var checkedAny = false;
            for (var i = 0; i < chain.Length - 1; i++)
            {
                if (!mapping.Bones.TryGetValue(chain[i], out var parent) || parent == null)
                    continue;
                if (!mapping.Bones.TryGetValue(chain[i + 1], out var child) || child == null)
                    continue;
                checkedAny = true;
                if (!IsAncestor(parent, child))
                    report.Errors.Add(new Item($"{label} chain invalid: {HumanoidBoneMapper.ToHumanName(chain[i + 1])} is not under {HumanoidBoneMapper.ToHumanName(chain[i])}.", chain[i], chain[i + 1]));
            }
            if (checkedAny)
                report.Infos.Add(new Item($"{label} hierarchy chain checked."));
        }

        private static void CheckDuplicates(HumanoidValidationReport report, HumanoidBoneMapper.MappingResult mapping)
        {
            var seen = new Dictionary<Transform, HumanBodyBones>();
            foreach (var kvp in mapping.Bones)
            {
                if (kvp.Value == null)
                    continue;
                if (seen.TryGetValue(kvp.Value, out var previous))
                    report.Warnings.Add(new Item($"Duplicate transform mapped: {kvp.Value.name} is both {HumanoidBoneMapper.ToHumanName(previous)} and {HumanoidBoneMapper.ToHumanName(kvp.Key)}.", previous, kvp.Key));
                else
                    seen[kvp.Value] = kvp.Key;
            }
        }

        private static void CheckLengths(HumanoidValidationReport report, HumanoidBoneMapper.MappingResult mapping)
        {
            CheckSegmentLength(report, mapping, HumanBodyBones.LeftUpperArm, HumanBodyBones.LeftLowerArm);
            CheckSegmentLength(report, mapping, HumanBodyBones.LeftLowerArm, HumanBodyBones.LeftHand);
            CheckSegmentLength(report, mapping, HumanBodyBones.RightUpperArm, HumanBodyBones.RightLowerArm);
            CheckSegmentLength(report, mapping, HumanBodyBones.RightLowerArm, HumanBodyBones.RightHand);
            CheckSegmentLength(report, mapping, HumanBodyBones.LeftUpperLeg, HumanBodyBones.LeftLowerLeg);
            CheckSegmentLength(report, mapping, HumanBodyBones.LeftLowerLeg, HumanBodyBones.LeftFoot);
            CheckSegmentLength(report, mapping, HumanBodyBones.RightUpperLeg, HumanBodyBones.RightLowerLeg);
            CheckSegmentLength(report, mapping, HumanBodyBones.RightLowerLeg, HumanBodyBones.RightFoot);
        }

        private static void CheckSegmentLength(HumanoidValidationReport report, HumanoidBoneMapper.MappingResult mapping, HumanBodyBones a, HumanBodyBones b)
        {
            if (!mapping.Bones.TryGetValue(a, out var ta) || ta == null || !mapping.Bones.TryGetValue(b, out var tb) || tb == null)
                return;
            var distance = Vector3.Distance(ta.position, tb.position);
            if (distance < 0.0001f)
                report.Errors.Add(new Item($"Zero-length segment: {HumanoidBoneMapper.ToHumanName(a)} -> {HumanoidBoneMapper.ToHumanName(b)}.", a, b));
        }

        private static void CheckSymmetry(HumanoidValidationReport report, HumanoidBoneMapper.MappingResult mapping)
        {
            if (!TryGet(mapping, HumanBodyBones.LeftUpperArm, out var lua) || !TryGet(mapping, HumanBodyBones.RightUpperArm, out var rua) ||
                !TryGet(mapping, HumanBodyBones.LeftUpperLeg, out var lul) || !TryGet(mapping, HumanBodyBones.RightUpperLeg, out var rul))
                return;

            var shoulderMidX = (lua.position.x + rua.position.x) * 0.5f;
            var hipMidX = (lul.position.x + rul.position.x) * 0.5f;
            if (Mathf.Sign(lua.position.x - shoulderMidX) == Mathf.Sign(rua.position.x - shoulderMidX))
                report.Warnings.Add(new Item("Left/right upper arms appear on the same side in world X. Check character orientation or side mapping.", HumanBodyBones.LeftUpperArm, HumanBodyBones.RightUpperArm));
            if (Mathf.Sign(lul.position.x - hipMidX) == Mathf.Sign(rul.position.x - hipMidX))
                report.Warnings.Add(new Item("Left/right upper legs appear on the same side in world X. Check character orientation or side mapping.", HumanBodyBones.LeftUpperLeg, HumanBodyBones.RightUpperLeg));
            report.Infos.Add(new Item("Basic left/right symmetry checked."));
        }

        private static void GuessPose(HumanoidValidationReport report, HumanoidBoneMapper.MappingResult mapping)
        {
            if (!TryGet(mapping, HumanBodyBones.LeftUpperArm, out var lua) || !TryGet(mapping, HumanBodyBones.LeftLowerArm, out var lla) ||
                !TryGet(mapping, HumanBodyBones.RightUpperArm, out var rua) || !TryGet(mapping, HumanBodyBones.RightLowerArm, out var rla))
                return;

            var leftDir = (lla.position - lua.position).normalized;
            var rightDir = (rla.position - rua.position).normalized;
            var leftHorizontal = Mathf.Abs(Vector3.Dot(leftDir, Vector3.up));
            var rightHorizontal = Mathf.Abs(Vector3.Dot(rightDir, Vector3.up));
            var avg = (leftHorizontal + rightHorizontal) * 0.5f;
            if (avg < 0.35f)
                report.Infos.Add(new Item("Pose guess: likely T-Pose or near T-Pose.", HumanBodyBones.LeftUpperArm, HumanBodyBones.RightUpperArm));
            else if (avg < 0.8f)
                report.Infos.Add(new Item("Pose guess: likely A-Pose.", HumanBodyBones.LeftUpperArm, HumanBodyBones.RightUpperArm));
            else
                report.Warnings.Add(new Item("Pose guess: arms appear mostly vertical; retargeting quality may need review.", HumanBodyBones.LeftUpperArm, HumanBodyBones.RightUpperArm));
        }

        private static bool TryGet(HumanoidBoneMapper.MappingResult mapping, HumanBodyBones bone, out Transform transform)
        {
            return mapping.Bones.TryGetValue(bone, out transform) && transform != null;
        }

        private static bool IsAncestor(Transform ancestor, Transform child)
        {
            var t = child;
            while (t != null)
            {
                if (t == ancestor)
                    return true;
                t = t.parent;
            }
            return false;
        }
    }
}
#endif
