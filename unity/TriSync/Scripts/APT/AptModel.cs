using System;

namespace BlenderSyncVNext.APT
{
    [Serializable]
    public sealed class AptDatabase
    {
        public int version = 1;
        public AptRecord[] records = Array.Empty<AptRecord>();
    }

    [Serializable]
    public sealed class AptRecord
    {
        public string assetId;
        public string sourceFingerprint;
        public string meshContentFingerprint;
        public string meshContentFingerprintNoUv;
        public AptSource source;
        public AptTarget target;
        public string mappingState; // mapped|conflict|missing_target
        public string updateState;  // up_to_date|needs_import|needs_update|error
        public long lastImportedAt;
        public string lastError;
        public AptReferenceRef[] referencedBy = Array.Empty<AptReferenceRef>();
        public AptMaterialTextureSlotFingerprint[] materialTextureSlots = Array.Empty<AptMaterialTextureSlotFingerprint>();
    }

    [Serializable]
    public sealed class AptReferenceRef
    {
        public string pairId;
        public string sceneObjectId;
    }

    [Serializable]
    public sealed class AptMaterialTextureSlotFingerprint
    {
        public string slot;
        public string importFingerprint;
        public string assetPath;
    }

    [Serializable]
    public sealed class AptSource
    {
        public string sourceUri;
        public string sourceFile;
        public string sourceObjectPath;
    }

    [Serializable]
    public sealed class AptTarget
    {
        public string unityAssetPath;
        public string resourceType;
        public string assetGuid;
        public long localFileId;
        public bool isSkinned;
        public int boneCount;
        public string skinEncoding;
    }
}
