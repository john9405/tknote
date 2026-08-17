"""Find/Replace dialogs powered by idlelib's SearchEngine.

Upgrades over the old one-shot dialogs:
  - regex, case, whole-word and wraparound options (persist in the
    process-wide SearchEngine singleton)
  - incremental highlight-all while typing
  - find next / previous, wrap around
  - replace / replace+find / replace all — per-occurrence edits inside
    a single undo block (never clobbers the undo history)
"""

import re
import tkinter as tk
from tkinter import ttk

from idlelib.searchengine import get as get_searchengine


def _reveal_line(editor, lineno):
    """Unfold the fold containing a line, if folding is active."""
    fold = getattr(editor, '_fold', None)
    if fold is not None:
        fold.unfold_containing(lineno)


class SearchDialogBase(tk.Toplevel):
    """Shared machinery for the Find and Replace dialogs."""

    HIT_BG = '#ff9800'
    FOUND_BG = '#fff59d'

    def __init__(self, app, editor, title):
        super().__init__(app.root)
        self.app = app
        self.editor = editor
        self.text = editor.get_text_widget()
        self.engine = get_searchengine(app.root)
        self._debounce_id = None

        self.title(title)
        self.transient(app.root)
        self.grab_set()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda e: self.close())
        self._last_hit = None  # (index1, index2) of the current hit
        self._build()
        self._entries[0].focus_set()
        self.highlight_all()

    # ── construction ───────────────────────────────────────────────────

    def _build(self):
        raise NotImplementedError

    def _build_option_row(self, row=1):
        frame = ttk.Frame(self)
        frame.grid(row=row, column=0, columnspan=2, sticky='w',
                   padx=10, pady=(0, 2))
        ttk.Checkbutton(frame, text='Regex', variable=self.engine.revar,
                        command=self.schedule_highlight).pack(side=tk.LEFT)
        ttk.Checkbutton(frame, text='Case', variable=self.engine.casevar,
                        command=self.schedule_highlight).pack(side=tk.LEFT)
        ttk.Checkbutton(frame, text='Word', variable=self.engine.wordvar,
                        command=self.schedule_highlight).pack(side=tk.LEFT)
        ttk.Checkbutton(frame, text='Wrap', variable=self.engine.wrapvar
                        ).pack(side=tk.LEFT)

    def _build_buttons(self, buttons, row):
        frame = ttk.Frame(self)
        frame.grid(row=row, column=0, columnspan=2, pady=(6, 10))
        for text, command in reversed(buttons):
            ttk.Button(frame, text=text, command=command
                       ).pack(side=tk.RIGHT, padx=2)

    def _bind_navigation(self):
        self.bind('<Return>', lambda e: self.find_next())
        self.bind('<Shift-Return>', lambda e: self.find_prev())

    # ── lifecycle ──────────────────────────────────────────────────────

    def close(self, event=None):
        self._cancel_debounce()
        self.clear_highlights()
        self.destroy()

    def _cancel_debounce(self):
        if self._debounce_id is not None:
            try:
                self.after_cancel(self._debounce_id)
            except tk.TclError:
                pass
            self._debounce_id = None

    def schedule_highlight(self, event=None):
        self._cancel_debounce()
        self._debounce_id = self.after(200, self.highlight_all)

    def clear_highlights(self):
        try:
            self.text.tag_remove('found', '1.0', 'end')
            self.text.tag_remove('hit', '1.0', 'end')
        except tk.TclError:
            pass

    # ── highlighting ───────────────────────────────────────────────────

    def highlight_all(self):
        """Tag every match with 'found' (incremental highlight-all)."""
        text = self.text
        text.tag_remove('found', '1.0', 'end')
        if not self.engine.getpat():
            return 0  # empty pattern — silently no-op (getprog would pop
                      # an error dialog)
        prog = self.engine.getprog()
        if not prog:
            return 0
        content = text.get('1.0', 'end-1c')
        count = 0
        for m in prog.finditer(content):
            if m.start() == m.end():
                continue  # skip zero-length matches
            text.tag_add('found', f'1.0+{m.start()}c',
                         f'1.0+{m.end()}c')
            count += 1
        text.tag_config('found', background=self.FOUND_BG)
        return count

    # ── navigation ─────────────────────────────────────────────────────

    def find_next(self, ok=False):
        return self._search(backwards=False, ok=ok)

    def find_prev(self, ok=False):
        return self._search(backwards=True, ok=ok)

    def _search(self, backwards, ok):
        if not self.engine.getpat():
            self.text.bell()
            return False
        prog = self.engine.getprog()
        if not prog:
            return False
        text = self.text
        # Advance from the previous hit when the cursor is still on it,
        # otherwise search from the cursor.  (The sel.first/sel.last
        # marks don't track tag_add('sel') on this Tk build, so the
        # engine's selection-based starting point is unusable.)
        if self._last_hit:
            h1, h2 = self._last_hit
            ins = text.index('insert')
            if text.compare(h1, '<=', ins) and text.compare(ins, '<=', h2):
                start = h1 if backwards else h2
            else:
                start = ins
        else:
            start = text.index('insert')
        line, col = map(int, start.split('.'))
        if backwards:
            res = self.engine.search_backward(text, prog, line, col,
                                              self.engine.iswrap(), 0)
        else:
            res = self.engine.search_forward(text, prog, line, col,
                                             self.engine.iswrap(), 0)
        if not res:
            text.bell()
            return False
        line, m = res
        index1 = f'{line}.{m.start()}'
        index2 = f'{line}.{m.end()}'
        _reveal_line(self.editor, line)
        text.tag_remove('hit', '1.0', 'end')
        text.tag_add('hit', index1, index2)
        text.tag_config('hit', background=self.HIT_BG,
                        foreground='#000000')
        # Select the match for visibility; advance from it next time.
        text.tag_remove('sel', '1.0', 'end')
        text.tag_add('sel', index1, index2)
        self._last_hit = (index1, index2)
        text.mark_set('insert', index1)
        text.see(index1)
        return True


