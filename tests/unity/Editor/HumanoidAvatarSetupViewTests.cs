using System.Reflection;
using NUnit.Framework;

namespace BlenderSyncVNext.Tests
{
    public sealed class HumanoidAvatarSetupViewTests
    {
        private const string TypeName = "BlenderSyncVNext.RootMotion.HumanoidAvatarSetupView";
        private MethodInfo _avatarStatusLabel;
        private MethodInfo _mappingSummaryLabel;
        private MethodInfo _generateStatusLabel;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _avatarStatusLabel = ProductApi.RequireStaticMethod(TypeName, "AvatarStatusLabel", 4);
            _mappingSummaryLabel = ProductApi.RequireStaticMethod(TypeName, "MappingSummaryLabel", 4);
            _generateStatusLabel = ProductApi.RequireStaticMethod(TypeName, "GenerateStatusLabel", 4);
        }

        [Test]
        public void AvatarStatusDistinguishesAssignmentAndValidation()
        {
            Assert.That(AvatarStatus(null, false, false, false), Is.EqualTo("Not assigned"));
            Assert.That(
                AvatarStatus("MixamoAvatar", true, true, true),
                Is.EqualTo("MixamoAvatar | Valid | Humanoid"));
            Assert.That(
                AvatarStatus("GenericAvatar", true, false, false),
                Is.EqualTo("GenericAvatar | Invalid | Generic"));
        }

        [Test]
        public void MappingSummaryReportsRequiredAndOptionalCoverage()
        {
            Assert.That(
                _mappingSummaryLabel.Invoke(null, new object[] { 15, 15, 22, 40 }),
                Is.EqualTo("Required 15/15 | Optional 22/40"));
        }

        [Test]
        public void GenerateStatusExplainsEveryReadinessGate()
        {
            Assert.That(
                GenerateStatus(false, false, 0, false),
                Is.EqualTo("Assign a character Animator to begin."));
            Assert.That(
                GenerateStatus(true, false, 0, false),
                Is.EqualTo("Humanoid mapping is unavailable."));
            Assert.That(
                GenerateStatus(true, true, 1, true),
                Is.EqualTo("1 required bone is missing."));
            Assert.That(
                GenerateStatus(true, true, 3, true),
                Is.EqualTo("3 required bones are missing."));
            Assert.That(
                GenerateStatus(true, true, 0, false),
                Is.EqualTo("One or more mapped bones are outside the target hierarchy."));
            Assert.That(
                GenerateStatus(true, true, 0, true),
                Is.EqualTo("Ready to generate and assign a Humanoid Avatar."));
        }

        private string AvatarStatus(string name, bool assigned, bool valid, bool human)
        {
            return (string)_avatarStatusLabel.Invoke(
                null,
                new object[] { name, assigned, valid, human });
        }

        private string GenerateStatus(
            bool hasTarget,
            bool hasMapping,
            int missingRequired,
            bool allBonesUnderTarget)
        {
            return (string)_generateStatusLabel.Invoke(
                null,
                new object[] { hasTarget, hasMapping, missingRequired, allBonesUnderTarget });
        }
    }
}
