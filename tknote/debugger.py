"""Debugger — ported from idlelib/debugger.py for in-process use.

Provides a GUI debug control panel (ttk.Frame) matching IDLE's
Debugger window layout exactly.

Based on idlelib's debugger.Debugger, adapted for:
  - Inline panel (ttk.Frame) instead of separate Toplevel
  - Direct bdb.Bdb instead of Idb proxy
  - tknote's shell integration
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
# StackViewer — ported from idlelib/debugger.py StackViewer(ScrolledList)
# ═══════════════════════════════════════════════════════════════════════════════

class StackViewer(ttk.Frame):
    """Display the current call stack in a scrollable listbox.

    Ported from idlelib.debugger.StackViewer(ScrolledList).
    """

    def __init__(self, master, gui):
        super().__init__(master)
        self.gui = gui
        self._stack = []

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(self)
        scrollbar.grid(row=0, column=1, sticky='ns')

        self.listbox = tk.Listbox(
            self, yscrollcommand=scrollbar.set,
            font=('TkFixedFont', 10), exportselection=False,
            width=80)
        self.listbox.grid(row=0, column=0, sticky='nsew')
        scrollbar.config(command=self.listbox.yview)

        # Right-click context menu (IDLE's popup_event / fill_menu)
        self.listbox.bind('<Button-2>', self._popup_event)
        self.listbox.bind('<Button-3>', self._popup_event)
        self.listbox.bind('<Control-Button-1>', self._popup_event)
        self.listbox.bind('<Double-Button-1>', self._on_double)
        self.listbox.bind('<ButtonRelease-1>', self._on_select)

        self._menu = tk.Menu(self, tearoff=0)
        self._menu.add_command(label="Go to source line",
                               command=self._goto_source_line)
        self._menu.add_command(label="Show stack frame",
                               command=self._show_stack_frame)

    # ── IDLE's ScrolledList-compatible API ───────────────────────────────

    def clear(self):
        self.listbox.delete(0, tk.END)

    def append(self, item):
        self.listbox.insert(tk.END, item)

    def select(self, index):
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.see(index)

    # ── IDLE's load_stack ────────────────────────────────────────────────

    def load_stack(self, stack, index=None):
        self._stack = stack
        self.clear()
        for i, (frame, lineno) in enumerate(stack):
            try:
                modname = frame.f_globals["__name__"]
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
            self.append(item)
        if index is not None:
            self.select(index)

    # ── IDLE's event handlers ────────────────────────────────────────────

    def _popup_event(self, event):
        if self._stack:
            idx = self.listbox.nearest(event.y)
            if 0 <= idx < len(self._stack):
                self.listbox.selection_clear(0, tk.END)
                self.listbox.selection_set(idx)
                try:
                    self._menu.tk_popup(event.x_root, event.y_root)
                finally:
                    self._menu.grab_release()

    def _on_select(self, event):
        idx = self.listbox.curselection()
        if idx and 0 <= idx[0] < len(self._stack):
            self.gui.show_frame(self._stack[idx[0]])

    def _on_double(self, event):
        idx = self.listbox.curselection()
        if idx:
            self._show_source(idx[0])

    def _goto_source_line(self):
        idx = self.listbox.index("active")
        self._show_source(idx)

    def _show_stack_frame(self):
        idx = self.listbox.index("active")
        if 0 <= idx < len(self._stack):
            self.gui.show_frame(self._stack[idx])

    def _show_source(self, index):
        if not (0 <= index < len(self._stack)):
            return
        frame, lineno = self._stack[index]
        filename = frame.f_code.co_filename
        self.gui._open_source(filename, lineno)

    def close(self):
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════════
# NamespaceViewer — ported from idlelib/debugger.py NamespaceViewer
# ═══════════════════════════════════════════════════════════════════════════════

class NamespaceViewer:
    """Global/local namespace viewer for debugger GUI.

    Ported directly from idlelib.debugger.NamespaceViewer.
    Uses Canvas + Frame with Label/Entry widgets for a table-like display.
    """

    def __init__(self, master, title, odict=None):
        import reprlib
        self.repr = reprlib.Repr()
        self.repr.maxstring = 60
        self.repr.maxother = 60

        self.master = master
        self.title = title
        self.prev_odict = -1  # Sentinel for initial comparison

        width = 0
        height = 40
        if odict:
            height = 20 * len(odict)

        self.frame = frame = tk.Frame(master)
        self.frame.pack(expand=1, fill="both")

        self.label = tk.Label(frame, text=title, borderwidth=2,
                              relief="groove")
        self.label.pack(fill="x")

        self.vbar = vbar = ttk.Scrollbar(frame)
        vbar.pack(side="right", fill="y")

        self.canvas = canvas = tk.Canvas(frame,
            height=min(300, max(40, height)),
            scrollregion=(0, 0, width, height))
        canvas.pack(side="left", fill="both", expand=1)
        vbar["command"] = canvas.yview
        canvas["yscrollcommand"] = vbar.set

        self.subframe = subframe = tk.Frame(canvas)
        self.sfid = canvas.create_window(0, 0, window=subframe, anchor="nw")

        # Track canvas width for Configure event
        canvas.bind("<Configure>", self._on_canvas_configure)

        self.load_dict(odict)

    def _on_canvas_configure(self, event):
        """Keep the inner frame width synced with the canvas."""
        self.canvas.itemconfig(self.sfid, width=event.width)

    def load_dict(self, odict, force=0, rpc_client=None):
        """Load a dictionary into the viewer — IDLE's exact load_dict."""
        if odict is self.prev_odict and not force:
            return
        subframe = self.subframe
        frame = self.frame
        for c in list(subframe.children.values()):
            c.destroy()
        self.prev_odict = None
        if not odict:
            l = tk.Label(subframe, text="None")
            l.grid(row=0, column=0)
        else:
            keys_list = odict.keys()
            names = sorted(keys_list)
            row = 0
            for name in names:
                if name.startswith('__') and name.endswith('__'):
                    continue  # skip dunder names
                value = odict[name]
                svalue = self.repr.repr(value)
                if rpc_client:
                    svalue = svalue[1:-1]
                # Name label (left column)
                l = tk.Label(subframe, text=name)
                l.grid(row=row, column=0, sticky="nw")
                # Value entry (right column, read-only look but selectable)
                e = tk.Entry(subframe, width=0, borderwidth=0,
                             readonlybackground=subframe.cget('bg'))
                e.insert(0, svalue)
                e.configure(state='readonly')
                e.grid(row=row, column=1, sticky="nw")
                row += 1
        self.prev_odict = odict
        subframe.update_idletasks()
        width = subframe.winfo_reqwidth()
        height = subframe.winfo_reqheight()
        canvas = self.canvas
        self.canvas["scrollregion"] = (0, 0, width, height)
        if height > 300:
            canvas["height"] = 300
            frame.pack(expand=1)
        else:
            canvas["height"] = height
            frame.pack(expand=0)

    def close(self):
        self.frame.destroy()


