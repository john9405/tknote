"""PythonShell — idlelib-style single-Text-widget Python shell with iomark.

Interaction and execution flow ported from idlelib's PyShell /
ModifiedInterpreter:
  - Enter submits input and continues multi-line blocks
  - Clicking/positioning in the read-only area and pressing Enter
    recalls that command to the input area
  - Ctrl+C interrupts: idle → new prompt, subprocess → SIGINT to child,
    in-process → KeyboardInterrupt (SIGINT handler + interrupt_main)
  - Ctrl+P/N and Up/Down navigate history
  - Tracebacks show the actual source lines (linecache stuffing)
  - Syntax errors highlight the offending position (ERROR tag)
"""

import bdb
import code
import linecache
import os
import re
import select
import signal
import string
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

from ..infra.delegator import Delegator, Percolator
from ..infra.undo import UndoDelegator
from ..infra.color import ColorDelegator
from ..infra.autoindent import AutoIndent
from ..debugger import Debugger
from .history import History


# ── ModifiedUndoDelegator — prevent editing before iomark ──────────────────

class _ModifiedUndoDelegator(UndoDelegator):
    """UndoDelegator that forbids insert/delete before the I/O mark.

    Also preserves the "stdin" tag when undoing deletions.
    """

    def insert(self, index, chars, tags=None):
        try:
            if self.delegate.compare(index, "<", "iomark"):
                self.delegate.bell()
                return
        except tk.TclError:
            pass
        UndoDelegator.insert(self, index, chars, tags)

    def delete(self, index1, index2=None):
        try:
            if self.delegate.compare(index1, "<", "iomark"):
                self.delegate.bell()
                return
        except tk.TclError:
            pass
        UndoDelegator.delete(self, index1, index2)

    def undo_event(self, event):
        # Monkey-patch insert to preserve "stdin" tag on undo
        orig_insert = self.delegate.insert
        self.delegate.insert = \
            lambda index, chars: orig_insert(index, chars, "stdin")
        try:
            super().undo_event(event)
        finally:
            self.delegate.insert = orig_insert


# ── UserInputTaggingDelegator — auto-tag keystrokes ────────────────────────

class _UserInputTaggingDelegator(Delegator):
    """Automatically tag user-typed characters with 'stdin'."""

    def insert(self, index, chars, tags=None):
        if tags is None:
            tags = "stdin"
        self.delegate.insert(index, chars, tags)


# ── ShellColorDelegator — colorizer that respects iomark ───────────────────

class _ShellColorDelegator(ColorDelegator):
    """ColorDelegator for shell: don't touch shell tags before iomark."""

    def recolorize_main(self):
        self.tag_remove("TODO", "1.0", "iomark")
        self.tag_add("SYNC", "1.0", "iomark")
        ColorDelegator.recolorize_main(self)

    def removecolors(self):
        # Don't remove shell color tags before iomark
        for tag in self.tagdefs if hasattr(self, 'tagdefs') else []:
            self.tag_remove(tag, "iomark", "end")


# ── _ShellConsole — execution engine (IDLE's ModifiedInterpreter subset) ───

