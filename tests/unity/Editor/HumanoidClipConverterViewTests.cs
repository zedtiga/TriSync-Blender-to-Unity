using System;
using System.Reflection;
using NUnit.Framework;
using Unity.Collections;
using UnityEditor;
using UnityEngine;
using UnityEngine.Animations;

namespace BlenderSyncVNext.Tests
{
    public sealed class HumanoidClipConverterViewTests
    {
        private const string TypeName = "BlenderSyncVNext.RootMotion.HumanoidClipConverterView";
        private Type _viewType;
        private MethodInfo _conversionStatusLabel;
        private MethodInfo _conversionActionLabel;
        private MethodInfo _conversionResultLabel;
        private MethodInfo _resolveSampleFps;
        private MethodInfo _sampleToHumanoidCurves;
        private MethodInfo _unityGoalQPostOffset;
        private MethodInfo _legacyBodyGoalPosition;
        private MethodInfo _legacyBodyGoalRotation;
        private MethodInfo _bindingRootWarning;
        private MethodInfo _isBindingRootValid;
        private MethodInfo _resolveSampleRoot;
        private MethodInfo _assignSourceAnimator;
        private MethodInfo _assignSourceClip;
        private MethodInfo _buildTransformCurveSets;
        private MethodInfo _applyTransformCurveSets;
        private Type _legacyGoalSamplerType;
        private Type _legacyAnimatorStateType;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _viewType = ProductApi.RequireType(TypeName);
            _conversionStatusLabel = ProductApi.RequireStaticMethod(TypeName, "ConversionStatusLabel", 7);
            _conversionActionLabel = ProductApi.RequireStaticMethod(TypeName, "ConversionActionLabel", 0);
            _conversionResultLabel = ProductApi.RequireStaticMethod(TypeName, "ConversionResultLabel", 2);
            _resolveSampleFps = ProductApi.RequireStaticMethod(TypeName, "ResolveSampleFps", 1);
            _sampleToHumanoidCurves = ProductApi.RequireStaticMethod(TypeName, "SampleToHumanoidCurves", 6);
            _unityGoalQPostOffset = ProductApi.RequireStaticMethod(TypeName, "UnityGoalQPostOffset", 1);
            _legacyBodyGoalPosition = ProductApi.RequireStaticMethod(TypeName, "LegacyBodyGoalPosition", 6);
            _legacyBodyGoalRotation = ProductApi.RequireStaticMethod(TypeName, "LegacyBodyGoalRotation", 2);
            _bindingRootWarning = ProductApi.RequireStaticMethod(TypeName, "BindingRootWarning", 2);
            _isBindingRootValid = _viewType.GetMethod(
                "IsBindingRootValid",
                BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(_isBindingRootValid, Is.Not.Null);
            _resolveSampleRoot = _viewType.GetMethod(
                "ResolveSampleRoot",
                BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(_resolveSampleRoot, Is.Not.Null);
            _assignSourceAnimator = RequireInstanceMethod("AssignSourceAnimator");
            _assignSourceClip = RequireInstanceMethod("AssignSourceClip");
            _buildTransformCurveSets = ProductApi.RequireStaticMethod(TypeName, "BuildTransformCurveSets", 2);
            _applyTransformCurveSets = ProductApi.RequireStaticMethod(TypeName, "ApplyTransformCurveSets", 2);
            _legacyGoalSamplerType = _viewType.GetNestedType(
                "LegacyGoalSampler",
                BindingFlags.NonPublic);
            Assert.That(_legacyGoalSamplerType, Is.Not.Null);
            _legacyAnimatorStateType = _viewType.GetNestedType(
                "LegacyAnimatorState",
                BindingFlags.NonPublic);
            Assert.That(_legacyAnimatorStateType, Is.Not.Null);
        }

        [Test]
        public void HandFootIkCurvesAreARequiredConversionInvariant()
        {
            Assert.That(
                _viewType.GetField(
                    "_includeHandFootIkCurves",
                    BindingFlags.Instance | BindingFlags.NonPublic),
                Is.Null);
            Assert.That(_sampleToHumanoidCurves.GetParameters()[5].ParameterType, Is.EqualTo(typeof(float)));
            AssertRotation(UnityEngine.AvatarIKGoal.LeftFoot, Quaternion.Euler(0f, 90f, -90f));
            AssertRotation(UnityEngine.AvatarIKGoal.RightFoot, Quaternion.Euler(0f, 90f, -90f));
            AssertRotation(UnityEngine.AvatarIKGoal.LeftHand, Quaternion.Euler(0f, 90f, 180f));
            AssertRotation(UnityEngine.AvatarIKGoal.RightHand, Quaternion.Euler(0f, -90f, 0f));
        }

        [Test]
        public void Unity6000ZeroGoalFormulaMatchesHumanPoseCoordinates()
        {
            var bodyPosition = new Vector3(0.00137608079f, 1.03918254f, 0.8375634f);
            var bodyRotation = new Quaternion(
                1.12060263e-8f,
                -3.03733987e-6f,
                2.41215048e-7f,
                1f);
            const float humanScale = 1.03516614f;
            var goalPositions = new[]
            {
                new Vector3(-0.09121831f, 0.221814036f, 1.24747109f),
                new Vector3(0.0912705362f, 0.10485369f, 0.797102451f),
                new Vector3(-0.737745f, 1.43561292f, 0.7617035f),
                new Vector3(0.6929667f, 1.21792483f, 0.761692047f),
            };
            var goalRotations = new[]
            {
                new Quaternion(-0.281379223f, 4.32133675e-7f, 1.49011612e-7f, 0.959596634f),
                new Quaternion(0f, 3.427267e-7f, 3.427267e-7f, 1f),
                new Quaternion(1.6430495e-7f, 0.7071074f, -3.563153e-8f, -0.7071061f),
                new Quaternion(0.142806321f, 0.69253546f, -0.142806709f, 0.6925368f),
            };
            var footBottomHeights = new[] { 0.104922026f, 0.104922175f, 0f, 0f };
            var expectedPositions = new[]
            {
                new Vector3(-0.08944645f, -0.8749091f, 0.450718433f),
                new Vector3(0.0868399441f, -1.00394619f, -0.0390869342f),
                new Vector3(-0.714012265f, 0.3829635f, -0.0732784942f),
                new Vector3(0.6680959f, 0.172669828f, -0.07329807f),
            };
            var expectedRotations = new[]
            {
                new Quaternion(-0.281379223f, 3.44216824e-6f, 7.89761543e-7f, 0.9595967f),
                new Quaternion(-1.49011612e-8f, 3.35276127e-6f, 1.04308128e-7f, 1f),
                new Quaternion(3.427963e-7f, 0.7071055f, 1.2701139e-7f, -0.7071085f),
                new Quaternion(0.142806083f, 0.6925378f, -0.14280735f, 0.692534864f),
            };

            for (var i = 0; i < goalPositions.Length; i++)
            {
                var position = (Vector3)_legacyBodyGoalPosition.Invoke(
                    null,
                    new object[]
                    {
                        goalPositions[i],
                        goalRotations[i],
                        bodyPosition,
                        bodyRotation,
                        humanScale,
                        footBottomHeights[i],
                    });
                var rotation = (Quaternion)_legacyBodyGoalRotation.Invoke(
                    null,
                    new object[] { goalRotations[i], bodyRotation });
                Assert.That(
                    Vector3.Distance(position, expectedPositions[i]),
                    Is.LessThan(1e-5f),
                    $"Goal {i} position drifted from Unity's HumanPose coordinates.");
                Assert.That(
                    Quaternion.Angle(rotation, expectedRotations[i]),
                    Is.LessThan(0.001f),
                    $"Goal {i} rotation drifted from Unity's HumanPose coordinates.");
            }
        }

        [Test]
        public void Unity6000ZeroSamplerReadsTheCurrentScenePose()
        {
            var constructors = _legacyGoalSamplerType.GetConstructors(
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            Assert.That(constructors, Has.Length.EqualTo(1));
            Assert.That(
                Array.ConvertAll(constructors[0].GetParameters(), parameter => parameter.ParameterType),
                Is.EqualTo(new[] { typeof(Animator), typeof(Avatar) }));
            Assert.That(
                _legacyGoalSamplerType.GetField("_sceneHandles", BindingFlags.Instance | BindingFlags.NonPublic)
                    ?.FieldType,
                Is.EqualTo(typeof(NativeArray<TransformSceneHandle>)));
            Assert.That(
                _legacyGoalSamplerType.GetField("_streamHandles", BindingFlags.Instance | BindingFlags.NonPublic)
                    ?.FieldType,
                Is.EqualTo(typeof(NativeArray<TransformStreamHandle>)));
            Assert.That(
                _legacyGoalSamplerType.GetField("_samplingClip", BindingFlags.Instance | BindingFlags.NonPublic),
                Is.Null);
            Assert.That(
                _legacyGoalSamplerType.GetField("_clipPlayable", BindingFlags.Instance | BindingFlags.NonPublic),
                Is.Null);
        }

        [Test]
        public void Unity6000ZeroSamplingStateUsesSelectedAvatarAndRestoresAnimator()
        {
            var character = new GameObject("Character");
            var skeleton = new GameObject("Skeleton");
            Avatar previousAvatar = null;
            Avatar selectedAvatar = null;
            IDisposable samplingState = null;
            try
            {
                skeleton.transform.SetParent(character.transform);
                var animator = character.AddComponent<Animator>();
                previousAvatar = AvatarBuilder.BuildGenericAvatar(character, skeleton.name);
                selectedAvatar = AvatarBuilder.BuildGenericAvatar(character, skeleton.name);
                animator.avatar = previousAvatar;
                animator.enabled = false;
                animator.cullingMode = AnimatorCullingMode.CullCompletely;

                samplingState = (IDisposable)Activator.CreateInstance(
                    _legacyAnimatorStateType,
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                    null,
                    new object[] { animator, selectedAvatar },
                    null);

                Assert.That(animator.avatar, Is.SameAs(selectedAvatar));
                Assert.That(animator.enabled, Is.True);
                Assert.That(animator.cullingMode, Is.EqualTo(AnimatorCullingMode.AlwaysAnimate));

                samplingState.Dispose();
                samplingState = null;
                Assert.That(animator.avatar, Is.SameAs(previousAvatar));
                Assert.That(animator.enabled, Is.False);
                Assert.That(animator.cullingMode, Is.EqualTo(AnimatorCullingMode.CullCompletely));
            }
            finally
            {
                samplingState?.Dispose();
                if (selectedAvatar != null)
                    UnityEngine.Object.DestroyImmediate(selectedAvatar);
                if (previousAvatar != null)
                    UnityEngine.Object.DestroyImmediate(previousAvatar);
                UnityEngine.Object.DestroyImmediate(character);
            }
        }

        [Test]
        public void EditableCharacterFieldsReplaceLegacyOverrideState()
        {
            Assert.That(
                _viewType.GetField("_sourceAvatarOverride", BindingFlags.Instance | BindingFlags.NonPublic),
                Is.Null);
            Assert.That(
                _viewType.GetField("_clipBindingRootOverride", BindingFlags.Instance | BindingFlags.NonPublic),
                Is.Null);
            Assert.That(
                _viewType.GetField("_sourceAvatar", BindingFlags.Instance | BindingFlags.NonPublic),
                Is.Not.Null);
            Assert.That(
                _viewType.GetField("_bindingRoot", BindingFlags.Instance | BindingFlags.NonPublic),
                Is.Not.Null);
            Assert.That(
                _viewType.GetMethod("FindBindingRoot", BindingFlags.Static | BindingFlags.NonPublic),
                Is.Null);
        }

        [Test]
        public void ConversionStatusExplainsEveryReadinessGate()
        {
            Assert.That(Status(false, false, false, false, false, false, false), Is.EqualTo("Assign a Humanoid Animator to begin."));
            Assert.That(Status(true, false, false, false, false, false, true), Is.EqualTo("Assign a Humanoid Avatar."));
            Assert.That(Status(true, false, false, true, false, true, true), Is.EqualTo("The selected Avatar must be valid and Humanoid."));
            Assert.That(Status(true, false, false, true, true, true, true), Is.EqualTo("Assign a Root Node."));
            Assert.That(Status(true, true, false, true, true, true, true), Is.EqualTo("Root Node must be under the Animator hierarchy."));
            Assert.That(Status(true, true, true, true, true, true, false), Is.EqualTo("Assign a source AnimationClip."));
            Assert.That(Status(true, true, true, true, true, true, true), Is.EqualTo("Ready to convert."));
        }

        [Test]
        public void ActionsAndResultsDescribeOneClipConversion()
        {
            Assert.That(_conversionActionLabel.Invoke(null, null), Is.EqualTo("Convert to Humanoid"));
            Assert.That(
                _conversionResultLabel.Invoke(null, new object[] { false, false }),
                Is.EqualTo("Converted Humanoid clip."));
            Assert.That(
                _conversionResultLabel.Invoke(null, new object[] { true, false }),
                Is.EqualTo("Humanoid conversion failed; see Diagnostics."));
            Assert.That(
                _conversionResultLabel.Invoke(null, new object[] { false, true }),
                Is.EqualTo("Converted Humanoid clip with a warning; see Diagnostics."));
        }

        [Test]
        public void SamplingAlwaysUsesTheSourceClipFrameRate()
        {
            var clip = new AnimationClip { frameRate = 24f };
            try
            {
                Assert.That(_resolveSampleFps.Invoke(null, new object[] { clip }), Is.EqualTo(24f));
                Assert.That(_resolveSampleFps.Invoke(null, new object[] { null }), Is.EqualTo(60f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void RecordedEulerCurvesDriveTheSampledTransformRotation()
        {
            var root = new GameObject("Root");
            var bone = new GameObject("Bone");
            var clip = new AnimationClip();
            try
            {
                bone.transform.SetParent(root.transform);
                SetTransformCurve(
                    clip,
                    "Bone",
                    "m_LocalEulerAngles.y",
                    AnimationCurve.Linear(0f, 0f, 1f, 15f));
                SetTransformCurve(
                    clip,
                    "Bone",
                    "localEulerAnglesRaw.y",
                    AnimationCurve.Linear(0f, 0f, 1f, 90f));

                var curveSets = _buildTransformCurveSets.Invoke(
                    null,
                    new object[] { root.transform, clip });
                _applyTransformCurveSets.Invoke(null, new[] { curveSets, (object)1f });

                Assert.That(
                    Quaternion.Angle(bone.transform.localRotation, Quaternion.Euler(0f, 90f, 0f)),
                    Is.LessThan(0.01f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
                UnityEngine.Object.DestroyImmediate(root);
            }
        }

        [Test]
        public void QuaternionCurvesRemainAuthoritativeWhenEulerCurvesCoexist()
        {
            var root = new GameObject("Root");
            var bone = new GameObject("Bone");
            var clip = new AnimationClip();
            try
            {
                bone.transform.SetParent(root.transform);
                var expected = Quaternion.Euler(0f, 30f, 0f);
                SetTransformCurve(clip, "Bone", "m_LocalRotation.x", AnimationCurve.Constant(0f, 1f, expected.x));
                SetTransformCurve(clip, "Bone", "m_LocalRotation.y", AnimationCurve.Constant(0f, 1f, expected.y));
                SetTransformCurve(clip, "Bone", "m_LocalRotation.z", AnimationCurve.Constant(0f, 1f, expected.z));
                SetTransformCurve(clip, "Bone", "m_LocalRotation.w", AnimationCurve.Constant(0f, 1f, expected.w));
                SetTransformCurve(
                    clip,
                    "Bone",
                    "localEulerAnglesRaw.y",
                    AnimationCurve.Constant(0f, 1f, 90f));

                var curveSets = _buildTransformCurveSets.Invoke(
                    null,
                    new object[] { root.transform, clip });
                _applyTransformCurveSets.Invoke(null, new[] { curveSets, (object)1f });

                Assert.That(
                    Quaternion.Angle(bone.transform.localRotation, expected),
                    Is.LessThan(0.01f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
                UnityEngine.Object.DestroyImmediate(root);
            }
        }

        [Test]
        public void AnimatorChangeClearsTheManualBindingRoot()
        {
            var character = new GameObject("Character");
            var skeleton = new GameObject("Skeleton");
            var view = Activator.CreateInstance(_viewType, true);
            try
            {
                skeleton.transform.SetParent(character.transform);
                var animator = character.AddComponent<Animator>();
                SetInstanceField(view, "_bindingRoot", skeleton);

                _assignSourceAnimator.Invoke(view, new object[] { animator });

                Assert.That(GetInstanceField<GameObject>(view, "_bindingRoot"), Is.Null);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(character);
            }
        }

        [Test]
        public void ClipChangePreservesTheManualBindingRoot()
        {
            var character = new GameObject("Character");
            var skeleton = new GameObject("Skeleton");
            var clip = CreateTransformClip("Hips/Spine");
            var view = Activator.CreateInstance(_viewType, true);
            try
            {
                skeleton.transform.SetParent(character.transform);
                SetInstanceField(view, "_sourceAnimator", character.AddComponent<Animator>());
                SetInstanceField(view, "_bindingRoot", skeleton);

                _assignSourceClip.Invoke(view, new object[] { clip });

                Assert.That(GetInstanceField<GameObject>(view, "_bindingRoot"), Is.SameAs(skeleton));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(clip);
                UnityEngine.Object.DestroyImmediate(character);
            }
        }

        [Test]
        public void BindingRootWarningReportsUnresolvedTransformPaths()
        {
            var skeleton = new GameObject("Skeleton");
            var hips = new GameObject("Hips");
            var spine = new GameObject("Spine");
            var completeClip = CreateTransformClip("Hips/Spine");
            var partialClip = CreateTransformClip("Hips/Spine", "Missing/Bone");
            try
            {
                hips.transform.SetParent(skeleton.transform);
                spine.transform.SetParent(hips.transform);

                Assert.That(
                    _bindingRootWarning.Invoke(null, new object[] { skeleton.transform, completeClip }),
                    Is.Null);
                Assert.That(
                    _bindingRootWarning.Invoke(null, new object[] { skeleton.transform, partialClip }),
                    Is.EqualTo("Root Node cannot resolve 1 of 2 Transform paths in the Source Clip. Unresolved curves will be omitted."));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(partialClip);
                UnityEngine.Object.DestroyImmediate(completeClip);
                UnityEngine.Object.DestroyImmediate(skeleton);
            }
        }

        [Test]
        public void ManualBindingRootMustRemainInsideTheAnimatorHierarchy()
        {
            var character = new GameObject("Character");
            var child = new GameObject("Skeleton");
            var outside = new GameObject("Outside");
            var view = Activator.CreateInstance(_viewType, true);
            try
            {
                child.transform.SetParent(character.transform);
                SetInstanceField(view, "_sourceAnimator", character.AddComponent<Animator>());

                SetInstanceField(view, "_bindingRoot", child);
                Assert.That(_isBindingRootValid.Invoke(view, null), Is.True);

                SetInstanceField(view, "_bindingRoot", outside);
                Assert.That(_isBindingRootValid.Invoke(view, null), Is.False);

                SetInstanceField(view, "_bindingRoot", null);
                Assert.That(_isBindingRootValid.Invoke(view, null), Is.False);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(outside);
                UnityEngine.Object.DestroyImmediate(character);
            }
        }

        [Test]
        public void ManualBindingRootIsUsedAndNullDoesNotFallback()
        {
            var character = new GameObject("Character");
            var child = new GameObject("Skeleton");
            var view = Activator.CreateInstance(_viewType, true);
            try
            {
                child.transform.SetParent(character.transform);
                SetInstanceField(view, "_sourceAnimator", character.AddComponent<Animator>());

                SetInstanceField(view, "_bindingRoot", child);
                Assert.That(_resolveSampleRoot.Invoke(view, null), Is.SameAs(child));

                SetInstanceField(view, "_bindingRoot", null);
                Assert.That(_resolveSampleRoot.Invoke(view, null), Is.Null);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(character);
            }
        }

        private static AnimationClip CreateTransformClip(params string[] paths)
        {
            var clip = new AnimationClip();
            foreach (var path in paths)
                AnimationUtility.SetEditorCurve(
                    clip,
                    EditorCurveBinding.FloatCurve(path, typeof(Transform), "m_LocalPosition.x"),
                    AnimationCurve.Constant(0f, 1f, 0f));
            return clip;
        }

        private static void SetTransformCurve(
            AnimationClip clip,
            string path,
            string propertyName,
            AnimationCurve curve)
        {
            AnimationUtility.SetEditorCurve(
                clip,
                EditorCurveBinding.FloatCurve(path, typeof(Transform), propertyName),
                curve);
        }

        private MethodInfo RequireInstanceMethod(string name)
        {
            var method = _viewType.GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(method, Is.Not.Null, $"Missing method {name}");
            return method;
        }

        private void SetInstanceField(object target, string name, object value)
        {
            var field = _viewType.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(field, Is.Not.Null, $"Missing field {name}");
            field.SetValue(target, value);
        }

        private T GetInstanceField<T>(object target, string name)
        {
            var field = _viewType.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(field, Is.Not.Null, $"Missing field {name}");
            return (T)field.GetValue(target);
        }

        private string Status(
            bool hasAnimator,
            bool hasBindingRoot,
            bool bindingRootValid,
            bool hasAvatar,
            bool avatarValid,
            bool avatarHuman,
            bool hasSourceClip)
        {
            return (string)_conversionStatusLabel.Invoke(
                null,
                new object[]
                {
                    hasAnimator,
                    hasBindingRoot,
                    bindingRootValid,
                    hasAvatar,
                    avatarValid,
                    avatarHuman,
                    hasSourceClip,
                });
        }

        private void AssertRotation(UnityEngine.AvatarIKGoal goal, Quaternion expected)
        {
            var actual = (Quaternion)_unityGoalQPostOffset.Invoke(null, new object[] { goal });
            Assert.That(Quaternion.Angle(actual, expected), Is.LessThan(0.001f));
        }
    }
}
