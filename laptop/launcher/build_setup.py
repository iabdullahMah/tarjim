"""Build the installer: dist/Tarjim-Setup-<version>.exe

One self-contained file: the setup program (Setup.cs) with everything the
program needs appended as a zip archive, laid out exactly like this repo so
the code finds its files unchanged:

    Tarjim.exe
    python/                 the Python runtime (relocatable build)
    laptop/tarjim/          the program
    laptop/.venv/           its libraries (pyvenv.cfg is written by the installer)
    models/opus-...         the translation model
    fonts/  profiles/       fonts and the game profiles
    packs/                  the game library (names for games without Arabic)
    .tools/platform-tools/  adb, for setting up the TV
    tv-app/.../app-debug.apk, phone-app/.../app-debug.apk

    python launcher/build_setup.py [--fast]     (--fast: store without compressing, for testing)
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAPTOP = HERE.parent
REPO = LAPTOP.parent
DIST = REPO / "dist"
sys.path.insert(0, str(HERE))
from build_update import runtime_id, version  # noqa: E402

VERSION = version()  # written once, in tarjim/__init__.py
MAGIC = b"TARJIMZ1"

SKIP_DIRS = {"__pycache__", "_pytest", "pytest", "test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip",
             "pip", "tcl", "include", "libs"}
SKIP_SUFFIXES = (".pyc", ".chm", ".pdb")
SKIP_FILES = {"_editable_impl_tarjim.pth"}  # points at this development folder


def python_home() -> Path:
    for line in (LAPTOP / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "home":
            return Path(value.strip()).resolve()
    raise RuntimeError("no Python home in pyvenv.cfg")


def tree(src: Path, dst: str, skip_dirs: set[str] = frozenset()):
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(src).parts
        if any(p in skip_dirs for p in parts[:-1]) or path.name in SKIP_FILES or path.suffix in SKIP_SUFFIXES:
            continue
        yield path, f"{dst}/{'/'.join(parts)}"


# The Visual C++ runtime, put next to the program (Microsoft allows this "app-local" copy).
# ctranslate2 (the translator) and onnxruntime (the reader) need msvcp140.dll and
# msvcp140_1.dll, which a freshly installed Windows does not have: without them the
# translation failed on a second PC while it worked on this one.
VC_RUNTIME = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
              "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll")
VC_PLACES = ("", "python/", "laptop/.venv/Lib/site-packages/ctranslate2/",
             "laptop/.venv/Lib/site-packages/onnxruntime/capi/")


def vc_runtime():
    system32 = Path(r"C:\Windows\System32")
    # where each place's own files come from: a file already there is not added twice
    origin = {"": REPO, "python/": python_home(),
              "laptop/.venv/Lib/site-packages/ctranslate2/": LAPTOP / ".venv" / "Lib" / "site-packages" / "ctranslate2",
              "laptop/.venv/Lib/site-packages/onnxruntime/capi/": LAPTOP / ".venv" / "Lib" / "site-packages" / "onnxruntime" / "capi"}
    for name in VC_RUNTIME:
        src = system32 / name
        if not src.exists():
            raise RuntimeError(f"{src} is missing: install the Visual C++ 2015-2022 Redistributable (x64)")
        for place in VC_PLACES:
            if not (origin[place] / name).exists():
                yield src, place + name


def payload():
    exe = REPO / "Tarjim.exe"
    if not exe.exists():
        raise RuntimeError("Tarjim.exe is missing: run launcher/build.py first")
    yield exe, "Tarjim.exe"
    yield from vc_runtime()
    yield from tree(python_home(), "python", SKIP_DIRS | {"Scripts"})
    yield from tree(LAPTOP / "tarjim", "laptop/tarjim", {"__pycache__"})
    for item in tree(LAPTOP / ".venv" / "Lib" / "site-packages", "laptop/.venv/Lib/site-packages",
                     {"__pycache__", "_pytest", "pytest", "pip"}):
        # onnxruntime's model-conversion tools are never used, and hold the deepest paths
        if not item[1].startswith("laptop/.venv/Lib/site-packages/onnxruntime/tools/"):
            yield item
    # What sys.executable points to. Never started; a real file keeps tools that look at it happy.
    yield python_home() / "pythonw.exe", "laptop/.venv/Scripts/pythonw.exe"
    yield from tree(REPO / "models" / "opus-mt-tc-big-en-ar-ct2", "models/opus-mt-tc-big-en-ar-ct2")
    yield from tree(REPO / "fonts", "fonts")
    yield from tree(REPO / "profiles", "profiles")
    yield from tree(REPO / "packs", "packs", {"source"})  # the game library (its sources stay in the repo)
    yield from tree(REPO / ".tools" / "platform-tools", ".tools/platform-tools")
    for app in ("tv-app", "phone-app"):
        apk = REPO / app / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
        if not apk.exists():
            raise RuntimeError(f"{apk} is missing: build the {app} first")
        yield apk, f"{app}/app/build/outputs/apk/debug/app-debug.apk"
    yield REPO / "docs" / "LICENSES.md", "docs/LICENSES.md"
    # Which Python libraries this installation has: a small update is only applied to an
    # installation whose libraries it was made for (tarjim/update.py).
    DIST.mkdir(exist_ok=True)
    note = DIST / "runtime.json"
    note.write_text(json.dumps({"runtime": runtime_id(), "installer": VERSION}), encoding="utf-8")
    yield note, "runtime.json"


def compile_stub(target: Path) -> None:
    csc = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    web = LAPTOP / "tarjim" / "app" / "web"
    cmd = [
        str(csc), "/nologo", "/target:winexe", "/platform:x64", "/optimize+", "/codepage:65001",
        "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll",
        "/r:System.IO.Compression.dll",
        f"/win32icon:{web / 'icon.ico'}", f"/resource:{web / 'icon.png'},icon.png",
        f"/resource:{REPO / 'fonts' / 'Harmattan-Regular.ttf'},Harmattan-Regular.ttf",
        f"/out:{target}", str(HERE / "Setup.cs"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000)
    if proc.returncode != 0 or not target.exists():
        raise RuntimeError(f"building the setup program failed:\n{proc.stdout}\n{proc.stderr}")


def build(fast: bool = False) -> Path:
    DIST.mkdir(exist_ok=True)
    stub = DIST / "setup-stub.exe"
    compile_stub(stub)
    archive = DIST / "payload.zip"
    t0 = time.time()
    count = raw = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED if fast else zipfile.ZIP_DEFLATED, compresslevel=None if fast else 6) as zf:
        for path, name in payload():
            zf.write(path, name)
            count += 1
            raw += path.stat().st_size
    size = archive.stat().st_size
    out = DIST / f"Tarjim-Setup-{VERSION}.exe"
    with out.open("wb") as fh:
        fh.write(stub.read_bytes())
        with archive.open("rb") as src:
            while chunk := src.read(1 << 22):
                fh.write(chunk)
        fh.write(MAGIC + struct.pack("<q", size))
    archive.unlink()
    (DIST / "runtime.json").unlink(missing_ok=True)
    print(f"{out}  {out.stat().st_size / 1e6:.0f} MB  ({count} files, {raw / 1e6:.0f} MB unpacked, {time.time() - t0:.0f} s)")
    return out


if __name__ == "__main__":
    build(fast="--fast" in sys.argv)
