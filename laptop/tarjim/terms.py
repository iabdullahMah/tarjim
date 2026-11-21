"""Game words that are not names.

A game says "the fence" for a dealer in stolen goods (not «السياج»), "case the bank" for
scouting it (not «نقاضي البنك»), "lose the law" for escaping the police (not «اخسر القانون»),
"hogtie", "parley", "sworn brother", "Kiryu-chan"... The model knows plain English, so these
are put into plain English before it reads the line, and the few Arabic words it still gets
wrong are put right after it.

Every game gets GENERAL (western, gangster, street and old English, soldiers' calls,
idioms); every game of the library belongs to a series that gets its own words as well.
Names of people, places and gangs are in the game's list (packs/) instead. The phone app
has the same lists in Terms.kt: keep the two alike.
"""

from __future__ import annotations

import re

SERIES_IDS = {
    "rdr": ("rdr1", "rdr2"),
    "gta": ("gta-sa", "gta4", "gta5", "gta6"),
    "tlou": ("tlou1", "tlou2"),
    "yakuza": ("yakuza0", "yakuza-kiwami", "yakuza-kiwami2", "yakuza3", "yakuza4", "yakuza5", "yakuza6",
               "lad7", "lad8", "lad-gaiden", "lad-pirate"),
    "sh": ("sh2r",),
    "maxpayne": ("max-payne-3",),
    "lanoire": ("la-noire",),
    "bully": ("bully",),
    "bg3": ("bg3",),
    "ff7": ("ff7r",),
    "kcd": ("kcd2",),
    "mafia": ("mafia-toc",),
    "persona": ("p5r",),
}
_TITLE_HINTS = (("red dead", "rdr"), ("grand theft auto", "gta"), ("gta", "gta"), ("last of us", "tlou"),
                ("yakuza", "yakuza"), ("like a dragon", "yakuza"), ("silent hill", "sh"), ("max payne", "maxpayne"),
                ("l.a. noire", "lanoire"), ("la noire", "lanoire"), ("bully", "bully"), ("canis canem", "bully"),
                ("baldur", "bg3"), ("final fantasy", "ff7"), ("kingdom come", "kcd"), ("mafia", "mafia"),
                ("persona", "persona"))


def series_of(profile: str, title: str = "") -> str:
    """The series of a game profile ("" for none): by the library's id, else by its title."""
    for series, ids in SERIES_IDS.items():
        if profile in ids:
            return series
    text = f"{title} {profile}".lower().replace("-", " ").replace("_", " ")
    return next((series for hint, series in _TITLE_HINTS if hint in text), "")


_I = re.IGNORECASE
_THINGS = r"(?:bank|place|house|store|shop|joint|train|town|camp|ranch|mansion|manor|station|saloon|farm|plantation)"
_CHASERS = r"(?:the law|the cops|the police|the posse|the lawmen|the bounty hunters|the pinkertons|the feds)"
_SENT_START = r"(?:^|(?<=[.!?] )|(?<=[.!?]\" ))"
# "case" is a verb only after these ("in case the house..." is not)
_CASE_VERB = (r"(?:^|(?<=\bto )|(?<=\bwill )|(?<='ll )|(?<=\blet's )|(?<=\bgo )|(?<=\band )|(?<=\bshould )|"
              r"(?<=\bmust )|(?<=\bcan )|(?<=\bcould )|(?<=\bgonna )|(?<=\bwe )|(?<=\byou )|(?<=\bI ))")
_AUX = r"(?:is|are|'s|was|were|will|'ll|has|have|had|might|may|could|would|can|won't|ain't|be|been)"
_LAW_HUNTS = (r"(?:after|coming|here|onto|on to|on our|on your|on his|on their|on my|close|everywhere|outside|"
              r"waiting|chasing|looking for|searching|hunting|find|finds|found|catch|catches|caught|get|gets|got|"
              r"show up|shows up|showed up|turn up|turns up|turned up|arrive|arrives|arrived|all over)")

Rule = tuple[re.Pattern, str]

