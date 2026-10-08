using System.Reflection;
using NUnit.Framework;

namespace BlenderSyncVNext.Tests
{
    public sealed class RootMotionWorkflowViewTests
    {
        private const string TypeName = "BlenderSyncVNext.RootMotion.RootMotionWorkflowView";
        private System.Type _viewType;
        private MethodInfo _genericAvatarActionLabel;
        private MethodInfo _needsHumanoidReplacementConfirmation;
        private MethodInfo _curveSummaryLabel;
        private MethodInfo _createStatusLabel;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _viewType = ProductApi.RequireType(TypeName);
            _genericAvatarActionLabel = ProductApi.RequireStaticMethod(TypeName, "GenericAvatarActionLabel", 1);
            _needsHumanoidReplacementConfirmation = ProductApi.RequireStaticMethod(
                TypeName,
                "NeedsHumanoidReplacementConfirmation",
                2);
            _curveSummaryLabel = ProductApi.RequireStaticMethod(TypeName, "CurveSummaryLabel", 2);
            _createStatusLabel = ProductApi.RequireStaticMethod(TypeName, "CreateStatusLabel", 6);
        }

        [Test]
        public void EditableAvatarFieldKeepsCreateReplaceAndHumanoidRiskRules()
        {
            Assert.That(
                _viewType.GetMethod("AvatarStatusLabel", BindingFlags.Static | BindingFlags.NonPublic),
                Is.Null);
            Assert.That(
                _viewType.GetMethod("AssignAnimatorAvatar", BindingFlags.Instance | BindingFlags.NonPublic),
                Is.Not.Null);
            Assert.That(
                _genericAvatarActionLabel.Invoke(null, new object[] { false }),
                Is.EqualTo("Create Generic Avatar"));
            Assert.That(
                _genericAvatarActionLabel.Invoke(null, new object[] { true }),
                Is.EqualTo("Replace with Generic Avatar"));
            Assert.That(NeedsConfirmation(false, false), Is.False);
            Assert.That(NeedsConfirmation(true, false), Is.False);
            Assert.That(NeedsConfirmation(true, true), Is.True);
        }

        [Test]
        public void CurveSummaryReportsMotionRootCoverage()
        {
            Assert.That(
                _curveSummaryLabel.Invoke(null, new object[] { 3, 4 }),
                Is.EqualTo("Position 3/3 | Rotation 4/4"));
            Assert.That(
                _curveSummaryLabel.Invoke(null, new object[] { 2, 0 }),
                Is.EqualTo("Position 2/3 | Rotation 0/4"));
        }

        [Test]
        public void CreateStatusExplainsReadinessWithoutRequiringAnAvatar()
        {
            Assert.That(
                CreateStatus(false, false, false, false, 0, 0),
                Is.EqualTo("Assign a character Animator to begin."));
            Assert.That(
                CreateStatus(true, false, false, false, 0, 0),
                Is.EqualTo("Assign a Root Node."));
            Assert.That(
                CreateStatus(true, true, false, false, 0, 0),
                Is.EqualTo("Root Node must be under the Animator hierarchy."));
            Assert.That(
                CreateStatus(true, true, true, false, 0, 0),
                Is.EqualTo("Assign a Source Clip."));
            Assert.That(
                CreateStatus(true, true, true, true, 2, 3),
                Is.EqualTo("Ready with constant fallbacks for missing root channels."));
            Assert.That(
                CreateStatus(true, true, true, true, 3, 4),
                Is.EqualTo("Ready to create a Root Motion Clip."));
        }

        private bool NeedsConfirmation(bool assigned, bool human)
        {
            return (bool)_needsHumanoidReplacementConfirmation.Invoke(
                null,
                new object[] { assigned, human });
        }

        private string CreateStatus(
            bool hasAnimator,
            bool hasMotionRoot,
            bool motionRootUnderAnimator,
            bool hasSourceClip,
            int positionCurves,
            int rotationCurves)
        {
            return (string)_createStatusLabel.Invoke(
                null,
                new object[]
                {
                    hasAnimator,
                    hasMotionRoot,
                    motionRootUnderAnimator,
                    hasSourceClip,
                    positionCurves,
                    rotationCurves,
                });
        }
    }
}
