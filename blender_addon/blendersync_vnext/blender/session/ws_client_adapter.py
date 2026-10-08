from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any

_VENDOR_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "vendor"))
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from blender.common.exception_boundary import report_boundary_exception
from blender.common.log import trace, warn
from blender.session.main_thread_dispatcher import (
    enqueue_main_thread_message,
    is_main_thread_message_type,
)
from blender.session.protocol_contract import PROTOCOL_VERSION


MAX_INBOUND_MESSAGE_BYTES = 64 * 1024 * 1024


class MinimalWsClientHook:
    """Current vNext minimal WS runtime.

    Despite the historical filename, this now hosts a local WS server in Blender
    and lets Unity connect outward as a client, matching the legacy proven shape.
    """

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint
        self._host, self._port = self._parse_endpoint(endpoint)
        self._server = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.RLock()
        self._connections: set[Any] = set()
        self._last_error: str | None = None
        self._ready = threading.Event()
        self._first_client_observed = threading.Event()

    def connect(self) -> bool:
        from blender.ui.state_view import get_session

        session = get_session()
        session.set_endpoint(self._endpoint)

        try:
            with self._lock:
                if self._running and self._thread is not None and self._thread.is_alive():
                    self._last_error = None
                    return True

                self._running = True
                self._ready.clear()
                self._first_client_observed.clear()
                self._thread = threading.Thread(target=self._serve_forever, daemon=True)
                self._thread.start()
            self._ready.wait(2.0)

            if self._server is None:
                with self._lock:
                    self._running = False
                    self._thread = None
                if not self._last_error:
                    self._last_error = "ws_server_not_ready"
                session.set_transport_connected(False)
                session.set_last_error(self._last_error)
                return False

            self._last_error = None
            threading.Thread(target=self._watch_first_client_timeout, daemon=True).start()
            return True
        except Exception as exc:
            with self._lock:
                self._running = False
                self._thread = None
            self._last_error = f"ws_server_start_failed:{exc}"
            session.set_transport_connected(False)
            session.set_last_error(self._last_error)
            return False

    def disconnect(self) -> None:
        from blender.ui.state_view import get_session

        try:
            self._running = False
            with self._lock:
                for ws in list(self._connections):
                    try:
                        ws.close()
                    except Exception:
                        pass
                self._connections.clear()

            if self._server is not None:
                try:
                    self._server.shutdown()
                except Exception:
                    pass
                self._server = None
        finally:
            self._first_client_observed.clear()
            get_session().set_transport_connected(False)

    def install_outbound_handoff(self, handoff=None) -> None:
        from blender.ui.state_view import get_session

        get_session().set_outbound_raw_handoff(self.send_raw)

    def clear_outbound_handoff(self) -> None:
        from blender.ui.state_view import get_session

        get_session().set_outbound_raw_handoff(None)

    def send_raw(self, raw: str) -> bool:
        from blender.ui.state_view import get_session, set_last_ws_handoff_error

        session = get_session()
        with self._lock:
            live = list(self._connections)

        if not live:
            self._last_error = "ws_no_unity_client_connected"
            session.set_last_error(self._last_error)
            set_last_ws_handoff_error(self._last_error)
            return False

        failures = []
        delivered = False
        dead = []
        for ws in live:
            try:
                ws.send(raw)
                delivered = True
            except Exception as exc:
                failures.append(str(exc))
                dead.append(ws)

        if dead:
            with self._lock:
                for ws in dead:
                    self._connections.discard(ws)
                session.set_transport_connected(bool(self._connections))

        if not delivered:
            self._last_error = f"ws_send_failed:{'; '.join(failures) if failures else 'unknown'}"
            session.set_last_error(self._last_error)
            set_last_ws_handoff_error(self._last_error)
            return False

        self._last_error = None
        session.set_last_error(None)
        set_last_ws_handoff_error(None)
        return True

    @property
    def connected(self) -> bool:
        with self._lock:
            return bool(self._connections)

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _watch_first_client_timeout(self) -> None:
        from blender.ui.state_view import get_session, set_last_ws_handoff_error

        if self._first_client_observed.wait(5.0):
            return

        if not self._running:
            return

        session = get_session()
        with self._lock:
            has_connections = bool(self._connections)

        if has_connections:
            return

        self._last_error = "handshake_timeout"
        session.set_last_error("handshake_timeout")
        set_last_ws_handoff_error(self._last_error)
        warn(
            "Session",
            "unity_client_timeout",
            "Timed out waiting for Unity to connect.",
            {"endpoint": self._endpoint},
        )

    def _serve_forever(self) -> None:
        try:
            with serve(self._handler, self._host, self._port, max_size=MAX_INBOUND_MESSAGE_BYTES) as server:
                self._server = server
                self._ready.set()
                server.serve_forever()
        except Exception as exc:
            self._last_error = f"ws_server_runtime_failed:{exc}"
            report_boundary_exception(
                "ws_server",
                exc,
                message=f"[vNext][SessionWs] SERVER_ERROR error={exc}",
            )
            self._ready.set()
            try:
                from blender.ui.state_view import get_session, set_last_ws_handoff_error

                session = get_session()
                session.set_transport_connected(False)
                session.set_last_error(self._last_error)
                set_last_ws_handoff_error(self._last_error)
            except Exception:
                pass
        finally:
            self._server = None
            self._ready.set()

    def _handler(self, websocket) -> None:
        from blender.session.entrypoints import ingest_final_handshake_ack_once, ingest_session_ack_once
        from blender.ui.state_view import clear_session_connection_errors, get_session

        session = get_session()
        stale_connections: list[Any] = []
        with self._lock:
            stale_connections = [ws for ws in self._connections if ws is not websocket]
            self._connections.clear()
            self._connections.add(websocket)
        for stale in stale_connections:
            try:
                stale.close()
            except Exception:
                pass
        self._first_client_observed.set()
        session.set_current_handshake_id(None)
        session.set_assets_import_root(None)
        session.set_texture_export_root(None)
        session.set_transport_connected(True)
        self._last_error = None
        clear_session_connection_errors()

        handshake_deadline = time.monotonic() + 5.0

        try:
            while self._running:
                if not self._is_active_connection(websocket):
                    break
                if not session.is_alive() and time.monotonic() > handshake_deadline:
                    session.set_last_error("handshake_timeout")
                    self._last_error = "handshake_timeout"
                    warn(
                        "Session",
                        "handshake_timeout",
                        "Timed out waiting for the final Unity handshake acknowledgement.",
                    )
                    try:
                        websocket.close()
                    except Exception:
                        pass
                    break
                try:
                    raw = websocket.recv(timeout=0.5)
                except TimeoutError:
                    continue
                except ConnectionClosed:
                    break

                if not self._is_active_connection(websocket):
                    break

                if raw is None:
                    continue

                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")

                if raw == "ping":
                    try:
                        websocket.send("pong")
                    except Exception:
                        pass
                    continue

                try:
                    payload = json.loads(raw)
                except Exception:
                    continue

                if not isinstance(payload, dict):
                    continue

                msg_type = payload.get("type")
                if not self._is_message_allowed_before_handshake(msg_type) and not session.is_alive():
                    trace(
                        "Session",
                        "message_before_handshake_dropped",
                        lambda: "Dropped an inbound message before the handshake completed.",
                        lambda: {"messageType": msg_type},
                    )
                    continue

                if msg_type == "session_hello":
                    handshake_id = payload.get("handshakeId")
                    if isinstance(handshake_id, str) and handshake_id:
                        trace(
                            "Session",
                            "hello_received",
                            lambda: "Received the Unity session hello.",
                        )
                        session.set_current_handshake_id(handshake_id)
                        negotiation = session.negotiate_protocol(payload)
                        if not negotiation.ok:
                            self._send_handshake_reject(
                                websocket,
                                handshake_id,
                                negotiation.reason or "protocol_negotiation_failed",
                                negotiation.peer_version,
                            )
                            break
                        if negotiation.legacy:
                            warn(
                                "Session",
                                "legacy_protocol",
                                "Connected to a peer without versioned protocol negotiation.",
                            )
                        session.mark_counterpart_observed()
                        session_ack = {
                            "type": "session_ack",
                            "timestamp": int(time.time()),
                            "handshakeId": handshake_id,
                            "criterion": "hello_observed",
                            "targetEndpoint": session.get_endpoint(),
                        }
                        session_ack.update(session.get_handshake_reply_fields())
                        try:
                            websocket.send(json.dumps(session_ack, ensure_ascii=False))
                            trace(
                                "Session",
                                "session_ack_sent",
                                lambda: "Sent the initial session acknowledgement.",
                            )
                        except Exception as exc:
                            self._last_error = f"ws_send_session_ack_failed:{exc}"
                elif msg_type == "session_ack":
                    # Compatibility path only (new strict flow should not need Blender to ingest phase-1 ack).
                    r = ingest_session_ack_once(payload)
                    if not r.get("ok"):
                        warn(
                            "Session",
                            "session_ack_rejected",
                            r.get("reason") or "ack_rejected",
                        )
                elif msg_type == "session.handshake_ack":
                    r = ingest_final_handshake_ack_once(payload)
                    if not r.get("ok"):
                        warn(
                            "Session",
                            "final_ack_rejected",
                            r.get("reason") or "ack_rejected",
                        )
                        if "negotiation_echo_mismatch" in str(r.get("reason") or ""):
                            self._send_handshake_reject(
                                websocket,
                                str(payload.get("handshakeId") or ""),
                                "negotiation_echo_mismatch",
                                payload.get("protocolVersion") if isinstance(payload.get("protocolVersion"), int) else None,
                            )
                            break
                    else:
                        trace(
                            "Session",
                            "handshake_confirmed",
                            lambda: "Confirmed the Unity session handshake.",
                        )
                        self._last_error = None
                        clear_session_connection_errors()
                        confirmed = {
                            "type": "session.handshake_confirmed",
                            "timestamp": int(time.time()),
                            "handshakeId": payload.get("handshakeId"),
                            "targetEndpoint": session.get_endpoint(),
                        }
                        confirmed.update(session.get_handshake_reply_fields())
                        try:
                            websocket.send(json.dumps(confirmed, ensure_ascii=False))
                        except Exception as exc:
                            self._last_error = f"ws_send_handshake_confirmed_failed:{exc}"
                elif msg_type == "session.handshake_reject":
                    error = session.record_handshake_reject(payload)
                    self._last_error = error
                    warn("Session", "handshake_rejected", error or "handshake_rejected")
                    try:
                        websocket.close()
                    except Exception:
                        pass
                    break
                elif msg_type == "session.import_root":
                    root = payload.get("assetsImportRoot")
                    texture_root = payload.get("textureExportRoot")
                    session.set_assets_import_root(root)
                    session.set_texture_export_root(texture_root)
                    trace(
                        "Session",
                        "import_roots_received",
                        lambda: "Received Unity import-root settings.",
                    )
                elif is_main_thread_message_type(msg_type):
                    result = enqueue_main_thread_message(payload)
                    if not result.get("ok"):
                        warn(
                            "Session",
                            "main_thread_message_rejected",
                            result.get("reason") or "queue_rejected",
                            {
                                "messageType": msg_type,
                                "pendingCount": result.get("pending"),
                                "capacity": result.get("capacity"),
                            },
                        )
        except Exception as exc:
            self._last_error = f"ws_handler_failed:{exc}"
            session.set_last_error(self._last_error)
            report_boundary_exception(
                "ws_connection_handler",
                exc,
                message=f"[vNext][SessionWs] HANDLER_ERROR error={exc}",
            )
            try:
                websocket.close()
            except Exception:
                pass
        finally:
            with self._lock:
                self._connections.discard(websocket)
                has_connections = bool(self._connections)
            session.set_transport_connected(has_connections)

    def _is_active_connection(self, websocket) -> bool:
        with self._lock:
            return websocket in self._connections

    def _send_handshake_reject(
        self,
        websocket,
        handshake_id: str,
        reason: str,
        peer_protocol_version: int | None,
    ) -> None:
        from blender.ui.state_view import get_session

        session = get_session()
        payload = {
            "type": "session.handshake_reject",
            "timestamp": int(time.time()),
            "handshakeId": str(handshake_id or ""),
            "reason": str(reason or "protocol_negotiation_failed"),
            "localProtocolVersion": PROTOCOL_VERSION,
            "peerProtocolVersion": peer_protocol_version,
            "targetEndpoint": session.get_endpoint(),
        }
        try:
            websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            self._last_error = f"ws_send_handshake_reject_failed:{exc}"
        try:
            websocket.close()
        except Exception:
            pass

    @staticmethod
    def _parse_endpoint(endpoint: str) -> tuple[str, int]:
        # expects ws://host:port/path ; current minimal slice ignores path routing.
        rest = endpoint.replace("ws://", "", 1)
        host_port = rest.split("/", 1)[0]
        if ":" not in host_port:
            raise ValueError(f"endpoint_missing_port:{endpoint}")
        host, port_str = host_port.rsplit(":", 1)
        return host, int(port_str)

    @staticmethod
    def _is_message_allowed_before_handshake(msg_type: Any) -> bool:
        return msg_type in {
            "session_hello",
            "session_ack",
            "session.handshake_ack",
            "session.handshake_reject",
            "session.import_root",
        }
