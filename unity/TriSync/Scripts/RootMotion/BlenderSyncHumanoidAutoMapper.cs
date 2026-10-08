#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;

namespace BlenderSyncVNext.RootMotion
{
    // Project-owned heuristic mapper based on public HumanBodyBones roles and
    // common rig naming conventions. Low-confidence roles remain unmapped.
    internal static class BlenderSyncHumanoidAutoMapper
    {
        private enum BoneSide
        {
            None,
            Left,
            Right,
        }

        private sealed class Candidate
        {
            public Transform Transform;
            public string Core;
            public List<string> Tokens;
            public List<int> Numbers;
            public BoneSide Side;
            public bool IsDirectBone;
            public bool IsHelper;
            public int Depth;
        }

        private static readonly HashSet<string> IgnoredTokens = new HashSet<string>(StringComparer.Ordinal)
        {
            "bip",
            "biped",
            "bone",
            "bones",
            "char",
            "def",
            "deform",
            "j",
            "joint",
            "mixamorig",
            "rig",
            "skeleton",
            "valve",
        };

        private static readonly HashSet<string> HelperTokens = new HashSet<string>(StringComparer.Ordinal)
        {
            "control",
            "ctrl",
            "end",
            "helper",
            "ik",
            "mch",
            "metacarpal",
            "org",
            "pole",
            "roll",
            "socket",
            "target",
            "twist",
        };

        public static Dictionary<HumanBodyBones, Transform> MapBones(
            Transform root,
            IReadOnlyDictionary<Transform, bool> validBones)
        {
            var mapping = new Dictionary<HumanBodyBones, Transform>();
            if (root == null)
                return mapping;

            var candidates = BuildCandidates(root, validBones);
            var used = new HashSet<Transform>();

            MapBody(candidates, mapping, used);
            MapLeg(candidates, mapping, used, BoneSide.Left);
            MapLeg(candidates, mapping, used, BoneSide.Right);
            MapArm(candidates, mapping, used, BoneSide.Left);
            MapArm(candidates, mapping, used, BoneSide.Right);
            MapHeadDetails(candidates, mapping, used);
            MapFingers(candidates, mapping, used, BoneSide.Left);
            MapFingers(candidates, mapping, used, BoneSide.Right);
            return mapping;
        }

        private static List<Candidate> BuildCandidates(
            Transform root,
            IReadOnlyDictionary<Transform, bool> validBones)
        {
            var source = new Dictionary<Transform, bool>();
            if (validBones != null)
            {
                foreach (var entry in validBones)
                {
                    if (entry.Key != null && IsDescendantOrSelf(entry.Key, root))
                        source[entry.Key] = entry.Value;
                }
            }

            if (source.Count == 0)
            {
                foreach (var transform in root.GetComponentsInChildren<Transform>(true))
                    source[transform] = true;
            }

            return source
                .Select(entry => AnalyzeCandidate(root, entry.Key, entry.Value))
                .Where(candidate => candidate != null)
                .OrderBy(candidate => candidate.Depth)
                .ThenBy(candidate => candidate.Transform.name, StringComparer.Ordinal)
                .ToList();
        }

        private static Candidate AnalyzeCandidate(Transform root, Transform transform, bool isDirectBone)
        {
            if (transform == null)
                return null;

            var tokens = Tokenize(transform.name);
            var side = DetectSide(tokens);
            var semanticTokens = tokens
                .Where(token => !IgnoredTokens.Contains(token))
                .Where(token => token != "left" && token != "right" && token != "l" && token != "r")
                .Where(token => !IsNumber(token))
                .ToList();
            var numbers = tokens
                .Select(token => int.TryParse(token, out var value) ? (int?)value : null)
                .Where(value => value.HasValue)
                .Select(value => value.Value)
                .ToList();

            return new Candidate
            {
                Transform = transform,
                Core = string.Concat(semanticTokens),
                Tokens = tokens,
                Numbers = numbers,
                Side = side,
                IsDirectBone = isDirectBone,
                IsHelper = tokens.Any(HelperTokens.Contains),
                Depth = DepthFromRoot(transform, root),
            };
        }