# Every game: western and crime-game words, and game words the model reads as other words.
GENERAL: tuple[Rule, ...] = (
    (re.compile(r"\bhog-?ties? (him|her|them|it|me|us)\b", _I), r"tie \1 up"),
    (re.compile(r"\bhog-?ties? (the|that|this|a|an) (\w+)\b", _I), r"tie up \1 \2"),
    (re.compile(r"\bhog-?tied\b", _I), "tied up"),
    (re.compile(r"\bhog-?tying\b", _I), "tying up"),
    (re.compile(r"\bhog-?ties?\b", _I), "tie up"),
    (re.compile(r"\blasso(?:es)? (him|her|them|it)\b", _I), r"catch \1 with your rope"),
    (re.compile(r"\blasso(?:es)? (the|that|this|a|an) (\w+)\b", _I), r"catch \1 \2 with your rope"),
    (re.compile(r"\blassoed\b", _I), "caught with a rope"),
    (re.compile(r"\b(your|my|a|the|his|her) lasso\b", _I), r"\1 rope"),
    (re.compile(r"\bparleyed\b", _I), "negotiated"),
    (re.compile(r"\bparleying\b", _I), "negotiating"),
    (re.compile(r"\b(a|the) parley\b", _I), r"\1 negotiation"),
    (re.compile(r"\bparleys?\b", _I), "negotiate"),
    (re.compile(rf"{_CASE_VERB}case (the|this|that|a) ({_THINGS})\b", _I), r"scout \1 \2"),
    (re.compile(rf"\bcased (the|this|that|a) ({_THINGS})\b", _I), r"scouted \1 \2"),
    (re.compile(rf"\bcasing (the|this|that|a) ({_THINGS})\b", _I), r"scouting \1 \2"),
    (re.compile(rf"\b(?:lose|shake off|shake) ({_CHASERS})", _I), r"escape from \1"),
    (re.compile(rf"\b(?:lost|shook off|shook) ({_CHASERS})", _I), r"escaped from \1"),
    (re.compile(rf"\b(?:losing|shaking off|shaking) ({_CHASERS})", _I), r"escaping from \1"),
    (re.compile(r"\b(?:shake|shook) (them|him|her|'em) off\b", _I), r"escape from \1"),
    (re.compile(r"(?:^|(?<=[.!?,;] )|(?<=[.!?]\" ))(?:lose|shake) (them|him|her|'em)(?![\w'])", _I), r"escape from \1"),
    (re.compile(r"\b(from|run from|running from|hide from|hiding from|on the run from|wanted by) the law\b", _I),
     r"\1 the police"),
    (re.compile(rf"\bthe law\b(?=\s+(?:{_AUX}\s+)?(?:gonna\s+|going to\s+|be\s+)?{_LAW_HUNTS}\b)", _I), "the police"),
    (re.compile(r"\b(we|you|I)(?:'ve| have)? got (?:some )?company\b", _I), r"\1 have visitors"),
    (re.compile(r"\b(we|you|I) have (?:some )?company\b", _I), r"\1 have visitors"),
    (re.compile(r"\b(?:lay|lie) low\b", _I), "stay out of sight"),
    (re.compile(r"\b(?:laying|lying) low\b", _I), "staying out of sight"),
    (re.compile(r"\bsaddle up\b", _I), "get on your horse"),
    (re.compile(r"\bgaining on (us|you|me|him|her|them)\b", _I), r"getting closer to \1"),
    (re.compile(r"(?:(?<=\bwe )|(?<=\blet's )|(?<='ll )|(?<=\bto )|(?<=\bgonna ))hit (the|a|that) "
                r"(train|bank|stagecoach|coach|store|shop|jewelry store|armored car|convoy)\b", _I), r"rob \1 \2"),
    (re.compile(r"\bsafe ?houses\b", _I), "hideouts"),
    (re.compile(r"\bsafe ?house\b", _I), "hideout"),
    (re.compile(r"\b(?:you've got|you have|you got|you're at|you are at) an? (\w+)[- ]star wanted level\b", _I),
     r"the police are chasing you with \1 stars"),
    (re.compile(r"\bwanted level\b", _I), "pursuit level"),
    (re.compile(r"\bchop shop\b", _I), "stolen car workshop"),
    (re.compile(r"\bloot (the|those|these|his|her|their|all the|every) "
                r"(bodies|body|corpses|corpse|dead|house|wagon|camp|place|safe|chest|wreck)\b", _I),
     r"search \1 \2 for valuables"),
    (re.compile(r"\bfast[- ]travel(?:led|ed)? to\b", _I), "travel quickly to"),
    (re.compile(r"\b(?:your|the) horse's bonding(?: level)? (?:went up|has gone up|increased|has increased|rose|"
                r"has risen|improved|has improved)\b", _I), "your bond with your horse grew stronger"),
    (re.compile(r"\b(?:your|the) horse's bonding(?: level)?\b", _I), "your bond with your horse"),
    (re.compile(r"\bbonding level\b", _I), "bond"),
    (re.compile(rf"{_SENT_START}(?:go )?fish(?:ing)? (in|at|near|from|off) (?!.*\b(?:are|is|were|was|swim|swims|live|lives)\b)",
                _I), r"catch fish \1 "),
    (re.compile(r"\bchores\b", _I), "tasks"),
    (re.compile(r"\bheld up (the|a|that) (stagecoach|coach|train|bank|store|shop|wagon|stage)\b", _I), r"robbed \1 \2"),
    (re.compile(r"\bhold(?:ing)? up (the|a|that) (stagecoach|coach|train|bank|store|shop|wagon|stage)\b", _I), r"rob \1 \2"),
    (re.compile(r"\b(run|runs|ran|running) off\b(?! (?:the|a) (?:road|bridge|cliff|edge|track))", _I), r"\1 away"),
    (re.compile(r"\b(the|a|that|your) gunsmith\b", _I), r"\1 gun shop"),
    # western speech
    (re.compile(r"\bhowdy\b", _I), "hello"),
    (re.compile(r"\bmuch obliged\b", _I), "thank you very much"),
    (re.compile(r"\b(?:skedaddle|vamoose)\b", _I), "run away"),
    (re.compile(r"\bhigh-?tailed it\b", _I), "ran away"),
    (re.compile(r"\bhigh-?tail(?:ing)? it\b", _I), "run away"),
    (re.compile(r"\bthe jig(?:'s| is) up\b", _I), "it's over"),
    (re.compile(r"\b(?:greenhorn|tenderfoot)s\b", _I), "beginners"),
    (re.compile(r"\b(?:greenhorn|tenderfoot)\b", _I), "beginner"),
    (re.compile(r"\byonder's\b", _I), "over there is"),
    (re.compile(r"\b(?:over |out )?yonder\b", _I), "over there"),
    # gangster and police slang
    (re.compile(r"\bpacking heat\b", _I), "carrying a gun"),
    (re.compile(r"\bpacking (a|his|her|their|the|my|your) (?:heater|gat|rod|roscoe|piece|gun|pistol|revolver)\b", _I),
     r"carrying \1 gun"),
    (re.compile(r"\b(has|had|got|with|pulled|pulls|drew|draws) (a|his|her|their|the|my|your) (?:heater|gat|roscoe|piece)\b",
                _I), r"\1 \2 gun"),
    (re.compile(r"(?<!self-)\bmade men\b", _I), "full members of the mafia"),
    (re.compile(r"(?<!self-)\ba made man\b", _I), "a full member of the mafia"),
    (re.compile(r"\b(?:rub|bump) (him|her|them|you|me|us) (?:out|off)\b", _I), r"kill \1"),
    (re.compile(r"\b(?:rubbed|bumped) (him|her|them|you|me|us) (?:out|off)\b", _I), r"killed \1"),
    (re.compile(r"\brub out (the|that|this) (\w+)\b", _I), r"kill \1 \2"),
    (re.compile(r"\brubbed out (the|that|this) (\w+)\b", _I), r"killed \1 \2"),
    (re.compile(r"\bsleeps with the fishes\b", _I), "is dead at the bottom of the sea"),
    (re.compile(r"\bsleeping with the fishes\b", _I), "dead at the bottom of the sea"),
    (re.compile(r"\bsleep with the fishes\b", _I), "be dead at the bottom of the sea"),
    (re.compile(r"\bomert[àa]\b", _I), "the law of silence"),
    (re.compile(r"\bpizzo\b", _I), "protection money"),
    (re.compile(r"\bpicciotti\b", _I), "young men"),
    (re.compile(r"\bpicciotto\b", _I), "young man"),
    (re.compile(r"\bconsiglieres?\b", _I), "advisor"),
    (re.compile(r"\bspeakeasies\b", _I), "secret bars"),
    (re.compile(r"\bspeakeasy\b", _I), "secret bar"),
    (re.compile(r"\b(?:hooch|moonshine)\b", _I), "illegal alcohol"),
    (re.compile(r"\bbootleg (liquor|booze|whiskey|alcohol)\b", _I), r"illegal \1"),
    (re.compile(r"\bsweat (him|her|them)\b(?! out)", _I), r"pressure \1"),
    (re.compile(r"\brats? (us|him|her|them|me|you) out\b", _I), r"inform on \1 to the police"),
    (re.compile(r"\bratted (us|him|her|them|me|you) out\b", _I), r"informed on \1 to the police"),
    (re.compile(r"\bratting (us|him|her|them|me|you) out\b", _I), r"informing on \1 to the police"),
    (re.compile(r"\bsquealed on (us|him|her|them|me|you)\b", _I), r"informed on \1 to the police"),
    (re.compile(r"\bsqueals? on (us|him|her|them|me|you)\b", _I), r"inform on \1 to the police"),
    # street slang
    (re.compile(r"\b(you|you're|he's|she's|they're|we're|I'm|are you|is he|is she|y'all) strapped\b(?! for)", _I),
     r"\1 armed"),
    (re.compile(r"\bopps\b", _I), "enemies"),
    (re.compile(r"\b(?:rolling|rollin') up on\b", _I), "coming at"),
    (re.compile(r"\brolled up on\b", _I), "came at"),
    (re.compile(r"\bhit (?:a|another|one more|this|that) lick\b", _I), "do a robbery"),
    (re.compile(r"\bfive-o\b", _I), "police"),
    (re.compile(r"\btrap houses?\b", _I), "drug house"),
    (re.compile(r"\bgive (you|him|her|me|them|us) (?:a )?wedgies?\b", _I), r"humiliate \1"),
    (re.compile(r"\bgives (you|him|her|me|them|us) (?:a )?wedgies?\b", _I), r"humiliates \1"),
    (re.compile(r"\bgave (you|him|her|me|them|us) (?:a )?wedgies?\b", _I), r"humiliated \1"),
    (re.compile(r"\bgiving (you|him|her|me|them|us) (?:a )?wedgies?\b", _I), r"humiliating \1"),
    # old English (Kingdom Come, Baldur's Gate and the like)
    (re.compile(r"\bhold (?:thy|your|thine) tongue\b", _I), "be quiet"),
    (re.compile(r"\bprithee\b", _I), "please"),
    (re.compile(r"\bmethinks\b", _I), "I think"),
    (re.compile(r"\b(?:mayhap|perchance)\b", _I), "maybe"),
    (re.compile(r"\bverily\b", _I), "truly"),
    (re.compile(r"\bforsooth\b", _I), "indeed"),
    (re.compile(r"\bnaught\b", _I), "nothing"),
    (re.compile(r"\b(?:m'lord|milord)\b", _I), "my lord"),
    (re.compile(r"\b(?:m'lady|milady)\b", _I), "my lady"),
    (re.compile(r"\bwherefore\b", _I), "why"),
    (re.compile(r"\bwhence\b", _I), "from where"),
    (re.compile(r"\bthither\b", _I), "there"),
    (re.compile(r"\bhither\b", _I), "here"),
    # soldiers' calls
    (re.compile(r"\btango down\b", _I), "the enemy is dead"),
    (re.compile(r"(?<=, )moving (to|up|out|in)\b", _I), r"we're moving \1"),
    (re.compile(r"^man down\b", _I), "a man has fallen"),
    (re.compile(r"\bfrag out\b", _I), "grenade"),
    (re.compile(r"\b(?:roger|copy) that\b", _I), "understood"),
    (re.compile(r"^move out\b", _I), "let's go"),
    (re.compile(r"^(?:all |room |area )?clear(?=!*$)", _I), "the area is clear"),
    # idioms
    (re.compile(r"\b(is|are|was|were|it's|it is) (?:crawling|swarming) with\b", _I), r"\1 full of"),
    (re.compile(r"\b(want|wants|wanted|need|needs|needed|'d like|would like) a (?:quick )?word with "
                r"(you|him|her|them|me|us)\b", _I), r"\1 to talk to \2"),
    (re.compile(r"\b(want|wants|wanted|need|needs|needed|'d like|would like) a (?:quick )?word\b", _I),
     r"\1 to talk to you"),
    (re.compile(r"\bworry about yourself\b", _I), "take care of yourself"),
    (re.compile(r"\bcatch you on the flip(?: ?side)?\b", _I), "see you later"),
    (re.compile(r"\bsay less\b", _I), "understood"),
    (re.compile(r"^do what now\?", _I), "what do you mean?"),
    (re.compile(r"\b(?:it'?ll|it will) be a cold day in (?:\w+ )?hell(?=[.!?]|$)", _I), "that will never happen"),
    (re.compile(r"\b(he|she|it)(?:'s| has| is) got another thing coming\b", _I), r"\1 is badly mistaken"),
    (re.compile(r"\b(you|they|we)(?:'ve| have)? got another thing coming\b", _I), r"\1 are badly mistaken"),
    (re.compile(r"\bpull(?:ed)? the plug on\b", _I), "cut ties with"),
    (re.compile(r"\bpulling the plug on\b", _I), "cutting ties with"),
    (re.compile(r"\bpull up (?:in )?front of\b", _I), "stop in front of"),
    (re.compile(r"\bblackball(ed|ing)?\b", _I), r"blacklist\1"),
    (re.compile(r"\bpoof!", _I), "just like that!"),
    (re.compile(r"\bwith your bun in her oven\b", _I), "pregnant with your baby"),
    (re.compile(r"\b(?:a|your|his|my) bun in (?:the|her) oven\b", _I), "a baby on the way"),
    (re.compile(r"\bhang(ing)? out with\b", _I), r"spend\1 time with"),
    # someone who stops answering: "she ghosted me"
    (re.compile(r"\bghosted (me|you|him|her|us|them)\b", _I), r"cut off all contact with \1"),
    (re.compile(r"\bghosting (me|you|him|her|us|them)\b", _I), r"cutting off all contact with \1"),
    (re.compile(r"\bghost (me|you|him|her|us|them)\b", _I), r"cut off all contact with \1"),
    (re.compile(r"\bgot ghosted\b", _I), "was abandoned"),
    (re.compile(r"\bpeaced out\b", _I), "left"),
    (re.compile(r"\bhook (me|us) up(?=[?!.,]|$)", _I), r"introduce \1 to someone"),
    (re.compile(r"\bhooking up with\b", _I), "dating"),
    (re.compile(r"\bhooked up with\b", _I), "dated"),
    (re.compile(r"\bhook up with\b", _I), "date"),
    # "I got you": I understand, or I'm here for you, not "I caught you"
    (re.compile(r"(?:^|(?<=[.!?] ))I got you(?=[.!]|$)", _I), "I'm with you"),
    (re.compile(r"\bmy guy\b", _I), "my friend"),
    (re.compile(r"(?<=[,!] )bro(?:-bro)?(?=[.!?,]|$)", _I), "brother"),
    (re.compile(r"\bdude\b", _I), "man"),
    # "baby" said to someone, not a child: "Drinks for everyone, baby!" «وطفل رضيع»
    (re.compile(r"(?<=[,!] )baby(?=[.!?,]|$)", _I), "darling"),
    (re.compile(r"^baby(?=, )", _I), "darling"),
    # swearing the model takes word for word
    (re.compile(r"\bget fucked\b", _I), "go to hell"),
    (re.compile(r"\byou fuck(?=[!.?,]|$)", _I), "you bastard"),
    (re.compile(r"\bfuckin'? A\b", _I), "absolutely"),
    (re.compile(r"\b(really|just|so|even) fuckin'?\s+(?=(?:you|me|him|her|it|us|them)\b)", _I), r"\1 "),
    (re.compile(r"\byou fuckin'? (understand|hear|listen|get|know|think|want|see)\b", _I), r"you \1"),
    (re.compile(r"\bfuckin'(?=\s)", _I), "fucking"),
    (re.compile(r"(?:^|(?<=[.!?\u2026] ))((?:oh|ah|aw|aww|ooh|uh),? )?shit(?=[!.\u2026,]|$)", _I), r"\1damn"),
    (re.compile(r"\bholy shit\b", _I), "oh my God"),
    (re.compile(r"^no shit\b", _I), "obviously"),
    (re.compile(r"\bget (my|your|his|her|our|their) shit together\b", _I), r"get \1 life in order"),
    (re.compile(r"\b(my|your|his|her|our|their) shit\b", _I), r"\1 stuff"),
    (re.compile(r"\bpiece of shit (car|truck|boat|phone|place|house|gun)\b", _I), r"junk \1"),
    (re.compile(r"\bpiece of shit\b", _I), "bastard"),
    (re.compile(r"\b(is|'s|are|'re) shitting (?:his|her|their|your) pants\b", _I), r"\1 terrified"),
    (re.compile(r"\bdon't shit yourself\b", _I), "don't panic"),
    # button prompts: the button is a picture the reader leaves out, "Hold to sprint"
    (re.compile(r"\b(Press|Hold|Use)to\b", _I), r"\1 to"),
    (re.compile(r"^Hold(?= (?:[LR][123]|or|and|to|down)\b)", _I), "Press and hold"),
    (re.compile(r"^Press to\b", _I), "Press the button to"),
    (re.compile(r"^Release to\b", _I), "Let go of the button to"),
    (re.compile(r"\bloadout\b", _I), "equipment"),
    (re.compile(r"^Keep (left|right)\b", _I), r"stay on the \1"),
    (re.compile(r"^Stash (the|your)\b", _I), r"hide \1"),
)

