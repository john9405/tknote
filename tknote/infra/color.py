"""Color / syntax-highlighting delegator for Python — subclass of idlelib's
ColorDelegator with light-theme colors.

Uses idlelib's battle-tested regex-based tokenizer (make_pat) and incremental
TODO-tag coloring engine.  Only LoadTagDefs() and config_colors() are
overridden to provide our own color scheme.
"""

from idlelib.colorizer import ColorDelegator as _BaseColorDelegator, color_config


# ── Tag color definitions ──────────────────────────────────────────────────
#
# idlelib's tokenizer produces these tags (via regex groups + _add_tags_in_section):
#   KEYWORD      — Python keywords + match/case soft keywords
#   BUILTIN      — built-in names (print, len, etc.)
#   STRING       — all string literals (single, double, triple-quoted, f-strings)
#   COMMENT      — # comments
#   DEFINITION   — function/class names after def/class
#   SYNC         — internal state-tracking tag (must be invisible)
#
# We also add:
#   TODO         — internal dirty-region marker (must be invisible)
#   ERROR        — for syntax error display in shell
#   hit          — for search result highlighting

# IDLE Classic color scheme (matches Python's default IDLE theme)
# keyword=orange, builtin=purple, comment=red, string=green, definition=blue
_TAGDEFS = {
    "COMMENT":    {'foreground': '#dd0000', 'background': None},
    "KEYWORD":    {'foreground': '#ff7700', 'background': None},
    "BUILTIN":    {'foreground': '#900090', 'background': None},
    "STRING":     {'foreground': '#00aa00', 'background': None},
    "DEFINITION": {'foreground': '#0000ff', 'background': None},
    "SYNC":       {'foreground': None,      'background': None},
    "TODO":       {'foreground': None,      'background': None},
    "ERROR":      {'foreground': '#000000', 'background': '#ff7777'},
    "hit":        {'foreground': '#ffffff', 'background': '#000000'},
}


class ColorDelegator(_BaseColorDelegator):
    """idlelib ColorDelegator subclass with light-theme colors.

    Inherits the full TODO-tag incremental coloring engine, regex tokenizer
    (make_pat), and recolorize_main() from idlelib.  Only the color scheme
    and Text widget appearance are customized.
    """

    # ── Color scheme ───────────────────────────────────────────────────

    def LoadTagDefs(self):
        """Override idleConf-based tag loading with our own colors."""
        self.tagdefs = dict(_TAGDEFS)

    def config_colors(self):
        """Configure Text widget appearance, then apply tag colors.

        Called automatically by setdelegate() when the colorizer is
        inserted into a Percolator chain, and by ResetColorizer().
        """
        text = self.delegate
        if text is not None:
            text.configure(
                foreground='#000000',
                background='#ffffff',
                insertbackground='#000000',
                selectforeground='#ffffff',
                selectbackground='gray',
            )
        # Apply tag colors from tagdefs
        super().config_colors()
        # Ensure text selection is always visible on top of syntax tags
        if text is not None:
            text.tag_raise('sel')

    # ── Convenience ────────────────────────────────────────────────────

    def recolor_full(self):
        """Force a full-document recolor (e.g. after bulk file load).

        Re-enables colorizing in case it was disabled by removefilter/set_text.
        """
        self.allow_colorizing = True
        self.stop_colorizing = False
        self.removecolors()
        self.notify_range("1.0", "end")
