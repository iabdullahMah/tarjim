"""The live loop: frame -> detect -> OCR -> translate -> render -> TV.

Reading and translating start the moment a line is first seen, on a worker
thread, while the frame loop keeps watching it. By the time the line has been
stable for the settle time the Arabic is usually ready, so the settle wait
costs nothing extra.
"""

from __future__ import annotations

import dataclasses
import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import paths
from . import protocol as P
from .translate import CachedTranslator, TranslateError, normalize_english
from .tvlink import TvLink, TvLinkError, now_ms
from .vision import render
from .vision.detect import Block, DetectParams, detect, mask_iou
from .vision.ocr import MIN_SCORE, OcrEngine
from .vision.tracker import Event, SubtitleTracker, TrackParams

LAYER_TTL_MS = 700
FILL_REFRESH_MS = 85  # about 12 fill refreshes per second
HOLD_KEEP_MS = 200
SAME_BLOCK_IOU = 0.80  # an early job belongs to the line that later became stable
# A plate at least this dark hides white English letters on its own (under 85% black they are
# within a few levels of the ground): it needs no fill made from the picture underneath.
PLATE_HIDES = 0.85
# The Arabic comes up and goes away softly instead of popping (the TV app animates it).
# Short: the line is still readable at once, and nothing stays on screen after the English.
FADE_IN_MS = 90
FADE_OUT_MS = 140
SIZE_LOCK_AFTER = 5  # lines measured before the Arabic font size is fixed for good
# An early reading stands for the confirmed line only when it saw the whole of it: every text
# line reaching as far left and right, within this share of a line's height.
COVER_SLACK = 0.5


def covers(early: Block, final: Block) -> bool:
    """True when `early` (the block a reading started from) holds all of `final` (the line once
    it stopped changing). A line still being written matches the finished one closely enough
    to pass for it, and its reading then lacks the last word."""
    if len(early.lines) != len(final.lines):
        return len(early.lines) > len(final.lines)
    for a, b in zip(sorted(early.lines, key=lambda l: l.y0), sorted(final.lines, key=lambda l: l.y0)):
        slack = COVER_SLACK * (b.y1 - b.y0)
        if a.x0 > b.x0 + slack or a.x1 < b.x1 - slack:
            return False
    return True


def reading_time_ms(arabic: str) -> float:
    """How long a line must stay up to be read: about 15 characters per second, 1.6 to 6 s."""
    return float(min(6000.0, max(1600.0, 900.0 + 65.0 * len(arabic))))


@dataclass
class StageTimes:
    detect: list[float] = field(default_factory=list)
    ocr: list[float] = field(default_factory=list)
    mt: list[float] = field(default_factory=list)
    mt_cached: list[float] = field(default_factory=list)
    render: list[float] = field(default_factory=list)
    tv: list[float] = field(default_factory=list)  # send -> drawn, from the TV's ack
    total: list[float] = field(default_factory=list)  # stable -> drawn on the TV
    first: list[float] = field(default_factory=list)  # first seen in the copy -> drawn on the TV
    refresh: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, dict[str, float]]:
        out = {}
        for name, values in self.__dict__.items():
            if values:
                a = np.array(values)
                out[name] = {"n": len(values), "p50": float(np.percentile(a, 50)), "p90": float(np.percentile(a, 90))}
        return out


@dataclass
class _Job:
    """One line being read and translated on the worker thread."""

    block: Block
    frame: np.ndarray
    t_first: float  # laptop ms when the line was first seen
    t_capture: float
    want_show: bool = False  # the tracker confirmed the line is stable
    t_stable: float = 0.0
    done: bool = False
    ok: bool = False
    cancelled: bool = False
    shown: bool = False
    english: str = ""
    arabic: str = ""
    speaker: str | None = None
    score: float = 0.0
    hit: bool = False
    ocr_ms: float = 0.0
    mt_ms: float = 0.0
    render_ms: float = 0.0
    reason: str = ""  # why the line was not shown (for the session log)
    source: str = ""  # where the Arabic came from (CachedTranslator.last_source)
    style: render.Style | None = None  # the look it was drawn in
    text_layer: render.RgbaLayer | None = None


