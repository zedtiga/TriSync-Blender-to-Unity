// Modified from Unity Universal Render Pipeline 17.3.0 sources under the Unity Companion License.
// See THIRD_PARTY_NOTICES.md for provenance and terms.
#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.Rendering;
using UnityEngine;
using UnityEngine.Experimental.Rendering;
using UnityEditor.Rendering.Universal.ShaderGUI;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;

namespace BlenderSyncVNext.Editor
{
    /// <summary>
    /// URP-Lit-based inspector for TriSync/Principled Lit URP.
    /// Keeps URP BaseShaderGUI foldouts/options/advanced behaviour, but replaces
    /// the Lit metallic+smoothness surface input block with Blender-style
    /// metallic+roughness inputs.
    /// </summary>
    public sealed class BlenderSyncPrincipledLitShaderGUI : BaseShaderGUI
    {
        private MaterialProperty _bsgBaseMap;
        private MaterialProperty _bsgBaseColor;
        private MaterialProperty _bsgMetallic;
        private MaterialProperty _bsgMetallicMap;
        private MaterialProperty _bsgMetallicChannel;
        private MaterialProperty _bsgRoughness;
        private MaterialProperty _bsgRoughnessMap;
        private MaterialProperty _bsgRoughnessChannel;
        private MaterialProperty _bsgNormalMap;
        private MaterialProperty _bsgNormalScale;
        private MaterialProperty _bsgOcclusionMap;
        private MaterialProperty _bsgOcclusionStrength;
        private MaterialProperty _bsgOcclusionChannel;
        private MaterialProperty _bsgParallaxMap;
        private MaterialProperty _bsgParallax;
        private MaterialProperty _bsgParallaxChannel;
        private MaterialProperty _bsgEmissionEnabled;
        private MaterialProperty _bsgEmissionMap;
        private MaterialProperty _bsgEmissionColor;
        private MaterialProperty _highlights;
        private MaterialProperty _reflections;
        private MaterialProperty _detailMask;
        private MaterialProperty _detailAlbedoMapScale;
        private MaterialProperty _detailAlbedoMap;
        private MaterialProperty _detailNormalMapScale;
        private MaterialProperty _detailNormalMap;

        private static GUIContent L(string text, string tooltip = null)
        {
            return new GUIContent(Tr(text), string.IsNullOrEmpty(tooltip) ? null : Tr(tooltip));
        }

        private static GUIContent DetailInputsText => L("Detail Inputs", "These settings define the surface details by tiling and overlaying additional maps on the surface.");
        private static GUIContent BaseMapText => L("Base Map", "Blender Principled Base Color texture.");
        private static GUIContent MetallicMapText => L("Metallic Map", "Blender Principled Metallic texture.");
        private static GUIContent MetallicChannelText => L("Metallic Channel", "Texture channel sampled for metallic. Imported Blender materials set this from the source node graph.");
        private static GUIContent MetallicText => L("Metallic");
        private static GUIContent RoughnessMapText => L("Roughness Map", "Blender Principled Roughness texture. The sampled roughness value is multiplied by Roughness, then converted internally to Unity smoothness.");
        private static GUIContent RoughnessChannelText => L("Roughness Channel", "Texture channel sampled for roughness. Imported Blender materials set this from the source node graph.");
        private static GUIContent RoughnessText => L("Roughness");
        private static GUIContent OcclusionMapText => L("Occlusion Map", "Ambient occlusion texture.");
        private static GUIContent OcclusionStrengthText => L("Occlusion Strength");
        private static GUIContent OcclusionChannelText => L("Occlusion Channel", "Texture channel sampled for ambient occlusion. Imported Blender materials set this from the source node graph.");
        private static GUIContent HeightMapText => L("Height Map", "Height map used for parallax mapping.");
        private static GUIContent HeightScaleText => L("Height Scale");
        private static GUIContent HeightChannelText => L("Height Channel", "Texture channel sampled for height/parallax. Imported Blender materials set this from the source node graph.");
        private static GUIContent EmissionMapText => L("Emission Map");
        private static GUIContent EmissionColorText => L("Emission Color");
        private static GUIContent HighlightsText => L("Specular Highlights");
        private static GUIContent ReflectionsText => L("Environment Reflections");
        private static GUIContent WorkflowText => L("Workflow Mode", "TriSync Principled Lit uses a fixed Blender Principled metallic/roughness workflow.");
        private static GUIContent DetailMaskText => L("Mask", "Select a mask for the Detail map. The mask uses the alpha channel of the selected texture. The Tiling and Offset settings have no effect on the mask.");
        private static GUIContent DetailAlbedoMapText => L("Base Map", "Select the surface detail texture.The alpha of your texture determines surface hue and intensity.");
        private static GUIContent DetailNormalMapText => L("Normal Map", "Designates a Normal Map to create the illusion of bumps and dents in the details of this Material's surface.");
        private static GUIContent DetailAlbedoMapScaleInfo => L("Setting the scaling factor to a value other than 1 results in a less performant shader variant.");
        private static GUIContent DetailAlbedoMapFormatError => L("This texture is not in linear space.");

