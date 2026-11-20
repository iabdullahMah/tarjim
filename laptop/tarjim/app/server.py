"""Local HTTP server behind the Tarjim window. Listens on 127.0.0.1 only."""

from __future__ import annotations

import io
import json
import mimetypes
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import paths
from ..protocol import DEFAULT_PORT
from . import remote as remote_mod
from . import window as window_mod
from .session import Hub, LiveSession, Tasks, friendly_error

HOST = "127.0.0.1"
PORT = 47900
WEB = Path(__file__).parent / "web"
IDLE_EXIT_S = 40.0  # quit when no window has been open for this long and nothing is running

hub = Hub()
live = LiveSession(hub)
tasks = Tasks(hub)
access = remote_mod.RemoteAccess()
access.on_change = lambda: hub.publish("phone", {})
_shutdown = threading.Event()
_remote_server: ThreadingHTTPServer | None = None
_remote_lock = threading.Lock()

# What a paired phone may not do: these need the laptop itself (a browser, the TV setup, the app's life).
LOCAL_ONLY = ("/api/quit", "/api/focus", "/api/ps5/", "/api/tv/setup", "/api/tv/revert", "/api/tv/test",
              "/api/phone", "/api/guide-seen", "/api/xbox", "/api/console", "/v5/", "/v6/",
              "/api/gemini",  # the key is typed on the laptop only
              "/api/update")

_xbox_auth = None


def xbox_auth():
    """The Xbox sign-in state (made on first use: most users never touch it)."""
    global _xbox_auth
    if _xbox_auth is None:
        from ..sources.xbox import XboxAuth

        _xbox_auth = XboxAuth()
    return _xbox_auth


def xbox_view() -> dict:
    auth = xbox_auth()
    cfg = paths.load_config()
    return {"kind": cfg.get("console", "ps5"), "linked": auth.linked, "state": auth.link_state, "error": auth.link_error,
            "code": auth.user_code if auth.link_state == "waiting" else "", "url": auth.verification_uri,
            "console": cfg.get("xbox") or None}
# What anyone on the home network may fetch without pairing: the page itself and the phone app.
PUBLIC_REMOTE = ("/api/hello", "/api/pair")


# -- helpers --------------------------------------------------------------------


def _port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ps5_status(host: str) -> dict | None:
    from ..sources import ps5

    try:
        device = ps5.device_for(host)
    except Exception:
        return None
    return {
        "name": device.host_name, "on": bool(device.is_on), "status": device.status_name,
        "linked": ps5.linked_users(device),
    }


def checks() -> dict:
    """The quick health check shown on the home screen."""
    cfg = paths.load_config()
    tv_ip = cfg.get("tv_ip")
    ps5_host = cfg.get("ps5_host")
    models = (paths.REPO / "models" / "opus-mt-tc-big-en-ar-ct2" / "model.bin").exists()
    # Probe the three things at the same time and never wait long: a device
    # that is switched off would otherwise hold the whole screen for seconds.
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeout

    pool = ThreadPoolExecutor(max_workers=3)
    f_tv = pool.submit(lambda: bool(tv_ip) and _port_open(tv_ip, 5555))
    f_app = pool.submit(lambda: bool(tv_ip) and _port_open(tv_ip, DEFAULT_PORT))
    f_ps5 = pool.submit(lambda: _ps5_status(ps5_host) if ps5_host else None)

    def result(future, default):
        try:
            return future.result(timeout=3.0)
        except (FutureTimeout, Exception):
            return default

    tv_reach, tv_app, ps5 = result(f_tv, False), result(f_app, False), result(f_ps5, None)
    pool.shutdown(wait=False)
    if cfg.get("console") == "xbox":
        return _xbox_checks(cfg, tv_ip, tv_reach, tv_app, models)
    items = [
        {"id": "power", "ok": not paths.on_battery(), "title": "اللابتوب على الشاحن",
         "hint": "شبّك الشاحن: على البطارية الترجمة أبطأ أربع مرات تقريباً."},
        {"id": "models", "ok": models, "title": "ملفات الترجمة موجودة", "hint": "مجلد models ناقص."},
        {"id": "tv", "ok": tv_reach, "title": "التلفزيون على الشبكة",
         "hint": "شغّل التلفزيون وتأكد إنه على نفس الشبكة." if tv_ip else "التلفزيون ما تجهّز للحين. افتح «الدليل»."},
        {"id": "ps5", "ok": bool(ps5 and ps5["on"]), "title": "الـPS5 شغّال",
         "hint": "شغّل الـPS5 (مو وضع الراحة)." if ps5_host else "الـPS5 ما انربط للحين. افتح «الدليل»."},
        {"id": "link", "ok": bool(ps5 and ps5["linked"]) or bool(cfg.get("ps5_user")), "title": "اللابتوب مربوط بالـPS5",
         "hint": "كمّل خطوة ربط الـPS5 في «الدليل»."},
    ]
    return {
        "items": items, "ready": all(i["ok"] for i in items if i["id"] != "power"),
        "tv_ip": tv_ip, "tv_app": tv_app, "tv_model": (cfg.get("tv") or {}).get("model"),
        "ps5_host": ps5_host, "ps5_name": (ps5 or {}).get("name") or (cfg.get("ps5") or {}).get("name"),
        "ps5_user": cfg.get("ps5_user"), "setup_done": bool(tv_ip and cfg.get("ps5_user")),
    }


