// Tarjim Setup: the installer, with the program's own look.
//
// One file. The program's files travel as a zip archive appended to this
// executable (see build_setup.py); a 16-byte trailer says where it starts.
// Installs for the current user only (no administrator rights):
//   %LOCALAPPDATA%\Programs\Tarjim
// Registers an uninstaller under "Installed apps" and creates the shortcuts.
//
//   Tarjim-Setup.exe                 the normal, graphical installation
//   Tarjim-Setup.exe /S [/D=folder]  silent (used by the build's own test)
//   Uninstall.exe                    the same program without a payload: removes the installation
//
// Written for the C# 5 compiler that ships with Windows (csc.exe, .NET Framework 4).

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Tarjim Setup")]
[assembly: AssemblyProduct("Tarjim")]
[assembly: AssemblyCompany("Tarjim")]
[assembly: AssemblyDescription("Installer for Tarjim: Arabic subtitles over PS5 games")]
[assembly: AssemblyCopyright("Copyright 2026 Tarjim")]
[assembly: AssemblyVersion("0.5.3.0")]
[assembly: AssemblyFileVersion("0.5.3.0")]

/// <summary>A window onto one part of a file, so the appended zip reads as a file of its own.</summary>
sealed class SubStream : Stream
{
    readonly Stream inner; readonly long start; readonly long length; long position;
    public SubStream(Stream inner, long start, long length) { this.inner = inner; this.start = start; this.length = length; }
    public override bool CanRead { get { return true; } }
    public override bool CanSeek { get { return true; } }
    public override bool CanWrite { get { return false; } }
    public override long Length { get { return length; } }
    public override long Position { get { return position; } set { position = value; } }
    public override void Flush() { }
    public override int Read(byte[] buffer, int offset, int count)
    {
        long left = length - position;
        if (left <= 0) return 0;
        if (count > left) count = (int)left;
        inner.Position = start + position;
        int n = inner.Read(buffer, offset, count);
        position += n;
        return n;
    }
    public override long Seek(long offset, SeekOrigin origin)
    {
        if (origin == SeekOrigin.Begin) position = offset;
        else if (origin == SeekOrigin.Current) position += offset;
        else position = length + offset;
        return position;
    }
    public override void SetLength(long value) { throw new NotSupportedException(); }
    public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
}

static class Installer
{
    public const string Version = "0.5.3";
    public const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Tarjim";
    static readonly byte[] Magic = Encoding.ASCII.GetBytes("TARJIMZ1");

    public static string Self { get { return Assembly.GetExecutingAssembly().Location; } }

