import builtins
import code
import fcntl
import keyword
import os
import pty
import re
import select
import signal
import string
import struct
import subprocess
import sys
import termios
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk


# =====================================================================
# idlelib-style editor infrastructure: Delegator / Percolator chain
# =====================================================================

class Delegator:
    """Base class for a delegator in a filter chain."""

    def __init__(self, delegate=None):
        self.delegate = delegate
        self.__cache = set()

    def __getattr__(self, name):
        attr = getattr(self.delegate, name)
        setattr(self, name, attr)
        self.__cache.add(name)
        return attr

    def resetcache(self):
        for key in self.__cache:
            try:
                delattr(self, key)
            except AttributeError:
                pass
        self.__cache.clear()

    def setdelegate(self, delegate):
        self.resetcache()
        self.delegate = delegate


class WidgetRedirector:
    """Intercepts Tcl-level widget commands (insert/delete)."""

    def __init__(self, widget):
        self._operations = {}
        self.widget = widget
        self.tk = tk = widget.tk
        w = widget._w
        self.orig = w + "_orig"
        tk.call("rename", w, self.orig)
        tk.createcommand(w, self.dispatch)

    def close(self):
        for operation in list(self._operations):
            self.unregister(operation)
        widget = self.widget
        tk = widget.tk
        w = widget._w
        tk.deletecommand(w)
        tk.call("rename", self.orig, w)
        del self.widget, self.tk

    def register(self, operation, function):
        self._operations[operation] = function
        setattr(self.widget, operation, function)
        return OriginalCommand(self, operation)

    def unregister(self, operation):
        if operation in self._operations:
            function = self._operations[operation]
            del self._operations[operation]
            try:
                delattr(self.widget, operation)
            except AttributeError:
                pass
            return function
        return None

    def dispatch(self, operation, *args):
        m = self._operations.get(operation)
        try:
            if m:
                return m(*args)
            else:
                return self.tk.call((self.orig, operation) + args)
        except tk.TclError:
            return ""


class OriginalCommand:
    """Callable that invokes the original (pre-redirect) Tcl command."""

    def __init__(self, redir, operation):
        self.tk_call = redir.tk.call
        self.orig_and_operation = (redir.orig, operation)

    def __call__(self, *args):
        return self.tk_call(self.orig_and_operation + args)


class Percolator:
    """Manages a chain of Delegator filters over a Text widget."""

    def __init__(self, text):
        self.text = text
        self.redir = WidgetRedirector(text)
        self.top = self.bottom = Delegator(text)
        self.bottom.insert = self.redir.register("insert", self.insert)
        self.bottom.delete = self.redir.register("delete", self.delete)

    def close(self):
        while self.top is not self.bottom:
            self.removefilter(self.top)
        self.top = None
        self.bottom.setdelegate(None)
        self.bottom = None
        self.redir.close()
        self.redir = None
        self.text = None

    def insert(self, index, chars, tags=None):
        self.top.insert(index, chars, tags)

    def delete(self, index1, index2=None):
        self.top.delete(index1, index2)

    def insertfilter(self, filter):
        assert isinstance(filter, Delegator)
        assert filter.delegate is None
        filter.setdelegate(self.top)
        self.top = filter

    def removefilter(self, filter):
        assert isinstance(filter, Delegator)
        assert filter.delegate is not None
        f = self.top
        if f is filter:
            self.top = filter.delegate
            filter.setdelegate(None)
        else:
            while f.delegate is not filter:
                assert f is not self.bottom
                f.resetcache()
                f = f.delegate
            f.setdelegate(filter.delegate)
            filter.setdelegate(None)


# =====================================================================
# Undo infrastructure (command-pattern)
# =====================================================================

class InsertCommand:
    """Undoable insert."""

    def __init__(self, index1, chars, tags=None):
        self.index1 = index1
        self.chars = chars
        self.tags = tags
        self.index2 = None
        self.marks_before = {}
        self.marks_after = {}

    def do(self, text):
        self.marks_before = self._save_marks(text)
        self.index1 = text.index(self.index1)
        if text.compare(self.index1, ">", "end-1c"):
            self.index1 = text.index("end-1c")
        text.insert(self.index1, self.chars, self.tags)
        self.index2 = text.index(f"{self.index1}+{len(self.chars)}c")
        self.marks_after = self._save_marks(text)

    def redo(self, text):
        text.mark_set("insert", self.index1)
        text.insert(self.index1, self.chars, self.tags)
        self._set_marks(text, self.marks_after)
        text.see("insert")

    def undo(self, text):
        text.mark_set("insert", self.index1)
        text.delete(self.index1, self.index2)
        self._set_marks(text, self.marks_before)
        text.see("insert")

    def merge(self, cmd):
        if self.__class__ is not cmd.__class__:
            return False
        if self.index2 != cmd.index1:
            return False
        if self.tags != cmd.tags:
            return False
        if len(cmd.chars) != 1:
            return False
        if self.chars and \
           self._classify(self.chars[-1]) != self._classify(cmd.chars):
            return False
        self.index2 = cmd.index2
        self.chars = self.chars + cmd.chars
        return True

    _ALPHANUM = string.ascii_letters + string.digits + "_"

    @classmethod
    def _classify(cls, c):
        if c in cls._ALPHANUM:
            return "alphanumeric"
        if c == "\n":
            return "newline"
        return "punctuation"

    @staticmethod
    def _save_marks(text):
        marks = {}
        for name in text.mark_names():
            if name not in ("insert", "current"):
                marks[name] = text.index(name)
        return marks

    @staticmethod
    def _set_marks(text, marks):
        for name, index in marks.items():
            text.mark_set(name, index)


class DeleteCommand:
    """Undoable delete."""

    def __init__(self, index1, index2=None):
        self.index1 = index1
        self.index2 = index2
        self.chars = None
        self.marks_before = {}
        self.marks_after = {}

    def do(self, text):
        self.marks_before = self._save_marks(text)
        self.index1 = text.index(self.index1)
        if self.index2:
            self.index2 = text.index(self.index2)
        else:
            self.index2 = text.index(f"{self.index1}+1c")
        if text.compare(self.index2, ">", "end-1c"):
            self.index2 = text.index("end-1c")
        self.chars = text.get(self.index1, self.index2)
        text.delete(self.index1, self.index2)
        self.marks_after = self._save_marks(text)

    def redo(self, text):
        text.mark_set("insert", self.index1)
        text.delete(self.index1, self.index2)
        self._set_marks(text, self.marks_after)
        text.see("insert")

    def undo(self, text):
        text.mark_set("insert", self.index1)
        text.insert(self.index1, self.chars)
        self._set_marks(text, self.marks_before)
        text.see("insert")

    @staticmethod
    def _save_marks(text):
        marks = {}
        for name in text.mark_names():
            if name not in ("insert", "current"):
                marks[name] = text.index(name)
        return marks

    @staticmethod
    def _set_marks(text, marks):
        for name, index in marks.items():
            text.mark_set(name, index)


class CommandSequence:
    """Groups multiple commands to be undone/redone as a unit."""

    def __init__(self):
        self.cmds = []
        self.depth = 0

    def __len__(self):
        return len(self.cmds)

    def append(self, cmd):
        self.cmds.append(cmd)

    def getcmd(self, i):
        return self.cmds[i]

    def redo(self, text):
        for cmd in self.cmds:
            cmd.redo(text)

    def undo(self, text):
        for cmd in reversed(self.cmds):
            cmd.undo(text)

    def bump_depth(self, incr=1):
        self.depth += incr
        return self.depth


class UndoDelegator(Delegator):
    """Delegator that provides undo/redo by tracking insert/delete commands."""

    max_undo = 1000

    def __init__(self):
        Delegator.__init__(self)
        self._modified_callback = None
        self.reset_undo()

    def setdelegate(self, delegate):
        if self.delegate is not None:
            self.unbind("<<undo>>")
            self.unbind("<<redo>>")
        Delegator.setdelegate(self, delegate)
        if delegate is not None:
            self.bind("<<undo>>", self.undo_event)
            self.bind("<<redo>>", self.redo_event)

    def reset_undo(self):
        self.was_saved = -1
        self.pointer = 0
        self.undolist = []
        self.undoblock = 0
        self.set_saved(1)

    def set_saved(self, flag):
        self.saved = self.pointer if flag else -1
        self.can_merge = False
        self._check_saved()

    def get_saved(self):
        return self.saved == self.pointer

    def set_modified_callback(self, callback):
        self._modified_callback = callback

    def _check_saved(self):
        is_saved = self.get_saved()
        if is_saved != self.was_saved:
            self.was_saved = is_saved
            if self._modified_callback:
                self._modified_callback(not is_saved)

    def insert(self, index, chars, tags=None):
        self.addcmd(InsertCommand(index, chars, tags))

    def delete(self, index1, index2=None):
        self.addcmd(DeleteCommand(index1, index2))

    def undo_block_start(self):
        if self.undoblock == 0:
            self.undoblock = CommandSequence()
        self.undoblock.bump_depth()

    def undo_block_stop(self):
        if self.undoblock.bump_depth(-1) == 0:
            cmd = self.undoblock
            self.undoblock = 0
            if len(cmd) > 0:
                if len(cmd) == 1:
                    cmd = cmd.getcmd(0)
                self.addcmd(cmd, 0)

    def addcmd(self, cmd, execute=True):
        if execute:
            cmd.do(self.delegate)
        if self.undoblock != 0:
            self.undoblock.append(cmd)
            return
        if self.can_merge and self.pointer > 0:
            lastcmd = self.undolist[self.pointer - 1]
            if lastcmd.merge(cmd):
                return
        self.undolist[self.pointer:] = [cmd]
        if self.saved > self.pointer:
            self.saved = -1
        self.pointer += 1
        if len(self.undolist) > self.max_undo:
            del self.undolist[0]
            self.pointer -= 1
            if self.saved >= 0:
                self.saved -= 1
        self.can_merge = True
        self._check_saved()

    def undo_event(self, event=None):
        if self.pointer == 0:
            self.bell()
            return "break"
        cmd = self.undolist[self.pointer - 1]
        cmd.undo(self.delegate)
        self.pointer -= 1
        self.can_merge = False
        self._check_saved()
        return "break"

    def redo_event(self, event=None):
        if self.pointer >= len(self.undolist):
            self.bell()
            return "break"
        cmd = self.undolist[self.pointer]
        cmd.redo(self.delegate)
        self.pointer += 1
        self.can_merge = False
        self._check_saved()
        return "break"


# =====================================================================
# Color / syntax-highlighting delegator
# =====================================================================

_KEYWORDS = set(keyword.kwlist)
_BUILTINS = set(
    name for name in dir(builtins)
    if not name.startswith('_') and name not in _KEYWORDS
)

_STRING_PAT = (
    r'(?:(?:[rR]|[uU]|[fF]|(?:[fF][rR])|(?:[rR][fF]))?'
    r"""(?:''' (?:[^'\\]|\\.)* (?:'''|$) )|"""
    r"""(?:' (?:[^'\\\n]|\\.)* (?:'|$) )|"""
    r"""(?:\"\"\" (?:[^"\\]|\\.)* (?:\"\"\"|$) )|"""
    r"""(?:" (?:[^"\\\n]|\\.)* (?:"|$) )"""
    r')'
)
_COMMENT_PAT = r'#[^\n]*'
_DECORATOR_PAT = r'@\w+'

