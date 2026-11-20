"""Fetch the translation model into models/ (it is not in git: about 460 MB).

    uv run --with huggingface_hub python launcher/fetch_model.py          download it
    uv run python launcher/fetch_model.py --check                          only prove it translates

The same files docs/DECISIONS.md names: a ready-made CTranslate2 conversion of
Helsinki-NLP/opus-mt-tc-big-en-ar, and the two tokenizer files from the official repository.
After downloading, the model is loaded the way the program loads it and asked for one line:
a model that does not answer in Arabic stops the build here instead of reaching a user.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "models" / "opus-mt-tc-big-en-ar-ct2"
CONVERTED = "ai-path/opus-mt-tc-big-en-ar-ct2"
OFFICIAL = "Helsinki-NLP/opus-mt-tc-big-en-ar"
TOKENIZERS = ("source.spm", "target.spm")


def download() -> None:
    from huggingface_hub import hf_hub_download, snapshot_download

    TARGET.mkdir(parents=True, exist_ok=True)
    if not (TARGET / "model.bin").exists():
        snapshot_download(CONVERTED, local_dir=str(TARGET))
    for name in TOKENIZERS:
        if not (TARGET / name).exists():
            hf_hub_download(OFFICIAL, name, local_dir=str(TARGET))


def check() -> str:
    sys.path.insert(0, str(REPO / "laptop"))
    from tarjim.translate import OpusTranslator

    missing = [n for n in ("model.bin", *TOKENIZERS) if not (TARGET / n).exists()]
    if missing:
        raise SystemExit(f"missing in {TARGET}: {', '.join(missing)}")
    arabic = OpusTranslator(TARGET).translate("We should leave before the storm gets here.")
    letters = sum(1 for ch in arabic if "؀" <= ch <= "ۿ")
    if letters < 8:
        raise SystemExit(f"the model did not answer in Arabic: {arabic!r}")
    return arabic


if __name__ == "__main__":
    if "--check" not in sys.argv:
        download()
    size = sum(p.stat().st_size for p in TARGET.rglob("*") if p.is_file()) / 1e6
    print(f"{TARGET}  {size:.0f} MB")
    sys.stdout.reconfigure(encoding="utf-8")
    print(check())