def _xbox_checks(cfg: dict, tv_ip, tv_reach: bool, tv_app: bool, models: bool) -> dict:
    """The home screen's health check when the console is an Xbox (no network probe: Xbox answers only when signed in)."""
    linked = xbox_auth().linked
    chosen = cfg.get("xbox") or {}
    items = [
        {"id": "power", "ok": not paths.on_battery(), "title": "اللابتوب على الشاحن",
         "hint": "شبّك الشاحن: على البطارية الترجمة أبطأ أربع مرات تقريباً."},
        {"id": "models", "ok": models, "title": "ملفات الترجمة موجودة", "hint": "مجلد models ناقص."},
        {"id": "tv", "ok": tv_reach, "title": "التلفزيون على الشبكة",
         "hint": "شغّل التلفزيون وتأكد إنه على نفس الشبكة." if tv_ip else "التلفزيون ما تجهّز للحين. افتح «الدليل»."},
        {"id": "ps5", "ok": bool(chosen.get("id")), "title": "الإكس بوكس مختار", "hint": "اختر الإكس بوكس من «الدليل» ‹ «الجهاز»."},
        {"id": "link", "ok": linked, "title": "اللابتوب مربوط بحساب الإكس بوكس", "hint": "اربط حساب مايكروسوفت من «الدليل» ‹ «الجهاز»."},
    ]
    return {
        "items": items, "ready": all(i["ok"] for i in items if i["id"] != "power"), "kind": "xbox",
        "tv_ip": tv_ip, "tv_app": tv_app, "tv_model": (cfg.get("tv") or {}).get("model"),
        "ps5_host": None, "ps5_name": chosen.get("name"), "ps5_user": "xbox" if linked and chosen.get("id") else None,
        "setup_done": bool(tv_ip and linked and chosen.get("id")),
    }


_update_seen: dict = {"at": 0.0, "info": None}


def update_view(fresh: bool = False) -> dict:
    """Is there a newer version? Asked of GitHub at most every ten minutes."""
    from .. import update

    now = time.monotonic()
    if fresh or _update_seen["info"] is None or now - _update_seen["at"] > 600:
        try:
            _update_seen["info"] = update.check()
        except update.UpdateError as exc:
            _update_seen["info"] = {"current": update.__version__, "available": False, "error": str(exc)}
        _update_seen["at"] = now
    info = _update_seen["info"]
    return {k: info.get(k) for k in ("current", "latest", "available", "needs_installer", "size", "page", "error")}


def update_apply() -> dict:
    from .. import update

    if live.state != "idle":
        return {"ok": False, "error": "وقّف الترجمة أول، ثم حدّث."}
    update_view()
    try:
        version = update.apply(_update_seen["info"] or {})
    except update.UpdateError as exc:
        return {"ok": False, "error": str(exc)}
    _update_seen["info"] = None
    return {"ok": True, "version": version}


def gemini_view() -> dict:
    """The stronger translator's settings for the page. The key itself never leaves the laptop:
    the page is told only that there is one, and its last four characters."""
    from .. import gemini
    from ..translate import Cache

    g = gemini.settings()
    cache = Cache()
    try:
        stored = cache.count_better()
    finally:
        cache.close()
    refiner = live.refiner
    return {
        "has_key": bool(g["key"]), "tail": g["key"][-4:] if len(g["key"]) >= 12 else "",
        "enabled": g["enabled"], "model": g["model"], "default_model": gemini.DEFAULT_MODEL,
        "stored": stored, "status": refiner.status() if refiner is not None else None,
    }


def gemini_save(body: dict) -> dict:
    from .. import gemini

    key = body.get("key")
    gemini.save_settings(
        key=str(key) if key is not None else None,
        enabled=bool(body["enabled"]) if "enabled" in body else None,
        model=str(body["model"]) if "model" in body else None,
    )
    live.apply_gemini()  # a running session starts (or stops) using it at once
    return gemini_view()


def gemini_test(body: dict) -> dict:
    """Try the key (the one typed now, else the saved one) with one small line."""
    from .. import gemini

    g = gemini.settings()
    key = str(body.get("key") or "").strip() or g["key"]
    model = str(body.get("model") or "").strip() or g["model"]
    if not key:
        return {"ok": False, "error": "اكتب المفتاح أول."}
    try:
        return {"ok": True, "sample": gemini.check(key, model)}
    except gemini.GeminiError as exc:
        return {"ok": False, "error": gemini.friendly(exc)}