        public override void FindProperties(MaterialProperty[] properties)
        {
            base.FindProperties(properties);
            _bsgBaseMap = FindProperty("_BSG_BaseMap", properties, false);
            _bsgBaseColor = FindProperty("_BSG_BaseColor", properties, false);
            _bsgMetallic = FindProperty("_BSG_Metallic", properties, false);
            _bsgMetallicMap = FindProperty("_BSG_MetallicMap", properties, false);
            _bsgMetallicChannel = FindProperty("_BSG_MetallicChannel", properties, false);
            _bsgRoughness = FindProperty("_BSG_Roughness", properties, false);
            _bsgRoughnessMap = FindProperty("_BSG_RoughnessMap", properties, false);
            _bsgRoughnessChannel = FindProperty("_BSG_RoughnessChannel", properties, false);
            _bsgNormalMap = FindProperty("_BSG_NormalMap", properties, false);
            _bsgNormalScale = FindProperty("_BSG_NormalScale", properties, false);
            _bsgOcclusionMap = FindProperty("_BSG_OcclusionMap", properties, false);
            _bsgOcclusionStrength = FindProperty("_BSG_OcclusionStrength", properties, false);
            _bsgOcclusionChannel = FindProperty("_BSG_OcclusionChannel", properties, false);
            _bsgParallaxMap = FindProperty("_BSG_ParallaxMap", properties, false);
            _bsgParallax = FindProperty("_BSG_Parallax", properties, false);
            _bsgParallaxChannel = FindProperty("_BSG_ParallaxChannel", properties, false);
            _bsgEmissionEnabled = FindProperty("_BSG_EmissionEnabled", properties, false);
            _bsgEmissionMap = FindProperty("_BSG_EmissionMap", properties, false);
            _bsgEmissionColor = FindProperty("_BSG_EmissionColor", properties, false);
            _highlights = FindProperty("_SpecularHighlights", properties, false);
            _reflections = FindProperty("_EnvironmentReflections", properties, false);
            _detailMask = FindProperty("_DetailMask", properties, false);
            _detailAlbedoMapScale = FindProperty("_DetailAlbedoMapScale", properties, false);
            _detailAlbedoMap = FindProperty("_DetailAlbedoMap", properties, false);
            _detailNormalMapScale = FindProperty("_DetailNormalMapScale", properties, false);
            _detailNormalMap = FindProperty("_DetailNormalMap", properties, false);
        }

        public override void ValidateMaterial(Material material)
        {
            SetupBlenderSyncKeywords(material);
            SetupMaterialBlendMode(material);
            SetupRequiredPasses(material);
            SetupMotionVectorOptions(material);
        }

        public override void FillAdditionalFoldouts(MaterialHeaderScopeList materialScopesList)
        {
            materialScopesList.RegisterHeaderScope(
                DetailInputsText,
                Expandable.Details,
                _ => DrawDetailArea());
        }

        public override void DrawSurfaceOptions(Material material)
        {
            using (new EditorGUI.DisabledScope(true))
            {
                EditorGUILayout.TextField(WorkflowText, Tr("Blender Principled / Metallic"));
            }
            base.DrawSurfaceOptions(material);
        }

