// Compile together with tools/windows/Setup.cs and a fixture InstallerConfig.cs:
// csc /langversion:5 /main:DigitalbuilderGR.WindowsSetup.Tests.WindowsSetupTests ...
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Reflection;

namespace DigitalbuilderGR.WindowsSetup.Tests
{
    internal static class WindowsSetupTests
    {
        private static int failures;

        private static int Main()
        {
            Run("quiet arguments require absolute isolated paths", TestQuietArguments);
            Run("Windows path components reject traversal, ADS and reserved names", TestUnsafeComponents);
            Run("workspace cleanup identity cannot escape its parent", TestWorkspaceIdentity);
            Run("repeat setup rejects occupied destination without changing DLL or ledger", TestExistingDestination);
            Run("valid rooted ZIP extracts required portable files", TestValidArchive);
            Run("ZIP rejects traversal, duplicate-case paths, wrong roots and links", TestInvalidArchives);
            Console.WriteLine(failures == 0 ? "windows_setup_tests: ok" : "windows_setup_tests: failed=" + failures);
            return failures == 0 ? 0 : 1;
        }

        private static void Run(string name, Action test)
        {
            try
            {
                test();
                Console.WriteLine("PASS " + name);
            }
            catch (Exception exception)
            {
                failures++;
                Console.WriteLine("FAIL " + name + ": " + exception.Message);
            }
        }

        private static void TestQuietArguments()
        {
            SetupOptions options;
            string error;
            Assert(!SetupOptions.TryParse(new[] { "/quiet" }, out options, out error), "missing paths accepted");
            Assert(!SetupOptions.TryParse(new[] { "/quiet", "/dir:relative", "/shortcuts:relative" },
                out options, out error), "relative paths accepted");
            Assert(SetupOptions.TryParse(new[] { "/quiet", "/dir:C:\\fixture\\app", "/shortcuts:C:\\fixture\\links" },
                out options, out error), "absolute fixture paths rejected");
        }

        private static void TestUnsafeComponents()
        {
            string[] invalid = { ".", "..", "CON", "con.txt", "LPT9.log", "name:stream", "trail.", "trail " };
            foreach (string value in invalid)
                Assert(!PathSafety.IsSafeSingleComponent(value), "unsafe component accepted: " + value);
            Assert(PathSafety.IsSafeSingleComponent("Digitalbuilder-GR_1.0.4"), "safe component rejected");
        }

        private static void TestWorkspaceIdentity()
        {
            string parent = Path.Combine(Path.GetTempPath(), "dbgr-parent");
            string valid = Path.Combine(parent, ".Digitalbuilder-setup-0123456789abcdef");
            PathSafety.ValidateWorkspace(valid, parent, ".Digitalbuilder-setup-");
            ExpectSetupException(delegate
            {
                PathSafety.ValidateWorkspace(Path.Combine(Path.GetTempPath(), "outside"), parent,
                    ".Digitalbuilder-setup-");
            });
        }

        private static void TestExistingDestination()
        {
            WithTemporaryDirectory(delegate(string temporary)
            {
                string destination = Path.Combine(temporary, "app");
                string runtime = Path.Combine(destination, "runtime");
                string data = Path.Combine(destination, "data");
                Directory.CreateDirectory(runtime);
                Directory.CreateDirectory(data);
                string dll = Path.Combine(runtime, "msvcp140.dll");
                string ledger = Path.Combine(data, "ledger.sqlite3");
                File.WriteAllText(dll, "existing runtime must remain unchanged");
                File.WriteAllText(ledger, "existing ledger must remain unchanged");
                MethodInfo method = typeof(InstallerEngine).GetMethod("ValidateEmptyDestination",
                    BindingFlags.NonPublic | BindingFlags.Static);
                ExpectSetupException(delegate
                {
                    try { method.Invoke(null, new object[] { destination }); }
                    catch (TargetInvocationException exception)
                    {
                        if (exception.InnerException != null) throw exception.InnerException;
                        throw;
                    }
                });
                Assert(File.ReadAllText(dll) == "existing runtime must remain unchanged", "existing DLL changed");
                Assert(File.ReadAllText(ledger) == "existing ledger must remain unchanged", "existing ledger changed");
                Assert(Directory.GetFiles(destination, "*", SearchOption.AllDirectories).Length == 2,
                    "repeat setup created duplicate files");
            });
        }

        private static void TestValidArchive()
        {
            WithTemporaryDirectory(delegate(string temporary)
            {
                string archive = Path.Combine(temporary, "valid.zip");
                CreateArchive(archive, new Dictionary<string, EntryValue>
                {
                    { global::InstallerConfig.ArchiveRoot + "/Digitalbuilder GR.exe", new EntryValue(new byte[] { 1 }, 0) },
                    { global::InstallerConfig.ArchiveRoot + "/launcher.py", new EntryValue(new byte[] { 2 }, 0) },
                    { global::InstallerConfig.ArchiveRoot + "/runtime/python.exe", new EntryValue(new byte[] { 3 }, 0) }
                });
                string extracted = InvokeExtract(archive, Path.Combine(temporary, "extract"));
                Assert(File.Exists(Path.Combine(extracted, "runtime", "python.exe")), "required file missing after extract");
                Assert(!Directory.Exists(Path.Combine(extracted, global::InstallerConfig.ArchiveRoot)),
                    "signed archive root was not stripped from the local extraction path");
            });
        }

