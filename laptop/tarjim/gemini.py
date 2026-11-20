"""A stronger translator beside the local one: Google's Gemini, with the user's own key.

Optional and off until the user gives a key. The local engine still answers first, so a line
never waits for the internet. Each line shown is then also sent to Gemini together with the
lines before it, who is speaking and the game's names; its Arabic is

  - stored (the `better` table of the cache), so the next time the line appears it is the
    Arabic shown from the first moment, with no request at all;
  - handed back at once (`on_better`), so the line still on the TV is rewritten in place.

Nothing here can hold a line up or take one down: every failure (no internet, a spent free
quota, a refused key, an answer that makes no sense) only means the local Arabic stays. A
free key has a small quota per minute and per day, so requests are spaced out, several
waiting lines go in one request, and after a refusal the thread waits before trying again.

The key is the user's secret: it is read from the private config file, sent only to Google
in a request header, and never logged or given to the interface.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque

from . import paths
from .translate import Cache, loose_key, normalize_arabic, normalize_english

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
DIALECTS = ("msa", "saudi")
GAP_S = 5.0  # between two requests: a free key allows about ten a minute
BATCH = 8  # lines in one request at most
BACKLOG = 400  # lines kept waiting while requests are refused
CONTEXT = 4  # earlier lines given with a request, so "he", "it" and the tone are understood
TIMEOUT_S = 8.0
MIN_LETTERS = 4  # shorter lines ("No.", "OK") are left to the local engine and its phrase list
PAUSES_S = (30.0, 120.0, 600.0, 1800.0)  # after one refusal, after the next one, ...

_STYLE = {
    "msa": (
        "Write simple Modern Standard Arabic, the way good film subtitles are written: clear, "
        "natural and short, never stiff or word for word."
    ),
    "saudi": (
        "Write Saudi colloquial Arabic (the everyday Najdi/white dialect young Saudis speak and "
        "write): «وش»، «ليش»، «أبي»، «الحين»، «مو»، «يلا». Natural and short, the way a "
        "friend would say it, never Standard Arabic and never word for word."
    ),
}
_RULES = (
    "You translate the English subtitles of a video game into Arabic for a player who reads them "
    "on the TV while playing.\n"
    "{style}\n"
    "- Translate the meaning. Idioms, slang and jokes get their Arabic sense, not their words.\n"
    "- Keep swearing as strong as the original; do not soften it and do not add any.\n"
    "- Use the Arabic spelling given for each name, exactly. Other names are written in Arabic "
    "letters by their sound.\n"
    "- The one spoken to is a man unless the line, the names marked (woman) or the earlier lines "
    "show otherwise; a woman speaking uses feminine forms for herself.\n"
    "- A mission goal or a button prompt is a command to the player (masculine singular).\n"
    "- The English was read off the screen by a program: a capital I may be a small l, a word may "
    "be cut. Read through such slips.\n"
    "- No short-vowel marks, Arabic punctuation (،  ؟), no brackets, no notes, no English left "
    "except a brand or a button name.\n"
    "Answer with a JSON array holding one Arabic string for each numbered line, in the same "
    "order, and nothing else. The earlier lines are for understanding only: do not translate them."
)
_SAFETY = [{"category": c, "threshold": "BLOCK_NONE"} for c in (
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT",
)]


class GeminiError(Exception):
    """`kind`: "key" (refused key), "quota" (too many requests), "net" (no answer), "bad" (an
    answer that cannot be used)."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


def settings() -> dict:
    """The user's Gemini settings: {"key", "enabled", "model"} (key empty when none was given)."""
    raw = paths.load_config().get("gemini")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "key": str(raw.get("key") or "").strip(),
        "enabled": bool(raw.get("enabled", True)),
        "model": str(raw.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
    }


def save_settings(key: str | None = None, enabled: bool | None = None, model: str | None = None) -> dict:
    cfg = paths.load_config()
    raw = cfg.get("gemini") if isinstance(cfg.get("gemini"), dict) else {}
    if key is not None:
        raw["key"] = key.strip()[:200]
    if enabled is not None:
        raw["enabled"] = bool(enabled)
    if model is not None:
        clean = "".join(ch for ch in model.strip() if ch.isalnum() or ch in "-._")[:80]
        raw["model"] = clean or DEFAULT_MODEL
    cfg["gemini"] = raw
    paths.save_config(cfg)
    return settings()


def dialect_of(profile: str, prof: dict) -> str:
    """The Arabic a game's lines are written in: the profile's own choice, else street talk for
    GTA VI (its speech is street talk) and Standard Arabic for every other game."""
    from . import gta6

    chosen = prof.get("dialect")
    if chosen in DIALECTS:
        return chosen
    return "saudi" if gta6.is_gta6(profile, str(prof.get("title") or "")) else "msa"


