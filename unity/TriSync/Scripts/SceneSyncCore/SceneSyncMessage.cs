using System;

namespace BlenderSyncVNext.SceneSyncCore
{
    [Serializable]
    public sealed class SceneSyncTransformMessage
    {
        public string type; // scene_sync.transform
        public long timestamp;
        public string pairId;
        public string sourceHint; // auto_sync | manual_sync | unspecified
        public float[] position; // [x,y,z]
        public float[] rotation; // [x,y,z,w]
        public float[] scale;    // [x,y,z]
    }

    [Serializable]
    public sealed class SceneSyncHierarchyMessage
    {
        public string type; // scene_sync.hierarchy
        public long timestamp;
        public string childPairId;
        public string parentPairId;
        public bool clearParent;
    }

    [Serializable]
    public sealed class SceneSyncObjectNameMessage
    {
        public string type; // scene_sync.object_name
        public long timestamp;
        public string pairId;
        public string objectName;
    }

    [Serializable]
    public sealed class SceneSyncVisibilityMessage
    {
        public string type; // scene_sync.visibility
        public long timestamp;
        public string pairId;
        public bool visible;
    }

    [Serializable]
    public sealed class SceneSyncAutoSyncStateMessage
    {
        public string type; // scene_sync.auto_sync_state
        public long timestamp;
        public bool enabled;
        public float previewIdleCommitSeconds;
    }

    [Serializable]
    public sealed class SceneSyncViewStateMessage
    {
        public string type; // scene_sync.view_state_v1
        public long timestamp;
        public string sourceHint;
        public string viewMode;
        public bool isOrthographic;
        public float[] pivot;
        public float[] rotation;
        public float[] forward;
        public float[] up;
        public float distance;
        public float lens;
        public float orthographicScale;
        public float viewScale;
        public float clipStart;
        public float clipEnd;
    }

    [Serializable]
    public sealed class SceneSyncObjectStateUpdateMessage
    {
        public string type; // scene_sync.object_state_update_v1
        public long timestamp;
        public string sourceHint;
        public SceneSyncObjectStateItem[] objects;
    }

    [Serializable]
    public sealed class SceneSyncObjectStateItem
    {
        public string pairId;
        public string objectName;
        public string objectType;
        public bool visible;
        public string parentPairId;
        public bool clearParent;
        public bool parentKnownInBatch;
        public float[] position;
        public float[] rotation;
        public float[] scale;
        public float[] matrixLocal;
        public float[] matrixWorld;
        public float[] originLocal;
        public float[] originWorld;
        public float[] worldPosition;
        public float[] worldRotation;
        public float[] worldScale;
    }

    [Serializable]
    public sealed class SceneSyncMeshUpdateMessage
    {
        public string type; // scene_sync.mesh_update
        public long timestamp;
        public string pairId;
        public string sourceHint; // auto_sync/manual_sync route to preview mesh; empty routes to persistent apply
        public string rebuildReason;
        public string meshRef; // optional preferred target mesh resource
        public string[] materialRefs; // optional slot-ordered material refs to apply with subMeshes
        public float[] vertices; // flattened xyzxyz...
        public int[] triangles;
        public SceneSyncMeshSubMesh[] subMeshes;
        public SceneSyncMeshBlendShape[] blendShapes;
        public float[] normals;  // optional flattened xyz
        public float[] uv;       // optional flattened xy
        public string meshContentFingerprint;
        public string meshContentFingerprintNoUv;
        public string meshContentFingerprintScope;
    }

    [Serializable]
    public sealed class SceneSyncMeshUpdateBinaryMessage
    {
        public string type; // scene_sync.mesh_update_binary_v1
        public long timestamp;
        public string pairId;
        public string sourceHint; // auto_sync/manual_sync route to preview mesh; empty routes to persistent apply
        public string rebuildReason;
        public string meshRef;
        public string[] materialRefs;
        public int vertexCount;
        public int indexCount;
        public SceneSyncBinaryBuffer[] buffers;
        public SceneSyncMeshSubMesh[] subMeshes;
        public SceneSyncBinaryBlendShape[] blendShapes;
        public SceneSyncBinaryUvChannel[] uvChannels;
        public int uvChannelCount;
        public string[] uvChannelNames;
        public SceneSyncBinaryBuffer color0;
        public string colorAttributeName;
        public SceneSyncBinaryProfile profile;
        public string meshContentFingerprint;
        public string meshContentFingerprintNoUv;
        public string meshContentFingerprintScope;
    }

