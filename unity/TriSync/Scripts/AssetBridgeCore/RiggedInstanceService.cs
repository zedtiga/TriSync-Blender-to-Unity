using System;
using BlenderSyncVNext.Protocol;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine.SceneManagement;
#endif

namespace BlenderSyncVNext.AssetBridgeCore
{
    public sealed class RiggedInstanceService
    {
#if UNITY_EDITOR
        public GameObject CreateManagedInstance(RiggedObjectPayload rig, string prefabPath, out string error)
        {
            error = null;
            var instance = InstantiatePrefab(rig, prefabPath, null, out error);
            MarkInstanceSceneDirty(instance);
            return instance;
        }

        public GameObject ReplaceManagedInstance(RiggedObjectPayload rig, string prefabPath, RiggedManagedInstanceRecord managedInstance, out string error)
        {
            error = null;
            GameObject previous = null;
            Vector3 position = Vector3.zero;
            Quaternion rotation = Quaternion.identity;
            Vector3 scale = Vector3.one;
            Transform previousParent = null;
            var siblingIndex = -1;
            Scene targetScene = default;

            if (managedInstance != null && !string.IsNullOrWhiteSpace(managedInstance.sceneObjectId) && GlobalObjectId.TryParse(managedInstance.sceneObjectId, out var gid))
            {
                previous = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid) as GameObject;
            }

            if (previous != null)
            {
                targetScene = previous.scene;
                previousParent = previous.transform.parent;
                siblingIndex = previous.transform.GetSiblingIndex();
                if (!IsUnityRigV1(rig))
                {
                    position = previous.transform.position;
                    rotation = previous.transform.rotation;
                    scale = previous.transform.localScale;
                }
            }

            var instance = InstantiatePrefab(rig, prefabPath, targetScene.IsValid() ? targetScene : (Scene?)null, out error);
            if (instance == null)
                return null;

            var isUnityRigV1 = IsUnityRigV1(rig);
            if (previousParent != null)
                instance.transform.SetParent(previousParent, !isUnityRigV1);

            if (previous != null)
            {
                if (!isUnityRigV1)
                {
                    instance.transform.position = position;
                    instance.transform.rotation = rotation;
                    instance.transform.localScale = scale;
                }
                UnityEngine.Object.DestroyImmediate(previous);
                if (siblingIndex >= 0)
                    instance.transform.SetSiblingIndex(siblingIndex);
            }

            ApplyInstanceRootTransform(instance, rig);
            MarkInstanceSceneDirty(instance);
            return instance;
        }

        private static GameObject InstantiatePrefab(RiggedObjectPayload rig, string prefabPath, Scene? scene, out string error)
        {
            error = null;
            if (rig == null)
            {
                error = "rigged_object_missing";
                return null;
            }
            if (string.IsNullOrWhiteSpace(prefabPath))
            {
                error = "rigged_prefab_path_missing";
                return null;
            }

            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            if (prefab == null)
            {
                error = "rigged_prefab_not_found";
                return null;
            }

            GameObject instance;
            if (scene.HasValue && scene.Value.IsValid())
                instance = PrefabUtility.InstantiatePrefab(prefab, scene.Value) as GameObject;
            else
                instance = PrefabUtility.InstantiatePrefab(prefab) as GameObject;
            if (instance == null)
            {
                error = "rigged_prefab_instantiate_failed";
                return null;
            }

            instance.name = string.IsNullOrWhiteSpace(rig.objectName) ? rig.riggedObjectId : rig.objectName;
            ApplyInstanceRootTransform(instance, rig);
            return instance;
        }

        private static void MarkInstanceSceneDirty(GameObject instance)
        {
            if (instance == null || !instance.scene.IsValid())
                return;

            EditorSceneManager.MarkSceneDirty(instance.scene);
        }

        private static bool IsUnityRigV1(RiggedObjectPayload rig)
        {
            return string.Equals(rig?.spaceSemantic, "unity_rig_v1", StringComparison.Ordinal);
        }

        private static void ApplyInstanceRootTransform(GameObject instance, RiggedObjectPayload rig)
        {
            if (instance == null || !IsUnityRigV1(rig))
                return;

            instance.transform.localPosition = ToVector3(rig.rigRootLocalPosition, Vector3.zero);
            instance.transform.localRotation = ToQuaternion(rig.rigRootLocalRotation, Quaternion.identity);
            instance.transform.localScale = ToVector3(rig.rigRootLocalScale, Vector3.one);
            instance.SetActive(!IsHidden(rig.visibilityState));
        }

        private static bool IsHidden(string visibilityState)
        {
            return string.Equals((visibilityState ?? string.Empty).Trim(), "hidden", StringComparison.OrdinalIgnoreCase);
        }

        private static Vector3 ToVector3(float[] values, Vector3 fallback)
        {
            return values != null && values.Length >= 3
                ? new Vector3(values[0], values[1], values[2])
                : fallback;
        }

        private static Quaternion ToQuaternion(float[] values, Quaternion fallback)
        {
            return values != null && values.Length >= 4
                ? new Quaternion(values[0], values[1], values[2], values[3])
                : fallback;
        }
#endif
    }
}
