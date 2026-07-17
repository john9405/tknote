"""Smart auto-indentation — idlelib-style smart indent, newline, and backspace."""

import re
import tkinter as tk


# ── helpers ────────────────────────────────────────────────────────────────

def _get_line_indent(line, tabwidth=8):
    """Return (raw_indent_chars, effective_spaces) for a line."""
    raw = len(line) - len(line.lstrip(' \t'))
    effective = len(line[:raw].expandtabs(tabwidth))
    return raw, effective


def _index2line(index):
    """Convert a Tk text index like '4.12' to line number 4."""
    return int(float(index))


# ── block detection patterns ───────────────────────────────────────────────

_BLOCK_OPENER_RE = re.compile(r':\s*(#.*)?$')
_BLOCK_CLOSER_KEYWORDS = {'return', 'break', 'continue', 'pass', 'raise'}
_INDENT_KEYWORDS = {'class', 'def', 'if', 'elif', 'else', 'while', 'for',
                    'try', 'except', 'finally', 'with', 'match', 'case'}


class AutoIndent:
    """Smart indentation for a text editor widget.

    Attach to an EditorWidget to provide idlelib-style keyboard behavior:
      - Tab → smart_indent (indent at line start, jump at mid-line)
      - Enter → newline_and_indent (inherit indent, detect blocks)
      - Shift-Tab / smart backspace → dedent to previous tab stop
    """

    indentwidth = 4      # spaces per indent level
    tabwidth = 8         # display width of a tab character (IDLE default)
    usetabs = False      # True → use tabs; False → use spaces

    def __init__(self, text_widget, undo=None):
        self.text = text_widget
        self.undo = undo  # UndoDelegator for undo_block_start/stop

    def _undo_start(self):
        if self.undo:
            self.undo.undo_block_start()

    def _undo_stop(self):
        if self.undo:
            self.undo.undo_block_stop()

    # ── Tab (smart indent) ──────────────────────────────────────────────

    def smart_indent_event(self, event=None):
        """Handle Tab key: indent region, indent line, or jump to next stop."""
        text = self.text
        # Try to get selection
        try:
            first = text.index(tk.SEL_FIRST)
            last = text.index(tk.SEL_LAST)
        except tk.TclError:
            first = last = None

        self._undo_start()
        try:
            if first and last:
                if _index2line(first) != _index2line(last):
                    return self._indent_region(first, last)
                text.delete(first, last)
                text.mark_set("insert", first)

            # Get whitespace to left of cursor
            prefix = text.get("insert linestart", "insert")
            raw, effective = _get_line_indent(prefix, self.tabwidth)

            if raw == len(prefix):
                # Cursor at (or before) leading whitespace → add indent
                self._reindent_to(effective + self.indentwidth)
            else:
                # Cursor in text → jump to next tab stop
                if self.usetabs:
                    pad = '\t'
                else:
                    n = self.indentwidth
                    pad = ' ' * (n - effective % n)
                text.insert("insert", pad)
            text.see("insert")
            return "break"
        finally:
            self._undo_stop()

    def _indent_region(self, first, last):
        """Indent each line in the selected region by one level."""
        text = self.text
        line_start = _index2line(first)
        line_end = _index2line(last)
        for i in range(line_start, line_end + 1):
            text.insert(f'{i}.0', self._indent_str())
        return "break"

    # ── Enter (newline + indent) ─────────────────────────────────────────

    def newline_and_indent_event(self, event=None):
        """Handle Enter key: insert newline + smart indent."""
        text = self.text
        try:
            first = text.index(tk.SEL_FIRST)
            last = text.index(tk.SEL_LAST)
        except tk.TclError:
            first = last = None

        self._undo_start()
        try:
            if first and last:
                text.delete(first, last)
                text.mark_set("insert", first)

            # Get current line info
            current_line = text.get("insert linestart", "insert")
            raw, effective = _get_line_indent(current_line, self.tabwidth)

            # Empty line (only whitespace) → just newline
            stripped = current_line.lstrip(' \t')
            indent = current_line[:raw]

            # Strip trailing whitespace
            i = 0
            while current_line and current_line[-1] in ' \t':
                current_line = current_line[:-1]
                i += 1
            if i:
                text.delete(f"insert -{i} chars", "insert")

            # Strip whitespace after cursor
            while text.get("insert") in ' \t':
                text.delete("insert")

            # Insert newline
            text.insert("insert", '\n')

            # Determine indent for new line
            stripped_line = current_line.strip()

            # Check if current line ends with colon → increase indent
            if _BLOCK_OPENER_RE.search(stripped_line):
                new_indent = effective + self.indentwidth
            # Check if current line starts with a block closer keyword → decrease
            elif stripped_line and (stripped_line.split()[0] in _BLOCK_CLOSER_KEYWORDS):
                new_indent = max(0, effective - self.indentwidth)
            # Handle bracket continuation — simple alignment
            elif self._is_in_bracket_continuation(current_line):
                # Find last open bracket and align after it
                bracket_pos = max(current_line.rfind('('),
                                  current_line.rfind('['),
                                  current_line.rfind('{'))
                if bracket_pos >= 0:
                    new_indent = len(current_line[:bracket_pos + 1].expandtabs(self.tabwidth))
                else:
                    new_indent = effective
            else:
                new_indent = effective

            text.insert("insert", ' ' * new_indent)
            text.see("insert")
            return "break"
        finally:
            self._undo_stop()

    def _is_in_bracket_continuation(self, line):
        """Check if line ends inside an open bracket structure."""
        opens = line.count('(') + line.count('[') + line.count('{')
        closes = line.count(')') + line.count(']') + line.count('}')
        return opens > closes

    # ── Backspace (smart dedent) ─────────────────────────────────────────

    def smart_backspace_event(self, event=None):
        """Handle Backspace: delete or dedent to previous tab stop."""
        text = self.text

        # Handle selection deletion first
        try:
            first = text.index(tk.SEL_FIRST)
            last = text.index(tk.SEL_LAST)
            text.delete(first, last)
            text.mark_set("insert", first)
            return "break"
        except tk.TclError:
            pass

        # Get whitespace to left of cursor
        chars = text.get("insert linestart", "insert")
        if chars == '':
            if text.compare("insert", ">", "1.0"):
                text.delete("insert-1c")
            else:
                text.bell()
            return "break"

        if chars[-1] not in ' \t':
            text.delete("insert-1c")
            return "break"

        # Backspacing over whitespace — jump to previous tab stop
        have = len(chars.expandtabs(self.tabwidth))
        want = ((have - 1) // self.indentwidth) * self.indentwidth

        self._undo_start()
        try:
            ncharsdeleted = 0
            while chars:
                chars = chars[:-1]
                ncharsdeleted += 1
                have = len(chars.expandtabs(self.tabwidth))
                if have <= want or (chars and chars[-1] not in ' \t'):
                    break
            text.delete(f"insert-{ncharsdeleted}c", "insert")
            if have < want:
                text.insert("insert", ' ' * (want - have))
        finally:
            self._undo_stop()
        return "break"

    # ── internals ────────────────────────────────────────────────────────

    def _indent_str(self):
        return '\t' if self.usetabs else ' ' * self.indentwidth

    def _reindent_to(self, column):
        """Replace leading whitespace on current line to reach `column`."""
        text = self.text
        line_start = text.index("insert linestart")
        line_end = text.index("insert")
        # Delete existing leading whitespace
        prefix = text.get(line_start, line_end)
        raw = len(prefix) - len(prefix.lstrip(' \t'))
        if raw > 0:
            text.delete(line_start, f"{line_start}+{raw}c")
        if self.usetabs:
            ntabs, nspc = divmod(column, self.tabwidth)
            text.insert(line_start, '\t' * ntabs + ' ' * nspc)
        else:
            text.insert(line_start, ' ' * column)