_TOKEN_RE = re.compile(
    '|'.join([
        f'(?P<STRING>{_STRING_PAT})',
        f'(?P<COMMENT>{_COMMENT_PAT})',
        f'(?P<DECORATOR>{_DECORATOR_PAT})',
        r'(?P<KEYWORD>\b' + '|'.join(_KEYWORDS) + r'\b)',
        r'(?P<BUILTIN>(?<![.\'\"\\#])\b' + '|'.join(_BUILTINS) + r'\b)',
        r'(?P<NUMBER>\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)',
        r'(?P<DEF>\bdef\s+(\w+)\b)',
        r'(?P<CLASS>\bclass\s+(\w+)\b)',
    ]),
    re.MULTILINE
)

_TAG_STYLES = {
    'KEYWORD':   {'foreground': '#ff7b00'},
    'BUILTIN':   {'foreground': '#795e26'},
    'STRING':    {'foreground': '#098658'},
    'COMMENT':   {'foreground': '#6a9955'},
    'DECORATOR': {'foreground': '#b08000'},
    'NUMBER':    {'foreground': '#098658'},
    'DEF':       {'foreground': '#795e26'},
    'CLASS':     {'foreground': '#795e26'},
}


class ColorDelegator(Delegator):
    """Re-tags text with syntax colors after each modification.

    Optimizations for large files:
      - Only recolor the visible region + a generous buffer (not the whole doc).
      - Skip scheduling a new recolor if one is already pending.
      - Scroll-triggered recoloring of newly visible lines.
    """

    COLOR_BUF_LINES = 150  # lines above/below visible region to also color

    def __init__(self):
        Delegator.__init__(self)
        self.prog = _TOKEN_RE
        self._recolor_pending = False
        self._colored_range = (0, 0)  # (first_line, last_line) already colored

    def setdelegate(self, delegate):
        if self.delegate is not None:
            self.unbind("<<toggle-auto-coloring>>")
        Delegator.setdelegate(self, delegate)
        if delegate is not None:
            self.bind("<<toggle-auto-coloring>>", self.toggle_colorize_event)
        self._setup_tags()

    def _setup_tags(self):
        text = self.delegate
        if text is None:
            return
        for tag_name, style in _TAG_STYLES.items():
            text.tag_configure(tag_name, **style)

    def insert(self, index, chars, tags=None):
        self.delegate.insert(index, chars, tags)
        self._recolor_after(index, chars)

    def delete(self, index1, index2=None):
        self.delegate.delete(index1, index2)
        self._recolor_after(index1, "")

    def _recolor_after(self, index, chars):
        if not self._recolor_pending:
            self._recolor_pending = True
            try:
                self.delegate.after_idle(self._do_recolor)
            except (tk.TclError, AttributeError):
                self._recolor_pending = False

    def recolor_full(self):
        """Force a full-document recolor (used after bulk load)."""
        self._colored_range = (0, 0)
        self._recolor_pending = False
        self._do_recolor_full()

    def _do_recolor_full(self):
        """Recolor the entire document (expensive, use sparingly)."""
        text = self.delegate
        if text is None:
            return
        try:
            for tag_name in _TAG_STYLES:
                text.tag_remove(tag_name, "1.0", "end")
            content = text.get("1.0", "end-1c")
            if not content:
                return
            for m in self.prog.finditer(content):
                start = f"1.0+{m.start()}c"
                end = f"1.0+{m.end()}c"
                for group_name in _TAG_STYLES:
                    if m.group(group_name) is not None:
                        text.tag_add(group_name, start, end)
                        break
            last_line = int(text.index('end-1c').split('.')[0])
            self._colored_range = (1, last_line)
        except tk.TclError:
            pass

    def _do_recolor(self):
        """Recolor only the visible region + buffer."""
        self._recolor_pending = False
        text = self.delegate
        if text is None:
            return
        try:
            vis_first, vis_last = self._visible_line_range(text)
            if vis_first is None:
                return

            buf = self.COLOR_BUF_LINES
            first = max(1, vis_first - buf)
            last_line = int(text.index('end-1c').split('.')[0])
            last = min(last_line, vis_last + buf)

            # Skip if already covered
            old_first, old_last = self._colored_range
            if first >= old_first and last <= old_last:
                return

            # Expand range to cover the gap
            first = min(first, old_first) if old_first else first
            last = max(last, old_last) if old_last else last

            # Remove old tags in range and re-tag
            for tag_name in _TAG_STYLES:
                text.tag_remove(tag_name, f'{first}.0', f'{last}.0 lineend')

            content = text.get(f'{first}.0', f'{last}.0 lineend')
            if not content:
                return

            offset = f'{first}.0'
            for m in self.prog.finditer(content):
                start = text.index(f"{offset}+{m.start()}c")
                end = text.index(f"{offset}+{m.end()}c")
                for group_name in _TAG_STYLES:
                    if m.group(group_name) is not None:
                        text.tag_add(group_name, start, end)
                        break

            self._colored_range = (first, last)
        except tk.TclError:
            pass

    def _visible_line_range(self, text):
        """Return (first_visible_line, last_visible_line) or (None, None)."""
        try:
            top = text.index('@0,0')
            bot = text.index(f'@0,{text.winfo_height()}')
            return int(top.split('.')[0]), int(bot.split('.')[0])
        except tk.TclError:
            return None, None

    def toggle_colorize_event(self, event=None):
        return "break"


# =====================================================================
# EditorWidget — self-contained editor with line numbers & highlighting
# =====================================================================

class EditorWidget(tk.Frame):
    """Self-contained code editor with line numbers, undo, and highlighting."""

    LINE_NUM_WIDTH = 40
    LINE_NUM_BG = '#f0f0f0'
    LINE_NUM_FG = '#999999'
    EDITOR_FONT = ('Monaco', 12)
    EDITOR_BG = '#ffffff'
    EDITOR_FG = '#1e1e1e'
    INSERT_BG = '#1e1e1e'

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._modified = False
        self._line_numbers_visible = True
        self._build_ui()

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self):
        # Line numbers (Canvas)
        self._line_canvas = tk.Canvas(
            self, width=self.LINE_NUM_WIDTH, bg=self.LINE_NUM_BG,
            highlightthickness=0, bd=0)
        self._line_canvas.pack(side=tk.LEFT, fill=tk.Y)

        # Text + scrollbar
        text_frame = tk.Frame(self)
        text_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._text = tk.Text(
            text_frame,
            wrap=tk.NONE,
            font=self.EDITOR_FONT,
            bg=self.EDITOR_BG,
            fg=self.EDITOR_FG,
            insertbackground=self.INSERT_BG,
            highlightthickness=0,
            bd=0,
            padx=8,
            pady=4,
            tabstyle='wordprocessor',
            undo=False,
            autoseparators=False,
        )
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._scrollbar = tk.Scrollbar(
            text_frame, orient=tk.VERTICAL, command=self._text.yview)
        self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._text.configure(yscrollcommand=self._on_scroll)

        # Percolator chain: Undo → Color
        self._percolator = Percolator(self._text)
        self._undo = UndoDelegator()
        self._percolator.insertfilter(self._undo)
        self._undo.set_modified_callback(self._on_modified_changed)

        self._color = ColorDelegator()
        self._percolator.insertfilter(self._color)

        # Track content changes for line numbers
        self._text.bind('<KeyRelease>', self._on_content_change)
        self._text.bind('<<Modified>>', self._noop)
        self._text.bind('<Configure>', self._on_text_configure)
        # Scroll-triggered recoloring for newly visible regions
        self._text.bind('<<Scroll>>', self._on_scroll_recolor)

        self.after_idle(self._draw_line_numbers)

    # ---- Scroll sync -------------------------------------------------------

    def _on_scroll(self, *args):
        self._scrollbar.set(*args)
        self._text.yview_moveto(args[0])
        self._draw_line_numbers()

    def _on_scroll_recolor(self, event=None):
        """Recolor newly visible lines after scrolling."""
        self._color._colored_range = (0, 0)  # force recolor of visible region
        self._color._recolor_after("1.0", "")

    def yview(self, *args):
        self._text.yview(*args)
        self._draw_line_numbers()

    def _on_text_configure(self, event=None):
        self._draw_line_numbers()

    def _on_content_change(self, event=None):
        self.after_idle(self._draw_line_numbers)

    def _noop(self, event=None):
        pass

    # ---- Line numbers ------------------------------------------------------

    def _draw_line_numbers(self):
        canvas = self._line_canvas
        text = self._text
        try:
            canvas.delete('all')
            if not self._line_numbers_visible:
                return
            top_idx = text.index('@0,0')
            bot_idx = text.index(f'@0,{text.winfo_height()}')
            top_line = int(top_idx.split('.')[0])
            bot_line = int(bot_idx.split('.')[0])
            last_char_y = text.bbox(f'{bot_line}.0')
            if last_char_y is None:
                last_char_y = (text.winfo_height(), 0)
            if last_char_y[1] <= 0:
                bot_line -= 1
            canvas_w = self.LINE_NUM_WIDTH
            for line in range(top_line, bot_line + 1):
                try:
                    dline = text.dlineinfo(f'{line}.0')
                    if dline is None:
                        continue
                    y = dline[1]
                    canvas.create_text(
                        canvas_w - 8, y,
                        text=str(line),
                        anchor='ne',
                        font=self.EDITOR_FONT,
                        fill=self.LINE_NUM_FG,
                    )
                except tk.TclError:
                    pass
            canvas.configure(height=text.winfo_height())
        except tk.TclError:
            pass

    # ---- Public API --------------------------------------------------------

    def get_text(self):
        return self._text.get('1.0', 'end-1c')

    def set_text(self, content):
        """Replace all text.  Bypasses the colorizer during bulk load for speed."""
        # Temporarily remove colorizer — large files would otherwise trigger
        # repeated regex scans during the bulk insert.
        self._percolator.removefilter(self._color)
        self._undo.undo_block_start()
        try:
            self._text.delete('1.0', 'end')
            if content:
                self._text.insert('1.0', content)
        finally:
            self._undo.undo_block_stop()
        self._undo.reset_undo()
        self._undo.set_saved(1)
        # Re-add colorizer; schedule visible-region recolor
        self._percolator.insertfilter(self._color)
        self._color._colored_range = (0, 0)
        self._color._recolor_after("1.0", "")  # schedule visible-region recolor
        self._draw_line_numbers()

    def get_text_widget(self):
        return self._text

    def undo(self):
        self._undo.undo_event()

    def redo(self):
        self._undo.redo_event()

    def cut(self):
        try:
            self._text.event_generate('<<Cut>>')
        except tk.TclError:
            pass

    def copy(self):
        try:
            self._text.event_generate('<<Copy>>')
        except tk.TclError:
            pass

    def paste(self):
        try:
            self._text.event_generate('<<Paste>>')
        except tk.TclError:
            pass

    def select_all(self):
        self._text.tag_add(tk.SEL, '1.0', 'end')
        self._text.mark_set(tk.INSERT, '1.0')
        self._text.see(tk.INSERT)
        return 'break'

    def is_modified(self):
        return self._modified

    def set_saved(self):
        self._undo.set_saved(1)

    # ---- Text widget delegation --------------------------------------------

    def get(self, *args):
        return self._text.get(*args)

    def insert(self, index, text, tags=None):
        self._text.insert(index, text, tags)

    def delete(self, index1, index2=None):
        self._text.delete(index1, index2)

    def index(self, index):
        return self._text.index(index)

    def tag_add(self, tag, index1, index2=None):
        self._text.tag_add(tag, index1, index2)

    def tag_remove(self, tag, index1, index2=None):
        self._text.tag_remove(tag, index1, index2)

    def tag_config(self, tag, **kw):
        self._text.tag_config(tag, **kw)

    def tag_delete(self, tag):
        self._text.tag_delete(tag)

    def mark_set(self, mark, index):
        self._text.mark_set(mark, index)

    def see(self, index):
        self._text.see(index)

    def search(self, pattern, index, **kw):
        return self._text.search(pattern, index, **kw)

    def dlineinfo(self, index):
        return self._text.dlineinfo(index)

    def compare(self, index1, op, index2):
        return self._text.compare(index1, op, index2)

    def edit_reset(self):
        self._undo.reset_undo()
        self._undo.set_saved(1)

    def edit_modified(self, flag=None):
        return False

    def edit_undo(self):
        self.undo()

    def edit_redo(self):
        self.redo()

    def edit_separator(self):
        pass

    def focus_set(self):
        self._text.focus_set()

    def get_line_numbers_visible(self):
        return self._line_numbers_visible

    def toggle_line_numbers(self):
        self._line_numbers_visible = not self._line_numbers_visible
        if self._line_numbers_visible:
            self._line_canvas.pack(side=tk.LEFT, fill=tk.Y)
            self._line_canvas.configure(width=self.LINE_NUM_WIDTH)
        else:
            self._line_canvas.pack_forget()
        self._draw_line_numbers()
        return self._line_numbers_visible

    def _on_modified_changed(self, modified):
        self._modified = modified

    def bind(self, sequence=None, func=None, add=None):
        return self._text.bind(sequence, func, add)

    def event_generate(self, sequence, **kw):
        self._text.event_generate(sequence, **kw)

    def destroy(self):
        try:
            self._percolator.close()
        except Exception:
            pass
        super().destroy()


