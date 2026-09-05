using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Digitalbuilder GR")]
[assembly: AssemblyDescription("Digitalbuilder GR portable launcher")]
[assembly: AssemblyCompany("Digitalbuilder GR")]
[assembly: AssemblyProduct("Digitalbuilder GR")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace DigitalbuilderGrLauncher
{
    internal static class Launcher
    {
        private const int MaxLogBytes = 256 * 1024;
        private const int MaxCapture = 16000;

        [STAThread]
        private static int Main(string[] args)
        {
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string runtime = Path.Combine(root, "runtime", "python.exe");
            string script = Path.Combine(root, "launcher.py");
            string data = Path.Combine(root, "data");
            string log = Path.Combine(data, "launcher.log");
            try
            {
                if (!File.Exists(runtime) || !File.Exists(script))
                {
                    Error("配布版のruntime\\python.exeまたはlauncher.pyが見つかりません。\n配布フォルダー全体を展開してから起動してください。");
                    return 1;
                }
                Directory.CreateDirectory(data);
                ProcessStartInfo start = new ProcessStartInfo {
                    FileName = runtime, WorkingDirectory = root, UseShellExecute = false,
                    CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
                };
                start.Arguments = "-E -s -B -X utf8 " + Quote(script);
                foreach (string arg in args) start.Arguments += " " + Quote(arg);
                Remove(start, "PYTHONHOME"); Remove(start, "PYTHONPATH"); Remove(start, "PYTHONSAFEPATH"); Remove(start, "VIRTUAL_ENV");
                start.EnvironmentVariables["DIGITALBUILDER_INSTALL_ROOT"] = root;
                start.EnvironmentVariables["DIGITALBUILDER_DATA_DIR"] = data;
                start.EnvironmentVariables["PLAYWRIGHT_BROWSERS_PATH"] = Path.Combine(root, "runtime", "browsers");
                start.EnvironmentVariables["TCL_LIBRARY"] = Path.Combine(root, "runtime", "tcl", "tcl8.6");
                start.EnvironmentVariables["TK_LIBRARY"] = Path.Combine(root, "runtime", "tcl", "tk8.6");
                start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
                start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
                using (Process process = new Process())
                {
                    process.StartInfo = start;
                    StringBuilder output = new StringBuilder(), error = new StringBuilder();
                    process.OutputDataReceived += delegate(object s, DataReceivedEventArgs e) { Append(output, e.Data); };
                    process.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e) { Append(error, e.Data); };
                    if (!process.Start()) { Error("アプリを起動できませんでした。"); return 1; }
                    process.BeginOutputReadLine(); process.BeginErrorReadLine(); process.WaitForExit(); process.WaitForExit();
                    int code = process.ExitCode;
                    if (code != 0) { WriteLog(log, "終了コード " + code + "\r\n標準出力:\r\n" + output + "\r\n標準エラー:\r\n" + error); Error("アプリが正常に起動または終了できませんでした。詳細はdata\\launcher.logを確認してください。"); }
                    return code;
                }
            }
            catch (Exception ex) { WriteLog(log, ex.GetType().FullName + ": " + ex.Message); Error("配布版を起動できませんでした。詳細はdata\\launcher.logを確認してください。"); return 1; }
        }

        private static void Remove(ProcessStartInfo s, string name) { if (s.EnvironmentVariables.ContainsKey(name)) s.EnvironmentVariables.Remove(name); }
        private static void Append(StringBuilder b, string value) { if (String.IsNullOrEmpty(value) || b.Length >= MaxCapture) return; int n = Math.Min(value.Length, MaxCapture - b.Length); b.Append(value.Substring(0, n)); b.Append("\r\n"); }

        private static string Quote(string value)
        {
            if (value == null || value.Length == 0) return "\"\"";
            StringBuilder b = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { b.Append(new string('\\', slashes * 2 + 1)); b.Append('"'); slashes = 0; continue; }
                b.Append(new string('\\', slashes)); b.Append(c); slashes = 0;
            }
            b.Append(new string('\\', slashes * 2)); b.Append('"'); return b.ToString();
        }

        private static void WriteLog(string path, string text)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                if (File.Exists(path) && new FileInfo(path).Length >= MaxLogBytes) File.WriteAllText(path, "旧ログを切り詰めました。\r\n", Encoding.UTF8);
                using (StreamWriter w = new StreamWriter(path, true, new UTF8Encoding(false))) w.WriteLine(DateTime.UtcNow.ToString("o") + " " + text);
            }
            catch { }
        }
        private static void Error(string text) { try { MessageBox.Show(text, "Digitalbuilder GR", MessageBoxButtons.OK, MessageBoxIcon.Error); } catch { } }
    }
}
