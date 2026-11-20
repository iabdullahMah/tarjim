"""Translation: one interface, local engines, an SQLite cache, Arabic clean-up."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from . import gender, paths, terms

MODELS_DIR = paths.REPO / "models"
CACHE_FILE = paths.HOME_DIR / "translations.sqlite"


class TranslateError(Exception):
    pass


class Translator(Protocol):
    name: str

    def translate(self, text: str, context: list[tuple[str, str]] | None = None) -> str: ...


# -- text clean-up & phrase memory -----------------------------------------------

_WS = re.compile(r"\s+")
_HAS_AR = re.compile(r"[؀-ۿ]")

# Short idioms, interjections and conversational responses where NMT models translate word-for-word.
PHRASES: dict[str, str] = {
    "what": "ماذا",
    "hm": "همم",
    "hmm": "همم",
    "hmmm": "همم",
    "mm-hmm": "أها",
    "uh-huh": "أها",
    "check": "تم",
    "whoa": "تمهّل",
    "woah": "تمهّل",
    "ouch": "آخ",
    "damn": "اللعنة",
    "dammit": "اللعنة",
    "damn it": "اللعنة",
    "goddammit": "اللعنة",
    "goddamn it": "اللعنة",
    "dagnabbit": "اللعنة",
    "son of a bitch": "ابن العاهرة",
    "bullshit": "هراء",
    "horseshit": "هراء",
    "horsecrap": "هراء",
    "no way": "مستحيل",
    "on my way": "أنا في طريقي",
    "i'm on my way": "أنا في طريقي",
    "i am on my way": "أنا في طريقي",
    "out of the way": "ابتعد عن الطريق",
    "get out of the way": "ابتعد عن الطريق",
    "after you": "تفضّل قبلي",
    "count me in": "أنا معكم",
    "count me out": "لا تحسبوني معكم",
    "you bet": "بالتأكيد",
    "over my dead body": "على جثتي",
    "piece of cake": "أمر سهل جداً",
    "a piece of cake": "أمر سهل جداً",
    "fair enough": "هذا معقول",
    "suit yourself": "كما تشاء",
    "my bad": "خطئي أنا",
    "so far so good": "كل شيء تمام حتى الآن",
    "long time no see": "لم أرك منذ زمن",
    "never mind": "لا عليك",
    "for the most part": "في الغالب",
    "i'm all ears": "كلّي آذان صاغية",
    "i am all ears": "كلّي آذان صاغية",
    "get a hold of yourself": "تمالك نفسك",
    "get ahold of yourself": "تمالك نفسك",
    "make up your mind": "اتخذ قرارك",
    "hold your horses": "تمهّل قليلاً",
    "take it easy": "على مهلك",
    "easy there": "على مهلك",
    "easy now": "على مهلك",
    "hang on": "انتظر لحظة",
    "hold on": "انتظر لحظة",
    "hang on a second": "انتظر لحظة",
    "hold on a second": "انتظر لحظة",
    "watch out": "انتبه",
    "look out": "انتبه",
    "heads up": "انتبه",
    "stay sharp": "ابقَ متيقظاً",
    "keep it up": "استمر هكذا",
    "keep it coming": "واصل ولا تتوقف",
    "handle with care": "تعامل معه بحذر",
    "don't work too hard": "لا ترهق نفسك بالعمل",
    "don't i know it": "أعرف ذلك جيداً",
    "here goes nothing": "فلنجرّب ونرَ",
    "here goes everything": "توكلنا على الله",
    "second nature": "أمر اعتدت عليه",
    "in the clear": "نحن في أمان الآن",
    "we're in the clear": "نحن في أمان الآن",
    "we are in the clear": "نحن في أمان الآن",
    "call it a day": "لنكتفِ بهذا اليوم",
    "let's call it a day": "لنكتفِ بهذا اليوم",
    "hit the road": "لننطلق الآن",
    "let's hit the road": "لننطلق الآن",
    "give it a rest": "كفّ عن هذا الكلام",
    "knock it off": "توقف عن هذا",
    "cut it out": "توقف عن هذا",
    "leave me be": "اتركني وشأني",
    "will you leave me be": "اتركني وشأني",
    "good luck with that": "بالتوفيق في ذلك",
    "what a joke": "يا لها من مهزلة",
    "spit it out": "قلها وخلّصني",
    "out with it": "قلها وخلّصني",
    "i owe you one": "أنا مدين لك",
    "thank you, i owe you one": "شكراً لك، أنا مدين لك",
    "thanks, i owe you one": "شكراً، أنا مدين لك",
    "beat it": "اغرب عن وجهي",
    "get lost": "اغرب عن وجهي",
    "no hard feelings": "لا ضغينة بيننا",
    "you don't say": "حقاً؟",
    "we're even": "الآن تعادلنا",
    "we are even": "الآن تعادلنا",
    "now we're even": "الآن تعادلنا",
    "take it or leave it": "إما أن تقبل أو ترفض",
    "break a leg": "بالتوفيق",
    "for crying out loud": "بحق الله",
    "give me a break": "ارحمني",
    "i've got your back": "أنا معك وأحميك",
    "i got your back": "أنا معك وأحميك",
    "watch your back": "احذر على نفسك",
    "my lips are sealed": "سرّك في بئر",
    "you're on your own": "أنت وحدك الآن",
    "you are on your own": "أنت وحدك الآن",
    "the coast is clear": "الطريق آمن",
    "i'm all set": "أنا جاهز تماماً",
    "all set": "كل شيء جاهز",
}

_STAGE_DIR = re.compile(
    r"^\s*[\(\[]\s*(?:sighs?|groans?|laughs?|coughs?|gasps?|chuckles?|grunts?|panting|sniffs?|"
    r"clears? throat|whistles?|screams?|cries|sobs?|mutters?|whispers?|yells?|shouts?)\s*[\)\]]\s*",
    flags=re.IGNORECASE,
)
_STUTTER = re.compile(r"\b([A-Za-z])-\1([a-z]{2,})\b")

# Contractions where OCR dropped the apostrophe, plus common single-word informal contractions.
_WORD_REWRITES: dict[str, str] = {
    "dont": "don't", "cant": "can't", "wont": "won't", "didnt": "didn't",
    "doesnt": "doesn't", "isnt": "isn't", "arent": "aren't", "wasnt": "wasn't",
    "werent": "weren't", "hasnt": "hasn't", "havent": "haven't", "hadnt": "hadn't",
    "wouldnt": "wouldn't", "couldnt": "couldn't", "shouldnt": "shouldn't",
    "aint": "ain't", "thats": "that's", "whats": "what's", "heres": "here's",
    "theres": "there's", "wheres": "where's", "whos": "who's", "youre": "you're",
    "theyre": "they're", "weve": "we've", "theyve": "they've", "youve": "you've",
    "youd": "you'd", "theyd": "they'd", "youll": "you'll", "theyll": "they'll",
    "outta": "out of", "gotta": "have to", "gonna": "going to", "wanna": "want to",
    "dunno": "do not know", "lemme": "let me", "gimme": "give me", "kinda": "kind of",
    "sorta": "sort of", "lotta": "lot of", "c'mere": "come here", "c'mon": "come on",
    "y'all": "all of you", "whatcha": "what are you", "gotcha": "got you",
    "betcha": "bet you", "don'tcha": "do not you", "cuz": "because", "till": "until",
    "dagnabbit": "damn it", "dagnabbi": "damn it", "goddammit": "damn it", "dammit": "damn it",
    "horsecrap": "nonsense", "bullcrap": "nonsense", "horseshit": "nonsense",
    "stagecoach": "carriage",
}
_WORD_REWRITE_RX = re.compile(
    r"\b(?:" + "|".join(sorted((re.escape(k) for k in _WORD_REWRITES), key=len, reverse=True)) + r")\b",
    flags=re.IGNORECASE,
)
_IM_IVE_RX = re.compile(r"\bI(m|ve)\b")
_LEAD_DASH = re.compile(r"^[-–—]+\s*")


def _fix_word(m: re.Match) -> str:
    w = m.group(0)
    repl = _WORD_REWRITES[w.lower()]
    if w[0].isupper() and repl[0].islower():
        return repl[0].upper() + repl[1:]
    return repl


# Dialect -in' / -in where the stem drops a silent 'e' or doubles a consonant before -ing.
_G_DROP_MAP = {
    "freezin": "freezing", "gettin": "getting", "runnin": "running", "sittin": "sitting",
    "puttin": "putting", "cuttin": "cutting", "stoppin": "stopping", "droppin": "dropping",
    "robbin": "robbing", "diggin": "digging", "beggin": "begging", "havin": "having",
    "makin": "making", "takin": "taking", "givin": "giving", "livin": "living",
    "leavin": "leaving", "comin": "coming", "movin": "moving", "ridin": "riding",
    "lovin": "loving", "hatin": "hating", "shakin": "shaking", "blazin": "blazing",
    "chargin": "charging", "starvin": "starving", "smokin": "smoking", "jokin": "joking",
    "hopin": "hoping", "usin": "using", "losin": "losing", "choosin": "choosing",
    "causin": "causing", "tramplin": "trampling", "darlin": "darling", "nothin": "nothing",
    "somethin": "something", "mornin": "morning", "evenin": "evening", "happenin": "happening",
    "talkin": "talking", "walkin": "walking", "lookin": "looking", "thinkin": "thinking",
    "workin": "working", "waitin": "waiting", "tryin": "trying", "sayin": "saying",
    "goin": "going", "doin": "doing", "shootin": "shooting", "fightin": "fighting",
    "killin": "killing", "huntin": "hunting", "drinkin": "drinking", "sleepin": "sleeping",
    "kiddin": "kidding", "playin": "playing", "stayin": "staying", "standin": "standing",
    "holdin": "holding", "keepin": "keeping", "messin": "messing", "kickin": "kicking",
    "mindin": "minding", "askin": "asking", "tellin": "telling", "callin": "calling",
    "turnin": "turning", "startin": "starting", "headin": "heading", "wonderin": "wondering",
    "sufferin": "suffering", "dyin": "dying", "lyin": "lying", "cryin": "crying",
}
_G_DROP_APOS = re.compile(r"\b([A-Za-z]{3,}in)'(?![A-Za-z])")
_G_DROP_BARE = re.compile(r"\b(" + "|".join(_G_DROP_MAP) + r")'?(?![A-Za-z])", flags=re.IGNORECASE)


def _fix_g_drop(m: re.Match) -> str:
    word = m.group(1)
    repl = _G_DROP_MAP.get(word.lower(), word + "g")
    return repl.capitalize() if word[0].isupper() else repl


_EN_REWRITES: tuple[tuple[re.Pattern, str], ...] = (
    # Clipped or merged OCR tokens
    (re.compile(r"(?:^|(?<=[.!?]\s))ust\s+(?=[a-z])"), "Just "),
    (re.compile(r"\bI'?know\b"), "I know"),
    (re.compile(r"\bIcan'?t\b"), "I can't"),
    (re.compile(r"\bNowwhat\b", re.I), "Now what"),
    (re.compile(r"\bsee i you\b", re.I), "see if you"),
    (re.compile(r"\bwait to s?ee\b", re.I), "wait to see"),
    (re.compile(r"\bgonna tun\b", re.I), "going to turn"),
    # Multi-word dialect grammar & contractions
    (re.compile(r"\b(I|we) (?:gotta|have to) get on\b", re.I), r"\1 have to get going"),
    (re.compile(r"\b(I|we) should be getting on\b", re.I), r"\1 should get going"),
    (re.compile(r"(?:^|(?<=[\s,]))'em\b", re.I), "them"),
    (re.compile(r"(?:^|(?<=[\s,]))'im\b", re.I), "him"),
    (re.compile(r"(?:^|(?<=[\s,]))'cause\b", re.I), "because"),
    (re.compile(r"(?:^|(?<=[\s,]))'til\b", re.I), "until"),
    (re.compile(r"\bgood ol'\b", re.I), "good old"),
    (re.compile(r"\blooky here\b", re.I), "look here"),
    (re.compile(r"\bya'?(?=\s|$|[.!?…,])", re.I), "you"),
    (re.compile(r"\b(I|you|we|they) been\b", re.I), r"\1 have been"),
    (re.compile(r"\bwhat (I|you|he|she|we|they) done\b", re.I), r"what \1 did"),
    (re.compile(r"\b(you|I|we|he|she|they)'d better\b", re.I), r"\1 had better"),
    (re.compile(r"\b(you|we|they|all of you) better (?!than\b)", re.I), r"\1 had better "),
    (re.compile(r"\b([A-Z][a-z]+)'d\s+(?=let|like|love|hate|rather|better|say|think|want|need|come|go|do|make|take|give|tell|kill|die|be\b)"), r"\1 would "),
    (re.compile(r"'s\s+(her|his|my|our|their|your)\s+name\b", re.I), r" is \1 name"),
    # Double negatives with ain't
    (re.compile(r"\bthere ain't no\b", re.I), "there is no"),
    (re.compile(r"\b(?:ain't|isn't|is not) (?:getting|taking) (us|you|me|them|him|her) (?:nowhere|anywhere)\b", re.I), r"is not leading \1 anywhere"),
    (re.compile(r"\bain't (getting|going|taking|leading) (\w+) nowhere\b", re.I), r"is not \1 \2 anywhere"),
    (re.compile(r"\bain't got no\b", re.I), "do not have any"),
    (re.compile(r"\bain't got nothing\b", re.I), "do not have anything"),
    # Vocative forms of address ("you old fox", "you crazy son of a gun", ", stranger")
    (re.compile(r"\byou old (fox|devil|dog|wolf|rascal|rogue|scoundrel|bastard|crook|bandit|thief|liar|snake|rat)\b", re.I), r"you cunning \1"),
    (re.compile(r"\byou (crazy|lucky|clever|sneaky|stubborn|greedy|filthy|dirty|miserable|poor|little) son of a (?:gun|bitch)\b", re.I), r"you \1 devil"),
    (re.compile(r"\bson of a gun\b", re.I), "bastard"),
    (re.compile(r",\s*(stranger|partner|cowboy|friend|kid|pal|buddy)(?=[.!?…]|$)", re.I), r", oh \1"),
    # Phrasal verbs ("be out", "go around", "make it", "get by", "turn in", "give out", "give away", "hold out", "lay low", "die down", "pull off")
    (re.compile(r"\b(the|this|that) (bridge|road|track|tracks|rail|rails|pass|trail|tunnel|path|way) (?:is|are|was|were) out\b", re.I), r"\1 \2 is blocked"),
    (re.compile(r"\b(the|this|that|our|my|your|his|her|their) (fire|light|lights|lantern|lamp|torch|candle|power) (?:is|are|was|were) out\b", re.I), r"\1 \2 has gone out"),
    (re.compile(r",\s*(we|I|you|they)(?:'ll| will| have to) (?:have to )?go around\b", re.I), r", so \1 must go around it"),
    (re.compile(r"\b(have to|need to|must|should|going to|will|'ll|let's|lets) go around(?=[.!?…,\s]|$)", re.I), r"\1 go around it"),
    (re.compile(r",\s*(I|we) (?:don't|do not) think (he|she|it|they|you|we)(?:'ll| will) (?:make it|survive)\b", re.I), r", and \1 do not think \2 will survive"),
    (re.compile(r"\b(going to|will|'ll|won't|wouldn't|didn't|can't|cannot|could) make it(?=[.!?…,\s]|$)", re.I), r"\1 survive"),
    (re.compile(r"\b(will|'ll|can|could|manage to|going to|try to) get by\b", re.I), r"\1 manage"),
    (re.compile(r",\s*(I'm|I am|we're|we are) (?:going to )?turn in(?:\s+for the night)?(?=[.!?…]|$)", re.I), r", so \1 going to go to sleep"),
    (re.compile(r"\b(going to|time to|let's|lets|will|'ll|should|need to|have to) turn in(?:\s+for the night)?(?=[.!?…]|$)", re.I), r"\1 go to sleep"),
    (re.compile(r"\b(horse|legs|heart|strength|breath|lungs|knees|back) gave out\b", re.I), r"\1 collapsed from exhaustion"),
    (re.compile(r"\b(rope|wagon|wheel|wheels|engine|bridge|roof|wall) gave out\b", re.I), r"\1 broke down"),
    (re.compile(r"\bgive (us|me|him|her|them|yourself|yourselves|our position|the plan) away\b", re.I), r"expose \1"),
    (re.compile(r"\bhold out (a little longer|until|till|much longer|for long|against)\b", re.I), r"endure \1"),
    (re.compile(r"\b(?:lay|lie|stay) low\b", re.I), "hide"),
    (re.compile(r"\buntil the heat dies down\b", re.I), "until things calm down"),
    (re.compile(r"\b(?:die|dies|died|dying) down\b", re.I), "calm down"),
    (re.compile(r"\bpulled it off\b", re.I), "did it"),  # «فعلتها»: "succeeded in doing it" read stiffly
    (re.compile(r"\bpull it off\b", re.I), "do it"),
    (re.compile(r",\s*make every (shot|bullet) count\b", re.I), r", so do not waste a single \1"),
    (re.compile(r"\bmake every (shot|bullet) count\b", re.I), r"do not waste a single \1"),
    (re.compile(r"\bbefore the law (?:shows up|arrives|gets here|comes)\b", re.I), "before the police arrive"),
    (re.compile(r"\b(I'm|I am|we're|we are|he's|he is|she's|she is|they're|they are) riding to (?=[A-Z])", re.I), r"\1 heading to "),
    # Idioms and conversational phrases
    (re.compile(r"\bget (that|this|those|these|the|your|his|her|my|our) (\w+) out of here\b", re.I), r"take \1 \2 out of here"),
    (re.compile(r"\b(?:watch|mind|hold|bite|guard) (your|his|her|their|my|our) (?:mouth|tongue)\b", re.I), r"hold \1 tongue"),
    (re.compile(r"\b(?:don't|do not) push your luck\b", re.I), "do not test my patience"),
    (re.compile(r"\bpush(?:ing)? (?:your|his|her|their) luck\b", re.I), "testing my patience"),
    (re.compile(r"\b(if you |don't |do not |never )cross (me|us)\b", re.I), r"\1betray \2"),
    (re.compile(r"\bdouble-?cross(ed|ing)?\b", re.I), r"betray\1"),
    (re.compile(r",\s*or else(?=[.!?…]|$)", re.I), ", or you will regret it"),
    (re.compile(r"\btake (one more|another) step\b", re.I), r"move \1 step"),
    (re.compile(r"\bput a bullet in (you|him|her|them)\b", re.I), r"shoot \1"),
    (re.compile(r"\bknew (?:it )?all along\b", re.I), "knew from the beginning"),
    (re.compile(r"\ball along(?=[.!?…]|$)", re.I), "from the beginning"),
    (re.compile(r"\b(?:haven't|have not|hasn't|has not) changed a bit\b", re.I), "have not changed at all"),
    (re.compile(r"\blook what the cat dragged in\b", re.I), "look who just showed up"),
    (re.compile(r"\b(?:save|(?:don't|do not) waste) your breath\b", re.I), "do not tire yourself talking"),
    (re.compile(r"\b(horse|mare|stallion|mule) (?:threw|lost|cast) a shoe\b", re.I), r"\1 lost its horseshoe"),
    (re.compile(r"\bsaddle the horses and pack the wagon\b", re.I), "prepare the horses and load the wagon"),
    (re.compile(r"\bsaddle the horses\b", re.I), "prepare the horses"),
    (re.compile(r"\btake the high ground\b", re.I), "move to the high ground"),
    (re.compile(r"\b(every|the|that|this|an old|a small|his|her|our|my) cabin\b", re.I), r"\1 hut"),
    (re.compile(r"\b(\d+%)\s+chance (?!that\b)", re.I), r"\1 probability that "),
    (re.compile(r"\bhold your horses\b", re.I), "wait a moment"),
    (re.compile(r"\ba piece of cake\b", re.I), "very easy"),
    (re.compile(r"\bpiece of cake\b", re.I), "very easy"),
    (re.compile(r"\bkeep an eye on\b", re.I), "watch"),
    (re.compile(r"\bgive (me|us|him|her|them) a hand\b", re.I), r"help \1"),
    (re.compile(r"\b(?:kidding|joking|fooling) around\b", re.I), "joking"),
    (re.compile(r"\bmessing around\b", re.I), "playing"),
    (re.compile(r"\b(sitting|waiting|standing|hanging|sticking|lying) around\b", re.I), r"\1"),
    (re.compile(r"\b(will|'ll|would) (?:see|have) (us|you|me|them|him|her) right\b", re.I), r"\1 take care of \2"),
    (re.compile(r"\btake it out on (me|you|him|her|us|them)\b", re.I), r"blame \1"),
    (re.compile(r"\bbored out of (?:my|your|his|her|our|their) mind\b", re.I), "extremely bored"),
    (re.compile(r"\bfor the most part\b", re.I), "mostly"),
    (re.compile(r"\bbehaving (?:himself|herself|yourself|themselves|myself)\b", re.I), "behaving well"),
    (re.compile(r"\bbehave (?:yourself|yourselves|himself|herself)\b", re.I), "behave politely"),
    (re.compile(r"\bhey now\b", re.I), "easy now"),
    (re.compile(r"\bis (this|that|it) meant to be\b", re.I), r"is \1 supposed to be"),
    (re.compile(r"\bkeep it up\b", re.I), "keep doing well"),
    (re.compile(r"\bsnap out of it\b", re.I), "wake up from this"),
    (re.compile(r"\bsad business\b", re.I), "sad matter"),
    (re.compile(r"\bwhen (\w+) passed(?=[.!?…,\s]|$)", re.I), r"when \1 died"),
    (re.compile(r"\bwhen anyone passes\b", re.I), "when anyone dies"),
    (re.compile(r"\bget back to it\b", re.I), "get back to work"),
    (re.compile(r"\breal (funny|nice|good|smart|clever|cute|sweet|kind|helpful|tough|brave)\b", re.I), r"very \1"),
    (re.compile(r"\b(I'm|I am|we're|we are) (?:just |only )?(?:playing|pulling your leg)(?: with you)?\b", re.I), r"\1 just joking with you"),
    (re.compile(r"\bhow (?:are )?you (?:boys |folks |fellas |all |guys )?keeping\b", re.I), "how are you doing"),
    (re.compile(r"\bhow (?:did )?you get on out there\b", re.I), "how did you do out there"),
    (re.compile(r"\byou(?:'re| are) slipping\b", re.I), "you are losing your touch"),
    (re.compile(r"\bnot (?:going to )?even dignify that with a response\b", re.I), "not even going to answer that"),
    (re.compile(r"\bif you can't take it, don't give it out\b", re.I), "if you cannot handle it, do not do it"),
    (re.compile(r"\b(?:I'm|I am) all ears\b", re.I), "I am listening carefully"),
    (re.compile(r"\bget a ?hold of yourself\b", re.I), "control yourself"),
    (re.compile(r"\b(?:have|having|take) a look around\b", re.I), "look around the place"),
    (re.compile(r"\bget the upper hand\b", re.I), "gain the advantage"),
    (re.compile(r"\bas far as I'm concerned\b", re.I), "in my opinion"),
    (re.compile(r"\blook a gift horse in the mouth\b", re.I), "reject a free gift"),
    (re.compile(r"\b(?:into|out of) thin air\b", re.I), "without a trace"),
    (re.compile(r"\bout of the blue\b", re.I), "suddenly"),
    (re.compile(r"\bonce in a blue moon\b", re.I), "very rarely"),
    (re.compile(r"\bunder the weather\b", re.I), "feeling sick"),
    (re.compile(r"\bspill the beans\b", re.I), "tell the secret"),
    (re.compile(r"\bbeat around the bush\b", re.I), "waste time talking"),
    (re.compile(r"\bbite the bullet\b", re.I), "face the situation"),
    (re.compile(r"\b(?:cut it out|knock it off)\b", re.I), "stop it"),
    (re.compile(r"\bgive it a rest\b", re.I), "stop talking about it"),
    (re.compile(r"\bcall it a day\b", re.I), "stop for today"),
    (re.compile(r"\bhit the road\b", re.I), "leave now"),
    (re.compile(r"\b(?:I'm|I am) done now\b", re.I), "I have stopped now"),
    (re.compile(r"\bsleep this off\b", re.I), "sleep until I recover"),
    # More everyday idioms the model took word for word (2026-11-18)
    (re.compile(r"\bspit it out already\b", re.I), "say it now"),
    (re.compile(r"\bspit it out\b", re.I), "say it"),
    (re.compile(r"\bdon't you dare\b", re.I), "never"),
    (re.compile(r"\b(he|she)(?:'s| has) got (?:some|a lot of) nerve\b", re.I), r"\1 is very shameless"),
    (re.compile(r"\b(they)(?:'ve| have) got (?:some|a lot of) nerve\b", re.I), r"\1 are very shameless"),
    (re.compile(r"\bput your back into it\b", re.I), "push harder"),
    (re.compile(r"^that was close(?:,\s*too close)?(?=[.!…]|$)", re.I), "we barely escaped"),
    (re.compile(r"\bI owe you one\b", re.I), "I owe you a favor"),
    (re.compile(r"\bowe (you|me|him|her|us|them) one\b", re.I), r"owe \1 a favor"),
    (re.compile(r"\b(?:you've|you have) got (?:some|a lot of) nerve\b", re.I), "you are very shameless"),
    (re.compile(r"\b(?:you've|you have) got nerve\b", re.I), "you are very shameless"),
    (re.compile(r"\btake it or leave it\b", re.I), "accept it or refuse it"),
    (re.compile(r"\bcall it a night\b", re.I), "stop for tonight"),
    (re.compile(r"\b(?:running|run|runs|ran) (his|her|your|their|my) mouth\b", re.I), "talking too much"),
    (re.compile(r",\s*not (?:one|a single) bit(?=[.!?…]|$)", re.I), ", not at all"),
    (re.compile(r"\bhit the (?:hay|sack)\b", re.I), "go to sleep"),
    (re.compile(r"\bwhat(?:'s| is) eating (you|him|her)\b", re.I), r"what is bothering \1"),
    (re.compile(r"\b(?:getting|got|get|have|having) cold feet\b", re.I), "hesitating"),
    (re.compile(r"\bkeep your chin up\b", re.I), "do not lose hope"),
    (re.compile(r"\bin (?:a|quite a|a real) (?:pickle|jam|bind)\b", re.I), "in trouble"),
    (re.compile(r"\bcost (you|me|us|him|her|them) an arm and a leg\b", re.I), r"cost \1 a fortune"),
    (re.compile(r"\b(you're|you are|he's|he is|she's|she is) (?:a real |quite a |a )piece of work\b", re.I),
     r"\1 an unbelievable person"),
    (re.compile(r"\bon thin ice\b", re.I), "in great danger"),
    (re.compile(r"\blet the cat out of the bag\b", re.I), "revealed the secret"),
    (re.compile(r"\bfair and square\b", re.I), "honestly"),
    (re.compile(r"\bturn(?:ed|ing)? a blind eye\b", re.I), "ignore it"),
    (re.compile(r"\bsleep on it\b", re.I), "think about it tonight"),
    (re.compile(r"\bmake a run for it\b", re.I), "escape quickly"),
    (re.compile(r"\bcut to the chase\b", re.I), "get to the point"),
    (re.compile(r"\bplay(?:ing)? dumb\b", re.I), "pretending not to know"),
    (re.compile(r"\byou(?:'re| are) dead meat\b", re.I), "you are finished"),
    (re.compile(r"\bkeep (?:that|it|this) in mind\b", re.I), "remember that"),
    (re.compile(r"\bget going(?=[.!?…,]|$)", re.I), "leave now"),
    (re.compile(r"\bheads? out at\b", re.I), "leave at"),
    (re.compile(r"\bbetter late than never\b", re.I), "arriving late is better than not arriving"),
    (re.compile(r"\b(?:I'm|I am) in no mood for\b", re.I), "I do not want"),
    (re.compile(r"\bgo get some (sleep|rest)\b", re.I), r"go and get some \1"),
)

_ALL_CAPS_WORD = re.compile(r"\b([A-Z]{2,})\b")
_ROMAN = {"II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "PS5", "PS4", "USB", "OK", "TV", "ID", "PC"}


def _fix_caps(text: str) -> str:
    """Truecased OPUS-MT shatters 3+ letter ALL-CAPS words into single-letter tokens."""
    def repl(m: re.Match) -> str:
        w = m.group(1)
        if w in _ROMAN:
            return w
        start = m.start()
        prefix = text[:start].rstrip()
        if not prefix or prefix[-1] in ".!?…:\"-–—([":
            return w.capitalize()
        return w.lower()

    return _ALL_CAPS_WORD.sub(repl, text)


def normalize_english(text: str) -> str:
    """Cache key form: one spacing, straight quotes; case is kept (it carries meaning)."""
    if "’" in text or "‘" in text or "“" in text or "”" in text:
        text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return _WS.sub(" ", text).strip()


_LOOSE_SAME = str.maketrans({"I": "l", "|": "l", "1": "l", "0": "o"})
_LOOSE_DROP = re.compile(r"[^a-z0-9]+")


def loose_key(text: str) -> str:
    """A line as the reader's slips leave it: no spacing or punctuation, small and capital
    letters alike, and the letters the reader mixes up counted as one (capital I, small l, 1
    and |; O and 0). A line read all in capitals is the one case it does not bring together.

    "l'm fine, lets go" and "I'm fine. Let's go!" have the same loose key, so a line the user
    rewrote is found again when the reader reads it a little differently. Small i and small l
    stay apart ("fail" is not "fall"), and no letter is ever added or dropped.
    """
    return _LOOSE_DROP.sub("", normalize_english(text).translate(_LOOSE_SAME).lower())


def _end_like(arabic: str, english: str) -> str:
    """A line found by its loose key may differ from the stored one in its last mark only
    ("You did it." and "You did it?"): the Arabic takes the question mark of the line on screen."""
    asks = english.rstrip("\"') ").endswith("?")
    body = arabic.rstrip()
    if asks and not body.endswith("؟"):
        return body.rstrip(".!،… ") + "؟"
    if not asks and body.endswith("؟"):
        return body[:-1].rstrip() + "."
    return arabic


_ANY_REWRITE_RX = re.compile("|".join(f"(?:{rx.pattern})" for rx, _ in _EN_REWRITES), flags=re.IGNORECASE)


def _apply_rewrites(text: str) -> str:
    if not _ANY_REWRITE_RX.search(text):
        return text
    for rx, repl in _EN_REWRITES:
        if rx.search(text):
            if repl.startswith("take "):
                text = rx.sub(repl, text)
            else:
                def _sub(m: re.Match, r: str = repl) -> str:
                    out = m.expand(r)
                    if m.group(0)[0].isupper() and out and out[0].islower():
                        return out[0].upper() + out[1:]
                    return out
                text = rx.sub(_sub, text)
    return text


def preprocess_english(text: str) -> str:
    """Turn contractions, dialect spellings, g-dropping and idioms into plain English before NMT."""
    text = normalize_english(text)
    if text.startswith(("(", "[")):
        stripped = _STAGE_DIR.sub("", text).strip()
        if stripped:
            text = stripped
    if text.startswith(("-", "–", "—")):
        text = _LEAD_DASH.sub("", text)
    if "-" in text:
        text = _STUTTER.sub(r"\1\2", text)
    text = _IM_IVE_RX.sub(r"I'\1", text)
    text = _WORD_REWRITE_RX.sub(_fix_word, text)
    text = _G_DROP_BARE.sub(_fix_g_drop, text)
    if "in'" in text:
        text = _G_DROP_APOS.sub(_fix_g_drop, text)
    text = _apply_rewrites(text)
    text = _fix_caps(text)
    return _WS.sub(" ", text).strip()


_PHRASE_SPLIT_RX = re.compile(r"^(.*?)([.!?…\"')\]\s]*)$")


def _phrase_lookup(sentence: str) -> str | None:
    """Exact phrase memory for short sentences and idioms, preserving end punctuation."""
    s = sentence.strip().lstrip("-–—").strip()
    m = _PHRASE_SPLIT_RX.match(s)
    if not m:
        return None
    core = m.group(1).strip().lower()
    tail = m.group(2).strip()
    ar = PHRASES.get(core)
    if ar is None:
        return None
    if "?" in tail:
        return f"{ar}؟"
    if "!" in tail:
        return f"{ar}!"
    if "..." in tail or ".." in tail or "…" in tail:
        return f"{ar}..."
    if "." in tail:
        return f"{ar}."
    return ar


# Systematic Arabic orthography and leftover-English fixes on NMT output.
_AR_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff\u2047\ufffd]")
_AR_SHORT_VOWELS = re.compile(r"[\u064e\u064f\u0650\u0652\u0670]")
_AR_PAREN_NAME = re.compile(r"\(([^()\s]+(?:\s[^()\s]+)?)\)")
_AR_COMMA = re.compile(r"(?<!\d)\s*,(?!\d)")
_AR_COMMA_AFTER = re.compile(r"(?<=[؀-ۿ])\s*,")
_AR_QMARK_AFTER = re.compile(r"(?<=[؀-ۿ])\s*\?")
_AR_SEMI_AFTER = re.compile(r"(?<=[؀-ۿ])\s*;")
_AR_MEME_TAIL = re.compile(r"\s*\b(?:LON\s+)?Meme\s+it\b\.?", flags=re.IGNORECASE)
_AR_EN_TAIL = re.compile(r"\s*باللغة الإنجليزية\.?$")
_AR_STRAY_LATIN_END = re.compile(r"(?:^|\s)[a-zA-Z]{1,2}(?=\s*$|[.!؟،])")
_AR_STRAY_LATIN_START = re.compile(r"^[a-zA-Z]{1,2}\s+(?=[؀-ۿ])")
_AR_ELLIPSIS_CHAR = re.compile(r"…+")
_AR_ELLIPSIS_DOTS = re.compile(r"\.{2,}")
_AR_DASH_PUNCT = re.compile(r"[-–—]+\s*([.!؟،])")
_AR_LEAD_DASH = re.compile(r"^[-–—]+\s*(?=[؀-ۿ])")
_AR_TIME_COLON = re.compile(r"(\d)\s*:\s*(\d)")
_AR_SPACE_BEFORE_PUNCT = re.compile(r"\s+([،؛؟.!:])")
_AR_DUP_PUNCT = re.compile(r"([،؛؟!])\1+")
_AR_PUNCT_NO_SPACE = re.compile(r"([،؛؟!])(?=[؀-ۿ])")
_AR_WAW_SPACE = re.compile(r"(?:^|(?<=[\s،؛؟.!]))و\s+(?=[؀-ۿ])")
_AR_YA_AL_TITLE = re.compile(r"\b(و?ف?يا) ال(عم|سيد|سيدة|آنسة)\b")
_AR_ALAL = re.compile(r"(?:^|(?<=[\s،؛؟.!]))الال(?=[؀-ۿ])")

_AR_MASDAR_WASL = re.compile(
    r"(?:^|(?<=[\s،؛؟.!«\"']))(و?ف?ب?ك?ل?)(?:(ال)إ(ست[؀-ۿ]{2,}|تّ?هام|هتمام|نتظار|حترام|عتذار|ختيار|ختبار|"
    r"عتماد|تّ?صال|تّ?فاق|نتباه|نتقام|نتهاء|قتراب|كتشاف|لتزام|متحان|بتسام|عتقاد|حتمال|نطلاق|نقسام|نقطاع|رتباك|شتباك)"
    r"|إ(ست[؀-ۿ]{3,}|تهام|هتمام|نتظار|حترام|عتذار|ختيار|ختبار|عتماد|تصال|تفاق|نتباه|نتقام|نتهاء|قتراب|كتشاف|لتزام|متحان|بتسام))\b"
)
_AR_IMP_WASL = re.compile(
    r"(?:^|(?<=[\s،؛؟.!«\"']))(و?ف?)إ("
    r"هد[أئء]|نهض|نتبه|نتظر|بتعد|ستمع|سمع|ذهب|حذر|حترس|تخذ|حسب|سأل|فعل|فتح|حصل|بق[ىيوا]?|"
    r"حرص|خرس|صمت|ركض|هرب|ترك|عتقد|قترب|كتشف|حتم|ستمر|ستعد|بحث|قفز|صعد|نزل|نبطح|جلس|حمل|رفع|خفض|نظر"
    r")([؀-ۿ]*)\b"
)
_AR_IMP_QAT = (
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)اعط(ني|يني|ه|ها|هم|نا|كم|ِ)?\b"), r"\1أعط\2"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)اخبر(ني|يني|ه|ها|هم|نا)\b"), r"\1أخبر\2"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)انقذ(ني|يني|ه|ها|هم|نا)\b"), r"\1أنقذ\2"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)اسرع(ي|وا|ن)?\b"), r"\1أسرع\2"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)اغلق(ي|وا|ه|ها)?\b"), r"\1أغلق\2"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)أنظر(وا|ي)?\b"), r"\1انظر\2"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))ان(ه|ها|هم|هن|ك|كم|نا|ني)\b"), r"إن\1"),
)
_AR_PHRASE_FIXES = (
    (re.compile(r"\bاهدأي\b"), "اهدئي"),
    (re.compile(r"\b(?:تحكم بنفسك|سيطر على نفسك)\b"), "تمالك نفسك"),
    (re.compile(r"\bلا تلومني\b"), "لا تلمني"),
    (re.compile(r"\bلم نموت\b"), "لم نمت"),
    (re.compile(r"\bنفذت(?=\s+(?:منا\s+)?(?:الذخيرة|ذخير|المؤن|الإمدادات|الطلقات))"), "نفدت"),
    (re.compile(r"\b(?:تراقب فمك|تمسك لسانك|تحرس لسانك)\b"), "تحفظ لسانك"),
    (re.compile(r"\b(?:راقب فمك|أمسك لسانك|امسك لسانك|احرس لسانك|انتبه إلى لسانك)\b"), "احفظ لسانك"),
    (re.compile(r"،\s*أنت\s+(ال[؀-ۿ]+(?:\s+ال[؀-ۿ]+)?)،"), r"، أيها \1،"),
    (re.compile(r"\b(الثعلب|الذئب|الكلب|البغل|الحمار|الشيطان|الوغد|الأحمق|الغبي|المجنون|اللص|المحتال|الكاذب|الجبان|الرجل) القديم\b"), r"\1 العجوز"),
    (re.compile(r"\bحصانك فقد حدوة حصانه\b"), "فقد حصانك حدوته"),
    (re.compile(r"\bفقد حصانك حدوة حصانه\b"), "فقد حصانك حدوته"),
    (re.compile(r"\bحدوة حصانه\b"), "حدوته"),
    (re.compile(r"\bتحمل لفترة أطول قليلا\s*،?\s*والمساعدة\b"), "اصمدوا قليلاً بعد، المساعدة"),
    (re.compile(r"\b(يستمع|يصغي|تستمع|تصغي) إلى السبب\b"), r"\1 إلى المنطق"),
    (re.compile(r"\bما يكفي من العطس\b"), "ما يكفي للعطس"),
    (re.compile(r"\bاطفئت\b"), "أطفئت"),
    (re.compile(
        r"(البينكرتون|الأودريسكول|الإخوة سكينر|آل بريثويت|آل غراي|آل مورفري|غزاة ليموين)\s+"
        r"(حاصر|أحرق|هاجم|قتل|سرق|نهب|دمّر|دمر|خطف|قطع|أخذ|ترك|هرب|دخل|خرج|وصل|عاد)\b"
    ), r"\1 \2وا"),
)
_LEFTOVER_WORDS = (
    (re.compile(r"\b(?:OK|Okay)\b", re.I), "حسناً"),
    (re.compile(r"\b(?:Yeah|Yep)\b", re.I), "نعم"),
    (re.compile(r"\b(?:Nope|Nah)\b", re.I), "لا"),
    (re.compile(r"\b(?:Whoa|Woah)\b", re.I), "تمهّل"),
    (re.compile(r"\bSheriff\b", re.I), "الشريف"),
    (re.compile(r"\bReverend\b", re.I), "القس"),
    (re.compile(r"\bCaptain\b", re.I), "الكابتن"),
    (re.compile(r"\bColonel\b", re.I), "الكولونيل"),
    (re.compile(r"\bSergeant\b", re.I), "الرقيب"),
    (re.compile(r"\bLieutenant\b", re.I), "الملازم"),
    (re.compile(r"\b(?:Ma'am|Madam)\b", re.I), "سيدتي"),
    (re.compile(r"\bSir\b", re.I), "سيدي"),
)
_HAS_LATIN = re.compile(r"[A-Za-z]")


def normalize_arabic(text: str) -> str:
    """Arabic punctuation, no stray Latin quotes, no doubled spaces or marks, clean orthography."""
    text = _AR_INVISIBLE.sub("", text)
    # Short vowels make subtitles slow to read; keep only tanween and shadda.
    text = _AR_SHORT_VOWELS.sub("", text)
    text = _WS.sub(" ", text).strip()
    text = text.strip("\"'«»“”‘’ ")
    # The models like to wrap names in brackets: "يا سيد (مورغان)" or leave unmatched "(أنت مصابة يا (فيكتوريا".
    if "(" in text:
        text = _AR_PAREN_NAME.sub(r"\1", text)
    if _HAS_AR.search(text):
        text = text.replace("(", "").replace(")", "").replace("[", "").replace("]", "")
        text = text.replace("?", "؟").replace(";", "؛")
        if "," in text:
            text = _AR_COMMA.sub("،", text)
        # Remove NMT hallucination tails and stray edge noise tokens when the sentence is Arabic.
        if _HAS_LATIN.search(text):
            text = _AR_MEME_TAIL.sub("", text)
            for rx, repl in _LEFTOVER_WORDS:
                text = rx.sub(repl, text)
            text = _AR_STRAY_LATIN_END.sub("", text)
            text = _AR_STRAY_LATIN_START.sub("", text)
        if "الإنجليزية" in text:
            text = _AR_EN_TAIL.sub("", text)
    else:
        text = _AR_COMMA_AFTER.sub("،", text)
        text = _AR_QMARK_AFTER.sub("؟", text)
        text = _AR_SEMI_AFTER.sub("؛", text)
    if "…" in text:
        text = _AR_ELLIPSIS_CHAR.sub("...", text)
    if ".." in text:
        text = _AR_ELLIPSIS_DOTS.sub("...", text)
    text = _AR_DASH_PUNCT.sub(r"\1", text)
    text = _AR_LEAD_DASH.sub("", text)
    if ":" in text:
        text = _AR_TIME_COLON.sub(r"\1:\2", text)
    text = _AR_SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _AR_DUP_PUNCT.sub(r"\1", text)
    text = _AR_PUNCT_NO_SPACE.sub(r"\1 ", text)
    # Attach standalone conjunction "و " to the following Arabic word.
    text = _AR_WAW_SPACE.sub("و", text)
    text = _AR_YA_AL_TITLE.sub(r"\1 \2", text)
    if "الال" in text:
        text = _AR_ALAL.sub("ال", text)
    # Fix hamzat wasl vs qat' on verbal nouns and imperatives.
    if "إ" in text:
        text = _AR_MASDAR_WASL.sub(lambda m: f"{m.group(1)}{m.group(2) or ''}ا{m.group(3) or m.group(4)}", text)
        text = _AR_IMP_WASL.sub(r"\1ا\2\3", text)
    for rx, repl in _AR_IMP_QAT:
        text = rx.sub(repl, text)
    for rx, repl in _AR_PHRASE_FIXES:
        text = rx.sub(repl, text)
    return _WS.sub(" ", text).strip()


# -- cache -------------------------------------------------------------------------


class Cache:
    """Keyed by profile, normalised English, engine and style. Thread-safe."""

    def __init__(self, path: Path | str | None = None) -> None:
        # Looked up now, not at import, so a test that moves the home folder moves the cache too.
        self.path = Path(path) if path else paths.HOME_DIR / CACHE_FILE.name
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS tr (profile TEXT, en TEXT, engine TEXT, style TEXT, ar TEXT, ts REAL,"
            " PRIMARY KEY (profile, en, engine, style))"
        )
        # A line the user corrected by hand always wins over the engine.
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS fix (profile TEXT, en TEXT, ar TEXT, ts REAL, sk TEXT,"
            " PRIMARY KEY (profile, en))"
        )
        # `sk` is the line's loose key (see loose_key). A cache file made before it existed gets
        # the column now, filled in for the lines already there.
        if "sk" not in {row[1] for row in self._db.execute("PRAGMA table_info(fix)")}:
            self._db.execute("ALTER TABLE fix ADD COLUMN sk TEXT")
        old = self._db.execute("SELECT profile, en FROM fix WHERE sk IS NULL").fetchall()
        if old:
            self._db.executemany("UPDATE fix SET sk=? WHERE profile=? AND en=?",
                                 [(loose_key(en), profile, en) for profile, en in old])
        self._db.execute("CREATE INDEX IF NOT EXISTS fix_sk ON fix (profile, sk)")
        # How the engine writes a glossary name on its own (see NameGuard).
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS names (engine TEXT, en TEXT, mode TEXT, variant TEXT,"
            " PRIMARY KEY (engine, en))"
        )
        self._db.commit()

    def get_fix(self, profile: str, en: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT ar FROM fix WHERE profile=? AND en=?", (profile, en)).fetchone()
        return row[0] if row else None

    def get_fix_loose(self, profile: str, en: str) -> str | None:
        """The user's Arabic for a line that differs from `en` only by the reader's slips.
        None when there is none, or when two such lines have different Arabic (no guessing)."""
        sk = loose_key(en)
        if not sk:
            return None
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT ar FROM fix WHERE profile=? AND sk=? LIMIT 2", (profile, sk)
            ).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def put_fix(self, profile: str, en: str, ar: str | None) -> None:
        """Store the user's own Arabic for a line; None or empty removes it."""
        with self._lock:
            if ar:
                self._db.execute("INSERT OR REPLACE INTO fix (profile, en, ar, ts, sk) VALUES (?,?,?,?,?)",
                                 (profile, en, ar, time.time(), loose_key(en)))
            else:
                self._db.execute("DELETE FROM fix WHERE profile=? AND en=?", (profile, en))
            self._db.commit()

    def add_fixes(self, profile: str, rows: list[tuple[str, str]]) -> int:
        """Many lines at once, in one write; a line that already has the user's wording keeps it.
        Returns how many were added."""
        with self._lock:
            before = self._db.total_changes
            self._db.executemany("INSERT OR IGNORE INTO fix (profile, en, ar, ts, sk) VALUES (?,?,?,?,?)",
                                 [(profile, en, ar, time.time(), loose_key(en)) for en, ar in rows if en and ar])
            self._db.commit()
            return self._db.total_changes - before

    def fixes(self, profile: str) -> list[tuple[str, str]]:
        """Every line the user rewrote for this game, oldest first."""
        with self._lock:
            rows = self._db.execute("SELECT en, ar FROM fix WHERE profile=? ORDER BY ts", (profile,)).fetchall()
        return [(en, ar) for en, ar in rows]

    def get_name(self, engine: str, en: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._db.execute("SELECT mode, variant FROM names WHERE engine=? AND en=?", (engine, en)).fetchone()
        return (row[0], row[1] or "") if row else None

    def put_name(self, engine: str, en: str, mode: str, variant: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO names VALUES (?,?,?,?)", (engine, en, mode, variant))
            self._db.commit()

    def get(self, profile: str, en: str, engine: str, style: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT ar FROM tr WHERE profile=? AND en=? AND engine=? AND style=?", (profile, en, engine, style)
            ).fetchone()
        return row[0] if row else None

    def put(self, profile: str, en: str, engine: str, style: str, ar: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO tr VALUES (?,?,?,?,?,?)", (profile, en, engine, style, ar, time.time())
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()


# -- sentences ---------------------------------------------------------------------

_SENT = re.compile(
    r"(?<=[.!?…])[\"')\]]*\s+(?=[-\u2013\u2014\"'(\[]*[A-Za-z0-9])"
    r"|\s+(?=[-\u2013\u2014]\s*[A-Z])"
    r"|;\s+(?=[A-Za-z\"'(])"
    r"|\s+(?:--|—)\s+(?=[A-Za-z\"'(])"
    r"|\s+(?=[A-Z][A-Za-z.'\-]{1,15}:\s+[A-Z])"
)
_ABBREV = re.compile(
    r"(?:^|\s)(?:Mr|Mrs|Ms|Dr|Prof|Rev|Fr|St|Jr|Sr|Mt|Lt|Sgt|Cpl|Pvt|Capt|Maj|Col|Gen|Cmdr|Adm|"
    r"Gov|Sen|Rep|Pres|No|vs|etc|Bros|Co|Inc|Ltd|(?:[A-Z]\.)+)\.?$",
    flags=re.IGNORECASE,
)
_INLINE_SPEAKER = re.compile(r"^([A-Z][A-Za-z.'\-]{1,15}):\s+(?=[A-Z\"'(])")
_END_PUNCT = {".": ".", "!": "!", "?": "؟", "…": "..."}


def split_sentences(text: str) -> list[str]:
    """NMT models tend to drop everything after the first sentence, so feed them one at a time."""
    parts: list[str] = []
    for piece in (p.strip() for p in _SENT.split(text)):
        if not piece:
            continue
        if parts and _ABBREV.search(parts[-1]):
            parts[-1] = f"{parts[-1]} {piece}"  # "Mr. Smith" or "J. Marston" is not two sentences
        else:
            parts.append(piece)
    return parts or [text]


def join_sentences(sources: list[str], translations: list[str]) -> str:
    """Join per-sentence translations, restoring end punctuation the model dropped or changed."""
    out = []
    for en, ar in zip(sources, translations):
        ar = ar.strip()
        if not ar:
            continue
        clean_en = en.rstrip("\"')] ")
        last = clean_en[-1:] if clean_en else ""
        is_ellipsis = clean_en.endswith(("...", "..", "…"))
        if is_ellipsis:
            ar = ar.rstrip(".!؟?…، ") + "..."
        elif last == "?":
            ar = ar.rstrip(".!؟?…، ") + "؟"
        elif last == "!":
            ar = ar.rstrip(".!؟?…، ") + "!"
        elif last in _END_PUNCT and ar[-1] not in ".!؟?…،":
            ar += _END_PUNCT[last]
        out.append(ar)
    return " ".join(out)


def _prepare_piece(piece: str) -> str:
    piece = _INLINE_SPEAKER.sub("", piece)
    return preprocess_english(piece)


# -- local engines -----------------------------------------------------------------


def _threads() -> int:
    import os

    # Up to 8 intra-op threads on modern multi-core CPUs: speeds up CTranslate2 without starving the frame loop.
    return max(2, min(8, (os.cpu_count() or 4) * 2 // 3))


class NllbTranslator:
    """facebook/nllb-200-distilled-600M converted to CTranslate2 int8 (eng_Latn to arb_Arab)."""

    name = "nllb-600m"

    def __init__(self, model_dir: Path | None = None) -> None:
        import ctranslate2
        import sentencepiece as spm

        d = model_dir or MODELS_DIR / "nllb-200-distilled-600M-ct2-int8"
        if not (d / "model.bin").exists():
            raise TranslateError(f"translation model missing: {d}")
        # From bytes, not by path: SentencePiece cannot open a path with Arabic letters on Windows.
        self._sp = spm.SentencePieceProcessor(model_proto=(d / "sentencepiece.bpe.model").read_bytes())
        self._tr = ctranslate2.Translator(str(d), device="cpu", compute_type="int8", intra_threads=_threads())

    def translate(self, text: str, context: list[tuple[str, str]] | None = None) -> str:
        return self.translate_many([text])[0]

    def translate_many(self, texts: list[str]) -> list[str]:
        """Several texts in one batch: much faster per text than one call each."""
        parts = [split_sentences(t) for t in texts]
        flat = [p for ps in parts for p in ps]
        flat_out: list[str] = [""] * len(flat)
        todo_idx: list[int] = []
        src: list[list[str]] = []
        for idx, p in enumerate(flat):
            hit = _phrase_lookup(p)
            if hit is not None:
                flat_out[idx] = hit
            else:
                prep = _prepare_piece(p)
                hit2 = _phrase_lookup(prep)
                if hit2 is not None:
                    flat_out[idx] = hit2
                else:
                    todo_idx.append(idx)
                    src.append(["eng_Latn"] + self._sp.encode(prep, out_type=str) + ["</s>"])
        if src:
            res = self._tr.translate_batch(
                src, target_prefix=[["arb_Arab"]] * len(src), beam_size=2, max_decoding_length=128,
                repetition_penalty=1.1, max_batch_size=16,
            )
            # hypotheses[0][0] is the language token
            for idx, r in zip(todo_idx, res):
                flat_out[idx] = self._sp.decode(r.hypotheses[0][1:])
        return _rejoin(parts, flat_out)


class OpusTranslator:
    """Helsinki-NLP/opus-mt-tc-big-en-ar converted to CTranslate2."""

    name = "opus-big"

    def __init__(self, model_dir: Path | None = None) -> None:
        import ctranslate2
        import sentencepiece as spm

        d = model_dir or MODELS_DIR / "opus-mt-tc-big-en-ar-ct2"
        if not (d / "model.bin").exists():
            raise TranslateError(f"translation model missing: {d}")
        # From bytes, not by path: SentencePiece cannot open a path with Arabic letters on Windows
        # (a Windows user named in Arabic installs the program under such a path).
        self._src = spm.SentencePieceProcessor(model_proto=(d / "source.spm").read_bytes())
        self._tgt = spm.SentencePieceProcessor(model_proto=(d / "target.spm").read_bytes())
        self._tr = ctranslate2.Translator(str(d), device="cpu", compute_type="int8", intra_threads=_threads())

    def translate(self, text: str, context: list[tuple[str, str]] | None = None) -> str:
        return self.translate_many([text])[0]

    def translate_many(self, texts: list[str]) -> list[str]:
        """Several texts in one batch: much faster per text than one call each."""
        parts = [split_sentences(t) for t in texts]
        flat = [p for ps in parts for p in ps]
        flat_out: list[str] = [""] * len(flat)
        todo_idx: list[int] = []
        src: list[list[str]] = []
        for idx, p in enumerate(flat):
            hit = _phrase_lookup(p)
            if hit is not None:
                flat_out[idx] = hit
            else:
                prep = _prepare_piece(p)
                hit2 = _phrase_lookup(prep)
                if hit2 is not None:
                    flat_out[idx] = hit2
                else:
                    todo_idx.append(idx)
                    src.append([">>ara<<"] + self._src.encode(prep, out_type=str) + ["</s>"])
        if src:
            res = self._tr.translate_batch(src, beam_size=2, max_decoding_length=64, repetition_penalty=1.1,
                                           return_scores=False, max_batch_size=16)
            for idx, r in zip(todo_idx, res):
                flat_out[idx] = self._tgt.decode(r.hypotheses[0])
        return _rejoin(parts, flat_out)


def _rejoin(parts: list[list[str]], flat_out: list[str]) -> list[str]:
    """Split a flat list of sentence translations back into one string per input text."""
    out, i = [], 0
    for ps in parts:
        out.append(join_sentences(ps, flat_out[i:i + len(ps)]))
        i += len(ps)
    return out


ENGINES = {"nllb-600m": NllbTranslator, "opus-big": OpusTranslator}
LOCAL_DEFAULT = "opus-big"


def make_engine(name: str) -> Translator:
    if name not in ENGINES:
        raise TranslateError(f"unknown engine {name!r}; choose from {', '.join(ENGINES)}")
    return ENGINES[name]()


def load_profile(name: str) -> dict:
    """profiles/<name>.yaml: glossary, style and detection settings for one game."""
    import yaml

    path = paths.REPO / "profiles" / f"{name}.yaml"
    if not path.exists():
        if name == "default":
            return {}
        raise TranslateError(f"no profile file: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TranslateError(f"profile {name} is not a mapping")
    return data


def default_engine_name() -> str:
    return paths.load_config().get("mt_engine", "opus-big")


# -- glossary ----------------------------------------------------------------------


def apply_glossary(arabic: str, english: str, glossary: dict[str, str]) -> str:
    """Make names consistent: if an English glossary term is in the source and the
    model left it in Latin letters, put the agreed Arabic form in its place."""
    for en, ar in glossary.items():
        if re.search(rf"\b{re.escape(en)}\b", english, flags=re.IGNORECASE):
            arabic = re.sub(rf"\b{re.escape(en)}\b", ar, arabic, flags=re.IGNORECASE)
    return arabic


# -- names ---------------------------------------------------------------------------

# Stand-in names the engine was measured to write the same way in every sentence.
PROXIES = (("Edward", "إدوارد"), ("Harold", "هارولد"), ("Leonard", "ليونارد"), ("Jonathan", "جوناثان"))
# Women's stand-ins, so verbs agree with a woman's name. Catherine first: with "Victoria" the
# model broke 4 of 8 longer lines ("..., go get some sleep" became «الذهاب الحصول على...»),
# with Catherine none (2026-11-18).
PROXIES_FEMALE = (
    ("Catherine", "كاثرين"), ("Margaret", "مارغريت"), ("Elizabeth", "إليزابيث"), ("Victoria", "فيكتوريا"),
)
# A group ("the Pinkertons", "the O'Driscolls") stood in for by one man made its verbs singular:
# «البينكرتون سيلحق بنا». A plural stand-in keeps them plural (2026-11-18).
PROXIES_PLURAL = (("soldiers", "الجنود"), ("guards", "الحراس"))
_GROUP_AR = re.compile(r"^(?:ال|آل |عصابة |غزاة |الإخوة )")


def _is_group(en: str, ar: str) -> bool:
    """A name for a group of people: plural in English, a family or band in Arabic."""
    return en.endswith("s") and not en.endswith("ss") and bool(_GROUP_AR.match(ar))


def _reads_female(en: str, ar: str) -> bool:
    """A one-word name not marked as a woman's that ends in "a" ("Majima", "Kasuga", "Luca"):
    alone, the engine takes it for a woman's and writes her verbs («ماجيما تريد»)."""
    return " " not in en.strip() and en[-1:].lower() == "a" and not _is_group(en, ar)


def _vocative_title(arabic: str, english: str, matched: list[tuple[str, str]]) -> str:
    """"Uncle, wake up" → «يا عم، استيقظ», not «العم، استيقظ»: a name or title written with
    «ال», spoken to at the start of the line."""
    for en, ar in matched:
        if not ar.startswith("ال") or " " in ar or _is_group(en, ar):
            continue
        if not re.match(rf"[\s\"'-]*{re.escape(en)}\s*,", english, flags=re.IGNORECASE):
            continue
        # the engine sometimes brackets the name: «(العم)، انهض»
        m = re.match(rf"\s*[(«\"]?{re.escape(ar)}[)»\"]?(\s*[،,])", arabic)
        if m:
            return "يا " + ar[2:] + m.group(1) + arabic[m.end():]
    return arabic


_PROBES = ("Hello, {}.", "{} is thinking about it.", "I told {} to wait.", "We got {}.")
_AR_EDGE = "()«»\"'.,،؛؟!:… "
_FEMALE_TITLE = re.compile(r"\b(?:Mrs|Miss|Ms|Lady|Madam|Sister|Aunt|Queen|Princess)\.?\s+$", flags=re.IGNORECASE)
_FEMALE_PREFIX = re.compile(r"^(?:Mrs|Miss|Ms|Lady|Madam|Sister|Aunt|Queen|Princess)\b", flags=re.IGNORECASE)
_FEMALE_EN_WORD = re.compile(
    r"\b(?:Mrs|Miss|Ms|Lady|Madam|Ma'am|Sister|Aunt|Queen|Princess|girl|woman|mother|mom|mama|"
    r"daughter|wife|bride|niece|grandma|grandmother|she|her|herself|lass|gal|"
    r"Victoria|Catherine|Elizabeth|Margaret)\b",
    flags=re.IGNORECASE,
)

_MASC_DEFAULT_FIXES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))[اأإ]بقي\s+(?=رأسك|يديك|عينيك|صوتك|سلاحك|فمك)"), "أبقِ "),
    (re.compile(r"\b[اأإ]بقي قريبة\b"), "ابقَ قريباً"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)راقبي\b"), r"\1راقب"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]تبعيني(?=[\s،؛؟.!]|$)"), r"\1اتبعني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]هد[أئ]ي(?=[\s،؛؟.!]|$)"), r"\1اهدأ"),
    (re.compile(r"\bأن تقولي\b"), "أن تقول"),
    (re.compile(r"\b(س?تندم)ين\b"), r"\1"),
    (re.compile(r"\b(تتحدث|تتكلم)ين\b"), r"\1"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)ساعديني(?=[\s،؛؟.!]|$)"), r"\1ساعدني"),
)