        private static void MapBody(
            IReadOnlyList<Candidate> candidates,
            IDictionary<HumanBodyBones, Transform> mapping,
            ISet<Transform> used)
        {
            MapBest(candidates, mapping, used, HumanBodyBones.Hips, null);
            MapBest(candidates, mapping, used, HumanBodyBones.Head, null);

            mapping.TryGetValue(HumanBodyBones.Hips, out var hips);
            mapping.TryGetValue(HumanBodyBones.Head, out var head);
            var path = BuildPathBetween(hips, head);
            var pathCandidates = candidates
                .Where(candidate => path.Contains(candidate.Transform))
                .Where(candidate => !candidate.IsHelper)
                .OrderBy(candidate => candidate.Depth)
                .ToList();

            var torso = pathCandidates.Where(IsTorsoCandidate).ToList();
            var spine = torso.FirstOrDefault(candidate => candidate.Core == "spine")
                ?? torso.FirstOrDefault();
            TryAssign(mapping, used, HumanBodyBones.Spine, spine);

            var chest = torso.FirstOrDefault(candidate => candidate.Core == "chest" || candidate.Core == "thorax");
            if (chest == null && torso.Count >= 2)
                chest = torso[torso.Count >= 3 ? torso.Count - 2 : 1];
            TryAssign(mapping, used, HumanBodyBones.Chest, chest);

            var upperChest = torso.FirstOrDefault(candidate => candidate.Core == "upperchest");
            if (upperChest == null && torso.Count >= 3)
                upperChest = torso[torso.Count - 1];
            TryAssign(mapping, used, HumanBodyBones.UpperChest, upperChest);

            var neck = pathCandidates.FirstOrDefault(candidate => candidate.Core == "neck");
            TryAssign(mapping, used, HumanBodyBones.Neck, neck);

            if (!mapping.ContainsKey(HumanBodyBones.Spine))
                MapBest(candidates, mapping, used, HumanBodyBones.Spine, hips);
            var chestAnchor = FirstMapped(mapping, HumanBodyBones.Spine, HumanBodyBones.Hips);
            if (!mapping.ContainsKey(HumanBodyBones.Chest))
                MapBest(candidates, mapping, used, HumanBodyBones.Chest, chestAnchor);
            var upperChestAnchor = FirstMapped(mapping, HumanBodyBones.Chest, HumanBodyBones.Spine, HumanBodyBones.Hips);
            if (!mapping.ContainsKey(HumanBodyBones.UpperChest))
                MapBest(candidates, mapping, used, HumanBodyBones.UpperChest, upperChestAnchor);
            var neckAnchor = FirstMapped(
                mapping,
                HumanBodyBones.UpperChest,
                HumanBodyBones.Chest,
                HumanBodyBones.Spine,
                HumanBodyBones.Hips);
            if (!mapping.ContainsKey(HumanBodyBones.Neck))
            {
                MapBest(
                    candidates,
                    mapping,
                    used,
                    HumanBodyBones.Neck,
                    neckAnchor,
                    candidate => head == null || IsDescendant(head, candidate.Transform));
            }
        }

