using System;
using System.Collections.Generic;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;

namespace BlenderSyncVNext.SessionCore
{
    // Honest runtime name for the current transport role.
    // Unity connects outward to Blender-hosted WS server.
    public sealed class SessionOutboundWsClient
    {
        public const string DefaultEndpoint = "ws://127.0.0.1:8765/ws/";

        private readonly object _stateLock = new object();
        private ClientWebSocket _ws;
        private CancellationTokenSource _cts;
        private SessionClient _session;
        private Task _runTask;
        private int _generation;

        public bool IsRunning
        {
            get
            {
                lock (_stateLock)
                {
                    return _runTask != null && !_runTask.IsCompleted;
                }
            }
        }

        public bool IsConnected
        {
            get
            {
                lock (_stateLock)
                {
                    return _ws != null && _ws.State == WebSocketState.Open;
                }
            }
        }

        public string Prefix { get; private set; }

        public bool IsFeatureNegotiated(string feature)
        {
            SessionClient session;
            lock (_stateLock)
                session = _session;
            return session != null && session.IsFeatureNegotiated(feature);
        }

        public void Start(SessionClient session, string prefix = DefaultEndpoint)
        {
            string endpoint;
            int generation;
            CancellationTokenSource cts;
            lock (_stateLock)
            {
                if (_runTask != null && !_runTask.IsCompleted) return;
                _session = session;
                Prefix = NormalizeEndpoint(prefix);
                endpoint = Prefix;
                cts = new CancellationTokenSource();
                _cts = cts;
                generation = Interlocked.Increment(ref _generation);
                _runTask = Task.Run(() => ConnectAndReceiveLoop(generation, endpoint, cts.Token));
            }
            BlenderSyncLog.Info(
                "Session",
                "connecting",
                "Connecting to the Blender WebSocket server.",
                new Dictionary<string, object> { { "endpoint", endpoint } });
        }

        public static string NormalizeEndpoint(string endpoint)
        {
            var normalized = endpoint?.Trim();
            return string.IsNullOrEmpty(normalized) ? DefaultEndpoint : normalized;
        }

        public void Stop()
        {
            Interlocked.Increment(ref _generation);
            CancellationTokenSource cts;
            ClientWebSocket ws;
            bool hadActiveTransport;
            lock (_stateLock)
            {
                cts = _cts;
                ws = _ws;
                hadActiveTransport = cts != null || ws != null || _runTask != null;
                _ws = null;
                _cts = null;
                _runTask = null;
            }
            try { cts?.Cancel(); } catch { }
            try
            {
                if (ws != null)
                {
                    if (ws.State == WebSocketState.Open)
                        ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", CancellationToken.None).Wait(500);
                    ws.Dispose();
                }
            }
            catch { }
            if (hadActiveTransport)
                BlenderSyncLog.Info(
                    "Session",
                    "transport_stopped",
                    "Stopped the Blender WebSocket transport.");
        }

        public void Send(string payload)
        {
            TrySend(payload);
        }

        public bool TrySend(string payload, Action<string> onFailure = null)
        {
            ClientWebSocket ws;
            CancellationToken ct;
            int generation;
            lock (_stateLock)
            {
                ws = _ws;
                ct = _cts != null ? _cts.Token : CancellationToken.None;
                generation = _generation;
            }

            if (string.IsNullOrWhiteSpace(payload))
            {
                onFailure?.Invoke("empty_payload");
                return false;
            }
            if (ws == null || ws.State != WebSocketState.Open || !IsCurrentGeneration(generation))
            {
                onFailure?.Invoke("not_connected");
                return false;
            }

            _ = SendAsync(ws, payload, ct, generation, onFailure);
            return true;
        }

        private async Task SendAsync(
            ClientWebSocket ws,
            string payload,
            CancellationToken ct,
            int generation,
            Action<string> onFailure)
        {
            try
            {
                var bytes = Encoding.UTF8.GetBytes(payload);
                await ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, ct);
            }
            catch (Exception ex)
            {
                if (IsCurrentGeneration(generation))
                    BlenderSyncLog.Warn(
                        "Session",
                        "send_failed",
                        ex.Message);
                try { onFailure?.Invoke(ex.Message); } catch { }
            }
        }