        public override void DrawSurfaceInputs(Material material)
        {
            // Keep the original Surface Inputs foldout from BaseShaderGUI, but draw
            // TriSync semantic properties instead of URP Lit's Metallic/Smoothness block.
            if (_bsgBaseMap != null && _bsgBaseColor != null)
                materialEditor.TexturePropertySingleLine(BaseMapText, _bsgBaseMap, _bsgBaseColor);
            else if (_bsgBaseMap != null)
                materialEditor.TexturePropertySingleLine(BaseMapText, _bsgBaseMap);
            else if (_bsgBaseColor != null)
                materialEditor.ColorProperty(_bsgBaseColor, Tr("Base Color"));

            if (_bsgMetallicMap != null && _bsgMetallic != null)
            {
                materialEditor.TexturePropertySingleLine(MetallicMapText, _bsgMetallicMap, _bsgMetallic);
                if (_bsgMetallicMap.textureValue != null && _bsgMetallicChannel != null)
                    materialEditor.ShaderProperty(_bsgMetallicChannel, MetallicChannelText);
            }
            else if (_bsgMetallic != null)
                materialEditor.ShaderProperty(_bsgMetallic, MetallicText);

            if (_bsgRoughnessMap != null && _bsgRoughness != null)
            {
                materialEditor.TexturePropertySingleLine(RoughnessMapText, _bsgRoughnessMap, _bsgRoughness);
                if (_bsgRoughnessMap.textureValue != null && _bsgRoughnessChannel != null)
                    materialEditor.ShaderProperty(_bsgRoughnessChannel, RoughnessChannelText);
            }
            else if (_bsgRoughness != null)
                materialEditor.ShaderProperty(_bsgRoughness, RoughnessText);

            DrawNormalArea(materialEditor, _bsgNormalMap, _bsgNormalScale);
            DrawBlenderSyncOcclusionAndHeight();
            DrawBlenderSyncEmission(material);
            DrawTileOffset(materialEditor, _bsgBaseMap);
        }

        public override void DrawAdvancedOptions(Material material)
        {
            if (_highlights != null)
                materialEditor.ShaderProperty(_highlights, HighlightsText);
            if (_reflections != null)
                materialEditor.ShaderProperty(_reflections, ReflectionsText);
            base.DrawAdvancedOptions(material);
        }

        private void DrawBlenderSyncOcclusionAndHeight()
        {
            if (_bsgOcclusionMap != null)
            {
                materialEditor.TexturePropertySingleLine(
                    OcclusionMapText,
                    _bsgOcclusionMap,
                    _bsgOcclusionMap.textureValue != null ? _bsgOcclusionStrength : null);
                if (_bsgOcclusionMap.textureValue != null && _bsgOcclusionChannel != null)
                    materialEditor.ShaderProperty(_bsgOcclusionChannel, OcclusionChannelText);
            }
            else if (_bsgOcclusionStrength != null)
                materialEditor.ShaderProperty(_bsgOcclusionStrength, OcclusionStrengthText);

            if (_bsgParallaxMap != null)
            {
                materialEditor.TexturePropertySingleLine(
                    HeightMapText,
                    _bsgParallaxMap,
                    _bsgParallaxMap.textureValue != null ? _bsgParallax : null);
                if (_bsgParallaxMap.textureValue != null && _bsgParallaxChannel != null)
                    materialEditor.ShaderProperty(_bsgParallaxChannel, HeightChannelText);
            }
            else if (_bsgParallax != null)
                materialEditor.ShaderProperty(_bsgParallax, HeightScaleText);
        }

        private void DrawBlenderSyncEmission(Material material)
        {
            var emissionEnabled = _bsgEmissionEnabled == null || _bsgEmissionEnabled.floatValue >= 0.5f;
            if (_bsgEmissionEnabled != null)
            {
                EditorGUI.BeginChangeCheck();
                emissionEnabled = EditorGUILayout.Toggle(L("Emission"), emissionEnabled);
                if (EditorGUI.EndChangeCheck())
                    _bsgEmissionEnabled.floatValue = emissionEnabled ? 1f : 0f;
            }

            using (new EditorGUI.DisabledScope(!emissionEnabled))
            {
                if (_bsgEmissionMap != null && _bsgEmissionColor != null)
                    materialEditor.TexturePropertySingleLine(EmissionMapText, _bsgEmissionMap, _bsgEmissionColor);
                else if (_bsgEmissionMap != null)
                    materialEditor.TexturePropertySingleLine(EmissionMapText, _bsgEmissionMap);
                else if (_bsgEmissionColor != null)
                    materialEditor.ColorProperty(_bsgEmissionColor, EmissionColorText.text);
            }

            if (material != null)
                SetupBlenderSyncKeywords(material);
        }

        private void DrawDetailArea()
        {
            if (_detailMask != null)
                materialEditor.TexturePropertySingleLine(DetailMaskText, _detailMask);

            if (_detailAlbedoMap != null)
                materialEditor.TexturePropertySingleLine(
                    DetailAlbedoMapText,
                    _detailAlbedoMap,
                    _detailAlbedoMap.textureValue != null ? _detailAlbedoMapScale : null);

            if (_detailAlbedoMapScale != null && _detailAlbedoMapScale.floatValue != 1.0f)
                EditorGUILayout.HelpBox(DetailAlbedoMapScaleInfo.text, MessageType.Info, true);

            var detailAlbedoTexture = _detailAlbedoMap != null ? _detailAlbedoMap.textureValue as Texture2D : null;
            if (detailAlbedoTexture != null && GraphicsFormatUtility.IsSRGBFormat(detailAlbedoTexture.graphicsFormat))
                EditorGUILayout.HelpBox(DetailAlbedoMapFormatError.text, MessageType.Warning, true);

            if (_detailNormalMap != null)
                materialEditor.TexturePropertySingleLine(
                    DetailNormalMapText,
                    _detailNormalMap,
                    _detailNormalMap.textureValue != null ? _detailNormalMapScale : null);

            if (_detailAlbedoMap != null)
                materialEditor.TextureScaleOffsetProperty(_detailAlbedoMap);
        }

