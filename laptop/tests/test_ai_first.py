"""One Arabic per line: with the stronger translator answering, a line waits for its answer
and goes up once; the local Arabic is only what is shown when no answer comes in time."""

import json
import threading
import time

import pytest

import tarjim.vision.arabic  # noqa: F401
from tarjim import gemini
from tarjim.faketv import FakeTv
from tarjim.pipeline import Pipeline
from tarjim.translate import Cache, CachedTranslator
from tarjim.tvlink import TvLink
from tarjim.vision.ocr import OcrEngine

from . import synth
from .test_gemini import KEY, Google, _wait


@pytest.fixture(scope="module")
def ocr():
    return OcrEngine()


@pytest.fixture()
def google():
    g = Google()
    yield g
    g.close()


class Local:
    name = "local"

    def translate(self, text, context=None):
        return "ترجمة محلية."


def make(google, tv_link, ocr, tmp_path, *, hold=None, wait_ms=1500.0, plan=()):
    google.plan = list(plan)
    cache = Cache(":memory:")
    pipe = Pipeline(tv_link, CachedTranslator(Local(), cache, profile="g"), ocr, log_path=tmp_path / "s.jsonl")
    r = gemini.Refiner(cache, "g", key=KEY, model="m", base=google.base, gap_s=0.0, pauses_s=(5.0,))
    if hold is None:
        r.on_better = pipe.better_arrived
    else:  # the answer is kept back until the test lets it go, as a slow service would
        r.on_better = lambda en, ar: (hold.wait(5.0), pipe.better_arrived(en, ar))
    pipe.refiner = r
    pipe.ai_first = True
    pipe.ai_wait_ms = wait_ms
    shown = []
    pipe.on_line = lambda en, ar: shown.append(ar)
    return pipe, r, shown


def feeder(pipe):
    t = [100.0]

    def feed(frame, n):
        for _ in range(n):
            pipe.process(frame, t[0])
            t[0] += 1 / 30

    return feed


def test_a_line_waits_for_the_answer_and_goes_up_once_in_it(google, ocr, tmp_path):
    line = synth.make_samples(1, 0, seed=11)[0]
    hold = threading.Event()
    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe, r, shown = make(google, link, ocr, tmp_path, hold=hold)
        feed = feeder(pipe)
        feed(line.frame, 10)
        pipe.wait_idle()
        time.sleep(0.3)
        feed(line.frame, 2)
        assert tv.layer_ids() == [] and shown == []  # nothing yet: not the local Arabic either
        hold.set()
        assert _wait(lambda: tv.layer_ids() == ["fill", "text"])
        assert shown == ["أذكى 1: سطر عربي."]  # once, in the answer's Arabic
        feed(line.frame, 6)
        time.sleep(0.2)
        assert shown == ["أذكى 1: سطر عربي."] and pipe.ai_shown == 1 and pipe.ai_late == 0 and pipe.bettered == 0
        pipe.close()
    recs = [json.loads(l) for l in (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r.get("source") for r in recs if "kind" not in r] == ["better"]
    assert not [r for r in recs if r.get("kind") == "better"]  # nothing was rewritten in place


def test_no_answer_in_time_shows_the_local_arabic_and_a_late_answer_changes_nothing(google, ocr, tmp_path):
    line = synth.make_samples(1, 0, seed=11)[0]
    hold = threading.Event()
    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe, r, shown = make(google, link, ocr, tmp_path, hold=hold, wait_ms=250.0)
        feed = feeder(pipe)
        feed(line.frame, 10)
        pipe.wait_idle()
        deadline = time.monotonic() + 3.0
        while not shown and time.monotonic() < deadline:
            feed(line.frame, 1)
            time.sleep(0.03)
        assert shown == ["ترجمة محلية."] and pipe.ai_late == 1  # its time was up: the line is not lost
        assert _wait(lambda: tv.layer_ids() == ["fill", "text"])
        before = tv.composite().tobytes()
        hold.set()  # the answer comes now, too late
        assert _wait(lambda: r.status()["done"] == 1)
        feed(line.frame, 4)
        time.sleep(0.2)
        assert shown == ["ترجمة محلية."] and tv.composite().tobytes() == before  # the line on the TV did not change
        # ... but it is kept: the next time this line is shown in it from the first moment
        assert pipe.translator.translate(line.text)[0] == "أذكى 1: سطر عربي."
        pipe.close()


def test_a_service_that_is_refusing_does_not_hold_lines_back(google, ocr, tmp_path):
    samples = synth.make_samples(2, 1, seed=11)
    first, second, empty = samples[0], samples[1], samples[2]
    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe, r, shown = make(google, link, ocr, tmp_path, wait_ms=5000.0, plan=[429])
        feed = feeder(pipe)
        feed(first.frame, 10)
        pipe.wait_idle()
        # the first line was waiting when the refusal came: it goes up in the local Arabic at once
        deadline = time.monotonic() + 3.0
        while not shown and time.monotonic() < deadline:
            feed(first.frame, 1)
            time.sleep(0.03)
        assert shown == ["ترجمة محلية."] and r.state == "waiting"
        feed(empty.frame, 4)
        t0 = time.monotonic()
        feed(second.frame, 10)
        assert _wait(lambda: len(shown) == 2)
        assert shown[1] == "ترجمة محلية." and time.monotonic() - t0 < 2.0  # not held back at all
        pipe.close()


def test_a_line_still_waiting_when_the_next_one_comes_is_not_lost(google, ocr, tmp_path):
    samples = synth.make_samples(2, 1, seed=11)
    first, second, empty = samples[0], samples[1], samples[2]
    hold = threading.Event()
    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe, r, shown = make(google, link, ocr, tmp_path, hold=hold, wait_ms=8000.0)
        feed = feeder(pipe)
        feed(first.frame, 10)
        pipe.wait_idle()
        feed(empty.frame, 4)
        feed(second.frame, 10)
        pipe.wait_idle()
        feed(second.frame, 2)
        assert shown == ["ترجمة محلية."]  # the first line went up in the local Arabic when the second arrived
        hold.set()
        assert _wait(lambda: len(shown) == 2)
        assert shown[1].startswith("أذكى")  # and the second got its answer
        pipe.close()


def test_the_users_own_line_and_a_stored_answer_never_wait(google, ocr, tmp_path):
    line = synth.make_samples(1, 0, seed=11)[0]
    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe, r, shown = make(google, link, ocr, tmp_path, hold=threading.Event(), wait_ms=8000.0)
        pipe.translator.fix(line.text, "ترجمتي أنا.")
        feed = feeder(pipe)
        t0 = time.monotonic()
        feed(line.frame, 10)
        assert _wait(lambda: shown == ["ترجمتي أنا."]) and time.monotonic() - t0 < 2.0
        assert google.requests == []  # nobody was asked
        pipe.close()


def test_the_switch_is_on_by_default_and_saved(tmp_path, monkeypatch):
    from tarjim import paths
    from tarjim.app import server

    repo = tmp_path / "repo"
    (repo / "profiles").mkdir(parents=True)
    monkeypatch.setattr(paths, "HOME_DIR", tmp_path / "home")
    monkeypatch.setattr(paths, "CONFIG_FILE", tmp_path / "home" / "config.json")
    monkeypatch.setattr(paths, "REPO", repo)
    assert server.gemini_view()["wait"] is True
    assert server.gemini_save({"wait": False})["wait"] is False and gemini.settings()["wait"] is False
    assert server.gemini_save({"enabled": True})["wait"] is False  # another change leaves it as it is
