from __future__ import annotations

import base64
import json
import math
import threading
import time
import uuid
from typing import Callable

from blender.common.constants import MAX_PAYLOAD_BYTES
from blender.common.errors import SessionError
from blender.common.types import SendResult, SessionState
from blender.session.ack_contract import validate_final_handshake_ack_contract, validate_session_ack_contract
from blender.session.crc32_ieee import crc32_ieee_hex
from blender.session.protocol_contract import (
    LARGE_PAYLOAD_CRC32_FEATURE,
    ProtocolNegotiation,
    build_protocol_error,
    negotiate_protocol_offer,
    protocol_reply_fields,
    validate_protocol_echo,
)


class BlenderSessionCore:
    def __init__(self, endpoint: str = "local://sprint0", application_version: str | None = None) -> None:
        self._state = SessionState(
            local_ready=False,
            connect_attempted=False,
            transport_connected=False,
            counterpart_observed=False,
            handshake_confirmed=False,
            endpoint=endpoint,
            last_error=None,
            assets_import_root=None,
            texture_export_root=None,
        )
        self._outbound_raw_handoff: Callable[[str], bool] | None = None
        self._lock = threading.RLock()
        self._application_version = str(application_version or "").strip() or None

    def connect(self) -> bool:
        with self._lock:
            self._state.connect_attempted = True
            self._state.local_ready = True
            # Strict handshake stage: transport/counterpart/handshake are external-signal driven.
            self._state.transport_connected = False
            self._state.counterpart_observed = False
            self._state.handshake_confirmed = False
            self._state.current_handshake_id = f"hs-{uuid.uuid4().hex}"
            self._state.last_error = None
            self._state.assets_import_root = None
            self._state.texture_export_root = None
            self._reset_protocol_negotiation_locked()
            return True

    def disconnect(self) -> None:
        with self._lock:
            self._state.local_ready = False
            self._state.connect_attempted = False
            self._state.transport_connected = False
            self._state.counterpart_observed = False
            self._state.handshake_confirmed = False
            self._state.current_handshake_id = None
            self._state.assets_import_root = None
            self._state.texture_export_root = None
            self._state.last_error = None
            self._reset_protocol_negotiation_locked()

    def is_alive(self) -> bool:
        # Strict mode: business send is allowed only after handshake is confirmed.
        with self._lock:
            return self._state.handshake_confirmed

    def get_truth_state(self) -> dict:
        with self._lock:
            return {
                "local_ready": self._state.local_ready,
                "connect_attempted": self._state.connect_attempted,
                "transport_connected": self._state.transport_connected,
                "counterpart_observed": self._state.counterpart_observed,
                "handshake_confirmed": self._state.handshake_confirmed,
                "current_handshake_id": self._state.current_handshake_id,
                "handshake_phase": self.get_handshake_phase(),
                "assets_import_root": self._state.assets_import_root,
                "texture_export_root": self._state.texture_export_root,
                "peer_protocol_version": self._state.peer_protocol_version,
                "negotiated_features": list(self._state.negotiated_features),
                "legacy_protocol": self._state.legacy_protocol,
            }

    def set_outbound_raw_handoff(self, handoff: Callable[[str], bool] | None) -> None:
        with self._lock:
            self._outbound_raw_handoff = handoff

    def set_endpoint(self, endpoint: str) -> None:
        with self._lock:
            self._state.endpoint = endpoint

    def set_transport_connected(self, connected: bool) -> None:
        with self._lock:
            self._state.transport_connected = connected
            if not connected:
                self._state.counterpart_observed = False
                self._state.handshake_confirmed = False
                self._reset_protocol_negotiation_locked()

    def set_current_handshake_id(self, handshake_id: str | None) -> None:
        with self._lock:
            if handshake_id != self._state.current_handshake_id:
                self._state.counterpart_observed = False
                self._state.handshake_confirmed = False
                self._reset_protocol_negotiation_locked()
            self._state.current_handshake_id = handshake_id

    def negotiate_protocol(self, payload: dict) -> ProtocolNegotiation:
        with self._lock:
            result = negotiate_protocol_offer(payload)
            if not result.ok:
                self._state.last_error = build_protocol_error(result.reason or "protocol_negotiation_failed", result.peer_version)
                self._state.handshake_confirmed = False
                return result
            self._state.peer_protocol_version = result.peer_version
            self._state.negotiated_features = tuple(result.negotiated_features)
            self._state.legacy_protocol = bool(result.legacy)
            self._state.last_error = None
            return result

    def validate_protocol_echo(self, payload: dict) -> tuple[bool, str | None]:
        with self._lock:
            ok, reason = validate_protocol_echo(
                payload,
                legacy=self._state.legacy_protocol,
                negotiated_features=self._state.negotiated_features,
            )
            if not ok:
                self._state.last_error = build_protocol_error(reason or "negotiation_echo_mismatch")
                self._state.handshake_confirmed = False
            return ok, reason

    def get_protocol_reply_fields(self) -> dict:
        with self._lock:
            return protocol_reply_fields(self._state.legacy_protocol, self._state.negotiated_features)

    def get_application_metadata_fields(self) -> dict:
        with self._lock:
            if not self._application_version:
                return {}
            return {"blenderVersion": self._application_version}

    def get_handshake_reply_fields(self) -> dict:
        with self._lock:
            fields = protocol_reply_fields(self._state.legacy_protocol, self._state.negotiated_features)
            if self._application_version:
                fields["blenderVersion"] = self._application_version
            return fields

    def is_feature_negotiated(self, feature: str) -> bool:
        with self._lock:
            return str(feature or "").strip() in self._state.negotiated_features

    def record_handshake_reject(self, payload: dict) -> str:
        with self._lock:
            reason = str((payload or {}).get("reason") or "protocol_negotiation_rejected").strip()
            peer_version = (payload or {}).get("localProtocolVersion")
            if isinstance(peer_version, bool) or not isinstance(peer_version, int):
                peer_version = None
            self._state.last_error = build_protocol_error(reason, peer_version)
            self._state.handshake_confirmed = False
            return self._state.last_error

    def _reset_protocol_negotiation_locked(self) -> None:
        self._state.peer_protocol_version = None
        self._state.negotiated_features = ()
        self._state.legacy_protocol = False

    def mark_counterpart_observed(self) -> None:
        with self._lock:
            self._state.counterpart_observed = True

    def mark_handshake_confirmed(self) -> None:
        with self._lock:
            self._state.counterpart_observed = True
            self._state.handshake_confirmed = True
            self._state.last_error = None

    def ingest_session_ack(self, ack: dict) -> bool:
        """Phase-1 strict handshake ack from Unity.

        Valid session_ack marks counterpart observed but not yet fully confirmed.
        """
        with self._lock:
            if not self._state.local_ready or not self._state.connect_attempted:
                self._state.last_error = "ack_without_connect_attempt"
                return False

            ok, reason = validate_session_ack_contract(ack, self._state.endpoint, self._state.current_handshake_id)
            if not ok:
                self._state.last_error = reason
                return False

            self._state.counterpart_observed = True
            self._state.last_error = None
            return True

    def ingest_final_handshake_ack(self, ack: dict) -> bool:
        """Phase-2 strict handshake final ack.

        Only this step confirms handshake_confirmed=true.
        """
        with self._lock:
            if not self._state.local_ready or not self._state.connect_attempted:
                self._state.last_error = "ack_without_connect_attempt"
                return False

            ok, reason = validate_final_handshake_ack_contract(ack, self._state.endpoint, self._state.current_handshake_id)
            if not ok:
                self._state.last_error = reason
                return False

            ok, reason = validate_protocol_echo(
                ack,
                legacy=self._state.legacy_protocol,
                negotiated_features=self._state.negotiated_features,
            )
            if not ok:
                self._state.last_error = build_protocol_error(reason or "negotiation_echo_mismatch")
                return False

            self._state.counterpart_observed = True
            self._state.handshake_confirmed = True
            self._state.last_error = None
            return True

    def send_auto(self, payload) -> SendResult:
        raw = json.dumps(payload, ensure_ascii=False)
        encoded = raw.encode("utf-8")
        if len(encoded) <= MAX_PAYLOAD_BYTES:
            return self.send(payload)

        transfer_id = str(uuid.uuid4())
        chunk_raw_size = max(64 * 1024, int(MAX_PAYLOAD_BYTES * 0.55))
        chunk_count = int(math.ceil(len(encoded) / float(chunk_raw_size)))
        original_type = payload.get("type") if isinstance(payload, dict) else None

        result = self.send({
            "type": "large_payload.begin",
            "transferId": transfer_id,
            "originalType": original_type,
            "totalBytes": len(encoded),
            "chunkCount": chunk_count,
            "encoding": "base64-json-utf8",
            "timestamp": int(time.time()),
        })
        if not result.ok:
            return result

        sent_bytes = 0
        for index in range(chunk_count):
            start = index * chunk_raw_size
            part = encoded[start:start + chunk_raw_size]
            sent_bytes += len(part)
            result = self.send({
                "type": "large_payload.chunk",
                "transferId": transfer_id,
                "index": index,
                "chunkCount": chunk_count,
                "data": base64.b64encode(part).decode("ascii"),
            })
            if not result.ok:
                return result

        end_payload = {
            "type": "large_payload.end",
            "transferId": transfer_id,
            "originalType": original_type,
            "totalBytes": len(encoded),
            "chunkCount": chunk_count,
            "sentBytes": sent_bytes,
            "timestamp": int(time.time()),
        }
        if self.is_feature_negotiated(LARGE_PAYLOAD_CRC32_FEATURE):
            end_payload.update({
                "checksumAlgorithm": "crc32-ieee",
                "checksum": crc32_ieee_hex(encoded),
            })
        return self.send(end_payload)

    def send(self, payload) -> SendResult:
        payload_type = payload.get("type") if isinstance(payload, dict) else None
        with self._lock:
            if not self._state.local_ready:
                self._state.last_error = "session_not_connected"
                return SendResult(ok=False, message="send rejected", error=self._state.last_error)

            if payload_type not in {"session_ack", "session.handshake_ack"} and not self._state.handshake_confirmed:
                self._state.last_error = "handshake_not_confirmed"
                return SendResult(ok=False, message="send rejected", error=self._state.last_error)

            handoff = self._outbound_raw_handoff

        raw = json.dumps(payload, ensure_ascii=False)
        encoded = raw.encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            with self._lock:
                self._state.last_error = "payload_too_large"
            raise SessionError("payload_too_large")

        if handoff is None:
            with self._lock:
                self._state.last_error = "outbound_handoff_missing"
            return SendResult(ok=False, message="send failed", error="outbound_handoff_missing", payload_size=len(encoded))

        try:
            ok = bool(handoff(raw))
        except Exception as exc:
            with self._lock:
                self._state.last_error = f"outbound_handoff_exception:{exc}"
                err = self._state.last_error
            return SendResult(ok=False, message="send failed", error=err, payload_size=len(encoded))

        if not ok:
            with self._lock:
                if not self._state.last_error:
                    self._state.last_error = "outbound_handoff_failed"
                err = self._state.last_error
            return SendResult(ok=False, message="send failed", error=err, payload_size=len(encoded))

        with self._lock:
            self._state.last_error = None
        return SendResult(ok=True, message="sent", payload_size=len(encoded))

    def set_assets_import_root(self, root: str | None) -> None:
        with self._lock:
            self._state.assets_import_root = root

    def get_assets_import_root(self) -> str | None:
        with self._lock:
            return self._state.assets_import_root

    def set_texture_export_root(self, root: str | None) -> None:
        with self._lock:
            self._state.texture_export_root = root

    def get_texture_export_root(self) -> str | None:
        with self._lock:
            return self._state.texture_export_root

    def get_last_error(self) -> str | None:
        with self._lock:
            return self._state.last_error

    def get_current_handshake_id(self) -> str | None:
        with self._lock:
            return self._state.current_handshake_id

    def get_handshake_phase(self) -> str:
        with self._lock:
            if self._state.handshake_confirmed:
                return "confirmed"
            if self._state.counterpart_observed:
                return "counterpart_observed"
            if self._state.transport_connected:
                return "transport_connected"
            if self._state.local_ready:
                return "local_ready"
            return "disconnected"

    def set_last_error(self, error: str | None) -> None:
        with self._lock:
            self._state.last_error = error

    def get_endpoint(self) -> str | None:
        with self._lock:
            return self._state.endpoint
