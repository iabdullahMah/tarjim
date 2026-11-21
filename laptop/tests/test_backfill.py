"""Catching up (gemini.Backfill): the lines already seen while playing, asked about twenty to
a request while nobody plays. Stand-in servers only."""

import json
import time

import pytest

import tarjim.vision.arabic  # noqa: F401
from tarjim import gemini, paths
from tarjim.translate import Cache, CachedTranslator

from .test_gemini import KEY, Google, _wait


class Local:
    name = "local"

    def translate(self, text, context=None):
        return "ترجمة محلية."


@pytest.fixture()
def google():
    g = Google()
    yield g
    g.close()


@pytest.fixture()
def played(tmp_path):
    """A cache file as play leaves it: 47 lines seen in order, and what is not speech."""
    path = tmp_path / "translations.sqlite"
    tr = CachedTranslator(Local(), Cache(path), profile="g")
    lines = [f"Line number {i} that somebody said out loud." for i in range(47)]
    for line in lines:
        tr.translate(line)
        time.sleep(0.001)
    for other in ("Yeah.", "Gas Station", "PHOTO STUDIO SELECT", "Follow Someone"):
        tr.translate(other)
    tr.cache.close()
    return path, lines


def provider(google, **kw):
    return gemini.Provider("gemini", KEY, "m", google.base, **kw)


def run(job, timeout=15):
    assert _wait(lambda: not job.running, timeout=timeout)
    return job.status()


def test_what_is_worth_asking_about():
    assert gemini.worth_asking("We leave at dawn, all of us.") and gemini.worth_asking("Follow Someone")
    for no in ("Yeah.", "Gas Station", "PHOTO STUDIO SELECT", "12:45", "x2"):
        assert not gemini.worth_asking(no), no


def test_the_lines_seen_are_asked_about_twenty_at_a_time_in_the_order_seen(google, played):
    path, lines = played
    cache = Cache(path)
    assert gemini.pending_lines(cache, "g", "msa") == lines + ["Follow Someone"]  # the goal too, not the label
    cache.close()
    told = []
    job = gemini.Backfill(told.append)
    assert job.start("g", providers=[provider(google)], cache_path=path, gap_s=0.0)
    assert job.start("g", providers=[provider(google)], cache_path=path) is False  # one at a time
    st = run(job)
    assert (st["total"], st["done"], st["stored"], st["error"]) == (48, 48, 48, "")
    assert len(google.requests) == 3  # 20 + 20 + 8, where play would have made 48 requests
    first = google.requests[0]["contents"][0]["parts"][0]["text"]
    assert "1. Line number 0 that" in first and "20. Line number 19 that" in first and "21." not in first
    assert told[-1]["running"] is False and any(t["done"] == 20 for t in told)
    # the next time one of these lines is on screen it is shown in this Arabic, with no request
    tr = CachedTranslator(Local(), Cache(path), profile="g")
    assert tr.translate(lines[5])[0].startswith("أذكى 6:") and tr.last_source == "better"
    assert gemini.pending_lines(tr.cache, "g", "msa") == []  # nothing is asked twice
    tr.cache.close()


def test_lines_answered_or_worded_by_the_user_are_left_out(google, played):
    path, lines = played
    cache = Cache(path)
    cache.put_better("g", lines[0], "msa", "جواب قديم.")
    cache.put_fix("g", lines[1], "صياغتي.")
    cache.put_better("g", lines[2].rstrip("."), "msa", "جواب قديم.")  # the same line read without its full stop
    pending = gemini.pending_lines(cache, "g", "msa")
    assert lines[0] not in pending and lines[1] not in pending and lines[2] not in pending and lines[3] in pending
    assert lines[0] in gemini.pending_lines(cache, "g", "saudi")  # another kind of Arabic is another answer
    cache.close()