    public static string DefaultFolder
    {
        get
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallKey))
            {
                string old = key == null ? null : key.GetValue("InstallLocation") as string;
                if (!string.IsNullOrEmpty(old) && Directory.Exists(old)) return old;  // update in place
            }
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Tarjim");
        }
    }

    /// <summary>The appended archive, or null when this copy carries none (it is then the uninstaller).</summary>
    public static ZipArchive OpenPayload(out FileStream file)
    {
        file = new FileStream(Self, FileMode.Open, FileAccess.Read, FileShare.Read);
        if (file.Length > 16)
        {
            byte[] tail = new byte[16];
            file.Position = file.Length - 16;
            file.Read(tail, 0, 16);
            bool ours = true;
            for (int i = 0; i < 8; i++) if (tail[i] != Magic[i]) ours = false;
            long size = BitConverter.ToInt64(tail, 8);
            if (ours && size > 0 && size < file.Length)
                return new ZipArchive(new SubStream(file, file.Length - 16 - size, size), ZipArchiveMode.Read);
        }
        file.Dispose();
        file = null;
        return null;
    }

    public static bool HasPayload()
    {
        FileStream file;
        ZipArchive zip = OpenPayload(out file);
        if (zip == null) return false;
        zip.Dispose(); file.Dispose();
        return true;
    }

    public static long PayloadBytes()
    {
        FileStream file;
        long total = 0;
        using (ZipArchive zip = OpenPayload(out file))
        using (file)
            foreach (ZipArchiveEntry e in zip.Entries) total += e.Length;
        return total;
    }

    /// <summary>Ask a running copy of the program to close, so its files can be replaced.</summary>
    public static void CloseRunningApp()
    {
        try
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:47900/api/quit");
            req.Method = "POST"; req.ContentType = "application/json"; req.Timeout = 1500; req.Proxy = null;
            byte[] body = Encoding.ASCII.GetBytes("{}");
            using (Stream s = req.GetRequestStream()) s.Write(body, 0, body.Length);
            using (req.GetResponse()) { }
            Thread.Sleep(2500);
        }
        catch (Exception) { /* not running: the usual case */ }
    }

    static string StepFor(string name)
    {
        if (name.StartsWith("models/")) return "أنسخ المترجم (أكبر ملف)…";
        if (name.StartsWith("python/")) return "أجهّز المحرّك…";
        if (name.StartsWith("laptop/.venv/")) return "أثبّت المكتبات…";
        if (name.StartsWith(".tools/") || name.StartsWith("tv-app/") || name.StartsWith("phone-app/")) return "أجهّز أدوات التلفزيون والجوال…";
        return "أنسخ ملفات البرنامج…";
    }

    /// <summary>Does everything. `report(fraction, step)` is called from this (worker) thread.</summary>
    const int MaxFolder = 140;

    public static void Install(string folder, bool desktopShortcut, Action<double, string> report)
    {
        // The program's deepest file is 114 characters below the folder and Windows stops at 259:
        // a deeper folder used to stop the copy halfway.
        if (Path.GetFullPath(folder).TrimEnd(Path.DirectorySeparatorChar).Length > MaxFolder)
            throw new PathTooLongException(@"مكان التثبيت طويل. اختر مجلد أقصر، مثل C:\Tarjim.");
        report(0, "أتأكد إن ترجِم مقفول…");
        CloseRunningApp();
        Directory.CreateDirectory(folder);
        // An update: clear what the new version brings whole. The user's games, sessions and logs stay.
        foreach (string stale in new string[] { "python", Path.Combine("laptop", "tarjim"), Path.Combine("laptop", ".venv") })
        {
            string path = Path.Combine(folder, stale);
            if (Directory.Exists(path)) Directory.Delete(path, true);
        }

        FileStream file;
        using (ZipArchive zip = OpenPayload(out file))
        using (file)
        {
            long total = 0, done = 0;
            foreach (ZipArchiveEntry e in zip.Entries) total += e.Length;
            byte[] buffer = new byte[1 << 20];
            string root = Path.GetFullPath(folder).TrimEnd('\\') + "\\";
            foreach (ZipArchiveEntry entry in zip.Entries)
            {
                string target = Path.GetFullPath(Path.Combine(folder, entry.FullName.Replace('/', '\\')));
                if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase)) continue;  // never write outside the folder
                if (entry.FullName.EndsWith("/")) { Directory.CreateDirectory(target); continue; }
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                bool keepUsers = entry.FullName.StartsWith("profiles/") && File.Exists(target);  // the user's own game settings
                if (keepUsers) { done += entry.Length; continue; }
                string step = StepFor(entry.FullName);
                using (Stream src = entry.Open())
                using (FileStream dst = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20))
                {
                    int n;
                    while ((n = src.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        dst.Write(buffer, 0, n);
                        done += n;
                        report(total == 0 ? 1 : 0.02 + 0.95 * done / total, step);
                    }
                }
            }
        }

        report(0.98, "ألمسات أخيرة…");
        // Point the program's environment at the Python that was just installed beside it.
        string venv = Path.Combine(folder, "laptop", ".venv");
        Directory.CreateDirectory(venv);
        File.WriteAllText(Path.Combine(venv, "pyvenv.cfg"),
            "home = " + Path.Combine(folder, "python") + "\r\nimplementation = CPython\r\nversion_info = 3.12\r\ninclude-system-site-packages = false\r\n",
            new UTF8Encoding(false));

        string exe = Path.Combine(folder, "Tarjim.exe");
        string uninstaller = Path.Combine(folder, "Uninstall.exe");
        WriteUninstaller(uninstaller);
        string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
        Shortcut(Path.Combine(programs, "Tarjim.lnk"), exe, folder);
        if (desktopShortcut)
            Shortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Tarjim.lnk"), exe, folder);

        using (RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallKey))
        {
            key.SetValue("DisplayName", "ترجِم (Tarjim)");
            key.SetValue("DisplayVersion", Version);
            key.SetValue("Publisher", "Tarjim");
            key.SetValue("DisplayIcon", exe + ",0");
            key.SetValue("InstallLocation", folder);
            key.SetValue("UninstallString", "\"" + uninstaller + "\"");
            key.SetValue("QuietUninstallString", "\"" + uninstaller + "\" /S");
            key.SetValue("NoModify", 1, RegistryValueKind.DWord);
            key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            key.SetValue("EstimatedSize", (int)(FolderSize(folder) / 1024), RegistryValueKind.DWord);
        }
        report(1, "تم");
    }

    /// <summary>This program without its payload: small, and it then acts as the uninstaller.</summary>
    static void WriteUninstaller(string target)
    {
        using (FileStream src = new FileStream(Self, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (FileStream dst = new FileStream(target, FileMode.Create, FileAccess.Write))
        {
            long keep = src.Length;
            byte[] tail = new byte[16];
            if (src.Length > 16)
            {
                src.Position = src.Length - 16;
                src.Read(tail, 0, 16);
                bool ours = true;
                for (int i = 0; i < 8; i++) if (tail[i] != Magic[i]) ours = false;
                if (ours) keep = src.Length - 16 - BitConverter.ToInt64(tail, 8);
            }
            src.Position = 0;
            byte[] buffer = new byte[1 << 16];
            while (keep > 0)
            {
                int n = src.Read(buffer, 0, (int)Math.Min(buffer.Length, keep));
                if (n <= 0) break;
                dst.Write(buffer, 0, n);
                keep -= n;
            }
        }
    }

    static long FolderSize(string folder)
    {
        long total = 0;
        try { foreach (string f in Directory.GetFiles(folder, "*", SearchOption.AllDirectories)) total += new FileInfo(f).Length; }
        catch (Exception) { }
        return total;
    }

    static void Shortcut(string link, string target, string workdir)
    {
        Type type = Type.GetTypeFromProgID("WScript.Shell");
        object shell = Activator.CreateInstance(type);
        try
        {
            object s = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { link });
            Type st = s.GetType();
            st.InvokeMember("TargetPath", BindingFlags.SetProperty, null, s, new object[] { target });
            st.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, s, new object[] { workdir });
            st.InvokeMember("IconLocation", BindingFlags.SetProperty, null, s, new object[] { target + ",0" });
            st.InvokeMember("Description", BindingFlags.SetProperty, null, s, new object[] { "Tarjim" });
            st.InvokeMember("Save", BindingFlags.InvokeMethod, null, s, null);
            Marshal.ReleaseComObject(s);
        }
        finally { Marshal.ReleaseComObject(shell); }
    }

    // -- removal ---------------------------------------------------------------

    public static string InstalledFolder()
    {
        using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallKey))
        {
            string where = key == null ? null : key.GetValue("InstallLocation") as string;
            if (!string.IsNullOrEmpty(where)) return where;
        }
        return Path.GetDirectoryName(Self);
    }

    /// <summary>Remove the installation. The user's private folder (~/.tarjim) is only removed when asked.</summary>
    public static void Uninstall(string folder, bool alsoUserData)
    {
        CloseRunningApp();
        string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
        string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        foreach (string link in new string[] { Path.Combine(programs, "Tarjim.lnk"), Path.Combine(desktop, "Tarjim.lnk") })
            try { if (File.Exists(link)) File.Delete(link); } catch (Exception) { }
        // Only a folder that really is a Tarjim installation is ever deleted.
        if (File.Exists(Path.Combine(folder, "Tarjim.exe")) && Directory.Exists(Path.Combine(folder, "laptop")))
        {
            foreach (string entry in Directory.GetFileSystemEntries(folder))
            {
                if (string.Equals(entry, Self, StringComparison.OrdinalIgnoreCase)) continue;
                try { if (Directory.Exists(entry)) Directory.Delete(entry, true); else File.Delete(entry); } catch (Exception) { }
            }
        }
        if (alsoUserData)
        {
            string home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".tarjim");
            try { if (Directory.Exists(home)) Directory.Delete(home, true); } catch (Exception) { }
        }
        try { Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false); } catch (Exception) { }
        // A running program cannot delete itself: let cmd do it a moment after we exit.
        if (Self.StartsWith(folder, StringComparison.OrdinalIgnoreCase))
        {
            ProcessStartInfo cleanup = new ProcessStartInfo("cmd.exe",
                "/c ping 127.0.0.1 -n 3 >nul & del /f /q \"" + Self + "\" & rmdir \"" + folder + "\"");
            cleanup.CreateNoWindow = true; cleanup.UseShellExecute = false; cleanup.WindowStyle = ProcessWindowStyle.Hidden;
            Process.Start(cleanup);
        }
    }
}

