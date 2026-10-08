using System.Collections.Generic;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class BlendShapeWeightsUpdateService
    {
        private static readonly Dictionary<string, double> LastLogAtByPair = new Dictionary<string, double>();
        private static readonly object LastLogLock = new object();
        private readonly BlendShapeWeightApplyService _apply;

        public BlendShapeWeightsUpdateService(BlendShapeWeightApplyService apply)
        {
            _apply = apply;
        }

        public void Apply(GameObject target, SceneSyncBlendShapeWeightsMessage msg)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("blendshape_weights_message_missing");
                return;
            }
            if (string.IsNullOrWhiteSpace(msg.pairId))
            {
                SceneSyncStateStore.MarkError("blendshape_weights_pair_id_missing");
                return;
            }
            msg.pairId = NormalizePairId(msg.pairId);

            if (!_apply.TryApply(target, msg, out var err, out var applied, out var missing))
            {
                SceneSyncStateStore.MarkError(err ?? "blendshape_weights_apply_failed", msg.pairId);
                return;
            }

            if (missing > 0 || BlenderSyncLog.VerboseEnabled)
            {
                var now = Time.realtimeSinceStartupAsDouble;
                var shouldLog = false;
                lock (LastLogLock)
                {
                    var hasLastLog = LastLogAtByPair.TryGetValue(msg.pairId, out var lastLogAt);
                    if (!hasLastLog || now - lastLogAt >= 1.0)
                    {
                        LastLogAtByPair[msg.pairId] = now;
                        shouldLog = true;
                    }
                }

                if (shouldLog)
                {
                    var fields = new Dictionary<string, object>
                    {
                        { "pairId", msg.pairId },
                        { "applied", applied },
                        { "missing", missing },
                        { "weights", msg.weights != null ? msg.weights.Length : 0 },
                    };
                    if (missing > 0)
                    {
                        BlenderSyncLog.Warn(
                            "BlendShape",
                            "weights_partially_applied",
                            "Some BlendShape weights could not be matched.",
                            fields);
                    }
                    else
                    {
                        BlenderSyncLog.Trace(
                            "BlendShape",
                            "weights_applied",
                            () => "Applied BlendShape weights.",
                            () => fields);
                    }
                }
            }

            SceneSyncStateStore.MarkApplied(msg.pairId);
        }

        private static string NormalizePairId(string pairId)
        {
            var normalized = pairId?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }
    }
}
