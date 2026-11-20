"""The stronger translator beside the local one (gemini.py), against a stand-in for Google's
server: no request ever leaves the computer in these tests, and no real key is used."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import tarjim.vision.arabic  # noqa: F401  (resolve the font folder before any test moves paths.REPO)
from tarjim import gemini, paths
from tarjim.faketv import FakeTv
from tarjim.pipeline import Pipeline
from tarjim.translate import Cache, CachedTranslator
from tarjim.tvlink import TvLink
from tarjim.vision.ocr import OcrEngine

from . import synth

KEY = "test-key-not-a-real-one"


class Google:
    """Answers generateContent the way the real service does. `plan` is a list of what to do
    with the next requests: "ok", an HTTP status code, or "junk"; after it, always "ok"."""

    def __init__(self, plan=()) -> None:
        self.plan = list(plan)
        self.requests: list[dict] = []
        self.keys: list[str] = []
        self.paths: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                return

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                outer.keys.append(self.headers.get("x-goog-api-key", ""))
                outer.paths.append(self.path)
                step = outer.plan.pop(0) if outer.plan else "ok"
                if isinstance(step, int):
                    self.send_response(step)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if step == "junk":
                    answer = {"candidates": [{"finishReason": "SAFETY"}]}
                else:
                    asked = body["contents"][0]["parts"][0]["text"].split("Translate:\n")[1].splitlines()
                    answer = {"candidates": [{"content": {"parts": [{"text": json.dumps(
                        [f"أذكى {i}: {outer.arabic_for(line)}" for i, line in enumerate(asked, 1)], ensure_ascii=False)}]}}]}
                data = json.dumps(answer).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1beta"

    @staticmethod
    def arabic_for(line: str) -> str:
        return "سطر عربي" + ("؟" if line.rstrip().endswith("?") else ".")

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def google():
    g = Google()
    yield g
    g.close()


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        time.sleep(0.01)
    return cond()


def refiner(google, cache, **kw):
    kw.setdefault("gap_s", 0.0)
    kw.setdefault("pauses_s", (0.05,))
    return gemini.Refiner(cache, "g", key=KEY, model="some-model", base=google.base, **kw)


# -- one request ----------------------------------------------------------------------------


def test_the_request_carries_the_style_the_names_and_the_earlier_lines(google):
    body = gemini.build_request(
        [{"en": "You strapped?", "speaker": "Jay"}, {"en": "Let's go.", "speaker": None}],
        title="Some Game", dialect="saudi", names=[("Maria", "ماريا", True), ("Jay", "جاي", False)],
        earlier=[{"en": "Get in the car.", "speaker": "Maria"}],
    )
    rules, asked = body["systemInstruction"]["parts"][0]["text"], body["contents"][0]["parts"][0]["text"]
    assert "Saudi colloquial" in rules and "JSON array" in rules
    assert "Modern Standard" in gemini.build_request([{"en": "x"}], title="", dialect="msa", names=[], earlier=[])[
        "systemInstruction"]["parts"][0]["text"]
    assert "Maria = ماريا (woman)" in asked and "Jay = جاي\n" in asked
    assert asked.index("[Maria] Get in the car.") < asked.index("Translate:\n1. [Jay] You strapped?\n2. Let's go.")
    out = gemini.call(KEY, "some-model", body, base=google.base)
    assert len(out) == 2 and google.keys == [KEY]
    assert google.paths == ["/v1beta/models/some-model:generateContent"]  # the key is in a header, never in the address
    assert KEY not in json.dumps(google.requests[0])


@pytest.mark.parametrize("step, kind", [(429, "quota"), (403, "key"), (400, "key"), (404, "key"), (500, "net"), ("junk", "bad")])
def test_a_refusal_is_told_apart(google, step, kind):
    google.plan = [step]
    with pytest.raises(gemini.GeminiError) as err:
        gemini.check(KEY, "some-model", base=google.base)
    assert err.value.kind == kind and KEY not in str(err.value)
    assert gemini.friendly(err.value)  # one Arabic sentence for each


def test_no_connection_is_a_network_error_not_a_crash():
    with pytest.raises(gemini.GeminiError) as err:
        gemini.check(KEY, "some-model", base="http://127.0.0.1:9/v1beta")
    assert err.value.kind == "net"


def test_trying_a_key_gives_its_arabic(google):
    assert gemini.check(KEY, "some-model", base=google.base) == "أذكى 1: سطر عربي."


# -- the thread -----------------------------------------------------------------------------


def test_an_answer_is_stored_and_shown_from_the_first_moment_next_time(google):
    cache = Cache(":memory:")

    class Local:
        name = "local"

        def translate(self, text, context=None):
            return "ترجمة محلية."

    tr = CachedTranslator(Local(), cache, profile="g")
    assert tr.translate("Where did you hide the money?")[0] == "ترجمة محلية." and tr.last_source == "engine"
    got = []
    r = refiner(google, cache)
    r.on_better = lambda en, ar: got.append((en, ar))
    r.seen("Where did you hide the money?", "Jay")
    assert _wait(lambda: got)
    assert got == [("Where did you hide the money?", "أذكى 1: سطر عربي؟")] and r.status()["done"] == 1
    # the next time: its Arabic at once, from the cache, also when read a little differently
    assert tr.translate("Where did you hide the money?")[:2] == ("أذكى 1: سطر عربي؟", True)
    assert tr.last_source == "better"
    assert tr.translate("Where did you hide the money")[0] == "أذكى 1: سطر عربي."
    # the user's own wording still wins over it
    tr.fix("Where did you hide the money?", "وين خبّيت الفلوس؟")
    assert tr.translate("Where did you hide the money?")[0] == "وين خبّيت الفلوس؟" and tr.last_source == "fix"
    # another kind of Arabic is another answer: the stored one is not served for it
    tr.fix("Where did you hide the money?", "")
    tr.dialect = "saudi"
    assert tr.translate("Where did you hide the money?")[0] == "ترجمة محلية."
    r.close()


def test_a_line_is_asked_about_once_and_short_or_known_lines_never(google):
    cache = Cache(":memory:")
    r = refiner(google, cache)
    r.seen("No.")  # left to the local engine
    r.seen("The user's own line, already right.", wanted=False)
    r.seen("We leave at dawn, all of us.")
    r.seen("We leave at dawn, all of us")  # the same line read a little differently
    assert _wait(lambda: r.status()["done"] == 1)
    time.sleep(0.15)
    assert len(google.requests) == 1
    asked = google.requests[0]["contents"][0]["parts"][0]["text"]
    # what was said before is given for understanding, the line itself is the only one translated
    assert "No.\nThe user's own line, already right." in asked and asked.endswith("Translate:\n1. We leave at dawn, all of us.")
    r.close()


def test_waiting_lines_go_in_one_request_and_a_spent_quota_only_waits(google):
    google.plan = [429]
    cache = Cache(":memory:")
    r = refiner(google, cache, pauses_s=(0.3,))
    r.seen("The first line that was said here.")
    assert _wait(lambda: r.status()["state"] == "waiting")
    assert r.status()["error"] and r.status()["done"] == 0
    for i in range(3):
        r.seen(f"Another line number {i} was said.")
    assert _wait(lambda: r.status()["done"] == 4)  # nothing was lost while it waited
    assert r.status()["state"] == "ok" and len(google.requests) == 2  # the refused one, then all four together
    assert google.requests[1]["contents"][0]["parts"][0]["text"].count("\n4. ") == 1
    r.close()


def test_a_refused_key_stops_asking(google):
    google.plan = [403]
    r = refiner(google, Cache(":memory:"))
    r.seen("The first line that was said here.")
    assert _wait(lambda: r.status()["state"] == "bad_key")
    r.seen("A second line that was said here.")
    time.sleep(0.15)
    assert len(google.requests) == 1 and r.status()["done"] == 0
    r.close()


def test_an_answer_that_is_not_arabic_is_not_stored(google):
    google.arabic_for = lambda line: "I cannot translate this line."
    cache = Cache(":memory:")
    r = refiner(google, cache)
    r.seen("Where did you hide the money?")
    assert _wait(lambda: r.failed == 1)
    assert cache.get_better("g", "Where did you hide the money?", "msa") is None
    r.close()
    google.arabic_for = Google.arabic_for


# -- on the TV ------------------------------------------------------------------------------


def test_a_line_on_the_tv_is_rewritten_in_place_and_comes_ready_next_time(google, tmp_path):
    ocr = OcrEngine()
    s = synth.make_samples(1, 1, seed=11)
    line, empty = s[0], s[1]

    class Local:
        name = "local"
        calls = 0

        def translate(self, text, context=None):
            Local.calls += 1
            return "ترجمة محلية."

    cache = Cache(":memory:")
    log = tmp_path / "session.jsonl"
    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe = Pipeline(link, CachedTranslator(Local(), cache, profile="g"), ocr, log_path=log)
        shown, rewritten = [], []
        pipe.on_line = lambda en, ar: shown.append(ar)
        pipe.on_better = lambda en, ar: rewritten.append(ar)
        hold = threading.Event()
        r = refiner(google, cache)
        # the answer is held back until the local Arabic is up, as on a real connection
        r.on_better = lambda en, ar: (hold.wait(3.0), pipe.better_arrived(en, ar))
        pipe.refiner = r
        t = 100.0

        def feed(frame, n):
            nonlocal t
            for _ in range(n):
                pipe.process(frame, t)
                t += 1 / 30

        feed(line.frame, 10)
        assert _wait(lambda: tv.layer_ids() == ["fill", "text"])
        assert shown == ["ترجمة محلية."]  # the local Arabic went up without waiting
        before = tv.composite().tobytes()
        hold.set()
        assert _wait(lambda: rewritten == ["أذكى 1: سطر عربي."])
        assert _wait(lambda: tv.composite().tobytes() != before)  # other words are on the TV now
        assert tv.layer_ids() == ["fill", "text"] and pipe.bettered == 1

        # the line leaves and comes back: the stored Arabic from the first moment, nobody asked
        deadline = time.monotonic() + 8.0
        while tv.layer_ids() and time.monotonic() < deadline:
            feed(empty.frame, 1)
            time.sleep(0.03)
        feed(line.frame, 10)
        assert _wait(lambda: len(shown) == 2)
        assert shown[1] == "أذكى 1: سطر عربي." and Local.calls == 1 and len(google.requests) == 1
        pipe.close()
    kinds = [json.loads(l).get("kind") for l in log.read_text(encoding="utf-8").splitlines()]
    assert kinds.count("better") == 1


# -- the app --------------------------------------------------------------------------------


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "profiles").mkdir(parents=True)
    (repo / "profiles" / "default.yaml").write_text("title: Any game\nglossary: {}\n", encoding="utf-8")
    home = tmp_path / "home"
    monkeypatch.setattr(paths, "HOME_DIR", home)
    monkeypatch.setattr(paths, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(paths, "REPO", repo)
    yield repo


def test_the_key_is_saved_privately_and_never_given_to_the_page(sandbox, google, monkeypatch):
    from tarjim.app import server

    monkeypatch.setattr(gemini, "API", google.base)
    monkeypatch.setattr(gemini.check, "__kwdefaults__", {"base": google.base})
    view = server.gemini_view()
    assert view["has_key"] is False and view["model"] == gemini.DEFAULT_MODEL
    assert server.gemini_test({}) == {"ok": False, "error": "اكتب المفتاح أول."}
    assert server.gemini_test({"key": "AIzaSy-a-long-test-key-1234"})["ok"] is True
    view = server.gemini_save({"key": "AIzaSy-a-long-test-key-1234", "enabled": True, "model": "my model/../x"})
    assert view["has_key"] is True and view["tail"] == "1234" and view["model"] == "mymodel..x"
    assert "AIzaSy" not in json.dumps(view)
    assert gemini.settings()["key"] == "AIzaSy-a-long-test-key-1234"
    assert server.gemini_save({"enabled": False})["enabled"] is False
    assert gemini.settings()["key"]  # switching it off keeps the key
    assert server.gemini_save({"key": ""})["has_key"] is False
    assert any("/api/gemini".startswith(p) for p in server.LOCAL_ONLY)  # a paired phone cannot read or set it


def test_each_game_has_its_own_arabic(sandbox):
    from tarjim.app import server

    server.profile_save({"name": "gta6", "title": "Grand Theft Auto VI"})
    server.profile_save({"name": "pbz", "title": "Phantom Blade Zero"})
    assert server.profile_view("gta6")["dialect"] == "saudi"  # street talk by default
    assert server.profile_view("pbz")["dialect"] == "msa"
    server.profile_save({"name": "pbz", "dialect": "saudi"})
    server.profile_save({"name": "gta6", "dialect": "msa"})
    server.profile_save({"name": "gta6", "dialect": "klingon"})  # ignored
    assert (server.profile_view("pbz")["dialect"], server.profile_view("gta6")["dialect"]) == ("saudi", "msa")


def test_every_change_of_state_goes_to_the_session_log_without_the_key(google):
    google.plan = [429]
    notes = []
    r = refiner(google, Cache(":memory:"), pauses_s=(0.1,))
    r.log = notes.append
    r.seen("The first line that was said here.")
    assert _wait(lambda: r.status()["done"] == 1)
    r.close()
    assert [(n["kind"], n["state"]) for n in notes] == [("gemini", "failed"), ("gemini", "ok")]
    assert notes[0]["why"] == "quota" and KEY not in json.dumps(notes)


def test_a_key_given_while_translating_is_used_at_once(sandbox, google, monkeypatch):
    """The key used to count only from the next start: a user who pasted it while playing got
    no better Arabic for the whole session, and nothing said why."""
    from tarjim.app import server
    from tarjim.app.session import Hub, LiveSession

    monkeypatch.setattr(gemini, "API", google.base)
    logged = []

    class Pipe:  # what the session needs of a running pipeline
        refiner = None
        on_better = None
        translator = CachedTranslator(type("E", (), {"name": "e", "translate": lambda self, t, context=None: "سطر."})(),
                                      Cache(":memory:"), profile="default")

        def better_arrived(self, en, ar):
            pass

        def log_record(self, rec):
            logged.append(rec)

    session = LiveSession(Hub())
    session._pipe, session.state, session.profile = Pipe(), "running", "default"
    monkeypatch.setattr(server, "live", session)
    assert session.apply_gemini() and Pipe.refiner is None and logged[-1]["why"] == "no key"
    server.gemini_save({"key": "AIzaSy-a-long-test-key-1234", "enabled": True})
    r = session._pipe.refiner
    assert r is not None and session.refiner is r and logged[-1]["state"] == "on"
    r.gap_s = 0.0
    r.seen("Where did you hide the money?")
    assert _wait(lambda: r.status()["done"] == 1)
    server.gemini_save({"enabled": False})  # switched off: taken away at once
    assert session._pipe.refiner is None and logged[-1]["why"] == "switched off"
    assert "AIzaSy" not in json.dumps(logged, default=str)
    session.state = "idle"
    assert session.apply_gemini() is False


def test_the_gap_widens_when_refused_for_quota_and_eases_back(google, monkeypatch):
    monkeypatch.setattr(gemini, "EASE_AFTER", 2)
    google.plan = [429, 429]
    r = refiner(google, Cache(":memory:"), gap_s=0.02, pauses_s=(0.02,))
    assert r.status()["gap_s"] == 0.0 and r.gap_s == 0.02
    r.seen("The first line that was said here.")
    assert _wait(lambda: r.status()["done"] == 1)
    widened = r.gap_s
    assert widened >= 1.5 and widened <= gemini.GAP_MAX_S  # longer at each refusal, from at least a second
    r.gap_s = 0.05  # (so the test need not wait out the real gap)
    for i in range(4):
        r.seen(f"Another line number {i} was said, one at a time.")
        assert _wait(lambda: r.status()["done"] == i + 2)
    assert r.gap_s < 0.05 and r.gap_s >= r.min_gap_s  # answered again and again: shorter, never under the start
    r.close()
