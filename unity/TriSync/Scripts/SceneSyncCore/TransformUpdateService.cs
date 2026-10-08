using System.Collections.Generic;
using UnityEngine;

namespace BlenderSyncVNext.SceneSyncCore
{
    public sealed class TransformUpdateService
    {
        private readonly Dictionary<string, TransformSnapshot> _lastApplied = new Dictionary<string, TransformSnapshot>();
        private readonly TransformApplyService _apply;

        private const float PositionThreshold = 0.001f;
        private const float RotationThresholdDeg = 0.1f;
        private const float ScaleThreshold = 0.001f;

        public TransformUpdateService(TransformApplyService apply)
        {
            _apply = apply;
        }

        public void RemovePair(string pairId)
        {
            pairId = NormalizePairId(pairId);
            if (string.IsNullOrWhiteSpace(pairId))
                return;
            _lastApplied.Remove(pairId);
        }

        public void Apply(Transform target, SceneSyncTransformMessage msg)
        {
            if (msg == null)
            {
                SceneSyncStateStore.MarkError("transform_message_missing");
                return;
            }
            if (string.IsNullOrWhiteSpace(msg.pairId))
            {
                SceneSyncStateStore.MarkError("transform_pair_id_missing");
                return;
            }
            msg.pairId = NormalizePairId(msg.pairId);

            if (!HasValidTransformPayload(msg))
            {
                SceneSyncStateStore.MarkError("invalid transform payload", msg.pairId);
                return;
            }

            if (IsBelowThreshold(msg))
            {
                SceneSyncStateStore.MarkSkipped("below_threshold", msg.pairId);
                return;
            }

            if (_apply.TryApply(target, msg, out var err))
            {
                _lastApplied[msg.pairId] = ToSnapshot(msg);
                SceneSyncStateStore.MarkApplied(msg.pairId);
                return;
            }

            SceneSyncStateStore.MarkError(err ?? "apply_failed", msg.pairId);
        }

        private static bool HasValidTransformPayload(SceneSyncTransformMessage msg)
        {
            return msg != null
                && msg.position != null && msg.position.Length == 3
                && msg.rotation != null && msg.rotation.Length == 4
                && msg.scale != null && msg.scale.Length == 3;
        }

        private bool IsBelowThreshold(SceneSyncTransformMessage msg)
        {
            var pairId = NormalizePairId(msg?.pairId);
            if (string.IsNullOrWhiteSpace(pairId) || !_lastApplied.TryGetValue(pairId, out var prev))
                return false; // first frame after map is always eligible

            var p = SceneSyncTransformMapper.MapPosition(msg.position);
            var r = SceneSyncTransformMapper.MapRotation(msg.rotation);
            var s = SceneSyncTransformMapper.MapScale(msg.scale);

            var posDelta = Vector3.Distance(prev.position, p);
            var rotDelta = Quaternion.Angle(prev.rotation, r);
            var scaleDelta = Vector3.Distance(prev.scale, s);

            return posDelta < PositionThreshold && rotDelta < RotationThresholdDeg && scaleDelta < ScaleThreshold;
        }

        private static string NormalizePairId(string pairId)
        {
            var normalized = pairId?.Trim();
            return string.IsNullOrEmpty(normalized) ? null : normalized;
        }

        private static TransformSnapshot ToSnapshot(SceneSyncTransformMessage msg)
        {
            return new TransformSnapshot
            {
                position = SceneSyncTransformMapper.MapPosition(msg.position),
                rotation = SceneSyncTransformMapper.MapRotation(msg.rotation),
                scale = SceneSyncTransformMapper.MapScale(msg.scale),
            };
        }

        private struct TransformSnapshot
        {
            public Vector3 position;
            public Quaternion rotation;
            public Vector3 scale;
        }
    }
}
