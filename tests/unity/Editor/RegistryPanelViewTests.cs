using System;
using System.IO;
using System.Reflection;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Protocol;
using BlenderSyncVNext.SceneSyncCore;
using NUnit.Framework;

namespace BlenderSyncVNext.Tests
{
    public sealed class RegistryPanelViewTests
    {
        private const string TypeName = "BlenderSyncVNext.UI.RegistryPanelView";
        private const BindingFlags InstanceFlags = BindingFlags.Instance | BindingFlags.NonPublic;
        private const BindingFlags StaticFlags = BindingFlags.Static | BindingFlags.NonPublic;

        private Type _viewType;
        private MethodInfo _includeAsset;
        private MethodInfo _includeObject;
        private MethodInfo _includeRigged;
        private MethodInfo _isRiggedBound;
        private MethodInfo _resolveRiggedPairId;
        private MethodInfo _describeRiggedSlots;
        private MethodInfo _collectRiggedMeshRefs;
        private MethodInfo _getRegistryFilePaths;
        private MethodInfo _buildRegistryFileSignature;
        private MethodInfo _getDiagnosticsData;
        private MethodInfo _formatPipelineQueue;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _viewType = ProductApi.RequireType(TypeName);
            _includeAsset = RequireMethod("ShouldIncludeAssetRecord", InstanceFlags);
            _includeObject = RequireMethod("ShouldIncludeObjectRecord", InstanceFlags);
            _includeRigged = RequireMethod("ShouldIncludeRiggedObjectRecord", InstanceFlags);
            _isRiggedBound = RequireMethod("IsRiggedObjectBound", StaticFlags);
            _resolveRiggedPairId = RequireMethod("ResolveRiggedPairId", StaticFlags);
            _describeRiggedSlots = RequireMethod("DescribeRiggedResourceSlots", StaticFlags);
            _getRegistryFilePaths = RequireMethod("GetRegistryFilePaths", StaticFlags);
            _buildRegistryFileSignature = RequireMethod("BuildRegistryFileSignature", StaticFlags);
            _getDiagnosticsData = _viewType.GetMethod(
                "GetDiagnosticsData",
                BindingFlags.Instance | BindingFlags.Public);
            Assert.That(_getDiagnosticsData, Is.Not.Null);
            _formatPipelineQueue = RequireMethod("FormatPipelineQueue", StaticFlags);
            var coordinatorType = ProductApi.RequireType("BlenderSyncVNext.AssetBridgeCore.RiggedObjectImportCoordinator");
            _collectRiggedMeshRefs = coordinatorType.GetMethod("CollectRiggedMeshRefs", StaticFlags);
            Assert.That(_collectRiggedMeshRefs, Is.Not.Null);
        }

        [Test]
        public void ResourceRegistryNoLongerFiltersUnmappedRecords()
        {
            Assert.That(_viewType.GetField("_assetMappedOnly", InstanceFlags), Is.Null);

            var view = CreateView();
            var record = new AptRecord
            {
                assetId = "mesh-unmapped",
                mappingState = "missing_target",
                target = new AptTarget { resourceType = "mesh" },
            };

            Assert.That(_includeAsset.Invoke(view, new object[] { record }), Is.EqualTo(true));
        }

        [Test]
        public void RiggedBoundFilterUsesTheManagedInstancePair()
        {
            var view = CreateView();
            SetField(view, "_objectBoundOnly", true);
            var record = CreateRiggedRecord();

            Assert.That(_includeRigged.Invoke(view, new object[] { record }), Is.EqualTo(false));
            Assert.That(_isRiggedBound.Invoke(null, new object[] { record }), Is.EqualTo(false));

            record.managedInstance = new RiggedManagedInstanceRecord { pairId = "rigpair-managed" };

            Assert.That(_includeRigged.Invoke(view, new object[] { record }), Is.EqualTo(true));
            Assert.That(_isRiggedBound.Invoke(null, new object[] { record }), Is.EqualTo(true));
        }

        [Test]
        public void RiggedSearchCoversIdentityNameAndResourceReferences()
        {
            var view = CreateView();
            SetField(view, "_objectBoundOnly", false);
            var record = CreateRiggedRecord();

            SetField(view, "_objectSearch", "hero armature");
            Assert.That(_includeRigged.Invoke(view, new object[] { record }), Is.EqualTo(true));

            SetField(view, "_objectSearch", "mesh-hero");
            Assert.That(_includeRigged.Invoke(view, new object[] { record }), Is.EqualTo(true));

            SetField(view, "_objectSearch", "not-present");
            Assert.That(_includeRigged.Invoke(view, new object[] { record }), Is.EqualTo(false));
        }

