"""Transcript editor panel: speaker tools, split/merge/delete, undo, find & replace, table."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

from . import editing
from .exporter import fmt_time
from .storage import Segment

COL_TIME, COL_SPEAKER, COL_TEXT = 0, 1, 2
UNDO_LIMIT = 100
PLAYING_BG = QColor(255, 193, 7, 70)   # soft amber, readable in light and dark themes


# --------------------------------------------------------------------------- delegates
class SpeakerDelegate(QStyledItemDelegate):
    """Editable drop-down of known speakers."""

    def __init__(self, speakers_fn, parent=None):
        super().__init__(parent)
        self.speakers_fn = speakers_fn

    def createEditor(self, parent, option, index):
        box = QComboBox(parent)
        box.setEditable(True)
        box.addItems(self.speakers_fn())
        return box

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText().strip())


class TextDelegate(QStyledItemDelegate):
    """Multi-line text editor. Enter saves, Esc cancels; the cursor position is used by Split."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active: QPlainTextEdit | None = None
        self.active_row = -1

    def createEditor(self, parent, option, index):
        ed = QPlainTextEdit(parent)
        ed.setFrameShape(QFrame.NoFrame)
        ed.installEventFilter(self)
        self.active, self.active_row = ed, index.row()
        ed.destroyed.connect(self._forget)
        return ed

    def _forget(self, *_):
        self.active, self.active_row = None, -1

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data() or "")
        editor.moveCursor(QTextCursor.End)

    def setModelData(self, editor, model, index):
        model.setData(index, " ".join(editor.toPlainText().split()))

    def updateEditorGeometry(self, editor, option, index):
        r = option.rect
        r.setHeight(max(r.height(), 64))
        editor.setGeometry(r)

    def eventFilter(self, obj, event):
        if isinstance(obj, QPlainTextEdit) and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
                self.commit_active()
                return True
        return super().eventFilter(obj, event)

    def commit_active(self):
        ed = self.active
        if ed is not None:
            self.commitData.emit(ed)
            self.closeEditor.emit(ed, QStyledItemDelegate.NoHint)


