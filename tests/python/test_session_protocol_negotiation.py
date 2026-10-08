from __future__ import annotations

import sys
import unittest
from pathlib import Path


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.session.core import BlenderSessionCore
from blender.session.protocol_contract import (
    LARGE_PAYLOAD_CRC32_FEATURE,
    PROTOCOL_VERSION,
    UNITY_MESH_IMPORT_RESULT_FEATURE,
)


class SessionProtocolNegotiationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = BlenderSessionCore(endpoint="ws://127.0.0.1:8765/ws/")
        self.session.connect()
        self.session.set_current_handshake_id("hs-test")

    def test_matching_version_ignores_unknown_features_and_validates_echo(self) -> None:
        result = self.session.negotiate_protocol(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "features": [
                    "unknown_feature",
                    UNITY_MESH_IMPORT_RESULT_FEATURE,
                    LARGE_PAYLOAD_CRC32_FEATURE,
                ],
            }
        )

        self.assertTrue(result.ok)
        self.assertFalse(result.legacy)
        self.assertEqual(
            (LARGE_PAYLOAD_CRC32_FEATURE, UNITY_MESH_IMPORT_RESULT_FEATURE),
            result.negotiated_features,
        )
        self.assertEqual(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "negotiatedFeatures": [
                    LARGE_PAYLOAD_CRC32_FEATURE,
                    UNITY_MESH_IMPORT_RESULT_FEATURE,
                ],
            },
            self.session.get_protocol_reply_fields(),
        )
        self.assertTrue(self.session.ingest_final_handshake_ack(self._final_ack()))
        self.assertTrue(self.session.is_feature_negotiated(LARGE_PAYLOAD_CRC32_FEATURE))
        self.assertTrue(self.session.is_feature_negotiated(UNITY_MESH_IMPORT_RESULT_FEATURE))

    def test_missing_version_uses_legacy_mode_without_echo_requirements(self) -> None:
        result = self.session.negotiate_protocol({"features": ["large_payload_crc32_v1"]})

        self.assertTrue(result.ok)
        self.assertTrue(result.legacy)
        self.assertEqual({}, self.session.get_protocol_reply_fields())
        self.assertTrue(self.session.ingest_final_handshake_ack(self._final_ack(include_protocol=False)))
        self.assertFalse(self.session.is_feature_negotiated(LARGE_PAYLOAD_CRC32_FEATURE))
        self.assertFalse(self.session.is_feature_negotiated(UNITY_MESH_IMPORT_RESULT_FEATURE))

    def test_explicit_version_mismatch_is_rejected_and_visible(self) -> None:
        result = self.session.negotiate_protocol(
            {"protocolVersion": PROTOCOL_VERSION + 1, "features": []}
        )

        self.assertFalse(result.ok)
        self.assertEqual("protocol_version_mismatch", result.reason)
        self.assertIn("local=1:peer=2", self.session.get_last_error())
        self.assertFalse(self.session.is_alive())

    def test_versioned_echo_mismatch_is_rejected_but_legacy_is_not(self) -> None:
        self.assertTrue(
            self.session.negotiate_protocol(
                {"protocolVersion": PROTOCOL_VERSION, "features": []}
            ).ok
        )
        mismatch = self._final_ack()
        mismatch["negotiatedFeatures"] = ["unexpected_feature"]

        self.assertFalse(self.session.ingest_final_handshake_ack(mismatch))
        self.assertIn("negotiation_echo_mismatch", self.session.get_last_error())

    def test_application_version_metadata_is_optional_and_not_negotiated(self) -> None:
        session = BlenderSessionCore(application_version="5.0.1")
        legacy = BlenderSessionCore()

        self.assertEqual({"blenderVersion": "5.0.1"}, session.get_application_metadata_fields())
        self.assertEqual({}, legacy.get_application_metadata_fields())
        self.assertNotIn("blenderVersion", session.get_protocol_reply_fields())
        self.assertEqual(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "negotiatedFeatures": [],
                "blenderVersion": "5.0.1",
            },
            session.get_handshake_reply_fields(),
        )

    def _final_ack(self, *, include_protocol: bool = True) -> dict:
        payload = {
            "type": "session.handshake_ack",
            "timestamp": 1,
            "handshakeId": "hs-test",
        }
        if include_protocol:
            payload.update(self.session.get_protocol_reply_fields())
        return payload


if __name__ == "__main__":
    unittest.main()
