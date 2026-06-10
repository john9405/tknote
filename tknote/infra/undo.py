"""Undo infrastructure — re-exports from Python's idlelib standard library.

Uses idlelib's battle-tested UndoDelegator, InsertCommand, DeleteCommand,
and CommandSequence rather than maintaining our own copies.
"""

from idlelib.undo import (InsertCommand, DeleteCommand,
                           CommandSequence, UndoDelegator)

__all__ = ['InsertCommand', 'DeleteCommand', 'CommandSequence', 'UndoDelegator']
