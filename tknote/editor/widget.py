"""EditorWidget — code editor with Text-based line numbers & highlighting.

Based on idlelib's EditorWindow + LineNumbers design:
  - Text widget sidebar for line numbers (not Canvas)
  - EndLineDelegator in Percolator chain for reliable line-count sync
  - Integrated ParenMatch, AutoIndent, AutoComplete
  - Line selection by dragging on the sidebar
"""

import itertools
import tkinter as tk

from ..infra.delegator import Delegator, Percolator
from ..infra.undo import UndoDelegator
from ..infra.color import ColorDelegator
from ..infra.parenmatch import ParenMatch
from ..infra.autoindent import AutoIndent
from ..infra.keywordhint import KeywordHint


# ── Editwin adapter (shared by ParenMatch and AutoComplete) ────────────────

class _EditwinAdapter:
    """Minimal adapter so idlelib's HyperParser/AutoComplete see the
    attributes they expect from an EditorWindow-like object."""

    def __init__(self, text_widget):
        self.text = text_widget
        self.indentwidth = 4
        self.tabwidth = 4
        self.prompt_last_line = ''
        self.num_context_lines = (50, 500, 5000)
        self.flist = None  # no subprocess → autocomplete uses local mode

    @property
    def text_frame(self):
        return self.text.master

    def is_char_in_string(self, text_index):
        return "STRING" in self.text.tag_names(text_index)

    def _build_char_in_string_func(self, startindex):
        def inner(offset, _startindex=startindex,
                  _icis=self.is_char_in_string):
            return _icis(_startindex + "+%dc" % offset)
        return inner


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_lineno(text, index):
    """Return the line number of a Tk text index."""
    return int(float(text.index(index)))


def _get_end_linenumber(text):
    """Return the last line number in the text widget."""
    return _get_lineno(text, 'end-1c')


# ── EndLineDelegator ───────────────────────────────────────────────────────

class EndLineDelegator(Delegator):
    """Notify a callback whenever the line count changes."""

    def __init__(self, changed_callback):
        Delegator.__init__(self)
        self.changed_callback = changed_callback

    def insert(self, index, chars, tags=None):
        result = self.delegate.insert(index, chars, tags)
        self.changed_callback(_get_end_linenumber(self.delegate))
        return result

    def delete(self, index1, index2=None):
        result = self.delegate.delete(index1, index2)
        self.changed_callback(_get_end_linenumber(self.delegate))
        return result


# ── EditorWidget ───────────────────────────────────────────────────────────

