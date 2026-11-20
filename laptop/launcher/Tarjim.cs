// Tarjim.exe: the program's own executable.
//
// It loads the Python runtime of the project's virtual environment into THIS
// process and runs `-m tarjim.app`. Because the window then belongs to
// Tarjim.exe (not pythonw.exe), Windows shows the right name and icon in the
// taskbar, when pinned, in Task Manager and in the firewall prompt.
//
// Build: python launcher/build.py   (uses the C# compiler that ships with Windows)

using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;

[assembly: AssemblyTitle("Tarjim")]
[assembly: AssemblyProduct("Tarjim")]
[assembly: AssemblyDescription("Arabic subtitles over PS5 games")]
[assembly: AssemblyCompany("Tarjim")]
[assembly: AssemblyCopyright("Copyright 2026 Tarjim")]
[assembly: AssemblyVersion("0.5.2.0")]
[assembly: AssemblyFileVersion("0.5.2.0")]

static class Program
{
    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadLibraryEx(string file, IntPtr reserved, uint flags);

    [DllImport("kernel32", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);

    [DllImport("user32", CharSet = CharSet.Unicode)]
    static extern int MessageBox(IntPtr owner, string text, string caption, uint type);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int PyMain(int argc, IntPtr argv);

    const uint LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008;

    static int Fail(string message)
    {
        // MB_ICONERROR | MB_RTLREADING | MB_RIGHT
        MessageBox(IntPtr.Zero, message, "ترجِم", 0x10 | 0x100000 | 0x80000);
        return 1;
    }

    static int Main(string[] args)
    {
        string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string laptop = Path.Combine(root, "laptop");
        string venv = Path.Combine(laptop, ".venv");
        string cfg = Path.Combine(venv, "pyvenv.cfg");
        if (!File.Exists(cfg))
            return Fail("ملفات البرنامج ناقصة (laptop\\.venv). لازم Tarjim.exe يبقى داخل مجلد tarjim.");

        string home = null;
        foreach (string line in File.ReadAllLines(cfg))
        {
            int eq = line.IndexOf('=');
            if (eq > 0 && line.Substring(0, eq).Trim() == "home")
                home = line.Substring(eq + 1).Trim();
        }
        if (home == null || !Directory.Exists(home))
            return Fail("ما لقيت بايثون اللي يشتغل عليه البرنامج:\n" + (home ?? "?"));

        string dll = null;
        foreach (string candidate in Directory.GetFiles(home, "python3*.dll"))
        {
            string name = Path.GetFileName(candidate).ToLowerInvariant();
            if (name != "python3.dll") dll = candidate;  // python312.dll, not the stable-ABI stub
        }
        if (dll == null)
            return Fail("ما لقيت ملف بايثون داخل:\n" + home);

        // The same variable Python's own venv launcher sets: use the environment's packages.
        Environment.SetEnvironmentVariable("__PYVENV_LAUNCHER__", Path.Combine(venv, "Scripts", "pythonw.exe"));
        Environment.SetEnvironmentVariable("PYTHONUTF8", "1");
        Environment.SetEnvironmentVariable("TARJIM_HOST", Assembly.GetExecutingAssembly().Location);
        Directory.SetCurrentDirectory(laptop);

        IntPtr module = LoadLibraryEx(dll, IntPtr.Zero, LOAD_WITH_ALTERED_SEARCH_PATH);
        if (module == IntPtr.Zero)
            return Fail("ما قدرت أحمّل بايثون:\n" + dll);
        IntPtr entry = GetProcAddress(module, "Py_Main");
        if (entry == IntPtr.Zero)
            return Fail("ملف بايثون ما فيه Py_Main:\n" + dll);

        string[] argv = new string[3 + args.Length];
        argv[0] = Assembly.GetExecutingAssembly().Location;
        argv[1] = "-m";
        argv[2] = "tarjim.app";
        Array.Copy(args, 0, argv, 3, args.Length);

        IntPtr block = Marshal.AllocHGlobal(IntPtr.Size * (argv.Length + 1));
        for (int i = 0; i < argv.Length; i++)
            Marshal.WriteIntPtr(block, i * IntPtr.Size, Marshal.StringToHGlobalUni(argv[i]));
        Marshal.WriteIntPtr(block, argv.Length * IntPtr.Size, IntPtr.Zero);

        PyMain main = (PyMain)Marshal.GetDelegateForFunctionPointer(entry, typeof(PyMain));
        return main(argv.Length, block);
    }
}
