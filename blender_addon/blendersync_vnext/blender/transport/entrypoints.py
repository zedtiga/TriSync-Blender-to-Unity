from __future__ import annotations

import json
from dataclasses import asdict

from blender.asset_registry import collect_asset_ids_from_selected_roots, filter_unknown_bootstrap_roots, mark_assets_known
from blender.common.constants import MAX_PAYLOAD_BYTES
from blender.common.errors import PackageBuildError, ResolverError, SessionError, TransportError
from blender.common.log import exception as log_exception, trace, warn
from blender.common.types import DependencyClosure, SendResult
from blender.deps.resolver import DependencyResolver
from blender.package.builder import PackageBuilder
from blender.session.core import BlenderSessionCore


def send_selected_resources(context) -> SendResult:
    return _run_pipeline(context)


def _log_result(operation: str, result, fields: dict | None = None) -> None:
    details = dict(fields or {})
    details["message"] = getattr(result, "message", None)
    if bool(getattr(result, "ok", False)):
        trace(
            "Transport",
            f"{operation}_sent",
            lambda: "Completed an outgoing TriSync operation.",
            lambda: details,
        )
        return
    warn(
        "Transport",
        f"{operation}_failed",
        getattr(result, "error", None) or getattr(result, "message", None) or "send_failed",
        details,
    )


def _log_failure(event: str, exc: Exception, fields: dict | None = None) -> None:
    details = dict(fields or {})
    details["failureCategory"] = _classify_failure(exc)
    if isinstance(exc, (SessionError, ResolverError, PackageBuildError, TransportError)):
        warn("Transport", event, str(exc), details)
        return
    log_exception("Transport", event, exc, fields=details)


def send_material_content_v1(context) -> SendResult:
    session = context.get("session")
    payload = context.get("materialContentV1Payload") or {}
    try:
        if session is None:
            raise SessionError("session_missing")
        if not payload or payload.get("type") != "scene_sync.material_content_v1":
            raise SessionError("material_content_v1_payload_missing")
        material_ref = payload.get("materialRef") or ""
        textures = payload.get("textures") or {}
        texture_count = sum(1 for value in textures.values() if value)
        warnings = payload.get("warnings") or []
        result = session.send_auto(payload)
        _log_result(
            "material_content",
            result,
            {
                "materialRef": material_ref,
                "textureCount": texture_count,
                "warningCount": len(warnings),
            },
        )
        if result.ok and material_ref:
            mark_assets_known([material_ref])
        return SendResult(
            ok=bool(result.ok),
            message=result.message or ("sent" if result.ok else "send_failed"),
            error=None if result.ok else (result.error or "material_content_v1_send_failed"),
            payload_size=_estimate_json_bytes(payload),
            last_package_id=None,
            last_resource_count=1,
            failure_category=None if result.ok else "session",
        )
    except Exception as exc:
        _log_failure("material_content_failed", exc, {"materialRef": payload.get("materialRef")})
        return SendResult(
            ok=False,
            message="send_failed",
            error=str(exc),
            payload_size=0,
            last_package_id=None,
            last_resource_count=0,
            failure_category=_classify_failure(exc),
        )


def send_scene_view_state(context) -> SendResult:
    session = context.get("session")
    payload = context.get("viewStatePayload") or {}
    try:
        if session is None:
            raise SessionError("session_missing")
        if not payload or payload.get("type") != "scene_sync.view_state_v1":
            raise SessionError("view_state_payload_missing")
        result = session.send_auto(payload)
        _log_result(
            "view_state",
            result,
            {"viewMode": payload.get("viewMode"), "distance": payload.get("distance")},
        )
        return SendResult(
            ok=bool(result.ok),
            message=result.message or ("sent" if result.ok else "send_failed"),
            error=None if result.ok else (result.error or "view_state_send_failed"),
            payload_size=_estimate_json_bytes(payload),
            last_package_id=None,
            last_resource_count=0,
            failure_category=None if result.ok else "session",
        )
    except Exception as exc:
        _log_failure("view_state_failed", exc)
        return SendResult(
            ok=False,
            message="send_failed",
            error=str(exc),
            payload_size=0,
            last_package_id=None,
            last_resource_count=0,
            failure_category=_classify_failure(exc),
        )