class EditorWidget(tk.Frame):
    """Self-contained code editor with line numbers, undo, and highlighting.

    Integrates idlelib-style features:
      - Text-widget sidebar with drag-to-select-lines
      - EndLineDelegator for reliable line-number sync
      - ParenMatch for bracket highlighting
      - AutoIndent for smart Tab/Enter/Backspace
    """

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
        self._sidebar_width = None
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        # ── Line-number sidebar (Text widget, not Canvas) ──
        self._line_text = tk.Text(
            self, width=1, wrap=tk.NONE,
            padx=2, pady=4,
            borderwidth=0, highlightthickness=0,
            bg=self.LINE_NUM_BG, fg=self.LINE_NUM_FG,
            font=self.EDITOR_FONT,
            takefocus=False, exportselection=False,
        )
        self._line_text.config(state=tk.DISABLED)
        self._line_text.tag_config('linenumber', justify=tk.RIGHT)
        self._line_text.insert('end', '1', 'linenumber')
        # Prepare for grid; show_sidebar / hide_sidebar manage visibility
        self._prev_end = 1
        self._line_text.pack(side=tk.LEFT, fill=tk.Y)

        # ── Text frame (holds main editor Text + scrollbar) ──
        text_frame = tk.Frame(self)
        text_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Main editor Text ──
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

        # ── Scrollbar ──
        self._scrollbar = tk.Scrollbar(
            text_frame, orient=tk.VERTICAL, command=self._on_scrollbar)
        self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Redirect yscrollcommand from main text — updates both scrollbar
        # and sidebar via redirect_yscroll_event
        self._text.configure(yscrollcommand=self._redirect_yscroll)

        # ── Percolator chain setup ──
        # Order: text → EndLineDelegator (tracks line count) →
        #        UndoDelegator → ColorDelegator
        self._percolator = Percolator(self._text)

        # Track line count via a delegator in the percolator chain
        self._endline = EndLineDelegator(self._update_sidebar_text)
        self._percolator.insertfilter(self._endline)

        self._undo = UndoDelegator()
        self._percolator.insertfilterafter(self._undo, after=self._endline)
        self._undo.set_saved_change_hook(self._on_saved_changed)
        self._text.undo_block_start = self._undo.undo_block_start
        self._text.undo_block_stop = self._undo.undo_block_stop

        self._color = ColorDelegator()
        self._percolator.insertfilter(self._color)

        # ── Integrated features ──
        self._paren_match = ParenMatch(self._text)
        self._auto_indent = AutoIndent(self._text, undo=self._undo)
        self._autocomplete = self._setup_autocomplete()
        self._calltip = self._setup_calltip()
        self._keyword_hint = KeywordHint(self._text)
        self._keyword_hint.attach()

        # ── Key bindings ──
        self._setup_bindings()

        # ── Sidebar mouse bindings ──
        self._setup_sidebar_bindings()

        # Initial sync
        end = _get_end_linenumber(self._text)
        self._update_sidebar_text(end)

    def _setup_bindings(self):
        """Bind editor keys — smart indent, backspace, paren close, etc."""
        text = self._text

        # Tab: try autocomplete first; fall back to auto-indent
        text.bind('<Tab>', self._handle_tab)
        text.bind('<Return>', self._auto_indent.newline_and_indent_event)
        text.bind('<Shift-Tab>', self._auto_indent.smart_backspace_event)
        text.bind('<BackSpace>', self._handle_backspace)
        text.bind('<Home>', self._home_callback)
        text.bind('<Shift-Home>', self._home_callback)

        # Virtual events mirror idlelib's EditorWindow entry points.
        text.bind('<<smart-indent>>', self._handle_tab)
        text.bind('<<newline-and-indent>>',
                  self._auto_indent.newline_and_indent_event)
        text.bind('<<smart-backspace>>', self._auto_indent.smart_backspace_event)
        text.bind('<<beginning-of-line>>', self._home_callback)
        text.bind('<<center-insert>>', self.center_insert_event)

        # Autocomplete triggers
        text.bind('<Control-space>', self._autocomplete.force_open_completions_event)
        text.bind('<Key-period>', self._autocomplete.try_open_completions_event, add=True)
        text.bind('<Key-slash>', self._autocomplete.try_open_completions_event, add=True)

        # Calltip on opening parenthesis
        text.bind('<Key-parenleft>', self._calltip.try_open_calltip_event, add=True)
        text.bind('<Key-bracketleft>', self._on_bracket_open, add=True)

        # Paren close → flash match
        for closer in (')', ']', '}'):
            text.bind(closer, self._on_paren_close)

        # Keyword hint — force-show on Ctrl+Shift+K / Cmd+Shift+K
        text.bind('<Control-Shift-K>', self._keyword_hint.force_show)
        text.bind('<Command-Shift-K>', self._keyword_hint.force_show)

    def _setup_autocomplete(self):
        """Set up idlelib autocomplete with editwin adapter."""
        from idlelib.autocomplete import AutoComplete
        adapter = _EditwinAdapter(self._text)
        return AutoComplete(editwin=adapter, tags=None)

    def _setup_calltip(self):
        """Set up idlelib calltip (function signature hints)."""
        from idlelib.calltip import Calltip
        adapter = _EditwinAdapter(self._text)
        return Calltip(editwin=adapter)

    def _on_bracket_open(self, event):
        """Open bracket '[' — try calltip for e.g. dict access, but
        don't interfere with normal typing."""
        return None  # let normal insertion proceed

    def _handle_tab(self, event):
        """Tab: try autocomplete first, fall back to auto-indent."""
        # If autocomplete window is open, let it handle Tab
        ac = self._autocomplete
        if ac.autocompletewindow and ac.autocompletewindow.is_active():
            return ac.autocomplete_event(event)
        # Try opening completions
        result = ac.autocomplete_event(event)
        if result == "break":
            return "break"
        # No completions — use auto-indent
        return self._auto_indent.smart_indent_event(event)

    def _setup_sidebar_bindings(self):
        """Bind sidebar mouse events for line selection and scroll sync."""
        lt = self._line_text

        # Redirect focus to main text
        lt.bind('<FocusIn>', lambda e: self._text.focus_set())

        # Redirect mouse wheel
        lt.bind('<MouseWheel>', self._sidebar_mousewheel)
        lt.bind('<Button-4>', self._sidebar_mousewheel)
        lt.bind('<Button-5>', self._sidebar_mousewheel)

        # Redirect right-click
        lt.bind('<Button-2>', self._sidebar_button_redirect)
        lt.bind('<Button-3>', self._sidebar_button_redirect)
        lt.bind('<Control-Button-1>', self._sidebar_button_redirect)

        # Line selection by drag on sidebar
        self._sidebar_drag_start = None
        self._sidebar_drag_last_y = None
        self._sidebar_autoscroll_after_id = None
        lt.bind('<Button-1>', self._sidebar_b1_down)
        lt.bind('<ButtonRelease-1>', self._sidebar_b1_up)
        lt.bind('<B1-Motion>', self._sidebar_b1_motion)
        lt.bind('<B1-Leave>', self._sidebar_b1_leave)
        lt.bind('<B1-Enter>', self._sidebar_b1_enter)

    # ── Scroll sync ───────────────────────────────────────────────────────

    def _on_scrollbar(self, *args):
        """Scrollbar command — syncs both main text and sidebar."""
        self._text.yview(*args)
        self._sync_sidebar_yview()

    def _redirect_yscroll(self, *args):
        """Redirect yscrollcommand from main text to scrollbar + sidebar."""
        self._scrollbar.set(*args)
        if args:
            self._line_text.yview_moveto(args[0])

    def yview(self, *args):
        result = self._text.yview(*args)
        if args:
            self._sync_sidebar_yview()
        return result

    def _sync_sidebar_yview(self):
        """Match the sidebar scroll position to the main text widget."""
        try:
            first, _last = self._text.yview()
            self._line_text.yview_moveto(first)
        except tk.TclError:
            pass

    def _sidebar_mousewheel(self, event):
        """Redirect mousewheel from sidebar to main text."""
        if getattr(event, 'num', None) in (4, 5):
            direction = -1 if event.num == 4 else 1
            self._text.yview_scroll(direction, 'units')
        else:
            self._text.event_generate('<MouseWheel>', x=0, y=event.y,
                                      delta=event.delta)
        return 'break'

    def _sidebar_button_redirect(self, event):
        """Redirect mouse buttons (right-click, etc.) to main text."""
        self._text.focus_set()
        self._text.event_generate(f'<Button-{event.num}>', x=0, y=event.y)
        return 'break'

    # ── Sidebar line selection ────────────────────────────────────────────

    def _sidebar_b1_down(self, event):
        """Mouse-down on sidebar: start line selection."""
        self._text.focus_set()
        lineno = self._sidebar_lineno_at_y(event.y)
        self._sidebar_drag_start = lineno
        self._sidebar_drag_last_y = event.y
        self._sidebar_update_selection(event.y)
        return 'break'

    def _sidebar_b1_up(self, event):
        """Mouse-up on sidebar: finish line selection."""
        self._sidebar_drag_start = None
        self._sidebar_drag_last_y = None
        self._cancel_sidebar_autoscroll()
        self._text.event_generate('<ButtonRelease-1>', x=0, y=event.y)
        return 'break'

    def _sidebar_b1_motion(self, event):
        """Mouse-drag on sidebar: extend selection to dragged line."""
        if self._sidebar_drag_start is None:
            return
        self._sidebar_drag_last_y = event.y
        self._sidebar_update_selection(event.y)
        return 'break'

    def _sidebar_b1_leave(self, event):
        if self._sidebar_drag_start is None:
            return
        self._sidebar_drag_last_y = event.y
        if self._sidebar_autoscroll_after_id is None:
            self._sidebar_autoscroll_after_id = self._line_text.after(
                0, self._sidebar_auto_scroll)

    def _sidebar_b1_enter(self, event):
        self._cancel_sidebar_autoscroll()

    def _sidebar_lineno_at_y(self, y):
        lineno = _get_lineno(self._text, f"@0,{y}")
        return max(1, min(lineno, _get_end_linenumber(self._text)))

    def _sidebar_update_selection(self, y):
        if self._sidebar_drag_start is None:
            return
        lineno = self._sidebar_lineno_at_y(y)
        a = min(self._sidebar_drag_start, lineno)
        b = max(self._sidebar_drag_start, lineno)
        self._text.tag_remove("sel", "1.0", "end")
        self._text.tag_add("sel", f"{a}.0", f"{b + 1}.0")
        insert_line = lineno if lineno == a else lineno + 1
        self._text.mark_set("insert", f"{insert_line}.0")
        self._text.see("insert")

    def _sidebar_auto_scroll(self):
        self._sidebar_autoscroll_after_id = None
        y = self._sidebar_drag_last_y
        if self._sidebar_drag_start is None or y is None:
            return

        height = self._line_text.winfo_height()
        if y < 0:
            self._text.yview_scroll(-1 + y, 'pixels')
            self._sidebar_update_selection(y)
        elif y > height:
            self._text.yview_scroll(1 + y - height, 'pixels')
            self._sidebar_update_selection(y)
        else:
            return

        self._sidebar_autoscroll_after_id = self._line_text.after(
            50, self._sidebar_auto_scroll)

    def _cancel_sidebar_autoscroll(self):
        if self._sidebar_autoscroll_after_id is None:
            return
        try:
            self._line_text.after_cancel(self._sidebar_autoscroll_after_id)
        except tk.TclError:
            pass
        self._sidebar_autoscroll_after_id = None

    # ── Sidebar text update ───────────────────────────────────────────────

    def _update_sidebar_text(self, end):
        """Sync the sidebar Text widget line count with the main editor."""
        if end == self._prev_end:
            return

        # Adjust sidebar width to accommodate the largest line number
        width_diff = len(str(end)) - len(str(self._prev_end))
        if width_diff:
            cur_width = int(float(self._line_text['width']))
            self._line_text['width'] = cur_width + width_diff

        # Temporarily enable sidebar text for editing
        self._line_text.config(state=tk.NORMAL)
        try:
            if end > self._prev_end:
                # Lines added — append new line numbers
                new_text = '\n'.join(itertools.chain(
                    [''],
                    map(str, range(self._prev_end + 1, end + 1)),
                ))
                self._line_text.insert('end -1c', new_text, 'linenumber')
            else:
                # Lines removed — delete extra line numbers
                self._line_text.delete(f'{end + 1}.0 -1c', 'end -1c')
        finally:
            self._line_text.config(state=tk.DISABLED)

        self._prev_end = end

    # ── Smart key handlers ────────────────────────────────────────────────

    def _handle_backspace(self, event):
        """Backspace: use smart backspace if at line-start whitespace."""
        # Check if we should use smart backspace
        chars = self._text.get("insert linestart", "insert")
        if chars and chars[-1] in ' \t':
            return self._auto_indent.smart_backspace_event(event)
        # Regular backspace — let Tk handle it
        return None

    def _on_paren_close(self, event):
        """Handle closing bracket key — flash matching opener."""
        self._paren_match.paren_closed_event(event)
        # Also perform the normal insertion (not break)
        return None

    def _home_callback(self, event):
        """Move between first nonblank character and physical line start."""
        if event and (event.state & 4) != 0 and event.keysym == "Home":
            return None

        line = self._text.get("insert linestart", "insert lineend")
        for insertpt, char in enumerate(line):
            if char not in (' ', '\t'):
                break
        else:
            insertpt = len(line)

        current_col = int(self._text.index("insert").split('.')[1])
        if insertpt == current_col:
            insertpt = 0

        dest = f"insert linestart+{insertpt}c"
        if event and (event.state & 1) != 0:
            self._extend_selection_to(dest)
        else:
            self._text.tag_remove("sel", "1.0", "end")

        self._text.mark_set("insert", dest)
        self._text.see("insert")
        return "break"

    def _extend_selection_to(self, dest):
        """Extend the selection to dest using idlelib's Home-key behavior."""
        try:
            sel_first = self._text.index("sel.first")
            sel_last = self._text.index("sel.last")
        except tk.TclError:
            self._text.mark_set("my_anchor", "insert")
        else:
            if self._text.compare(sel_first, "<", self._text.index("insert")):
                self._text.mark_set("my_anchor", sel_first)
            else:
                self._text.mark_set("my_anchor", sel_last)

        first = self._text.index(dest)
        last = self._text.index("my_anchor")
        if self._text.compare(first, ">", last):
            first, last = last, first
        self._text.tag_remove("sel", "1.0", "end")
        self._text.tag_add("sel", first, last)

    def center_insert_event(self, event=None):
        """Center the insert cursor vertically in the editor viewport."""
        self.center()
        return "break"

    def center(self, mark="insert"):
        """Scroll so mark is near the vertical center, like idlelib."""
        top, bottom = self._get_window_lines()
        lineno = _get_lineno(self._text, mark)
        height = max(1, bottom - top)
        self._text.yview(max(0, lineno - height // 2))

    def _get_window_lines(self):
        top = _get_lineno(self._text, "@0,0")
        bottom = _get_lineno(
            self._text, f"@0,{max(0, self._text.winfo_height() - 1)}")
        return top, max(top + 1, bottom)

    # ── Public API ────────────────────────────────────────────────────────

    def get_text(self):
        return self._text.get('1.0', 'end-1c')

    def set_text(self, content):
        """Replace all text.  Bypasses the colorizer during bulk load."""
        # Bulk replace inside undo block; keep colorizer in chain
        self._undo.undo_block_start()
        try:
            self._text.delete('1.0', 'end')
            if content:
                self._text.insert('1.0', content)
        finally:
            self._undo.undo_block_stop()
        self._undo.reset_undo()
        self._undo.set_saved(1)
        # Trigger full recolor (colorizer stays in Percolator chain)
        self._color.recolor_full()

    def get_text_widget(self):
        return self._text

    def undo(self):
        self._undo.undo_event(None)

    def redo(self):
        self._undo.redo_event(None)

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
        return not self._undo.get_saved()

    def set_saved(self):
        self._undo.set_saved(1)

    def flash_paren(self):
        """Highlight surrounding brackets (menu / shortcut entry point)."""
        self._paren_match.flash_paren_event()

    def show_keyword_hint(self):
        """Force-show keyword hint for the word at the cursor."""
        self._keyword_hint.force_show()

    # ── Text widget delegation ────────────────────────────────────────────

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
        if flag is None:
            return self.is_modified()
        if flag:
            self._undo.set_saved(0)
        else:
            self._undo.set_saved(1)
        return self.is_modified()

    def edit_undo(self):
        self.undo()

    def edit_redo(self):
        self.redo()

    def edit_separator(self):
        self._undo.can_merge = False

    def focus_set(self):
        self._text.focus_set()

    def get_line_numbers_visible(self):
        return self._line_numbers_visible

    def toggle_line_numbers(self):
        self._line_numbers_visible = not self._line_numbers_visible
        if self._line_numbers_visible:
            self._line_text.pack(side=tk.LEFT, fill=tk.Y)
        else:
            self._line_text.pack_forget()
        return self._line_numbers_visible

    def _on_saved_changed(self):
        """Called by idlelib UndoDelegator when saved state changes."""
        # The '_modified' attribute is kept for external consumers;
        # actual state lives in the UndoDelegator.
        self._modified = not self._undo.get_saved()

    def bind(self, sequence=None, func=None, add=None):
        return self._text.bind(sequence, func, add)

    def event_generate(self, sequence, **kw):
        self._text.event_generate(sequence, **kw)

    def destroy(self):
        try:
            self._cancel_sidebar_autoscroll()
            self._keyword_hint.detach()
            self._color.close()
            self._percolator.close()
        except Exception:
            pass
        super().destroy()
