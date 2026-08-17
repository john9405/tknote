"""CodeContext — block context strip above the editor (IDEA breadcrumb).

Simplified port of idlelib's codecontext: a read-only one-line-plus
Text strip showing the enclosing block headers (def/class/if/...)
when they scroll off the top of the editor.  Clicking a context line
jumps to it.  Fixed light colors instead of idleConf; pack layout
instead of grid.
"""

import re
from sys import maxsize as INFINITY

import tkinter as tk

BLOCKOPENERS = {'class', 'def', 'if', 'elif', 'else', 'while', 'for',
                'try', 'except', 'finally', 'with', 'async'}


def get_spaces_firstword(codeline, c=re.compile(r"^(\s*)(\w*)")):
    """Extract the beginning whitespace and first word from codeline."""
    return c.match(codeline).groups()


def get_line_info(codeline):
    """Return tuple of (line indent value, codeline, block start keyword).

    The indentation of empty lines (or comment lines) is INFINITY.
    If the line does not start a block, the keyword value is False.
    """
    spaces, firstword = get_spaces_firstword(codeline)
    indent = len(spaces)
    if len(codeline) == indent or codeline[indent] == '#':
        indent = INFINITY
    opener = firstword in BLOCKOPENERS and firstword
    return indent, codeline, opener


class CodeContext:
    """Display block context above the editor text."""

    UPDATEINTERVAL = 100   # ms
    MAX_LINES = 15
    BG = '#f0f0f0'
    FG = '#555555'

    def __init__(self, editor):
        self.editor = editor
        self.text = editor.get_text_widget()
        self._reset()

    def _reset(self):
        self.context = None
        self.t1 = None
        self.topvisible = 1
        self.info = [(0, -1, "", False)]

    def close(self):
        if self.t1 is not None:
            try:
                self.text.after_cancel(self.t1)
            except tk.TclError:
                pass
            self.t1 = None

    # ── toggle ─────────────────────────────────────────────────────────

    def toggle_code_context_event(self, event=None):
        """Show/hide the context strip."""
        if self.context is None:
            self.context = tk.Text(
                self.editor._text_frame,
                height=1, width=1,  # don't request more than we get
                wrap=tk.NONE, padx=8, borderwidth=0,
                highlightthickness=0,
                bg=self.BG, fg=self.FG,
                font=self.editor.EDITOR_FONT,
                takefocus=False, state=tk.DISABLED)
            self.context.pack(side=tk.TOP, fill=tk.X,
                              before=self.text)
            self.t1 = self.text.after(self.UPDATEINTERVAL, self.timer_event)
            self.context.bind('<ButtonRelease-1>', self.jumptoline)
        else:
            self.context.destroy()
            self.context = None
            self.text.after_cancel(self.t1)
            self._reset()
        return "break"

    def is_visible(self):
        return self.context is not None

    # ── update logic (idlelib port) ────────────────────────────────────

    def get_context(self, new_topvisible, stopline=1, stopindent=0):
        """Return a list of block line tuples and the 'last' indent.

        The tuple fields are (linenum, indent, text, opener).
        The list represents header lines from new_topvisible back to
        stopline with successively shorter indents > stopindent.
        """
        assert stopline > 0
        lines = []
        lastindent = INFINITY
        for linenum in range(new_topvisible, stopline - 1, -1):
            codeline = self.text.get(f'{linenum}.0', f'{linenum}.end')
            indent, text, opener = get_line_info(codeline)
            if indent < lastindent:
                lastindent = indent
                if opener in ("else", "elif"):
                    lastindent += 1  # also show the matching if
                if opener and linenum < new_topvisible and \
                        indent >= stopindent:
                    lines.append((linenum, indent, text, opener))
                if lastindent <= stopindent:
                    break
        lines.reverse()
        return lines, lastindent

    def update_code_context(self):
        """Update the strip when the top visible line changes."""
        new_topvisible = int(float(self.text.index('@0,0')))
        if self.topvisible == new_topvisible:      # haven't scrolled
            return
        if self.topvisible < new_topvisible:       # scrolled down
            lines, lastindent = self.get_context(new_topvisible,
                                                 self.topvisible)
            while self.info[-1][1] >= lastindent:
                del self.info[-1]
        else:                                      # scrolled up
            stopindent = self.info[-1][1] + 1
            while self.info[-1][0] >= new_topvisible:
                stopindent = self.info[-1][1]
                del self.info[-1]
            lines, lastindent = self.get_context(new_topvisible,
                                                 self.info[-1][0] + 1,
                                                 stopindent)
        self.info.extend(lines)
        self.topvisible = new_topvisible
        context_strings = [x[2] for x in self.info[-self.MAX_LINES:]]
        showfirst = 0 if context_strings[0] else 1
        self.context['height'] = len(context_strings) - showfirst
        self.context['state'] = 'normal'
        self.context.delete('1.0', 'end')
        self.context.insert('end', '\n'.join(context_strings[showfirst:]))
        self.context['state'] = 'disabled'

    def jumptoline(self, event=None):
        """Show the clicked context line at the top of the editor."""
        try:
            self.context.index('sel.first')
        except tk.TclError:
            lines = len(self.info)
            if lines == 1:  # no context lines are showing
                newtop = 1
            else:
                contextline = int(float(self.context.index('insert')))
                offset = max(1, lines - self.MAX_LINES) - 1
                newtop = self.info[offset + contextline][0]
            self.text.yview(f'{newtop}.0')
            self.update_code_context()

    def timer_event(self):
        """Poll for scroll changes every UPDATEINTERVAL ms."""
        if self.context is not None:
            self.update_code_context()
            self.t1 = self.text.after(self.UPDATEINTERVAL, self.timer_event)
