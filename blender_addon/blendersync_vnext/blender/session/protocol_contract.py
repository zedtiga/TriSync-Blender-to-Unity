from __future__ import annotations

from dataclasses import dataclass


PROTOCOL_VERSION = 1
LARGE_PAYLOAD_CRC32_FEATURE = "large_payload_crc32_v1"
UNITY_MESH_IMPORT_RESULT_FEATURE = "unity_mesh_import_result_v1"
SUPPORTED_FEATURES: tuple[str, ...] = (
    LARGE_PAYLOAD_CRC32_FEATURE,
    UNITY_MESH_IMPORT_RESULT_FEATURE,
)


@dataclass(frozen=True)
class ProtocolNegotiation:
    ok: bool
    legacy: bool = False
    peer_version: int | None = None
    negotiated_features: tuple[str, ...] = ()
    reason: str | None = None


def negotiate_protocol_offer(payload: dict) -> ProtocolNegotiation:
    if not isinstance(payload, dict):
        return ProtocolNegotiation(ok=False, reason="protocol_offer_invalid")
    if "protocolVersion" not in payload:
        return ProtocolNegotiation(ok=True, legacy=True)
    peer_version = payload.get("protocolVersion")
    if isinstance(peer_version, bool) or not isinstance(peer_version, int):
        return ProtocolNegotiation(ok=False, reason="protocol_version_invalid")
    if peer_version != PROTOCOL_VERSION:
        return ProtocolNegotiation(
            ok=False,
            peer_version=peer_version,
            reason="protocol_version_mismatch",
        )
    peer_features = _normalize_features(payload.get("features"))
    supported = set(SUPPORTED_FEATURES)
    negotiated = tuple(feature for feature in peer_features if feature in supported)
    return ProtocolNegotiation(
        ok=True,
        peer_version=peer_version,
        negotiated_features=negotiated,
    )


def validate_protocol_echo(
    payload: dict,
    *,
    legacy: bool,
    negotiated_features: tuple[str, ...],
) -> tuple[bool, str | None]:
    if legacy:
        return True, None
    if not isinstance(payload, dict):
        return False, "negotiation_echo_mismatch"
    version = payload.get("protocolVersion")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        return False, "negotiation_echo_mismatch"
    echoed = _normalize_features(payload.get("negotiatedFeatures"))
    if echoed != tuple(negotiated_features):
        return False, "negotiation_echo_mismatch"
    return True, None


def protocol_reply_fields(legacy: bool, negotiated_features: tuple[str, ...]) -> dict:
    if legacy:
        return {}
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "negotiatedFeatures": list(negotiated_features),
    }


def build_protocol_error(reason: str, peer_version: int | None = None) -> str:
    if reason == "protocol_version_mismatch":
        peer = "unknown" if peer_version is None else str(peer_version)
        return f"protocol_version_mismatch:local={PROTOCOL_VERSION}:peer={peer}:update_other_endpoint"
    if reason == "negotiation_echo_mismatch":
        return f"negotiation_echo_mismatch:local={PROTOCOL_VERSION}:update_other_endpoint"
    return str(reason or "protocol_negotiation_failed")


def _normalize_features(value) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    normalized = []
    seen = set()
    for item in value:
        feature = str(item or "").strip()
        if not feature or feature in seen:
            continue
        seen.add(feature)
        normalized.append(feature)
    return tuple(sorted(normalized))
