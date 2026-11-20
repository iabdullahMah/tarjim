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

More keys can be added beside the first (another service such as Grok, through the request
shape most services accept). Each key has its own thread, its own pace and its own state, and
they share the waiting lines: with two keys twice as many lines are rewritten while still on
the TV, and when one is refused the other carries on.

The key is the user's secret: it is read from the private config file, sent only to Google
in a request header, and never logged or given to the interface.
"""

from __future__ import annotations

import hashlib
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
# Between two requests. It starts short, so most lines are rewritten while still on the TV
# (a game says a line every three seconds or so); each time the service refuses for quota it
# doubles, up to GAP_MAX_S, and it comes back down slowly while requests are answered.
GAP_S = 2.0
GAP_MAX_S = 20.0
EASE_AFTER = 10  # answered requests in a row before the gap is shortened again
BATCH = 8  # lines in one request at most
BACKLOG = 400  # lines kept waiting while requests are refused
CONTEXT = 4  # earlier lines given with a request, so "he", "it" and the tone are understood
TIMEOUT_S = 8.0
MIN_LETTERS = 4  # shorter lines ("No.", "OK") are left to the local engine and its phrase list
PAUSES_S = (10.0, 30.0, 120.0, 600.0, 1800.0)  # after one refusal, after the next one, ...

_STYLE = {
    "msa": (
        "Write simple Modern Standard Arabic, the way good film subtitles are written: clear, "
        "natural and short, never stiff or word for word."
    ),
    "saudi": (
        "Write Saudi colloquial Arabic (the everyday Najdi/white dialect young Saudis speak and "
        "write): «وش»، «ليش»، «أبي»، «الحين»، «مو»، «يلا». Natural and short, the way a "
        "friend would say it, never Standard Arabic and never word for word. Swearing is in the "
        "same dialect too, and the same word each time for the same English one (never the "
        "Standard «تباً» or «اللعنة» in the middle of colloquial speech)."
    ),
}
_RULES = (
    "You translate the English subtitles of a video game into Arabic for a player who reads them "
    "on the TV while playing.\n"
    "{style}\n"
    "- Translate the meaning. Idioms, slang and jokes get their Arabic sense, not their words.\n"
    "- Keep swearing as strong as the original; do not soften it and do not add any.\n"
    "- Say what the line says: never put another word in a word's place because it sounds "
    "close (\"bury\" is to put in a grave, not to forgive). A sound of worry or surprise "
    "(\"Uh-oh\", \"Whoa\", \"Huh\") stays one, it does not become \"OK\".\n"
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


# -- more keys: other services beside the first Gemini key --------------------------------------

MAX_EXTRA = 4
# What the page offers. "openai" is the request shape most other services accept
# (POST <base>/chat/completions with a Bearer key).
PRESETS = {
    "grok": {"kind": "openai", "name": "Grok", "base": "https://api.x.ai/v1", "model": "grok-4.3"},
    "gemini": {"kind": "gemini", "name": "Gemini 2", "base": "", "model": DEFAULT_MODEL},
    "other": {"kind": "openai", "name": "", "base": "", "model": ""},
}


def _clean_model(model: str, default: str = "") -> str:
    return "".join(ch for ch in str(model).strip() if ch.isalnum() or ch in "-._:/")[:80] or default


def _clean_base(base: str) -> str:
    """An address requests may be sent to: https only (plain http for this computer, for tests)."""
    base = str(base).strip().rstrip("/")[:200]
    if base.startswith("https://") or base.startswith("http://127.0.0.1"):
        return base
    raise ValueError("عنوان الخدمة لازم يبدأ بـ https://")


def extra_keys() -> list[dict]:
    """The other keys the user added: [{"id", "kind", "name", "key", "model", "base"}]."""
    raw = paths.load_config().get("gemini")
    items = raw.get("extra") if isinstance(raw, dict) else None
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("key") and item.get("kind") in ("gemini", "openai"):
            out.append({k: str(item.get(k) or "") for k in ("id", "kind", "name", "key", "model", "base")})
    return out[:MAX_EXTRA]


def make_extra(preset: str, key: str, model: str = "", base: str = "", name: str = "") -> dict:
    """One more key as it will be stored, from what the page sent. Raises ValueError (Arabic)."""
    p = PRESETS.get(preset)
    if p is None:
        raise ValueError("نوع الخدمة مو معروف.")
    key = str(key).strip()[:300]
    if len(key) < 8:
        raise ValueError("الصق المفتاح أول.")
    item = {
        "id": hashlib.sha256(f"{time.time()}{key}".encode()).hexdigest()[:8], "kind": p["kind"], "key": key,
        "name": ("".join(ch for ch in str(name) if ch.isprintable())[:24].strip() or p["name"] or "خدمة ثانية"),
        "model": _clean_model(model, p["model"]),
        "base": _clean_base(base or p["base"]) if p["kind"] == "openai" else "",
    }
    if not item["model"]:
        raise ValueError("اكتب اسم الموديل.")
    return item


def add_extra(item: dict) -> None:
    cfg = paths.load_config()
    raw = cfg.get("gemini") if isinstance(cfg.get("gemini"), dict) else {}
    items = [i for i in extra_keys() if i["key"] != item["key"]]
    if len(items) >= MAX_EXTRA:
        raise ValueError(f"أقصى عدد للمفاتيح الإضافية {MAX_EXTRA}. احذف واحد أول.")
    raw["extra"] = items + [item]
    cfg["gemini"] = raw
    paths.save_config(cfg)


def remove_extra(item_id: str) -> None:
    cfg = paths.load_config()
    raw = cfg.get("gemini") if isinstance(cfg.get("gemini"), dict) else {}
    raw["extra"] = [i for i in extra_keys() if i["id"] != item_id]
    cfg["gemini"] = raw
    paths.save_config(cfg)


def dialect_of(profile: str, prof: dict) -> str:
    """The Arabic a game's lines are written in: the profile's own choice, else street talk for
    GTA VI (its speech is street talk) and Standard Arabic for every other game."""
    from . import gta6

    chosen = prof.get("dialect")
    if chosen in DIALECTS:
        return chosen
    return "saudi" if gta6.is_gta6(profile, str(prof.get("title") or "")) else "msa"


def friendly(exc: Exception, name: str = "Gemini") -> str:
    """One plain Arabic sentence for the interface."""
    kind = getattr(exc, "kind", "")
    if kind == "key":
        return f"{name} رفض المفتاح. تأكد إنك نسخته كامل، وإن اسم الموديل صحيح."
    if kind == "quota":
        return f"خلص حد مفتاح {name} حالياً. الترجمة تكمل بالباقي ويرجع لحاله بعدين."
    if kind == "net":
        return f"ما قدرت أوصل لـ{name}. تأكد من الإنترنت."
    return f"{name} رد بشي ما ينفع. جرّب مرة ثانية."


# -- one request ----------------------------------------------------------------------------


def build_prompt(lines: list[dict], *, title: str, dialect: str, names: list[tuple[str, str, bool]],
                 earlier: list[dict]) -> tuple[str, str]:
    """(the standing instructions, this request's text) for some lines ({"en", "speaker"})."""
    parts = []
    if title:
        parts.append(f"Game: {title}")
    if names:
        parts.append("Names:\n" + "\n".join(f"{en} = {ar}{' (woman)' if f else ''}" for en, ar, f in names))
    if earlier:
        parts.append("Earlier lines (for understanding only):\n" + "\n".join(_said(l) for l in earlier))
    parts.append("Translate:\n" + "\n".join(f"{i}. {_said(l)}" for i, l in enumerate(lines, 1)))
    return _RULES.format(style=_STYLE.get(dialect, _STYLE["msa"])), "\n\n".join(parts)


def build_request(lines: list[dict], *, title: str, dialect: str, names: list[tuple[str, str, bool]],
                  earlier: list[dict]) -> dict:
    """Gemini's request body for some lines of one game."""
    rules, text = build_prompt(lines, title=title, dialect=dialect, names=names, earlier=earlier)
    return {
        "systemInstruction": {"parts": [{"text": rules}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
            "responseSchema": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "safetySettings": _SAFETY,
    }


def _said(line: dict) -> str:
    return f"[{line['speaker']}] {line['en']}" if line.get("speaker") else line["en"]


def _post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "Tarjim", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise GeminiError("quota", "HTTP 429") from None
        if exc.code in (400, 401, 403, 404):  # 404: no such model
            raise GeminiError("key", f"HTTP {exc.code}") from None
        raise GeminiError("net", f"HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:  # no connection, a timeout, or an answer that is not JSON
        raise GeminiError("net", type(exc).__name__) from None


def _strings(text) -> list[str]:
    """The list of strings in an answer: a JSON array, or one inside a fenced block or an
    object (services without a strict output shape wrap it their own way)."""
    if not isinstance(text, str):
        raise GeminiError("bad", "no usable answer")
    try:
        out = json.loads(text)
    except ValueError:
        a, b = text.find("["), text.rfind("]")
        if a < 0 or b <= a:
            raise GeminiError("bad", "no list in the answer") from None
        try:
            out = json.loads(text[a:b + 1])
        except ValueError:
            raise GeminiError("bad", "no list in the answer") from None
    if isinstance(out, dict):
        out = next((v for v in out.values() if isinstance(v, list)), None)
    if not isinstance(out, list) or not all(isinstance(x, str) for x in out):
        raise GeminiError("bad", "not a list of strings")
    return out


def call(key: str, model: str, body: dict, *, base: str = API, timeout: float = TIMEOUT_S) -> list[str]:
    """Send one request to Gemini; the strings of the JSON array it answers with."""
    data = _post(f"{base}/models/{model}:generateContent", {"x-goog-api-key": key}, body, timeout)
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise GeminiError("bad", "no usable answer") from None  # refused by its filter, or cut off
    return _strings(text)


def call_openai(key: str, model: str, base: str, rules: str, text: str, *, timeout: float = TIMEOUT_S) -> list[str]:
    """The same request in the shape most other services accept (chat completions)."""
    body = {"model": model, "temperature": 0.3,
            "messages": [{"role": "system", "content": rules}, {"role": "user", "content": text}]}
    data = _post(f"{base}/chat/completions", {"Authorization": f"Bearer {key}"}, body, timeout)
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise GeminiError("bad", "no usable answer") from None
    return _strings(answer)


class Provider:
    """One key of one service, with its own pace and state."""

    def __init__(self, kind: str, key: str, model: str, base: str = "", name: str = "", id: str = "",
                 gap_s: float = GAP_S) -> None:
        self.kind, self.key, self.model = kind, key, model
        self.base = base or API
        self.name = name or ("Gemini" if kind == "gemini" else "خدمة ثانية")
        self.id = id
        self.gap_s = gap_s  # the gap now; it moves between min_gap_s and GAP_MAX_S
        self.min_gap_s = gap_s
        self.state = "ok"  # ok | waiting (refused for now) | bad_key
        self.error = ""
        self.done = 0
        self.requests = 0
        self.answered = 0  # requests answered in a row
        self.refusals = 0
        self.not_before = 0.0

    def ask(self, lines: list[dict], **about) -> list[str]:
        if self.kind == "gemini":
            return call(self.key, self.model, build_request(lines, **about), base=self.base)
        rules, text = build_prompt(lines, **about)
        return call_openai(self.key, self.model, self.base, rules, text)

    def view(self) -> dict:
        return {"id": self.id, "name": self.name, "state": self.state, "done": self.done,
                "gap_s": round(self.gap_s, 1), "error": self.error}


def check(key: str, model: str, *, base: str = API) -> str:
    """Try a Gemini key with one small line; its Arabic. Raises GeminiError."""
    return check_provider(Provider("gemini", key, model, base))


def check_provider(provider: Provider) -> str:
    """Try any key with one small line; its Arabic. Raises GeminiError."""
    en = "Wait here, I'll be right back."
    out = provider.ask([{"en": en}], title="", dialect="msa", names=[], earlier=[])
    ar = normalize_arabic(out[0]) if out else ""
    if not _usable(en, ar):
        raise GeminiError("bad", "not Arabic")
    return ar


def _usable(en: str, ar: str) -> bool:
    """Arabic letters, and not far longer than the line could need (an answer that explains)."""
    letters = sum(1 for ch in ar if "؀" <= ch <= "ۿ")
    return letters >= 2 and letters >= 0.5 * sum(1 for ch in ar if ch.isalpha()) and len(ar) <= 4 * len(en) + 40


# -- the threads ----------------------------------------------------------------------------


class Refiner:
    """Sends shown lines to the stronger translators in the background and stores what comes
    back. `key` is the first Gemini key (may be empty); `extra` holds more Providers."""

    def __init__(self, cache: Cache, profile: str, *, key: str = "", model: str = DEFAULT_MODEL, title: str = "",
                 dialect: str = "msa", glossary: dict[str, str] | None = None, female: set[str] | None = None,
                 base: str = API, gap_s: float = GAP_S, pauses_s: tuple[float, ...] = PAUSES_S,
                 extra: list[Provider] | None = None) -> None:
        self.cache = cache
        self.profile = profile
        self.title = title
        self.dialect = dialect if dialect in DIALECTS else "msa"
        self.glossary = dict(glossary or {})
        self.female = set(female or ())
        self.pauses_s = pauses_s
        self.providers: list[Provider] = ([Provider("gemini", key, model, base, gap_s=gap_s)] if key else []) + list(extra or [])
        self.on_better = None  # callback(english, arabic) when a line's better Arabic arrives
        self.log = None  # callback(record) for the session log: each change of state, never a key
        self.failed = 0  # lines given up on
        self._lock = threading.Condition()
        self._todo: deque[dict] = deque(maxlen=BACKLOG)
        self._known: set[str] = set()  # loose keys of lines waiting or already asked about
        self._recent: deque[dict] = deque(maxlen=CONTEXT)
        self._closed = False
        self._threads = [threading.Thread(target=self._run, args=(p,), name=f"tarjim-refine-{i}", daemon=True)
                         for i, p in enumerate(self.providers)]
        for t in self._threads:
            t.start()

    # The first key's pace, as before more keys existed (the tests and the log read it).
    @property
    def gap_s(self) -> float:
        return self.providers[0].gap_s if self.providers else 0.0

    @gap_s.setter
    def gap_s(self, value: float) -> None:
        for p in self.providers:
            p.gap_s = value

    @property
    def min_gap_s(self) -> float:
        return self.providers[0].min_gap_s if self.providers else 0.0

    @property
    def done(self) -> int:
        return sum(p.done for p in self.providers)

    @property
    def state(self) -> str:
        states = [p.state for p in self.providers]
        return "ok" if "ok" in states else "waiting" if "waiting" in states else "bad_key"

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
            state = self.state
            error = "" if state == "ok" else next((p.error for p in self.providers if p.error), "")
            return {"state": state, "done": self.done, "waiting": len(self._todo), "error": error,
                    "gap_s": round(self.gap_s, 1), "providers": [p.view() for p in self.providers]}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()
        for t in self._threads:
            t.join(timeout=1.0)

    # -- the workers: one per key, sharing the waiting lines ---------------------------------

    def _take(self, p: Provider) -> list[dict] | None:
        """Wait for work and for this key's gap; the newest lines, oldest first (the newest is
        the one on the TV now). None when closing."""
        with self._lock:
            while not self._closed:
                wait = p.not_before - time.monotonic()
                if self._todo and wait <= 0 and p.state != "bad_key":
                    batch = [self._todo.pop() for _ in range(min(BATCH, len(self._todo)))]
                    batch.reverse()
                    p.not_before = time.monotonic() + p.gap_s  # the next request, whatever this one does
                    return batch
                self._lock.wait(timeout=wait if self._todo and wait > 0 and p.state != "bad_key" else None)
        return None

    def _names(self, batch: list[dict]) -> list[tuple[str, str, bool]]:
        """The game's names that are in these lines or the ones before them (not the whole list:
        a game can have hundreds)."""
        text = " ".join(l["en"] for b in batch for l in [b, *b["earlier"]]).lower()
        return [(en, ar, en in self.female) for en, ar in self.glossary.items() if en and en.lower() in text][:40]

    def _run(self, p: Provider) -> None:
        while True:
            batch = self._take(p)
            if batch is None:
                return
            try:
                p.requests += 1
                out = p.ask(batch, title=self.title, dialect=self.dialect, names=self._names(batch),
                            earlier=batch[0]["earlier"])
                if len(out) != len(batch):
                    raise GeminiError("bad", f"{len(out)} answers for {len(batch)} lines")
            except GeminiError as exc:
                self._failed(p, batch, exc)
                continue
            except Exception as exc:  # a thread must never die: the local Arabic simply stays
                self._failed(p, batch, GeminiError("bad", type(exc).__name__))
                continue
            with self._lock:
                was = p.state if p.requests > 1 else ""
                p.state, p.error, p.refusals = "ok", "", 0
                p.answered += 1
                if p.answered % EASE_AFTER == 0 and p.gap_s > p.min_gap_s:
                    p.gap_s = max(p.min_gap_s, p.gap_s * 0.8)
                p.not_before = time.monotonic() + p.gap_s
            if was != "ok":
                self._note(p, {"state": "ok", "lines": len(batch)})  # the first answer, or back after a refusal
            for line, raw in zip(batch, out):
                ar = normalize_arabic(raw)
                if not _usable(line["en"], ar):
                    self.failed += 1
                    continue
                try:
                    self.cache.put_better(self.profile, line["en"], self.dialect, ar, p.model)
                except Exception:
                    continue  # the cache was closed under us: the session is ending
                p.done += 1
                callback = self.on_better
                if callback is not None:
                    try:
                        callback(line["en"], ar)
                    except Exception:
                        pass  # drawing it now failed; it is stored for the next time all the same

    def _note(self, p: Provider, rec: dict) -> None:
        if self.log is not None:
            try:
                self.log({"kind": "gemini", "provider": p.name, **rec})
            except Exception:
                pass

    def _failed(self, p: Provider, batch: list[dict], exc: GeminiError) -> None:
        self._note(p, {"state": "failed", "why": exc.kind, "detail": exc.detail, "lines": len(batch),
                       "gap_s": round(p.gap_s, 1)})
        with self._lock:
            p.error = friendly(exc, p.name)
            if exc.kind == "bad":  # these lines will not do better a second time
                self.failed += len(batch)
                p.not_before = time.monotonic() + p.gap_s
                return
            self._todo.extend(batch)  # asked again later, by this key or another
            if exc.kind == "key":
                p.state = "bad_key"
            else:
                if exc.kind == "quota":  # asked too often for this key: ask less often from now on
                    p.answered = 0
                    p.gap_s = min(GAP_MAX_S, max(p.gap_s * 2.0, p.min_gap_s, 1.0))
                pause = self.pauses_s[min(p.refusals, len(self.pauses_s) - 1)] if self.pauses_s else p.gap_s
                p.refusals += 1
                p.state = "waiting"
                p.not_before = time.monotonic() + pause
            self._lock.notify_all()  # another key may take these lines now
