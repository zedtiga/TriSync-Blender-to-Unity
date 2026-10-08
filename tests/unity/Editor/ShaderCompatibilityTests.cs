using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class ShaderCompatibilityTests
    {
        [Test]
        public void PrincipledLitUrpImportsWithoutCompilerErrors()
        {
            var shader = Shader.Find("TriSync/Principled Lit URP");

            Assert.That(shader, Is.Not.Null, "TriSync Principled Lit URP shader was not imported.");
            Assert.That(
                ShaderUtil.ShaderHasError(shader),
                Is.False,
                "TriSync Principled Lit URP shader has compiler errors; inspect the Unity Editor log.");
        }
    }
}