# ═══════════════════════════════════════════════════════════════════════════════
# Debugger — GUI debug control panel (ttk.Frame)
# ═══════════════════════════════════════════════════════════════════════════════

class Debugger(ttk.Frame):
    """GUI debugger panel — ported from idlelib.debugger.Debugger.

    Matches IDLE's Debugger window layout exactly:
      - Inline ttk.Frame (instead of Toplevel)
      - Direct bdb.Bdb (instead of Idb proxy)
      - tknote shell integration for open_source
    """

    vstack = None    # class-level BooleanVar for "Show Stack"
    vsource = None   # class-level BooleanVar for "Show Source"
    vlocals = None   # class-level BooleanVar for "Show Locals"
    vglobals = None  # class-level BooleanVar for "Show Globals"

    def __init__(self, parent, pyshell, show_header=True, **kwargs):
        """Create the debugger panel.

        :param parent: parent widget (usually a PanedWindow or Notebook).
        :param pyshell: The PythonShell instance that owns this debugger.
        :param show_header: If False, hide the header bar.
        """
        super().__init__(parent, **kwargs)
        self.pyshell = pyshell
        self._show_header = show_header
        self._close_callback = None
        self.frame = None          # current frame being inspected
        self.interacting = False   # True while waiting for user input
        self.nesting_level = 0     # for re-entrant run detection

        # Viewers (matching IDLE's class-level defaults)
        self.stackviewer = None
        self.localsviewer = None
        self.globalsviewer = None

        # Breakpoints: {filename: set(lineno), ...}
        self.breakpoints = {}

        # Create the bdb.Bdb instance (IDLE's Idb equivalent)
        self.idb = bdb.Bdb()
        self.idb.user_line = self._user_line
        self.idb.user_exception = self._user_exception
        self.idb.user_return = self._user_return
        self.idb.user_call = None

        self.make_gui()

    # ── bdb.Bdb callbacks (IDLE's Idb.user_line / user_exception) ────────

    def _user_line(self, frame):
        message = _frame2message(frame)
        try:
            self.interaction(message, frame)
        except tk.TclError:
            pass

    def _user_exception(self, frame, exc_info):
        message = _frame2message(frame)
        try:
            self.interaction(message, frame, exc_info)
        except tk.TclError:
            pass

    def _user_return(self, frame, retval):
        pass  # silent — IDLE also ignores returns

    # ── Close button ──────────────────────────────────────────────────────

    def set_close_callback(self, callback):
        self._close_callback = callback

    def _on_close(self):
        self.pyshell.close_debugger()

    # ── Focus ─────────────────────────────────────────────────────────────

    def focus_input(self):
        if self.buttons:
            self.buttons[0].focus_set()

    # ── GUI construction (IDLE's make_gui, adapted for ttk.Frame) ─────────

    def make_gui(self):
        """Build the debugger panel — IDLE's exact layout.

        IDLE layout (top-to-bottom, pack-based):
          bframe (anchor='w')   — buttons + checkboxes
            [Go][Step][Over][Out][Quit]   (pack side='left')
            cframe                         (pack side='left')
              [Stack][Source]              (grid row=0)
              [Locals][Globals]            (grid row=1)
          status               — current position
          error                — exception info
          fstack               — stack viewer (expand, fill both)
          flocals              — locals viewer (expand, fill both)
          fglobals             — globals viewer (expand, fill both)
        """
        root = self.pyshell.winfo_toplevel()
        self.root = root

        if self._show_header:
            header = ttk.Frame(self)
            header.pack(fill='x', anchor='n')
            ttk.Label(header, text='Debug Control',
                      font=('Helvetica', 10, 'bold')).pack(
                side=tk.LEFT, padx=(4, 0), pady=(2, 0))
            ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill='x')

        # ── bframe: button bar + checkboxes (IDLE: Frame(top), pack anchor='w')
        self.bframe = bframe = tk.Frame(self)  # tk.Frame matching IDLE
        bframe.pack(anchor='w', fill='x', padx=4, pady=(4, 2))

        self.buttons = bl = []

        # IDLE's exact button creation pattern
        self.bcont = b = ttk.Button(bframe, text='Go', command=self.cont)
        bl.append(b)
        self.bstep = b = ttk.Button(bframe, text='Step', command=self.step)
        bl.append(b)
        self.bnext = b = ttk.Button(bframe, text='Over', command=self.next)
        bl.append(b)
        self.bret = b = ttk.Button(bframe, text='Out', command=self.ret)
        bl.append(b)
        self.bquit = b = ttk.Button(bframe, text='Quit', command=self.quit)
        bl.append(b)

        for b in bl:
            b.configure(state='disabled')
            b.pack(side='left')

        # ── cframe: checkboxes (IDLE: Frame(bframe), pack side='left')
        self.cframe = cframe = tk.Frame(bframe)
        cframe.pack(side='left', padx=(12, 0))

        if not self.vstack:
            self.__class__.vstack = tk.BooleanVar(root)
            self.vstack.set(True)
        self.bstack = tk.Checkbutton(cframe,
            text='Stack', command=self.show_stack, variable=self.vstack)
        self.bstack.grid(row=0, column=0, sticky='w')

        if not self.vsource:
            self.__class__.vsource = tk.BooleanVar(root)
            self.vsource.set(True)
        self.bsource = tk.Checkbutton(cframe,
            text='Source', command=self.show_source, variable=self.vsource)
        self.bsource.grid(row=0, column=1, sticky='w')

        if not self.vlocals:
            self.__class__.vlocals = tk.BooleanVar(root)
            self.vlocals.set(True)
        self.blocals = tk.Checkbutton(cframe,
            text='Locals', command=self.show_locals, variable=self.vlocals)
        self.blocals.grid(row=1, column=0, sticky='w')

        if not self.vglobals:
            self.__class__.vglobals = tk.BooleanVar(root)
        self.bglobals = tk.Checkbutton(cframe,
            text='Globals', command=self.show_globals, variable=self.vglobals)
        self.bglobals.grid(row=1, column=1, sticky='w')

        # ── Status & Error labels (IDLE: Label(top, anchor='w'))
        self.status = tk.Label(self, text='', anchor='w')
        self.status.pack(anchor='w', fill='x', padx=4)

        self.error = tk.Label(self, text='', anchor='w')
        self.error.pack(anchor='w', fill='x', padx=4)
        self.errorbg = self.error.cget('background')

        # ── Viewer frames (IDLE: Frame(top), pack expand=1 fill='both')
        self.fstack = tk.Frame(self, height=1)
        self.fstack.pack(expand=1, fill='both', padx=4, pady=(2, 0))

        self.flocals = tk.Frame(self)
        self.flocals.pack(expand=1, fill='both', padx=4, pady=(2, 0))

        self.fglobals = tk.Frame(self, height=1)
        self.fglobals.pack(expand=1, fill='both', padx=4, pady=(2, 4))

        # Show initial views
        if self.vstack.get():
            self.show_stack()
        if self.vlocals.get():
            self.show_locals()
        if self.vglobals.get():
            self.show_globals()

        # Initialize the vwait sentinel
        self.root.tk.call('set', '::tknote_debug_wait', '0')

    # ── Interaction (IDLE's interaction, adapted for in-process) ──────────

    def interaction(self, message, frame, info=None):
        """Handle a debugger stop: update UI and wait for user command."""
        self.frame = frame
        self.status.configure(text=message)

        if info:
            type, value, tb = info
            try:
                m1 = type.__name__
            except AttributeError:
                m1 = str(type)
            if value is not None:
                try:
                    m1 = f"{m1}: {value}"
                except Exception:
                    pass
            bg = "yellow"
        else:
            m1 = ""
            tb = None
            bg = self.errorbg
        self.error.configure(text=m1, background=bg)

        sv = self.stackviewer
        if sv:
            stack, i = self.idb.get_stack(self.frame, tb)
            sv.load_stack(stack, i)

        self.show_variables(1)

        if self.vsource.get():
            self.sync_source_line()

        for b in self.buttons:
            b.configure(state='normal')

        # Nested event loop — vwait (IDLE's exact approach)
        self.nesting_level += 1
        self.root.tk.call('vwait', '::tknote_debug_wait')
        self.nesting_level -= 1

        for b in self.buttons:
            b.configure(state='disabled')
        self.status.configure(text='')
        self.error.configure(text='', background=self.errorbg)
        self.frame = None

    # ── Source line tracking (IDLE's sync_source_line) ────────────────────

    def sync_source_line(self):
        frame = self.frame
        if not frame:
            return
        filename, lineno = self._frame2fileline(frame)
        if filename[:1] + filename[-1:] != "<>" and os.path.exists(filename):
            self._open_source(filename, lineno)

    def _frame2fileline(self, frame):
        code = frame.f_code
        return code.co_filename, frame.f_lineno

    def _open_source(self, filename, lineno):
        if hasattr(self.pyshell, '_open_source_callback') and \
                self.pyshell._open_source_callback:
            self.pyshell._open_source_callback(filename, lineno)

    # ── Debug control commands (IDLE's cont/step/next/ret/quit) ───────────

    def cont(self):
        self.idb.set_continue()
        self._abort_loop()

    def step(self):
        self.idb.set_step()
        self._abort_loop()

    def next(self):
        if self.frame:
            self.idb.set_next(self.frame)
        self._abort_loop()

    def ret(self):
        if self.frame:
            self.idb.set_return(self.frame)
        self._abort_loop()

    def quit(self):
        self.idb.set_quit()
        self._abort_loop()

    def _abort_loop(self):
        self.root.tk.call('set', '::tknote_debug_wait', '1')

    # ── View management (IDLE's show_stack/source/locals/globals) ─────────

    def show_stack(self):
        if not self.stackviewer and self.vstack.get():
            self.stackviewer = sv = StackViewer(self.fstack, self)
            sv.pack(fill='both', expand=True)
            if self.frame:
                stack, i = self.idb.get_stack(self.frame, None)
                sv.load_stack(stack, i)
        else:
            sv = self.stackviewer
            if sv and not self.vstack.get():
                self.stackviewer = None
                sv.close()
                self.fstack['height'] = 1

    def show_source(self):
        if self.vsource.get():
            self.sync_source_line()

    def show_frame(self, stackitem):
        self.frame = stackitem[0]  # lineno is stackitem[1]
        self.show_variables()
        if self.vsource.get():
            self.sync_source_line()

    def show_locals(self):
        lv = self.localsviewer
        if self.vlocals.get():
            if not lv:
                self.localsviewer = NamespaceViewer(self.flocals, "Locals")
        else:
            if lv:
                self.localsviewer = None
                lv.close()
                self.flocals['height'] = 1
        self.show_variables()

    def show_globals(self):
        gv = self.globalsviewer
        if self.vglobals.get():
            if not gv:
                self.globalsviewer = NamespaceViewer(self.fglobals, "Globals")
        else:
            if gv:
                self.globalsviewer = None
                gv.close()
                self.fglobals['height'] = 1
        self.show_variables()

    def show_variables(self, force=0):
        lv = self.localsviewer
        gv = self.globalsviewer
        frame = self.frame
        if not frame:
            ldict = gdict = None
        else:
            ldict = frame.f_locals
            gdict = frame.f_globals
            if lv and gv and ldict is gdict:
                ldict = None
        if lv:
            lv.load_dict(ldict, force)
        if gv:
            gv.load_dict(gdict, force)

    # ── Run (IDLE's run, adapted for in-process source execution) ─────────

    def run(self, source, filename=None):
        """Run source code under debugger control.

        :param source: Python source code string to execute.
        :param filename: Optional file path used when compiling the source.
        """
        if self.nesting_level > 0:
            self._abort_loop()
            self.root.after(100, lambda: self.run(source, filename))
            return
        try:
            self.interacting = True
            shell_locals = getattr(self.pyshell, '_locals', {})
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
        self.status.configure(text='')
        self.error.configure(text='', background=self.errorbg)
        self.frame = None

    # ── Close (IDLE's close, adapted for inline panel) ────────────────────

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
