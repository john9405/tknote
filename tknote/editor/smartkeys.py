"""SmartKeys — IDEA-style editing keystrokes for EditorWidget.

Features:
  - Auto-close ( [ { and quotes; skip over an existing closing char
  - Paired backspace (deletes both halves of an empty pair)
  - Duplicate line/selection           (Cmd+D)
  - Move line(s) up/down               (Alt+Shift+Up/Down)
  - Join lines                         (Ctrl+Shift+J)
  - Expand / shrink selection          (Ctrl+W / Ctrl+Shift+W)
"""

import re
import tkinter as tk

from idlelib.hyperparser import HyperParser

from ..infra.editwin_adapter import EditwinAdapter


_OPEN_TO_CLOSE = {'(': ')', '[': ']', '{': '}'}
_CLOSE_TO_OPEN = {v: k for k, v in _OPEN_TO_CLOSE.items()}
_QUOTE_CHARS = {'"', "'"}
# Characters after which a typed quote is treated as an opening quote.
_QUOTE_PRECEDERS = '([{=,:+-*/%&|^~!<>'
_WORD_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def _lineno(text, index):
    return int(float(text.index(index)))


def _end_line(text):
    """Last content line number (Tk keeps an implicit final newline,
    so the last real char is at 'end-2c')."""
    try:
        return _lineno(text, 'end-2c')
    except tk.TclError:
        return 1


class _UndoBlock:
    """Context manager wrapping edits in a single undo step."""

    def __init__(self, editor):
        self.editor = editor

    def __enter__(self):
        self.editor._undo.undo_block_start()

    def __exit__(self, *exc):
        self.editor._undo.undo_block_stop()


