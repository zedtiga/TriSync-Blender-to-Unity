using System.Reflection;
using BlenderSyncVNext.SceneSyncCore;
using NUnit.Framework;

namespace BlenderSyncVNext.Tests
{
    public sealed class SceneViewStateServiceTests
    {
        private const string TypeName = "BlenderSyncVNext.SceneSyncCore.SceneViewStateService";
        private MethodInfo _calculateSceneViewSize;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _calculateSceneViewSize = ProductApi.RequireStaticMethod(TypeName, "CalculateSceneViewSize", 1);
        }

        [Test]
        public void LegacyOrInvalidViewScaleKeepsCurrentFraming()
        {
            var baseline = OrthographicSize(1.0f);

            Assert.That(OrthographicSize(0.0f), Is.EqualTo(baseline).Within(0.0001f));
            Assert.That(OrthographicSize(-2.0f), Is.EqualTo(baseline).Within(0.0001f));
            Assert.That(OrthographicSize(float.NaN), Is.EqualTo(baseline).Within(0.0001f));
        }

        [Test]
        public void ViewScaleAppliesUniformlyToPerspectiveAndOrthographicFraming()
        {
            var perspective = PerspectiveSize(1.0f);
            var orthographic = OrthographicSize(1.0f);

            Assert.That(PerspectiveSize(0.5f), Is.EqualTo(perspective * 0.5f).Within(0.0001f));
            Assert.That(PerspectiveSize(2.0f), Is.EqualTo(perspective * 2.0f).Within(0.0001f));
            Assert.That(OrthographicSize(0.5f), Is.EqualTo(orthographic * 0.5f).Within(0.0001f));
            Assert.That(OrthographicSize(2.0f), Is.EqualTo(orthographic * 2.0f).Within(0.0001f));
        }

        [Test]
        public void ViewScaleUsesThePublishedPreferenceRange()
        {
            Assert.That(OrthographicSize(0.01f), Is.EqualTo(0.4f).Within(0.0001f));
            Assert.That(OrthographicSize(10.0f), Is.EqualTo(20.0f).Within(0.0001f));
        }

        private float PerspectiveSize(float viewScale)
        {
            return Calculate(new SceneSyncViewStateMessage
            {
                distance = 10.0f,
                lens = 50.0f,
                viewScale = viewScale,
            });
        }

        private float OrthographicSize(float viewScale)
        {
            return Calculate(new SceneSyncViewStateMessage
            {
                isOrthographic = true,
                orthographicScale = 4.0f,
                viewScale = viewScale,
            });
        }

        private float Calculate(SceneSyncViewStateMessage message)
        {
            return (float)_calculateSceneViewSize.Invoke(null, new object[] { message });
        }
    }
}
