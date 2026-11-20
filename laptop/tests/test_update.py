"""Small updates (update.py), against a stand-in for GitHub: nothing leaves the computer."""

import hashlib
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tarjim import update


def make_zip(path, version="9.9.9", extra=()):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("laptop/tarjim/__init__.py", f'"""Tarjim."""\n\n__version__ = "{version}"\n')
        zf.writestr("laptop/tarjim/app/web/index.html", "<p>new page</p>")
        zf.writestr("packs/new-game.json", "{}")
        zf.writestr("tv-app/app/build/outputs/apk/debug/app-debug.apk", b"apk")
        for name, data in extra:
            zf.writestr(name, data)
    return path


@pytest.fixture()
def installed(tmp_path):
    """A folder laid out like an installation, with an older version and the user's own files."""
    root = tmp_path / "Tarjim"
    (root / "laptop" / "tarjim" / "app" / "web").mkdir(parents=True)
    (root / "laptop" / "tarjim" / "__init__.py").write_text('__version__ = "0.5.0"\n', encoding="utf-8")
    (root / "laptop" / "tarjim" / "dropped_in_new_version.py").write_text("x = 1\n", encoding="utf-8")
    (root / "laptop" / "tarjim" / "app" / "web" / "index.html").write_text("<p>old page</p>", encoding="utf-8")
    (root / "profiles").mkdir()
    (root / "profiles" / "gta6.yaml").write_text("title: mine\n", encoding="utf-8")
    (root / "models").mkdir()
    (root / "models" / "model.bin").write_bytes(b"big")
    (root / "packs").mkdir()
    (root / "packs" / "old-game.json").write_text("{}", encoding="utf-8")
    return root


def test_an_update_replaces_the_program_and_leaves_the_users_files(installed, tmp_path):
    archive = make_zip(tmp_path / "u.zip")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert update.apply_zip(archive, installed, sha256=sha) == "9.9.9"
    code = installed / "laptop" / "tarjim"
    assert '"9.9.9"' in (code / "__init__.py").read_text(encoding="utf-8")
    assert (code / "app" / "web" / "index.html").read_text(encoding="utf-8") == "<p>new page</p>"
    assert not (code / "dropped_in_new_version.py").exists()  # a file the new version no longer has
    assert (installed / "packs" / "new-game.json").exists() and (installed / "packs" / "old-game.json").exists()
    assert (installed / "tv-app/app/build/outputs/apk/debug/app-debug.apk").read_bytes() == b"apk"
    # the user's game profile and the model are untouched, and nothing is left behind
    assert (installed / "profiles" / "gta6.yaml").read_text(encoding="utf-8") == "title: mine\n"
    assert (installed / "models" / "model.bin").read_bytes() == b"big"
    assert not (installed / ".update").exists()


@pytest.mark.parametrize("name", [
    "../outside.txt", "laptop/tarjim/../../outside.txt", "profiles/gta6.yaml", "models/model.bin",
    "Tarjim.exe", "/abs.txt", "laptop\\tarjim\\x.py", "C:/x.py", "python/python.exe",
])
def test_a_zip_with_anything_else_in_it_is_refused_whole(installed, tmp_path, name):
    archive = make_zip(tmp_path / "u.zip", extra=[(name, "bad")])
    with pytest.raises(update.UpdateError):
        update.apply_zip(archive, installed)
    assert '"0.5.0"' in (installed / "laptop" / "tarjim" / "__init__.py").read_text(encoding="utf-8")  # nothing written
    assert (installed / "profiles" / "gta6.yaml").read_text(encoding="utf-8") == "title: mine\n"
    assert not (tmp_path / "outside.txt").exists()


def test_a_changed_or_broken_download_is_refused(installed, tmp_path):
    archive = make_zip(tmp_path / "u.zip")
    with pytest.raises(update.UpdateError):
        update.apply_zip(archive, installed, sha256="0" * 64)
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    with pytest.raises(update.UpdateError):
        update.apply_zip(tmp_path / "junk.zip", installed)
    assert (installed / "laptop" / "tarjim" / "dropped_in_new_version.py").exists()


