using System;
using System.Collections.Generic;
using System.Linq;
using BlenderSyncVNext.APT;
using BlenderSyncVNext.Diagnostics;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace BlenderSyncVNext.SceneSyncCore
{
    // Minimal material reference apply helper (A-slice).
    // Current priority: resolve managed material asset through APT mapping first.
    // Runtime registry remains as probe/support fallback.
    public sealed class MaterialReferenceApplyService
    {
        private const string DefaultMaterialAssetPath = GeneratedResourcePaths.DefaultMaterialAssetPath;
        private const string BlenderSyncLitShaderName = "TriSync/Principled Lit URP";
        private const string UrpLitShaderName = "Universal Render Pipeline/Lit";
        private const int MaxPendingRetryAttempts = 5;
        private static readonly object MaterialRegistryLock = new object();
        private static readonly Dictionary<string, Material> MaterialRegistry = new Dictionary<string, Material>();
        private static Material RuntimeDefaultMaterial;
        private static readonly object PendingLock = new object();
        private static readonly List<PendingMaterialRefs> PendingMaterialRefEntries = new List<PendingMaterialRefs>();
        private readonly ResourceResolver _resolver = new ResourceResolver();

        private sealed class PendingMaterialRefs
        {
            public string pairId;
            public GameObject target;
            public string[] materialRefs;
            public string[] unresolvedRefs;
            public string reason;
            public int attempts;
        }

        public static void RegisterRuntimeMaterial(string resourceRef, Material material)
        {
            var normalizedRef = NormalizeResourceRef(resourceRef);
            if (string.IsNullOrWhiteSpace(normalizedRef) || material == null) return;
            lock (MaterialRegistryLock)
            {
                MaterialRegistry[normalizedRef] = material;
            }
        }

        public bool TryApply(GameObject target, string resourceRef, out string error)
        {
            error = null;
            if (target == null)
            {
                error = "target is null";
                return false;
            }
            var normalizedRef = NormalizeResourceRef(resourceRef);
            if (string.IsNullOrWhiteSpace(normalizedRef))
            {
                error = "resourceRef is missing";
                return false;
            }

            var renderer = target.GetComponent<Renderer>();
            if (renderer == null)
            {
                error = "renderer missing";
                return false;
            }

            if (_resolver.TryResolveMaterial(normalizedRef, out var managedMat, out var resolveErr) && managedMat != null)
            {
                renderer.sharedMaterial = managedMat;
                return true;
            }

            error = resolveErr;

            if (!TryGetRuntimeMaterial(normalizedRef, out var mat))
            {
                error = "material_not_resolved";
                return false;
            }

            renderer.sharedMaterial = mat;
            return true;
        }

        public bool TryApplyMany(GameObject target, IReadOnlyList<string> resourceRefs, out string error)
        {
            return TryApplyMany(target, resourceRefs, out error, out _);
        }

        public bool TryApplyMany(GameObject target, IReadOnlyList<string> resourceRefs, out string error, out string[] unresolvedRefs, bool warnOnUnresolved = true)
        {
            error = null;
            unresolvedRefs = Array.Empty<string>();
            if (target == null)
            {
                error = "target is null";
                return false;
            }

            var renderer = target.GetComponent<Renderer>();
            if (renderer == null)
            {
                error = "renderer missing";
                return false;
            }

            if (resourceRefs == null)
            {
                error = "resourceRefs missing";
                return false;
            }

            var requested = resourceRefs.Count;
            if (requested == 0)
            {
                var defaultMaterial = ResolveDefaultMaterial();
                renderer.sharedMaterials = defaultMaterial != null
                    ? new[] { defaultMaterial }
                    : Array.Empty<Material>();
                BlenderSyncLog.Trace(
                    "Material",
                    "default_material_applied",
                    () => "Applied the TriSync default material.",
                    () => new Dictionary<string, object>
                    {
                        { "applied", defaultMaterial != null ? 1 : 0 },
                    });
                return true;
            }

            var resolved = new List<Material>();
            var unresolved = new List<string>();
            var unresolvedSlots = new List<bool>();
            var nonEmptyRequested = 0;
            foreach (var resourceRef in resourceRefs)
            {
                var normalizedRef = NormalizeResourceRef(resourceRef);
                if (string.IsNullOrWhiteSpace(normalizedRef))
                {
                    resolved.Add(null);
                    unresolvedSlots.Add(false);
                    continue;
                }
                nonEmptyRequested++;

                if (_resolver.TryResolveMaterial(normalizedRef, out var managedMat, out var _) && managedMat != null)
                {
                    resolved.Add(managedMat);
                    unresolvedSlots.Add(false);
                    continue;
                }

                if (TryGetRuntimeMaterial(normalizedRef, out var runtimeMat))
                {
                    resolved.Add(runtimeMat);
                    unresolvedSlots.Add(false);
                    continue;
                }

                resolved.Add(null);
                unresolvedSlots.Add(true);
                unresolved.Add(normalizedRef);
            }

            unresolvedRefs = DistinctRefs(unresolved);
            if (nonEmptyRequested > 0 && resolved.All(m => m == null))
            {
                LogResolutionIssue(
                    warnOnUnresolved,
                    "references_unresolved",
                    "No requested material references could be resolved.",
                    requested,
                    0,
                    unresolvedRefs.Length);
                error = "material_not_resolved";
                return false;
            }

            renderer.sharedMaterials = PreserveUnresolvedSlots(renderer.sharedMaterials, resolved, unresolvedSlots);
            var applied = resolved.Count(m => m != null);

            if (applied < nonEmptyRequested)
                LogResolutionIssue(
                    warnOnUnresolved,
                    "references_partially_resolved",
                    "Some material references are still unresolved.",
                    requested,
                    applied,
                    unresolvedRefs.Length);
            else
                BlenderSyncLog.Trace(
                    "Material",
                    "references_applied",
                    () => "Applied material references to the object.",
                    () => new Dictionary<string, object>
                    {
                        { "requested", requested },
                        { "resolved", applied },
                    });

            return true;
        }

        private static Material[] PreserveUnresolvedSlots(Material[] currentMaterials, IReadOnlyList<Material> resolved, IReadOnlyList<bool> unresolvedSlots)
        {
            if (resolved == null || resolved.Count == 0)
                return Array.Empty<Material>();

            var fallback = ResolveDefaultMaterial();
            var materials = new Material[resolved.Count];
            for (var i = 0; i < resolved.Count; i++)
            {
                var material = resolved[i];
                if (material != null)
                {
                    materials[i] = material;
                    continue;
                }

                var unresolved = unresolvedSlots != null && i < unresolvedSlots.Count && unresolvedSlots[i];
                if (!unresolved)
                {
                    materials[i] = null;
                    continue;
                }

                materials[i] = currentMaterials != null && i < currentMaterials.Length && currentMaterials[i] != null
                    ? currentMaterials[i]
                    : fallback;
            }
            return materials;
        }

        public static Material ResolveDefaultMaterial()
        {
#if UNITY_EDITOR
            var material = AssetDatabase.LoadAssetAtPath<Material>(DefaultMaterialAssetPath);
            if (material != null)
                return material;

            var shader = ResolveDefaultShader();
            if (shader == null)
                return null;

            EnsureAssetFolder(System.IO.Path.GetDirectoryName(DefaultMaterialAssetPath));
            material = new Material(shader)
            {
                name = "TriSyncDefault",
            };
            ApplyDefaultMaterialColor(material);
            AssetDatabase.CreateAsset(material, DefaultMaterialAssetPath);
            AssetDatabase.SaveAssets();
            return AssetDatabase.LoadAssetAtPath<Material>(DefaultMaterialAssetPath) ?? material;
#else
            if (RuntimeDefaultMaterial != null)
                return RuntimeDefaultMaterial;
            var shader = ResolveDefaultShader();
            RuntimeDefaultMaterial = shader != null ? new Material(shader) { name = "TriSyncDefault" } : null;
            ApplyDefaultMaterialColor(RuntimeDefaultMaterial);
            return RuntimeDefaultMaterial;
#endif
        }

        private static void ApplyDefaultMaterialColor(Material material)
        {
            if (material == null)
                return;
            var color = new Color(0.8f, 0.8f, 0.8f, 1.0f);
            if (material.HasProperty("_BSG_BaseColor")) material.SetColor("_BSG_BaseColor", color);
            else if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor", color);
            else if (material.HasProperty("_Color")) material.SetColor("_Color", color);
        }

        private static Shader ResolveDefaultShader()
        {
            return Shader.Find(BlenderSyncLitShaderName)
                   ?? Shader.Find(UrpLitShaderName)
                   ?? Shader.Find("Standard")
                   ?? Shader.Find("Sprites/Default");
        }

#if UNITY_EDITOR
        private static void EnsureAssetFolder(string assetFolder)
        {
            if (string.IsNullOrWhiteSpace(assetFolder))
                return;
            var normalized = assetFolder.Replace('\\', '/');
            var parts = normalized.Split('/');
            if (parts.Length == 0 || parts[0] != "Assets")
                return;
            var current = "Assets";
            for (var i = 1; i < parts.Length; i++)
            {
                var next = current + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next))
                    AssetDatabase.CreateFolder(current, parts[i]);
                current = next;
            }
        }
