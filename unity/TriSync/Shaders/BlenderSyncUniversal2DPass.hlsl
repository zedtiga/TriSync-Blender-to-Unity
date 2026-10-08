// Modified from Unity Universal Render Pipeline 17.3.0 sources under the Unity Companion License.
// See THIRD_PARTY_NOTICES.md for provenance and terms.
#ifndef BLENDERSYNC_UNIVERSAL_2D_PASS_INCLUDED
#define BLENDERSYNC_UNIVERSAL_2D_PASS_INCLUDED

struct Attributes
{
    float4 positionOS       : POSITION;
    float2 uv               : TEXCOORD0;
    UNITY_VERTEX_INPUT_INSTANCE_ID
};

struct Varyings
{
    float2 uv        : TEXCOORD0;
    float4 vertex    : SV_POSITION;
    UNITY_VERTEX_INPUT_INSTANCE_ID
};

Varyings BlenderSyncUniversal2DVertex(Attributes input)
{
    Varyings output = (Varyings)0;

    UNITY_SETUP_INSTANCE_ID(input);
    UNITY_TRANSFER_INSTANCE_ID(input, output);

    VertexPositionInputs vertexInput = GetVertexPositionInputs(input.positionOS.xyz);
    output.vertex = vertexInput.positionCS;
    output.uv = TRANSFORM_TEX(input.uv, _BSG_BaseMap);

    return output;
}

half4 BlenderSyncUniversal2DFragment(Varyings input) : SV_Target
{
    UNITY_SETUP_INSTANCE_ID(input);

    half4 texColor = SAMPLE_TEXTURE2D(_BSG_BaseMap, sampler_BSG_BaseMap, input.uv);
    half alpha = Alpha(texColor.a, _BSG_BaseColor, _Cutoff);
    half3 color = texColor.rgb * _BSG_BaseColor.rgb;

#ifdef _ALPHAPREMULTIPLY_ON
    color *= alpha;
#endif

    return half4(color, alpha);
}

#endif
