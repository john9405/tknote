"""Doc lookup for the completion window's preview panel.

Resolves the object a completion candidate refers to and returns a
compact signature + docstring, like IDEA's completion documentation.
"""

import __main__
import builtins
import inspect
import sys

from idlelib.calltip import get_argspec

MAX_CHARS = 600


def _namespace():
    """Namespace spanning builtins, sys.modules and __main__."""
    return {**builtins.__dict__, **sys.modules, **__main__.__dict__}


def resolve_doc(comp_what, name, mode):
    """Return documentation text for completion candidate *name*.

    comp_what is the expression the completion applies to ('' for
    top-level names); mode is idlelib.autocomplete.ATTRS or .FILES.

    Only *comp_what* is ever evaluated — a short expression already
    present in the editor (same precedent as idlelib's get_entity).
    The completion name itself is resolved via getattr, never eval.
    All exceptions are swallowed: a doc preview must never crash.
    """
    from idlelib.autocomplete import ATTRS
    if mode != ATTRS:
        return ''
    try:
        if comp_what:
            obj = eval(comp_what, _namespace())  # noqa: S307 — see docstring
            obj = getattr(obj, name)
        else:
            obj = _namespace().get(name)
        if obj is None:
            return ''
        if callable(obj):
            text = get_argspec(obj)
        else:
            text = f"type: {type(obj).__name__}"
            try:
                r = repr(obj)
            except BaseException:
                r = ''
            if r and len(r) < 100:
                text += f" = {r}"
        doc = inspect.getdoc(obj)
        if doc:
            first = doc.strip().split('\n', 1)[0]
            text = f"{text}\n{first}" if text else first
        return text[:MAX_CHARS]
    except BaseException:
        return ''
