using System;
using System.Reflection;
using BlenderSyncVNext.SceneSyncCore;
using BlenderSyncVNext.SessionCore;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class SceneSyncTransformSmootherTests
    {
        private const string SmootherTypeName = "BlenderSyncVNext.SceneSyncCore.SceneSyncTransformSmoother";
        private const string PreferencesTypeName = "BlenderSyncVNext.SceneSyncCore.TransformSmoothingPreferences";
        private const string EnabledEditorPrefKey = "BlenderSyncVNext.TransformSmoothingEnabled";

        private bool _hadEnabledPreference;
        private bool _enabledPreference;

        [SetUp]
        public void SetUp()
        {
            ClearAllSmoothing();
            _hadEnabledPreference = EditorPrefs.HasKey(EnabledEditorPrefKey);
            _enabledPreference = EditorPrefs.GetBool(EnabledEditorPrefKey, true);
            EditorPrefs.SetBool(EnabledEditorPrefKey, true);
            ReloadPreferences();
        }

        [TearDown]
        public void TearDown()
        {
            ClearAllSmoothing();
            if (_hadEnabledPreference)
                EditorPrefs.SetBool(EnabledEditorPrefKey, _enabledPreference);
            else
                EditorPrefs.DeleteKey(EnabledEditorPrefKey);
            ReloadPreferences();
        }

        [Test]
        public void SmootherIsAnEditorOnlyManagedService()
        {
            var smootherType = ProductApi.RequireType(SmootherTypeName);

            Assert.That(smootherType.IsAbstract && smootherType.IsSealed, Is.True);
            Assert.That(typeof(Component).IsAssignableFrom(smootherType), Is.False);
            Assert.That(smootherType.Assembly.GetName().Name, Is.EqualTo("BlenderSyncVNext.Editor"));
        }

        [Test]
        public void FollowCurveControlsProgressAndSettlesExactlyAtTheConfiguredTime()
        {
            var smootherType = ProductApi.RequireType(SmootherTypeName);
            var stateType = smootherType.GetNestedType("TargetState", BindingFlags.NonPublic);
            Assert.That(stateType, Is.Not.Null);
            var setTarget = RequireMethod(stateType, "SetTarget", BindingFlags.NonPublic | BindingFlags.Instance);
            var tickOnce = RequireMethod(stateType, "TickOnce", BindingFlags.NonPublic | BindingFlags.Instance);
            var gameObject = new GameObject("SceneSyncTransformCurveTest");
            try
            {
                var state = Activator.CreateInstance(
                    stateType,
                    BindingFlags.Instance | BindingFlags.NonPublic,
                    null,
                    new object[] { gameObject.transform },
                    null);
                setTarget.Invoke(state, new object[]
                {
                    new Vector3(8f, 0f, 0f),
                    Quaternion.identity,
                    Vector3.one,
                    1f,
                    AnimationCurve.Linear(0f, 0f, 1f, 1f),
                });

                Assert.That(tickOnce.Invoke(state, new object[] { 0.25f }), Is.EqualTo(true));
                Assert.That(gameObject.transform.position.x, Is.EqualTo(2f).Within(0.0001f));

                Assert.That(tickOnce.Invoke(state, new object[] { 0.75f }), Is.EqualTo(false));
                Assert.That(gameObject.transform.position.x, Is.EqualTo(8f).Within(0.0001f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(gameObject);
            }
        }

        [Test]
        public void AutoTransformUsesManagedEditorStateAndManualTransformCancelsIt()
        {
            var gameObject = new GameObject("SceneSyncTransformRouteTest");
            try
            {
                var service = new TransformApplyService();
                var message = TransformMessage("auto_sync", 2f);
                Assert.That(service.TryApply(gameObject.transform, message, out var error), Is.True, error);
                RemoveSmoothing(gameObject.transform, completeTarget: true);
                Assert.That(gameObject.transform.position.x, Is.EqualTo(2f).Within(0.0001f));

                message = TransformMessage("auto_sync", 3f);
                Assert.That(service.TryApply(gameObject.transform, message, out error), Is.True, error);
                message = TransformMessage("manual_sync", 4f);
                Assert.That(service.TryApply(gameObject.transform, message, out error), Is.True, error);
                Assert.That(gameObject.transform.position.x, Is.EqualTo(4f).Within(0.0001f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(gameObject);
            }
        }

        [Test]
        public void DisabledSmoothingAppliesAutomaticTransformsImmediately()
        {
            EditorPrefs.SetBool(EnabledEditorPrefKey, false);
            ReloadPreferences();
            var gameObject = new GameObject("SceneSyncTransformDisabledTest");
            try
            {
                var service = new TransformApplyService();
                Assert.That(
                    service.TryApply(gameObject.transform, TransformMessage("auto_sync", 6f), out var error),
                    Is.True,
                    error);
                Assert.That(gameObject.transform.position.x, Is.EqualTo(6f).Within(0.0001f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(gameObject);
            }
        }

        [Test]
        public void DisablingSmoothingCompletesAndRemovesAnActiveTarget()
        {
            var preferencesType = ProductApi.RequireType(PreferencesTypeName);
            var enabledProperty = preferencesType.GetProperty(
                "Enabled",
                BindingFlags.NonPublic | BindingFlags.Static);
            Assert.That(enabledProperty, Is.Not.Null);
            var gameObject = new GameObject("SceneSyncTransformDisableActiveTest");
            try
            {
                var service = new TransformApplyService();
                Assert.That(
                    service.TryApply(gameObject.transform, TransformMessage("auto_sync", 7f), out var error),
                    Is.True,
                    error);
                enabledProperty.SetValue(null, false);

                Assert.That(gameObject.transform.position.x, Is.EqualTo(7f).Within(0.0001f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(gameObject);
            }
        }

        [Test]
        public void DirectObjectStateCancelsAnOlderWorldSpaceSmoothingTarget()
        {
            var gameObject = new GameObject("SceneSyncObjectStateRouteTest");
            try
            {
                var transformService = new TransformApplyService();
                Assert.That(
                    transformService.TryApply(
                        gameObject.transform,
                        TransformMessage("auto_sync", 50f),
                        out var error),
                    Is.True,
                    error);
                var message = new SceneSyncObjectStateUpdateMessage
                {
                    type = "scene_sync.object_state_update_v1",
                    objects = new[]
                    {
                        new SceneSyncObjectStateItem
                        {
                            pairId = "pair-test",
                            objectName = gameObject.name,
                            objectType = "mesh",
                            visible = true,
                            clearParent = true,
                            position = new[] { 3f, 0f, 0f },
                            rotation = new[] { 0f, 0f, 0f, 1f },
                            scale = new[] { 1f, 1f, 1f },
                        },
                    },
                };

                new ObjectStateUpdateService().Apply(
                    message,
                    pairId => pairId == "pair-test" ? gameObject.transform : null);

                ClearAllSmoothing();
                Assert.That(gameObject.transform.position.x, Is.EqualTo(3f).Within(0.0001f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(gameObject);
            }
        }

        [Test]
        public void TransformApplyRejectsPlayModeWithoutChangingTheTarget()
        {
            var guardType = ProductApi.RequireType("BlenderSyncVNext.SceneSyncCore.EditModeGuard");
            var availableField = guardType.GetField(
                "_isAvailable",
                BindingFlags.NonPublic | BindingFlags.Static);
            Assert.That(availableField, Is.Not.Null);
            var previous = (bool)availableField.GetValue(null);
            var gameObject = new GameObject("SceneSyncTransformPlayModeGuardTest");
            try
            {
                gameObject.transform.position = new Vector3(1f, 2f, 3f);
                availableField.SetValue(null, false);

                var applied = new TransformApplyService().TryApply(
                    gameObject.transform,
                    TransformMessage("auto_sync", 9f),
                    out var error);

                Assert.That(applied, Is.False);
                Assert.That(error, Is.EqualTo("transform_edit_mode_only"));
                Assert.That(gameObject.transform.position, Is.EqualTo(new Vector3(1f, 2f, 3f)));
            }
            finally
            {
                availableField.SetValue(null, previous);
                UnityEngine.Object.DestroyImmediate(gameObject);
            }
        }

        [Test]
        public void SessionConnectRejectsPlayMode()
        {
            var guardType = ProductApi.RequireType("BlenderSyncVNext.SceneSyncCore.EditModeGuard");
            var availableField = guardType.GetField(
                "_isAvailable",
                BindingFlags.NonPublic | BindingFlags.Static);
            Assert.That(availableField, Is.Not.Null);
            var previous = (bool)availableField.GetValue(null);
            try
            {
                SessionClient.SharedTransport.Stop();
                availableField.SetValue(null, false);

                new SessionClient().Connect("ws://127.0.0.1:1/ws/");

                Assert.That(SessionClient.SharedTransport.IsRunning, Is.False);
                Assert.That(SessionClient.LastLifecycleState, Is.EqualTo("edit_mode_required"));
            }
            finally
            {
                availableField.SetValue(null, previous);
                SessionClient.SharedTransport.Stop();
            }
        }

        [TestCase(false, false, true)]
        [TestCase(true, true, false)]
        [TestCase(false, true, false)]
        public void EditModeGuardRejectsPlayAndTransitionStates(
            bool isPlaying,
            bool isPlayingOrWillChangePlaymode,
            bool expected)
        {
            var method = RequireMethod(
                ProductApi.RequireType("BlenderSyncVNext.SceneSyncCore.EditModeGuard"),
                "IsEditMode",
                BindingFlags.NonPublic | BindingFlags.Static);

            Assert.That(
                method.Invoke(null, new object[] { isPlaying, isPlayingOrWillChangePlaymode }),
                Is.EqualTo(expected));
        }

        private static SceneSyncTransformMessage TransformMessage(string sourceHint, float blenderX)
        {
            return new SceneSyncTransformMessage
            {
                type = "scene_sync.transform",
                pairId = "pair-test",
                sourceHint = sourceHint,
                position = new[] { blenderX, 0f, 0f },
                rotation = new[] { 0f, 0f, 0f, 1f },
                scale = new[] { 1f, 1f, 1f },
            };
        }

        private static MethodInfo RequireMethod(Type type, string name, BindingFlags flags)
        {
            return type.GetMethod(name, flags)
                ?? throw new InvalidOperationException($"Method not found: {type.FullName}.{name}");
        }

        private static void ClearAllSmoothing()
        {
            RequireMethod(
                    ProductApi.RequireType(SmootherTypeName),
                    "CompleteAll",
                    BindingFlags.Public | BindingFlags.Static)
                .Invoke(null, null);
        }

        private static void RemoveSmoothing(Transform target, bool completeTarget)
        {
            RequireMethod(
                    ProductApi.RequireType(SmootherTypeName),
                    "RemoveFrom",
                    BindingFlags.Public | BindingFlags.Static)
                .Invoke(null, new object[] { target, completeTarget });
        }

        private static void ReloadPreferences()
        {
            RequireMethod(
                    ProductApi.RequireType(PreferencesTypeName),
                    "Reload",
                    BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, null);
        }
    }
}
