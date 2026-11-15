"""Pure transcript-editing operations (no UI), so they are easy to test.

Every function returns a NEW list and leaves the input untouched, which makes undo trivial.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .storage import Segment

DEFAULT_SPEAKERS = ["Interviewer", "Respondent"]


def _copy(segments: list[Segment]) -> list[Segment]:
    return deepcopy(segments)


def assign_speaker(segments: list[Segment], rows: list[int], name: str) -> list[Segment]:
    out = _copy(segments)
    for r in rows:
        if 0 <= r < len(out):
            out[r].speaker = name
    return out


def rename_speaker(segments: list[Segment], old: str, new: str) -> list[Segment]:
    out = _copy(segments)
    for s in out:
        if s.speaker == old:
            s.speaker = new
    return out


def merge(segments: list[Segment], rows: list[int]) -> list[Segment]:
    """Merge the given rows (must be at least 2) into the first one, in time order."""
    rows = sorted(set(r for r in rows if 0 <= r < len(segments)))
    if len(rows) < 2:
        return _copy(segments)
    out = _copy(segments)
    first = out[rows[0]]
    parts = [out[r] for r in rows]
    first.text = " ".join(p.text.strip() for p in parts if p.text.strip())
    first.start_ms = min(p.start_ms for p in parts)
    first.end_ms = max(p.end_ms for p in parts)
    for r in reversed(rows[1:]):
        del out[r]
    return out


def split(segments: list[Segment], row: int, char_pos: int) -> list[Segment]:
    """Split a row at a character position; time is divided in proportion to text length."""
    if not 0 <= row < len(segments):
        return _copy(segments)
    out = _copy(segments)
    seg = out[row]
    left, right = seg.text[:char_pos].strip(), seg.text[char_pos:].strip()
    if not left or not right:
        return out
    span = max(0, seg.end_ms - seg.start_ms)
    cut = seg.start_ms + int(span * len(left) / max(1, len(left) + len(right)))
    out[row] = Segment(seg.start_ms, cut, left, seg.speaker)
    out.insert(row + 1, Segment(cut, seg.end_ms, right, seg.speaker))
    return out


def delete(segments: list[Segment], rows: list[int]) -> list[Segment]:
    drop = set(rows)
    return [deepcopy(s) for i, s in enumerate(segments) if i not in drop]


def _pattern(query: str, match_case: bool) -> re.Pattern:
    return re.compile(re.escape(query), 0 if match_case else re.IGNORECASE)


def find_next(segments: list[Segment], query: str, after_row: int, match_case: bool = False) -> int:
    """Row index of the next segment containing query (wraps around); -1 if none."""
    if not query or not segments:
        return -1
    pat = _pattern(query, match_case)
    n = len(segments)
    for i in range(1, n + 1):
        r = (after_row + i) % n
        if pat.search(segments[r].text):
            return r
    return -1


def replace_in_row(segments: list[Segment], row: int, query: str, repl: str,
                   match_case: bool = False) -> list[Segment]:
    out = _copy(segments)
    if query and 0 <= row < len(out):
        out[row].text = _pattern(query, match_case).sub(lambda _m: repl, out[row].text)
    return out


def replace_all(segments: list[Segment], query: str, repl: str,
                match_case: bool = False) -> tuple[list[Segment], int]:
    out = _copy(segments)
    if not query:
        return out, 0
    pat = _pattern(query, match_case)
    total = 0
    for s in out:
        s.text, n = pat.subn(lambda _m: repl, s.text)
        total += n
    return out, total


def paragraphs(segments: list[Segment], gap_ms: int = 2500, max_chars: int = 1200) -> list[Segment]:
    """Join consecutive lines into readable paragraphs.

    Same speaker -> same paragraph (speaker change starts a new one).
    No speakers  -> a pause longer than gap_ms starts a new paragraph.
    """
    out: list[Segment] = []
    for s in segments:
        if out:
            last = out[-1]
            same_speaker = s.speaker == last.speaker
            close = bool(s.speaker) or (s.start_ms - last.end_ms) < gap_ms
            if same_speaker and close and len(last.text) + len(s.text) < max_chars:
                last.text = f"{last.text} {s.text}".strip()
                last.end_ms = max(last.end_ms, s.end_ms)
                continue
        out.append(deepcopy(s))
    return out
