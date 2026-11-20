"""Small updates: fetch only what changed, not the whole installer.

The installer is about 700 MB because it carries Python, its libraries and the translation
model, and those almost never change. What does change is the program's own code, its pages
and the game library: a few megabytes. Every build publishes them as `Tarjim-Update-<v>.zip`
beside the installer, with `update.json` describing it. The running program

  - asks GitHub for the newest release of its own repository (`check`),
  - downloads that zip, proves it is the published one (SHA-256) and holds only the files an
    update may hold, and puts them in place (`apply`). The user then starts the program again.

An update only fits an installation with the same Python libraries. Each build names its
libraries by a short id (the hash of the lock file); an update made for other libraries is
not applied, and the user is pointed to the full installer for that one time.

Nothing here touches the user's own files: game profiles, the cache with their line fixes and
stored translations, and the settings all stay as they are.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import __version__, paths

REPO = "iabdullahMah/tarjim"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=15"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"  # the only place files are taken from
MANIFEST = "update.json"
MAX_ZIP = 150 * 1024 * 1024
# The libraries of the installers made before `runtime.json` existed (0.5.0 and 0.5.1).
LEGACY_RUNTIME = "5d9cdbc1cf0b51e1"
# What an update may write, relative to the installation folder. Anything else in the zip
# makes the whole update refused.
ALLOWED_DIRS = ("laptop/tarjim/", "packs/")
ALLOWED_FILES = (
    "tv-app/app/build/outputs/apk/debug/app-debug.apk",
    "phone-app/app/build/outputs/apk/debug/app-debug.apk",
    "docs/LICENSES.md",
)
NEEDED = "laptop/tarjim/__init__.py"


class UpdateError(Exception):
    """An update that could not be checked or applied; the message is Arabic, for the user."""


def version_key(text: str) -> tuple[int, ...]:
    """"0.5.2" or "v0.5.2-build3" -> (0, 5, 2): what versions are compared by."""
    core = str(text).strip().lstrip("vV").split("-")[0]
    out = []
    for part in core.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def installed_runtime(root: Path | None = None) -> str:
    try:
        data = json.loads(((root or paths.REPO) / "runtime.json").read_text(encoding="utf-8"))
        return str(data.get("runtime") or LEGACY_RUNTIME)
    except (OSError, ValueError):
        return LEGACY_RUNTIME


def _get(url: str, timeout: float, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Tarjim", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = res.read(limit + 1)
    except (OSError, ValueError) as exc:
        raise UpdateError("ما قدرت أوصل لصفحة التحديثات. تأكد من الإنترنت.") from exc
    if len(data) > limit:
        raise UpdateError("ملف التحديث أكبر من المتوقع، ما نزّلته.")
    return data


def check(timeout: float = 8.0) -> dict:
    """Is there a newer version? {"current", "latest", "available", "needs_installer", ...}."""
    out = {"current": __version__, "latest": __version__, "available": False, "needs_installer": False}
    try:
        releases = json.loads(_get(API, timeout, 2_000_000).decode("utf-8"))
    except ValueError as exc:
        raise UpdateError("صفحة التحديثات ردّت بشي ما ينقرأ.") from exc
    best = None
    for rel in releases if isinstance(releases, list) else []:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        assets = {a.get("name"): a for a in rel.get("assets") or [] if isinstance(a, dict)}
        if MANIFEST not in assets:
            continue  # an installer published before small updates existed
        key = version_key(rel.get("tag_name", ""))
        if best is None or key > best[0]:
            best = (key, rel, assets)
    if best is None or best[0] <= version_key(__version__):
        return out
    _key, rel, assets = best
    url = str(assets[MANIFEST].get("browser_download_url") or "")
    if not url.startswith(DOWNLOADS):
        raise UpdateError("عنوان التحديث مو من صفحة ترجِم، تجاهلته.")
    try:
        manifest = json.loads(_get(url, timeout, 100_000).decode("utf-8"))
        name, sha, size = str(manifest["zip"]), str(manifest["sha256"]).lower(), int(manifest["size"])
        latest, runtime = str(manifest["version"]), str(manifest["runtime"])
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError("وصف التحديث ما ينقرأ.") from exc
    zip_url = str((assets.get(name) or {}).get("browser_download_url") or "")
    if not zip_url.startswith(DOWNLOADS) or version_key(latest) <= version_key(__version__):
        return out
    out.update(
        latest=latest, available=True, size=size, sha256=sha, url=zip_url, page=str(rel.get("html_url") or ""),
        needs_installer=runtime != installed_runtime(),
    )
    return out


# -- applying ---------------------------------------------------------------------------------


def _allowed(name: str) -> bool:
    if name.startswith("/") or "\\" in name or ":" in name or ".." in name.split("/"):
        return False
    return name in ALLOWED_FILES or any(name.startswith(d) for d in ALLOWED_DIRS)


def apply_zip(archive: Path, root: Path | None = None, sha256: str | None = None) -> str:
    """Put an update's files in place under `root`; the version it brought.

    The zip is checked whole before anything is written: its hash when one is given, and
    every name in it. The program's own folder is swapped in one move where Windows allows
    it (so files an older version had and this one dropped are gone); otherwise the new files
    are copied over the old ones."""
    root = Path(root or paths.REPO)
    if (root / ".git").exists():
        raise UpdateError("هذي نسخة المطوّر (مجلد git)، تتحدّث بـgit مو من هنا.")
    archive = Path(archive)
    if sha256:
        h = hashlib.sha256()
        with archive.open("rb") as fh:
            while chunk := fh.read(1 << 20):
                h.update(chunk)
        if h.hexdigest().lower() != sha256.lower():
            raise UpdateError("ملف التحديث نزل ناقص أو متغيّر. جرّب مرة ثانية.")
    try:
        zf = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError("ملف التحديث خربان.") from exc
    with zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        bad = [n for n in names if not _allowed(n)]
        if bad or NEEDED not in names:
            raise UpdateError("ملف التحديث فيه شي ما ينتمي للبرنامج، ما ركّبته.")
        stage = root / ".update" / f"new-{int(time.time())}"
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        try:
            for name in names:
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            version = _version_in(stage / NEEDED)
            _place(stage, root)
        finally:
            shutil.rmtree(root / ".update", ignore_errors=True)
    return version


def _version_in(init_file: Path) -> str:
    for line in init_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise UpdateError("ملف التحديث ما فيه رقم نسخة.")


def _place(stage: Path, root: Path) -> None:
    new_code, code = stage / "laptop" / "tarjim", root / "laptop" / "tarjim"
    old = root / ".update" / "old-tarjim"
    swapped = False
    if code.exists():
        try:
            os.replace(code, old)
            try:
                os.replace(new_code, code)
                swapped = True
            except OSError:
                os.replace(old, code)  # put the running version back, then copy over it instead
        except OSError:
            pass  # a file inside is held open: copy over it instead
    if not swapped:
        shutil.copytree(new_code, code, dirs_exist_ok=True)
    if (stage / "packs").is_dir():
        shutil.copytree(stage / "packs", root / "packs", dirs_exist_ok=True)
    for name in ALLOWED_FILES:
        src = stage / name
        if src.is_file():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, root / name)


def apply(info: dict, root: Path | None = None, timeout: float = 120.0) -> str:
    """Download the update `check` found and put it in place; the new version."""
    if not info.get("available"):
        raise UpdateError("ما فيه تحديث جديد.")
    if info.get("needs_installer"):
        raise UpdateError("هذا التحديث يغيّر مكتبات البرنامج، ويحتاج ملف التثبيت الكامل مرة وحدة.")
    url = str(info.get("url") or "")
    if not url.startswith(DOWNLOADS):
        raise UpdateError("عنوان التحديث مو من صفحة ترجِم، تجاهلته.")
    data = _get(url, timeout, MAX_ZIP)
    fd, tmp = tempfile.mkstemp(prefix="tarjim-update-", suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        sha = str(info.get("sha256") or "")
        if len(sha) != 64:
            raise UpdateError("وصف التحديث ناقص، ما ركّبته.")
        return apply_zip(Path(tmp), root, sha256=sha)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
