"""ParenMatch — subclass of idlelib's ParenMatch with our own config.

Uses idlelib's HyperParser-based bracket matching engine, bypassing
idleConf for configuration.  Accepts a Tk Text widget directly via
a thin adapter that satisfies idlelib's editwin protocol.
"""

from idlelib.parenmatch import ParenMatch as _BaseParenMatch


class _EditwinAdapter:
    """Minimal adapter so idlelib's ParenMatch and HyperParser see the
    attributes they expect from an EditorWindow-like object."""

    def __init__(self, text_widget):
        self.text = text_widget
        self.indentwidth = 4
        self.tabwidth = 4
        self.prompt_last_line = ''   # not a shell, so no prompt
        self.num_context_lines = (50, 500, 5000)

    @property
    def text_frame(self):
        return self.text.master

    def is_char_in_string(self, text_index):
        """Check if a text index is inside a string, via colorizer tags."""
        return "STRING" in self.text.tag_names(text_index)

    def _build_char_in_string_func(self, startindex):
        """Build a function that checks if an offset is inside a string."""
        def inner(offset, _startindex=startindex,
                  _icis=self.is_char_in_string):
            return _icis(_startindex + "+%dc" % offset)
        return inner


class ParenMatch(_BaseParenMatch):
    """idlelib ParenMatch subclass that accepts a tk.Text widget directly.

    Style options:
      - 'opener'   — highlight only the matching opener
      - 'parens'   — highlight both opener and closer
      - 'expression' — highlight entire expression
    """

    def __init__(self, text_widget):
        adapter = _EditwinAdapter(text_widget)
        super().__init__(adapter)

    @classmethod
    def reload(cls):
        """Override: set config directly instead of reading from idleConf."""
        cls.STYLE = 'opener'
        cls.FLASH_DELAY = 500   # ms; 0 = stay until cursor moves
        cls.BELL = True         # bell on mismatch
        cls.HILITE_CONFIG = {
            'background': '#b0b0b0',
            'foreground': '#000000',
        }


# Initialize class config
ParenMatch.reload()