# A rule of a series, with an optional condition the whole line must meet.
SeriesRule = tuple[re.Pattern, str, "re.Pattern | None"]
_SELLING = re.compile(r"\b(?:sell|sold|selling|stolen|loot|valuables|trinkets|jewel|jewelry|take it|price|pay|buys|"
                      r"fenced|goods)\b", _I)
_LAW_KEEP_BEFORE = ("against", "above", "break", "breaking", "broke", "broken", "obey", "obeying", "within", "by",
                    "under", "follow", "following", "respect", "it's", "is", "that's", "was", "uphold", "upholding",
                    "enforce", "I'm", "am", "letter of", "according to", "eyes of")
_LAW_KEEP = "".join(f"(?<!\\b{w} )" for w in _LAW_KEEP_BEFORE)

_HONORIFICS: tuple[SeriesRule, ...] = (
    # "-san" stays: without it the model takes a name like "Majima" for a woman's
    (re.compile(r"\b([A-Z][a-z]+)-(?:chan|kun|sama|senpai|sensei|dono|han|bo)\b"), r"\1", None),
    (re.compile(r"\b(?:nii-?san|nii-?chan|onii-?san|onii-?chan|aniki)\b", _I), "big brother", None),
    (re.compile(r"\b(?:nee-?san|onee-?san|onee-?chan|ane-?san)\b", _I), "big sister", None),
)
_KILLED: tuple[SeriesRule, ...] = (  # "whacked him" is killed him in a crime story, hit him elsewhere
    (re.compile(r"\bwhacks? (him|her|them|you|me|us|the \w+|that \w+)\b", _I), r"kill \1", None),
    (re.compile(r"\bwhacked (him|her|them|you|me|us|the \w+|that \w+)\b", _I), r"killed \1", None),
    (re.compile(r"\bwhacking (him|her|them|you|me|us)\b", _I), r"killing \1", None),
    (re.compile(r"\b(got|get|gets|getting) whacked\b", _I), r"\1 killed", None),
)

