"""Debugger — idlelib-style debugger for tknote.

Provides a GUI debug control panel (ttk.Frame) with:
  - Go / Step / Over / Out / Quit buttons
  - Stack viewer, Locals/Globals viewers
  - Source line tracking in the editor
  - Breakpoint management

Based on idlelib's debugger.Debugger, simplified for in-process use.
"""

import bdb
import linecache
import os
import tkinter as tk
from tkinter import ttk


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers (from idlelib/debugger.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _frame2message(frame):
    """Return a message string describing a frame: 'file:line: func()'."""
    code = frame.f_code
    filename = code.co_filename
    lineno = frame.f_lineno
    basename = os.path.basename(filename)
    message = f"{basename}:{lineno}"
    if code.co_name != "?":
        message = f"{message}: {code.co_name}()"
    return message


# ═══════════════════════════════════════════════════════════════════════════════
# StackViewer — simple Listbox-based stack display
# ═══════════════════════════════════════════════════════════════════════════════

class StackViewer(ttk.Frame):
    """Display the current call stack in a scrollable listbox."""

    def __init__(self, parent, debugger_gui):
        super().__init__(parent)
        self.gui = debugger_gui
        self._stack = []
        self._build_ui()

    def _build_ui(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(self)
        scrollbar.grid(row=0, column=1, sticky='ns')

        self.listbox = tk.Listbox(
            self, yscrollcommand=scrollbar.set,
            font=('Monaco', 10), exportselection=False)
        self.listbox.grid(row=0, column=0, sticky='nsew')
        scrollbar.config(command=self.listbox.yview)

        self.listbox.bind('<Double-Button-1>', self._on_double)
        self.listbox.bind('<ButtonRelease-1>', self._on_select)
        self.listbox.bind('<Button-2>', self._on_right_click)
        self.listbox.bind('<Button-3>', self._on_right_click)

        # Context menu
        self._menu = tk.Menu(self, tearoff=0)
        self._menu.add_command(label="Go to source line",
                               command=self._goto_source)
        self._menu.add_command(label="Show stack frame",
                               command=self._show_frame)

    def load_stack(self, stack, index=None):
        """Load a new stack trace into the listbox.

        stack: list of (frame, lineno) tuples
        index: index of the current frame
        """
        self._stack = stack
        self.listbox.delete(0, tk.END)

        for i, (frame, lineno) in enumerate(stack):
            try:
                modname = frame.f_globals.get("__name__", "?")
            except Exception:
                modname = "?"
            code = frame.f_code
            filename = code.co_filename
            funcname = code.co_name
            sourceline = linecache.getline(filename, lineno).strip()
            if funcname in ("?", "", None):
                item = f"{modname}, line {lineno}: {sourceline}"
            else:
                item = f"{modname}.{funcname}(), line {lineno}: {sourceline}"
            if i == index:
                item = "> " + item
            self.listbox.insert(tk.END, item)

        if index is not None:
            self.listbox.selection_set(index)
            self.listbox.see(index)

    def _on_select(self, event):
        idx = self.listbox.curselection()
        if idx:
            self.gui.show_frame(self._stack[idx[0]])

    def _on_double(self, event):
        idx = self.listbox.curselection()
        if idx:
            self._show_source(idx[0])

    def _on_right_click(self, event):
        idx = self.listbox.nearest(event.y)
        if 0 <= idx < len(self._stack):
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(idx)
            try:
                self._menu.tk_popup(event.x_root, event.y_root)
            finally:
                self._menu.grab_release()

    def _goto_source(self):
        idx = self.listbox.curselection()
        if idx:
            self._show_source(idx[0])

    def _show_frame(self):
        idx = self.listbox.curselection()
        if idx and 0 <= idx[0] < len(self._stack):
            self.gui.show_frame(self._stack[idx[0]])

    def _show_source(self, index):
        if 0 <= index < len(self._stack):
            frame, lineno = self._stack[index]
            filename = frame.f_code.co_filename
            self.gui._open_source(filename, lineno)

    def close(self):
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════════
# NamespaceViewer — simple Treeview-based variable display
# ═══════════════════════════════════════════════════════════════════════════════

class NamespaceViewer(ttk.Frame):
    """Display a namespace (locals or globals) in a treeview."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.title = title
        self._build_ui()

    def _build_ui(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(self)
        scrollbar.grid(row=0, column=1, sticky='ns')

        self.tree = ttk.Treeview(
            self, columns=('value',), show='headings',
            yscrollcommand=scrollbar.set)
        self.tree.heading('#0', text='Name')
        self.tree.heading('value', text='Value')
        self.tree.column('#0', width=120, stretch=False)
        self.tree.column('value', width=200)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar.config(command=self.tree.yview)

    def load_dict(self, d, force=False):
        """Load a dictionary into the treeview."""
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)

        if d is None:
            return

        items = sorted(d.items(), key=lambda x: x[0])
        for name, value in items:
            if name.startswith('__') and name.endswith('__'):
                continue  # skip dunder names
            try:
                val_repr = repr(value)
                if len(val_repr) > 80:
                    val_repr = val_repr[:77] + '...'
            except Exception:
                val_repr = '<unprintable>'

            # Determine type for display
            typ = type(value).__name__
            self.tree.insert('', tk.END, text=name, values=(f"{val_repr}",))

    def close(self):
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════════
# Debugger — GUI debug control panel (ttk.Frame)
# ═══════════════════════════════════════════════════════════════════════════════

class Debugger(ttk.Frame):
    """GUI debugger panel based on idlelib's debugger.Debugger.

    Renders as an inline panel (like the Terminal / Shell panels) instead
    of a separate Toplevel window.  Uses bdb.Bdb directly for in-process
    debugging.
    """

    vstack = None    # class-level BooleanVar for "Show Stack"
    vsource = None   # class-level BooleanVar for "Show Source"
    vlocals = None   # class-level BooleanVar for "Show Locals"
    vglobals = None  # class-level BooleanVar for "Show Globals"

    def __init__(self, parent, pyshell, **kwargs):
        """Create the debugger panel.

        :param parent: parent widget (usually the editor PanedWindow).
        :param pyshell: The PythonShell instance that owns this debugger.
        """
        super().__init__(parent, **kwargs)
        self.pyshell = pyshell
        self._close_callback = None
        self.frame = None          # current frame being inspected
        self.interacting = False   # True while waiting for user input
        self.nesting_level = 0     # for re-entrant run detection

        # Viewers
        self.stackviewer = None
        self.localsviewer = None
        self.globalsviewer = None

        # Breakpoints: {filename: set(lineno), ...}
        self.breakpoints = {}

        # Create the bdb.Bdb instance
        self.idb = bdb.Bdb()
        self.idb.user_line = self._user_line
        self.idb.user_exception = self._user_exception
        self.idb.user_return = self._user_return
        self.idb.user_call = None

        self.make_gui()

    # ── bdb.Bdb callbacks ──────────────────────────────────────────────────

    def _user_line(self, frame):
        """Called by bdb.Bdb when execution reaches a new line."""
        message = _frame2message(frame)
        try:
            self.interaction(message, frame)
        except tk.TclError:
            pass

    def _user_exception(self, frame, exc_info):
        """Called by bdb.Bdb when an exception occurs."""
        message = _frame2message(frame)
        try:
            self.interaction(message, frame, exc_info)
        except tk.TclError:
            pass

    def _user_return(self, frame, retval):
        """Called by bdb.Bdb when a function returns — silently pass.

        In step mode, bdb stops at every line anyway.  Returns are noise.
        """
        pass

    # ── Close button ──────────────────────────────────────────────────────

    def set_close_callback(self, callback):
        """Called when the close (×) button is clicked."""
        self._close_callback = callback

    def _on_close(self):
        """Handle close button click — close the debugger."""
        self.pyshell.close_debugger()

    # ── Focus ─────────────────────────────────────────────────────────────

    def focus_input(self):
        """No text input in debug panel; focus the first button."""
        if self.buttons:
            self.buttons[0].focus_set()

    # ── GUI construction ───────────────────────────────────────────────────

    def make_gui(self):
        """Build the debugger panel UI."""
        root = self.pyshell.winfo_toplevel()
        self.root = root

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Header bar ──
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky='ew')

        ttk.Label(header, text='Debug Control',
                  font=('Helvetica', 10, 'bold')).pack(
            side=tk.LEFT, padx=(4, 0), pady=(2, 0))

        # ── Separator ──
        ttk.Separator(self, orient=tk.HORIZONTAL).grid(
            row=1, column=0, sticky='ew')

        # ── Content area ──
        content = ttk.Frame(self)
        content.grid(row=2, column=0, sticky='nsew')
        content.grid_rowconfigure(0, weight=0)   # button bar
        content.grid_rowconfigure(1, weight=0)   # status label
        content.grid_rowconfigure(2, weight=0)   # error label
        content.grid_rowconfigure(3, weight=1)   # stack viewer
        content.grid_rowconfigure(4, weight=1)   # locals viewer
        content.grid_rowconfigure(5, weight=1)   # globals viewer
        content.grid_columnconfigure(0, weight=1)

        # ── Button frame ──
        bframe = ttk.Frame(content)
        bframe.grid(row=0, column=0, sticky='ew', padx=4, pady=4)

        self.buttons = []

        btn_go = ttk.Button(bframe, text="Go", command=self.cont)
        btn_go.pack(side=tk.LEFT, padx=1)
        self.buttons.append(btn_go)

        btn_step = ttk.Button(bframe, text="Step", command=self.step)
        btn_step.pack(side=tk.LEFT, padx=1)
        self.buttons.append(btn_step)

        btn_over = ttk.Button(bframe, text="Over", command=self.next)
        btn_over.pack(side=tk.LEFT, padx=1)
        self.buttons.append(btn_over)

        btn_out = ttk.Button(bframe, text="Out", command=self.ret)
        btn_out.pack(side=tk.LEFT, padx=1)
        self.buttons.append(btn_out)

        btn_quit = ttk.Button(bframe, text="Quit", command=self.quit)
        btn_quit.pack(side=tk.LEFT, padx=1)
        self.buttons.append(btn_quit)

        # Disable all buttons initially
        for b in self.buttons:
            b.configure(state=tk.DISABLED)

        # ── Checkboxes frame ──
        cframe = ttk.Frame(bframe)
        cframe.pack(side=tk.LEFT, padx=(12, 0))

        if Debugger.vstack is None:
            Debugger.vstack = tk.BooleanVar(root)
            Debugger.vstack.set(True)
        self.bstack = ttk.Checkbutton(
            cframe, text="Stack", command=self.show_stack,
            variable=Debugger.vstack)
        self.bstack.grid(row=0, column=0, padx=2)

        if Debugger.vsource is None:
            Debugger.vsource = tk.BooleanVar(root)
            Debugger.vsource.set(True)
        self.bsource = ttk.Checkbutton(
            cframe, text="Source", command=self.show_source,
            variable=Debugger.vsource)
        self.bsource.grid(row=0, column=1, padx=2)

        if Debugger.vlocals is None:
            Debugger.vlocals = tk.BooleanVar(root)
            Debugger.vlocals.set(True)
        self.blocals = ttk.Checkbutton(
            cframe, text="Locals", command=self.show_locals,
            variable=Debugger.vlocals)
        self.blocals.grid(row=1, column=0, padx=2)

        if Debugger.vglobals is None:
            Debugger.vglobals = tk.BooleanVar(root)
            Debugger.vglobals.set(False)
        self.bglobals = ttk.Checkbutton(
            cframe, text="Globals", command=self.show_globals,
            variable=Debugger.vglobals)
        self.bglobals.grid(row=1, column=1, padx=2)

        # ── Status & Error labels ──
        self.status = ttk.Label(content, text="", anchor=tk.W)
        self.status.grid(row=1, column=0, sticky='ew', padx=4)

        self.error = tk.Label(content, text="", anchor=tk.W)
        self.error.grid(row=2, column=0, sticky='ew', padx=4)
        self.errorbg = self.error.cget("background")

        # ── Stack frame ──
        self.fstack = ttk.Frame(content, height=1)
        self.fstack.grid(row=3, column=0, sticky='nsew', padx=4, pady=2)

        # ── Locals frame ──
        self.flocals = ttk.Frame(content, height=1)
        self.flocals.grid(row=4, column=0, sticky='nsew', padx=4, pady=2)

        # ── Globals frame ──
        self.fglobals = ttk.Frame(content, height=1)
        self.fglobals.grid(row=5, column=0, sticky='nsew', padx=4, pady=2)

        # Show initial views
        if Debugger.vstack.get():
            self.show_stack()
        if Debugger.vlocals.get():
            self.show_locals()
        if Debugger.vglobals.get():
            self.show_globals()

        # Initialize the vwait sentinel variable
        self.root.tk.call('set', '::tknote_debug_wait', '0')

    # ── Interaction ────────────────────────────────────────────────────────

    def interaction(self, message, frame, info=None):
        """Handle a debugger stop: update UI and wait for user command."""
        self.frame = frame
        self.status.configure(text=message)

        if info:
            exc_type, exc_value, exc_tb = info
            try:
                m1 = exc_type.__name__
            except AttributeError:
                m1 = str(exc_type)
            if exc_value is not None:
                try:
                    m1 = f"{m1}: {exc_value}"
                except Exception:
                    pass
            bg = "yellow"
        else:
            m1 = ""
            bg = self.errorbg
        self.error.configure(text=m1, background=bg)

        # Update stack viewer
        if self.stackviewer:
            stack, i = self.idb.get_stack(frame, info[2] if info else None)
            self.stackviewer.load_stack(stack, i)

        # Update variable viewers
        self.show_variables(force=True)

        # Sync source line in editor
        if Debugger.vsource.get():
            self.sync_source_line()

        # Enable control buttons
        for b in self.buttons:
            b.configure(state=tk.NORMAL)

        # Nested event loop — wait for user to click Go/Step/Over/Out/Quit
        self.nesting_level += 1
        self.root.tk.call('vwait', '::tknote_debug_wait')
        self.nesting_level -= 1

        # Disable buttons after user action
        for b in self.buttons:
            b.configure(state=tk.DISABLED)

        self.status.configure(text="")
        self.error.configure(text="", background=self.errorbg)
        self.frame = None

    def sync_source_line(self):
        """Highlight the current source line in the editor."""
        frame = self.frame
        if not frame:
            return
        filename = frame.f_code.co_filename
        lineno = frame.f_lineno
        if filename[:1] + filename[-1:] != "<>" and os.path.exists(filename):
            self._open_source(filename, lineno)

    def _open_source(self, filename, lineno):
        """Open a file and jump to a specific line (delegated to pyshell)."""
        if hasattr(self.pyshell, '_open_source_callback') and \
                self.pyshell._open_source_callback:
            self.pyshell._open_source_callback(filename, lineno)

    # ── Debug control commands ─────────────────────────────────────────────

    def cont(self):
        """Continue execution (Go)."""
        self.idb.set_continue()
        self._abort_loop()

    def step(self):
        """Step into next line."""
        self.idb.set_step()
        self._abort_loop()

    def next(self):
        """Step over (next) — don't enter function calls."""
        if self.frame:
            self.idb.set_next(self.frame)
        self._abort_loop()

    def ret(self):
        """Step out — run until current function returns."""
        if self.frame:
            self.idb.set_return(self.frame)
        self._abort_loop()

    def quit(self):
        """Quit debugging — terminate the running program."""
        self.idb.set_quit()
        self._abort_loop()

    def _abort_loop(self):
        """Exit the nested event loop in interaction()."""
        self.root.tk.call('set', '::tknote_debug_wait', '1')

    # ── View management ────────────────────────────────────────────────────

    def show_stack(self):
        """Toggle the stack viewer."""
        if Debugger.vstack.get():
            if not self.stackviewer:
                self.stackviewer = StackViewer(self.fstack, self)
                self.stackviewer.pack(fill=tk.BOTH, expand=True)
                if self.frame:
                    stack, i = self.idb.get_stack(self.frame, None)
                    self.stackviewer.load_stack(stack, i)
        else:
            if self.stackviewer:
                self.stackviewer.close()
                self.stackviewer = None

    def show_source(self):
        """Toggle source line tracking."""
        if Debugger.vsource.get():
            self.sync_source_line()

    def show_locals(self):
        """Toggle the locals viewer."""
        if Debugger.vlocals.get():
            if not self.localsviewer:
                self.localsviewer = NamespaceViewer(self.flocals, "Locals")
                self.localsviewer.pack(fill=tk.BOTH, expand=True)
        else:
            if self.localsviewer:
                self.localsviewer.close()
                self.localsviewer = None
        self.show_variables()

    def show_globals(self):
        """Toggle the globals viewer."""
        if Debugger.vglobals.get():
            if not self.globalsviewer:
                self.globalsviewer = NamespaceViewer(self.fglobals, "Globals")
                self.globalsviewer.pack(fill=tk.BOTH, expand=True)
        else:
            if self.globalsviewer:
                self.globalsviewer.close()
                self.globalsviewer = None
        self.show_variables()

    def show_variables(self, force=False):
        """Refresh the locals/globals viewers."""
        frame = self.frame
        ldict = frame.f_locals if frame else None
        gdict = frame.f_globals if frame else None

        # If locals and globals are the same dict, hide locals
        if self.localsviewer and self.globalsviewer and ldict is gdict:
            ldict = None

        if self.localsviewer:
            self.localsviewer.load_dict(ldict, force)
        if self.globalsviewer:
            self.globalsviewer.load_dict(gdict, force)

    def show_frame(self, stackitem):
        """Select a stack frame for inspection."""
        self.frame = stackitem[0]  # frame; lineno is stackitem[1]
        self.show_variables()
        if Debugger.vsource.get():
            self.sync_source_line()

    # ── Run ────────────────────────────────────────────────────────────────

    def run(self, source, filename=None):
        """Run source code under debugger control.

        :param source: Python source code string to execute.
        :param filename: Optional file path used when compiling the source.
            When provided, breakpoints keyed by this filename will match,
            enabling breakpoint-based debugging.
        """
        # Handle nested run (e.g., running code while already in debugger)
        if self.nesting_level > 0:
            self._abort_loop()
            self.root.after(100, lambda: self.run(source, filename))
            return

        try:
            self.interacting = True
            # Use the shell's locals so variables persist in the shell
            shell_locals = getattr(self.pyshell, '_locals', {})
            # Compile with the real filename so breakpoints match
            if isinstance(source, str):
                code = compile(source, filename or '<string>', 'exec')
            else:
                code = source
            self.idb.run(code, shell_locals, shell_locals)
        finally:
            self.interacting = False
            self._clear_views()

    def _clear_views(self):
        """Clear all viewers when session ends."""
        if self.stackviewer:
            self.stackviewer.load_stack([], None)
        if self.localsviewer:
            self.localsviewer.load_dict(None)
        if self.globalsviewer:
            self.globalsviewer.load_dict(None)
        self.status.configure(text="")
        self.error.configure(text="", background=self.errorbg)
        self.frame = None

    # ── Close ──────────────────────────────────────────────────────────────

    def close(self):
        """Close the debugger — quit bdb and destroy panel."""
        try:
            self.quit()
        except Exception:
            pass
        if self.interacting:
            self.root.bell()
            return
        if self.stackviewer:
            self.stackviewer.close()
            self.stackviewer = None
        if self.localsviewer:
            self.localsviewer.close()
            self.localsviewer = None
        if self.globalsviewer:
            self.globalsviewer.close()
            self.globalsviewer = None
        self.destroy()

    # ── Breakpoints ────────────────────────────────────────────────────────

    def set_breakpoint(self, filename, lineno):
        """Set a breakpoint in the debugger."""
        msg = self.idb.set_break(filename, lineno)
        if filename not in self.breakpoints:
            self.breakpoints[filename] = set()
        self.breakpoints[filename].add(lineno)
        return msg

    def clear_breakpoint(self, filename, lineno):
        """Clear a breakpoint in the debugger."""
        msg = self.idb.clear_break(filename, lineno)
        if filename in self.breakpoints:
            self.breakpoints[filename].discard(lineno)
            if not self.breakpoints[filename]:
                del self.breakpoints[filename]
        return msg

    def clear_file_breaks(self, filename):
        """Clear all breakpoints for a file."""
        msg = self.idb.clear_all_file_breaks(filename)
        self.breakpoints.pop(filename, None)
        return msg

    def get_file_breaks(self, filename):
        """Get the set of breakpoint line numbers for a file."""
        return self.breakpoints.get(filename, set())
