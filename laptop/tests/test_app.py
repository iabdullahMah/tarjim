import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import tarjim.vision.arabic  # noqa: F401  (resolve the font folder before any test moves paths.REPO)
from tarjim import paths
from tarjim.app import server
from tarjim.app.session import Hub, friendly_error


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Profiles and config in a temp folder, so tests never touch the user's files."""
    repo = tmp_path / "repo"
    (repo / "profiles").mkdir(parents=True)
    (repo / "profiles" / "default.yaml").write_text("title: Any game\nglossary: {}\n", encoding="utf-8")
    home = tmp_path / "home"
    monkeypatch.setattr(paths, "HOME_DIR", home)
    monkeypatch.setattr(paths, "CONFIG_FILE", home / "config.json")
    real_repo = paths.REPO
    monkeypatch.setattr(paths, "REPO", repo)
    yield repo, real_repo


def test_profile_roundtrip_and_limits(sandbox):
    server.profile_save({
        "name": "default", "title": "My game", "font": "noto-naskh", "font_size": 500, "background": "none",
        "read_scale": 9, "settle_ms": 280,
        "glossary": [{"en": "Morgan", "ar": "مورغان"}, {"en": " ", "ar": "x"}, {"en": "Dutch", "ar": ""}],
    })
    view = server.profile_view("default")
    assert view["title"] == "My game" and view["font"] == "noto-naskh"
    assert view["font_size"] == 90 and view["font_auto"] is False  # clamped
    assert view["read_scale"] == 2.5 and view["background"] == "none" and view["settle_ms"] == 280
    assert view["glossary"] == [{"en": "Morgan", "ar": "مورغان", "f": False}]  # incomplete rows are dropped

    server.profile_save({"name": "default", "font": "not-a-font", "font_size": None})
    view = server.profile_view("default")
    assert view["font"] == "noto-naskh"  # an unknown font is ignored
    assert view["font_auto"] is True and view["font_size"] is None  # back to automatic


def test_screen_text_switch_is_for_gta6_only_and_off_by_default(sandbox):
    server.profile_save({"name": "gta6", "title": "Grand Theft Auto VI"})
    assert server.profile_view("gta6")["screen_text"] is False  # off until switched on
    server.profile_save({"name": "gta6", "screen_text": True})
    assert server.profile_view("gta6")["screen_text"] is True
    server.profile_save({"name": "rdr2", "title": "Red Dead Redemption 2"})
    assert server.profile_view("rdr2")["screen_text"] is None  # no switch for other games


def test_profile_names_cannot_escape_the_folder(sandbox):
    from tarjim.translate import TranslateError, save_profile

    for bad in ("../evil", "a/b", "A B", "", "x" * 60):
        with pytest.raises(TranslateError):
            save_profile(bad, {})


def test_preview_renders_every_font_and_look(sandbox, monkeypatch):
    repo, real_repo = sandbox
    monkeypatch.setattr(paths, "REPO", real_repo)  # the renderer needs the real fonts
    server._preview_cache.clear()
    seen = set()
    looks = [{"font": f} for f in ("harmattan", "harmattan-bold", "noto-naskh", "noto-sans-arabic")]
    looks += [
        {"background": "plate"}, {"background": "plate", "plate_opacity": 0.9}, {"background": "none"},
        {"effect": "outline"}, {"effect": "none"}, {"text_color": "#ffe14d"}, {"y_offset": -60}, {"font_size": 60},
    ]
    for look in looks:
        png = server.preview_png(look, "تجربة الخط 123")
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000
        seen.add(png)
    assert len(seen) == len(looks)  # every option really changes the picture


def test_look_settings_are_validated(sandbox):
    from tarjim import settings

    server.profile_save({"name": "default", "background": "plate", "plate_opacity": 5, "text_color": "#FFE14D",
                         "effect": "outline", "y_offset": -9999})
    v = server.profile_view("default")
    assert (v["background"], v["plate_opacity"], v["text_color"], v["effect"], v["y_offset"]) == (
        "plate", 0.95, "#ffe14d", "outline", -300)
    server.profile_save({"name": "default", "background": "sideways", "text_color": "auto", "effect": "sparkles"})
    v = server.profile_view("default")
    assert (v["background"], v["text_color"], v["effect"]) == ("plate", "auto", "outline")  # bad values ignored
    assert settings.parse_color("#ff0000") == (0, 0, 255) and settings.parse_color("red") is None
    assert settings.background({"fill": False}) == "none" and settings.background({}) == "cover"  # old profiles


def test_errors_become_plain_arabic():
    assert "تلفزيون" in friendly_error(RuntimeError("cannot connect to 192.168.1.5:5555"))
    assert "وضع الراحة" in friendly_error(RuntimeError("the console is in rest mode"))
    assert "الدليل" in friendly_error(RuntimeError("this laptop is not linked to the console; run: tarjim source setup"))
    assert friendly_error(RuntimeError("شي ثاني")) == "شي ثاني"


def test_live_picture_is_as_wide_as_the_page_asks_but_never_enlarged():
    import cv2
    import numpy as np

    from tarjim.app.session import LiveSession

    live = LiveSession(Hub())
    assert live.frame_jpeg(1920) is None  # not running

    class Pipe:
        _latest = np.full((1080, 1920, 3), 90, np.uint8)

    live._pipe = Pipe()

    def width(asked):
        return cv2.imdecode(np.frombuffer(live.frame_jpeg(asked), np.uint8), cv2.IMREAD_COLOR).shape[1]

    assert width(960) == 960  # older pages
    assert width(1600) == 1600  # the window's real pixels
    assert width(3840) == 1920  # a 4K screen: the PS5's own picture, not enlarged
    assert width(10) == 320


def test_hub_fans_out_and_drops_when_a_window_is_stuck():
    hub = Hub()
    a, b = hub.subscribe(), hub.subscribe()
    hub.publish("line", {"n": 1})
    assert a.get_nowait() == ("line", {"n": 1}) and b.get_nowait() == ("line", {"n": 1})
    for i in range(1000):  # nobody reads: must not block or raise
        hub.publish("line", {"n": i})
    hub.unsubscribe(a)
    assert hub.clients == 1


def test_http_is_local_only_and_serves_no_files_outside_web(sandbox, monkeypatch):
    repo, real_repo = sandbox
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    monkeypatch.setattr(server, "PORT", port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def get(path, headers=None):
        req = urllib.request.Request(base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    try:
        status, body = get("/")
        assert status == 200 and "ترجِم".encode("utf-8") in body
        assert get("/app.js")[0] == 200 and get("/app.css")[0] == 200
        assert get("/..%2fserver.py")[0] == 404 and get("/../session.py")[0] == 404
        state = json.loads(get("/api/state")[1])
        assert state["live"]["state"] == "idle" and state["profiles"][0]["name"] == "default"
        # A web page on another site must not be able to drive the app.
        assert get("/api/state", {"Origin": "http://evil.example"})[0] == 403
        assert get("/api/state", {"Host": "evil.example"})[0] == 403
        req = urllib.request.Request(base + "/api/tv/setup", data=b'{"ip":"not an ip"}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 400
    finally:
        httpd.shutdown()


def test_the_page_is_told_which_version_is_running(sandbox):
    from tarjim import __version__

    assert server._version() == __version__ and __version__.count(".") == 2
    page = (server.WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="ver"' in page  # shown beside the name on every page
