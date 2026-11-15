"""Application paths and per-user settings."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "InterviewTranscriber"


def app_root() -> Path:
    """Folder that contains tools/ and models/ (works when frozen by PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


DEFAULTS: dict = {
    "interviews_dir": str(Path.home() / "Documents" / "Interviews"),
    "whisper_cli": str(app_root() / "tools" / "whisper" / "whisper-cli.exe"),
    "ffmpeg": str(app_root() / "tools" / "ffmpeg" / "ffmpeg.exe"),
    "models_dir": str(app_root() / "models"),
    "model": "ggml-base.en-q5_1.bin",
    "language": "en",
    "threads": max(1, os.cpu_count() or 4),
    "input_device": None,          # None = system default microphone
    "vocabulary": "",              # names / terms to help spelling
    "organisation": "",            # printed on exports
    "default_interviewer": "",
    "use_vad": True,               # skip silence (needs the Silero VAD model)
    "vad_model": "ggml-silero-v5.1.2.bin",
    "split_sentences": True,       # one line per sentence instead of ~30 s blocks
    "playback_rate": 1.0,
}


class Settings:
    def __init__(self) -> None:
        self.path = user_data_dir() / "settings.json"
        self.data = dict(DEFAULTS)
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass  # corrupt settings -> fall back to defaults

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def vad_model_path(self) -> Path | None:
        """Path to the VAD model if silence skipping is on and the model exists."""
        if not self["use_vad"]:
            return None
        p = Path(self["models_dir"]) / self["vad_model"]
        return p if p.exists() else None

    def available_models(self) -> list[str]:
        folder = Path(self["models_dir"])
        if not folder.is_dir():
            return []
        return sorted(
            p.name for p in folder.glob("ggml-*.bin") if "silero" not in p.name.lower()
        )
