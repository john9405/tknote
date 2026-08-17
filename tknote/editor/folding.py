"""Code folding — elide-based fold regions with a marker margin.

Foldable regions are def/class bodies and any block opened by a
keyword line ending in ':'.  Folding hides body lines via the Text
widget's elide attribute: line indices never shift, `get()` still
covers folded text, and navigation targets inside folds must be
explicitly unfolded (see unfold_containing).
"""

import re
import tkinter as tk

BLOCKOPENERS = {'class', 'def', 'if', 'elif', 'else', 'while', 'for',
                'try', 'except', 'finally', 'with', 'async'}
_HEADER_RE = re.compile(r'^(async\s+)?(def|class)\b')
ELIDE_TAG = 'fold_elide'
RECOMPUTE_DELAY = 300   # ms debounce after edits


def compute_regions(lines):
    """Compute foldable regions from the document's lines.

    Returns a list of (start, end, header_text) tuples, 1-based;
    *end* is the last body line (inclusive).  Header lines are
    def/class lines or lines whose first word opens a block and end
    with ':' (annotations like ``x: int`` and dict literals like
    ``d = {`` don't qualify).
    """
    n = len(lines)
    indent = []
    for line in lines:
        stripped = line.lstrip(' \t')
        indent.append(len(line) - len(stripped) if stripped else None)

    significant = [i for i, line in enumerate(lines)
                   if line.strip() and not line.lstrip().startswith('#')]

    regions = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or indent[i] is None:
            continue
        first_word = stripped.split()[0].rstrip(':')
        is_header = bool(_HEADER_RE.match(stripped)) or (
            first_word in BLOCKOPENERS and line.rstrip().endswith(':'))
        if not is_header:
            continue
        # Body starts at the first significant line indented deeper
        # than the header.
        body = None
        for j in significant:
            if j <= i:
                continue
            if indent[j] is not None and indent[j] > indent[i]:
                body = j
                break
            if indent[j] is not None and indent[j] <= indent[i]:
                break  # same or shallower indent — not our body
        if body is None:
            continue
        # Region ends before the first significant line indented no
        # deeper than the header.
        end = n - 1
        for j in significant:
            if j > body and indent[j] is not None and \
                    indent[j] <= indent[i]:
                end = j - 1
                break
        # Trim trailing blank lines so folding doesn't swallow the
        # spacing between blocks.
        while end > body and not lines[end].strip():
            end -= 1
        if end >= body:
            regions.append((i + 1, end + 1, line))
    return regions