class SmartKeys:
    """IDEA-inspired editing commands bound to an EditorWidget."""

    def __init__(self, editor):
        self.editor = editor
        self.text = editor.get_text_widget()
        self._adapter = EditwinAdapter(self.text)
        self._expand_stack = []   # selection ladder for Ctrl+W

    # ── helpers ────────────────────────────────────────────────────────

    def _sel(self):
        """Return (first, last) of the current selection, or None."""
        # Note: the sel.first/sel.last *marks* always exist in a Tk Text,
        # so the sel tag's ranges are the only reliable presence check.
        ranges = self.text.tag_ranges('sel')
        if not ranges:
            return None
        return (self.text.index(ranges[0]), self.text.index(ranges[1]))

    def _set_sel(self, first, last):
        """Set the selection and place the insert at its end."""
        self.text.tag_remove('sel', '1.0', 'end')
        if self.text.compare(first, '!=', last):
            self.text.tag_add('sel', first, last)
        self.text.mark_set('insert', last)
        self.text.see('insert')

    def _in_string_or_comment(self, index):
        tags = self.text.tag_names(index)
        return "STRING" in tags or "COMMENT" in tags

    def _autocomplete_active(self):
        ac = getattr(self.editor, '_autocomplete', None)
        return bool(ac and ac.autocompletewindow
                    and ac.autocompletewindow.is_active())

    # ── Auto-close & skip-over ─────────────────────────────────────────

    def auto_close_opener(self, event):
        """Auto-close ( [ { — return 'break' when handled."""
        char = event.char if event else None
        if not char or char not in _OPEN_TO_CLOSE:
            return None
        if self._autocomplete_active():
            return None  # the completion window owns typing
        if self._in_string_or_comment('insert'):
            return None
        self.text.insert('insert', char + _OPEN_TO_CLOSE[char])
        self.text.mark_set('insert', 'insert-1c')
        self.text.see('insert')
        return 'break'

    def auto_close_quote(self, event):
        """Auto-close a quote when it opens a string — 'break' when handled."""
        char = event.char if event else None
        if not char or char not in _QUOTE_CHARS:
            return None
        if self._autocomplete_active():
            return None
        if self._in_string_or_comment('insert'):
            return None
        if self.text.compare('insert', '==', 'insert linestart'):
            prev = ''
        else:
            prev = self.text.get('insert-1c')
        if prev and not (prev.isspace() or prev in _QUOTE_PRECEDERS):
            return None  # likely a closing quote mid-word — let Tk insert
        self.text.insert('insert', char + char)
        self.text.mark_set('insert', 'insert-1c')
        self.text.see('insert')
        return 'break'

    def skip_over_closer(self, event):
        """Skip over an existing closing char — 'break' when handled."""
        char = event.char if event else None
        if not char or char not in _CLOSE_TO_OPEN:
            return None
        if self.text.get('insert') == char:
            self.text.mark_set('insert', 'insert+1c')
            self.text.see('insert')
            return 'break'
        return None

    def paired_backspace(self, event=None):
        """Delete both halves of an empty pair — 'break' when handled."""
        text = self.text
        try:
            left = text.get('insert-1c')
            right = text.get('insert')
        except tk.TclError:
            return None
        if left in _OPEN_TO_CLOSE and _OPEN_TO_CLOSE[left] == right:
            with _UndoBlock(self.editor):
                text.delete('insert-1c', 'insert+1c')
            text.see('insert')
            return 'break'
        return None

    # ── Duplicate line / selection (Cmd+D) ─────────────────────────────

    def duplicate_line_or_selection(self, event=None):
        """Duplicate the selection, or the cursor line when nothing is
        selected (IDEA's Duplicate Line)."""
        text = self.text
        sel = self._sel()
        with _UndoBlock(self.editor):
            if sel and text.compare(sel[0], '!=', sel[1]):
                chars = text.get(sel[0], sel[1])
                text.insert(sel[1], chars)
                self._set_sel(sel[1], f"{sel[1]}+{len(chars)}c")
            else:
                lineno = _lineno(text, 'insert')
                line = text.get(f'{lineno}.0', f'{lineno}.0 lineend')
                text.insert(f'{lineno}.0 lineend', '\n' + line)
                text.mark_set('insert', f'{lineno + 1}.0')
                text.see('insert')
        return 'break'

    # ── Move line(s) up/down ───────────────────────────────────────────

    def move_line_up(self, event=None):
        return self._move_line(-1)

    def move_line_down(self, event=None):
        return self._move_line(1)

    def _move_line(self, delta):
        text = self.text
        first, last = self.editor._get_selected_lines()
        end_line = _end_line(text)
        if delta < 0:
            if first <= 1:
                return 'break'
            new_first = first - 1
        else:
            if last >= end_line:
                return 'break'
            new_first = first + 1

        with _UndoBlock(self.editor):
            added_newline = False
            if last >= end_line and text.get('end-2c') != '\n':
                # Moving the final line up — no trailing newline exists,
                # so take the line content and supply the newline.
                block = text.get(f'{first}.0', f'{last}.0 lineend') + '\n'
                text.delete(f'{first}.0', f'{last}.0 lineend')
                added_newline = True
            else:
                block = text.get(f'{first}.0', f'{last + 1}.0')
                text.delete(f'{first}.0', f'{last + 1}.0')
            text.insert(f'{new_first}.0', block)
            if added_newline:
                # Preserve the original no-trailing-newline ending.
                text.delete('end-2c')

        # Reselect the moved lines.
        new_last = new_first + (last - first)
        self._set_sel(f'{new_first}.0', f'{new_last}.0 lineend')
        return 'break'

    # ── Join lines (Ctrl+Shift+J) ──────────────────────────────────────

    def join_lines(self, event=None):
        """Join the selected lines (or the cursor line with the next)."""
        text = self.text
        first, last = self.editor._get_selected_lines()
        if first == last:
            if last >= _end_line(text):
                return 'break'
            last = first + 1

        with _UndoBlock(self.editor):
            for i in range(first, last):
                cur = text.get(f'{i}.0', f'{i}.0 lineend')
                nxt = text.get(f'{i + 1}.0', f'{i + 1}.0 lineend')
                # No separator when joining into a blank line, after an
                # open paren, or into a comment.
                if (cur.strip() and nxt.strip() and
                        not cur.rstrip().endswith((' ', '\t', '(')) and
                        not nxt.lstrip().startswith('#')):
                    sep = ' '
                else:
                    sep = ''
                text.delete(f'{i}.0', f'{i + 2}.0')
                text.insert(f'{i}.0', cur + sep + nxt.lstrip() + '\n')
        self.text.see('insert')
        return 'break'

    # ── Expand / shrink selection (Ctrl+W / Ctrl+Shift+W) ──────────────

    def expand_selection(self, event=None):
        """Extend the selection to the next structural level."""
        stack = self._expand_stack
        if not stack:
            sel = self._sel()
            start = sel if sel else None
            nxt = self._next_expansion(*start) if start else \
                self._next_expansion('insert', 'insert')
            if nxt is None:
                return 'break'
            stack.append(nxt)
            self._set_sel(*nxt)
            return 'break'
        first, last = stack[-1]
        nxt = self._next_expansion(first, last)
        if nxt is not None:
            stack.append(nxt)
            self._set_sel(*nxt)
        return 'break'

    def shrink_selection(self, event=None):
        """Shrink the selection back down the expansion ladder."""
        if not self._expand_stack:
            return self.expand_selection(event)
        self._expand_stack.pop()
        if self._expand_stack:
            self._set_sel(*self._expand_stack[-1])
        return 'break'

    def _on_keyrelease(self, event):
        """Reset the expansion ladder when the selection changes."""
        if not self._expand_stack:
            return None
        sel = self._sel()
        if not sel or sel != self._expand_stack[-1]:
            self._expand_stack = []
        return None

    def _next_expansion(self, first, last):
        """Return the smallest strictly-larger selection than
        (first, last), or None when there is nothing larger."""
        text = self.text
        candidates = []

        def larger(f, l):
            return (text.compare(first, '>', f) or
                    (first == f and text.compare(last, '<', l)))

        candidates.append(self._word_range(first, last))
        candidates.append(self._string_content_range(first, last))
        candidates.append(self._string_quote_range(first, last))
        candidates.append(self._bracket_content_range(first, last))
        candidates.append(self._bracket_range(first, last))
        candidates.append(self._line_range(first, last))
        candidates.append(self._block_range(first, last))
        candidates.append(('1.0', 'end-1c'))

        for cand in candidates:
            if cand and larger(*cand):
                return cand
        return None

    # -- structural candidates -----------------------------------------

    def _word_range(self, first, last):
        """The word under the cursor."""
        text = self.text
        line_start = text.index('insert linestart')
        line = text.get(line_start, 'insert lineend')
        col = len(text.get(line_start, 'insert'))
        for m in _WORD_RE.finditer(line):
            if m.start() <= col <= m.end():
                base = _lineno(text, line_start)
                return (f'{base}.{m.start()}', f'{base}.{m.end()}')
        return None

    def _string_info(self):
        """Return (content_range, quote_range) of the string enclosing the
        cursor, or (None, None).  Single-line strings only."""
        text = self.text
        line_start = text.index('insert linestart')
        line = text.get(line_start, 'insert lineend')
        col = len(text.get(line_start, 'insert'))

        quote = None
        for q in ('"', "'"):
            if line[:col].count(q) % 2 == 1:
                quote = q
                break
        if quote is None:
            return None, None
        qpos = [i for i, ch in enumerate(line) if ch == quote]
        if len(qpos) < 2:
            return None, None
        for i in range(0, len(qpos) - 1, 2):
            if qpos[i] <= col < qpos[i + 1] + 1:
                base = _lineno(text, line_start)
                content = (f'{base}.{qpos[i] + 1}', f'{base}.{qpos[i + 1]}')
                quoted = (f'{base}.{qpos[i]}', f'{base}.{qpos[i + 1] + 1}')
                return content, quoted
        return None, None

    def _string_content_range(self, first, last):
        """Contents of the string enclosing the cursor."""
        return self._string_info()[0]

    def _string_quote_range(self, first, last):
        """The string including its quotes."""
        return self._string_info()[1]

    def _bracket_content_range(self, first, last):
        """Contents of the innermost brackets around the cursor."""
        try:
            hp = HyperParser(self._adapter, 'insert')
            br = hp.get_surrounding_brackets()
        except Exception:
            return None
        if not br:
            return None
        opener, closer = br
        return (f'{opener}+1c', closer)

    def _bracket_range(self, first, last):
        """Contents plus the surrounding brackets themselves."""
        try:
            hp = HyperParser(self._adapter, 'insert')
            br = hp.get_surrounding_brackets()
        except Exception:
            return None
        if not br:
            return None
        opener, closer = br
        return (opener, f'{closer}+1c')

    def _line_range(self, first, last):
        """Whole lines spanned by the current selection."""
        text = self.text
        first_line = _lineno(text, first)
        last_line = _lineno(text, last)
        if text.compare(last, '==', f'{last_line}.0'):
            # selection ends exactly at a line start — exclude that line
            last_line = max(first_line, last_line - 1)
        return (f'{first_line}.0', f'{last_line}.0 lineend')

    def _block_range(self, first, last):
        """The enclosing indented block."""
        text = self.text
        first_line = _lineno(text, first)
        last_line = _lineno(text, last)

        def indent_of(lineno):
            line = text.get(f'{lineno}.0', f'{lineno}.0 lineend')
            return len(line) - len(line.lstrip(' \t'))

        indent = indent_of(first_line)
        if indent == 0:
            return None  # top level — nothing enclosing
        # scan up for the block header (smaller indent)
        start = first_line
        while start > 1 and indent_of(start - 1) >= indent:
            start -= 1
        start -= 1
        if start < 1:
            return None
        # scan down past lines indented deeper than the header
        block_indent = indent_of(start)
        end = last_line
        end_line_no = _end_line(text)
        while end < end_line_no:
            nxt = end + 1
            content = text.get(f'{nxt}.0', f'{nxt}.0 lineend')
            if not content.strip():
                end = nxt
            elif indent_of(nxt) > block_indent:
                end = nxt
            else:
                break
        # Trim trailing blank lines from the selection.
        while end > last_line and \
                not text.get(f'{end}.0', f'{end}.0 lineend').strip():
            end -= 1
        return (f'{start}.0', f'{end}.0 lineend')