def _masculinize_default(arabic: str) -> str:
    for rx, repl in _MASC_DEFAULT_FIXES:
        arabic = rx.sub(repl, arabic)
    return gender.masculinize(arabic)  # every common command, not only the ones above


_FEM_VOCATIVE_FIXES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"،\s*والبقاء هنا"), "، ابقي هنا"),
    (re.compile(r"\bومشاهدة الخيول\b"), "وراقبي الخيول"),
    (re.compile(r"\bيجب أن تبقى\b"), "يجب أن تبقي"),
    (re.compile(r"\bتحتاج إلى\b"), "تحتاجين إلى"),
    (re.compile(r"\bتحتاج أن\b"), "تحتاجين أن"),
    (re.compile(r"\bتستطيع أن\b"), "تستطيعين أن"),
    (re.compile(r"\bيمكنك أن تفعل\b"), "يمكنك أن تفعلي"),
    (re.compile(r"\bماذا تفعل\b"), "ماذا تفعلين"),
    (re.compile(r"\bأين تذهب\b"), "أين تذهبين"),
    (re.compile(r"\bأين كنت\b(?=[\s؟.!،]|$)"), "أين كنتِ"),
    (re.compile(r"\bهل أنت متأكد\b"), "هل أنتِ متأكدة"),
    (re.compile(r"\bهل أنت مستعد\b"), "هل أنتِ مستعدة"),
    (re.compile(r"\bهل أنت جاهز\b"), "هل أنتِ جاهزة"),
    (re.compile(r"\bأنت مصاب\b"), "أنتِ مصابة"),
    (re.compile(r"\bأنت متعب\b"), "أنتِ متعبة"),
    (re.compile(r"\bكن حذراً\b"), "كوني حذرة"),
    (re.compile(r"\bابقَ? قريباً\b"), "ابقي قريبة"),
    (re.compile(r"\bابقَ? هنا\b"), "ابقي هنا"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]نبطح(?=[\s،؛؟.!]|$)"), r"\1انبطحي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]نتبه(?=[\s،؛؟.!]|$)"), r"\1انتبهي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]حترس(?=[\s،؛؟.!]|$)"), r"\1احترسي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]حذر(?=[\s،؛؟.!]|$)"), r"\1احذري"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)راقب(?=[\s،؛؟.!]|$)"), r"\1راقبي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]حرس(?=[\s،؛؟.!]|$)"), r"\1احرسي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اأإ]غلق(?=[\s،؛؟.!]|$)"), r"\1أغلقي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]فتح(?=[\s،؛؟.!]|$)"), r"\1افتحي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]نتظر(?=[\s،؛؟.!]|$)"), r"\1انتظري"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]نتظرني(?=[\s،؛؟.!]|$)"), r"\1انتظريني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]سمع(?=[\s،؛؟.!]|$)"), r"\1اسمعي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]سمعني(?=[\s،؛؟.!]|$)"), r"\1اسمعيني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]ستمع(?=[\s،؛؟.!]|$)"), r"\1استمعي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)تعال(?=[\s،؛؟.!]|$)"), r"\1تعالي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]ذهب(?=[\s،؛؟.!]|$)"), r"\1اذهبي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]ركض(?=[\s،؛؟.!]|$)"), r"\1اركضي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]هرب(?=[\s،؛؟.!]|$)"), r"\1اهربي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اأإ]سرع(?=[\s،؛؟.!]|$)"), r"\1أسرعي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)توقف(?=[\s،؛؟.!]|$)"), r"\1توقفي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)تحرك(?=[\s،؛؟.!]|$)"), r"\1تحركي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]هدأ(?=[\s،؛؟.!]|$)"), r"\1اهدئي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]نهض(?=[\s،؛؟.!]|$)"), r"\1انهضي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]جلس(?=[\s،؛؟.!]|$)"), r"\1اجلسي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]دخل(?=[\s،؛؟.!]|$)"), r"\1ادخلي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]خرج(?=[\s،؛؟.!]|$)"), r"\1اخرجي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]صعد(?=[\s،؛؟.!]|$)"), r"\1اصعدي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]نزل(?=[\s،؛؟.!]|$)"), r"\1انزلي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]بتعد(?=[\s،؛؟.!]|$)"), r"\1ابتعدي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]قترب(?=[\s،؛؟.!]|$)"), r"\1اقتربي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)تمسك(?=[\s،؛؟.!]|$)"), r"\1تمسكي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)تماسك(?=[\s،؛؟.!]|$)"), r"\1تماسكي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]حتم(?=[\s،؛؟.!]|$)"), r"\1احتمي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]ختبئ(?=[\s،؛؟.!]|$)"), r"\1اختبئي"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اأإ]جبني(?=[\s،؛؟.!]|$)"), r"\1أجيبيني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اأإ]خبرني(?=[\s،؛؟.!]|$)"), r"\1أخبريني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اأإ]عطني(?=[\s،؛؟.!]|$)"), r"\1أعطيني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)ساعدني(?=[\s،؛؟.!]|$)"), r"\1ساعديني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)سامحني(?=[\s،؛؟.!]|$)"), r"\1سامحيني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)صدقني(?=[\s،؛؟.!]|$)"), r"\1صدقيني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]تركني(?=[\s،؛؟.!]|$)"), r"\1اتركيني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]تبعني(?=[\s،؛؟.!]|$)"), r"\1اتبعيني"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)[اإ]نظر(?=[\s،؛؟.!]|$)"), r"\1انظري"),
    (re.compile(r"\bألقِ? نظرة\b"), "ألقي نظرة"),
    (re.compile(r"(?:^|(?<=[\s،؛؟.!]))(و?ف?)خذ(?=[\s،؛؟.!]|$)"), r"\1خذي"),
    (re.compile(r"\bلا تقلق(?=[\s،؛؟.!]|$)"), "لا تقلقي"),
    (re.compile(r"\bلا تخف(?=[\s،؛؟.!]|$)"), "لا تخافي"),
    (re.compile(r"\bلا تتحرك(?=[\s،؛؟.!]|$)"), "لا تتحركي"),
    (re.compile(r"\bلا تتحرّك(?=[\s،؛؟.!]|$)"), "لا تتحرّكي"),
    (re.compile(r"\bلا تذهب(?=[\s،؛؟.!]|$)"), "لا تذهبي"),
    (re.compile(r"\bلا تفعل(?=[\s،؛؟.!]|$)"), "لا تفعلي"),
    (re.compile(r"\bلا تنسَ?(?=[\s،؛؟.!]|$)"), "لا تنسي"),
)


