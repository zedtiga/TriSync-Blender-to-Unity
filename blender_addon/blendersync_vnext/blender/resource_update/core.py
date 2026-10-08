from __future__ import annotations

import json
from array import array

from blender.common.log import trace
from blender.resource_update.binary_v1 import build_mesh_update_binary_payload
from blender.resource_update.fingerprint import compute_mesh_content_fingerprint
from blender.resource_update.types import MeshUpdateResult, MeshUpdateState
from blender.session.core import BlenderSessionCore


class MeshUpdateCore:
    def __init__(self) -> None:
        self._state = MeshUpdateState(active=False)
        self._last_fingerprint_by_pair: dict[str, str] = {}
        self._last_material_refs_by_pair: dict[str, tuple[str, ...]] = {}

    def _fingerprint_key(self, pair_id: str | None, mesh_ref: str | None) -> str | None:
        if not pair_id:
            return None
        mesh_ref_value = str(mesh_ref or "")
        return f"{pair_id}|{mesh_ref_value}"

    def get_state(self) -> MeshUpdateState:
        return self._state

    def send_mesh_update_once(self, context: dict) -> MeshUpdateResult:
        session: BlenderSessionCore = context.get("session")
        pair_entry = context.get("pair_entry", {})
        pair_id = pair_entry.get("pairId")

        if session is None or not session.is_alive():
            return self._skip("session_inactive", pair_id)
        if not pair_entry.get("mapped", False):
            return self._skip("not_mapped", pair_id)
        if not pair_entry.get("syncEnabled", False):
            return self._skip("sync_disabled", pair_id)

        mesh_content = context.get("mesh_content") or {}
        prebuilt_binary = context.get("prebuilt_binary") if isinstance(context.get("prebuilt_binary"), dict) else None
        vertices = mesh_content.get("vertices")
        triangles = mesh_content.get("triangles")
        if prebuilt_binary is None and (not self._is_sequence(vertices) or not self._is_sequence(triangles)):
            return self._skip("mesh_missing", pair_id)

        normals = mesh_content.get("normals") if self._is_sequence(mesh_content.get("normals")) else []
        uv = mesh_content.get("uv") if self._is_sequence(mesh_content.get("uv")) else []
        mesh_ref = str(context.get("mesh_ref") or "").strip() or None
        material_refs = list(context.get("material_refs") or [])
        source_hint = str(context.get("source_hint") or "").strip() or None
        mesh_source = str(context.get("mesh_source") or "").strip() or None
        mesh_source_reason = str(context.get("mesh_source_reason") or "").strip() or None
        rebuild_reason = str(context.get("rebuild_reason") or "").strip() or None
        force_send = bool(context.get("force_send") or context.get("forceSend"))
        send_mesh_content_fingerprint = bool(context.get("send_mesh_content_fingerprint") or context.get("sendMeshContentFingerprint"))

        precomputed_content_fingerprint = str(context.get("mesh_content_fingerprint") or context.get("meshContentFingerprint") or "").strip()
        precomputed_content_fingerprint_no_uv = str(context.get("mesh_content_fingerprint_no_uv") or context.get("meshContentFingerprintNoUv") or "").strip()
        preview_fingerprint = precomputed_content_fingerprint or compute_mesh_content_fingerprint(
            mesh_content,
            prebuilt_binary,
            vertices=vertices,
            triangles=triangles,
            normals=normals,
            uv=uv,
            submeshes=mesh_content.get("subMeshes") or [],
        )
        content_fingerprint = precomputed_content_fingerprint
        content_fingerprint_no_uv = precomputed_content_fingerprint_no_uv
        if send_mesh_content_fingerprint:
            content_fingerprint = content_fingerprint or preview_fingerprint
            content_fingerprint_no_uv = content_fingerprint_no_uv or compute_mesh_content_fingerprint(
                mesh_content,
                prebuilt_binary,
                vertices=vertices,
                triangles=triangles,
                normals=normals,
                submeshes=mesh_content.get("subMeshes") or [],
                include_uv=False,
            )
            context["mesh_content_fingerprint"] = content_fingerprint
            context["mesh_content_fingerprint_no_uv"] = content_fingerprint_no_uv
        self._log_preview_compare(
            context,
            pair_id=pair_id,
            mesh_ref=mesh_ref,
            source_hint=source_hint,
            mesh_source=mesh_source,
            mesh_source_reason=mesh_source_reason,
            rebuild_reason=rebuild_reason,
            preview_fingerprint=preview_fingerprint,
            content_fingerprint=content_fingerprint,
            content_fingerprint_no_uv=content_fingerprint_no_uv,
            prebuilt_binary=prebuilt_binary,
        )
        fingerprint_key = self._fingerprint_key(pair_id, mesh_ref)
        if (
            not force_send
            and fingerprint_key is not None
            and self._last_fingerprint_by_pair.get(fingerprint_key) == preview_fingerprint
            and self._last_material_refs_by_pair.get(fingerprint_key) == tuple(material_refs)
        ):
            return self._skip("no_mesh_change", pair_id)

        payload = {
            "type": "scene_sync.mesh_update",
            "timestamp": int(context.get("timestamp", 0)),
            "pairId": pair_id,
            "sourceHint": source_hint,
            "meshRef": mesh_ref,
            "materialRefs": material_refs,
            "vertices": [float(v) for v in vertices] if self._is_sequence(vertices) else [],
            "triangles": [int(i) for i in triangles] if self._is_sequence(triangles) else [],
            "subMeshes": mesh_content.get("subMeshes") or [],
            "blendShapes": self._json_blend_shapes(mesh_content.get("blendShapes") or []),
            "normals": [float(n) for n in mesh_content.get("normals", [])]
            if self._is_sequence(mesh_content.get("normals"))
            else None,
            "uv": [float(u) for u in mesh_content.get("uv", [])]
            if self._is_sequence(mesh_content.get("uv"))
            else None,
            "meshFingerprint": preview_fingerprint,
        }
        if mesh_source:
            payload["meshSource"] = mesh_source
        if mesh_source_reason:
            payload["meshSourceReason"] = mesh_source_reason
        if rebuild_reason:
            payload["rebuildReason"] = rebuild_reason
        if send_mesh_content_fingerprint:
            payload["meshContentFingerprint"] = content_fingerprint
            payload["meshContentFingerprintNoUv"] = content_fingerprint_no_uv
            if context.get("mesh_content_fingerprint_scope"):
                payload["meshContentFingerprintScope"] = str(context.get("mesh_content_fingerprint_scope"))

        try:
            fallback_json_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            binary_payload = self._prebuilt_binary_payload(context, prebuilt_binary) if prebuilt_binary is not None else None
            if binary_payload is None:
                binary_payload = build_mesh_update_binary_payload(context, fallback_json_bytes=fallback_json_bytes)
            send_payload = binary_payload or payload
            if binary_payload is not None:
                prof = binary_payload.get("profile") or {}
                trace(
                    "MeshUpdate",
                    "mesh_binary_sent",
                    lambda: "Prepared a binary mesh update for sending.",
                    lambda: {
                        "pairId": pair_id,
                        "vertexCount": binary_payload.get("vertexCount"),
                        "indexCount": binary_payload.get("indexCount"),
                        "blendShapeCount": prof.get("blendShapeCount"),
                        "blendShapeBinaryBytes": prof.get("blendShapeBinaryBytes"),
                        "binaryBytes": prof.get("binaryBytes"),
                        "fallbackJsonBytes": prof.get("fallbackJsonBytes"),
                        "packMs": prof.get("packMs"),
                        "writeMs": prof.get("writeMs"),
                    },
                )
                detail = prof.get("writeDetail") or {}
                if detail:
                    trace(
                        "MeshUpdate",
                        "mesh_binary_profile",
                        lambda: "Recorded detailed mesh-update binary timings.",
                        lambda: {
                            "pairId": pair_id,
                            "hashReused": detail.get("hashReused"),
                            "hashComputed": detail.get("hashComputed"),
                            "positionWriteMs": detail.get("positionWriteMs"),
                            "indexWriteMs": detail.get("indexWriteMs"),
                            "normalWriteMs": detail.get("normalWriteMs"),
                            "uvWriteMs": detail.get("uvWriteMs"),
                            "subMeshWriteMs": detail.get("submeshWriteMs"),
                            "colorWriteMs": detail.get("colorWriteMs"),
                            "blendShapeWriteMs": detail.get("blendShapeWriteMs"),
                        },
                    )
            send_result = session.send_auto(send_payload)
            payload = send_payload
            if not send_result.ok:
                return self._error("transport_failure", pair_id, payload)

            if fingerprint_key is not None:
                self._last_fingerprint_by_pair[fingerprint_key] = preview_fingerprint
                self._last_material_refs_by_pair[fingerprint_key] = tuple(material_refs)
            self._state.active = True
            self._state.last_skip_reason = None
            self._state.counters.sends += 1
            result = MeshUpdateResult(ok=True, reason=None, pair_id=pair_id, payload=payload)
            self._state.last_result = result
            return result
        except Exception:
            return self._error("transport_failure", pair_id, payload)

    def _is_sequence(self, value) -> bool:
        return isinstance(value, (list, tuple, array))

    def _json_blend_shapes(self, blend_shapes) -> list[dict]:
        result = []
        for shape in blend_shapes or []:
            if not isinstance(shape, dict):
                continue
            deltas = shape.get("deltaPositions")
            if not self._is_sequence(deltas):
                continue
            item = {
                "name": str(shape.get("name") or "ShapeKey"),
                "frameWeight": float(shape.get("frameWeight") or 100.0),
                "value": float(shape.get("value") or 0.0),
                "sliderMin": float(shape.get("sliderMin") or 0.0),
                "sliderMax": float(shape.get("sliderMax") or 1.0),
                "vertexCount": int(shape.get("vertexCount") or (len(deltas) // 3)),
                "deltaPositions": [float(v) for v in deltas],
            }
            result.append(item)
        return result

    def _log_preview_compare(
        self,
        context: dict,
        *,
        pair_id: str | None,
        mesh_ref: str | None,
        source_hint: str | None,
        mesh_source: str | None,
        mesh_source_reason: str | None,
        rebuild_reason: str | None,
        preview_fingerprint: str,
        prebuilt_binary: dict | None,
        content_fingerprint: str,
        content_fingerprint_no_uv: str,
    ) -> None:
        debug = context.get("mesh_content_fingerprint_debug") if isinstance(context.get("mesh_content_fingerprint_debug"), dict) else {}
        profile = prebuilt_binary.get("profile") if isinstance(prebuilt_binary, dict) else {}
        hash_profile = prebuilt_binary.get("hashProfile") if isinstance(prebuilt_binary, dict) else {}
        if not isinstance(hash_profile, dict) or not hash_profile:
            hash_profile = profile.get("hashProfile") if isinstance(profile, dict) else {}
        if not isinstance(hash_profile, dict):
            hash_profile = {}
        trace(
            "MeshUpdate",
            "mesh_preview_compared",
            lambda: "Compared preview and content fingerprints.",
            lambda: {
                "pairId": pair_id,
                "meshRef": mesh_ref,
                "sourceHint": source_hint,
                "meshSource": mesh_source,
                "meshSourceReason": mesh_source_reason,
                "rebuildReason": rebuild_reason or "",
                "buildMode": debug.get("buildMode") or "",
                "previewFingerprint": self._short(preview_fingerprint),
                "contentFingerprint": self._short(content_fingerprint),
                "contentFingerprintNoUv": self._short(content_fingerprint_no_uv),
                "referenceFingerprint": self._short(debug.get("referenceFingerprint")),
                "sourceVertexCount": debug.get("sourceVertexCount"),
                "exportVertexCount": debug.get("exportVertexCount"),
                "indexCount": debug.get("indexCount"),
                "uvChannelCount": debug.get("uvChannelCount"),
                "topologyHash": self._short(hash_profile.get("topologySha1")),
            },
        )

    def _short(self, value) -> str:
        value = str(value or "").strip()
        if not value:
            return "<empty>"
        return value[:12]

    def _prebuilt_binary_payload(self, context: dict, prebuilt_binary: dict | None) -> dict | None:
        if not isinstance(prebuilt_binary, dict):
            return None
        pair_entry = context.get("pair_entry") or {}
        pair_id = pair_entry.get("pairId")
        buffers = prebuilt_binary.get("buffers") or []
        if not pair_id or not buffers:
            return None
        profile = dict(prebuilt_binary.get("profile") or {})
        if "fallbackJsonBytes" not in profile:
            profile["fallbackJsonBytes"] = 0
        return {
            "type": "scene_sync.mesh_update_binary_v1",
            "timestamp": int(context.get("timestamp", 0)),
            "pairId": pair_id,
            "sourceHint": str(context.get("source_hint") or "").strip() or None,
            "meshRef": str(context.get("mesh_ref") or "").strip() or None,
            "materialRefs": list(context.get("material_refs") or []),
            "meshSource": str(context.get("mesh_source") or "").strip() or None,
            "meshSourceReason": str(context.get("mesh_source_reason") or "").strip() or None,
            "rebuildReason": str(context.get("rebuild_reason") or "").strip() or None,
            "meshContentFingerprint": str(context.get("mesh_content_fingerprint") or context.get("meshContentFingerprint") or "").strip() or None,
            "meshContentFingerprintNoUv": str(context.get("mesh_content_fingerprint_no_uv") or context.get("meshContentFingerprintNoUv") or "").strip() or None,
            "meshContentFingerprintScope": str(context.get("mesh_content_fingerprint_scope") or context.get("meshContentFingerprintScope") or "").strip() or None,
            "vertexCount": int(prebuilt_binary.get("vertexCount") or 0),
            "indexCount": int(prebuilt_binary.get("indexCount") or 0),
            "buffers": buffers,
            "subMeshes": prebuilt_binary.get("subMeshes") or [],
            "blendShapes": prebuilt_binary.get("blendShapes") or [],
            "uvChannels": prebuilt_binary.get("uvChannels") or [],
            "uvChannelCount": len(prebuilt_binary.get("uvChannels") or []),
            "uvChannelNames": [str(ch.get("name") or f"UV{ch.get('index')}") for ch in (prebuilt_binary.get("uvChannels") or []) if isinstance(ch, dict)],
            "color0": prebuilt_binary.get("color0"),
            "colorAttributeName": prebuilt_binary.get("colorAttributeName") or "",
            "profile": profile,
        }

    def _skip(self, reason: str, pair_id: str | None) -> MeshUpdateResult:
        self._state.active = reason != "session_inactive"
        self._state.last_skip_reason = reason
        self._state.counters.skips += 1
        result = MeshUpdateResult(ok=False, reason=reason, pair_id=pair_id)
        self._state.last_result = result
        return result

    def _error(self, reason: str, pair_id: str | None, payload: dict | None = None) -> MeshUpdateResult:
        self._state.active = True
        self._state.last_skip_reason = reason
        self._state.counters.errors += 1
        result = MeshUpdateResult(ok=False, reason=reason, pair_id=pair_id, payload=payload)
        self._state.last_result = result
        return result