        private bool IsCurrentGeneration(int generation)
        {
            return Interlocked.CompareExchange(ref _generation, 0, 0) == generation;
        }

        private static bool IsRemoteClosedException(Exception ex)
        {
            if (ex == null)
                return false;

            if (HasSocketError(ex, SocketError.ConnectionReset) || HasSocketError(ex, SocketError.ConnectionAborted))
                return true;

            if (ex is WebSocketException wex)
            {
                var text = wex.ToString();
                if (text.IndexOf("closed the WebSocket connection", StringComparison.OrdinalIgnoreCase) >= 0)
                    return true;
                if (text.IndexOf("Unable to read data from the transport connection", StringComparison.OrdinalIgnoreCase) >= 0)
                    return true;
                if (text.IndexOf("远程主机强迫关闭了一个现有的连接", StringComparison.OrdinalIgnoreCase) >= 0)
                    return true;
            }

            return IsRemoteClosedException(ex.InnerException);
        }

        private static bool IsConnectionRefusedException(Exception ex)
        {
            if (ex == null)
                return false;

            if (HasSocketError(ex, SocketError.ConnectionRefused))
                return true;

            var text = ex.ToString();
            if (text.IndexOf("actively refused", StringComparison.OrdinalIgnoreCase) >= 0)
                return true;
            if (text.IndexOf("目标计算机积极拒绝", StringComparison.OrdinalIgnoreCase) >= 0)
                return true;
            if (text.IndexOf("Unable to connect to the remote server", StringComparison.OrdinalIgnoreCase) >= 0)
                return true;

            return IsConnectionRefusedException(ex.InnerException);
        }

        private static bool HasSocketError(Exception ex, SocketError socketError)
        {
            if (ex == null)
                return false;
            if (ex is SocketException socketException && socketException.SocketErrorCode == socketError)
                return true;
            return HasSocketError(ex.InnerException, socketError);
        }

        private async Task ConnectAndReceiveLoop(int generation, string endpoint, CancellationToken ct)
        {
            ClientWebSocket ws = null;
            try
            {
                ws = new ClientWebSocket();
                lock (_stateLock)
                {
                    if (!IsCurrentGeneration(generation))
                    {
                        ws.Dispose();
                        return;
                    }
                    _ws = ws;
                }
                await ws.ConnectAsync(new Uri(endpoint), ct);
                if (!IsCurrentGeneration(generation))
                {
                    ws.Dispose();
                    return;
                }

                _session?.MarkTransportConnected("ws_client_connected");

                async void AckHandler(string payload)
                {
                    try
                    {
                        if (!IsCurrentGeneration(generation) || ws.State != WebSocketState.Open) return;
                        if (string.IsNullOrWhiteSpace(payload))
                        {
                            BlenderSyncLog.Warn(
                                "Session",
                                "ack_payload_empty",
                                "Skipped an empty handshake acknowledgement payload.");
                            return;
                        }

                        var bytes = Encoding.UTF8.GetBytes(payload ?? string.Empty);
                        await ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, ct);
                    }
                    catch (Exception ex)
                    {
                        if (IsCurrentGeneration(generation))
                            BlenderSyncLog.Trace(
                                "Session",
                                "ack_send_failed",
                                () => ex.Message);
                    }
                }

                if (_session != null)
                {
                    _session.OnAckEmitRequested += AckHandler;
                    _session.AnnounceImportRoot();
                }

                // Strict handshake phase-0: Unity proactively sends hello after transport connected.
                var handshakeConfirmed = false;
                try
                {
                    var hello = "{" +
                                "\"type\":\"session_hello\"," +
                                "\"timestamp\":" + DateTimeOffset.UtcNow.ToUnixTimeSeconds() + "," +
                                "\"handshakeId\":\"hs-" + Guid.NewGuid().ToString("N") + "\"" +
                                SessionProtocolContract.OfferJsonFields() +
                                "}";
                    var helloBytes = Encoding.UTF8.GetBytes(hello);
                    await ws.SendAsync(new ArraySegment<byte>(helloBytes), WebSocketMessageType.Text, true, ct);
                }
                catch (Exception ex)
                {
                    if (IsCurrentGeneration(generation))
                        BlenderSyncLog.Trace(
                            "Session",
                            "hello_send_failed",
                            () => ex.Message);
                }

                var buffer = new byte[64 * 1024];
                var handshakeConfirmedFlag = 0;

                _ = Task.Run(async () =>
                {
                    try
                    {
                        await Task.Delay(TimeSpan.FromSeconds(5), ct);
                        if (ct.IsCancellationRequested)
                            return;
                        if (Interlocked.CompareExchange(ref handshakeConfirmedFlag, 0, 0) == 1)
                            return;
                        if (!IsCurrentGeneration(generation) || ws.State != WebSocketState.Open)
                            return;

                        BlenderSyncLog.Warn(
                            "Session",
                            "handshake_timed_out",
                            "Blender did not confirm the session handshake before the timeout.");
                        _session?.ReportError("handshake_timeout");
                        try
                        {
                            await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "handshake_timeout", CancellationToken.None);
                        }
                        catch { }
                    }
                    catch (OperationCanceledException)
                    {
                        // normal stop
                    }
                }, ct);

