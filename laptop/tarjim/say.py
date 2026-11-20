"""Short Arabic console messages. Forces UTF-8 so Windows consoles print them."""

from __future__ import annotations

import sys


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


_hook = None


def set_hook(fn) -> None:
    """fn(kind, message) is called for every message; kind is step/ok/info/warn/fail."""
    global _hook
    _hook = fn


def _emit(kind: str, msg: str) -> None:
    if _hook is not None:
        try:
            _hook(kind, msg)
        except Exception:  # a broken listener must never break the work itself
            pass


def _print(text: str, error: bool = False) -> None:
    """Print for whoever watches a console. The desktop program has none, and on a Windows
    whose language is not Arabic its output stream cannot hold Arabic letters: the message
    already reached the interface through the hook, so a stream that refuses it is ignored
    (it used to stop the TV setup with "'charmap' codec can't encode characters")."""
    stream = sys.stderr if error else sys.stdout
    if stream is None:
        return
    try:
        print(text, file=stream, flush=True)
    except (UnicodeError, OSError, ValueError):
        pass


def step(msg: str) -> None:
    _emit("step", msg)
    _print(f"… {msg}")


def ok(msg: str) -> None:
    _emit("ok", msg)
    _print(f"✔ {msg}")


def info(msg: str) -> None:
    _emit("info", msg)
    _print(f"  {msg}")


def warn(msg: str) -> None:
    _emit("warn", msg)
    _print(f"⚠ {msg}")


def fail(msg: str) -> None:
    _emit("fail", msg)
    _print(f"✖ {msg}", error=True)
