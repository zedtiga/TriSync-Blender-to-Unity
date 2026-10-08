from __future__ import annotations


def _validate_common_ack_fields(ack: dict, expected_endpoint: str | None, expected_handshake_id: str | None) -> tuple[bool, str | None]:
    if not isinstance(ack, dict):
        return False, "ack_invalid_payload"

    timestamp = ack.get("timestamp")
    if not isinstance(timestamp, int):
        return False, "ack_timestamp_invalid"

    handshake_id = ack.get("handshakeId")
    if not isinstance(handshake_id, str) or not handshake_id:
        return False, "ack_handshake_id_invalid"

    if expected_handshake_id and handshake_id != expected_handshake_id:
        return False, "ack_handshake_id_mismatch"

    target_endpoint = ack.get("targetEndpoint")
    if target_endpoint is not None and target_endpoint != expected_endpoint:
        return False, "ack_endpoint_mismatch"

    return True, None


def validate_session_ack_contract(ack: dict, expected_endpoint: str | None, expected_handshake_id: str | None) -> tuple[bool, str | None]:
    """Validate phase-1 ack from Unity for strict handshake.

    Required fields:
    - type: session_ack
    - timestamp: int
    - handshakeId: non-empty, must match current
    - criterion: non-empty string
    Optional:
    - targetEndpoint: if present, must match
    """
    ok, reason = _validate_common_ack_fields(ack, expected_endpoint, expected_handshake_id)
    if not ok:
        return ok, reason

    ack_type = ack.get("type")
    if ack_type != "session_ack":
        return False, "ack_type_invalid"

    criterion = ack.get("criterion")
    if not isinstance(criterion, str) or not criterion:
        return False, "ack_criterion_invalid"

    return True, None


def validate_final_handshake_ack_contract(ack: dict, expected_endpoint: str | None, expected_handshake_id: str | None) -> tuple[bool, str | None]:
    """Validate phase-2 final ack for strict handshake.

    Required fields:
    - type: session.handshake_ack
    - timestamp: int
    - handshakeId: non-empty, must match current
    Optional:
    - targetEndpoint: if present, must match
    """
    ok, reason = _validate_common_ack_fields(ack, expected_endpoint, expected_handshake_id)
    if not ok:
        return ok, reason

    ack_type = ack.get("type")
    if ack_type != "session.handshake_ack":
        return False, "ack_type_invalid"

    return True, None