        private static void MapLeg(
            IReadOnlyList<Candidate> candidates,
            IDictionary<HumanBodyBones, Transform> mapping,
            ISet<Transform> used,
            BoneSide side)
        {
            var upper = side == BoneSide.Left ? HumanBodyBones.LeftUpperLeg : HumanBodyBones.RightUpperLeg;
            var lower = side == BoneSide.Left ? HumanBodyBones.LeftLowerLeg : HumanBodyBones.RightLowerLeg;
            var foot = side == BoneSide.Left ? HumanBodyBones.LeftFoot : HumanBodyBones.RightFoot;
            var toes = side == BoneSide.Left ? HumanBodyBones.LeftToes : HumanBodyBones.RightToes;

            mapping.TryGetValue(HumanBodyBones.Hips, out var hips);
            MapBest(candidates, mapping, used, upper, hips);
            mapping.TryGetValue(upper, out var upperTransform);
            MapBest(candidates, mapping, used, lower, upperTransform);
            mapping.TryGetValue(lower, out var lowerTransform);
            MapBest(candidates, mapping, used, foot, lowerTransform);
            mapping.TryGetValue(foot, out var footTransform);
            MapBest(candidates, mapping, used, toes, footTransform);
        }

        private static void MapArm(
            IReadOnlyList<Candidate> candidates,
            IDictionary<HumanBodyBones, Transform> mapping,
            ISet<Transform> used,
            BoneSide side)
        {
            var shoulder = side == BoneSide.Left ? HumanBodyBones.LeftShoulder : HumanBodyBones.RightShoulder;
            var upper = side == BoneSide.Left ? HumanBodyBones.LeftUpperArm : HumanBodyBones.RightUpperArm;
            var lower = side == BoneSide.Left ? HumanBodyBones.LeftLowerArm : HumanBodyBones.RightLowerArm;
            var hand = side == BoneSide.Left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand;
            var body = FirstMapped(
                mapping,
                HumanBodyBones.UpperChest,
                HumanBodyBones.Chest,
                HumanBodyBones.Spine,
                HumanBodyBones.Hips);

            MapBest(candidates, mapping, used, shoulder, body);
            var upperAnchor = mapping.TryGetValue(shoulder, out var shoulderTransform) ? shoulderTransform : body;
            MapBest(candidates, mapping, used, upper, upperAnchor);
            mapping.TryGetValue(upper, out var upperTransform);
            MapBest(candidates, mapping, used, lower, upperTransform);
            mapping.TryGetValue(lower, out var lowerTransform);
            MapBest(candidates, mapping, used, hand, lowerTransform);
        }

        private static void MapHeadDetails(
            IReadOnlyList<Candidate> candidates,
            IDictionary<HumanBodyBones, Transform> mapping,
            ISet<Transform> used)
        {
            mapping.TryGetValue(HumanBodyBones.Head, out var head);
            MapBest(candidates, mapping, used, HumanBodyBones.LeftEye, head);
            MapBest(candidates, mapping, used, HumanBodyBones.RightEye, head);
            MapBest(candidates, mapping, used, HumanBodyBones.Jaw, head);
        }

        private static void MapFingers(
            IReadOnlyList<Candidate> candidates,
            IDictionary<HumanBodyBones, Transform> mapping,
            ISet<Transform> used,
            BoneSide side)
        {
            var handBone = side == BoneSide.Left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand;
            if (!mapping.TryGetValue(handBone, out var hand) || hand == null)
                return;

            MapFinger(candidates, mapping, used, side, hand, new[] { "thumb" }, FingerRoles(side, "thumb"));
            MapFinger(candidates, mapping, used, side, hand, new[] { "index", "pointer" }, FingerRoles(side, "index"));
            MapFinger(candidates, mapping, used, side, hand, new[] { "middle" }, FingerRoles(side, "middle"));
            MapFinger(candidates, mapping, used, side, hand, new[] { "ring" }, FingerRoles(side, "ring"));
            MapFinger(candidates, mapping, used, side, hand, new[] { "little", "pinky" }, FingerRoles(side, "little"));
        }