        [Test]
        public void ObjectTypeFilterSeparatesRegularAndRiggedRecords()
        {
            var view = CreateView();
            SetField(view, "_objectBoundOnly", false);
            var regular = new ObjectBindingEntry { pairId = "pair-1", bindingState = "bound" };
            var rigged = CreateRiggedRecord();

            SetField(view, "_objectTypeFilterIndex", 1);
            Assert.That(_includeObject.Invoke(view, new object[] { regular }), Is.EqualTo(true));
            Assert.That(_includeRigged.Invoke(view, new object[] { rigged }), Is.EqualTo(false));

            SetField(view, "_objectTypeFilterIndex", 2);
            Assert.That(_includeObject.Invoke(view, new object[] { regular }), Is.EqualTo(false));
            Assert.That(_includeRigged.Invoke(view, new object[] { rigged }), Is.EqualTo(true));
        }

        [Test]
        public void RiggedUnregisterPairPrefersManagedIdAndFallsBackToImplicitId()
        {
            var record = CreateRiggedRecord();

            Assert.That(
                _resolveRiggedPairId.Invoke(null, new object[] { record }),
                Is.EqualTo("rigpair-rigobj-hero"));

            record.managedInstance = new RiggedManagedInstanceRecord { pairId = " custom-pair " };
            Assert.That(
                _resolveRiggedPairId.Invoke(null, new object[] { record }),
                Is.EqualTo("custom-pair"));
        }

        [Test]
        public void RiggedResourceSlotsDescribeDirectMeshAndMaterialReferences()
        {
            var record = CreateRiggedRecord();

            Assert.That(
                _describeRiggedSlots.Invoke(null, new object[] { record, "mesh-hero" }),
                Is.EqualTo("Mesh"));
            Assert.That(
                _describeRiggedSlots.Invoke(null, new object[] { record, "mat-hero" }),
                Is.EqualTo("Material[0]"));
            Assert.That(
                _describeRiggedSlots.Invoke(null, new object[] { record, "unrelated" }),
                Is.EqualTo(string.Empty));

            record.meshRefs = new[] { "mesh-hero", "mesh-secondary" };
            Assert.That(
                _describeRiggedSlots.Invoke(null, new object[] { record, "mesh-secondary" }),
                Is.EqualTo("Mesh[1]"));
        }

        [Test]
        public void RiggedImportCollectsAllDistinctMeshPartReferences()
        {
            var payload = new RiggedObjectPayload
            {
                meshRef = "mesh-primary",
                meshParts = new[]
                {
                    new RiggedMeshPartPayload { meshRef = "mesh-primary" },
                    new RiggedMeshPartPayload { meshRef = " mesh-secondary " },
                    new RiggedMeshPartPayload { meshRef = "mesh-secondary" },
                },
            };

            var refs = (string[])_collectRiggedMeshRefs.Invoke(null, new object[] { payload });
            CollectionAssert.AreEqual(new[] { "mesh-primary", "mesh-secondary" }, refs);
        }

        [Test]
        public void AutoRefreshSignatureTracksAllRegistryFiles()
        {
            var paths = (string[])_getRegistryFilePaths.Invoke(null, null);

            Assert.That(paths, Has.Length.EqualTo(5));
            CollectionAssert.AreEquivalent(
                new[] { "meshes.json", "materials.json", "textures.json", "object-bindings.json", "rigged-objects.json" },
                Array.ConvertAll(paths, Path.GetFileName));
        }

