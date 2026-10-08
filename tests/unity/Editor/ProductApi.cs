using System;
using System.Linq;
using System.Reflection;

namespace BlenderSyncVNext.Tests
{
    internal static class ProductApi
    {
        public static Type RequireType(string fullName)
        {
            var type = AppDomain.CurrentDomain
                .GetAssemblies()
                .Select(assembly => assembly.GetType(fullName, false))
                .FirstOrDefault(candidate => candidate != null);
            if (type == null)
                throw new InvalidOperationException($"Product type not found: {fullName}");
            return type;
        }

        public static MethodInfo RequireStaticMethod(string typeName, string methodName, int parameterCount)
        {
            var method = RequireType(typeName)
                .GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
                .SingleOrDefault(candidate =>
                    candidate.Name == methodName &&
                    candidate.GetParameters().Length == parameterCount);
            if (method == null)
                throw new InvalidOperationException($"Product method not found: {typeName}.{methodName}/{parameterCount}");
            return method;
        }
    }
}