def test_a_wrong_count_is_asked_again_in_small_batches_and_nothing_is_lost(google, played, monkeypatch):
    path, lines = played
    real = Google.arabic_for
    calls = []

    def flaky(line):
        calls.append(line)
        return real(line)

    # the first large answer comes back one short
    original_post = gemini._post
    state = {"first": True}

    def post(url, headers, body, timeout):
        data = original_post(url, headers, body, timeout)
        if state["first"]:
            state["first"] = False
            arr = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
            data["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(arr[:-1], ensure_ascii=False)
        return data

    monkeypatch.setattr(gemini, "_post", post)
    job = gemini.Backfill()
    job.start("g", providers=[provider(google)], cache_path=path, gap_s=0.0)
    st = run(job)
    assert (st["done"], st["stored"], st["skipped"]) == (48, 48, 0)
    sizes = [r["contents"][0]["parts"][0]["text"].count("\n") for r in google.requests]
    assert any(n <= gemini.BACKFILL_SMALL for n in sizes)


def test_spent_keys_stop_the_job_and_keep_what_was_stored(google, played):
    path, lines = played
    google.plan = ["ok", 429, 429, 429]
    job = gemini.Backfill()
    job.start("g", providers=[provider(google)], cache_path=path, gap_s=0.0, quota_waits_s=(0.02, 0.02))
    st = run(job)
    assert st["stored"] == 20 and st["done"] == 20 and "بكرة" in st["error"]
    cache = Cache(path)
    assert cache.count_better("g") == 20 and len(gemini.pending_lines(cache, "g", "msa")) == 28
    cache.close()


def test_it_stops_when_asked_and_goes_on_from_there_next_time(google, played):
    path, lines = played
    job = gemini.Backfill()
    job.start("g", providers=[provider(google)], cache_path=path, gap_s=0.6)
    assert _wait(lambda: job.status()["done"] >= 20)
    job.stop(wait=3.0)
    st = job.status()
    assert st["running"] is False and 20 <= st["done"] < 48
    job.start("g", providers=[provider(google)], cache_path=path, gap_s=0.0)
    st2 = run(job)
    assert st2["total"] == 48 - st["done"] and st2["done"] == st2["total"]


def test_no_key_is_said_plainly(played):
    path, _ = played
    job = gemini.Backfill()
    job.start("g", providers=[], cache_path=path)
    assert "مفتاح" in run(job)["error"]


# -- the app --------------------------------------------------------------------------------


def test_the_app_counts_what_is_left_and_gives_way_to_a_session(tmp_path, google, monkeypatch):
    from tarjim.app import server

    repo = tmp_path / "repo"
    (repo / "profiles").mkdir(parents=True)
    (repo / "profiles" / "g.yaml").write_text("title: Some Game\nglossary: {}\n", encoding="utf-8")
    home = tmp_path / "home"
    monkeypatch.setattr(paths, "HOME_DIR", home)
    monkeypatch.setattr(paths, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(paths, "REPO", repo)
    monkeypatch.setattr(gemini, "API", google.base)
    monkeypatch.setattr(server, "_backfill", None)
    tr = CachedTranslator(Local(), Cache(), profile="g")
    for i in range(25):
        tr.translate(f"Line number {i} that somebody said out loud.")
    tr.cache.close()

    assert server.backfill_view("g")["pending"] == 25
    assert server.backfill_start({"profile": "g"})["ok"] is False  # no key yet
    gemini.save_settings(key="AIzaSy-a-long-test-key-1234", enabled=True)
    monkeypatch.setattr(server.live, "state", "running")
    assert server.backfill_start({"profile": "g"})["ok"] is False  # not while translating
    monkeypatch.setattr(server.live, "state", "idle")
    q = server.hub.subscribe()
    try:
        monkeypatch.setattr(gemini, "BACKFILL_GAP_S", 0.0)
        assert server.backfill_start({"profile": "g"})["ok"] is True
        assert _wait(lambda: not server.backfill().running, timeout=15)
    finally:
        server.hub.unsubscribe(q)
    view = server.backfill_view("g")
    assert view["pending"] == 0 and view["stored"] == 25 and view["error"] == ""
    assert any("/api/gemini/backfill".startswith(p) for p in server.LOCAL_ONLY)
    assert "AIzaSy" not in json.dumps(view)