# =====================================================================
# TabbedEditor
# =====================================================================

class TabbedEditor(ttk.Frame):
    """Multi-tab editor using Canvas for tab headers + Frame per tab content."""

    TAB_HEIGHT = 28
    MIN_TAB_WIDTH = 80
    MAX_TAB_WIDTH = 160
    TAB_PAD_X = 8
    CLOSE_SIZE = 16

    def __init__(self, parent, on_tab_created=None, on_tab_switch=None,
                 on_close_request=None, on_new_tab_request=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._on_tab_created = on_tab_created
        self._on_tab_switch = on_tab_switch
        self._on_close_request = on_close_request
        self._on_new_tab_request = on_new_tab_request

        self._tabs = []          # [{id, title, file_path, editor, frame, modified}]
        self._active_index = -1
        self._tab_id_counter = 0

        # Hit-testing state (rebuilt on each _redraw)
        self._tab_rects = []     # [(x1, y1, x2, y2, tab_index), ...]
        self._close_rects = []   # [(x1, y1, x2, y2, tab_index), ...]

        self._build_ui()

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self):
        # Tab bar area
        tab_bar_frame = ttk.Frame(self)
        tab_bar_frame.pack(fill=tk.X, side=tk.TOP)

        self.tab_canvas = tk.Canvas(
            tab_bar_frame, height=self.TAB_HEIGHT + 3,
            bg='#ececec', highlightthickness=0
        )
        self.tab_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.tab_canvas.bind('<Button-1>', self._on_canvas_click)
        self.tab_canvas.bind('<MouseWheel>', self._on_mousewheel)

        # Horizontal scrollbar (hidden until needed)
        self.tab_scrollbar = ttk.Scrollbar(
            tab_bar_frame, orient=tk.HORIZONTAL,
            command=self.tab_canvas.xview
        )
        self.tab_canvas.configure(xscrollcommand=self.tab_scrollbar.set)

        # Content area
        self.content_frame = ttk.Frame(self)
        self.content_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        # Separator line below tabs
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, side=tk.TOP)

    # ---- Public API --------------------------------------------------------

    def add_tab(self, title='Untitled', file_path=None, content=''):
        """Add a new tab and return its tab_id."""
        tab_id = self._tab_id_counter
        self._tab_id_counter += 1

        # Main tab frame
        tab_frame = ttk.Frame(self.content_frame)

        # Editor widget (idlelib-style: line numbers + text + syntax highlighting)
        editor = EditorWidget(tab_frame)
        editor.pack(fill=tk.BOTH, expand=True)

        if content:
            editor.set_text(content)
            editor.set_saved()

        tab = {
            'id': tab_id,
            'title': title,
            'file_path': file_path,
            'editor': editor,
            'frame': tab_frame,
        }
        self._tabs.append(tab)

        # Notify so caller can bind keyboard shortcuts
        if self._on_tab_created:
            self._on_tab_created(tab_id, editor)

        # Switch to the new tab
        self.switch_to_tab(len(self._tabs) - 1)
        return tab_id

    def close_tab(self, index):
        """Remove a tab by index. Returns True if closed, False if not found."""
        if index < 0 or index >= len(self._tabs):
            return False

        was_active = (index == self._active_index)
        tab = self._tabs[index]
        tab['frame'].destroy()
        self._tabs.pop(index)

        if self._tabs:
            if was_active:
                # Active tab was closed — pick the nearest remaining tab
                new_idx = max(0, min(index, len(self._tabs) - 1))
            else:
                # Non-active tab closed — keep the active tab active
                if index < self._active_index:
                    self._active_index -= 1  # shift down
                new_idx = self._active_index

            # Invalidate so switch_to_tab won't skip the re-pack
            self._active_index = -1
            self.switch_to_tab(new_idx)
        else:
            self._active_index = -1
            self._redraw()
            if self._on_tab_switch:
                self._on_tab_switch(None)
        return True

    def switch_to_tab(self, index):
        """Activate the tab at the given index."""
        if index < 0 or index >= len(self._tabs):
            return

        if self._active_index == index:
            return

        # Hide current tab
        if 0 <= self._active_index < len(self._tabs):
            self._tabs[self._active_index]['frame'].pack_forget()

        # Show new tab
        self._active_index = index
        tab = self._tabs[index]
        tab['frame'].pack(fill=tk.BOTH, expand=True)
        tab['editor'].focus_set()
        self._redraw()

        if self._on_tab_switch:
            self._on_tab_switch(tab['id'])

    def find_tab_by_path(self, file_path):
        """Return tab index for the given file path, or -1 if not found."""
        if file_path is None:
            return -1
        for i, tab in enumerate(self._tabs):
            if tab['file_path'] == file_path:
                return i
        return -1

    def get_tab_index(self, tab_id):
        """Return tab index for the given tab_id, or -1 if not found."""
        for i, tab in enumerate(self._tabs):
            if tab['id'] == tab_id:
                return i
        return -1

    def get_active_editor(self):
        """Return the EditorWidget of the active tab, or None."""
        if 0 <= self._active_index < len(self._tabs):
            return self._tabs[self._active_index]['editor']
        return None

    def get_active_tab(self):
        """Return the active tab dict, or None."""
        if 0 <= self._active_index < len(self._tabs):
            return self._tabs[self._active_index]
        return None

    def get_tab_by_id(self, tab_id):
        """Return the tab dict for tab_id, or None."""
        for tab in self._tabs:
            if tab['id'] == tab_id:
                return tab
        return None

    def set_tab_path(self, tab_id, file_path):
        """Update a tab's file path and title."""
        tab = self.get_tab_by_id(tab_id)
        if tab:
            tab['file_path'] = file_path
            tab['title'] = os.path.basename(file_path)
            self._redraw()

    def set_tab_title(self, tab_id, title):
        """Update a tab's display title."""
        tab = self.get_tab_by_id(tab_id)
        if tab:
            tab['title'] = title
            self._redraw()

    def set_tab_modified(self, tab_id=None, modified=None):
        """No-op — EditorWidget tracks its own modified state internally."""
        self._redraw()

    def get_tab_count(self):
        """Return the number of open tabs."""
        return len(self._tabs)

    def get_active_index(self):
        """Return the index of the currently active tab."""
        return self._active_index

    # ---- Internal helpers --------------------------------------------------

    def _redraw(self):
        """Redraw all tab headers on the canvas."""
        self.tab_canvas.delete('all')
        self._tab_rects = []
        self._close_rects = []

        # Use a reasonable default width if canvas not yet realized
        canvas_w = self.tab_canvas.winfo_width()
        if canvas_w < 10:
            canvas_w = 800

        x, y, h = 4, 1, self.TAB_HEIGHT

        for i, tab in enumerate(self._tabs):
            # Build display text
            modified = tab['editor'].is_modified()
            display = '• ' + tab['title'] if modified else tab['title']

            # Estimate text width (approx pixels at 11pt Helvetica)
            text_px = len(display) * 8
            close_w = self.CLOSE_SIZE + 6
            tab_w = max(self.MIN_TAB_WIDTH,
                        min(self.MAX_TAB_WIDTH, text_px + close_w + self.TAB_PAD_X * 2))

            is_active = (i == self._active_index)

            if is_active:
                # Active tab: lighter, slightly taller, bridges to content
                bg = '#f0f0f0'
                self.tab_canvas.create_rectangle(
                    x, y - 1, x + tab_w, y + h + 2,
                    fill=bg, outline='#b0b0b0', width=1
                )
            else:
                bg = '#d8d8d8'
                self.tab_canvas.create_rectangle(
                    x, y, x + tab_w, y + h,
                    fill=bg, outline='#b0b0b0', width=1
                )

            # Tab title text
            text_x = x + self.TAB_PAD_X
            text_y = y + h // 2 + 1
            self.tab_canvas.create_text(
                text_x, text_y, text=display, anchor='w',
                font=('Helvetica', 11),
                fill='#222222' if is_active else '#444444'
            )

            # Store tab hit rect (with tab_id, not index)
            self._tab_rects.append((x, y, x + tab_w, y + h, tab['id']))

            # Close button (×)
            cx = x + tab_w - self.CLOSE_SIZE // 2 - 4
            cy = y + h // 2 + 1
            self.tab_canvas.create_text(
                cx, cy, text='×',
                font=('Helvetica', 13),
                fill='#666666', anchor='center'
            )
            self._close_rects.append((
                cx - self.CLOSE_SIZE // 2 - 1, y + 2,
                cx + self.CLOSE_SIZE // 2 + 1, y + h - 2, tab['id']
            ))

            x += tab_w + 2

        # Update scroll region
        total_w = x + 4
        self.tab_canvas.configure(scrollregion=(0, 0, total_w, h + 4))

        # Show/hide scrollbar
        if total_w > canvas_w + 4:
            self.tab_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        else:
            self.tab_scrollbar.pack_forget()

    def _on_canvas_click(self, event):
        """Handle mouse clicks on the tab canvas."""
        x = self.tab_canvas.canvasx(event.x)
        y = event.y

        # Check close buttons first (higher priority)
        for rx1, ry1, rx2, ry2, tab_id in reversed(self._close_rects):
            if rx1 <= x <= rx2 and ry1 <= y <= ry2:
                if self._on_close_request:
                    self._on_close_request(tab_id)
                return

        # Check tab rectangles for selection
        for rx1, ry1, rx2, ry2, tab_id in reversed(self._tab_rects):
            if rx1 <= x <= rx2 and ry1 <= y <= ry2:
                tab_idx = self.get_tab_index(tab_id)
                if tab_idx >= 0 and tab_idx != self._active_index:
                    self.switch_to_tab(tab_idx)
                return

    def _on_mousewheel(self, event):
        """Horizontal scrolling via mousewheel / trackpad (macOS)."""
        self.tab_canvas.xview_scroll(int(-event.delta / 30), 'units')