@lru_cache(maxsize=512)
def _vocative_rxs(en: str) -> tuple[re.Pattern, re.Pattern, re.Pattern]:
    esc = _literal(en)
    return (
        re.compile(rf"(?:^|[.!?…]\s+|,\s*){esc}\s*[.!?…]+", flags=re.IGNORECASE),
        re.compile(rf",\s*{esc}\s*(?:[.!?…\"')\]]*$)", flags=re.IGNORECASE),
        re.compile(rf"(?:^|[.!?…]\s+){esc}\s*,\s*(?!and\b|[A-Z][a-z]+\s+and\b)", flags=re.IGNORECASE),
    )


def _is_vocative(text: str, en: str) -> bool:
    """True when `en` is addressed directly in `text` ('Sadie, ...', '..., Sadie.', 'Sadie! ...')."""
    r1, r2, r3 = _vocative_rxs(en)
    return bool(r1.search(text) or r2.search(text) or r3.search(text))


def _feminize_vocative(arabic: str) -> str:
    arabic = gender.feminize(arabic)  # every common command said to her
    for rx, repl in _FEM_VOCATIVE_FIXES:
        arabic = rx.sub(repl, arabic)
    return arabic


_FEM_PLURAL_FIXES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bغادر(?:وا|ت)?(?=\s+المخيم|\s+المعسكر|\s+البلدة|\s+المدينة|\s+المنزل|\s+المكان|\b)"), "غادرن"),
    (re.compile(r"\bذهبوا\b"), "ذهبن"),
    (re.compile(r"\bوصلوا\b"), "وصلن"),
    (re.compile(r"\bعادوا\b"), "عدن"),
    (re.compile(r"\bكانوا\b"), "كنّ"),
    (re.compile(r"\bهربوا\b"), "هربن"),
    (re.compile(r"\bخرجوا\b"), "خرجن"),
    (re.compile(r"\bدخلوا\b"), "دخلن"),
    (re.compile(r"\bبقوا\b"), "بقين"),
    (re.compile(r"\bماتوا\b"), "متن"),
)
_PLURAL_GROUP_VERB = re.compile(
    r"(البينكرتون|الأودريسكول|الإخوة سكينر|آل بريثويت|آل غراي|آل مورفري|غزاة ليموين)\)?\s+"
    r"(حاصر|أحرق|هاجم|قتل|سرق|نهب|دمّر|دمر|خطف|قطع|أخذ|ترك|هرب|دخل|خرج|وصل|عاد)\b"
)


