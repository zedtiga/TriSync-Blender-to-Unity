using System.Reflection;
using NUnit.Framework;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class SceneSyncTransformMapperTests
    {
        private const string TypeName = "BlenderSyncVNext.SceneSyncCore.SceneSyncTransformMapper";
        private MethodInfo _mapPoint;
        private MethodInfo _mapDirection;
        private MethodInfo _mapTriangles;
        private MethodInfo _mapRotationForObjectType;
        private MethodInfo _convertComposedTransform;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _mapPoint = ProductApi.RequireStaticMethod(TypeName, "MapPoint", 1);
            _mapDirection = ProductApi.RequireStaticMethod(TypeName, "MapDirection", 1);
            _mapTriangles = ProductApi.RequireStaticMethod(TypeName, "MapTriangles", 1);
            _mapRotationForObjectType = ProductApi.RequireStaticMethod(TypeName, "MapRotationForObjectType", 2);
            _convertComposedTransform = ProductApi.RequireStaticMethod(TypeName, "ConvertComposedTransform", 9);
        }

        [Test]
        public void PointAndDirectionUseCurrentBlenderToUnityBasis()
        {
            var input = new Vector3(1f, 2f, 3f);

            AssertVector((Vector3)_mapPoint.Invoke(null, new object[] { input }), new Vector3(1f, 3f, 2f));
            AssertVector((Vector3)_mapDirection.Invoke(null, new object[] { input }), new Vector3(1f, 3f, 2f));
        }

        [Test]
        public void TriangleWindingSwapsSecondAndThirdIndices()
        {
            var source = new[] { 0, 1, 2, 3, 4, 5 };
            var mapped = (int[])_mapTriangles.Invoke(null, new object[] { source });

            Assert.That(mapped, Is.EqualTo(new[] { 0, 2, 1, 3, 5, 4 }));
            Assert.That(source, Is.EqualTo(new[] { 0, 1, 2, 3, 4, 5 }));
        }

        [TestCase("camera")]
        [TestCase("LIGHT")]
        public void CameraAndLightReceiveLocalAxisOffset(string objectType)
        {
            var identityRotation = new[] { 0f, 0f, 0f, 1f };
            var mapped = (Quaternion)_mapRotationForObjectType.Invoke(
                null,
                new object[] { identityRotation, objectType });

            Assert.That(
                Quaternion.Angle(mapped, Quaternion.AngleAxis(90f, Vector3.right)),
                Is.LessThan(0.001f));
        }

        [Test]
        public void OrdinaryObjectDoesNotReceiveLocalAxisOffset()
        {
            var identityRotation = new[] { 0f, 0f, 0f, 1f };
            var mapped = (Quaternion)_mapRotationForObjectType.Invoke(
                null,
                new object[] { identityRotation, "mesh" });

            Assert.That(Quaternion.Angle(mapped, Quaternion.identity), Is.LessThan(0.001f));
        }

        [Test]
        public void ComposedTransformMatchesManualIdentityRotationComposition()
        {
            var arguments = new object[]
            {
                new[] { 1f, 2f, 3f },
                new[] { 0f, 0f, 0f, 1f },
                new[] { 2f, 3f, 4f },
                new[] { 1f, 1f, 1f },
                new[] { 0f, 0f, 0f, 1f },
                new[] { 0.5f, 2f, 1.5f },
                null,
                null,
                null,
            };

            _convertComposedTransform.Invoke(null, arguments);

            AssertVector((Vector3)arguments[6], new Vector3(3f, 7f, 5f));
            Assert.That(Quaternion.Angle((Quaternion)arguments[7], Quaternion.identity), Is.LessThan(0.001f));
            AssertVector((Vector3)arguments[8], new Vector3(1f, 6f, 6f));
        }

        private static void AssertVector(Vector3 actual, Vector3 expected)
        {
            Assert.That(actual.x, Is.EqualTo(expected.x).Within(0.0001f));
            Assert.That(actual.y, Is.EqualTo(expected.y).Within(0.0001f));
            Assert.That(actual.z, Is.EqualTo(expected.z).Within(0.0001f));
        }
    }
}
