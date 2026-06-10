"""History class — re-export from Python's idlelib standard library."""

from idlelib.history import History

# Enable cyclic history navigation by default (wraps around)
History.cyclic = True

__all__ = ['History']
