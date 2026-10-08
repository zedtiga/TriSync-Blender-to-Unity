using NUnit.Framework;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class RiggedPrefabBoundsTests
    {
        [Test]
        public void RiggedRendererConvertsMeshBoundsIntoRootBoneSpace()
        {
            var applyBounds = ProductApi.RequireStaticMethod(
                "BlenderSyncVNext.SceneSyncCore.SkinnedMeshBoundsUtility",
                "ApplySharedMeshBounds",
                1);
            var hierarchyRoot = new GameObject("RiggedPrefabBoundsRoot");
            var rendererObject = new GameObject("Renderer");
            var rootBoneObject = new GameObject("RootBone");
            var mesh = new Mesh { name = "RootBoneSpaceBoundsMesh" };
            try
            {
                rendererObject.transform.SetParent(hierarchyRoot.transform, false);
                rootBoneObject.transform.SetParent(hierarchyRoot.transform, false);
                rootBoneObject.transform.localPosition = new Vector3(0f, 10f, 0f);

                var renderer = rendererObject.AddComponent<SkinnedMeshRenderer>();
                mesh.bounds = new Bounds(new Vector3(0f, 2f, 0f), new Vector3(4f, 6f, 8f));
                renderer.sharedMesh = mesh;
                renderer.rootBone = rootBoneObject.transform;
                renderer.bones = new[] { rootBoneObject.transform };

                applyBounds.Invoke(null, new object[] { renderer });

                AssertBoundsEqual(
                    new Bounds(new Vector3(0f, -8f, 0f), mesh.bounds.size),
                    renderer.localBounds);
                AssertBoundsEqual(mesh.bounds, renderer.bounds);
            }
            finally
            {
                Object.DestroyImmediate(hierarchyRoot);
                Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void RiggedRendererRefreshesTransformedBoundsWhenMeshChanges()
        {
            var applyBounds = ProductApi.RequireStaticMethod(
                "BlenderSyncVNext.SceneSyncCore.SkinnedMeshBoundsUtility",
                "ApplySharedMeshBounds",
                1);
            var hierarchyRoot = new GameObject("RiggedPrefabBoundsRoot");
            var rendererObject = new GameObject("Renderer");
            var rootBoneObject = new GameObject("RootBone");
            var firstMesh = new Mesh { name = "FirstBoundsMesh" };
            var updatedMesh = new Mesh { name = "UpdatedBoundsMesh" };
            try
            {
                hierarchyRoot.transform.SetPositionAndRotation(
                    new Vector3(4f, -2f, 1f),
                    Quaternion.Euler(0f, 35f, 0f));
                rendererObject.transform.SetParent(hierarchyRoot.transform, false);
                rendererObject.transform.SetLocalPositionAndRotation(
                    new Vector3(0.4f, -0.3f, 0.2f),
                    Quaternion.Euler(10f, 25f, -5f));
                rendererObject.transform.localScale = new Vector3(1.2f, 0.8f, 1.1f);
                rootBoneObject.transform.SetParent(hierarchyRoot.transform, false);
                rootBoneObject.transform.SetLocalPositionAndRotation(
                    new Vector3(-0.2f, 1.1f, 0.3f),
                    Quaternion.Euler(-15f, 20f, 8f));
                rootBoneObject.transform.localScale = new Vector3(0.9f, 1.3f, 0.7f);

                var renderer = rendererObject.AddComponent<SkinnedMeshRenderer>();
                renderer.rootBone = rootBoneObject.transform;
                renderer.bones = new[] { rootBoneObject.transform };
                firstMesh.bounds = new Bounds(
                    new Vector3(1.5f, -2f, 3.25f),
                    new Vector3(4f, 5.5f, 6f));
                updatedMesh.bounds = new Bounds(
                    new Vector3(-4f, 5f, -6f),
                    new Vector3(7.5f, 8f, 9.25f));

                var meshToRootBone = rootBoneObject.transform.worldToLocalMatrix
                    * rendererObject.transform.localToWorldMatrix;

                renderer.sharedMesh = firstMesh;
                renderer.localBounds = new Bounds(Vector3.zero, Vector3.one);
                applyBounds.Invoke(null, new object[] { renderer });
                AssertBoundsEqual(TransformBoundsByCorners(firstMesh.bounds, meshToRootBone), renderer.localBounds);

                renderer.sharedMesh = updatedMesh;
                renderer.localBounds = new Bounds(Vector3.zero, Vector3.one);
                applyBounds.Invoke(null, new object[] { renderer });
                AssertBoundsEqual(TransformBoundsByCorners(updatedMesh.bounds, meshToRootBone), renderer.localBounds);
            }
            finally
            {
                Object.DestroyImmediate(hierarchyRoot);
                Object.DestroyImmediate(firstMesh);
                Object.DestroyImmediate(updatedMesh);
            }
        }

        private static Bounds TransformBoundsByCorners(Bounds source, Matrix4x4 matrix)
        {
            var min = source.min;
            var max = source.max;
            var result = new Bounds(matrix.MultiplyPoint3x4(min), Vector3.zero);
            for (var corner = 1; corner < 8; corner++)
            {
                result.Encapsulate(matrix.MultiplyPoint3x4(new Vector3(
                    (corner & 1) == 0 ? min.x : max.x,
                    (corner & 2) == 0 ? min.y : max.y,
                    (corner & 4) == 0 ? min.z : max.z)));
            }
            return result;
        }

        private static void AssertBoundsEqual(Bounds expected, Bounds actual)
        {
            Assert.That(Vector3.Distance(actual.center, expected.center), Is.LessThan(0.0001f), "center");
            Assert.That(Vector3.Distance(actual.size, expected.size), Is.LessThan(0.0001f), "size");
        }
    }
}