def profile_view(name: str) -> dict:
    from .. import gemini, settings
    from .. import gta6
    from ..translate import load_profile

    prof = load_profile(name)
    cfg = paths.load_config()
    return {
        "name": name,
        "title": prof.get("title") or name,
        "glossary": [{"en": str(k), "ar": str(v), "f": str(k) in {str(n) for n in (prof.get("female") or [])}}
                     for k, v in (prof.get("glossary") or {}).items()],
        "font": prof.get("font", "harmattan"),
        "font_size": prof.get("font_size") or cfg.get("font_sizes", {}).get(name),
        "font_auto": not prof.get("font_size"),
        "background": settings.background(prof),
        "plate_opacity": settings.plate_opacity(prof),
        "text_color": prof.get("text_color") if settings.parse_color(prof.get("text_color")) else "auto",
        "effect": prof.get("effect") if prof.get("effect") in settings.EFFECTS else "shadow",
        "y_offset": int(prof.get("y_offset", 0) or 0),
        "region": settings.region(prof),
        "region_default": settings.region(prof) == settings.DEFAULT_REGION,
        "read_scale": float(prof.get("read_scale", 1.0)),
        "settle_ms": float(prof.get("settle_ms", 60)),
        "style": prof.get("style", "msa"),
        "dialect": gemini.dialect_of(name, prof),  # the Arabic the stronger translator writes for this game
        # GTA VI's menus and messages (screen_text.py): None for every other game
        "screen_text": gta6.screen_text_on(name, prof) if gta6.is_gta6(name, str(prof.get("title") or "")) else None,
    }


def profile_save(body: dict) -> dict:
    from ..translate import load_profile, save_profile
    from ..vision.arabic import FONT_FILES

    name = str(body["name"])
    try:
        prof = load_profile(name)
    except Exception:
        prof = {}
    if "title" in body:
        prof["title"] = str(body["title"]).strip()[:60] or name
    if "glossary" in body:
        glossary, female = {}, []
        for row in body["glossary"]:
            en, ar = str(row.get("en", "")).strip(), str(row.get("ar", "")).strip()
            if en and ar:
                glossary[en[:60]] = ar[:60]
                if row.get("f"):
                    female.append(en[:60])
        prof["glossary"] = glossary
        prof["female"] = female
    if "font" in body and body["font"] in FONT_FILES:
        prof["font"] = body["font"]
    if "font_size" in body:
        if body["font_size"] in (None, "", 0):
            prof.pop("font_size", None)  # back to automatic
            cfg = paths.load_config()
            if cfg.get("font_sizes", {}).pop(name, None) is not None:
                paths.save_config(cfg)
        else:
            prof["font_size"] = max(18, min(90, int(body["font_size"])))
    from .. import settings

    if body.get("background") in settings.BACKGROUNDS:
        prof["background"] = body["background"]
        prof.pop("fill", None)
    if "plate_opacity" in body:
        prof["plate_opacity"] = max(0.2, min(0.95, float(body["plate_opacity"])))
    if "text_color" in body:
        if settings.parse_color(body["text_color"]):
            prof["text_color"] = str(body["text_color"]).lower()
        else:
            prof.pop("text_color", None)  # automatic: the colour of the game's own subtitles
    if body.get("effect") in settings.EFFECTS:
        prof["effect"] = body["effect"]
    if "y_offset" in body:
        prof["y_offset"] = max(-300, min(120, int(body["y_offset"])))
    if "region" in body:
        if body["region"] is None:
            prof.pop("region", None)  # back to the default area
        else:
            prof["region"] = settings.region({"region": body["region"]})
    if "read_scale" in body:
        prof["read_scale"] = max(0.5, min(2.5, float(body["read_scale"])))
    if "settle_ms" in body:
        prof["settle_ms"] = max(0.0, min(600.0, float(body["settle_ms"])))
    if "screen_text" in body:
        prof["screen_text"] = bool(body["screen_text"])
    if body.get("dialect") in ("msa", "saudi"):
        prof["dialect"] = body["dialect"]
    save_profile(name, prof)
    live.apply_profile(name)  # a running session picks the new look up at once
    return profile_view(name)


def pack_in(path: str, body: dict) -> dict:
    """A game from the bundled library, or a game file the user picked, into a profile."""
    from .. import packs
    from ..translate import list_profiles

    if path == "/api/packs/add":
        pack = packs.library_pack(str(body.get("id", "")))
    else:
        pack = packs.parse(body.get("pack"))
    result = packs.merge(pack, into=str(body.get("into") or "") or None)
    live.apply_profile(result["name"])  # the running game gets its new names at once
    result["profiles"] = list_profiles()
    return result


_preview_cache: dict = {}
SAMPLE_AR = "يجب أن نغادر قبل وصول العاصفة."