/// <summary>The window. Everything is drawn by hand so it matches the program.</summary>
sealed class SetupForm : Form
{
    enum Page { Welcome, Working, Done, Failed, AskRemove, Removed }

    // The program's look: graphite and white, no colours of its own (the games bring theirs).
    // "Amber" is what you press: white, with near-black writing on it, like the program's start button.
    static readonly Color Ink = Color.FromArgb(11, 11, 13), Ink2 = Color.FromArgb(22, 22, 26), Ink3 = Color.FromArgb(34, 34, 40),
        Ink4 = Color.FromArgb(54, 54, 62), Paper = Color.FromArgb(255, 255, 255), Muted = Color.FromArgb(160, 160, 170),
        Amber = Color.FromArgb(255, 255, 255), AmberInk = Color.FromArgb(12, 12, 14), Live = Color.FromArgb(52, 210, 123),
        Bad = Color.FromArgb(255, 107, 107);

    static readonly string[][] Lines = new string[][] {
        new string[] { "We should leave before the storm gets here.", "يجب أن نغادر قبل وصول العاصفة." },
        new string[] { "Keep your voice down and stay close.", "اخفض صوتك وابقَ قريباً." },
        new string[] { "I never thought I'd see this place again.", "لم أظن أنني سأرى هذا المكان مرة أخرى." },
    };

    [DllImport("user32")] static extern bool ReleaseCapture();
    [DllImport("user32")] static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
    [DllImport("dwmapi")] static extern int DwmSetWindowAttribute(IntPtr h, int attr, ref int value, int size);
    [DllImport("gdi32")] static extern IntPtr AddFontMemResourceEx(IntPtr font, uint size, IntPtr reserved, ref uint count);

    readonly bool uninstalling;
    readonly float s;  // display scale
    readonly PrivateFontCollection fonts = new PrivateFontCollection();
    readonly FontFamily subtitleFamily;
    readonly Image icon;
    readonly System.Windows.Forms.Timer clock = new System.Windows.Forms.Timer();
    readonly Stopwatch time = Stopwatch.StartNew();

