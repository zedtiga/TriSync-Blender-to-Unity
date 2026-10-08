using System;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class MaterialContentV1UpdateService
    {
        private readonly MaterialContentV1DryRunService _dryRun;
        private readonly MaterialContentV1ApplyService _apply;

        public MaterialContentV1UpdateService(MaterialContentV1DryRunService dryRun, MaterialContentV1ApplyService apply)
        {
            _dryRun = dryRun;
            _apply = apply;
        }

        public void Apply(SceneSyncMaterialContentV1Message msg)
        {
            if (!_dryRun.TryDryRun(msg, out var dryRunSummary, out var dryRunErr))
            {
                SceneSyncStateStore.MarkError(dryRunErr ?? "material_content_v1_dry_run_failed", msg != null ? msg.materialRef : null);
                return;
            }

            BlenderSyncLog.Trace(
                "Material",
                "content_dry_run_passed",
                () => dryRunSummary,
                () => new System.Collections.Generic.Dictionary<string, object>
                {
                    { "materialRef", msg?.materialRef },
                });
            if (_apply.TryApply(msg, out var applySummary, out var applyErr))
            {
                if (!string.IsNullOrWhiteSpace(applySummary) && applySummary.StartsWith("skip unchanged", StringComparison.Ordinal))
                    SceneSyncStateStore.MarkSkipped("material_content_v1_unchanged", msg.materialRef);
                else
                    SceneSyncStateStore.MarkApplied(msg.materialRef);
                MaterialReferenceApplyService.RetryPendingForMaterial(msg.materialRef);
                return;
            }

            SceneSyncStateStore.MarkError(applyErr ?? "material_content_v1_apply_failed", msg.materialRef);
        }
    }
}