def _preview_scene():
    """A neutral scene with one English subtitle line, and the block our detector finds in it."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    from ..vision.arabic import FONT_DIR
    from ..vision.detect import detect

    if "scene" not in _preview_cache:
        w, h = 1920, 1080
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = 18 + 26 * (xx / w) + 30 * np.exp(-(((xx - 1350) / 420) ** 2 + ((yy - 520) / 300) ** 2))
        g = 26 + 34 * (yy / h) + 22 * np.exp(-(((xx - 500) / 500) ** 2 + ((yy - 800) / 260) ** 2))
        b = 46 + 44 * (1 - yy / h)
        img = Image.fromarray(np.clip(np.dstack([r, g, b]), 0, 255).astype(np.uint8))
        latin = ImageFont.truetype(io.BytesIO((FONT_DIR / "NotoSans-VF.ttf").read_bytes()), 40)  # any install path
        line = "We should leave before the storm gets here."
        tw = ImageDraw.Draw(img).textlength(line, font=latin)
        x, y = int((w - tw) / 2), 940
        shadow = Image.new("L", img.size, 0)
        ImageDraw.Draw(shadow).text((x + 2, y + 2), line, font=latin, fill=230)
        img = Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, shadow.filter(ImageFilter.GaussianBlur(2.2)))
        ImageDraw.Draw(img).text((x, y), line, font=latin, fill=(242, 247, 241))
        frame = np.ascontiguousarray(np.asarray(img)[:, :, ::-1])
        block = detect(frame)
        if block is None:
            raise RuntimeError("preview scene has no detectable line")
        _preview_cache["scene"] = (frame, block)
    return _preview_cache["scene"]


def _look_layers(profile: dict, text: str, on_tv: bool = False):
    """The layers a line gets with this profile's look. `on_tv`: skip the scene-coloured fill,
    which only makes sense over the scene it was computed from."""
    from .. import settings
    from ..vision import render

    frame, block = _preview_scene()
    style = settings.make_style(profile, None)
    mode = settings.background(profile)
    text_layer = render.make_text(frame, block, text or SAMPLE_AR, style)
    layers = []
    if mode in ("cover", "plate") and not on_tv:
        layers.append(render.make_fill(frame, block))
    if mode == "plate":
        cover = None if on_tv else (block.x0, block.y0, block.x1, block.y1)
        layers.append(render.make_plate(text_layer, frame.shape, settings.plate_opacity(profile), cover=cover))
    layers.append(text_layer)
    return frame, layers


def preview_png(profile: dict, text: str) -> bytes:
    """How the Arabic will look, drawn by the real renderer over a neutral scene."""
    import cv2

    from ..vision import render

    frame, layers = _look_layers(profile, text)
    out = render.composite(frame, layers)
    ok, buf = cv2.imencode(".png", out[760:1050, 420:1500])
    if not ok:
        raise RuntimeError("preview encoding failed")
    return buf.tobytes()


def do_tv_try(name: str, text: str):
    """Show one sample line on the real TV for a few seconds with the profile's look."""
    import cv2

    from .. import protocol as P
    from .. import say, tv
    from ..adb import Adb
    from ..translate import load_profile
    from ..tvlink import TvLink

    cfg = paths.load_config()
    ip = cfg.get("tv_ip")
    if not ip:
        raise RuntimeError("التلفزيون مو مجهّز للحين. افتح «الدليل» وكمّل خطوة التلفزيون.")
    adb = Adb(ip if ":" in ip else f"{ip}:5555")
    tv.connect(adb, wait_s=15)
    tv.start_service(adb)
    caps = tv._wait_for_app(ip)
    _frame, layers = _look_layers(load_profile(name), text, on_tv=True)
    out = []
    for layer in layers:
        bgra = layer.bgra[: P.MAX_LAYER_H, : P.MAX_LAYER_W]
        ok, buf = cv2.imencode(".png", bgra, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if not ok:
            raise RuntimeError("PNG encoding failed")
        out.append(P.Layer(id=layer.id, png=buf.tobytes(), x=int(layer.x), y=int(layer.y),
                           w=bgra.shape[1], h=bgra.shape[0], z=layer.z, ttl_ms=900))
    window = cfg.get("window") or ("A" if "A" in caps["windows"] else caps["windows"][0])
    with TvLink(ip) as link:
        link.connect()
        link.show(window, out)
        say.ok("السطر التجريبي على التلفزيون الحين (8 ثواني).")
        end = time.monotonic() + 8.0
        while time.monotonic() < end:
            link.keep([layer.id for layer in out])
            time.sleep(0.3)
        link.clear(None)
    return {"ok": True}


# -- setup actions ----------------------------------------------------------------


def do_tv_setup(ip: str):
    from .. import tv

    tv.setup(ip.strip())
    return {"ok": True}


def do_tv_test():
    from .. import tv

    tv.test(None, 25, False)
    return {"ok": True}


def do_tv_revert():
    """Remove our app from the TV and put back every setting the setup changed."""
    from .. import tv

    tv.revert(None)
    cfg = paths.load_config()
    for key in ("tv_ip", "tv", "overlay"):
        cfg.pop(key, None)
    paths.save_config(cfg)
    return {"ok": True}


def ps5_discover() -> dict:
    from ..sources import ps5

    found = [f for f in ps5.discover() if f.host_type == "PS5"]
    if not found:
        return {"found": False}
    first = found[0]
    cfg = paths.load_config()
    cfg["ps5_host"] = first.host
    cfg["ps5"] = {"name": first.name, "firmware": first.firmware, "mac": first.mac}
    paths.save_config(cfg)
    device = ps5.device_for(first.host)
    linked = ps5.linked_users(device)
    if linked and cfg.get("ps5_user") not in linked:
        cfg["ps5_user"] = linked[0]
        paths.save_config(cfg)
    return {"found": True, "host": first.host, "name": first.name, "status": first.status,
            "on": first.status.lower().startswith("ok"), "linked": linked, "pending": ps5.pending_users()}


def ps5_redirect(url: str) -> dict:
    from ..sources import ps5

    return {"user": ps5.add_user_from_redirect(url)}


def ps5_pin(pin: str) -> dict:
    from ..sources import ps5

    cfg = paths.load_config()
    host = cfg.get("ps5_host")
    if not host:
        raise RuntimeError("دوّر على الـPS5 أول.")
    pending = ps5.pending_users()
    if not pending:
        raise RuntimeError("سجّل دخول بالحساب الثاني أول (الخطوة اللي قبلها).")
    device = ps5.device_for(host)
    ps5.link(device, pending[-1], pin)
    cfg["ps5_user"] = pending[-1]
    paths.save_config(cfg)
    return {"user": pending[-1]}


def ps5_error(exc: Exception) -> str:
    text = str(exc)
    if "redirect" in text:
        return "هذا مو عنوان صفحة redirect. انسخ العنوان كامل من شريط المتصفح بعد تسجيل الدخول."
    if "rejected the sign-in code" in text:
        return "الرمز انتهى (يصلح دقائق بس). افتح صفحة الدخول من جديد وانسخ العنوان على طول."
    if "PIN must be 8 digits" in text:
        return "الرقم لازم يكون 8 أرقام."
    if "rejected the PIN" in text:
        return "الـPS5 رفض الرقم (يتغيّر كل كم دقيقة). طلّع رقم جديد وجرّب."
    return friendly_error(exc)


# -- HTTP -------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Tarjim"

    def log_message(self, fmt, *args) -> None:  # keep the console quiet
        return

    remote = False  # True in the handler of the home-network listener

    def _token(self) -> str | None:
        token = self.headers.get("X-Tarjim-Token")
        if token:
            return token
        values = parse_qs(urlparse(self.path).query).get("k")
        return values[0] if values else None

    def _allowed(self, path: str) -> bool:
        host = self.headers.get("Host") or ""
        origin = self.headers.get("Origin")
        if not self.remote:
            if host.split(":")[0] not in ("127.0.0.1", "localhost"):
                return False
            return origin is None or origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")
        # From the home network: same-origin requests only, the laptop-only actions never,
        # and everything under /api/ needs a paired phone's token.
        if origin is not None and origin != f"http://{host}":
            return False
        if not access.enabled or any(path.startswith(p) for p in LOCAL_ONLY):
            return False
        if not path.startswith("/api/") or path in PUBLIC_REMOTE:
            return True
        return access.allowed(self._token())

    def _send(self, code: int, body: bytes, ctype: str, cache: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self, limit: int = 200_000) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            return {}
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if not self._allowed(url.path):
            return self._send(403, b"forbidden", "text/plain")
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/events":
                return self._events()
            if url.path == "/api/hello":
                return self._json({"tarjim": 1, "remote": self.remote, "name": socket.gethostname(),
                                   "paired": (not self.remote) or access.allowed(self._token()),
                                   "apk": paths.PHONE_APK.exists()})
            if url.path == "/tarjim.apk":
                if not paths.PHONE_APK.exists():
                    return self._send(404, b"not built", "text/plain")
                return self._send(200, paths.PHONE_APK.read_bytes(), "application/vnd.android.package-archive")
            if url.path == "/api/xbox":
                return self._json(xbox_view())
            if url.path == "/api/xbox/consoles":
                return self._json({"consoles": xbox_auth().consoles()})
            if url.path.startswith(("/v5/", "/v6/")):  # the window's Xbox streaming calls
                status, body = xbox_auth().proxy("GET", self.path, None, self.headers.get("X-MS-Device-Info"))
                return self._send(status, body, "application/json; charset=utf-8")
            if url.path == "/api/phone":
                return self._json(access.view())
            if url.path == "/api/phone/qr.svg":
                view = access.view()
                if not view["pair_url"]:
                    return self._send(404, b"", "text/plain")
                return self._send(200, remote_mod.qr_svg(view["pair_url"]), "image/svg+xml")
            if url.path == "/api/state":
                from ..translate import list_profiles

                cfg = paths.load_config()
                return self._json({
                    "live": live.snapshot(), "task": tasks.snapshot(), "profiles": list_profiles(),
                    "last_profile": cfg.get("last_profile"), "guide_seen": bool(cfg.get("guide_seen")),
                    "setup_done": bool(cfg.get("tv_ip") and (cfg.get("ps5_user") if cfg.get("console") != "xbox"
                                                              else (cfg.get("xbox") or {}).get("id"))),
                    "remote": self.remote, "console": cfg.get("console", "ps5"),
                    "now": time.time(),
                })
            if url.path == "/api/check":
                return self._json(checks())
            if url.path == "/api/gemini":
                return self._json(gemini_view())
            if url.path == "/api/update":
                return self._json(update_view(fresh=q.get("fresh") == "1"))
            if url.path == "/api/profile":
                return self._json(profile_view(q.get("name", "default")))
            if url.path == "/api/packs":
                from .. import packs

                return self._json({"packs": packs.library_view()})
            if url.path == "/api/pack/export":
                from .. import packs

                pack = packs.export(q.get("name", "default"))
                return self._json({"pack": pack, "file": packs.file_name(pack)})
            if url.path == "/api/preview":
                from ..translate import load_profile

                profile = dict(load_profile(q.get("name", "default")))
                overrides = json.loads(q.get("o") or "{}")
                if isinstance(overrides, dict):  # what the user is trying right now, not saved yet
                    profile.update(overrides)
                    if overrides.get("font_size") in (None, "", 0):
                        profile.pop("font_size", None)
                    if overrides.get("text_color") == "auto":
                        profile.pop("text_color", None)
                return self._send(200, preview_png(profile, q.get("text", "")), "image/png")
            if url.path == "/api/frame":
                try:  # the page's width in real screen pixels (older pages do not say)
                    width = int(q.get("w") or 960)
                except ValueError:
                    width = 960
                jpg = live.frame_jpeg(width)
                if jpg is None:
                    return self._send(404, b"", "text/plain")
                return self._send(200, jpg, "image/jpeg")
            if url.path == "/api/ps5/login-url":
                from ..sources import ps5

                return self._json({"url": ps5.login_url()})
            if url.path.startswith("/fonts/"):
                return self._file(paths.REPO / "fonts" / Path(url.path).name, cache=True)
            name = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            return self._file(WEB / name)
        except Exception as exc:
            return self._json({"error": friendly_error(exc)}, 500)

    def _file(self, path: Path, cache: bool = False) -> None:
        base = (paths.REPO / "fonts") if cache else WEB
        try:
            resolved = path.resolve()
            resolved.relative_to(base.resolve())
        except (ValueError, OSError):
            return self._send(404, b"not found", "text/plain")
        if not resolved.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        if resolved.suffix in (".html", ".css", ".js"):
            ctype += "; charset=utf-8"
        self._send(200, resolved.read_bytes(), ctype, cache=cache)

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = hub.subscribe()
        try:
            self.wfile.write(b"retry: 1500\n\n")
            self.wfile.flush()
            while not _shutdown.is_set():
                try:
                    kind, data = q.get(timeout=8.0)
                    chunk = f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    chunk = ": ping\n\n"
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except OSError:
            pass  # the window was closed
        finally:
            hub.unsubscribe(q)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._allowed(path):
            return self._send(403, b"forbidden", "text/plain")
        try:
            if path == "/api/xbox/frame":  # a picture of the Xbox stream from the window: raw JPEG, not JSON
                length = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(length) if 0 < length < 3_000_000 else b""
                source = live.xbox
                return self._json({"ok": bool(source is not None and data and source.push_jpeg(data))})
            if path.startswith(("/v5/", "/v6/")):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if 0 < length < 400_000 else b""
                status, answer = xbox_auth().proxy("POST", self.path, raw, self.headers.get("X-MS-Device-Info"))
                return self._send(status, answer, "application/json; charset=utf-8")
            # A game file with a player's line fixes can be a few megabytes.
            body = self._body(8_000_000 if path == "/api/pack/import" else 200_000)
            if path in ("/api/packs/add", "/api/pack/import"):
                return self._json(pack_in(path, body))
            if path == "/api/console":
                kind = "xbox" if body.get("kind") == "xbox" else "ps5"
                cfg = paths.load_config()
                cfg["console"] = kind
                paths.save_config(cfg)
                return self._json(xbox_view())
            if path == "/api/xbox/link":
                return self._json(xbox_auth().start_link())
            if path == "/api/xbox/open":
                _open_url(xbox_auth().verification_uri or "https://www.microsoft.com/link")
                return self._json({"ok": True})
            if path == "/api/xbox/select":
                cfg = paths.load_config()
                cfg["xbox"] = {"id": str(body.get("id", ""))[:80], "name": str(body.get("name", "Xbox"))[:60]}
                cfg["console"] = "xbox"
                paths.save_config(cfg)
                return self._json(xbox_view())
            if path == "/api/xbox/forget":
                xbox_auth().forget()
                cfg = paths.load_config()
                cfg.pop("xbox", None)
                paths.save_config(cfg)
                return self._json(xbox_view())
            if path == "/api/xbox/state":
                source = live.xbox
                if source is not None:
                    source.state = str(body.get("state", ""))[:20]
                    if body.get("error"):
                        source.last_error = str(body["error"])[:200]
                return self._json({"ok": True})
            if path == "/api/pair":
                try:
                    token = access.pair(str(body.get("pin", "")), str(body.get("name", "")))
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"token": token})
            if path == "/api/phone":
                access.set_enabled(bool(body.get("enabled")))
                sync_remote_server()
                return self._json(access.view())
            if path == "/api/phone/forget":
                access.forget(str(body.get("id", "")))
                return self._json(access.view())
            if path == "/api/line/fix":
                en, ar = str(body.get("en", ""))[:400], str(body.get("ar", ""))[:400]
                profile = str(body.get("profile") or live.profile or "default")
                if not en.strip():
                    return self._json({"error": "ما فيه سطر."}, 400)
                live.fix_line(profile, en, ar)
                return self._json({"ok": True})
            if path == "/api/focus":
                return self._json({"ok": window_mod.focus()})
            if path == "/api/tv/revert":
                if live.state != "idle":
                    return self._json({"error": "وقّف الترجمة أول."}, 400)
                return self._json({"started": tasks.run("tv-revert", do_tv_revert)})
            if path == "/api/start":
                profile = str(body.get("profile") or "default")
                cfg = paths.load_config()
                cfg["last_profile"] = profile
                paths.save_config(cfg)
                return self._json({"started": live.start(profile)})
            if path == "/api/stop":
                live.stop()
                return self._json({"ok": True})
            if path == "/api/profile":
                return self._json(profile_save(body))
            if path == "/api/gemini":
                return self._json(gemini_save(body))
            if path == "/api/gemini/test":
                return self._json(gemini_test(body))
            if path == "/api/update/apply":
                return self._json(update_apply())
            if path == "/api/profile/new":
                from ..translate import save_profile

                title = str(body.get("title", "")).strip()[:60]
                slug = "".join(c if c.isalnum() and c.isascii() else "-" for c in title.lower()).strip("-")
                slug = "-".join(p for p in slug.split("-") if p)[:30] or f"game-{int(time.time()) % 100000}"
                if (paths.REPO / "profiles" / f"{slug}.yaml").exists():
                    slug = f"{slug}-{int(time.time()) % 1000}"
                save_profile(slug, {"title": title or slug, "style": "msa", "settle_ms": 60, "glossary": {}})
                return self._json(profile_view(slug))
            if path == "/api/guide-seen":
                cfg = paths.load_config()
                cfg["guide_seen"] = True
                paths.save_config(cfg)
                return self._json({"ok": True})
            if path == "/api/tv/setup":
                ip = str(body.get("ip", "")).strip()
                parts = ip.split(".")
                if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                    return self._json({"error": "اكتب عنوان التلفزيون بهذا الشكل: 192.168.1.50"}, 400)
                return self._json({"started": tasks.run("tv-setup", lambda: do_tv_setup(ip))})
            if path == "/api/tv/test":
                if live.state != "idle":
                    return self._json({"error": "وقّف الترجمة أول ثم جرّب المربعات."}, 400)
                return self._json({"started": tasks.run("tv-test", do_tv_test)})
            if path == "/api/tv/try":
                if live.state != "idle":
                    return self._json({"error": "الترجمة شغّالة: التعديل يتطبّق عليها مباشرة، شوف التلفزيون."}, 400)
                name, text = str(body.get("name") or "default"), str(body.get("text") or "")[:120]
                return self._json({"started": tasks.run("tv-try", lambda: do_tv_try(name, text))})
            if path == "/api/ps5/discover":
                return self._json(ps5_discover())
            if path == "/api/ps5/open-login":
                from ..sources import ps5

                _open_url(ps5.login_url())
                return self._json({"ok": True})
            if path == "/api/ps5/redirect":
                try:
                    return self._json(ps5_redirect(str(body.get("url", ""))))
                except Exception as exc:
                    return self._json({"error": ps5_error(exc)}, 400)
            if path == "/api/ps5/pin":
                try:
                    return self._json(ps5_pin(str(body.get("pin", ""))))
                except Exception as exc:
                    return self._json({"error": ps5_error(exc)}, 400)
            if path == "/api/quit":
                live.stop()
                self._json({"ok": True})
                threading.Thread(target=_quit_now, daemon=True).start()
                return None
            return self._json({"error": "unknown"}, 404)
        except Exception as exc:
            return self._json({"error": friendly_error(exc)}, 500)


