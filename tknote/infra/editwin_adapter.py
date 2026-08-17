"""Shared editwin adapter — satisfies idlelib's EditorWindow protocol.

idlelib's HyperParser, AutoComplete, Calltip, and ParenMatch all expect
an EditorWindow-like object.  This adapter lets them work with a plain
tk.Text widget (tknote's EditorWidget):

  - HyperParser needs prompt_last_line / num_context_lines and
    _build_char_in_string_func (hyperparser.py:38-49)
  - AutoComplete needs flist (None → local mode) and text
  - codecontext-style consumers need getlineno() / text_frame
"""


class EditwinAdapter:
    """Minimal adapter so idlelib modules see EditorWindow-like attrs."""

    def __init__(self, text_widget):
        self.text = text_widget
        self.indentwidth = 4
        self.tabwidth = 8
        self.prompt_last_line = ''   # not a shell, so no prompt
        self.num_context_lines = (50, 500, 5000)
        self.flist = None            # no subprocess → autocomplete local mode

    @property
    def text_frame(self):
        return self.text.master

    def getlineno(self, index):
        """Return the line number of a Tk text index."""
        return int(float(self.text.index(index)))

    def is_char_in_string(self, text_index):
        """Check if a text index is inside a string, via colorizer tags."""
        return "STRING" in self.text.tag_names(text_index)

    def _build_char_in_string_func(self, startindex):
        """Build a function that checks if an offset is inside a string."""
        def inner(offset, _startindex=startindex,
                  _icis=self.is_char_in_string):
            return _icis(_startindex + "+%dc" % offset)
        return inner
