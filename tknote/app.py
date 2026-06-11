"""MarkdownEditor — main application window with file tree, git, search, etc."""

import os
import re
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .editor.tabbed import TabbedEditor
from .editor.widget import EditorWidget
from .terminal.panel import TerminalPanel
from .git_ops import run_git_command


class MarkdownEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("Markdown Editor")
        self.root.geometry("1200x700")

        self.current_folder = None
        self.setup_ui()
        self.root.protocol('WM_DELETE_WINDOW', self._on_window_close)

    def _run_git(self, args):
        """Run a git command in the currently opened folder."""
        return run_git_command(args, cwd=self.current_folder)

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

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Toggle Terminal", command=self.toggle_terminal, accelerator="Cmd+J")

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

        # -- Main layout --
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ---- Activity Bar (far left) ----
        self.activity_bar = tk.Canvas(
            main_frame, width=48, bg='#ececec', highlightthickness=0
        )
        self.activity_bar.pack(side=tk.LEFT, fill=tk.Y)

        self._activity_items = {}
        # Files button
        fid = self.activity_bar.create_text(
            24, 16, text='📁', font=('Helvetica', 18),
            anchor='n', fill='#ffffff'
        )
        self._activity_items['files'] = fid
        self.activity_bar.tag_bind(fid, '<Button-1>', lambda e: self._switch_activity('files'))

        # Git button
        gid = self.activity_bar.create_text(
            24, 56, text='🔀', font=('Helvetica', 18),
            anchor='n', fill='#888888'
        )
        self._activity_items['git'] = gid
        self.activity_bar.tag_bind(gid, '<Button-1>', lambda e: self._switch_activity('git'))

        self._active_activity = 'files'

        # ---- PanedWindow: sidebar + editor ----
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ---- Sidebar ----
        sidebar_frame = ttk.Frame(paned)
        editor_paned = ttk.PanedWindow(paned, orient=tk.VERTICAL)

        paned.add(sidebar_frame, weight=0)
        paned.add(editor_paned, weight=1)
        self.editor_paned = editor_paned

        # -- File tree panel (inside sidebar) --
        self.file_tree_frame = ttk.Frame(sidebar_frame)

        tree_header = ttk.Frame(self.file_tree_frame)
        tree_header.pack(fill=tk.X)
        ttk.Label(tree_header, text="Files", font=("Helvetica", 10, "bold")).pack(side=tk.LEFT, padx=(0, 5))

        btn_frame = ttk.Frame(tree_header)
        btn_frame.pack(side=tk.RIGHT)

        new_btn = ttk.Button(btn_frame, text="+", width=1, command=self.show_new_menu)
        new_btn.pack(side=tk.LEFT, padx=1)

        refresh_btn = ttk.Button(btn_frame, text="⟳", width=1, command=self.refresh_file_tree)
        refresh_btn.pack(side=tk.LEFT, padx=1)

        tree_scroll = ttk.Scrollbar(self.file_tree_frame)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.file_tree = ttk.Treeview(self.file_tree_frame, yscrollcommand=tree_scroll.set, selectmode="browse")
        self.file_tree.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self.file_tree.yview)
        self.file_tree.bind("<Double-1>", self.on_tree_double_click)
        self.file_tree.bind("<Button-2>", self.show_context_menu)
        self.file_tree.bind("<Button-3>", self.show_context_menu)
        self.file_tree.bind("<Control-Button-1>", self.show_context_menu)

        # File tree context menu
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="New File", command=self.new_file_in_tree)
        self.context_menu.add_command(label="New Folder", command=self.new_folder_in_tree)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Rename", command=self.rename_selected)
        self.context_menu.add_command(label="Move", command=self.move_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Delete", command=self.delete_selected)

        # -- Git panel (inside sidebar) --
        self.git_panel_frame = ttk.Frame(sidebar_frame)

        git_header = ttk.Frame(self.git_panel_frame)
        git_header.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(git_header, text="Source Control", font=("Helvetica", 10, "bold")).pack(side=tk.LEFT)

        # ---- Top buttons: repository setup (conditional) + commit area ----
        git_btn_frame = ttk.Frame(self.git_panel_frame)
        git_btn_frame.pack(fill=tk.X, padx=5, pady=5)
        # Store button references for show/hide control
        self._git_btns = {}

        # Row 0: repository setup
        self._git_btns['clone'] = ttk.Button(git_btn_frame, text="Clone", command=self.git_clone)
        self._git_btns['clone'].grid(row=0, column=0, columnspan=2, padx=1, pady=1, sticky='ew')
        self._git_btns['init'] = ttk.Button(git_btn_frame, text="Init", command=self.git_init)
        self._git_btns['init'].grid(row=0, column=1, columnspan=2, padx=1, pady=1, sticky='ew')

        # Row 1: remote setup
        self._git_btns['remote'] = ttk.Button(git_btn_frame, text="Remote", command=self.git_set_remote)
        self._git_btns['remote'].grid(row=1, column=0, columnspan=2, padx=1, pady=1, sticky='ew')

        # Row 2: commit message entry
        self._commit_msg_var = tk.StringVar()
        self._commit_msg_entry = ttk.Entry(git_btn_frame, textvariable=self._commit_msg_var)
        self._commit_msg_entry.grid(row=2, column=0, columnspan=2, padx=1, pady=(4, 1), sticky='ew')
        self._commit_msg_entry.insert(0, "Enter commit message...")
        self._commit_msg_entry.bind('<FocusIn>', self._on_commit_msg_focus_in)
        self._commit_msg_entry.bind('<FocusOut>', self._on_commit_msg_focus_out)

        # Row 3: commit + push buttons
        self._git_btns['commit'] = ttk.Button(git_btn_frame, text="Commit", command=self.git_commit)
        self._git_btns['commit'].grid(row=3, column=0, padx=1, pady=1, sticky='ew')
        self._git_btns['push_panel'] = ttk.Button(git_btn_frame, text="Push", command=self.git_push)
        self._git_btns['push_panel'].grid(row=3, column=1, padx=1, pady=1, sticky='ew')

        git_btn_frame.columnconfigure(0, weight=1)
        git_btn_frame.columnconfigure(1, weight=1)
        self._update_git_buttons()

        # ---- Changed files list ----
        ttk.Label(self.git_panel_frame, text="Changed Files",
                  font=("Helvetica", 10, "bold")).pack(anchor="w", padx=5, pady=(5, 0))

        git_status_frame = ttk.Frame(self.git_panel_frame)
        git_status_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        git_status_scroll = ttk.Scrollbar(git_status_frame)
        git_status_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.git_status_text = tk.Text(
            git_status_frame, height=8, font=("Monaco", 10),
            yscrollcommand=git_status_scroll.set, state=tk.DISABLED,
            cursor='hand2'
        )
        self.git_status_text.pack(fill=tk.BOTH, expand=True)
        self.git_status_text.bind('<Double-Button-1>', self._on_git_status_double_click)
        self.git_status_text.bind('<Button-2>', self._on_git_status_right_click)
        self.git_status_text.bind('<Button-3>', self._on_git_status_right_click)
        self.git_status_text.bind('<Control-Button-1>', self._on_git_status_right_click)
        git_status_scroll.config(command=self.git_status_text.yview)

        # Git status context menu (right-click on changed files)
        self._git_context_menu = tk.Menu(self.root, tearoff=0)
        self._git_context_menu.add_command(label="Rollback", command=self._git_rollback)
        self._git_context_menu.add_command(label="Open File", command=self._git_open_selected)
        self._git_context_menu.add_command(label="Show Diff", command=self._git_show_diff)
        self._git_context_menu.add_separator()
        self._git_context_menu.add_command(label="Refresh", command=self._refresh_git_panel)
        self._git_right_clicked_file = None

        # ---- Commit log ----
        ttk.Label(self.git_panel_frame, text="Commit Log",
                  font=("Helvetica", 10, "bold")).pack(anchor="w", padx=5, pady=(5, 0))

        log_frame = ttk.Frame(self.git_panel_frame)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        log_scroll = ttk.Scrollbar(log_frame)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._git_log_text = tk.Text(
            log_frame, height=6, font=("Monaco", 10),
            yscrollcommand=log_scroll.set, state=tk.DISABLED
        )
        self._git_log_text.pack(fill=tk.BOTH, expand=True)
        log_scroll.config(command=self._git_log_text.yview)

        # Show file tree by default
        self.file_tree_frame.pack(fill=tk.BOTH, expand=True)

        # -- Tabbed editor --
        self.tabbed_editor = TabbedEditor(
            editor_paned,
            on_tab_created=self._bind_editor_shortcuts,
            on_tab_switch=self._on_tab_switch,
            on_close_request=self._on_close_request,
            on_new_tab_request=self.new_file,
        )
        editor_paned.add(self.tabbed_editor, weight=3)

        # -- Terminal panel (hidden by default) --
        self.terminal_panel = TerminalPanel(editor_paned, cwd=os.path.expanduser('~'))
        self.terminal_panel.set_close_callback(self.toggle_terminal)
        self._terminal_visible = False

        # -- Status bar --
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=8)

        # Left: git info frame (branch name + push + pull; always visible, disabled when no repo)
        self._status_git_frame = ttk.Frame(status_frame)

        self._status_branch_label = ttk.Label(
            self._status_git_frame, text="⎇ no repo", font=("Helvetica", 10))
        self._status_branch_label.pack(side=tk.LEFT, padx=(0, 8))
        self._status_branch_label.bind('<Button-1>', self._show_branch_popup)

        self._status_push_btn = ttk.Button(
            self._status_git_frame, text="↑", command=self.git_push, width=1,
            state=tk.DISABLED)
        self._status_push_btn.pack(side=tk.LEFT, padx=1)

        self._status_pull_btn = ttk.Button(
            self._status_git_frame, text="↓", command=self.git_pull, width=1,
            state=tk.DISABLED)
        self._status_pull_btn.pack(side=tk.LEFT, padx=(1, 8))

        self._status_git_frame.pack(side=tk.LEFT)

        # Center: status text
        self.status_bar = ttk.Label(
            status_frame, text="Ready", relief=tk.SUNKEN, anchor='w')
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Right: terminal toggle
        self._terminal_status = ttk.Label(
            status_frame, text='⬆ Terminal', relief=tk.SUNKEN,
            cursor='hand2', padding=(6, 1))
        self._terminal_status.pack(side=tk.RIGHT)
        self._terminal_status.bind(
            '<Button-1>', lambda e: self.toggle_terminal())
        # Update status bar widget references
        self._status_frame = status_frame

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
        editor.bind("<Command-j>", lambda _: self.toggle_terminal())
        editor.bind("<Command-Shift-c>", lambda _: self.insert_format("`", "`"))
        editor.bind("<Command-f>", lambda _: self.show_find_dialog())
        editor.bind("<Command-Shift-f>", lambda _: self.show_search_dialog())
        editor.bind("<Command-a>", lambda _: self.select_all())
        editor.bind("<Command-z>", lambda _: self.undo())
        editor.bind("<Command-Shift-Z>", lambda _: self.redo())
        editor.bind("<Command-Shift-P>", lambda _: self.flash_paren())
        editor.bind("<Command-Shift-K>", lambda _: self.show_keyword_hint())
        editor.bind("<Button-2>", self.show_editor_context_menu)
        editor.bind("<Button-3>", self.show_editor_context_menu)
        editor.bind("<Control-Button-1>", self.show_editor_context_menu)

    # ---- Terminal panel ----------------------------------------------------

    def toggle_terminal(self):
        """Show or hide the terminal panel."""
        if self._terminal_visible:
            self.editor_paned.forget(self.terminal_panel)
            self._terminal_visible = False
            self._terminal_status.configure(text='⬆ Terminal')
            self.status_bar.configure(text='Terminal hidden')
        else:
            self.editor_paned.add(self.terminal_panel, weight=1)
            self._terminal_visible = True
            self._terminal_status.configure(text='⬇ Terminal')
            self.status_bar.configure(text='Terminal shown')
            self.terminal_panel.focus_input()

    def _on_window_close(self):
        """Clean up subprocesses before closing."""
        self.terminal_panel.cleanup()
        self.root.destroy()

    # ---- Tab event callbacks -----------------------------------------------

    def _on_tab_switch(self, tab_id):
        """Called when the active tab changes."""
        tab = self.tabbed_editor.get_active_tab()
        if tab and tab['file_path']:
            base = os.path.basename(tab['file_path'])
            self.status_bar.config(text=f"Editing: {base}")
        else:
            self.status_bar.config(text="New file")

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

    # ---- Activity bar / Sidebar switching ------------------------------------

    def _switch_activity(self, activity):
        """Switch the sidebar between 'files' and 'git' views."""
        self._active_activity = activity
        # Update activity bar button colors
        for name, item_id in self._activity_items.items():
            self.activity_bar.itemconfig(
                item_id, fill='#ffffff' if name == activity else '#888888'
            )
        # Swap sidebar content
        if activity == 'files':
            self.git_panel_frame.pack_forget()
            self.file_tree_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self.file_tree_frame.pack_forget()
            self.git_panel_frame.pack(fill=tk.BOTH, expand=True)
            self._refresh_git_panel()

    def _refresh_git_panel(self):
        """Update the git panel with current branch, status, and log."""
        if not self.current_folder:
            self._status_branch_label.config(text="⎇ no repo", cursor='')
            self._status_push_btn.config(state=tk.DISABLED)
            self._status_pull_btn.config(state=tk.DISABLED)
            self.git_status_text.config(state=tk.NORMAL)
            self.git_status_text.delete(1.0, tk.END)
            self.git_status_text.insert(tk.END, "Open a folder to see git status.")
            self.git_status_text.config(state=tk.DISABLED)
            self._git_log_text.config(state=tk.NORMAL)
            self._git_log_text.delete(1.0, tk.END)
            self._git_log_text.config(state=tk.DISABLED)
            self._update_git_buttons()
            return

        has_git = os.path.isdir(os.path.join(self.current_folder, '.git'))
        if not has_git:
            self._status_branch_label.config(text="⎇ no repo", cursor='')
            self._status_push_btn.config(state=tk.DISABLED)
            self._status_pull_btn.config(state=tk.DISABLED)
        else:
            _, branch, _ = self._run_git(['branch', '--show-current'])
            branch = branch.strip() if branch else 'unknown'
            self._status_branch_label.config(text=f"⎇ {branch}", cursor='hand2')
            self._status_push_btn.config(state=tk.NORMAL)
            self._status_pull_btn.config(state=tk.NORMAL)

        # Changed files
        _, status, _ = self._run_git(['status', '--short'])
        self.git_status_text.config(state=tk.NORMAL)
        self.git_status_text.delete(1.0, tk.END)
        if status.strip():
            self.git_status_text.insert(tk.END, status)
        else:
            self.git_status_text.insert(tk.END, "Working tree clean")
        self.git_status_text.config(state=tk.DISABLED)

        # Commit log
        self._refresh_commit_log()
        self._update_git_buttons()

    def _parse_git_status_line(self, event):
        """Extract (rel_path, file_path) from a click on the git status text."""
        if not self.current_folder:
            return None, None
        idx = self.git_status_text.index(f'@{event.x},{event.y}')
        line_text = self.git_status_text.get(f'{idx} linestart', f'{idx} lineend')
        if not line_text.strip() or len(line_text) <= 3 or '->' in line_text:
            return None, None
        rel_path = line_text[3:].strip()
        return rel_path, os.path.join(self.current_folder, rel_path)

    def _on_git_status_double_click(self, event):
        """Show git diff for a file double-clicked in the git status panel."""
        rel_path, file_path = self._parse_git_status_line(event)
        if rel_path is None:
            return
        self._show_diff_for(rel_path)

    def _on_git_status_right_click(self, event):
        """Show context menu on right-click in git status panel."""
        rel_path, file_path = self._parse_git_status_line(event)
        self._git_right_clicked_file = (rel_path, file_path)
        try:
            self._git_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._git_context_menu.grab_release()

    def _git_rollback(self):
        """Discard changes to the selected file (git checkout -- <file>)."""
        info = self._git_right_clicked_file
        if not info or not info[0] or not info[1]:
            return
        rel_path, file_path = info
        name = os.path.basename(rel_path)
        confirm = messagebox.askyesno(
            "Rollback Changes",
            f"Discard all changes to '{name}'?\n\nThis cannot be undone.",
            parent=self.root
        )
        if confirm:
            rc, _, stderr = self._run_git(['checkout', '--', rel_path])
            if rc == 0:
                self.status_bar.config(text=f"Rolled back: {name}")
                self._refresh_git_panel()
                # Update any open tab of this file with reverted content
                tab_idx = self.tabbed_editor.find_tab_by_path(file_path)
                if tab_idx >= 0:
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            new_content = f.read()
                        tab = self.tabbed_editor._tabs[tab_idx]
                        editor = tab['editor']
                        editor.set_text(new_content)
                        editor.set_saved()
                    except Exception as e:
                        self.status_bar.config(text=f"Error reloading file: {e}")
                # Close diff tab for this file
                diff_title = f'Diff: {name}'
                for i, tab in enumerate(self.tabbed_editor._tabs):
                    if tab['title'] == diff_title:
                        self.tabbed_editor.close_tab(i)
                        break
            else:
                self.status_bar.config(text=f"Rollback failed: {stderr.strip()}")

    def _git_open_selected(self):
        """Open the right-clicked file in an editor tab."""
        info = self._git_right_clicked_file
        if info and info[0]:
            rel_path, file_path = info
            if os.path.isfile(file_path):
                self._open_file_in_tab(file_path)

    def _git_show_diff(self):
        """Show git diff for the right-clicked file."""
        info = self._git_right_clicked_file
        if info and info[0]:
            self._show_diff_for(info[0])

    def _show_diff_for(self, rel_path):
        """Create a tab showing git diff for the given relative path."""
        _, diff, _ = self._run_git(['diff', '--', rel_path])
        if not diff.strip():
            _, diff, _ = self._run_git(['diff', '--cached', '--', rel_path])

        title = f'Diff: {os.path.basename(rel_path)}'
        content = diff if diff.strip() else '(no changes)'
        # Check if a diff tab already exists for this file
        for i, tab in enumerate(self.tabbed_editor._tabs):
            if tab['title'] == title:
                self.tabbed_editor.switch_to_tab(i)
                # Update content
                tab['editor'].set_text(content)
                tab['editor'].set_saved()
                return
        self.tabbed_editor.add_tab(title=title, content=content)

    def _update_git_buttons(self):
        """Show/hide git buttons based on current state."""
        folder_open = self.current_folder is not None
        has_git = False
        if folder_open:
            has_git = os.path.isdir(os.path.join(self.current_folder, '.git'))

        # Clone: only visible when no folder is open
        if folder_open:
            self._git_btns['clone'].grid_remove()
        else:
            self._git_btns['clone'].grid()

        # Init: visible only when folder is open AND no .git exists
        if folder_open and not has_git:
            self._git_btns['init'].grid()
        else:
            self._git_btns['init'].grid_remove()

        # Remote: visible only when folder is open
        if folder_open:
            self._git_btns['remote'].grid()
        else:
            self._git_btns['remote'].grid_remove()

        # Commit msg entry, commit button, push button: visible when folder is open
        if folder_open and has_git:
            self._commit_msg_entry.grid()
            self._git_btns['commit'].grid()
            self._git_btns['push_panel'].grid()
        else:
            self._commit_msg_entry.grid_remove()
            self._git_btns['commit'].grid_remove()
            self._git_btns['push_panel'].grid_remove()

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
            self.populate_file_tree(folder_path)
            self.terminal_panel.cd_to(folder_path)
            self.status_bar.config(text=f"Opened folder: {os.path.basename(folder_path)}")
            self._refresh_git_panel()

    def close_folder(self):
        """Close the currently opened folder, clear file tree and git panel."""
        self.current_folder = None
        self.file_tree.delete(*self.file_tree.get_children())
        self._refresh_git_panel()
        self.status_bar.config(text="Folder closed")

    def populate_file_tree(self, folder_path):
        self.file_tree.delete(*self.file_tree.get_children())

        def add_tree_items(path, parent_id):
            try:
                items = sorted(os.listdir(path), key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
                for item in items:
                    full_path = os.path.join(path, item)
                    if os.path.isdir(full_path):
                        if item.startswith('.'):
                            continue
                        node_id = self.file_tree.insert(parent_id, "end", text=f"📁 {item}", values=(full_path,), tags=("dir",), open=False)
                        add_tree_items(full_path, node_id)
                    else:
                        self.file_tree.insert(parent_id, "end", text=f"📄 {item}", values=(full_path,), tags=("file",))
            except PermissionError:
                pass

        add_tree_items(folder_path, "")

    def refresh_file_tree(self):
        if self.current_folder:
            self.populate_file_tree(self.current_folder)
            self.status_bar.config(text="File tree refreshed")
        else:
            self.status_bar.config(text="No folder opened")

    def on_tree_double_click(self, _):
        selection = self.file_tree.selection()
        if selection:
            item = selection[0]
            values = self.file_tree.item(item, "values")
            if values:
                file_path = values[0]
                if os.path.isfile(file_path):
                    self._open_file_in_tab(file_path)

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

    # ---- File tree helpers -------------------------------------------------

    def get_selected_path(self):
        selection = self.file_tree.selection()
        if selection:
            item = selection[0]
            values = self.file_tree.item(item, "values")
            if values:
                return values[0]
        return self.current_folder

    def show_new_menu(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="New File", command=self.new_file_in_tree)
        menu.add_command(label="New Folder", command=self.new_folder_in_tree)
        menu.post(self.file_tree.winfo_rootx() + 50, self.file_tree.winfo_rooty() + 10)

    def show_context_menu(self, event):
        if not self.current_folder:
            return
        item = self.file_tree.identify_row(event.y)
        if item:
            self.file_tree.selection_set(item)
        self.context_menu.post(event.x_root, event.y_root)

    def new_file_in_tree(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        parent_path = self.get_selected_path()
        if parent_path and os.path.isfile(parent_path):
            parent_path = os.path.dirname(parent_path)

        if parent_path:
            filename = simpledialog.askstring(
                "New File", "Enter file name:",
                parent=self.root
            )
            if filename:
                new_file_path = os.path.join(parent_path, filename)
                try:
                    with open(new_file_path, 'w', encoding='utf-8') as f:
                        f.write('')
                    self.populate_file_tree(self.current_folder)
                    self.status_bar.config(text=f"Created: {filename}")
                except Exception as e:
                    self.status_bar.config(text=f"Error creating file: {e}")

    def new_folder_in_tree(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        parent_path = self.get_selected_path()
        if parent_path and os.path.isfile(parent_path):
            parent_path = os.path.dirname(parent_path)

        if parent_path:
            foldername = simpledialog.askstring("New Folder", "Enter folder name:", parent=self.root)
            if foldername:
                new_folder_path = os.path.join(parent_path, foldername)
                try:
                    os.makedirs(new_folder_path, exist_ok=False)
                    self.populate_file_tree(self.current_folder)
                    self.status_bar.config(text=f"Created folder: {foldername}")
                except FileExistsError:
                    self.status_bar.config(text=f"Folder already exists: {foldername}")
                except Exception as e:
                    self.status_bar.config(text=f"Error creating folder: {e}")

    def delete_selected(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        selection = self.file_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.file_tree.item(item, "values")
        if not values:
            return

        path = values[0]
        item_text = self.file_tree.item(item, "text")
        name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        item_type = "folder" if os.path.isdir(path) else "file"
        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete the {item_type} '{name}'?",
            parent=self.root
        )

        if confirm:
            try:
                if os.path.isdir(path):
                    import shutil
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                self.populate_file_tree(self.current_folder)
                self.status_bar.config(text=f"Deleted: {name}")

                # Close the tab if the deleted file is open
                existing = self.tabbed_editor.find_tab_by_path(path)
                if existing >= 0:
                    # Force close without unsaved prompt (file already deleted)
                    self.tabbed_editor._tabs[existing]['editor'].set_saved()
                    self.tabbed_editor.close_tab(existing)
                    if self.tabbed_editor.get_tab_count() == 0:
                        self.tabbed_editor.add_tab(content='')
            except Exception as e:
                self.status_bar.config(text=f"Error deleting: {e}")

    def rename_selected(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        selection = self.file_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.file_tree.item(item, "values")
        if not values:
            return

        path = values[0]
        item_text = self.file_tree.item(item, "text")
        old_name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        new_name = simpledialog.askstring("Rename", "Enter new name:", initialvalue=old_name, parent=self.root)
        if new_name and new_name != old_name:
            parent_dir = os.path.dirname(path)
            new_path = os.path.join(parent_dir, new_name)
            try:
                import shutil
                shutil.move(path, new_path)
                self.populate_file_tree(self.current_folder)

                # Update tab if this file is open
                existing = self.tabbed_editor.find_tab_by_path(path)
                if existing >= 0:
                    self.tabbed_editor.set_tab_path(
                        self.tabbed_editor._tabs[existing]['id'], new_path)

                self.status_bar.config(text=f"Renamed to: {new_name}")
            except Exception as e:
                self.status_bar.config(text=f"Error renaming: {e}")

    def move_selected(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        selection = self.file_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.file_tree.item(item, "values")
        if not values:
            return

        source_path = values[0]
        item_text = self.file_tree.item(item, "text")
        name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        dest_dir = filedialog.askdirectory(title="Select destination folder", initialdir=self.current_folder)
        if dest_dir:
            dest_path = os.path.join(dest_dir, name)
            try:
                import shutil
                if os.path.exists(dest_path):
                    overwrite = messagebox.askyesno(
                        "File Exists",
                        f"'{name}' already exists in the destination. Overwrite?",
                        parent=self.root
                    )
                    if not overwrite:
                        return
                    if os.path.isdir(dest_path):
                        shutil.rmtree(dest_path)
                    else:
                        os.remove(dest_path)
                shutil.move(source_path, dest_path)
                self.populate_file_tree(self.current_folder)

                # Update tab if this file is open
                existing = self.tabbed_editor.find_tab_by_path(source_path)
                if existing >= 0:
                    self.tabbed_editor.set_tab_path(
                        self.tabbed_editor._tabs[existing]['id'], dest_path)

                self.status_bar.config(text=f"Moved to: {dest_dir}")
            except Exception as e:
                self.status_bar.config(text=f"Error moving: {e}")

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

    # ---- Git operations ----------------------------------------------------

    def git_clone(self):
        clone_dialog = tk.Toplevel(self.root)
        clone_dialog.title("Clone Repository")
        clone_dialog.geometry("500x200")
        clone_dialog.transient(self.root)
        clone_dialog.grab_set()

        ttk.Label(clone_dialog, text="Repository URL:").pack(anchor="w", padx=10, pady=(10, 5))

        url_var = tk.StringVar()
        url_entry = ttk.Entry(clone_dialog, textvariable=url_var)
        url_entry.pack(fill=tk.X, padx=10, pady=5)
        url_entry.focus()

        ttk.Label(clone_dialog, text="Destination Directory:").pack(anchor="w", padx=10, pady=(10, 5))

        dest_frame = ttk.Frame(clone_dialog)
        dest_frame.pack(fill=tk.X, padx=10, pady=5)

        dest_var = tk.StringVar(value=os.path.expanduser('~'))
        dest_entry = ttk.Entry(dest_frame, textvariable=dest_var)
        dest_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def browse_dest():
            dest_dir = filedialog.askdirectory(title="Select Destination", initialdir=dest_var.get())
            if dest_dir:
                dest_var.set(dest_dir)

        ttk.Button(dest_frame, text="Browse...", command=browse_dest).pack(side=tk.LEFT, padx=5)

        def do_clone():
            url = url_var.get().strip()
            dest = dest_var.get().strip()

            if not url:
                messagebox.showwarning("Missing URL", "Please enter repository URL", parent=clone_dialog)
                return

            if not dest:
                messagebox.showwarning("Missing Destination", "Please select destination directory", parent=clone_dialog)
                return

            try:
                result = subprocess.run(
                    ['git', '-c', 'core.quotePath=false', 'clone', url, dest],
                    capture_output=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=120
                )

                if result.returncode == 0:
                    self.status_bar.config(text="Repository cloned successfully")
                    clone_dialog.destroy()
                    messagebox.showinfo("Success", "Repository cloned successfully", parent=self.root)
                    self.current_folder = dest
                    self.populate_file_tree(dest)
                    self.terminal_panel.cd_to(dest)
                    self.status_bar.config(text=f"Opened: {os.path.basename(dest)}")
                    self._update_git_buttons()
                else:
                    error_msg = result.stderr if result.stderr else result.stdout
                    messagebox.showerror("Error", f"Failed to clone repository:\n{error_msg}", parent=clone_dialog)
            except FileNotFoundError:
                messagebox.showerror("Error", "Git not found. Please install git.", parent=clone_dialog)
            except subprocess.TimeoutExpired:
                messagebox.showerror("Error", "Clone operation timed out", parent=clone_dialog)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to clone:\n{str(e)}", parent=clone_dialog)

        button_frame = ttk.Frame(clone_dialog)
        button_frame.pack(fill=tk.X, padx=10, pady=(10, 10))

        ttk.Button(button_frame, text="Clone", command=do_clone).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Cancel", command=clone_dialog.destroy).pack(side=tk.RIGHT, padx=5)

        url_entry.bind("<Return>", lambda _: do_clone())

    def git_init(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        confirm = messagebox.askyesno(
            "Init Git Repository",
            f"Initialize git repository in:\n{self.current_folder}?",
            parent=self.root
        )
        if confirm:
            returncode, stdout, stderr = self._run_git(['init'])
            if returncode == 0:
                self.status_bar.config(text="Git repository initialized")
                messagebox.showinfo("Success", "Git repository initialized successfully", parent=self.root)
                self._update_git_buttons()
            else:
                self.status_bar.config(text=f"Git init failed: {stderr}")
                messagebox.showerror("Error", f"Failed to initialize git:\n{stderr}", parent=self.root)

    def git_set_remote(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self._run_git(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, _ = self._run_git(['remote', '-v'])
        current_remote = ""
        if returncode == 0 and stdout.strip():
            current_remote = f"\nCurrent remotes:\n{stdout}"

        remote_url = simpledialog.askstring(
            "Set Git Remote",
            f"Enter remote URL:{current_remote}\n\nExamples:\nhttps://github.com/username/repo.git\ngit@github.com:username/repo.git",
            parent=self.root
        )
        if remote_url:
            returncode, _, _ = self._run_git(['remote', 'get-url', 'origin'])
            if returncode == 0:
                returncode, stdout, stderr = self._run_git(['remote', 'set-url', 'origin', remote_url])
            else:
                returncode, stdout, stderr = self._run_git(['remote', 'add', 'origin', remote_url])

            if returncode == 0:
                self.status_bar.config(text="Remote set successfully")
                messagebox.showinfo("Success", "Remote URL set successfully", parent=self.root)
            else:
                self.status_bar.config(text=f"Failed to set remote: {stderr}")
                messagebox.showerror("Error", f"Failed to set remote:\n{stderr}", parent=self.root)

    def git_commit(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, stderr = self._run_git(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        # Get commit message from the entry field, fall back to dialog if placeholder
        commit_msg = self._commit_msg_var.get().strip()
        if not commit_msg or commit_msg == "Enter commit message...":
            commit_msg = simpledialog.askstring(
                "Git Commit",
                "Enter commit message:",
                parent=self.root
            )
        if commit_msg:
            returncode, stdout, stderr = self._run_git(['add', '.'])
            if returncode != 0:
                messagebox.showerror("Error", f"Failed to stage files:\n{stderr}", parent=self.root)
                return

            returncode, stdout, stderr = self._run_git(['commit', '-m', commit_msg])
            if returncode == 0:
                self.status_bar.config(text=f"Committed: {commit_msg}")
                self._commit_msg_var.set("")
                self._commit_msg_entry.insert(0, "Enter commit message...")
                messagebox.showinfo("Success", f"Changes committed:\n{commit_msg}", parent=self.root)
                self._refresh_git_panel()
            else:
                self.status_bar.config(text=f"Commit failed: {stderr}")
                messagebox.showerror("Error", f"Failed to commit:\n{stderr}", parent=self.root)

    def git_pull(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self._run_git(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        confirm = messagebox.askyesno("Git Pull", "Pull changes from remote?", parent=self.root)
        if confirm:
            returncode, stdout, stderr = self._run_git(['pull'])
            if returncode == 0:
                self.status_bar.config(text="Pull successful")
                messagebox.showinfo("Success", f"Pull successful:\n{stdout}", parent=self.root)
                self.populate_file_tree(self.current_folder)
                self._refresh_git_panel()
            else:
                self.status_bar.config(text=f"Pull failed: {stderr}")
                messagebox.showerror("Error", f"Failed to pull:\n{stderr}", parent=self.root)

    def git_push(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self._run_git(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, _ = self._run_git(['remote', 'get-url', 'origin'])
        if returncode != 0:
            messagebox.showwarning("No Remote", "Please set remote URL first", parent=self.root)
            return

        returncode, branch_stdout, _ = self._run_git(['branch', '--show-current'])
        current_branch = branch_stdout.strip() if returncode == 0 else 'main'

        returncode, _, _ = self._run_git(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])

        confirm = messagebox.askyesno("Git Push", "Push changes to remote?", parent=self.root)
        if confirm:
            if returncode != 0:
                returncode, stdout, stderr = self._run_git(['push', '--set-upstream', 'origin', current_branch])
            else:
                returncode, stdout, stderr = self._run_git(['push'])

            if returncode == 0:
                self.status_bar.config(text="Push successful")
                messagebox.showinfo("Success", f"Push successful:\n{stdout}", parent=self.root)
            else:
                self.status_bar.config(text=f"Push failed: {stderr}")
                messagebox.showerror("Error", f"Failed to push:\n{stderr}", parent=self.root)

    def _refresh_commit_log(self):
        """Refresh the inline commit log widget."""
        if not self.current_folder:
            return
        returncode, stdout, stderr = self._run_git(
            ['log', '--oneline', '--graph', '--all', '-20'])
        self._git_log_text.config(state=tk.NORMAL)
        self._git_log_text.delete(1.0, tk.END)
        if returncode != 0:
            self._git_log_text.insert(tk.END, f"Error: {stderr}")
        elif not stdout.strip():
            self._git_log_text.insert(tk.END, "No commits yet")
        else:
            self._git_log_text.insert(tk.END, stdout)
        self._git_log_text.config(state=tk.DISABLED)

    def _on_commit_msg_focus_in(self, event):
        """Clear placeholder text when entry gets focus."""
        if self._commit_msg_var.get() == "Enter commit message...":
            self._commit_msg_var.set("")

    def _on_commit_msg_focus_out(self, event):
        """Restore placeholder text when entry loses focus and is empty."""
        if not self._commit_msg_var.get().strip():
            self._commit_msg_var.set("Enter commit message...")

    # ---- Branch management popup -----------------------------------------

    def _show_branch_popup(self, event=None):
        """Show a popup dialog listing local branches, with switch and create actions."""
        if not self.current_folder:
            return

        has_git = os.path.isdir(os.path.join(self.current_folder, '.git'))
        if not has_git:
            return

        # Get branch info
        _, current_branch, _ = self._run_git(['branch', '--show-current'])
        current_branch = current_branch.strip()

        _, output, _ = self._run_git(['branch'])
        branches = [b.lstrip('* ').strip() for b in output.splitlines() if b.strip()]

        popup = tk.Toplevel(self.root)
        popup.title("Branches")
        popup.geometry("300x350")
        popup.transient(self.root)
        popup.grab_set()

        ttk.Label(popup, text="Local Branches",
                  font=("Helvetica", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 5))

        list_frame = ttk.Frame(popup)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        list_scroll = ttk.Scrollbar(list_frame)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        branch_list = tk.Listbox(list_frame, yscrollcommand=list_scroll.set,
                                 font=("Monaco", 11), selectmode=tk.SINGLE)
        branch_list.pack(fill=tk.BOTH, expand=True)
        list_scroll.config(command=branch_list.yview)

        # Populate branches, select current
        for i, b in enumerate(branches):
            branch_list.insert(tk.END, b)
            if b == current_branch:
                branch_list.selection_set(i)
                branch_list.see(i)
                branch_list.itemconfig(i, {'bg': '#d0e0f0'})

        def do_switch():
            sel = branch_list.curselection()
            if not sel:
                return
            target = branch_list.get(sel[0])
            if target == current_branch:
                return
            self._switch_to_branch(target, popup)

        def do_create():
            self._create_and_switch_branch(popup, current_branch)

        # Double-click to switch
        branch_list.bind('<Double-Button-1>', lambda e: do_switch())

        btn_frame = ttk.Frame(popup)
        btn_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        ttk.Button(btn_frame, text="Switch", command=do_switch).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="New", command=do_create).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Close", command=popup.destroy).pack(side=tk.RIGHT)

    def _switch_to_branch(self, branch_name, popup):
        """Check out the given branch and refresh state."""
        returncode, _, stderr = self._run_git(['checkout', branch_name])
        if returncode != 0:
            messagebox.showerror("Switch Branch Failed",
                                 f"Could not switch to '{branch_name}':\n{stderr}",
                                 parent=self.root)
            return
        self.status_bar.config(text=f"Switched to branch: {branch_name}")
        popup.destroy()
        self._refresh_git_panel()
        self.populate_file_tree(self.current_folder)

    def _create_and_switch_branch(self, popup, current_branch):
        """Create a new branch from current branch and switch to it."""
        new_name = simpledialog.askstring(
            "New Branch", "Enter new branch name:",
            parent=popup
        )
        if not new_name:
            return

        returncode, _, stderr = self._run_git(['checkout', '-b', new_name])
        if returncode != 0:
            messagebox.showerror("Create Branch Failed",
                                 f"Could not create branch '{new_name}':\n{stderr}",
                                 parent=self.root)
            return
        self.status_bar.config(text=f"Created and switched to branch: {new_name}")
        popup.destroy()
        self._refresh_git_panel()
        self.populate_file_tree(self.current_folder)

    def show_git_log(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self._run_git(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, stderr = self._run_git(['log', '--oneline', '--graph', '--all', '-20'])
        if returncode != 0:
            messagebox.showerror("Error", f"Failed to get log:\n{stderr}", parent=self.root)
            return

        log_window = tk.Toplevel(self.root)
        log_window.title("Git Log")
        log_window.geometry("600x400")
        log_window.transient(self.root)

        btn_frame = ttk.Frame(log_window)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(btn_frame, text="Refresh", command=lambda: self.refresh_git_log(log_text)).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="Close", command=log_window.destroy).pack(side=tk.RIGHT, padx=5)

        log_scroll = ttk.Scrollbar(log_window)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        log_text = tk.Text(log_window, yscrollcommand=log_scroll.set, font=("Monaco", 11))
        log_text.pack(fill=tk.BOTH, expand=True, padx=(10, 0), pady=(0, 10))
        log_scroll.config(command=log_text.yview)

        if not stdout.strip():
            log_text.insert(tk.END, "No commits yet")
        else:
            log_text.insert(tk.END, stdout)

        log_text.config(state=tk.DISABLED)

    def refresh_git_log(self, log_text):
        returncode, stdout, stderr = self._run_git(['log', '--oneline', '--graph', '--all', '-20'])
        log_text.config(state=tk.NORMAL)
        log_text.delete(1.0, tk.END)
        if returncode != 0:
            log_text.insert(tk.END, f"Error: {stderr}")
        elif not stdout.strip():
            log_text.insert(tk.END, "No commits yet")
        else:
            log_text.insert(tk.END, stdout)
        log_text.config(state=tk.DISABLED)