SERIES: dict[str, tuple[SeriesRule, ...]] = {
    "gta": _KILLED + (
        (re.compile(r"\b(nice|new|sweet|fancy|my|your|his|her|that|this) whip\b", _I), r"\1 car", None),
        (re.compile(r"\bchicks\b", _I), "girls", None),
        (re.compile(r"\bchick\b", _I), "girl", None),
        (re.compile(r"\b(so|real|really|pretty|damn|fucking) dope\b", _I), r"\1 cool", None),
        (re.compile(r"\bdope!", _I), "cool!", None),
        (re.compile(r"\b(?:a )?lame(?=[,.!?]|$)", _I), "boring", None),
        (re.compile(r"\b(?:yeyo|yayo)\b", _I), "cocaine", None),
        (re.compile(r"\bsome blanco\b", _I), "some cocaine", None),
        (re.compile(r"\bstay on his ass\b", _I), "stay right behind him", None),
        (re.compile(r"\bstay on her ass\b", _I), "stay right behind her", None),
        (re.compile(r"\bstay on their ass(?:es)?\b", _I), "stay right behind them", None),
        (re.compile(r"\bthe Keys\b"), "the islands", None),  # Leonida's islands, not keys
        (re.compile(r"\ball over the keys\b", _I), "all over the islands", None),
        (re.compile(r"(?<=, )(?:dog|dawg)(?=[.!?,]|$)", _I), "man", None),
        (re.compile(r"(?<=, )mama(?=[.!?,]|$)", _I), "girl", None),
        (re.compile(r"^Chase(?= [A-Z]|$)"), "Pursue", None),  # a mission goal, not a name
        (re.compile(r"\bcap (him|her|them|you|that \w+)\b", _I), r"shoot \1", None),
        (re.compile(r"\bcapped (him|her|them|you|that \w+)\b", _I), r"shot \1", None),
    ),
    "mafia": _KILLED,
    "lanoire": _KILLED,
    "maxpayne": _KILLED,
    "persona": _HONORIFICS + (
        (re.compile(r"\bcalling cards\b", _I), "warning cards", None),
        (re.compile(r"\bcalling card\b", _I), "warning card", None),
    ),
    "sh": (
        (re.compile(r"\bstatic\b", _I), "noise", None),  # the radio's warning
    ),
    "bully": (
        (re.compile(r"\b(get|got|getting|gets) detention\b", _I), r"\1 punished after school", None),
        (re.compile(r"\bdetention\b", _I), "after-school punishment", None),
    ),
    "kcd": (
        (re.compile(r"\bere\b", _I), "before", None),
    ),
    "bg3": (
        (re.compile(r"\bere\b", _I), "before", None),
    ),
    "tlou": (
        (re.compile(r"^(Runners|Clickers|Stalkers|Bloaters|Shamblers|Infected)!", _I), r"\1 are coming!", None),
    ),
    "rdr": (
        # in a western "the law" is the lawmen, except "against the law", "it's the law", ...
        (re.compile(rf"{_LAW_KEEP}\bthe law\b(?!\s+(?:of|says|said|is clear|and order|is the law))", _I), "the police", None),
        (re.compile(r"\b(the|a|that|this|another|their|his|her|your) posse\b", _I), r"\1 pursuers", None),
        (re.compile(r"\b(your|my|his|her|the|a|that) repeater\b", _I), r"\1 rifle", None),
        (re.compile(r"\b(the|a|that|your) trapper\b", _I), r"\1 fur trader", None),
        (re.compile(r"\b(a|the|any|your|that) fence\b", _I), r"\1 stolen goods dealer", _SELLING),
        (re.compile(r"\bfence (it|them|this|that|these|those)\b(?! off| in)", _I), r"sell \1 as stolen goods", None),
    ),
    "yakuza": _HONORIFICS + (
        (re.compile(r"\b(?:oyaji|oyassan)\b", _I), "old man", None),
        (re.compile(r"\blieutenants\b", _I), "deputies", None),
        (re.compile(r"\blieutenant\b", _I), "deputy", None),
        (re.compile(r"\boyabun\b", _I), "boss", None),
        (re.compile(r"\bkyodai\b", _I), "brother", None),
        (re.compile(r"\bpatriarch of\b", _I), "head of", None),
        (re.compile(r"\bpatriarch\b", _I), "family head", None),
        (re.compile(r"\bsworn brothers\b", _I), "brothers by oath", None),
        (re.compile(r"\bsworn brother\b", _I), "brother by oath", None),
    ),
}

