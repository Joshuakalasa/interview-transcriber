"""Interview project folders: metadata, audio and transcript on disk."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    text: str
    speaker: str = ""


META_FIELDS = ("title", "interviewee", "interviewer", "date", "location", "notes")


class Interview:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.meta: dict = {}
        self.load_meta()

    # ---- paths -------------------------------------------------------
    @property
    def meta_path(self) -> Path:
        return self.folder / "meta.json"

    @property
    def audio_path(self) -> Path:
        return self.folder / "audio.wav"

    @property
    def transcript_path(self) -> Path:
        return self.folder / "transcript.json"

    @property
    def whisper_out_base(self) -> Path:
        """whisper-cli appends .json to this."""
        return self.folder / "whisper_raw"

    # ---- metadata ----------------------------------------------------
    def load_meta(self) -> None:
        if self.meta_path.exists():
            try:
                self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.meta = {}
        self.meta.setdefault("title", self.folder.name)
        for f in META_FIELDS:
            self.meta.setdefault(f, "")
        self.meta.setdefault("status", "new")

    def save_meta(self) -> None:
        self.meta["updated"] = datetime.now().isoformat(timespec="seconds")
        self.meta_path.write_text(json.dumps(self.meta, indent=2), encoding="utf-8")

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.folder.name

    @property
    def has_audio(self) -> bool:
        return self.audio_path.exists()

    # ---- transcript --------------------------------------------------
    def load_segments(self) -> list[Segment]:
        if not self.transcript_path.exists():
            return []
        data = json.loads(self.transcript_path.read_text(encoding="utf-8"))
        return [Segment(**s) for s in data.get("segments", [])]

    def save_segments(self, segments: list[Segment]) -> None:
        payload = {"segments": [asdict(s) for s in segments]}
        tmp = self.transcript_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.transcript_path)  # atomic-ish save


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return s[:50] or "interview"


def create_interview(root: Path, title: str, interviewer: str = "") -> Interview:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    base = f"{date.today().isoformat()}_{_slug(title)}"
    folder = root / base
    n = 2
    while folder.exists():
        folder = root / f"{base}-{n}"
        n += 1
    folder.mkdir()
    iv = Interview(folder)
    iv.meta.update(
        title=title,
        interviewer=interviewer,
        date=date.today().isoformat(),
        created=datetime.now().isoformat(timespec="seconds"),
    )
    iv.save_meta()
    return iv


def list_interviews(root: Path) -> list[Interview]:
    root = Path(root)
    if not root.is_dir():
        return []
    items = [Interview(p) for p in root.iterdir() if (p / "meta.json").exists()]
    items.sort(key=lambda i: i.meta.get("created", ""), reverse=True)
    return items