        [Test]
        public void AutoRefreshSignatureIsStableUntilFileContentChanges()
        {
            var directory = CreateTempDirectory();
            try
            {
                var first = Path.Combine(directory, "first.json");
                var second = Path.Combine(directory, "second.json");
                File.WriteAllText(first, "one");
                File.WriteAllText(second, "two");

                var initial = BuildRegistryFileSignature(first, second);
                Assert.That(BuildRegistryFileSignature(first, second), Is.EqualTo(initial));

                File.WriteAllText(second, "changed-content");
                Assert.That(BuildRegistryFileSignature(first, second), Is.Not.EqualTo(initial));
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        [Test]
        public void AutoRefreshSignatureDetectsFileCreationAndDeletion()
        {
            var directory = CreateTempDirectory();
            try
            {
                var path = Path.Combine(directory, "registry.json");
                var missing = BuildRegistryFileSignature(path);

                File.WriteAllText(path, "created");
                var created = BuildRegistryFileSignature(path);
                Assert.That(created, Is.Not.EqualTo(missing));

                File.Delete(path);
                Assert.That(BuildRegistryFileSignature(path), Is.EqualTo(missing));
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        [Test]
        public void DiagnosticsSummaryIncludesRiggedObjectsWithoutTreatingUninstantiatedRigsAsErrors()
        {
            var view = CreateView();
            SetField(view, "_objectDb", new ObjectBindingDatabase
            {
                records = new[]
                {
                    new ObjectBindingEntry { pairId = "pair-bound", bindingState = "bound" },
                    new ObjectBindingEntry { pairId = "pair-missing", bindingState = "missing" },
                },
            });
            SetField(view, "_riggedDb", new RiggedObjectDatabase
            {
                records = new[]
                {
                    new RiggedObjectRecord
                    {
                        riggedObjectId = "rig-managed",
                        mappingState = "mapped",
                        updateState = "up_to_date",
                        managedInstance = new RiggedManagedInstanceRecord { pairId = "rigpair-managed" },
                    },
                    new RiggedObjectRecord
                    {
                        riggedObjectId = "rig-prefab-only",
                        mappingState = "mapped",
                        updateState = "up_to_date",
                    },
                    new RiggedObjectRecord
                    {
                        riggedObjectId = "rig-error",
                        mappingState = "missing_target",
                        updateState = "error",
                    },
                },
            });

            var data = _getDiagnosticsData.Invoke(view, null);
            Assert.That(GetFieldValue<int>(data, "objectCount"), Is.EqualTo(5));
            Assert.That(GetFieldValue<int>(data, "riggedObjectCount"), Is.EqualTo(3));
            Assert.That(GetFieldValue<int>(data, "boundObjectCount"), Is.EqualTo(2));
            Assert.That(GetFieldValue<int>(data, "missingOrRemovedObjectCount"), Is.EqualTo(1));
            Assert.That(GetFieldValue<int>(data, "objectIssueCount"), Is.EqualTo(2));
        }

        [Test]
        public void PipelineDiagnosticsExposeOnlyConciseActionableQueueState()
        {
            Assert.That(FormatPipelineQueue(false, 0, 0), Is.EqualTo("Idle"));
            Assert.That(FormatPipelineQueue(false, 3, 0), Is.EqualTo("Pending, 3 pending"));
            Assert.That(
                FormatPipelineQueue(true, 3, 1),
                Is.EqualTo("Processing, 3 pending, 1 failed"));
            Assert.That(FormatPipelineQueue(false, 0, 2), Is.EqualTo("Idle, 2 failed"));
        }

        private object CreateView()
        {
            return Activator.CreateInstance(_viewType, true);
        }

        private string BuildRegistryFileSignature(params string[] paths)
        {
            return (string)_buildRegistryFileSignature.Invoke(null, new object[] { paths });
        }

        private static string CreateTempDirectory()
        {
            var path = Path.Combine(Path.GetTempPath(), "BlenderSyncRegistryPanelTests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(path);
            return path;
        }

        private MethodInfo RequireMethod(string name, BindingFlags flags)
        {
            var method = _viewType.GetMethod(name, flags);
            Assert.That(method, Is.Not.Null, $"Missing product method: {TypeName}.{name}");
            return method;
        }

        private static RiggedObjectRecord CreateRiggedRecord()
        {
            return new RiggedObjectRecord
            {
                riggedObjectId = "rigobj-hero",
                objectName = "Hero Armature",
                meshRef = "mesh-hero",
                materialRefs = new[] { "mat-hero" },
                mappingState = "mapped",
                updateState = "up_to_date",
            };
        }

        private static void SetField(object target, string name, object value)
        {
            var field = target.GetType().GetField(name, InstanceFlags);
            Assert.That(field, Is.Not.Null, $"Missing product field: {name}");
            field.SetValue(target, value);
        }

        private static T GetFieldValue<T>(object target, string name)
        {
            var field = target.GetType().GetField(
                name,
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            Assert.That(field, Is.Not.Null, $"Missing diagnostics field: {name}");
            return (T)field.GetValue(target);
        }

        private string FormatPipelineQueue(bool processing, int pendingCount, int failedCount)
        {
            return (string)_formatPipelineQueue.Invoke(
                null,
                new object[] { processing, pendingCount, failedCount });
        }
    }
}