class _WidgetWriter:
    """Redirects writes to a tkinter Text widget (thread-safe via after)."""

    def __init__(self, widget, panel):
        self.widget = widget
        self.panel = panel

    def write(self, s):
        if s:
            self.panel.after(0, lambda s=s: self._write(s))

    def _write(self, s):
        try:
            self.widget.configure(state=tk.NORMAL)
            self.widget.insert(tk.END, s)
            self.widget.see(tk.END)
            self.widget.configure(state=tk.DISABLED)
        except tk.TclError:
            pass  # widget destroyed

    def flush(self):
        pass


class TerminalPanel(ttk.Frame):
    """Bottom panel with Python IDLE Shell and system Terminal tabs."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._python_console = None
        self._python_locals = {}
        self._python_history = []
        self._python_history_pos = 0
        self._python_multiline = []
        self._shell_proc = None
        self._shell_master_fd = None
        self._reader_thread = None
        self._reader_stop = threading.Event()
        self._active_tab = 'python'
        self._close_callback = None
        self._build_ui()
        self._start_python_shell()
        self._start_shell()

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self):
        self.configure(height=200)

        # Tab bar
        self._tab_bar = ttk.Frame(self)
        self._tab_bar.pack(fill=tk.X, side=tk.TOP)

        self._python_btn = ttk.Button(
            self._tab_bar, text='Python', width=10,
            command=lambda: self._switch_tab('python'))
        self._python_btn.pack(side=tk.LEFT, padx=(4, 1), pady=(2, 0))

        self._shell_btn = ttk.Button(
            self._tab_bar, text='Terminal', width=10,
            command=lambda: self._switch_tab('shell'))
        self._shell_btn.pack(side=tk.LEFT, padx=1, pady=(2, 0))

        # Close button
        close_btn = ttk.Button(
            self._tab_bar, text='×', width=2,
            command=self._on_close)
        close_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=(2, 0))

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, side=tk.TOP)

        # Content area (stacks tab frames)
        self._content = ttk.Frame(self)
        self._content.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        # ---- Python tab ----
        self._python_frame = ttk.Frame(self._content)
        self._python_frame.grid_rowconfigure(0, weight=1)
        self._python_frame.grid_columnconfigure(1, weight=1)

        self._python_output = tk.Text(
            self._python_frame, state=tk.DISABLED,
            bg='#1e1e1e', fg='#d4d4d4', insertbackground='white',
            font=('Monaco', 11), wrap=tk.WORD)
        py_scroll = ttk.Scrollbar(self._python_frame,
                                  command=self._python_output.yview)
        self._python_output.configure(yscrollcommand=py_scroll.set)
        self._python_output.grid(row=0, column=0, columnspan=2, sticky='nsew')
        py_scroll.grid(row=0, column=2, sticky='ns')
        self._python_output.bind('<Key>', lambda e: 'break')  # block typing in output

        # Input row
        ttk.Label(self._python_frame, text='>>>',
                  font=('Monaco', 11, 'bold'),
                  foreground='#569cd6').grid(
            row=1, column=0, sticky='w', padx=(4, 2), pady=(2, 4))
        self._python_input = ttk.Entry(self._python_frame, font=('Monaco', 11))
        self._python_input.grid(row=1, column=1, sticky='ew', padx=(0, 4), pady=(2, 4))
        self._python_input.bind('<Return>', lambda e: self._send_python_input())
        self._python_input.bind('<Up>', self._python_history_up)
        self._python_input.bind('<Down>', self._python_history_down)

        # ---- Shell tab ----
        self._shell_frame = ttk.Frame(self._content)
        self._shell_frame.grid_rowconfigure(0, weight=1)
        self._shell_frame.grid_columnconfigure(1, weight=1)

        self._shell_output = tk.Text(
            self._shell_frame, state=tk.DISABLED,
            bg='#1e1e1e', fg='#d4d4d4', insertbackground='white',
            font=('Monaco', 11), wrap=tk.WORD)
        sh_scroll = ttk.Scrollbar(self._shell_frame,
                                  command=self._shell_output.yview)
        self._shell_output.configure(yscrollcommand=sh_scroll.set)
        self._shell_output.grid(row=0, column=0, columnspan=2, sticky='nsew')
        sh_scroll.grid(row=0, column=2, sticky='ns')
        self._shell_output.bind('<Key>', lambda e: 'break')

        ttk.Label(self._shell_frame, text='$',
                  font=('Monaco', 11, 'bold'),
                  foreground='#569cd6').grid(
            row=1, column=0, sticky='w', padx=(4, 2), pady=(2, 4))
        self._shell_input = ttk.Entry(self._shell_frame, font=('Monaco', 11))
        self._shell_input.grid(row=1, column=1, sticky='ew', padx=(0, 4), pady=(2, 4))
        self._shell_input.bind('<Return>', lambda e: self._send_shell_input())

        # Show Python tab by default
        self._switch_tab('python')

    # ---- Tab switching -----------------------------------------------------

    def set_close_callback(self, callback):
        """Called when the close (×) button is clicked."""
        self._close_callback = callback

    def _on_close(self):
        if self._close_callback:
            self._close_callback()

    def _switch_tab(self, tab):
        self._active_tab = tab
        if tab == 'python':
            self._shell_frame.pack_forget()
            self._python_frame.pack(fill=tk.BOTH, expand=True)
            self._python_input.focus_set()
        else:
            self._python_frame.pack_forget()
            self._shell_frame.pack(fill=tk.BOTH, expand=True)
            self._shell_input.focus_set()

    # ---- Python IDLE Shell ------------------------------------------------

    def _start_python_shell(self):
        self._python_console = code.InteractiveConsole(locals=self._python_locals)
        version = sys.version.split()[0]
        self._append_python_output(
            f'Python {version} Interactive Shell\n'
            f'Type "help", "copyright", "credits" or "license" for more.\n'
            f'>>> ')

    def _append_python_output(self, text):
        try:
            self._python_output.configure(state=tk.NORMAL)
            self._python_output.insert(tk.END, text)
            self._python_output.see(tk.END)
            self._python_output.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _send_python_input(self):
        code_text = self._python_input.get()
        self._python_input.delete(0, tk.END)

        if self._python_multiline:
            # Continuing a multi-line block
            self._python_history.append(code_text)
            self._python_history_pos = len(self._python_history)

            if code_text.strip() == '':
                # Empty line → execute the block
                block = '\n'.join(self._python_multiline)
                self._python_multiline = []
                self._append_python_output('\n')
                self._execute_python(block)
                self._append_python_output('>>> ')
            else:
                self._python_multiline.append(code_text)
                self._append_python_output(f'... {code_text}\n')
            return

        if not code_text.strip():
            self._append_python_output('>>> ')
            return

        self._python_history.append(code_text)
        self._python_history_pos = len(self._python_history)
        self._append_python_output(code_text + '\n')

        # Test if this input starts a multi-line block
        try:
            compiled = code.compile_command(code_text, '<stdin>', 'single')
        except (SyntaxError, OverflowError, ValueError):
            # Show the error and continue
            self._execute_python(code_text)
            self._append_python_output('>>> ')
            return

        if compiled is None:
            # Incomplete — start multi-line collection
            self._python_multiline = [code_text]
            self._append_python_output('... ')
        else:
            self._execute_python(code_text)
            self._append_python_output('>>> ')

    def _execute_python(self, code_text):
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = _WidgetWriter(self._python_output, self)
        sys.stderr = _WidgetWriter(self._python_output, self)
        try:
            self._python_console.push(code_text)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def _python_history_up(self, event):
        if self._python_history and not self._python_multiline:
            self._python_history_pos = max(0, self._python_history_pos - 1)
            self._python_input.delete(0, tk.END)
            self._python_input.insert(
                0, self._python_history[self._python_history_pos])

    def _python_history_down(self, event):
        if self._python_history and not self._python_multiline:
            self._python_history_pos = min(
                len(self._python_history), self._python_history_pos + 1)
            self._python_input.delete(0, tk.END)
            if self._python_history_pos < len(self._python_history):
                self._python_input.insert(
                    0, self._python_history[self._python_history_pos])

    # ---- System Shell / Terminal ------------------------------------------

    def _start_shell(self):
        shell = os.environ.get('SHELL', '/bin/zsh')
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError:
            self._append_shell_output('[Error: could not open PTY]\n')
            return

        # Set terminal size
        try:
            winsize = struct.pack('HHHH', 24, 80, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

        try:
            self._shell_proc = subprocess.Popen(
                [shell, '-i'],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid,
                env={**os.environ, 'TERM': 'xterm-256color'},
                close_fds=True,
            )
        except Exception as e:
            self._append_shell_output(f'[Error: {e}]\n')
            os.close(master_fd)
            os.close(slave_fd)
            return

        os.close(slave_fd)
        self._shell_master_fd = master_fd

        # Non-blocking reads
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self._reader_stop.clear()
        self._reader_thread = threading.Thread(
            target=self._read_pty_output, daemon=True)
        self._reader_thread.start()

    def _append_shell_output(self, text):
        try:
            self._shell_output.configure(state=tk.NORMAL)
            self._shell_output.insert(tk.END, text)
            self._shell_output.see(tk.END)
            self._shell_output.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _read_pty_output(self):
        fd = self._shell_master_fd
        while not self._reader_stop.is_set() and fd is not None:
            try:
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    data = os.read(fd, 4096)
                    if not data:
                        break
                    decoded = data.decode('utf-8', errors='replace')
                    self.after(0, lambda d=decoded: self._append_shell_output(d))
            except OSError:
                break
        # Subprocess exited
        if not self._reader_stop.is_set():
            self.after(0, self._handle_shell_exit)

    def _send_shell_input(self):
        text = self._shell_input.get()
        self._shell_input.delete(0, tk.END)
        fd = self._shell_master_fd
        if fd is not None and text:
            try:
                os.write(fd, (text + '\n').encode('utf-8'))
            except OSError:
                self._append_shell_output('[Error: cannot send input]\n')

    def _handle_shell_exit(self):
        self._append_shell_output('\n[Process exited — restarting...]\n')
        if self._shell_master_fd is not None:
            try:
                os.close(self._shell_master_fd)
            except OSError:
                pass
            self._shell_master_fd = None
        self._shell_proc = None
        # Auto-restart after a brief delay
        self.after(500, self._start_shell)

    # ---- Lifecycle ---------------------------------------------------------

    def focus_input(self):
        """Focus the input field of the active tab."""
        if self._active_tab == 'python':
            self._python_input.focus_set()
        else:
            self._shell_input.focus_set()

    def cleanup(self):
        """Kill subprocess and stop reader thread."""
        self._reader_stop.set()
        # Wake up select() by closing the fd
        fd = self._shell_master_fd
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            self._shell_master_fd = None
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        if self._shell_proc and self._shell_proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._shell_proc.pid), signal.SIGTERM)
                self._shell_proc.wait(timeout=2)
            except Exception:
                try:
                    self._shell_proc.kill()
                except Exception:
                    pass


class MarkdownEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("Markdown Editor")
        self.root.geometry("1200x700")

        self.current_folder = None
        self.setup_ui()
        self.root.protocol('WM_DELETE_WINDOW', self._on_window_close)

    # ---- Editor property (delegates to active tab) ------------------------

    @property
    def editor(self):
        """Return the active tab's editor widget."""
        return self.tabbed_editor.get_active_editor()

    # ---- UI setup ----------------------------------------------------------

    def setup_ui(self):
        # -- Menubar --
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="New", command=self.new_file, accelerator="Cmd+N")
        file_menu.add_command(label="Open", command=self.open_file, accelerator="Cmd+O")
        file_menu.add_command(label="Open Folder", command=self.open_folder, accelerator="Cmd+Shift+O")
        file_menu.add_command(label="Save", command=self.save_file, accelerator="Cmd+S")
        file_menu.add_command(label="Save As", command=self.save_file_as, accelerator="Cmd+Shift+S")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)

        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(label="Undo", command=self.undo, accelerator="Cmd+Z")
        edit_menu.add_command(label="Redo", command=self.redo, accelerator="Cmd+Shift+Z")
        edit_menu.add_separator()
        edit_menu.add_command(label="Cut", command=self.cut, accelerator="Cmd+X")
        edit_menu.add_command(label="Copy", command=self.copy, accelerator="Cmd+C")
        edit_menu.add_command(label="Paste", command=self.paste, accelerator="Cmd+V")
        edit_menu.add_command(label="Select All", command=self.select_all, accelerator="Cmd+A")
        edit_menu.add_separator()
        edit_menu.add_command(label="Bold", command=lambda: self.insert_format("**", "**"), accelerator="Cmd+B")
        edit_menu.add_command(label="Italic", command=lambda: self.insert_format("*", "*"), accelerator="Cmd+I")
        edit_menu.add_command(label="Heading", command=self.insert_heading, accelerator="Cmd+H")
        edit_menu.add_command(label="Link", command=self.insert_link, accelerator="Cmd+K")
        edit_menu.add_command(label="Code", command=lambda: self.insert_format("`", "`"), accelerator="Cmd+Shift+C")

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Toggle Terminal", command=self.toggle_terminal, accelerator="Cmd+J")

        search_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Search", menu=search_menu)
        search_menu.add_command(label="Find in File", command=self.show_find_dialog, accelerator="Cmd+F")
        search_menu.add_command(label="Search in Files", command=self.show_search_dialog, accelerator="Cmd+Shift+F")

        # -- Editor context menu (right-click) --
        self.editor_context_menu = tk.Menu(self.root, tearoff=0)
        self.editor_context_menu.add_command(label="Cut", command=self.cut)
        self.editor_context_menu.add_command(label="Copy", command=self.copy)
        self.editor_context_menu.add_command(label="Paste", command=self.paste)
        self.editor_context_menu.add_separator()
        self.editor_context_menu.add_command(label="Select All", command=self.select_all)

        # -- Main layout --
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ---- Activity Bar (far left) ----
        self.activity_bar = tk.Canvas(
            main_frame, width=48, bg='#ececec', highlightthickness=0
        )
        self.activity_bar.pack(side=tk.LEFT, fill=tk.Y)

        self._activity_items = {}
        # Files button
        fid = self.activity_bar.create_text(
            24, 16, text='📁', font=('Helvetica', 18),
            anchor='n', fill='#ffffff'
        )
        self._activity_items['files'] = fid
        self.activity_bar.tag_bind(fid, '<Button-1>', lambda e: self._switch_activity('files'))

        # Git button
        gid = self.activity_bar.create_text(
            24, 56, text='🔀', font=('Helvetica', 18),
            anchor='n', fill='#888888'
        )
        self._activity_items['git'] = gid
        self.activity_bar.tag_bind(gid, '<Button-1>', lambda e: self._switch_activity('git'))

        self._active_activity = 'files'

        # ---- PanedWindow: sidebar + editor ----
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ---- Sidebar ----
        sidebar_frame = ttk.Frame(paned)
        editor_paned = ttk.PanedWindow(paned, orient=tk.VERTICAL)

        paned.add(sidebar_frame, weight=0)
        paned.add(editor_paned, weight=1)
        self.editor_paned = editor_paned

        # -- File tree panel (inside sidebar) --
        self.file_tree_frame = ttk.Frame(sidebar_frame)

        tree_header = ttk.Frame(self.file_tree_frame)
        tree_header.pack(fill=tk.X)
        ttk.Label(tree_header, text="Files", font=("Helvetica", 10, "bold")).pack(side=tk.LEFT, padx=(0, 5))

        btn_frame = ttk.Frame(tree_header)
        btn_frame.pack(side=tk.RIGHT)

        new_btn = ttk.Button(btn_frame, text="+", width=1, command=self.show_new_menu)
        new_btn.pack(side=tk.LEFT, padx=1)

        refresh_btn = ttk.Button(btn_frame, text="⟳", width=1, command=self.refresh_file_tree)
        refresh_btn.pack(side=tk.LEFT, padx=1)

        tree_scroll = ttk.Scrollbar(self.file_tree_frame)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.file_tree = ttk.Treeview(self.file_tree_frame, yscrollcommand=tree_scroll.set, selectmode="browse")
        self.file_tree.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self.file_tree.yview)
        self.file_tree.bind("<Double-1>", self.on_tree_double_click)
        self.file_tree.bind("<Button-2>", self.show_context_menu)
        self.file_tree.bind("<Button-3>", self.show_context_menu)
        self.file_tree.bind("<Control-Button-1>", self.show_context_menu)

        # File tree context menu
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="New File", command=self.new_file_in_tree)
        self.context_menu.add_command(label="New Folder", command=self.new_folder_in_tree)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Rename", command=self.rename_selected)
        self.context_menu.add_command(label="Move", command=self.move_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Delete", command=self.delete_selected)

        # -- Git panel (inside sidebar) --
        self.git_panel_frame = ttk.Frame(sidebar_frame)

        git_header = ttk.Frame(self.git_panel_frame)
        git_header.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(git_header, text="Source Control", font=("Helvetica", 10, "bold")).pack(side=tk.LEFT)

        self.git_branch_label = ttk.Label(
            self.git_panel_frame, text="", font=("Helvetica", 10)
        )
        self.git_branch_label.pack(anchor="w", padx=10, pady=(5, 0))

        git_status_frame = ttk.Frame(self.git_panel_frame)
        git_status_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        git_status_scroll = ttk.Scrollbar(git_status_frame)
        git_status_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.git_status_text = tk.Text(
            git_status_frame, height=10, font=("Monaco", 10),
            yscrollcommand=git_status_scroll.set, state=tk.DISABLED,
            cursor='hand2'
        )
        self.git_status_text.pack(fill=tk.BOTH, expand=True)
        self.git_status_text.bind('<Double-Button-1>', self._on_git_status_double_click)
        self.git_status_text.bind('<Button-2>', self._on_git_status_right_click)
        self.git_status_text.bind('<Button-3>', self._on_git_status_right_click)
        self.git_status_text.bind('<Control-Button-1>', self._on_git_status_right_click)
        git_status_scroll.config(command=self.git_status_text.yview)

        # Git status context menu
        self._git_context_menu = tk.Menu(self.root, tearoff=0)
        self._git_context_menu.add_command(label="Rollback", command=self._git_rollback)
        self._git_context_menu.add_command(label="Open File", command=self._git_open_selected)
        self._git_context_menu.add_command(label="Show Diff", command=self._git_show_diff)
        self._git_right_clicked_file = None

        git_btn_frame = ttk.Frame(self.git_panel_frame)
        git_btn_frame.pack(fill=tk.X, padx=5, pady=5)
        # Store button references for show/hide control
        self._git_btns = {}
        # Row 0: repository setup
        self._git_btns['clone'] = ttk.Button(git_btn_frame, text="Clone", command=self.git_clone)
        self._git_btns['clone'].grid(row=0, column=0, padx=1, pady=1, sticky='ew')
        self._git_btns['init'] = ttk.Button(git_btn_frame, text="Init", command=self.git_init)
        self._git_btns['init'].grid(row=0, column=1, padx=1, pady=1, sticky='ew')
        # Row 1: remote + commit
        self._git_btns['remote'] = ttk.Button(git_btn_frame, text="Remote", command=self.git_set_remote)
        self._git_btns['remote'].grid(row=1, column=0, padx=1, pady=1, sticky='ew')
        self._git_btns['commit'] = ttk.Button(git_btn_frame, text="Commit", command=self.git_commit)
        self._git_btns['commit'].grid(row=1, column=1, padx=1, pady=1, sticky='ew')
        # Row 2: push + pull
        self._git_btns['push'] = ttk.Button(git_btn_frame, text="Push", command=self.git_push)
        self._git_btns['push'].grid(row=2, column=0, padx=1, pady=1, sticky='ew')
        self._git_btns['pull'] = ttk.Button(git_btn_frame, text="Pull", command=self.git_pull)
        self._git_btns['pull'].grid(row=2, column=1, padx=1, pady=1, sticky='ew')
        # Row 3: log + refresh
        self._git_btns['log'] = ttk.Button(git_btn_frame, text="Log", command=self.show_git_log)
        self._git_btns['log'].grid(row=3, column=0, padx=1, pady=1, sticky='ew')
        self._git_btns['refresh'] = ttk.Button(git_btn_frame, text="Refresh", command=self._refresh_git_panel)
        self._git_btns['refresh'].grid(row=3, column=1, padx=1, pady=1, sticky='ew')
        git_btn_frame.columnconfigure(0, weight=1)
        git_btn_frame.columnconfigure(1, weight=1)
        self._update_git_buttons()

        # Show file tree by default
        self.file_tree_frame.pack(fill=tk.BOTH, expand=True)

        # -- Tabbed editor --
        self.tabbed_editor = TabbedEditor(
            editor_paned,
            on_tab_created=self._bind_editor_shortcuts,
            on_tab_switch=self._on_tab_switch,
            on_close_request=self._on_close_request,
            on_new_tab_request=self.new_file,
        )
        editor_paned.add(self.tabbed_editor, weight=3)

        # -- Terminal panel (hidden by default) --
        self.terminal_panel = TerminalPanel(editor_paned)
        self.terminal_panel.set_close_callback(self.toggle_terminal)
        self._terminal_visible = False

        # -- Status bar --
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=8)

        self.status_bar = ttk.Label(
            status_frame, text="Ready", relief=tk.SUNKEN, anchor='w')
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._terminal_status = ttk.Label(
            status_frame, text='⬆ Terminal', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._terminal_status.pack(side=tk.RIGHT)
        self._terminal_status.bind(
            '<Button-1>', lambda e: self.toggle_terminal())
        # Update status bar widget references
        self._status_frame = status_frame

    # ---- Shortcut binding for new editors ----------------------------------

    def _bind_editor_shortcuts(self, tab_id, editor):
        """Bind all keyboard shortcuts + events to a newly created editor."""
        editor.bind("<Command-n>", lambda _: self.new_file())
        editor.bind("<Command-o>", lambda _: self.open_file())
        editor.bind("<Command-s>", lambda _: self.save_file())
        editor.bind("<Command-Shift-S>", lambda _: self.save_file_as())
        editor.bind("<Command-b>", lambda _: self.insert_format("**", "**"))
        editor.bind("<Command-i>", lambda _: self.insert_format("*", "*"))
        editor.bind("<Command-h>", lambda _: self.insert_heading())
        editor.bind("<Command-k>", lambda _: self.insert_link())
        editor.bind("<Command-j>", lambda _: self.toggle_terminal())
        editor.bind("<Command-Shift-c>", lambda _: self.insert_format("`", "`"))
        editor.bind("<Command-f>", lambda _: self.show_find_dialog())
        editor.bind("<Command-Shift-f>", lambda _: self.show_search_dialog())
        editor.bind("<Command-a>", lambda _: self.select_all())
        editor.bind("<Command-z>", lambda _: self.undo())
        editor.bind("<Command-Shift-Z>", lambda _: self.redo())
        editor.bind("<Button-2>", self.show_editor_context_menu)
        editor.bind("<Button-3>", self.show_editor_context_menu)
        editor.bind("<Control-Button-1>", self.show_editor_context_menu)

    # ---- Terminal panel ----------------------------------------------------

    def toggle_terminal(self):
        """Show or hide the terminal panel."""
        if self._terminal_visible:
            self.editor_paned.forget(self.terminal_panel)
            self._terminal_visible = False
            self._terminal_status.configure(text='⬆ Terminal')
            self.status_bar.configure(text='Terminal hidden')
        else:
            self.editor_paned.add(self.terminal_panel, weight=1)
            self._terminal_visible = True
            self._terminal_status.configure(text='⬇ Terminal')
            self.status_bar.configure(text='Terminal shown')
            self.terminal_panel.focus_input()

    def _on_window_close(self):
        """Clean up subprocesses before closing."""
        self.terminal_panel.cleanup()
        self.root.destroy()

    # ---- Tab event callbacks -----------------------------------------------

    def _on_tab_switch(self, tab_id):
        """Called when the active tab changes."""
        tab = self.tabbed_editor.get_active_tab()
        if tab and tab['file_path']:
            base = os.path.basename(tab['file_path'])
            self.status_bar.config(text=f"Editing: {base}")
        else:
            self.status_bar.config(text="New file")

    def _on_close_request(self, tab_id):
        """Called when the user clicks the close button on a tab."""
        tab = self.tabbed_editor.get_tab_by_id(tab_id)
        if not tab:
            return

        if tab['editor'].is_modified():
            title = tab['title']
            result = messagebox.askyesnocancel(
                "Unsaved Changes",
                f"'{title}' has unsaved changes.\n\nSave before closing?",
                parent=self.root
            )
            if result is None:   # Cancel
                return
            elif result:          # Yes — save
                if tab['file_path']:
                    try:
                        content = tab['editor'].get_text()
                        with open(tab['file_path'], 'w', encoding='utf-8') as f:
                            f.write(content)
                        tab['editor'].set_saved()
                    except Exception as e:
                        self.status_bar.config(text=f"Error saving: {e}")
                        return
                else:
                    # No path — switch to tab and do Save As
                    idx = self.tabbed_editor.get_tab_index(tab_id)
                    if idx >= 0:
                        self.tabbed_editor.switch_to_tab(idx)
                        if not self.save_file_as():
                            return  # user cancelled Save As

        self.tabbed_editor.close_tab(self.tabbed_editor.get_tab_index(tab_id))

    # ---- Activity bar / Sidebar switching ------------------------------------

    def _switch_activity(self, activity):
        """Switch the sidebar between 'files' and 'git' views."""
        self._active_activity = activity
        # Update activity bar button colors
        for name, item_id in self._activity_items.items():
            self.activity_bar.itemconfig(
                item_id, fill='#ffffff' if name == activity else '#888888'
            )
        # Swap sidebar content
        if activity == 'files':
            self.git_panel_frame.pack_forget()
            self.file_tree_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self.file_tree_frame.pack_forget()
            self.git_panel_frame.pack(fill=tk.BOTH, expand=True)
            self._refresh_git_panel()

    def _refresh_git_panel(self):
        """Update the git panel with current branch and status."""
        if not self.current_folder:
            self.git_branch_label.config(text="No folder opened")
            self.git_status_text.config(state=tk.NORMAL)
            self.git_status_text.delete(1.0, tk.END)
            self.git_status_text.insert(tk.END, "Open a folder to see git status.")
            self.git_status_text.config(state=tk.DISABLED)
            self._update_git_buttons()
            return

        _, branch, _ = self.run_git_command(['branch', '--show-current'])
        branch = branch.strip() if branch else 'unknown'
        self.git_branch_label.config(text=f"Branch: {branch}")

        _, status, _ = self.run_git_command(['status', '--short'])
        self.git_status_text.config(state=tk.NORMAL)
        self.git_status_text.delete(1.0, tk.END)
        if status.strip():
            self.git_status_text.insert(tk.END, status)
        else:
            self.git_status_text.insert(tk.END, "Working tree clean")
        self.git_status_text.config(state=tk.DISABLED)
        self._update_git_buttons()

    def _parse_git_status_line(self, event):
        """Extract (rel_path, file_path) from a click on the git status text."""
        if not self.current_folder:
            return None, None
        idx = self.git_status_text.index(f'@{event.x},{event.y}')
        line_text = self.git_status_text.get(f'{idx} linestart', f'{idx} lineend')
        if not line_text.strip() or len(line_text) <= 3 or '->' in line_text:
            return None, None
        rel_path = line_text[3:].strip()
        return rel_path, os.path.join(self.current_folder, rel_path)

    def _on_git_status_double_click(self, event):
        """Show git diff for a file double-clicked in the git status panel."""
        rel_path, file_path = self._parse_git_status_line(event)
        if rel_path is None:
            return
        self._show_diff_for(rel_path)

    def _on_git_status_right_click(self, event):
        """Show context menu on right-click in git status panel."""
        rel_path, file_path = self._parse_git_status_line(event)
        self._git_right_clicked_file = (rel_path, file_path)
        try:
            self._git_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._git_context_menu.grab_release()

    def _git_rollback(self):
        """Discard changes to the selected file (git checkout -- <file>)."""
        info = self._git_right_clicked_file
        if not info or not info[0] or not info[1]:
            return
        rel_path, file_path = info
        name = os.path.basename(rel_path)
        confirm = messagebox.askyesno(
            "Rollback Changes",
            f"Discard all changes to '{name}'?\n\nThis cannot be undone.",
            parent=self.root
        )
        if confirm:
            rc, _, stderr = self.run_git_command(['checkout', '--', rel_path])
            if rc == 0:
                self.status_bar.config(text=f"Rolled back: {name}")
                self._refresh_git_panel()
                # Update any open tab of this file with reverted content
                tab_idx = self.tabbed_editor.find_tab_by_path(file_path)
                if tab_idx >= 0:
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            new_content = f.read()
                        tab = self.tabbed_editor._tabs[tab_idx]
                        editor = tab['editor']
                        editor.set_text(new_content)
                        editor.set_saved()
                    except Exception as e:
                        self.status_bar.config(text=f"Error reloading file: {e}")
                # Close diff tab for this file
                diff_title = f'Diff: {name}'
                for i, tab in enumerate(self.tabbed_editor._tabs):
                    if tab['title'] == diff_title:
                        self.tabbed_editor.close_tab(i)
                        break
            else:
                self.status_bar.config(text=f"Rollback failed: {stderr.strip()}")

    def _git_open_selected(self):
        """Open the right-clicked file in an editor tab."""
        info = self._git_right_clicked_file
        if info and info[0]:
            rel_path, file_path = info
            if os.path.isfile(file_path):
                self._open_file_in_tab(file_path)

    def _git_show_diff(self):
        """Show git diff for the right-clicked file."""
        info = self._git_right_clicked_file
        if info and info[0]:
            self._show_diff_for(info[0])

    def _show_diff_for(self, rel_path):
        """Create a tab showing git diff for the given relative path."""
        _, diff, _ = self.run_git_command(['diff', '--', rel_path])
        if not diff.strip():
            _, diff, _ = self.run_git_command(['diff', '--cached', '--', rel_path])

        title = f'Diff: {os.path.basename(rel_path)}'
        content = diff if diff.strip() else '(no changes)'
        # Check if a diff tab already exists for this file
        for i, tab in enumerate(self.tabbed_editor._tabs):
            if tab['title'] == title:
                self.tabbed_editor.switch_to_tab(i)
                # Update content
                tab['editor'].set_text(content)
                tab['editor'].set_saved()
                return
        self.tabbed_editor.add_tab(title=title, content=content)

    def _update_git_buttons(self):
        """Show/hide git buttons based on current state."""
        folder_open = self.current_folder is not None
        has_git = False
        if folder_open:
            has_git = os.path.isdir(os.path.join(self.current_folder, '.git'))

        # Clone: only visible when no folder is open
        if folder_open:
            self._git_btns['clone'].grid_remove()
        else:
            self._git_btns['clone'].grid()

        # Init: visible only when folder is open AND no .git exists
        if folder_open and not has_git:
            self._git_btns['init'].grid()
        else:
            self._git_btns['init'].grid_remove()

        # All other buttons: visible only when folder is open
        for name in ('remote', 'commit', 'push', 'pull', 'log', 'refresh'):
            if folder_open:
                self._git_btns[name].grid()
            else:
                self._git_btns[name].grid_remove()

    # ---- File opening helpers ----------------------------------------------

    def _open_file_in_tab(self, file_path):
        """Open a file: switch to its tab if already open, otherwise create one."""
        existing = self.tabbed_editor.find_tab_by_path(file_path)
        if existing >= 0:
            self.tabbed_editor.switch_to_tab(existing)
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            self.status_bar.config(text=f"Error opening file: {e}")
            return

        self.tabbed_editor.add_tab(
            title=os.path.basename(file_path),
            file_path=file_path,
            content=content,
        )
        self.status_bar.config(text=f"Opened: {os.path.basename(file_path)}")

    # ---- Save helpers ------------------------------------------------------

    def _save_to_tab(self, tab):
        """Write a tab's editor content to its file_path."""
        try:
            content = tab['editor'].get_text()
            with open(tab['file_path'], 'w', encoding='utf-8') as f:
                f.write(content)
            tab['editor'].set_saved()
            self.status_bar.config(text=f"Saved: {os.path.basename(tab['file_path'])}")
            return True
        except Exception as e:
            self.status_bar.config(text=f"Error saving file: {e}")
            return False

    # ---- File operations ---------------------------------------------------

    def new_file(self):
        """Create a new tab, asking for a filename."""
        filename = simpledialog.askstring(
            "New File", "Enter file name:",
            parent=self.root
        )
        if not filename:
            filename = 'Untitled'
        self.tabbed_editor.add_tab(title=filename, content='')
        self.status_bar.config(text="New file")

    def open_file(self):
        file_path = filedialog.askopenfilename(
            title="Open File",
            filetypes=[("All files", "*.*")]
        )
        if file_path:
            self._open_file_in_tab(file_path)

    def open_folder(self):
        folder_path = filedialog.askdirectory(title="Open Folder", initialdir=os.path.expanduser('~'))
        if folder_path:
            self.current_folder = folder_path
            self.populate_file_tree(folder_path)
            self.status_bar.config(text=f"Opened folder: {os.path.basename(folder_path)}")
            self._update_git_buttons()

    def populate_file_tree(self, folder_path):
        self.file_tree.delete(*self.file_tree.get_children())

        def add_tree_items(path, parent_id):
            try:
                items = sorted(os.listdir(path), key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
                for item in items:
                    full_path = os.path.join(path, item)
                    if os.path.isdir(full_path):
                        if item.startswith('.'):
                            continue
                        node_id = self.file_tree.insert(parent_id, "end", text=f"📁 {item}", values=(full_path,), tags=("dir",), open=False)
                        add_tree_items(full_path, node_id)
                    else:
                        self.file_tree.insert(parent_id, "end", text=f"📄 {item}", values=(full_path,), tags=("file",))
            except PermissionError:
                pass

        add_tree_items(folder_path, "")

    def refresh_file_tree(self):
        if self.current_folder:
            self.populate_file_tree(self.current_folder)
            self.status_bar.config(text="File tree refreshed")
        else:
            self.status_bar.config(text="No folder opened")

    def on_tree_double_click(self, _):
        selection = self.file_tree.selection()
        if selection:
            item = selection[0]
            values = self.file_tree.item(item, "values")
            if values:
                file_path = values[0]
                if os.path.isfile(file_path):
                    self._open_file_in_tab(file_path)

    def save_file(self):
        tab = self.tabbed_editor.get_active_tab()
        if not tab:
            return False
        if tab['file_path']:
            return self._save_to_tab(tab)
        else:
            return self.save_file_as()

    def save_file_as(self):
        tab = self.tabbed_editor.get_active_tab()
        if not tab:
            return False
        file_path = filedialog.asksaveasfilename(
            title="Save File",
            filetypes=[("All files", "*.*")]
        )
        if file_path:
            self.tabbed_editor.set_tab_path(tab['id'], file_path)
            return self._save_to_tab(tab)
        return False

    # ---- Text formatting ---------------------------------------------------

    def insert_format(self, before, after):
        editor = self.editor
        if editor is None:
            return
        try:
            sel_start = editor.index(tk.SEL_FIRST)
            sel_end = editor.index(tk.SEL_LAST)
            selected = editor.get(sel_start, sel_end)
            editor.delete(sel_start, sel_end)
            editor.insert(sel_start, f"{before}{selected}{after}")
        except:
            editor.insert(tk.INSERT, f"{before}{after}")

    def insert_heading(self):
        editor = self.editor
        if editor is None:
            return
        line_start = editor.index("insert linestart")
        line = editor.get(line_start, line_start + " lineend")
        if line.startswith("# "):
            editor.delete(line_start, line_start + "+2 chars")
            editor.insert(line_start, "## ")
        elif line.startswith("## "):
            editor.delete(line_start, line_start + "+3 chars")
            editor.insert(line_start, "### ")
        elif line.startswith("### "):
            editor.delete(line_start, line_start + "+4 chars")
            editor.insert(line_start, "#### ")
        else:
            editor.insert(line_start, "# ")

    def insert_link(self):
        editor = self.editor
        if editor is None:
            return
        try:
            sel_start = editor.index(tk.SEL_FIRST)
            sel_end = editor.index(tk.SEL_LAST)
            selected = editor.get(sel_start, sel_end)
            editor.delete(sel_start, sel_end)
            editor.insert(sel_start, f"[{selected}](url)")
        except:
            editor.insert(tk.INSERT, "[text](url)")

    # ---- Edit operations ---------------------------------------------------

    def undo(self):
        editor = self.editor
        if editor is None:
            return
        editor.undo()

    def redo(self):
        editor = self.editor
        if editor is None:
            return
        editor.redo()

    def cut(self):
        editor = self.editor
        if editor is None:
            return
        try:
            editor.event_generate("<<Cut>>")
        except tk.TclError:
            pass

    def copy(self):
        editor = self.editor
        if editor is None:
            return
        try:
            editor.event_generate("<<Copy>>")
        except tk.TclError:
            pass

    def paste(self):
        editor = self.editor
        if editor is None:
            return
        try:
            editor.event_generate("<<Paste>>")
        except tk.TclError:
            pass

    def select_all(self):
        editor = self.editor
        if editor is None:
            return "break"
        editor.tag_add(tk.SEL, "1.0", tk.END)
        editor.mark_set(tk.INSERT, "1.0")
        editor.see(tk.INSERT)
        return "break"

    def show_editor_context_menu(self, event):
        try:
            self.editor_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.editor_context_menu.grab_release()

    # ---- File tree helpers -------------------------------------------------

    def get_selected_path(self):
        selection = self.file_tree.selection()
        if selection:
            item = selection[0]
            values = self.file_tree.item(item, "values")
            if values:
                return values[0]
        return self.current_folder

    def show_new_menu(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="New File", command=self.new_file_in_tree)
        menu.add_command(label="New Folder", command=self.new_folder_in_tree)
        menu.post(self.file_tree.winfo_rootx() + 50, self.file_tree.winfo_rooty() + 10)

    def show_context_menu(self, event):
        if not self.current_folder:
            return
        item = self.file_tree.identify_row(event.y)
        if item:
            self.file_tree.selection_set(item)
        self.context_menu.post(event.x_root, event.y_root)

    def new_file_in_tree(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        parent_path = self.get_selected_path()
        if parent_path and os.path.isfile(parent_path):
            parent_path = os.path.dirname(parent_path)

        if parent_path:
            filename = simpledialog.askstring(
                "New File", "Enter file name:",
                parent=self.root
            )
            if filename:
                new_file_path = os.path.join(parent_path, filename)
                try:
                    with open(new_file_path, 'w', encoding='utf-8') as f:
                        f.write('')
                    self.populate_file_tree(self.current_folder)
                    self.status_bar.config(text=f"Created: {filename}")
                except Exception as e:
                    self.status_bar.config(text=f"Error creating file: {e}")

    def new_folder_in_tree(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        parent_path = self.get_selected_path()
        if parent_path and os.path.isfile(parent_path):
            parent_path = os.path.dirname(parent_path)

        if parent_path:
            foldername = simpledialog.askstring("New Folder", "Enter folder name:", parent=self.root)
            if foldername:
                new_folder_path = os.path.join(parent_path, foldername)
                try:
                    os.makedirs(new_folder_path, exist_ok=False)
                    self.populate_file_tree(self.current_folder)
                    self.status_bar.config(text=f"Created folder: {foldername}")
                except FileExistsError:
                    self.status_bar.config(text=f"Folder already exists: {foldername}")
                except Exception as e:
                    self.status_bar.config(text=f"Error creating folder: {e}")

    def delete_selected(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        selection = self.file_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.file_tree.item(item, "values")
        if not values:
            return

        path = values[0]
        item_text = self.file_tree.item(item, "text")
        name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        item_type = "folder" if os.path.isdir(path) else "file"
        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete the {item_type} '{name}'?",
            parent=self.root
        )

        if confirm:
            try:
                if os.path.isdir(path):
                    import shutil
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                self.populate_file_tree(self.current_folder)
                self.status_bar.config(text=f"Deleted: {name}")

                # Close the tab if the deleted file is open
                existing = self.tabbed_editor.find_tab_by_path(path)
                if existing >= 0:
                    # Force close without unsaved prompt (file already deleted)
                    self.tabbed_editor._tabs[existing]['editor'].set_saved()
                    self.tabbed_editor.close_tab(existing)
                    if self.tabbed_editor.get_tab_count() == 0:
                        self.tabbed_editor.add_tab(content='')
            except Exception as e:
                self.status_bar.config(text=f"Error deleting: {e}")

    def rename_selected(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        selection = self.file_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.file_tree.item(item, "values")
        if not values:
            return

        path = values[0]
        item_text = self.file_tree.item(item, "text")
        old_name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        new_name = simpledialog.askstring("Rename", "Enter new name:", initialvalue=old_name, parent=self.root)
        if new_name and new_name != old_name:
            parent_dir = os.path.dirname(path)
            new_path = os.path.join(parent_dir, new_name)
            try:
                import shutil
                shutil.move(path, new_path)
                self.populate_file_tree(self.current_folder)

                # Update tab if this file is open
                existing = self.tabbed_editor.find_tab_by_path(path)
                if existing >= 0:
                    self.tabbed_editor.set_tab_path(
                        self.tabbed_editor._tabs[existing]['id'], new_path)

                self.status_bar.config(text=f"Renamed to: {new_name}")
            except Exception as e:
                self.status_bar.config(text=f"Error renaming: {e}")

    def move_selected(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        selection = self.file_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.file_tree.item(item, "values")
        if not values:
            return

        source_path = values[0]
        item_text = self.file_tree.item(item, "text")
        name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        dest_dir = filedialog.askdirectory(title="Select destination folder", initialdir=self.current_folder)
        if dest_dir:
            dest_path = os.path.join(dest_dir, name)
            try:
                import shutil
                if os.path.exists(dest_path):
                    overwrite = messagebox.askyesno(
                        "File Exists",
                        f"'{name}' already exists in the destination. Overwrite?",
                        parent=self.root
                    )
                    if not overwrite:
                        return
                    if os.path.isdir(dest_path):
                        shutil.rmtree(dest_path)
                    else:
                        os.remove(dest_path)
                shutil.move(source_path, dest_path)
                self.populate_file_tree(self.current_folder)

                # Update tab if this file is open
                existing = self.tabbed_editor.find_tab_by_path(source_path)
                if existing >= 0:
                    self.tabbed_editor.set_tab_path(
                        self.tabbed_editor._tabs[existing]['id'], dest_path)

                self.status_bar.config(text=f"Moved to: {dest_dir}")
            except Exception as e:
                self.status_bar.config(text=f"Error moving: {e}")

    # ---- Search ------------------------------------------------------------

    def show_find_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Find in File")
        dialog.geometry("400x120")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Find:").pack(anchor="w", padx=10, pady=(10, 5))

        search_var = tk.StringVar()
        search_entry = ttk.Entry(dialog, textvariable=search_var)
        search_entry.pack(fill=tk.X, padx=10, pady=5)
        search_entry.focus()

        case_var = tk.BooleanVar()

        options_frame = ttk.Frame(dialog)
        options_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Checkbutton(options_frame, text="Case sensitive", variable=case_var).pack(side=tk.LEFT)

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        def do_find():
            query = search_var.get()
            if not query:
                return
            self.find_in_editor(query, case_var.get())

        ttk.Button(button_frame, text="Find", command=do_find).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

        search_entry.bind("<Return>", lambda _: do_find())

    def find_in_editor(self, query, case_sensitive=False):
        editor = self.editor
        if editor is None:
            return
        content = editor.get("1.0", tk.END)
        flags = 0 if case_sensitive else re.IGNORECASE

        editor.tag_remove("found", "1.0", tk.END)

        try:
            for match in re.finditer(re.escape(query), content, flags):
                start_idx = f"1.0 + {match.start()} chars"
                end_idx = f"1.0 + {match.end()} chars"
                editor.tag_add("found", start_idx, end_idx)
            editor.tag_config("found", background="yellow", foreground="black")

            first_match = editor.search(f"\\m{re.escape(query)}\\M", "1.0", forwards=True,
                                         regexp=1, nocase=not case_sensitive)
            if first_match:
                editor.mark_set("insert", first_match)
                editor.see(first_match)
                self.status_bar.config(text=f"Found matches")
            else:
                self.status_bar.config(text="No matches found")
        except re.error:
            self.status_bar.config(text="Invalid search pattern")

    def show_search_dialog(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Search in Files")
        dialog.geometry("500x350")
        dialog.transient(self.root)
        dialog.grab_set()

        input_frame = ttk.Frame(dialog)
        input_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Label(input_frame, text="Search:").pack(side=tk.LEFT)

        search_var = tk.StringVar()
        search_entry = ttk.Entry(input_frame, textvariable=search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        search_entry.focus()

        case_var = tk.BooleanVar()
        ttk.Checkbutton(input_frame, text="Case sensitive", variable=case_var).pack(side=tk.LEFT)

        ttk.Label(dialog, text="Results:").pack(anchor="w", padx=10, pady=(10, 5))

        result_frame = ttk.Frame(dialog)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        result_scroll = ttk.Scrollbar(result_frame)
        result_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        result_list = tk.Listbox(result_frame, yscrollcommand=result_scroll.set)
        result_list.pack(fill=tk.BOTH, expand=True)
        result_scroll.config(command=result_list.yview)

        def do_search():
            query = search_var.get()
            if not query or not self.current_folder:
                return
            result_list.delete(0, tk.END)
            results = self.search_in_files(self.current_folder, query, case_var.get())
            for file_path, matches in results:
                rel_path = os.path.relpath(file_path, self.current_folder)
                result_list.insert(tk.END, f"{rel_path} ({matches} matches)")

        def open_selected(_):
            selection = result_list.curselection()
            if selection:
                item = result_list.get(selection[0])
                file_name = item.split(' (')[0]
                file_path = os.path.join(self.current_folder, file_name)
                query = search_var.get()
                if os.path.isfile(file_path):
                    self._open_file_in_tab(file_path)
                    if query:
                        self.find_in_editor(query, case_var.get())

        result_list.bind("<Double-Button-1>", open_selected)

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        ttk.Button(button_frame, text="Search", command=do_search).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

        search_entry.bind("<Return>", lambda _: do_search())

    def search_in_files(self, folder, query, case_sensitive=False):
        results = []
        flags = 0 if case_sensitive else re.IGNORECASE

        def search_dir(path):
            try:
                for item in os.listdir(path):
                    full_path = os.path.join(path, item)
                    if os.path.isdir(full_path):
                        if not item.startswith('.'):
                            search_dir(full_path)
                    elif os.path.isfile(full_path):
                        try:
                            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                                matches = len(list(re.finditer(re.escape(query), content, flags)))
                                if matches > 0:
                                    results.append((full_path, matches))
                        except Exception:
                            pass
            except PermissionError:
                pass

        search_dir(folder)
        return sorted(results, key=lambda x: x[0])

    # ---- Git operations ----------------------------------------------------

    def run_git_command(self, args, cwd=None):
        if not cwd:
            cwd = self.current_folder
        try:
            result = subprocess.run(
                ['git', '-c', 'core.quotePath=false'] + args,
                cwd=cwd,
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=30
            )
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError:
            return 1, "", "Git not found. Please install git."
        except subprocess.TimeoutExpired:
            return 1, "", "Command timed out"
        except Exception as e:
            return 1, "", str(e)

    def git_clone(self):
        clone_dialog = tk.Toplevel(self.root)
        clone_dialog.title("Clone Repository")
        clone_dialog.geometry("500x200")
        clone_dialog.transient(self.root)
        clone_dialog.grab_set()

        ttk.Label(clone_dialog, text="Repository URL:").pack(anchor="w", padx=10, pady=(10, 5))

        url_var = tk.StringVar()
        url_entry = ttk.Entry(clone_dialog, textvariable=url_var)
        url_entry.pack(fill=tk.X, padx=10, pady=5)
        url_entry.focus()

        ttk.Label(clone_dialog, text="Destination Directory:").pack(anchor="w", padx=10, pady=(10, 5))

        dest_frame = ttk.Frame(clone_dialog)
        dest_frame.pack(fill=tk.X, padx=10, pady=5)

        dest_var = tk.StringVar(value=os.path.expanduser('~'))
        dest_entry = ttk.Entry(dest_frame, textvariable=dest_var)
        dest_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def browse_dest():
            dest_dir = filedialog.askdirectory(title="Select Destination", initialdir=dest_var.get())
            if dest_dir:
                dest_var.set(dest_dir)

        ttk.Button(dest_frame, text="Browse...", command=browse_dest).pack(side=tk.LEFT, padx=5)

        def do_clone():
            url = url_var.get().strip()
            dest = dest_var.get().strip()

            if not url:
                messagebox.showwarning("Missing URL", "Please enter repository URL", parent=clone_dialog)
                return

            if not dest:
                messagebox.showwarning("Missing Destination", "Please select destination directory", parent=clone_dialog)
                return

            try:
                result = subprocess.run(
                    ['git', '-c', 'core.quotePath=false', 'clone', url, dest],
                    capture_output=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=120
                )

                if result.returncode == 0:
                    self.status_bar.config(text="Repository cloned successfully")
                    clone_dialog.destroy()
                    messagebox.showinfo("Success", "Repository cloned successfully", parent=self.root)
                    self.current_folder = dest
                    self.populate_file_tree(dest)
                    self.status_bar.config(text=f"Opened: {os.path.basename(dest)}")
                    self._update_git_buttons()
                else:
                    error_msg = result.stderr if result.stderr else result.stdout
                    messagebox.showerror("Error", f"Failed to clone repository:\n{error_msg}", parent=clone_dialog)
            except FileNotFoundError:
                messagebox.showerror("Error", "Git not found. Please install git.", parent=clone_dialog)
            except subprocess.TimeoutExpired:
                messagebox.showerror("Error", "Clone operation timed out", parent=clone_dialog)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to clone:\n{str(e)}", parent=clone_dialog)

        button_frame = ttk.Frame(clone_dialog)
        button_frame.pack(fill=tk.X, padx=10, pady=(10, 10))

        ttk.Button(button_frame, text="Clone", command=do_clone).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Cancel", command=clone_dialog.destroy).pack(side=tk.RIGHT, padx=5)

        url_entry.bind("<Return>", lambda _: do_clone())

    def git_init(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        confirm = messagebox.askyesno(
            "Init Git Repository",
            f"Initialize git repository in:\n{self.current_folder}?",
            parent=self.root
        )
        if confirm:
            returncode, stdout, stderr = self.run_git_command(['init'])
            if returncode == 0:
                self.status_bar.config(text="Git repository initialized")
                messagebox.showinfo("Success", "Git repository initialized successfully", parent=self.root)
                self._update_git_buttons()
            else:
                self.status_bar.config(text=f"Git init failed: {stderr}")
                messagebox.showerror("Error", f"Failed to initialize git:\n{stderr}", parent=self.root)

    def git_set_remote(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, _ = self.run_git_command(['remote', '-v'])
        current_remote = ""
        if returncode == 0 and stdout.strip():
            current_remote = f"\nCurrent remotes:\n{stdout}"

        remote_url = simpledialog.askstring(
            "Set Git Remote",
            f"Enter remote URL:{current_remote}\n\nExamples:\nhttps://github.com/username/repo.git\ngit@github.com:username/repo.git",
            parent=self.root
        )
        if remote_url:
            returncode, _, _ = self.run_git_command(['remote', 'get-url', 'origin'])
            if returncode == 0:
                returncode, stdout, stderr = self.run_git_command(['remote', 'set-url', 'origin', remote_url])
            else:
                returncode, stdout, stderr = self.run_git_command(['remote', 'add', 'origin', remote_url])

            if returncode == 0:
                self.status_bar.config(text="Remote set successfully")
                messagebox.showinfo("Success", "Remote URL set successfully", parent=self.root)
            else:
                self.status_bar.config(text=f"Failed to set remote: {stderr}")
                messagebox.showerror("Error", f"Failed to set remote:\n{stderr}", parent=self.root)

    def git_commit(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, stderr = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, _ = self.run_git_command(['status', '--short'])
        status_text = stdout if stdout.strip() else "No changes to commit"

        commit_msg = simpledialog.askstring(
            "Git Commit",
            f"Git Status:\n{status_text}\n\nEnter commit message:",
            parent=self.root
        )
        if commit_msg:
            returncode, stdout, stderr = self.run_git_command(['add', '.'])
            if returncode != 0:
                messagebox.showerror("Error", f"Failed to stage files:\n{stderr}", parent=self.root)
                return

            returncode, stdout, stderr = self.run_git_command(['commit', '-m', commit_msg])
            if returncode == 0:
                self.status_bar.config(text=f"Committed: {commit_msg}")
                messagebox.showinfo("Success", f"Changes committed:\n{commit_msg}", parent=self.root)
            else:
                self.status_bar.config(text=f"Commit failed: {stderr}")
                messagebox.showerror("Error", f"Failed to commit:\n{stderr}", parent=self.root)

    def git_pull(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        confirm = messagebox.askyesno("Git Pull", "Pull changes from remote?", parent=self.root)
        if confirm:
            returncode, stdout, stderr = self.run_git_command(['pull'])
            if returncode == 0:
                self.status_bar.config(text="Pull successful")
                messagebox.showinfo("Success", f"Pull successful:\n{stdout}", parent=self.root)
                self.populate_file_tree(self.current_folder)
            else:
                self.status_bar.config(text=f"Pull failed: {stderr}")
                messagebox.showerror("Error", f"Failed to pull:\n{stderr}", parent=self.root)

    def git_push(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, _ = self.run_git_command(['remote', 'get-url', 'origin'])
        if returncode != 0:
            messagebox.showwarning("No Remote", "Please set remote URL first", parent=self.root)
            return

        returncode, branch_stdout, _ = self.run_git_command(['branch', '--show-current'])
        current_branch = branch_stdout.strip() if returncode == 0 else 'main'

        returncode, _, _ = self.run_git_command(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])

        confirm = messagebox.askyesno("Git Push", "Push changes to remote?", parent=self.root)
        if confirm:
            if returncode != 0:
                returncode, stdout, stderr = self.run_git_command(['push', '--set-upstream', 'origin', current_branch])
            else:
                returncode, stdout, stderr = self.run_git_command(['push'])

            if returncode == 0:
                self.status_bar.config(text="Push successful")
                messagebox.showinfo("Success", f"Push successful:\n{stdout}", parent=self.root)
            else:
                self.status_bar.config(text=f"Push failed: {stderr}")
                messagebox.showerror("Error", f"Failed to push:\n{stderr}", parent=self.root)

    def show_git_log(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, stderr = self.run_git_command(['log', '--oneline', '--graph', '--all', '-20'])
        if returncode != 0:
            messagebox.showerror("Error", f"Failed to get log:\n{stderr}", parent=self.root)
            return

        log_window = tk.Toplevel(self.root)
        log_window.title("Git Log")
        log_window.geometry("600x400")
        log_window.transient(self.root)

        btn_frame = ttk.Frame(log_window)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(btn_frame, text="Refresh", command=lambda: self.refresh_git_log(log_text)).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="Close", command=log_window.destroy).pack(side=tk.RIGHT, padx=5)

        log_scroll = ttk.Scrollbar(log_window)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        log_text = tk.Text(log_window, yscrollcommand=log_scroll.set, font=("Monaco", 11))
        log_text.pack(fill=tk.BOTH, expand=True, padx=(10, 0), pady=(0, 10))
        log_scroll.config(command=log_text.yview)

        if not stdout.strip():
            log_text.insert(tk.END, "No commits yet")
        else:
            log_text.insert(tk.END, stdout)

        log_text.config(state=tk.DISABLED)

    def refresh_git_log(self, log_text):
        returncode, stdout, stderr = self.run_git_command(['log', '--oneline', '--graph', '--all', '-20'])
        log_text.config(state=tk.NORMAL)
        log_text.delete(1.0, tk.END)
        if returncode != 0:
            log_text.insert(tk.END, f"Error: {stderr}")
        elif not stdout.strip():
            log_text.insert(tk.END, "No commits yet")
        else:
            log_text.insert(tk.END, stdout)
        log_text.config(state=tk.DISABLED)


if __name__ == "__main__":
    root = tk.Tk()
    app = MarkdownEditor(root)
    root.mainloop()
