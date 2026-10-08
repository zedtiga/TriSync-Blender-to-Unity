using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public static class SceneSyncTransformMapper
    {
        private static readonly Matrix4x4 BlenderToUnityBasis = new Matrix4x4(
            new Vector4(1f, 0f, 0f, 0f),
            new Vector4(0f, 0f, 1f, 0f),
            new Vector4(0f, 1f, 0f, 0f),
            new Vector4(0f, 0f, 0f, 1f)
        );

        public static Vector3 MapPosition(float[] position)
        {
            ConvertTransform(position, null, null, out var mappedPosition, out _, out _);
            return mappedPosition;
        }

        public static Quaternion MapRotation(float[] rotation)
        {
            ConvertTransform(null, rotation, null, out _, out var mappedRotation, out _);
            return mappedRotation;
        }

        public static Quaternion MapRotationForObjectType(float[] rotation, string objectType)
        {
            var mappedRotation = MapRotation(rotation);
            var localOffset = GetObjectTypeLocalAxisOffset(objectType);
            return mappedRotation * localOffset;
        }

        public static Quaternion GetObjectTypeLocalAxisOffset(string objectType)
        {
            if (string.IsNullOrWhiteSpace(objectType))
                return Quaternion.identity;

            var value = objectType.Trim().ToLowerInvariant();
            if (value == "camera" || value == "light")
                return Quaternion.AngleAxis(90f, Vector3.right);

            return Quaternion.identity;
        }

        public static Vector3 MapScale(float[] scale)
        {
            ConvertTransform(null, null, scale, out _, out _, out var mappedScale);
            return mappedScale;
        }

        public static void ConvertTransform(float[] position, float[] rotation, float[] scale, out Vector3 mappedPosition, out Quaternion mappedRotation, out Vector3 mappedScale)
        {
            var blenderMatrix = BuildBlenderTransformMatrix(position, rotation, scale);
            var unityMatrix = ConvertMatrix(blenderMatrix);

            mappedPosition = ExtractPosition(unityMatrix);
            mappedScale = ExtractScale(unityMatrix);
            mappedRotation = ExtractRotation(unityMatrix, mappedScale);
        }

        public static Vector3 MapPoint(Vector3 point)
        {
            return BlenderToUnityBasis.MultiplyPoint3x4(point);
        }

        public static Vector3 MapDirection(Vector3 direction)
        {
            return BlenderToUnityBasis.MultiplyVector(direction);
        }

        public static int[] MapTriangles(int[] triangles)
        {
            if (triangles == null)
                return null;

            var mapped = (int[])triangles.Clone();
            for (var i = 0; i + 2 < mapped.Length; i += 3)
            {
                var tmp = mapped[i + 1];
                mapped[i + 1] = mapped[i + 2];
                mapped[i + 2] = tmp;
            }
            return mapped;
        }

        public static Matrix4x4 MapRowMajorMatrix(float[] rowMajor16, int startIndex)
        {
            var blenderMatrix = ReadRowMajorMatrix(rowMajor16, startIndex);
            return ConvertMatrix(blenderMatrix);
        }

        public static Matrix4x4 MapBoneRowMajorMatrix(float[] rowMajor16, int startIndex)
        {
            return MapRowMajorMatrix(rowMajor16, startIndex);
        }

        public static void ConvertBoneTransform(float[] position, float[] rotation, float[] scale, out Vector3 mappedPosition, out Quaternion mappedRotation, out Vector3 mappedScale)
        {
            var blenderMatrix = BuildBlenderTransformMatrix(position, rotation, scale);
            var unityMatrix = ConvertMatrix(blenderMatrix);

            mappedPosition = ExtractPosition(unityMatrix);
            mappedScale = ExtractScale(unityMatrix);
            mappedRotation = ExtractRotation(unityMatrix, mappedScale);
        }

        public static void ConvertComposedTransform(
            float[] parentPosition,
            float[] parentRotation,
            float[] parentScale,
            float[] childPosition,
            float[] childRotation,
            float[] childScale,
            out Vector3 mappedPosition,
            out Quaternion mappedRotation,
            out Vector3 mappedScale)
        {
            var parentMatrix = BuildBlenderTransformMatrix(parentPosition, parentRotation, parentScale);
            var childMatrix = BuildBlenderTransformMatrix(childPosition, childRotation, childScale);
            var unityMatrix = ConvertMatrix(parentMatrix * childMatrix);

            mappedPosition = ExtractPosition(unityMatrix);
            mappedScale = ExtractScale(unityMatrix);
            mappedRotation = ExtractRotation(unityMatrix, mappedScale);
        }

        public static Matrix4x4 ConvertComposedRowMajorMatrix(float[] parentPosition, float[] parentRotation, float[] parentScale, float[] childRowMajor16, int startIndex)
        {
            var parentMatrix = BuildBlenderTransformMatrix(parentPosition, parentRotation, parentScale);
            var childMatrix = new Matrix4x4();
            childMatrix.m00 = childRowMajor16[startIndex + 0];
            childMatrix.m01 = childRowMajor16[startIndex + 1];
            childMatrix.m02 = childRowMajor16[startIndex + 2];
            childMatrix.m03 = childRowMajor16[startIndex + 3];
            childMatrix.m10 = childRowMajor16[startIndex + 4];
            childMatrix.m11 = childRowMajor16[startIndex + 5];
            childMatrix.m12 = childRowMajor16[startIndex + 6];
            childMatrix.m13 = childRowMajor16[startIndex + 7];
            childMatrix.m20 = childRowMajor16[startIndex + 8];
            childMatrix.m21 = childRowMajor16[startIndex + 9];
            childMatrix.m22 = childRowMajor16[startIndex + 10];
            childMatrix.m23 = childRowMajor16[startIndex + 11];
            childMatrix.m30 = childRowMajor16[startIndex + 12];
            childMatrix.m31 = childRowMajor16[startIndex + 13];
            childMatrix.m32 = childRowMajor16[startIndex + 14];
            childMatrix.m33 = childRowMajor16[startIndex + 15];
            return ConvertMatrix(parentMatrix * childMatrix);
        }

        private static Matrix4x4 BuildBlenderTransformMatrix(float[] position, float[] rotation, float[] scale)
        {
            var p = ToVector3(position, Vector3.zero);
            var s = ToVector3(scale, Vector3.one);
            var r = BuildBlenderRotationMatrix(rotation);

            r.m00 *= s.x; r.m10 *= s.x; r.m20 *= s.x;
            r.m01 *= s.y; r.m11 *= s.y; r.m21 *= s.y;
            r.m02 *= s.z; r.m12 *= s.z; r.m22 *= s.z;
            r.m03 = p.x;
            r.m13 = p.y;
            r.m23 = p.z;
            r.m33 = 1f;
            return r;
        }

        private static Matrix4x4 BuildBlenderRotationMatrix(float[] rotation)
        {
            if (rotation == null || rotation.Length < 4)
                return Matrix4x4.identity;

            var x = rotation[0];
            var y = rotation[1];
            var z = rotation[2];
            var w = rotation[3];

            var xx = x * x;
            var yy = y * y;
            var zz = z * z;
            var xy = x * y;
            var xz = x * z;
            var yz = y * z;
            var wx = w * x;
            var wy = w * y;
            var wz = w * z;

            var m = Matrix4x4.identity;
            m.m00 = 1f - 2f * (yy + zz);
            m.m01 = 2f * (xy - wz);
            m.m02 = 2f * (xz + wy);

            m.m10 = 2f * (xy + wz);
            m.m11 = 1f - 2f * (xx + zz);
            m.m12 = 2f * (yz - wx);

            m.m20 = 2f * (xz - wy);
            m.m21 = 2f * (yz + wx);
            m.m22 = 1f - 2f * (xx + yy);
            return m;
        }

        private static Matrix4x4 ReadRowMajorMatrix(float[] rowMajor16, int startIndex)
        {
            var blenderMatrix = new Matrix4x4();
            blenderMatrix.m00 = rowMajor16[startIndex + 0];
            blenderMatrix.m01 = rowMajor16[startIndex + 1];
            blenderMatrix.m02 = rowMajor16[startIndex + 2];
            blenderMatrix.m03 = rowMajor16[startIndex + 3];
            blenderMatrix.m10 = rowMajor16[startIndex + 4];
            blenderMatrix.m11 = rowMajor16[startIndex + 5];
            blenderMatrix.m12 = rowMajor16[startIndex + 6];
            blenderMatrix.m13 = rowMajor16[startIndex + 7];
            blenderMatrix.m20 = rowMajor16[startIndex + 8];
            blenderMatrix.m21 = rowMajor16[startIndex + 9];
            blenderMatrix.m22 = rowMajor16[startIndex + 10];
            blenderMatrix.m23 = rowMajor16[startIndex + 11];
            blenderMatrix.m30 = rowMajor16[startIndex + 12];
            blenderMatrix.m31 = rowMajor16[startIndex + 13];
            blenderMatrix.m32 = rowMajor16[startIndex + 14];
            blenderMatrix.m33 = rowMajor16[startIndex + 15];
            return blenderMatrix;
        }

        private static Matrix4x4 ConvertMatrix(Matrix4x4 blenderMatrix)
        {
            return BlenderToUnityBasis * blenderMatrix * BlenderToUnityBasis;
        }

        private static Vector3 ToVector3(float[] values, Vector3 fallback)
        {
            if (values == null || values.Length < 3)
                return fallback;
            return new Vector3(values[0], values[1], values[2]);
        }

        private static Vector3 ExtractPosition(Matrix4x4 matrix)
        {
            return matrix.GetColumn(3);
        }

        private static Vector3 ExtractScale(Matrix4x4 matrix)
        {
            return new Vector3(
                matrix.GetColumn(0).magnitude,
                matrix.GetColumn(1).magnitude,
                matrix.GetColumn(2).magnitude
            );
        }

        private static Quaternion ExtractRotation(Matrix4x4 matrix, Vector3 scale)
        {
            var right = matrix.GetColumn(0);
            var up = matrix.GetColumn(1);
            var forward = matrix.GetColumn(2);

            if (scale.x > 1e-6f) right /= scale.x;
            if (scale.y > 1e-6f) up /= scale.y;
            if (scale.z > 1e-6f) forward /= scale.z;

            if (forward.sqrMagnitude < 1e-8f || up.sqrMagnitude < 1e-8f)
                return Quaternion.identity;

            return Quaternion.LookRotation(forward.normalized, up.normalized);
        }
    }
}
