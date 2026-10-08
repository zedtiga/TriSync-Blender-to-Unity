using UnityEditor;

namespace BlenderSyncVNext.SceneSyncCore
{
    internal static class EditModeGuard
    {
        private static volatile bool _isAvailable = true;

        internal static bool IsAvailable => _isAvailable;

        [InitializeOnLoadMethod]
        private static void Initialize()
        {
            _isAvailable = IsEditMode(
                EditorApplication.isPlaying,
                EditorApplication.isPlayingOrWillChangePlaymode);
            EditorApplication.playModeStateChanged -= HandlePlayModeStateChanged;
            EditorApplication.playModeStateChanged += HandlePlayModeStateChanged;
        }

        private static void HandlePlayModeStateChanged(PlayModeStateChange state)
        {
            switch (state)
            {
                case PlayModeStateChange.EnteredEditMode:
                    _isAvailable = true;
                    break;
                case PlayModeStateChange.ExitingEditMode:
                case PlayModeStateChange.EnteredPlayMode:
                case PlayModeStateChange.ExitingPlayMode:
                    _isAvailable = false;
                    break;
                default:
                    _isAvailable = false;
                    break;
            }
        }

        private static bool IsEditMode(bool isPlaying, bool isPlayingOrWillChangePlaymode)
        {
            return !isPlaying && !isPlayingOrWillChangePlaymode;
        }
    }
}
