"""Build ``Perspective Correction.exe``: a double-click starter with the logo.

User, 2026-10-23: "will eine start datei mit logo. bat nervt" -- the .bat flashes
a console and a shortcut to it wears a generic icon. This writes a tiny Windows
program (C#, compiled by the csc.exe that ships with every Windows .NET
Framework 4) that starts ``pythonw perspective_correction.py --gui`` from its own folder: no
console window, the mark as its icon, and photographs dropped onto it open in
the review window.

It is a starter, not a bundle: the Python installation it finds (pythonw on
PATH, else the py launcher's pyw) runs the code in this folder, so it stays
in step with every ``git pull``. Run once after cloning:

    python tools/make_launcher.py

The .exe and .ico are build output and not tracked.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "Perspective Correction.exe")
ICO = os.path.join(ROOT, "tools", "launcher.ico")
CSC = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                   "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe")

SOURCE = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Perspective Correction")]

static class Launcher {
    [STAThread]
    static int Main(string[] args) {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(dir, "perspective_correction.py");
        if (!File.Exists(script)) {
            MessageBox.Show("perspective_correction.py is not next to this program:\n" + dir,
                            "Perspective Correction", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
        string rest = string.Join(" ", args.Select(a => "\"" + a + "\""));
        string cmdline = "\"" + script + "\" --gui " + rest;
        foreach (string exe in new[] { "pythonw.exe", "pyw.exe" }) {
            try {
                var psi = new ProcessStartInfo(exe, cmdline);
                psi.WorkingDirectory = dir;
                psi.UseShellExecute = false;
                Process.Start(psi);
                return 0;
            } catch (System.ComponentModel.Win32Exception) {
                // not on PATH -- try the next one
            }
        }
        MessageBox.Show("No Python found (pythonw.exe or pyw.exe on PATH).\n" +
                        "Install Python 3.9+ from python.org with \"Add to PATH\".",
                        "Perspective Correction", MessageBoxButtons.OK, MessageBoxIcon.Error);
        return 1;
    }
}
'''


def make_icon(path):
    """The window's own mark (`gui._beholder_pil`), at every size Windows asks for."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from pc.gui import _beholder_pil
    big = _beholder_pil(256)
    big.save(path, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main() -> int:
    if os.name != "nt":
        print("The launcher is a Windows program; elsewhere run: python perspective_correction.py --gui")
        return 2
    if not os.path.exists(CSC):
        print(f"csc.exe not found at {CSC} (.NET Framework 4 is part of Windows 10/11).")
        return 2
    make_icon(ICO)
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "launcher.cs")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(SOURCE)
        cmd = [CSC, "/nologo", "/target:winexe", "/optimize+",
               f"/win32icon:{ICO}", f"/out:{EXE}",
               "/reference:System.Windows.Forms.dll", "/reference:System.Core.dll", src]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout, r.stderr)
            return r.returncode
    print(f"wrote {EXE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
