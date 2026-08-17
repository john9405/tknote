"""AutoCompleteEx — idlelib autocomplete with a doc preview panel.

Subclasses idlelib's AutoComplete/AutoCompleteWindow and adds a
documentation strip at the bottom of the completion popup (IDEA's
completion docs, in miniature).
"""

import tkinter as tk

from idlelib import autocomplete_w
from idlelib.autocomplete import AutoComplete

from ..infra.doclookup import resolve_doc


class AutoCompleteWindowEx(autocomplete_w.AutoCompleteWindow):
    """AutoCompleteWindow with a doc label under the candidate list."""

    DOC_FONT = ('TkFixedFont', 9)
    DOC_BG = '#ffffd0'

    def __init__(self, widget, tags=None, auto=None):
        self._auto = auto
        self._doc_label = None
        super().__init__(widget, tags)

    # ── doc panel ──────────────────────────────────────────────────────

    def _update_doc(self):
        """Update the doc label for the current selection."""
        if self._doc_label is None:
            return
        try:
            cursel = int(self.listbox.curselection()[0])
            name = self.completions[cursel]
        except (tk.TclError, IndexError, ValueError):
            return
        comp_what, mode = '', self.mode
        if self._auto is not None:
            comp_what = getattr(self._auto, '_last_comp_what', '')
            mode = getattr(self._auto, '_last_mode', self.mode)
        doc = resolve_doc(comp_what, name, mode)
        self._doc_label.configure(text=doc)

    # ── overrides ──────────────────────────────────────────────────────

    def _selection_changed(self):
        super()._selection_changed()
        self._update_doc()

    def show_window(self, comp_lists, index, complete, mode, userWantsWin):
        """Show the completion list (with doc panel), bind events."""
        result = super().show_window(comp_lists, index, complete, mode,
                                     userWantsWin)
        # If the window was shown, add the doc label to its Toplevel.
        if self.autocompletewindow is not None:
            acw = self.autocompletewindow
            self._doc_label = tk.Label(
                acw, text='', justify=tk.LEFT, anchor=tk.NW,
                wraplength=420, font=self.DOC_FONT, bg=self.DOC_BG,
                padx=4, pady=2)
            self._doc_label.pack(side=tk.BOTTOM, fill=tk.X)
            self._update_doc()
        return result


class AutoCompleteEx(AutoComplete):
    """AutoComplete that tracks the completion context for doc previews."""

    AutoCompleteWindow = AutoCompleteWindowEx

    # Pop the completion list sooner than IDLE's default 2000 ms.
    popupwait = 500

    def __init__(self, editwin=None, tags=None):
        self._last_comp_what = ''
        self._last_mode = None
        super().__init__(editwin, tags)

    def _make_autocomplete_window(self):
        return self.AutoCompleteWindow(self.text, tags=self.tags, auto=self)

    def fetch_completions(self, what, mode):
        """Hook: remember the expression being completed."""
        self._last_comp_what = what
        self._last_mode = mode
        return super().fetch_completions(what, mode)
