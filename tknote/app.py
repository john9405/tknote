"""MarkdownEditor — main application window with file tree, git, search, etc."""

import os
import re
import shlex
import textwrap
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .editor.tabbed import TabbedEditor
from .editor.widget import EditorWidget
from .terminal.shell import PythonShell
from .terminal.packages import PackageManager
from .terminal.terminal import SystemTerminal
from .venv_manager import VenvManager
from .file_tree import FileTreePanel
from .git_panel import GitPanel


class MarkdownEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("IDLE")
        self.root.geometry("1200x700")

        self.current_folder = None
        self.venv_manager = VenvManager()
        self._last_search_query = ''
        self._last_search_case = False
        self.setup_ui()
        self.root.protocol('WM_DELETE_WINDOW', self._on_window_close)

    # ---- Editor property (delegates to active tab) ------------------------

    @property
    def editor(self):
        """Return the active tab's editor widget."""
        return self.tabbed_editor.get_active_editor()

    # ---- UI setup ----------------------------------------------------------

    def setup_ui(self):
        # -- Menubar --
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="New", command=self.new_file, accelerator="Cmd+N")
        file_menu.add_command(label="Open", command=self.open_file, accelerator="Cmd+O")
        file_menu.add_command(label="Open Folder", command=self.open_folder, accelerator="Cmd+Shift+O")
        file_menu.add_command(label="Close Folder", command=self.close_folder)
        file_menu.add_command(label="Save", command=self.save_file, accelerator="Cmd+S")
        file_menu.add_command(label="Save As", command=self.save_file_as, accelerator="Cmd+Shift+S")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)

        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(label="Undo", command=self.undo, accelerator="Cmd+Z")
        edit_menu.add_command(label="Redo", command=self.redo, accelerator="Cmd+Shift+Z")
        edit_menu.add_separator()
        edit_menu.add_command(label="Select All", command=self.select_all, accelerator="Cmd+A")
        edit_menu.add_command(label="Cut", command=self.cut, accelerator="Cmd+X")
        edit_menu.add_command(label="Copy", command=self.copy, accelerator="Cmd+C")
        edit_menu.add_command(label="Paste", command=self.paste, accelerator="Cmd+V")
        edit_menu.add_separator()
        edit_menu.add_command(label="Find...", command=self.show_find_dialog, accelerator="Cmd+F")
        edit_menu.add_command(label="Find Again", command=self.find_again, accelerator="Cmd+G")
        edit_menu.add_command(label="Find Selection", command=self.find_selection, accelerator="Cmd+F3")
        edit_menu.add_command(label="Find in Files...", command=self.show_search_dialog, accelerator="Cmd+Shift+F")
        edit_menu.add_command(label="Replace...", command=self.show_replace_dialog, accelerator="Cmd+R")
        edit_menu.add_separator()
        edit_menu.add_command(label="Go to Line", command=self.show_goto_line, accelerator="Cmd+J")
        edit_menu.add_command(label="Show Completions", command=self._show_completions, accelerator="Ctrl+Space")
        edit_menu.add_command(label="Show Call Tip", command=self._show_calltip, accelerator="Ctrl+\\")
        edit_menu.add_command(label="Show Surrounding Parens", command=self.flash_paren, accelerator="Ctrl+0")

        format_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Format", menu=format_menu)
        format_menu.add_command(label="Format Paragraph", command=self.format_paragraph)
        format_menu.add_separator()
        format_menu.add_command(label="Indent Region", command=self.indent_region, accelerator="Cmd+]")
        format_menu.add_command(label="Dedent Region", command=self.dedent_region, accelerator="Cmd+[")
        format_menu.add_command(label="Comment Out Region", command=self.comment_region, accelerator="Ctrl+3")
        format_menu.add_command(label="Uncomment Region", command=self.uncomment_region, accelerator="Ctrl+4")
        format_menu.add_separator()
        format_menu.add_command(label="Tabify Region", command=self.tabify_region)
        format_menu.add_command(label="Untabify Region", command=self.untabify_region)
        format_menu.add_command(label="Toggle Tabs", command=self.toggle_tabs)
        format_menu.add_separator()
        format_menu.add_command(label="New Indent Width", command=self.change_indentwidth)
        format_menu.add_command(label="Strip Trailing Whitespace", command=self.rstrip_region)

        run_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Run", menu=run_menu)
        run_menu.add_command(label="Run Module", command=self.run_current_file, accelerator="F5")
        run_menu.add_command(label="Run... Customized", command=self.run_custom)
        run_menu.add_command(label="Check Module", command=self.check_module)
        run_menu.add_separator()
        run_menu.add_command(label="Debug Module", command=self.debug_module)
        run_menu.add_command(label="Run in Terminal", command=self.run_in_terminal, accelerator="Cmd+Shift+R")
        run_menu.add_separator()
        run_menu.add_command(label="Python Shell", command=self._open_python_shell)

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Toggle Bottom Panel", command=self._toggle_bottom_notebook, accelerator="Cmd+Shift+J")

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About tknote", command=self.show_about)

        # -- Editor context menu (right-click) --
        self.editor_context_menu = None  # Created dynamically on right-click

        # -- Main layout --
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ---- PanedWindow: primary sidebar + main area + auxiliary sidebar ----
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.main_paned = paned

        # ---- Primary sidebar / Main area / Auxiliary sidebar ----
        primary_sidebar_frame = ttk.Frame(paned, width=260)
        editor_paned = ttk.PanedWindow(paned, orient=tk.VERTICAL)
        auxiliary_sidebar_frame = ttk.Frame(paned, width=320)
        primary_sidebar_frame.pack_propagate(False)
        auxiliary_sidebar_frame.pack_propagate(False)

        paned.add(primary_sidebar_frame, weight=0)
        paned.add(editor_paned, weight=1)
        self.primary_sidebar_frame = primary_sidebar_frame
        self.auxiliary_sidebar_frame = auxiliary_sidebar_frame
        self._auxiliary_sidebar_visible = False
        self.editor_paned = editor_paned

        # -- File tree panel (inside primary sidebar) --
        self.file_tree = FileTreePanel(primary_sidebar_frame, callbacks={
            'get_current_folder': lambda: self.current_folder,
            'set_current_folder': lambda v: setattr(self, 'current_folder', v),
            'open_file_in_tab': self._open_file_in_tab,
            'file_deleted': self._on_file_deleted,
            'file_path_changed': self._on_file_path_changed,
            'new_file_requested': self.new_file,
        })

        # -- Git panel (inside auxiliary sidebar) --
        self.git_panel = GitPanel(auxiliary_sidebar_frame, callbacks={
            'get_current_folder': lambda: self.current_folder,
            'open_file_in_tab': self._open_file_in_tab,
            'refresh_file_tree': self.file_tree.refresh,
            'folder_opened': self._on_git_folder_opened,
            'reload_file': self._reload_file_in_tab,
            'close_diff_tab': self._close_diff_tab,
            'open_diff_tab': self._open_diff_tab,
        })

        # Show sidebars by default
        self.file_tree.pack(fill=tk.BOTH, expand=True)
        self.git_panel.pack(fill=tk.BOTH, expand=True)

        # -- Tabbed editor --
        self.tabbed_editor = TabbedEditor(
            editor_paned,
            on_tab_created=self._bind_editor_shortcuts,
            on_tab_switch=self._on_tab_switch,
            on_close_request=self._on_close_request,
            on_new_tab_request=self.new_file,
        )
        editor_paned.add(self.tabbed_editor, weight=3)

        # -- Bottom notebook (tabs for Shell, Terminal, Debug, Packages) --
        cwd = os.path.expanduser('~')
        self._bottom_notebook = ttk.Notebook(editor_paned)
        self._bottom_notebook_shown = False
        editor_paned.add(self._bottom_notebook, weight=0)

        self._shell_panel = PythonShell(self._bottom_notebook, show_header=False)
        self._shell_panel._open_source_callback = self._on_debug_source
        self._pkg_panel = PackageManager(self._bottom_notebook)
        self._sys_term = SystemTerminal(self._bottom_notebook, cwd=cwd, show_header=False)
        from .debugger import Debugger
        self._debug_panel = Debugger(self._bottom_notebook, self._shell_panel, show_header=False)

        self._bottom_notebook.add(self._shell_panel, text='Python Shell')
        self._bottom_notebook.add(self._sys_term, text='Terminal')
        self._bottom_notebook.add(self._debug_panel, text='Debug')
        self._bottom_notebook.add(self._pkg_panel, text='Packages')

        # -- Status bar --
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=8)

        # Right: source control panel toggle
        self._git_panel_status = ttk.Label(
            status_frame, text='Source Control', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._git_panel_status.pack(side=tk.RIGHT)
        self._git_panel_status.bind('<Button-1>', lambda e: self._toggle_auxiliary_sidebar())

        # Right: bottom panel toggle (unified button)
        self._panel_status = ttk.Label(
            status_frame, text='Panel ▴', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._panel_status.pack(side=tk.RIGHT)
        self._panel_status.bind('<Button-1>', lambda e: self._toggle_bottom_notebook())

        # Python environment indicator (right)
        self._venv_status = ttk.Label(
            status_frame, text='Python', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._venv_status.pack(side=tk.RIGHT)
        self._venv_status.bind('<Button-1>', lambda e: self._show_venv_popup())

        # Cursor position (right)
        self._cursor_pos = ttk.Label(
            status_frame, text='Ln 1, Col 1', relief=tk.SUNKEN,
            padding=(6, 1))
        self._cursor_pos.pack(side=tk.RIGHT)

        self._status_frame = status_frame
        self.git_panel.refresh()

        # Push notebook sash down to hide it
        self.root.update_idletasks()
        try:
            total = editor_paned.winfo_height()
            editor_paned.sashpos(0, total)
        except tk.TclError:
            pass

    # ---- Shortcut binding for new editors ----------------------------------

    def _bind_editor_shortcuts(self, tab_id, editor):
        """Bind all keyboard shortcuts + events to a newly created editor."""
        editor.bind("<Command-n>", lambda _: self.new_file())
        editor.bind("<Command-o>", lambda _: self.open_file())
        editor.bind("<Command-s>", lambda _: self.save_file())
        editor.bind("<Command-Shift-S>", lambda _: self.save_file_as())
        editor.bind("<Command-Shift-J>", lambda _: self._toggle_bottom_notebook())
        editor.bind("<Command-f>", lambda _: self.show_find_dialog())
        editor.bind("<Command-g>", lambda _: self.find_again())
        editor.bind("<Command-F3>", lambda _: self.find_selection())
        editor.bind("<Command-Shift-f>", lambda _: self.show_search_dialog())
        editor.bind("<Command-r>", lambda _: self.show_replace_dialog())
        editor.bind("<F5>", lambda _: self.run_current_file())
        editor.bind("<Command-j>", lambda _: self.show_goto_line())
        editor.bind("<Command-l>", lambda _: self.show_goto_line())
        editor.bind("<Command-a>", lambda _: self.select_all())
        editor.bind("<Command-z>", lambda _: self.undo())
        editor.bind("<Command-Shift-Z>", lambda _: self.redo())
        editor.bind("<Command-Shift-R>", lambda _: self.run_in_terminal())
        editor.bind("<Command-bracketright>", lambda _: self.indent_region())
        editor.bind("<Command-bracketleft>", lambda _: self.dedent_region())
        editor.bind("<Control-Key-3>", lambda _: self.comment_region())
        editor.bind("<Control-Key-4>", lambda _: self.uncomment_region())
        editor.bind("<Command-slash>", lambda _: self.comment_region())
        editor.bind("<Command-Shift-slash>", lambda _: self.uncomment_region())
        editor.bind("<Button-2>", self._editor_right_menu)
        editor.bind("<Button-3>", self._editor_right_menu)
        editor.bind("<Control-Button-1>", self._editor_right_menu)

        # Cursor position tracking
        editor.bind('<KeyRelease>', lambda e: self._update_cursor_pos())
        editor.bind('<ButtonRelease-1>', lambda e: self._update_cursor_pos())

    # ---- Bottom notebook management -----------------------------------------

    def _toggle_bottom_notebook(self):
        if self._bottom_notebook_shown:
            try:
                total = self.editor_paned.winfo_height()
                self.editor_paned.sashpos(0, total)
            except tk.TclError:
                pass
            self._bottom_notebook_shown = False
            self._update_status_indicators()
        else:
            self._bottom_notebook_shown = True
            self._update_status_indicators()
            self.root.after_idle(self._show_notebook_sash)

    def _show_notebook_sash(self):
        """Position the sash after layout has settled."""
        try:
            total = self.editor_paned.winfo_height()
            if total > 1:
                target = min(400, total // 3)
                self.editor_paned.sashpos(0, total - target)
        except tk.TclError:
            pass

    def _switch_to_tab(self, tab_name):
        tab_index = -1
        for i, child in enumerate(self._bottom_notebook.winfo_children()):
            if self._bottom_notebook.tab(i, 'text') == tab_name:
                tab_index = i
                break
        if tab_index < 0:
            return
        if self._bottom_notebook_shown:
            current = self._bottom_notebook.index('current')
            if current == tab_index:
                self._toggle_bottom_notebook()
                return
        if not self._bottom_notebook_shown:
            self._toggle_bottom_notebook()
        self._bottom_notebook.select(tab_index)
        self._update_status_indicators()
        child_widgets = self._bottom_notebook.winfo_children()
        if 0 <= tab_index < len(child_widgets):
            try:
                child_widgets[tab_index].focus_input()
            except AttributeError:
                pass

    def _update_status_indicators(self):
        if self._bottom_notebook_shown:
            self._panel_status.config(text='Panel ▾')
        else:
            self._panel_status.config(text='Panel ▴')

    # ---- Debugger ----------------------------------------------------------

    def _toggle_debugger(self):
        if self._shell_panel.is_debugging():
            self._shell_panel.close_debugger()
            if self._bottom_notebook_shown:
                current = self._bottom_notebook.index('current')
                if self._bottom_notebook.tab(current, 'text') == 'Debug':
                    self._switch_to_tab('Python Shell')
            self._update_status_indicators()
        else:
            self._shell_panel.open_debugger(self._debug_panel)
            self._load_breakpoints_to_debugger()
            self._switch_to_tab('Debug')

    def _toggle_breakpoint(self):
        """Toggle a breakpoint on the current cursor line in the active editor."""
        editor = self.editor
        if editor is None:
            return
        lineno = int(float(editor.index('insert')))
        added = editor.toggle_breakpoint(lineno)
        # Sync breakpoints to debugger if active
        self._sync_breakpoint_to_debugger(editor, lineno, added)

    def _clear_breakpoint(self):
        """Clear a breakpoint on the current cursor line if one exists."""
        editor = self.editor
        if editor is None:
            return
        lineno = int(float(editor.index('insert')))
        if editor.has_breakpoint(lineno):
            editor.toggle_breakpoint(lineno)
            # Sync to debugger if active
            self._sync_breakpoint_to_debugger(editor, lineno, False)

    def _load_breakpoints_to_debugger(self):
        """Load all breakpoints from open editors into the debugger."""
        dbg = self._shell_panel.get_debugger()
        if dbg is None:
            return
        for tab in self.tabbed_editor._tabs:
            editor = tab['editor']
            file_path = tab.get('file_path')
            if file_path and os.path.isfile(file_path):
                for lineno in editor.get_breakpoints():
                    dbg.set_breakpoint(file_path, lineno)

    def _sync_breakpoint_to_debugger(self, editor, lineno, added):
        """Sync a single breakpoint change to the debugger."""
        dbg = self._shell_panel.get_debugger()
        if dbg is None:
            return
        tab = self.tabbed_editor.get_active_tab()
        if not tab or not tab.get('file_path'):
            return
        file_path = tab['file_path']
        if os.path.isfile(file_path):
            if added:
                dbg.set_breakpoint(file_path, lineno)
            else:
                dbg.clear_breakpoint(file_path, lineno)

    def _on_debug_source(self, filename, lineno):
        """Open a file and jump to a specific line (called by debugger)."""
        self._open_file_in_tab(filename)
        editor = self.editor
        if editor:
            editor.mark_set('insert', f'{lineno}.0')
            editor.see(f'{lineno}.0')
            # Highlight the line
            editor.tag_remove('debug_line', '1.0', 'end')
            editor.tag_add('debug_line', f'{lineno}.0', f'{lineno}.0 lineend')
            editor.tag_config('debug_line', background='#ffeb3b')
            # Remove highlight after 2 seconds
            self.root.after(2000, lambda: editor.tag_remove('debug_line', '1.0', 'end'))

    def _on_window_close(self):
        """Clean up subprocesses before closing."""
        self._shell_panel.cleanup()
        self._sys_term.cleanup()
        self.root.destroy()

    # ---- Python environment management -------------------------------------

    def _update_venv_status(self):
        """Refresh the venv indicator label in the status bar."""
        if not self.current_folder:
            self._venv_status.config(text='Python')
            return
        name = self.venv_manager.get_display_name()
        self._venv_status.config(text=name)

    def _show_venv_popup(self, event=None):
        """Show a popup menu for selecting/creating Python environments."""
        if not self.current_folder:
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Python Environment", state=tk.DISABLED)
        menu.add_separator()

        # System Python (always available)
        active = self.venv_manager.venv_path
        label = "✓ System Python" if not active else "System Python"
        menu.add_command(label=label, command=self._deactivate_venv)

        # Auto-detected venvs in the project folder
        detected = self.venv_manager.detect_venvs()
        if detected:
            menu.add_separator()
            for vp in detected:
                name = os.path.basename(vp)
                if vp == active:
                    label = f"✓ {name}"
                else:
                    label = f"    {name}"
                menu.add_command(
                    label=label,
                    command=lambda p=vp: self._select_venv(p))

        menu.add_separator()
        menu.add_command(label="Create New Venv...", command=self._create_venv)
        menu.add_command(label="Locate Existing...", command=self._locate_venv)

        if self.venv_manager.is_active:
            menu.add_separator()
            menu.add_command(label="Deactivate", command=self._deactivate_venv)

        try:
            menu.post(
                self._venv_status.winfo_rootx(),
                self._venv_status.winfo_rooty() + self._venv_status.winfo_height())
        finally:
            # Keep menu alive until dismissed
            menu.grab_release()

    def _select_venv(self, venv_path):
        """Activate a selected virtual environment."""
        try:
            self.venv_manager.activate(venv_path)
            self._apply_venv_to_subsystems()
            self._update_venv_status()
        except ValueError as e:
            messagebox.showerror("Venv Error", str(e), parent=self.root)

    def _create_venv(self):
        """Create a new virtual environment in the current project folder."""
        name = simpledialog.askstring(
            "Create Virtual Environment",
            "Enter environment name:",
            initialvalue=".venv",
            parent=self.root)
        if not name:
            return
        try:
            self.venv_manager.create_venv(name)
            self._apply_venv_to_subsystems()
            self._update_venv_status()
        except Exception as e:
            messagebox.showerror(
                "Error", f"Failed to create venv:\n{e}", parent=self.root)

    def _locate_venv(self):
        """Browse the filesystem for an existing venv directory."""
        path = filedialog.askdirectory(
            title="Select Python Virtual Environment",
            parent=self.root)
        if path:
            self._select_venv(path)

    def _deactivate_venv(self):
        """Revert to system Python."""
        if not self.venv_manager.is_active:
            return
        self.venv_manager.deactivate()
        self._apply_venv_to_subsystems()
        self._update_venv_status()

    def _apply_venv_to_subsystems(self):
        """Apply the current venv state to all three bottom panels."""
        venv_path = self.venv_manager.venv_path
        site_packages = self.venv_manager.get_site_packages()
        self._sys_term.set_venv(venv_path)
        self._shell_panel.set_venv(site_packages)
        self._pkg_panel.set_venv(venv_path)

    # ---- Tab event callbacks -----------------------------------------------

    def _on_tab_switch(self, tab_id):
        """Called when the active tab changes."""
        self.root.after(1, self._update_cursor_pos)

    def _update_cursor_pos(self):
        """Update the cursor position label in the status bar."""
        editor = self.editor
        if editor is None:
            return
        try:
            pos = editor.index(tk.INSERT)
            line, col = pos.split('.')
            self._cursor_pos.config(text=f'Ln {line}, Col {int(col) + 1}')
        except (tk.TclError, ValueError, AttributeError):
            pass

    def _on_close_request(self, tab_id):
        """Called when the user clicks the close button on a tab."""
        tab = self.tabbed_editor.get_tab_by_id(tab_id)
        if not tab:
            return

        if tab['editor'].is_modified():
            title = tab['title']
            result = messagebox.askyesnocancel(
                "Unsaved Changes",
                f"'{title}' has unsaved changes.\n\nSave before closing?",
                parent=self.root
            )
            if result is None:   # Cancel
                return
            elif result:          # Yes — save
                if tab['file_path']:
                    try:
                        content = tab['editor'].get_text()
                        with open(tab['file_path'], 'w', encoding='utf-8') as f:
                            f.write(content)
                        tab['editor'].set_saved()
                    except Exception as e:
                        return
                else:
                    # No path — switch to tab and do Save As
                    idx = self.tabbed_editor.get_tab_index(tab_id)
                    if idx >= 0:
                        self.tabbed_editor.switch_to_tab(idx)
                        if not self.save_file_as():
                            return  # user cancelled Save As

        self.tabbed_editor.close_tab(self.tabbed_editor.get_tab_index(tab_id))

    # ---- Auxiliary sidebar switching ---------------------------------------

    def _show_auxiliary_sidebar(self):
        """Show the auxiliary sidebar that hosts the git panel."""
        if not self._auxiliary_sidebar_visible:
            self.main_paned.add(self.auxiliary_sidebar_frame, weight=0)
            self._auxiliary_sidebar_visible = True
        self._git_panel_status.config(text='Source Control')
        self.git_panel.refresh()

    def _hide_auxiliary_sidebar(self):
        """Hide the auxiliary sidebar."""
        if self._auxiliary_sidebar_visible:
            self.main_paned.forget(self.auxiliary_sidebar_frame)
            self._auxiliary_sidebar_visible = False
        self._git_panel_status.config(text='Source Control')

    def _toggle_auxiliary_sidebar(self):
        """Toggle the auxiliary sidebar from the status bar."""
        if self._auxiliary_sidebar_visible:
            self._hide_auxiliary_sidebar()
        else:
            self._show_auxiliary_sidebar()

    # ---- File opening helpers ----------------------------------------------

    def _open_file_in_tab(self, file_path):
        """Open a file: switch to its tab if already open, otherwise create one."""
        existing = self.tabbed_editor.find_tab_by_path(file_path)
        if existing >= 0:
            self.tabbed_editor.switch_to_tab(existing)
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            return

        self.tabbed_editor.add_tab(
            title=os.path.basename(file_path),
            file_path=file_path,
            content=content,
        )

    # ---- Bridge callbacks for FileTreePanel / GitPanel ---------------------

    def _on_file_deleted(self, path):
        """Close any open tab for a deleted file."""
        existing = self.tabbed_editor.find_tab_by_path(path)
        if existing >= 0:
            self.tabbed_editor._tabs[existing]['editor'].set_saved()
            self.tabbed_editor.close_tab(existing)
            if self.tabbed_editor.get_tab_count() == 0:
                self.tabbed_editor.add_tab(content='')

    def _on_file_path_changed(self, old_path, new_path):
        """Update tab path after a rename/move operation."""
        existing = self.tabbed_editor.find_tab_by_path(old_path)
        if existing >= 0:
            self.tabbed_editor.set_tab_path(
                self.tabbed_editor._tabs[existing]['id'], new_path)

    def _on_git_folder_opened(self, folder):
        """Handle folder opened after git clone — update app state."""
        self.current_folder = folder
        self.venv_manager.set_folder(folder)
        self.file_tree.populate(folder)
        self._sys_term.cd_to(folder)
        self._update_venv_status()
        if self.venv_manager.is_active:
            self._apply_venv_to_subsystems()

    def _reload_file_in_tab(self, file_path):
        """Reload file content in an open tab (e.g. after git rollback)."""
        tab_idx = self.tabbed_editor.find_tab_by_path(file_path)
        if tab_idx >= 0:
            with open(file_path, 'r', encoding='utf-8') as f:
                new_content = f.read()
            tab = self.tabbed_editor._tabs[tab_idx]
            tab['editor'].set_text(new_content)
            tab['editor'].set_saved()

    def _close_diff_tab(self, base_name):
        """Close the diff tab for the given file base name."""
        diff_title = f'Diff: {base_name}'
        for i, tab in enumerate(self.tabbed_editor._tabs):
            if tab['title'] == diff_title:
                self.tabbed_editor.close_tab(i)
                break

    def _open_diff_tab(self, title, content):
        """Open or update a diff tab with the given title and content."""
        for i, tab in enumerate(self.tabbed_editor._tabs):
            if tab['title'] == title:
                self.tabbed_editor.switch_to_tab(i)
                tab['editor'].set_text(content)
                tab['editor'].set_saved()
                return
        self.tabbed_editor.add_tab(title=title, content=content)

    # ---- Save helpers ------------------------------------------------------

    def _save_to_tab(self, tab):
        """Write a tab's editor content to its file_path."""
        try:
            content = tab['editor'].get_text()
            with open(tab['file_path'], 'w', encoding='utf-8') as f:
                f.write(content)
            tab['editor'].set_saved()
            return True
        except Exception:
            return False

    # ---- File operations ---------------------------------------------------

    def new_file(self):
        """Create a new tab, asking for a filename."""
        filename = simpledialog.askstring(
            "New File", "Enter file name:",
            parent=self.root
        )
        if not filename:
            filename = 'Untitled'
        self.tabbed_editor.add_tab(title=filename, content='')

    def open_file(self):
        file_path = filedialog.askopenfilename(
            title="Open File",
            filetypes=[("All files", "*.*")]
        )
        if file_path:
            self._open_file_in_tab(file_path)

    def open_folder(self):
        folder_path = filedialog.askdirectory(title="Open Folder", initialdir=os.path.expanduser('~'))
        if folder_path:
            self.current_folder = folder_path
            self.venv_manager.set_folder(folder_path)
            self.file_tree.populate(folder_path)
            self._sys_term.cd_to(folder_path)
            self.git_panel.refresh()
            self._update_venv_status()
            if self.venv_manager.is_active:
                self._apply_venv_to_subsystems()

    def close_folder(self):
        """Close the currently opened folder, clear file tree and git panel."""
        self.current_folder = None
        self.venv_manager.set_folder(None)
        self.file_tree.populate("")  # clears the tree
        self.git_panel.refresh()
        self._update_venv_status()
        self._apply_venv_to_subsystems()

    def save_file(self):
        tab = self.tabbed_editor.get_active_tab()
        if not tab:
            return False
        if tab['file_path']:
            return self._save_to_tab(tab)
        else:
            return self.save_file_as()

    def save_file_as(self):
        tab = self.tabbed_editor.get_active_tab()
        if not tab:
            return False
        file_path = filedialog.asksaveasfilename(
            title="Save File",
            filetypes=[("All files", "*.*")]
        )
        if file_path:
            self.tabbed_editor.set_tab_path(tab['id'], file_path)
            return self._save_to_tab(tab)
        return False

    # ---- Run code ----------------------------------------------------------

    def run_current_file(self):
        """Run the current file in the Python shell (IDLE Run Module style).

        Restarts the shell, saves the file, and executes with
        ``__name__ == '__main__'``.
        """
        tab = self.tabbed_editor.get_active_tab()
        if not tab:
            return

        # IDLE-style: must save before running
        if tab['file_path']:
            if tab['editor'].is_modified():
                self._save_to_tab(tab)
            file_path = tab['file_path']
        else:
            # Unsaved file — force Save As first
            if not self.save_file_as():
                return
            file_path = tab['file_path']

        source = tab['editor'].get_text()

        self._switch_to_tab('Python Shell')
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass

        self._shell_panel.run_code(source, file_path=file_path,
                                   python_exe=self.venv_manager.get_python_exe())

    def debug_module(self):
        """Debug the current file under the debugger.

        Opens the debugger panel and runs the file in-process so breakpoints
        and stepping are available.
        """
        tab = self.tabbed_editor.get_active_tab()
        if not tab:
            return

        # Save first (IDLE-style: must save before debugging)
        if tab['file_path']:
            if tab['editor'].is_modified():
                self._save_to_tab(tab)
            file_path = tab['file_path']
        else:
            # Unsaved file — force Save As first
            if not self.save_file_as():
                return
            file_path = tab['file_path']

        source = tab['editor'].get_text()

        self._switch_to_tab('Python Shell')
        if not self._shell_panel.is_debugging():
            self._shell_panel.open_debugger(self._debug_panel)
            self._load_breakpoints_to_debugger()
        self._switch_to_tab('Debug')

        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass

        self._shell_panel.run_code_debug(source, file_path=file_path)

    def run_in_terminal(self):
        """Run the current file in the system terminal."""
        tab = self.tabbed_editor.get_active_tab()
        if not tab:
            return

        # Save if there's a file path
        if tab['file_path']:
            if tab['editor'].is_modified():
                self._save_to_tab(tab)

            file_path = tab['file_path']
            dir_name = os.path.dirname(file_path)
            file_name = os.path.basename(file_path)

            self._switch_to_tab('Terminal')
            try:
                self.root.update_idletasks()
            except tk.TclError:
                pass

            # cd to directory and run
            self._sys_term.cd_to(dir_name)
            self._sys_term.send_command(f'python3 -- {shlex.quote(file_name)}')
        else:
            # Unsaved file — save first
            if not self.save_file_as():
                return
            self.run_in_terminal()

    def check_module(self):
        """Syntax-check the current file without running it."""
        tab = self.tabbed_editor.get_active_tab()
        if not tab:
            return
        source = tab['editor'].get_text()
        try:
            compile(source, tab.get('file_path', '<check>'), 'exec')
            messagebox.showinfo("Check Module", "No syntax errors found.", parent=self.root)
        except SyntaxError as e:
            messagebox.showerror(
                "Syntax Error",
                f"Line {e.lineno}: {e.msg}",
                parent=self.root,
            )

    def run_custom(self):
        tab = self.tabbed_editor.get_active_tab()
        if not tab:
            return
        if tab['file_path']:
            if tab['editor'].is_modified():
                self._save_to_tab(tab)
            file_path = tab['file_path']
        else:
            if not self.save_file_as():
                return
            file_path = tab['file_path']
        args_str = simpledialog.askstring('Run Customized', 'Command line arguments:', parent=self.root)
        if args_str is None:
            return
        try:
            args = shlex.split(args_str) if args_str.strip() else []
        except ValueError as e:
            messagebox.showerror('Run Customized', f'Invalid arguments: {e}', parent=self.root)
            return
        self._switch_to_tab('Terminal')
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass
        cwd = os.path.dirname(file_path)
        self._sys_term.cd_to(cwd)
        cmd = f'python3 -- {shlex.quote(os.path.basename(file_path))}'
        if args:
            cmd += ' ' + ' '.join(shlex.quote(a) for a in args)
        self._sys_term.send_command(cmd)

    def _open_python_shell(self):
        self._switch_to_tab('Python Shell')

    # ---- Region / Navigation (delegates to active editor) -------------------

    def comment_region(self):
        editor = self.editor
        if editor:
            editor.comment_region()

    def uncomment_region(self):
        editor = self.editor
        if editor:
            editor.uncomment_region()

    def indent_region(self):
        editor = self.editor
        if editor:
            editor.indent_region()

    def dedent_region(self):
        editor = self.editor
        if editor:
            editor.dedent_region()

    def show_goto_line(self):
        """Dialog: jump to a line number."""
        editor = self.editor
        if editor is None:
            return
        max_line = int(float(editor.index('end-1c')))
        lineno = simpledialog.askinteger(
            "Go to Line",
            f"Enter line number (1–{max_line}):",
            parent=self.root,
            minvalue=1,
            maxvalue=max_line,
        )
        if lineno:
            editor.go_to_line(lineno)

    def show_replace_dialog(self):
        """Dialog: find and replace text in the current file."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Replace")
        dialog.geometry("420x180")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Find:").grid(row=0, column=0, sticky='w', padx=10, pady=(10, 2))
        find_var = tk.StringVar()
        find_entry = ttk.Entry(dialog, textvariable=find_var)
        find_entry.grid(row=0, column=1, sticky='ew', padx=(0, 10), pady=(10, 2))
        find_entry.focus()

        ttk.Label(dialog, text="Replace with:").grid(row=1, column=0, sticky='w', padx=10, pady=2)
        replace_var = tk.StringVar()
        replace_entry = ttk.Entry(dialog, textvariable=replace_var)
        replace_entry.grid(row=1, column=1, sticky='ew', padx=(0, 10), pady=2)

        dialog.grid_columnconfigure(1, weight=1)

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(10, 5))

        def do_replace():
            query = find_var.get()
            replacement = replace_var.get()
            if not query:
                return
            editor = self.editor
            if editor is None:
                return
            content = editor.get('1.0', 'end-1c')
            new_content = content.replace(query, replacement)
            if new_content != content:
                editor.set_text(new_content)

        ttk.Button(btn_frame, text="Replace All", command=do_replace).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Close", command=dialog.destroy).pack(side=tk.LEFT, padx=2)

        find_entry.bind('<Return>', lambda _: do_replace())
        replace_entry.bind('<Return>', lambda _: do_replace())

    def show_about(self):
        """Show About dialog."""
        messagebox.showinfo(
            "About tknote",
            "tknote — Python IDE\n\n"
            "A lightweight IDE inspired by IDLE.\n"
            "Built with tkinter.",
            parent=self.root,
        )

    # ---- Text formatting ---------------------------------------------------

    def insert_format(self, before, after):
        editor = self.editor
        if editor is None:
            return
        try:
            sel_start = editor.index(tk.SEL_FIRST)
            sel_end = editor.index(tk.SEL_LAST)
            selected = editor.get(sel_start, sel_end)
            editor.delete(sel_start, sel_end)
            editor.insert(sel_start, f"{before}{selected}{after}")
        except:
            editor.insert(tk.INSERT, f"{before}{after}")

    def insert_heading(self):
        editor = self.editor
        if editor is None:
            return
        line_start = editor.index("insert linestart")
        line = editor.get(line_start, line_start + " lineend")
        if line.startswith("# "):
            editor.delete(line_start, line_start + "+2 chars")
            editor.insert(line_start, "## ")
        elif line.startswith("## "):
            editor.delete(line_start, line_start + "+3 chars")
            editor.insert(line_start, "### ")
        elif line.startswith("### "):
            editor.delete(line_start, line_start + "+4 chars")
            editor.insert(line_start, "#### ")
        else:
            editor.insert(line_start, "# ")

    def insert_link(self):
        editor = self.editor
        if editor is None:
            return
        try:
            sel_start = editor.index(tk.SEL_FIRST)
            sel_end = editor.index(tk.SEL_LAST)
            selected = editor.get(sel_start, sel_end)
            editor.delete(sel_start, sel_end)
            editor.insert(sel_start, f"[{selected}](url)")
        except:
            editor.insert(tk.INSERT, "[text](url)")

    # ---- Edit operations ---------------------------------------------------

    def undo(self):
        editor = self.editor
        if editor is None:
            return
        editor.undo()

    def redo(self):
        editor = self.editor
        if editor is None:
            return
        editor.redo()

    def cut(self):
        editor = self.editor
        if editor is None:
            return
        try:
            editor.event_generate("<<Cut>>")
        except tk.TclError:
            pass

    def copy(self):
        editor = self.editor
        if editor is None:
            return
        try:
            editor.event_generate("<<Copy>>")
        except tk.TclError:
            pass

    def paste(self):
        editor = self.editor
        if editor is None:
            return
        try:
            editor.event_generate("<<Paste>>")
        except tk.TclError:
            pass

    def select_all(self):
        editor = self.editor
        if editor is None:
            return "break"
        editor.tag_add(tk.SEL, "1.0", tk.END)
        editor.mark_set(tk.INSERT, "1.0")
        editor.see(tk.INSERT)
        return "break"

    def flash_paren(self):
        """Highlight matching brackets around cursor."""
        editor = self.editor
        if editor is None:
            return
        editor.flash_paren()

    def show_keyword_hint(self):
        """Show Python keyword hint popup at cursor position."""
        editor = self.editor
        if editor is None:
            return
        editor.show_keyword_hint()

    def _editor_right_menu(self, event):
        editor = self.editor
        if editor is None:
            return
        text = editor.get_text_widget()
        try:
            text.mark_set('insert', f'@{event.x},{event.y}')
        except tk.TclError:
            pass
        try:
            sel_first = text.index('sel.first')
            sel_last = text.index('sel.last')
            if not text.compare(sel_first, '<=', 'insert') or not text.compare('insert', '<=', sel_last):
                text.tag_remove('sel', '1.0', 'end')
                has_sel = False
            else:
                has_sel = True
        except tk.TclError:
            has_sel = False
        try:
            has_clipboard = bool(self.root.clipboard_get())
        except tk.TclError:
            has_clipboard = False
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label='Cut', command=self.cut, state=tk.NORMAL if has_sel else tk.DISABLED)
        menu.add_command(label='Copy', command=self.copy, state=tk.NORMAL if has_sel else tk.DISABLED)
        menu.add_command(label='Paste', command=self.paste, state=tk.NORMAL if has_clipboard else tk.DISABLED)
        menu.add_separator()
        menu.add_command(label='Set Breakpoint', command=self._toggle_breakpoint)
        menu.add_command(label='Clear Breakpoint', command=self._clear_breakpoint)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ---- Format menu (IDLE-style) ------------------------------------------

    def format_paragraph(self):
        editor = self.editor
        if editor is None:
            return
        import textwrap
        text = editor.get_text_widget()
        try:
            sel_first = text.index('sel.first')
            sel_last = text.index('sel.last')
            has_sel = True
        except tk.TclError:
            sel_first = text.index('insert linestart')
            while True:
                prev = text.index(f'{sel_first} -1 line')
                if text.compare(prev, '==', sel_first):
                    break
                line = text.get(f'{prev} linestart', f'{prev} lineend').strip()
                if not line:
                    break
                sel_first = text.index(f'{prev} linestart')
            sel_last = text.index('insert')
            while True:
                next_line = text.index(f'{sel_last} +1 line')
                if text.compare(next_line, '==', sel_last):
                    break
                line = text.get(f'{next_line} linestart', f'{next_line} lineend').strip()
                if not line:
                    break
                sel_last = text.index(f'{next_line} lineend')
            has_sel = False
        paragraph = text.get(sel_first, sel_last)
        lines = paragraph.splitlines()
        result = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                result.append(textwrap.fill(stripped, width=72))
            else:
                result.append('')
        new_text = '\n'.join(result)
        if new_text != paragraph:
            text.delete(sel_first, sel_last)
            text.insert(sel_first, new_text)
            if has_sel:
                text.tag_add('sel', sel_first, f'{sel_first}+{len(new_text)}c')

    def tabify_region(self):
        editor = self.editor
        if editor is None:
            return
        tabwidth = editor._auto_indent.tabwidth
        first, last = editor._get_selected_lines()
        editor._undo.undo_block_start()
        try:
            for line in range(first, last + 1):
                line_text = editor.get(f'{line}.0', f'{line}.0 lineend')
                indent = len(line_text) - len(line_text.lstrip(' '))
                if indent > 0:
                    tabs = '\t' * (indent // tabwidth)
                    spaces = ' ' * (indent % tabwidth)
                    editor.delete(f'{line}.0', f'{line}.0+{indent}c')
                    editor.insert(f'{line}.0', tabs + spaces)
        finally:
            editor._undo.undo_block_stop()

    def untabify_region(self):
        editor = self.editor
        if editor is None:
            return
        tabwidth = editor._auto_indent.tabwidth
        first, last = editor._get_selected_lines()
        editor._undo.undo_block_start()
        try:
            for line in range(first, last + 1):
                line_text = editor.get(f'{line}.0', f'{line}.0 lineend')
                stripped = line_text.lstrip('\t')
                tab_count = len(line_text) - len(stripped)
                if tab_count > 0:
                    editor.delete(f'{line}.0', f'{line}.0+{tab_count}c')
                    editor.insert(f'{line}.0', ' ' * (tab_count * tabwidth))
        finally:
            editor._undo.undo_block_stop()

    def toggle_tabs(self):
        editor = self.editor
        if editor is None:
            return
        current = editor._auto_indent.usetabs
        editor._auto_indent.usetabs = not current
        mode = 'Tabs' if editor._auto_indent.usetabs else 'Spaces'

    def change_indentwidth(self):
        editor = self.editor
        if editor is None:
            return
        current = editor._auto_indent.indentwidth
        new_width = simpledialog.askinteger(
            'New Indent Width',
            f'Current indent width: {current}\nEnter new width (2-16):',
            parent=self.root, minvalue=2, maxvalue=16, initialvalue=current)
        if new_width:
            editor._auto_indent.indentwidth = new_width

    def rstrip_region(self):
        editor = self.editor
        if editor is None:
            return
        try:
            first = int(float(editor.index('sel.first')))
            last = int(float(editor.index('sel.last')))
        except (tk.TclError, ValueError):
            first, last = 1, int(float(editor.index('end-1c')))
        editor._undo.undo_block_start()
        try:
            for line in range(first, last + 1):
                line_text = editor.get(f'{line}.0', f'{line}.0 lineend')
                stripped = line_text.rstrip()
                if stripped != line_text:
                    editor.delete(f'{line}.0', f'{line}.0 lineend')
                    editor.insert(f'{line}.0', stripped)
        finally:
            editor._undo.undo_block_stop()

    # ---- Search ------------------------------------------------------------

    def find_again(self):
        if not self._last_search_query:
            return
        editor = self.editor
        if editor is None:
            return
        query = self._last_search_query
        case = self._last_search_case
        nocase = not case
        try:
            match = editor.search(f"\\m{re.escape(query)}\\M", "insert", forwards=True, regexp=1, nocase=nocase)
            if match:
                editor.mark_set("insert", match)
                editor.see(match)
            else:
                match = editor.search(f"\\m{re.escape(query)}\\M", "1.0", forwards=True, regexp=1, nocase=nocase)
                if match:
                    editor.mark_set("insert", match)
                    editor.see(match)
        except re.error:
            pass

    def find_selection(self):
        editor = self.editor
        if editor is None:
            return
        try:
            sel = editor.get(tk.SEL_FIRST, tk.SEL_LAST)
            if sel:
                self.find_in_editor(sel)
        except tk.TclError:
            pass

    def _show_completions(self):
        editor = self.editor
        if editor is not None:
            editor._autocomplete.force_open_completions_event(None)

    def _show_calltip(self):
        editor = self.editor
        if editor is not None:
            editor._calltip.try_open_calltip_event(None)

    def show_find_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Find in File")
        dialog.geometry("400x120")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Find:").pack(anchor="w", padx=10, pady=(10, 5))

        search_var = tk.StringVar()
        search_entry = ttk.Entry(dialog, textvariable=search_var)
        search_entry.pack(fill=tk.X, padx=10, pady=5)
        search_entry.focus()

        case_var = tk.BooleanVar()

        options_frame = ttk.Frame(dialog)
        options_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Checkbutton(options_frame, text="Case sensitive", variable=case_var).pack(side=tk.LEFT)

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        def do_find():
            query = search_var.get()
            if not query:
                return
            self.find_in_editor(query, case_var.get())

        ttk.Button(button_frame, text="Find", command=do_find).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

        search_entry.bind("<Return>", lambda _: do_find())

    def find_in_editor(self, query, case_sensitive=False):
        self._last_search_query = query
        self._last_search_case = case_sensitive
        editor = self.editor
        if editor is None:
            return
        content = editor.get("1.0", tk.END)
        flags = 0 if case_sensitive else re.IGNORECASE

        editor.tag_remove("found", "1.0", tk.END)

        try:
            for match in re.finditer(re.escape(query), content, flags):
                start_idx = f"1.0 + {match.start()} chars"
                end_idx = f"1.0 + {match.end()} chars"
                editor.tag_add("found", start_idx, end_idx)
            editor.tag_config("found", background="yellow", foreground="black")

            first_match = editor.search(f"\\m{re.escape(query)}\\M", "1.0", forwards=True,
                                        regexp=1, nocase=not case_sensitive)
            if first_match:
                editor.mark_set("insert", first_match)
                editor.see(first_match)
        except re.error:
            pass

    def show_search_dialog(self):
        if not self.current_folder:
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Search in Files")
        dialog.geometry("500x350")
        dialog.transient(self.root)
        dialog.grab_set()

        input_frame = ttk.Frame(dialog)
        input_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Label(input_frame, text="Search:").pack(side=tk.LEFT)

        search_var = tk.StringVar()
        search_entry = ttk.Entry(input_frame, textvariable=search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        search_entry.focus()

        case_var = tk.BooleanVar()
        ttk.Checkbutton(input_frame, text="Case sensitive", variable=case_var).pack(side=tk.LEFT)

        ttk.Label(dialog, text="Results:").pack(anchor="w", padx=10, pady=(10, 5))

        result_frame = ttk.Frame(dialog)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        result_scroll = ttk.Scrollbar(result_frame)
        result_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        result_list = tk.Listbox(result_frame, yscrollcommand=result_scroll.set)
        result_list.pack(fill=tk.BOTH, expand=True)
        result_scroll.config(command=result_list.yview)

        def do_search():
            query = search_var.get()
            if not query or not self.current_folder:
                return
            result_list.delete(0, tk.END)
            results = self.search_in_files(self.current_folder, query, case_var.get())
            for file_path, matches in results:
                rel_path = os.path.relpath(file_path, self.current_folder)
                result_list.insert(tk.END, f"{rel_path} ({matches} matches)")

        def open_selected(_):
            selection = result_list.curselection()
            if selection:
                item = result_list.get(selection[0])
                file_name = item.split(' (')[0]
                file_path = os.path.join(self.current_folder, file_name)
                query = search_var.get()
                if os.path.isfile(file_path):
                    self._open_file_in_tab(file_path)
                    if query:
                        self.find_in_editor(query, case_var.get())

        result_list.bind("<Double-Button-1>", open_selected)

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        ttk.Button(button_frame, text="Search", command=do_search).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

        search_entry.bind("<Return>", lambda _: do_search())

    def search_in_files(self, folder, query, case_sensitive=False):
        results = []
        flags = 0 if case_sensitive else re.IGNORECASE

        def search_dir(path):
            try:
                for item in os.listdir(path):
                    full_path = os.path.join(path, item)
                    if os.path.isdir(full_path):
                        if not item.startswith('.'):
                            search_dir(full_path)
                    elif os.path.isfile(full_path):
                        try:
                            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                                matches = len(list(re.finditer(re.escape(query), content, flags)))
                                if matches > 0:
                                    results.append((full_path, matches))
                        except Exception:
                            pass
            except PermissionError:
                pass

        search_dir(folder)
        return sorted(results, key=lambda x: x[0])
