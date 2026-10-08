from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, fields

from blender.resource_update.binary_v1 import build_mesh_binary_buffers_from_content
from blender.resource_update.fingerprint import compute_mesh_content_fingerprint

from blender.common.constants import ASSET_BRIDGE_CONTRACT_VERSION
from blender.common.errors import PackageBuildError
from blender.common.log import trace, warn
from blender.common.types import DependencyNode, MeshPayload, PackageEnvelope, ResourceEntry


class PackageBuilder:
    def __init__(self) -> None:
        self._mesh_binary_cache: dict[tuple[str, str], dict] = {}

    def build(self, closure, meta: dict) -> PackageEnvelope:
        resources = [self._make_resource_entry(node) for node in closure.nodes]

        package = {
            "packageId": meta.get("packageId", "pkg-sprint0"),
            "resources": [asdict(r) for r in resources],
        }
        envelope = PackageEnvelope(contractVersion=ASSET_BRIDGE_CONTRACT_VERSION, package=package)
        setattr(envelope, "type", meta.get("type", "asset_bridge_import_mvp"))
        return envelope

    def _make_resource_entry(self, node: DependencyNode) -> ResourceEntry:
        resource_start = time.perf_counter()
        asset_id = node.metadata.get("assetId") or node.key
        source_fp = node.metadata.get("sourceFingerprint") or self._fingerprint(node)
        resource_type = str(node.type or "mesh").strip().lower()

        mesh_payload = None
        mesh_content_fingerprint = None
        mesh_content_fingerprint_no_uv = None
        if resource_type == "mesh" and isinstance(node.metadata.get("mesh"), dict):
            mesh_dict = dict(node.metadata.get("mesh") or {})
            source_vertex_count = mesh_dict.get("vertexCount")
            export_vertex_count = len(mesh_dict.get("vertices") or []) // 3
            index_count = len(mesh_dict.get("indices") or mesh_dict.get("triangles") or [])
            normal_count = len(mesh_dict.get("normals") or []) // 3
            uv0_count = len(mesh_dict.get("uv0") or mesh_dict.get("uv") or []) // 2
            uv_channel_count = len(mesh_dict.get("uvChannels") or [])
            submesh_count = len(mesh_dict.get("subMeshes") or [])
            binary = None
            try:
                binary_start = time.perf_counter()
                fallback_json_bytes = self._estimate_mesh_json_bytes(mesh_dict)
                cache_key = (asset_id, source_fp)
                binary = self._mesh_binary_cache.get(cache_key)
                built_now = False
                if binary is None:
                    binary = build_mesh_binary_buffers_from_content(
                        mesh_dict,
                        pair_id=asset_id,
                        mesh_ref=f"mesh-{asset_id}",
                        fallback_json_bytes=fallback_json_bytes,
                    )
                    if binary is not None:
                        self._mesh_binary_cache[cache_key] = binary
                        built_now = True
                else:
                    trace(
                        "PackageBuilder",
                        "mesh_binary_cache_hit",
                        lambda: "Reused cached mesh binary buffers.",
                        lambda: {"assetId": asset_id},
                    )
                if binary is not None:
                    mesh_dict["binaryBuffers"] = binary.get("buffers") or []
                    mesh_dict["binaryProfile"] = binary.get("profile") or {}
                    mesh_dict["subMeshes"] = binary.get("subMeshes") or []
                    if binary.get("blendShapes"):
                        mesh_dict["blendShapes"] = binary.get("blendShapes") or []
                    if binary.get("uvChannels"):
                        mesh_dict["uvChannels"] = binary.get("uvChannels") or []
                    if binary.get("color0"):
                        mesh_dict["color0Buffer"] = binary.get("color0")
                        mesh_dict["color0"] = []
                        mesh_dict["colorAttributeName"] = binary.get("colorAttributeName") or mesh_dict.get("colorAttributeName") or ""
                        mesh_dict["colorAttributeCount"] = int(mesh_dict.get("colorAttributeCount") or 1)
                    else:
                        mesh_dict["color0Buffer"] = None
                        mesh_dict["color0"] = []
                    mesh_dict["vertices"] = []
                    mesh_dict["indices"] = []
                    mesh_dict["normals"] = []
                    mesh_dict["uv0"] = []
                    if built_now:
                        prof = mesh_dict["binaryProfile"]
                        trace(
                            "PackageBuilder",
                            "mesh_binary_built",
                            lambda: "Built binary mesh buffers for a package resource.",
                            lambda: {
                                "assetId": asset_id,
                                "vertexCount": mesh_dict.get("vertexCount"),
                                "binaryBytes": prof.get("binaryBytes"),
                                "fallbackJsonBytes": prof.get("fallbackJsonBytes"),
                                "subMeshCount": prof.get("subMeshCount"),
                                "subMeshBinaryBytes": prof.get("subMeshBinaryBytes"),
                                "packMs": prof.get("packMs"),
                                "writeMs": prof.get("writeMs"),
                                "totalMs": round((time.perf_counter() - binary_start) * 1000.0, 2),
                            },
                        )
                        detail = prof.get("writeDetail") or {}
                        if detail:
                            trace(
                                "PackageBuilder",
                                "mesh_binary_profile",
                                lambda: "Recorded detailed mesh binary write timings.",
                                lambda: {
                                    "assetId": asset_id,
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
            except Exception as exc:
                warn(
                    "PackageBuilder",
                    "mesh_binary_fallback",
                    str(exc) or "binary_build_failed",
                    {"assetId": asset_id},
                )
            fingerprint_start = time.perf_counter()
            if binary is not None:
                mesh_content_fingerprint = compute_mesh_content_fingerprint(mesh_dict, binary)
                mesh_content_fingerprint_no_uv = compute_mesh_content_fingerprint(mesh_dict, binary, include_uv=False)
            else:
                mesh_content_fingerprint = compute_mesh_content_fingerprint(
                    mesh_dict,
                    None,
                    vertices=mesh_dict.get("vertices"),
                    triangles=mesh_dict.get("indices") or mesh_dict.get("triangles"),
                    normals=mesh_dict.get("normals"),
                    uv=mesh_dict.get("uv0") if mesh_dict.get("uv0") is not None else mesh_dict.get("uv"),
                    color0=mesh_dict.get("color0"),
                    submeshes=mesh_dict.get("subMeshes") or [],
                )
                mesh_content_fingerprint_no_uv = compute_mesh_content_fingerprint(
                    mesh_dict,
                    None,
                    vertices=mesh_dict.get("vertices"),
                    triangles=mesh_dict.get("indices") or mesh_dict.get("triangles"),
                    normals=mesh_dict.get("normals"),
                    color0=mesh_dict.get("color0"),
                    submeshes=mesh_dict.get("subMeshes") or [],
                    include_uv=False,
                )
            fingerprint_ms = (time.perf_counter() - fingerprint_start) * 1000.0
            trace(
                "PackageBuilder",
                "mesh_fingerprint_computed",
                lambda: "Computed mesh-content fingerprints for a package resource.",
                lambda: {
                    "assetId": asset_id,
                    "fingerprint": mesh_content_fingerprint[:12] if mesh_content_fingerprint else "<empty>",
                    "fingerprintNoUv": mesh_content_fingerprint_no_uv[:12] if mesh_content_fingerprint_no_uv else "<empty>",
                    "sourceVertexCount": source_vertex_count,
                    "exportVertexCount": export_vertex_count,
                    "indexCount": index_count,
                    "normalCount": normal_count,
                    "uv0Count": uv0_count,
                    "uvChannelCount": uv_channel_count,
                    "subMeshCount": submesh_count,
                    "fingerprintMs": round(fingerprint_ms, 2),
                },
            )
            if mesh_content_fingerprint:
                try:
                    node.metadata["meshContentFingerprint"] = mesh_content_fingerprint
                    node.metadata["mesh"]["meshContentFingerprint"] = mesh_content_fingerprint
                except Exception:
                    pass
            if mesh_content_fingerprint_no_uv:
                try:
                    node.metadata["meshContentFingerprintNoUv"] = mesh_content_fingerprint_no_uv
                    node.metadata["mesh"]["meshContentFingerprintNoUv"] = mesh_content_fingerprint_no_uv
                except Exception:
                    pass
            mesh_dict.pop("_binaryHashProfile", None)
            mesh_dict = self._filter_mesh_payload_fields(mesh_dict)
            mesh_payload = MeshPayload(**mesh_dict)

        entry = ResourceEntry(
            assetId=asset_id,
            sourceFingerprint=source_fp,
            type=resource_type,
            source={"sourceUri": node.source_uri},
            meshContentFingerprint=mesh_content_fingerprint,
            meshContentFingerprintNoUv=mesh_content_fingerprint_no_uv,
            mesh=mesh_payload,
        )
        self._validate_required_fields(entry)
        if resource_type == "mesh":
            trace(
                "PackageBuilder",
                "mesh_resource_built",
                lambda: "Built a mesh package resource.",
                lambda: {
                    "assetId": asset_id,
                    "totalMs": round((time.perf_counter() - resource_start) * 1000.0, 2),
                },
            )
        return entry

    def _estimate_mesh_json_bytes(self, mesh_dict: dict) -> int:
        def sequence_len(value) -> int:
            if value is None or isinstance(value, (str, bytes, dict)):
                return 0
            try:
                return len(value)
            except Exception:
                return 0

        float_values = 0
        float_values += sequence_len(mesh_dict.get("vertices"))
        float_values += sequence_len(mesh_dict.get("normals"))
        float_values += sequence_len(mesh_dict.get("uv0") if mesh_dict.get("uv0") is not None else mesh_dict.get("uv"))
        float_values += sequence_len(mesh_dict.get("color0"))

        for channel in mesh_dict.get("uvChannels") or []:
            if isinstance(channel, dict):
                float_values += sequence_len(channel.get("data") or channel.get("uv") or channel.get("values"))

        blend_shape_count = 0
        for shape in mesh_dict.get("blendShapes") or []:
            if not isinstance(shape, dict):
                continue
            blend_shape_count += 1
            float_values += sequence_len(shape.get("deltaPositions"))
            float_values += sequence_len(shape.get("deltaNormals"))
            float_values += sequence_len(shape.get("deltaTangents"))

        index_values = sequence_len(mesh_dict.get("indices") or mesh_dict.get("triangles"))
        submesh_count = sequence_len(mesh_dict.get("subMeshes"))

        # Diagnostic estimate only.  The binary payload is authoritative, so avoid
        # serializing a giant fallback JSON blob just to fill profile.jsonBytes.
        estimate = 4096
        estimate += float_values * 11
        estimate += index_values * 8
        estimate += int(mesh_dict.get("vertexCount") or 0) * 2
        estimate += submesh_count * 512
        estimate += blend_shape_count * 1024
        return int(estimate * 1.15)

    def _fingerprint(self, node: DependencyNode) -> str:
        base = f"{node.key}|{node.type}|{node.source_uri}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()

    def _filter_mesh_payload_fields(self, mesh_dict: dict) -> dict:
        allowed = {field.name for field in fields(MeshPayload)}
        return {key: value for key, value in (mesh_dict or {}).items() if key in allowed}

    def _validate_required_fields(self, entry: ResourceEntry) -> None:
        if not entry.assetId:
            raise PackageBuildError("assetId_required")
        if not entry.sourceFingerprint:
            raise PackageBuildError("sourceFingerprint_required")
        if entry.type != "mesh":
            raise PackageBuildError(f"unsupported_resource_type:{entry.type or '<empty>'}")
        if entry.mesh is None:
            raise PackageBuildError("mesh_payload_required")
