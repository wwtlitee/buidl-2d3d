// Windowed startup for build something / 造点啥. v0.6.0 / Unclecow.
// Uses Windows' built-in .NET Framework; no console, browser, or global Python.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("build something · 造点啥")]
[assembly: AssemblyDescription("造点啥桌面应用启动器")]
[assembly: AssemblyCompany("Unclecow")]
[assembly: AssemblyProduct("build something · 造点啥")]
[assembly: AssemblyVersion("__FILE_VERSION__")]
[assembly: AssemblyFileVersion("__FILE_VERSION__")]
[assembly: AssemblyInformationalVersion("__PRODUCT_VERSION__")]

internal static class NativeLauncher
{
    internal const string Version = "__PRODUCT_VERSION__";

    [STAThread]
    private static int Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        string root = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
        if (root.Length > Path.GetPathRoot(root).Length) root = root.TrimEnd(Path.DirectorySeparatorChar);
        string key;
        using (SHA256 hash = SHA256.Create())
            key = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant()))).Replace("-", "").Substring(0, 20);

        bool created;
        using (Mutex mutex = new Mutex(true, "Local\\BuildSomething.NativeStartup." + key, out created))
        using (EventWaitHandle focus = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\BuildSomething.NativeStartupFocus." + key))
        {
            if (!created)
            {
                focus.Set();
                try { new StartupLog(root).WriteStatus("already-preparing", "应用正在启动，请稍候。", null, null); }
                catch { /* A duplicate never starts another preparation, even in a read-only directory. */ }
                return 0;
            }
            try
            {
                using (StartupForm form = new StartupForm(root, focus))
                {
                    Application.Run(form);
                    return form.ExitCode;
                }
            }
            catch (Exception error)
            {
                MessageBox.Show("造点啥无法启动。请确认解压目录可写，然后重新打开应用。\n\n" + error.Message,
                    "造点啥 · 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            finally { mutex.ReleaseMutex(); }
        }
    }

    internal static string QuotePowerShell(string value) { return "'" + value.Replace("'", "''") + "'"; }

    internal static string ResolveShell(string root, bool preparing)
    {
        if (!preparing)
        {
            string portable = Path.Combine(root, "runtime", "tools", "pwsh", "pwsh.exe");
            if (File.Exists(portable)) return portable;
            foreach (string directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
            {
                try
                {
                    string candidate = Path.Combine(directory.Trim('"'), "pwsh.exe");
                    if (File.Exists(candidate)) return candidate;
                }
                catch (ArgumentException) { }
            }
        }
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),
            "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
    }
}

internal sealed class StartupLog
{
    internal readonly string Root;
    internal readonly string LogPath;
    internal readonly string StatusPath;
    internal readonly string NativeStatusPath;
    private readonly object sync = new object();

    internal StartupLog(string root)
    {
        Root = root;
        string directory = Path.Combine(root, "logs", "desktop-launcher");
        Directory.CreateDirectory(directory);
        string stem = DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fff") + "-" + Process.GetCurrentProcess().Id + "-" + Guid.NewGuid().ToString("N").Substring(0, 6);
        LogPath = Path.Combine(directory, stem + ".log");
        StatusPath = Path.Combine(directory, stem + "-startup.json");
        NativeStatusPath = Path.Combine(directory, stem + "-native.json");
    }

    internal void Append(string line)
    {
        if (line == null) return;
        lock (sync) File.AppendAllText(LogPath, DateTime.UtcNow.ToString("o") + " " + line + Environment.NewLine, new UTF8Encoding(false));
    }

    internal void WriteStatus(string status, string message, string error, IDictionary<string, object> extra)
    {
        var value = new Dictionary<string, object> {
            { "version", NativeLauncher.Version }, { "status", status }, { "message", message },
            { "root", Root }, { "launcherPid", Process.GetCurrentProcess().Id },
            { "updatedAt", DateTime.UtcNow.ToString("o") }, { "logPath", LogPath },
            { "nativeStatusPath", NativeStatusPath }, { "consoleVisible", false }
        };
        if (error != null) value["error"] = error;
        if (extra != null) foreach (var item in extra) value[item.Key] = item.Value;
        lock (sync)
        {
            string temporary = StatusPath + ".tmp";
            File.WriteAllText(temporary, new JavaScriptSerializer().Serialize(value), new UTF8Encoding(false));
            if (File.Exists(StatusPath)) File.Replace(temporary, StatusPath, null);
            else File.Move(temporary, StatusPath);
        }
    }
}

internal sealed class StartupForm : Form
{
    private readonly string root;
    private readonly StartupLog log;
    private readonly EventWaitHandle focus;
    private readonly Label message;
    private readonly Label detail;
    private readonly ProgressBar progress;
    private readonly Button action;
    private readonly Button logs;
    private readonly System.Windows.Forms.Timer focusTimer;
    private readonly object ownership = new object();
    private OwnedJob activeJob;
    private bool cancelled;
    private bool completed;
    private bool failed;
    internal int ExitCode { get; private set; }

    internal StartupForm(string directory, EventWaitHandle focusEvent)
    {
        root = directory;
        focus = focusEvent;
        log = new StartupLog(root);
        Text = "造点啥 · 正在启动";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(480, 225);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        Font = new Font("Microsoft YaHei UI", 9F);
        BackColor = Color.FromArgb(248, 249, 251);

        var heading = new Label { Text = "build something · 造点啥", Location = new Point(26, 20),
            Size = new Size(428, 30), Font = new Font(Font.FontFamily, 15F, FontStyle.Bold) };
        message = new Label { Text = "正在检查应用运行环境…", Location = new Point(26, 62), Size = new Size(428, 28) };
        detail = new Label { Text = "首次打开会自动准备所需环境，模型安装在应用内选择。", Location = new Point(26, 94),
            Size = new Size(428, 42), ForeColor = Color.FromArgb(85, 92, 105) };
        progress = new ProgressBar { Location = new Point(26, 144), Size = new Size(428, 8), Style = ProgressBarStyle.Marquee, MarqueeAnimationSpeed = 30 };
        action = new Button { Text = "取消", Location = new Point(366, 173), Size = new Size(88, 30) };
        logs = new Button { Text = "查看日志", Location = new Point(258, 173), Size = new Size(96, 30), Visible = false };
        Controls.AddRange(new Control[] { heading, message, detail, progress, action, logs });
        action.Click += delegate { Close(); };
        logs.Click += delegate { Process.Start(new ProcessStartInfo("explorer.exe", "/select,\"" + log.LogPath + "\"") { UseShellExecute = true }); };
        focusTimer = new System.Windows.Forms.Timer { Interval = 200 };
        focusTimer.Tick += delegate { if (focus.WaitOne(0)) { Show(); Activate(); } };
        focusTimer.Start();
        Shown += async delegate { await RunStartup(); };
        FormClosing += OnClosing;
        FormClosed += delegate { focusTimer.Dispose(); };
        ExitCode = 2;
        log.WriteStatus("starting", "正在检查应用运行环境…", null, null);
    }

    private async Task RunStartup()
    {
        try
        {
            await Task.Run((Action)Launch);
            lock (ownership)
            {
                if (cancelled) return;
                completed = true;
                ExitCode = 0;
            }
            Close();
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            lock (ownership)
            {
                if (cancelled) return;
                failed = true;
                ExitCode = 1;
            }
            string explanation = FriendlyError(error.Message);
            log.Append(error.ToString());
            log.WriteStatus("failed", explanation, error.Message, null);
            Text = "造点啥 · 启动失败";
            message.Text = explanation;
            detail.Text = "请关闭后重新打开应用重试。已下载的文件会保留；详情可查看日志。";
            progress.Style = ProgressBarStyle.Blocks;
            progress.Value = 0;
            action.Text = "关闭";
            logs.Visible = true;
        }
    }

    private void Launch()
    {
        string bootstrap = Path.Combine(root, "scripts", "bootstrap-runtime.ps1");
        string desktop = Path.Combine(root, "scripts", "start-desktop-factory.ps1");
        if (!File.Exists(bootstrap) || !File.Exists(desktop))
            throw new InvalidOperationException("应用文件不完整，请重新解压完整文件夹后打开造点啥.exe。");
        RunPhase("preparing", "正在准备应用运行环境…", bootstrap,
            " -Mode desktop -Root " + NativeLauncher.QuotePowerShell(root) + " -Port 7860 -PrepareOnly", true);
        CheckCancelled();
        UpdateMessage("环境已就绪，正在打开造点啥…");
        RunPhase("opening", "正在打开应用窗口…", desktop,
            " -Port 7860 -StartupStatusPath " + NativeLauncher.QuotePowerShell(log.NativeStatusPath), false);
    }

    private void RunPhase(string status, string label, string script, string arguments, bool preparing)
    {
        CheckCancelled();
        string gateName = "Local\\BuildSomething.StartGate." + Process.GetCurrentProcess().Id + "." + Guid.NewGuid().ToString("N");
        using (EventWaitHandle gate = new EventWaitHandle(false, EventResetMode.ManualReset, gateName))
        using (OwnedJob job = new OwnedJob())
        using (Process process = new Process())
        {
            string command = "$ErrorActionPreference='Stop';"
                + "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
                + "$OutputEncoding=[Console]::OutputEncoding;"
                + "$gate=[Threading.EventWaitHandle]::OpenExisting(" + NativeLauncher.QuotePowerShell(gateName) + ");"
                + "if(-not $gate.WaitOne(15000)){exit 9};$gate.Dispose();"
                + "$global:LASTEXITCODE=0;try{& " + NativeLauncher.QuotePowerShell(script) + arguments
                + ";if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}}catch{[Console]::Error.WriteLine($_.Exception.ToString());exit 1}";
            process.StartInfo = new ProcessStartInfo(NativeLauncher.ResolveShell(root, preparing),
                "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand "
                + Convert.ToBase64String(Encoding.Unicode.GetBytes(command))) {
                WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs output) { CaptureOutput(output.Data); };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs output) { CaptureOutput(output.Data); };
            lock (ownership)
            {
                CheckCancelled();
                process.Start();
                try { job.Assign(process); }
                catch { if (!process.HasExited) process.Kill(); throw; }
                activeJob = job;
                log.WriteStatus(status, label, null, new Dictionary<string, object> { { "bootstrapPid", process.Id } });
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                // The script cannot spawn any child before its hidden shell joins our job.
                gate.Set();
            }
            try
            {
                DateTime deadline = DateTime.UtcNow.AddSeconds(preparing ? 7200 : 90);
                while (DateTime.UtcNow < deadline)
                {
                    CheckCancelled();
                    if (!preparing && TryNativeReady(job)) return;
                    if (process.WaitForExit(100))
                    {
                        process.WaitForExit(); // Flush asynchronous log readers.
                        if (process.ExitCode != 0)
                            throw new InvalidOperationException((preparing ? "应用环境准备失败" : "应用窗口启动失败") + "（退出码 " + process.ExitCode + "）。");
                        if (preparing) return;
                        if (TryNativeReady(job)) return;
                        throw new InvalidOperationException("应用窗口没有提供可见的启动确认，请查看日志后重试。");
                    }
                }
                throw new TimeoutException(preparing ? "环境准备时间过长，请检查网络连接后重新打开应用。" : "应用窗口启动超时，请查看日志后重试。");
            }
            finally
            {
                lock (ownership) { if (activeJob == job) activeJob = null; }
            }
        }
    }

    private bool TryNativeReady(OwnedJob job)
    {
        if (!File.Exists(log.NativeStatusPath)) return false;
        Dictionary<string, object> value;
        try { value = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(log.NativeStatusPath, Encoding.UTF8)); }
        catch (IOException) { return false; }
        catch (ArgumentException) { return false; } // An in-progress receipt is retried, never called ready.
        if (value == null || !value.ContainsKey("status")) return false;
        string status = Convert.ToString(value["status"]);
        if (status == "failed")
            throw new InvalidOperationException(value.ContainsKey("error") ? Convert.ToString(value["error"]) : "应用窗口启动失败。");
        if (status != "ready" && status != "foregrounded") return false;
        if (!value.ContainsKey("visible") || !(value["visible"] is bool) || !(bool)value["visible"])
            throw new InvalidOperationException("应用窗口尚未确认可见，请查看日志后重试。");
        if (value.ContainsKey("minimized") && Object.Equals(value["minimized"], true))
            throw new InvalidOperationException("应用窗口仍处于最小化状态，请重试。");
        if (!value.ContainsKey("version") || Convert.ToString(value["version"]) != NativeLauncher.Version)
            throw new InvalidOperationException("应用版本不一致，请使用完整的 v" + NativeLauncher.Version + " 文件夹重新打开。");
        if (!value.ContainsKey("pid") || Convert.ToInt64(value["pid"]) <= 0)
            throw new InvalidOperationException("应用窗口启动确认缺少有效进程信息。");
        lock (ownership)
        {
            CheckCancelled();
            // The now-visible desktop owns its backend lifecycle. Closing this startup form
            // must not terminate that application's processes after successful handover.
            job.ReleaseChildren();
            completed = true;
            log.WriteStatus("ready", "造点啥已打开。关闭应用时，其后台任务也会停止。", null, value);
        }
        return true;
    }

    private void CaptureOutput(string line)
    {
        if (line == null) return;
        log.Append(line);
        if (line.IndexOf("Downloading", StringComparison.OrdinalIgnoreCase) >= 0)
            UpdateMessage("正在下载应用运行环境，请稍候…");
        else if (line.IndexOf("Preparing private Python", StringComparison.OrdinalIgnoreCase) >= 0)
            UpdateMessage("正在准备应用独立运行环境…");
        else if (line.IndexOf("WebView2", StringComparison.OrdinalIgnoreCase) >= 0)
            UpdateMessage("正在准备应用窗口组件…");
    }

    private void UpdateMessage(string text)
    {
        if (IsDisposed || !IsHandleCreated) return;
        try { BeginInvoke((Action)delegate { if (!IsDisposed && !failed && !cancelled) message.Text = text; }); }
        catch (InvalidOperationException) { }
    }

    private static string FriendlyError(string error)
    {
        if (error.IndexOf("WebView2", StringComparison.OrdinalIgnoreCase) >= 0) return "应用窗口组件准备失败，请检查网络后重试。";
        if (error.IndexOf("准备", StringComparison.OrdinalIgnoreCase) >= 0) return "应用运行环境准备失败，请重试。";
        return "造点啥启动失败，请查看日志后重试。";
    }

    private void CheckCancelled()
    {
        lock (ownership) { if (cancelled) throw new OperationCanceledException(); }
    }

    private void OnClosing(object sender, FormClosingEventArgs args)
    {
        lock (ownership)
        {
            if (completed || failed) return;
            cancelled = true;
            if (activeJob != null) activeJob.Dispose();
            ExitCode = 2;
            log.WriteStatus("cancelled", "已取消本次启动，重新打开应用即可继续准备。", null, null);
        }
    }
}