def send_object_state_update(context) -> SendResult:
    session = context.get("session")
    payload = context.get("objectStatePayload") or {}
    objects = payload.get("objects") or []
    correlation_id = context.get("correlationId") or "manual"
    try:
        if session is None:
            raise SessionError("session_missing")
        if not payload or payload.get("type") != "scene_sync.object_state_update_v1":
            raise SessionError("object_state_payload_missing")

        result = session.send_auto(payload)
        _log_result(
            "object_state",
            result,
            {"correlationId": correlation_id, "objectCount": len(objects)},
        )
        payload_size = _estimate_json_bytes(payload)
        return SendResult(
            ok=bool(result.ok),
            message=result.message or ("sent" if result.ok else "send_failed"),
            error=None if result.ok else (result.error or "object_state_send_failed"),
            payload_size=payload_size,
            last_package_id=None,
            last_resource_count=0,
            failure_category=None if result.ok else "session",
        )
    except Exception as exc:
        _log_failure(
            "object_state_failed",
            exc,
            {"correlationId": correlation_id, "objectCount": len(objects)},
        )
        return SendResult(
            ok=False,
            message="send_failed",
            error=str(exc),
            payload_size=0,
            last_package_id=None,
            last_resource_count=0,
            failure_category=_classify_failure(exc),
        )


def send_rigged_pose_sync(context) -> SendResult:
    session = context.get("session")
    payload = context.get("riggedPosePayload") or {}
    try:
        if session is None:
            raise SessionError("session_missing")
        if not payload or payload.get("type") != "asset_bridge.rigged_pose_v1":
            raise SessionError("rigged_pose_payload_missing")

        mode = payload.get("mode") or "current_pose"
        bone_count = len(payload.get("bones") or [])
        result = session.send_auto(payload)
        _log_result(
            "rigged_pose",
            result,
            {
                "riggedObjectId": payload.get("riggedObjectId"),
                "mode": mode,
                "rigAxisMode": payload.get("rigAxisMode"),
                "boneCount": bone_count,
            },
        )
        return SendResult(
            ok=bool(result.ok),
            message=result.message or ("sent" if result.ok else "send_failed"),
            error=None if result.ok else (result.error or "rigged_pose_sync_failed"),
            payload_size=_estimate_json_bytes(payload),
            last_package_id=None,
            last_resource_count=0,
            failure_category=None if result.ok else "session",
        )
    except Exception as exc:
        _log_failure("rigged_pose_failed", exc, {"riggedObjectId": payload.get("riggedObjectId")})
        return SendResult(
            ok=False,
            message="send_failed",
            error=str(exc),
            payload_size=0,
            last_package_id=None,
            last_resource_count=0,
            failure_category=_classify_failure(exc),
        )


def send_rigged_blendshape_weights_sync(context) -> SendResult:
    session = context.get("session")
    payload = context.get("riggedBlendShapeWeightsPayload") or {}
    try:
        if session is None:
            raise SessionError("session_missing")
        if payload.get("type") != "asset_bridge.rigged_blendshape_weights_v1":
            raise SessionError("rigged_blendshape_weights_payload_missing")

        parts = payload.get("parts") or []
        weight_count = sum(len(part.get("weights") or []) for part in parts if isinstance(part, dict))
        result = session.send_auto(payload)
        _log_result(
            "rigged_blendshape_weights",
            result,
            {
                "riggedObjectId": payload.get("riggedObjectId"),
                "partCount": len(parts),
                "weightCount": weight_count,
            },
        )
        return SendResult(
            ok=bool(result.ok),
            message=result.message or ("sent" if result.ok else "send_failed"),
            error=None if result.ok else (result.error or "rigged_blendshape_weights_sync_failed"),
            payload_size=_estimate_json_bytes(payload),
            last_package_id=None,
            last_resource_count=0,
            failure_category=None if result.ok else "session",
        )
    except Exception as exc:
        _log_failure(
            "rigged_blendshape_weights_failed",
            exc,
            {"riggedObjectId": payload.get("riggedObjectId")},
        )
        return SendResult(
            ok=False,
            message="send_failed",
            error=str(exc),
            payload_size=0,
            last_package_id=None,
            last_resource_count=0,
            failure_category=_classify_failure(exc),
        )


