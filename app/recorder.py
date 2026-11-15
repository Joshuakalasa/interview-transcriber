"""Microphone recording to WAV, with pause and a live level meter."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from .audio_utils import TARGET_SR


def list_input_devices() -> list[tuple[int, str]]:
    devices = []
    try:
        hostapis = sd.query_hostapis()
        for idx, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                api = hostapis[d["hostapi"]]["name"]
                # MME/WASAPI duplicates are common on Windows; show the API to tell apart.
                devices.append((idx, f"{d['name']}  [{api}]"))
    except Exception:
        pass
    return devices


class Recorder:
    """Records mono audio. Tries 16 kHz directly; falls back to the device's native rate.

    Audio is pushed from the audio callback into a queue and written by a separate
    thread, so slow disks never cause dropped audio.
    """

    def __init__(self) -> None:
        self.stream: sd.InputStream | None = None
        self.file: sf.SoundFile | None = None
        self.queue: queue.Queue = queue.Queue()
        self.writer: threading.Thread | None = None
        self.paused = False
        self.level = 0.0               # 0..1 RMS level for the meter
        self.samplerate = TARGET_SR
        self.frames_written = 0
        self.path: Path | None = None
        self.error: str | None = None
        self._started_at = 0.0

    @property
    def is_recording(self) -> bool:
        return self.stream is not None

    @property
    def elapsed_seconds(self) -> float:
        return self.frames_written / float(self.samplerate) if self.samplerate else 0.0

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata[:, 0].copy()
        self.level = float(min(1.0, np.sqrt(np.mean(mono.astype(np.float32) ** 2)) * 4))
        if not self.paused:
            self.queue.put(mono)

    def _write_loop(self) -> None:
        while True:
            chunk = self.queue.get()
            if chunk is None:
                break
            try:
                self.file.write(chunk)
                self.frames_written += len(chunk)
            except Exception as e:  # disk full etc.
                self.error = str(e)

    def start(self, path: Path, device: int | None = None) -> None:
        self.path = Path(path)
        self.paused = False
        self.frames_written = 0
        self.error = None
        try:
            sd.check_input_settings(device=device, samplerate=TARGET_SR, channels=1,
                                    dtype="float32")
            self.samplerate = TARGET_SR
        except Exception:
            info = sd.query_devices(device, "input")
            self.samplerate = int(info["default_samplerate"])

        self.file = sf.SoundFile(str(self.path), "w", samplerate=self.samplerate,
                                 channels=1, subtype="PCM_16")
        self.writer = threading.Thread(target=self._write_loop, daemon=True)
        self.writer.start()
        self.stream = sd.InputStream(device=device, samplerate=self.samplerate, channels=1,
                                     dtype="float32", callback=self._callback,
                                     blocksize=1024)
        self.stream.start()
        self._started_at = time.time()

    def pause(self, value: bool) -> None:
        self.paused = value

    def stop(self) -> Path | None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.writer is not None:
            self.queue.put(None)
            self.writer.join()
            self.writer = None
        if self.file is not None:
            self.file.close()
            self.file = None
        self.level = 0.0
        return self.path
