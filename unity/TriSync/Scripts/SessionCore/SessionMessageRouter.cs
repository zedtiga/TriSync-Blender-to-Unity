using System;
using System.Text;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;

namespace BlenderSyncVNext.SessionCore
{
    internal sealed class SessionMessageHandlers
    {
        public Action<string> OnMessage;
        public Action<string> OnIncomingPayload;
        public Action<string> OnSessionHello;
        public Action<string> OnSessionAck;
        public Action<string> OnFinalHandshakeAck;
        public Action<string> OnHandshakeConfirmed;
        public Action<string> OnHandshakeReject;
        public Action<string> OnUnityMeshImportResult;
        public Func<string, bool> IsFeatureNegotiated;
        public Action<string> OnTransform;
        public Action<string> OnHierarchy;
        public Action<string> OnObjectName;
        public Action<string> OnVisibility;
        public Action<string> OnAutoSyncState;
        public Action<string> OnObjectStateUpdate;
        public Action<string> OnViewState;
        public Action<string> OnMeshFork;
        public Action<string> OnMeshUpdate;
        public Action<string> OnMeshUpdateBinary;
        public Action<string> OnMeshPositionsUpdate;
        public Action<string> OnMeshUvUpdate;
        public Action<string> OnPreviewCommit;
        public Action<string> OnPreviewCommitMesh;
        public Action<string> OnReferenceChange;
        public Action<string> OnBlendShapeWeights;
        public Action<string> OnMaterialContentV1;
        public Action<string> OnObjectAssembly;
        public Action<string> OnObjectRemove;
        public Action<string> OnAssetEnvelope;
        public Action<string> OnRiggedObject;
        public Action<string> OnRiggedPose;
        public Action<string> OnRiggedBlendShapeWeights;
        public Action<string> OnAnimationClip;
    }

    internal static class SessionMessageRouter
    {
        public static void Route(string rawJson, SessionMessageHandlers handlers)
        {
            try
            {
                if (!string.IsNullOrWhiteSpace(rawJson))
                {
                    var env = JsonUtility.FromJson<SessionIncomingTypeEnvelope>(rawJson);
                    if (env != null)
                    {
                        if (RouteKnownType(rawJson, env.type, handlers))
                            return;
                    }
                }
            }
            catch (Exception ex)
            {
                BlenderSyncLog.Exception(
                    "Session",
                    "incoming_route_failed",
                    ex,
                    "An incoming TriSync message could not be routed.");
            }

            handlers?.OnMessage?.Invoke(rawJson);
        }