# --------------------------------------------------------------------------- dialogs
class SplitDialog(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Split line")
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Click where the new line should start, then press Split."))
        self.edit = QPlainTextEdit(text)
        self.edit.setReadOnly(True)
        self.edit.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        cur = self.edit.textCursor()
        cur.setPosition(len(text) // 2)
        self.edit.setTextCursor(cur)
        v.addWidget(self.edit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Split")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.resize(560, 220)

    def position(self) -> int:
        return self.edit.textCursor().position()


class RenameSpeakerDialog(QDialog):
    def __init__(self, speakers: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rename speaker")
        f = QFormLayout(self)
        self.old = QComboBox()
        self.old.addItems(speakers)
        self.new = QLineEdit()
        self.new.setPlaceholderText("e.g. Mrs Banda")
        f.addRow("Speaker", self.old)
        f.addRow("New name", self.new)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


# --------------------------------------------------------------------------- editor
class TranscriptEditor(QWidget):
    changed = Signal()                 # segments edited (autosave)
    speakers_changed = Signal(list)    # speaker list edited (save to meta)
    seek = Signal(int)                 # play from this time (ms)
    message = Signal(str)              # status-bar text

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments: list[Segment] = []
        self.speakers: list[str] = list(editing.DEFAULT_SPEAKERS)
        self._undo: list[list[Segment]] = []
        self._loading = False
        self._playing_row = -1
        self._build()
        self._shortcuts()

    # ---------------- construction
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        # tool row
        tools = QHBoxLayout()
        tools.addWidget(QLabel("Speaker:"))
        self.speaker_row = QHBoxLayout()
        self.speaker_row.setSpacing(4)
        tools.addLayout(self.speaker_row)
        self.btn_add_speaker = QToolButton(text="+")
        self.btn_add_speaker.setToolTip("Add a speaker")
        self.btn_add_speaker.clicked.connect(self.add_speaker)
        self.btn_rename = QToolButton(text="Rename…")
        self.btn_rename.setToolTip("Rename a speaker everywhere in this transcript")
        self.btn_rename.clicked.connect(self.rename_speaker)
        tools.addWidget(self.btn_add_speaker)
        tools.addWidget(self.btn_rename)
        tools.addSpacing(16)
        self.action_buttons = []
        for text, tip, slot in [
            ("Merge", "Join selected lines (Ctrl+M)", self.merge_selected),
            ("Split", "Split a line at the cursor (Ctrl+K)", self.split_current),
            ("Delete", "Delete selected lines (Ctrl+Delete)", self.delete_selected),
            ("Undo", "Undo last change (Ctrl+Z)", self.undo),
            ("Find…", "Find and replace (Ctrl+F)", self.show_find),
        ]:
            b = QToolButton(text=text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            tools.addWidget(b)
            self.action_buttons.append(b)
        self.btn_undo = self.action_buttons[3]
        tools.addStretch(1)
        v.addLayout(tools)

        # find bar
        self.find_bar = QWidget()
        fb = QHBoxLayout(self.find_bar)
        fb.setContentsMargins(0, 0, 0, 0)
        self.find_edit = QLineEdit(placeholderText="Find")
        self.find_edit.returnPressed.connect(self.find_next)
        self.replace_edit = QLineEdit(placeholderText="Replace with")
        self.match_case = QCheckBox("Match case")
        fb.addWidget(self.find_edit, 2)
        fb.addWidget(self.replace_edit, 2)
        fb.addWidget(self.match_case)
        for text, slot in (("Next", self.find_next), ("Replace", self.replace_one),
                           ("Replace all", self.replace_all), ("✕", self.hide_find)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            fb.addWidget(b)
        self.find_bar.hide()
        v.addWidget(self.find_bar)

        # table
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Time", "Speaker", "Text"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_TIME, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_SPEAKER, QHeaderView.Interactive)
        hh.setSectionResizeMode(COL_TEXT, QHeaderView.Stretch)
        self.table.setColumnWidth(COL_SPEAKER, 120)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setItemDelegateForColumn(COL_SPEAKER, SpeakerDelegate(lambda: self.speakers, self.table))
        self.text_delegate = TextDelegate(self.table)
        self.table.setItemDelegateForColumn(COL_TEXT, self.text_delegate)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        v.addWidget(self.table, 1)
        self._rebuild_speaker_buttons()

    def _shortcuts(self):
        def sc(keys, slot):
            s = QShortcut(QKeySequence(keys), self)
            s.setContext(Qt.WindowShortcut)
            s.activated.connect(slot)

        for n in range(1, 10):
            sc(f"Ctrl+{n}", lambda n=n: self.assign_speaker_index(n - 1))
        sc("Ctrl+0", lambda: self.assign_speaker(""))
        sc("Ctrl+M", self.merge_selected)
        sc("Ctrl+K", self.split_current)
        sc("Ctrl+Delete", self.delete_selected)
        sc("Ctrl+F", self.show_find)
        sc("Ctrl+H", self.show_find)
        sc("F3", self.find_next)
        esc = QShortcut(QKeySequence("Escape"), self.find_bar)
        esc.setContext(Qt.WidgetWithChildrenShortcut)
        esc.activated.connect(self.hide_find)
        # Ctrl+Z only when not typing inside a cell (the cell editor has its own undo)
        undo = QShortcut(QKeySequence.Undo, self.table)
        undo.setContext(Qt.WidgetShortcut)
        undo.activated.connect(self.undo)

    # ---------------- public API
    def set_content(self, segments: list[Segment], speakers: list[str] | None):
        self.segments = deepcopy(segments)
        self.speakers = list(speakers) if speakers else list(editing.DEFAULT_SPEAKERS)
        for s in self.segments:  # keep any speaker used in the text
            if s.speaker and s.speaker not in self.speakers:
                self.speakers.append(s.speaker)
        self._undo.clear()
        self._playing_row = -1
        self._rebuild_speaker_buttons()
        self._reload(keep_scroll=False)
        self._update_actions()

    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def current_start_ms(self) -> int | None:
        r = self.table.currentRow()
        return self.segments[r].start_ms if 0 <= r < len(self.segments) else None

    def set_playing_ms(self, ms: int | None):
        """Highlight the line being played (does not change the user's selection)."""
        row = -1
        if ms is not None:
            for r, s in enumerate(self.segments):
                if s.start_ms <= ms < max(s.end_ms, s.start_ms + 1):
                    row = r
                    break
        if row == self._playing_row:
            return
        self._loading = True
        for r, color in ((self._playing_row, QColor(0, 0, 0, 0)), (row, PLAYING_BG)):
            if 0 <= r < self.table.rowCount():
                for c in range(3):
                    item = self.table.item(r, c)
                    if item:
                        item.setBackground(color)
        self._loading = False
        self._playing_row = row
        if row >= 0:
            self.table.scrollToItem(self.table.item(row, COL_TIME), QAbstractItemView.EnsureVisible)

    # ---------------- internals
    def _reload(self, keep_scroll=True, select: list[int] | None = None):
        scroll = self.table.verticalScrollBar().value()
        self._loading = True
        self.table.setRowCount(len(self.segments))
        for r, s in enumerate(self.segments):
            t = QTableWidgetItem(fmt_time(s.start_ms))
            t.setFlags(t.flags() & ~Qt.ItemIsEditable)
            t.setForeground(QColor("#42a5f5"))
            t.setToolTip("Double-click to play from here")
            self.table.setItem(r, COL_TIME, t)
            self.table.setItem(r, COL_SPEAKER, QTableWidgetItem(s.speaker))
            self.table.setItem(r, COL_TEXT, QTableWidgetItem(s.text))
        self.table.resizeRowsToContents()
        self._loading = False
        self._playing_row = -1
        if keep_scroll:
            self.table.verticalScrollBar().setValue(scroll)
        if select:
            self.table.clearSelection()
            mode = self.table.selectionMode()
            self.table.setSelectionMode(QAbstractItemView.MultiSelection)
            for r in select:
                if 0 <= r < len(self.segments):
                    self.table.selectRow(r)
            self.table.setSelectionMode(mode)
            if 0 <= select[0] < len(self.segments):
                self.table.setCurrentCell(select[0], COL_TEXT)

    def _apply(self, new_segments: list[Segment], select: list[int] | None = None):
        """Replace the transcript with an undo point, refresh and signal a change."""
        self._undo.append(deepcopy(self.segments))
        del self._undo[:-UNDO_LIMIT]
        self.segments = new_segments
        self._reload(select=select)
        self._update_actions()
        self.changed.emit()

    def _update_actions(self):
        has = bool(self.segments)
        for b in self.action_buttons:
            b.setEnabled(has)
        self.btn_undo.setEnabled(bool(self._undo))

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._loading or item.row() >= len(self.segments):
            return
        seg = self.segments[item.row()]
        value = item.text().strip()
        if item.column() == COL_SPEAKER and value != seg.speaker:
            self._undo.append(deepcopy(self.segments))
            seg.speaker = value
            if value and value not in self.speakers:
                self.speakers.append(value)
                self._rebuild_speaker_buttons()
                self.speakers_changed.emit(self.speakers)
        elif item.column() == COL_TEXT and value != seg.text:
            self._undo.append(deepcopy(self.segments))
            seg.text = value
            self.table.resizeRowToContents(item.row())
        else:
            return
        self._update_actions()
        self.changed.emit()

    def _on_double_click(self, row: int, col: int):
        if col == COL_TIME and row < len(self.segments):
            self.seek.emit(self.segments[row].start_ms)

    # ---------------- speakers
    def _rebuild_speaker_buttons(self):
        while self.speaker_row.count():
            w = self.speaker_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, name in enumerate(self.speakers):
            b = QPushButton(f"{name}" + (f"  (Ctrl+{i + 1})" if i < 9 else ""))
            b.setToolTip(f"Set selected lines to '{name}'" + (f"  (Ctrl+{i + 1})" if i < 9 else ""))
            b.clicked.connect(lambda _=False, n=name: self.assign_speaker(n))
            self.speaker_row.addWidget(b)
        clear = QPushButton("None  (Ctrl+0)")
        clear.setToolTip("Clear the speaker of selected lines (Ctrl+0)")
        clear.clicked.connect(lambda: self.assign_speaker(""))
        self.speaker_row.addWidget(clear)

    def assign_speaker_index(self, i: int):
        if i < len(self.speakers):
            self.assign_speaker(self.speakers[i])

    def _commit_typing(self):
        """Save text the user is still typing in a cell before a structural edit."""
        if self.text_delegate.active is not None:
            self.text_delegate.commit_active()

    def assign_speaker(self, name: str):
        self._commit_typing()
        rows = self.selected_rows()
        if not rows:
            self.message.emit("Select one or more lines first.")
            return
        self._apply(editing.assign_speaker(self.segments, rows, name), select=rows)
        # move to the next line so you can label a whole interview from the keyboard
        nxt = rows[-1] + 1
        if len(rows) == 1 and nxt < len(self.segments):
            self.table.clearSelection()
            self.table.selectRow(nxt)
            self.table.setCurrentCell(nxt, COL_TEXT)

    def add_speaker(self):
        name, ok = QInputDialog.getText(self, "Add speaker", "Speaker name:")
        name = name.strip()
        if ok and name and name not in self.speakers:
            self.speakers.append(name)
            self._rebuild_speaker_buttons()
            self.speakers_changed.emit(self.speakers)

    def rename_speaker(self):
        if not self.speakers:
            return
        dlg = RenameSpeakerDialog(self.speakers, self)
        if not dlg.exec():
            return
        old, new = dlg.old.currentText(), dlg.new.text().strip()
        if not new or new == old:
            return
        self._apply(editing.rename_speaker(self.segments, old, new))
        self.speakers = [new if s == old else s for s in self.speakers]
        self.speakers = list(dict.fromkeys(self.speakers))  # de-duplicate, keep order
        self._rebuild_speaker_buttons()
        self.speakers_changed.emit(self.speakers)
        self.message.emit(f"Renamed '{old}' to '{new}'.")

    # ---------------- structure edits
    def merge_selected(self):
        self._commit_typing()
        rows = self.selected_rows()
        if len(rows) < 2:
            self.message.emit("Select two or more lines to merge (Shift+click or Ctrl+click).")
            return
        self._apply(editing.merge(self.segments, rows), select=[rows[0]])

    def split_current(self):
        row, pos, text = -1, -1, ""
        ed = self.text_delegate.active
        if ed is not None:  # currently typing in a cell: split at the text cursor
            row = self.text_delegate.active_row
            pos = ed.textCursor().position()
            text = ed.toPlainText()
            self.text_delegate.commit_active()
            pos -= len(text) - len(text.lstrip())  # commit strips leading spaces
        else:
            row = self.table.currentRow()
            if not 0 <= row < len(self.segments):
                self.message.emit("Click a line to split first.")
                return
            dlg = SplitDialog(self.segments[row].text, self)
            if not dlg.exec():
                return
            pos = dlg.position()
        if 0 <= row < len(self.segments):
            new = editing.split(self.segments, row, pos)
            if len(new) == len(self.segments):
                self.message.emit("Put the cursor inside the text (not at the start or end).")
                return
            self._apply(new, select=[row + 1])

    def delete_selected(self):
        self._commit_typing()
        rows = self.selected_rows()
        if not rows:
            return
        if len(rows) > 1 and QMessageBox.question(
                self, "Delete lines", f"Delete {len(rows)} lines? (You can undo with Ctrl+Z.)") != QMessageBox.Yes:
            return
        self._apply(editing.delete(self.segments, rows), select=[min(rows[0], len(self.segments) - len(rows) - 1)])

    def undo(self):
        if not self._undo:
            return
        self.segments = self._undo.pop()
        for s in self.segments:
            if s.speaker and s.speaker not in self.speakers:
                self.speakers.append(s.speaker)
        self._rebuild_speaker_buttons()
        self._reload()
        self._update_actions()
        self.changed.emit()
        self.message.emit("Undone.")

    # ---------------- find & replace
    def show_find(self):
        self.find_bar.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def hide_find(self):
        if self.find_bar.isVisible():
            self.find_bar.hide()
            self.table.setFocus()

    def find_next(self):
        q = self.find_edit.text()
        if not q:
            self.show_find()
            return
        r = editing.find_next(self.segments, q, self.table.currentRow(), self.match_case.isChecked())
        if r < 0:
            self.message.emit(f"'{q}' not found.")
            return
        self.table.clearSelection()
        self.table.selectRow(r)
        self.table.setCurrentCell(r, COL_TEXT)
        self.table.scrollToItem(self.table.item(r, COL_TEXT), QAbstractItemView.PositionAtCenter)

    def replace_one(self):
        q, rep, case = self.find_edit.text(), self.replace_edit.text(), self.match_case.isChecked()
        r = self.table.currentRow()
        if q and 0 <= r < len(self.segments):
            new = editing.replace_in_row(self.segments, r, q, rep, case)
            if new[r].text != self.segments[r].text:
                self._apply(new, select=[r])
        self.find_next()

    def replace_all(self):
        q, rep = self.find_edit.text(), self.replace_edit.text()
        new, n = editing.replace_all(self.segments, q, rep, self.match_case.isChecked())
        if n:
            self._apply(new)
        self.message.emit(f"Replaced {n} occurrence(s)." if n else f"'{q}' not found.")