    [Serializable]
    public sealed class SceneSyncMeshSubMesh
    {
        public int materialSlot;
        public string topology;
        public int[] indices;
        public SceneSyncBinaryBuffer indicesBuffer;
    }

    [Serializable]
    public sealed class SceneSyncBinaryUvChannel
    {
        public int index;
        public string name;
        public SceneSyncBinaryBuffer buffer;
    }

    [Serializable]
    public sealed class SceneSyncBinaryBlendShape
    {
        public string name;
        public float frameWeight;
        public float value;
        public float sliderMin;
        public float sliderMax;
        public int vertexCount;
        public SceneSyncBinaryBuffer deltaPositionsBuffer;
    }

    [Serializable]
    public sealed class SceneSyncMeshBlendShape
    {
        public string name;
        public float frameWeight;
        public float value;
        public float sliderMin;
        public float sliderMax;
        public int vertexCount;
        public float[] deltaPositions;
    }

    [Serializable]
    public sealed class SceneSyncMeshPositionsUpdateMessage
    {
        public string type; // scene_sync.mesh_positions_update_v1
        public long timestamp;
        public string pairId;
        public string sourceHint;
        public string meshRef;
        public int sourceVertexCount;
        public int exportVertexCount;
        public int[] topologySignature;
        public SceneSyncBinaryBuffer buffer;
        public SceneSyncBinaryProfile profile;
    }

    [Serializable]
    public sealed class SceneSyncMeshUvUpdateMessage
    {
        public string type; // scene_sync.mesh_uv_update_v1
        public long timestamp;
        public string pairId;
        public string sourceHint;
        public string meshRef;
        public int exportVertexCount;
        public SceneSyncBinaryBuffer buffer;
        public SceneSyncBinaryProfile profile;
    }

    [Serializable]
    public sealed class SceneSyncBinaryBuffer
    {
        public string semantic; // POSITION | INDEX | NORMAL | UV0
        public string format;   // float32 | int32
        public int components;
        public int count;
        public string path;
        public long byteLength;
    }

    [Serializable]
    public sealed class SceneSyncBinaryProfile
    {
        public float packMs;
        public float writeMs;
        public long binaryBytes;
        public long fallbackJsonBytes;
        public int blendShapeCount;
        public float blendShapePackMs;
        public long blendShapeBinaryBytes;
        public int subMeshCount;
        public long subMeshBinaryBytes;
    }

    [Serializable]
    public sealed class SceneSyncBlendShapeWeightItem
    {
        public string name;
        public int index;
        public float value;
        public float weight;
    }

    [Serializable]
    public sealed class SceneSyncBlendShapeWeightsMessage
    {
        public string type; // scene_sync.blendshape_weights_v1
        public long timestamp;
        public string pairId;
        public string meshRef;
        public string sourceHint;
        public SceneSyncBlendShapeWeightItem[] weights;
    }

    [Serializable]
    public sealed class SceneSyncMeshForkMessage
    {
        public string type; // scene_sync.mesh_fork
        public long timestamp;
        public string pairId;
        public string sourceMeshRef;
        public string targetMeshRef;
        public string objectName;
        public string reason;
    }

    [Serializable]
    public sealed class SceneSyncPreviewCommitMessage
    {
        public string type; // scene_sync.preview_commit
        public long timestamp;
        public string pairId;
        public string meshRef;
        public string reason;
    }

    [Serializable]
    public sealed class SceneSyncPreviewCommitMeshMessage
    {
        public string type; // scene_sync.preview_commit_mesh_v1
        public long timestamp;
        public string pairId;
        public string meshRef;
        public string reason;
        public string meshContentFingerprint;
        public string meshContentFingerprintNoUv;
        public string meshContentFingerprintScope;
        public string[] materialRefs;
        public int vertexCount;
        public int indexCount;
        public SceneSyncBinaryBuffer[] buffers;
        public SceneSyncMeshSubMesh[] subMeshes;
        public SceneSyncBinaryBlendShape[] blendShapes;
        public SceneSyncBinaryUvChannel[] uvChannels;
        public int uvChannelCount;
        public string[] uvChannelNames;
        public SceneSyncBinaryBuffer color0;
        public string colorAttributeName;
        public SceneSyncBinaryProfile profile;
    }

