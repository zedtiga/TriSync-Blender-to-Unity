# Asset Identity And Registry

TriSync uses stable Blender-side identities and Unity-side registries to
avoid accidental asset collisions while still allowing deliberate updates.

## Identity Concepts

Main identity types:

- `assetId`: stable identity for Blender data that becomes a Unity resource.
- `pairId`: stable identity for a synced Blender object to Unity scene object
  mapping.
- `riggedObjectId`: stable identity for rigged object imports.

Display names are not identities. Two different Blender files can contain
objects, meshes, materials, or textures with the same display name.

## Generated Naming

Generated persistent assets use readable names plus a short stable suffix when
collision safety is required:

```text
FriendlyName_ab12cd.asset
FriendlyName_ab12cd.prefab
FriendlyName_ab12cd.png
```

The suffix comes from the stable identity, not from a random import timestamp.
This gives predictable paths while preventing unrelated same-name assets from
overwriting each other.

Existing registry entries resolve updates to the registered Unity path.

## Shared Meshes

If two Blender objects intentionally share the same Mesh datablock and asset
identity, Unity should reuse the same generated mesh resource.

When editing one instance in Edit Mode, preview commit uses copy-on-write if the
current Unity mesh resource is shared by multiple scene objects. This prevents
one object's local edit from mutating another object unintentionally.

Deliberate full updates to the shared mesh identity continue to update the
shared mesh resource.

## Animation Clips

Animation clips are different from managed mesh/material/texture resources.

Current rule:

- Unity creates generated `.anim` assets.
- Clips are not stored in `animation-clips.json`.
- Clips are not shown as registry-managed update resources.
- Importing a source animation again creates a new unique `.anim` path instead
  of overwriting the old clip.

This is intentional. Animation assets are often edited, referenced, renamed, or
kept as variants inside Unity. A silent overwrite is more dangerous than a new
generated file.

## Registry Files

Unity registry state lives under:

```text
Assets/TriSync/Registry
```

Important registry state includes:

- Resource records for generated mesh/material/texture/prefab-like assets.
- Object bindings for Blender object pair IDs to Unity scene objects.
- Rigged object records and managed instance data.
- Reference and referenced-by relationships.

Registry files are not temporary debug output. They are the state that lets
updates resolve previously imported content.

## Registry Tab

Open:

```text
TriSync/Open TriSync, then select Registry
```

The Registry tab is organized around:

- Resources.
- Objects.
- Registry diagnostics are available in the top-level Diagnostics tab.

It is an inspection and registry maintenance tool. It should not mutate
registry state unless the user invokes an explicit action such as unregister.

## Unregister Behavior

Unregister is a Unity-local ownership cleanup action.

It can:

- Remove selected resource records from the Unity resource registry.
- Remove selected object records from `object-bindings.json`.
- Remove matching rigged-object registry records when applicable.
- Clear current Unity runtime mapped targets for unregistered scene objects.
- Remove unregistered object references from resource `Referenced By` lists.

It does not:

- Delete Unity assets.
- Delete Unity scene objects.
- Delete Blender objects.
- Notify Blender.
- Store offline tombstones.
- Block future payloads with the same `assetId` or `pairId`.

If Blender sends the same asset or scene object after it was unregistered in
Unity, Unity treats it as a normal new import/update and can recreate registry
records.

To stop an object from syncing, disable its Blender-side ready/sync state or
stop sending it from Blender.

## Object Remove

The object remove protocol is separate from manual unregister. When Blender
sends an object remove event, Unity removes the matching scene object and then
cleans the corresponding Unity-side registry records.

This keeps automatic delete behavior cleaner than leaving stale object
bindings behind.
