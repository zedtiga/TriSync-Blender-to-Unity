#if UNITY_EDITOR
using System;
using System.Linq;
using System.Reflection;
using BlenderSyncVNext.Diagnostics;
using UnityEditor;
using UnityEngine;

namespace BlenderSyncVNext.RootMotion
{
    public static class AvatarConfigureBridge
    {
        public static bool TryOpen(UnityEngine.Object context, out string diagnostic)
        {
            context = context != null ? context : Selection.activeObject;
            var contextPath = context != null ? AssetDatabase.GetAssetPath(context) : null;
            var importer = !string.IsNullOrEmpty(contextPath) ? AssetImporter.GetAtPath(contextPath) as ModelImporter : null;

            if (importer != null)
            {
                Selection.activeObject = context;
                EditorGUIUtility.PingObject(context);
                TrySetInspectorDebugMode(false);
                diagnostic = $"Selected model importer asset '{contextPath}'. If Unity exposes Configure for this model, use Inspector > Rig > Configure. Internal configure window is importer-owned.";
                BlenderSyncReportStore.Add("Humanoid Configure", "INFO", diagnostic);
                return true;
            }

            var avatar = context as Avatar;
            if (avatar == null && context is Animator animator)
                avatar = animator.avatar;
            if (avatar == null && context is GameObject go)
                avatar = go.GetComponentInParent<Animator>()?.avatar ?? go.GetComponentInChildren<Animator>()?.avatar;

            var attempts = 0;
            var failedAttempts = 0;
            string firstFailureType = null;
            foreach (var type in AppDomain.CurrentDomain.GetAssemblies().SelectMany(SafeGetTypes))
            {
                var fullName = type.FullName ?? string.Empty;
                if (fullName.IndexOf("Avatar", StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                if (fullName.IndexOf("Preview", StringComparison.OrdinalIgnoreCase) >= 0)
                    continue;

                foreach (var method in type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static))
                {
                    if (!LooksLikeConfigureEntrypoint(method.Name))
                        continue;
                    var parameters = method.GetParameters();
                    if (parameters.Length > 2)
                        continue;
                    attempts++;
                    try
                    {
                        var args = BuildArgs(parameters, avatar, context);
                        if (args == null)
                            continue;
                        method.Invoke(null, args);
                        diagnostic = $"Invoked internal Unity method {fullName}.{method.Name}. This is unsupported and may be version-dependent.";
                        BlenderSyncReportStore.Add("Humanoid Configure", "WARN", diagnostic);
                        TraceProbeFailures(attempts, failedAttempts, firstFailureType);
                        return true;
                    }
                    catch (Exception ex)
                    {
                        failedAttempts++;
                        if (firstFailureType == null)
                            firstFailureType = ex.GetBaseException().GetType().Name;
                    }
                }
            }

            TraceProbeFailures(attempts, failedAttempts, firstFailureType);
            diagnostic = avatar != null
                ? $"No compatible internal Configure entry point accepted standalone Avatar '{avatar.name}'. Checked {attempts} candidate methods. Unity's native Humanoid Configure UI appears to be ModelImporter/FBX-owned in this version."
                : $"No Avatar or ModelImporter context found. Select a model asset, Animator, GameObject, or Avatar first. Checked {attempts} candidate methods.";
            BlenderSyncReportStore.Add("Humanoid Configure", "WARN", diagnostic);
            return false;
        }

        private static void TraceProbeFailures(int attempts, int failedAttempts, string firstFailureType)
        {
            if (failedAttempts <= 0)
                return;
            BlenderSyncLog.Trace(
                "HumanoidConfigure",
                "internal_probe_summary",
                () => "Some internal Unity avatar configuration entry points rejected the supplied context.",
                () => new System.Collections.Generic.Dictionary<string, object>
                {
                    { "attempts", attempts },
                    { "failedAttempts", failedAttempts },
                    { "firstFailureType", firstFailureType },
                });
        }

        private static bool LooksLikeConfigureEntrypoint(string name)
        {
            return name.IndexOf("Configure", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("AvatarSetup", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("Open", StringComparison.OrdinalIgnoreCase) >= 0 && name.IndexOf("Avatar", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("Show", StringComparison.OrdinalIgnoreCase) >= 0 && name.IndexOf("Avatar", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static object[] BuildArgs(ParameterInfo[] parameters, Avatar avatar, UnityEngine.Object context)
        {
            var args = new object[parameters.Length];
            for (var i = 0; i < parameters.Length; i++)
            {
                var p = parameters[i].ParameterType;
                if (p == typeof(Avatar))
                {
                    if (avatar == null)
                        return null;
                    args[i] = avatar;
                }
                else if (p == typeof(UnityEngine.Object))
                {
                    args[i] = context != null ? context : avatar;
                }
                else if (p == typeof(GameObject))
                {
                    if (context is GameObject go)
                        args[i] = go;
                    else if (context is Animator animator)
                        args[i] = animator.gameObject;
                    else
                        return null;
                }
                else if (p == typeof(Animator))
                {
                    if (context is Animator animator)
                        args[i] = animator;
                    else if (context is GameObject go)
                        args[i] = go.GetComponentInParent<Animator>() ?? go.GetComponentInChildren<Animator>();
                    else
                        return null;
                    if (args[i] == null)
                        return null;
                }
                else if (p == typeof(bool))
                {
                    args[i] = true;
                }
                else if (p == typeof(int))
                {
                    args[i] = 0;
                }
                else
                {
                    return null;
                }
            }
            return args;
        }

        private static Type[] SafeGetTypes(Assembly assembly)
        {
            try
            {
                return assembly.GetTypes();
            }
            catch (ReflectionTypeLoadException ex)
            {
                return ex.Types.Where(t => t != null).ToArray();
            }
            catch
            {
                return Array.Empty<Type>();
            }
        }

        private static void TrySetInspectorDebugMode(bool debug)
        {
            // Placeholder for future importer-focused UX; keep native inspector behavior unchanged for now.
        }
    }
}
#endif
