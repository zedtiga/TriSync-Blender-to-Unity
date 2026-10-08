using System;

namespace BlenderSyncVNext.Protocol
{
    [Serializable]
    public sealed class AssetBridgeEnvelope
    {
        public string contractVersion;
        public AssetBridgePackage package;
    }

    [Serializable]
    public sealed class AssetBridgePackage
    {
        public string packageId;
        public AssetBridgeResourceEntry[] resources;
    }

    [Serializable]
    public sealed class AssetBridgeResourceEntry
    {
        public string assetId;
        public string sourceFingerprint;
        public string meshContentFingerprint;
        public string meshContentFingerprintNoUv;
        public string type;
        public AssetBridgeSource source;
        public MeshPayload mesh;
    }

    [Serializable]
    public sealed class RiggedObjectEnvelope
    {
        public string type;
        public long timestamp;
        public RiggedObjectPayload riggedObject;
    }

    [Serializable]
    public sealed class RiggedPoseEnvelope
    {
        public string type;
        public int version;
        public long timestamp;
        public string mode;
        public string riggedObjectId;
        public string objectName;
        public string spaceSemantic;
        public string rigAxisMode;
        public string primaryBoneAxis;
        public string secondaryBoneAxis;
        public string[] orderedBoneIds;
        public RiggedPoseBonePayload[] bones;
    }

    [Serializable]
    public sealed class RiggedPoseBonePayload
    {
        public string boneId;
        public string name;
        public float[] localPosition;
        public float[] localRotation;
        public float[] localScale;
    }

    [Serializable]
    public sealed class RiggedBlendShapeWeightsEnvelope
    {
        public string type;
        public int version;
        public long timestamp;
        public string riggedObjectId;
        public string objectName;
        public string spaceSemantic;
        public RiggedBlendShapePartPayload[] parts;
    }

    [Serializable]
    public sealed class RiggedBlendShapePartPayload
    {
        public string objectName;
        public string meshRef;
        public RiggedBlendShapeWeightPayload[] weights;
    }

    [Serializable]
    public sealed class RiggedBlendShapeWeightPayload
    {
        public string name;
        public int index;
        public float value;
        public float weight;
    }

    [Serializable]
    public sealed class RiggedMeshPartPayload
    {
        public string objectName;
        public string sourceObjectPath;
        public string meshRef;
        public string visibilityState;
        public string[] materialRefs;
        public string[] orderedBoneIds;
        public float[] localPosition;
        public float[] localRotation;
        public float[] localScale;
    }

    [Serializable]
    public sealed class RiggedObjectPayload
    {
        public string riggedObjectId;
        public string objectName;
        public string spaceSemantic;
        public string rigAxisMode;
        public string primaryBoneAxis;
        public string secondaryBoneAxis;
        public string sourceObjectPath;
        public string sourceArmatureObjectPath;
        public string meshRef;
        public string visibilityState;
        public string[] materialRefs;
        public RiggedMeshPartPayload[] meshParts;
        public float[] rigRootLocalPosition;
        public float[] rigRootLocalRotation;
        public float[] rigRootLocalScale;
        public float[] armatureLocalPosition;
        public float[] armatureLocalRotation;
        public float[] armatureLocalScale;
        public string rootBoneId;
        public string[] orderedBoneIds;
        public RiggedSkeletonPayload skeleton;
        public RiggedPrefabPolicyPayload prefabPolicy;
    }

    [Serializable]
    public sealed class RiggedSkeletonPayload
    {
        public RiggedBonePayload[] bones;
    }

    [Serializable]
    public sealed class RiggedBonePayload
    {
        public string boneId;
        public string name;
        public string parentBoneId;
        public float[] localPosition;
        public float[] localRotation;
        public float[] localScale;
        public float[] localMatrix; // optional 4x4 row-major parent-relative bone matrix from Blender
        public float[] restLocalPosition;
        public float[] restLocalRotation;
        public float[] restLocalScale;
        public float[] restLocalMatrix; // preferred explicit rest/baseline local matrix
        public bool isLeaf;
        public float[] tailLocalPosition;
        public float[] tailLocalMatrix;
    }

    [Serializable]
    public sealed class RiggedPrefabPolicyPayload
    {
        public bool generatePrefab;
        public bool autoInstantiate;
    }

    [Serializable]
    public sealed class AssetBridgeSource
    {
        public string sourceUri;
    }

    [Serializable]
    public sealed class MeshPayload
    {
        public string topology;
        public string spaceSemantic;
        public int vertexCount;
        public float[] vertices;
        public int[] indices;
        public MeshSubMeshPayload[] subMeshes;
        public float[] normals;
        public float[] uv0;
        public MeshUvChannelPayload[] uvChannels;
        public int uvChannelCount;
        public string[] uvChannelNames;
        public float[] color0;
        public string colorAttributeName;
        public int colorAttributeCount;
        public MeshBinaryBuffer color0Buffer;
        public MeshBinaryBuffer[] binaryBuffers;
        public MeshBinaryProfile binaryProfile;
        public MeshSkinPayload skin;
        public MeshBlendShapePayload[] blendShapes;
        public string meshSource;
        public string meshSourceRequested;
        public string meshSourceResolved;
        public string meshSourceReason;
        public int sourceVertexCount;
        public int sourcePolygonCount;
        public int evaluatedVertexCount;
        public int evaluatedPolygonCount;
        public int evaluatedLoopCount;
        public int evaluatedTriangleCount;
        public string[] modifierSummary;
        public string blendShapeSkipReason;
        public string skinSkipReason;
    }

    [Serializable]
    public sealed class MeshSubMeshPayload
    {
        public int materialSlot;
        public string topology;
        public int[] indices;
        public MeshBinaryBuffer indicesBuffer;
    }

    [Serializable]
    public sealed class MeshUvChannelPayload
    {
        public int index;
        public string name;
        public float[] values;
        public MeshBinaryBuffer buffer;
    }

    [Serializable]
    public sealed class MeshBlendShapePayload
    {
        public string name;
        public float frameWeight;
        public float value;
        public float sliderMin;
        public float sliderMax;
        public int vertexCount;
        public float[] deltaPositions;
        public MeshBinaryBuffer deltaPositionsBuffer;
    }

    [Serializable]
    public sealed class MeshBinaryBuffer
    {
        public string semantic; // POSITION | INDEX | NORMAL | UV0
        public string format;   // float32 | int32
        public int components;
        public int count;
        public string path;
        public long byteLength;
    }

    [Serializable]
    public sealed class MeshBinaryProfile
    {
        public float packMs;
        public float writeMs;
        public long binaryBytes;
        public long fallbackJsonBytes;
        public int blendShapeCount;
        public float blendShapePackMs;
        public long blendShapeBinaryBytes;
        public int uvChannelCount;
        public long uvBinaryBytes;
        public int subMeshCount;
        public long subMeshBinaryBytes;
    }

    [Serializable]
    public sealed class MeshSkinPayload
    {
        public bool isSkinned;
        public string skinEncoding;
        public string spaceSemantic;
        public int boneCount;
        public float[] bindPoses;
        public int[] bonesPerVertex;
        public int[] boneIndices;
        public float[] boneWeights;
        public float[] meshWorldMatrix;
        public float[] armatureWorldMatrix;
        public float[] clusterTransforms;
        public float[] clusterTransformLinks;
        public float[] clusterAssociateModel;
    }

}
