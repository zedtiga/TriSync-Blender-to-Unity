from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.session import core as session_core_module
from blender.session.core import BlenderSessionCore
from blender.session.crc32_ieee import crc32_ieee_hex
from blender.session.protocol_contract import LARGE_PAYLOAD_CRC32_FEATURE, PROTOCOL_VERSION


class LargePayloadCrcTests(unittest.TestCase):
    def test_crc32_ieee_matches_standard_vector(self) -> None:
        self.assertEqual("cbf43926", crc32_ieee_hex(b"123456789"))

    def test_negotiated_large_payload_end_includes_checksum(self) -> None:
        session, sent = self._ready_session(versioned=True)
        payload = {"type": "test.large", "data": "x" * 200_000}

        with mock.patch.object(session_core_module, "MAX_PAYLOAD_BYTES", 128 * 1024):
            result = session.send_auto(payload)

        self.assertTrue(result.ok)
        end = sent[-1]
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertEqual("large_payload.end", end["type"])
        self.assertEqual("crc32-ieee", end["checksumAlgorithm"])
        self.assertEqual(crc32_ieee_hex(encoded), end["checksum"])

    def test_legacy_large_payload_end_omits_checksum(self) -> None:
        session, sent = self._ready_session(versioned=False)
        payload = {"type": "test.large", "data": "x" * 200_000}

        with mock.patch.object(session_core_module, "MAX_PAYLOAD_BYTES", 128 * 1024):
            result = session.send_auto(payload)

        self.assertTrue(result.ok)
        end = sent[-1]
        self.assertEqual("large_payload.end", end["type"])
        self.assertNotIn("checksumAlgorithm", end)
        self.assertNotIn("checksum", end)

    @staticmethod
    def _ready_session(*, versioned: bool) -> tuple[BlenderSessionCore, list[dict]]:
        session = BlenderSessionCore(endpoint="ws://127.0.0.1:8765/ws/")
        session.connect()
        session.set_transport_connected(True)
        session.set_current_handshake_id("hs-crc")
        offer = {"features": [LARGE_PAYLOAD_CRC32_FEATURE]}
        if versioned:
            offer["protocolVersion"] = PROTOCOL_VERSION
        negotiation = session.negotiate_protocol(offer)
        if not negotiation.ok:
            raise AssertionError(negotiation.reason)
        session.mark_handshake_confirmed()
        sent: list[dict] = []

        def capture(raw: str) -> bool:
            sent.append(json.loads(raw))
            return True

        session.set_outbound_raw_handoff(capture)
        return session, sent


if __name__ == "__main__":
    unittest.main()
