using System;
using System.IO;
using System.Linq;
using System.Reflection;
using BlenderSyncVNext.APT;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class MaterialContentV1ApplyServiceTests
    {
        private const string TypeName = "BlenderSyncVNext.SceneSyncCore.MaterialContentV1ApplyService";
        private const string MessageTypeName = "BlenderSyncVNext.SceneSyncCore.SceneSyncMaterialContentV1Message";
        private const string MaterialReferenceTypeName = "BlenderSyncVNext.SceneSyncCore.MaterialReferenceApplyService";
        private const string DefaultMaterialPath = "Assets/TriSync/Resources/Materials/TriSyncDefault.mat";
        private MethodInfo _buildAppliedFingerprint;
        private MethodInfo _buildMaterialAssetPath;
        private MethodInfo _buildTextureAssetPath;
        private MethodInfo _resolveDefaultMaterial;
        private System.Type _messageType;
        private System.Type _textureType;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _buildAppliedFingerprint = ProductApi.RequireStaticMethod(TypeName, "BuildAppliedFingerprint", 1);
            _buildMaterialAssetPath = ProductApi.RequireStaticMethod(TypeName, "BuildMaterialAssetPath", 2);
            _buildTextureAssetPath = ProductApi.RequireStaticMethod(TypeName, "BuildTextureAssetPath", 1);
            _resolveDefaultMaterial = ProductApi.RequireStaticMethod(
                MaterialReferenceTypeName,
                "ResolveDefaultMaterial",
                0);
            _messageType = ProductApi.RequireType(MessageTypeName);
            _textureType = ProductApi.RequireType("BlenderSyncVNext.SceneSyncCore.MaterialContentV1Texture");
        }

        [Test]
        public void AppliedFingerprintIsStableForIdenticalContent()
        {
            var message = CreateMessage();

            Assert.That(Fingerprint(message), Is.EqualTo(Fingerprint(message)));
            Assert.That(Fingerprint(CreateMessage()), Is.EqualTo(Fingerprint(message)));
        }

        [Test]
        public void AppliedFingerprintTracksContentAndTextureDependencies()
        {
            var baseline = Fingerprint(CreateMessage());
            var contentChanged = CreateMessage(contentHash: "content-b");
            var textureChanged = CreateMessage(sourceChannel: "alpha");

            Assert.That(Fingerprint(contentChanged), Is.Not.EqualTo(baseline));
            Assert.That(Fingerprint(textureChanged), Is.Not.EqualTo(baseline));
        }

        [Test]
        public void GeneratedMaterialAndTexturePathsUseUnversionedResourceFolders()
        {
            var materialPath = (string)_buildMaterialAssetPath.Invoke(
                null,
                new object[] { "mat-abcdef", "Body" });
            var texture = JsonUtility.FromJson(
                "{\"usage\":\"baseColor\",\"textureRef\":\"tex-abcdef\",\"fileName\":\"Body.png\"}",
                _textureType);
            var texturePath = (string)_buildTextureAssetPath.Invoke(null, new[] { texture });

            Assert.That(materialPath, Does.StartWith("Assets/TriSync/Resources/Materials/"));
            Assert.That(texturePath, Does.StartWith("Assets/TriSync/Resources/Textures/baseColor/"));
            Assert.That(materialPath, Does.Not.Contain("MaterialsV1"));
            Assert.That(texturePath, Does.Not.Contain("TexturesV1"));
        }

        [Test]
        public void DefaultMaterialUsesTriSyncAssetAndObjectNames()
        {
            AssetDatabase.DeleteAsset(DefaultMaterialPath);
            try
            {
                var material = (Material)_resolveDefaultMaterial.Invoke(null, null);

                Assert.That(material, Is.Not.Null);
                Assert.That(material.name, Is.EqualTo("TriSyncDefault"));
                Assert.That(AssetDatabase.GetAssetPath(material), Is.EqualTo(DefaultMaterialPath));
            }
            finally
            {
                AssetDatabase.DeleteAsset(DefaultMaterialPath);
                AssetDatabase.Refresh();
            }
        }

        [Test]
        public void MaterialApplyRegistersTextureRecordsAndRepairsMissingRecords()
        {
            var suffix = Guid.NewGuid().ToString("N");
            var materialRef = "mat-registry-" + suffix;
            var textureRef = "tex-registry-" + suffix;
            var sourceAssetPath = "Assets/TriSync/Resources/Textures/registry-tests/" + suffix + ".png";
            var sourceAbsolutePath = Path.Combine(
                Application.dataPath,
                "TriSync/Resources/Textures/registry-tests/" + suffix + ".png").Replace('\\', '/');
            var materialPath = null as string;

            try
            {
                WriteTestTexture(sourceAssetPath);
                var message = CreateMaterialMessage(materialRef, textureRef, sourceAbsolutePath);
                var service = new BlenderSyncVNext.SceneSyncCore.MaterialContentV1ApplyService();

                Assert.That(service.TryApply(message, out _, out var firstError), Is.True, firstError);

                var repository = new AptRepository();
                var firstDb = repository.Load();
                var materialRecord = repository.FindByAssetId(firstDb, materialRef);
                Assert.That(materialRecord, Is.Not.Null);
                materialPath = materialRecord.target.unityAssetPath;

                var baseTextureRecord = repository.FindByAssetId(firstDb, textureRef);
                AssertTextureRecord(baseTextureRecord, sourceAssetPath);

                var normalTextureRecord = repository.FindByAssetId(firstDb, textureRef + ":normal");
                Assert.That(normalTextureRecord, Is.Not.Null);
                Assert.That(normalTextureRecord.target.resourceType, Is.EqualTo("texture"));
                Assert.That(normalTextureRecord.target.unityAssetPath, Is.Not.EqualTo(sourceAssetPath));

                firstDb.records = (firstDb.records ?? Array.Empty<AptRecord>())
                    .Where(record => record != null
                        && !string.Equals(record.assetId, textureRef, StringComparison.Ordinal)
                        && !string.Equals(record.assetId, textureRef + ":normal", StringComparison.Ordinal))
                    .ToArray();
                repository.Save(firstDb);
                Assert.That(repository.FindByAssetId(repository.Load(), textureRef), Is.Null);

                Assert.That(service.TryApply(message, out _, out var repairError), Is.True, repairError);

                var repairedDb = repository.Load();
                AssertTextureRecord(repository.FindByAssetId(repairedDb, textureRef), sourceAssetPath);
                Assert.That(repository.FindByAssetId(repairedDb, textureRef + ":normal"), Is.Not.Null);
            }
            finally
            {
                CleanupTestMaterialAndTextures(materialRef, textureRef, materialPath, sourceAssetPath);
            }
        }

        [Test]
        public void MaterialApplyPresentsBlenderLinearBaseColorAsGammaRgb()
        {
            var materialRef = "mat-color-space-" + Guid.NewGuid().ToString("N");
            var materialPath = (string)_buildMaterialAssetPath.Invoke(
                null,
                new object[] { materialRef, "Color Space Test" });
            try
            {
                var json = "{\"type\":\"scene_sync.material_content_v1\",\"schema\":\"material_content_v1\",\"materialRef\":\"" + materialRef +
                    "\",\"source\":{\"name\":\"Color Space Test\"},\"properties\":{\"baseColor\":[0,0.155640006,0.799340009,1],\"metallic\":0,\"roughness\":0.5,\"alpha\":1}," +
                    "\"fingerprint\":{\"contentHash\":\"color-space-content\",\"textureDependencyHash\":\"\"}}";
                var message = JsonUtility.FromJson<BlenderSyncVNext.SceneSyncCore.SceneSyncMaterialContentV1Message>(json);
                var service = new BlenderSyncVNext.SceneSyncCore.MaterialContentV1ApplyService();

                Assert.That(service.TryApply(message, out _, out var error), Is.True, error);

                var material = AssetDatabase.LoadAssetAtPath<Material>(materialPath);
                Assert.That(material, Is.Not.Null);
                var color = material.GetColor("_BSG_BaseColor");
                var expected = new Color(0f, 0.155640006f, 0.799340009f, 1f);
                if (PlayerSettings.colorSpace == ColorSpace.Linear)
                    expected = expected.gamma;
                Assert.That(color.r, Is.EqualTo(0f).Within(0.0001f));
                Assert.That(color.g, Is.EqualTo(expected.g).Within(0.0002f));
                Assert.That(color.b, Is.EqualTo(expected.b).Within(0.0002f));
            }
            finally
            {
                AssetDatabase.DeleteAsset(materialPath);
                var repository = new AptRepository();
                var db = repository.Load();
                db.records = (db.records ?? Array.Empty<AptRecord>())
                    .Where(record => record != null && !string.Equals(record.assetId, materialRef, StringComparison.Ordinal))
                    .ToArray();
                repository.Save(db);
                AssetDatabase.Refresh();
            }
        }

        private object CreateMessage(string contentHash = "content-a", string sourceChannel = "rgb")
        {
            var json = "{\"fingerprint\":{\"contentHash\":\"" + contentHash +
                "\",\"textureDependencyHash\":\"textures-a\"},\"textures\":{\"baseColor\":{" +
                "\"usage\":\"baseColor\",\"textureRef\":\"tex-a\"," +
                "\"sourcePath\":\"Z:/missing/texture.png\",\"sourceChannel\":\"" + sourceChannel + "\"}}}";
            return JsonUtility.FromJson(json, _messageType);
        }

        private string Fingerprint(object message)
        {
            return (string)_buildAppliedFingerprint.Invoke(null, new object[] { message });
        }

        private static BlenderSyncVNext.SceneSyncCore.SceneSyncMaterialContentV1Message CreateMaterialMessage(string materialRef, string textureRef, string sourcePath)
        {
            var escapedPath = sourcePath.Replace("\\", "\\\\").Replace("\"", "\\\"");
            var json = "{\"type\":\"scene_sync.material_content_v1\",\"schema\":\"material_content_v1\",\"materialRef\":\"" + materialRef +
                "\",\"source\":{\"name\":\"Registry Test Material\"},\"shader\":{\"policy\":\"native\",\"target\":\"principled_lit_urp\"}," +
                "\"properties\":{\"baseColor\":[1,1,1,1],\"metallic\":0,\"roughness\":0.5,\"alpha\":1}," +
                "\"textures\":{\"baseColor\":{\"textureRef\":\"" + textureRef + "\",\"sourcePath\":\"" + escapedPath +
                "\",\"imageName\":\"Registry Test Texture\",\"fileName\":\"" + Path.GetFileName(sourcePath) +
                "\",\"usage\":\"baseColor\",\"sourceKind\":\"external\"},\"normal\":{\"textureRef\":\"" + textureRef +
                "\",\"sourcePath\":\"" + escapedPath + "\",\"imageName\":\"Registry Test Texture\",\"fileName\":\"" +
                Path.GetFileName(sourcePath) + "\",\"usage\":\"normal\",\"sourceKind\":\"external\",\"normalSpace\":\"TANGENT\"}}," +
                "\"fingerprint\":{\"contentHash\":\"registry-content\",\"textureDependencyHash\":\"registry-textures\"}}";
            return JsonUtility.FromJson<BlenderSyncVNext.SceneSyncCore.SceneSyncMaterialContentV1Message>(json);
        }

        private static void WriteTestTexture(string assetPath)
        {
            var fullPath = Path.Combine(
                Application.dataPath,
                assetPath.Substring("Assets/".Length)).Replace('\\', '/');
            Directory.CreateDirectory(Path.GetDirectoryName(fullPath));
            var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            try
            {
                texture.SetPixels(new[]
                {
                    Color.red,
                    Color.green,
                    Color.blue,
                    Color.white,
                });
                File.WriteAllBytes(fullPath, texture.EncodeToPNG());
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }
            AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport);
        }

        private static void AssertTextureRecord(AptRecord record, string expectedPath)
        {
            Assert.That(record, Is.Not.Null);
            Assert.That(record.target, Is.Not.Null);
            Assert.That(record.target.resourceType, Is.EqualTo("texture"));
            Assert.That(record.target.unityAssetPath, Is.EqualTo(expectedPath));
            Assert.That(record.target.assetGuid, Is.Not.Empty);
            Assert.That(record.mappingState, Is.EqualTo("mapped"));
            Assert.That(record.updateState, Is.EqualTo("up_to_date"));
        }

        private static void CleanupTestMaterialAndTextures(string materialRef, string textureRef, string materialPath, string sourceAssetPath)
        {
            var repository = new AptRepository();
            var db = repository.Load();
            var testRecords = (db.records ?? Array.Empty<AptRecord>())
                .Where(record => record != null && !string.IsNullOrWhiteSpace(record.assetId) &&
                    (string.Equals(record.assetId, materialRef, StringComparison.Ordinal) ||
                     string.Equals(record.assetId, textureRef, StringComparison.Ordinal) ||
                     record.assetId.StartsWith(textureRef + ":", StringComparison.Ordinal)))
                .ToArray();
            foreach (var record in testRecords)
            {
                if (!string.IsNullOrWhiteSpace(record.target?.unityAssetPath))
                    AssetDatabase.DeleteAsset(record.target.unityAssetPath);
            }
            if (!string.IsNullOrWhiteSpace(materialPath))
                AssetDatabase.DeleteAsset(materialPath);
            AssetDatabase.DeleteAsset(sourceAssetPath);

            db.records = (db.records ?? Array.Empty<AptRecord>())
                .Where(record => record != null && !string.IsNullOrWhiteSpace(record.assetId) &&
                    !string.Equals(record.assetId, materialRef, StringComparison.Ordinal) &&
                    !string.Equals(record.assetId, textureRef, StringComparison.Ordinal) &&
                    !record.assetId.StartsWith(textureRef + ":", StringComparison.Ordinal))
                .ToArray();
            repository.Save(db);
            AssetDatabase.Refresh();
        }
    }
}
