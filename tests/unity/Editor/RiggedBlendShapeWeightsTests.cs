using System;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Protocol;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class RiggedBlendShapeWeightsTests
    {
        [Test]
        public void AppliesMultipleRiggedRenderersAndRecordsUndo()
        {
            Undo.ClearAll();
            var root = new GameObject("RiggedShapeRoot");
            var face = CreateRenderer(root.transform, "Face", "Smile");
            var body = CreateRenderer(root.transform, "Body", "Breath");
            try
            {
                var payload = new RiggedBlendShapeWeightsEnvelope
                {
                    type = "asset_bridge.rigged_blendshape_weights_v1",
                    version = 1,
                    riggedObjectId = "rig-id",
                    parts = new[]
                    {
                        new RiggedBlendShapePartPayload
                        {
                            objectName = "Face",
                            weights = new[]
                            {
                                new RiggedBlendShapeWeightPayload { name = "Smile", index = 1, weight = 72f },
                            },
                        },
                        new RiggedBlendShapePartPayload
                        {
                            objectName = "Body",
                            weights = new[]
                            {
                                new RiggedBlendShapeWeightPayload { name = "Breath", index = 1, weight = 28f },
                            },
                        },
                    },
                };

                var ok = new RiggedBlendShapeWeightsApplyService().TryApplyToRoot(root, payload, out var error);

                Assert.That(ok, Is.True, error);
                Assert.That(face.GetBlendShapeWeight(0), Is.EqualTo(72f).Within(0.001f));
                Assert.That(body.GetBlendShapeWeight(0), Is.EqualTo(28f).Within(0.001f));

                Undo.PerformUndo();
                Assert.That(face.GetBlendShapeWeight(0), Is.EqualTo(0f).Within(0.001f));
                Assert.That(body.GetBlendShapeWeight(0), Is.EqualTo(0f).Within(0.001f));
            }
            finally
            {
                Undo.ClearAll();
                UnityEngine.Object.DestroyImmediate(face.sharedMesh);
                UnityEngine.Object.DestroyImmediate(body.sharedMesh);
                UnityEngine.Object.DestroyImmediate(root);
            }
        }

        [Test]
        public void AppliesByRendererNameAndReportsPartialShapeKeyMatches()
        {
            var root = new GameObject("RiggedShapePartialRoot");
            var face = CreateRenderer(root.transform, "Face", "Smile");
            try
            {
                var payload = new RiggedBlendShapeWeightsEnvelope
                {
                    type = "asset_bridge.rigged_blendshape_weights_v1",
                    riggedObjectId = "rig-id",
                    parts = new[]
                    {
                        new RiggedBlendShapePartPayload
                        {
                            objectName = "Face",
                            weights = new[]
                            {
                                new RiggedBlendShapeWeightPayload { name = "Smile", index = 1, weight = 50f },
                                new RiggedBlendShapeWeightPayload { name = "Missing", index = 2, weight = 10f },
                            },
                        },
                    },
                };

                var ok = new RiggedBlendShapeWeightsApplyService().TryApplyToRoot(root, payload, out var error);

                Assert.That(ok, Is.True, error);
                Assert.That(face.GetBlendShapeWeight(0), Is.EqualTo(50f).Within(0.001f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(face.sharedMesh);
                UnityEngine.Object.DestroyImmediate(root);
            }
        }

        private static SkinnedMeshRenderer CreateRenderer(Transform parent, string name, string blendShapeName)
        {
            var node = new GameObject(name);
            node.transform.SetParent(parent, false);
            var renderer = node.AddComponent<SkinnedMeshRenderer>();
            var mesh = new Mesh { name = name + "Mesh" };
            mesh.vertices = new[]
            {
                new Vector3(0f, 0f, 0f),
                new Vector3(1f, 0f, 0f),
                new Vector3(0f, 1f, 0f),
            };
            mesh.triangles = new[] { 0, 1, 2 };
            mesh.AddBlendShapeFrame(
                blendShapeName,
                100f,
                new[] { new Vector3(0.1f, 0f, 0f), Vector3.zero, Vector3.zero },
                new Vector3[3],
                new Vector3[3]);
            renderer.sharedMesh = mesh;
            return renderer;
        }
    }
}