# -- process life -------------------------------------------------------------------


def _chrome() -> str | None:
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return shutil.which("chrome") or shutil.which("msedge")


def _open_url(url: str) -> None:
    """Open a normal browser tab (used for Sony's sign-in page)."""
    chrome = _chrome()
    if chrome:
        subprocess.Popen([chrome, "--new-window", url], creationflags=paths.NO_WINDOW)
    else:
        webbrowser.open(url)


def open_browser_window() -> None:
    """Fallback when the native window is unavailable: the browser in app mode (no tabs, no address bar)."""
    url = f"http://{HOST}:{PORT}/"
    chrome = _chrome()
    if chrome:
        profile_dir = paths.ensure_home() / "app-window"
        subprocess.Popen([
            chrome, f"--app={url}", "--window-size=1200,860", f"--user-data-dir={profile_dir}",
            "--no-first-run", "--no-default-browser-check",
        ])
    else:
        webbrowser.open(url)


def _quit_soon() -> None:
    deadline = time.monotonic() + 6
    while live.state != "idle" and time.monotonic() < deadline:
        time.sleep(0.1)
    _shutdown.set()


def _quit_now() -> None:
    """The page asked to quit (its own "close" dialog): let the session clear the TV, then close."""
    global _closing_confirmed
    deadline = time.monotonic() + 6
    while live.state != "idle" and time.monotonic() < deadline:
        time.sleep(0.1)
    _closing_confirmed = True
    window_mod.close()
    _shutdown.set()