# "pills" with nothing in the line about birth control: the model writes «حبوب منع الحمل»
# for any pill ("a real doctor, with real pills", "take a pill for it").
_PILLS_EN = re.compile(r"^(?!.*\b(?:birth[- ]control|contracepti\w*|on the pill)\b).*\bpills?\b", _I)

# Arabic the model still gets wrong: (English that must be in the line or None, wrong, right).
AR_FIXES: dict[str, tuple[tuple["re.Pattern | None", str, str], ...]] = {
    "": (
        (_PILLS_EN, "حبوب منع الحمل", "الحبوب"),
        (_PILLS_EN, "حبة منع الحمل", "الحبة"),
        (None, "بطاقات التموينية", "بطاقات التموين"),
        (re.compile(r"\bbachelor part(?:y|ies)\b", _I), "حزب البكالوريوس", "حفلة توديع العزوبية"),
        (re.compile(r"\bbachelor part(?:y|ies)\b", _I), "حفلة البكالوريوس", "حفلة توديع العزوبية"),
    ),
    "tlou": (
        (re.compile(r"\bspores?\b", _I), "الجراثيم", "الأبواغ"),
        (re.compile(r"\bspores?\b", _I), "جراثيم", "أبواغ"),
    ),
}
_SHOOT_EN = re.compile(r"\b(?:shoot|shoots|shot|shooting|fire|fires|fired|firing)\b", _I)
# Arabic patterns the model gets wrong: (English that must be in the line or None, wrong, right).
AR_PATTERNS: tuple[tuple["re.Pattern | None", re.Pattern, str], ...] = (
    (None, re.compile(r"(?<![ء-ي])([وف]?)[اإأ]بقى(?![ء-ي])"), r"\1ابق"),  # «إبقى خلفي» → «ابق خلفي», «وابقى» → «وابق»
    # "unless they shoot first" → «إلا إذا أطلقوا أولاً»: the fire went missing
    (_SHOOT_EN, re.compile(r"(?<![ء-ي])((?:و|ف)?[أا]طلق(?:وا|ت|نا)?) (?=(?:أولاً|أولا|أولًا|علي(?:ه|ها|هم|نا|ك|كم)|عليّ)(?![ء-ي]))"),
     r"\1 النار "),
)
_ANY = re.compile("|".join(f"(?:{rx.pattern})" for rx, _ in GENERAL), _I)