def friendly(exc: Exception) -> str:
    """One plain Arabic sentence for the interface."""
    kind = getattr(exc, "kind", "")
    if kind == "key":
        return "Google رفض المفتاح. تأكد إنك نسخته كامل من AI Studio، وإن اسم الموديل صحيح."
    if kind == "quota":
        return "خلص الحد المجاني لهذا المفتاح حالياً. الترجمة تكمل بالمترجم المحلي ويرجع Gemini لحاله بعدين."
    if kind == "net":
        return "ما قدرت أوصل لـGoogle. تأكد من الإنترنت."
    return "Gemini رد بشي ما ينفع. جرّب مرة ثانية."


# -- one request ----------------------------------------------------------------------------


def build_request(lines: list[dict], *, title: str, dialect: str, names: list[tuple[str, str, bool]],
                  earlier: list[dict]) -> dict:
    """The request body for some lines ({"en", "speaker"}) of one game."""
    parts = []
    if title:
        parts.append(f"Game: {title}")
    if names:
        parts.append("Names:\n" + "\n".join(f"{en} = {ar}{' (woman)' if f else ''}" for en, ar, f in names))
    if earlier:
        parts.append("Earlier lines (for understanding only):\n" + "\n".join(_said(l) for l in earlier))
    parts.append("Translate:\n" + "\n".join(f"{i}. {_said(l)}" for i, l in enumerate(lines, 1)))
    return {
        "systemInstruction": {"parts": [{"text": _RULES.format(style=_STYLE.get(dialect, _STYLE["msa"]))}]},
        "contents": [{"role": "user", "parts": [{"text": "\n\n".join(parts)}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
            "responseSchema": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "safetySettings": _SAFETY,
    }


def _said(line: dict) -> str:
    return f"[{line['speaker']}] {line['en']}" if line.get("speaker") else line["en"]


def call(key: str, model: str, body: dict, *, base: str = API, timeout: float = TIMEOUT_S) -> list[str]:
    """Send one request; the strings of the JSON array it answers with."""
    req = urllib.request.Request(
        f"{base}/models/{model}:generateContent", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise GeminiError("quota", "HTTP 429") from None
        if exc.code in (400, 401, 403, 404):  # 404: no such model
            raise GeminiError("key", f"HTTP {exc.code}") from None
        raise GeminiError("net", f"HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:  # no connection, a timeout, or an answer that is not JSON
        raise GeminiError("net", type(exc).__name__) from None
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        out = json.loads(text)
    except (KeyError, IndexError, TypeError, ValueError):
        raise GeminiError("bad", "no usable answer") from None  # refused by its filter, or cut off
    if not isinstance(out, list) or not all(isinstance(x, str) for x in out):
        raise GeminiError("bad", "not a list of strings")
    return out


def check(key: str, model: str, *, base: str = API) -> str:
    """Try the key with one small line; its Arabic. Raises GeminiError."""
    body = build_request([{"en": "Wait here, I'll be right back."}], title="", dialect="msa", names=[], earlier=[])
    out = call(key, model, body, base=base)
    ar = normalize_arabic(out[0]) if out else ""
    if not _usable("Wait here, I'll be right back.", ar):
        raise GeminiError("bad", "not Arabic")
    return ar


def _usable(en: str, ar: str) -> bool:
    """Arabic letters, and not far longer than the line could need (an answer that explains)."""
    letters = sum(1 for ch in ar if "؀" <= ch <= "ۿ")
    return letters >= 2 and letters >= 0.5 * sum(1 for ch in ar if ch.isalpha()) and len(ar) <= 4 * len(en) + 40


# -- the thread -----------------------------------------------------------------------------


class Refiner:
    """Sends shown lines to Gemini in the background and stores what comes back."""

    def __init__(self, cache: Cache, profile: str, *, key: str, model: str = DEFAULT_MODEL, title: str = "",
                 dialect: str = "msa", glossary: dict[str, str] | None = None, female: set[str] | None = None,
                 base: str = API, gap_s: float = GAP_S, pauses_s: tuple[float, ...] = PAUSES_S) -> None:
        self.cache = cache
        self.profile = profile
        self.key = key
        self.model = model
        self.title = title
        self.dialect = dialect if dialect in DIALECTS else "msa"
        self.glossary = dict(glossary or {})
        self.female = set(female or ())
        self.base = base
        self.gap_s = gap_s
        self.pauses_s = pauses_s
        self.on_better = None  # callback(english, arabic) when a line's better Arabic arrives
        self.log = None  # callback(record) for the session log: each change of state, never the key
        self.state = "ok"  # ok | waiting (refused for now) | bad_key
        self.error = ""
        self.done = 0  # lines stored
        self.requests = 0
        self.failed = 0  # lines given up on
        self._lock = threading.Condition()
        self._todo: deque[dict] = deque(maxlen=BACKLOG)
        self._known: set[str] = set()  # loose keys of lines waiting or already asked about
        self._recent: deque[dict] = deque(maxlen=CONTEXT)
        self._refusals = 0
        self._not_before = 0.0
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="tarjim-gemini", daemon=True)
        self._thread.start()

    # -- from the pipeline ------------------------------------------------------------------

    def seen(self, english: str, speaker: str | None = None, *, wanted: bool = True) -> None:
        """A line went up on the TV. `wanted` is False when its Arabic is already the user's own
        or an earlier answer: it is then only remembered as what was said before the next line."""
        en = normalize_english(english)
        line = {"en": en, "speaker": speaker or None}
        with self._lock:
            earlier = list(self._recent)
            self._recent.append(line)
            if not wanted or self._closed or self.state == "bad_key":
                return
            sk = loose_key(en)
            if sum(ch.isalpha() for ch in en) < MIN_LETTERS or sk in self._known:
                return
            self._known.add(sk)
            self._todo.append({**line, "earlier": earlier})
            self._lock.notify_all()

    def status(self) -> dict:
        with self._lock:
            return {"state": self.state, "done": self.done, "waiting": len(self._todo), "error": self.error}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()
        self._thread.join(timeout=1.0)

    # -- the worker -------------------------------------------------------------------------

    def _take(self) -> list[dict] | None:
        """Wait for work and for the gap since the last request; the newest lines, oldest first
        (the newest is the one on the TV now). None when closing."""
        with self._lock:
            while not self._closed:
                wait = self._not_before - time.monotonic()
                if self._todo and wait <= 0 and self.state != "bad_key":
                    batch = [self._todo.pop() for _ in range(min(BATCH, len(self._todo)))]
                    batch.reverse()
                    return batch
                self._lock.wait(timeout=wait if self._todo and wait > 0 else None)
        return None

    def _names(self, batch: list[dict]) -> list[tuple[str, str, bool]]:
        """The game's names that are in these lines or the ones before them (not the whole list:
        a game can have hundreds)."""
        text = " ".join(l["en"] for b in batch for l in [b, *b["earlier"]]).lower()
        return [(en, ar, en in self.female) for en, ar in self.glossary.items() if en and en.lower() in text][:40]

    def _run(self) -> None:
        while True:
            batch = self._take()
            if batch is None:
                return
            body = build_request(batch, title=self.title, dialect=self.dialect, names=self._names(batch),
                                 earlier=batch[0]["earlier"])
            try:
                self.requests += 1
                out = call(self.key, self.model, body, base=self.base)
                if len(out) != len(batch):
                    raise GeminiError("bad", f"{len(out)} answers for {len(batch)} lines")
            except GeminiError as exc:
                self._failed(batch, exc)
                continue
            except Exception as exc:  # this thread must never die: the local Arabic simply stays
                self._failed(batch, GeminiError("bad", type(exc).__name__))
                continue
            with self._lock:
                was = self.state if self.requests > 1 else ""
                self.state, self.error, self._refusals = "ok", "", 0
                self._not_before = time.monotonic() + self.gap_s
            if was != "ok":
                self._note({"state": "ok", "lines": len(batch)})  # the first answer, or back after a refusal
            for line, raw in zip(batch, out):
                ar = normalize_arabic(raw)
                if not _usable(line["en"], ar):
                    self.failed += 1
                    continue
                try:
                    self.cache.put_better(self.profile, line["en"], self.dialect, ar, self.model)
                except Exception:
                    continue  # the cache was closed under us: the session is ending
                self.done += 1
                callback = self.on_better
                if callback is not None:
                    try:
                        callback(line["en"], ar)
                    except Exception:
                        pass  # drawing it now failed; it is stored for the next time all the same

    def _note(self, rec: dict) -> None:
        if self.log is not None:
            try:
                self.log({"kind": "gemini", **rec})
            except Exception:
                pass

    def _failed(self, batch: list[dict], exc: GeminiError) -> None:
        self._note({"state": "failed", "why": exc.kind, "detail": exc.detail, "lines": len(batch)})
        with self._lock:
            self.error = friendly(exc)
            if exc.kind == "bad":  # these lines will not do better a second time
                self.failed += len(batch)
                self._not_before = time.monotonic() + self.gap_s
                return
            self._todo.extend(batch)  # asked again later
            if exc.kind == "key":
                self.state = "bad_key"
                return
            pause = self.pauses_s[min(self._refusals, len(self.pauses_s) - 1)] if self.pauses_s else self.gap_s
            self._refusals += 1
            self.state = "waiting"
            self._not_before = time.monotonic() + pause