class _ShellConsole(code.InteractiveConsole):
    """InteractiveConsole bound to a PythonShell.

    Ports the execution-related parts of idlelib's ModifiedInterpreter:
      - stuffsource(): linecache stuffing so tracebacks show source lines
      - runcode(): executing-state handling, debugger dispatch, and
        SystemExit / KeyboardInterrupt handling
      - showsyntaxerror(): highlight the offending position and print a
        compact message
    """

    gid = 0

    def __init__(self, shell):
        self.shell = shell
        super().__init__(locals=shell._locals)

    # ── linecache stuffing (IDLE's stuffsource / runsource) ─────────────

    def runsource(self, source, filename="<input>", symbol="single"):
        """Compile and run, returning True if more input is required.

        Reimplemented instead of using InteractiveInterpreter.runsource
        to avoid codeop's incomplete-input detection, which is broken on
        this Python build (suite inputs without a trailing newline are
        misreported as incomplete).  Plain compile() with implicit
        dedents handles the missing-trailing-newline case, so only
        genuinely incomplete input fails to compile.
        """
        filename = self.stuffsource(source)
        try:
            code = compile(source, filename, symbol, 0, 1)  # dont_inherit
        except (OverflowError, ValueError):
            self.showsyntaxerror(filename)
            return False
        except SyntaxError as e:
            if self._is_incomplete(e):
                return True
            self.showsyntaxerror(filename)
            return False
        self.runcode(code)
        return False

    @staticmethod
    def _is_incomplete(e):
        """True if the SyntaxError means 'more input required'."""
        msg = getattr(e, 'msg', '')
        return any(fragment in msg for fragment in (
            "expected an indented block",
            "unexpected EOF",
            "was never closed",
            "unterminated string",
            "unterminated triple-quoted string literal",
            "incomplete input",
        ))

    def stuffsource(self, source):
        """Stuff source in the filename cache (IDLE's stuffsource)."""
        filename = f"<pyshell#{self.gid}>"
        self.gid += 1
        lines = source.split("\n")
        linecache.cache[filename] = (len(source) + 1, 0, lines, filename)
        return filename

    # ── output ──────────────────────────────────────────────────────────

    def write(self, s):
        return self.shell.write(s, 'stderr')

    # ── execution (IDLE's ModifiedInterpreter.runcode) ──────────────────

    def runcode(self, code):
        """Execute code — under debugger if active, otherwise directly."""
        shell = self.shell
        shell.beginexecuting()
        try:
            debugger = shell.get_debugger()
            if debugger is not None:
                debugger.run(code, shell._locals)
            else:
                exec(code, self.locals)
        except SystemExit:
            shell.write("SystemExit: exit() ignored — the shell stays open\n",
                        'stderr')
        except KeyboardInterrupt:
            shell.write("KeyboardInterrupt\n", 'stderr')
        except bdb.BdbQuit:
            shell.write("\n[DEBUG QUIT]\n", 'stderr')
        except Exception:
            self.showtraceback()
        finally:
            shell.endexecuting()

    # ── syntax errors (IDLE's ModifiedInterpreter.showsyntaxerror) ──────

    def showsyntaxerror(self, filename=None):
        """Colorize the offending position instead of printing a caret."""
        shell = self.shell
        text = shell.text_widget
        text.tag_remove("ERROR", "1.0", "end")
        type, value, tb = sys.exc_info()
        msg = getattr(value, 'msg', '') or value or "<no detail available>"
        lineno = getattr(value, 'lineno', '') or 1
        offset = getattr(value, 'offset', '') or 0
        line = int(float(text.index("iomark linestart"))) + lineno - 1
        if offset > 0:
            pos = f"{line}.{offset - 1}"
        else:
            # This parser build reports offset 0 for some end-of-input
            # errors — highlight the end of the offending line instead.
            pos = f"{line}.end-1c"
        # Consume the input first, then colorize — the colorizer's
        # recolorize pass would otherwise wipe the ERROR tag.
        shell.resetoutput()
        shell.colorize_syntax_error(text, pos)
        shell.write("SyntaxError: %s\n" % msg, 'stderr')
        shell.showprompt()


# ── PythonShell ────────────────────────────────────────────────────────────

