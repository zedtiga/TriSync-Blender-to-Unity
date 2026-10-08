using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using BlenderSyncVNext.SceneSyncCore;
using NUnit.Framework;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class PreviewMeshServiceTests
    {
        private const string TypeName = "BlenderSyncVNext.SceneSyncCore.PreviewMeshService";
        private MethodInfo _runtimeFingerprint;
        private MethodInfo _copyMeshData;
        private readonly List<Mesh> _meshes = new List<Mesh>();
        private readonly List<string> _temporaryFiles = new List<string>();

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _runtimeFingerprint = ProductApi.RequireStaticMethod(TypeName, "ComputeUnityMeshRuntimeFingerprint", 1);
            _copyMeshData = ProductApi.RequireStaticMethod(TypeName, "CopyMeshData", 2);
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var mesh in _meshes)
            {
                if (mesh != null)
                    UnityEngine.Object.DestroyImmediate(mesh);
            }
            _meshes.Clear();
            foreach (var path in _temporaryFiles)
            {
                if (File.Exists(path))
                    File.Delete(path);
            }
            _temporaryFiles.Clear();
        }

        [Test]
        public void RuntimeFingerprintIsStableForIdenticalMeshData()
        {
            var first = CreateTriangleMesh();
            var second = CreateTriangleMesh();

            Assert.That(Fingerprint(first), Is.EqualTo(Fingerprint(first)));
            Assert.That(Fingerprint(second), Is.EqualTo(Fingerprint(first)));
        }

        [Test]
        public void RuntimeFingerprintCoversGeometryUvColorAndIndices()
        {
            var baseline = Fingerprint(CreateTriangleMesh());
            var vertexChanged = CreateTriangleMesh();
            var vertices = vertexChanged.vertices;
            vertices[1] = new Vector3(2f, 0f, 0f);
            vertexChanged.vertices = vertices;

            var uvChanged = CreateTriangleMesh();
            var uvs = uvChanged.uv;
            uvs[2] = new Vector2(0.5f, 0.75f);
            uvChanged.uv = uvs;

            var colorChanged = CreateTriangleMesh();
            var colors = colorChanged.colors32;
            colors[0] = new Color32(10, 20, 30, 255);
            colorChanged.colors32 = colors;

            var indicesChanged = CreateTriangleMesh();
            indicesChanged.SetTriangles(new[] { 0, 2, 1 }, 0);

            Assert.That(Fingerprint(vertexChanged), Is.Not.EqualTo(baseline));
            Assert.That(Fingerprint(uvChanged), Is.Not.EqualTo(baseline));
            Assert.That(Fingerprint(colorChanged), Is.Not.EqualTo(baseline));
            Assert.That(Fingerprint(indicesChanged), Is.Not.EqualTo(baseline));
        }

        [Test]
        public void RuntimeFingerprintCoversBlendShapeFrameDeltas()
        {
            var first = CreateTriangleMesh();
            first.AddBlendShapeFrame(
                "Smile",
                100f,
                new[] { Vector3.zero, new Vector3(0.1f, 0f, 0f), Vector3.zero },
                new Vector3[3],
                new Vector3[3]);

            var second = CreateTriangleMesh();
            second.AddBlendShapeFrame(
                "Smile",
                100f,
                new[] { Vector3.zero, new Vector3(0.2f, 0f, 0f), Vector3.zero },
                new Vector3[3],
                new Vector3[3]);

            Assert.That(Fingerprint(second), Is.Not.EqualTo(Fingerprint(first)));
        }

        [Test]
        public void CopyMeshDataPreservesAllSupportedUvChannels()
        {
            var source = CreateTriangleMesh();
            var target = new Mesh { name = "CopyTarget" };
            _meshes.Add(target);
            source.SetUVs(1, new List<Vector2>
            {
                new Vector2(0.1f, 0.2f),
                new Vector2(0.3f, 0.4f),
                new Vector2(0.5f, 0.6f),
            });
            source.SetUVs(7, new List<Vector2>
            {
                new Vector2(0.7f, 0.8f),
                new Vector2(0.9f, 1.0f),
                new Vector2(1.1f, 1.2f),
            });

            _copyMeshData.Invoke(null, new object[] { source, target });

            var uv = new List<Vector2>();
            target.GetUVs(0, uv);
            Assert.That(uv, Is.EqualTo(new List<Vector2>
            {
                Vector2.zero,
                Vector2.right,
                Vector2.up,
            }));
            target.GetUVs(1, uv = new List<Vector2>());
            Assert.That(uv, Is.EqualTo(new List<Vector2>
            {
                new Vector2(0.1f, 0.2f),
                new Vector2(0.3f, 0.4f),
                new Vector2(0.5f, 0.6f),
            }));
            target.GetUVs(7, uv = new List<Vector2>());
            Assert.That(uv, Is.EqualTo(new List<Vector2>
            {
                new Vector2(0.7f, 0.8f),
                new Vector2(0.9f, 1.0f),
                new Vector2(1.1f, 1.2f),
            }));
            target.GetUVs(2, uv = new List<Vector2>());
            Assert.That(uv, Is.Empty);
        }

        [Test]
        public void PreviewFullAndIncrementalUpdatesRegenerateTangents()
        {
            var pairId = "pair-preview-tangent-" + Guid.NewGuid().ToString("N");
            var target = new GameObject("PreviewTangentTarget");
            var service = new PreviewMeshService();
            try
            {
                var ok = service.TryApplyPreview(
                    pairId,
                    target,
                    null,
                    new[] { Vector3.zero, Vector3.right, Vector3.up },
                    new[] { 0, 1, 2 },
                    new[] { Vector3.forward, Vector3.forward, Vector3.forward },
                    new[] { Vector2.zero, Vector2.right, Vector2.up },
                    "full",
                    out var error);

                Assert.That(ok, Is.True, error);
                var mesh = target.GetComponent<MeshFilter>().sharedMesh;
                AssertValidTangents(mesh);
                var originalHandedness = Mathf.Sign(mesh.tangents[0].w);

                mesh.tangents = StaleTangents(mesh.vertexCount);
                var uvBuffer = WriteFloatBuffer("UV0", 2, new[]
                {
                    0f, 0f,
                    -1f, 0f,
                    0f, 1f,
                });
                ok = service.TryApplyPreviewUvBinary(pairId, target, uvBuffer, "uv", out error);

                Assert.That(ok, Is.True, error);
                AssertValidTangents(mesh);
                Assert.That(Mathf.Sign(mesh.tangents[0].w), Is.EqualTo(-originalHandedness));

                mesh.tangents = StaleTangents(mesh.vertexCount);
                var positionBuffer = WriteFloatBuffer("POSITION", 3, new[]
                {
                    0f, 0f, 0f,
                    1f, 0f, 0f,
                    0f, 0f, 1f,
                });
                ok = service.TryApplyPreviewPositionsBinary(pairId, target, positionBuffer, "position", out error);

                Assert.That(ok, Is.True, error);
                AssertValidTangents(mesh);
            }
            finally
            {
                service.TryDiscardPreview(target, out _);
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        private Mesh CreateTriangleMesh()
        {
            var mesh = new Mesh { name = "FingerprintTriangle" };
            mesh.vertices = new[]
            {
                new Vector3(0f, 0f, 0f),
                new Vector3(1f, 0f, 0f),
                new Vector3(0f, 1f, 0f),
            };
            mesh.normals = new[] { Vector3.forward, Vector3.forward, Vector3.forward };
            mesh.uv = new[] { Vector2.zero, Vector2.right, Vector2.up };
            mesh.colors32 = new[]
            {
                new Color32(255, 0, 0, 255),
                new Color32(0, 255, 0, 255),
                new Color32(0, 0, 255, 255),
            };
            mesh.subMeshCount = 1;
            mesh.SetTriangles(new[] { 0, 1, 2 }, 0);
            _meshes.Add(mesh);
            return mesh;
        }

        private string Fingerprint(Mesh mesh)
        {
            return (string)_runtimeFingerprint.Invoke(null, new object[] { mesh });
        }

        private SceneSyncBinaryBuffer WriteFloatBuffer(string semantic, int components, float[] values)
        {
            var path = Path.Combine(Path.GetTempPath(), $"trisync-{Guid.NewGuid():N}.bin");
            var bytes = new byte[values.Length * sizeof(float)];
            Buffer.BlockCopy(values, 0, bytes, 0, bytes.Length);
            File.WriteAllBytes(path, bytes);
            _temporaryFiles.Add(path);
            return new SceneSyncBinaryBuffer
            {
                semantic = semantic,
                format = "float32",
                components = components,
                count = values.Length / components,
                byteLength = bytes.Length,
                path = path,
            };
        }

        private static Vector4[] StaleTangents(int count)
        {
            var tangents = new Vector4[count];
            for (var i = 0; i < count; i++)
                tangents[i] = new Vector4(0f, 1f, 0f, 0f);
            return tangents;
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
    }
}
