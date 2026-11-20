"""Build the small update: dist/Tarjim-Update-<version>.zip and dist/update.json

    python launcher/build_update.py

The zip holds only what changes between versions (the program's code and pages, the game
library, the TV and phone apps), laid out like the installation folder, so the running
program can put it in place (tarjim/update.py) and it can also simply be extracted over an
installation. `update.json` names the zip, its SHA-256 and the id of the Python libraries it
was made for.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAPTOP = HERE.parent
REPO = LAPTOP.parent
DIST = REPO / "dist"


def version() -> str:
    """The one place the version is written: tarjim/__init__.py."""
    for line in (LAPTOP / "tarjim" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("no __version__ in tarjim/__init__.py")


def runtime_id() -> str:
    """A short name for the Python libraries an installer carries: the hash of the lock file
    (line endings aside: Windows checks it out with other ones) and the Python version."""
    lock = (LAPTOP / "uv.lock").read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(b"python-3.12\n" + lock).hexdigest()[:16]


def files():
    for path in sorted((LAPTOP / "tarjim").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            yield path, "laptop/tarjim/" + path.relative_to(LAPTOP / "tarjim").as_posix()
    for path in sorted((REPO / "packs").rglob("*")):
        rel = path.relative_to(REPO / "packs")
        if path.is_file() and rel.parts[0] != "source":
            yield path, "packs/" + rel.as_posix()
    for app in ("tv-app", "phone-app"):
        name = f"{app}/app/build/outputs/apk/debug/app-debug.apk"
        if (REPO / name).exists():
            yield REPO / name, name
    yield REPO / "docs" / "LICENSES.md", "docs/LICENSES.md"


def build() -> Path:
    DIST.mkdir(exist_ok=True)
    v = version()
    out = DIST / f"Tarjim-Update-{v}.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path, name in files():
            zf.write(path, name)
            count += 1
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    manifest = {"version": v, "runtime": runtime_id(), "zip": out.name, "sha256": sha, "size": out.stat().st_size}
    (DIST / "update.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{out}  {out.stat().st_size / 1e6:.1f} MB  ({count} files)  runtime {manifest['runtime']}")
    return out


if __name__ == "__main__":
    if "--runtime" in sys.argv:
        print(runtime_id())
    else:
        build()
