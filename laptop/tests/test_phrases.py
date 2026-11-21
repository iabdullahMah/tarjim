"""One wording for the lines a game says again and again, and a key's spent allowance."""

import time

import pytest

from tarjim import gemini, saudi
from tarjim.translate import Cache, CachedTranslator

from .test_gemini import KEY, Google, _wait


class Local:
    name = "local"

    def __init__(self):
        self.calls = []

    def translate(self, text, context=None):
        self.calls.append(text)
        return "ترجمة فصحى."


def make(dialect="saudi"):
    engine = Local()
    tr = CachedTranslator(engine, Cache(":memory:"), profile="g")
    tr.dialect = dialect
    return engine, tr


def test_an_everyday_line_has_one_colloquial_wording_and_keeps_its_own_mark():
    assert saudi.lookup("Let's go!") == "يلا!" and saudi.lookup("let’s go") == "يلا"
    assert saudi.lookup("Okay.") == "تمام." and saudi.lookup("OKAY") == "تمام"
    assert saudi.lookup("What?") == "وش؟" and saudi.lookup("Oh, okay...") == "أها، تمام..."
    assert saudi.lookup("- I don't know.") == "ما أدري."
    # only whole lines: a sentence that merely starts the same way is the translator's
    assert saudi.lookup("Let's go to the car.") is None and saudi.lookup("Okay, I will call him.") is None
    assert saudi.lookup("") is None and saudi.lookup("...") is None


def test_the_table_holds_nothing_that_changes_with_a_man_or_a_woman():
    """A command or an adjective is worded for a man or for a woman; the table cannot know
    which, so it holds none (those go to the translator, which is told who is spoken to)."""
    for key in ("listen", "look", "run", "stop", "sorry", "you sure", "ready", "shut up", "trust me", "calm down"):
        assert key not in saudi.PHRASES, key
    assert all(k == k.lower() and k.strip() == k for k in saudi.PHRASES)
    assert all(any("؀" <= ch <= "ۿ" for ch in v) for v in saudi.PHRASES.values())


def test_a_colloquial_game_says_them_the_same_way_every_time_without_any_engine():
    engine, tr = make("saudi")
    for _ in range(3):
        assert tr.translate("Let's go!")[:2] == ("يلا!", True)
    assert tr.last_source == "phrase" and engine.calls == []
    # a line made only of such sentences is made of them
    assert tr.translate("Okay. Let's go.")[0] == "تمام. يلا." and engine.calls == []
    # anything else is translated as before
    assert tr.translate("Let's go to the old bridge.")[0] == "ترجمة فصحى." and len(engine.calls) == 1
    assert tr.translate("Okay. Meet me at the old bridge.")[0] == "ترجمة فصحى."


def test_a_game_in_standard_arabic_is_left_to_its_engine():
    engine, tr = make("msa")
    assert tr.translate("Let's go!")[0] == "ترجمة فصحى." and tr.last_source == "engine"


def test_the_users_own_wording_still_wins_over_the_table():
    engine, tr = make("saudi")
    tr.fix("Let's go!", "مشينا!")
    assert tr.translate("Let's go!")[0] == "مشينا!" and tr.last_source == "fix"


def test_a_line_made_of_sentences_answered_before_needs_no_request():
    engine, tr = make("saudi")
    tr.cache.put_better("g", "Meet me at the old bridge.", "saudi", "قابلني عند الجسر القديم.")
    assert tr.translate("Okay. Meet me at the old bridge.")[0] == "تمام. قابلني عند الجسر القديم."
    assert tr.last_source == "phrase" and engine.calls == []
    # one sentence not known: the whole line goes to the engine, nothing is patched together
    assert tr.translate("Okay. Meet me at the new bridge.")[0] == "ترجمة فصحى."


# -- a key's allowance ------------------------------------------------------------------------


@pytest.fixture()
def google():
    g = Google()
    yield g
    g.close()


def test_one_word_is_not_worth_a_request_and_two_are(google):
    r = gemini.Refiner(Cache(":memory:"), "g", key=KEY, model="m", base=google.base, gap_s=0.0, pauses_s=(0.05,))
    for line in ("Yeah.", "What?", "Beautiful!"):
        assert not r.wants(line)
        r.seen(line)
    # a goal or a curse of two words is worth it: the local engine words those badly
    assert r.wants("Follow Someone") and r.wants("Get lost!")
    r.seen("Follow Someone")
    assert _wait(lambda: r.status()["done"] == 1)
    time.sleep(0.15)
    assert len(google.requests) == 1
    r.close()


def test_a_busy_service_keeps_a_key_silent_for_seconds_not_for_long():
    assert gemini.NET_PAUSES_S[0] <= 3.0 < gemini.PAUSES_S[0]  # quota waits longer than a hiccup
    assert gemini.TIMEOUT_S <= 8.0


def test_a_name_written_on_the_picture_is_not_speech_and_a_goal_is():
    from tarjim import terms
    from tarjim.vision.ocr import looks_like_label

    for label in ("Gas Station", "Pawn Shop", "Port Somewhere", "Somewhere", "Upgrade ($12,500)", "Some Ave"):
        assert looks_like_label(label) and not terms.starts_with_command(label), label
    for goal in ("Follow Someone", "Approach Someone"):
        assert looks_like_label(goal) and terms.starts_with_command(goal), goal  # kept by its verb
    for speech in ("Get lost!", "Really, guys?", "Oh, good!", "Very funny.", "I'm listening.", "Wait, Sam!",
                   "We leave at dawn", "Uh huh"):
        assert not looks_like_label(speech), speech


def test_a_key_refused_again_and_again_is_reported_spent_until_it_answers(google):
    google.plan = [429] * gemini.SPENT_AFTER
    r = gemini.Refiner(Cache(":memory:"), "g", key=KEY, model="m", base=google.base, gap_s=0.0, pauses_s=(0.02,))
    assert r.status()["spent"] is False
    r.seen("The first line that was said here.")
    assert _wait(lambda: r.status()["spent"], timeout=8)
    assert "لليوم" in r.status()["providers"][0]["error"]
    assert _wait(lambda: r.status()["done"] == 1, timeout=8)  # then it answers again
    assert r.status()["spent"] is False
    r.close()


def test_spent_means_every_key_that_could_answer(google):
    from .test_more_keys import Other

    other = Other()
    try:
        google.plan = [429] * (gemini.SPENT_AFTER + 2)
        r = gemini.Refiner(Cache(":memory:"), "g", key=KEY, model="m", base=google.base, gap_s=0.0,
                           pauses_s=(0.02,), extra=[other.provider()])
        for i in range(6):
            r.seen(f"Line number {i} that somebody said out loud.")
            time.sleep(0.05)
        assert _wait(lambda: r.status()["done"] >= 1)
        assert r.status()["spent"] is False  # the other key still answers
        r.close()
    finally:
        other.close()