class PythonShell(ttk.Frame):
    """A single-Text-widget Python interactive shell.

    Uses idlelib's iomark pattern:
      - Text before iomark = read-only (output, prompts)
      - Text after iomark  = editable (user input)

    Integrates History, AutoIndent, and color tags (console/stdin/stdout/stderr).
    """

    # Font and colors
    SHELL_FONT = ('Monaco', 11)
    SHELL_BG = '#ffffff'
    SHELL_FG = '#1e1e1e'
    INSERT_BG = '#1e1e1e'

    # Tag color schemes
    TAG_STYLES = {
        'console': {'foreground': '#0451a5'},   # prompts: >>>, ...
        'stdin':   {'foreground': '#1e1e1e'},   # user input
        'stdout':  {'foreground': '#1e1e1e'},   # program output
        'stderr':  {'foreground': '#d32f2f'},   # error output
        # Syntax-error highlight.  Deliberately NOT in the colorizer's
        # tagdefs, whose removecolors() pass would wipe it.
        'ERROR':   {'foreground': '#000000', 'background': '#ff7777'},
    }

    # Interactive input prompt — gains a "[DEBUG ON]" banner while debugging.
    prompt = '>>> '

    # Trailing-whitespace stripper for submitted input (IDLE's
    # _last_newline_re — allows hitting return twice to end a statement).
    _last_newline_re = re.compile(r"[ \t]*(\n[ \t]*)?\Z")

    executing = False  # True while user code is running
    canceled = False   # True → next write() raises KeyboardInterrupt

    def __init__(self, parent, show_header=True, **kwargs):
        super().__init__(parent, **kwargs)
        self._show_header = show_header
        self._close_callback = None
        self._console = None
        self._locals = {}
        self._history = None
        self._auto_indent = None
        self._added_sys_paths: list[str] = []
        self._debugger = None      # Debugger instance (None = debug off)
        self._open_source_callback = None  # (filename, lineno) callback
        self._subproc = None       # running child process (None = none)
        self._poll_after = None    # after() id of the subprocess poller
        self._default_sigint = signal.getsignal(signal.SIGINT)
        self._saved_stdout = None
        self._saved_stderr = None
        self._build_ui()
        self._setup_percolator()
        self._setup_tags()
        self._setup_bindings()
        self.begin()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        if self._show_header:
            # Header bar
            header = ttk.Frame(self)
            header.grid(row=0, column=0, sticky='ew')

            ttk.Label(header, text='Python Shell', font=('Helvetica', 10, 'bold')).pack(
                side=tk.LEFT, padx=(4, 0), pady=(2, 0))

            # Separator
            ttk.Separator(self, orient=tk.HORIZONTAL).grid(
                row=1, column=0, sticky='ew')

        # Main shell Text widget
        text_frame = tk.Frame(self, bg=self.SHELL_BG)
        text_frame.grid(row=2, column=0, sticky='nsew')
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)

        self._text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=self.SHELL_FONT,
            bg=self.SHELL_BG,
            fg=self.SHELL_FG,
            insertbackground=self.INSERT_BG,
            highlightthickness=0,
            bd=0,
            padx=4,
            pady=4,
            tabstyle='wordprocessor',
            undo=False,
            autoseparators=False,
        )
        self._text.grid(row=0, column=0, sticky='nsew')

        self._scrollbar = tk.Scrollbar(
            text_frame, orient=tk.VERTICAL,
            command=self._on_scrollbar)
        self._scrollbar.grid(row=0, column=1, sticky='ns')

        self._text.configure(yscrollcommand=self._scrollbar.set)

    # ── Close button ──────────────────────────────────────────────────────

    def set_close_callback(self, callback):
        """Called when the close (×) button is clicked."""
        self._close_callback = callback

    def _on_close(self):
        if self._close_callback:
            self._close_callback()

    # ── Percolator chain ──────────────────────────────────────────────────

    def _setup_percolator(self):
        """Set up the filter chain: text → ModUndo → InputTagging → Color."""
        self._percolator = Percolator(self._text)

        self._undo = _ModifiedUndoDelegator()
        self._percolator.insertfilter(self._undo)

        self._input_tagger = _UserInputTaggingDelegator()
        self._percolator.insertfilterafter(self._input_tagger, after=self._undo)

        self._color = _ShellColorDelegator()
        self._percolator.insertfilter(self._color)

    # ── Tags ──────────────────────────────────────────────────────────────

    def _setup_tags(self):
        """Configure shell-specific color tags."""
        for tag_name, style in self.TAG_STYLES.items():
            self._text.tag_configure(tag_name, **style)

    # ── Key bindings ──────────────────────────────────────────────────────

    def _setup_bindings(self):
        text = self._text

        # Enter → submit or continue a block
        text.bind('<Return>', self._enter_callback)
        # Shift-Enter → always newline (autoindented)
        text.bind('<Shift-Return>', self._linefeed_callback)
        # Home → go to iomark if on the input line, else start of line
        text.bind('<Home>', self._home_callback)
        # Ctrl+C — interrupt (IDLE's <<interrupt-execution>>)
        text.bind('<Control-Key-c>', self._cancel_callback)
        text.bind('<Control-Key-C>', self._cancel_callback)

        # History keys: IDLE's mac keymap (Control-P/N) plus terminal-style
        # arrows (arrows fall back to cursor movement off the input line)
        text.event_add('<<history-previous>>', '<Control-Key-p>',
                       '<Control-Key-P>')
        text.event_add('<<history-next>>', '<Control-Key-n>',
                       '<Control-Key-N>')
        text.bind('<Up>', self._history_up_callback)
        text.bind('<Down>', self._history_down_callback)

        # Tab → auto-indent
        self._auto_indent = AutoIndent(text, undo=self._undo)
        text.bind('<Tab>', self._auto_indent.smart_indent_event)

    # ── Scroll sync ───────────────────────────────────────────────────────

    def _on_scrollbar(self, *args):
        self._text.yview(*args)

    # ── Shell lifecycle ───────────────────────────────────────────────────

    def begin(self):
        """Initialize iomark, print welcome message, show first prompt."""
        self._text.mark_set("iomark", "insert")
        self._text.mark_gravity("iomark", "left")

        self._console = _ShellConsole(self)
        self._history = History(self._text)

        version = sys.version.split()[0]
        self.write(f'Python {version} Interactive Shell\n')
        self.write('Type "help", "copyright", "credits" or "license" for more.\n')
        self.resetoutput()
        self.showprompt()

    def showprompt(self):
        """Write the prompt after iomark (IDLE's showprompt).

        Starts with resetoutput(), like IDLE — this closes out any
        unfinished input line so the prompt lands on a fresh line.
        """
        self.resetoutput()
        try:
            # Add console tag to the newline before prompt
            self._text.tag_add("console", "iomark-1c")

            # Write the prompt with console tag
            self._text.mark_gravity("iomark", "right")
            self._text.insert("iomark", self.prompt, "console")
            self._text.mark_gravity("iomark", "left")
            self._text.see("end-1c")

            self._text.mark_set("insert", "end-1c")
        except tk.TclError:
            pass

    def resetoutput(self):
        """Save current input to history, insert final newline, move iomark."""
        try:
            source = self._text.get("iomark", "end-1c")
            if self._history:
                self._history.store(source)
            if self._text.get("end-2c") != "\n":
                self._text.insert("end-1c", "\n")
            self._text.mark_set("iomark", "end-1c")
        except tk.TclError:
            pass

    # ── Write output ──────────────────────────────────────────────────────

    def write(self, s, tags=()):
        """Write text in the read-only output area (before iomark)."""
        if not tags:
            # An empty tags tuple is dropped to None inside the
            # percolator chain (idlelib's undo Command keeps tags only
            # when truthy), which would tag this text "stdin" and break
            # the recall logic.  Default to "stdout" instead.
            tags = 'stdout'
        try:
            self._text.mark_gravity("iomark", "right")
            self._text.insert("iomark", s, tags)
            self._text.mark_gravity("iomark", "left")
            self._text.see("iomark")
        except tk.TclError:
            pass
        if self.canceled:
            # IDLE's interrupt mechanism: raising from the write path
            # propagates through the user's own code (a raise from a Tk
            # callback would be swallowed by the event dispatcher).
            self.canceled = False
            raise KeyboardInterrupt
        return len(s)

    # ── Key handlers ──────────────────────────────────────────────────────

    def _enter_callback(self, event):
        """Handle Enter (IDLE's enter_callback port).

        Recalls commands from the read-only area, inserts autoindented
        newlines, and submits the input when on the last line.
        """
        text = self._text

        # If some text is selected, recall the selection
        # (but only if this before the I/O mark)
        try:
            sel = text.get("sel.first", "sel.last")
            if sel:
                if text.compare("sel.last", "<=", "iomark"):
                    self._recall(sel, event)
                    return "break"
        except tk.TclError:
            pass

        # If we're strictly before the line containing iomark, recall
        # the current line, less a leading prompt, less leading or
        # trailing whitespace
        if text.compare("insert", "<", "iomark linestart"):
            # Check if there's a relevant stdin range -- if so, use it.
            prev = text.tag_prevrange("stdin", "insert")
            if (prev and
                    text.compare("insert", "<", prev[1]) and
                    "console" not in text.tag_names("insert")):
                prev_cons = text.tag_prevrange("console", "insert")
                if prev_cons and text.compare(prev_cons[1], ">=", prev[0]):
                    prev = (prev_cons[1], prev[1])
                next_cons = text.tag_nextrange("console", "insert")
                if next_cons and text.compare(next_cons[0], "<", prev[1]):
                    prev = (prev[0], text.index(next_cons[0] + "+1c"))
                self._recall(text.get(prev[0], prev[1]), event)
                return "break"
            next = text.tag_nextrange("stdin", "insert")
            if next and text.compare("insert lineend", ">=", next[0]):
                next_cons = text.tag_nextrange("console", "insert lineend")
                if next_cons and text.compare(next_cons[0], "<", next[1]):
                    next = (next[0], text.index(next_cons[0] + "+1c"))
                self._recall(text.get(next[0], next[1]), event)
                return "break"
            # No stdin mark -- just get the current line, less any prompt
            indices = text.tag_nextrange("console", "insert linestart")
            if indices and text.compare(indices[0], "<=", "insert linestart"):
                self._recall(text.get(indices[1], "insert lineend"), event)
            else:
                self._recall(
                    text.get("insert linestart", "insert lineend"), event)
            return "break"

        # If we're between the beginning of the line and the iomark, i.e.
        # in the prompt area, move to the end of the prompt
        if text.compare("insert", "<", "iomark"):
            text.mark_set("insert", "iomark")

        # If we're in the current input and there's only whitespace
        # beyond the cursor, erase that whitespace first
        s = text.get("insert", "end-1c")
        if s and not s.strip():
            text.delete("insert", "end-1c")

        # If we're in the current input before its last line,
        # insert a newline right at the insert point
        if text.compare("insert", "<", "end-1c linestart"):
            self._auto_indent.newline_and_indent_event(event)
            return "break"

        # We're in the last line; append a newline and submit it
        text.mark_set("insert", "end-1c")
        self._auto_indent.newline_and_indent_event(event)
        text.update_idletasks()
        self._runit()
        return "break"

    def _recall(self, s, event):
        """Copy a previous command to the input area (IDLE's recall)."""
        # remove leading and trailing empty or whitespace lines
        s = re.sub(r'^\s*\n', '', s)
        s = re.sub(r'\n\s*$', '', s)
        lines = s.split('\n')
        text = self._text
        # undo_block_start isn't available in every tkinter build
        undo_start = getattr(text, 'undo_block_start', None)
        undo_stop = getattr(text, 'undo_block_stop', None)
        if undo_start:
            undo_start()
        try:
            text.tag_remove("sel", "1.0", "end")
            text.mark_set("insert", "end-1c")
            prefix = text.get("insert linestart", "insert")
            if prefix.rstrip().endswith(':'):
                self._auto_indent.newline_and_indent_event(event)
                prefix = text.get("insert linestart", "insert")
            text.insert("insert", lines[0].strip(), "stdin")
            if len(lines) > 1:
                orig_base_indent = re.search(r'^([ \t]*)', lines[0]).group(0)
                new_base_indent = re.search(r'^([ \t]*)', prefix).group(0)
                for line in lines[1:]:
                    if line.startswith(orig_base_indent):
                        # replace orig base indentation with new indentation
                        line = new_base_indent + line[len(orig_base_indent):]
                    text.insert('insert', '\n' + line.rstrip(), "stdin")
        finally:
            text.see("insert")
            if undo_stop:
                undo_stop()

    def _linefeed_callback(self, event):
        """Shift-Enter: insert an autoindented newline without submitting."""
        text = self._text
        if text.compare("insert", "<", "iomark"):
            text.mark_set("insert", "end-1c")
        self._auto_indent.newline_and_indent_event(event)
        text.see("insert")
        return "break"

    def _home_callback(self, event):
        """Home key (IDLE's home_callback port).

        On the input line: jump to iomark.  Elsewhere: start of line.
        Control-Home keeps the default (start of document) binding.
        """
        text = self._text
        if event.state & 4 != 0 and event.keysym == "Home":
            # state&4 == Control. If <Control-Home>, use the Tk binding.
            return None
        if text.index("iomark") and \
                text.compare("iomark", "<=", "insert lineend") and \
                text.compare("insert linestart", "<=", "iomark"):
            # insert is on the line with the input marker
            text.mark_set("insert", "iomark")
            text.tag_remove("sel", "1.0", "end")
            return "break"
        text.mark_set("insert", "insert linestart")
        text.tag_remove("sel", "1.0", "end")
        return "break"

    def _history_up_callback(self, event):
        """Up: recall history on the input line, cursor movement elsewhere."""
        if (self._text.compare("insert", "<", "end-1c linestart") or
                self._text.compare("insert", "<", "iomark")):
            return None  # not in the input area — default cursor movement
        return self._history.history_prev(event)

    def _history_down_callback(self, event):
        """Down: recall history on the input line, cursor movement elsewhere."""
        if (self._text.compare("insert", "<", "end-1c linestart") or
                self._text.compare("insert", "<", "iomark")):
            return None
        return self._history.history_next(event)

    def _cancel_callback(self, event=None):
        """Handle Ctrl+C (IDLE's cancel_callback, adapted for in-process).

        Idle:          reset input, print KeyboardInterrupt, new prompt.
        Subprocess:    forward SIGINT to the child.
        Debugger stop: quit the debugged run.
        In-process:    set the canceled flag — the next write() from the
                       running user code raises KeyboardInterrupt there
                       (IDLE's -n-mode mechanism; raising from a Tk
                       callback directly would be swallowed).
        """
        text = self._text
        try:
            if text.compare("sel.first", "!=", "sel.last"):
                return  # Active selection -- always use default binding
        except tk.TclError:
            pass
        if not self.executing:
            self.resetoutput()
            self.write("KeyboardInterrupt\n", 'stderr')
            self.showprompt()
            return "break"
        if self._subproc is not None and self._subproc.poll() is None:
            self._subproc.send_signal(signal.SIGINT)
            return "break"
        dbg = self._debugger
        if dbg is not None and dbg.interacting:
            dbg.quit()
            return "break"
        if self.executing:
            self.canceled = True
        return "break"

    # ── Code execution ────────────────────────────────────────────────────

    def _runit(self):
        """Submit the input between iomark and end-1c (IDLE's runit)."""
        index_before = self._text.index("end-2c")
        line = self._text.get("iomark", "end-1c")
        # Strip off last newline and surrounding whitespace.
        # (To allow you to hit return twice to end a statement.)
        line = self._last_newline_re.sub("", line)
        if not line.strip():
            # Deviation from IDLE (which accumulates blank lines):
            # empty input gets a fresh prompt, like a real REPL.
            self.resetoutput()
            self.showprompt()
            return
        input_is_complete = self._console.runsource(line)
        if not input_is_complete:
            if self._text.get(index_before) == '\n':
                self._text.tag_remove("stdin", index_before)

    # ── Executing state (IDLE's beginexecuting / endexecuting) ────────────

    def beginexecuting(self):
        """Mark the shell as executing (IDLE port).

        Consumes the current input, redirects stdout/stderr into the
        shell, and arms a SIGINT handler so terminal Ctrl+C interrupts
        in-process user code.
        """
        self.resetoutput()
        self.executing = True
        self._saved_stdout = sys.stdout
        self._saved_stderr = sys.stderr
        sys.stdout = _ShellWriter(self, 'stdout')
        sys.stderr = _ShellWriter(self, 'stderr')
        signal.signal(signal.SIGINT, self._sigint_handler)

    def endexecuting(self):
        """End an execution: restore streams and SIGINT, show a prompt."""
        self.executing = False
        self.canceled = False
        if self._saved_stdout is not None:
            sys.stdout = self._saved_stdout
            sys.stderr = self._saved_stderr
            self._saved_stdout = None
            self._saved_stderr = None
        signal.signal(signal.SIGINT, self._default_sigint)
        self.showprompt()

    def is_executing(self):
        """Return True while user code is executing."""
        return self.executing

    def _sigint_handler(self, signum, frame):
        """SIGINT while executing: forward to the child or interrupt.

        In-process equivalent of IDLE's interrupt_subprocess: raising
        KeyboardInterrupt at the next bytecode boundary aborts the
        running command (bdb catches it and stops the debugger).
        """
        proc = self._subproc
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGINT)
        elif self.executing:
            raise KeyboardInterrupt

    # ── Debugger management ────────────────────────────────────────────────

    def open_debugger(self, debugger_panel=None):
        """Start the debugger and enable debug mode (IDLE port).

        :param debugger_panel: Optional pre-created Debugger panel.
            If not provided, one will be created (legacy mode).
        """
        if self._debugger is not None:
            return
        if self.executing:
            # IDLE refuses to toggle the debugger while executing.
            self._text.bell()
            return
        if debugger_panel is not None:
            self._debugger = debugger_panel
        else:
            # Legacy: create standalone debugger (for testing)
            self._debugger = Debugger(self.master, self)
        self.resetoutput()
        self.prompt = '[DEBUG ON]\n>>> '
        self.showprompt()

    def close_debugger(self):
        """Stop the debugger and disable debug mode (IDLE port)."""
        db = self._debugger
        if db is None:
            return
        self._debugger = None
        try:
            db.quit()
        except Exception:
            pass
        self.resetoutput()
        self.write('[DEBUG OFF]\n', 'console')
        self.prompt = '>>> '
        self.showprompt()

    def toggle_debugger(self):
        """Toggle debug mode on/off."""
        if self._debugger is not None:
            self.close_debugger()
            return False
        else:
            self.open_debugger()
            return True

    def is_debugging(self):
        """Return True if the debugger is active."""
        return self._debugger is not None

    def get_debugger(self):
        """Return the Debugger instance, or None."""
        return self._debugger

    def colorize_syntax_error(self, text, pos):
        """Highlight the offending position (IDLE's colorize_syntax_error)."""
        # Tag the offending character (non-zero range so the highlight
        # survives the colorizer and is actually visible).
        text.tag_add("ERROR", pos, pos + "+1c")
        char = text.get(pos)
        if char and char in string.ascii_letters + string.digits + "_":
            text.tag_add("ERROR", pos + " wordstart", pos)
        if '\n' == text.get(pos):   # error at line end
            text.mark_set("insert", pos)
        else:
            text.mark_set("insert", pos + "+1c")
        text.see(pos)

    # ── Public API ────────────────────────────────────────────────────────

    def focus_input(self):
        """Focus the shell and move cursor to the input area."""
        self._text.focus_set()
        self._text.mark_set("insert", "end-1c")
        self._text.see("insert")

    def set_venv(self, site_packages_path: str | None):
        """Activate or deactivate a venv in the Python shell.

        On activation: calls site.addsitedir() to process .pth files and
        make the venv's packages importable.
        On deactivation (None): removes the paths that were added.

        Imported modules from the venv are NOT unloaded — matching the
        behaviour of 'deactivate' in a real terminal.
        """
        if site_packages_path and os.path.isdir(site_packages_path):
            import site
            before = set(sys.path)
            site.addsitedir(site_packages_path)
            after = set(sys.path)
            self._added_sys_paths = list(after - before)
            name = os.path.basename(os.path.dirname(site_packages_path))
            self.write(f'[Venv: {name}]\n', 'stdout')
        else:
            if self._added_sys_paths:
                for p in self._added_sys_paths:
                    try:
                        sys.path.remove(p)
                    except ValueError:
                        pass
                self._added_sys_paths = []
            self.write('[Venv: system Python]\n', 'stdout')

    def cleanup(self):
        """Clean up: kill subprocess, restore SIGINT, close colorizer."""
        self._kill_subprocess()
        signal.signal(signal.SIGINT, self._default_sigint)
        try:
            self._color.close()
            self._percolator.close()
        except Exception:
            pass

    def run_code(self, source, file_path=None, python_exe=None):
        """Execute Python source — IDLE Run Module style.

        When *file_path* is provided and the file exists on disk, the
        script is launched as a subprocess (so GUI apps have their own
        event loop and closing the window triggers completion naturally).
        Output is streamed into the shell without blocking the UI.

        Otherwise falls back to in-process ``exec()`` (e.g. unsaved buffers).

        Parameters
        ----------
        source : str
            The Python source code (used for in-process fallback only).
        file_path : str, optional
            If provided and exists on disk, launched via subprocess.
        python_exe : str, optional
            Python interpreter to use for subprocess execution.
            Defaults to ``sys.executable``.
        """
        if file_path and os.path.isfile(file_path):
            self._run_subprocess(file_path, python_exe=python_exe)
        else:
            self._run_inprocess(source, file_path)

    def run_code_debug(self, source, file_path=None):
        """Execute Python source under debugger control — always in-process.

        Unlike ``run_code`` which may fork a subprocess for files on disk,
        this always runs in-process so the debugger can trace execution.

        Parameters
        ----------
        source : str
            The Python source code to execute.
        file_path : str, optional
            If provided, the file path is used for sys.path setup and
            restart banner labelling.
        """
        self.restart(file_path)

        label = os.path.basename(file_path) if file_path else '<script>'
        self.write(f'── Debugging {label} ──\n', 'console')

        # Ensure debugger is active
        if self._debugger is None:
            self.open_debugger()

        # Set up the module environment (__file__, sys.argv[0])
        old_argv = sys.argv
        if file_path:
            self._locals['__file__'] = os.path.abspath(file_path)
            sys.argv = [file_path] + old_argv[1:]

        self.beginexecuting()
        try:
            filename = file_path or '<script>'
            compiled = compile(source, filename, 'exec')
            self._debugger.run(compiled)
        except bdb.BdbQuit:
            self.write("\n[DEBUG QUIT]\n", 'stderr')
        except KeyboardInterrupt:
            self.write("KeyboardInterrupt\n", 'stderr')
        except Exception:
            # Runtime errors (ZeroDivisionError, etc.) — print traceback
            # to the shell's stderr so the user can see what happened.
            import traceback
            traceback.print_exc()
        finally:
            sys.argv = old_argv
            self._emit_completion()

    def _run_subprocess(self, file_path, python_exe=None):
        """Launch *file_path* in a subprocess, streaming output asynchronously.

        Ported from IDLE's poll_subprocess pattern — the Tk event loop
        stays responsive while the child runs.
        """
        self.restart(file_path)

        python = python_exe or sys.executable
        label = os.path.basename(file_path)
        self.write(f'── Running {label} ──\n', 'console')

        self.beginexecuting()
        try:
            self._subproc = subprocess.Popen(
                [python, '-u', file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                cwd=os.path.dirname(file_path),
            )
        except FileNotFoundError:
            self._subproc = None
            self.write('[Error: Python interpreter not found]\n', 'stderr')
            self._emit_completion()
            return
        self._poll_subprocess()

    def _poll_subprocess(self):
        """Drain subprocess output and reschedule until it exits."""
        proc = self._subproc
        if proc is None:
            return
        self._drain_pipes(proc)
        if proc.poll() is None:
            self._poll_after = self._text.after(50, self._poll_subprocess)
        else:
            self._drain_pipes(proc)  # catch anything remaining
            self._subproc = None
            self._emit_completion()

    def _drain_pipes(self, proc):
        """Read available bytes from the child's pipes and write them out."""
        for stream, tag in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
            try:
                while True:
                    ready, _, _ = select.select([stream], [], [], 0)
                    if not ready:
                        break
                    chunk = os.read(stream.fileno(), 65536)
                    if not chunk:
                        break
                    self.write(chunk.decode('utf-8', 'replace'), tag)
            except (OSError, ValueError):
                break

    def _kill_subprocess(self):
        """Terminate a running subprocess (restart/close)."""
        proc, self._subproc = self._subproc, None
        if self._poll_after is not None:
            try:
                self._text.after_cancel(self._poll_after)
            except tk.TclError:
                pass
            self._poll_after = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def _run_inprocess(self, source, file_path):
        """Fallback: run source in-process via exec()."""
        self.restart(file_path)

        label = os.path.basename(file_path) if file_path else '<script>'
        self.write(f'── Running {label} ──\n', 'console')

        filename = file_path or '<script>'

        # Set up the module environment (__file__, sys.argv[0])
        old_argv = sys.argv
        if file_path:
            sys.argv = [file_path] + old_argv[1:]

        self.beginexecuting()
        try:
            compiled = compile(source, filename, 'exec')
            globals_dict = {'__name__': '__main__', '__file__': filename}
            exec(compiled, globals_dict)
        except SystemExit:
            pass
        except KeyboardInterrupt:
            self.write("KeyboardInterrupt\n", 'stderr')
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            sys.argv = old_argv
            self._emit_completion()

    # ── Shell restart (IDLE-style) ─────────────────────────────────────────

    def restart(self, file_path=None):
        """Restart the Python interpreter — clear all state.

        This mimics IDLE's Shell restart: saves history, resets locals,
        clears the console, and prints a restart banner.

        Parameters
        ----------
        file_path : str, optional
            If provided, shown in the restart banner and its parent
            directory is added to sys.path.
        """
        # Stop any running child process first.
        self._kill_subprocess()

        # Reset output area
        self.resetoutput()

        # Save history before clearing state
        if self._history:
            try:
                self._history.store(self._text.get("iomark", "end-1c"))
            except tk.TclError:
                pass

        # Clear locals and recreate console
        self._locals.clear()
        self._console = _ShellConsole(self)

        # Add file directory to sys.path (for imports)
        if file_path:
            dir_name = os.path.dirname(os.path.abspath(file_path))
            if dir_name not in sys.path:
                sys.path.insert(0, dir_name)

        # Print IDLE-style restart banner
        banner = '=' * 20 + ' RESTART: '
        if file_path:
            banner += file_path
        else:
            banner += 'Shell'
        banner += ' ' + '=' * 20
        self.write(f'\n{banner}\n', 'console')

    def _emit_completion(self):
        """Signal that script execution has finished.

        Writes a completion marker and shows a new prompt.  Silently
        returns if the underlying Text widget has been destroyed.
        """
        try:
            if self._text.winfo_exists():
                self.write('── Finished ──\n', 'console')
        except (tk.TclError, KeyboardInterrupt):
            pass
        self.endexecuting()

    @property
    def text_widget(self):
        return self._text


# ── _ShellWriter — stdout/stderr redirector ────────────────────────────────

class _ShellWriter:
    """Redirect writes to a PythonShell with a specific tag."""

    def __init__(self, shell, tag):
        self.shell = shell
        self.tag = tag

    def write(self, s):
        if s:
            self.shell.write(s, self.tag)

    def flush(self):
        pass