_PLURAL_GROUP_PRESENT = re.compile(
    r"(البينكرتون|الأودريسكول|الإخوة سكينر|آل بريثويت|آل غراي|آل مورفري|غزاة ليموين)\)?\s+"
    r"(س?)[يت]([؀-ۿ]{2,}?)(?<!ون)(?![؀-ۿ])"
)


def _feminize_plural(arabic: str) -> str:
    for rx, repl in _FEM_PLURAL_FIXES:
        arabic = rx.sub(repl, arabic)
    return arabic


def _words(arabic: str) -> set[str]:
    return {w.strip(_AR_EDGE) for w in normalize_arabic(arabic).split()} - {""}


class NameGuard:
    """Keeps glossary names right.

    A small translation model treats some names as words ("Dutch" came out as
    "the Dutch woman" in every measured line) and spells others its own way.
    Each name is tried once in a few plain sentences and filed as:

    - "ok":      the engine already writes the agreed Arabic form;
    - "variant": it always writes one other spelling, which is swapped afterwards;
    - "proxy":   it is unreliable, so the name is replaced in the English by a
                 stand-in name before translating, and swapped back in the Arabic.
    """

    def __init__(self, engine: Translator, cache: Cache) -> None:
        self.engine = engine
        self.cache = cache
        self._proxy_out: dict[str, str] = {}
        self._memo: dict[tuple[str, str], tuple[str, str]] = {}

    def mode(self, en: str, ar: str) -> tuple[str, str]:
        if (en, ar) in self._memo:
            return self._memo[(en, ar)]
        # Multi-word names ("Hosea Matthews", "Braithwaite Manor") and names with punctuation
        # cannot be tracked as a single Arabic word, so they always use a stand-in.
        if " " in en.strip() or " " in ar.strip() or "'" in en or "’" in en or "-" in en:
            self._memo[(en, ar)] = ("proxy", "")
            return "proxy", ""
        stored = self.cache.get_name(self.engine.name, en)
        if stored is None:
            stored = self._probe(en)
            self.cache.put_name(self.engine.name, en, *stored)
        mode, variant = stored
        if mode == "variant" and variant == ar:
            mode = "ok"
        self._memo[(en, ar)] = (mode, variant)
        return mode, variant

    def _probe(self, en: str) -> tuple[str, str]:
        """The one word the engine writes for this name in every trial sentence, if there is one:
        the word that is in each translation with the name and in none with the stand-in."""
        proxy_en = PROXIES[0][0]
        common: set[str] | None = None
        for probe in _PROBES:
            if probe not in self._proxy_out:
                self._proxy_out[probe] = self.engine.translate(probe.format(proxy_en))
            own = _words(self.engine.translate(probe.format(en))) - _words(self._proxy_out[probe])
            if len(own) != 1:
                return "proxy", ""
            common = own if common is None else common & own
            if not common:
                return "proxy", ""
        return ("variant", common.pop()) if common and len(common) == 1 else ("proxy", "")

    def _decide(self, outs: list[str]) -> tuple[str, str]:
        """The same verdict as `_probe`, from translations made in a batch."""
        common: set[str] | None = None
        for probe, out in zip(_PROBES, outs):
            own = _words(out) - _words(self._proxy_out[probe])
            if len(own) != 1:
                return "proxy", ""
            common = own if common is None else common & own
            if not common:
                return "proxy", ""
        return ("variant", common.pop()) if common and len(common) == 1 else ("proxy", "")

    def prepare(self, names: list[tuple[str, str]], per_batch: int = 4, stop=None) -> None:
        """Try every name not tried before, a few names per batch.

        One batch is several times faster than the same sentences one by one, and a small
        batch keeps a live line that arrives meanwhile from waiting long behind it.
        `stop` (optional) is asked between batches whether to give up.
        """
        if stop is not None and stop():
            return
        many = getattr(self.engine, "translate_many", None)
        todo = [en for en, ar in names if en and " " not in en.strip() and " " not in ar.strip()
                and "'" not in en and "’" not in en and "-" not in en
                and (en, ar) not in self._memo and self.cache.get_name(self.engine.name, en) is None]
        if many is not None and todo:
            proxy_en = PROXIES[0][0]
            missing = [p for p in _PROBES if p not in self._proxy_out]
            if missing:
                for probe, out in zip(missing, many([p.format(proxy_en) for p in missing])):
                    self._proxy_out[probe] = out
            for i in range(0, len(todo), per_batch):
                if stop is not None and stop():
                    return
                chunk = todo[i:i + per_batch]
                outs = many([p.format(en) for en in chunk for p in _PROBES])
                for j, en in enumerate(chunk):
                    if self.cache.get_name(self.engine.name, en) is None:  # a live line may have tried it meanwhile
                        got = outs[j * len(_PROBES):(j + 1) * len(_PROBES)]
                        self.cache.put_name(self.engine.name, en, *self._decide(got))
        for en, ar in names:
            if stop is not None and stop():
                return
            if en and ar:
                self.mode(en, ar)  # from the cache now; engines without batches try one by one here


