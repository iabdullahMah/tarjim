"""Everyday short lines in Saudi colloquial Arabic, said the same way every time.

A game says "Okay.", "Come on!" and "Let's go." hundreds of times. With a game set to
colloquial Arabic these went through the local engine, which writes Standard Arabic («هيا
بنا»), or through the stronger translator, which costs one request each and may word the same
line differently from one time to the next. Here each has one fixed wording: it goes up at
once, costs nothing, and is the same every time it is said.

Only lines that read the same whoever says them, to whomever: no commands and no word that
changes with a man or a woman («متأكد», «آسف», «اسمع»), nothing that depends on what came before. Keys are lower case without punctuation; the line's own last mark
(? ! ...) is put back on the Arabic. These are general phrases written for this program, not
lines taken from any game.
"""

from __future__ import annotations

import re

PHRASES: dict[str, str] = {
    # yes, no, fine
    "ok": "تمام", "okay": "تمام", "alright": "زين", "all right": "زين", "fine": "زين", "sure": "أكيد",
    "yeah": "إيه", "yes": "إيه", "yep": "إيه", "yup": "إيه", "uh-huh": "إيه", "no": "لا", "nope": "لا", "nah": "لا",
    "of course": "أكيد", "exactly": "بالضبط", "right": "صح", "that's right": "صح", "true": "صح",
    "no way": "مستحيل", "not really": "مو مرة", "maybe": "يمكن", "i guess": "يمكن", "i think so": "أظن كذا",
    "okay okay": "تمام تمام", "yeah yeah": "إيه إيه", "no no": "لا لا", "no no no": "لا لا لا",
    "oh okay": "أها، تمام", "oh yeah": "إيه والله", "oh no": "يا ساتر", "oh": "أوه",
    # going
    "let's go": "يلا", "lets go": "يلا", "come on": "يلا", "c'mon": "يلا",
    "let's move": "يلا نتحرك",
    "let's get out of here": "يلا نطلع من هنا", "let's do this": "يلا نسويها", "let's do it": "يلا نسويها",
    "here we go": "يلا بدينا", "this way": "من هنا", "over here": "هنا",
    "wait": "لحظة", "wait a minute": "لحظة شوي", "wait a second": "لحظة شوي", "hold on": "لحظة",
    "hang on": "لحظة",
    # asking
    "what": "وش", "what the hell": "وش ذا", "why": "ليش", "why not": "ليش لا", "where": "وين", "who": "مين",
    "how": "كيف", "when": "متى", "really": "صدق", "what happened": "وش صار", "what's going on": "وش السالفة",
    "what's up": "وش الأخبار", "what now": "والحين وش", "now what": "والحين وش", "what is it": "وش فيه", "what is this": "وش ذا",
    "where are we going": "وين رايحين", "who is it": "مين", "got it": "تمام فهمت", "understood": "مفهوم",
    # knowing
    "i know": "أدري", "i don't know": "ما أدري", "i dunno": "ما أدري", "no idea": "ما عندي علم",
    "i see": "فهمت", "i get it": "فهمت", "i don't understand": "ما فهمت", "whatever": "اللي يصير يصير", "it's fine": "عادي",
    "it's okay": "عادي", "no problem": "ما فيه مشكلة", "no worries": "ولا يهمك",
    "i'm fine": "أنا بخير", "i'm okay": "أنا بخير", "i'm good": "أنا تمام", "i'm in": "أنا معكم",
    "my bad": "غلطتي", "excuse me": "لو سمحت",
    # greeting, thanking
    "hey": "هيه", "hi": "هلا", "hello": "هلا", "hey man": "هلا والله", "what's up man": "وش الأخبار يا رجال",
    "bye": "مع السلامة", "goodbye": "مع السلامة", "see you": "أشوفك", "see you later": "أشوفك بعدين",
    "see you around": "أشوفك على خير",
    "good morning": "صباح الخير", "thanks": "شكراً", "thank you": "شكراً", "thanks man": "شكراً يا رجال",
    "thank you so much": "شكراً جزيلاً", "you're welcome": "العفو", "please": "تكفى", "welcome": "حياك",
    "good": "زين", "great": "ممتاز", "nice": "حلو", "perfect": "ممتاز", "good job": "كفو", "well done": "كفو",
    "nice work": "كفو", "not bad": "مو شين", "cool": "حلو", "awesome": "خطير", "good luck": "بالتوفيق",
    "congratulations": "مبروك", "cheers": "بصحتك",
    # reacting
    "damn": "يا ساتر", "oh my god": "يا ساتر", "my god": "يا ساتر", "jesus": "يا ساتر", "oh man": "يا رجال",
    "come on man": "يا رجال يلا", "wow": "ما شاء الله", "whoa": "هوب هوب", "oops": "أوبس", "ouch": "آخ",
    "no kidding": "لا يا شيخ",
    "i can't believe it": "ما أصدق", "that's crazy": "شي ما ينصدق", "that's it": "بس كذا", "that's all": "بس كذا",
    "that's enough": "خلاص يكفي", "enough": "يكفي",
    "help": "الحقوني", "there": "هناك", "here": "هنا", "now": "الحين", "not now": "مو الحين",
    "not yet": "لسا", "almost": "قرّبنا", "again": "مرة ثانية", "one more time": "مرة ثانية", "done": "خلصت",
    "we're done": "خلصنا", "it's over": "خلاص انتهى", "i'm done": "خلصت", "nothing": "ولا شي", "me too": "وأنا بعد",
    "same here": "وأنا بعد", "me neither": "ولا أنا", "it's me": "أنا", "it's him": "هذا هو",
}

_EDGE = re.compile(r"^[\s\"'“”‘’\-–—(\[]+|[\s\"'“”‘’)\]]+$")
_TAIL = re.compile(r"[.!?…]+$")
_INNER = re.compile(r"[,;:]+")
_WS = re.compile(r"\s+")


def _key(text: str) -> tuple[str, str]:
    """(the line as the table writes its keys, the line's own last marks)."""
    s = _EDGE.sub("", text.replace("’", "'"))
    m = _TAIL.search(s)
    tail = m.group(0) if m else ""
    core = _WS.sub(" ", _INNER.sub(" ", _TAIL.sub("", s))).strip().lower()
    return core, tail


def lookup(text: str) -> str | None:
    """The fixed colloquial wording of a short everyday line, or None."""
    core, tail = _key(text)
    ar = PHRASES.get(core)
    if ar is None:
        return None
    if "?" in tail:
        return ar + "؟"
    if "!" in tail:
        return ar + "!"
    if "…" in tail or ".." in tail:
        return ar + "..."
    return ar + "." if tail else ar
