using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading;
using BlenderSyncVNext.Diagnostics;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace BlenderSyncVNext.Tests
{
    public sealed class BlenderSyncLogTests
    {
        private MethodInfo _setVerboseOverride;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _setVerboseOverride = typeof(BlenderSyncLog).GetMethod(
                "SetVerboseOverride",
                BindingFlags.Static | BindingFlags.NonPublic);
            Assert.That(_setVerboseOverride, Is.Not.Null);
        }

        [SetUp]
        public void SetUp()
        {
            BlenderSyncLog.Clear();
            SetVerbose(false);
        }

        [TearDown]
        public void TearDown()
        {
            BlenderSyncLog.Clear();
            _setVerboseOverride.Invoke(null, new object[] { null });
        }

        [Test]
        public void DefaultModeBuffersInfoAndWarnButDoesNotBuildTrace()
        {
            var traceFactoryCalls = 0;

            Assert.That(BlenderSyncLog.Info("Session", "connected", "ready"), Is.True);
            Assert.That(BlenderSyncLog.Warn("Mesh", "degraded", "uv skipped"), Is.True);
            Assert.That(
                BlenderSyncLog.Trace("Preview", "profile", () =>
                {
                    traceFactoryCalls += 1;
                    return "expensive";
                }),
                Is.False);

            Assert.That(traceFactoryCalls, Is.EqualTo(0));
            Assert.That(BlenderSyncLog.Recent.Count, Is.EqualTo(2));
            Assert.That(BlenderSyncLog.Recent[0].level, Is.EqualTo("INFO"));
            Assert.That(BlenderSyncLog.Recent[1].level, Is.EqualTo("WARN"));
        }

        [Test]
        public void ErrorAlwaysWritesConsoleAndBuffer()
        {
            LogAssert.Expect(
                LogType.Error,
                new Regex(@"\[BlenderSync\]\[ERROR\]\[Session\] failed connection lost"));

            BlenderSyncLog.Error("Session", "failed", "connection lost");

            Assert.That(BlenderSyncLog.Recent.Count, Is.EqualTo(1));
            Assert.That(BlenderSyncLog.Recent[0].level, Is.EqualTo("ERROR"));
        }

        [Test]
        public void VerboseModeWritesLazyTraceToConsoleAndBuffer()
        {
            SetVerbose(true);
            LogAssert.Expect(
                LogType.Log,
                new Regex(@"\[BlenderSync\]\[TRACE\]\[Preview\] profile totalMs=4\.2"));

            Assert.That(
                BlenderSyncLog.Trace("Preview", "profile", () => "totalMs=4.2"),
                Is.True);

            Assert.That(BlenderSyncLog.Recent.Count, Is.EqualTo(1));
            Assert.That(BlenderSyncLog.Recent[0].level, Is.EqualTo("TRACE"));
        }

        [Test]
        public void RingBufferAndFieldsAreBounded()
        {
            for (var index = 0; index < BlenderSyncLog.MaxEntries + 5; index++)
            {
                BlenderSyncLog.Info(
                    "Loop",
                    "tick",
                    fields: new Dictionary<string, object> { { "index", index } });
            }

            Assert.That(BlenderSyncLog.Recent.Count, Is.EqualTo(BlenderSyncLog.MaxEntries));
            Assert.That(BlenderSyncLog.Recent[0].fields["index"], Is.EqualTo("5"));

            BlenderSyncLog.Clear();
            BlenderSyncLog.Info(
                "Import",
                "bounded",
                new string('x', BlenderSyncLog.MaxSummaryLength + 100),
                new Dictionary<string, object>
                {
                    { "path", "C:\\Users\\alice\\asset.fbx" },
                    { "payload", "secret" },
                    { "nested", new object() },
                    { "long", new string('y', BlenderSyncLog.MaxFieldValueLength + 100) },
                });

            var entry = BlenderSyncLog.Recent[0];
            Assert.That(entry.summary.Length, Is.LessThanOrEqualTo(BlenderSyncLog.MaxSummaryLength));
            Assert.That(entry.fields.Keys, Is.EquivalentTo(new[] { "path", "long" }));
            Assert.That(entry.fields["long"].Length, Is.LessThanOrEqualTo(BlenderSyncLog.MaxFieldValueLength));
        }

        [Test]
        public void BackgroundThreadCanWriteWithoutEditorApiAccess()
        {
            Exception failure = null;
            var thread = new Thread(() =>
            {
                try
                {
                    BlenderSyncLog.Info("Transport", "received", fields: new Dictionary<string, object> { { "bytes", 12 } });
                }
                catch (Exception ex)
                {
                    failure = ex;
                }
            });
            thread.Start();
            Assert.That(thread.Join(TimeSpan.FromSeconds(5)), Is.True);

            Assert.That(failure, Is.Null);
            Assert.That(BlenderSyncLog.Recent.Count, Is.EqualTo(1));
            Assert.That(BlenderSyncLog.Recent[0].fields["bytes"], Is.EqualTo("12"));
        }

        [Test]
        public void ConcurrentWritesPreserveBufferSequenceOrder()
        {
            const int threadCount = 8;
            const int entriesPerThread = 20;
            var threads = Enumerable.Range(0, threadCount)
                .Select(threadIndex => new Thread(() =>
                {
                    for (var index = 0; index < entriesPerThread; index++)
                        BlenderSyncLog.Info("Worker", $"{threadIndex}:{index}");
                }))
                .ToArray();

            foreach (var thread in threads)
                thread.Start();
            foreach (var thread in threads)
                Assert.That(thread.Join(TimeSpan.FromSeconds(5)), Is.True);

            var sequences = BlenderSyncLog.Recent.Select(entry => entry.sequence).ToArray();
            Assert.That(sequences.Length, Is.EqualTo(threadCount * entriesPerThread));
            Assert.That(sequences, Is.Ordered);
            Assert.That(sequences.Distinct().Count(), Is.EqualTo(sequences.Length));
        }

        [Test]
        public void ExceptionKeepsStackAndExceptionType()
        {
            var exception = new InvalidOperationException("boom");
            LogAssert.Expect(
                LogType.Error,
                new Regex(@"\[BlenderSync\]\[ERROR\]\[Controller\] tick_failed boom.*InvalidOperationException", RegexOptions.Singleline));

            BlenderSyncLog.Exception("Controller", "tick_failed", exception);

            Assert.That(BlenderSyncLog.Recent[0].fields["exceptionType"], Is.EqualTo("InvalidOperationException"));
        }

        private void SetVerbose(bool enabled)
        {
            _setVerboseOverride.Invoke(null, new object[] { (bool?)enabled });
        }
    }
}