        private static bool RouteKnownType(string rawJson, string messageType, SessionMessageHandlers handlers)
        {
            var normalizedType = (messageType ?? string.Empty).Trim();
            switch (normalizedType)
            {
                case "session_hello":
                    handlers?.OnSessionHello?.Invoke(rawJson);
                    handlers?.OnMessage?.Invoke(rawJson);
                    return true;
                case "session_ack":
                    handlers?.OnSessionAck?.Invoke(rawJson);
                    handlers?.OnMessage?.Invoke(rawJson);
                    return true;
                case "session.handshake_ack":
                    handlers?.OnFinalHandshakeAck?.Invoke(rawJson);
                    handlers?.OnMessage?.Invoke(rawJson);
                    return true;
                case "session.handshake_confirmed":
                    handlers?.OnHandshakeConfirmed?.Invoke(rawJson);
                    handlers?.OnMessage?.Invoke(rawJson);
                    return true;
                case "session.handshake_reject":
                    handlers?.OnHandshakeReject?.Invoke(rawJson);
                    handlers?.OnMessage?.Invoke(rawJson);
                    return true;
                case "unity_mesh.import_result_v1":
                    handlers?.OnUnityMeshImportResult?.Invoke(rawJson);
                    break;
                case "scene_sync.transform":
                    handlers?.OnTransform?.Invoke(rawJson);
                    break;
                case "scene_sync.hierarchy":
                    handlers?.OnHierarchy?.Invoke(rawJson);
                    break;
                case "scene_sync.object_name":
                    handlers?.OnObjectName?.Invoke(rawJson);
                    break;
                case "scene_sync.visibility":
                    handlers?.OnVisibility?.Invoke(rawJson);
                    break;
                case "scene_sync.auto_sync_state":
                    handlers?.OnAutoSyncState?.Invoke(rawJson);
                    break;
                case "scene_sync.object_state_update_v1":
                    handlers?.OnObjectStateUpdate?.Invoke(rawJson);
                    break;
                case "scene_sync.view_state_v1":
                    handlers?.OnViewState?.Invoke(rawJson);
                    break;
                case "scene_sync.mesh_fork":
                    handlers?.OnMeshFork?.Invoke(rawJson);
                    break;
                case "scene_sync.mesh_update":
                    handlers?.OnMeshUpdate?.Invoke(rawJson);
                    break;
                case "scene_sync.mesh_update_binary_v1":
                    handlers?.OnMeshUpdateBinary?.Invoke(rawJson);
                    break;
                case "scene_sync.mesh_positions_update_v1":
                    handlers?.OnMeshPositionsUpdate?.Invoke(rawJson);
                    break;
                case "scene_sync.mesh_uv_update_v1":
                    handlers?.OnMeshUvUpdate?.Invoke(rawJson);
                    break;
                case "scene_sync.preview_commit":
                    handlers?.OnPreviewCommit?.Invoke(rawJson);
                    break;
                case "scene_sync.preview_commit_mesh_v1":
                    handlers?.OnPreviewCommitMesh?.Invoke(rawJson);
                    break;
                case "scene_sync.reference_change":
                    handlers?.OnReferenceChange?.Invoke(rawJson);
                    break;
                case "scene_sync.blendshape_weights_v1":
                    handlers?.OnBlendShapeWeights?.Invoke(rawJson);
                    break;
                case "scene_sync.material_content_v1":
                    handlers?.OnMaterialContentV1?.Invoke(rawJson);
                    break;
                case "scene_sync.object_assembly":
                    handlers?.OnObjectAssembly?.Invoke(rawJson);
                    break;
                case "scene_sync.object_remove":
                    handlers?.OnObjectRemove?.Invoke(rawJson);
                    break;
                case "asset_bridge_import_mvp":
                    handlers?.OnAssetEnvelope?.Invoke(rawJson);
                    break;
                case "asset_bridge.rigged_object":
                case "unity_rig_v1":
                    handlers?.OnRiggedObject?.Invoke(rawJson);
                    break;
                case "asset_bridge.rigged_pose_v1":
                    handlers?.OnRiggedPose?.Invoke(rawJson);
                    break;
                case "asset_bridge.rigged_blendshape_weights_v1":
                    handlers?.OnRiggedBlendShapeWeights?.Invoke(rawJson);
                    break;
                case "asset_bridge.animation_clip":
                    handlers?.OnAnimationClip?.Invoke(rawJson);
                    break;
                default:
                    if (normalizedType.StartsWith("large_payload.", StringComparison.Ordinal))
                    {
                        var checksumRequired = handlers?.IsFeatureNegotiated?.Invoke(SessionProtocolContract.LargePayloadCrc32Feature) == true;
                        var assembled = LargePayloadAssembler.HandleEnvelope(rawJson, normalizedType, checksumRequired);
                        if (!string.IsNullOrEmpty(assembled))
                        {
                            BlenderSyncLog.Trace(
                                "Session",
                                "large_payload_assembled",
                                () => "Reassembled a chunked TriSync payload.",
                                () => new System.Collections.Generic.Dictionary<string, object>
                                {
                                    { "bytes", Encoding.UTF8.GetByteCount(assembled) },
                                });
                            handlers?.OnIncomingPayload?.Invoke(assembled);
                        }
                    }
                    break;
            }

            handlers?.OnMessage?.Invoke(rawJson);
            return true;
        }

#pragma warning disable 0649 // JsonUtility populates this DTO field via reflection.
        [Serializable]
        private sealed class SessionIncomingTypeEnvelope
        {
            public string type;
        }
#pragma warning restore 0649
    }
}
