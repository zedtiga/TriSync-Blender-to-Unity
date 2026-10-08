using System;
using BlenderSyncVNext.Protocol;
using BlenderSyncVNext.SceneSyncCore;
using NUnit.Framework;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class MeshApplyServiceTests
    {
        [Test]
        public void PairBindingTracksTheCurrentMeshIdentity()
        {
            var pairId = "pair-test-" + Guid.NewGuid().ToString("N");
            var first = new Mesh { name = "FirstBindingMesh" };
            var second = new Mesh { name = "SecondBindingMesh" };
            try
            {
                MeshApplyService.RegisterPairMeshBinding(pairId, first);
                Assert.That(MeshApplyService.IsPairBoundToMesh(pairId, first), Is.True);
                Assert.That(MeshApplyService.IsPairBoundToMesh(pairId, second), Is.False);

                MeshApplyService.RegisterPairMeshBinding(pairId, second);
                Assert.That(MeshApplyService.IsPairBoundToMesh(pairId, first), Is.False);
                Assert.That(MeshApplyService.IsPairBoundToMesh(pairId, second), Is.True);

                MeshApplyService.ClearPairMeshBinding(pairId);
                Assert.That(MeshApplyService.IsPairBoundToMesh(pairId, second), Is.False);
            }
            finally
            {
                MeshApplyService.ClearPairMeshBinding(pairId);
                UnityEngine.Object.DestroyImmediate(first);
                UnityEngine.Object.DestroyImmediate(second);
            }
        }

        [Test]
        public void RendererReconciliationIncludesInactiveObjects()
        {
            var mesh = new Mesh { name = "ReconcileMesh" };
            var active = CreateMeshObject("ActiveMeshObject", mesh, true);
            var inactive = CreateMeshObject("InactiveMeshObject", mesh, false);
            try
            {
                Assert.That(MeshReferenceApplyService.ReconcileAllRenderersForMesh(mesh), Is.EqualTo(2));
                Assert.That(active.GetComponent<MeshFilter>().sharedMesh, Is.SameAs(mesh));
                Assert.That(inactive.GetComponent<MeshFilter>().sharedMesh, Is.SameAs(mesh));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(active);
                UnityEngine.Object.DestroyImmediate(inactive);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void RendererReconciliationPreservesBlendShapeWeightsByName()
        {
            var oldMesh = CreateBlendShapeMesh("OldBlendShapeMesh", "Smile", "Blink");
            var newMesh = CreateBlendShapeMesh("NewBlendShapeMesh", "Blink", "Smile");
            var owner = new GameObject("SkinnedReconcileObject");
            var skinned = owner.AddComponent<SkinnedMeshRenderer>();
            skinned.sharedMesh = oldMesh;
            skinned.SetBlendShapeWeight(oldMesh.GetBlendShapeIndex("Smile"), 37f);
            skinned.SetBlendShapeWeight(oldMesh.GetBlendShapeIndex("Blink"), 82f);

            try
            {
                MeshReferenceApplyService.ReconcileRendererForMesh(owner, newMesh);

                var result = owner.GetComponent<SkinnedMeshRenderer>();
                Assert.That(result, Is.Not.Null);
                Assert.That(result.sharedMesh, Is.SameAs(newMesh));
                Assert.That(result.GetBlendShapeWeight(newMesh.GetBlendShapeIndex("Smile")), Is.EqualTo(37f).Within(0.001f));
                Assert.That(result.GetBlendShapeWeight(newMesh.GetBlendShapeIndex("Blink")), Is.EqualTo(82f).Within(0.001f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(owner);
                UnityEngine.Object.DestroyImmediate(oldMesh);
                UnityEngine.Object.DestroyImmediate(newMesh);
            }
        }

        [Test]
        public void TangentGenerationPreservesMirroredUvHandedness()
        {
            var regular = CreateTangentTriangle("RegularTangentMesh", mirroredUv: false);
            var mirrored = CreateTangentTriangle("MirroredTangentMesh", mirroredUv: true);
            try
            {
                Assert.That(MeshApplyService.RecalculateTangentsIfPossible(regular), Is.True);
                Assert.That(MeshApplyService.RecalculateTangentsIfPossible(mirrored), Is.True);
                AssertValidTangents(regular);
                AssertValidTangents(mirrored);
                Assert.That(
                    Mathf.Sign(mirrored.tangents[0].w),
                    Is.EqualTo(-Mathf.Sign(regular.tangents[0].w)));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(regular);
                UnityEngine.Object.DestroyImmediate(mirrored);
            }
        }

        [Test]
        public void TangentGenerationClearsStaleDataWithoutUvOrTriangleTopology()
        {
            var noUv = CreateTangentTriangle("NoUvTangentMesh", mirroredUv: false);
            var lines = CreateTangentTriangle("LineTangentMesh", mirroredUv: false);
            try
            {
                noUv.uv = Array.Empty<Vector2>();
                noUv.tangents = StaleTangents(noUv.vertexCount);
                Assert.That(MeshApplyService.RecalculateTangentsIfPossible(noUv), Is.False);
                Assert.That(noUv.tangents, Is.Empty);

                lines.SetIndices(new[] { 0, 1, 1, 2 }, MeshTopology.Lines, 0);
                lines.tangents = StaleTangents(lines.vertexCount);
                Assert.That(MeshApplyService.RecalculateTangentsIfPossible(lines), Is.False);
                Assert.That(lines.tangents, Is.Empty);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(noUv);
                UnityEngine.Object.DestroyImmediate(lines);
            }
        }

        [Test]
        public void SceneSyncFullApplyGeneratesTangents()
        {
            var pairId = "pair-tangent-" + Guid.NewGuid().ToString("N");
            var target = new GameObject("SceneSyncTangentTarget");
            Mesh mesh = null;
            try
            {
                var message = new SceneSyncMeshUpdateMessage
                {
                    vertices = TriangleVerticesFlat(),
                    triangles = new[] { 0, 1, 2 },
                    uv = TriangleUvFlat(mirrored: false),
                };

                var ok = new MeshApplyService().TryApply(pairId, target, message, out var error);

                Assert.That(ok, Is.True, error);
                mesh = target.GetComponent<MeshFilter>().sharedMesh;
                AssertValidTangents(mesh);
            }
            finally
            {
                MeshApplyService.ClearPairMeshBinding(pairId);
                UnityEngine.Object.DestroyImmediate(target);
                if (mesh != null)
                    UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void AssetBridgeMeshApplyGeneratesTangents()
        {
            var mesh = new Mesh { name = "AssetBridgeTangentMesh" };
            try
            {
                var payload = new MeshPayload
                {
                    topology = "triangles",
                    vertices = TriangleVerticesFlat(),
                    indices = new[] { 0, 1, 2 },
                    uvChannels = new[]
                    {
                        new MeshUvChannelPayload
                        {
                            index = 0,
                            name = "UVMap",
                            values = TriangleUvFlat(mirrored: false),
                        },
                    },
                };
                var apply = ProductApi.RequireStaticMethod(
                    "BlenderSyncVNext.AssetBridgeCore.AssetContainerService",
                    "ApplyMeshPayload",
                    2);

                apply.Invoke(null, new object[] { mesh, payload });

                AssertValidTangents(mesh);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        private static GameObject CreateMeshObject(string name, Mesh mesh, bool active)
        {
            var owner = new GameObject(name);
            owner.AddComponent<MeshFilter>().sharedMesh = mesh;
            owner.AddComponent<MeshRenderer>();
            owner.SetActive(active);
            return owner;
        }

        private static Mesh CreateBlendShapeMesh(string name, params string[] shapeNames)
        {
            var mesh = new Mesh { name = name };
            mesh.vertices = new[]
            {
                Vector3.zero,
                Vector3.right,
                Vector3.up,
            };
            mesh.triangles = new[] { 0, 1, 2 };
            foreach (var shapeName in shapeNames)
            {
                mesh.AddBlendShapeFrame(
                    shapeName,
                    100f,
                    new[] { Vector3.zero, new Vector3(0.1f, 0f, 0f), Vector3.zero },
                    new Vector3[3],
                    new Vector3[3]);
            }
            return mesh;
        }

        private static Mesh CreateTangentTriangle(string name, bool mirroredUv)
        {
            var mesh = new Mesh { name = name };
            mesh.vertices = new[] { Vector3.zero, Vector3.right, Vector3.up };
            mesh.normals = new[] { Vector3.forward, Vector3.forward, Vector3.forward };
            mesh.uv = mirroredUv
                ? new[] { Vector2.zero, Vector2.left, Vector2.up }
                : new[] { Vector2.zero, Vector2.right, Vector2.up };
            mesh.triangles = new[] { 0, 1, 2 };
            return mesh;
        }

        private static void AssertValidTangents(Mesh mesh)
        {
            Assert.That(mesh, Is.Not.Null);
            Assert.That(mesh.tangents, Has.Length.EqualTo(mesh.vertexCount));
            foreach (var tangent in mesh.tangents)
            {
                Assert.That(new Vector3(tangent.x, tangent.y, tangent.z).magnitude, Is.EqualTo(1f).Within(0.0001f));
                Assert.That(Mathf.Abs(tangent.w), Is.EqualTo(1f).Within(0.0001f));
            }
        }

        private static Vector4[] StaleTangents(int count)
        {
            var tangents = new Vector4[count];
            for (var i = 0; i < count; i++)
                tangents[i] = new Vector4(0f, 1f, 0f, 1f);
            return tangents;
        }

        private static float[] TriangleVerticesFlat()
        {
            return new[]
            {
                0f, 0f, 0f,
                1f, 0f, 0f,
                0f, 1f, 0f,
            };
        }

        private static float[] TriangleUvFlat(bool mirrored)
        {
            return mirrored
                ? new[] { 0f, 0f, -1f, 0f, 0f, 1f }
                : new[] { 0f, 0f, 1f, 0f, 0f, 1f };
        }
    }
}
