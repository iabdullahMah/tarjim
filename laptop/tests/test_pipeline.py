import time
from pathlib import Path

import numpy as np
import pytest

from tarjim.faketv import FakeTv
from tarjim.pipeline import Pipeline
from tarjim.translate import Cache, CachedTranslator, apply_glossary, normalize_arabic, normalize_english
from tarjim.tvlink import TvLink
from tarjim.vision.detect import detect
from tarjim.vision.ocr import OcrEngine, join_lines, looks_like_subtitle, split_speaker
from tarjim.vision.tracker import Event

from . import synth
from .test_vision import SAMPLES, load_sample


@pytest.fixture(scope="module")
def ocr():
    return OcrEngine()


def _cer(ref: str, hyp: str) -> tuple[int, int]:
    """Levenshtein distance and reference length."""
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i]
        for j, hc in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[-1], len(ref)


# -- OCR -----------------------------------------------------------------------


def test_ocr_character_error_rate_on_synthetic_set(ocr):
    errors = total = 0
    worst = []
    for s in synth.make_samples(n_text=36, n_empty=0, seed=21):
        b = detect(s.frame)
        assert b is not None, s.text
        got = ocr.read(s.frame, b)
        e, n = _cer(s.text, got.text)
        errors += e
        total += n
        if e:
            worst.append((s.style, s.text, got.text))
    assert errors / total <= 0.02, worst


@pytest.mark.parametrize("image", ["uncharted-two-lines.webp", "rdr2-boxed-micah.webp"])
def test_ocr_reads_user_samples_exactly(ocr, image):
    expected = (SAMPLES / (Path(image).stem + ".txt")).read_text(encoding="utf-8-sig").strip()
    frame = load_sample(image)
    got = ocr.read(frame, detect(frame))
    assert got.ok and got.text == expected


def test_text_helpers():
    assert join_lines(["Now you have to stay", "with everyone else."]) == "Now you have to stay with everyone else."
    assert join_lines(["an unbeliev-", "able story"]) == "an unbelievable story"
    assert split_speaker("MARIA: We go around.") == ("MARIA", "We go around.")
    assert split_speaker("We go around: now.") == (None, "We go around: now.")
    assert looks_like_subtitle("Keep your voice down.")
    assert not looks_like_subtitle("12:45") and not looks_like_subtitle("警告 危険")
    assert not looks_like_subtitle("PS5") and not looks_like_subtitle("OLD TOWN")
    assert looks_like_subtitle("No.") and looks_like_subtitle("RUN!") and looks_like_subtitle("We have 12 men.")
    # a radio caption or a menu bar, however long, is not speech; a shouted line still is
    assert not looks_like_subtitle("NIGHT FM SUMMER SONG THE MIDNIGHT BAND")
    assert not looks_like_subtitle("SELECT  ZOOM  BACK")
    assert looks_like_subtitle("GET DOWN, ALL OF YOU!") and looks_like_subtitle("THEY'RE COMING...")


# -- translation plumbing (no model) ------------------------------------------------


class FakeEngine:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def translate(self, text, context=None):
        self.calls += 1
        return '  "ترجمة: ' + text[:10] + ' , نعم?"  '


def test_cache_hit_is_fast_and_skips_the_engine():
    engine = FakeEngine()
    tr = CachedTranslator(engine, Cache(":memory:"), profile="p")
    ar1, hit1, _ = tr.translate("Keep  your voice down.")
    ar2, hit2, ms2 = tr.translate("Keep your voice down.")  # same after normalisation
    assert (hit1, hit2) == (False, True) and ar1 == ar2 and engine.calls == 1
    assert ms2 < 2.0
    other = CachedTranslator(engine, tr.cache, profile="other-game")
    assert other.translate("Keep your voice down.")[1] is False  # the profile is part of the key


