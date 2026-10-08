using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
using UnityEngine.Rendering;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    // Product asset paths are unversioned; protocol and implementation names
    // such as MaterialContentV1 retain their independent contract versions.
    internal static class GeneratedResourcePaths
    {
        internal const string MaterialsRootAssetPath = "Assets/TriSync/Resources/Materials";
        internal const string TexturesRootAssetPath = "Assets/TriSync/Resources/Textures";
        internal const string DefaultMaterialAssetPath = MaterialsRootAssetPath + "/TriSyncDefault.mat";
    }

    internal static class MaterialColorSpaceUtility
    {
        // MaterialContentV1 carries Blender NodeSocketColor values in scene-linear space.
        internal static Color ToUnityBaseColor(Color blenderLinearColor)
        {
#if UNITY_EDITOR
            return PlayerSettings.colorSpace == ColorSpace.Linear ? blenderLinearColor.gamma : blenderLinearColor;
#else
            return blenderLinearColor;
#endif
        }
    }

    public sealed class MaterialContentV1ApplyService
    {
        private const string BlenderSyncLitShaderName = "TriSync/Principled Lit URP";
        private const string UrpLitShaderName = "Universal Render Pipeline/Lit";
        private const string ApplyFingerprintVersion = "material_content_v1_apply_v4";
        private readonly AptRepository _repository = new AptRepository();

        public bool TryApply(SceneSyncMaterialContentV1Message msg, out string summary, out string error)
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

#if UNITY_EDITOR
            var totalSw = System.Diagnostics.Stopwatch.StartNew();
            var materialName = msg.source != null && !string.IsNullOrWhiteSpace(msg.source.name) ? msg.source.name : msg.materialRef;
            var desiredAssetPath = BuildMaterialAssetPath(msg.materialRef, materialName);
            var assetPath = desiredAssetPath;
            var materialAssetName = MaterialAssetObjectName(assetPath, materialName);
            EnsureAssetFolder(Path.GetDirectoryName(assetPath));

            var material = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
            var renamedExistingMaterial = EnsureMaterialObjectName(material, materialAssetName);
            var appliedFingerprint = BuildAppliedFingerprint(msg);
            var priorTextureImports = LoadPriorTextureImportSlots(msg.materialRef);
            var currentTextureImports = BuildTextureImportFingerprints(msg);
            if (!ValidateImportableTextureSources(msg.textures, out var missingTextureSources))
            {
                error = "material_texture_source_missing:" + missingTextureSources;
                return false;
            }
            if (material != null && IsMaterialApplyUpToDate(msg.materialRef, assetPath, appliedFingerprint, material, currentTextureImports))
            {
                if (renamedExistingMaterial)
                    AssetDatabase.SaveAssets();
                var skipGuid = AssetDatabase.AssetPathToGUID(assetPath);
                RepairTextureRegistryRecords(msg, material, currentTextureImports);
                ResourceRuntimeMapping.PutResolved(msg.materialRef, "material", skipGuid, assetPath, appliedFingerprint, material);
                TryDeleteTextureStagingSources(msg.textures);
                summary = $"skip unchanged materialRef={msg.materialRef} name={material.name} assetPath={assetPath} guid={(string.IsNullOrWhiteSpace(skipGuid) ? "<none>" : skipGuid)}";
                return true;
            }

            LogPayloadWarnings(msg);

            var operation = material == null ? "create" : "update";
            var shader = ResolveShaderForMaterialContent(msg, materialName, out var shaderPolicyInfo);
            if (material == null)
            {
                material = new Material(shader) { name = materialAssetName };
                ApplyNumericProperties(material, msg, priorTextureImports, currentTextureImports);
                FinalizeMaterialState(material);
                AssetDatabase.CreateAsset(material, assetPath);
            }
            else
            {
                if (shader != null && material.shader != shader)
                    material.shader = shader;
                material.name = materialAssetName;
                ApplyNumericProperties(material, msg, priorTextureImports, currentTextureImports);
                FinalizeMaterialState(material);
                EditorUtility.SetDirty(material);
            }

            var saveStart = totalSw.Elapsed.TotalMilliseconds;
            AssetDatabase.SaveAssets();
            RefreshMaterialViews(material);
            var saveMs = totalSw.Elapsed.TotalMilliseconds - saveStart;
            material = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
            if (material == null)
            {
                error = "material_asset_save_failed";
                return false;
            }
            var guid = AssetDatabase.AssetPathToGUID(assetPath);
            if (string.IsNullOrWhiteSpace(guid))
            {
                error = "material_asset_guid_missing";
                return false;
            }
            var appliedTextureImports = BuildAppliedTextureImportFingerprints(material, currentTextureImports);
            if (TryGetMissingAppliedTextureImports(material, currentTextureImports, appliedTextureImports, out var missingTextureSlots))
            {
                error = "material_texture_import_failed:" + missingTextureSlots;
                return false;
            }

            UpsertRegistry(msg, material, assetPath, guid, appliedFingerprint, appliedTextureImports);
            ResourceRuntimeMapping.PutResolved(msg.materialRef, "material", guid, assetPath, appliedFingerprint, material);

            summary = $"{operation} materialRef={msg.materialRef} name={material.name} sourceName={materialName} assetPath={assetPath} guid={(string.IsNullOrWhiteSpace(guid) ? "<none>" : guid)} shaderPolicy={shaderPolicyInfo} saveMs={saveMs:F2} totalMs={totalSw.Elapsed.TotalMilliseconds:F2}";
            return true;
#else
            error = "material_content_v1_apply_requires_editor";
            return false;
#endif
        }

