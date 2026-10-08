using System;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
#if UNITY_EDITOR
    internal static class BlendShapeWeightEditorRepaintPump
    {
        private static readonly object PumpLock = new object();
        private static double _until;
        private static bool _hooked;
        private const double DefaultDurationSeconds = 0.25;

        public static void Request(double seconds)
        {
            var now = UnityEditor.EditorApplication.timeSinceStartup;
            lock (PumpLock)
            {
                _until = Math.Max(_until, now + Math.Max(0.05, seconds));
                if (_hooked)
                    return;
                UnityEditor.EditorApplication.update += Pump;
                _hooked = true;
            }
        }

        private static void Pump()
        {
            UnityEditor.SceneView.RepaintAll();
            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            UnityEditor.EditorApplication.QueuePlayerLoopUpdate();

            lock (PumpLock)
            {
                if (UnityEditor.EditorApplication.timeSinceStartup < _until)
                    return;
                UnityEditor.EditorApplication.update -= Pump;
                _hooked = false;
            }
        }
    }
#endif

    public sealed class BlendShapeWeightApplyService
    {
        internal static int ResolveBlendShapeIndex(Mesh mesh, string name, int blenderIndex)
        {
            if (mesh == null)
                return -1;

            var index = -1;
            if (!string.IsNullOrWhiteSpace(name))
                index = mesh.GetBlendShapeIndex(name);
            if (index < 0 && blenderIndex > 0)
                index = blenderIndex - 1; // Blender key_blocks index includes Basis; Unity BlendShape index does not.
            return index;
        }

        public bool TryApply(GameObject target, SceneSyncBlendShapeWeightsMessage msg, out string error, out int applied, out int missing)
        {
            error = null;
            applied = 0;
            missing = 0;

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
            if (msg.weights == null || msg.weights.Length == 0)
                return true;

            var skinned = target.GetComponent<SkinnedMeshRenderer>();
            if (skinned == null || skinned.sharedMesh == null)
            {
                var meshFilter = target.GetComponent<MeshFilter>();
                if (meshFilter != null && meshFilter.sharedMesh != null && meshFilter.sharedMesh.blendShapeCount > 0)
                {
                    MeshReferenceApplyService.ReconcileRendererForMesh(target, meshFilter.sharedMesh);
                    skinned = target.GetComponent<SkinnedMeshRenderer>();
                }
                if (skinned == null || skinned.sharedMesh == null)
                {
                    error = "skinned_mesh_renderer_missing";
                    return false;
                }
            }

            var mesh = skinned.sharedMesh;
            for (var i = 0; i < msg.weights.Length; i++)
            {
                var item = msg.weights[i];
                if (item == null)
                    continue;

                var index = ResolveBlendShapeIndex(mesh, item.name, item.index);

                if (index < 0 || index >= mesh.blendShapeCount)
                {
                    missing++;
                    continue;
                }

                skinned.SetBlendShapeWeight(index, item.weight);
                applied++;
            }

#if UNITY_EDITOR
            if (applied > 0)
            {
                UnityEditor.EditorUtility.SetDirty(skinned);
                UnityEditor.EditorUtility.SetDirty(target);
                if (!UnityEditor.EditorApplication.isPlaying)
                {
                    var wasEnabled = skinned.enabled;
                    skinned.enabled = false;
                    skinned.enabled = wasEnabled;
                }
                BlendShapeWeightEditorRepaintPump.Request(0.25);
            }
#endif
            return true;
        }
    }
}