internal sealed class OwnedJob : IDisposable
{
    private IntPtr handle;
    private const uint KillOnClose = 0x2000;

    internal OwnedJob()
    {
        handle = CreateJobObject(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        try { SetLimits(KillOnClose); }
        catch { Dispose(); throw; }
    }

    internal void Assign(Process process)
    {
        if (!AssignProcessToJobObject(handle, process.Handle)) throw new Win32Exception(Marshal.GetLastWin32Error());
    }

    internal void ReleaseChildren() { SetLimits(0); }

    private void SetLimits(uint flags)
    {
        var limits = new ExtendedLimitInformation();
        limits.BasicLimitInformation.LimitFlags = flags;
        int size = Marshal.SizeOf(typeof(ExtendedLimitInformation));
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(limits, buffer, false);
            if (!SetInformationJobObject(handle, 9, buffer, (uint)size)) throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    public void Dispose()
    {
        IntPtr current = Interlocked.Exchange(ref handle, IntPtr.Zero);
        if (current != IntPtr.Zero) CloseHandle(current);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimitInformation
    {
        internal long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        internal uint LimitFlags;
        internal UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        internal uint ActiveProcessLimit;
        internal UIntPtr Affinity;
        internal uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters { internal ulong ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount, WriteTransferCount, OtherTransferCount; }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimitInformation
    {
        internal BasicLimitInformation BasicLimitInformation;
        internal IoCounters IoInfo;
        internal UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr security, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int informationClass, IntPtr information, uint length);
    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr value);
}
