"""CalltipEx — idlelib calltip plus subscript ('[') tips.

idlelib's Calltip only handles '('.  CalltipEx also shows a tip for
subscript expressions: a callable before '[' gets its signature,
anything else gets its type.  Dict/string literals ('{', quotes) get
no tip — the expression before them is empty.
"""

from idlelib.calltip import Calltip, get_argspec, get_entity
from idlelib.hyperparser import HyperParser


class CalltipEx(Calltip):
    """Calltip supporting both '(' and '[' brackets."""

    def open_calltip(self, evalfuncs):
        """Open a calltip for '(' (via the base class) or '['."""
        hp = HyperParser(self.editwin, "insert")
        if hp.get_surrounding_brackets('('):
            return super().open_calltip(evalfuncs)

        sur_bracket = hp.get_surrounding_brackets('[')
        if not sur_bracket:
            self.remove_calltip_window()
            return

        # Don't reopen a tip already shown for this exact bracket.
        if self.active_calltip:
            opener_line, opener_col = map(int, sur_bracket[0].split('.'))
            if (opener_line, opener_col) == (
                    self.active_calltip.parenline,
                    self.active_calltip.parencol):
                return

        hp.set_index(sur_bracket[0])
        try:
            expression = hp.get_expression()
        except ValueError:
            expression = None
        if not expression or '(' in expression:
            # Empty expression (literal list) or involves a call —
            # evaluating those is not worth the surprise.
            return

        obj = get_entity(expression)
        if obj is None:
            return
        if callable(obj):
            tip = get_argspec(obj)
        else:
            tip = f"type: {type(obj).__name__}"
        if not tip:
            return

        self.remove_calltip_window()
        self.active_calltip = self._calltip_window()
        self.active_calltip.showtip(tip, sur_bracket[0], sur_bracket[1])
