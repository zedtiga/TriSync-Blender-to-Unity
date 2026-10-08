using System;
using System.Collections.Generic;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using BlenderSyncVNext.AnimationClipImport;

namespace BlenderSyncVNext.Tests
{
    public sealed class AnimationClipDurationTests
    {
        [SetUp]
        public void RemoveStaleGeneratedAssets()
        {
            DeleteGeneratedAssets();
        }

        [TearDown]
        public void RemoveGeneratedAssets()
        {
            DeleteGeneratedAssets();
        }

        [Test]
        public void ImportedStaticClipUsesExportedDurationInsteadOfSingleKeyExtent()
        {
            var assetName = "StaticDurationTest_" + Guid.NewGuid().ToString("N");
            var payload = new AnimationClipPayload
            {
                assetId = assetName,
                name = assetName,
                frameRate = 30f,
                startFrame = 1,
                endFrame = 61,
                channelSemantic = "unity_object_v1",
                exportSettings = new AnimationExportSettings
                {
                    staticCurves = "collapse_constant",
                },
                exportReport = new AnimationExportReport
                {
                    startFrame = 1,
                    endFrame = 61,
                    sceneFrameRate = 30f,
                    durationSeconds = 2f,
                },
                tracks = new List<AnimationTrackDto>
                {
                    new AnimationTrackDto
                    {
                        path = string.Empty,
                        targetType = "object",
                        property = "localPosition",
                        component = "x",
                        keys = new List<AnimationKeyDto>
                        {
                            new AnimationKeyDto { time = 0f, value = 1f },
                        },
                    },
                },
            };
            var result = new AnimationClipBuilder().BuildOrUpdateClip(payload);
            try
            {
                Assert.That(result.success, Is.True, result.error);
                var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(result.assetPath);
                Assert.That(clip, Is.Not.Null);
                var settings = AnimationUtility.GetAnimationClipSettings(clip);
                Assert.That(
                    settings.stopTime,
                    Is.EqualTo(2f).Within(1e-5f),
                    $"length={clip.length:0.######}");
                Assert.That(clip.length, Is.EqualTo(2f).Within(1e-5f));
                var bindings = AnimationUtility.GetCurveBindings(clip);
                Assert.That(bindings, Has.Length.EqualTo(1));
                Assert.That(AnimationUtility.GetEditorCurve(clip, bindings[0]).length, Is.EqualTo(2));
            }
            finally
            {
                DeleteGeneratedAssets();
            }
        }

        [Test]
        public void RiggedClipBindsShapeKeyTrackToSkinnedMeshRendererChild()
        {
            var assetName = "StaticDurationTest_RigShapes_" + Guid.NewGuid().ToString("N");
            var payload = new AnimationClipPayload
            {
                assetId = assetName,
                name = assetName,
                frameRate = 30f,
                startFrame = 1,
                endFrame = 31,
                channelSemantic = "unity_rig_v1",
                exportSettings = new AnimationExportSettings
                {
                    interpolation = "linear",
                },
                tracks = new List<AnimationTrackDto>
                {
                    new AnimationTrackDto
                    {
                        path = "Face",
                        targetType = "blend_shape",
                        property = "blendShapeWeight",
                        component = "Smile",
                        keys = new List<AnimationKeyDto>
                        {
                            new AnimationKeyDto { time = 0f, value = 0f },
                            new AnimationKeyDto { time = 1f, value = 100f },
                        },
                    },
                },
            };
            var result = new AnimationClipBuilder().BuildOrUpdateClip(payload);
            try
            {
                Assert.That(result.success, Is.True, result.error);
                var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(result.assetPath);
                Assert.That(clip, Is.Not.Null);
                var bindings = AnimationUtility.GetCurveBindings(clip);
                Assert.That(bindings, Has.Length.EqualTo(1));
                Assert.That(bindings[0].path, Is.EqualTo("Face"));
                Assert.That(bindings[0].type, Is.EqualTo(typeof(SkinnedMeshRenderer)));
                Assert.That(bindings[0].propertyName, Is.EqualTo("blendShape.Smile"));
                var curve = AnimationUtility.GetEditorCurve(clip, bindings[0]);
                Assert.That(curve, Is.Not.Null);
                Assert.That(curve.Evaluate(0f), Is.EqualTo(0f).Within(1e-5f));
                Assert.That(curve.Evaluate(1f), Is.EqualTo(100f).Within(1e-5f));
            }
            finally
            {
                DeleteGeneratedAssets();
            }
        }

        private static void DeleteGeneratedAssets()
        {
            foreach (var guid in AssetDatabase.FindAssets(
                         "StaticDurationTest_",
                         new[] { "Assets/TriSync/Resources/AnimationClips" }))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                if (!string.IsNullOrWhiteSpace(path))
                    AssetDatabase.DeleteAsset(path);
            }
            AssetDatabase.Refresh();
        }
    }
}
