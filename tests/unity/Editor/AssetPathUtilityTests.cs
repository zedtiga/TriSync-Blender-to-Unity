using System.Reflection;
using NUnit.Framework;

namespace BlenderSyncVNext.Tests
{
    public sealed class AssetPathUtilityTests
    {
        private const string TypeName = "BlenderSyncVNext.AssetBridgeCore.AssetPathUtility";
        private MethodInfo _buildStableAssetPath;
        private MethodInfo _buildStableFileName;
        private MethodInfo _shortStableId;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _buildStableAssetPath = ProductApi.RequireStaticMethod(TypeName, "BuildStableAssetPath", 4);
            _buildStableFileName = ProductApi.RequireStaticMethod(TypeName, "BuildStableFileName", 3);
            _shortStableId = ProductApi.RequireStaticMethod(TypeName, "ShortStableId", 1);
        }

        [Test]
        public void DifferentStableIdsCreateDifferentPathsForSameName()
        {
            var first = BuildPath("Assets/Meshes", "Cube", "mesh-abcdef01", ".asset");
            var second = BuildPath("Assets/Meshes", "Cube", "mesh-12345678", ".asset");

            Assert.That(first, Is.EqualTo("Assets/Meshes/Cube_abcdef.asset"));
            Assert.That(second, Is.EqualTo("Assets/Meshes/Cube_123456.asset"));
            Assert.That(first, Is.Not.EqualTo(second));
        }

        [Test]
        public void SameStableIdProducesStablePath()
        {
            var first = BuildPath("Assets\\Meshes\\", "Cube", "mesh-ABCDEF01", "asset");
            var second = BuildPath("Assets/Meshes", "Cube", "mesh-ABCDEF01", ".asset");

            Assert.That(first, Is.EqualTo(second));
        }

        [TestCase("mesh-Ab12Cd34", "ab12cd")]
        [TestCase("mat_Ab12Cd34", "ab12cd")]
        [TestCase("tex-Ab12Cd34", "ab12cd")]
        [TestCase("rigobj-Ab12Cd34", "ab12cd")]
        public void KnownPrefixIsRemovedFromShortSuffix(string stableId, string expected)
        {
            Assert.That(ShortId(stableId), Is.EqualTo(expected));
        }

        [Test]
        public void ShortSuffixUsesSixAlphanumericCharacters()
        {
            Assert.That(ShortId("mesh-A1-B2_C3-D4"), Is.EqualTo("a1b2c3"));
        }

        [Test]
        public void InvalidFileNameCharactersAreSanitized()
        {
            var fileName = (string)_buildStableFileName.Invoke(
                null,
                new object[] { "Bad:Name/Part\\Mesh", "mesh-abcdef", ".asset" });

            Assert.That(fileName, Is.EqualTo("Bad_Name_Part_Mesh_abcdef.asset"));
        }

        private string BuildPath(string folder, string name, string stableId, string extension)
        {
            return (string)_buildStableAssetPath.Invoke(
                null,
                new object[] { folder, name, stableId, extension });
        }

        private string ShortId(string stableId)
        {
            return (string)_shortStableId.Invoke(null, new object[] { stableId });
        }
    }
}
