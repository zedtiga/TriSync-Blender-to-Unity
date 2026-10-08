using BlenderSyncVNext.APT;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class ResourceResolver
    {
        private readonly AptRepository _repository = new AptRepository();

        public bool TryResolveMesh(string assetId, out Mesh mesh, out string error)
            => TryResolveAsset(assetId, "mesh", out mesh, out error);

        public bool TryResolveMaterial(string assetId, out Material material, out string error)
            => TryResolveAsset(assetId, "material", out material, out error);

        public bool TryResolveTexture(string assetId, out Texture2D texture, out string error)
            => TryResolveAsset(assetId, "texture", out texture, out error);

        private bool TryResolveAsset<T>(string assetId, string expectedType, out T asset, out string error) where T : UnityEngine.Object
        {
            asset = null;
            error = null;

            var normalizedAssetId = (assetId ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(normalizedAssetId))
            {
                error = "assetId is missing";
                return false;
            }

            if (ResourceRuntimeMapping.TryGet(normalizedAssetId, out var cached) && cached != null && cached.state == "resolved" && cached.loadedObject is T cachedTyped && cachedTyped != null)
            {
                asset = cachedTyped;
                return true;
            }

            var db = _repository.Load();
            var rec = _repository.FindByAssetId(db, normalizedAssetId);
            if (rec == null || rec.target == null)
            {
                ResourceRuntimeMapping.MarkMissing(normalizedAssetId);
                error = expectedType + "_asset_not_mapped";
                return false;
            }

            if (!string.IsNullOrWhiteSpace(rec.target.resourceType) && !string.Equals(rec.target.resourceType.Trim(), expectedType, System.StringComparison.OrdinalIgnoreCase))
            {
                error = expectedType + "_resource_type_mismatch";
                return false;
            }

#if UNITY_EDITOR
            T loaded = null;
            if (!string.IsNullOrWhiteSpace(rec.target.assetGuid))
            {
                var pathFromGuid = AssetDatabase.GUIDToAssetPath(rec.target.assetGuid);
                if (!string.IsNullOrWhiteSpace(pathFromGuid))
                    loaded = AssetDatabase.LoadAssetAtPath<T>(pathFromGuid);
            }

            if (loaded == null && !string.IsNullOrWhiteSpace(rec.target.unityAssetPath))
                loaded = AssetDatabase.LoadAssetAtPath<T>(rec.target.unityAssetPath);

            if (loaded == null)
            {
                ResourceRuntimeMapping.MarkMissing(normalizedAssetId);
                error = expectedType + "_asset_not_found";
                return false;
            }

            ResourceRuntimeMapping.PutResolved(normalizedAssetId, rec.target.resourceType, rec.target.assetGuid, rec.target.unityAssetPath, rec.sourceFingerprint, loaded);
            asset = loaded;
            return true;
#else
            error = expectedType + "_resolve_requires_editor";
            return false;
#endif
        }
    }
}
