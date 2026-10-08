from __future__ import annotations

from blender.common.errors import ResolverError
from blender.common.types import DependencyClosure, DependencyNode


class DependencyResolver:
    def resolve_selected(self, context) -> DependencyClosure:
        selected = context.get("selected", [])
        return self._resolve(selected)

    def _resolve(self, roots) -> DependencyClosure:
        nodes: dict[str, DependencyNode] = {}
        missing: list[str] = []

        for root in roots:
            self._collect_recursive(root, nodes, missing)

        if missing:
            raise ResolverError(f"missing_dependencies: {missing}")

        ordered = self._stable_sort(list(nodes.values()))
        return DependencyClosure(nodes=ordered, missing_dependencies=[])

    def _collect_recursive(self, obj, nodes: dict[str, DependencyNode], missing: list[str]) -> None:
        key = obj.get("key")
        if not key:
            missing.append("missing_key")
            return

        node = DependencyNode(
            key=key,
            type=obj.get("type", "mesh"),
            source_uri=obj.get("source_uri", "unknown://source"),
            metadata=obj.get("metadata", {}),
        )
        nodes[key] = node

        for dep in obj.get("deps", []):
            self._collect_recursive(dep, nodes, missing)

    def _stable_sort(self, nodes: list[DependencyNode]) -> list[DependencyNode]:
        return sorted(nodes, key=lambda n: (n.type, n.key, n.source_uri))