                try
                {
                    while (!ct.IsCancellationRequested && IsCurrentGeneration(generation) && ws.State == WebSocketState.Open)
                    {
                        var sb = new StringBuilder();
                        WebSocketReceiveResult result;
                        do
                        {
                            result = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), ct);
                            if (result.MessageType == WebSocketMessageType.Close)
                            {
                                await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", CancellationToken.None);
                                if (IsCurrentGeneration(generation))
                                    _session?.RequestDisconnectFromTransport("remote_close_frame");
                                return;
                            }
                            sb.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
                        }
                        while (!result.EndOfMessage);

                        var text = sb.ToString();
                        _session?.HandleIncoming(text);

                        if (_session != null && _session.Status == "protocol_mismatch")
                        {
                            try
                            {
                                await ws.CloseAsync(WebSocketCloseStatus.PolicyViolation, "protocol_mismatch", CancellationToken.None);
                            }
                            catch { }
                            return;
                        }

                        if (!handshakeConfirmed && _session != null && _session.Status == "handshake_confirmed")
                        {
                            handshakeConfirmed = true;
                            Interlocked.Exchange(ref handshakeConfirmedFlag, 1);
                        }
                    }
                }
                finally
                {
                    if (_session != null)
                        _session.OnAckEmitRequested -= AckHandler;
                }
            }
            catch (OperationCanceledException)
            {
                // normal stop
            }
            catch (Exception ex)
            {
                if (!IsCurrentGeneration(generation))
                    return;

                if (IsRemoteClosedException(ex))
                {
                    BlenderSyncLog.Info(
                        "Session",
                        "remote_closed",
                        "Blender closed the WebSocket connection.");
                    _session?.RequestDisconnectFromTransport("remote_closed");
                    return;
                }

                if (IsConnectionRefusedException(ex))
                {
                    BlenderSyncLog.Info(
                        "Session",
                        "server_not_ready",
                        "Blender is not accepting connections yet.");
                    _session?.MarkReadyListening("blender_ws_server_not_ready_click_blender_connect");
                    return;
                }

                BlenderSyncLog.Exception(
                    "Session",
                    "connect_receive_failed",
                    ex,
                    "The Blender WebSocket connection failed.");
                _session?.ReportError("ws_client_failed:" + ex.Message);
            }
            finally
            {
                CleanupSocketForCompletedRun(generation, ws);
            }
        }

        private void CleanupSocketForCompletedRun(int generation, ClientWebSocket ws)
        {
            if (ws == null)
                return;

            var shouldDispose = false;
            lock (_stateLock)
            {
                if (IsCurrentGeneration(generation) && ReferenceEquals(_ws, ws))
                {
                    _ws = null;
                    _cts = null;
                    _runTask = null;
                    shouldDispose = true;
                }
            }

            if (!shouldDispose)
                return;

            try { ws.Dispose(); } catch { }
        }
    }
}
