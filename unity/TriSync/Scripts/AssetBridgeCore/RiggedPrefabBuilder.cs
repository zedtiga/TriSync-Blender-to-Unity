using System;
using System.IO;
using BlenderSyncVNext.Protocol;
using BlenderSyncVNext.SceneSyncCore;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.AssetBridgeCore
{
    public sealed class RiggedPrefabBuilder
    {
        private const string RigSpaceSemantic = "unity_rig_v1";
        private const string RigAxisModePreserveRestBoneAxes = "preserve_rest_bone_axes";
        private const string StaticMeshFallbackBoneId = "bone-__blendersync_static_mesh__";

        public sealed class BuildResult
        {
            public bool success;
            public string operation;
            public string prefabPath;
            public string error;
            public int boneCount;
            public int materialCount;
        }

        public BuildResult BuildOrUpdate(RiggedObjectPayload rig)
        {
            var result = new BuildResult
            {
                success = false,
                operation = "error",
                prefabPath = null,
                error = null,
                boneCount = rig != null && rig.orderedBoneIds != null ? rig.orderedBoneIds.Length : 0,
                materialCount = rig != null && rig.materialRefs != null ? rig.materialRefs.Length : 0,
            };

#if UNITY_EDITOR
            try
            {
                if (rig == null)
                {
                    result.error = "rigged_object_missing";
                    return result;
                }
                if (!string.Equals(rig.spaceSemantic, RigSpaceSemantic, StringComparison.Ordinal))
                {
                    result.error = "rigged_object_space_semantic_unsupported";
                    return result;
                }
                if (string.IsNullOrWhiteSpace(rig.riggedObjectId))
                {
                    result.error = "rigged_object_id_missing";
                    return result;
                }
                var hasPrimaryMeshRef = !string.IsNullOrWhiteSpace(rig.meshRef);
                var hasMeshParts = rig.meshParts != null && rig.meshParts.Length > 0;
                if (!hasPrimaryMeshRef && !hasMeshParts)
                {
                    result.error = "rigged_object_mesh_ref_missing";
                    return result;
                }
                if (rig.skeleton == null || rig.skeleton.bones == null || rig.skeleton.bones.Length == 0)
                {
                    result.error = "rigged_object_skeleton_missing";
                    return result;
                }

                var meshParts = ResolveMeshParts(rig, out var resolveError);
                if (meshParts == null || meshParts.Length == 0)
                {
                    result.error = resolveError ?? "rigged_mesh_resolve_failed";
                    return result;
                }

                var prefabDir = Path.Combine("Assets", "TriSync", "Resources", "RiggedObjects").Replace('\\', '/');
                if (!AssetDatabase.IsValidFolder(prefabDir))
                {
                    Directory.CreateDirectory(Path.Combine(Directory.GetCurrentDirectory(), prefabDir.Replace("Assets/", "Assets" + Path.DirectorySeparatorChar)));
                    AssetDatabase.Refresh();
                }

                var safeName = AssetPathUtility.SanitizeFileName(string.IsNullOrWhiteSpace(rig.objectName) ? rig.riggedObjectId : rig.objectName, "rigged-object");
                var prefabPath = AssetPathUtility.BuildStableAssetPath(prefabDir, safeName, rig.riggedObjectId, ".prefab");
                result.prefabPath = prefabPath;
                result.operation = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath) == null ? "create" : "update";

                var root = new GameObject(string.IsNullOrWhiteSpace(rig.objectName) ? rig.riggedObjectId : rig.objectName);
                try
                {
                    ApplyUnityReadyLocalTransform(root.transform, rig.rigRootLocalPosition, rig.rigRootLocalRotation, rig.rigRootLocalScale);

                    var armatureNode = new GameObject(DeriveArmatureNodeName(rig));
                    armatureNode.transform.SetParent(root.transform, false);
                    ApplyUnityReadyLocalTransform(armatureNode.transform, rig.armatureLocalPosition, rig.armatureLocalRotation, rig.armatureLocalScale);

                    var boneMap = BuildBoneHierarchy(rig, armatureNode.transform, out var buildError);
                    if (boneMap == null)
                    {
                        result.error = buildError ?? "rigged_bone_hierarchy_build_failed";
                        return result;
                    }

                    Transform rootBone = null;
                    if (!string.IsNullOrWhiteSpace(rig.rootBoneId))
                    {
                        if (!boneMap.TryGetValue(rig.rootBoneId, out rootBone) || rootBone == null)
                        {
                            result.error = "rigged_root_bone_missing";
                            return result;
                        }
                    }

                    foreach (var part in meshParts)
                    {
                        var renderNodeName = string.IsNullOrWhiteSpace(part.objectName) ? DeriveRenderNodeName(rig) : part.objectName;
                        var renderNode = new GameObject(renderNodeName);
                        renderNode.transform.SetParent(root.transform, false);
                        ApplyUnityReadyLocalTransform(renderNode.transform, part.localPosition, part.localRotation, part.localScale);
                        renderNode.SetActive(!IsHidden(part.visibilityState));

                        var smr = renderNode.AddComponent<SkinnedMeshRenderer>();
                        smr.sharedMaterials = part.materials ?? Array.Empty<Material>();
                        smr.bones = BuildPartBones(part.orderedBoneIds, rig, boneMap, renderNode.transform, out buildError);
                        if (smr.bones == null)
                        {
                            result.error = buildError ?? "rigged_part_ordered_bones_build_failed";
                            return result;
                        }
                        smr.sharedMesh = BuildRendererMesh(part.mesh, smr.bones, renderNode.transform);
                        if (rootBone != null)
                            smr.rootBone = rootBone;
                        SkinnedMeshBoundsUtility.ApplySharedMeshBounds(smr);
                    }

                    var savedPrefab = PrefabUtility.SaveAsPrefabAsset(root, prefabPath);
                    if (savedPrefab == null)
                    {
                        result.error = "rigged_prefab_save_failed";
                        return result;
                    }
                    AssetDatabase.SaveAssets();
                    AssetDatabase.Refresh();
                    result.success = true;
                    return result;
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(root);
                }
            }
            catch (Exception ex)
            {
                result.error = ex.Message;
                return result;
            }
#else
            result.error = "rigged_prefab_builder_requires_editor";
            return result;
#endif
        }

#if UNITY_EDITOR
        private sealed class ResolvedMeshPart
        {
            public string objectName;
            public string visibilityState;
            public Mesh mesh;
            public Material[] materials;
            public string[] orderedBoneIds;
            public float[] localPosition;
            public float[] localRotation;
            public float[] localScale;
        }

        private static ResolvedMeshPart[] ResolveMeshParts(RiggedObjectPayload rig, out string error)
        {
            error = null;
            var resolver = new ResourceResolver();
            var parts = new System.Collections.Generic.List<ResolvedMeshPart>();
            var sourceParts = rig.meshParts != null && rig.meshParts.Length > 0
                ? rig.meshParts
                : new[]
                {
                    new RiggedMeshPartPayload
                    {
                        objectName = rig.objectName,
                        visibilityState = rig.visibilityState,
                        meshRef = rig.meshRef,
                        materialRefs = rig.materialRefs,
                        orderedBoneIds = rig.orderedBoneIds,
                        localPosition = Array.Empty<float>(),
                        localRotation = Array.Empty<float>(),
                        localScale = Array.Empty<float>(),
                    }
                };

            foreach (var sourcePart in sourceParts)
            {
                if (sourcePart == null || string.IsNullOrWhiteSpace(sourcePart.meshRef))
                    continue;
                if (!resolver.TryResolveMesh(sourcePart.meshRef, out var mesh, out var meshError) || mesh == null)
                {
                    error = meshError ?? "mesh_asset_not_mapped";
                    return null;
                }
                var materials = ResolveMaterials(sourcePart.materialRefs, out error);
                if (error != null)
                    return null;
                parts.Add(new ResolvedMeshPart
                {
                    objectName = sourcePart.objectName,
                    visibilityState = sourcePart.visibilityState,
                    mesh = mesh,
                    materials = materials,
                    orderedBoneIds = sourcePart.orderedBoneIds,
                    localPosition = sourcePart.localPosition,
                    localRotation = sourcePart.localRotation,
                    localScale = sourcePart.localScale,
                });
            }

            return parts.ToArray();
        }

        private static Material[] ResolveMaterials(string[] materialRefs, out string error)
        {
            error = null;
            if (materialRefs == null || materialRefs.Length == 0)
            {
                var defaultMaterial = MaterialReferenceApplyService.ResolveDefaultMaterial();
                return defaultMaterial != null ? new[] { defaultMaterial } : Array.Empty<Material>();
            }

            var resolver = new ResourceResolver();
            var mats = new Material[materialRefs.Length];
            for (var i = 0; i < materialRefs.Length; i++)
            {
                var materialRef = materialRefs[i];
                if (string.IsNullOrWhiteSpace(materialRef))
                    continue;
                if (!resolver.TryResolveMaterial(materialRef, out var mat, out error) || mat == null)
                    return null;
                mats[i] = mat;
            }
            return mats;
        }

        private static Matrix4x4 ReadRowMajorMatrix(float[] rowMajor16, int startIndex)
        {
            var m = new Matrix4x4();
            m.m00 = rowMajor16[startIndex + 0];
            m.m01 = rowMajor16[startIndex + 1];
            m.m02 = rowMajor16[startIndex + 2];
            m.m03 = rowMajor16[startIndex + 3];
            m.m10 = rowMajor16[startIndex + 4];
            m.m11 = rowMajor16[startIndex + 5];
            m.m12 = rowMajor16[startIndex + 6];
            m.m13 = rowMajor16[startIndex + 7];
            m.m20 = rowMajor16[startIndex + 8];
            m.m21 = rowMajor16[startIndex + 9];
            m.m22 = rowMajor16[startIndex + 10];
            m.m23 = rowMajor16[startIndex + 11];
            m.m30 = rowMajor16[startIndex + 12];
            m.m31 = rowMajor16[startIndex + 13];
            m.m32 = rowMajor16[startIndex + 14];
            m.m33 = rowMajor16[startIndex + 15];
            return m;
        }

        private static System.Collections.Generic.Dictionary<string, Transform> BuildBoneHierarchy(RiggedObjectPayload rig, Transform prefabRoot, out string error)
        {
            error = null;
            var map = new System.Collections.Generic.Dictionary<string, Transform>(StringComparer.Ordinal);
            foreach (var bone in rig.skeleton.bones)
            {
                if (bone == null || string.IsNullOrWhiteSpace(bone.boneId))
                {
                    error = "rigged_bone_id_missing";
                    return null;
                }
                if (map.ContainsKey(bone.boneId))
                {
                    error = "rigged_bone_id_duplicate";
                    return null;
                }
                var go = new GameObject(SanitizeTransformName(string.IsNullOrWhiteSpace(bone.name) ? bone.boneId : bone.name));
                map[bone.boneId] = go.transform;
            }

            foreach (var bone in rig.skeleton.bones)
            {
                var t = map[bone.boneId];
                if (!string.IsNullOrWhiteSpace(bone.parentBoneId))
                {
                    if (!map.TryGetValue(bone.parentBoneId, out var parent))
                    {
                        error = "rigged_parent_bone_missing";
                        return null;
                    }
                    t.SetParent(parent, false);
                }
                else
                {
                    t.SetParent(prefabRoot, false);
                }

                ApplyUnityRigV1BoneLocal(t, bone, IsPreserveRestBoneAxes(rig));
            }

            foreach (var bone in rig.skeleton.bones)
            {
                if (bone == null || !bone.isLeaf)
                    continue;
                var boneName = string.IsNullOrWhiteSpace(bone.name) ? bone.boneId : bone.name;
                if (!string.IsNullOrWhiteSpace(boneName) && boneName.EndsWith("_end", StringComparison.OrdinalIgnoreCase))
                    continue;
                if (!map.TryGetValue(bone.boneId ?? string.Empty, out var parent) || parent == null)
                    continue;

                var endNode = new GameObject(SanitizeTransformName(boneName + "_end"));
                var endTransform = endNode.transform;
                endTransform.SetParent(parent, false);
                ApplyUnityRigV1TailLocal(endTransform, bone, IsPreserveRestBoneAxes(rig));
            }

            return map;
        }

        private static bool IsPreserveRestBoneAxes(RiggedObjectPayload rig)
        {
            return string.Equals(rig != null ? rig.rigAxisMode : null, RigAxisModePreserveRestBoneAxes, StringComparison.Ordinal);
        }

        private static void ApplyUnityRigV1BoneLocal(Transform transform, RiggedBonePayload bone, bool preserveRestBoneAxes)
        {
            if (transform == null || bone == null)
                return;

            var matrixValues = bone.restLocalMatrix != null && bone.restLocalMatrix.Length >= 16
                ? bone.restLocalMatrix
                : bone.localMatrix;
            if (matrixValues != null && matrixValues.Length >= 16)
            {
                var matrix = preserveRestBoneAxes
                    ? ReadRowMajorMatrix(matrixValues, 0)
                    : SceneSyncTransformMapper.MapBoneRowMajorMatrix(matrixValues, 0);
                if (preserveRestBoneAxes)
                    ApplyLocalMatrix(transform, matrix);
                else
                    transform.localPosition = matrix.GetColumn(3);
            }
            else
            {
                SceneSyncTransformMapper.ConvertBoneTransform(bone.restLocalPosition ?? bone.localPosition, null, null, out var position, out _, out _);
                transform.localPosition = position;
            }
            if (!preserveRestBoneAxes)
            {
                transform.localRotation = Quaternion.identity;
                transform.localScale = Vector3.one;
            }
        }

        private static void ApplyUnityRigV1TailLocal(Transform transform, RiggedBonePayload bone, bool preserveRestBoneAxes)
        {
            if (transform == null || bone == null)
                return;

            if (bone.tailLocalMatrix != null && bone.tailLocalMatrix.Length >= 16)
            {
                var matrix = preserveRestBoneAxes
                    ? ReadRowMajorMatrix(bone.tailLocalMatrix, 0)
                    : SceneSyncTransformMapper.MapBoneRowMajorMatrix(bone.tailLocalMatrix, 0);
                if (preserveRestBoneAxes)
                    ApplyLocalMatrix(transform, matrix);
                else
                    transform.localPosition = matrix.GetColumn(3);
            }
            else
            {
                SceneSyncTransformMapper.ConvertBoneTransform(bone.tailLocalPosition, null, null, out var position, out _, out _);
                transform.localPosition = position;
            }
            if (!preserveRestBoneAxes)
            {
                transform.localRotation = Quaternion.identity;
                transform.localScale = Vector3.one;
            }
        }

        private static void ApplyLocalMatrix(Transform transform, Matrix4x4 matrix)
        {
            transform.localPosition = matrix.GetColumn(3);
            var scale = ExtractScale(matrix);
            transform.localScale = scale;
            transform.localRotation = ExtractRotation(matrix, scale);
        }

        private static Vector3 ExtractScale(Matrix4x4 matrix)
        {
            return new Vector3(
                matrix.GetColumn(0).magnitude,
                matrix.GetColumn(1).magnitude,
                matrix.GetColumn(2).magnitude
            );
        }

        private static Quaternion ExtractRotation(Matrix4x4 matrix, Vector3 scale)
        {
            var right = matrix.GetColumn(0);
            var up = matrix.GetColumn(1);
            var forward = matrix.GetColumn(2);

            if (scale.x > 1e-6f) right /= scale.x;
            if (scale.y > 1e-6f) up /= scale.y;
            if (scale.z > 1e-6f) forward /= scale.z;

            if (forward.sqrMagnitude < 1e-8f || up.sqrMagnitude < 1e-8f)
                return Quaternion.identity;

            return Quaternion.LookRotation(forward.normalized, up.normalized);
        }

        private static void ApplyUnityReadyLocalTransform(Transform transform, float[] localPosition, float[] localRotation, float[] localScale)
        {
            if (transform == null)
                return;
            transform.localPosition = localPosition != null && localPosition.Length >= 3 ? new Vector3(localPosition[0], localPosition[1], localPosition[2]) : Vector3.zero;
            transform.localRotation = localRotation != null && localRotation.Length >= 4 ? new Quaternion(localRotation[0], localRotation[1], localRotation[2], localRotation[3]) : Quaternion.identity;
            transform.localScale = localScale != null && localScale.Length >= 3 ? new Vector3(localScale[0], localScale[1], localScale[2]) : Vector3.one;
        }

        private static string DeriveRenderNodeName(RiggedObjectPayload rig)
        {
            var objectName = string.IsNullOrWhiteSpace(rig?.objectName) ? rig?.riggedObjectId : rig.objectName;
            if (string.IsNullOrWhiteSpace(objectName))
                return "Mesh";
            return objectName;
        }

        private static string DeriveArmatureNodeName(RiggedObjectPayload rig)
        {
            var sourcePath = rig != null ? rig.sourceArmatureObjectPath : null;
            if (!string.IsNullOrWhiteSpace(sourcePath))
            {
                var normalized = sourcePath.Replace('\\', '/').Trim();
                var lastSlash = normalized.LastIndexOf('/');
                var tail = lastSlash >= 0 ? normalized.Substring(lastSlash + 1) : normalized;
                if (!string.IsNullOrWhiteSpace(tail))
                    return SanitizeTransformName(tail);
            }

            var objectName = rig != null ? rig.objectName : null;
            if (!string.IsNullOrWhiteSpace(objectName))
                return SanitizeTransformName(objectName);
            return "Armature";
        }

        private static Transform[] BuildPartBones(string[] orderedBoneIds, RiggedObjectPayload rig, System.Collections.Generic.Dictionary<string, Transform> boneMap, Transform rendererTransform, out string error)
        {
            error = null;
            var ids = orderedBoneIds != null && orderedBoneIds.Length > 0 ? orderedBoneIds : rig.orderedBoneIds;
            if (ids == null)
                return Array.Empty<Transform>();
            var bones = new Transform[ids.Length];
            for (var i = 0; i < ids.Length; i++)
            {
                var boneId = ids[i];
                if (string.Equals(boneId, StaticMeshFallbackBoneId, StringComparison.Ordinal))
                {
                    bones[i] = rendererTransform;
                    continue;
                }
                if (!boneMap.TryGetValue(boneId ?? string.Empty, out var t) || t == null)
                {
                    error = "rigged_ordered_bone_missing";
                    return null;
                }
                bones[i] = t;
            }
            return bones;
        }

        private static Mesh BuildRendererMesh(Mesh sourceMesh, Transform[] bones, Transform rendererTransform)
        {
            if (sourceMesh == null)
                return null;
            if (bones == null || bones.Length == 0)
                return sourceMesh;

            var mesh = sourceMesh;
            var bindposes = new Matrix4x4[bones.Length];
            var meshLocalToWorld = rendererTransform != null ? rendererTransform.localToWorldMatrix : Matrix4x4.identity;
            for (var i = 0; i < bones.Length; i++)
            {
                var bone = bones[i];
                bindposes[i] = bone != null
                    ? bone.worldToLocalMatrix * meshLocalToWorld
                    : Matrix4x4.identity;
            }
            mesh.bindposes = bindposes;
            EditorUtility.SetDirty(mesh);
            return mesh;
        }

        private static string SanitizeTransformName(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return "Bone";
            return value.Replace('/', '_').Replace('\\', '_').Trim();
        }

        private static bool IsHidden(string visibilityState)
        {
            return string.Equals((visibilityState ?? string.Empty).Trim(), "hidden", StringComparison.OrdinalIgnoreCase);
        }
#endif
    }
}
