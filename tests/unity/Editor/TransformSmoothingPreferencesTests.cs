using System;
using System.Reflection;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class TransformSmoothingPreferencesTests
    {
        private const string TypeName = "BlenderSyncVNext.SceneSyncCore.TransformSmoothingPreferences";
        private const string EnabledKey = "BlenderSyncVNext.TransformSmoothingEnabled";
        private const string TimeKey = "BlenderSyncVNext.TransformSmoothingTime";
        private const string CurveKey = "BlenderSyncVNext.TransformSmoothingCurve";

        private Type _type;
        private bool _hadEnabled;
        private bool _enabled;
        private bool _hadTime;
        private float _time;
        private bool _hadCurve;
        private string _curve;

        [SetUp]
        public void SetUp()
        {
            _type = ProductApi.RequireType(TypeName);
            _hadEnabled = EditorPrefs.HasKey(EnabledKey);
            _enabled = EditorPrefs.GetBool(EnabledKey, true);
            _hadTime = EditorPrefs.HasKey(TimeKey);
            _time = EditorPrefs.GetFloat(TimeKey, 0.2f);
            _hadCurve = EditorPrefs.HasKey(CurveKey);
            _curve = EditorPrefs.GetString(CurveKey, string.Empty);
            EditorPrefs.DeleteKey(EnabledKey);
            EditorPrefs.DeleteKey(TimeKey);
            EditorPrefs.DeleteKey(CurveKey);
            Reload();
        }

        [TearDown]
        public void TearDown()
        {
            if (_hadEnabled)
                EditorPrefs.SetBool(EnabledKey, _enabled);
            else
                EditorPrefs.DeleteKey(EnabledKey);
            if (_hadTime)
                EditorPrefs.SetFloat(TimeKey, _time);
            else
                EditorPrefs.DeleteKey(TimeKey);
            if (_hadCurve)
                EditorPrefs.SetString(CurveKey, _curve);
            else
                EditorPrefs.DeleteKey(CurveKey);
            Reload();
        }

        [Test]
        public void DefaultsMatchTheCurrentTransformSmoothingBehavior()
        {
            Assert.That(GetProperty<bool>("Enabled"), Is.True);
            Assert.That(GetProperty<float>("SmoothingTime"), Is.EqualTo(0.2f).Within(0.0001f));
            var curve = GetProperty<AnimationCurve>("FollowCurve");
            Assert.That(curve.keys[0].time, Is.EqualTo(0f));
            Assert.That(curve.keys[0].value, Is.EqualTo(0f));
            Assert.That(curve.keys[0].outTangent, Is.EqualTo(2f).Within(0.0001f));
            Assert.That(curve.keys[curve.length - 1].time, Is.EqualTo(1f));
            Assert.That(curve.keys[curve.length - 1].value, Is.EqualTo(1f));
            Assert.That(curve.keys[curve.length - 1].inTangent, Is.EqualTo(0f).Within(0.0001f));
            Assert.That(curve.Evaluate(0.25f), Is.EqualTo(0.4375f).Within(0.0001f));
            Assert.That(curve.Evaluate(0.25f), Is.GreaterThan(0.25f));
        }

        [Test]
        public void UserTimeAndCurveRoundTripThroughEditorPrefs()
        {
            SetProperty("SmoothingTime", 0.42f);
            SetProperty(
                "FollowCurve",
                new AnimationCurve(
                    new Keyframe(0f, 0f),
                    new Keyframe(0.4f, 0.8f),
                    new Keyframe(1f, 1f)));
            Reload();

            Assert.That(GetProperty<float>("SmoothingTime"), Is.EqualTo(0.42f).Within(0.0001f));
            var curve = GetProperty<AnimationCurve>("FollowCurve");
            Assert.That(curve.length, Is.EqualTo(3));
            Assert.That(curve.keys[1].time, Is.EqualTo(0.4f).Within(0.0001f));
            Assert.That(curve.keys[1].value, Is.EqualTo(0.8f).Within(0.0001f));
        }

        [Test]
        public void CurveNormalizationPinsEndpointsAndPreventsDescendingKeys()
        {
            var source = new AnimationCurve(
                new Keyframe(-1f, 0.3f),
                new Keyframe(0.3f, 0.8f),
                new Keyframe(0.7f, 0.2f),
                new Keyframe(2f, 0.6f));
            var normalized = (AnimationCurve)RequireMethod("NormalizeCurve")
                .Invoke(null, new object[] { source });

            Assert.That(normalized.keys[0].time, Is.EqualTo(0f));
            Assert.That(normalized.keys[0].value, Is.EqualTo(0f));
            Assert.That(normalized.keys[normalized.length - 1].time, Is.EqualTo(1f));
            Assert.That(normalized.keys[normalized.length - 1].value, Is.EqualTo(1f));
            for (var index = 1; index < normalized.length; index++)
            {
                Assert.That(normalized.keys[index].time, Is.GreaterThan(normalized.keys[index - 1].time));
                Assert.That(normalized.keys[index].value, Is.GreaterThanOrEqualTo(normalized.keys[index - 1].value));
            }
        }

        [Test]
        public void CorruptStoredValuesFallBackToSafeDefaults()
        {
            EditorPrefs.SetFloat(TimeKey, float.NaN);
            EditorPrefs.SetString(CurveKey, "{not-json");
            Reload();

            Assert.That(GetProperty<float>("SmoothingTime"), Is.EqualTo(0.2f).Within(0.0001f));
            var curve = GetProperty<AnimationCurve>("FollowCurve");
            Assert.That(curve.keys[0].value, Is.EqualTo(0f));
            Assert.That(curve.keys[curve.length - 1].value, Is.EqualTo(1f));
        }

        [Test]
        public void ResetRemovesOverridesAndRestoresAllSmoothingDefaults()
        {
            SetProperty("Enabled", false);
            SetProperty("SmoothingTime", 0.6f);
            SetProperty(
                "FollowCurve",
                AnimationCurve.Linear(0f, 0f, 1f, 1f));

            RequireMethod("ResetSmoothing").Invoke(null, null);

            Assert.That(GetProperty<bool>("Enabled"), Is.True);
            Assert.That(GetProperty<float>("SmoothingTime"), Is.EqualTo(0.2f).Within(0.0001f));
            Assert.That(EditorPrefs.HasKey(EnabledKey), Is.False);
            Assert.That(EditorPrefs.HasKey(TimeKey), Is.False);
            Assert.That(EditorPrefs.HasKey(CurveKey), Is.False);
        }

        private T GetProperty<T>(string name)
        {
            return (T)RequireProperty(name).GetValue(null);
        }

        private void SetProperty(string name, object value)
        {
            RequireProperty(name).SetValue(null, value);
        }

        private PropertyInfo RequireProperty(string name)
        {
            return _type.GetProperty(name, BindingFlags.NonPublic | BindingFlags.Static)
                ?? throw new InvalidOperationException($"Property not found: {TypeName}.{name}");
        }

        private MethodInfo RequireMethod(string name)
        {
            return _type.GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static)
                ?? throw new InvalidOperationException($"Method not found: {TypeName}.{name}");
        }

        private void Reload()
        {
            RequireMethod("Reload").Invoke(null, null);
        }
    }
}
