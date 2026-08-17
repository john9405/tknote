"""ParenMatch — subclass of idlelib's ParenMatch with our own config.

Uses idlelib's HyperParser-based bracket matching engine, bypassing
idleConf for configuration.  Accepts a Tk Text widget directly via
a thin adapter that satisfies idlelib's editwin protocol.
"""

import tkinter as tk

from idlelib.hyperparser import HyperParser
from idlelib.parenmatch import ParenMatch as _BaseParenMatch

from .editwin_adapter import EditwinAdapter


class ParenMatch(_BaseParenMatch):
    """idlelib ParenMatch subclass that accepts a tk.Text widget directly.

    Style options:
      - 'opener'   — highlight only the matching opener
      - 'parens'   — highlight both opener and closer
      - 'expression' — highlight entire expression
    """

    def __init__(self, text_widget):
        adapter = EditwinAdapter(text_widget)
        super().__init__(adapter)

    @classmethod
    def reload(cls):
        """Override: set config directly instead of reading from idleConf."""
        cls.STYLE = 'opener'
        cls.FLASH_DELAY = 500   # ms; 0 = stay until cursor moves
        cls.BELL = True         # bell on mismatch
        cls.HILITE_CONFIG = {
            'background': 'gray',
            'foreground': '#000000',
        }


# Initialize class config
ParenMatch.reload()


class PersistentParen:
    """Persistent highlight of the bracket pair at the cursor.

    Unlike ParenMatch's transient flash, keeps both halves of the pair
    highlighted while the cursor stays adjacent to any bracket
    (IDEA-style).  Refreshes on cursor movement with a small debounce.
    """

    TAG = 'paren_persist'
    BG = '#e3e3e3'
    DELAY = 120   # ms debounce
    BRACKETS = '([{)]}'

    def __init__(self, text_widget):
        self.text = text_widget
        self._adapter = EditwinAdapter(text_widget)
        self._after_id = None
        self.text.tag_config(self.TAG, background=self.BG)
        # Keep the selection readable above the highlight.
        self.text.tag_lower(self.TAG, 'sel')

    def schedule_check(self, event=None):
        """Recheck after the debounce (KeyRelease/ButtonRelease)."""
        if self._after_id is not None:
            try:
                self.text.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self._after_id = self.text.after(self.DELAY, self._check)

    def clear(self):
        self.text.tag_remove(self.TAG, '1.0', 'end')

    def _check(self):
        self._after_id = None
        self.clear()
        text = self.text
        for index in ('insert-1c', 'insert'):
            try:
                char = text.get(index)
            except tk.TclError:
                return
            if char in self.BRACKETS:
                # HyperParser wants the index inside the pair (or on
                # the closer) — step past an opener.
                hp_index = f'{index}+1c' if char in '([{' else index
                try:
                    hp = HyperParser(self._adapter, hp_index)
                    br = hp.get_surrounding_brackets()
                except Exception:
                    return
                if br:
                    self._tag_pair(br[0], br[1])
                return

    def _tag_pair(self, opener, closer):
        self.text.tag_add(self.TAG, opener, f'{opener}+1c')
        self.text.tag_add(self.TAG, closer, f'{closer}+1c')