def _sub(rx: re.Pattern, repl: str, text: str) -> str:
    """Replace, keeping a capital where the replaced words had one ("Howdy" → "Hello"): the
    model translates "Be quiet, peasant!" better than "be quiet, peasant!"."""
    def one(m: re.Match) -> str:
        out = m.expand(repl)
        if m.group(0)[:1].isupper() and out[:1].islower():
            out = out[:1].upper() + out[1:]
        return out
    return rx.sub(one, text)


def rewrite(english: str, series: str = "") -> str:
    """The line in plain English for the model: game words, then the series' words."""
    if _ANY.search(english):
        for rx, repl in GENERAL:
            english = _sub(rx, repl, english)
    for rx, repl, cond in SERIES.get(series, ()):
        if cond is None or cond.search(english):
            english = _sub(rx, repl, english)
    return english


def fix_arabic(arabic: str, english: str, series: str = "") -> str:
    for group in ("", series) if series else ("",):
        for rx, wrong, right in AR_FIXES.get(group, ()):
            if wrong in arabic and (rx is None or rx.search(english)):
                arabic = arabic.replace(wrong, right)
    for en_rx, rx, right in AR_PATTERNS:
        if (en_rx is None or en_rx.search(english)) and rx.search(arabic):
            arabic = rx.sub(right, arabic)
    return arabic