@lru_cache(maxsize=256)
def _name_rx(en: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z]){re.escape(en)}(?![A-Za-z])", flags=re.IGNORECASE)


def _literal(text: str) -> str:
    """A regex for this text where straight and curly apostrophes count as the same."""
    return "".join("['’]" if ch in "'’" else re.escape(ch) for ch in text)


@lru_cache(maxsize=2048)
def name_pattern(en: str) -> re.Pattern:
    """How a glossary name is found in a line.

    A name that starts with a capital ("Chop", "Families") must appear with that
    capital, or all in capitals ("CHOP"), so the same word in small letters ("chop the
    wood", "our families") is left to the translator. A name written in small letters
    ("van der Linde", "clicker") is found in any case.
    """
    first = next((c for c in en if c.isalpha()), "")
    if first.isupper():
        exact, upper = _literal(en), _literal(en.upper())
        body = exact if exact == upper else f"(?:{exact}|{upper})"
        return re.compile(rf"(?<![A-Za-z]){body}(?![A-Za-z])")
    return re.compile(rf"(?<![A-Za-z]){_literal(en)}(?![A-Za-z])", flags=re.IGNORECASE)


def _swap_proxy(raw: str, swaps: list[tuple[str, str]]) -> tuple[str, bool]:
    """Replace stand-in Arabic names with the agreed spellings, tolerating bare alef."""
    all_found = True
    for proxy_ar, ar in swaps:
        alt = proxy_ar.replace("إ", "ا").replace("أ", "ا")
        if proxy_ar in raw:
            raw = raw.replace(proxy_ar, ar)
        elif alt in raw:
            raw = raw.replace(alt, ar)
        elif proxy_ar.startswith("ال") and ar.startswith("ال") and re.search(
                rf"(?<![ء-ي]){re.escape(proxy_ar[2:])}(?![ء-ي])", raw):
            # a group stand-in written without «ال» ("there are soldiers" → «هناك جنود»): the
            # group's name without it too («هناك رانرز»)
            raw = re.sub(rf"(?<![ء-ي]){re.escape(proxy_ar[2:])}(?![ء-ي])", lambda _m: ar[2:], raw)
        else:
            all_found = False
    return raw, all_found


