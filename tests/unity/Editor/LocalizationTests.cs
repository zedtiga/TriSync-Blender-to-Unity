using BlenderSyncVNext.Localization;
using NUnit.Framework;
using UnityEngine;

namespace BlenderSyncVNext.Tests
{
    public sealed class LocalizationTests
    {
        [Test]
        public void ExplicitLanguageLookupIsDeterministic()
        {
            Assert.That(
                BlenderSyncLocalization.TranslateForLanguage(
                    "Session",
                    BlenderSyncLanguage.English),
                Is.EqualTo("Session"));
            Assert.That(
                BlenderSyncLocalization.TranslateForLanguage(
                    "Session",
                    BlenderSyncLanguage.SimplifiedChinese),
                Is.EqualTo("会话"));
        }

        [Test]
        public void UnknownProtocolAndLogTextRemainUnchanged()
        {
            const string protocol = "handshake_confirmed";
            const string logSummary = "Sent a direct JSON mesh snapshot to Blender.";
            Assert.That(
                BlenderSyncLocalization.TranslateForLanguage(
                    protocol,
                    BlenderSyncLanguage.SimplifiedChinese),
                Is.EqualTo(protocol));
            Assert.That(
                BlenderSyncLocalization.TranslateForLanguage(
                    logSummary,
                    BlenderSyncLanguage.SimplifiedChinese),
                Is.EqualTo(logSummary));
        }

        [Test]
        public void SystemLanguageOnlySelectsTheSupportedChineseLocale()
        {
            Assert.That(
                BlenderSyncLocalization.ResolveLanguage(
                    BlenderSyncLanguage.System,
                    SystemLanguage.ChineseSimplified),
                Is.EqualTo(BlenderSyncLanguage.SimplifiedChinese));
            Assert.That(
                BlenderSyncLocalization.ResolveLanguage(
                    BlenderSyncLanguage.System,
                    SystemLanguage.English),
                Is.EqualTo(BlenderSyncLanguage.English));
            Assert.That(
                BlenderSyncLocalization.ResolveLanguage(
                    BlenderSyncLanguage.English,
                    SystemLanguage.ChineseSimplified),
                Is.EqualTo(BlenderSyncLanguage.English));
        }

        [Test]
        public void LocalizedFormattingPreservesTemplateArguments()
        {
            Assert.That(
                BlenderSyncLocalization.FormatForLanguage(
                    "Showing latest {0} of {1}.",
                    BlenderSyncLanguage.SimplifiedChinese,
                    5,
                    12),
                Is.EqualTo("显示最近 5 条，共 12 条。"));
        }

        [Test]
        public void EditorGuiUsesTheSelectedLanguageWithoutVersionOverrides()
        {
            Assert.That(
                BlenderSyncLocalization.ResolveLanguage(
                    BlenderSyncLanguage.SimplifiedChinese,
                    SystemLanguage.English),
                Is.EqualTo(BlenderSyncLanguage.SimplifiedChinese));
            Assert.That(
                BlenderSyncLocalization.ResolveLanguage(
                    BlenderSyncLanguage.System,
                    SystemLanguage.ChineseSimplified),
                Is.EqualTo(BlenderSyncLanguage.SimplifiedChinese));
        }

        [Test]
        public void TrUsesTheEditorGuiCompatibilityLanguage()
        {
            var previous = BlenderSyncLocalization.Language;
            try
            {
                BlenderSyncLocalization.Language = BlenderSyncLanguage.SimplifiedChinese;
                var expectedLanguage = BlenderSyncLocalization.ResolveLanguage(
                    BlenderSyncLanguage.SimplifiedChinese,
                    Application.systemLanguage);

                Assert.That(
                    BlenderSyncLocalization.Tr("Language"),
                    Is.EqualTo(BlenderSyncLocalization.TranslateForLanguage("Language", expectedLanguage)));
            }
            finally
            {
                BlenderSyncLocalization.Language = previous;
            }
        }
    }
}