# -- commands written as verbal nouns -------------------------------------------------
#
# The model often writes a command as a verbal noun: "Buy some tonic" → «شراء بعض منشط»,
# "Pay the protection money" → «دفع أموال الحماية», "Negotiate with them first" → «التفاوض معهم».
# When the English line (or the part after "Name,") opens with one of these verbs and the
# Arabic with its verbal noun, the command is written instead: «اشتر»، «ادفع»، «تفاوض». Its
# gender is put right afterwards with the rest of the line (gender.py).

VERB_NOUNS: dict[str, dict[str, str]] = {
    "buy": {"شراء": "اشتر"},
    "sell": {"بيع": "بع"},
    "pay": {"دفع": "ادفع"},
    "hunt": {"صيد": "اصطد"},
    "catch": {"صيد": "اصطد", "القبض": "اقبض", "الإمساك": "أمسك", "إمساك": "أمسك"},
    "remove": {"إزالة": "أزل"},
    "negotiate": {"التفاوض": "تفاوض"},
    "escape": {"الهروب": "اهرب", "الهرب": "اهرب", "هروب": "اهرب", "هرب": "اهرب"},
    "run": {"الركض": "اركض", "الهروب": "اهرب"},
    "cook": {"طبخ": "اطبخ", "طهي": "اطبخ"},
    "craft": {"صنع": "اصنع", "صناعة": "اصنع"},
    "make": {"صنع": "اصنع"},
    "rob": {"سرقة": "اسرق"},
    "steal": {"سرقة": "اسرق"},
    "search": {"البحث": "ابحث", "بحث": "ابحث", "تفتيش": "فتّش"},
    "look": {"البحث": "ابحث", "النظر": "انظر"},
    "ride": {"ركوب": "اركب"},
    "feed": {"إطعام": "أطعم"},
    "clean": {"تنظيف": "نظّف"},
    "brush": {"تنظيف": "نظّف"},
    "save": {"حفظ": "احفظ", "إنقاذ": "أنقذ"},
    "rescue": {"إنقاذ": "أنقذ", "الإنقاذ": "أنقذ"},
    "collect": {"جمع": "اجمع"},
    "gather": {"جمع": "اجمع"},
    "carry": {"حمل": "احمل"},
    "kill": {"قتل": "اقتل"},
    "drink": {"شرب": "اشرب"},
    "follow": {"اتباع": "اتبع"},
    "stay": {"البقاء": "ابق", "الابتعاد": "ابتعد"},
    "keep": {"الابتعاد": "ابتعد", "الحفاظ": "حافظ"},
    "go": {"الذهاب": "اذهب"},
    "get": {"الحصول": "احصل"},
    "watch": {"مشاهدة": "شاهد", "مراقبة": "راقب"},
    "bring": {"إحضار": "أحضر", "جلب": "اجلب"},
    "fetch": {"إحضار": "أحضر", "جلب": "اجلب"},
    "use": {"استخدام": "استخدم"},
    "return": {"إعادة": "أعد", "العودة": "عد"},
    "deliver": {"تسليم": "سلّم"},
    "hand": {"تسليم": "سلّم"},
    "drive": {"قيادة": "قد", "القيادة": "قد"},
    "draw": {"سحب": "اسحب"},
    "pull": {"سحب": "اسحب"},
    "take": {"أخذ": "خذ", "اتخاذ": "خذ"},
    "grab": {"أخذ": "خذ", "الإمساك": "أمسك"},
    "put": {"وضع": "ضع"},
    "place": {"وضع": "ضع"},
    "leave": {"ترك": "اترك", "مغادرة": "غادر"},
    "shoot": {"إطلاق": "أطلق"},
    "fire": {"إطلاق": "أطلق"},
    "find": {"العثور": "اعثر", "إيجاد": "جد", "البحث": "ابحث"},
    "locate": {"تحديد": "حدد"},
    "wait": {"الانتظار": "انتظر", "انتظار": "انتظر"},
    "check": {"التحقق": "تحقق", "فحص": "افحص"},
    "hide": {"الاختباء": "اختبئ", "إخفاء": "أخف"},
    "approach": {"الاقتراب": "اقترب"},
    "listen": {"الاستماع": "استمع"},
    "stop": {"التوقف": "توقف", "إيقاف": "أوقف"},
    "tie": {"ربط": "اربط"},
    "travel": {"السفر": "سافر"},
    "open": {"فتح": "افتح"},
    "close": {"إغلاق": "أغلق"},
    "help": {"مساعدة": "ساعد", "المساعدة": "ساعد"},
    "skin": {"سلخ": "اسلخ"},
    "donate": {"التبرع": "تبرع"},
    "head": {"التوجه": "توجه"},
    "meet": {"مقابلة": "قابل", "لقاء": "قابل"},
    "talk": {"التحدث": "تحدث"},
    "speak": {"التحدث": "تحدث"},
    "defend": {"الدفاع": "دافع"},
    "protect": {"حماية": "احم"},
    "attack": {"مهاجمة": "هاجم", "الهجوم": "هاجم"},
    "destroy": {"تدمير": "دمّر"},
    "eat": {"تناول": "تناول"},
    "give": {"إعطاء": "أعط"},
    "tell": {"إخبار": "أخبر"},
    "send": {"إرسال": "أرسل"},
    "call": {"الاتصال": "اتصل"},
    "throw": {"رمي": "ارم"},
    "jump": {"القفز": "اقفز"},
    "swim": {"السباحة": "اسبح"},
    "track": {"تعقب": "تعقب", "تتبع": "تتبع"},
    "sneak": {"التسلل": "تسلل"},
    "climb": {"تسلق": "تسلق"},
    "escort": {"مرافقة": "رافق"},
    "choose": {"اختيار": "اختر"},
    "select": {"اختيار": "اختر", "تحديد": "حدد"},
    "sit": {"الجلوس": "اجلس", "جلوس": "اجلس"},
    "move": {"التحرك": "تحرك", "تحريك": "حرّك", "نقل": "انقل"},
    "dance": {"الرقص": "ارقص"},
    "examine": {"فحص": "افحص"},
    "pick": {"التقاط": "التقط", "إلتقاط": "التقط", "اختيار": "اختر"},
    "deal": {"التعامل": "تعامل"},
    "pursue": {"مطاردة": "طارد", "ملاحقة": "لاحق"},
    "chase": {"مطاردة": "طارد", "ملاحقة": "لاحق"},
    "change": {"تغيير": "غيّر", "التغيير": "غيّر"},
    "visit": {"زيارة": "زر"},
    "enter": {"الدخول": "ادخل", "دخول": "ادخل"},
    "equip": {"تجهيز": "جهّز"},
    "investigate": {"التحقيق": "حقق"},
    "order": {"طلب": "اطلب"},
    "spend": {"قضاء": "اقض"},
}
# The word after the first one shows it is a noun, not a command: "Escape is impossible".
_NOT_A_COMMAND_NEXT = {"is", "was", "are", "were", "will", "would", "can", "could", "should", "has", "have", "had",
                       "isn't", "wasn't", "won't", "might", "must", "may", "did", "does", "do", "makes", "made",
                       "seems", "looks", "needs", "takes", "sounds", "feels", "costs", "of", "'s", "and", "or"}
