using System;
using System.Collections.Generic;
using System.Threading;
using BlenderSyncVNext.AssetBridgeCore;
using BlenderSyncVNext.Diagnostics;
using BlenderSyncVNext.SceneSyncCore;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SessionCore
{
    public sealed class SessionClient
    {
        public const string UnityMeshImportResultFeature = SessionProtocolContract.UnityMeshImportResultFeature;

        public event Action<string> OnMessage;
        public event Action<string> OnSceneSyncTransformRaw;
        public event Action<string> OnSceneSyncHierarchyRaw;
        public event Action<string> OnSceneSyncMeshForkRaw;
        public event Action<string> OnSceneSyncMeshUpdateRaw;
        public event Action<string> OnSceneSyncReferenceChangeRaw;
        public event Action<string> OnSceneSyncMaterialContentV1Raw;
        public event Action<string> OnSceneSyncObjectAssemblyRaw;
        public event Action<string> OnAckEmitRequested;
        public event Action<string> OnError;
        public event Action<string> OnStatusChanged;
        public static event Action<string> UnityMeshImportResultReceived;

        private static readonly SceneSyncCoordinator SharedSceneSyncCoordinator = new SceneSyncCoordinator();
#if UNITY_EDITOR
        private static readonly object TransformPumpLock = new object();
        private static bool _transformPumpInstalled;
        private static SessionClient _activeSession;
        private readonly SynchronizationContext _mainThreadContext;
        private readonly int _mainThreadId;
#endif

        private readonly SessionLifecycleTracker _lifecycle = new SessionLifecycleTracker();
        private readonly SessionHandshakeController _handshake;
        private readonly SessionMessageHandlers _messageHandlers;
        public string Status => _lifecycle.Status;

        public string CurrentHandshakeId => _handshake.CurrentHandshakeId;

        public static IReadOnlyList<string> GetLifecycleTrace() => SessionLifecycleTracker.GetLifecycleTrace();
        public static IReadOnlyList<string> GetAckTrace() => SessionHandshakeController.GetAckTrace();
        public static string LastLifecycleState => SessionLifecycleTracker.LastLifecycleState;
        public static long LastLifecycleAt => SessionLifecycleTracker.LastLifecycleAt;
        public static string LastAckPayload => SessionHandshakeController.LastAckPayload;
        public static string LastProtocolError => SessionHandshakeController.LastProtocolError;
        public bool LegacyProtocol => _handshake.LegacyProtocol;
        public int? PeerProtocolVersion => _handshake.PeerProtocolVersion;
        public string PeerApplicationVersion => _handshake.PeerApplicationVersion;
        public string[] NegotiatedFeatures => _handshake.NegotiatedFeatures;
        public bool IsFeatureNegotiated(string feature) => _handshake.IsFeatureNegotiated(feature);

        // Strict handshake boundary (B + long connection):
        // session_hello -> session_ack -> session.handshake_ack -> session.handshake_confirmed.
        public static string HandshakeCriterion => SessionHandshakeController.HandshakeCriterion;
        public static SessionOutboundWsClient SharedTransport { get; } = new SessionOutboundWsClient();
        public SessionClient()
        {
#if UNITY_EDITOR
            _mainThreadContext = SynchronizationContext.Current;
            _mainThreadId = Thread.CurrentThread.ManagedThreadId;
#endif
            _handshake = new SessionHandshakeController(
                payload => OnAckEmitRequested?.Invoke(payload),
                MarkCounterpartObserved,
                MarkHandshakeConfirmed,
                ReportProtocolError);
            _messageHandlers = new SessionMessageHandlers
            {
                OnMessage = raw => OnMessage?.Invoke(raw),
                OnIncomingPayload = HandleIncoming,
                OnSessionHello = raw =>
                {
                    LargePayloadAssembler.ResetPendingAssemblies();
                    _handshake.HandleSessionHello(raw);
                },
                OnSessionAck = _handshake.HandleSessionAck,
                OnFinalHandshakeAck = _handshake.HandleFinalHandshakeAck,
                OnHandshakeConfirmed = _handshake.HandleHandshakeConfirmed,
                OnHandshakeReject = _handshake.HandleHandshakeReject,
                OnUnityMeshImportResult = raw => UnityMeshImportResultReceived?.Invoke(raw),
                IsFeatureNegotiated = _handshake.IsFeatureNegotiated,
                OnTransform = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueTransformRaw, OnSceneSyncTransformRaw),
                OnHierarchy = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueHierarchyRaw, OnSceneSyncHierarchyRaw),
                OnObjectName = SessionPendingMessages.EnqueueObjectNameRaw,
                OnVisibility = SessionPendingMessages.EnqueueVisibilityRaw,
                OnAutoSyncState = SessionPendingMessages.EnqueueAutoSyncStateRaw,
                OnObjectStateUpdate = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueObjectStateRaw),
                OnViewState = SessionPendingMessages.EnqueueViewStateRaw,
                OnMeshFork = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueMeshForkRaw, OnSceneSyncMeshForkRaw),
                OnMeshUpdate = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueMeshRaw, OnSceneSyncMeshUpdateRaw),
                OnMeshUpdateBinary = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueMeshBinaryRaw, OnSceneSyncMeshUpdateRaw),
                OnMeshPositionsUpdate = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueMeshPositionsRaw, OnSceneSyncMeshUpdateRaw),
                OnMeshUvUpdate = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueMeshUvRaw, OnSceneSyncMeshUpdateRaw),
                OnPreviewCommit = SessionPendingMessages.EnqueuePreviewCommitRaw,
                OnPreviewCommitMesh = SessionPendingMessages.EnqueuePreviewCommitMeshRaw,
                OnReferenceChange = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueReferenceRaw, OnSceneSyncReferenceChangeRaw),
                OnBlendShapeWeights = SessionPendingMessages.EnqueueBlendShapeWeightsRaw,
                OnMaterialContentV1 = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueMaterialContentV1Raw, OnSceneSyncMaterialContentV1Raw),
                OnObjectAssembly = raw => EnqueuePending(raw, SessionPendingMessages.EnqueueObjectAssemblyRaw, OnSceneSyncObjectAssemblyRaw),
                OnObjectRemove = SessionPendingMessages.EnqueueObjectRemoveRaw,
                OnAssetEnvelope = SessionPendingMessages.EnqueueAssetEnvelopeRaw,
                OnRiggedObject = SessionPendingMessages.EnqueueRiggedObjectRaw,
                OnRiggedPose = SessionPendingMessages.EnqueueRiggedPoseRaw,
                OnRiggedBlendShapeWeights = SessionPendingMessages.EnqueueRiggedBlendShapeWeightsRaw,
                OnAnimationClip = raw => new BlenderSyncVNext.AnimationClipImport.AnimationClipImportCoordinator().HandleEnvelope(raw),
            };
        }

        public void Connect(string endpoint)
        {
#if UNITY_EDITOR
            if (!EditModeGuard.IsAvailable)
            {
                SetStatus("edit_mode_required", "play_mode");
                return;
            }
#endif
            endpoint = SessionOutboundWsClient.NormalizeEndpoint(endpoint);
            if (SharedTransport.IsRunning)
            {
                _lifecycle.TraceOnly("connect_ignored_already_running", endpoint);
                return;
            }

            EnsureTransformPump();
            LargePayloadAssembler.ResetPendingAssemblies();
            _handshake.ResetForConnect(endpoint);
            _lifecycle.TraceOnly("connect_attempted", endpoint);
            SetStatus("local_ready", endpoint);
#if UNITY_EDITOR
            _activeSession = this;
#endif
            SharedTransport.Start(this, endpoint);
        }

        public void MarkReadyListening(string endpoint = "manual_host_ready")
        {
            _handshake.SetEndpoint(endpoint);
            SetStatus("ready_listening", endpoint);
        }

        public void ClearReadyListening(string reason = "manual_ready_cleared")
        {
            SetStatus("ready_cleared", reason);
        }

        public void Disconnect()
        {
            try { SharedTransport.Stop(); } catch { }
            LargePayloadAssembler.ResetPendingAssemblies();
#if UNITY_EDITOR
            SessionPendingMessages.Clear();
            SceneSyncTransformSmoother.CompleteAll();
            if (ReferenceEquals(_activeSession, this))
                _activeSession = null;
#endif
            SetStatus("disconnected");
        }

        public void RequestDisconnectFromTransport(string reason = "transport_closed")
        {
#if UNITY_EDITOR
            if (_mainThreadContext != null && Thread.CurrentThread.ManagedThreadId != _mainThreadId)
            {
                _mainThreadContext.Post(_ => Disconnect(), null);
                return;
            }
#endif
            Disconnect();
        }

        public void MarkCounterpartObserved(string detail = null)
        {
            SetStatus("counterpart_observed", detail);
        }

        public void MarkTransportConnected(string detail = null)
        {
            SetStatus("transport_connected", detail);
        }

        // Reserved for future stricter handshake model with explicit accept/ack delivered and ingested.
        public void MarkHandshakeConfirmed(string detail = null)
        {
            SetStatus("handshake_confirmed", detail);
        }

        // Transport adapter should call this on incoming payload.
        public void HandleIncoming(string rawJson)
        {
#if UNITY_EDITOR
            if (!EditModeGuard.IsAvailable)
                return;
#endif
            SessionMessageRouter.Route(rawJson, _messageHandlers);
        }

        public void ReportError(string error)
        {
            LargePayloadAssembler.ResetPendingAssemblies();
            SetStatus("error", error);
            OnError?.Invoke(error);
        }

        public void ReportProtocolError(string error)
        {
            LargePayloadAssembler.ResetPendingAssemblies();
            SetStatus("protocol_mismatch", error);
            OnError?.Invoke(error);
        }

        public static void SendToBlender(string payload)
        {
            TrySendToBlender(payload);
        }

        public static bool TrySendToBlender(string payload, Action<string> onFailure = null)
        {
#if UNITY_EDITOR
            if (!EditModeGuard.IsAvailable)
            {
                onFailure?.Invoke("edit_mode_required");
                return false;
            }
#endif
            if (string.IsNullOrWhiteSpace(payload))
            {
                onFailure?.Invoke("payload_empty");
                return false;
            }
            try
            {
                return SharedTransport.TrySend(payload, onFailure);
            }
            catch (Exception ex)
            {
                BlenderSyncLog.Exception(
                    "Session",
                    "send_failed",
                    ex,
                    "The outgoing TriSync message could not be queued.");
                onFailure?.Invoke(ex.Message);
                return false;
            }
        }

        public static void UnregisterMappedTarget(string pairId)
        {
            SharedSceneSyncCoordinator.UnregisterMappedTarget(pairId);
        }

        private static void EnqueuePending(string rawJson, Action<string> enqueue, Action<string> notify = null)
        {
            enqueue(rawJson);
            notify?.Invoke(rawJson);
        }

        private static void EnsureTransformPump()
        {
#if UNITY_EDITOR
            lock (TransformPumpLock)
            {
                if (_transformPumpInstalled) return;
                EditorApplication.update += PumpPendingTransformRaw;
                _transformPumpInstalled = true;
            }
#endif
        }

        private static void PumpPendingTransformRaw()
        {
#if UNITY_EDITOR
            if (!EditModeGuard.IsAvailable)
            {
                SessionPendingMessages.Clear();
                return;
            }
#endif
            var handledAny = SessionPendingMessages.Pump(SharedSceneSyncCoordinator);
#if UNITY_EDITOR
            if (handledAny)
            {
                EditorApplication.QueuePlayerLoopUpdate();
                SceneView.RepaintAll();
            }
#endif
        }

#if UNITY_EDITOR
        [InitializeOnLoadMethod]
        private static void InstallPlayModeBoundary()
        {
            EditorApplication.playModeStateChanged -= HandlePlayModeStateChanged;
            EditorApplication.playModeStateChanged += HandlePlayModeStateChanged;
        }

        private static void HandlePlayModeStateChanged(PlayModeStateChange state)
        {
            if (state != PlayModeStateChange.ExitingEditMode &&
                state != PlayModeStateChange.EnteredPlayMode)
            {
                return;
            }

            var session = _activeSession;
            if (session != null)
                session.Disconnect();
            else
            {
                try { SharedTransport.Stop(); } catch { }
                LargePayloadAssembler.ResetPendingAssemblies();
                SessionPendingMessages.Clear();
                SceneSyncTransformSmoother.CompleteAll();
            }
        }
#endif

        public void AnnounceImportRoot()
        {
            _handshake.AnnounceImportRoot();
        }

        private void SetStatus(string state, string detail = null)
        {
            _lifecycle.SetStatus(state, detail, status => OnStatusChanged?.Invoke(status));
        }
    }
}
