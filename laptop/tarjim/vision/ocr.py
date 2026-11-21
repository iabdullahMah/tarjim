"""OCR of a detected subtitle block, one text line at a time (RapidOCR, recognition only)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from .detect import Block

TARGET_LINE_H = 48  # px; gives an x-height of at least 24 px for subtitle faces
MIN_SCORE = 0.60
# One or two capitalised words, then a colon: "MARIA: ...", "Old Tom: ...".
_SPEAKER = re.compile(r"^([A-Z][A-Za-z.'\-]{1,15}(?: [A-Z][A-Za-z.'\-]{1,15})?):\s+(.*)$")


@dataclass
class OcrResult:
    text: str  # all lines joined into one sentence
    lines: list[str]
    score: float  # lowest line confidence
    speaker: str | None = None

    @property
    def ok(self) -> bool:
        return self.score >= MIN_SCORE and looks_like_subtitle(self.text)


def looks_like_subtitle(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 2:
        return False
    if any(c.isdigit() for c in text) and len(letters) < 4:
        return False  # "PS5", "x2", "L1": interface labels, not speech
    if text.isupper() and not text.rstrip().endswith(("!", "?", ".", "…")):
        # all-caps labels: names, places and button prompts, and longer ones too: the radio's
        # song captions and menu bars ("SELECT  ZOOM  BACK") are not speech
        return False
    latin = sum(1 for c in letters if c.isascii())
    return latin / len(letters) >= 0.7


def looks_like_label(text: str) -> bool:
    """One to three words, each starting with a capital or a mark, and no sentence's end: the
    name of a street, a shop or a place the game writes on the picture ("Gas Station", "Port
    Somewhere", a price), or a piece of one. Speech ends in a mark, and is rarely all capitals
    at the start of each word. A mission goal has the same shape ("Follow Someone"): the caller
    lets those through by their verb."""
    words = text.split()
    if not 1 <= len(words) <= 3 or text.rstrip()[-1:] in ".!?…,\"'-":
        return False
    return all(w[:1].isupper() or not w[:1].isalpha() for w in words)


def keep_lines(lines: list[str], scores: list[float]) -> tuple[list[str], list[float]]:
    """Leave out what is not part of the sentence: an empty reading, or a few badly read
    characters on a line of their own beside a well read line (a button picture or a mark the
    game draws under its subtitle). The whole block used to be judged by its worst line, so one
    such scrap ("KW", "21", "V") made a perfectly read sentence count as unreadable and it got
    no Arabic at all. A scrap that is all there is stays, and is rejected as before."""
    pairs = [(t, s) for t, s in zip(lines, scores) if t.strip()]
    if not pairs:
        return [], []
    longest = max(len(t.strip()) for t, _ in pairs)
    good = [(t, s) for t, s in pairs if not (s < MIN_SCORE and len(t.strip()) <= max(6, 0.3 * longest))]
    if good and len(good) < len(pairs) and all(s >= MIN_SCORE for _, s in good):
        pairs = good
    return [t for t, _ in pairs], [s for _, s in pairs]


def join_lines(lines: list[str]) -> str:
    out = ""
    for line in (l.strip() for l in lines):
        if not line:
            continue
        if out.endswith("-") and len(out) > 1 and out[-2].isalpha():
            out = out[:-1] + line  # a word hyphenated across the line break
        else:
            out = f"{out} {line}" if out else line
    return re.sub(r"\s+", " ", out).strip()


def split_speaker(text: str) -> tuple[str | None, str]:
    m = _SPEAKER.match(text)
    return (m.group(1), m.group(2)) if m else (None, text)


class OcrEngine:
    def __init__(self) -> None:
        from rapidocr import RapidOCR
        from rapidocr.ch_ppocr_rec.typings import TextRecInput
        from rapidocr.utils.typings import LangRec

        self._rec_input = TextRecInput
        self._engine = RapidOCR(
            params={
                "Rec.lang_type": LangRec.EN,
                "Global.use_det": False,
                "Global.use_cls": False,
                "Global.log_level": "error",
                "EngineConfig.onnxruntime.intra_op_num_threads": 4,
            }
        )

        # Warm-up: the first inference loads the model and is several times slower.
        self._engine(np.full((TARGET_LINE_H, 320, 3), 255, np.uint8), use_det=False, use_cls=False, use_rec=True)

    def _read_line(self, img: np.ndarray) -> tuple[str, float]:
        res = self._engine.text_rec(self._rec_input(img=[img]))
        txts = getattr(res, "txts", None) or ()
        scores = getattr(res, "scores", None) or ()
        if not txts:
            return "", 0.0
        return str(txts[0]).strip(), float(scores[0])

    def read(self, frame_bgr: np.ndarray, block: Block) -> OcrResult:
        fh, fw = frame_bgr.shape[:2]
        lines: list[str] = []
        scores: list[float] = []
        for ln in block.lines:
            pad = max(4, (ln.y1 - ln.y0) // 3)
            # The line box comes from full-height glyphs; the stroke mask also holds
            # the small marks around them (a final full stop, an apostrophe), so take
            # the horizontal extent from the mask instead of padding blindly, which
            # would pull in neighbouring text such as a speaker name.
            rows = block.mask[max(0, ln.y0 - block.y0) : ln.y1 - block.y0]
            cols = np.where(rows.max(axis=0) > 0)[0] if rows.size else np.array([], dtype=int)
            lx0, lx1 = ln.x0, ln.x1
            if len(cols):
                reach = ln.y1 - ln.y0
                near = cols[(cols + block.x0 >= ln.x0 - reach) & (cols + block.x0 <= ln.x1 + reach)]
                if len(near):
                    lx0, lx1 = min(lx0, int(near[0]) + block.x0), max(lx1, int(near[-1]) + block.x0 + 1)
            y0, y1 = max(0, ln.y0 - pad), min(fh, ln.y1 + pad)
            x0, x1 = max(0, lx0 - pad), min(fw, lx1 + pad)
            crop = frame_bgr[y0:y1, x0:x1]
            if crop.shape[0] < 2 or crop.shape[1] < 2:
                continue  # nothing to read (a line box at the very edge of the picture)
            scale = TARGET_LINE_H / max(1, crop.shape[0])
            interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
            colour = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=interp)
            text, score = self._read_line(colour)
            if score < 0.85:
                # Second opinion: a clean black-on-white render of the stroke mask.
                m = block.mask[y0 - block.y0 : y1 - block.y0, x0 - block.x0 : x1 - block.x0]
                # (a mask a pixel or two wide scales to nothing, and resizing to nothing raises)
                if m.size and m.shape[0] * scale >= 1 and m.shape[1] * scale >= 1:
                    clean = cv2.resize(255 - m, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                    t2, s2 = self._read_line(cv2.cvtColor(clean, cv2.COLOR_GRAY2BGR))
                    if s2 > score:
                        text, score = t2, s2
            lines.append(text)
            scores.append(score)
        lines, scores = keep_lines(lines, scores)
        joined = join_lines(lines)
        speaker, body = split_speaker(joined)
        return OcrResult(body, lines, min(scores) if scores else 0.0, speaker)