        private static void SetupBlenderSyncKeywords(Material material)
        {
            if (material == null)
                return;
            SetKeyword(material, "_NORMALMAP", material.HasProperty("_BSG_NormalMap") && material.GetTexture("_BSG_NormalMap") != null);
            SetKeyword(material, "_BSG_METALLICMAP", material.HasProperty("_BSG_MetallicMap") && material.GetTexture("_BSG_MetallicMap") != null);
            SetKeyword(material, "_BSG_ROUGHNESSMAP", material.HasProperty("_BSG_RoughnessMap") && material.GetTexture("_BSG_RoughnessMap") != null);
            SetKeyword(material, "_BSG_OCCLUSIONMAP", material.HasProperty("_BSG_OcclusionMap") && material.GetTexture("_BSG_OcclusionMap") != null);
            SetKeyword(material, "_BSG_PARALLAXMAP", material.HasProperty("_BSG_ParallaxMap") && material.GetTexture("_BSG_ParallaxMap") != null);
            var receiveShadows = !material.HasProperty("_ReceiveShadows") || material.GetFloat("_ReceiveShadows") >= 0.5f;
            SetKeyword(material, "_RECEIVE_SHADOWS_OFF", !receiveShadows);
            SetupDetailKeywords(material);

            var emissionColor = material.HasProperty("_BSG_EmissionColor") ? material.GetColor("_BSG_EmissionColor") : Color.black;
            var hasEmissionMap = material.HasProperty("_BSG_EmissionMap") && material.GetTexture("_BSG_EmissionMap") != null;
            var emissionEnabled = material.HasProperty("_BSG_EmissionEnabled")
                ? material.GetFloat("_BSG_EmissionEnabled") >= 0.5f
                : (hasEmissionMap || emissionColor.maxColorComponent > 0.0001f);
            SetKeyword(material, "_EMISSION", emissionEnabled);
            material.globalIlluminationFlags = emissionEnabled
                ? MaterialGlobalIlluminationFlags.BakedEmissive
                : MaterialGlobalIlluminationFlags.EmissiveIsBlack;
        }

        private static void SetupDetailKeywords(Material material)
        {
            if (material == null || !material.HasProperty("_DetailAlbedoMap") || !material.HasProperty("_DetailNormalMap") || !material.HasProperty("_DetailAlbedoMapScale"))
                return;

            var isScaled = material.GetFloat("_DetailAlbedoMapScale") != 1.0f;
            var hasDetailMap = material.GetTexture("_DetailAlbedoMap") != null || material.GetTexture("_DetailNormalMap") != null;
            SetKeyword(material, "_DETAIL_MULX2", !isScaled && hasDetailMap);
            SetKeyword(material, "_DETAIL_SCALED", isScaled && hasDetailMap);
        }

        private static void SetupMotionVectorOptions(Material material)
        {
            if (material == null)
                return;

            if (material.HasProperty("_AddPrecomputedVelocity"))
            {
                var enabled = material.GetFloat("_AddPrecomputedVelocity") != 0.0f;
                SetKeyword(material, "_ADD_PRECOMPUTED_VELOCITY", enabled);
                material.SetShaderPassEnabled("MotionVectors", enabled);
            }

            if (material.HasProperty("_XRMotionVectorsPass"))
            {
                var enabled = material.GetFloat("_XRMotionVectorsPass") != 0.0f;
                material.SetShaderPassEnabled("XRMotionVectors", enabled);
            }
        }

        private static void SetupRequiredPasses(Material material)
        {
            if (material == null)
                return;
            material.SetShaderPassEnabled("ForwardLit", true);
            material.SetShaderPassEnabled("Meta", true);
            material.SetShaderPassEnabled("ShadowCaster", IsOpaqueSurface(material));
        }

        private static bool IsOpaqueSurface(Material material)
        {
            return !material.HasProperty("_Surface") || material.GetFloat("_Surface") < 0.5f;
        }

        private static void SetKeyword(Material material, string keyword, bool enabled)
        {
            if (enabled) material.EnableKeyword(keyword);
            else material.DisableKeyword(keyword);
        }
    }
}
#endif