    [Serializable]
    public sealed class SceneSyncReferenceChangeMessage
    {
        public string type; // scene_sync.reference_change
        public long timestamp;
        public string pairId;
        public string resourceKind; // minimal: mesh | material | texture (slice uses relation only)
        public string resourceRef;  // primary mesh ref, or first/single material ref
        public string[] resourceRefs; // slot-ordered refs for material/object-wide reference sync
    }

    [Serializable]
    public sealed class SceneSyncMaterialContentV1Message
    {
        public string type; // scene_sync.material_content_v1
        public string schema; // material_content_v1
        public string materialRef;
        public MaterialContentV1Source source;
        public MaterialContentV1Shader shader;
        public MaterialContentV1Properties properties;
        public MaterialContentV1Textures textures;
        public MaterialContentV1Fingerprint fingerprint;
        public MaterialContentV1Warning[] warnings;
    }

    [Serializable]
    public sealed class MaterialContentV1Source
    {
        public string name;
        public string blenderMaterialName;
    }

    [Serializable]
    public sealed class MaterialContentV1Shader
    {
        public string policy;
        public string target;
    }

    [Serializable]
    public sealed class MaterialContentV1Properties
    {
        public float[] baseColor;
        public float metallic;
        public float roughness;
        public float smoothness;
        public float alpha;
        public string alphaModeHint;
        public float alphaCutoff;
        public float[] emissionColor;
        public float emissionStrength;
    }

    [Serializable]
    public sealed class MaterialContentV1Textures
    {
        public MaterialContentV1Texture baseColor;
        public MaterialContentV1Texture normal;
        public MaterialContentV1Texture metallic;
        public MaterialContentV1Texture roughness;
        public MaterialContentV1Texture occlusion;
        public MaterialContentV1Texture height;
        public MaterialContentV1Texture alpha;
        public MaterialContentV1Texture emission;
    }

    [Serializable]
    public sealed class MaterialContentV1Texture
    {
        public string textureRef;
        public string sourcePath;
        public string imageName;
        public string fileName;
        public string colorSpace;
        public string blenderColorSpace;
        public string usage;
        public string sourceKind;
        public string sourceChannel;
        public string extension;
        public string interpolation;
        public string projection;
        public float normalStrength;
        public string normalSpace;
        public float heightScale;
    }

    [Serializable]
    public sealed class MaterialContentV1Fingerprint
    {
        public string contentHash;
        public string textureDependencyHash;
    }

    [Serializable]
    public sealed class MaterialContentV1Warning
    {
        public string code;
        public string slot;
        public string imageName;
        public string message;
    }

    [Serializable]
    public sealed class SceneSyncObjectAssemblyMessage
    {
        public string type; // scene_sync.object_assembly
        public long timestamp;
        public string pairId;
        public string objectName;
        public string objectType; // mesh | camera | light (optional, defaults to mesh)
        public bool active = true;
        public SceneSyncCameraPayload camera; // optional
        public SceneSyncLightPayload light;   // optional
        public string meshRef;
        public string[] materialRefs; // slot-ordered refs, required in strict mode
        public float[] position;
        public float[] rotation;
        public float[] scale;
    }

    [Serializable]
    public sealed class SceneSyncCameraPayload
    {
        public float fov;
        public float near;
        public float far;
    }

    [Serializable]
    public sealed class SceneSyncLightPayload
    {
        public string lightType; // POINT | SPOT | SUN/Directional
        public float[] color; // RGB
        public float intensity;
        public float range;
        public float spotAngle;
    }

    [Serializable]
    public sealed class SceneSyncObjectRemoveMessage
    {
        public string type; // scene_sync.object_remove
        public long timestamp;
        public string[] removedPairIds;
    }
}
