using System.Linq;
using NUnit.Framework;
using UnityEditor.Compilation;

namespace BlenderSyncVNext.Tests
{
    public sealed class AssemblyBoundaryTests
    {
        [Test]
        public void PlayerAssemblyGraphExcludesAllTriSyncCode()
        {
            var assemblyNames = CompilationPipeline
                .GetAssemblies(AssembliesType.PlayerWithoutTestAssemblies)
                .Select(assembly => assembly.name)
                .ToArray();

            Assert.That(
                assemblyNames.Where(name => name.StartsWith("BlenderSyncVNext.")),
                Is.Empty);
            Assert.That(assemblyNames, Does.Not.Contain("BlenderSyncVNext.Editor"));
            Assert.That(assemblyNames, Does.Not.Contain("BlenderSyncVNext.URP.Editor"));
        }
    }
}
