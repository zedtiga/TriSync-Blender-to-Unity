using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class TransformApplyService
    {
        public bool TryApply(Transform target, SceneSyncTransformMessage msg, out string error)
        {
            error = null;
            if (!EditModeGuard.IsAvailable)
            {
                error = "transform_edit_mode_only";
                return false;
            }
            if (target == null)
            {
                error = "target is null";
                return false;
            }
            if (msg == null)
            {
                error = "message is null";
                return false;
            }
            if (msg.position == null || msg.position.Length != 3 ||
                msg.rotation == null || msg.rotation.Length != 4 ||
                msg.scale == null || msg.scale.Length != 3)
            {
                error = "invalid transform payload";
                return false;
            }

            var targetPosition = SceneSyncTransformMapper.MapPosition(msg.position);
            var targetRotation = SceneSyncTransformMapper.MapRotation(msg.rotation);
            var targetScale = SceneSyncTransformMapper.MapScale(msg.scale);

            string objectType = null;
            if (target.GetComponent<Camera>() != null)
                objectType = "camera";
            else if (target.GetComponent<Light>() != null)
                objectType = "light";
            if (!string.IsNullOrWhiteSpace(objectType))
                targetRotation = SceneSyncTransformMapper.MapRotationForObjectType(msg.rotation, objectType);

            var sourceHint = (msg.sourceHint ?? string.Empty).Trim().ToLowerInvariant();
            var useDirectApply = sourceHint == "manual_sync" || !TransformSmoothingPreferences.Enabled;

            if (useDirectApply)
            {
                SceneSyncTransformSmoother.RemoveFrom(target, completeTarget: false);

                target.position = targetPosition;
                target.rotation = targetRotation;
                target.localScale = targetScale;
            }
            else
            {
                SceneSyncTransformSmoother.SmoothTo(
                    target,
                    targetPosition,
                    targetRotation,
                    targetScale,
                    TransformSmoothingPreferences.SmoothingTime,
                    TransformSmoothingPreferences.RuntimeCurve);
            }

            return true;
        }
    }
}
