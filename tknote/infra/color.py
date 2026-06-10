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

_TAGDEFS = {
    "COMMENT":    {'foreground': '#6a9955', 'background': None},
    "KEYWORD":    {'foreground': '#0000ff', 'background': None},
    "BUILTIN":    {'foreground': '#795e26', 'background': None},
    "STRING":     {'foreground': '#a31515', 'background': None},
    "DEFINITION": {'foreground': '#267f99', 'background': None},
    "SYNC":       {'foreground': None,      'background': None},
    "TODO":       {'foreground': None,      'background': None},
    "ERROR":      {'foreground': '#f44747', 'background': None},
    "hit":        {'foreground': '#000000', 'background': '#ffff00'},
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
                foreground='#1e1e1e',
                background='#ffffff',
                insertbackground='#1e1e1e',
                selectforeground='#ffffff',
                selectbackground='#264f78',
                inactiveselectbackground='#d4d4d4',
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