def test_arabic_and_english_normalisation():
    assert normalize_english("  It’s   “fine”  ") == 'It\'s "fine"'
    assert normalize_arabic('"هل سمعت ذلك ?"') == "هل سمعت ذلك؟"
    assert normalize_arabic("نعم , لا ;") == "نعم، لا؛"
    assert normalize_arabic("انتظر......") == "انتظر..."
    assert normalize_arabic("بَنَى أَبِي هَذَا المَنْزِلَ") == "بنى أبي هذا المنزل"
    assert normalize_arabic("شكراً جزيلاً، تحرّك") == "شكراً جزيلاً، تحرّك"  # tanween and shadda stay
    assert normalize_arabic("Arthur, come here") == "Arthur, come here"  # Latin text keeps its commas
    assert apply_glossary("ذهب Arthur إلى البلدة", "Arthur went to town", {"Arthur": "آرثر"}) == "ذهب آرثر إلى البلدة"


def test_idioms_contractions_and_phrase_memory():
    from tarjim.translate import _phrase_lookup, preprocess_english

    assert _phrase_lookup("Piece of cake.") == "أمر سهل جداً."
    assert _phrase_lookup("Hold your horses!") == "تمهّل قليلاً!"
    assert _phrase_lookup("Fair enough...") == "هذا معقول..."
    assert _phrase_lookup("Hm.") == "همم."
    assert _phrase_lookup("Check.") == "تم."
    assert _phrase_lookup("What?") == "ماذا؟"

    assert preprocess_english("(sighs) We gotta move, I'm freezin' out here.") == "We have to move, I'm freezing out here."
    assert preprocess_english("Get that wagon outta here.") == "take that wagon out of here."
    assert preprocess_english("ust making sure you're alright.") == "Just making sure you're alright."
    assert preprocess_english("I dont know what youre talking about.") == "I don't know what you're talking about."
    assert preprocess_english("Either way, it ain't getting us nowhere.") == "Either way, it is not leading us anywhere."
    assert preprocess_english("We're gonna make it.") == "We're going to survive."
    assert preprocess_english("Don't take it out on her.") == "Don't blame her."
    assert preprocess_english("LOOK AT THE HORIZON!") == "Look at the horizon!"


def test_arabic_orthography_and_leftover_english_cleanup():
    assert normalize_arabic("(أنت مصابة يا (فيكتوريا.") == "أنت مصابة يا فيكتوريا."
    assert normalize_arabic("ذهبنا و رأينا المكان.") == "ذهبنا ورأينا المكان."
    assert normalize_arabic("شكراً يا السيد حسن ويا الآنسة سلمى.") == "شكراً يا سيد حسن ويا آنسة سلمى."
    assert normalize_arabic("إهدأ وإنتظر هنا، الإستماع والإهتمام مطلوبان، اعطني المفتاح.") == (
        "اهدأ وانتظر هنا، الاستماع والاهتمام مطلوبان، أعطني المفتاح."
    )
    assert normalize_arabic("⁇ وصلنا إلى البوابة OK xy") == "وصلنا إلى البوابة حسناً"
    assert normalize_arabic("انهم ينتظرون في الداخل..،لا تتأخر!") == "إنهم ينتظرون في الداخل...، لا تتأخر!"


def test_sentence_splitting_and_punctuation_restoration():
    from tarjim.translate import join_sentences, split_sentences

    assert split_sentences("We met J. Carter and Rev. Miller in St. Louis. They were ready.") == [
        "We met J. Carter and Rev. Miller in St. Louis.",
        "They were ready.",
    ]
    assert split_sentences("-Stay behind the wall. -Watch out!") == ["-Stay behind the wall.", "-Watch out!"]
    assert split_sentences("Lock the gate; do not let anyone in.") == ["Lock the gate", "do not let anyone in."]
    assert split_sentences("We reached the tower. Elena: Look at the bridge.") == [
        "We reached the tower.",
        "Elena: Look at the bridge.",
    ]
    assert join_sentences(
        ["Are you ready?", "Move now!", "Wait for me..."],
        ["هل أنت مستعد.", "تحرك الآن.", "انتظرني."],
    ) == "هل أنت مستعد؟ تحرك الآن! انتظرني..."


