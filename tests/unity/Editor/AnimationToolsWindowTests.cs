using System.Reflection;
using NUnit.Framework;

namespace BlenderSyncVNext.Tests
{
    public sealed class AnimationToolsWindowTests
    {
        private const string WindowTypeName = "BlenderSyncVNext.RootMotion.AnimationToolsWindow";
        private MethodInfo _normalizeTab;
        private MethodInfo _tabLabel;
        private MethodInfo _canonicalMenuPath;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _normalizeTab = ProductApi.RequireStaticMethod(WindowTypeName, "NormalizeTab", 1);
            _tabLabel = ProductApi.RequireStaticMethod(WindowTypeName, "TabLabel", 1);
            _canonicalMenuPath = ProductApi.RequireStaticMethod(WindowTypeName, "CanonicalMenuPath", 0);
        }

        [Test]
        public void TabsUseStableValuesAndNormalizeInvalidStoredValues()
        {
            Assert.That(_normalizeTab.Invoke(null, new object[] { 0 }), Is.EqualTo(0));
            Assert.That(_normalizeTab.Invoke(null, new object[] { 1 }), Is.EqualTo(1));
            Assert.That(_normalizeTab.Invoke(null, new object[] { 2 }), Is.EqualTo(2));
            Assert.That(_normalizeTab.Invoke(null, new object[] { -1 }), Is.EqualTo(0));
            Assert.That(_normalizeTab.Invoke(null, new object[] { 3 }), Is.EqualTo(0));
            Assert.That(_tabLabel.Invoke(null, new object[] { 0 }), Is.EqualTo("Humanoid Avatar"));
            Assert.That(_tabLabel.Invoke(null, new object[] { 1 }), Is.EqualTo("Humanoid Clips"));
            Assert.That(_tabLabel.Invoke(null, new object[] { 2 }), Is.EqualTo("Root Motion"));
        }

        [Test]
        public void UnifiedWindowUsesOneCanonicalMenuCommand()
        {
            Assert.That(
                _canonicalMenuPath.Invoke(null, null),
                Is.EqualTo("TriSync/Open Animation Tools"));
        }

        [Test]
        public void EachWorkflowRemainsAnIndependentView()
        {
            Assert.That(ProductApi.RequireType("BlenderSyncVNext.RootMotion.HumanoidAvatarSetupView"), Is.Not.Null);
            Assert.That(ProductApi.RequireType("BlenderSyncVNext.RootMotion.HumanoidClipConverterView"), Is.Not.Null);
            Assert.That(ProductApi.RequireType("BlenderSyncVNext.RootMotion.RootMotionWorkflowView"), Is.Not.Null);
        }
    }
}
