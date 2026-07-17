"""PythonShell — idlelib-style single-Text-widget Python shell with iomark."""

import bdb
import code
import os
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
    }

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

        # Enter → submit or newline
        text.bind('<Return>', self._enter_callback)
        # Shift-Enter → always newline
        text.bind('<Shift-Return>', self._linefeed_callback)
        # Home → go to iomark if before it, otherwise beginning of line
        text.bind('<Home>', self._home_callback)
        # Block typing in read-only area (handled by ModifiedUndoDelegator)

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

        self._console = code.InteractiveConsole(locals=self._locals)
        self._history = History(self._text)

        version = sys.version.split()[0]
        self.write(f'Python {version} Interactive Shell\n')
        self.write('Type "help", "copyright", "credits" or "license" for more.\n')
        self.resetoutput()
        self.showprompt()

    def showprompt(self):
        """Write the '>>> ' prompt after iomark (caller must reset first)."""
        try:
            # Add console tag to the newline before prompt
            self._text.tag_add("console", "iomark-1c")

            # Write the prompt with console tag
            self._text.mark_gravity("iomark", "right")
            self._text.insert("iomark", ">>> ", "console")
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
        try:
            self._text.mark_gravity("iomark", "right")
            self._text.insert("iomark", s, tags)
            self._text.mark_gravity("iomark", "left")
            self._text.see("iomark")
        except tk.TclError:
            pass
        return len(s)

    # ── Key handlers ──────────────────────────────────────────────────────

    def _enter_callback(self, event):
        """Handle Enter key: submit code if at end of input, else newline."""
        text = self._text

        # If cursor is before iomark, jump to end
        if text.compare("insert", "<", "iomark"):
            text.mark_set("insert", "end-1c")
            return "break"

        # Strip trailing whitespace from cursor to end
        s = text.get("insert", "end-1c")
        if s and not s.strip():
            text.delete("insert", "end-1c")

        # If not on last line, insert a newline
        if text.compare("insert", "<", "end-1c linestart"):
            self._auto_indent.newline_and_indent_event(event)
            return "break"

        # On last line — submit the code directly
        self._runit()
        return "break"

    def _linefeed_callback(self, event):
        """Shift-Enter: insert newline without submitting."""
        text = self._text
        if text.compare("insert", "<", "iomark"):
            text.mark_set("insert", "end-1c")
        text.insert("insert", "\n", "stdin")
        text.see("insert")
        return "break"

    def _home_callback(self, event):
        """Home key: go to iomark if before it, else go to start of input."""
        text = self._text
        if text.compare("insert", "<", "iomark"):
            text.mark_set("insert", "iomark")
            return "break"
        # If after iomark, go to beginning of current input
        text.mark_set("insert", "iomark")
        return "break"

    # ── Code execution ────────────────────────────────────────────────────

    def _runit(self):
        """Execute the code between iomark and end-1c."""
        source = self._text.get("iomark", "end-1c")
        # Strip trailing whitespace/newlines
        source = source.rstrip()

        if not source:
            self.showprompt()
            return

        # Check if input is complete
        try:
            compiled = code.compile_command(source, '<stdin>', 'single')
        except (SyntaxError, OverflowError, ValueError) as e:
            self._show_syntax_error(source)
            self.resetoutput()
            self.showprompt()
            return

        if compiled is None:
            # Incomplete — wait for more input
            return

        # Save input to history and move iomark BEFORE executing,
        # so that stdout/stderr output appears after the saved input.
        self.resetoutput()

        # Execute (output goes after the saved input line)
        self._execute(source)

        # Show new prompt
        self.showprompt()

    def _execute(self, source):
        """Execute Python code — under debugger if active, otherwise normally."""
        if self._debugger is not None:
            self._execute_under_debugger(source)
        else:
            self._execute_normal(source)

    def _execute_normal(self, source):
        """Execute Python code and capture stdout/stderr."""
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = _ShellWriter(self, 'stdout')
        sys.stderr = _ShellWriter(self, 'stderr')
        try:
            more = self._console.push(source)
            # If there's more to run, don't show prompt yet
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def _execute_under_debugger(self, source):
        """Execute Python code under the debugger's control."""
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = _ShellWriter(self, 'stdout')
        sys.stderr = _ShellWriter(self, 'stderr')
        try:
            self._debugger.run(source)
        except bdb.BdbQuit:
            self.write("\n[DEBUG QUIT]\n", 'stderr')
        except Exception:
            # Runtime errors (ZeroDivisionError, etc.) — print traceback
            # to the shell's stderr so the user can see what happened.
            import traceback
            traceback.print_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    # ── Debugger management ────────────────────────────────────────────────

    def open_debugger(self, debugger_panel=None):
        """Start the debugger and enable debug mode.

        :param debugger_panel: Optional pre-created Debugger panel.
            If not provided, one will be created (legacy mode).
        """
        if self._debugger is not None:
            return
        if debugger_panel is not None:
            self._debugger = debugger_panel
        else:
            # Legacy: create standalone debugger (for testing)
            self._debugger = Debugger(self.master, self)

    def close_debugger(self):
        """Stop the debugger and disable debug mode."""
        if self._debugger is None:
            return
        try:
            self._debugger.quit()
        except Exception:
            pass
        self._debugger = None

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

    def _show_syntax_error(self, source):
        """Display a syntax error in the shell."""
        import traceback
        try:
            code.compile_command(source, '<stdin>', 'exec')
        except (SyntaxError, OverflowError, ValueError) as e:
            lines = traceback.format_exception_only(type(e), e)
            for line in lines:
                self.write(line, 'stderr')

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
        """Clean up colorizer and percolator."""
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

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = _ShellWriter(self, 'stdout')
        sys.stderr = _ShellWriter(self, 'stderr')
        try:
            self._debugger.run(source, filename=file_path)
        except bdb.BdbQuit:
            self.write("\n[DEBUG QUIT]\n", 'stderr')
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        self._emit_completion()

    def _run_subprocess(self, file_path, python_exe=None):
        """Launch *file_path* in a subprocess and capture output."""
        self.restart(file_path)

        python = python_exe or sys.executable
        label = os.path.basename(file_path)
        self.write(f'── Running {label} ──\n', 'console')

        try:
            result = subprocess.run(
                [python, '-u', file_path],
                capture_output=True,
                text=True,
                cwd=os.path.dirname(file_path),
            )
        except FileNotFoundError:
            self.write('[Error: Python interpreter not found]\n', 'stderr')
            self._emit_completion()
            return

        if result.stdout:
            self.write(result.stdout, 'stdout')
        if result.stderr:
            self.write(result.stderr, 'stderr')

        self._emit_completion()

    def _run_inprocess(self, source, file_path):
        """Fallback: run source in-process via exec()."""
        self.restart(file_path)

        label = os.path.basename(file_path) if file_path else '<script>'
        self.write(f'── Running {label} ──\n', 'console')

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = _ShellWriter(self, 'stdout')
        sys.stderr = _ShellWriter(self, 'stderr')
        try:
            filename = file_path or '<script>'
            compiled = compile(source, filename, 'exec')
            exec(compiled, {'__name__': '__main__'})
        except SystemExit:
            pass
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

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
        self._console = code.InteractiveConsole(locals=self._locals)

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
                self.showprompt()
        except tk.TclError:
            pass

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