def test_a_developer_checkout_is_never_overwritten(installed, tmp_path):
    (installed / ".git").mkdir()
    with pytest.raises(update.UpdateError):
        update.apply_zip(make_zip(tmp_path / "u.zip"), installed)


def test_versions_compare_by_their_numbers():
    key = update.version_key
    assert key("v0.5.10-build7") > key("0.5.9") and key("0.5.2") == key("v0.5.2-build3") and key("1.0") > key("0.99.9")


# -- asking GitHub ------------------------------------------------------------------------------


class Hub:
    """A stand-in for GitHub: the list of releases, and the files of the newest one."""

    def __init__(self, tmp_path, version, runtime):
        self.archive = make_zip(tmp_path / f"Tarjim-Update-{version}.zip", version)
        data = self.archive.read_bytes()
        self.manifest = {"version": version, "runtime": runtime, "zip": self.archive.name,
                         "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        self.asked = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                outer.asked.append(self.path)
                if self.path.startswith("/api"):
                    body = json.dumps(outer.releases()).encode()
                elif self.path.endswith("update.json"):
                    body = json.dumps(outer.manifest).encode()
                else:
                    body = data
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.version = version

    def releases(self):
        dl = f"{self.base}/dl/"
        return [
            {"tag_name": "v0.0.1-build1", "html_url": "old", "assets": [{"name": "Tarjim-Setup-0.0.1.exe"}]},
            {"tag_name": f"v{self.version}-build9", "html_url": f"{self.base}/page", "assets": [
                {"name": "update.json", "browser_download_url": dl + "update.json"},
                {"name": self.archive.name, "browser_download_url": dl + self.archive.name},
            ]},
        ]


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    def make(version, runtime=update.LEGACY_RUNTIME):
        h = Hub(tmp_path, version, runtime)
        monkeypatch.setattr(update, "API", h.base + "/api")
        monkeypatch.setattr(update, "DOWNLOADS", h.base + "/dl/")
        made.append(h)
        return h

    made = []
    yield make
    for h in made:
        h.httpd.shutdown()
        h.httpd.server_close()


def test_a_newer_version_is_found_downloaded_and_put_in_place(hub, installed, monkeypatch):
    h = hub("99.0.0")
    monkeypatch.setattr(update, "installed_runtime", lambda root=None: update.LEGACY_RUNTIME)
    info = update.check()
    assert info["available"] and info["latest"] == "99.0.0" and not info["needs_installer"]
    assert info["size"] == h.manifest["size"]
    assert update.apply(info, installed) == "99.0.0"
    assert '"99.0.0"' in (installed / "laptop" / "tarjim" / "__init__.py").read_text(encoding="utf-8")


def test_the_same_or_an_older_version_is_not_an_update(hub):
    hub(update.__version__)
    assert update.check()["available"] is False
    hub("0.0.2")
    assert update.check()["available"] is False


def test_an_update_for_other_libraries_points_to_the_installer(hub, installed, monkeypatch):
    hub("99.0.0", runtime="some-other-libraries")
    monkeypatch.setattr(update, "installed_runtime", lambda root=None: update.LEGACY_RUNTIME)
    info = update.check()
    assert info["available"] and info["needs_installer"] and info["page"].endswith("/page")
    with pytest.raises(update.UpdateError):
        update.apply(info, installed)
    assert '"0.5.0"' in (installed / "laptop" / "tarjim" / "__init__.py").read_text(encoding="utf-8")


def test_files_are_taken_only_from_the_programs_own_release_page(hub, installed, monkeypatch):
    hub("99.0.0")
    info = update.check()
    monkeypatch.setattr(update, "DOWNLOADS", "https://github.com/iabdullahMah/tarjim/releases/download/")
    with pytest.raises(update.UpdateError):
        update.apply(info, installed)  # its address is not under the release page any more
    with pytest.raises(update.UpdateError):
        update.check()


def test_no_connection_is_an_arabic_sentence_not_a_crash(monkeypatch):
    monkeypatch.setattr(update, "API", "http://127.0.0.1:9/api")
    with pytest.raises(update.UpdateError) as err:
        update.check(timeout=2)
    assert "الإنترنت" in str(err.value)


def test_the_installed_libraries_are_named_by_a_file_or_taken_as_the_first_ones(tmp_path):
    assert update.installed_runtime(tmp_path) == update.LEGACY_RUNTIME
    (tmp_path / "runtime.json").write_text('{"runtime": "abc123"}', encoding="utf-8")
    assert update.installed_runtime(tmp_path) == "abc123"


def test_the_update_built_here_passes_its_own_rules(tmp_path, monkeypatch):
    """The zip the build makes holds only names an update may hold, and installs."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "build_update", Path(__file__).resolve().parents[1] / "launcher" / "build_update.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DIST", tmp_path)
    archive = mod.build()
    manifest = json.loads((tmp_path / "update.json").read_text(encoding="utf-8"))
    assert manifest["version"] == update.__version__ and manifest["runtime"] == mod.runtime_id()
    assert manifest["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    root = tmp_path / "Tarjim"
    (root / "laptop" / "tarjim").mkdir(parents=True)
    assert update.apply_zip(archive, root, sha256=manifest["sha256"]) == update.__version__
    assert (root / "laptop" / "tarjim" / "update.py").exists() and (root / "packs" / "gta6.json").exists()
    assert not list((root / "laptop").rglob("__pycache__"))


def test_the_download_reports_its_progress(hub, installed, monkeypatch):
    h = hub("99.0.0")
    monkeypatch.setattr(update, "installed_runtime", lambda root=None: update.LEGACY_RUNTIME)
    seen = []
    assert update.apply(update.check(), installed, progress=lambda done, total: seen.append((done, total))) == "99.0.0"
    assert seen and seen[-1] == (h.manifest["size"], h.manifest["size"])
    assert all(a[0] <= b[0] for a, b in zip(seen, seen[1:]))


def test_the_app_updates_in_the_background_and_tells_the_page(hub, installed, monkeypatch):
    """The page is told the share downloaded, then that it is done. Started from a console
    (no Tarjim.exe to start again) the program stays open and says to reopen it by hand."""
    import time

    from tarjim import paths
    from tarjim.app import server

    hub("99.0.0")
    monkeypatch.setattr(update, "installed_runtime", lambda root=None: update.LEGACY_RUNTIME)
    monkeypatch.setattr(paths, "REPO", installed)
    monkeypatch.delenv("TARJIM_HOST", raising=False)
    monkeypatch.setattr(server, "_update_seen", {"at": 0.0, "info": None})
    quit_calls = []
    monkeypatch.setattr(server, "_quit_now", lambda: quit_calls.append(1))
    q = server.hub.subscribe()
    try:
        assert server.update_view(fresh=True)["available"] is True
        assert server.update_apply() == {"ok": True, "started": True}
        events = []
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not (events and events[-1]["stage"] in ("done", "error")):
            try:
                kind, data = q.get(timeout=0.5)
            except Exception:
                continue
            if kind == "update":
                events.append(data)
    finally:
        server.hub.unsubscribe(q)
    assert events[0] == {"stage": "download", "pct": 0}
    assert events[-1] == {"stage": "done", "pct": 100, "version": "99.0.0", "restart": False}
    assert [e["pct"] for e in events] == sorted(e["pct"] for e in events)
    assert '"99.0.0"' in (installed / "laptop" / "tarjim" / "__init__.py").read_text(encoding="utf-8")
    assert quit_calls == []  # nothing to start again with: it does not close itself
    for _ in range(50):  # the worker lets go of its lock
        if not server._update_busy.locked():
            break
        time.sleep(0.05)
    assert not server._update_busy.locked()


def test_the_program_restarts_itself_only_through_its_own_executable(tmp_path, monkeypatch):
    from tarjim.app import server

    monkeypatch.delenv("TARJIM_HOST", raising=False)
    assert server._restart_command() is None
    monkeypatch.setenv("TARJIM_HOST", str(tmp_path / "gone.exe"))
    assert server._restart_command() is None
    exe = tmp_path / "Tarjim.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("TARJIM_HOST", str(exe))
    assert server._restart_command() == [str(exe), "--wait"]


def test_an_update_is_not_started_while_translating(monkeypatch):
    from tarjim.app import server

    monkeypatch.setattr(server.live, "state", "running")
    assert server.update_apply()["ok"] is False