#if UNITY_EDITOR
        private static Shader ResolveUrpLitShader()
        {
            return Shader.Find(UrpLitShaderName)
                   ?? Shader.Find("Standard")
                   ?? Shader.Find("Sprites/Default");
        }

        private static Shader ResolveShaderForMaterialContent(SceneSyncMaterialContentV1Message msg, string materialName, out string shaderPolicyInfo)
        {
            var blenderSyncLit = Shader.Find(BlenderSyncLitShaderName);
            if (blenderSyncLit != null)
            {
                shaderPolicyInfo = "shader=blendersync_principled_lit_urp source=native_urp_lit_copy";
                return blenderSyncLit;
            }

            shaderPolicyInfo = "shader=urp_lit_fallback reason=blendersync_native_lit_copy_missing";
            BlenderSyncLog.Warn(
                "Material",
                "shader_fallback",
                "The TriSync shader was unavailable; using the URP Lit fallback.",
                new Dictionary<string, object>
                {
                    { "materialRef", msg?.materialRef },
                    { "missingShader", BlenderSyncLitShaderName },
                });
            return ResolveUrpLitShader();
        }

        private static void FinalizeMaterialState(Material material)
        {
            if (material == null)
                return;
            ApplyRequiredPasses(material);
            ApplyReceiveShadowsKeyword(material);
            ApplyMotionVectorOptions(material);
            EditorUtility.SetDirty(material);
        }

        private static void ApplyRequiredPasses(Material material)
        {
            material.SetShaderPassEnabled("ForwardLit", true);
            material.SetShaderPassEnabled("Meta", true);
            material.SetShaderPassEnabled("ShadowCaster", IsOpaqueSurface(material));
        }

        private static void ApplyReceiveShadowsKeyword(Material material)
        {
            var receiveShadows = !material.HasProperty("_ReceiveShadows") || material.GetFloat("_ReceiveShadows") >= 0.5f;
            if (receiveShadows) material.DisableKeyword("_RECEIVE_SHADOWS_OFF");
            else material.EnableKeyword("_RECEIVE_SHADOWS_OFF");
        }

        private static void ApplyMotionVectorOptions(Material material)
        {
            if (material.HasProperty("_AddPrecomputedVelocity"))
            {
                var enabled = material.GetFloat("_AddPrecomputedVelocity") != 0.0f;
                if (enabled) material.EnableKeyword("_ADD_PRECOMPUTED_VELOCITY");
                else material.DisableKeyword("_ADD_PRECOMPUTED_VELOCITY");
                material.SetShaderPassEnabled("MotionVectors", enabled);
            }

            if (material.HasProperty("_XRMotionVectorsPass"))
            {
                var enabled = material.GetFloat("_XRMotionVectorsPass") != 0.0f;
                material.SetShaderPassEnabled("XRMotionVectors", enabled);
            }
        }

        private static bool IsOpaqueSurface(Material material)
        {
            return !material.HasProperty("_Surface") || material.GetFloat("_Surface") < 0.5f;
        }

        private static void LogPayloadWarnings(SceneSyncMaterialContentV1Message msg)
        {
            if (msg == null || msg.warnings == null || msg.warnings.Length == 0)
                return;

            var informationalCount = 0;
            var warningCount = 0;
            MaterialContentV1Warning firstInformational = null;
            MaterialContentV1Warning firstWarning = null;
            foreach (var warning in msg.warnings)
            {
                if (warning == null)
                    continue;
                if (IsInformationalPayloadWarning(warning))
                {
                    informationalCount++;
                    if (firstInformational == null)
                        firstInformational = warning;
                }
                else
                {
                    warningCount++;
                    if (firstWarning == null)
                        firstWarning = warning;
                }
            }

            if (informationalCount > 0)
                LogPayloadWarningSummary(msg.materialRef, "payload_information", informationalCount, firstInformational, false);
            if (warningCount > 0)
                LogPayloadWarningSummary(msg.materialRef, "payload_warning", warningCount, firstWarning, true);
        }

        private static void LogPayloadWarningSummary(
            string materialRef,
            string eventName,
            int count,
            MaterialContentV1Warning first,
            bool warning)
        {
            var fields = new Dictionary<string, object>
            {
                { "materialRef", materialRef },
                { "count", count },
                { "firstCode", first?.code },
                { "firstSlot", first?.slot },
            };
            var summary = first != null && !string.IsNullOrWhiteSpace(first.message)
                ? first.message
                : $"Material payload reported {count} item(s).";
            if (warning)
                BlenderSyncLog.Warn("Material", eventName, summary, fields);
            else
                BlenderSyncLog.Info("Material", eventName, summary, fields);
        }

        private static bool IsInformationalPayloadWarning(MaterialContentV1Warning warning)
        {
            var code = warning != null ? warning.code : null;
            if (string.IsNullOrWhiteSpace(code))
                return false;
            return string.Equals(code, "image_packed_exported", StringComparison.OrdinalIgnoreCase)
                   || string.Equals(code, "image_buffer_exported", StringComparison.OrdinalIgnoreCase);
        }

        private static void RefreshMaterialViews(Material material)
        {
            if (material == null)
                return;
            EditorUtility.SetDirty(material);
            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            EditorApplication.QueuePlayerLoopUpdate();
        }

        private bool IsMaterialApplyUpToDate(string materialRef, string assetPath, string appliedFingerprint, Material material, Dictionary<string, string> requestedTextureImports)
        {
            if (string.IsNullOrWhiteSpace(materialRef) || string.IsNullOrWhiteSpace(assetPath) || string.IsNullOrWhiteSpace(appliedFingerprint))
                return false;
            var db = _repository.Load();
            var record = _repository.FindByAssetId(db, materialRef);
            if (record == null
                || !PathsEqual(record.target != null ? record.target.unityAssetPath : null, assetPath)
                || !string.Equals(record.sourceFingerprint, appliedFingerprint, StringComparison.Ordinal))
            {
                return false;
            }

            var appliedTextureImports = BuildAppliedTextureImportFingerprints(material, requestedTextureImports);
            return !TryGetMissingAppliedTextureImports(material, requestedTextureImports, appliedTextureImports, out _)
                && TextureSlotsMatch(record.materialTextureSlots, appliedTextureImports);
        }

        private static void ApplyNumericProperties(
            Material material,
            SceneSyncMaterialContentV1Message msg,
            Dictionary<string, AptMaterialTextureSlotFingerprint> priorTextureImports,
            Dictionary<string, string> currentTextureImports)
        {
            if (material == null || msg == null)
                return;

            var props = msg.properties;
            var baseColor = ToColor(props != null ? props.baseColor : null, new Color(0.8f, 0.8f, 0.8f, 1f));
            if (props != null && props.alpha > 0f)
                baseColor.a = props.alpha;

            var unityBaseColor = MaterialColorSpaceUtility.ToUnityBaseColor(baseColor);
            if (material.HasProperty("_BSG_BaseColor")) material.SetColor("_BSG_BaseColor", unityBaseColor);
            else if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor", unityBaseColor);
            else if (material.HasProperty("_Color")) material.SetColor("_Color", unityBaseColor);

            var baseMapAssigned = ApplyBaseColorTexture(material, msg.textures != null ? msg.textures.baseColor : null, priorTextureImports, currentTextureImports);
            if (baseMapAssigned)
            {
                var textureTint = new Color(1f, 1f, 1f, baseColor.a);
                if (material.HasProperty("_BSG_BaseColor")) material.SetColor("_BSG_BaseColor", textureTint);
            }

            var metallic = props != null ? props.metallic : 0f;
            if (material.HasProperty("_BSG_Metallic")) material.SetFloat("_BSG_Metallic", metallic);

            var hasRoughnessTexture = msg.textures != null && msg.textures.roughness != null && !string.IsNullOrWhiteSpace(msg.textures.roughness.sourcePath);
            var roughness = hasRoughnessTexture ? 1.0f : (props != null ? props.roughness : 0.5f);
            if (material.HasProperty("_BSG_Roughness")) material.SetFloat("_BSG_Roughness", roughness);

            var emissionColor = ToColor3(props != null ? props.emissionColor : null, Color.black);
            var emissionStrength = props != null ? Mathf.Max(0f, props.emissionStrength) : 0f;
            var finalEmissionColor = emissionColor * Mathf.Max(0f, emissionStrength);
            finalEmissionColor.a = 1f;
            if (finalEmissionColor.maxColorComponent <= 0.0001f && !HasEmissionTexture(msg))
                finalEmissionColor = Color.black;
            if (material.HasProperty("_BSG_EmissionColor")) material.SetColor("_BSG_EmissionColor", finalEmissionColor);
            var emissionMapAssigned = ApplyEmissionTexture(material, msg.textures != null ? msg.textures.emission : null, priorTextureImports, currentTextureImports);
            ApplyBsgEmissionEnabled(material, finalEmissionColor, emissionMapAssigned);
            ApplyEmission(material, finalEmissionColor, emissionMapAssigned);
            ApplyMetallicTexture(material, msg.textures != null ? msg.textures.metallic : null, priorTextureImports, currentTextureImports);
            ApplyRoughnessTexture(material, msg.textures != null ? msg.textures.roughness : null, priorTextureImports, currentTextureImports);
            ApplyNormalTexture(material, msg.textures != null ? msg.textures.normal : null, priorTextureImports, currentTextureImports);
            ApplyOcclusionTexture(material, msg.textures != null ? msg.textures.occlusion : null, priorTextureImports, currentTextureImports);
            ApplyHeightTexture(material, msg.textures != null ? msg.textures.height : null, priorTextureImports, currentTextureImports);
            ApplySurfaceMode(material, props);
        }

        private static bool HasEmissionTexture(SceneSyncMaterialContentV1Message msg)
        {
            return msg != null
                && msg.textures != null
                && msg.textures.emission != null
                && !string.IsNullOrWhiteSpace(msg.textures.emission.sourcePath);
        }

        private static bool ShouldUseAlphaTransparency(MaterialContentV1Properties props)
        {
            if (props == null || string.IsNullOrWhiteSpace(props.alphaModeHint))
                return false;
            var mode = props.alphaModeHint.Trim();
            return string.Equals(mode, "Cutout", StringComparison.OrdinalIgnoreCase)
                || string.Equals(mode, "Fade", StringComparison.OrdinalIgnoreCase)
                || string.Equals(mode, "Transparent", StringComparison.OrdinalIgnoreCase)
                || string.Equals(mode, "Blend", StringComparison.OrdinalIgnoreCase);
        }

        private static void ApplySurfaceMode(Material material, MaterialContentV1Properties props)
        {
            if (material == null)
                return;

            var mode = (props != null && !string.IsNullOrWhiteSpace(props.alphaModeHint) ? props.alphaModeHint : "Opaque").Trim();
            var isCutout = string.Equals(mode, "Cutout", StringComparison.OrdinalIgnoreCase);
            var isTransparent = string.Equals(mode, "Fade", StringComparison.OrdinalIgnoreCase)
                || string.Equals(mode, "Transparent", StringComparison.OrdinalIgnoreCase)
                || string.Equals(mode, "Blend", StringComparison.OrdinalIgnoreCase);
            var cutoff = props != null ? Mathf.Clamp01(props.alphaCutoff) : 0.5f;
            if (cutoff <= 0f)
                cutoff = 0.5f;

            if (material.HasProperty("_Cutoff")) material.SetFloat("_Cutoff", cutoff);
            if (material.HasProperty("_QueueOffset")) material.SetFloat("_QueueOffset", 0f);

            if (isCutout)
            {
                if (material.HasProperty("_Surface")) material.SetFloat("_Surface", 0f); // SurfaceType.Opaque
                if (material.HasProperty("_Blend")) material.SetFloat("_Blend", 0f); // BlendMode.Alpha, inert for opaque/cutout
                if (material.HasProperty("_BlendModePreserveSpecular")) material.SetFloat("_BlendModePreserveSpecular", 0f);
                if (material.HasProperty("_AlphaClip")) material.SetFloat("_AlphaClip", 1f);
                if (material.HasProperty("_AlphaToMask")) material.SetFloat("_AlphaToMask", 1f);
                SetMaterialSrcDstBlendProperties(material, BlendMode.One, BlendMode.Zero);
                SetMaterialZWriteProperty(material, true);
                material.SetOverrideTag("RenderType", "TransparentCutout");
                material.renderQueue = (int)RenderQueue.AlphaTest;
                material.EnableKeyword("_ALPHATEST_ON");
                material.DisableKeyword("_SURFACE_TYPE_TRANSPARENT");
                material.DisableKeyword("_ALPHAPREMULTIPLY_ON");
                material.DisableKeyword("_ALPHAMODULATE_ON");
                material.SetShaderPassEnabled("DepthOnly", true);
                return;
            }

            if (isTransparent)
            {
                if (material.HasProperty("_Surface")) material.SetFloat("_Surface", 1f); // SurfaceType.Transparent
                if (material.HasProperty("_Blend")) material.SetFloat("_Blend", 0f); // BlendMode.Alpha
                if (material.HasProperty("_BlendModePreserveSpecular")) material.SetFloat("_BlendModePreserveSpecular", 0f);
                if (material.HasProperty("_AlphaClip")) material.SetFloat("_AlphaClip", 0f);
                if (material.HasProperty("_AlphaToMask")) material.SetFloat("_AlphaToMask", 0f);
                SetMaterialSrcDstBlendProperties(material, BlendMode.SrcAlpha, BlendMode.OneMinusSrcAlpha, BlendMode.One, BlendMode.OneMinusSrcAlpha);
                SetMaterialZWriteProperty(material, false);
                material.SetOverrideTag("RenderType", "Transparent");
                material.renderQueue = (int)RenderQueue.Transparent;
                material.DisableKeyword("_ALPHATEST_ON");
                material.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
                material.DisableKeyword("_ALPHAPREMULTIPLY_ON");
                material.DisableKeyword("_ALPHAMODULATE_ON");
                material.SetShaderPassEnabled("DepthOnly", false);
                return;
            }

            if (material.HasProperty("_Surface")) material.SetFloat("_Surface", 0f); // SurfaceType.Opaque
            if (material.HasProperty("_Blend")) material.SetFloat("_Blend", 0f);
            if (material.HasProperty("_BlendModePreserveSpecular")) material.SetFloat("_BlendModePreserveSpecular", 0f);
            if (material.HasProperty("_AlphaClip")) material.SetFloat("_AlphaClip", 0f);
            if (material.HasProperty("_AlphaToMask")) material.SetFloat("_AlphaToMask", 0f);
            SetMaterialSrcDstBlendProperties(material, BlendMode.One, BlendMode.Zero);
            SetMaterialZWriteProperty(material, true);
            material.SetOverrideTag("RenderType", "Opaque");
            material.renderQueue = (int)RenderQueue.Geometry;
            material.DisableKeyword("_ALPHATEST_ON");
            material.DisableKeyword("_SURFACE_TYPE_TRANSPARENT");
            material.DisableKeyword("_ALPHAPREMULTIPLY_ON");
            material.DisableKeyword("_ALPHAMODULATE_ON");
            material.SetShaderPassEnabled("DepthOnly", true);
        }

        private static void SetMaterialSrcDstBlendProperties(Material material, BlendMode srcBlend, BlendMode dstBlend)
        {
            SetMaterialSrcDstBlendProperties(material, srcBlend, dstBlend, srcBlend, dstBlend);
        }

        private static void SetMaterialSrcDstBlendProperties(Material material, BlendMode srcBlendRgb, BlendMode dstBlendRgb, BlendMode srcBlendAlpha, BlendMode dstBlendAlpha)
        {
            if (material.HasProperty("_SrcBlend")) material.SetFloat("_SrcBlend", (float)srcBlendRgb);
            if (material.HasProperty("_DstBlend")) material.SetFloat("_DstBlend", (float)dstBlendRgb);
            if (material.HasProperty("_SrcBlendAlpha")) material.SetFloat("_SrcBlendAlpha", (float)srcBlendAlpha);
            if (material.HasProperty("_DstBlendAlpha")) material.SetFloat("_DstBlendAlpha", (float)dstBlendAlpha);
        }

        private static void SetMaterialZWriteProperty(Material material, bool zwrite)
        {
            if (material.HasProperty("_ZWrite")) material.SetFloat("_ZWrite", zwrite ? 1f : 0f);
        }

        private static Color ToColor(float[] values, Color fallback)
        {
            if (values == null || values.Length < 4)
                return fallback;
            return new Color(values[0], values[1], values[2], values[3]);
        }

        private static Color ToColor3(float[] values, Color fallback)
        {
            if (values == null || values.Length < 3)
                return fallback;
            return new Color(values[0], values[1], values[2], 1f);
        }

        private static void ApplyBsgEmissionEnabled(Material material, Color emissionColor, bool hasEmissionTexture)
        {
            if (material == null || !material.HasProperty("_BSG_EmissionEnabled"))
                return;
            var enabled = hasEmissionTexture || emissionColor.maxColorComponent > 0.0001f;
            material.SetFloat("_BSG_EmissionEnabled", enabled ? 1f : 0f);
        }

        private static void ApplyEmission(Material material, Color emissionColor, bool hasEmissionTexture)
        {
            if (material == null)
                return;

            var enabled = hasEmissionTexture || emissionColor.maxColorComponent > 0.0001f;
            if (material.HasProperty("_BSG_EmissionEnabled"))
                enabled = material.GetFloat("_BSG_EmissionEnabled") >= 0.5f;

            if (!enabled)
            {
                SetEmissionKeyword(material, false);
                material.globalIlluminationFlags = MaterialGlobalIlluminationFlags.EmissiveIsBlack;
                return;
            }

            material.globalIlluminationFlags = MaterialGlobalIlluminationFlags.BakedEmissive;
            SetEmissionKeyword(material, true);
        }

        private static void SetEmissionKeyword(Material material, bool enabled)
        {
            if (enabled)
            {
                material.EnableKeyword("_EMISSION");
            }
            else
            {
                material.DisableKeyword("_EMISSION");
            }
        }

        private static bool ApplyEmissionTexture(Material material, MaterialContentV1Texture texture, Dictionary<string, AptMaterialTextureSlotFingerprint> priorTextureImports, Dictionary<string, string> currentTextureImports)
        {
            if (material == null)
                return false;

            var hasBsgEmissionMap = material.HasProperty("_BSG_EmissionMap");
            if (!hasBsgEmissionMap)
                return false;

            if (texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
            {
                material.SetTexture("_BSG_EmissionMap", null);
                return false;
            }

            var tex = ImportOrLoadTextureForSlot(material, "_BSG_EmissionMap", "emission", texture, sRgb: true, isNormal: false, alphaIsTransparency: false, priorTextureImports, currentTextureImports);
            if (tex == null)
            {
                material.SetTexture("_BSG_EmissionMap", null);
                return false;
            }

            material.SetTexture("_BSG_EmissionMap", tex);
            return true;
        }

        private static bool ApplyBaseColorTexture(Material material, MaterialContentV1Texture texture, Dictionary<string, AptMaterialTextureSlotFingerprint> priorTextureImports, Dictionary<string, string> currentTextureImports)
        {
            if (material == null)
                return false;

            var hasBsgBaseMap = material.HasProperty("_BSG_BaseMap");
            if (!hasBsgBaseMap)
                return false;

            if (texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
            {
                material.SetTexture("_BSG_BaseMap", null);
                return false;
            }

            var tex = ImportOrLoadTextureForSlot(material, "_BSG_BaseMap", "baseColor", texture, sRgb: true, isNormal: false, alphaIsTransparency: false, priorTextureImports, currentTextureImports);
            if (tex == null)
            {
                material.SetTexture("_BSG_BaseMap", null);
                return false;
            }

            material.SetTexture("_BSG_BaseMap", tex);
            return true;
        }


        private static bool ApplyMetallicTexture(Material material, MaterialContentV1Texture texture, Dictionary<string, AptMaterialTextureSlotFingerprint> priorTextureImports, Dictionary<string, string> currentTextureImports)
        {
            if (material == null)
                return false;

            var hasBsgMetallicMap = material.HasProperty("_BSG_MetallicMap");
            if (!hasBsgMetallicMap)
                return false;

            if (texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
            {
                ClearMetallicTexture(material);
                return false;
            }

            var tex = ImportOrLoadTextureForSlot(material, "_BSG_MetallicMap", "metallic", texture, sRgb: false, isNormal: false, alphaIsTransparency: false, priorTextureImports, currentTextureImports);
            if (tex == null)
            {
                ClearMetallicTexture(material);
                return false;
            }

            material.SetTexture("_BSG_MetallicMap", tex);
            material.EnableKeyword("_BSG_METALLICMAP");
            if (material.HasProperty("_BSG_MetallicChannel"))
                material.SetFloat("_BSG_MetallicChannel", TextureChannelIndex(texture, 0.0f, "METALLIC"));
            return true;
        }

        private static bool ApplyRoughnessTexture(Material material, MaterialContentV1Texture texture, Dictionary<string, AptMaterialTextureSlotFingerprint> priorTextureImports, Dictionary<string, string> currentTextureImports)
        {
            if (material == null)
                return false;

            var hasBsgRoughnessMap = material.HasProperty("_BSG_RoughnessMap");
            if (!hasBsgRoughnessMap)
            {
                if (texture != null && !string.IsNullOrWhiteSpace(texture.sourcePath))
                    BlenderSyncLog.Warn(
                        "Material",
                        "roughness_texture_unsupported",
                        "The selected shader has no separate roughness-map input.",
                        new Dictionary<string, object>
                        {
                            { "shader", material.shader?.name },
                            { "textureRef", texture.textureRef },
                        });
                return false;
            }

            if (texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
            {
                ClearRoughnessTexture(material);
                return false;
            }

            var tex = ImportOrLoadTextureForSlot(material, "_BSG_RoughnessMap", "roughness", texture, sRgb: false, isNormal: false, alphaIsTransparency: false, priorTextureImports, currentTextureImports);
            if (tex == null)
            {
                ClearRoughnessTexture(material);
                return false;
            }

            material.SetTexture("_BSG_RoughnessMap", tex);
            material.EnableKeyword("_BSG_ROUGHNESSMAP");
            if (material.HasProperty("_BSG_RoughnessChannel"))
                material.SetFloat("_BSG_RoughnessChannel", TextureChannelIndex(texture, 0.0f, "ROUGHNESS"));
            return true;
        }

        private static bool ApplyOcclusionTexture(Material material, MaterialContentV1Texture texture, Dictionary<string, AptMaterialTextureSlotFingerprint> priorTextureImports, Dictionary<string, string> currentTextureImports)
        {
            if (material == null)
                return false;

            var hasBsgOcclusionMap = material.HasProperty("_BSG_OcclusionMap");
            if (!hasBsgOcclusionMap)
                return false;

            if (texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
            {
                ClearOcclusionTexture(material);
                return false;
            }

            var tex = ImportOrLoadTextureForSlot(material, "_BSG_OcclusionMap", "occlusion", texture, sRgb: false, isNormal: false, alphaIsTransparency: false, priorTextureImports, currentTextureImports);
            if (tex == null)
            {
                ClearOcclusionTexture(material);
                return false;
            }

            material.SetTexture("_BSG_OcclusionMap", tex);
            material.EnableKeyword("_BSG_OCCLUSIONMAP");
            if (material.HasProperty("_BSG_OcclusionChannel"))
                material.SetFloat("_BSG_OcclusionChannel", TextureChannelIndex(texture, 1.0f, "OCCLUSION"));
            if (material.HasProperty("_BSG_OcclusionStrength"))
                material.SetFloat("_BSG_OcclusionStrength", 1.0f);
            return true;
        }

        private static void ClearMetallicTexture(Material material)
        {
            material.SetTexture("_BSG_MetallicMap", null);
            material.DisableKeyword("_BSG_METALLICMAP");
            if (material.HasProperty("_BSG_MetallicChannel"))
                material.SetFloat("_BSG_MetallicChannel", 0.0f);
        }

        private static void ClearRoughnessTexture(Material material)
        {
            material.SetTexture("_BSG_RoughnessMap", null);
            material.DisableKeyword("_BSG_ROUGHNESSMAP");
            if (material.HasProperty("_BSG_RoughnessChannel"))
                material.SetFloat("_BSG_RoughnessChannel", 0.0f);
        }

        private static void ClearOcclusionTexture(Material material)
        {
            material.SetTexture("_BSG_OcclusionMap", null);
            material.DisableKeyword("_BSG_OCCLUSIONMAP");
            if (material.HasProperty("_BSG_OcclusionChannel"))
                material.SetFloat("_BSG_OcclusionChannel", 1.0f);
            if (material.HasProperty("_BSG_OcclusionStrength"))
                material.SetFloat("_BSG_OcclusionStrength", 1.0f);
        }

        private static float TextureChannelIndex(MaterialContentV1Texture texture, float defaultIndex, string role)
        {
            var sourceChannel = texture != null ? texture.sourceChannel : null;
            if (string.IsNullOrWhiteSpace(sourceChannel))
                return defaultIndex;
            switch (sourceChannel.Trim().ToUpperInvariant())
            {
                case "R":
                case "RED":
                    return 0.0f;
                case "G":
                case "GREEN":
                    return 1.0f;
                case "B":
                case "BLUE":
                    return 2.0f;
                case "A":
                case "ALPHA":
                    return 3.0f;
                default:
                    BlenderSyncLog.Warn(
                        "Material",
                        "texture_channel_unsupported",
                        "The requested texture channel is unsupported; using the slot default.",
                        new Dictionary<string, object>
                        {
                            { "role", role },
                            { "sourceChannel", sourceChannel },
                            { "textureRef", texture?.textureRef },
                        });
                    return defaultIndex;
            }
        }

        private static bool ApplyHeightTexture(Material material, MaterialContentV1Texture texture, Dictionary<string, AptMaterialTextureSlotFingerprint> priorTextureImports, Dictionary<string, string> currentTextureImports)
        {
            if (material == null)
                return false;

            var hasBsgParallaxMap = material.HasProperty("_BSG_ParallaxMap");
            if (!hasBsgParallaxMap)
                return false;

            if (texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
            {
                ClearHeightTexture(material);
                return false;
            }

            var tex = ImportOrLoadTextureForSlot(material, "_BSG_ParallaxMap", "height", texture, sRgb: false, isNormal: false, alphaIsTransparency: false, priorTextureImports, currentTextureImports);
            if (tex == null)
            {
                ClearHeightTexture(material);
                return false;
            }

            material.SetTexture("_BSG_ParallaxMap", tex);
            material.EnableKeyword("_BSG_PARALLAXMAP");
            if (material.HasProperty("_BSG_Parallax"))
                material.SetFloat("_BSG_Parallax", HeightScale(texture));
            if (material.HasProperty("_BSG_ParallaxChannel"))
                material.SetFloat("_BSG_ParallaxChannel", TextureChannelIndex(texture, 1.0f, "HEIGHT"));
            return true;
        }

        private static void ClearHeightTexture(Material material)
        {
            material.SetTexture("_BSG_ParallaxMap", null);
            material.DisableKeyword("_BSG_PARALLAXMAP");
            if (material.HasProperty("_BSG_Parallax"))
                material.SetFloat("_BSG_Parallax", 0.005f);
            if (material.HasProperty("_BSG_ParallaxChannel"))
                material.SetFloat("_BSG_ParallaxChannel", 1.0f);
        }

        private static float HeightScale(MaterialContentV1Texture texture)
        {
            var scale = texture != null && texture.heightScale > 0f ? texture.heightScale : 0.005f;
            return Mathf.Clamp(scale, 0.005f, 0.08f);
        }

        private static void ApplyNormalTexture(Material material, MaterialContentV1Texture texture, Dictionary<string, AptMaterialTextureSlotFingerprint> priorTextureImports, Dictionary<string, string> currentTextureImports)
        {
            if (material == null)
                return;

            var hasBsgNormalMap = material.HasProperty("_BSG_NormalMap");
            if (!hasBsgNormalMap)
                return;

            if (texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
            {
                ClearNormalTexture(material);
                return;
            }

            var normalSpace = string.IsNullOrWhiteSpace(texture.normalSpace) ? "TANGENT" : texture.normalSpace.Trim().ToUpperInvariant();
            if (normalSpace != "TANGENT" && normalSpace != "TANGENT_SPACE")
            {
                BlenderSyncLog.Warn(
                    "Material",
                    "normal_space_unsupported",
                    "The normal texture was skipped because only tangent-space normals are supported.",
                    new Dictionary<string, object>
                    {
                        { "normalSpace", texture.normalSpace },
                        { "textureRef", texture.textureRef },
                    });
                ClearNormalTexture(material);
                return;
            }

            var tex = ImportOrLoadTextureForSlot(material, "_BSG_NormalMap", "normal", texture, sRgb: false, isNormal: true, alphaIsTransparency: false, priorTextureImports, currentTextureImports);
            if (tex == null)
            {
                ClearNormalTexture(material);
                return;
            }

            var normalStrength = texture.normalStrength > 0f ? texture.normalStrength : 1f;
            material.SetTexture("_BSG_NormalMap", tex);
            if (material.HasProperty("_BSG_NormalScale"))
                material.SetFloat("_BSG_NormalScale", normalStrength);
            material.EnableKeyword("_NORMALMAP");
        }

        private static void ClearNormalTexture(Material material)
        {
            material.SetTexture("_BSG_NormalMap", null);
            material.DisableKeyword("_NORMALMAP");
            if (material.HasProperty("_BSG_NormalScale"))
                material.SetFloat("_BSG_NormalScale", 1.0f);
        }

        private static Texture2D ImportOrLoadTextureForSlot(
            Material material,
            string propertyName,
            string slot,
            MaterialContentV1Texture texture,
            bool sRgb,
            bool isNormal,
            bool alphaIsTransparency,
            Dictionary<string, AptMaterialTextureSlotFingerprint> priorTextureImports,
            Dictionary<string, string> currentTextureImports)
        {
            if (CanReuseExistingTexture(material, propertyName, slot, texture, sRgb, isNormal, alphaIsTransparency, priorTextureImports, currentTextureImports, out var existing))
            {
                TryDeleteGeneratedTextureStagingSource(texture);
                return existing;
            }
            return ImportOrLoadTexture(texture, sRgb, isNormal, alphaIsTransparency);
        }

        private static bool CanReuseExistingTexture(
            Material material,
            string propertyName,
            string slot,
            MaterialContentV1Texture requestedTexture,
            bool sRgb,
            bool isNormal,
            bool alphaIsTransparency,
            Dictionary<string, AptMaterialTextureSlotFingerprint> priorTextureImports,
            Dictionary<string, string> currentTextureImports,
            out Texture2D texture)
        {
            texture = null;
            if (material == null || !material.HasProperty(propertyName) || string.IsNullOrWhiteSpace(slot))
                return false;
            if (priorTextureImports == null || currentTextureImports == null)
                return false;
            if (!priorTextureImports.TryGetValue(slot, out var priorSlot) || priorSlot == null || string.IsNullOrWhiteSpace(priorSlot.importFingerprint))
                return false;
            if (!currentTextureImports.TryGetValue(slot, out var currentFingerprint) || !string.Equals(priorSlot.importFingerprint, currentFingerprint, StringComparison.Ordinal))
                return false;
            texture = material.GetTexture(propertyName) as Texture2D;
            if (texture != null)
                return true;

            var assetPath = priorSlot.assetPath;
            if (string.IsNullOrWhiteSpace(assetPath) || AssetDatabase.LoadAssetAtPath<Texture2D>(assetPath) == null)
                return false;
            if (!TextureImporterMatchesRole(assetPath, requestedTexture, sRgb, isNormal, alphaIsTransparency))
                return false;
            texture = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPath);
            return texture != null;
        }

        private static Texture2D ImportOrLoadTexture(MaterialContentV1Texture texture, bool sRgb, bool isNormal, bool alphaIsTransparency)
        {
            var sourcePath = texture != null ? texture.sourcePath : null;
            if (string.IsNullOrWhiteSpace(sourcePath) || !File.Exists(sourcePath))
                return null;

            var assetPath = TryConvertAbsolutePathToAssetPath(sourcePath);
            var usesSourceAsset = !string.IsNullOrWhiteSpace(assetPath);
            var generatedSource = IsGeneratedTextureSource(texture);
            if (usesSourceAsset)
                EnsureTextureAssetImported(assetPath, generatedSource);
            if (usesSourceAsset && !generatedSource && !TextureImporterMatchesRole(assetPath, texture, sRgb, isNormal, alphaIsTransparency))
            {
                var isolatedPath = BuildTextureAssetPath(texture);
                BlenderSyncLog.Info(
                    "Material",
                    "texture_role_isolated",
                    "Copied a texture so this material role can use independent import settings.",
                    new Dictionary<string, object>
                    {
                        { "usage", texture.usage },
                        { "textureRef", texture.textureRef },
                    });
                assetPath = isolatedPath;
                usesSourceAsset = false;
            }
            if (string.IsNullOrWhiteSpace(assetPath))
            {
                assetPath = BuildTextureAssetPath(texture);
                usesSourceAsset = false;
            }

            var copied = false;
            if (!usesSourceAsset)
            {
                EnsureAssetFolder(Path.GetDirectoryName(assetPath));
                try
                {
                    File.Copy(sourcePath, ToAbsoluteAssetPath(assetPath), overwrite: true);
                    copied = true;
                }
                catch (Exception exc)
                {
                    BlenderSyncLog.Warn(
                        "Material",
                        "texture_copy_failed",
                        exc.Message,
                        new Dictionary<string, object>
                        {
                            { "usage", texture?.usage },
                            { "textureRef", texture?.textureRef },
                        });
                    return null;
                }
            }
            else if (!File.Exists(ToAbsoluteAssetPath(assetPath)))
            {
                return null;
            }

            if (copied)
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport);
            ConfigureTextureImporter(assetPath, texture, sRgb, isNormal, alphaIsTransparency);
            var loaded = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPath);
            if (loaded == null)
            {
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport);
                loaded = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPath);
            }
            if (loaded == null)
                BlenderSyncLog.Warn(
                    "Material",
                    "texture_load_failed",
                    "Unity could not load the imported texture asset.",
                    new Dictionary<string, object>
                    {
                        { "usage", texture?.usage },
                        { "sourceKind", texture?.sourceKind },
                        { "textureRef", texture?.textureRef },
                    });
            else
                TryDeleteGeneratedTextureStagingSource(texture);
            return loaded;
        }

        private static bool IsGeneratedTextureSource(MaterialContentV1Texture texture)
        {
            var sourceKind = texture != null ? texture.sourceKind : null;
            if (string.IsNullOrWhiteSpace(sourceKind))
                return false;
            sourceKind = sourceKind.Trim();
            return string.Equals(sourceKind, "packed", StringComparison.OrdinalIgnoreCase)
                   || string.Equals(sourceKind, "buffer", StringComparison.OrdinalIgnoreCase);
        }

        private static void EnsureTextureAssetImported(string assetPath, bool force)
        {
            if (string.IsNullOrWhiteSpace(assetPath))
                return;
            if (!force && AssetImporter.GetAtPath(assetPath) != null && AssetDatabase.LoadAssetAtPath<Texture2D>(assetPath) != null)
                return;
            AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport);
        }

        private static void TryDeleteGeneratedTextureStagingSource(MaterialContentV1Texture texture)
        {
            if (string.IsNullOrWhiteSpace(texture?.sourcePath))
                return;
            if (!TryNormalizeTextureStagingPath(texture.sourcePath, out var fullSource))
                return;
            try
            {
                if (File.Exists(fullSource))
                {
                    File.Delete(fullSource);
                    BlenderSyncLog.Trace(
                        "Material",
                        "texture_staging_deleted",
                        () => "Deleted a generated texture staging file after import.",
                        () => new Dictionary<string, object> { { "sourceKind", texture.sourceKind } });
                }
            }
            catch (Exception exc)
            {
                BlenderSyncLog.Warn(
                    "Material",
                    "texture_staging_delete_failed",
                    exc.Message,
                    new Dictionary<string, object> { { "sourceKind", texture.sourceKind } });
            }
        }

        private static void TryDeleteTextureStagingSources(MaterialContentV1Textures textures)
        {
            if (textures == null)
                return;
            TryDeleteGeneratedTextureStagingSource(textures.baseColor);
            TryDeleteGeneratedTextureStagingSource(textures.normal);
            TryDeleteGeneratedTextureStagingSource(textures.metallic);
            TryDeleteGeneratedTextureStagingSource(textures.roughness);
            TryDeleteGeneratedTextureStagingSource(textures.occlusion);
            TryDeleteGeneratedTextureStagingSource(textures.height);
            TryDeleteGeneratedTextureStagingSource(textures.alpha);
            TryDeleteGeneratedTextureStagingSource(textures.emission);
        }

        private static bool TryNormalizeTextureStagingPath(string sourcePath, out string fullSource)
        {
            fullSource = null;
            if (string.IsNullOrWhiteSpace(sourcePath))
                return false;
            try
            {
                fullSource = Path.GetFullPath(sourcePath).Replace('\\', '/');
            }
            catch
            {
                return false;
            }
            var stagingRoot = Path.GetFullPath(Path.Combine(Directory.GetCurrentDirectory(), "Temp", "BlenderSyncVNext", "TextureExportsV1"))
                .Replace('\\', '/')
                .TrimEnd('/');
            return fullSource.StartsWith(stagingRoot + "/", StringComparison.OrdinalIgnoreCase);
        }

        private static string TryConvertAbsolutePathToAssetPath(string sourcePath)
        {
            var fullSource = Path.GetFullPath(sourcePath).Replace('\\', '/');
            var assetsRoot = Path.GetFullPath(Application.dataPath).Replace('\\', '/');
            if (!fullSource.StartsWith(assetsRoot + "/", StringComparison.OrdinalIgnoreCase))
                return null;
            return "Assets" + fullSource.Substring(assetsRoot.Length);
        }

        private static string ToAbsoluteAssetPath(string assetPath)
        {
            var normalized = (assetPath ?? string.Empty).Replace('\\', '/');
            if (!normalized.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                return normalized;
            var projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..")).Replace('\\', '/');
            return Path.Combine(projectRoot, normalized).Replace('\\', '/');
        }

        private static bool TextureImporterMatchesRole(string assetPath, MaterialContentV1Texture texture, bool sRgb, bool isNormal, bool alphaIsTransparency)
        {
            var importer = AssetImporter.GetAtPath(assetPath) as TextureImporter;
            if (importer == null)
                return true;
            var desiredType = isNormal ? TextureImporterType.NormalMap : TextureImporterType.Default;
            if (importer.textureType != desiredType)
                return false;
            if (importer.sRGBTexture != sRgb)
                return false;
            if (!isNormal && importer.alphaSource != TextureImporterAlphaSource.FromInput)
                return false;
            if (!isNormal && importer.alphaIsTransparency != alphaIsTransparency)
                return false;
            if (importer.wrapMode != DesiredWrapMode(texture))
                return false;
            if (importer.filterMode != DesiredFilterMode(texture))
                return false;
            return true;
        }

        private static string BuildTextureAssetPath(MaterialContentV1Texture texture)
        {
            var fileName = !string.IsNullOrWhiteSpace(texture.fileName)
                ? texture.fileName
                : Path.GetFileName(texture.sourcePath);
            var safeFileName = SanitizeFileName(fileName, "texture.png");
            var safeUsage = SanitizeFileName(!string.IsNullOrWhiteSpace(texture.usage) ? texture.usage : "default", "default");
            var readableFileName = BuildReadableTextureFileName(texture, safeFileName);
            return Path.Combine(GeneratedResourcePaths.TexturesRootAssetPath, safeUsage, readableFileName).Replace('\\', '/');
        }

        private static string BuildReadableTextureFileName(MaterialContentV1Texture texture, string safeFileName)
        {
            var cleanName = StripBlenderNumericSuffix(safeFileName);
            var ext = Path.GetExtension(cleanName);
            var hasKnownExt = IsKnownTextureExtension(ext);
            var stem = hasKnownExt ? Path.GetFileNameWithoutExtension(cleanName) : cleanName;
            stem = SanitizeFileName(stem, "texture").Trim('.', '_');
            if (string.IsNullOrWhiteSpace(stem))
                stem = "texture";
            var stableId = BuildTextureStableId(texture, safeFileName);
            stem = StripExistingStableIdSuffix(stem, stableId);
            return AssetPathUtility.BuildStableFileName(stem, stableId, hasKnownExt ? ext.ToLowerInvariant() : string.Empty);
        }

        private static string BuildTextureStableId(MaterialContentV1Texture texture, string safeFileName)
        {
            if (!string.IsNullOrWhiteSpace(texture?.textureRef))
                return texture.textureRef;

            var builder = new StringBuilder();
            builder.Append("texture|")
                .Append(texture?.usage).Append('|')
                .Append(texture?.sourcePath).Append('|')
                .Append(texture?.fileName).Append('|')
                .Append(texture?.imageName).Append('|')
                .Append(safeFileName);
            return Sha1Text(builder.ToString());
        }

        private static string StripExistingStableIdSuffix(string stem, string stableId)
        {
            if (string.IsNullOrWhiteSpace(stem) || string.IsNullOrWhiteSpace(stableId))
                return stem;

            var shortId = AssetPathUtility.ShortStableId(stableId);
            if (string.IsNullOrWhiteSpace(shortId))
                return stem;

            var separator = stem.LastIndexOf('_');
            if (separator <= 0 || separator >= stem.Length - 1)
                return stem;

            var suffix = stem.Substring(separator + 1).ToLowerInvariant();
            if (suffix.Length < shortId.Length || suffix.Length > 12)
                return stem;
            for (var i = 0; i < suffix.Length; i++)
            {
                if (!char.IsLetterOrDigit(suffix[i]))
                    return stem;
            }
            return suffix.StartsWith(shortId, StringComparison.Ordinal)
                ? stem.Substring(0, separator).TrimEnd('_', '.', '-', ' ')
                : stem;
        }

        private static string StripBlenderNumericSuffix(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return value;
            var dot = value.LastIndexOf('.');
            if (dot < 0 || dot == value.Length - 1)
                return value;
            var suffix = value.Substring(dot + 1);
            if (suffix.Length < 3)
                return value;
            for (var i = 0; i < suffix.Length; i++)
            {
                if (!char.IsDigit(suffix[i]))
                    return value;
            }
            return value.Substring(0, dot);
        }

        private static bool IsKnownTextureExtension(string ext)
        {
            if (string.IsNullOrWhiteSpace(ext))
                return false;
            switch (ext.Trim().ToLowerInvariant())
            {
                case ".png":
                case ".jpg":
                case ".jpeg":
                case ".tga":
                case ".tif":
                case ".tiff":
                case ".bmp":
                case ".exr":
                case ".hdr":
                    return true;
                default:
                    return false;
            }
        }

        private static void ConfigureTextureImporter(string assetPath, MaterialContentV1Texture texture, bool sRgb, bool isNormal, bool alphaIsTransparency)
        {
            var importer = AssetImporter.GetAtPath(assetPath) as TextureImporter;
            if (importer == null)
                return;
            var changed = false;
            var desiredType = isNormal ? TextureImporterType.NormalMap : TextureImporterType.Default;
            if (importer.textureType != desiredType)
            {
                importer.textureType = desiredType;
                changed = true;
            }
            if (importer.sRGBTexture != sRgb)
            {
                importer.sRGBTexture = sRgb;
                changed = true;
            }
            if (!isNormal && importer.alphaSource != TextureImporterAlphaSource.FromInput)
            {
                importer.alphaSource = TextureImporterAlphaSource.FromInput;
                changed = true;
            }
            if (!isNormal && importer.alphaIsTransparency != alphaIsTransparency)
            {
                importer.alphaIsTransparency = alphaIsTransparency;
                changed = true;
            }
            var desiredWrap = DesiredWrapMode(texture);
            if (importer.wrapMode != desiredWrap)
            {
                importer.wrapMode = desiredWrap;
                changed = true;
            }
            var desiredFilter = DesiredFilterMode(texture);
            if (importer.filterMode != desiredFilter)
            {
                importer.filterMode = desiredFilter;
                changed = true;
            }
            if (changed)
                importer.SaveAndReimport();
        }

        private static TextureWrapMode DesiredWrapMode(MaterialContentV1Texture texture)
        {
            var extension = texture != null ? texture.extension : null;
            if (string.IsNullOrWhiteSpace(extension))
                return TextureWrapMode.Repeat;
            switch (extension.Trim().ToUpperInvariant())
            {
                case "EXTEND":
                case "CLIP":
                    return TextureWrapMode.Clamp;
                case "MIRROR":
                    return TextureWrapMode.Mirror;
                case "REPEAT":
                default:
                    return TextureWrapMode.Repeat;
            }
        }

        private static FilterMode DesiredFilterMode(MaterialContentV1Texture texture)
        {
            var interpolation = texture != null ? texture.interpolation : null;
            if (string.IsNullOrWhiteSpace(interpolation))
                return FilterMode.Bilinear;
            switch (interpolation.Trim().ToUpperInvariant())
            {
                case "CLOSEST":
                    return FilterMode.Point;
                case "LINEAR":
                case "CUBIC":
                case "SMART":
                default:
                    return FilterMode.Bilinear;
            }
        }

        private static string BuildMaterialAssetPath(string materialRef, string materialName)
        {
            return AssetPathUtility.BuildStableAssetPath(GeneratedResourcePaths.MaterialsRootAssetPath, materialName, materialRef, ".mat");
        }

        private static string MaterialAssetObjectName(string assetPath, string fallback)
        {
            var stem = Path.GetFileNameWithoutExtension(assetPath ?? string.Empty);
            return string.IsNullOrWhiteSpace(stem) ? (string.IsNullOrWhiteSpace(fallback) ? "Material" : fallback) : stem;
        }

        private static bool EnsureMaterialObjectName(Material material, string desiredName)
        {
            if (material == null || string.IsNullOrWhiteSpace(desiredName) || material.name == desiredName)
                return false;
            material.name = desiredName;
            EditorUtility.SetDirty(material);
            return true;
        }

        private static bool PathsEqual(string a, string b)
        {
            return string.Equals((a ?? "").Replace('\\', '/'), (b ?? "").Replace('\\', '/'), StringComparison.OrdinalIgnoreCase);
        }

        private static string SanitizeFileName(string value, string fallback)
        {
            var raw = string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
            foreach (var ch in Path.GetInvalidFileNameChars())
                raw = raw.Replace(ch, '_');
            raw = raw.Replace(':', '_').Replace('/', '_').Replace('\\', '_');
            return string.IsNullOrWhiteSpace(raw) ? fallback : raw;
        }

        private static void EnsureAssetFolder(string assetFolder)
        {
            if (string.IsNullOrWhiteSpace(assetFolder))
                return;
            var normalized = assetFolder.Replace('\\', '/');
            var parts = normalized.Split('/');
            if (parts.Length == 0 || parts[0] != "Assets")
                throw new InvalidOperationException("asset_path_must_start_with_assets:" + assetFolder);
            var current = "Assets";
            for (var i = 1; i < parts.Length; i++)
            {
                var next = current + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next))
                    AssetDatabase.CreateFolder(current, parts[i]);
                current = next;
            }
        }

        private Dictionary<string, AptMaterialTextureSlotFingerprint> LoadPriorTextureImportSlots(string materialRef)
        {
            var result = new Dictionary<string, AptMaterialTextureSlotFingerprint>(StringComparer.Ordinal);
            if (string.IsNullOrWhiteSpace(materialRef))
                return result;

            var db = _repository.Load();
            var record = _repository.FindByAssetId(db, materialRef);
            var slots = record != null ? record.materialTextureSlots : null;
            if (slots == null)
                return result;

            foreach (var slot in slots)
            {
                if (slot == null || string.IsNullOrWhiteSpace(slot.slot) || string.IsNullOrWhiteSpace(slot.importFingerprint))
                    continue;
                result[slot.slot] = slot;
            }
            return result;
        }

        private static Dictionary<string, string> BuildTextureImportFingerprints(SceneSyncMaterialContentV1Message msg)
        {
            var result = new Dictionary<string, string>(StringComparer.Ordinal);
            if (msg == null || msg.textures == null)
                return result;

            AddTextureImportFingerprint(result, "baseColor", msg.textures.baseColor, sRgb: true, isNormal: false, alphaIsTransparency: false);
            AddTextureImportFingerprint(result, "normal", msg.textures.normal, sRgb: false, isNormal: true, alphaIsTransparency: false);
            AddTextureImportFingerprint(result, "metallic", msg.textures.metallic, sRgb: false, isNormal: false, alphaIsTransparency: false);
            AddTextureImportFingerprint(result, "roughness", msg.textures.roughness, sRgb: false, isNormal: false, alphaIsTransparency: false);
            AddTextureImportFingerprint(result, "occlusion", msg.textures.occlusion, sRgb: false, isNormal: false, alphaIsTransparency: false);
            AddTextureImportFingerprint(result, "height", msg.textures.height, sRgb: false, isNormal: false, alphaIsTransparency: false);
            AddTextureImportFingerprint(result, "alpha", msg.textures.alpha, sRgb: false, isNormal: false, alphaIsTransparency: false);
            AddTextureImportFingerprint(result, "emission", msg.textures.emission, sRgb: true, isNormal: false, alphaIsTransparency: false);
            return result;
        }

        private static bool ValidateImportableTextureSources(MaterialContentV1Textures textures, out string missingSlots)
        {
            var missing = new List<string>();
            if (textures != null)
            {
                AddMissingTextureSource(missing, "baseColor", textures.baseColor);
                AddMissingTextureSource(missing, "normal", textures.normal);
                AddMissingTextureSource(missing, "metallic", textures.metallic);
                AddMissingTextureSource(missing, "roughness", textures.roughness);
                AddMissingTextureSource(missing, "occlusion", textures.occlusion);
                AddMissingTextureSource(missing, "height", textures.height);
                AddMissingTextureSource(missing, "emission", textures.emission);
            }

            missingSlots = string.Join(",", missing.ToArray());
            return missing.Count == 0;
        }

        private static void AddMissingTextureSource(List<string> missing, string slot, MaterialContentV1Texture texture)
        {
            if (missing == null || texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
                return;
            if (File.Exists(texture.sourcePath))
                return;
            missing.Add(slot);
        }

        private static Dictionary<string, string> BuildAppliedTextureImportFingerprints(Material material, Dictionary<string, string> requestedTextureImports)
        {
            var result = new Dictionary<string, string>(StringComparer.Ordinal);
            if (material == null || requestedTextureImports == null || requestedTextureImports.Count == 0)
                return result;

            AddAppliedTextureImport(result, requestedTextureImports, "baseColor", material, "_BSG_BaseMap");
            AddAppliedTextureImport(result, requestedTextureImports, "normal", material, "_BSG_NormalMap");
            AddAppliedTextureImport(result, requestedTextureImports, "metallic", material, "_BSG_MetallicMap");
            AddAppliedTextureImport(result, requestedTextureImports, "roughness", material, "_BSG_RoughnessMap");
            AddAppliedTextureImport(result, requestedTextureImports, "occlusion", material, "_BSG_OcclusionMap");
            AddAppliedTextureImport(result, requestedTextureImports, "height", material, "_BSG_ParallaxMap");
            AddAppliedTextureImport(result, requestedTextureImports, "emission", material, "_BSG_EmissionMap");
            return result;
        }

        private static void AddAppliedTextureImport(Dictionary<string, string> result, Dictionary<string, string> requestedTextureImports, string slot, Material material, string propertyName)
        {
            if (result == null || requestedTextureImports == null || material == null || string.IsNullOrWhiteSpace(slot))
                return;
            if (!requestedTextureImports.TryGetValue(slot, out var fingerprint) || string.IsNullOrWhiteSpace(fingerprint))
                return;
            if (string.IsNullOrWhiteSpace(propertyName) || !material.HasProperty(propertyName) || material.GetTexture(propertyName) == null)
                return;
            result[slot] = fingerprint;
        }

        private static bool TryGetMissingAppliedTextureImports(Material material, Dictionary<string, string> requestedTextureImports, Dictionary<string, string> appliedTextureImports, out string missingSlots)
        {
            var missing = new List<string>();
            AddMissingAppliedTextureImport(missing, material, requestedTextureImports, appliedTextureImports, "baseColor", "_BSG_BaseMap");
            AddMissingAppliedTextureImport(missing, material, requestedTextureImports, appliedTextureImports, "normal", "_BSG_NormalMap");
            AddMissingAppliedTextureImport(missing, material, requestedTextureImports, appliedTextureImports, "metallic", "_BSG_MetallicMap");
            AddMissingAppliedTextureImport(missing, material, requestedTextureImports, appliedTextureImports, "roughness", "_BSG_RoughnessMap");
            AddMissingAppliedTextureImport(missing, material, requestedTextureImports, appliedTextureImports, "occlusion", "_BSG_OcclusionMap");
            AddMissingAppliedTextureImport(missing, material, requestedTextureImports, appliedTextureImports, "height", "_BSG_ParallaxMap");
            AddMissingAppliedTextureImport(missing, material, requestedTextureImports, appliedTextureImports, "emission", "_BSG_EmissionMap");
            missingSlots = string.Join(",", missing.ToArray());
            return missing.Count > 0;
        }

        private static void AddMissingAppliedTextureImport(List<string> missing, Material material, Dictionary<string, string> requestedTextureImports, Dictionary<string, string> appliedTextureImports, string slot, string propertyName)
        {
            if (missing == null || material == null || requestedTextureImports == null || string.IsNullOrWhiteSpace(slot))
                return;
            if (!requestedTextureImports.ContainsKey(slot))
                return;
            if (appliedTextureImports != null && appliedTextureImports.ContainsKey(slot))
                return;
            if (!string.IsNullOrWhiteSpace(propertyName) && material.HasProperty(propertyName))
                missing.Add(slot);
        }

        private static bool TextureSlotsMatch(AptMaterialTextureSlotFingerprint[] priorSlots, Dictionary<string, string> appliedTextureImports)
        {
            var expected = appliedTextureImports ?? new Dictionary<string, string>(StringComparer.Ordinal);
            var actual = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var slot in priorSlots ?? Array.Empty<AptMaterialTextureSlotFingerprint>())
            {
                if (slot == null || string.IsNullOrWhiteSpace(slot.slot) || string.IsNullOrWhiteSpace(slot.importFingerprint))
                    continue;
                actual[slot.slot] = slot.importFingerprint;
            }

            if (actual.Count != expected.Count)
                return false;
            foreach (var pair in expected)
            {
                if (!actual.TryGetValue(pair.Key, out var fingerprint) || !string.Equals(fingerprint, pair.Value, StringComparison.Ordinal))
                    return false;
            }
            return true;
        }

        private static void AddTextureImportFingerprint(Dictionary<string, string> result, string slot, MaterialContentV1Texture texture, bool sRgb, bool isNormal, bool alphaIsTransparency)
        {
            if (result == null || string.IsNullOrWhiteSpace(slot) || texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
                return;

            var builder = new StringBuilder();
            builder.Append("texture_import_v1|")
                .Append(slot).Append('|')
                .Append(texture.usage).Append('|')
                .Append(texture.textureRef).Append('|')
                .Append(texture.sourcePath).Append('|')
                .Append(sRgb).Append('|')
                .Append(isNormal).Append('|')
                .Append(alphaIsTransparency).Append('|')
                .Append(DesiredWrapMode(texture)).Append('|')
                .Append(DesiredFilterMode(texture));
            try
            {
                if (!string.IsNullOrWhiteSpace(texture.sourcePath) && File.Exists(texture.sourcePath))
                {
                    AppendTextureFileIdentity(builder, texture);
                }
                else
                {
                    builder.Append("|missing");
                }
            }
            catch
            {
                builder.Append("|missing");
            }

            using (var sha1 = SHA1.Create())
            {
                var bytes = Encoding.UTF8.GetBytes(builder.ToString());
                result[slot] = "texture_import_v1:" + BitConverter.ToString(sha1.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            }
        }

        private static string BuildAppliedFingerprint(SceneSyncMaterialContentV1Message msg)
        {
            var builder = new StringBuilder();
            builder.Append(ApplyFingerprintVersion).Append('|');
            builder.Append(msg != null && msg.fingerprint != null ? msg.fingerprint.contentHash : null).Append('|');
            builder.Append(msg != null && msg.fingerprint != null ? msg.fingerprint.textureDependencyHash : null);
            AppendTextureFingerprint(builder, msg != null && msg.textures != null ? msg.textures.baseColor : null);
            AppendTextureFingerprint(builder, msg != null && msg.textures != null ? msg.textures.normal : null);
            AppendTextureFingerprint(builder, msg != null && msg.textures != null ? msg.textures.metallic : null);
            AppendTextureFingerprint(builder, msg != null && msg.textures != null ? msg.textures.roughness : null);
            AppendTextureFingerprint(builder, msg != null && msg.textures != null ? msg.textures.occlusion : null);
            AppendTextureFingerprint(builder, msg != null && msg.textures != null ? msg.textures.height : null);
            AppendTextureFingerprint(builder, msg != null && msg.textures != null ? msg.textures.alpha : null);
            AppendTextureFingerprint(builder, msg != null && msg.textures != null ? msg.textures.emission : null);

            using (var sha1 = SHA1.Create())
            {
                var bytes = Encoding.UTF8.GetBytes(builder.ToString());
                return ApplyFingerprintVersion + ":" + BitConverter.ToString(sha1.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            }
        }

        private static void AppendTextureFingerprint(StringBuilder builder, MaterialContentV1Texture texture)
        {
            if (texture == null)
                return;
            builder.Append('|')
                .Append(texture.usage).Append(':')
                .Append(texture.textureRef).Append(':')
                .Append(texture.sourcePath).Append(':')
                .Append(texture.sourceChannel).Append(':')
                .Append(texture.normalStrength).Append(':')
                .Append(texture.normalSpace).Append(':')
                .Append(texture.heightScale);
            try
            {
                if (!string.IsNullOrWhiteSpace(texture.sourcePath) && File.Exists(texture.sourcePath))
                {
                    AppendTextureFileIdentity(builder, texture);
                    return;
                }
            }
            catch
            {
                // Fingerprint fallback below keeps apply conservative when file metadata cannot be read.
            }
            builder.Append(":missing");
        }

        private static void AppendTextureFileIdentity(StringBuilder builder, MaterialContentV1Texture texture)
        {
            if (builder == null || texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
                return;
            var info = new FileInfo(texture.sourcePath);
            if (IsGeneratedTextureSource(texture) || TryNormalizeTextureStagingPath(texture.sourcePath, out _))
            {
                builder.Append(":bytes=").Append(info.Length).Append(":sha1=").Append(Sha1File(texture.sourcePath));
                return;
            }
            builder.Append(":bytes=").Append(info.Length).Append(":mtime=").Append(info.LastWriteTimeUtc.Ticks);
        }

        private static string Sha1File(string path)
        {
            using (var sha1 = SHA1.Create())
            using (var stream = File.OpenRead(path))
            {
                return BitConverter.ToString(sha1.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
            }
        }

        private static string Sha1Text(string value)
        {
            using (var sha1 = SHA1.Create())
            {
                var bytes = Encoding.UTF8.GetBytes(value ?? string.Empty);
                return BitConverter.ToString(sha1.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            }
        }

        private void UpsertRegistry(SceneSyncMaterialContentV1Message msg, Material material, string assetPath, string guid, string appliedFingerprint, Dictionary<string, string> textureImportFingerprints)
        {
            var db = _repository.Load();
            var record = _repository.FindByAssetId(db, msg.materialRef) ?? new AptRecord { assetId = msg.materialRef };
            record.sourceFingerprint = appliedFingerprint;
            record.source = new AptSource
            {
                sourceUri = "blender://material/" + (msg.source != null ? msg.source.name : msg.materialRef),
            };
            record.target = new AptTarget
            {
                unityAssetPath = assetPath,
                resourceType = "material",
                assetGuid = guid,
                localFileId = 0,
            };
            record.mappingState = "mapped";
            record.updateState = "up_to_date";
            record.lastImportedAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            record.lastError = null;
            record.materialTextureSlots = BuildAptTextureSlots(textureImportFingerprints, msg != null && msg.textures != null ? msg.textures : null);
            UpsertTextureRegistryRecords(db, material, msg != null ? msg.textures : null, textureImportFingerprints);
            _repository.Upsert(db, record);
            _repository.Save(db);
        }

        private void RepairTextureRegistryRecords(SceneSyncMaterialContentV1Message msg, Material material, Dictionary<string, string> textureImportFingerprints)
        {
            if (msg == null || material == null || textureImportFingerprints == null || textureImportFingerprints.Count == 0)
                return;

            var db = _repository.Load();
            if (!UpsertTextureRegistryRecords(db, material, msg.textures, textureImportFingerprints))
                return;
            _repository.Save(db);
        }

        private bool UpsertTextureRegistryRecords(
            AptDatabase db,
            Material material,
            MaterialContentV1Textures textures,
            Dictionary<string, string> textureImportFingerprints)
        {
            if (db == null || material == null || textureImportFingerprints == null || textureImportFingerprints.Count == 0)
                return false;

            var changed = false;
            foreach (var candidate in BuildAppliedTextureRegistryCandidates(material, textures, textureImportFingerprints))
            {
                var baseAssetId = TextureRegistryBaseAssetId(candidate.texture);
                if (string.IsNullOrWhiteSpace(baseAssetId) || string.IsNullOrWhiteSpace(candidate.assetPath))
                    continue;

                var assetId = ResolveTextureRegistryAssetId(db, baseAssetId, candidate.slot, candidate.assetPath);
                var existing = _repository.FindByAssetId(db, assetId);
                if (existing == null)
                {
                    existing = new AptRecord { assetId = assetId };
                    changed = true;
                }

                var target = existing.target ?? new AptTarget();
                var source = existing.source ?? new AptSource();
                var sourceUri = "blender://texture/" + baseAssetId;
                if (!string.Equals(target.unityAssetPath, candidate.assetPath, StringComparison.Ordinal)
                    || !string.Equals(target.resourceType, "texture", StringComparison.OrdinalIgnoreCase)
                    || !string.Equals(target.assetGuid, candidate.assetGuid, StringComparison.Ordinal)
                    || !string.Equals(existing.sourceFingerprint, candidate.importFingerprint, StringComparison.Ordinal)
                    || !string.Equals(source.sourceUri, sourceUri, StringComparison.Ordinal)
                    || !string.Equals(source.sourceObjectPath, candidate.slot, StringComparison.Ordinal)
                    || !string.Equals(existing.mappingState, "mapped", StringComparison.Ordinal)
                    || !string.Equals(existing.updateState, "up_to_date", StringComparison.Ordinal)
                    || !string.IsNullOrWhiteSpace(existing.lastError))
                {
                    target.unityAssetPath = candidate.assetPath;
                    target.resourceType = "texture";
                    target.assetGuid = candidate.assetGuid;
                    target.localFileId = 0;
                    source.sourceUri = sourceUri;
                    source.sourceObjectPath = candidate.slot;
                    existing.target = target;
                    existing.source = source;
                    existing.sourceFingerprint = candidate.importFingerprint;
                    existing.mappingState = "mapped";
                    existing.updateState = "up_to_date";
                    existing.lastImportedAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    existing.lastError = null;
                    existing.materialTextureSlots = Array.Empty<AptMaterialTextureSlotFingerprint>();
                    changed = true;
                }

                _repository.Upsert(db, existing);
                if (candidate.textureObject is Texture2D texture2D)
                {
                    ResourceRuntimeMapping.PutResolved(
                        assetId,
                        "texture",
                        candidate.assetGuid,
                        candidate.assetPath,
                        candidate.importFingerprint,
                        texture2D);
                }
            }

            return changed;
        }

        private static List<AppliedTextureRegistryCandidate> BuildAppliedTextureRegistryCandidates(
            Material material,
            MaterialContentV1Textures textures,
            Dictionary<string, string> textureImportFingerprints)
        {
            var result = new List<AppliedTextureRegistryCandidate>();
            AddAppliedTextureRegistryCandidate(result, material, textures != null ? textures.baseColor : null, textureImportFingerprints, "baseColor", "_BSG_BaseMap");
            AddAppliedTextureRegistryCandidate(result, material, textures != null ? textures.normal : null, textureImportFingerprints, "normal", "_BSG_NormalMap");
            AddAppliedTextureRegistryCandidate(result, material, textures != null ? textures.metallic : null, textureImportFingerprints, "metallic", "_BSG_MetallicMap");
            AddAppliedTextureRegistryCandidate(result, material, textures != null ? textures.roughness : null, textureImportFingerprints, "roughness", "_BSG_RoughnessMap");
            AddAppliedTextureRegistryCandidate(result, material, textures != null ? textures.occlusion : null, textureImportFingerprints, "occlusion", "_BSG_OcclusionMap");
            AddAppliedTextureRegistryCandidate(result, material, textures != null ? textures.height : null, textureImportFingerprints, "height", "_BSG_ParallaxMap");
            AddAppliedTextureRegistryCandidate(result, material, textures != null ? textures.emission : null, textureImportFingerprints, "emission", "_BSG_EmissionMap");
            return result;
        }

        private static void AddAppliedTextureRegistryCandidate(
            List<AppliedTextureRegistryCandidate> result,
            Material material,
            MaterialContentV1Texture texture,
            Dictionary<string, string> textureImportFingerprints,
            string slot,
            string propertyName)
        {
            if (result == null || material == null || texture == null || textureImportFingerprints == null)
                return;
            if (!textureImportFingerprints.TryGetValue(slot, out var importFingerprint) || string.IsNullOrWhiteSpace(importFingerprint))
                return;
            if (string.IsNullOrWhiteSpace(propertyName) || !material.HasProperty(propertyName))
                return;

            var textureObject = material.GetTexture(propertyName);
            var assetPath = textureObject != null ? AssetDatabase.GetAssetPath(textureObject) : null;
            if (string.IsNullOrWhiteSpace(assetPath))
                return;

            result.Add(new AppliedTextureRegistryCandidate
            {
                slot = slot,
                texture = texture,
                textureObject = textureObject,
                assetPath = assetPath.Replace('\\', '/'),
                assetGuid = AssetDatabase.AssetPathToGUID(assetPath),
                importFingerprint = importFingerprint,
            });
        }

        private static string TextureRegistryBaseAssetId(MaterialContentV1Texture texture)
        {
            if (!string.IsNullOrWhiteSpace(texture?.textureRef))
                return texture.textureRef.Trim();
            return "tex-" + BuildTextureStableId(texture, texture?.fileName ?? "texture.png");
        }

        private static string ResolveTextureRegistryAssetId(AptDatabase db, string baseAssetId, string slot, string assetPath)
        {
            var repository = new AptRepository();
            var existing = repository.FindByAssetId(db, baseAssetId);
            if (existing == null)
                return baseAssetId;
            if (existing.target != null
                && string.Equals(existing.target.resourceType, "texture", StringComparison.OrdinalIgnoreCase)
                && PathsEqual(existing.target.unityAssetPath, assetPath))
                return baseAssetId;
            if (existing.target != null
                && string.Equals(existing.target.resourceType, "texture", StringComparison.OrdinalIgnoreCase)
                && (string.Equals(existing.source?.sourceObjectPath, slot, StringComparison.Ordinal)
                    || (string.IsNullOrWhiteSpace(existing.source?.sourceObjectPath)
                        && string.Equals(slot, "baseColor", StringComparison.Ordinal))))
                return baseAssetId;

            var roleAssetId = baseAssetId + ":" + slot;
            var roleExisting = repository.FindByAssetId(db, roleAssetId);
            if (roleExisting == null)
                return roleAssetId;
            if (roleExisting.target != null
                && string.Equals(roleExisting.target.resourceType, "texture", StringComparison.OrdinalIgnoreCase)
                && PathsEqual(roleExisting.target.unityAssetPath, assetPath))
                return roleAssetId;
            if (roleExisting.target != null
                && string.Equals(roleExisting.target.resourceType, "texture", StringComparison.OrdinalIgnoreCase)
                && string.Equals(roleExisting.source?.sourceObjectPath, slot, StringComparison.Ordinal))
                return roleAssetId;

            return roleAssetId + ":" + Sha1Text(assetPath).Substring(0, 8);
        }

        private sealed class AppliedTextureRegistryCandidate
        {
            public string slot;
            public MaterialContentV1Texture texture;
            public UnityEngine.Texture textureObject;
            public string assetPath;
            public string assetGuid;
            public string importFingerprint;
        }

        private static AptMaterialTextureSlotFingerprint[] BuildAptTextureSlots(Dictionary<string, string> textureImportFingerprints, MaterialContentV1Textures textures)
        {
            var result = new List<AptMaterialTextureSlotFingerprint>();
            AddAptTextureSlot(result, textureImportFingerprints, "baseColor", textures != null ? textures.baseColor : null, sRgb: true, isNormal: false, alphaIsTransparency: false);
            AddAptTextureSlot(result, textureImportFingerprints, "normal", textures != null ? textures.normal : null, sRgb: false, isNormal: true, alphaIsTransparency: false);
            AddAptTextureSlot(result, textureImportFingerprints, "metallic", textures != null ? textures.metallic : null, sRgb: false, isNormal: false, alphaIsTransparency: false);
            AddAptTextureSlot(result, textureImportFingerprints, "roughness", textures != null ? textures.roughness : null, sRgb: false, isNormal: false, alphaIsTransparency: false);
            AddAptTextureSlot(result, textureImportFingerprints, "occlusion", textures != null ? textures.occlusion : null, sRgb: false, isNormal: false, alphaIsTransparency: false);
            AddAptTextureSlot(result, textureImportFingerprints, "height", textures != null ? textures.height : null, sRgb: false, isNormal: false, alphaIsTransparency: false);
            AddAptTextureSlot(result, textureImportFingerprints, "alpha", textures != null ? textures.alpha : null, sRgb: false, isNormal: false, alphaIsTransparency: false);
            AddAptTextureSlot(result, textureImportFingerprints, "emission", textures != null ? textures.emission : null, sRgb: true, isNormal: false, alphaIsTransparency: false);
            return result.ToArray();
        }

        private static void AddAptTextureSlot(List<AptMaterialTextureSlotFingerprint> result, Dictionary<string, string> textureImportFingerprints, string slot, MaterialContentV1Texture texture, bool sRgb, bool isNormal, bool alphaIsTransparency)
        {
            if (result == null || textureImportFingerprints == null || string.IsNullOrWhiteSpace(slot))
                return;
            if (!textureImportFingerprints.TryGetValue(slot, out var fingerprint) || string.IsNullOrWhiteSpace(fingerprint))
                return;
            result.Add(new AptMaterialTextureSlotFingerprint
            {
                slot = slot,
                importFingerprint = fingerprint,
                assetPath = TextureAssetPathForRecord(texture, sRgb, isNormal, alphaIsTransparency),
            });
        }

        private static string TextureAssetPathForRecord(MaterialContentV1Texture texture, bool sRgb, bool isNormal, bool alphaIsTransparency)
        {
            if (texture == null || string.IsNullOrWhiteSpace(texture.sourcePath))
                return null;

            var assetPath = TryConvertAbsolutePathToAssetPath(texture.sourcePath);
            if (!string.IsNullOrWhiteSpace(assetPath)
                && !IsGeneratedTextureSource(texture)
                && !TextureImporterMatchesRole(assetPath, texture, sRgb, isNormal, alphaIsTransparency))
            {
                return BuildTextureAssetPath(texture);
            }

            return assetPath ?? BuildTextureAssetPath(texture);
        }
#endif
    }
}