_closing_confirmed = False


def _may_close() -> bool:
    """Closing the window while a session runs: ask first, in the page's own dialog."""
    if _closing_confirmed or live.state == "idle":
        return True
    hub.publish("ask-close", {})
    return False


def _watch_idle() -> None:
    """Browser fallback only: close the program when its window has been gone for a while."""
    while not _shutdown.wait(5.0):
        if hub.clients == 0 and live.state == "idle" and not tasks.running:
            if time.monotonic() - hub.last_client_seen > IDLE_EXIT_S:
                _shutdown.set()


# -- phone control (home-network listener) ---------------------------------------------


class RemoteHandler(Handler):
    remote = True


_discovery_stop: threading.Event | None = None


def sync_remote_server() -> None:
    """Start or stop the home-network listener to match the user's switch."""
    global _remote_server, _discovery_stop
    with _remote_lock:
        if access.enabled and _remote_server is None:
            try:
                server = ThreadingHTTPServer(("0.0.0.0", remote_mod.REMOTE_PORT), RemoteHandler)
            except OSError:
                return  # port taken; the phone screen shows that the address is unavailable
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, name="tarjim-remote", daemon=True).start()
            _remote_server = server
            _discovery_stop = threading.Event()
            threading.Thread(target=remote_mod.serve_discovery, args=(_discovery_stop, access),
                             name="tarjim-discovery", daemon=True).start()
        elif not access.enabled and _remote_server is not None:
            server, _remote_server = _remote_server, None
            if _discovery_stop is not None:
                _discovery_stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
            server.server_close()