    Page page;
    string folder;
    bool desktopShortcut = true, removeUserData = false;
    double progress, shownProgress;
    string step = "", error = "";
    long payloadBytes;
    RectangleF primary, secondary, closeBox, changeLink, toggle;
    int hover;  // 1 primary, 2 secondary, 3 close, 4 change, 5 toggle

    public SetupForm(bool uninstalling, string chosenFolder)
    {
        this.uninstalling = uninstalling;
        page = uninstalling ? Page.AskRemove : Page.Welcome;
        folder = chosenFolder ?? (uninstalling ? Installer.InstalledFolder() : Installer.DefaultFolder);
        if (!uninstalling) payloadBytes = Installer.PayloadBytes();

        Text = uninstalling ? "إزالة ترجِم" : "تثبيت ترجِم";
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Ink;
        DoubleBuffered = true;
        KeyPreview = true;
        using (Graphics g = CreateGraphics()) s = g.DpiX / 96f;
        ClientSize = new Size((int)(860 * s), (int)(520 * s));
        Icon = System.Drawing.Icon.ExtractAssociatedIcon(Installer.Self);

        Assembly me = Assembly.GetExecutingAssembly();
        using (Stream png = me.GetManifestResourceStream("icon.png")) icon = Image.FromStream(png);
        using (Stream ttf = me.GetManifestResourceStream("Harmattan-Regular.ttf"))
        {
            byte[] data = new byte[ttf.Length];
            ttf.Read(data, 0, data.Length);
            IntPtr mem = Marshal.AllocCoTaskMem(data.Length);
            Marshal.Copy(data, 0, mem, data.Length);
            fonts.AddMemoryFont(mem, data.Length);
            uint count = 0;
            AddFontMemResourceEx(mem, (uint)data.Length, IntPtr.Zero, ref count);
        }
        subtitleFamily = fonts.Families.Length > 0 ? fonts.Families[0] : new FontFamily("Segoe UI");

        clock.Interval = 33;
        clock.Tick += delegate { shownProgress += (progress - shownProgress) * 0.18; Invalidate(); };
        clock.Start();
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        int round = 2, dark = 1;  // Windows 11: rounded corners and a dark frame
        try { DwmSetWindowAttribute(Handle, 33, ref round, 4); DwmSetWindowAttribute(Handle, 20, ref dark, 4); } catch (Exception) { }
    }

    protected override CreateParams CreateParams
    {
        get { CreateParams cp = base.CreateParams; cp.ClassStyle |= 0x20000; cp.Style |= 0x20000; return cp; }  // shadow, minimise from taskbar
    }

    // -- drawing ---------------------------------------------------------------

    Font Ui(float size, FontStyle style) { return new Font("Segoe UI", size * s, style, GraphicsUnit.Pixel); }