        private static void MapFinger(
            IReadOnlyList<Candidate> candidates,
            IDictionary<HumanBodyBones, Transform> mapping,
            ISet<Transform> used,
            BoneSide side,
            Transform hand,
            IReadOnlyList<string> aliases,
            IReadOnlyList<HumanBodyBones> roles)
        {
            var fingerCandidates = candidates
                .Where(candidate => !candidate.IsHelper && !used.Contains(candidate.Transform))
                .Where(candidate => IsDescendant(candidate.Transform, hand))
                .Where(candidate => candidate.Side == BoneSide.None || candidate.Side == side)
                .Where(candidate => aliases.Any(alias => candidate.Core.Contains(alias)))
                .OrderBy(candidate => candidate.Depth)
                .ToList();
            if (fingerCandidates.Count == 0)
                return;

            var selected = new Candidate[3];
            foreach (var candidate in fingerCandidates)
            {
                var segment = FingerSegment(candidate);
                if (segment >= 0 && segment < selected.Length && selected[segment] == null)
                    selected[segment] = candidate;
            }

            var remaining = new Queue<Candidate>(fingerCandidates.Where(candidate => !selected.Contains(candidate)));
            for (var index = 0; index < selected.Length; index++)
            {
                if (selected[index] == null && remaining.Count > 0)
                    selected[index] = remaining.Dequeue();
                TryAssign(mapping, used, roles[index], selected[index]);
            }
        }

        private static int FingerSegment(Candidate candidate)
        {
            if (candidate.Tokens.Contains("proximal"))
                return 0;
            if (candidate.Tokens.Contains("intermediate"))
                return 1;
            if (candidate.Tokens.Contains("distal"))
                return 2;
            if (candidate.Numbers.Count == 0)
                return -1;
            var number = candidate.Numbers[candidate.Numbers.Count - 1];
            return number >= 1 && number <= 3 ? number - 1 : -1;
        }

        private static HumanBodyBones[] FingerRoles(BoneSide side, string finger)
        {
            if (side == BoneSide.Left)
            {
                switch (finger)
                {
                    case "thumb": return new[] { HumanBodyBones.LeftThumbProximal, HumanBodyBones.LeftThumbIntermediate, HumanBodyBones.LeftThumbDistal };
                    case "index": return new[] { HumanBodyBones.LeftIndexProximal, HumanBodyBones.LeftIndexIntermediate, HumanBodyBones.LeftIndexDistal };
                    case "middle": return new[] { HumanBodyBones.LeftMiddleProximal, HumanBodyBones.LeftMiddleIntermediate, HumanBodyBones.LeftMiddleDistal };
                    case "ring": return new[] { HumanBodyBones.LeftRingProximal, HumanBodyBones.LeftRingIntermediate, HumanBodyBones.LeftRingDistal };
                    default: return new[] { HumanBodyBones.LeftLittleProximal, HumanBodyBones.LeftLittleIntermediate, HumanBodyBones.LeftLittleDistal };
                }
            }

            switch (finger)
            {
                case "thumb": return new[] { HumanBodyBones.RightThumbProximal, HumanBodyBones.RightThumbIntermediate, HumanBodyBones.RightThumbDistal };
                case "index": return new[] { HumanBodyBones.RightIndexProximal, HumanBodyBones.RightIndexIntermediate, HumanBodyBones.RightIndexDistal };
                case "middle": return new[] { HumanBodyBones.RightMiddleProximal, HumanBodyBones.RightMiddleIntermediate, HumanBodyBones.RightMiddleDistal };
                case "ring": return new[] { HumanBodyBones.RightRingProximal, HumanBodyBones.RightRingIntermediate, HumanBodyBones.RightRingDistal };
                default: return new[] { HumanBodyBones.RightLittleProximal, HumanBodyBones.RightLittleIntermediate, HumanBodyBones.RightLittleDistal };
            }
        }