        private static void TestInvalidArchives()
        {
            WithTemporaryDirectory(delegate(string temporary)
            {
                List<IDictionary<string, EntryValue>> cases = new List<IDictionary<string, EntryValue>>();
                cases.Add(RequiredWith(global::InstallerConfig.ArchiveRoot + "/../escape.txt", new EntryValue(new byte[] { 1 }, 0)));
                cases.Add(RequiredWith("/absolute.txt", new EntryValue(new byte[] { 1 }, 0)));
                cases.Add(RequiredWith("C:/drive.txt", new EntryValue(new byte[] { 1 }, 0)));
                cases.Add(RequiredWith(global::InstallerConfig.ArchiveRoot + "/name:stream", new EntryValue(new byte[] { 1 }, 0)));
                cases.Add(RequiredWith(global::InstallerConfig.ArchiveRoot + "/CON.txt", new EntryValue(new byte[] { 1 }, 0)));
                cases.Add(RequiredWith(global::InstallerConfig.ArchiveRoot + "/Launcher.py", new EntryValue(new byte[] { 1 }, 0)));
                cases.Add(RequiredWith("OtherRoot/file.txt", new EntryValue(new byte[] { 1 }, 0)));
                cases.Add(RequiredWith(global::InstallerConfig.ArchiveRoot + "/link", new EntryValue(new byte[] { 1 }, 0xA000 << 16)));
                for (int index = 0; index < cases.Count; index++)
                {
                    string archive = Path.Combine(temporary, "invalid-" + index + ".zip");
                    CreateArchive(archive, cases[index]);
                    int captured = index;
                    ExpectSetupException(delegate
                    {
                        InvokeExtract(archive, Path.Combine(temporary, "extract-" + captured));
                    });
                }
            });
        }

        private static Dictionary<string, EntryValue> RequiredWith(string name, EntryValue value)
        {
            Dictionary<string, EntryValue> entries = new Dictionary<string, EntryValue>
            {
                { global::InstallerConfig.ArchiveRoot + "/Digitalbuilder GR.exe", new EntryValue(new byte[] { 1 }, 0) },
                { global::InstallerConfig.ArchiveRoot + "/launcher.py", new EntryValue(new byte[] { 2 }, 0) },
                { global::InstallerConfig.ArchiveRoot + "/runtime/python.exe", new EntryValue(new byte[] { 3 }, 0) }
            };
            entries.Add(name, value);
            return entries;
        }

        private static void CreateArchive(string path, IDictionary<string, EntryValue> entries)
        {
            using (FileStream stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write))
            using (ZipArchive archive = new ZipArchive(stream, ZipArchiveMode.Create, false))
            {
                foreach (KeyValuePair<string, EntryValue> pair in entries)
                {
                    ZipArchiveEntry entry = archive.CreateEntry(pair.Key, CompressionLevel.Fastest);
                    entry.ExternalAttributes = pair.Value.ExternalAttributes;
                    using (Stream output = entry.Open())
                        output.Write(pair.Value.Content, 0, pair.Value.Content.Length);
                }
            }
        }

        private static string InvokeExtract(string archive, string destination)
        {
            MethodInfo method = typeof(InstallerEngine).GetMethod("ExtractArchive", BindingFlags.NonPublic | BindingFlags.Static);
            try
            {
                return (string)method.Invoke(null, new object[]
                {
                    archive, destination, new Action<InstallProgress>(delegate(InstallProgress unused) { }),
                    new Func<bool>(delegate { return false; })
                });
            }
            catch (TargetInvocationException exception)
            {
                if (exception.InnerException != null)
                    throw exception.InnerException;
                throw;
            }
        }

        private static void ExpectSetupException(Action action)
        {
            try
            {
                action();
            }
            catch (SetupException)
            {
                return;
            }
            throw new Exception("SetupException was not raised");
        }

        private static void WithTemporaryDirectory(Action<string> action)
        {
            string path = Path.Combine(Path.GetTempPath(), "dbgr-setup-test-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(path);
            try { action(path); }
            finally { if (Directory.Exists(path)) Directory.Delete(path, true); }
        }

        private static void Assert(bool condition, string message)
        {
            if (!condition) throw new Exception(message);
        }

        private sealed class EntryValue
        {
            internal readonly byte[] Content;
            internal readonly int ExternalAttributes;

            internal EntryValue(byte[] content, int externalAttributes)
            {
                Content = content;
                ExternalAttributes = externalAttributes;
            }
        }
    }
}
