// Digitalbuilder GR bootstrap installer. Compiles with .NET Framework 4.8 / C# 5.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace DigitalbuilderGR.WindowsSetup
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            SetupOptions options;
            string argumentError;
            if (!SetupOptions.TryParse(args, out options, out argumentError))
            {
                SetupResult invalid = SetupResult.Error(argumentError, null);
                if (ContainsQuiet(args))
                    QuietOutput.Write(invalid);
                else
                    MessageBox.Show(argumentError, "Digitalbuilder GR セットアップ", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return invalid.ExitCode;
            }

            if (options.Quiet)
            {
                SetupResult result;
                try
                {
                    InstallerEngine engine = new InstallerEngine();
                    result = engine.Install(options, delegate(InstallProgress unused) { }, delegate { return false; });
                }
                catch (InstallCancelledException)
                {
                    result = SetupResult.Cancelled("セットアップをキャンセルしました。");
                }
                catch (Exception ex)
                {
                    result = SetupResult.Error(SafeError(ex), options.InstallDirectory);
                }
                QuietOutput.Write(result);
                return result.ExitCode;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (SetupForm form = new SetupForm(options))
                Application.Run(form);
            return 0;
        }

        private static bool ContainsQuiet(string[] args)
        {
            foreach (string value in args)
                if (string.Equals(value, "/quiet", StringComparison.OrdinalIgnoreCase))
                    return true;
            return false;
        }

        internal static string SafeError(Exception exception)
        {
            if (exception is SetupException || exception is InstallCancelledException)
                return exception.Message;
            if (exception is UnauthorizedAccessException)
                return "保存先へ書き込めません。別のフォルダーを選んでください。";
            if (exception is IOException)
                return "ファイル処理に失敗しました。保存先の空き容量と使用状況を確認してください。";
            if (exception is WebException)
                return "配布サイトからセットアップファイルを取得できません。";
            return "セットアップ中に予期しないエラーが発生しました。";
        }
    }

    internal sealed class SetupOptions
    {
        internal bool Quiet;
        internal string InstallDirectory;
        internal string ShortcutDirectory;
        internal bool DesktopShortcut;

        internal static SetupOptions GuiDefaults()
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return new SetupOptions
            {
                Quiet = false,
                InstallDirectory = Path.Combine(local, "Programs", "Digitalbuilder GR"),
                ShortcutDirectory = null,
                DesktopShortcut = true
            };
        }

        internal static bool TryParse(string[] args, out SetupOptions options, out string error)
        {
            options = GuiDefaults();
            error = null;
            bool quiet = false;
            string directory = null;
            string shortcuts = null;
            foreach (string argument in args)
            {
                if (string.Equals(argument, "/quiet", StringComparison.OrdinalIgnoreCase))
                    quiet = true;
                else if (argument.StartsWith("/dir:", StringComparison.OrdinalIgnoreCase))
                    directory = argument.Substring(5);
                else if (argument.StartsWith("/shortcuts:", StringComparison.OrdinalIgnoreCase))
                    shortcuts = argument.Substring(11);
                else
                {
                    error = "未対応の引数です: " + argument;
                    return false;
                }
            }
            if (!quiet && (directory != null || shortcuts != null))
            {
                error = "/dir と /shortcuts は /quiet と一緒に指定してください。";
                return false;
            }
            if (quiet)
            {
                if (string.IsNullOrWhiteSpace(directory) || string.IsNullOrWhiteSpace(shortcuts))
                {
                    error = "/quiet では /dir:<絶対パス> と /shortcuts:<絶対パス> が必要です。";
                    return false;
                }
                if (!Path.IsPathRooted(directory) || !Path.IsPathRooted(shortcuts))
                {
                    error = "/dir と /shortcuts には絶対パスを指定してください。";
                    return false;
                }
                options.Quiet = true;
                options.InstallDirectory = directory;
                options.ShortcutDirectory = shortcuts;
                options.DesktopShortcut = false;
            }
            return true;
        }
    }

    internal sealed class SetupResult
    {
        internal string Status;
        internal int ExitCode;
        internal string Message;
        internal string InstallDirectory;
        internal string Version;
        internal int Sequence;

        internal static SetupResult Success(string message, string directory)
        {
            return new SetupResult
            {
                Status = "success", ExitCode = 0, Message = message, InstallDirectory = directory,
                Version = global::InstallerConfig.Version, Sequence = global::InstallerConfig.Sequence
            };
        }

        internal static SetupResult Cancelled(string message)
        {
            return new SetupResult
            {
                Status = "cancelled", ExitCode = 2, Message = message,
                Version = global::InstallerConfig.Version, Sequence = global::InstallerConfig.Sequence
            };
        }

        internal static SetupResult Error(string message, string directory)
        {
            return new SetupResult
            {
                Status = "error", ExitCode = 1, Message = message, InstallDirectory = directory,
                Version = global::InstallerConfig.Version, Sequence = global::InstallerConfig.Sequence
            };
        }
    }

    internal sealed class InstallProgress
    {
        internal readonly int Percent;
        internal readonly string Message;

        internal InstallProgress(int percent, string message)
        {
            Percent = Math.Max(0, Math.Min(100, percent));
            Message = message;
        }
    }

    internal sealed class SetupException : Exception
    {
        internal SetupException(string message) : base(message) { }
        internal SetupException(string message, Exception inner) : base(message, inner) { }
    }

    internal sealed class InstallCancelledException : Exception
    {
        internal InstallCancelledException() : base("セットアップをキャンセルしました。") { }
    }

    internal sealed class InstallerEngine
    {
        private const long MaximumArchiveBytes = 2L * 1024L * 1024L * 1024L;
        private const long MaximumExpandedBytes = 2L * 1024L * 1024L * 1024L;
        private const int MaximumEntries = 10000;
        private const int DownloadTimeoutMilliseconds = 30000;
        private const int MaximumDownloadMinutes = 15;
        private const int InitializeTimeoutSeconds = 180;
        private const string SetupPrefix = ".Digitalbuilder-setup-";
        private const string ProductExeName = "Digitalbuilder GR.exe";
        private const string FixedOrigin = "https://digitalbuilder-gr-updates.rinntyu2000.chatgpt.site";

        internal SetupResult Install(SetupOptions options, Action<InstallProgress> report, Func<bool> cancelled)
        {
            ValidatePinnedConfiguration();
            string destination = PathSafety.CanonicalAbsoluteDirectory(options.InstallDirectory, "保存先");
            string shortcutOverride = options.ShortcutDirectory == null
                ? null : PathSafety.CanonicalAbsoluteDirectory(options.ShortcutDirectory, "ショートカット保存先");
            string parent = Path.GetDirectoryName(destination);
            if (string.IsNullOrEmpty(parent))
                throw new SetupException("保存先の親フォルダーを確認できません。");
            Directory.CreateDirectory(parent);
            PathSafety.RejectReparseAncestors(parent);
            ValidateEmptyDestination(destination);

            IList<string> shortcutPaths = PlannedShortcuts(destination, shortcutOverride, options.DesktopShortcut);
            foreach (string shortcutPath in shortcutPaths)
                ShortcutWriter.ValidateExistingTarget(shortcutPath, Path.Combine(destination, ProductExeName));

            string workspace = Path.Combine(parent, SetupPrefix + Guid.NewGuid().ToString("N"));
            PathSafety.ValidateWorkspace(workspace, parent, SetupPrefix);
            Directory.CreateDirectory(workspace);
            string archivePath = Path.Combine(workspace, "package.zip");
            // The signed ZIP has one fixed top-level folder.  Validate that
            // name, then strip it locally to keep .NET Framework paths short.
            string extractDirectory = Path.Combine(workspace, "p");
            bool moved = false;
            string cleanupWarning = null;
            string shortcutWarning = null;
            try
            {
                ThrowIfCancelled(cancelled);
                report(new InstallProgress(2, "配布ファイルをダウンロードしています…"));
                DownloadArchive(archivePath, report, cancelled);
                ThrowIfCancelled(cancelled);
                report(new InstallProgress(58, "配布ファイルを検証して展開しています…"));
                string payloadRoot = ExtractArchive(archivePath, extractDirectory, report, cancelled);
                ValidatePortableLayout(payloadRoot);
                PathSafety.RejectReparseTree(payloadRoot);

                ThrowIfCancelled(cancelled);
                report(new InstallProgress(82, "初回起動の準備をしています…"));
                InitializePortableApplication(payloadRoot, cancelled);
                PathSafety.RejectReparseTree(payloadRoot);

                ThrowIfCancelled(cancelled);
                ValidateEmptyDestination(destination);
                if (Directory.Exists(destination))
                    Directory.Delete(destination, false);
                Directory.Move(payloadRoot, destination);
                moved = true;

                report(new InstallProgress(94, "ショートカットを作成しています…"));
                foreach (string shortcutPath in shortcutPaths)
                {
                    try
                    {
                        ShortcutWriter.Create(shortcutPath, Path.Combine(destination, ProductExeName), destination);
                    }
                    catch
                    {
                        shortcutWarning = "一部のショートカットを作成できませんでした。";
                    }
                }
                report(new InstallProgress(100, "セットアップが完了しました。"));
            }
            finally
            {
                try
                {
                    PathSafety.SafeDeleteWorkspace(workspace, parent, SetupPrefix);
                }
                catch (Exception)
                {
                    cleanupWarning = "一時ファイルを完全に削除できませんでした。";
                }
            }

            if (!moved)
                throw new SetupException("アプリを保存先へ移設できませんでした。");
            string message = "Digitalbuilder GR をインストールしました。";
            if (shortcutWarning != null) message += shortcutWarning;
            if (cleanupWarning != null) message += cleanupWarning;
            return SetupResult.Success(message, destination);
        }

        private static void ValidatePinnedConfiguration()
        {
            Uri uri;
            if (!Uri.TryCreate(global::InstallerConfig.ArchiveUrl, UriKind.Absolute, out uri))
                throw new SetupException("配布URL設定が不正です。");
            Uri fixedOrigin = new Uri(FixedOrigin, UriKind.Absolute);
            string expectedPath = "/api/installers/" + global::InstallerConfig.Sequence.ToString(CultureInfo.InvariantCulture) + "/download";
            if (!string.Equals(uri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase) ||
                !string.Equals(uri.Host, fixedOrigin.Host, StringComparison.OrdinalIgnoreCase) ||
                uri.Port != fixedOrigin.Port || !string.IsNullOrEmpty(uri.UserInfo) ||
                !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment) ||
                !string.Equals(uri.AbsolutePath, expectedPath, StringComparison.Ordinal))
                throw new SetupException("配布URLが固定のHTTPS配布先と一致しません。");
            if (global::InstallerConfig.Sequence <= 0 || string.IsNullOrWhiteSpace(global::InstallerConfig.Version))
                throw new SetupException("配布版設定が不正です。");
            if (global::InstallerConfig.ArchiveSize <= 0 || global::InstallerConfig.ArchiveSize > MaximumArchiveBytes)
                throw new SetupException("配布ファイルのサイズ設定が不正です。");
            if (!IsSha256Hex(global::InstallerConfig.ArchiveSha256))
                throw new SetupException("配布ファイルのハッシュ設定が不正です。");
            if (!PathSafety.IsSafeSingleComponent(global::InstallerConfig.ArchiveRoot))
                throw new SetupException("配布ZIPのルート設定が不正です。");
            DateTimeOffset expires;
            if (!DateTimeOffset.TryParse(global::InstallerConfig.ExpiresAt, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out expires) ||
                DateTimeOffset.UtcNow > expires)
                throw new SetupException("このセットアップの有効期限が切れています。最新版を取得してください。");
        }

        private static bool IsSha256Hex(string value)
        {
            if (value == null || value.Length != 64)
                return false;
            foreach (char character in value)
                if (!((character >= '0' && character <= '9') ||
                    (character >= 'a' && character <= 'f') || (character >= 'A' && character <= 'F')))
                    return false;
            return true;
        }

        private static void ValidateEmptyDestination(string destination)
        {
            if (File.Exists(destination))
                throw new SetupException("保存先と同名のファイルが存在します。");
            if (!Directory.Exists(destination))
                return;
            FileAttributes attributes = File.GetAttributes(destination);
            if ((attributes & FileAttributes.ReparsePoint) != 0)
                throw new SetupException("リンクまたはジャンクションの保存先は使用できません。");
            using (IEnumerator<string> entries = Directory.EnumerateFileSystemEntries(destination).GetEnumerator())
                if (entries.MoveNext())
                    throw new SetupException("保存先が空ではありません。既存利用者はアプリ内更新を使用してください。");
        }

        private static IList<string> PlannedShortcuts(string destination, string shortcutOverride, bool desktop)
        {
            List<string> paths = new List<string>();
            if (shortcutOverride != null)
            {
                if (PathSafety.IsWithin(destination, shortcutOverride) || PathSafety.IsWithin(shortcutOverride, destination))
                    throw new SetupException("ショートカット保存先はアプリ保存先の外を指定してください。");
                paths.Add(Path.Combine(shortcutOverride, "Digitalbuilder GR.lnk"));
                return paths;
            }
            string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
            paths.Add(Path.Combine(programs, "Digitalbuilder GR", "Digitalbuilder GR.lnk"));
            if (desktop)
                paths.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Digitalbuilder GR.lnk"));
            return paths;
        }

        private static void DownloadArchive(string archivePath, Action<InstallProgress> report, Func<bool> cancelled)
        {
            // Keep the platform certificate chain/hostname validation.  Explicit
            // TLS 1.2 avoids legacy .NET Framework defaults on older Windows.
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(global::InstallerConfig.ArchiveUrl);
            request.Method = "GET";
            request.AllowAutoRedirect = false;
            request.AutomaticDecompression = DecompressionMethods.None;
            request.Timeout = DownloadTimeoutMilliseconds;
            request.ReadWriteTimeout = DownloadTimeoutMilliseconds;
            request.UserAgent = "Digitalbuilder-GR-Setup/1";
            Stopwatch elapsed = Stopwatch.StartNew();
            try
            {
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                {
                    if (response.StatusCode != HttpStatusCode.OK ||
                        !string.Equals(response.ResponseUri.AbsoluteUri, global::InstallerConfig.ArchiveUrl, StringComparison.Ordinal))
                        throw new SetupException("配布サイトが固定URL以外へ応答しました。");
                    if (response.ContentLength >= 0 && response.ContentLength != global::InstallerConfig.ArchiveSize)
                        throw new SetupException("配布ファイルの応答サイズが一致しません。");
                    long total = 0;
                    byte[] buffer = new byte[1024 * 128];
                    using (SHA256 sha = SHA256.Create())
                    using (Stream input = response.GetResponseStream())
                    using (FileStream output = new FileStream(archivePath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    {
                        int read;
                        while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
                        {
                            ThrowIfCancelled(cancelled);
                            if (elapsed.Elapsed > TimeSpan.FromMinutes(MaximumDownloadMinutes))
                                throw new SetupException("配布ファイルの取得が制限時間を超えました。");
                            total += read;
                            if (total > global::InstallerConfig.ArchiveSize || total > MaximumArchiveBytes)
                                throw new SetupException("配布ファイルがサイズ上限を超えています。");
                            output.Write(buffer, 0, read);
                            sha.TransformBlock(buffer, 0, read, buffer, 0);
                            int percent = 2 + (int)(Math.Min(1.0, (double)total / global::InstallerConfig.ArchiveSize) * 53.0);
                            report(new InstallProgress(percent, "配布ファイルをダウンロードしています…"));
                        }
                        sha.TransformFinalBlock(new byte[0], 0, 0);
                        output.Flush(true);
                        if (total != global::InstallerConfig.ArchiveSize)
                            throw new SetupException("配布ファイルのサイズが一致しません。");
                        string actual = BytesToHex(sha.Hash);
                        if (!FixedTimeHexEquals(actual, global::InstallerConfig.ArchiveSha256))
                            throw new SetupException("配布ファイルのSHA-256が一致しません。");
                    }
                }
            }
            catch (WebException ex)
            {
                if (ex.Response != null)
                    ex.Response.Close();
                throw new SetupException("配布サイトからセットアップファイルを取得できません。", ex);
            }
        }

        private static string BytesToHex(byte[] value)
        {
            StringBuilder result = new StringBuilder(value.Length * 2);
            foreach (byte item in value)
                result.Append(item.ToString("x2", CultureInfo.InvariantCulture));
            return result.ToString();
        }

        private static bool FixedTimeHexEquals(string left, string right)
        {
            if (left == null || right == null || left.Length != right.Length)
                return false;
            int difference = 0;
            for (int index = 0; index < left.Length; index++)
                difference |= char.ToUpperInvariant(left[index]) ^ char.ToUpperInvariant(right[index]);
            return difference == 0;
        }

        private static string ExtractArchive(string archivePath, string extractDirectory,
            Action<InstallProgress> report, Func<bool> cancelled)
        {
            Directory.CreateDirectory(extractDirectory);
            string extractRoot = Path.GetFullPath(extractDirectory);
            string extractPrefix = PathSafety.WithSeparator(extractRoot);
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            HashSet<string> files = new HashSet<string>(StringComparer.Ordinal);
            long totalExpanded = 0;
            int entryCount = 0;
            using (FileStream stream = new FileStream(archivePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (ZipArchive archive = new ZipArchive(stream, ZipArchiveMode.Read, false))
            {
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    ThrowIfCancelled(cancelled);
                    entryCount++;
                    if (entryCount > MaximumEntries)
                        throw new SetupException("配布ZIPのファイル数が上限を超えています。");
                    string normalized;
                    bool directory;
                    ValidateZipEntry(entry, out normalized, out directory);
                    if (!seen.Add(normalized))
                        throw new SetupException("配布ZIPに大文字小文字だけが異なる重複パスがあります。");
                    if (entry.Length < 0 || entry.Length > MaximumExpandedBytes - totalExpanded)
                        throw new SetupException("配布ZIPの展開サイズが上限を超えています。");
                    totalExpanded += entry.Length;
                    string archiveRoot = global::InstallerConfig.ArchiveRoot;
                    string localRelative = normalized.Length == archiveRoot.Length
                        ? "" : normalized.Substring(archiveRoot.Length + 1);
                    if (localRelative.Length == 0 && !directory)
                        throw new SetupException("配布ZIPのルートはディレクトリである必要があります。");
                    string target = localRelative.Length == 0
                        ? extractRoot
                        : Path.GetFullPath(Path.Combine(extractRoot, localRelative.Replace('/', Path.DirectorySeparatorChar)));
                    if (!string.Equals(target, extractRoot, StringComparison.OrdinalIgnoreCase) &&
                        !target.StartsWith(extractPrefix, StringComparison.OrdinalIgnoreCase))
                        throw new SetupException("配布ZIPに保存先外のパスがあります。");
                    if (target.Length >= 260)
                        throw new SetupException("配布ZIPのパスが長すぎます。");
                    if (directory)
                    {
                        Directory.CreateDirectory(target);
                        continue;
                    }
                    string targetParent = Path.GetDirectoryName(target);
                    Directory.CreateDirectory(targetParent);
                    long written = 0;
                    try
                    {
                        using (Stream input = entry.Open())
                        using (FileStream output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                        {
                            byte[] buffer = new byte[1024 * 128];
                            int read;
                            while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
                            {
                                ThrowIfCancelled(cancelled);
                                written += read;
                                if (written > entry.Length)
                                    throw new SetupException("配布ZIPの展開サイズが宣言値を超えました。");
                                output.Write(buffer, 0, read);
                            }
                        }
                    }
                    catch (InvalidDataException ex)
                    {
                        throw new SetupException("暗号化または破損したZIP項目は展開できません。", ex);
                    }
                    if (written != entry.Length)
                        throw new SetupException("配布ZIPの展開サイズが一致しません。");
                    files.Add(normalized);
                    int percent = 58 + (int)(Math.Min(1.0, (double)totalExpanded / Math.Max(1L, MaximumExpandedBytes)) * 18.0);
                    report(new InstallProgress(percent, "配布ファイルを検証して展開しています…"));
                }
            }
            string root = global::InstallerConfig.ArchiveRoot;
            string[] required =
            {
                root + "/" + ProductExeName,
                root + "/launcher.py",
                root + "/runtime/python.exe"
            };
            foreach (string requiredFile in required)
                if (!files.Contains(requiredFile))
                    throw new SetupException("配布ZIPに必要なファイルがありません: " + requiredFile);
            string payloadRoot = extractRoot;
            if (!Directory.Exists(payloadRoot))
                throw new SetupException("配布ZIPのルートフォルダーがありません。");
            return payloadRoot;
        }

        private static void ValidateZipEntry(ZipArchiveEntry entry, out string normalized, out bool directory)
        {
            string raw = entry.FullName;
            if (string.IsNullOrEmpty(raw) || raw.IndexOf('\0') >= 0 || raw.IndexOf('\\') >= 0 ||
                raw.StartsWith("/", StringComparison.Ordinal) || raw.IndexOf(':') >= 0)
                throw new SetupException("配布ZIPに不正なパスがあります。");
            directory = raw.EndsWith("/", StringComparison.Ordinal);
            normalized = directory ? raw.Substring(0, raw.Length - 1) : raw;
            if (normalized.Length == 0 || normalized.EndsWith("/", StringComparison.Ordinal))
                throw new SetupException("配布ZIPに不正なディレクトリ項目があります。");
            string[] parts = normalized.Split('/');
            if (parts.Length == 0 || !string.Equals(parts[0], global::InstallerConfig.ArchiveRoot, StringComparison.Ordinal))
                throw new SetupException("配布ZIPのルートフォルダーが設定と一致しません。");
            foreach (string part in parts)
                if (!PathSafety.IsSafeSingleComponent(part))
                    throw new SetupException("配布ZIPにWindowsで安全でないパスがあります。");

            int unixType = (entry.ExternalAttributes >> 16) & 0xF000;
            bool unixLink = unixType == 0xA000;
            bool unixSpecial = unixType != 0 && unixType != 0x8000 && unixType != 0x4000;
            bool windowsReparse = (entry.ExternalAttributes & (int)FileAttributes.ReparsePoint) != 0;
            if (unixLink || unixSpecial || windowsReparse)
                throw new SetupException("配布ZIPのリンクまたは特殊ファイルは禁止です。");
            if (directory && entry.Length != 0)
                throw new SetupException("配布ZIPのディレクトリ項目が不正です。");
        }

        private static void ValidatePortableLayout(string payloadRoot)
        {
            string[] required =
            {
                Path.Combine(payloadRoot, ProductExeName),
                Path.Combine(payloadRoot, "launcher.py"),
                Path.Combine(payloadRoot, "runtime", "python.exe")
            };
            foreach (string path in required)
                if (!File.Exists(path))
                    throw new SetupException("同梱版に必要な起動ファイルがありません。");
        }

        private static void InitializePortableApplication(string payloadRoot, Func<bool> cancelled)
        {
            string python = Path.Combine(payloadRoot, "runtime", "python.exe");
            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = python,
                // Validate PDF dependencies before committing the staged installation.
                // In-memory synthetic PDF: no invoice data and no extra packages installed.
                Arguments = "-E -s -B -X utf8 -c \"import pymupdf,runpy,sys; d=pymupdf.open(); d.new_page(); b=d.tobytes(); d.close(); p=pymupdf.open(stream=b,filetype='pdf'); pix=p[0].get_pixmap(); assert pix.width>0 and pix.height>0; p.close(); sys.argv=['launcher.py','--init-db']; runpy.run_path('launcher.py',run_name='__main__')\"",
                WorkingDirectory = payloadRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            start.EnvironmentVariables.Remove("PYTHONHOME");
            start.EnvironmentVariables.Remove("PYTHONPATH");
            start.EnvironmentVariables.Remove("VIRTUAL_ENV");
            start.EnvironmentVariables["DIGITALBUILDER_DATA_DIR"] = Path.Combine(payloadRoot, "data");
            start.EnvironmentVariables["DIGITALBUILDER_INSTALL_ROOT"] = payloadRoot;
            start.EnvironmentVariables["TCL_LIBRARY"] = Path.Combine(payloadRoot, "runtime", "tcl", "tcl8.6");
            start.EnvironmentVariables["TK_LIBRARY"] = Path.Combine(payloadRoot, "runtime", "tcl", "tk8.6");
            start.EnvironmentVariables["PLAYWRIGHT_BROWSERS_PATH"] = Path.Combine(payloadRoot, "runtime", "ms-playwright");

            using (Process process = new Process())
            {
                process.StartInfo = start;
                StringBuilder output = new StringBuilder();
                DataReceivedEventHandler capture = delegate(object sender, DataReceivedEventArgs args)
                {
                    if (args.Data != null && output.Length < 8192)
                        output.AppendLine(args.Data);
                };
                process.OutputDataReceived += capture;
                process.ErrorDataReceived += capture;
                if (!process.Start())
                    throw new SetupException("同梱版の初期化を開始できません。");
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                Stopwatch elapsed = Stopwatch.StartNew();
                while (!process.WaitForExit(200))
                {
                    if (cancelled())
                    {
                        TryKill(process);
                        throw new InstallCancelledException();
                    }
                    if (elapsed.Elapsed > TimeSpan.FromSeconds(InitializeTimeoutSeconds))
                    {
                        TryKill(process);
                        throw new SetupException("同梱版の初期化が制限時間を超えました。");
                    }
                }
                process.WaitForExit();
                if (process.ExitCode != 0)
                    throw new SetupException("同梱版のPDF表示部品または初期化の確認に失敗しました。既存のアプリは変更していません。");
            }
        }

        private static void TryKill(Process process)
        {
            try { if (!process.HasExited) process.Kill(); }
            catch { }
        }

        private static void ThrowIfCancelled(Func<bool> cancelled)
        {
            if (cancelled())
                throw new InstallCancelledException();
        }
    }

    internal static class PathSafety
    {
        private static readonly HashSet<string> Reserved = BuildReservedNames();

        private static HashSet<string> BuildReservedNames()
        {
            HashSet<string> values = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { "CON", "PRN", "AUX", "NUL" };
            for (int index = 1; index <= 9; index++)
            {
                values.Add("COM" + index.ToString(CultureInfo.InvariantCulture));
                values.Add("LPT" + index.ToString(CultureInfo.InvariantCulture));
            }
            return values;
        }

        internal static bool IsSafeSingleComponent(string value)
        {
            if (string.IsNullOrEmpty(value) || value == "." || value == ".." ||
                value.EndsWith(" ", StringComparison.Ordinal) || value.EndsWith(".", StringComparison.Ordinal) ||
                value.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || value.IndexOf(':') >= 0)
                return false;
            string stem = value.Split('.')[0];
            return !Reserved.Contains(stem);
        }

        internal static string CanonicalAbsoluteDirectory(string value, string label)
        {
            if (string.IsNullOrWhiteSpace(value) || !Path.IsPathRooted(value))
                throw new SetupException(label + "には絶対パスを指定してください。");
            string full;
            try { full = Path.GetFullPath(value); }
            catch (Exception ex) { throw new SetupException(label + "のパスが不正です。", ex); }
            string root = Path.GetPathRoot(full);
            if (string.Equals(full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
                throw new SetupException(label + "にドライブのルートは指定できません。");
            return full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        internal static string WithSeparator(string path)
        {
            return path.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        }

        internal static bool IsWithin(string parent, string child)
        {
            string parentFull = WithSeparator(Path.GetFullPath(parent));
            string childFull = WithSeparator(Path.GetFullPath(child));
            return childFull.StartsWith(parentFull, StringComparison.OrdinalIgnoreCase);
        }

        internal static void RejectReparseAncestors(string path)
        {
            DirectoryInfo current = new DirectoryInfo(Path.GetFullPath(path));
            while (current != null)
            {
                if (current.Exists && (current.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new SetupException("リンクまたはジャンクション配下は保存先に使用できません。");
                current = current.Parent;
            }
        }

        internal static void RejectReparseTree(string root)
        {
            DirectoryInfo directory = new DirectoryInfo(root);
            InspectNoReparse(directory);
        }

        private static void InspectNoReparse(DirectoryInfo directory)
        {
            if ((directory.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new SetupException("作業フォルダー内にリンクまたはジャンクションがあります。");
            foreach (FileSystemInfo item in directory.GetFileSystemInfos())
            {
                if ((item.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new SetupException("作業フォルダー内にリンクまたはジャンクションがあります。");
                DirectoryInfo child = item as DirectoryInfo;
                if (child != null)
                    InspectNoReparse(child);
            }
        }

        internal static void ValidateWorkspace(string workspace, string parent, string prefix)
        {
            string fullWorkspace = Path.GetFullPath(workspace);
            string fullParent = Path.GetFullPath(parent);
            if (!string.Equals(Path.GetDirectoryName(fullWorkspace), fullParent, StringComparison.OrdinalIgnoreCase) ||
                !Path.GetFileName(fullWorkspace).StartsWith(prefix, StringComparison.Ordinal) ||
                Path.GetFileName(fullWorkspace).Length <= prefix.Length)
                throw new SetupException("一時作業フォルダーが安全な場所にありません。");
        }

        internal static void SafeDeleteWorkspace(string workspace, string parent, string prefix)
        {
            if (!Directory.Exists(workspace))
                return;
            ValidateWorkspace(workspace, parent, prefix);
            RejectReparseTree(workspace);
            Directory.Delete(workspace, true);
        }
    }

    internal static class ShortcutWriter
    {
        internal static void ValidateExistingTarget(string shortcutPath, string intendedTarget)
        {
            if (!File.Exists(shortcutPath))
                return;
            string existingTarget = ReadTarget(shortcutPath);
            if (!string.Equals(Path.GetFullPath(existingTarget), Path.GetFullPath(intendedTarget), StringComparison.OrdinalIgnoreCase))
                throw new SetupException("別のアプリを指す既存ショートカットは上書きできません: " + shortcutPath);
        }

        internal static void Create(string shortcutPath, string target, string workingDirectory)
        {
            ValidateExistingTarget(shortcutPath, target);
            string parent = Path.GetDirectoryName(shortcutPath);
            Directory.CreateDirectory(parent);
            object shell = null;
            object shortcut = null;
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null)
                    throw new SetupException("Windowsショートカット機能を利用できません。");
                shell = Activator.CreateInstance(shellType);
                shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell,
                    new object[] { shortcutPath }, CultureInfo.InvariantCulture);
                Type shortcutType = shortcut.GetType();
                shortcutType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut,
                    new object[] { target }, CultureInfo.InvariantCulture);
                shortcutType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut,
                    new object[] { workingDirectory }, CultureInfo.InvariantCulture);
                shortcutType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut,
                    new object[] { target + ",0" }, CultureInfo.InvariantCulture);
                shortcutType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, new object[0],
                    CultureInfo.InvariantCulture);
            }
            finally
            {
                if (shortcut != null && Marshal.IsComObject(shortcut)) Marshal.FinalReleaseComObject(shortcut);
                if (shell != null && Marshal.IsComObject(shell)) Marshal.FinalReleaseComObject(shell);
            }
        }

        private static string ReadTarget(string shortcutPath)
        {
            object shell = null;
            object shortcut = null;
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null)
                    throw new SetupException("既存ショートカットを確認できません。");
                shell = Activator.CreateInstance(shellType);
                shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell,
                    new object[] { shortcutPath }, CultureInfo.InvariantCulture);
                object value = shortcut.GetType().InvokeMember("TargetPath", BindingFlags.GetProperty, null, shortcut,
                    new object[0], CultureInfo.InvariantCulture);
                string target = value as string;
                if (string.IsNullOrWhiteSpace(target))
                    throw new SetupException("既存ショートカットの参照先を確認できません。");
                return target;
            }
            finally
            {
                if (shortcut != null && Marshal.IsComObject(shortcut)) Marshal.FinalReleaseComObject(shortcut);
                if (shell != null && Marshal.IsComObject(shell)) Marshal.FinalReleaseComObject(shell);
            }
        }
    }

    internal static class QuietOutput
    {
        internal static void Write(SetupResult result)
        {
            string json = "{" +
                "\"status\":\"" + Escape(result.Status) + "\"," +
                "\"exit_code\":" + result.ExitCode.ToString(CultureInfo.InvariantCulture) + "," +
                "\"version\":\"" + Escape(result.Version) + "\"," +
                "\"sequence\":" + result.Sequence.ToString(CultureInfo.InvariantCulture) + "," +
                "\"install_dir\":" + NullableString(result.InstallDirectory) + "," +
                "\"message\":\"" + Escape(result.Message) + "\"}";
            byte[] bytes = new UTF8Encoding(false).GetBytes(json + Environment.NewLine);
            try
            {
                using (Stream output = Console.OpenStandardOutput())
                {
                    output.Write(bytes, 0, bytes.Length);
                    output.Flush();
                }
            }
            catch { }
        }

        private static string NullableString(string value)
        {
            return value == null ? "null" : "\"" + Escape(value) + "\"";
        }

        private static string Escape(string value)
        {
            if (value == null) return "";
            StringBuilder result = new StringBuilder();
            foreach (char character in value)
            {
                switch (character)
                {
                    case '\\': result.Append("\\\\"); break;
                    case '"': result.Append("\\\""); break;
                    case '\b': result.Append("\\b"); break;
                    case '\f': result.Append("\\f"); break;
                    case '\n': result.Append("\\n"); break;
                    case '\r': result.Append("\\r"); break;
                    case '\t': result.Append("\\t"); break;
                    default:
                        if (character < 0x20)
                            result.Append("\\u" + ((int)character).ToString("x4", CultureInfo.InvariantCulture));
                        else
                            result.Append(character);
                        break;
                }
            }
            return result.ToString();
        }
    }

    internal sealed class SetupForm : Form
    {
        private readonly TextBox directoryBox;
        private readonly Button browseButton;
        private readonly CheckBox desktopShortcut;
        private readonly ProgressBar progressBar;
        private readonly Label statusLabel;
        private readonly Button installButton;
        private readonly Button cancelButton;
        private readonly Button launchButton;
        private readonly BackgroundWorker worker;
        private string installedDirectory;

        internal SetupForm(SetupOptions defaults)
        {
            Text = "Digitalbuilder GR セットアップ";
            ClientSize = new Size(650, 330);
            MinimumSize = new Size(650, 369);
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Yu Gothic UI", 10F, FontStyle.Regular, GraphicsUnit.Point);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;

            Label title = new Label
            {
                Text = "Digitalbuilder GR をセットアップ",
                Font = new Font(Font.FontFamily, 18F, FontStyle.Bold),
                AutoSize = true,
                Location = new Point(28, 24)
            };
            Label explanation = new Label
            {
                Text = "必要な実行環境を含む配布版を検証し、このPCへ安全に配置します。",
                AutoSize = true,
                ForeColor = Color.FromArgb(71, 85, 105),
                Location = new Point(31, 66)
            };
            Label destinationLabel = new Label { Text = "保存先", AutoSize = true, Location = new Point(31, 111) };
            directoryBox = new TextBox { Text = defaults.InstallDirectory, Location = new Point(32, 136), Size = new Size(493, 28) };
            browseButton = new Button { Text = "参照…", Location = new Point(535, 134), Size = new Size(82, 31) };
            browseButton.Click += BrowseClicked;
            desktopShortcut = new CheckBox
            {
                Text = "デスクトップにもショートカットを作成",
                Checked = true,
                AutoSize = true,
                Location = new Point(32, 177)
            };
            progressBar = new ProgressBar { Location = new Point(32, 214), Size = new Size(585, 17) };
            statusLabel = new Label
            {
                Text = "インストールを押すと開始します。",
                AutoEllipsis = true,
                ForeColor = Color.FromArgb(71, 85, 105),
                Location = new Point(32, 240),
                Size = new Size(585, 25)
            };
            installButton = new Button { Text = "インストール", Location = new Point(382, 278), Size = new Size(112, 36) };
            installButton.Click += InstallClicked;
            cancelButton = new Button { Text = "閉じる", Location = new Point(505, 278), Size = new Size(112, 36) };
            cancelButton.Click += CancelClicked;
            launchButton = new Button { Text = "起動", Location = new Point(382, 278), Size = new Size(112, 36), Visible = false };
            launchButton.Click += LaunchClicked;

            Controls.AddRange(new Control[] { title, explanation, destinationLabel, directoryBox, browseButton,
                desktopShortcut, progressBar, statusLabel, installButton, cancelButton, launchButton });
            AcceptButton = installButton;
            CancelButton = cancelButton;

            worker = new BackgroundWorker { WorkerReportsProgress = true, WorkerSupportsCancellation = true };
            worker.DoWork += WorkerDoWork;
            worker.ProgressChanged += WorkerProgressChanged;
            worker.RunWorkerCompleted += WorkerCompleted;
            FormClosing += SetupFormClosing;
        }

        private void BrowseClicked(object sender, EventArgs args)
        {
            using (FolderBrowserDialog dialog = new FolderBrowserDialog())
            {
                dialog.Description = "新しいインストール先フォルダーを選択してください";
                dialog.SelectedPath = directoryBox.Text;
                dialog.ShowNewFolderButton = true;
                if (dialog.ShowDialog(this) == DialogResult.OK)
                    directoryBox.Text = dialog.SelectedPath;
            }
        }

        private void InstallClicked(object sender, EventArgs args)
        {
            SetupOptions options = SetupOptions.GuiDefaults();
            options.InstallDirectory = directoryBox.Text;
            options.DesktopShortcut = desktopShortcut.Checked;
            SetRunning(true);
            statusLabel.Text = "セットアップを開始しています…";
            worker.RunWorkerAsync(options);
        }

        private void CancelClicked(object sender, EventArgs args)
        {
            if (worker.IsBusy)
            {
                worker.CancelAsync();
                cancelButton.Enabled = false;
                statusLabel.Text = "安全に中止しています…";
            }
            else
                Close();
        }

        private void WorkerDoWork(object sender, DoWorkEventArgs args)
        {
            InstallerEngine engine = new InstallerEngine();
            BackgroundWorker current = (BackgroundWorker)sender;
            try
            {
                args.Result = engine.Install((SetupOptions)args.Argument,
                    delegate(InstallProgress progress) { current.ReportProgress(progress.Percent, progress.Message); },
                    delegate { return current.CancellationPending; });
            }
            catch (InstallCancelledException)
            {
                args.Cancel = true;
            }
        }

        private void WorkerProgressChanged(object sender, ProgressChangedEventArgs args)
        {
            progressBar.Value = Math.Max(progressBar.Minimum, Math.Min(progressBar.Maximum, args.ProgressPercentage));
            statusLabel.Text = args.UserState as string ?? "処理しています…";
        }

        private void WorkerCompleted(object sender, RunWorkerCompletedEventArgs args)
        {
            SetRunning(false);
            if (args.Cancelled)
            {
                statusLabel.Text = "セットアップをキャンセルしました。";
                return;
            }
            if (args.Error != null)
            {
                string message = Program.SafeError(args.Error);
                statusLabel.Text = message;
                MessageBox.Show(this, message, "Digitalbuilder GR セットアップ", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            SetupResult result = (SetupResult)args.Result;
            installedDirectory = result.InstallDirectory;
            progressBar.Value = 100;
            statusLabel.Text = result.Message;
            installButton.Visible = false;
            launchButton.Visible = true;
            launchButton.Enabled = true;
            cancelButton.Text = "閉じる";
            AcceptButton = launchButton;
        }

        private void SetRunning(bool running)
        {
            directoryBox.Enabled = !running;
            browseButton.Enabled = !running;
            desktopShortcut.Enabled = !running;
            installButton.Enabled = !running;
            cancelButton.Text = running ? "キャンセル" : "閉じる";
            cancelButton.Enabled = true;
        }

        private void LaunchClicked(object sender, EventArgs args)
        {
            string executable = Path.Combine(installedDirectory, "Digitalbuilder GR.exe");
            try
            {
                Process.Start(new ProcessStartInfo { FileName = executable, WorkingDirectory = installedDirectory, UseShellExecute = true });
                Close();
            }
            catch
            {
                MessageBox.Show(this, "アプリを起動できません。作成したショートカットから起動してください。",
                    "Digitalbuilder GR セットアップ", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void SetupFormClosing(object sender, FormClosingEventArgs args)
        {
            if (!worker.IsBusy)
                return;
            worker.CancelAsync();
            args.Cancel = true;
            cancelButton.Enabled = false;
            statusLabel.Text = "安全に中止しています…";
        }
    }
}