class FoldManager:
    """Manages fold regions and elision for one EditorWidget."""

    def __init__(self, editor):
        self.editor = editor
        self.text = editor.get_text_widget()
        self.regions = []           # [(start, end, header_text)]
        self.folded = set()         # region start lines that are folded
        self._intervals = []        # sorted [(start, end)] of folded
        self._visible = []          # cached visible line numbers
        self._recompute_after = None
        self.text.tag_config(ELIDE_TAG, elide=True)

    # ── scheduling ─────────────────────────────────────────────────────

    def mark_dirty(self):
        """Called on every edit — debounced recompute."""
        if self._recompute_after is not None:
            try:
                self.text.after_cancel(self._recompute_after)
            except tk.TclError:
                pass
        self._recompute_after = self.text.after(RECOMPUTE_DELAY,
                                                self._recompute)

    def _recompute(self):
        self._recompute_after = None
        self.recompute()

    def cancel(self):
        if self._recompute_after is not None:
            try:
                self.text.after_cancel(self._recompute_after)
            except tk.TclError:
                pass
            self._recompute_after = None

    # ── state ──────────────────────────────────────────────────────────

    def recompute(self, keep_state=True):
        """Rescan the document for foldable regions.

        With keep_state, folded regions are re-matched by header text
        within ±2 lines, so edits don't silently unfold everything.
        """
        old = {}
        if keep_state:
            old = {header: start for start, _e, header in self.regions
                   if start in self.folded}
        self.regions = compute_regions(
            self.text.get('1.0', 'end-1c').split('\n'))
        self.folded = set()
        for start, end, header in self.regions:
            if header in old and abs(start - old[header]) <= 2:
                self.folded.add(start)
        self._apply_elide()
        self.editor.refresh_sidebar_for_fold()

    def _apply_elide(self):
        """(Re)apply the elide tags for all folded regions."""
        self.text.tag_remove(ELIDE_TAG, '1.0', 'end')
        self._intervals = []
        for start, end, _h in self.regions:
            if start in self.folded:
                self.text.tag_add(ELIDE_TAG, f'{start + 1}.0',
                                  f'{end + 1}.0')
                self._intervals.append((start, end))
        self._intervals.sort()
        # Cache the visible line numbers.
        hidden = set()
        for s, e in self._intervals:
            hidden.update(range(s + 1, e + 1))
        total = int(float(self.text.index('end-2c')))
        self._visible = [ln for ln in range(1, total + 1)
                         if ln not in hidden]

    # ── queries ────────────────────────────────────────────────────────

    def has_folds(self):
        return bool(self.folded)

    def is_elided(self, lineno):
        """True if *lineno* (1-based) is hidden inside a fold."""
        for s, e in self._intervals:
            if s < lineno <= e:
                return True
        return False

    def elided_before(self, lineno):
        """Number of hidden lines strictly before *lineno*.

        Only counts intervals not nested inside an already-counted
        folded ancestor.
        """
        total = 0
        last_end = 0
        for s, e in self._intervals:
            if s >= lineno:
                break
            if s <= last_end:
                continue
            total += min(e, lineno - 1) - s
            last_end = max(last_end, e)
        return total

    def display_line(self, lineno):
        """1-based display row of a real line number."""
        return lineno - self.elided_before(lineno)

    def visible_lines(self):
        """1-based real line numbers of all visible lines."""
        return list(self._visible)

    def line_at_display(self, row):
        """Real line number at a 1-based display row, or None."""
        if 1 <= row <= len(self._visible):
            return self._visible[row - 1]
        return None

    def containing_region(self, lineno):
        """Innermost region containing *lineno*, or None."""
        best = None
        for s, e, h in self.regions:
            if s <= lineno <= e and (best is None or s > best[0]):
                best = (s, e, h)
        return best

    # ── operations ─────────────────────────────────────────────────────

    def toggle(self, lineno):
        """Toggle the fold at *lineno*.

        A region starting at the line toggles directly; otherwise the
        innermost region containing the line toggles.  Returns True if
        a region was found.
        """
        region = None
        for s, e, h in self.regions:
            if s == lineno:
                region = (s, e, h)
                break
        if region is None:
            region = self.containing_region(lineno)
        if region is None:
            return False
        s, e, h = region
        if s in self.folded:
            self.folded.discard(s)
        else:
            self.folded.add(s)
        self._apply_elide()
        self.editor.refresh_sidebar_for_fold()
        return True

    def fold_all(self):
        self.folded = {s for s, _e, _h in self.regions}
        self._apply_elide()
        self.editor.refresh_sidebar_for_fold()

    def unfold_all(self):
        self.folded = set()
        self._apply_elide()
        self.editor.refresh_sidebar_for_fold()

    def unfold_containing(self, lineno):
        """Unfold every folded region containing *lineno*."""
        changed = False
        for s, e, _h in self.regions:
            if s in self.folded and s < lineno <= e:
                self.folded.discard(s)
                changed = True
        if changed:
            self._apply_elide()
            self.editor.refresh_sidebar_for_fold()


class FoldMargin(tk.Canvas):
    """Narrow margin with fold markers (▾ folded open / ▸ folded shut)."""

    WIDTH = 14
    MARKER_FG = '#888888'

    def __init__(self, parent, editor):
        super().__init__(parent, width=self.WIDTH, bd=0,
                         highlightthickness=0, bg=editor.LINE_NUM_BG)
        self.editor = editor
        self.fold = editor._fold
        self.bind('<Button-1>', self._on_click)
        # Redirect scrolling and context menus to the editor.
        self.bind('<MouseWheel>', self.editor._sidebar_mousewheel)
        self.bind('<Button-2>', lambda e: self.editor._line_text
                  .event_generate('<Button-2>', x=0, y=e.y))
        self.bind('<Button-3>', lambda e: self.editor._line_text
                  .event_generate('<Button-3>', x=0, y=e.y))

    def _line_height(self):
        text = self.editor.get_text_widget()
        try:
            info = text.dlineinfo(text.index('@0,0'))
        except tk.TclError:
            return 16
        return info[3] if info and info[3] else 16

    def redraw(self):
        """Redraw markers for the visible region headers."""
        self.delete('all')
        text = self.editor.get_text_widget()
        if not self.fold.regions:
            return
        try:
            top = int(float(text.index('@0,0')))
        except tk.TclError:
            return
        line_h = self._line_height()
        top_row = self.fold.display_line(top)
        height = self.winfo_height()
        for s, e, _h in self.fold.regions:
            if self.fold.is_elided(s):
                continue  # header itself hidden inside a folded ancestor
            row = self.fold.display_line(s)
            y = (row - top_row) * line_h + line_h // 2
            if -line_h <= y <= height + line_h:
                marker = '▸' if s in self.fold.folded else '▾'
                self.create_text(self.WIDTH // 2, y, text=marker,
                                 font=('TkFixedFont', 8),
                                 fill=self.MARKER_FG)

    def _on_click(self, event):
        text = self.editor.get_text_widget()
        try:
            top = int(float(text.index('@0,0')))
        except tk.TclError:
            return
        line_h = self._line_height()
        row = self.fold.display_line(top) + int(event.y // line_h)
        lineno = self.fold.line_at_display(row)
        if lineno is not None:
            self.fold.toggle(lineno)