def _set_app_id() -> None:
    """Only when started through pythonw: give the window its own taskbar entry instead of Python's.
    Under Tarjim.exe the executable itself is the identity, which is what makes pinning work."""
    if sys.platform == "win32" and not os.environ.get("TARJIM_HOST"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("dev.tarjim.app")
        except (OSError, AttributeError):
            pass


def _ask_running_copy_to_show() -> None:
    import urllib.request

    try:
        req = urllib.request.Request(f"http://{HOST}:{PORT}/api/focus", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=2).read()
    except OSError:
        pass


def main(window: bool = True) -> int:
    os.environ.setdefault("PYTHONUTF8", "1")
    from .. import say

    say.setup_console()  # a Windows in another language gives streams that cannot hold Arabic
    os.environ.pop("__PYVENV_LAUNCHER__", None)  # set by Tarjim.exe for this process only
    if _port_open(HOST, PORT, 0.4):  # already running: bring its window forward instead of a second copy
        if window:
            _ask_running_copy_to_show()
        return 0
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f"cannot start on port {PORT}: {exc}", file=sys.stderr)
        return 1
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="tarjim-http", daemon=True).start()
    sync_remote_server()
    _set_app_id()
    shown = False
    if window:  # returns when the user closes the window
        shown = window_mod.run(f"http://{HOST}:{PORT}/", WEB / "icon.ico", _may_close)
    if not shown and not _shutdown.is_set():
        if window:
            open_browser_window()
            threading.Thread(target=_watch_idle, name="tarjim-idle", daemon=True).start()
        try:
            while not _shutdown.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
    live.stop()
    _quit_soon()
    server.shutdown()
    return 0