        private static void MapBest(
            IReadOnlyList<Candidate> candidates,
            IDictionary<HumanBodyBones, Transform> mapping,
            ISet<Transform> used,
            HumanBodyBones bone,
            Transform anchor,
            Func<Candidate, bool> extraFilter = null)
        {
            var best = candidates
                .Where(candidate => !used.Contains(candidate.Transform))
                .Where(candidate => extraFilter == null || extraFilter(candidate))
                .Select(candidate => new { Candidate = candidate, Score = Score(candidate, bone, anchor) })
                .Where(item => item.Score >= 100)
                .OrderByDescending(item => item.Score)
                .ThenBy(item => item.Candidate.Depth)
                .ThenBy(item => item.Candidate.Transform.name, StringComparer.Ordinal)
                .Select(item => item.Candidate)
                .FirstOrDefault();
            TryAssign(mapping, used, bone, best);
        }

        private static int Score(Candidate candidate, HumanBodyBones bone, Transform anchor)
        {
            if (candidate == null || candidate.Transform == null || candidate.IsHelper)
                return int.MinValue;

            var requiredSide = SideFor(bone);
            if (requiredSide == BoneSide.None)
            {
                if (candidate.Side != BoneSide.None)
                    return int.MinValue;
            }
            else if (candidate.Side != requiredSide)
            {
                return int.MinValue;
            }

            var aliases = AliasesFor(bone);
            if (!aliases.Contains(candidate.Core, StringComparer.Ordinal))
                return int.MinValue;

            var score = 120;
            if (candidate.IsDirectBone)
                score += 5;
            if (anchor != null)
            {
                var distance = DescendantDistance(candidate.Transform, anchor);
                if (distance <= 0)
                    return int.MinValue;
                score += Math.Max(0, 24 - distance);
            }
            return score;
        }

        private static string[] AliasesFor(HumanBodyBones bone)
        {
            switch (bone)
            {
                case HumanBodyBones.Hips: return new[] { "hips", "pelvis" };
                case HumanBodyBones.Spine: return new[] { "spine" };
                case HumanBodyBones.Chest: return new[] { "chest", "thorax" };
                case HumanBodyBones.UpperChest: return new[] { "upperchest" };
                case HumanBodyBones.Neck: return new[] { "neck" };
                case HumanBodyBones.Head: return new[] { "head" };
                case HumanBodyBones.LeftShoulder:
                case HumanBodyBones.RightShoulder:
                    return new[] { "shoulder", "clavicle", "collar" };
                case HumanBodyBones.LeftUpperArm:
                case HumanBodyBones.RightUpperArm:
                    return new[] { "upperarm", "arm" };
                case HumanBodyBones.LeftLowerArm:
                case HumanBodyBones.RightLowerArm:
                    return new[] { "lowerarm", "forearm" };
                case HumanBodyBones.LeftHand:
                case HumanBodyBones.RightHand:
                    return new[] { "hand", "wrist" };
                case HumanBodyBones.LeftUpperLeg:
                case HumanBodyBones.RightUpperLeg:
                    return new[] { "upperleg", "upleg", "thigh" };
                case HumanBodyBones.LeftLowerLeg:
                case HumanBodyBones.RightLowerLeg:
                    return new[] { "lowerleg", "leg", "shin", "calf" };
                case HumanBodyBones.LeftFoot:
                case HumanBodyBones.RightFoot:
                    return new[] { "foot", "ankle" };
                case HumanBodyBones.LeftToes:
                case HumanBodyBones.RightToes:
                    return new[] { "toes", "toe", "toebase", "ball" };
                case HumanBodyBones.LeftEye:
                case HumanBodyBones.RightEye:
                    return new[] { "eye" };
                case HumanBodyBones.Jaw:
                    return new[] { "jaw", "mandible" };
                default:
                    return Array.Empty<string>();
            }
        }

        private static BoneSide SideFor(HumanBodyBones bone)
        {
            var name = bone.ToString();
            if (name.StartsWith("Left", StringComparison.Ordinal))
                return BoneSide.Left;
            if (name.StartsWith("Right", StringComparison.Ordinal))
                return BoneSide.Right;
            return BoneSide.None;
        }