    static GraphicsPath Round(RectangleF r, float radius)
    {
        GraphicsPath p = new GraphicsPath();
        float d = radius * 2;
        p.AddArc(r.X, r.Y, d, d, 180, 90); p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90); p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }

    void Rtl(Graphics g, string text, Font font, Color color, RectangleF box, StringAlignment line)
    {
        using (StringFormat f = new StringFormat(StringFormatFlags.DirectionRightToLeft))
        using (SolidBrush b = new SolidBrush(color))
        {
            f.Alignment = StringAlignment.Near; f.LineAlignment = line;  // "near" is the right edge in right-to-left
            g.DrawString(text, font, b, box, f);
        }
    }

    void Centered(Graphics g, string text, Font font, Color color, RectangleF box, bool rtl)
    {
        using (StringFormat f = new StringFormat(rtl ? StringFormatFlags.DirectionRightToLeft : 0))
        using (SolidBrush b = new SolidBrush(color))
        {
            f.Alignment = StringAlignment.Center; f.LineAlignment = StringAlignment.Center;
            g.DrawString(text, font, b, box, f);
        }
    }

    void Subtitle(Graphics g, string text, Font font, int alpha, RectangleF box, bool rtl)
    {
        if (alpha <= 0) return;
        RectangleF shadow = box; shadow.Offset(2 * s, 2 * s);
        Centered(g, text, font, Color.FromArgb(alpha * 9 / 10, 0, 0, 0), shadow, rtl);
        Centered(g, text, font, Color.FromArgb(alpha, 242, 247, 241), box, rtl);
    }

    /// <summary>A quiet graphite scene, like the program's home before a game is chosen, playing one line: English, then Arabic in its place.</summary>
    void DrawScene(Graphics g, RectangleF r)
    {
        using (GraphicsPath clip = Round(r, 18 * s))
        {
            GraphicsState saved = g.Save();
            g.SetClip(clip);
            using (LinearGradientBrush ground = new LinearGradientBrush(r, Color.FromArgb(30, 30, 35), Color.FromArgb(11, 11, 13), 90f))
                g.FillRectangle(ground, r);
            double t = time.Elapsed.TotalSeconds;
            // a soft light from the top corner that drifts a little, as a screen glows in a dark room
            float lx = r.X + r.Width * (0.78f + 0.04f * (float)Math.Sin(t / 6)), ly = r.Y - r.Height * 0.1f, lr = r.Width * 0.75f;
            using (GraphicsPath glow = new GraphicsPath())
            {
                glow.AddEllipse(lx - lr, ly - lr * 0.8f, lr * 2, lr * 1.6f);
                using (PathGradientBrush light = new PathGradientBrush(glow))
                {
                    light.CenterColor = Color.FromArgb(70, 140, 140, 156);
                    light.SurroundColors = new Color[] { Color.FromArgb(0, 30, 30, 35) };
                    g.FillPath(light, glow);
                }
            }
            using (LinearGradientBrush shade = new LinearGradientBrush(r, Color.FromArgb(70, 0, 0, 0), Color.FromArgb(130, 0, 0, 0), 90f))
            {
                ColorBlend fade = new ColorBlend();
                fade.Colors = new Color[] { Color.FromArgb(70, 0, 0, 0), Color.FromArgb(0, 0, 0, 0), Color.FromArgb(0, 0, 0, 0), Color.FromArgb(140, 0, 0, 0) };
                fade.Positions = new float[] { 0f, 0.25f, 0.6f, 1f };
                shade.InterpolationColors = fade;
                g.FillRectangle(shade, r);
            }

            // One line every 5 seconds: the game's English, wiped, Arabic in its place.
            double cycle = 5.0, at = t % cycle;
            string[] line = Lines[(int)(t / cycle) % Lines.Length];
            int en = at < 0.3 ? (int)(255 * at / 0.3) : at < 1.5 ? 255 : at < 1.9 ? (int)(255 * (1.9 - at) / 0.4) : 0;
            int ar = at < 1.7 ? 0 : at < 2.2 ? (int)(255 * (at - 1.7) / 0.5) : at < 4.5 ? 255 : (int)(255 * (cycle - at) / 0.5);
            RectangleF box = new RectangleF(r.X + 14 * s, r.Bottom - 104 * s, r.Width - 28 * s, 84 * s);
            using (Font latin = Ui(13.5f, FontStyle.Regular)) Subtitle(g, line[0], latin, en, box, false);
            float lift = at < 2.2 && at >= 1.7 ? (float)(2.2 - at) * 12 * s : 0;
            box.Offset(0, lift);
            using (Font arabic = new Font(subtitleFamily, 23 * s, FontStyle.Regular, GraphicsUnit.Pixel)) Subtitle(g, line[1], arabic, ar, box, true);
            g.Restore(saved);
        }
        using (GraphicsPath edge = Round(r, 18 * s))
        using (Pen pen = new Pen(Color.FromArgb(52, 52, 60), 1.5f * s)) g.DrawPath(pen, edge);
    }

    void Button(Graphics g, RectangleF r, string label, bool filled, bool hot, Color fill)
    {
        using (GraphicsPath p = Round(r, 14 * s))
        {
            Color lit = fill.GetBrightness() > 0.9f ? Color.FromArgb(228, 228, 232) : ControlPaint.Light(fill, 0.25f);
            Color back = filled ? (hot ? lit : fill) : (hot ? Ink4 : Ink3);
            using (SolidBrush b = new SolidBrush(back)) g.FillPath(b, p);
            if (!filled) using (Pen pen = new Pen(Color.FromArgb(60, 60, 68), s)) g.DrawPath(pen, p);
        }
        using (Font f = Ui(16.5f, FontStyle.Bold)) Centered(g, label, f, filled ? AmberInk : Paper, r, true);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        float W = ClientSize.Width, H = ClientSize.Height, pad = 26 * s;

        // a soft glow behind the scene, like a TV's backlight on the wall
        RectangleF scene = new RectangleF(pad, pad, 370 * s, H - pad * 2);
        using (GraphicsPath halo = new GraphicsPath())
        {
            halo.AddEllipse(scene.X - 90 * s, scene.Y + scene.Height * 0.25f, scene.Width + 260 * s, scene.Height * 0.9f);
            using (PathGradientBrush b = new PathGradientBrush(halo))
            {
                b.CenterColor = Color.FromArgb(34, 210, 210, 222);
                b.SurroundColors = new Color[] { Color.FromArgb(0, 10, 11, 15) };
                g.FillPath(b, halo);
            }
        }
        DrawScene(g, scene);

        float left = scene.Right + 40 * s, right = W - 44 * s, width = right - left;
        closeBox = new RectangleF(W - 46 * s, 12 * s, 32 * s, 32 * s);
        if (hover == 3) using (GraphicsPath p = Round(closeBox, 9 * s)) using (SolidBrush b = new SolidBrush(Ink4)) g.FillPath(b, p);
        using (Pen x = new Pen(page == Page.Working ? Ink4 : Muted, 1.6f * s))
        {
            float cx = closeBox.X + closeBox.Width / 2, cy = closeBox.Y + closeBox.Height / 2, d = 5.5f * s;
            g.DrawLine(x, cx - d, cy - d, cx + d, cy + d); g.DrawLine(x, cx + d, cy - d, cx - d, cy + d);
        }

        // brand
        float iconSize = 58 * s, top = 62 * s;
        g.DrawImage(icon, right - iconSize, top, iconSize, iconSize);
        using (Font f = Ui(30, FontStyle.Bold)) Rtl(g, "ترجِم", f, Paper, new RectangleF(left, top - 4 * s, width - iconSize - 16 * s, 40 * s), StringAlignment.Center);
        using (Font f = Ui(14, FontStyle.Regular)) Rtl(g, "ترجمة عربية فوق ألعاب الـPS5", f, Muted, new RectangleF(left, top + 34 * s, width - iconSize - 16 * s, 24 * s), StringAlignment.Center);

        float y = top + 96 * s;
        primary = secondary = changeLink = toggle = RectangleF.Empty;
        RectangleF bottomButton = new RectangleF(left, H - 44 * s - 56 * s, width, 56 * s);

        if (page == Page.Welcome)
        {
            using (Font f = Ui(15.5f, FontStyle.Regular))
                Rtl(g, "يثبّت البرنامج كامل مع مترجمه. يشتغل بدون إنترنت، وما يحتاج صلاحيات مدير.", f, Paper, new RectangleF(left, y, width, 56 * s), StringAlignment.Near);
            y += 76 * s;
            RectangleF card = new RectangleF(left, y, width, 98 * s);
            using (GraphicsPath p = Round(card, 16 * s)) using (SolidBrush b = new SolidBrush(Ink2)) g.FillPath(b, p);
            using (Font small = Ui(12.5f, FontStyle.Regular))
            using (Font mono = Ui(13, FontStyle.Regular))
            {
                Rtl(g, "مكان التثبيت", small, Muted, new RectangleF(card.X + 16 * s, card.Y + 12 * s, card.Width - 32 * s, 20 * s), StringAlignment.Center);
                using (StringFormat f = new StringFormat(StringFormatFlags.NoWrap))
                using (SolidBrush b = new SolidBrush(Paper))
                {
                    f.Trimming = StringTrimming.EllipsisPath; f.Alignment = StringAlignment.Far; f.LineAlignment = StringAlignment.Center;
                    g.DrawString(folder, mono, b, new RectangleF(card.X + 76 * s, card.Y + 33 * s, card.Width - 92 * s, 24 * s), f);
                }
                changeLink = new RectangleF(card.X + 12 * s, card.Y + 31 * s, 60 * s, 28 * s);
                using (Font link = Ui(13, hover == 4 ? FontStyle.Bold | FontStyle.Underline : FontStyle.Bold)) Centered(g, "تغيير", link, Amber, changeLink, true);
                string need = "يحتاج " + (payloadBytes / 1073741824.0).ToString("0.0") + " قيقا من القرص";
                Rtl(g, need, small, Muted, new RectangleF(card.X + 16 * s, card.Y + 64 * s, card.Width - 32 * s, 22 * s), StringAlignment.Center);
            }
            y += 116 * s;
            // switch: desktop shortcut
            toggle = new RectangleF(left, y, width, 30 * s);
            RectangleF track = new RectangleF(right - 46 * s, y + 2 * s, 46 * s, 26 * s);
            using (GraphicsPath p = Round(track, 13 * s)) using (SolidBrush b = new SolidBrush(desktopShortcut ? Live : Ink4)) g.FillPath(b, p);
            float knob = desktopShortcut ? track.X + 3 * s : track.Right - 23 * s;
            using (SolidBrush b = new SolidBrush(desktopShortcut ? Paper : Muted)) g.FillEllipse(b, knob, track.Y + 3 * s, 20 * s, 20 * s);
            using (Font f = Ui(14, FontStyle.Regular)) Rtl(g, "اختصار على سطح المكتب", f, Paper, new RectangleF(left, y, width - 58 * s, 30 * s), StringAlignment.Center);
            primary = bottomButton;
            Button(g, primary, "ثبّت ترجِم", true, hover == 1, Amber);
        }
        else if (page == Page.Working)
        {
            using (Font big = Ui(64, FontStyle.Bold))
            using (SolidBrush b = new SolidBrush(Paper))
            using (StringFormat f = new StringFormat())
            {
                f.Alignment = StringAlignment.Far; f.LineAlignment = StringAlignment.Near;
                g.DrawString(((int)Math.Round(shownProgress * 100)).ToString() + "%", big, b, new RectangleF(left, y - 6 * s, width + 6 * s, 90 * s), f);
            }
            y += 104 * s;
            RectangleF bar = new RectangleF(left, y, width, 8 * s);
            using (GraphicsPath p = Round(bar, 4 * s)) using (SolidBrush b = new SolidBrush(Ink4)) g.FillPath(b, p);
            float fill = Math.Max(8 * s, bar.Width * (float)shownProgress);
            RectangleF done = new RectangleF(bar.Right - fill, bar.Y, fill, bar.Height);  // fills from the right, as Arabic reads
            using (GraphicsPath p = Round(done, 4 * s)) using (SolidBrush b = new SolidBrush(Amber)) g.FillPath(b, p);
            using (Font f = Ui(15, FontStyle.Bold)) Rtl(g, step, f, Paper, new RectangleF(left, y + 22 * s, width, 28 * s), StringAlignment.Center);
            using (Font f = Ui(13.5f, FontStyle.Regular))
                Rtl(g, uninstalling ? "لحظات…" : "ياخذ دقيقة أو دقيقتين. بعدها: افتح ترجِم، امشِ مع الدليل مرة وحدة، وخلاص.", f, Muted, new RectangleF(left, y + 56 * s, width, 50 * s), StringAlignment.Near);
        }
        else if (page == Page.Done || page == Page.Removed)
        {
            RectangleF mark = new RectangleF(right - 54 * s, y, 54 * s, 54 * s);
            using (SolidBrush b = new SolidBrush(Color.FromArgb(40, Live))) g.FillEllipse(b, mark);
            using (Pen pen = new Pen(Live, 3.2f * s))
            {
                pen.StartCap = pen.EndCap = LineCap.Round; pen.LineJoin = LineJoin.Round;
                g.DrawLines(pen, new PointF[] { new PointF(mark.X + 16 * s, mark.Y + 28 * s), new PointF(mark.X + 24 * s, mark.Y + 36 * s), new PointF(mark.X + 39 * s, mark.Y + 19 * s) });
            }
            using (Font f = Ui(24, FontStyle.Bold)) Rtl(g, page == Page.Done ? "ترجِم جاهز" : "انشال ترجِم", f, Paper, new RectangleF(left, y + 4 * s, width - 70 * s, 46 * s), StringAlignment.Center);
            using (Font f = Ui(15, FontStyle.Regular))
                Rtl(g, page == Page.Done
                    ? "تلقاه في قائمة ابدأ" + (desktopShortcut ? " وعلى سطح المكتب" : "") + ". أول مرة يفتح لك الدليل: تجهيز التلفزيون وربط الـPS5، مرة وحدة بس."
                    : "انحذف البرنامج من هذا الجهاز. لو ثبّت تطبيق التلفزيون، تقدر تشيله من إعدادات التلفزيون ‹ التطبيقات.",
                    f, Muted, new RectangleF(left, y + 74 * s, width, 90 * s), StringAlignment.Near);
            primary = bottomButton;
            Button(g, primary, page == Page.Done ? "افتح ترجِم" : "إغلاق", true, hover == 1, Amber);
        }
        else if (page == Page.Failed)
        {
            using (Font f = Ui(22, FontStyle.Bold)) Rtl(g, "ما اكتمل", f, Bad, new RectangleF(left, y, width, 40 * s), StringAlignment.Center);
            using (Font f = Ui(14, FontStyle.Regular)) Rtl(g, error, f, Paper, new RectangleF(left, y + 50 * s, width, 150 * s), StringAlignment.Near);
            primary = bottomButton;
            Button(g, primary, "جرّب مرة ثانية", true, hover == 1, Amber);
        }
        else if (page == Page.AskRemove)
        {
            using (Font f = Ui(22, FontStyle.Bold)) Rtl(g, "تشيل ترجِم من الجهاز؟", f, Paper, new RectangleF(left, y, width, 40 * s), StringAlignment.Center);
            using (Font f = Ui(14.5f, FontStyle.Regular))
                Rtl(g, "ينحذف البرنامج ومترجمه واختصاراته. قبلها، لو تبي تشيله من التلفزيون بعد: افتح ترجِم ‹ الدليل ‹ «شيله من التلفزيون».", f, Muted, new RectangleF(left, y + 50 * s, width, 80 * s), StringAlignment.Near);
            y += 146 * s;
            toggle = new RectangleF(left, y, width, 30 * s);
            RectangleF track = new RectangleF(right - 46 * s, y + 2 * s, 46 * s, 26 * s);
            using (GraphicsPath p = Round(track, 13 * s)) using (SolidBrush b = new SolidBrush(removeUserData ? Bad : Ink4)) g.FillPath(b, p);
            float knob = removeUserData ? track.X + 3 * s : track.Right - 23 * s;
            using (SolidBrush b = new SolidBrush(removeUserData ? Ink : Muted)) g.FillEllipse(b, knob, track.Y + 3 * s, 20 * s, 20 * s);
            using (Font f = Ui(14, FontStyle.Regular)) Rtl(g, "احذف إعداداتي وربط الـPS5 بعد", f, Paper, new RectangleF(left, y, width - 58 * s, 30 * s), StringAlignment.Center);
            float half = (width - 12 * s) / 2;
            primary = new RectangleF(left + half + 12 * s, bottomButton.Y, half, bottomButton.Height);
            secondary = new RectangleF(left, bottomButton.Y, half, bottomButton.Height);
            Button(g, primary, "شيله", true, hover == 1, Bad);
            Button(g, secondary, "إلغاء", false, hover == 2, Ink3);
        }
        using (Font f = Ui(11.5f, FontStyle.Regular))
        using (SolidBrush b = new SolidBrush(Color.FromArgb(93, 96, 112)))
            g.DrawString("v" + Installer.Version, f, b, left, H - 30 * s);
    }

    // -- input -------------------------------------------------------------------

    int Hit(Point p)
    {
        if (primary.Contains(p)) return 1;
        if (secondary.Contains(p)) return 2;
        if (closeBox.Contains(p)) return 3;
        if (changeLink.Contains(p)) return 4;
        if (toggle.Contains(p)) return 5;
        return 0;
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        int now = Hit(e.Location);
        if (now != hover) { hover = now; Cursor = now == 0 ? Cursors.Default : Cursors.Hand; Invalidate(); }
    }

    protected override void OnMouseLeave(EventArgs e) { hover = 0; Invalidate(); }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        if (e.Button != MouseButtons.Left) return;
        int what = Hit(e.Location);
        if (what == 0) { ReleaseCapture(); SendMessage(Handle, 0xA1, (IntPtr)2, IntPtr.Zero); return; }  // drag the window
        if (what == 1) Primary();
        else if (what == 2 || what == 3) { if (page != Page.Working) Close(); }
        else if (what == 4) ChooseFolder();
        else if (what == 5) { if (page == Page.Welcome) desktopShortcut = !desktopShortcut; else removeUserData = !removeUserData; Invalidate(); }
    }

    protected override void OnKeyDown(KeyEventArgs e)
    {
        if (e.KeyCode == Keys.Enter && page != Page.Working && page != Page.AskRemove) Primary();
        if (e.KeyCode == Keys.Escape && page != Page.Working) Close();
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (page == Page.Working) e.Cancel = true;  // never leave a half-written installation
        base.OnFormClosing(e);
    }

    void ChooseFolder()
    {
        using (FolderBrowserDialog dialog = new FolderBrowserDialog())
        {
            dialog.Description = "اختر المجلد اللي ينحط فيه مجلد Tarjim";
            if (dialog.ShowDialog(this) != DialogResult.OK) return;
            string chosen = dialog.SelectedPath;
            folder = Path.GetFileName(chosen.TrimEnd('\\')).Equals("Tarjim", StringComparison.OrdinalIgnoreCase) ? chosen : Path.Combine(chosen, "Tarjim");
            Invalidate();
        }
    }

    void Primary()
    {
        if (page == Page.Welcome || page == Page.Failed) Work(false);
        else if (page == Page.AskRemove) Work(true);
        else if (page == Page.Done)
        {
            try { Process.Start(new ProcessStartInfo(Path.Combine(folder, "Tarjim.exe")) { WorkingDirectory = folder }); } catch (Exception) { }
            Close();
        }
        else if (page == Page.Removed) Close();
    }

    void Work(bool remove)
    {
        page = Page.Working; progress = shownProgress = 0; step = remove ? "أشيل الملفات…" : "أبدأ…"; hover = 0;
        Cursor = Cursors.Default;
        Thread worker = new Thread(delegate ()
        {
            try
            {
                if (remove) { progress = 0.4; Installer.Uninstall(folder, removeUserData); progress = 1; }
                else Installer.Install(folder, desktopShortcut, delegate (double fraction, string label) { progress = fraction; step = label; });
                Thread.Sleep(450);  // let the bar reach the end
                page = remove ? Page.Removed : Page.Done;
            }
            catch (PathTooLongException ex) { error = ex.Message; page = Page.Failed; }
            catch (UnauthorizedAccessException)
            {
                error = "ما قدرت أكتب في هذا المجلد. اقفل ترجِم لو كان مفتوح، أو اختر مكان ثاني."; page = Page.Failed;
            }
            catch (IOException ex)
            {
                error = "ملف مستخدم أو القرص ممتلئ. اقفل ترجِم لو كان مفتوح وتأكد من المساحة.\n\n" + ex.Message; page = Page.Failed;
            }
            catch (Exception ex) { error = ex.Message; page = Page.Failed; }
        });
        worker.IsBackground = true;
        worker.Start();
    }
}