class Pipeline:
    def __init__(
        self,
        link: TvLink,
        translator: CachedTranslator,
        ocr: OcrEngine | None = None,
        *,
        window: str = "A",
        style: render.Style | None = None,
        detect_params: DetectParams | None = None,
        track_params: TrackParams | None = None,
        log_path: Path | None = None,
        show_fill: bool = True,
        read_scale: float = 1.0,
        background: str | None = None,
        plate_opacity: float = 0.62,
    ) -> None:
        self.link = link
        self.translator = translator
        self.ocr = ocr or OcrEngine()
        self.window = window
        self.style = style or render.Style()
        self.detect_params = detect_params or DetectParams()
        self.tracker = SubtitleTracker(track_params)
        self.times = StageTimes()
        # "cover": hide the English strokes with scene colour (the original look);
        # "plate": a dark box behind the Arabic, which also hides the English;
        # "none": Arabic only, the English stays visible underneath.
        self.background = background or ("cover" if show_fill else "none")
        self.plate_opacity = plate_opacity
        self.read_scale = read_scale  # above 1: Arabic stays longer; below 1: shorter
        self.enabled = True
        self.lines_shown = 0
        self.rejected = 0
        self.held = 0
        self.early_ready = 0  # lines whose Arabic was ready before the settle time ended
        self.on_line = None  # optional callback(english, arabic) when a line goes up
        # Optional screen text (screen_text.py: a game's menus and messages) on its own thread.
        # It is handed the newest picture and nothing else: the subtitles never wait for it.
        self.screen = None
        self.on_size_locked = None  # optional callback(size) once the font size is fixed
        # Optional stronger translator (gemini.py) on its own thread. It is told each line that
        # goes up; when its Arabic arrives while the line is still up, the line is rewritten.
        self.refiner = None
        self.on_better = None  # optional callback(english, arabic) when a line was rewritten
        self.bettered = 0  # lines rewritten in place
        self._shown_job: _Job | None = None
        self._plate_up = False
        self._sizes: list[int] = []
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tarjim-work")
        self._job: _Job | None = None
        self._latest: np.ndarray | None = None
        self._text_up = False
        self._last_fill = 0.0
        self._shown_at = 0.0  # when the current Arabic line went up (laptop ms)
        self._read_ms = 0.0
        self._holding = False  # English gone, Arabic kept up so it can be read
        self._text_layer: render.RgbaLayer | None = None
        self._frame_shape = (1080, 1920)
        self._last_keep = 0.0
        self._log = log_path.open("a", encoding="utf-8") if log_path else None
        self._pending: dict[int, dict] = {}

    # -- geometry -----------------------------------------------------------

    def _to_overlay(self, layer: render.RgbaLayer, frame_shape) -> P.Layer:
        ow, oh = self.link.caps.get("w", 1920), self.link.caps.get("h", 1080)
        fh, fw = frame_shape[:2]
        bgra = layer.bgra
        x, y = layer.x, layer.y
        if (fw, fh) != (ow, oh):  # render size differs from the overlay plane: scale once, here
            sx, sy = ow / fw, oh / fh
            x0, y0 = int(round(x * sx)), int(round(y * sy))
            x1, y1 = int(round((x + layer.w) * sx)), int(round((y + layer.h) * sy))
            bgra = cv2.resize(bgra, (max(1, x1 - x0), max(1, y1 - y0)), interpolation=cv2.INTER_AREA)
            x, y = x0, y0
        h = min(bgra.shape[0], P.MAX_LAYER_H)
        w = min(bgra.shape[1], P.MAX_LAYER_W)
        bgra = np.ascontiguousarray(bgra[:h, :w])
        ok, buf = cv2.imencode(".png", bgra, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if not ok:
            raise RuntimeError("PNG encoding failed")
        return P.Layer(id=layer.id, png=buf.tobytes(), x=int(x), y=int(y), w=w, h=h, z=layer.z, ttl_ms=LAYER_TTL_MS)

    # -- per frame ------------------------------------------------------------

    def process(self, frame: np.ndarray, t_capture: float) -> Event:
        """Handle one frame. `t_capture` is time.monotonic() seconds."""
        t0 = time.perf_counter()
        block = detect(frame, self.detect_params)
        self.times.detect.append((time.perf_counter() - t0) * 1000.0)
        with self._lock:
            self._latest = frame
            self._frame_shape = frame.shape
            event = self.tracker.update(block, t_capture * 1000.0)
            try:
                if event is Event.NEW:
                    self._on_new(frame, self.tracker.shown, t_capture)
                elif event is Event.REFRESH:
                    self._on_refresh(frame)
                elif event is Event.GONE:
                    self._on_gone()
                elif block is not None and self.tracker.shown is None:
                    self._maybe_start_early(frame, block, t_capture)
                if self._holding:
                    self._hold_tick()
            except TvLinkError:
                self._text_up = False  # the TV clears by itself when the link drops
                self._holding = False
            self._collect_acks()
        if self.screen is not None:
            self.screen.feed(frame)  # it searches on its own thread; nothing waits for it here
            self.screen.tick()
        return event

    @property
    def busy(self) -> bool:
        """True while a subtitle line is being read and translated (screen text waits for it)."""
        job = self._job
        return job is not None and not job.done

    # -- the worker -------------------------------------------------------------

    def _start_job(self, frame: np.ndarray, block: Block, t_capture: float, want_show: bool) -> None:
        if self._job is not None and not self._job.done:
            self._job.cancelled = True  # it finishes on its own; its result is ignored
        job = _Job(block, frame, now_ms(), t_capture, want_show=want_show, t_stable=now_ms() if want_show else 0.0)
        self._job = job
        self._pool.submit(self._work, job)

    def _maybe_start_early(self, frame: np.ndarray, block: Block, t_capture: float) -> None:
        """A line was just seen but is not confirmed stable yet: start reading it now."""
        if not self.enabled or self.tracker.is_rejected(block):
            return
        job = self._job
        # A reading that already went up belongs to the line's earlier appearance: the same
        # line back on screen (said twice, or lost for a moment behind a flash) is read again.
        if (job is not None and not job.cancelled and not job.shown
                and mask_iou(job.block, block) >= SAME_BLOCK_IOU and covers(job.block, block)):
            return  # already working on this line
        if job is not None and not job.done:
            return  # busy with another text (typewriter text grows each frame): wait for it
        self._start_job(frame, block, t_capture, want_show=False)

    def _work(self, job: _Job) -> None:
        try:
            t = time.perf_counter()
            read = self.ocr.read(job.frame, job.block)
            job.ocr_ms = (time.perf_counter() - t) * 1000.0
            job.english, job.speaker, job.score = read.text, read.speaker, read.score
            if read.ok and not job.cancelled:
                job.arabic, job.hit, job.mt_ms = self.translator.translate(read.text)
                job.source = getattr(self.translator, "last_source", "")
                t = time.perf_counter()
                job.style = self._style_for(job.block)
                job.text_layer = render.make_text(job.frame, job.block, job.arabic, job.style)
                job.render_ms = (time.perf_counter() - t) * 1000.0
                job.ok = True
            elif not read.ok:
                job.reason = "low_score" if read.score < MIN_SCORE else "not_speech"
        except TranslateError as exc:
            job.ok = False
            job.reason = f"translate: {str(exc)[:200]}"
        except Exception as exc:  # a worker crash must not kill the frame loop; the line is skipped
            job.ok = False
            job.reason = f"error: {type(exc).__name__}: {str(exc)[:200]} @ {_where(exc)}"
        with self._lock:
            job.done = True
            self.times.ocr.append(job.ocr_ms)
            if job.ok:
                (self.times.mt_cached if job.hit else self.times.mt).append(job.mt_ms)
            if job.want_show and not job.cancelled:
                try:
                    self._finish(job)
                except TvLinkError:
                    self._text_up = False

    def _style_for(self, block: Block) -> render.Style:
        """One font size for every line. The game's subtitle size does not change,
        but measuring it line by line wobbles by a few pixels, which looked like
        the Arabic growing and shrinking. Measure the first lines, take their
        median, then keep it."""
        if self.style.fixed_size:
            return self.style
        self._sizes.append(render.measured_size(block, self.style))
        size = int(statistics.median(self._sizes))
        if len(self._sizes) >= SIZE_LOCK_AFTER:
            self.style = dataclasses.replace(self.style, fixed_size=size)
            if self.on_size_locked is not None:
                self.on_size_locked(size)
            return self.style
        return dataclasses.replace(self.style, fixed_size=size)

    def _on_new(self, frame: np.ndarray, block: Block, t_capture: float) -> None:
        job = self._job
        if (job is not None and not job.cancelled and not job.shown
                and mask_iou(job.block, block) >= SAME_BLOCK_IOU and covers(job.block, block)):
            job.want_show = True
            job.t_stable = now_ms()
            if job.done:
                self.early_ready += 1
                self._finish(job)
            return
        self._start_job(frame, block, t_capture, want_show=True)

    def _finish(self, job: _Job) -> None:
        """Called with the lock held, once the job is done and the line is confirmed."""
        if job.shown:
            return
        job.shown = True
        if not job.ok:
            self.rejected += 1
            # Why a line seen on screen got no Arabic: without this a session that shows
            # nothing leaves nothing to look at afterwards.
            self._write_log({
                "kind": "rejected", "reason": job.reason or "unknown", "t": job.t_first,
                "en": job.english, "ocr_score": round(job.score, 3), "ocr_ms": round(job.ocr_ms, 1),
            })
            if self.tracker.shown is not None and mask_iou(self.tracker.shown, job.block) >= SAME_BLOCK_IOU:
                self.tracker.reject()
            return
        if not self.enabled or job.text_layer is None:
            return
        still_there = self.tracker.shown is not None and mask_iou(self.tracker.shown, job.block) >= SAME_BLOCK_IOU
        frame = self._latest if self._latest is not None else job.frame
        t = time.perf_counter()
        layers = []
        mode = self.background
        b = self.tracker.shown
        covers = self._uses_fill()
        if still_there and covers:
            # The fill comes from the newest frame, so it matches the picture as closely as possible.
            # Plate mode needs it too: a translucent plate alone leaves the English readable through it.
            layers.append(self._to_overlay(render.make_fill(frame, b), frame.shape))
        if mode == "plate" or (not still_there and mode == "cover"):
            # A plate hides the English (plate mode) or keeps a line readable whose English already left.
            cover = (b.x0, b.y0, b.x1, b.y1) if still_there else None
            plate = render.make_plate(job.text_layer, frame.shape, self.plate_opacity, cover=cover)
            layers.append(self._to_overlay(plate, frame.shape))
        has_plate = any(layer.id == "plate" for layer in layers)
        layers.append(self._to_overlay(job.text_layer, frame.shape))
        job.render_ms += (time.perf_counter() - t) * 1000.0
        self.times.render.append(job.render_ms)
        stale = [name for name, kept in (("fill", still_there and covers), ("plate", has_plate)) if not kept]
        if stale and self._text_up:
            self.link.clear(stale)
        t_sent = now_ms()
        if not self._text_up:  # coming up from nothing fades in; a line replacing another swaps at once
            layers = [dataclasses.replace(layer, fade_ms=FADE_IN_MS) for layer in layers]
        seq = self.link.show(self.window, layers)
        self._text_up = True
        self._holding = not still_there
        if self._holding:
            self.held += 1
            self._last_keep = t_sent
        self._last_fill = t_sent
        self._shown_at = t_sent
        self._read_ms = reading_time_ms(job.arabic) * self.read_scale
        self._text_layer = job.text_layer
        self._shown_job = job
        self._plate_up = has_plate
        self.lines_shown += 1
        self._pending[seq] = {
            "t_frame": job.t_capture * 1000.0, "t_first": job.t_first, "t_stable": job.t_stable, "t_sent": t_sent,
            "ocr_ms": round(job.ocr_ms, 1), "mt_ms": round(job.mt_ms, 1), "render_ms": round(job.render_ms, 1),
            "en": job.english, "ar": job.arabic, "speaker": job.speaker, "ocr_score": round(job.score, 3),
            "engine": self.translator.engine.name,
            "cache_hit": job.hit, "mode": mode if still_there else f"{mode}-late", "source": job.source,
        }
        if self.on_line is not None:
            self.on_line(job.english, job.arabic)
        if self.refiner is not None:
            # the user's own wording and an answer stored before need no asking again
            self.refiner.seen(job.english, job.speaker, wanted=job.source not in ("fix", "better"))

    def better_arrived(self, english: str, arabic: str) -> None:
        """The stronger translator's Arabic for a line (called from its thread). It is stored
        already; when that line is still the one on the TV, its Arabic is rewritten in place."""
        with self._lock:
            job = self._shown_job
            if (job is None or not self._text_up or not self.enabled or job.text_layer is None
                    or normalize_english(job.english) != english or arabic == job.arabic):
                return
            try:
                text = render.make_text(job.frame, job.block, arabic, job.style or self.style)
                layers = []
                if self._plate_up:
                    b = None if self._holding else self.tracker.shown
                    cover = (b.x0, b.y0, b.x1, b.y1) if b is not None else None
                    layers.append(self._to_overlay(
                        render.make_plate(text, self._frame_shape, self.plate_opacity, cover=cover), self._frame_shape))
                layers.append(self._to_overlay(text, self._frame_shape))
                self.link.show(self.window, layers)  # same layer names: it swaps at once, no fade
            except TvLinkError:
                return
            now = now_ms()
            after = now - self._shown_at
            job.arabic, job.text_layer = arabic, text
            self._text_layer = text
            # the new wording gets time to be read too
            self._read_ms = max(self._read_ms, after + 0.7 * reading_time_ms(arabic) * self.read_scale)
            self.bettered += 1
            self._write_log({"kind": "better", "en": job.english, "ar": arabic, "after_ms": round(after, 1)})
        if self.on_better is not None:
            self.on_better(job.english, arabic)

    # -- while a line is up -------------------------------------------------------

    def _uses_fill(self) -> bool:
        """Cover mode always; plate mode only while the plate is too light to hide the English."""
        return self.background == "cover" or (self.background == "plate" and self.plate_opacity < PLATE_HIDES)

    def _on_refresh(self, frame: np.ndarray) -> None:
        if not self._text_up or not self.enabled or self._holding:
            return
        now = now_ms()
        if now - self._last_fill < FILL_REFRESH_MS:
            return
        t = time.perf_counter()
        if self._uses_fill():
            fill = self._to_overlay(render.make_fill(frame, self.tracker.shown), frame.shape)
            self.link.show(self.window, [fill])
        self.link.keep(["text", "plate"])
        self._last_fill = now
        self.times.refresh.append((time.perf_counter() - t) * 1000.0)

    def _on_gone(self) -> None:
        """The English left. Keep the Arabic until it has had its reading time."""
        if not self._text_up or self._holding:
            return
        remaining = self._shown_at + self._read_ms - now_ms()
        if remaining <= 150 or self._text_layer is None or not self.enabled:
            self._clear()
            return
        if self.background == "cover":
            # No English underneath any more: swap the stroke fill for a dark plate,
            # which also hides the next English line until its own Arabic is ready.
            plate = render.make_plate(self._text_layer, self._frame_shape, self.plate_opacity)
            self.link.show(self.window, [self._to_overlay(plate, self._frame_shape)])
            self._plate_up = True
        if self._uses_fill():
            self.link.clear(["fill"])  # nothing left to cover
        self.link.keep(["text", "plate"])
        self._holding = True
        self._last_keep = now_ms()
        self.held += 1

    def _hold_tick(self) -> None:
        now = now_ms()
        if now >= self._shown_at + self._read_ms:
            self._clear()
        elif now - self._last_keep >= HOLD_KEEP_MS:
            self.link.keep(["text", "plate"])
            self._last_keep = now

    def _clear(self) -> None:
        if self._text_up:
            # With screen text beside it, only the subtitles' own layers go.
            self.link.clear(None if self.screen is None else ["fill", "text", "plate"], fade_ms=FADE_OUT_MS)
        self._text_up = False
        self._holding = False
        self._plate_up = False
        self._shown_job = None

    def log_record(self, rec: dict) -> None:
        """A line of the session log from another thread (screen text)."""
        with self._lock:
            self._write_log(rec)

    def set_enabled(self, on: bool) -> None:
        if self.screen is not None:
            self.screen.set_enabled(on and self.screen.wanted)
        with self._lock:
            self.enabled = on
            if not on:
                try:
                    self._clear()
                except TvLinkError:
                    pass
                if self._job is not None:
                    self._job.cancelled = True
                self.tracker.reset()

    def wait_idle(self, timeout: float = 5.0) -> None:
        """Block until the worker has finished its current line (for previews and tests)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._job is None or self._job.done:
                    return
            time.sleep(0.005)

    # -- log ----------------------------------------------------------------------

    def _collect_acks(self) -> None:
        for seq in list(self._pending):
            ack = self.link.wait_ack(seq, timeout=0.0)
            if ack is None:
                if now_ms() - self._pending[seq]["t_sent"] > 3000:
                    self._write_log(self._pending.pop(seq))
                continue
            rec = self._pending.pop(seq)
            if ack.get("t_drawn") is not None:
                drawn = self.link.tv_time_to_laptop(float(ack["t_drawn"]))
                if drawn is not None:
                    rec["t_tv_drawn"] = drawn
                    rec["tv_ms"] = round(drawn - rec["t_sent"], 1)
                    rec["total_ms"] = round(drawn - rec["t_stable"], 1)
                    rec["first_ms"] = round(drawn - rec["t_first"], 1)
                    self.times.tv.append(rec["tv_ms"])
                    self.times.total.append(rec["total_ms"])
                    self.times.first.append(rec["first_ms"])
            self._write_log(rec)

    def _write_log(self, rec: dict) -> None:
        if self._log:
            self._log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._log.flush()

    def close(self) -> None:
        if self.refiner is not None:
            self.refiner.close()
        if self.screen is not None:
            try:
                self.screen.close()
            except TvLinkError:
                pass
        self.wait_idle(2.0)
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            try:
                self._clear()
            except TvLinkError:
                pass
            deadline = time.monotonic() + 0.5
            while self._pending and time.monotonic() < deadline:
                self._collect_acks()
                time.sleep(0.02)
            for rec in self._pending.values():
                self._write_log(rec)
            self._pending.clear()
            if self._log:
                self._log.close()
                self._log = None


def _where(exc: BaseException) -> str:
    """File and line of the deepest frame of an error, for the session log."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    return f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}" if tb is not None else "?"


def new_log_path() -> Path:
    d = paths.REPO / "out" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"session-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
