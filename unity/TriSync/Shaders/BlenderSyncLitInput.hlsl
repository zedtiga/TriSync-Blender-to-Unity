// Modified from Unity Universal Render Pipeline 17.3.0 sources under the Unity Companion License.
// See THIRD_PARTY_NOTICES.md for provenance and terms.
#ifndef BLENDERSYNC_LIT_INPUT_INCLUDED
#define BLENDERSYNC_LIT_INPUT_INCLUDED

#include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
#include "Packages/com.unity.render-pipelines.core/ShaderLibrary/CommonMaterial.hlsl"
#include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/SurfaceInput.hlsl"
#include "Packages/com.unity.render-pipelines.core/ShaderLibrary/ParallaxMapping.hlsl"

#if defined(_DETAIL_MULX2) || defined(_DETAIL_SCALED)
#define _DETAIL
#endif

CBUFFER_START(UnityPerMaterial)
float4 _BSG_BaseMap_ST;
float4 _DetailAlbedoMap_ST;
half4 _BSG_BaseColor;
half _BSG_Metallic;
half _BSG_MetallicChannel;
half _BSG_Roughness;
half _BSG_RoughnessChannel;
half _BSG_NormalScale;
half _BSG_OcclusionStrength;
half _BSG_OcclusionChannel;
half _BSG_Parallax;
half _BSG_ParallaxChannel;
half _BSG_EmissionEnabled;
half4 _BSG_EmissionColor;
half _Cutoff;
half _Surface;
half _DetailAlbedoMapScale;
half _DetailNormalMapScale;
CBUFFER_END

TEXTURE2D(_BSG_BaseMap);       SAMPLER(sampler_BSG_BaseMap);
TEXTURE2D(_BSG_NormalMap);     SAMPLER(sampler_BSG_NormalMap);
TEXTURE2D(_BSG_EmissionMap);   SAMPLER(sampler_BSG_EmissionMap);
TEXTURE2D(_BSG_MetallicMap);   SAMPLER(sampler_BSG_MetallicMap);
TEXTURE2D(_BSG_RoughnessMap);  SAMPLER(sampler_BSG_RoughnessMap);
TEXTURE2D(_BSG_OcclusionMap);  SAMPLER(sampler_BSG_OcclusionMap);
TEXTURE2D(_BSG_ParallaxMap);   SAMPLER(sampler_BSG_ParallaxMap);
TEXTURE2D(_DetailMask);        SAMPLER(sampler_DetailMask);
TEXTURE2D(_DetailAlbedoMap);   SAMPLER(sampler_DetailAlbedoMap);
TEXTURE2D(_DetailNormalMap);   SAMPLER(sampler_DetailNormalMap);

// Matches URP Lit detail behavior while feeding TriSync's Principled-style
// surface data. Detail maps are intentionally kept on Unity's native _Detail*
// properties so the stock URP Detail Inputs inspector can drive them directly.
half3 ScaleDetailAlbedo(half3 detailAlbedo, half scale)
{
    return half(2.0) * detailAlbedo * scale - scale + half(1.0);
}

half3 ApplyDetailAlbedo(float2 detailUv, half3 albedo, half detailMask)
{
#if defined(_DETAIL)
    half3 detailAlbedo = SAMPLE_TEXTURE2D(_DetailAlbedoMap, sampler_DetailAlbedoMap, detailUv).rgb;

#if defined(_DETAIL_SCALED)
    detailAlbedo = ScaleDetailAlbedo(detailAlbedo, _DetailAlbedoMapScale);
#else
    detailAlbedo = half(2.0) * detailAlbedo;
#endif

    return albedo * LerpWhiteTo(detailAlbedo, detailMask);
#else
    return albedo;
#endif
}

half3 ApplyDetailNormal(float2 detailUv, half3 normalTS, half detailMask)
{
#if defined(_DETAIL)
#if BUMP_SCALE_NOT_SUPPORTED
    half3 detailNormalTS = UnpackNormal(SAMPLE_TEXTURE2D(_DetailNormalMap, sampler_DetailNormalMap, detailUv));
#else
    half3 detailNormalTS = UnpackNormalScale(SAMPLE_TEXTURE2D(_DetailNormalMap, sampler_DetailNormalMap, detailUv), _DetailNormalMapScale);
#endif

    detailNormalTS = normalize(detailNormalTS);
    return lerp(normalTS, BlendNormalRNM(normalTS, detailNormalTS), detailMask);
#else
    return normalTS;
#endif
}