_EN_VOCATIVE = re.compile(r"^(?:[A-Z][\w'.\-]*\s+){0,2}[A-Z][\w'.\-]*,\s+")
_AR_WORD = re.compile(r"[ء-يّ]+")
_SUFFIX = ("", "ه", "ها", "هم", "هما", "ني", "نا")


def _command(token: str, nouns: dict[str, str]) -> str | None:
    for noun, cmd in nouns.items():
        if token.startswith(noun) and token[len(noun):] in _SUFFIX:
            return cmd + token[len(noun):]
    return None


def starts_with_command(english: str) -> bool:
    """The line opens with a command we know ("Buy Maria a ring"), not with a noun."""
    words = english.lstrip(" \"'-–—").split()
    if len(words) < 2:
        return False
    return words[0].lower().strip(",.!?") in VERB_NOUNS and words[1].lower().strip(",.!?") not in _NOT_A_COMMAND_NEXT


def commands_not_nouns(arabic: str, english: str) -> str:
    """The first command of the line, and those joined to it by «و», as commands."""
    en = english.lstrip(" \"'-–—")
    start = 0
    vm = _EN_VOCATIVE.match(en)
    if vm:
        en = en[vm.end():]
        comma = arabic.find("،")
        if comma < 0:
            comma = arabic.find(",")
        if comma < 0 or comma > 40:
            return arabic
        start = comma + 1
    words = en.split()
    if len(words) < 2:
        return arabic
    verb, nxt = words[0].lower().strip(",.!?"), words[1].lower().strip(",.!?")
    nouns = VERB_NOUNS.get(verb)
    if not nouns or nxt in _NOT_A_COMMAND_NEXT:
        return arabic
    m = _AR_WORD.search(arabic, start)
    if not m or arabic[start:m.start()].strip(" «\"'"):
        return arabic
    cmd = _command(m.group(0), nouns)
    if cmd is None:
        return arabic
    out = arabic[:m.start()] + cmd + arabic[m.end():]
    # «... واتخاذ اللحوم» for "... and take the meat": the next commands of the sentence
    end = len(out)
    for stop in ".!؟?":
        k = out.find(stop, m.start())
        if 0 <= k < end:
            end = k
    pos = m.start() + len(cmd)
    for v in re.findall(r"\band (?:then )?([a-z]+)\b", en.lower()):
        later = VERB_NOUNS.get(v)
        if not later:
            continue
        for w in _AR_WORD.finditer(out, pos, end):
            tok = w.group(0)
            if tok.startswith("و") and (c := _command(tok[1:], later)):
                out = out[:w.start()] + "و" + c + out[w.end():]
                pos = w.start() + 1 + len(c)
                end += len(c) + 1 - len(tok)
                break
    return out