# -- end to end on FakeTv -------------------------------------------------------------


def _wait(cond, timeout=1.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_end_to_end_overlays_appear_and_clear(ocr):
    s = synth.make_samples(2, 1, seed=11)
    line_a, line_b, empty = s[0], s[1], s[2]

    class DictEngine:
        name = "dict"

        calls = 0

        def translate(self, text, context=None):
            DictEngine.calls += 1
            return {line_a.text: "السطر الأول.", line_b.text: "السطر الثاني."}[text]

    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe = Pipeline(link, CachedTranslator(DictEngine(), Cache(":memory:")), ocr)
        t = 100.0
        seen = []

        def feed(frame, n):
            nonlocal t
            for _ in range(n):
                seen.append(pipe.process(frame, t))
                t += 1 / 30

        feed(empty.frame, 4)
        assert tv.layer_ids() == []
        feed(line_a.frame, 10)
        assert _wait(lambda: tv.layer_ids() == ["fill", "text"])
        assert seen.count(Event.NEW) == 1
        assert pipe.translator.engine.calls == 1  # the early start and the confirmation share one job
        shown = np.asarray(tv.composite().convert("RGB"))
        x0, y0, x1, y1 = line_a.bbox
        assert shown[y0:y1, x0:x1].max() > 150  # Arabic pixels were drawn where the English is
        assert shown[: y0 - 80].max() == 0 and shown[:, : x0 - 150].max() == 0  # and nowhere far from it

        # The line leaves before it could be read: the Arabic stays on a dark
        # plate (no stroke fill: there is no English under it any more) ...
        feed(empty.frame, 4)
        assert _wait(lambda: tv.layer_ids() == ["plate", "text"])
        assert pipe.held == 1
        # ... and is cleared once its reading time is over.
        deadline = time.monotonic() + 4.0
        while tv.layer_ids() and time.monotonic() < deadline:
            feed(empty.frame, 1)
            time.sleep(0.03)
        assert tv.layer_ids() == []
        feed(line_b.frame, 10)
        assert _wait(lambda: tv.layer_ids() == ["fill", "text"])
        assert pipe.lines_shown == 2

        # A new line arriving while the previous one is held replaces it at once.
        feed(empty.frame, 4)
        assert _wait(lambda: "plate" in tv.layer_ids())
        feed(line_a.frame, 10)  # a repeated line: comes from the cache
        assert _wait(lambda: tv.layer_ids() == ["fill", "text"])
        assert pipe.times.mt_cached and max(pipe.times.mt_cached) < 2.0
        pipe.close()
        assert _wait(lambda: tv.layer_ids() == [])


def test_stalled_pipeline_never_leaves_stale_arabic(ocr):
    s = synth.make_samples(1, 0, seed=4)[0]

    class OneEngine:
        name = "one"

        def translate(self, text, context=None):
            return "سطر."

    with FakeTv() as tv:
        link = TvLink(tv.host, tv.port, auto_reconnect=False)
        link.connect()
        pipe = Pipeline(link, CachedTranslator(OneEngine(), Cache(":memory:")), ocr)
        for i in range(10):
            pipe.process(s.frame, 50.0 + i / 30)
        assert _wait(lambda: tv.layer_ids() == ["fill", "text"])
        # The pipeline freezes: no more frames, no refresh. Layers expire by TTL (0.7 s) on their own.
        t0 = time.monotonic()
        assert _wait(lambda: tv.layer_ids() == [], timeout=1.2)
        assert time.monotonic() - t0 <= 1.0
        link.close()


def test_reading_time_scales_with_length():
    from tarjim.pipeline import reading_time_ms

    assert reading_time_ms("نعم.") == 1600.0
    assert 3000 < reading_time_ms("س" * 50) < 5000
    assert reading_time_ms("س" * 300) == 6000.0


def test_reading_starts_at_first_sight_not_after_settling(ocr):
    """The worker starts on the first frame that shows the line; when the
    tracker confirms it a few frames later the Arabic is already there."""
    s = synth.make_samples(1, 1, seed=31)
    line, empty = s[0], s[1]
    started = []

    class SlowEngine:
        name = "slow"

        def translate(self, text, context=None):
            started.append(time.monotonic())
            return "سطر."

    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe = Pipeline(link, CachedTranslator(SlowEngine(), Cache(":memory:")), ocr)
        pipe.process(empty.frame, 10.0)
        t_first = time.monotonic()
        assert pipe.process(line.frame, 10.033) is Event.NONE  # seen, not yet confirmed
        pipe.wait_idle()
        assert started and started[0] - t_first < 0.6  # translation already ran
        assert tv.layer_ids() == []  # but nothing is shown before the line is confirmed
        for i in range(2, 8):
            pipe.process(line.frame, 10.0 + i * 0.033)
        assert _wait(lambda: tv.layer_ids() == ["fill", "text"])
        assert pipe.early_ready == 1 and len(started) == 1
        pipe.close()


def test_line_that_left_during_translation_is_still_shown_on_a_plate(ocr):
    s = synth.make_samples(1, 1, seed=33)
    line, empty = s[0], s[1]
    gate = __import__("threading").Event()

    class GatedEngine:
        name = "gated"

        def translate(self, text, context=None):
            gate.wait(3.0)
            return "سطر طويل يحتاج وقتاً للقراءة."

    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe = Pipeline(link, CachedTranslator(GatedEngine(), Cache(":memory:")), ocr)
        for i in range(8):
            pipe.process(line.frame, 20.0 + i * 0.033)  # confirmed, translation still running
        for i in range(8, 12):
            pipe.process(empty.frame, 20.0 + i * 0.033)  # the English is gone
        assert tv.layer_ids() == []
        gate.set()
        assert _wait(lambda: tv.layer_ids() == ["plate", "text"])  # no fill: nothing to cover any more
        pipe.close()


def test_font_size_is_locked_after_the_first_lines(ocr):
    """Lines of slightly different measured height must all be drawn at one size."""
    from tarjim.vision import render as R

    class Engine:
        name = "e"

        def translate(self, text, context=None):
            return "هذا سطر عربي للتجربة."

    locked = []
    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe = Pipeline(link, CachedTranslator(Engine(), Cache(":memory:")), ocr)
        pipe.on_size_locked = locked.append
        rng = np.random.default_rng(5)
        used = []
        for i, size in enumerate([40, 42, 38, 41, 40, 44, 36, 40]):  # the game font "wobbles" by a few px
            frame, _ = synth.draw_subtitle(synth.background(rng, "dark"), [synth.LINES[i]], "shadow", size, 1000)
            block = detect(frame)
            used.append(pipe._style_for(block).fixed_size)
        assert len(locked) == 1 and pipe.style.fixed_size == locked[0]
        assert len(set(used[4:])) == 1  # from the fifth line on the size never changes
        assert max(used) - min(used) <= 3  # and the first lines already use a running median
        long_text = "هذه جملة طويلة جداً كتبناها لنتأكد أن السطر ينقسم ولا يصغر الخط مهما طال الكلام في هذا المشهد الطويل."
        lay = R.layout_text(long_text, block, pipe.style)
        assert lay.size == pipe.style.fixed_size and len(lay.lines) >= 2
        pipe.close()


@pytest.mark.parametrize("mode,opacity,shown,held", [
    ("plate", 0.62, ["fill", "plate", "text"], ["plate", "text"]),  # a light plate: the English is erased under it
    ("plate", 0.9, ["plate", "text"], ["plate", "text"]),  # a dark plate hides it alone: nothing made from the picture
    ("none", 0.62, ["text"], ["text"]),
])
def test_background_modes(ocr, mode, opacity, shown, held):
    s = synth.make_samples(1, 1, seed=41)
    line, empty = s[0], s[1]

    class Engine:
        name = "e"

        def translate(self, text, context=None):
            return "سطر عربي طويل يحتاج وقتاً للقراءة."

    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe = Pipeline(link, CachedTranslator(Engine(), Cache(":memory:")), ocr, background=mode,
                        plate_opacity=opacity)
        for i in range(12):
            pipe.process(line.frame, 30.0 + i * 0.033)
        assert _wait(lambda: tv.layer_ids() == shown)
        if mode == "plate":
            plate = tv.layers()["plate"]
            x0, y0, x1, y1 = line.bbox
            assert plate.x <= x0 and plate.y <= y0 and plate.x + plate.w >= x1 and plate.y + plate.h >= y1
        for i in range(12, 16):
            pipe.process(empty.frame, 30.0 + i * 0.033)  # English leaves: the Arabic is held as it is
        assert _wait(lambda: tv.layer_ids() == held)  # the fill goes with the English; the look stays
        pipe.close()


def test_a_line_read_before_it_was_whole_is_read_again(ocr):
    """The reading starts at first sight. When the line was still being written then, and the
    finished line is close enough to pass for the same one, it used to go up without its last
    word. The finished line is read again."""
    line = "We should leave before the storm gets here."
    frames = synth.typewriter_frames(line, hold=14)
    part, whole = frames[len(line) - 6], frames[-1]  # "... the storm gets" and the whole line
    asked, shown = [], []

    class EchoEngine:
        name = "echo"

        def translate(self, text, context=None):
            asked.append(text)
            return "سطر."

    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe = Pipeline(link, CachedTranslator(EchoEngine(), Cache(":memory:")), ocr)
        pipe.on_line = lambda en, ar: shown.append(en)
        pipe.process(part, 10.0)
        pipe.wait_idle()  # the early reading of the unfinished line is done
        assert asked and not asked[-1].rstrip().endswith("here.")
        for i in range(1, 10):
            pipe.process(whole, 10.0 + i * 0.033)
        assert _wait(lambda: tv.layer_ids() == ["fill", "text"])
        assert shown == [line]
        pipe.close()


def test_covers_tells_a_whole_line_from_one_still_being_written():
    from tarjim.pipeline import covers

    line = "We should leave before the storm gets here."
    frames = synth.typewriter_frames(line, hold=1)
    whole, part = detect(frames[-1]), detect(frames[len(line) - 6])
    assert covers(whole, whole) and covers(whole, part) and not covers(part, whole)
    two = detect(synth.make_samples(4, 0, seed=7)[3].frame)
    assert len(two.lines) == 2 and covers(two, two)


def test_a_line_that_gets_no_arabic_leaves_its_reason_in_the_log(ocr, tmp_path):
    import json

    rng = np.random.default_rng(5)
    label, _ = synth.draw_subtitle(synth.background(rng, "dark"), ["OLD TOWN ROAD"], "shadow", 42, 1000)
    speech = synth.make_samples(1, 0, seed=12)[0]

    class BrokenEngine:
        name = "broken"

        def translate(self, text, context=None):
            raise RuntimeError("engine fell over")

    log = tmp_path / "session.jsonl"
    with FakeTv() as tv, TvLink(tv.host, tv.port) as link:
        link.connect()
        pipe = Pipeline(link, CachedTranslator(BrokenEngine(), Cache(":memory:")), ocr, log_path=log)
        t = 10.0
        for frame in (label, speech.frame):
            for _ in range(8):
                pipe.process(frame, t)
                t += 0.033
            pipe.wait_idle()
            pipe.process(frame, t)
            for _ in range(4):  # the picture goes empty between the two
                t += 0.033
                pipe.process(np.zeros_like(frame), t)
        assert pipe.rejected == 2 and tv.layer_ids() == []
        pipe.close()
    recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in recs] == ["rejected", "rejected"]
    assert recs[0]["reason"] == "not_speech" and recs[0]["en"] == "OLD TOWN ROAD"
    assert recs[1]["reason"].startswith("error: RuntimeError: engine fell over @ test_pipeline.py:")
    assert recs[1]["en"] == speech.text
