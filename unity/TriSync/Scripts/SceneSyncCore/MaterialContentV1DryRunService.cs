using System;
using System.Linq;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class MaterialContentV1DryRunService
    {
        public bool TryDryRun(SceneSyncMaterialContentV1Message msg, out string summary, out string error)
        {
            summary = null;
            error = null;

            if (msg == null)
            {
                error = "message_missing";
                return false;
            }
            if (msg.type != "scene_sync.material_content_v1" || msg.schema != "material_content_v1")
            {
                error = "invalid_material_content_v1_payload";
                return false;
            }
            if (string.IsNullOrWhiteSpace(msg.materialRef))
            {
                error = "material_ref_missing";
                return false;
            }

            var materialName = msg.source != null && !string.IsNullOrWhiteSpace(msg.source.name)
                ? msg.source.name
                : "<unnamed>";
            var shaderTarget = msg.shader != null && !string.IsNullOrWhiteSpace(msg.shader.target)
                ? msg.shader.target
                : "<none>";
            var shaderPolicy = msg.shader != null && !string.IsNullOrWhiteSpace(msg.shader.policy)
                ? msg.shader.policy
                : "<none>";
            var textureCount = CountTextures(msg.textures);
            var warningCount = msg.warnings != null ? msg.warnings.Length : 0;
            var contentHash = msg.fingerprint != null ? msg.fingerprint.contentHash : null;

            summary =
                $"materialRef={msg.materialRef} name={materialName} shader={shaderTarget} " +
                $"policy={shaderPolicy} textures={textureCount} warnings={warningCount} " +
                $"contentHash={(string.IsNullOrWhiteSpace(contentHash) ? "<none>" : contentHash)}";
            return true;
        }

        private static int CountTextures(MaterialContentV1Textures textures)
        {
            if (textures == null) return 0;
            var entries = new[]
            {
                textures.baseColor,
                textures.normal,
                textures.metallic,
                textures.roughness,
                textures.occlusion,
                textures.height,
                textures.alpha,
                textures.emission,
            };
            return entries.Count(entry => entry != null && !string.IsNullOrWhiteSpace(entry.textureRef));
        }
    }
}
