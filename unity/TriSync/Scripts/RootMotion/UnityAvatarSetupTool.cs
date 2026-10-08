#if UNITY_EDITOR
using UnityEngine;

namespace BlenderSyncVNext.RootMotion
{
    internal static class UnityAvatarSetupTool
    {
        public static Quaternion AvatarComputeOrientation(Vector3 leftUpLeg, Vector3 rightUpLeg, Vector3 leftArm, Vector3 rightArm)
        {
            var right = (rightUpLeg - leftUpLeg + rightArm - leftArm).normalized;
            if (right.sqrMagnitude < 1e-8f)
                right = Vector3.right;

            var hipsCenter = (leftUpLeg + rightUpLeg) * 0.5f;
            var armsCenter = (leftArm + rightArm) * 0.5f;
            var up = (armsCenter - hipsCenter).normalized;
            if (up.sqrMagnitude < 1e-8f)
                up = Vector3.up;

            var forward = Vector3.Cross(right, up).normalized;
            if (forward.sqrMagnitude < 1e-8f)
                forward = Vector3.forward;
            up = Vector3.Cross(forward, right).normalized;
            return Quaternion.LookRotation(forward, up);
        }
    }
}
#endif