RULES = "r7"  # raise when the rules around the engine change, so the cache does not serve old output


class CachedTranslator:
    """The user's own fix first, then the cache, then the engine; always returns cleaned Arabic."""

    def __init__(self, engine: Translator, cache: Cache, profile: str = "default", style: str = "msa",
                 glossary: dict[str, str] | None = None, female: set[str] | None = None,
                 series: str = "") -> None:
        self.engine = engine
        self.series = series  # the game's series, for its own words (terms.py)
        self.cache = cache
        self.profile = profile
        self.style = style
        self._patterns: list[tuple[str, str, re.Pattern]] = []
        self._any_name_rx: re.Pattern | None = None
        self.glossary = glossary or {}
        self.female = set(female or ())  # glossary names that belong to women
        self.names = NameGuard(engine, cache)
        self.history: list[tuple[str, str]] = []

    @property
    def glossary(self) -> dict[str, str]:
        return self._glossary

    @glossary.setter
    def glossary(self, value: dict[str, str]) -> None:
        # Compiled once per list, longest first ("Mary Beth" before "Mary"): a game list
        # can have hundreds of names, and every line is checked against all of them.
        g = {str(k): str(v) for k, v in (value or {}).items()}
        self._glossary = g
        self._patterns = sorted(((en, ar, name_pattern(en)) for en, ar in g.items() if en and ar),
                                key=lambda t: -len(t[0]))
        if self._patterns:
            body = "|".join(_literal(en) for en, _ar, _p in self._patterns)
            self._any_name_rx = re.compile(rf"(?<![A-Za-z])(?:{body})(?![A-Za-z])", flags=re.IGNORECASE)
        else:
            self._any_name_rx = None

    def prepare_names(self, stop=None) -> None:
        """Try every glossary name once now, so the first line with a name is not slowed down."""
        self.names.prepare(list(self.glossary.items()), stop=stop)

    def _matched(self, key: str) -> list[tuple[str, str]]:
        """The glossary names in this line, longest first. A name found only inside a longer
        one ("Arthur" inside "Arthur Morgan") is part of that one and does not count."""
        if self._any_name_rx is None or not self._any_name_rx.search(key):
            return []
        found, masked = [], key
        key_low = key.lower()
        for en, ar, pattern in self._patterns:
            if "'" not in en and "’" not in en and en.lower() not in key_low:
                continue
            spans = [m.span() for m in pattern.finditer(masked)]
            if not spans:
                continue
            found.append((en, ar))
            for a, b in spans:
                masked = masked[:a] + "\0" * (b - a) + masked[b:]
        return found

    def _is_female(self, en: str, key: str, pattern: re.Pattern) -> bool:
        if en in self.female or _FEMALE_PREFIX.search(en):
            return True
        m = pattern.search(key)
        return bool(m and _FEMALE_TITLE.search(key[:m.start()]))

    def _engine_key(self, matched: list[tuple[str, str]]) -> str:
        """Lines with glossary names are cached per spelling, so editing a name takes effect at once.
        RULES is part of the key: lines translated by older rules are translated again."""
        if not matched:
            return f"{self.engine.name}+{RULES}"
        import hashlib

        marked = sorted((en, ar, en in self.female) for en, ar in matched)
        sig = hashlib.sha1(repr(marked).encode("utf-8")).hexdigest()[:10]
        return f"{self.engine.name}+{RULES}#n2-{sig}"

    def _translate_with_names(self, key: str, matched: list[tuple[str, str]]) -> str:
        modes = {en: self.names.mode(en, ar) for en, ar in matched}
        patterns = {en: pattern for en, _ar, pattern in self._patterns}
        free = [p for p in PROXIES if not _name_rx(p[0]).search(key)]
        free_f = [p for p in PROXIES_FEMALE if not _name_rx(p[0]).search(key)]
        free_g = [p for p in PROXIES_PLURAL if not _name_rx(p[0]).search(key)]
        swaps: list[tuple[str, str]] = []  # (stand-in Arabic, agreed Arabic)
        source = key
        fem_flags = {}
        for en, ar in matched:
            pattern = patterns.get(en) or name_pattern(en)
            is_fem = self._is_female(en, key, pattern)
            fem_flags[en] = is_fem
            pool = free_f if is_fem else (free_g if _is_group(en, ar) and free_g else free)
            # A woman's name always goes through a stand-in: the engine then gets her verbs right.
            # So does a man's name the engine would take for a woman's ("Majima" → «ماجيما تريد»).
            if (modes[en][0] == "proxy" or is_fem or _reads_female(en, ar)) and pool:
                if not pattern.search(source):
                    continue  # it sat inside a longer name that already has its stand-in
                proxy_en, proxy_ar = pool.pop(0)
                source = pattern.sub(proxy_en, source)
                swaps.append((proxy_ar, ar))
        raw = self.engine.translate(source, self.history[-4:])
        if swaps:
            swapped, all_found = _swap_proxy(raw, swaps)
            if all_found:
                raw = swapped
            else:
                raw = self.engine.translate(key, self.history[-4:])  # a stand-in got lost: plain translation
        for en, ar in matched:
            mode, variant = modes[en]
            if mode == "variant" and variant and ar not in raw:
                raw = raw.replace(variant, ar)
            if ar not in raw:
                for alt_ar in {ar.replace("آ", "أ"), ar.replace("آ", "ا"), ar.replace("أ", "ا"), ar.replace("إ", "ا")} - {ar}:
                    if alt_ar in raw:
                        raw = raw.replace(alt_ar, ar)
                        break
        raw = apply_glossary(raw, key, dict(matched))
        # Self-healing fallback: if a name filed as "ok" or "variant" was translated as a common
        # word in this sentence ("Follow Ash into the cave" -> "الرماد"), retry with a stand-in.
        missing = [(en, ar) for en, ar in matched if modes[en][0] != "proxy" and not fem_flags[en] and ar not in raw]
        if missing and free:
            retry_src = source
            retry_swaps = list(swaps)
            for en, ar in missing:
                if not free:
                    break
                pattern = patterns.get(en) or name_pattern(en)
                if pattern.search(retry_src):
                    proxy_en, proxy_ar = free.pop(0)
                    retry_src = pattern.sub(proxy_en, retry_src)
                    retry_swaps.append((proxy_ar, ar))
                    self.names._memo[(en, ar)] = ("proxy", "")
            if retry_src != source:
                raw2 = self.engine.translate(retry_src, self.history[-4:])
                swapped2, ok2 = _swap_proxy(raw2, retry_swaps)
                if ok2:
                    raw = apply_glossary(swapped2, key, dict(matched))
        raw = terms.commands_not_nouns(raw, key)
        raw = _vocative_title(raw, key, matched)
        raw = _PLURAL_GROUP_VERB.sub(r"\1 \2وا", raw)
        raw = _PLURAL_GROUP_PRESENT.sub(lambda m: f"{m.group(1)} {m.group(2)}ي{m.group(3)}ون", raw)
        if "mill" in key.lower() and "المصنع" in raw:
            raw = raw.replace("المصنع القديم", "الطاحونة القديمة").replace("المصنع", "الطاحونة")
        if all(fem_flags.values()):
            if any(_is_vocative(key, en) for en, _ in matched):
                raw = _feminize_vocative(raw)
            else:
                # "Buy Maria a ring", "Don't you like Maria?": she is named, not spoken to, so the
                # one spoken to is a man by default, as everywhere («اطلبي» → «اطلب», «تحبين» → «تحب»)
                raw = _masculinize_default(raw)
            if len(matched) >= 2:
                raw = _feminize_plural(raw)
        elif not any(fem_flags.values()) and not _FEMALE_EN_WORD.search(key):
            raw = _masculinize_default(raw)
        return raw

    def translate(self, text: str) -> tuple[str, bool, float]:
        """Returns (arabic, cache_hit, milliseconds)."""
        t0 = time.perf_counter()
        key = normalize_english(text)
        fixed = self.cache.get_fix(self.profile, key)
        if fixed is None:
            # the same line read a little differently (a capital I for a small l, a lost comma)
            fixed = self.cache.get_fix_loose(self.profile, key)
            if fixed is not None:
                fixed = _end_like(fixed, key)
        if fixed is not None:
            return fixed, True, (time.perf_counter() - t0) * 1000.0
        matched = self._matched(key)
        engine_key = self._engine_key(matched)
        hit = self.cache.get(self.profile, key, engine_key, self.style)
        if hit is not None:
            return hit, True, (time.perf_counter() - t0) * 1000.0
        src = terms.rewrite(key, self.series)  # game words in plain English
        if matched:
            raw = self._translate_with_names(src, matched)
        else:
            raw = terms.commands_not_nouns(self.engine.translate(src, self.history[-4:]), src)
            if not _FEMALE_EN_WORD.search(key):
                raw = _masculinize_default(raw)
        raw = terms.fix_arabic(raw, src, self.series)
        if "(" not in key:
            raw = raw.replace("(", "").replace(")", "")  # the engine brackets names on its own
        if '"' not in key:
            raw = raw.replace('"', "")  # and sometimes wraps place names in quotes
        ar = normalize_arabic(raw)
        if not ar:
            raise TranslateError(f"empty translation for {key!r}")
        self.cache.put(self.profile, key, engine_key, self.style, ar)
        self.history.append((key, ar))
        del self.history[:-8]
        return ar, False, (time.perf_counter() - t0) * 1000.0

    def fix(self, english: str, arabic: str | None) -> None:
        """Remember the user's own wording for a line (None: forget it)."""
        ar = normalize_arabic(arabic or "")
        self.cache.put_fix(self.profile, normalize_english(english), ar or None)


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def list_profiles() -> list[dict]:
    """Every game profile: file name and display title."""
    import yaml

    out = []
    for path in sorted((paths.REPO / "profiles").glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
        title = data.get("title") if isinstance(data, dict) else None
        out.append({"name": path.stem, "title": str(title or path.stem)})
    return out


def save_profile(name: str, data: dict) -> None:
    import yaml

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", name):
        raise TranslateError("profile names use small English letters, digits, - and _ only")
    path = paths.REPO / "profiles" / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
