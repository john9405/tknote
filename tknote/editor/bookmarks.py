"""Bookmarks — persistent per-file line bookmarks (IDEA-style).

Bookmarks are stored in ~/.tknote_bookmarks.json keyed by absolute
file path, shown as markers in the line-number sidebar, and navigable
with next/previous (wraparound).
"""

import json
import os
import tkinter as tk

BOOKMARK_FILE = os.path.expanduser('~/.tknote_bookmarks.json')
SAVE_DELAY = 500   # ms debounce before writing to disk


class BookmarkManager:
    """Bookmarks for one EditorWidget."""

    def __init__(self, editor):
        self.editor = editor
        self.text = editor.get_text_widget()
        self._bookmarks = set()
        self._save_after_id = None
        self.load()

    # ── persistence ────────────────────────────────────────────────────

    def get_file_path(self):
        return getattr(self.editor, '_file_path', None)

    def load(self):
        path = self.get_file_path()
        if not path:
            return
        try:
            with open(BOOKMARK_FILE, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        self._bookmarks = set(data.get(path, []))
        self.refresh_markers()

    def _schedule_save(self):
        if self._save_after_id is not None:
            try:
                self.text.after_cancel(self._save_after_id)
            except tk.TclError:
                pass
        self._save_after_id = self.text.after(SAVE_DELAY, self._save)

    def _save(self):
        self._save_after_id = None
        path = self.get_file_path()
        if not path:
            return
        try:
            try:
                with open(BOOKMARK_FILE, encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
            if self._bookmarks:
                data[path] = sorted(self._bookmarks)
            else:
                data.pop(path, None)
            with open(BOOKMARK_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except OSError:
            pass

    # ── operations ─────────────────────────────────────────────────────

    def toggle(self, lineno=None):
        """Toggle a bookmark on the given (or cursor) line.

        Returns True if the bookmark was added, False if removed.
        """
        if lineno is None:
            lineno = int(float(self.text.index('insert')))
        if lineno in self._bookmarks:
            self._bookmarks.discard(lineno)
            added = False
        else:
            self._bookmarks.add(lineno)
            added = True
        self.refresh_markers()
        self._schedule_save()
        return added

    def has(self, lineno):
        return lineno in self._bookmarks

    def next(self, lineno=None):
        """Jump to the next bookmark after lineno (wraps around)."""
        if not self._bookmarks:
            self.text.bell()
            return None
        if lineno is None:
            lineno = int(float(self.text.index('insert')))
        later = sorted(l for l in self._bookmarks if l > lineno)
        target = later[0] if later else min(self._bookmarks)
        self._goto(target)
        return target

    def prev(self, lineno=None):
        """Jump to the previous bookmark before lineno (wraps around)."""
        if not self._bookmarks:
            self.text.bell()
            return None
        if lineno is None:
            lineno = int(float(self.text.index('insert')))
        earlier = sorted(l for l in self._bookmarks if l < lineno)
        target = earlier[-1] if earlier else max(self._bookmarks)
        self._goto(target)
        return target

    def _goto(self, lineno):
        self.editor.go_to_line(lineno)

    def refresh_markers(self):
        """Redraw bookmark markers in the line-number sidebar."""
        line_text = getattr(self.editor, '_line_text', None)
        if line_text is None:
            return
        # Map to display rows when folding is active; skip elided lines.
        fold = getattr(self.editor, '_fold', None)
        line_text.config(state=tk.NORMAL)
        try:
            line_text.tag_remove('bookmark', '1.0', 'end')
            for lineno in sorted(self._bookmarks):
                if fold is not None and fold.has_folds():
                    if fold.is_elided(lineno):
                        continue
                    lineno = fold.display_line(lineno)
                line_text.tag_add('bookmark', f'{lineno}.0',
                                  f'{lineno}.0 lineend')
        finally:
            line_text.config(state=tk.DISABLED)
