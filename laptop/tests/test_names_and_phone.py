"""Glossary names kept right, the user's own fixes, and the phone-control access rules."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from tarjim import paths
from tarjim.translate import Cache, CachedTranslator


class FakeEngine:
    """Translates like the real small model misbehaves: "Dutch" becomes a nationality,
    "Marston" always gets one other spelling, stand-in names come out reliably."""

    name = "fake"
    WORDS = {"Dutch": None, "Marston": "مارستون", "Arthur": "آرثر", "Edward": "إدوارد", "Harold": "هارولد",
             "Victoria": "فيكتوريا", "Catherine": "كاثرين", "Margaret": "مارغريت", "Sadie": "سادي"}

    def __init__(self):
        self.calls = []

    def translate(self, text, context=None):
        self.calls.append(text)
        out = []
        for word in text.replace(",", " ,").replace(".", " .").split():
            if word == "Dutch":
                out.append("الهولندية" if "thinking" in text or "told" in text else "هولندي")  # unreliable on purpose
            elif word in self.WORDS:
                out.append(f"({self.WORDS[word]})")  # the model likes brackets around names
            elif word in ",.":
                out.append(word)
            else:
                out.append({"Hello": "مرحباً", "is": "", "thinking": "يفكر", "about": "في", "it": "ذلك", "I": "",
                            "told": "أخبرت", "to": "أن", "wait": "ينتظر", "We": "", "got": "أمسكنا"}.get(word, "كلمة"))
        return " ".join(w for w in out if w)


def make(glossary, female=()):
    engine = FakeEngine()
    return engine, CachedTranslator(engine, Cache(":memory:"), profile="g", glossary=glossary, female=set(female))


def test_an_unreliable_name_goes_through_a_stand_in():
    engine, tr = make({"Dutch": "دتش"})
    assert tr.names.mode("Dutch", "دتش")[0] == "proxy"
    arabic, hit, _ = tr.translate("We got Dutch.")
    assert "دتش" in arabic and "هولند" not in arabic and "إدوارد" not in arabic
    assert engine.calls[-1] == "We got Edward."  # the engine never saw the troublesome word
    assert "(" not in arabic  # brackets the engine adds on its own are removed


def test_a_consistent_other_spelling_is_swapped_and_a_good_name_is_left_alone():
    engine, tr = make({"Marston": "مارستن", "Arthur": "آرثر"})
    assert tr.names.mode("Marston", "مارستن") == ("variant", "مارستون")
    assert tr.names.mode("Arthur", "آرثر")[0] == "ok"
    assert "مارستن" in tr.translate("Hello, Marston.")[0]
    assert engine.calls[-1] == "Hello, Marston."  # translated as it is, then corrected
    assert "آرثر" in tr.translate("Hello, Arthur.")[0]


def test_a_womans_name_uses_a_female_stand_in():
    engine, tr = make({"Sadie": "سيدي"}, female={"Sadie"})
    arabic = tr.translate("I told Sadie to wait.")[0]
    # Catherine first: with "Victoria" the real model broke 4 of 8 longer lines, with Catherine none.
    assert engine.calls[-1] == "I told Catherine to wait."
    assert "سيدي" in arabic and "كاثرين" not in arabic


def test_editing_a_name_takes_effect_despite_the_cache():
    engine, tr = make({"Dutch": "دتش"})
    first = tr.translate("We got Dutch.")[0]
    assert tr.translate("We got Dutch.")[1] is True  # second time: from the cache
    tr.glossary = {"Dutch": "داتش"}
    again, hit, _ = tr.translate("We got Dutch.")
    assert hit is False and "داتش" in again and again != first


def test_the_users_own_wording_wins_and_can_be_removed():
    engine, tr = make({})
    tr.translate("Hello there.")
    tr.fix("Hello  there.", "هلا والله")  # spacing differences do not matter
    assert tr.translate("Hello there.") [0] == "هلا والله"
    tr.fix("Hello there.", "")
    assert tr.translate("Hello there.")[0] != "هلا والله"


def test_a_name_inside_a_longer_name_is_part_of_it():
    engine, tr = make({"Sadie Adler": "سايدي آدلر", "Sadie": "سايدي", "Adler": "آدلر", "Dutch": "داتش"})
    assert [en for en, _ in tr._matched("Sadie Adler and Dutch left.")] == ["Sadie Adler", "Dutch"]
    assert [en for en, _ in tr._matched("Sadie went, Adler stayed.")] == ["Sadie", "Adler"]
    # Both names need a stand-in: the shorter one must not use up a second one and fail the line.
    tr.names._memo.update({("Sadie Adler", "سايدي آدلر"): ("proxy", ""), ("Dutch", "داتش"): ("proxy", "")})
    arabic = tr.translate("We got Sadie Adler and Dutch.")[0]
    assert engine.calls[-1] == "We got Edward and Harold."
    assert "سايدي آدلر" in arabic and "داتش" in arabic


def test_a_capitalised_name_is_not_taken_for_the_same_word_in_small_letters():
    engine, tr = make({"Chop": "تشوب", "van der Linde": "فان دير ليند", "O'Driscoll": "أودريسكول"})
    assert tr._matched("Chop, come here!") == [("Chop", "تشوب")]
    assert tr._matched("CHOP! COME HERE!") == [("Chop", "تشوب")]
    assert tr._matched("I will chop the wood.") == []
    assert tr._matched("Van der Linde is gone.") == [("van der Linde", "فان دير ليند")]  # small letters: any case
    assert tr._matched("An O’Driscoll was here.") == [("O'Driscoll", "أودريسكول")]  # curly apostrophe


class BatchEngine(FakeEngine):
    """The fake engine, with the batch call the real engines have."""

    def __init__(self):
        super().__init__()
        self.batches = 0

    def translate_many(self, texts):
        self.batches += 1
        return [FakeEngine.translate(self, t) for t in texts]


def test_names_tried_in_batches_get_the_same_verdict_as_one_by_one():
    glossary = {"Dutch": "دتش", "Marston": "مارستن", "Arthur": "آرثر", "Sadie": "سادي"}
    single = make(glossary)[1]
    single.prepare_names()
    engine = BatchEngine()
    batched = CachedTranslator(engine, Cache(":memory:"), profile="g", glossary=glossary)
    batched.prepare_names()
    for en, ar in glossary.items():
        assert batched.names.mode(en, ar) == single.names.mode(en, ar), en
    assert engine.batches == 2  # the stand-in's sentences, then all four names together
    calls = len(engine.calls)
    batched.prepare_names()
    assert len(engine.calls) == calls  # remembered: nothing is tried twice


def test_trying_names_stops_when_asked():
    engine = BatchEngine()
    tr = CachedTranslator(engine, Cache(":memory:"), profile="g", glossary={f"Name{i}": "اسم" for i in range(12)})
    tr.prepare_names(stop=lambda: True)
    assert engine.calls == []


def test_female_vocative_title_and_plural_agreement():
    engine, tr = make(
        {"Clara": "كلارا", "Elena": "إيلينا", "Nora": "نورا", "Barton": "بارتون"},
        female={"Clara", "Elena", "Nora"},
    )
    # A surname preceded by Mrs./Miss uses a female stand-in even when the surname itself is not marked female.
    tr.translate("Mrs. Barton is waiting.")
    assert "Mrs. Catherine" in engine.calls[-1]
    tr.translate("Mr. Barton is waiting.")
    assert "Catherine" not in engine.calls[-1]

    # Vocative imperatives and 2nd-person verbs are feminised when addressing a woman.
    classMasc = type("MascEngine", (), {
        "name": "m",
        "translate": lambda self, text, context=None: "كاثرين! إنتبه! يجب أن تبقى هنا. تحتاج إلى الراحة. أجبني!",
    })()
    tr_f = CachedTranslator(classMasc, Cache(":memory:"), profile="g", glossary={"Clara": "كلارا"}, female={"Clara"})
    out = tr_f.translate("Clara! Watch out! You need to stay here. Answer me!")[0]
    assert out == "كلارا! انتبهي! يجب أن تبقي هنا. تحتاجين إلى الراحة. أجيبيني!"

    # Three female subjects get feminine plural verbs instead of masculine -وا.
    classPlur = type("PlurEngine", (), {
        "name": "p",
        "translate": lambda self, text, context=None: "كاثرين ومارغريت وإليزابيث غادروا هذا الصباح.",
    })()
    tr_p = CachedTranslator(
        classPlur, Cache(":memory:"), profile="g",
        glossary={"Clara": "كلارا", "Elena": "إيلينا", "Nora": "نورا"},
        female={"Clara", "Elena", "Nora"},
    )
    assert tr_p.translate("Clara, Elena and Nora left this morning.")[0] == "كلارا وإيلينا ونورا غادرن هذا الصباح."


def test_multi_word_names_and_word_name_fallback():
    engine, tr = make({"Silas Vance": "سيلاس فانس", "Oak Manor": "قصر أوك", "Ash": "آش"})
    # Multi-word names always use a stand-in so neither word is left behind or doubled.
    assert tr.names.mode("Silas Vance", "سيلاس فانس") == ("proxy", "")
    assert tr.names.mode("Oak Manor", "قصر أوك") == ("proxy", "")

    # If a name was filed as "ok" in probes but translated as a common noun in a sentence, it retries with a stand-in.
    class AshEngine:
        name = "ash"

        def __init__(self):
            self.calls = []

        def translate(self, text, context=None):
            self.calls.append(text)
            if "Follow Edward" in text:
                return "اتبع إدوارد إلى الكهف."
            if "Edward" in text:
                return "مرحباً إدوارد."
            if "Follow Ash" in text:
                return "اتبع الرماد إلى الكهف."
            return "مرحباً آش."

    ash_engine = AshEngine()
    tr_ash = CachedTranslator(ash_engine, Cache(":memory:"), profile="g", glossary={"Ash": "آش"})
    assert tr_ash.names.mode("Ash", "آش")[0] == "ok"
    ar, _, _ = tr_ash.translate("Follow Ash into the cave.")
    assert ar == "اتبع آش إلى الكهف." and "الرماد" not in ar
    assert ash_engine.calls[-1] == "Follow Edward into the cave."


# -- phone control -----------------------------------------------------------------


@pytest.fixture()
def phone(tmp_path, monkeypatch):
    from tarjim.app import server

    repo = tmp_path / "repo"
    (repo / "profiles").mkdir(parents=True)
    (repo / "profiles" / "default.yaml").write_text("title: Any game\nglossary: {}\n", encoding="utf-8")
    home = tmp_path / "home"
    monkeypatch.setattr(paths, "HOME_DIR", home)
    monkeypatch.setattr(paths, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(paths, "REPO", repo)
    monkeypatch.setattr(paths, "PHONE_APK", repo / "none.apk")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.RemoteHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None, token=None, headers=None):
        h = dict(headers or {})
        if token:
            h["X-Tarjim-Token"] = token
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, headers=h, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    yield server, call
    server.access.set_enabled(False)
    httpd.shutdown()


def test_phone_control_is_closed_until_switched_on_and_paired(phone):
    server, call = phone
    assert call("/")[0] == 403 and call("/api/hello")[0] == 403  # switched off: nothing at all
    server.access.set_enabled(True)
    assert call("/")[0] == 200 and call("/app.js")[0] == 200  # the page itself is public
    assert json.loads(call("/api/hello")[1])["paired"] is False
    assert call("/api/state")[0] == 403 and call("/api/state?k=guess")[0] == 403
    assert call("/api/start", {"profile": "default"})[0] == 403

    assert call("/api/pair", {"pin": "not it"})[0] == 400
    status, body = call("/api/pair", {"pin": server.access.pin, "name": "Test phone"})
    token = json.loads(body)["token"]
    assert status == 200 and len(token) > 30
    assert call("/api/state", token=token)[0] == 200 and call(f"/api/state?k={token}")[0] == 200
    assert json.loads(call("/api/hello", token=token)[1])["paired"] is True
    # A paired phone still cannot do what needs the laptop itself, and other sites cannot use its token.
    for path in ("/api/quit", "/api/phone", "/api/tv/setup", "/api/tv/revert", "/api/ps5/discover", "/api/focus"):
        assert call(path, {}, token=token)[0] == 403
    assert call("/api/state", token=token, headers={"Origin": "http://evil.example"})[0] == 403

    assert [d["name"] for d in server.access.devices()] == ["Test phone"]
    assert "hash" not in server.access.devices()[0] and token not in paths.CONFIG_FILE.read_text(encoding="utf-8")
    server.access.forget(server.access.devices()[0]["id"])
    assert call("/api/state", token=token)[0] == 403


def test_pairing_code_is_single_use_and_wrong_guesses_lock_it(phone):
    server, call = phone
    server.access.set_enabled(True)
    pin = server.access.pin
    assert call("/api/pair", {"pin": pin})[0] == 200
    assert call("/api/pair", {"pin": pin})[0] == 400  # a used code is dead
    from tarjim.app import remote

    old = server.access.pin
    for _ in range(remote.MAX_TRIES):
        call("/api/pair", {"pin": "x"})
    assert server.access.pin != old
    assert call("/api/pair", {"pin": server.access.pin})[0] == 400  # locked for a minute, even with the right code


def test_qr_code_reads_back_as_the_pairing_link():
    import cv2
    import numpy as np

    from tarjim.app import remote

    link = "http://192.168.1.20:47901/?pair=123456"
    svg = remote.qr_svg(link).decode("utf-8")
    assert svg.startswith("<svg") and "#111114" in svg  # the page's near-black
    import segno

    matrix = np.array(segno.make(link, error="m").matrix, dtype=np.uint8)
    img = np.kron(1 - matrix, np.ones((8, 8), np.uint8)) * 255
    img = cv2.copyMakeBorder(img, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255)
    assert cv2.QRCodeDetector().detectAndDecode(img)[0] == link


# -- the user's own lines, found again when the reader reads them a little differently ---------


def test_loose_key_ignores_the_readers_slips_and_nothing_else():
    from tarjim.translate import loose_key

    same = ["I'm fine. Let's go!", "l'm fine, lets go", "I'm fine.Let's  go", "I’m Fine. let's go"]
    assert len({loose_key(t) for t in same}) == 1
    assert loose_key("It's 10 o'clock") == loose_key("lt's 1O o'clock")
    # a different word is a different line: no letter is added, dropped or swapped
    assert loose_key("Don't fail me.") != loose_key("Don't fall me.")
    assert loose_key("We can go now.") != loose_key("We can't go now.")
    assert loose_key("He is here.") != loose_key("She is here.")
    assert loose_key("...") == ""


def test_a_fixed_line_is_found_when_read_a_little_differently():
    engine, tr = make({})
    tr.fix("I'm fine. Let's go, Ill wait.", "أنا بخير. هيا بنا")
    calls = len(engine.calls)
    for read in ("l'm fine. Lets go, Ill wait.", "I'm fine, Let's go,Ill wait", "I'm fine. let's go, lll wait!"):
        ar, hit, _ms = tr.translate(read)
        assert (ar, hit) == ("أنا بخير. هيا بنا", True), read
    assert len(engine.calls) == calls  # the engine was never asked
    # the line on screen asks: the Arabic asks too
    assert tr.translate("l'm fine. Lets go, Ill wait?")[0] == "أنا بخير. هيا بنا؟"
    # another line is still the engine's
    assert tr.translate("I'm fine. Let's go, I'll walk.")[0] != "أنا بخير. هيا بنا"


def test_two_fixed_lines_with_one_loose_key_are_not_guessed_between():
    engine, tr = make({})
    tr.fix("Well, go.", "حسناً، اذهب.")
    tr.fix("We'll go.", "سنذهب.")
    assert tr.translate("Well, go.")[0] == "حسناً، اذهب."  # the exact line always wins
    assert tr.translate("We'll go.")[0] == "سنذهب."
    assert tr.translate("WELL GO")[0] not in ("حسناً، اذهب.", "سنذهب.")


def test_an_older_cache_file_gets_loose_keys_for_its_lines(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE fix (profile TEXT, en TEXT, ar TEXT, ts REAL, PRIMARY KEY (profile, en))")
    db.execute("INSERT INTO fix VALUES ('p', 'I told you, wait here.', 'قلت لك انتظر هنا.', 1.0)")
    db.commit()
    db.close()
    cache = Cache(path)
    try:
        assert cache.get_fix("p", "I told you, wait here.") == "قلت لك انتظر هنا."
        assert cache.get_fix_loose("p", "l told you wait here") == "قلت لك انتظر هنا."
        assert cache.add_fixes("p", [("Stay close.", "ابقَ قريباً.")]) == 1
        assert cache.get_fix_loose("p", "STAY CLOSE") == "ابقَ قريباً."
    finally:
        cache.close()
    Cache(path).close()  # opening it again changes nothing
