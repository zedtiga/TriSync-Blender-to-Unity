# TriSync Documentation

This directory contains only the current core documentation for TriSync.
Older phase notes, temporary diagnostics, and retired experiments are not kept
as active docs. The public repository starts from the current source snapshot.

## Core Documents

| Document | Purpose |
| --- | --- |
| [QUICK_START.md](QUICK_START.md) | User-facing install, first connection, first import, and common issues. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Current module boundaries and active product paths. |
| [PROTOCOL.md](PROTOCOL.md) | Blender/Unity message contracts, payload formats, and generation rules. |
| [ASSET_IDENTITY.md](ASSET_IDENTITY.md) | Asset IDs, generated paths, registries, shared meshes, and unregister behavior. |
| [TESTING.md](TESTING.md) | Automated validation, smoke tests, focused regression areas, and unit test targets. |
| [COMPATIBILITY.md](COMPATIBILITY.md) | Current support range, verified version matrix, and multi-version test runner. |
| [RELEASE.md](RELEASE.md) | Stable handoff and tagging checklist. |
| [HUMANOID.md](HUMANOID.md) | Humanoid Avatar generation and clip conversion boundary. |
| [RIG_AXIS.md](RIG_AXIS.md) | Rig axis modes and rest-bone axis boundaries. |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Development setup, local checks, and contribution guidance. |

## Documentation Rules

- Keep these docs current with the code and product decisions.
- Do not add date-stamped phase notes for normal work.
- If a behavior changes, update the relevant core document instead of creating a
  new one-off record.
- Temporary debugging scripts, payload dumps, and retired probes should not live
  in `docs`.
- Generated Unity assets, Blender caches, and runtime debug snapshots should
  stay ignored by Git.

## Current Version

- Product version: `1.0.0` from the repository `VERSION` file
- Release branch: `main`
- Blender add-on version: `1.0.0`
- Native helper component version: `0.2.0`

Product, native component, and protocol/contract versions are independent.
Protocol identifiers change only for incompatible wire-contract changes.

Current support target:

- Blender `4.2.x` through `5.2.x`
- Unity `6000.0.x` through `6000.5.x`
- Universal Render Pipeline `17.0.x` through `17.5.x`

Unity and URP minors are supported as matching `6000.n` / `17.n` pairs. The
tested patch releases, endpoint results, and perimeter interoperability coverage
are recorded in [COMPATIBILITY.md](COMPATIBILITY.md). Versions outside these
ranges require their own compatibility promotion gate.