static class Program
{
    [DllImport("user32")] static extern bool SetProcessDPIAware();

    [STAThread]
    static int Main(string[] args)
    {
        bool silent = false; string folder = null;
        foreach (string a in args)
        {
            if (a.Equals("/S", StringComparison.OrdinalIgnoreCase)) silent = true;
            if (a.StartsWith("/D=", StringComparison.OrdinalIgnoreCase)) folder = a.Substring(3).Trim('"');
        }
        bool hasPayload = Installer.HasPayload();
        if (silent)
        {
            try
            {
                if (hasPayload) Installer.Install(folder ?? Installer.DefaultFolder, false, delegate (double f, string label) { });
                else Installer.Uninstall(folder ?? Installer.InstalledFolder(), false);
                return 0;
            }
            catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
        }
        // The uninstaller must not hold the folder it deletes: run from a temporary copy.
        string self = Installer.Self;
        if (!hasPayload && !self.StartsWith(Path.GetTempPath(), StringComparison.OrdinalIgnoreCase))
        {
            string copy = Path.Combine(Path.GetTempPath(), "Tarjim-Uninstall.exe");
            try
            {
                File.Copy(self, copy, true);
                Process.Start(new ProcessStartInfo(copy, folder == null ? "" : "\"/D=" + folder + "\"") { WorkingDirectory = Path.GetTempPath() });
                return 0;
            }
            catch (Exception) { /* run from here instead */ }
        }
        SetProcessDPIAware();
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new SetupForm(!hasPayload, folder));
        return 0;
    }
}
