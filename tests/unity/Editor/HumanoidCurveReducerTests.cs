using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class HumanoidCurveReducerTests
    {
        private const string ReducerTypeName = "BlenderSyncVNext.RootMotion.HumanoidCurveReducer";
        private const string PresetTypeName = "BlenderSyncVNext.RootMotion.HumanoidCurveReductionPreset";
        private const string StaticModeTypeName = "BlenderSyncVNext.RootMotion.HumanoidStaticCurveMode";
        private MethodInfo _reduce;
        private MethodInfo _reduceWithStaticMode;
        private MethodInfo _reduceAllFloatCurves;
        private MethodInfo _reduceAllFloatCurvesWithStaticMode;
        private Type _presetType;
        private Type _staticModeType;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _reduce = ProductApi.RequireStaticMethod(ReducerTypeName, "Reduce", 2);
            _reduceWithStaticMode = ProductApi.RequireStaticMethod(ReducerTypeName, "Reduce", 3);
            _reduceAllFloatCurves = ProductApi.RequireStaticMethod(
                ReducerTypeName,
                "ReduceAllFloatCurves",
                2);
            _reduceAllFloatCurvesWithStaticMode = ProductApi.RequireStaticMethod(
                ReducerTypeName,
                "ReduceAllFloatCurves",
                3);
            _presetType = ProductApi.RequireType(PresetTypeName);
            _staticModeType = ProductApi.RequireType(StaticModeTypeName);
        }

        [Test]
        public void ConverterDefaultsToLightReductionAndOffPreservesDenseCurves()
        {
            var viewType = ProductApi.RequireType("BlenderSyncVNext.RootMotion.HumanoidClipConverterView");
            var presetField = viewType.GetField("_keyReduction", BindingFlags.Instance | BindingFlags.NonPublic);
            var staticCurvesField = viewType.GetField("_staticCurves", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(presetField, Is.Not.Null);
            Assert.That(staticCurvesField, Is.Not.Null);
            var view = Activator.CreateInstance(viewType, true);
            Assert.That(presetField.GetValue(view).ToString(), Is.EqualTo("Light"));
            Assert.That(staticCurvesField.GetValue(view).ToString(), Is.EqualTo("Off"));

            var clip = new AnimationClip();
            try
            {
                var sourceKeys = Enumerable.Range(0, 9)
                    .Select(index => new Keyframe(index / 8f, index * index, index + 1f, index + 2f))
                    .ToArray();
                SetAnimatorCurve(clip, "Spine Front-Back", new AnimationCurve(sourceKeys));

                var result = Reduce(clip, "Off");
                var actual = GetAnimatorCurve(clip, "Spine Front-Back").keys;

                Assert.That(ResultInt(result, "KeysBefore"), Is.EqualTo(sourceKeys.Length));
                Assert.That(ResultInt(result, "KeysAfter"), Is.EqualTo(sourceKeys.Length));
                Assert.That(actual.Length, Is.EqualTo(sourceKeys.Length));
                for (var i = 0; i < actual.Length; i++)
                {
                    Assert.That(actual[i].time, Is.EqualTo(sourceKeys[i].time));
                    Assert.That(actual[i].value, Is.EqualTo(sourceKeys[i].value));
                    Assert.That(actual[i].inTangent, Is.EqualTo(sourceKeys[i].inTangent));
                    Assert.That(actual[i].outTangent, Is.EqualTo(sourceKeys[i].outTangent));
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void ScalarReductionRemovesRedundantKeysWithinTheLightErrorBound()
        {
            const int sampleCount = 121;
            var clip = new AnimationClip();
            try
            {
                var times = SampleTimes(sampleCount);
                var values = times.Select(time => Mathf.Sin(time * Mathf.PI * 2f)).ToArray();
                SetAnimatorCurve(clip, "Spine Front-Back", DenseCurve(times, values));

                var result = Reduce(clip, "Light");
                var reduced = GetAnimatorCurve(clip, "Spine Front-Back");

                Assert.That(reduced.length, Is.LessThan(sampleCount));
                Assert.That(ResultInt(result, "KeysBefore"), Is.EqualTo(sampleCount));
                Assert.That(ResultInt(result, "KeysAfter"), Is.EqualTo(reduced.length));
                for (var i = 0; i < sampleCount; i++)
                    Assert.That(Mathf.Abs(reduced.Evaluate(times[i]) - values[i]), Is.LessThanOrEqualTo(0.00051f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void PositionGroupsShareKeyTimesAndRespectTheLightDistanceBound()
        {
            const int sampleCount = 121;
            var clip = new AnimationClip();
            try
            {
                var times = SampleTimes(sampleCount);
                var rootValues = BuildPositionSamples(times, 1f);
                var footValues = BuildPositionSamples(times, 0.35f);
                SetVectorGroup(clip, "RootT", times, rootValues);
                SetVectorGroup(clip, "LeftFootT", times, footValues);

                Reduce(clip, "Light");

                AssertVectorGroup(clip, "RootT", times, rootValues, 0.00011f);
                AssertVectorGroup(clip, "LeftFootT", times, footValues, 0.00011f);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void QuaternionGroupsStaySynchronizedContinuousAndWithinAngularError()
        {
            const int sampleCount = 121;
            var clip = new AnimationClip();
            try
            {
                var times = SampleTimes(sampleCount);
                var rootValues = BuildRotationSamples(times, 1f);
                var handValues = BuildRotationSamples(times, 0.6f);
                SetQuaternionGroup(clip, "RootQ", times, rootValues, true);
                SetQuaternionGroup(clip, "RightHandQ", times, handValues, true);

                AssertSourceQuaternionGroup(clip, "RootQ", times, rootValues);
                AssertSourceQuaternionGroup(clip, "RightHandQ", times, handValues);

                Reduce(clip, "Light");

                AssertQuaternionGroup(clip, "RootQ", times, rootValues, 0.051f);
                AssertQuaternionGroup(clip, "RightHandQ", times, handValues, 0.051f);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void RootMotionReductionDefaultsOffAndPreservesCurvesAndObjectReferences()
        {
            var viewType = ProductApi.RequireType("BlenderSyncVNext.RootMotion.RootMotionWorkflowView");
            var presetField = viewType.GetField("_keyReduction", BindingFlags.Instance | BindingFlags.NonPublic);
            var staticCurvesField = viewType.GetField("_staticCurves", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(presetField, Is.Not.Null);
            Assert.That(staticCurvesField, Is.Not.Null);
            var view = Activator.CreateInstance(viewType, true);
            Assert.That(presetField.GetValue(view).ToString(), Is.EqualTo("Off"));
            Assert.That(staticCurvesField.GetValue(view).ToString(), Is.EqualTo("Off"));

            var clip = new AnimationClip();
            var texture = new Texture2D(1, 1);
            try
            {
                var sourceKeys = Enumerable.Range(0, 9)
                    .Select(index => new Keyframe(index / 8f, index * index, index + 1f, index + 2f))
                    .ToArray();
                var floatBinding = EditorCurveBinding.FloatCurve(
                    "Mesh",
                    typeof(SkinnedMeshRenderer),
                    "blendShape.Smile");
                AnimationUtility.SetEditorCurve(clip, floatBinding, new AnimationCurve(sourceKeys));

                var objectBinding = EditorCurveBinding.PPtrCurve(
                    "Mesh",
                    typeof(Renderer),
                    "m_Materials.Array.data[0]");
                var objectKeys = new[]
                {
                    new ObjectReferenceKeyframe { time = 0f, value = texture },
                    new ObjectReferenceKeyframe { time = 1f, value = null },
                };
                AnimationUtility.SetObjectReferenceCurve(clip, objectBinding, objectKeys);

                var result = ReduceAllFloatCurves(clip, "Off");
                var actual = AnimationUtility.GetEditorCurve(clip, floatBinding).keys;
                var actualObjects = AnimationUtility.GetObjectReferenceCurve(clip, objectBinding);

                Assert.That(ResultInt(result, "KeysBefore"), Is.EqualTo(sourceKeys.Length));
                Assert.That(ResultInt(result, "KeysAfter"), Is.EqualTo(sourceKeys.Length));
                Assert.That(actualObjects, Has.Length.EqualTo(objectKeys.Length));
                Assert.That(actualObjects[0].value, Is.SameAs(texture));
                Assert.That(actualObjects[1].value, Is.Null);
                for (var i = 0; i < actual.Length; i++)
                {
                    Assert.That(actual[i].time, Is.EqualTo(sourceKeys[i].time));
                    Assert.That(actual[i].value, Is.EqualTo(sourceKeys[i].value));
                    Assert.That(actual[i].inTangent, Is.EqualTo(sourceKeys[i].inTangent));
                    Assert.That(actual[i].outTangent, Is.EqualTo(sourceKeys[i].outTangent));
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void RootMotionReductionHandlesTransformGroupsAndContinuousScalars()
        {
            const int sampleCount = 121;
            var clip = new AnimationClip();
            try
            {
                var times = SampleTimes(sampleCount);
                var positions = BuildPositionSamples(times, 0.4f);
                var scales = times.Select(time => new Vector3(
                        1f + 0.05f * Mathf.Sin(time * Mathf.PI * 2f),
                        1f + 0.04f * time * time,
                        1f - 0.03f * time))
                    .ToArray();
                var rotations = BuildRotationSamples(times, 0.7f);
                var blendShape = times.Select(time => 50f + 40f * Mathf.Sin(time * Mathf.PI * 2f)).ToArray();

                SetTransformVectorGroup(clip, "Root", "m_LocalPosition", times, positions);
                SetTransformVectorGroup(clip, "Root", "m_LocalScale", times, scales);
                SetTransformQuaternionGroup(clip, "Root", "m_LocalRotation", times, rotations);
                var blendShapeBinding = EditorCurveBinding.FloatCurve(
                    "Mesh",
                    typeof(SkinnedMeshRenderer),
                    "blendShape.Smile");
                AnimationUtility.SetEditorCurve(clip, blendShapeBinding, DenseCurve(times, blendShape));

                var result = ReduceAllFloatCurves(clip, "Light");

                Assert.That(ResultInt(result, "KeysAfter"), Is.LessThan(ResultInt(result, "KeysBefore")));
                AssertTransformVectorGroup(clip, "Root", "m_LocalPosition", times, positions, 0.00011f);
                AssertTransformVectorGroup(clip, "Root", "m_LocalScale", times, scales, 0.00011f);
                AssertTransformQuaternionGroup(clip, "Root", "m_LocalRotation", times, rotations, 0.051f);
                var reducedBlendShape = AnimationUtility.GetEditorCurve(clip, blendShapeBinding);
                Assert.That(reducedBlendShape.length, Is.LessThan(sampleCount));
                for (var i = 0; i < sampleCount; i++)
                    Assert.That(
                        Mathf.Abs(reducedBlendShape.Evaluate(times[i]) - blendShape[i]),
                        Is.LessThanOrEqualTo(0.00051f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void RootMotionEulerReductionUnwrapsAnglesWithoutChangingTheRotation()
        {
            const int sampleCount = 121;
            var clip = new AnimationClip();
            try
            {
                var times = SampleTimes(sampleCount);
                var continuous = times.Select(time => new Vector3(
                        8f * Mathf.Sin(time * Mathf.PI),
                        350f + 40f * time,
                        -5f * time))
                    .ToArray();
                var stored = continuous.Select(value => new Vector3(
                        Mathf.Repeat(value.x, 360f),
                        Mathf.Repeat(value.y, 360f),
                        Mathf.Repeat(value.z, 360f)))
                    .ToArray();
                SetTransformVectorGroup(
                    clip,
                    "Root",
                    "localEulerAnglesRaw",
                    times,
                    stored);

                ReduceAllFloatCurves(clip, "Light");

                var x = GetTransformCurve(clip, "Root", "localEulerAnglesRaw.x");
                var y = GetTransformCurve(clip, "Root", "localEulerAnglesRaw.y");
                var z = GetTransformCurve(clip, "Root", "localEulerAnglesRaw.z");
                AssertSynchronized(x, y, z);
                Assert.That(x.length, Is.LessThan(sampleCount));
                for (var i = 1; i < y.length; i++)
                    Assert.That(Mathf.Abs(y.keys[i].value - y.keys[i - 1].value), Is.LessThan(180f));
                for (var i = 0; i < sampleCount; i++)
                {
                    var actual = Quaternion.Euler(
                        x.Evaluate(times[i]),
                        y.Evaluate(times[i]),
                        z.Evaluate(times[i]));
                    Assert.That(
                        PreciseQuaternionAngleDegrees(actual, Quaternion.Euler(continuous[i])),
                        Is.LessThanOrEqualTo(0.051f));
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void RootMotionReductionLeavesIncompleteQuaternionAndDiscreteCurvesUntouched()
        {
            var clip = new AnimationClip();
            try
            {
                var times = SampleTimes(21);
                foreach (var axis in new[] { "x", "y", "z" })
                    AnimationUtility.SetEditorCurve(
                        clip,
                        TransformBinding("Root", "m_LocalRotation." + axis),
                        DenseCurve(times, times.Select(time => Mathf.Sin(time * Mathf.PI * 2f)).ToArray()));
                var discreteBinding = EditorCurveBinding.DiscreteCurve(
                    string.Empty,
                    typeof(GameObject),
                    "m_IsActive");
                var discrete = DenseCurve(
                    times,
                    times.Select((time, index) => index % 2 == 0 ? 0f : 1f).ToArray());
                AnimationUtility.SetEditorCurve(clip, discreteBinding, discrete);

                ReduceAllFloatCurves(clip, "Aggressive");

                foreach (var axis in new[] { "x", "y", "z" })
                    Assert.That(
                        GetTransformCurve(clip, "Root", "m_LocalRotation." + axis).length,
                        Is.EqualTo(times.Length));
                var actualDiscrete = AnimationUtility.GetEditorCurve(clip, discreteBinding);
                Assert.That(actualDiscrete.length, Is.EqualTo(discrete.length));
                for (var i = 0; i < discrete.length; i++)
                    Assert.That(actualDiscrete.keys[i].value, Is.EqualTo(discrete.keys[i].value));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void StrongerPresetsNeverRetainMoreKeysThanLighterPresets()
        {
            var counts = new List<int>();
            foreach (var preset in new[] { "Off", "Light", "Medium", "Aggressive" })
            {
                var clip = new AnimationClip();
                try
                {
                    var times = SampleTimes(181);
                    var values = times
                        .Select(time => Mathf.Sin(time * Mathf.PI * 4f) + 0.2f * Mathf.Sin(time * Mathf.PI * 19f))
                        .ToArray();
                    SetAnimatorCurve(clip, "Spine Front-Back", DenseCurve(times, values));
                    Reduce(clip, preset);
                    counts.Add(GetAnimatorCurve(clip, "Spine Front-Back").length);
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(clip);
                }
            }

            Assert.That(counts[0], Is.GreaterThan(counts[1]));
            Assert.That(counts[1], Is.GreaterThanOrEqualTo(counts[2]));
            Assert.That(counts[2], Is.GreaterThanOrEqualTo(counts[3]));
            Assert.That(counts[1], Is.GreaterThan(counts[3]));
        }

        [Test]
        public void StaticHumanoidCurvesCollapseToOneKeyWithoutDroppingRootGoalOrMuscleBindings()
        {
            var clip = new AnimationClip();
            try
            {
                var times = SampleTimes(5);
                var constantPosition = Enumerable.Repeat(new Vector3(1f, 2f, 3f), times.Length).ToArray();
                var constantRotation = Enumerable.Repeat(Quaternion.identity, times.Length).ToArray();

                SetVectorGroup(clip, "RootT", times, constantPosition);
                SetQuaternionGroup(clip, "RootQ", times, constantRotation, true);
                SetVectorGroup(clip, "LeftFootT", times, constantPosition);
                SetQuaternionGroup(clip, "LeftHandQ", times, constantRotation, true);
                SetAnimatorCurve(
                    clip,
                    "Spine Front-Back",
                    DenseCurve(times, Enumerable.Repeat(0.25f, times.Length).ToArray()));
                SetAnimatorCurve(
                    clip,
                    "Chest Front-Back",
                    DenseCurve(times, times.Select(time => time).ToArray()));

                var result = Reduce(clip, "Off", "CollapseConstant");

                Assert.That(ResultInt(result, "CollapsedStaticTrackCount"), Is.EqualTo(15));
                Assert.That(GetAnimatorCurve(clip, "RootT.x").length, Is.EqualTo(1));
                Assert.That(GetAnimatorCurve(clip, "RootT.y").length, Is.EqualTo(1));
                Assert.That(GetAnimatorCurve(clip, "RootT.z").length, Is.EqualTo(1));
                Assert.That(GetAnimatorCurve(clip, "RootQ.x").length, Is.EqualTo(1));
                Assert.That(GetAnimatorCurve(clip, "RootQ.y").length, Is.EqualTo(1));
                Assert.That(GetAnimatorCurve(clip, "RootQ.z").length, Is.EqualTo(1));
                Assert.That(GetAnimatorCurve(clip, "RootQ.w").length, Is.EqualTo(1));
                Assert.That(GetAnimatorCurve(clip, "LeftFootT.x").length, Is.EqualTo(1));
                Assert.That(GetAnimatorCurve(clip, "LeftHandQ.w").length, Is.EqualTo(1));
                Assert.That(GetAnimatorCurve(clip, "Spine Front-Back").length, Is.EqualTo(1));
                Assert.That(GetAnimatorCurve(clip, "Chest Front-Back").length, Is.EqualTo(times.Length));

                Assert.That(
                    new Vector3(
                        GetAnimatorCurve(clip, "RootT.x").Evaluate(0f),
                        GetAnimatorCurve(clip, "RootT.y").Evaluate(0f),
                        GetAnimatorCurve(clip, "RootT.z").Evaluate(0f)),
                    Is.EqualTo(new Vector3(1f, 2f, 3f)));
                var collapsedRotation = Normalize(new Quaternion(
                    GetAnimatorCurve(clip, "RootQ.x").Evaluate(0f),
                    GetAnimatorCurve(clip, "RootQ.y").Evaluate(0f),
                    GetAnimatorCurve(clip, "RootQ.z").Evaluate(0f),
                    GetAnimatorCurve(clip, "RootQ.w").Evaluate(0f)));
                Assert.That(PreciseQuaternionAngleDegrees(collapsedRotation, Quaternion.identity), Is.LessThan(0.001f));

                foreach (var binding in AnimationUtility.GetCurveBindings(clip))
                    Assert.That(AnimationUtility.GetEditorCurve(clip, binding), Is.Not.Null, binding.propertyName);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void StaticHumanoidCurveCollapsePreservesAllConstantClipDuration()
        {
            var clip = new AnimationClip();
            try
            {
                var times = new[] { 0f, 1f, 2f };
                SetVectorGroup(
                    clip,
                    "RootT",
                    times,
                    Enumerable.Repeat(new Vector3(1f, 2f, 3f), times.Length).ToArray());
                SetQuaternionGroup(
                    clip,
                    "RootQ",
                    times,
                    Enumerable.Repeat(Quaternion.identity, times.Length).ToArray(),
                    false);
                SetAnimatorCurve(
                    clip,
                    "Spine Front-Back",
                    DenseCurve(times, Enumerable.Repeat(0.25f, times.Length).ToArray()));
                var lengthBefore = clip.length;
                var stopTimeBefore = AnimationUtility.GetAnimationClipSettings(clip).stopTime;

                Reduce(clip, "Off", "CollapseConstant");

                var settingsAfter = AnimationUtility.GetAnimationClipSettings(clip);
                Assert.That(
                    settingsAfter.stopTime,
                    Is.EqualTo(stopTimeBefore).Within(1e-6f),
                    $"length={clip.length:0.######}");
                Assert.That(clip.length, Is.EqualTo(lengthBefore).Within(1e-6f));
                Assert.That(
                    AnimationUtility.GetCurveBindings(clip)
                        .Sum(binding => AnimationUtility.GetEditorCurve(clip, binding).length),
                    Is.EqualTo(9));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void StaticRootMotionCurvesCollapseOnlyCompleteTransformGroupsAndPreserveOtherCurveKinds()
        {
            var clip = new AnimationClip();
            var texture = new Texture2D(1, 1);
            try
            {
                var times = SampleTimes(5);
                var constantPosition = Enumerable.Repeat(new Vector3(1f, 2f, 3f), times.Length).ToArray();
                var constantScale = Enumerable.Repeat(new Vector3(1f, 1f, 1f), times.Length).ToArray();
                var constantRotation = Enumerable.Repeat(Quaternion.identity, times.Length).ToArray();
                SetTransformVectorGroup(clip, "Root", "m_LocalPosition", times, constantPosition);
                SetTransformQuaternionGroup(clip, "Root", "m_LocalRotation", times, constantRotation);
                SetTransformVectorGroup(clip, "Root", "m_LocalScale", times, constantScale);

                SetTransformVectorGroup(
                    clip,
                    "Moving",
                    "m_LocalPosition",
                    times,
                    times.Select(time => new Vector3(time, 0f, 0f)).ToArray());
                foreach (var axis in new[] { "x", "y", "z" })
                    AnimationUtility.SetEditorCurve(
                        clip,
                        TransformBinding("Partial", "m_LocalRotation." + axis),
                        DenseCurve(times, Enumerable.Repeat(0f, times.Length).ToArray()));
                SetTransformQuaternionGroup(
                    clip,
                    "Invalid",
                    "m_LocalRotation",
                    times,
                    times.Select((time, index) => index == 0 ? new Quaternion() : Quaternion.identity).ToArray());

                foreach (var axis in new[] { "x", "y", "z" })
                    AnimationUtility.SetEditorCurve(
                        clip,
                        EditorCurveBinding.DiscreteCurve(
                            "DiscreteTransform",
                            typeof(Transform),
                            "m_LocalScale." + axis),
                        DenseCurve(times, Enumerable.Repeat(1f, times.Length).ToArray()));

                var blendShapeBinding = EditorCurveBinding.FloatCurve(
                    "Mesh",
                    typeof(SkinnedMeshRenderer),
                    "blendShape.Smile");
                AnimationUtility.SetEditorCurve(
                    clip,
                    blendShapeBinding,
                    DenseCurve(times, Enumerable.Repeat(25f, times.Length).ToArray()));

                var discreteBinding = EditorCurveBinding.DiscreteCurve(
                    string.Empty,
                    typeof(GameObject),
                    "m_IsActive");
                var discrete = DenseCurve(
                    times,
                    times.Select((time, index) => index % 2 == 0 ? 0f : 1f).ToArray());
                AnimationUtility.SetEditorCurve(clip, discreteBinding, discrete);

                var objectBinding = EditorCurveBinding.PPtrCurve(
                    "Mesh",
                    typeof(Renderer),
                    "m_Materials.Array.data[0]");
                var objectKeys = new[]
                {
                    new ObjectReferenceKeyframe { time = 0f, value = texture },
                    new ObjectReferenceKeyframe { time = 1f, value = null },
                };
                AnimationUtility.SetObjectReferenceCurve(clip, objectBinding, objectKeys);

                var result = ReduceAllFloatCurves(clip, "Off", "CollapseConstant");

                Assert.That(ResultInt(result, "CollapsedStaticTrackCount"), Is.EqualTo(10));
                foreach (var property in new[]
                         {
                             "m_LocalPosition.x", "m_LocalPosition.y", "m_LocalPosition.z",
                             "m_LocalRotation.x", "m_LocalRotation.y", "m_LocalRotation.z", "m_LocalRotation.w",
                             "m_LocalScale.x", "m_LocalScale.y", "m_LocalScale.z",
                         })
                    Assert.That(GetTransformCurve(clip, "Root", property).length, Is.EqualTo(1), property);

                Assert.That(GetTransformCurve(clip, "Moving", "m_LocalPosition.x").length, Is.EqualTo(times.Length));
                Assert.That(GetTransformCurve(clip, "Partial", "m_LocalRotation.x").length, Is.EqualTo(times.Length));
                Assert.That(GetTransformCurve(clip, "Invalid", "m_LocalRotation.w").length, Is.EqualTo(times.Length));
                foreach (var axis in new[] { "x", "y", "z" })
                    Assert.That(
                        AnimationUtility.GetEditorCurve(
                            clip,
                            EditorCurveBinding.DiscreteCurve(
                                "DiscreteTransform",
                                typeof(Transform),
                                "m_LocalScale." + axis)).length,
                        Is.EqualTo(times.Length));
                Assert.That(AnimationUtility.GetEditorCurve(clip, blendShapeBinding).length, Is.EqualTo(times.Length));
                Assert.That(AnimationUtility.GetEditorCurve(clip, discreteBinding).length, Is.EqualTo(times.Length));
                var actualObjects = AnimationUtility.GetObjectReferenceCurve(clip, objectBinding);
                Assert.That(actualObjects, Has.Length.EqualTo(objectKeys.Length));
                Assert.That(actualObjects[0].value, Is.SameAs(texture));
                Assert.That(actualObjects[1].value, Is.Null);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void StaticTransformCurveCollapsePreservesAllConstantClipDuration()
        {
            var clip = new AnimationClip();
            try
            {
                var times = new[] { 0f, 1f, 2f };
                SetTransformVectorGroup(
                    clip,
                    "Root",
                    "m_LocalPosition",
                    times,
                    Enumerable.Repeat(new Vector3(1f, 2f, 3f), times.Length).ToArray());
                SetTransformQuaternionGroup(
                    clip,
                    "Root",
                    "m_LocalRotation",
                    times,
                    Enumerable.Repeat(Quaternion.identity, times.Length).ToArray());
                SetTransformVectorGroup(
                    clip,
                    "Root",
                    "m_LocalScale",
                    times,
                    Enumerable.Repeat(Vector3.one, times.Length).ToArray());
                var lengthBefore = clip.length;
                var stopTimeBefore = AnimationUtility.GetAnimationClipSettings(clip).stopTime;

                ReduceAllFloatCurves(clip, "Off", "CollapseConstant");

                var settingsAfter = AnimationUtility.GetAnimationClipSettings(clip);
                Assert.That(
                    settingsAfter.stopTime,
                    Is.EqualTo(stopTimeBefore).Within(1e-6f),
                    $"length={clip.length:0.######}");
                Assert.That(clip.length, Is.EqualTo(lengthBefore).Within(1e-6f));
                Assert.That(
                    AnimationUtility.GetCurveBindings(clip)
                        .Sum(binding => AnimationUtility.GetEditorCurve(clip, binding).length),
                    Is.EqualTo(11));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        private object Reduce(AnimationClip clip, string preset)
        {
            return _reduce.Invoke(null, new[] { clip, Enum.Parse(_presetType, preset) });
        }

        private object Reduce(AnimationClip clip, string preset, string staticMode)
        {
            return _reduceWithStaticMode.Invoke(
                null,
                new[]
                {
                    clip,
                    Enum.Parse(_presetType, preset),
                    Enum.Parse(_staticModeType, staticMode),
                });
        }

        private object ReduceAllFloatCurves(AnimationClip clip, string preset)
        {
            return _reduceAllFloatCurves.Invoke(
                null,
                new[] { clip, Enum.Parse(_presetType, preset) });
        }

        private object ReduceAllFloatCurves(AnimationClip clip, string preset, string staticMode)
        {
            return _reduceAllFloatCurvesWithStaticMode.Invoke(
                null,
                new[]
                {
                    clip,
                    Enum.Parse(_presetType, preset),
                    Enum.Parse(_staticModeType, staticMode),
                });
        }

        private static int ResultInt(object result, string fieldName)
        {
            var field = result.GetType().GetField(fieldName, BindingFlags.Instance | BindingFlags.Public);
            Assert.That(field, Is.Not.Null, $"Missing result field {fieldName}");
            return (int)field.GetValue(result);
        }

        private static float[] SampleTimes(int count)
        {
            return Enumerable.Range(0, count)
                .Select(index => index / (float)(count - 1))
                .ToArray();
        }

        private static AnimationCurve DenseCurve(IReadOnlyList<float> times, IReadOnlyList<float> values)
        {
            var keys = new Keyframe[times.Count];
            for (var i = 0; i < keys.Length; i++)
                keys[i] = new Keyframe(times[i], values[i]);
            return new AnimationCurve(keys);
        }

        private static Vector3[] BuildPositionSamples(IReadOnlyList<float> times, float scale)
        {
            return times.Select(time => new Vector3(
                    scale * time,
                    scale * time * time,
                    scale * Mathf.Sin(time * Mathf.PI * 2f)))
                .ToArray();
        }

        private static Quaternion[] BuildRotationSamples(IReadOnlyList<float> times, float scale)
        {
            return times.Select(time => Quaternion.Euler(
                    scale * 15f * Mathf.Sin(time * Mathf.PI * 2f),
                    scale * (150f * time + 10f * Mathf.Sin(time * Mathf.PI * 3f)),
                    scale * 8f * Mathf.Sin(time * Mathf.PI * 5f)))
                .ToArray();
        }

        private static void SetVectorGroup(
            AnimationClip clip,
            string prefix,
            IReadOnlyList<float> times,
            IReadOnlyList<Vector3> values)
        {
            SetAnimatorCurve(clip, prefix + ".x", DenseCurve(times, values.Select(value => value.x).ToArray()));
            SetAnimatorCurve(clip, prefix + ".y", DenseCurve(times, values.Select(value => value.y).ToArray()));
            SetAnimatorCurve(clip, prefix + ".z", DenseCurve(times, values.Select(value => value.z).ToArray()));
        }

        private static void SetQuaternionGroup(
            AnimationClip clip,
            string prefix,
            IReadOnlyList<float> times,
            IReadOnlyList<Quaternion> values,
            bool alternateSigns)
        {
            var stored = values
                .Select((value, index) => alternateSigns && index % 11 == 0
                    ? new Quaternion(-value.x, -value.y, -value.z, -value.w)
                    : value)
                .ToArray();
            AnimationUtility.SetEditorCurves(
                clip,
                new[]
                {
                    AnimatorBinding(prefix + ".x"),
                    AnimatorBinding(prefix + ".y"),
                    AnimatorBinding(prefix + ".z"),
                    AnimatorBinding(prefix + ".w"),
                },
                new[]
                {
                    DenseCurve(times, stored.Select(value => value.x).ToArray()),
                    DenseCurve(times, stored.Select(value => value.y).ToArray()),
                    DenseCurve(times, stored.Select(value => value.z).ToArray()),
                    DenseCurve(times, stored.Select(value => value.w).ToArray()),
                });
        }

        private static void SetTransformVectorGroup(
            AnimationClip clip,
            string path,
            string prefix,
            IReadOnlyList<float> times,
            IReadOnlyList<Vector3> values)
        {
            AnimationUtility.SetEditorCurves(
                clip,
                new[]
                {
                    TransformBinding(path, prefix + ".x"),
                    TransformBinding(path, prefix + ".y"),
                    TransformBinding(path, prefix + ".z"),
                },
                new[]
                {
                    DenseCurve(times, values.Select(value => value.x).ToArray()),
                    DenseCurve(times, values.Select(value => value.y).ToArray()),
                    DenseCurve(times, values.Select(value => value.z).ToArray()),
                });
        }

        private static void SetTransformQuaternionGroup(
            AnimationClip clip,
            string path,
            string prefix,
            IReadOnlyList<float> times,
            IReadOnlyList<Quaternion> values)
        {
            AnimationUtility.SetEditorCurves(
                clip,
                new[]
                {
                    TransformBinding(path, prefix + ".x"),
                    TransformBinding(path, prefix + ".y"),
                    TransformBinding(path, prefix + ".z"),
                    TransformBinding(path, prefix + ".w"),
                },
                new[]
                {
                    DenseCurve(times, values.Select(value => value.x).ToArray()),
                    DenseCurve(times, values.Select(value => value.y).ToArray()),
                    DenseCurve(times, values.Select(value => value.z).ToArray()),
                    DenseCurve(times, values.Select(value => value.w).ToArray()),
                });
        }

        private static void AssertVectorGroup(
            AnimationClip clip,
            string prefix,
            IReadOnlyList<float> times,
            IReadOnlyList<Vector3> expected,
            float tolerance)
        {
            var x = GetAnimatorCurve(clip, prefix + ".x");
            var y = GetAnimatorCurve(clip, prefix + ".y");
            var z = GetAnimatorCurve(clip, prefix + ".z");
            AssertSynchronized(x, y, z);
            Assert.That(x.length, Is.LessThan(times.Count));
            for (var i = 0; i < times.Count; i++)
            {
                var actual = new Vector3(x.Evaluate(times[i]), y.Evaluate(times[i]), z.Evaluate(times[i]));
                Assert.That(Vector3.Distance(actual, expected[i]), Is.LessThanOrEqualTo(tolerance));
            }
        }

        private static void AssertQuaternionGroup(
            AnimationClip clip,
            string prefix,
            IReadOnlyList<float> times,
            IReadOnlyList<Quaternion> expected,
            float toleranceDegrees)
        {
            var x = GetAnimatorCurve(clip, prefix + ".x");
            var y = GetAnimatorCurve(clip, prefix + ".y");
            var z = GetAnimatorCurve(clip, prefix + ".z");
            var w = GetAnimatorCurve(clip, prefix + ".w");
            AssertSynchronized(x, y, z, w);
            Assert.That(x.length, Is.LessThan(times.Count));

            Quaternion? previous = null;
            for (var i = 0; i < x.length; i++)
            {
                var key = Normalize(new Quaternion(
                    x.keys[i].value,
                    y.keys[i].value,
                    z.keys[i].value,
                    w.keys[i].value));
                if (previous.HasValue)
                    Assert.That(Quaternion.Dot(previous.Value, key), Is.GreaterThanOrEqualTo(0f));
                previous = key;
            }

            for (var i = 0; i < times.Count; i++)
            {
                var actual = Normalize(new Quaternion(
                    x.Evaluate(times[i]),
                    y.Evaluate(times[i]),
                    z.Evaluate(times[i]),
                    w.Evaluate(times[i])));
                var error = PreciseQuaternionAngleDegrees(actual, expected[i]);
                Assert.That(
                    error,
                    Is.LessThanOrEqualTo(toleranceDegrees),
                    $"{prefix} sample={i} time={times[i]:0.######} retained={x.length}");
            }
        }

        private static void AssertTransformVectorGroup(
            AnimationClip clip,
            string path,
            string prefix,
            IReadOnlyList<float> times,
            IReadOnlyList<Vector3> expected,
            float tolerance)
        {
            var x = GetTransformCurve(clip, path, prefix + ".x");
            var y = GetTransformCurve(clip, path, prefix + ".y");
            var z = GetTransformCurve(clip, path, prefix + ".z");
            AssertSynchronized(x, y, z);
            Assert.That(x.length, Is.LessThan(times.Count));
            for (var i = 0; i < times.Count; i++)
            {
                var actual = new Vector3(x.Evaluate(times[i]), y.Evaluate(times[i]), z.Evaluate(times[i]));
                Assert.That(Vector3.Distance(actual, expected[i]), Is.LessThanOrEqualTo(tolerance));
            }
        }

        private static void AssertTransformQuaternionGroup(
            AnimationClip clip,
            string path,
            string prefix,
            IReadOnlyList<float> times,
            IReadOnlyList<Quaternion> expected,
            float toleranceDegrees)
        {
            var x = GetTransformCurve(clip, path, prefix + ".x");
            var y = GetTransformCurve(clip, path, prefix + ".y");
            var z = GetTransformCurve(clip, path, prefix + ".z");
            var w = GetTransformCurve(clip, path, prefix + ".w");
            AssertSynchronized(x, y, z, w);
            Assert.That(x.length, Is.LessThan(times.Count));
            for (var i = 0; i < times.Count; i++)
            {
                var actual = Normalize(new Quaternion(
                    x.Evaluate(times[i]),
                    y.Evaluate(times[i]),
                    z.Evaluate(times[i]),
                    w.Evaluate(times[i])));
                Assert.That(
                    PreciseQuaternionAngleDegrees(actual, expected[i]),
                    Is.LessThanOrEqualTo(toleranceDegrees));
            }
        }

        private static void AssertSourceQuaternionGroup(
            AnimationClip clip,
            string prefix,
            IReadOnlyList<float> times,
            IReadOnlyList<Quaternion> expected)
        {
            var x = GetAnimatorCurve(clip, prefix + ".x");
            var y = GetAnimatorCurve(clip, prefix + ".y");
            var z = GetAnimatorCurve(clip, prefix + ".z");
            var w = GetAnimatorCurve(clip, prefix + ".w");
            for (var i = 0; i < times.Count; i++)
            {
                var actual = Normalize(new Quaternion(
                    x.Evaluate(times[i]),
                    y.Evaluate(times[i]),
                    z.Evaluate(times[i]),
                    w.Evaluate(times[i])));
                Assert.That(
                    PreciseQuaternionAngleDegrees(actual, expected[i]),
                    Is.LessThanOrEqualTo(0.001f),
                    $"source {prefix} sample={i}");
            }
        }

        private static void AssertSynchronized(params AnimationCurve[] curves)
        {
            var reference = curves[0].keys;
            foreach (var curve in curves.Skip(1))
            {
                Assert.That(curve.length, Is.EqualTo(reference.Length));
                for (var i = 0; i < reference.Length; i++)
                    Assert.That(curve.keys[i].time, Is.EqualTo(reference[i].time).Within(1e-7f));
            }
        }

        private static Quaternion Normalize(Quaternion value)
        {
            var magnitude = Mathf.Sqrt(
                value.x * value.x +
                value.y * value.y +
                value.z * value.z +
                value.w * value.w);
            return magnitude > 1e-6f
                ? new Quaternion(value.x / magnitude, value.y / magnitude, value.z / magnitude, value.w / magnitude)
                : Quaternion.identity;
        }

        private static float PreciseQuaternionAngleDegrees(Quaternion from, Quaternion to)
        {
            var dot =
                (double)from.x * to.x +
                (double)from.y * to.y +
                (double)from.z * to.z +
                (double)from.w * to.w;
            var fromLengthSquared =
                (double)from.x * from.x +
                (double)from.y * from.y +
                (double)from.z * from.z +
                (double)from.w * from.w;
            var toLengthSquared =
                (double)to.x * to.x +
                (double)to.y * to.y +
                (double)to.z * to.z +
                (double)to.w * to.w;
            var lengthProduct = Math.Sqrt(fromLengthSquared * toLengthSquared);
            if (lengthProduct <= 1e-12d)
                return 180f;
            dot /= lengthProduct;
            dot = Math.Min(1d, Math.Abs(dot));
            return (float)(2d * Math.Acos(dot) * 180d / Math.PI);
        }

        private static void SetAnimatorCurve(AnimationClip clip, string propertyName, AnimationCurve curve)
        {
            AnimationUtility.SetEditorCurve(clip, AnimatorBinding(propertyName), curve);
        }

        private static EditorCurveBinding AnimatorBinding(string propertyName)
        {
            return EditorCurveBinding.FloatCurve(string.Empty, typeof(Animator), propertyName);
        }

        private static EditorCurveBinding TransformBinding(string path, string propertyName)
        {
            return EditorCurveBinding.FloatCurve(path, typeof(Transform), propertyName);
        }

        private static AnimationCurve GetAnimatorCurve(AnimationClip clip, string propertyName)
        {
            return AnimationUtility.GetEditorCurve(
                clip,
                EditorCurveBinding.FloatCurve(string.Empty, typeof(Animator), propertyName));
        }

        private static AnimationCurve GetTransformCurve(
            AnimationClip clip,
            string path,
            string propertyName)
        {
            return AnimationUtility.GetEditorCurve(
                clip,
                TransformBinding(path, propertyName));
        }
    }
}