def _send_object_assemblies_if_present(session, context) -> None:
    payloads = context.get("objectAssemblies") or []
    if not payloads:
        single = context.get("objectAssembly")
        if single:
            payloads = [single]

    if not payloads:
        return

    for payload in payloads:
        result = session.send_auto(payload)
        if not result.ok:
            raise SessionError(result.error or "object_assembly_send_failed")
        trace(
            "Transport",
            "object_assembly_sent",
            lambda: "Sent an object assembly.",
            lambda: {
                "pairId": payload.get("pairId"),
                "meshRef": payload.get("meshRef"),
                "materialCount": len(payload.get("materialRefs") or []),
            },
        )


def _send_name_payloads_if_present(session, context) -> None:
    payloads = context.get("namePayloads") or []
    if not payloads:
        return

    for payload in payloads:
        result = session.send_auto(payload)
        if not result.ok:
            raise SessionError(result.error or "object_name_send_failed")
        trace(
            "Transport",
            "object_name_sent",
            lambda: "Sent an object name update.",
            lambda: {"pairId": payload.get("pairId")},
        )



def _send_hierarchy_payloads_if_present(session, context) -> None:
    payloads = context.get("hierarchyPayloads") or []
    if not payloads:
        return

    for payload in payloads:
        result = session.send_auto(payload)
        if not result.ok:
            raise SessionError(result.error or "hierarchy_send_failed")
        trace(
            "Transport",
            "hierarchy_sent",
            lambda: "Sent a hierarchy update.",
            lambda: {
                "childPairId": payload.get("childPairId"),
                "parentPairId": payload.get("parentPairId"),
            },
        )


def _send_visibility_payloads_if_present(session, context) -> None:
    payloads = context.get("visibilityPayloads") or []
    if not payloads:
        return

    for payload in payloads:
        result = session.send_auto(payload)
        if not result.ok:
            raise SessionError(result.error or "visibility_send_failed")
        trace(
            "Transport",
            "visibility_sent",
            lambda: "Sent a visibility update.",
            lambda: {"pairId": payload.get("pairId"), "visible": payload.get("visible")},
        )


def _send_material_contents_if_present(session, context) -> None:
    if context.get("sendMaterialContents") is False or context.get("suppressMaterialContents"):
        return

    material_contents = []
    seen_refs: set[str] = set()
    for material_content in context.get("materialContents", []) or []:
        if not isinstance(material_content, dict):
            continue
        material_ref = str(material_content.get("materialRef") or "").strip()
        if material_ref and material_ref in seen_refs:
            continue
        if material_ref:
            seen_refs.add(material_ref)
        material_contents.append(material_content)

    for entry in context.get("selected", []) or []:
        if not isinstance(entry, dict):
            continue
        for material_content in entry.get("materialContents", []) or []:
            if not isinstance(material_content, dict):
                continue
            material_ref = str(material_content.get("materialRef") or "").strip()
            if material_ref and material_ref in seen_refs:
                continue
            if material_ref:
                seen_refs.add(material_ref)
            material_contents.append(material_content)

    if material_contents:
        trace(
            "Transport",
            "embedded_materials_sending",
            lambda: "Sending embedded material content.",
            lambda: {"materialCount": len(material_contents)},
        )

    for payload in material_contents:
        result = session.send_auto(payload)
        if not result.ok:
            raise SessionError(result.error or "material_content_v1_send_failed")
        material_ref = str(payload.get("materialRef") or "").strip()
        if material_ref:
            mark_assets_known([material_ref])


def _send_rigged_objects_if_present(session, context) -> None:
    payloads = context.get("riggedObjects") or []
    if not payloads:
        return

    for payload in payloads:
        rig = payload.get("riggedObject") or {}
        result = session.send_auto(payload)
        if not result.ok:
            raise SessionError(result.error or "rigged_object_send_failed")
        trace(
            "Transport",
            "rigged_object_sent",
            lambda: "Sent a rigged object.",
            lambda: {
                "riggedObjectId": rig.get("riggedObjectId"),
                "meshRef": rig.get("meshRef"),
            },
        )



def _estimate_json_bytes(payload: dict) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return -1


def _make_closure_with_nodes(nodes: list) -> DependencyClosure:
    return DependencyClosure(nodes=list(nodes), missing_dependencies=[])


def _envelope_dict(builder: PackageBuilder, nodes: list, package_id: str) -> dict:
    envelope = builder.build(_make_closure_with_nodes(nodes), meta={"packageId": package_id, "type": "asset_bridge_import_mvp"})
    payload = asdict(envelope)
    payload["type"] = "asset_bridge_import_mvp"
    return payload


