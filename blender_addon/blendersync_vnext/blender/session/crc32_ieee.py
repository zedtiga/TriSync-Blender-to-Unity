from __future__ import annotations

import zlib


def crc32_ieee_hex(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
