"""More keys beside the first Gemini key (gemini.py): another service through the request
shape most services accept, each key at its own pace. Stand-in servers only."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import tarjim.vision.arabic  # noqa: F401  (resolve the font folder before any test moves paths.REPO)
from tarjim import gemini, paths
from tarjim.translate import Cache

from .test_gemini import KEY, Google, _wait

OTHER_KEY = "xai-test-key-not-a-real-one"


class Other:
    """A service that speaks the common shape: POST <base>/chat/completions, Bearer key.
    `wrap` is how it dresses its answer: "plain" JSON, a "fenced" block, or an "object"."""

    def __init__(self, plan=(), wrap="plain") -> None:
        self.plan = list(plan)
        self.wrap = wrap
        self.requests: list[dict] = []
        self.auth: list[str] = []
        self.paths: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                return

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                outer.auth.append(self.headers.get("Authorization", ""))
                outer.paths.append(self.path)
                step = outer.plan.pop(0) if outer.plan else "ok"
                if isinstance(step, int):
                    self.send_response(step)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                asked = body["messages"][1]["content"].split("Translate:\n")[1].splitlines()
                lines = [f"غروك {i}: سطر عربي." for i, _ in enumerate(asked, 1)]
                text = json.dumps(lines, ensure_ascii=False)
                if outer.wrap == "fenced":
                    text = "Here you go:\n```json\n" + text + "\n```"
                elif outer.wrap == "object":
                    text = json.dumps({"lines": lines}, ensure_ascii=False)
                data = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def provider(self, **kw):
        kw.setdefault("gap_s", 0.0)
        return gemini.Provider("openai", OTHER_KEY, "some-grok", self.base, name="Grok", id="x1", **kw)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def other():
    o = Other()
    yield o
    o.close()


@pytest.fixture()
def google():
    g = Google()
    yield g
    g.close()


@pytest.mark.parametrize("wrap", ["plain", "fenced", "object"])
def test_another_service_is_asked_in_the_common_shape_however_it_dresses_its_answer(other, wrap):
    other.wrap = wrap
    assert gemini.check_provider(other.provider()) == "غروك 1: سطر عربي."
    assert other.paths == ["/v1/chat/completions"] and other.auth == [f"Bearer {OTHER_KEY}"]
    sent = other.requests[0]
    assert sent["model"] == "some-grok" and [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert "JSON array" in sent["messages"][0]["content"] and OTHER_KEY not in json.dumps(sent)


@pytest.mark.parametrize("step, kind", [(429, "quota"), (401, "key"), (404, "key"), (503, "net")])
def test_its_refusals_are_told_apart_the_same_way(other, step, kind):
    other.plan = [step]
    with pytest.raises(gemini.GeminiError) as err:
        gemini.check_provider(other.provider())
    assert err.value.kind == kind and "Grok" in gemini.friendly(err.value, "Grok")


def test_two_keys_share_the_lines_and_each_keeps_its_own_count(google, other):
    cache = Cache(":memory:")
    r = gemini.Refiner(cache, "g", key=KEY, model="some-model", base=google.base, gap_s=0.15, pauses_s=(0.05,),
                       extra=[other.provider(gap_s=0.15)])
    for i in range(12):
        r.seen(f"Line number {i} that somebody said out loud.")
        time.sleep(0.05)
    assert _wait(lambda: r.status()["done"] == 12, timeout=6)
    st = r.status()
    by = {p["name"]: p["done"] for p in st["providers"]}
    assert set(by) == {"Gemini", "Grok"} and by["Gemini"] > 0 and by["Grok"] > 0 and sum(by.values()) == 12
    assert google.requests and other.requests  # both were really asked
    assert st["state"] == "ok" and cache.count_better("g") == 12
    r.close()


def test_when_one_key_is_refused_the_other_carries_on(google, other):
    google.plan = [403]  # the Gemini key is refused for good
    notes = []
    r = gemini.Refiner(Cache(":memory:"), "g", key=KEY, model="some-model", base=google.base, gap_s=0.0,
                       pauses_s=(0.05,), extra=[other.provider()])
    r.log = notes.append
    for i in range(5):
        r.seen(f"Line number {i} that somebody said out loud.")
        time.sleep(0.03)
    assert _wait(lambda: r.status()["done"] == 5, timeout=6)
    st = r.status()
    states = {p["name"]: p["state"] for p in st["providers"]}
    assert states == {"Gemini": "bad_key", "Grok": "ok"} and st["state"] == "ok"  # still working, on one key
    assert len(google.requests) == 1  # the refused key is not asked again
    assert {n["provider"] for n in notes} == {"Gemini", "Grok"}
    assert KEY not in json.dumps(notes) and OTHER_KEY not in json.dumps(notes)
    r.close()


def test_a_session_can_run_on_another_service_alone(other):
    r = gemini.Refiner(Cache(":memory:"), "g", key="", extra=[other.provider()])
    r.seen("Where did you hide the money this time?")
    assert _wait(lambda: r.status()["done"] == 1)
    assert [p["name"] for p in r.status()["providers"]] == ["Grok"]
    r.close()


# -- settings and the app -------------------------------------------------------------------


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


def test_what_a_key_may_be(sandbox):
    grok = gemini.make_extra("grok", "xai-a-long-key-123456")
    assert (grok["kind"], grok["name"], grok["base"]) == ("openai", "Grok", "https://api.x.ai/v1") and grok["model"]
    assert gemini.make_extra("gemini", "AIza-another-long-key")["kind"] == "gemini"
    own = gemini.make_extra("other", "sk-a-long-key-123456", model="kimi-k2", base="https://api.example.com/v1/", name="Kimi")
    assert (own["name"], own["model"], own["base"]) == ("Kimi", "kimi-k2", "https://api.example.com/v1")
    for bad in (
        lambda: gemini.make_extra("nope", "xai-a-long-key-123456"),
        lambda: gemini.make_extra("grok", "short"),
        lambda: gemini.make_extra("other", "sk-a-long-key-123456", model="m", base="http://plain.example.com/v1"),
        lambda: gemini.make_extra("other", "sk-a-long-key-123456", model="m", base="ftp://x"),
        lambda: gemini.make_extra("other", "sk-a-long-key-123456", model="", base="https://api.example.com/v1"),
    ):
        with pytest.raises(ValueError):
            bad()


def test_a_key_is_tried_before_it_is_saved_and_never_given_to_the_page(sandbox, other, monkeypatch):
    from tarjim.app import server

    assert server.gemini_view()["extra"] == [] and set(server.gemini_view()["presets"]) == {"grok", "gemini", "other"}
    # refused by the service: an Arabic sentence, and nothing saved
    other.plan = [401]
    r = server.gemini_extra_add({"preset": "other", "key": OTHER_KEY, "model": "some-grok", "base": other.base, "name": "Grok"})
    assert r["ok"] is False and "Grok" in r["error"] and gemini.extra_keys() == []
    assert server.gemini_extra_add({"preset": "grok", "key": ""})["ok"] is False
    # accepted
    r = server.gemini_extra_add({"preset": "other", "key": OTHER_KEY, "model": "some-grok", "base": other.base, "name": "Grok"})
    assert r["ok"] is True and r["sample"] == "غروك 1: سطر عربي."
    assert [(x["name"], x["model"], x["tail"]) for x in r["extra"]] == [("Grok", "some-grok", OTHER_KEY[-4:])]
    assert OTHER_KEY not in json.dumps(r)
    assert gemini.extra_keys()[0]["key"] == OTHER_KEY  # kept in the private config file
    # the same key again is the same entry, not a second one
    server.gemini_extra_add({"preset": "other", "key": OTHER_KEY, "model": "some-grok", "base": other.base, "name": "Grok"})
    assert len(gemini.extra_keys()) == 1
    assert server.gemini_extra_remove({"id": gemini.extra_keys()[0]["id"]})["extra"] == []
    assert any("/api/gemini/extra".startswith(p) for p in server.LOCAL_ONLY)  # closed to a paired phone


def test_a_key_added_while_translating_joins_at_once(sandbox, google, other, monkeypatch):
    from tarjim.app import server
    from tarjim.app.session import Hub, LiveSession
    from tarjim.translate import CachedTranslator

    monkeypatch.setattr(gemini, "API", google.base)
    logged = []

    class Pipe:
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
    server.gemini_save({"key": "AIzaSy-a-long-test-key-1234", "enabled": True})
    assert [p.name for p in session._pipe.refiner.providers] == ["Gemini"]
    r = server.gemini_extra_add({"preset": "other", "key": OTHER_KEY, "model": "some-grok", "base": other.base, "name": "Grok"})
    assert r["ok"] and [p.name for p in session._pipe.refiner.providers] == ["Gemini", "Grok"]
    assert logged[-1]["keys"] == ["Gemini:gemini-3.5-flash-lite", "Grok:some-grok"]
    assert [p["name"] for p in server.gemini_view()["status"]["providers"]] == ["Gemini", "Grok"]
    server.gemini_save({"key": ""})  # the first key is deleted: the other one alone carries on
    assert [p.name for p in session._pipe.refiner.providers] == ["Grok"]
    session._pipe.refiner.close()
    session.state = "idle"