#endif

        public static void RegisterPending(GameObject target, string pairId, IReadOnlyList<string> materialRefs, IReadOnlyList<string> unresolvedRefs, string reason)
        {
            if (target == null || materialRefs == null || unresolvedRefs == null || unresolvedRefs.Count == 0)
                return;

            var normalizedRefs = materialRefs.Select(NormalizeResourceRef).ToArray();
            var unresolved = DistinctRefs(unresolvedRefs.Select(NormalizeResourceRef));
            if (unresolved.Length == 0)
                return;

            var normalizedPairId = NormalizeResourceRef(pairId);
            lock (PendingLock)
            {
                var existing = PendingMaterialRefEntries.FirstOrDefault(p =>
                    p != null
                    && p.target == target
                    && string.Equals(p.pairId, normalizedPairId, StringComparison.Ordinal)
                    && SameRefs(p.materialRefs, normalizedRefs));
                if (existing == null)
                {
                    PendingMaterialRefEntries.Add(new PendingMaterialRefs
                    {
                        pairId = normalizedPairId,
                        target = target,
                        materialRefs = normalizedRefs,
                        unresolvedRefs = unresolved,
                        reason = reason,
                    });
                }
                else
                {
                    existing.unresolvedRefs = unresolved;
                    existing.reason = reason;
                    existing.attempts = 0;
                }
            }

            BlenderSyncLog.Trace(
                "Material",
                "references_queued",
                () => "Queued unresolved material references for retry.",
                () => new Dictionary<string, object>
                {
                    { "pairId", normalizedPairId },
                    { "unresolved", unresolved.Length },
                    { "reason", reason },
                });
        }

        public static int RetryPendingForMaterial(string materialRef)
        {
            var normalizedMaterialRef = NormalizeResourceRef(materialRef);
            if (string.IsNullOrWhiteSpace(normalizedMaterialRef))
                return 0;

            List<PendingMaterialRefs> snapshot;
            lock (PendingLock)
            {
                snapshot = PendingMaterialRefEntries
                    .Where(p => p != null && p.unresolvedRefs != null && p.unresolvedRefs.Contains(normalizedMaterialRef))
                    .ToList();
            }

            var retried = 0;
            var service = new MaterialReferenceApplyService();
            var linkRegistry = new ObjectResourceLinkRegistry();
            foreach (var pending in snapshot)
            {
                if (pending.target == null)
                {
                    RemovePending(pending);
                    continue;
                }
                if (pending.attempts >= MaxPendingRetryAttempts)
                {
                    RemovePending(pending);
                    continue;
                }

                pending.attempts++;
                retried++;
                var unresolvedAfterRetry = 0;
                if (service.TryApplyMany(
                    pending.target,
                    pending.materialRefs,
                    out var err,
                    out var unresolved,
                    warnOnUnresolved: false))
                {
                    if (unresolved == null || unresolved.Length == 0)
                    {
                        linkRegistry.UpdateReference(pending.pairId, pending.target, "material", FirstNonEmpty(pending.materialRefs), pending.materialRefs);
                        RemovePending(pending);
                        LogRetryTrace("retry_completed", pending, normalizedMaterialRef, 0, null);
                    }
                    else
                    {
                        pending.unresolvedRefs = unresolved;
                        unresolvedAfterRetry = unresolved.Length;
                        LogRetryTrace("retry_partial", pending, normalizedMaterialRef, unresolved.Length, null);
                    }
                }
                else if (unresolved != null && unresolved.Length > 0)
                {
                    pending.unresolvedRefs = unresolved;
                    unresolvedAfterRetry = unresolved.Length;
                    LogRetryTrace("retry_waiting", pending, normalizedMaterialRef, unresolved.Length, err);
                }
                else
                {
                    BlenderSyncLog.Warn(
                        "Material",
                        "reference_retry_failed",
                        err ?? "A material reference retry failed.",
                        new Dictionary<string, object>
                        {
                            { "pairId", pending.pairId },
                            { "materialRef", normalizedMaterialRef },
                            { "attempt", pending.attempts },
                        });
                }

                if (pending.attempts >= MaxPendingRetryAttempts && unresolvedAfterRetry > 0)
                {
                    BlenderSyncLog.Warn(
                        "Material",
                        "reference_retry_exhausted",
                        "Material references remained unresolved after the retry limit.",
                        new Dictionary<string, object>
                        {
                            { "pairId", pending.pairId },
                            { "materialRef", normalizedMaterialRef },
                            { "attempts", pending.attempts },
                            { "unresolved", unresolvedAfterRetry },
                        });
                }

                if (pending.attempts >= MaxPendingRetryAttempts)
                    RemovePending(pending);
            }

            return retried;
        }

        private static string NormalizeResourceRef(string resourceRef)
        {
            return (resourceRef ?? string.Empty).Trim();
        }

        private static void LogResolutionIssue(
            bool warn,
            string eventName,
            string summary,
            int requested,
            int resolved,
            int unresolved)
        {
            var fields = new Dictionary<string, object>
            {
                { "requested", requested },
                { "resolved", resolved },
                { "unresolved", unresolved },
            };
            if (warn)
                BlenderSyncLog.Warn("Material", eventName, summary, fields);
            else
                BlenderSyncLog.Trace("Material", eventName, () => summary, () => fields);
        }

        private static void LogRetryTrace(
            string eventName,
            PendingMaterialRefs pending,
            string materialRef,
            int unresolved,
            string error)
        {
            BlenderSyncLog.Trace(
                "Material",
                eventName,
                () => "Processed a queued material reference retry.",
                () => new Dictionary<string, object>
                {
                    { "pairId", pending?.pairId },
                    { "materialRef", materialRef },
                    { "attempt", pending?.attempts ?? 0 },
                    { "unresolved", unresolved },
                    { "error", error },
                });
        }

        private static string[] DistinctRefs(IEnumerable<string> refsToNormalize)
        {
            if (refsToNormalize == null)
                return Array.Empty<string>();
            return refsToNormalize
                .Select(NormalizeResourceRef)
                .Where(r => !string.IsNullOrWhiteSpace(r))
                .Distinct(StringComparer.Ordinal)
                .ToArray();
        }

        private static bool SameRefs(string[] a, string[] b)
        {
            if (a == null || b == null)
                return a == b;
            if (a.Length != b.Length)
                return false;
            for (var i = 0; i < a.Length; i++)
            {
                if (!string.Equals(a[i], b[i], StringComparison.Ordinal))
                    return false;
            }
            return true;
        }

        private static string FirstNonEmpty(string[] values)
        {
            if (values == null)
                return null;
            for (var i = 0; i < values.Length; i++)
            {
                if (!string.IsNullOrWhiteSpace(values[i]))
                    return values[i].Trim();
            }
            return null;
        }

        private static void RemovePending(PendingMaterialRefs pending)
        {
            lock (PendingLock)
            {
                PendingMaterialRefEntries.Remove(pending);
            }
        }

        private static bool TryGetRuntimeMaterial(string normalizedRef, out Material material)
        {
            lock (MaterialRegistryLock)
            {
                if (MaterialRegistry.TryGetValue(normalizedRef, out material) && material != null)
                {
                    return true;
                }
            }

            material = null;
            return false;
        }
    }
}
