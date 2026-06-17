"""MarkdownEditor — main application window with file tree, git, search, etc."""

import os
import re
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
        edit_menu.add_command(label="Cut", command=self.cut, accelerator="Cmd+X")
        edit_menu.add_command(label="Copy", command=self.copy, accelerator="Cmd+C")
        edit_menu.add_command(label="Paste", command=self.paste, accelerator="Cmd+V")
        edit_menu.add_command(label="Select All", command=self.select_all, accelerator="Cmd+A")
        edit_menu.add_separator()
        edit_menu.add_command(label="Bold", command=lambda: self.insert_format("**", "**"), accelerator="Cmd+B")
        edit_menu.add_command(label="Italic", command=lambda: self.insert_format("*", "*"), accelerator="Cmd+I")
        edit_menu.add_command(label="Heading", command=self.insert_heading, accelerator="Cmd+H")
        edit_menu.add_command(label="Link", command=self.insert_link, accelerator="Cmd+K")
        edit_menu.add_command(label="Code", command=lambda: self.insert_format("`", "`"), accelerator="Cmd+Shift+C")
        edit_menu.add_separator()
        edit_menu.add_command(label="Flash Parens", command=self.flash_paren, accelerator="Cmd+Shift+P")
        edit_menu.add_command(label="Keyword Hint", command=self.show_keyword_hint, accelerator="Cmd+Shift+K")

        run_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Run", menu=run_menu)
        run_menu.add_command(label="Run Module", command=self.run_current_file, accelerator="Cmd+R")
        run_menu.add_command(label="Run in Terminal", command=self.run_in_terminal, accelerator="Cmd+Shift+R")

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Toggle Terminal", command=self._toggle_terminal, accelerator="Cmd+J")

        search_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Search", menu=search_menu)
        search_menu.add_command(label="Find in File", command=self.show_find_dialog, accelerator="Cmd+F")
        search_menu.add_command(label="Search in Files", command=self.show_search_dialog, accelerator="Cmd+Shift+F")

        # -- Editor context menu (right-click) --
        self.editor_context_menu = tk.Menu(self.root, tearoff=0)
        self.editor_context_menu.add_command(label="Cut", command=self.cut)
        self.editor_context_menu.add_command(label="Copy", command=self.copy)
        self.editor_context_menu.add_command(label="Paste", command=self.paste)
        self.editor_context_menu.add_separator()
        self.editor_context_menu.add_command(label="Select All", command=self.select_all)
        self.editor_context_menu.add_separator()
        self.editor_context_menu.add_command(label="Toggle Breakpoint",
                                             command=self._toggle_breakpoint)

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
            'set_status': lambda msg: self.status_bar.config(text=msg),
            'file_deleted': self._on_file_deleted,
            'file_path_changed': self._on_file_path_changed,
            'new_file_requested': self.new_file,
        })

        # -- Git panel (inside auxiliary sidebar) --
        self.git_panel = GitPanel(auxiliary_sidebar_frame, callbacks={
            'get_current_folder': lambda: self.current_folder,
            'set_status': lambda msg: self.status_bar.config(text=msg),
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

        # -- Bottom panels (hidden by default) --
        cwd = os.path.expanduser('~')
        self._shell_panel = PythonShell(editor_paned)
        self._shell_panel.set_close_callback(self._toggle_shell)
        self._shell_panel._open_source_callback = self._on_debug_source

        self._pkg_panel = PackageManager(editor_paned)
        self._pkg_panel.set_close_callback(self._toggle_packages)

        self._sys_term = SystemTerminal(editor_paned, cwd=cwd)
        self._sys_term.set_close_callback(self._toggle_terminal)

        # Debugger panel (hidden by default)
        from .debugger import Debugger
        self._debug_panel = Debugger(editor_paned, self._shell_panel)
        self._debug_panel.set_close_callback(self._toggle_debugger)

        # -- Status bar --
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=8)

        # Center: status text
        self.status_bar = ttk.Label(
            status_frame, text="Ready", relief=tk.SUNKEN, anchor='w')
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        # Cursor position
        self._cursor_pos = ttk.Label(
            status_frame, text='Ln 1, Col 1', relief=tk.SUNKEN,
            padding=(6, 1))
        self._cursor_pos.pack(side=tk.LEFT)
        # Right (packed before terminal so it appears to its left):
        # Python environment indicator
        self._venv_status = ttk.Label(
            status_frame, text='Python', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._venv_status.pack(side=tk.LEFT)
        self._venv_status.bind('<Button-1>', lambda e: self._show_venv_popup())

        # Right: source control panel toggle
        self._git_panel_status = ttk.Label(
            status_frame, text='Source Control', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._git_panel_status.pack(side=tk.RIGHT)
        self._git_panel_status.bind('<Button-1>', lambda e: self._toggle_auxiliary_sidebar())

        # Right: terminal toggle
        self._terminal_status = ttk.Label(
            status_frame, text='⬆ Terminal', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._terminal_status.pack(side=tk.RIGHT)
        self._terminal_status.bind(
            '<Button-1>', lambda e: self._toggle_terminal())
        # Right: debug toggle
        self._debug_status = ttk.Label(
            status_frame, text='🐞 Debug', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._debug_status.pack(side=tk.RIGHT)
        self._debug_status.bind('<Button-1>', lambda e: self._toggle_debugger())
        # Right: python shell entry
        self._shell_status = ttk.Label(
            status_frame, text='▶ Shell', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._shell_status.pack(side=tk.RIGHT)
        self._shell_status.bind('<Button-1>', lambda e: self._toggle_shell())

        # Right: packages entry
        self._pkg_status = ttk.Label(
            status_frame, text='📦 Packages', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._pkg_status.pack(side=tk.RIGHT)
        self._pkg_status.bind('<Button-1>', lambda e: self._toggle_packages())
        # Update status bar widget references
        self._status_frame = status_frame
        self.git_panel.refresh()

    # ---- Shortcut binding for new editors ----------------------------------

    def _bind_editor_shortcuts(self, tab_id, editor):
        """Bind all keyboard shortcuts + events to a newly created editor."""
        editor.bind("<Command-n>", lambda _: self.new_file())
        editor.bind("<Command-o>", lambda _: self.open_file())
        editor.bind("<Command-s>", lambda _: self.save_file())
        editor.bind("<Command-Shift-S>", lambda _: self.save_file_as())
        editor.bind("<Command-b>", lambda _: self.insert_format("**", "**"))
        editor.bind("<Command-i>", lambda _: self.insert_format("*", "*"))
        editor.bind("<Command-h>", lambda _: self.insert_heading())
        editor.bind("<Command-k>", lambda _: self.insert_link())
        editor.bind("<Command-j>", lambda _: self._toggle_terminal())
        editor.bind("<Command-Shift-c>", lambda _: self.insert_format("`", "`"))
        editor.bind("<Command-f>", lambda _: self.show_find_dialog())
        editor.bind("<Command-Shift-f>", lambda _: self.show_search_dialog())
        editor.bind("<Command-a>", lambda _: self.select_all())
        editor.bind("<Command-z>", lambda _: self.undo())
        editor.bind("<Command-Shift-Z>", lambda _: self.redo())
        editor.bind("<Command-Shift-P>", lambda _: self.flash_paren())
        editor.bind("<Command-Shift-K>", lambda _: self.show_keyword_hint())
        editor.bind("<Command-r>", lambda _: self.run_current_file())
        editor.bind("<Command-Shift-R>", lambda _: self.run_in_terminal())
        editor.bind("<Button-2>", self.show_editor_context_menu)
        editor.bind("<Button-3>", self.show_editor_context_menu)
        editor.bind("<Control-Button-1>", self.show_editor_context_menu)

        # Cursor position tracking
        editor.bind('<KeyRelease>', lambda e: self._update_cursor_pos())
        editor.bind('<ButtonRelease-1>', lambda e: self._update_cursor_pos())

    # ---- Bottom panels (Shell / Packages / Terminal) -----------------------

    def _show_bottom_panel(self, panel):
        """Toggle a bottom panel. Hide any other visible panel first.

        Returns True if the panel is now visible, False if it was hidden.
        """
        if panel.winfo_ismapped():
            self.editor_paned.forget(panel)
            return False
        # Hide any other visible bottom panel
        for p in [self._shell_panel, self._pkg_panel, self._sys_term,
                   self._debug_panel]:
            if p is not panel and p.winfo_ismapped():
                self.editor_paned.forget(p)
        self.editor_paned.add(panel, weight=1)
        panel.focus_input()
        return True

    def _toggle_shell(self):
        """Show or hide the Python Shell panel."""
        visible = self._show_bottom_panel(self._shell_panel)
        self._shell_status.configure(text='⏹ Shell' if visible else '▶ Shell')
        self.status_bar.configure(
            text='Python Shell shown' if visible else 'Python Shell hidden')

    def _toggle_packages(self):
        """Show or hide the Packages panel."""
        visible = self._show_bottom_panel(self._pkg_panel)
        self._pkg_status.configure(
            text='⏹ Packages' if visible else '📦 Packages')
        self.status_bar.configure(
            text='Packages shown' if visible else 'Packages hidden')

    def _toggle_terminal(self):
        """Show or hide the system Terminal panel."""
        visible = self._show_bottom_panel(self._sys_term)
        self._terminal_status.configure(
            text='⬇ Terminal' if visible else '⬆ Terminal')
        self.status_bar.configure(
            text='Terminal shown' if visible else 'Terminal hidden')

    def _show_shell(self):
        """Show the Python Shell panel (called from status bar)."""
        if not self._shell_panel.winfo_ismapped():
            self._toggle_shell()

    def _show_packages(self):
        """Show the Packages panel (called from status bar)."""
        if not self._pkg_panel.winfo_ismapped():
            self._toggle_packages()

    # ---- Debugger ----------------------------------------------------------

    def _toggle_debugger(self):
        """Toggle the debugger panel and debug mode on/off."""
        visible = self._show_bottom_panel(self._debug_panel)
        if visible:
            self._shell_panel.open_debugger(self._debug_panel)
            self._debug_status.configure(text='⏹ Debug')
            self.status_bar.configure(text='Debugger ON — run code in Shell to debug')
            # Load breakpoints from all open editors
            self._load_breakpoints_to_debugger()
        else:
            self._shell_panel.close_debugger()
            self._debug_status.configure(text='🐞 Debug')
            self.status_bar.configure(text='Debugger OFF')

    def _toggle_breakpoint(self):
        """Toggle a breakpoint on the current cursor line in the active editor."""
        editor = self.editor
        if editor is None:
            return
        lineno = int(float(editor.index('insert')))
        added = editor.toggle_breakpoint(lineno)
        if added:
            self.status_bar.configure(text=f'Breakpoint set at line {lineno}')
        else:
            self.status_bar.configure(text=f'Breakpoint cleared at line {lineno}')
        # Sync breakpoints to debugger if active
        self._sync_breakpoint_to_debugger(editor, lineno, added)

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
            self.status_bar.config(text="Open a folder first to manage Python environments")
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
            self.status_bar.config(
                text=f"Python env: {self.venv_manager.get_display_name()}")
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
            self.status_bar.config(
                text=f"Created and activated: {name}")
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
        self.status_bar.config(text="Python: system")

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
        tab = self.tabbed_editor.get_active_tab()
        if tab and tab['file_path']:
            base = os.path.basename(tab['file_path'])
            self.status_bar.config(text=f"Editing: {base}")
        else:
            self.status_bar.config(text="New file")
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
                        self.status_bar.config(text=f"Error saving: {e}")
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
            self.status_bar.config(text="Source Control hidden")
        else:
            self._show_auxiliary_sidebar()
            self.status_bar.config(text="Source Control shown")

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
            self.status_bar.config(text=f"Error opening file: {e}")
            return

        self.tabbed_editor.add_tab(
            title=os.path.basename(file_path),
            file_path=file_path,
            content=content,
        )
        self.status_bar.config(text=f"Opened: {os.path.basename(file_path)}")

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
        self.status_bar.config(text=f"Opened: {os.path.basename(folder)}")
        self._update_venv_status()
        if self.venv_manager.is_active:
            self._apply_venv_to_subsystems()

    def _reload_file_in_tab(self, file_path):
        """Reload file content in an open tab (e.g. after git rollback)."""
        tab_idx = self.tabbed_editor.find_tab_by_path(file_path)
        if tab_idx >= 0:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    new_content = f.read()
                tab = self.tabbed_editor._tabs[tab_idx]
                tab['editor'].set_text(new_content)
                tab['editor'].set_saved()
            except Exception as e:
                self.status_bar.config(text=f"Error reloading file: {e}")

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
            self.status_bar.config(text=f"Saved: {os.path.basename(tab['file_path'])}")
            return True
        except Exception as e:
            self.status_bar.config(text=f"Error saving file: {e}")
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
        self.status_bar.config(text="New file")

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
            self.status_bar.config(text=f"Opened folder: {os.path.basename(folder_path)}")
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
        self.status_bar.config(text="Folder closed")

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
            self.status_bar.config(text="No file to run")
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

        # Show the Python shell
        if not self._shell_panel.winfo_ismapped():
            self._toggle_shell()
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass

        self._shell_panel.run_code(source, file_path=file_path)
        try:
            self.status_bar.config(text=f"Ran: {os.path.basename(file_path)}")
        except tk.TclError:
            pass  # widget may have been destroyed by user code

    def run_in_terminal(self):
        """Run the current file in the system terminal."""
        tab = self.tabbed_editor.get_active_tab()
        if not tab:
            self.status_bar.config(text="No file to run")
            return

        # Save if there's a file path
        if tab['file_path']:
            if tab['editor'].is_modified():
                self._save_to_tab(tab)

            file_path = tab['file_path']
            dir_name = os.path.dirname(file_path)
            file_name = os.path.basename(file_path)

            # Show the terminal
            if not self._sys_term.winfo_ismapped():
                self._toggle_terminal()
            try:
                self.root.update_idletasks()
            except tk.TclError:
                pass

            # cd to directory and run
            self._sys_term.cd_to(dir_name)
            self._sys_term.send_command(f'python3 "{file_name}"')
            try:
                self.status_bar.config(text=f"Running in Terminal: {file_name}")
            except tk.TclError:
                pass
        else:
            # Unsaved file — save first
            if not self.save_file_as():
                return
            self.run_in_terminal()

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

    def show_editor_context_menu(self, event):
        try:
            self.editor_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.editor_context_menu.grab_release()

    # ---- Search ------------------------------------------------------------

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
                self.status_bar.config(text=f"Found matches")
            else:
                self.status_bar.config(text="No matches found")
        except re.error:
            self.status_bar.config(text="Invalid search pattern")

    def show_search_dialog(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
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
