#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;
using static BlenderSyncVNext.Localization.BlenderSyncLocalization;

namespace BlenderSyncVNext.RootMotion
{
    public sealed class AnimationToolsWindow : EditorWindow
    {
        private const string MenuPath = "TriSync/Open Animation Tools";
        private const string ActiveTabEditorPrefKey = "BlenderSyncVNext.AnimationTools.ActiveTab";

        private enum AnimationTab
        {
            HumanoidAvatar = 0,
            HumanoidClips = 1,
            RootMotion = 2,
        }

        private static readonly string[] TabLabels =
        {
            "Humanoid Avatar",
            "Humanoid Clips",
            "Root Motion",
        };

        private int _activeTab;
        private HumanoidAvatarSetupView _avatarView;
        private HumanoidClipConverterView _clipView;
        private RootMotionWorkflowView _rootMotionView;

        [MenuItem(MenuPath)]
        public static void Open()
        {
            var window = GetWindow<AnimationToolsWindow>(false, Tr("TriSync Animation"), true);
            window.minSize = new Vector2(760f, 560f);
            window.Show();
        }

        private void OnEnable()
        {
            titleContent = new GUIContent(Tr("TriSync Animation"));
            minSize = new Vector2(760f, 560f);
            _activeTab = NormalizeTab(EditorPrefs.GetInt(
                ActiveTabEditorPrefKey,
                (int)AnimationTab.HumanoidAvatar));
            EnsureViews();
        }

        private void OnGUI()
        {
            EnsureViews();
            DrawTabs();
            EditorGUILayout.Space(6f);
            DrawActiveView();
        }

        private void EnsureViews()
        {
            if (_avatarView == null)
                _avatarView = new HumanoidAvatarSetupView();
            if (_clipView == null)
                _clipView = new HumanoidClipConverterView();
            if (_rootMotionView == null)
                _rootMotionView = new RootMotionWorkflowView();
        }

        private void DrawTabs()
        {
            using (new EditorGUILayout.HorizontalScope(EditorStyles.toolbar))
            {
                var selected = GUILayout.Toolbar(
                    NormalizeTab(_activeTab),
                    new[] { Tr(TabLabels[0]), Tr(TabLabels[1]), Tr(TabLabels[2]) },
                    EditorStyles.toolbarButton,
                    GUILayout.ExpandWidth(true));
                if (selected != _activeTab)
                    SelectTab(selected);
            }
        }

        private void SelectTab(int value)
        {
            _activeTab = NormalizeTab(value);
            EditorPrefs.SetInt(ActiveTabEditorPrefKey, _activeTab);
        }

        private void DrawActiveView()
        {
            switch ((AnimationTab)NormalizeTab(_activeTab))
            {
                case AnimationTab.HumanoidClips:
                    _clipView.Activate();
                    _clipView.Draw();
                    break;
                case AnimationTab.RootMotion:
                    _rootMotionView.Draw();
                    break;
                default:
                    _avatarView.Activate();
                    _avatarView.Draw();
                    break;
            }
        }

        private static int NormalizeTab(int value)
        {
            return value >= (int)AnimationTab.HumanoidAvatar && value <= (int)AnimationTab.RootMotion
                ? value
                : (int)AnimationTab.HumanoidAvatar;
        }

        private static string TabLabel(int value)
        {
            return TabLabels[NormalizeTab(value)];
        }

        private static string CanonicalMenuPath()
        {
            return MenuPath;
        }
    }
}
#endif