def _sequence_json_estimate(values, *, scalar_chars: int) -> int:
    try:
        count = len(values or [])
    except Exception:
        return 2
    if count <= 0:
        return 2
    return max(2, count * scalar_chars + max(0, count - 1) + 2)


def _mesh_resource_json_estimate(mesh: dict) -> int:
    vertices = mesh.get("vertices") or []
    indices = mesh.get("indices") or mesh.get("triangles") or []
    normals = mesh.get("normals") or []
    uv0 = mesh.get("uv0") if mesh.get("uv0") is not None else mesh.get("uv")
    vertex_count = int(mesh.get("vertexCount") or (len(vertices) // 3 if vertices else 0) or 0)

    buffer_count = 2
    if normals:
        buffer_count += 1
    if uv0:
        buffer_count += 1
    for channel in mesh.get("uvChannels") or []:
        if isinstance(channel, dict):
            try:
                if int(channel.get("index") or 0) > 0:
                    buffer_count += 1
            except Exception:
                continue
    if mesh.get("color0"):
        buffer_count += 1

    estimate = 4096
    estimate += buffer_count * 512
    estimate += _sequence_json_estimate(mesh.get("materialRefs") or [], scalar_chars=48)
    estimate += _sequence_json_estimate(mesh.get("subMeshes") or [], scalar_chars=96)

    skin = mesh.get("skin") or {}
    if isinstance(skin, dict):
        estimate += _sequence_json_estimate(skin.get("bonesPerVertex") or [], scalar_chars=2)
        estimate += _sequence_json_estimate(skin.get("boneIndices") or [], scalar_chars=4)
        estimate += _sequence_json_estimate(skin.get("boneWeights") or [], scalar_chars=12)
        estimate += _sequence_json_estimate(skin.get("bindPoses") or [], scalar_chars=180)
        estimate += _sequence_json_estimate(skin.get("meshWorldMatrix") or [], scalar_chars=14)
        estimate += _sequence_json_estimate(skin.get("armatureWorldMatrix") or [], scalar_chars=14)

    blend_shape_count = len(mesh.get("blendShapes") or [])
    estimate += blend_shape_count * (1024 + max(0, vertex_count) * 24)

    # Binary mesh payloads carry file manifests in JSON, not the vertex/index arrays.
    # Keep a small vertex/index contribution for metadata variance and margin.
    estimate += int((max(0, vertex_count) + len(indices or [])) * 0.25)
    return estimate


def _material_resource_json_estimate(material: dict) -> int:
    try:
        return max(2048, len(json.dumps(material, ensure_ascii=False, default=str).encode("utf-8")) + 1024)
    except Exception:
        return 4096


def _resource_node_json_estimate(node) -> int:
    metadata = getattr(node, "metadata", {}) or {}
    estimate = 1024
    estimate += len(str(getattr(node, "key", "") or "").encode("utf-8"))
    estimate += len(str(getattr(node, "source_uri", "") or "").encode("utf-8"))
    asset_id = metadata.get("assetId")
    if asset_id:
        estimate += len(str(asset_id).encode("utf-8"))
    node_type = getattr(node, "type", "")
    if node_type == "mesh" and isinstance(metadata.get("mesh"), dict):
        estimate += _mesh_resource_json_estimate(metadata.get("mesh") or {})
    elif node_type == "material" and isinstance(metadata.get("material"), dict):
        estimate += _material_resource_json_estimate(metadata.get("material") or {})
    else:
        try:
            estimate += len(json.dumps(metadata, ensure_ascii=False, default=str).encode("utf-8"))
        except Exception:
            estimate += 4096
    return int(estimate * 1.25)


def _build_resource_batches(builder: PackageBuilder, closure, package_id: str) -> list[tuple[dict, list, int]]:
    nodes = list(getattr(closure, "nodes", []) or [])
    batches: list[tuple[dict, list, int]] = []
    current_nodes: list = []
    current_estimated_bytes = 0

    # Budget using single-resource envelopes only.  Candidate batch size is a
    # conservative estimate, and each final batch is built exactly once.  This
    # avoids repeatedly rebuilding candidate envelopes, which only produced
    # MeshAssetBinaryV1 CACHE_HIT noise after binary buffers were cached.
    estimate_margin = 1.10
    envelope_overhead_bytes = 2048

    def estimated_batch_bytes(resource_bytes_sum: int, count: int) -> int:
        if count <= 0:
            return 0
        return int((resource_bytes_sum + envelope_overhead_bytes + max(0, count - 1) * 16) * estimate_margin)

    def flush_current() -> None:
        nonlocal current_nodes, current_estimated_bytes
        if not current_nodes:
            return
        built_nodes = list(current_nodes)
        envelope = _envelope_dict(builder, built_nodes, package_id)
        actual_bytes = _estimate_json_bytes(envelope)
        if actual_bytes > MAX_PAYLOAD_BYTES and len(built_nodes) > 1:
            for node in built_nodes:
                single_envelope = _envelope_dict(builder, [node], package_id)
                batches.append((single_envelope, [node], _estimate_json_bytes(single_envelope)))
        else:
            batches.append((envelope, built_nodes, actual_bytes))
        current_nodes = []
        current_estimated_bytes = 0

    for node in nodes:
        single_bytes = _resource_node_json_estimate(node)
        asset_id = getattr(node, "metadata", {}).get("assetId")
        trace(
            "Transport",
            "resource_budgeted",
            lambda: "Estimated one resource payload.",
            lambda: {
                "assetId": asset_id,
                "resourceType": getattr(node, "type", None),
                "estimatedBytes": single_bytes,
            },
        )

        if single_bytes > MAX_PAYLOAD_BYTES:
            trace(
                "Transport",
                "large_payload_selected",
                lambda: "A resource exceeds the single-packet budget and will use large-payload transport.",
                lambda: {
                    "assetId": asset_id,
                    "estimatedBytes": single_bytes,
                    "limitBytes": MAX_PAYLOAD_BYTES,
                },
            )
            flush_current()
            single_envelope = _envelope_dict(builder, [node], package_id)
            batches.append((single_envelope, [node], _estimate_json_bytes(single_envelope)))
            continue

        candidate_sum = current_estimated_bytes + single_bytes
        candidate_count = len(current_nodes) + 1
        candidate_estimate = estimated_batch_bytes(candidate_sum, candidate_count)

        if current_nodes and candidate_estimate > MAX_PAYLOAD_BYTES:
            flush_current()

        current_nodes.append(node)
        current_estimated_bytes += single_bytes

    flush_current()
    return batches


def _run_pipeline(context) -> SendResult:
    resolver = DependencyResolver()
    builder = PackageBuilder()
    session = context.get("session") or BlenderSessionCore()
    package_id = context.get("packageId", "pkg-sprint0")
    send_mode = context.get("sendMode") or "full_send"
    trigger_type = context.get("triggerType") or "unknown"
    update_intent = context.get("updateIntent") or "unknown"
    correlation_id = context.get("correlationId") or "none"

    try:
        if not session.is_alive():
            raise SessionError("handshake_not_confirmed")

        effective_context = dict(context)
        selected_roots = list(context.get("selected", []) or [])
        suppressed_asset_ids: list[str] = []
        trace(
            "Transport",
            "resource_sync_started",
            lambda: "Started a resource sync.",
            lambda: {
                "intent": update_intent,
                "trigger": trigger_type,
                "correlationId": correlation_id,
                "packageId": package_id,
                "sendMode": send_mode,
                "selectedRoots": len(selected_roots),
                "selectedObjects": context.get("selectedObjectCount"),
                "supportedObjects": context.get("supportedSelectedObjectCount"),
                "unsupportedObjects": context.get("unsupportedSelectedObjectCount"),
            },
        )

        if send_mode == "full_send":
            filtered_roots, suppressed_asset_ids = filter_unknown_bootstrap_roots(selected_roots)
            effective_context["selected"] = filtered_roots
            trace(
                "Transport",
                "known_assets_filtered",
                lambda: "Filtered resources already known by Unity.",
                lambda: {
                    "packageId": package_id,
                    "kept": len(filtered_roots),
                    "suppressed": len(suppressed_asset_ids),
                },
            )
        elif send_mode == "manual_resource_resend":
            effective_context["selected"] = selected_roots
            trace(
                "Transport",
                "known_asset_filter_bypassed",
                lambda: "Bypassed the known-asset filter for a manual resend.",
                lambda: {"packageId": package_id, "kept": len(selected_roots)},
            )

        asset_ids_to_mark = collect_asset_ids_from_selected_roots(effective_context.get("selected", []) or [])
        closure = resolver.resolve_selected(effective_context)
        closure_nodes = getattr(closure, "nodes", None) or []

        if closure_nodes:
            _debug_log_closure("selected", package_id, closure)

        if not closure_nodes:
            trace(
                "Transport",
                "resource_package_skipped",
                lambda: "No resource package was needed; sending object-level updates only.",
                lambda: {"packageId": package_id},
            )
            _send_material_contents_if_present(session, context)
            _send_object_assemblies_if_present(session, context)
            _send_name_payloads_if_present(session, context)
            _send_hierarchy_payloads_if_present(session, context)
            _send_visibility_payloads_if_present(session, context)
            _send_rigged_objects_if_present(session, context)
            trace(
                "Transport",
                "resource_sync_completed",
                lambda: "Completed an assemblies-only resource sync.",
                lambda: {
                    "intent": update_intent,
                    "trigger": trigger_type,
                    "packageId": package_id,
                    "mode": "assemblies_only",
                },
            )
            return SendResult(
                ok=True,
                message="assemblies_only",
                error=None,
                payload_size=0,
                last_package_id=package_id,
                last_resource_count=0,
                failure_category=None,
            )

        batches = _build_resource_batches(builder, closure, package_id)
        total_payload_bytes = 0
        total_resource_count = 0
        for batch_index, (envelope_dict, batch_nodes, batch_bytes) in enumerate(batches, start=1):
            total_payload_bytes += max(batch_bytes, 0)
            total_resource_count += len(batch_nodes)
            send_result = session.send_auto(envelope_dict)
            if not send_result.ok:
                raise SessionError(send_result.error or "resource_package_send_failed")
            trace(
                "Transport",
                "resource_batch_sent",
                lambda: "Sent one resource package batch.",
                lambda: {
                    "packageId": package_id,
                    "batchIndex": batch_index,
                    "batchCount": len(batches),
                    "resourceCount": len(batch_nodes),
                    "bytes": batch_bytes,
                },
            )

        if asset_ids_to_mark:
            mark_assets_known(asset_ids_to_mark)
            trace(
                "Transport",
                "assets_marked_known",
                lambda: "Recorded resources acknowledged by Unity.",
                lambda: {"assetCount": len(asset_ids_to_mark)},
            )
        _send_material_contents_if_present(session, context)
        _send_object_assemblies_if_present(session, context)
        _send_name_payloads_if_present(session, context)
        _send_hierarchy_payloads_if_present(session, context)
        _send_visibility_payloads_if_present(session, context)
        _send_rigged_objects_if_present(session, context)
        trace(
            "Transport",
            "resource_sync_completed",
            lambda: "Completed a resource sync.",
            lambda: {
                "intent": update_intent,
                "trigger": trigger_type,
                "packageId": package_id,
                "batchCount": len(batches),
                "resourceCount": total_resource_count,
                "bytes": total_payload_bytes,
            },
        )
        return SendResult(
            ok=True,
            message="sent",
            error=None,
            payload_size=total_payload_bytes,
            last_package_id=package_id,
            last_resource_count=total_resource_count,
            failure_category=None,
        )
    except Exception as exc:
        _log_failure(
            "resource_sync_failed",
            exc,
            {
                "intent": update_intent,
                "trigger": trigger_type,
                "correlationId": correlation_id,
                "packageId": package_id,
            },
        )
        return SendResult(
            ok=False,
            message="pipeline_failed",
            error=str(exc),
            payload_size=0,
            last_package_id=package_id,
            last_resource_count=0,
            failure_category=_classify_failure(exc),
        )


def _debug_log_closure(scope: str, package_id: str, closure) -> None:
    def build_fields() -> dict:
        nodes = getattr(closure, "nodes", []) or []
        return {
            "packageId": package_id,
            "scope": scope,
            "resourceCount": len(nodes),
            "materialCount": sum(1 for node in nodes if getattr(node, "type", None) == "material"),
        }

    trace(
        "Transport",
        "dependency_closure",
        lambda: "Built the dependency closure for a resource sync.",
        build_fields,
    )


def _classify_failure(exc: Exception) -> str:
    if isinstance(exc, SessionError):
        return "session_failure"
    if isinstance(exc, ResolverError):
        return "resolver_failure"
    if isinstance(exc, PackageBuildError):
        return "builder_failure"
    if isinstance(exc, TransportError):
        return "transport_failure"
    return "unknown_failure"
