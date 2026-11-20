import io

import pytest
from PIL import Image

from tarjim import patterns
from tarjim.adb import A11Y_SERVICE, AdbNotAllowed, check_allowed
from tarjim.faketv import FakeTv
from tarjim.tv import _a11y_list
from tarjim.tvlink import TvLink

ALLOWED = [
    ["connect", "192.168.1.50:5555"],
    ["devices"],
    ["install", "-r", r"C:\Users\some user\tarjim\tv-app\app\build\outputs\apk\debug\app-debug.apk"],
    ["uninstall", "dev.tarjim.tv"],
    ["exec-out", "screencap", "-p"],
    ["shell", "getprop", "ro.build.version.sdk"],
    ["shell", "wm", "size"],
    ["shell", "dumpsys", "activity", "services", "dev.tarjim.tv"],
    ["shell", "appops", "set", "dev.tarjim.tv", "SYSTEM_ALERT_WINDOW", "allow"],
    ["shell", "appops", "get", "dev.tarjim.tv", "SYSTEM_ALERT_WINDOW"],
    ["shell", "settings", "get", "secure", "enabled_accessibility_services"],
    ["shell", "settings", "put", "secure", "enabled_accessibility_services", f"'com.other/.Svc:{A11Y_SERVICE}'"],
    ["shell", "settings", "put", "secure", "accessibility_enabled", "1"],
    ["shell", "am", "start-foreground-service", "-n", "dev.tarjim.tv/.OverlayService"],
    ["shell", "am", "stopservice", "-n", "dev.tarjim.tv/.OverlayService"],
]

FORBIDDEN = [
    ["root"],
    ["reboot"],
    ["reboot", "bootloader"],
    ["uninstall", "com.google.android.youtube.tv"],
    ["install", "-r", "evil.apk"],
    ["shell", "pm", "disable-user", "com.tcl.something"],
    ["shell", "pm", "uninstall", "com.tcl.something"],
    ["shell", "wm", "size", "1280x720"],
    ["shell", "wm", "density", "240"],
    ["shell", "settings", "put", "global", "adb_enabled", "0"],
    ["shell", "settings", "put", "secure", "accessibility_enabled", "1; reboot"],
    ["shell", "settings", "put", "secure", "enabled_accessibility_services", "'a'; reboot"],
    ["shell", "appops", "set", "com.other", "SYSTEM_ALERT_WINDOW", "allow"],
    ["shell", "input", "keyevent", "3"],
    ["shell", "getprop", "x;", "reboot"],
    ["shell", "am", "force-stop", "com.other"],
    ["shell", "su"],
]


@pytest.mark.parametrize("args", ALLOWED)
def test_allowed_adb_commands(args):
    check_allowed(args)


@pytest.mark.parametrize("args", FORBIDDEN)
def test_forbidden_adb_commands(args):
    with pytest.raises(AdbNotAllowed):
        check_allowed(args)


def test_a11y_list_parsing():
    assert _a11y_list("null") == []
    assert _a11y_list("") == []
    assert _a11y_list("a/.B:c/.D") == ["a/.B", "c/.D"]


def test_patterns_fit_protocol_limits_and_do_not_overlap():
    for size in [(1920, 1080), (1280, 720), (3840, 2160)]:
        pats = patterns.make_patterns(*size)
        a, b = pats["A"], pats["B"]
        assert a.w <= 1920 and a.h <= 480
        assert a.x + a.w < b.x and b.x + b.w <= size[0]


def test_selfcheck_passes_on_faketv_and_fails_when_missing():
    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        caps = link.connect()
        pats = patterns.make_patterns(caps["w"], caps["h"])
        for p in pats.values():
            assert link.wait_ack(link.show(p.win, [patterns.pattern_layer(p)])) is not None
        shot = tv.composite().convert("RGB")
        # A 4K screencap of a 1080p overlay plane must still pass.
        big = shot.resize((3840, 2160), Image.NEAREST)
        for image in (shot, big):
            result = patterns.check_screencap(image, caps["w"], caps["h"], list(pats.values()))
            assert result["A"]["ok"] and result["B"]["ok"]
        only_a = tv.composite(win="A").convert("RGB")
        result = patterns.check_screencap(only_a, caps["w"], caps["h"], list(pats.values()))
        assert result["A"]["ok"] and not result["B"]["ok"]
        buf = io.BytesIO()
        shot.save(buf, format="PNG")
        (__import__("pathlib").Path(__file__).parent / "out").mkdir(exist_ok=True)
        (__import__("pathlib").Path(__file__).parent / "out" / "patterns.png").write_bytes(buf.getvalue())


def test_a_message_never_fails_on_a_stream_that_cannot_hold_arabic(monkeypatch):
    """On a Windows in another language the program's output stream is "charmap": printing an
    Arabic progress message there used to stop the TV setup."""
    import io
    import sys

    from tarjim import say

    got = []
    say.set_hook(lambda kind, msg: got.append((kind, msg)))
    try:
        for stream in (io.TextIOWrapper(io.BytesIO(), encoding="cp1252"), None):
            monkeypatch.setattr(sys, "stdout", stream)
            monkeypatch.setattr(sys, "stderr", stream)
            say.step("أتصل بالتلفزيون…")
            say.ok("تم")
            say.info("شوف شاشة التلفزيون")
            say.warn("انتبه")
            say.fail("ما قدرت")
    finally:
        say.set_hook(None)
    assert len(got) == 10 and got[0] == ("step", "أتصل بالتلفزيون…")
