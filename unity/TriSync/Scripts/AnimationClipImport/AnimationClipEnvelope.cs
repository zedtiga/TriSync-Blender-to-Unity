using System;
using System.Collections.Generic;

namespace BlenderSyncVNext.AnimationClipImport
{
    [Serializable]
    public sealed class AnimationClipEnvelope
    {
        public string kind;
        public int version;
        public AnimationClipPayload clip;
    }

    [Serializable]
    public sealed class AnimationClipPayload
    {
        public string assetId;
        public string name;
        public string sourceActionName;
        public float frameRate;
        public int startFrame;
        public int endFrame;
        public string wrapModeHint;
        public string bindingSpace;
        public string channelSemantic;
        public AnimationExportSettings exportSettings;
        public AnimationExportContext exportContext;
        public AnimationExportReport exportReport;
        public AnimationRootMotionDebug rootMotion;
        public List<AnimationTrackDto> tracks;
    }

    [Serializable]
    public sealed class AnimationExportSettings
    {
        public string rangeMode;
        public string sampleMode;
        public string sampleRateMode;
        public int sampleStep;
        public string animationSource;
        public bool bakeEvaluatedPose;
        public string rigAxisMode;
        public string primaryBoneAxis;
        public string secondaryBoneAxis;
        public string interpolation;
        public bool quaternionContinuity;
        public string simplify;
        public string staticCurves;
        public int sampleCount;
    }

    [Serializable]
    public sealed class AnimationExportReport
    {
        public string clipName;
        public string sampleMode;
        public string sampleRateMode;
        public float frameRate;
        public float sceneFrameRate;
        public int startFrame;
        public int endFrame;
        public float durationSeconds;
        public int sampleCount;
        public int trackCount;
        public int keyCountBeforeSimplify;
        public int keyCountAfterSimplify;
        public int keyCount;
        public int removedKeyCount;
        public int simplifyRemovedKeyCount;
        public int staticCurveRemovedKeyCount;
        public int collapsedStaticTrackCount;
        public int targetCount;
        public bool bakeEvaluatedPose;
        public string interpolation;
        public bool quaternionContinuity;
        public string simplify;
        public string staticCurves;
        public string loop;
        public bool rootMotionEnabled;
        public string rootMotionSource;
        public string rootMotionBone;
        public float rootMotionDistance;
        public float rootMotionYawDegrees;
    }

    [Serializable]
    public sealed class AnimationRootMotionDebug
    {
        public bool enabled;
        public string source;
        public string sourceBone;
        public string spaceSemantic;
        public int sampleCount;
        public float[] times;
        public float[] positions;
        public float[] rotations;
        public float[] deltaPositions;
        public float[] totalDisplacement;
        public float totalDistance;
        public float totalYawDegrees;
        public string applyPolicy;
        public string error;
    }

    [Serializable]
    public sealed class AnimationExportContext
    {
        public string sourceObjectName;
        public string sourceArmatureName;
        public string sourceObjectType;
        public string sourcePairId;
        public string sourceRiggedObjectId;
        public string targetKind;
    }

    [Serializable]
    public sealed class AnimationTrackDto
    {
        public string path;
        public string targetType;
        public string property;
        public string component;
        public string interpolation;
        public List<AnimationKeyDto> keys;
    }

    [Serializable]
    public sealed class AnimationKeyDto
    {
        public float time;
        public float value;
    }
}
