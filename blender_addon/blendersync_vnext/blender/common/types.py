from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionState:
    local_ready: bool = False
    connect_attempted: bool = False
    transport_connected: bool = False
    counterpart_observed: bool = False
    handshake_confirmed: bool = False
    current_handshake_id: str | None = None
    endpoint: str | None = None
    last_error: str | None = None
    assets_import_root: str | None = None
    texture_export_root: str | None = None
    peer_protocol_version: int | None = None
    negotiated_features: tuple[str, ...] = field(default_factory=tuple)
    legacy_protocol: bool = False


@dataclass(frozen=True)
class DependencyNode:
    key: str
    type: str
    source_uri: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DependencyClosure:
    nodes: list[DependencyNode] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)


@dataclass
class MeshSkinPayload:
    isSkinned: bool = False
    skinEncoding: str = "variable"
    spaceSemantic: str = "blender_live_v0"
    boneCount: int = 0
    bindPoses: list[list[float]] = field(default_factory=list)
    bonesPerVertex: list[int] = field(default_factory=list)
    boneIndices: list[int] = field(default_factory=list)
    boneWeights: list[float] = field(default_factory=list)
    meshWorldMatrix: list[float] = field(default_factory=list)
    armatureWorldMatrix: list[float] = field(default_factory=list)
    clusterTransforms: list[float] = field(default_factory=list)
    clusterTransformLinks: list[float] = field(default_factory=list)
    clusterAssociateModel: list[float] = field(default_factory=list)


@dataclass
class MeshPayload:
    topology: str = "triangles"
    spaceSemantic: str = "blender_live_v0"
    vertexCount: int = 0
    vertices: list[float] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    subMeshes: list[dict] | None = None
    normals: list[float] = field(default_factory=list)
    uv0: list[float] = field(default_factory=list)
    uvChannels: list[dict] | None = None
    uvChannelCount: int = 0
    uvChannelNames: list[str] | None = None
    color0: list[float] | None = None
    color0Buffer: dict | None = None
    colorAttributeName: str = ""
    colorAttributeCount: int = 0
    binaryBuffers: list[dict] | None = None
    binaryProfile: dict | None = None
    skin: MeshSkinPayload | None = None
    blendShapes: list[dict] | None = None
    meshSource: str = "original"
    meshSourceRequested: str = "auto"
    meshSourceResolved: str = "original"
    meshSourceReason: str = ""
    sourceVertexCount: int = 0
    sourcePolygonCount: int = 0
    evaluatedVertexCount: int = 0
    evaluatedPolygonCount: int = 0
    evaluatedLoopCount: int = 0
    evaluatedTriangleCount: int = 0
    modifierSummary: list[str] | None = None
    blendShapeSkipReason: str = ""
    skinSkipReason: str = ""


@dataclass
class ResourceEntry:
    assetId: str
    sourceFingerprint: str
    type: str
    source: dict[str, Any]
    meshContentFingerprint: str | None = None
    meshContentFingerprintNoUv: str | None = None
    mesh: MeshPayload | None = None


@dataclass
class RiggedBonePayload:
    boneId: str
    name: str
    parentBoneId: str | None = None
    localPosition: list[float] = field(default_factory=list)
    localRotation: list[float] = field(default_factory=list)
    localScale: list[float] = field(default_factory=list)
    localMatrix: list[float] = field(default_factory=list)
    restLocalPosition: list[float] = field(default_factory=list)
    restLocalRotation: list[float] = field(default_factory=list)
    restLocalScale: list[float] = field(default_factory=list)
    restLocalMatrix: list[float] = field(default_factory=list)
    isLeaf: bool = False
    tailLocalPosition: list[float] = field(default_factory=list)
    tailLocalMatrix: list[float] = field(default_factory=list)


@dataclass
class RiggedSkeletonPayload:
    bones: list[RiggedBonePayload] = field(default_factory=list)


@dataclass
class RiggedPrefabPolicyPayload:
    generatePrefab: bool = True
    autoInstantiate: bool = True


@dataclass
class RiggedMeshPartPayload:
    objectName: str
    sourceObjectPath: str
    meshRef: str
    visibilityState: str = "visible"
    materialRefs: list[str] = field(default_factory=list)
    orderedBoneIds: list[str] = field(default_factory=list)
    localPosition: list[float] = field(default_factory=list)
    localRotation: list[float] = field(default_factory=list)
    localScale: list[float] = field(default_factory=list)


@dataclass
class RiggedObjectPayload:
    riggedObjectId: str
    objectName: str
    sourceObjectPath: str
    sourceArmatureObjectPath: str
    meshRef: str
    spaceSemantic: str = "blender_live_v0"
    rigAxisMode: str = "baked_joint_axes"
    primaryBoneAxis: str = "Z"
    secondaryBoneAxis: str = "X"
    visibilityState: str = "visible"
    materialRefs: list[str] = field(default_factory=list)
    meshParts: list[RiggedMeshPartPayload] = field(default_factory=list)
    rigRootLocalPosition: list[float] = field(default_factory=list)
    rigRootLocalRotation: list[float] = field(default_factory=list)
    rigRootLocalScale: list[float] = field(default_factory=list)
    armatureLocalPosition: list[float] = field(default_factory=list)
    armatureLocalRotation: list[float] = field(default_factory=list)
    armatureLocalScale: list[float] = field(default_factory=list)
    rootBoneId: str = ""
    orderedBoneIds: list[str] = field(default_factory=list)
    skeleton: RiggedSkeletonPayload = field(default_factory=RiggedSkeletonPayload)
    prefabPolicy: RiggedPrefabPolicyPayload = field(default_factory=RiggedPrefabPolicyPayload)


@dataclass
class PackageEnvelope:
    contractVersion: str
    package: dict[str, Any]


@dataclass
class SendResult:
    ok: bool
    message: str
    error: str | None = None
    payload_size: int = 0
    last_package_id: str | None = None
    last_resource_count: int = 0
    failure_category: str | None = None