half SampleBsgTextureChannel(half4 sampleValue, half channel)
{
    half value = sampleValue.g;
    if (channel < 0.5h)
        value = sampleValue.r;
    else if (channel > 2.5h)
        value = sampleValue.a;
    else if (channel > 1.5h)
        value = sampleValue.b;
    return value;
}

void ApplyBsgPerPixelDisplacement(half3 viewDirTS, inout float2 uv)
{
#if defined(_BSG_PARALLAXMAP)
    half height = SampleBsgTextureChannel(SAMPLE_TEXTURE2D(_BSG_ParallaxMap, sampler_BSG_ParallaxMap, uv), _BSG_ParallaxChannel);
    uv += ParallaxOffset1Step(height, _BSG_Parallax, viewDirTS);
#endif
}

half SampleBsgOcclusion(float2 uv)
{
#if defined(_BSG_OCCLUSIONMAP)
    half occ = SampleBsgTextureChannel(SAMPLE_TEXTURE2D(_BSG_OcclusionMap, sampler_BSG_OcclusionMap, uv), _BSG_OcclusionChannel);
    return LerpWhiteTo(occ, _BSG_OcclusionStrength);
#else
    return 1.0h;
#endif
}

inline void InitializeBlenderSyncLitSurfaceData(float2 uv, out SurfaceData outSurfaceData)
{
    outSurfaceData = (SurfaceData)0;

    half4 baseSample = SAMPLE_TEXTURE2D(_BSG_BaseMap, sampler_BSG_BaseMap, uv);
    outSurfaceData.alpha = Alpha(baseSample.a, _BSG_BaseColor, _Cutoff);
    outSurfaceData.albedo = baseSample.rgb * _BSG_BaseColor.rgb;
    outSurfaceData.albedo = AlphaModulate(outSurfaceData.albedo, outSurfaceData.alpha);

    half metallic = saturate(_BSG_Metallic);
#ifdef _BSG_METALLICMAP
    metallic = SampleBsgTextureChannel(SAMPLE_TEXTURE2D(_BSG_MetallicMap, sampler_BSG_MetallicMap, uv), _BSG_MetallicChannel);
#endif

    half roughness = saturate(_BSG_Roughness);
#ifdef _BSG_ROUGHNESSMAP
    roughness *= SampleBsgTextureChannel(SAMPLE_TEXTURE2D(_BSG_RoughnessMap, sampler_BSG_RoughnessMap, uv), _BSG_RoughnessChannel);
#endif

    outSurfaceData.metallic = saturate(metallic);
    outSurfaceData.specular = half3(0.0h, 0.0h, 0.0h);
    outSurfaceData.smoothness = saturate(1.0h - roughness);
    outSurfaceData.normalTS = SampleNormal(uv, TEXTURE2D_ARGS(_BSG_NormalMap, sampler_BSG_NormalMap), _BSG_NormalScale);

#if defined(_DETAIL)
    half detailMask = SAMPLE_TEXTURE2D(_DetailMask, sampler_DetailMask, uv).a;
    float2 detailUv = uv * _DetailAlbedoMap_ST.xy + _DetailAlbedoMap_ST.zw;
    outSurfaceData.albedo = ApplyDetailAlbedo(detailUv, outSurfaceData.albedo, detailMask);
    outSurfaceData.normalTS = ApplyDetailNormal(detailUv, outSurfaceData.normalTS, detailMask);
#endif

#ifdef _EMISSION
    outSurfaceData.emission = SAMPLE_TEXTURE2D(_BSG_EmissionMap, sampler_BSG_EmissionMap, uv).rgb * _BSG_EmissionColor.rgb;
#else
    outSurfaceData.emission = 0.0h;
#endif
    outSurfaceData.occlusion = SampleBsgOcclusion(uv);
    outSurfaceData.clearCoatMask = 0.0h;
    outSurfaceData.clearCoatSmoothness = 0.0h;
}

#endif