        private static BoneSide DetectSide(IReadOnlyCollection<string> tokens)
        {
            if (tokens.Contains("left") || tokens.Contains("l"))
                return BoneSide.Left;
            if (tokens.Contains("right") || tokens.Contains("r"))
                return BoneSide.Right;
            return BoneSide.None;
        }

        private static bool IsTorsoCandidate(Candidate candidate)
        {
            return candidate != null &&
                (candidate.Core == "spine" ||
                 candidate.Core == "chest" ||
                 candidate.Core == "upperchest" ||
                 candidate.Core == "thorax");
        }

        private static void TryAssign(
            IDictionary<HumanBodyBones, Transform> mapping,
            ISet<Transform> used,
            HumanBodyBones bone,
            Candidate candidate)
        {
            if (candidate == null || candidate.Transform == null || used.Contains(candidate.Transform))
                return;
            mapping[bone] = candidate.Transform;
            used.Add(candidate.Transform);
        }

        private static Transform FirstMapped(
            IDictionary<HumanBodyBones, Transform> mapping,
            params HumanBodyBones[] bones)
        {
            foreach (var bone in bones)
            {
                if (mapping.TryGetValue(bone, out var transform) && transform != null)
                    return transform;
            }
            return null;
        }

        private static HashSet<Transform> BuildPathBetween(Transform ancestor, Transform descendant)
        {
            var path = new HashSet<Transform>();
            if (ancestor == null || descendant == null || ancestor == descendant)
                return path;

            var cursor = descendant.parent;
            while (cursor != null && cursor != ancestor)
            {
                path.Add(cursor);
                cursor = cursor.parent;
            }
            if (cursor != ancestor)
                path.Clear();
            return path;
        }

        private static bool IsDescendantOrSelf(Transform transform, Transform ancestor)
        {
            return transform == ancestor || IsDescendant(transform, ancestor);
        }

        private static bool IsDescendant(Transform transform, Transform ancestor)
        {
            return DescendantDistance(transform, ancestor) > 0;
        }

        private static int DescendantDistance(Transform transform, Transform ancestor)
        {
            if (transform == null || ancestor == null || transform == ancestor)
                return 0;
            var distance = 0;
            var cursor = transform;
            while (cursor != null)
            {
                if (cursor == ancestor)
                    return distance;
                cursor = cursor.parent;
                distance++;
            }
            return -1;
        }

        private static int DepthFromRoot(Transform transform, Transform root)
        {
            var depth = 0;
            var cursor = transform;
            while (cursor != null && cursor != root)
            {
                cursor = cursor.parent;
                depth++;
            }
            return cursor == root ? depth : int.MaxValue;
        }

        private static bool IsNumber(string value)
        {
            return int.TryParse(value, out _);
        }

        private static List<string> Tokenize(string value)
        {
            var tokens = new List<string>();
            var current = new StringBuilder();
            var priorKind = 0;
            var priorCharacter = '\0';
            foreach (var character in value ?? string.Empty)
            {
                var kind = char.IsLetter(character) ? 1 : char.IsDigit(character) ? 2 : 0;
                var camelBoundary = kind == 1 && current.Length > 0 && char.IsUpper(character) &&
                    char.IsLower(priorCharacter);
                var kindBoundary = kind != 0 && priorKind != 0 && kind != priorKind;
                if (kind == 0 || camelBoundary || kindBoundary)
                {
                    FlushToken(tokens, current);
                    if (kind == 0)
                    {
                        priorKind = 0;
                        priorCharacter = '\0';
                        continue;
                    }
                }
                current.Append(char.ToLowerInvariant(character));
                priorKind = kind;
                priorCharacter = character;
            }
            FlushToken(tokens, current);
            return tokens;
        }

        private static void FlushToken(ICollection<string> tokens, StringBuilder current)
        {
            if (current.Length == 0)
                return;
            tokens.Add(current.ToString());
            current.Clear();
        }
    }
}
#endif