class FindDialog(SearchDialogBase):
    """Find dialog: options + incremental highlight + next/prev."""

    def _build(self):
        self.grid_columnconfigure(1, weight=1)

        ttk.Label(self, text='Find:').grid(row=0, column=0, sticky='w',
                                           padx=10, pady=(10, 2))
        self._entries = [ttk.Entry(self, textvariable=self.engine.patvar)]
        self._entries[0].grid(row=0, column=1, sticky='ew',
                              padx=(0, 10), pady=(10, 2))
        self._entries[0].bind('<KeyRelease>', self.schedule_highlight)

        self._build_option_row(row=1)
        self._build_buttons([
            ('Close', self.close),
            ('Find Prev', self.find_prev),
            ('Find Next', self.find_next),
        ], row=2)
        self._bind_navigation()


class ReplaceDialog(SearchDialogBase):
    """Replace dialog: single replace, replace+find, replace all.

    All replacement edits go through the undo stack as a single
    undo step — the old dialog replaced the whole buffer via
    set_text, which reset the undo history.
    """

    def _build(self):
        self.grid_columnconfigure(1, weight=1)

        ttk.Label(self, text='Find:').grid(row=0, column=0, sticky='w',
                                           padx=10, pady=(10, 2))
        self._entries = [ttk.Entry(self, textvariable=self.engine.patvar)]
        self._entries[0].grid(row=0, column=1, sticky='ew',
                              padx=(0, 10), pady=(10, 2))
        self._entries[0].bind('<KeyRelease>', self.schedule_highlight)

        ttk.Label(self, text='Replace with:').grid(row=1, column=0,
                                                   sticky='w', padx=10)
        self._repl_var = tk.StringVar()
        self._entries.append(ttk.Entry(self, textvariable=self._repl_var))
        self._entries[1].grid(row=1, column=1, sticky='ew', padx=(0, 10))

        self._build_option_row(row=2)
        self._build_buttons([
            ('Close', self.close),
            ('Replace All', self.replace_all),
            ('Replace+Find', self.replace_find),
            ('Replace', self.do_replace),
        ], row=3)
        self._bind_navigation()

    # ── replacement ────────────────────────────────────────────────────

    def _expanded(self, m):
        repl = self._repl_var.get()
        if self.engine.isre():
            try:
                return m.expand(repl)
            except re.error:
                return repl
        return repl

    def _replace_at(self, line, m):
        """Replace the match at (line, m) — single undo step."""
        if m.start() == m.end():
            return
        text = self.text
        index1 = f'{line}.{m.start()}'
        index2 = f'{line}.{m.end()}'
        expanded = self._expanded(m)
        text.undo_block_start()
        try:
            text.delete(index1, index2)
            text.insert(index1, expanded)
        finally:
            text.undo_block_stop()
        text.mark_set('insert', index1)
        text.see('insert')
        # Track the replacement as the current hit so Replace+Find
        # continues after it.
        self._last_hit = (index1, text.index(f'{index1}+{len(expanded)}c'))
        self.highlight_all()

    def do_replace(self):
        """Replace the next match from the cursor."""
        if not self.engine.getpat():
            self.text.bell()
            return False
        prog = self.engine.getprog()
        if not prog:
            return False
        text = self.text
        line, col = map(int, text.index('insert').split('.'))
        res = self.engine.search_forward(text, prog, line, col,
                                         self.engine.iswrap(), 1)
        if not res:
            text.bell()
            return False
        line, m = res
        _reveal_line(self.editor, line)
        self._replace_at(line, m)
        return True

    def replace_find(self):
        """Replace the next match, then jump to the following one."""
        if not self.engine.getpat():
            self.text.bell()
            return False
        if not self.do_replace():
            return False
        return self.find_next(ok=False)

    def replace_all(self):
        """Replace every match — one single undo step."""
        prog = self.engine.getprog()
        if not prog:
            return 0
        text = self.text
        content = text.get('1.0', 'end-1c')
        matches = [m for m in prog.finditer(content)
                   if m.start() != m.end()]
        if not matches:
            text.bell()
            return 0
        text.undo_block_start()
        try:
            # Reverse order keeps the offsets valid as text shifts.
            for m in reversed(matches):
                text.delete(f'1.0+{m.start()}c', f'1.0+{m.end()}c')
                text.insert(f'1.0+{m.start()}c', self._expanded(m))
        finally:
            text.undo_block_stop()
        self._last_hit = None
        self.highlight_all()
        return len(matches)
