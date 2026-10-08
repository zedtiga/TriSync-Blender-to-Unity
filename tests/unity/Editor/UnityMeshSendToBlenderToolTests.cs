using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using BlenderSyncVNext.SessionCore;
using BlenderSyncVNext.UI;
using NUnit.Framework;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class UnityMeshSendToBlenderToolTests
    {
        private const string TypeName = "BlenderSyncVNext.UI.UnityMeshSendToBlenderTool";
        private MethodInfo _buildUvChannels;
        private MethodInfo _buildBlendShapes;
        private MethodInfo _mapTriangles;
        private MethodInfo _buildSnapshot;
        private MethodInfo _buildMeshSnapshots;
        private MethodInfo _writeBinaryMeshes;
        private MethodInfo _isDeadlineExpired;
        private MethodInfo _setLocalStatus;
        private MethodInfo _beginImportResultWait;
        private MethodInfo _tryExpirePendingResult;
        private MethodInfo _completeSendWithoutResult;
        private FieldInfo _importResultDeadlineSeconds;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _buildUvChannels = ProductApi.RequireStaticMethod(TypeName, "BuildUvChannels", 2);
            _buildBlendShapes = ProductApi.RequireStaticMethod(TypeName, "BuildBlendShapes", 3);
            _mapTriangles = ProductApi.RequireStaticMethod(TypeName, "MapTrianglesToBlender", 1);
            _buildSnapshot = ProductApi.RequireStaticMethod(TypeName, "BuildSnapshot", 5);
            _buildMeshSnapshots = ProductApi.RequireStaticMethod(TypeName, "BuildMeshSnapshots", 2);
            _writeBinaryMeshes = ProductApi.RequireStaticMethod(TypeName, "WriteBinaryMeshes", 2);
            _isDeadlineExpired = ProductApi.RequireStaticMethod(TypeName, "IsDeadlineExpired", 2);
            _setLocalStatus = ProductApi.RequireStaticMethod(TypeName, "SetLocalStatus", 3);
            _beginImportResultWait = ProductApi.RequireStaticMethod(TypeName, "BeginImportResultWait", 1);
            _tryExpirePendingResult = ProductApi.RequireStaticMethod(TypeName, "TryExpirePendingResult", 1);
            _completeSendWithoutResult = ProductApi.RequireStaticMethod(TypeName, "CompleteSendWithoutResult", 2);
            _importResultDeadlineSeconds = ProductApi.RequireType(TypeName).GetField(
                "_importResultDeadlineSeconds",
                BindingFlags.NonPublic | BindingFlags.Static);
            Assert.That(_importResultDeadlineSeconds, Is.Not.Null);
        }

        [Test]
        public void QueuedMeshDefersReadabilityValidationUntilSnapshotBuild()
        {
            var mesh = TriangleMesh("UnreadableQueueItem");
            try
            {
                Assert.That(
                    UnityMeshSendToBlenderTool.TryCreateMeshSource(mesh, out var source, out var sourceWarning),
                    Is.True);
                mesh.UploadMeshData(true);
                var warnings = new List<string>();
                var queue = new List<UnityMeshSendToBlenderTool.MeshSource> { source };

                var snapshots = (IList)_buildMeshSnapshots.Invoke(null, new object[] { queue, warnings });

                Assert.That(mesh.isReadable, Is.False);
                Assert.That(sourceWarning, Is.Null);
                Assert.That(snapshots, Is.Empty);
                Assert.That(warnings, Has.Count.EqualTo(1));
                StringAssert.Contains("mesh_not_readable", warnings[0]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void QueuedMeshSourcesDeduplicateSharedMesh()
        {
            var mesh = TriangleMesh("SharedQueueMesh");
            var first = new GameObject("First", typeof(MeshFilter));
            var second = new GameObject("Second", typeof(MeshFilter));
            try
            {
                first.GetComponent<MeshFilter>().sharedMesh = mesh;
                second.GetComponent<MeshFilter>().sharedMesh = mesh;
                Assert.That(
                    UnityMeshSendToBlenderTool.TryCreateMeshSource(first, out var firstSource, out var firstWarning),
                    Is.True);
                Assert.That(
                    UnityMeshSendToBlenderTool.TryCreateMeshSource(second, out var secondSource, out var secondWarning),
                    Is.True);

                var warnings = new List<string>();
                var queue = new List<UnityMeshSendToBlenderTool.MeshSource> { firstSource, secondSource };
                var snapshots = (IList)_buildMeshSnapshots.Invoke(null, new object[] { queue, warnings });

                Assert.That(snapshots, Has.Count.EqualTo(1));
                Assert.That(firstWarning, Is.Null);
                Assert.That(secondWarning, Is.Null);
                Assert.That(warnings, Is.Empty);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(first);
                UnityEngine.Object.DestroyImmediate(second);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void ImportResultDeadlineUsesMonotonicLocalClock()
        {
            Assert.That((bool)_isDeadlineExpired.Invoke(null, new object[] { 9.99d, 10.0d }), Is.False);
            Assert.That((bool)_isDeadlineExpired.Invoke(null, new object[] { 10.0d, 10.0d }), Is.True);
            Assert.That((bool)_isDeadlineExpired.Invoke(null, new object[] { 10.0d, 0.0d }), Is.False);
        }

        [Test]
        public void QueuedResultRefreshesTheActiveImportDeadline()
        {
            _setLocalStatus.Invoke(null, new object[] { "creating", "Waiting", Array.Empty<string>() });
            _beginImportResultWait.Invoke(null, new object[] { true });
            _importResultDeadlineSeconds.SetValue(null, 1.0d);

            UnityMeshSendToBlenderTool.HandleImportResult(
                "{\"type\":\"unity_mesh.import_result_v1\",\"status\":\"queued\",\"message\":\"Queued\",\"created\":0,\"skipped\":0,\"warnings\":[],\"timestamp\":1}");

            Assert.That(UnityMeshSendToBlenderTool.IsCreatePending, Is.True);
            Assert.That(UnityMeshSendToBlenderTool.GetLatestResult().status, Is.EqualTo("queued"));
            Assert.That((double)_importResultDeadlineSeconds.GetValue(null), Is.GreaterThan(1.0d));

            UnityMeshSendToBlenderTool.HandleImportResult(
                "{\"type\":\"unity_mesh.import_result_v1\",\"status\":\"imported\",\"message\":\"Created\",\"created\":1,\"skipped\":0,\"warnings\":[],\"timestamp\":2}");
        }

        [Test]
        public void TimeoutBecomesWarningWhileLateTerminalResultStillWins()
        {
            _setLocalStatus.Invoke(null, new object[] { "creating", "Waiting", Array.Empty<string>() });
            _beginImportResultWait.Invoke(null, new object[] { true });

            Assert.That(
                (bool)_tryExpirePendingResult.Invoke(null, new object[] { double.MaxValue }),
                Is.True);
            var timedOut = UnityMeshSendToBlenderTool.GetLatestResult();
            Assert.That(UnityMeshSendToBlenderTool.IsCreatePending, Is.False);
            Assert.That(timedOut.status, Is.EqualTo("warning"));
            Assert.That(timedOut.error, Is.EqualTo("result_timeout"));

            UnityMeshSendToBlenderTool.HandleImportResult(
                "{\"type\":\"unity_mesh.import_result_v1\",\"status\":\"queued\",\"message\":\"Late queue\",\"created\":0,\"skipped\":0,\"warnings\":[],\"timestamp\":3}");
            Assert.That(UnityMeshSendToBlenderTool.IsCreatePending, Is.False);
            Assert.That(UnityMeshSendToBlenderTool.GetLatestResult().status, Is.EqualTo("warning"));

            UnityMeshSendToBlenderTool.HandleImportResult(
                "{\"type\":\"unity_mesh.import_result_v1\",\"status\":\"partial\",\"message\":\"Created late\",\"created\":1,\"skipped\":0,\"warnings\":[\"late warning\"],\"timestamp\":4}");
            var terminal = UnityMeshSendToBlenderTool.GetLatestResult();
            Assert.That(UnityMeshSendToBlenderTool.IsCreatePending, Is.False);
            Assert.That(terminal.status, Is.EqualTo("partial"));
            Assert.That(terminal.created, Is.EqualTo(1));
            Assert.That(terminal.warnings, Is.EqualTo(new[] { "late warning" }));
        }

        [Test]
        public void FeatureMissingSendCompletesWithoutPendingResult()
        {
            _setLocalStatus.Invoke(null, new object[] { "creating", "Waiting", Array.Empty<string>() });
            _completeSendWithoutResult.Invoke(null, new object[] { false, new[] { "degraded" } });

            var result = UnityMeshSendToBlenderTool.GetLatestResult();
            Assert.That(UnityMeshSendToBlenderTool.IsCreatePending, Is.False);
            Assert.That(result.status, Is.EqualTo("sent"));
            StringAssert.Contains("does not report creation results", result.message);
            Assert.That(result.warnings, Is.EqualTo(new[] { "degraded" }));
        }

        [Test]
        public void SkinnedMeshRendererSourceExportsSharedMeshAndBlendShape()
        {
            var mesh = TriangleMesh("SkinnedQueueMesh");
            var owner = new GameObject("Face", typeof(SkinnedMeshRenderer));
            try
            {
                mesh.AddBlendShapeFrame(
                    "Smile",
                    100f,
                    new[] { Vector3.right, Vector3.zero, Vector3.zero },
                    new Vector3[3],
                    new Vector3[3]);
                owner.GetComponent<SkinnedMeshRenderer>().sharedMesh = mesh;
                Assert.That(
                    UnityMeshSendToBlenderTool.TryCreateMeshSource(owner, out var source, out var sourceWarning),
                    Is.True);

                var warnings = new List<string>();
                var queue = new List<UnityMeshSendToBlenderTool.MeshSource> { source };
                var snapshots = (IList)_buildMeshSnapshots.Invoke(null, new object[] { queue, warnings });

                Assert.That(snapshots, Has.Count.EqualTo(1));
                Assert.That(Field<string>(snapshots[0], "sourceKind"), Is.EqualTo("skinned_mesh_renderer"));
                Assert.That(Field<Array>(snapshots[0], "blendShapes"), Has.Length.EqualTo(1));
                Assert.That(sourceWarning, Is.Null);
                Assert.That(warnings, Has.Count.EqualTo(1));
                StringAssert.Contains("SKINNED_MESH_SHARED_ONLY", warnings[0]);
                StringAssert.Contains("skin_weights", warnings[0]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(owner);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void MeshFieldAcceptsMeshAssetsAndSkinnedGameObjects()
        {
            var mesh = TriangleMesh("FieldMesh");
            var owner = new GameObject("FieldFace", typeof(SkinnedMeshRenderer));
            try
            {
                owner.GetComponent<SkinnedMeshRenderer>().sharedMesh = mesh;

                Assert.That(
                    UnityMeshSendToBlenderTool.TryCreateMeshSource(mesh, out var assetSource, out var assetWarning),
                    Is.True);
                Assert.That(assetWarning, Is.Null);
                Assert.That(assetSource.Mesh, Is.SameAs(mesh));
                Assert.That(assetSource.SourceKind, Is.EqualTo("mesh_asset"));

                Assert.That(
                    UnityMeshSendToBlenderTool.TryCreateMeshSource(owner, out var objectSource, out var objectWarning),
                    Is.True);
                Assert.That(objectWarning, Is.Null);
                Assert.That(objectSource.Mesh, Is.SameAs(mesh));
                Assert.That(objectSource.Owner, Is.SameAs(owner));
                Assert.That(objectSource.SourceKind, Is.EqualTo("skinned_mesh_renderer"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(owner);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void UvExportIncludesChannelSeven()
        {
            var mesh = TriangleMesh("UvChannels");
            try
            {
                mesh.SetUVs(0, TriangleUvs(0f));
                mesh.SetUVs(7, TriangleUvs(0.5f));

                var channels = (Array)_buildUvChannels.Invoke(null, new object[] { mesh, 3 });

                Assert.That(channels.Length, Is.EqualTo(2));
                Assert.That(Field<int>(channels.GetValue(0), "index"), Is.EqualTo(0));
                Assert.That(Field<int>(channels.GetValue(1), "index"), Is.EqualTo(7));
                Assert.That(Field<float[]>(channels.GetValue(1), "values"), Has.Length.EqualTo(6));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void SingleFrameBlendShapeMapsPositionDeltasToBlenderAxes()
        {
            var mesh = TriangleMesh("SingleFrameShape");
            try
            {
                mesh.AddBlendShapeFrame(
                    "Smile",
                    100f,
                    new[] { new Vector3(1f, 2f, 3f), Vector3.zero, Vector3.zero },
                    new Vector3[3],
                    new Vector3[3]);
                var warnings = new List<string>();

                var shapes = (Array)_buildBlendShapes.Invoke(null, new object[] { mesh, 3, warnings });

                Assert.That(shapes.Length, Is.EqualTo(1));
                Assert.That(Field<string>(shapes.GetValue(0), "name"), Is.EqualTo("Smile"));
                Assert.That(Field<float>(shapes.GetValue(0), "frameWeight"), Is.EqualTo(100f));
                Assert.That(
                    Field<float[]>(shapes.GetValue(0), "deltaPositions"),
                    Is.EqualTo(new[] { 1f, 3f, 2f, 0f, 0f, 0f, 0f, 0f, 0f }));
                Assert.That(warnings, Is.Empty);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void MultiFrameBlendShapeIsOmittedWithExplicitWarning()
        {
            var mesh = TriangleMesh("MultiFrameShape");
            try
            {
                var deltas = new[] { Vector3.right, Vector3.zero, Vector3.zero };
                mesh.AddBlendShapeFrame("Pulse", 50f, deltas, new Vector3[3], new Vector3[3]);
                mesh.AddBlendShapeFrame("Pulse", 100f, deltas, new Vector3[3], new Vector3[3]);
                var warnings = new List<string>();

                var shapes = (Array)_buildBlendShapes.Invoke(null, new object[] { mesh, 3, warnings });

                Assert.That(shapes.Length, Is.Zero);
                Assert.That(warnings, Has.Count.EqualTo(1));
                StringAssert.Contains("multiple_frames_not_supported", warnings[0]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void TriangleWindingIsReversedForBlenderCoordinates()
        {
            Assert.That(
                _mapTriangles.Invoke(null, new object[] { new[] { 0, 1, 2, 3, 4, 5 } }),
                Is.EqualTo(new[] { 0, 2, 1, 3, 5, 4 }));
        }

        [Test]
        public void StagedBinaryPreservesUvSevenAndSingleFrameBlendShape()
        {
            var mesh = TriangleMesh("BinaryParity");
            var path = Path.Combine(Path.GetTempPath(), "blendersync-binary-" + Guid.NewGuid().ToString("N") + ".bin");
            try
            {
                mesh.SetUVs(7, TriangleUvs(0.25f));
                mesh.AddBlendShapeFrame(
                    "Smile",
                    100f,
                    new[] { Vector3.right, Vector3.zero, Vector3.zero },
                    new Vector3[3],
                    new Vector3[3]);
                var warnings = new List<string>();
                var snapshot = _buildSnapshot.Invoke(
                    null,
                    new object[] { mesh, "BinaryParity", "mesh_asset", null, warnings });
                var listType = typeof(List<>).MakeGenericType(snapshot.GetType());
                var snapshots = (IList)Activator.CreateInstance(listType);
                snapshots.Add(snapshot);

                Array binaryMeshes;
                using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read))
                    binaryMeshes = (Array)_writeBinaryMeshes.Invoke(null, new object[] { stream, snapshots });

                var binary = binaryMeshes.GetValue(0);
                var uvChannels = Field<Array>(binary, "uvChannels");
                var blendShapes = Field<Array>(binary, "blendShapes");
                Assert.That(uvChannels.Length, Is.EqualTo(1));
                Assert.That(Field<int>(uvChannels.GetValue(0), "index"), Is.EqualTo(7));
                Assert.That(blendShapes.Length, Is.EqualTo(1));
                Assert.That(Field<string>(blendShapes.GetValue(0), "name"), Is.EqualTo("Smile"));
                var deltaBuffer = Field<object>(blendShapes.GetValue(0), "deltaPositions");
                Assert.That(Field<int>(deltaBuffer, "components"), Is.EqualTo(3));
                Assert.That(Field<int>(deltaBuffer, "count"), Is.EqualTo(3));
                Assert.That(new FileInfo(path).Length, Is.GreaterThan(0));
                Assert.That(warnings, Is.Empty);
            }
            finally
            {
                if (File.Exists(path))
                    File.Delete(path);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void StagedBinaryUsesTypedEmptyDescriptorsForMissingOptionalAttributes()
        {
            var mesh = TriangleMesh("MissingOptionalAttributes");
            var path = Path.Combine(Path.GetTempPath(), "blendersync-binary-" + Guid.NewGuid().ToString("N") + ".bin");
            try
            {
                var warnings = new List<string>();
                var snapshot = _buildSnapshot.Invoke(
                    null,
                    new object[] { mesh, "MissingOptionalAttributes", "mesh_asset", null, warnings });
                var listType = typeof(List<>).MakeGenericType(snapshot.GetType());
                var snapshots = (IList)Activator.CreateInstance(listType);
                snapshots.Add(snapshot);

                Array binaryMeshes;
                using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read))
                    binaryMeshes = (Array)_writeBinaryMeshes.Invoke(null, new object[] { stream, snapshots });

                var binary = binaryMeshes.GetValue(0);
                var normals = Field<object>(binary, "normals");
                var colors = Field<object>(binary, "colors");
                Assert.That(Field<string>(normals, "valueType"), Is.EqualTo("float32"));
                Assert.That(Field<int>(normals, "components"), Is.EqualTo(3));
                Assert.That(Field<int>(normals, "count"), Is.EqualTo(0));
                Assert.That(Field<long>(normals, "byteCount"), Is.EqualTo(0));
                Assert.That(Field<string>(colors, "valueType"), Is.EqualTo("float32"));
                Assert.That(Field<int>(colors, "components"), Is.EqualTo(4));
                Assert.That(Field<int>(colors, "count"), Is.EqualTo(0));
                Assert.That(Field<long>(colors, "byteCount"), Is.EqualTo(0));

                var json = JsonUtility.ToJson(binary);
                StringAssert.Contains("\"normals\":{\"semantic\":\"NORMAL\",\"valueType\":\"float32\"", json);
                StringAssert.Contains("\"colors\":{\"semantic\":\"COLOR\",\"valueType\":\"float32\"", json);
                Assert.That(warnings, Is.Empty);
            }
            finally
            {
                if (File.Exists(path))
                    File.Delete(path);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void ImportResultRouterUpdatesQueuedAndTerminalState()
        {
            _setLocalStatus.Invoke(null, new object[] { "creating", "Waiting", Array.Empty<string>() });
            _beginImportResultWait.Invoke(null, new object[] { true });
            var client = new SessionClient();
            client.HandleIncoming(
                "{\"type\":\"unity_mesh.import_result_v1\",\"status\":\"queued\",\"message\":\"Queued\",\"created\":0,\"skipped\":0,\"warnings\":[],\"timestamp\":1}");
            Assert.That(UnityMeshSendToBlenderTool.IsCreatePending, Is.True);
            Assert.That(UnityMeshSendToBlenderTool.GetLatestResult().status, Is.EqualTo("queued"));

            client.HandleIncoming(
                "{\"type\":\"unity_mesh.import_result_v1\",\"status\":\"partial\",\"message\":\"Created\",\"created\":1,\"skipped\":0,\"warnings\":[\"shape omitted\"],\"timestamp\":2}");
            var result = UnityMeshSendToBlenderTool.GetLatestResult();
            Assert.That(UnityMeshSendToBlenderTool.IsCreatePending, Is.False);
            Assert.That(result.status, Is.EqualTo("partial"));
            Assert.That(result.created, Is.EqualTo(1));
            Assert.That(result.warnings, Is.EqualTo(new[] { "shape omitted" }));
        }

        private static Mesh TriangleMesh(string name)
        {
            var mesh = new Mesh { name = name };
            mesh.vertices = new[] { Vector3.zero, Vector3.right, Vector3.up };
            mesh.triangles = new[] { 0, 1, 2 };
            return mesh;
        }

        private static List<Vector2> TriangleUvs(float offset)
        {
            return new List<Vector2>
            {
                new Vector2(offset, offset),
                new Vector2(1f, 0f),
                new Vector2(0f, 1f),
            };
        }

        private static T Field<T>(object target, string name)
        {
            var field = target.GetType().GetField(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            Assert.That(field, Is.Not.Null, "Missing field " + name);
            return (T)field.GetValue(target);
        }
    }
}
