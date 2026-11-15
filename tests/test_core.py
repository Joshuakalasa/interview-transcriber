"""Core tests (no microphone or whisper needed). Run:  .venv\\Scripts\\python -m unittest -v"""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from app import editing, exporter
from app.audio_utils import TARGET_SR, is_whisper_ready
from app.recorder import PCM_INFO_NAME, PCM_NAME, finalize_recording, pending_recording
from app.storage import Segment, create_interview
from app.transcriber import clean_text, parse_whisper_json


def segs():
    return [Segment(0, 2000, "Hello there.", "Interviewer"),
            Segment(2000, 5000, "How are you today?", "Interviewer"),
            Segment(6000, 9000, "I am fine, thank you.", "Respondent"),
            Segment(9500, 12000, "The weather in Lusaka is nice.", "Respondent")]


class EditingTests(unittest.TestCase):
    def test_merge(self):
        out = editing.merge(segs(), [1, 0])
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0].text, "Hello there. How are you today?")
        self.assertEqual((out[0].start_ms, out[0].end_ms), (0, 5000))

    def test_merge_does_not_modify_input(self):
        original = segs()
        editing.merge(original, [0, 1])
        self.assertEqual(len(original), 4)

    def test_split_proportional_time(self):
        out = editing.split([Segment(0, 1000, "aaaa bbbb")], 0, 4)
        self.assertEqual([s.text for s in out], ["aaaa", "bbbb"])
        self.assertEqual(out[0].end_ms, out[1].start_ms)
        self.assertTrue(400 <= out[0].end_ms <= 600)

    def test_split_at_edge_is_noop(self):
        self.assertEqual(len(editing.split(segs(), 0, 0)), 4)

    def test_speakers(self):
        out = editing.assign_speaker(segs(), [0], "Moderator")
        self.assertEqual(out[0].speaker, "Moderator")
        out = editing.rename_speaker(segs(), "Respondent", "Mrs Banda")
        self.assertEqual([s.speaker for s in out][2:], ["Mrs Banda", "Mrs Banda"])

    def test_delete(self):
        self.assertEqual(len(editing.delete(segs(), [0, 3])), 2)

    def test_find_wraps(self):
        self.assertEqual(editing.find_next(segs(), "lusaka", 3), 3)
        self.assertEqual(editing.find_next(segs(), "hello", 2), 0)
        self.assertEqual(editing.find_next(segs(), "Lusaka", 0, match_case=False), 3)
        self.assertEqual(editing.find_next(segs(), "zzz", 0), -1)

    def test_replace_all_literal(self):
        out, n = editing.replace_all(segs(), "a.", "X")   # '.' must not be a regex wildcard
        self.assertEqual(n, 0)
        out, n = editing.replace_all(segs(), "lusaka", "Ndola")
        self.assertEqual(n, 1)
        self.assertIn("Ndola", out[3].text)

    def test_paragraphs_by_speaker(self):
        out = editing.paragraphs(segs())
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1].text, "I am fine, thank you. The weather in Lusaka is nice.")

    def test_paragraphs_by_pause_without_speakers(self):
        s = [Segment(0, 1000, "A."), Segment(1500, 2000, "B."), Segment(9000, 9500, "C.")]
        self.assertEqual([p.text for p in editing.paragraphs(s)], ["A. B.", "C."])


def _tok(text, a, b):
    return {"text": text, "offsets": {"from": a, "to": b}}


class WhisperParseTests(unittest.TestCase):
    def _write(self, items):
        p = Path(tempfile.mkdtemp()) / "w.json"
        p.write_text(json.dumps({"transcription": items}), encoding="utf-8")
        return p

    def test_sentence_split_and_decimal(self):
        toks = [_tok("[_BEG_]", 0, 0), _tok(" It", 0, 100), _tok(" costs", 100, 300), _tok(" 3", 300, 400),
                _tok(".", 400, 400), _tok("5", 400, 500), _tok(" kwacha", 500, 900), _tok(" per", 900, 1000),
                _tok(" day", 1000, 1200), _tok(".", 1200, 1200), _tok(" Then", 1500, 1700),
                _tok(" we", 1700, 1800), _tok(" went", 1800, 2000), _tok(" home", 2000, 2200),
                _tok(" together", 2200, 2600), _tok(" again", 2600, 2900), _tok(".", 2900, 2900)]
        out = parse_whisper_json(self._write([{"offsets": {"from": 0, "to": 3000}, "text": "", "tokens": toks}]))
        self.assertEqual([s.text for s in out],
                         ["It costs 3.5 kwacha per day.", "Then we went home together again."])
        self.assertEqual(out[1].start_ms, 1500)

    def test_sentence_joined_across_segments(self):
        a = [_tok(" I", 0, 100), _tok(" worked", 100, 400), _tok(" for", 400, 500), _tok(" five", 500, 800)]
        b = [_tok(" years", 0, 300), _tok(" as", 300, 400), _tok(" an", 400, 500), _tok(" analyst", 500, 900), _tok(".", 900, 900)]
        items = [{"offsets": {"from": 0, "to": 800}, "tokens": a},
                 {"offsets": {"from": 5000, "to": 5900}, "tokens": b}]
        out = parse_whisper_json(self._write(items), vad=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].text, "I worked for five years as an analyst.")
        self.assertEqual(out[0].end_ms, 5900)   # VAD remap puts the end on the real timeline

    def test_clean_text_quotes(self):
        self.assertEqual(clean_text(' "Hello there."'), "Hello there.")
        self.assertEqual(clean_text('"Good morning'), "Good morning")
        self.assertEqual(clean_text('He said "yes" twice'), 'He said "yes" twice')


class RecordingRecoveryTests(unittest.TestCase):
    def test_raw_pcm_recovery_48k(self):
        folder = Path(tempfile.mkdtemp())
        sr = 48000
        tone = (np.sin(np.arange(sr * 2) * 2 * np.pi * 440 / sr) * 8000).astype("<i2")
        (folder / PCM_NAME).write_bytes(tone.tobytes())
        (folder / PCM_INFO_NAME).write_text(json.dumps({"samplerate": sr}))
        self.assertIsNotNone(pending_recording(folder))
        dst = folder / "audio.wav"
        finalize_recording(folder, dst)
        self.assertTrue(is_whisper_ready(dst))
        self.assertAlmostEqual(sf.info(str(dst)).duration, 2.0, places=1)
        self.assertIsNone(pending_recording(folder))
        self.assertFalse((folder / PCM_NAME).exists())


class ExportTests(unittest.TestCase):
    def test_exports(self):
        iv = create_interview(Path(tempfile.mkdtemp()), "Test interview", "Joshua")
        iv.meta["interviewee"] = "Mrs Banda"
        for layout in ("lines", "paragraphs"):
            txt = iv.folder / f"{layout}.txt"
            exporter.export_txt(iv, segs(), txt, layout=layout)
            body = txt.read_text(encoding="utf-8")
            self.assertIn("Mrs Banda", body)
            self.assertIn("Respondent: I am fine", body)
            exporter.export_docx(iv, segs(), iv.folder / f"{layout}.docx", layout=layout)
        self.assertEqual((iv.folder / "paragraphs.txt").read_text(encoding="utf-8").count("Respondent:"), 1)
        exporter.export_srt(segs(), iv.folder / "t.srt")
        self.assertIn("00:00:06,000 --> 00:00:09,000",
                      (iv.folder / "t.srt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
