using System;
using System.Linq;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class ReferenceChangeService
    {
        private readonly MaterialReferenceApplyService _materialApply;
        private readonly MeshReferenceApplyService _meshRefApply;
        private readonly ObjectResourceLinkRegistry _linkRegistry;

        public ReferenceChangeService(
            MaterialReferenceApplyService materialApply,
            MeshReferenceApplyService meshRefApply,
            ObjectResourceLinkRegistry linkRegistry)
        {
            _materialApply = materialApply;
            _meshRefApply = meshRefApply;
            _linkRegistry = linkRegistry;
        }

        public void Apply(SceneSyncReferenceChangeMessage msg, GameObject target)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("reference_update_message_missing");
                return;
            }

            var resourceKind = NormalizeResourceKind(msg.resourceKind);
            if (resourceKind == "material")
            {
                ApplyMaterialReference(msg, target, resourceKind);
                return;
            }

            if (resourceKind == "mesh")
            {
                ApplyMeshReference(msg, target, resourceKind);
                return;
            }

            SceneSyncStateStore.MarkError("reference_update_failed", msg.pairId);
        }

        private void ApplyMaterialReference(SceneSyncReferenceChangeMessage msg, GameObject target, string resourceKind)
        {
            var effectiveMaterialRefs = (msg.resourceRefs ?? Array.Empty<string>())
                .Select(r => (r ?? string.Empty).Trim())
                .ToArray();
            if (effectiveMaterialRefs.Length == 0 && !string.IsNullOrWhiteSpace(msg.resourceRef))
                effectiveMaterialRefs = new[] { msg.resourceRef.Trim() };
            if (effectiveMaterialRefs.Length == 0 && msg.resourceRefs == null)
            {
                SceneSyncStateStore.MarkError("material_reference_apply_failed", msg.pairId);
                return;
            }

            if (!_materialApply.TryApplyMany(target, effectiveMaterialRefs, out var matErr, out var unresolvedRefs, warnOnUnresolved: false))
            {
                MaterialReferenceApplyService.RegisterPending(target, msg.pairId, effectiveMaterialRefs, unresolvedRefs, "reference_change_failed");
                if (unresolvedRefs != null && unresolvedRefs.Length > 0)
                {
                    SceneSyncStateStore.MarkSkipped("material_reference_pending", msg.pairId);
                    return;
                }

                SceneSyncStateStore.MarkError(matErr ?? "material_reference_apply_failed", msg.pairId);
                return;
            }
            if (unresolvedRefs != null && unresolvedRefs.Length > 0)
                MaterialReferenceApplyService.RegisterPending(target, msg.pairId, effectiveMaterialRefs, unresolvedRefs, "reference_change_partial");

            _linkRegistry.UpdateReference(msg.pairId, target, resourceKind, msg.resourceRef, effectiveMaterialRefs);
            SceneSyncStateStore.MarkReferenceApplied(msg.pairId, resourceKind, string.Join(",", effectiveMaterialRefs));
        }

        private void ApplyMeshReference(SceneSyncReferenceChangeMessage msg, GameObject target, string resourceKind)
        {
            if (!_meshRefApply.TryApply(target, msg.pairId, msg.resourceRef, out var meshErr))
            {
                SceneSyncStateStore.MarkError(meshErr ?? "mesh_reference_apply_failed", msg.pairId);
                return;
            }

            _linkRegistry.UpdateReference(msg.pairId, target, resourceKind, msg.resourceRef);
            SceneSyncStateStore.MarkReferenceApplied(msg.pairId, resourceKind, msg.resourceRef);
        }

        private static string NormalizeResourceKind(string resourceKind)
        {
            return (resourceKind ?? string.Empty).Trim().ToLowerInvariant();
        }
    }
}
