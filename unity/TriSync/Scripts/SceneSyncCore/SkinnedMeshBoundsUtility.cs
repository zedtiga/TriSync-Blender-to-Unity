using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    internal static class SkinnedMeshBoundsUtility
    {
        internal static void ApplySharedMeshBounds(SkinnedMeshRenderer renderer)
        {
            if (renderer == null || renderer.sharedMesh == null)
                return;

            var boundsSpace = renderer.rootBone != null ? renderer.rootBone : renderer.transform;
            var meshToBoundsSpace = boundsSpace.worldToLocalMatrix * renderer.transform.localToWorldMatrix;
            renderer.localBounds = TransformBounds(renderer.sharedMesh.bounds, meshToBoundsSpace);
        }

        private static Bounds TransformBounds(Bounds source, Matrix4x4 matrix)
        {
            var sourceExtents = source.extents;
            var axisX = matrix.MultiplyVector(new Vector3(sourceExtents.x, 0f, 0f));
            var axisY = matrix.MultiplyVector(new Vector3(0f, sourceExtents.y, 0f));
            var axisZ = matrix.MultiplyVector(new Vector3(0f, 0f, sourceExtents.z));
            var transformedExtents = new Vector3(
                Mathf.Abs(axisX.x) + Mathf.Abs(axisY.x) + Mathf.Abs(axisZ.x),
                Mathf.Abs(axisX.y) + Mathf.Abs(axisY.y) + Mathf.Abs(axisZ.y),
                Mathf.Abs(axisX.z) + Mathf.Abs(axisY.z) + Mathf.Abs(axisZ.z));

            return new Bounds(matrix.MultiplyPoint3x4(source.center), transformedExtents * 2f);
        }
    }
}
