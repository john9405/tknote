"""Editor infrastructure — re-exports from Python's idlelib standard library.

Uses idlelib's battle-tested Delegator, WidgetRedirector, and Percolator
directly rather than maintaining our own copies.
"""

from idlelib.delegator import Delegator
from idlelib.redirector import WidgetRedirector, OriginalCommand
from idlelib.percolator import Percolator

__all__ = ['Delegator', 'WidgetRedirector', 'OriginalCommand', 'Percolator']
