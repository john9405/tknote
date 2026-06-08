import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, simpledialog, messagebox
from tkhtmlview import HTMLLabel
import markdown
import tempfile
import os
import re
import subprocess
import webbrowser


class TabbedEditor(ttk.Frame):
    """Multi-tab editor using Canvas for tab headers + Frame per tab content."""

    TAB_HEIGHT = 28
    MIN_TAB_WIDTH = 80
    MAX_TAB_WIDTH = 160
    TAB_PAD_X = 8
    CLOSE_SIZE = 16

    def __init__(self, parent, on_tab_created=None, on_tab_switch=None,
                 on_close_request=None, on_new_tab_request=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._on_tab_created = on_tab_created
        self._on_tab_switch = on_tab_switch
        self._on_close_request = on_close_request
        self._on_new_tab_request = on_new_tab_request

        self._tabs = []          # [{id, title, file_path, editor, frame, modified}]
        self._active_index = -1
        self._tab_id_counter = 0

        # Hit-testing state (rebuilt on each _redraw)
        self._tab_rects = []     # [(x1, y1, x2, y2, tab_index), ...]
        self._close_rects = []   # [(x1, y1, x2, y2, tab_index), ...]
        self._plus_rect = None   # (x1, y1, x2, y2)

        self._build_ui()

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self):
        # Tab bar area
        tab_bar_frame = ttk.Frame(self)
        tab_bar_frame.pack(fill=tk.X, side=tk.TOP)

        self.tab_canvas = tk.Canvas(
            tab_bar_frame, height=self.TAB_HEIGHT + 3,
            bg='#ececec', highlightthickness=0
        )
        self.tab_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.tab_canvas.bind('<Button-1>', self._on_canvas_click)
        self.tab_canvas.bind('<MouseWheel>', self._on_mousewheel)

        # Plus button
        self._plus_canvas = tk.Canvas(
            tab_bar_frame, width=26, height=self.TAB_HEIGHT + 3,
            bg='#ececec', highlightthickness=0
        )
        self._plus_canvas.pack(side=tk.RIGHT)
        self._plus_canvas.bind('<Button-1>',
            lambda e: self._on_new_tab_request and self._on_new_tab_request())
        self._draw_plus_button()

        # Horizontal scrollbar (hidden until needed)
        self.tab_scrollbar = ttk.Scrollbar(
            tab_bar_frame, orient=tk.HORIZONTAL,
            command=self.tab_canvas.xview
        )
        self.tab_canvas.configure(xscrollcommand=self.tab_scrollbar.set)

        # Content area
        self.content_frame = ttk.Frame(self)
        self.content_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        # Separator line below tabs
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, side=tk.TOP)

    def _draw_plus_button(self):
        self._plus_canvas.delete('all')
        w, h = 26, self.TAB_HEIGHT + 3
        self._plus_canvas.create_text(
            w // 2, h // 2 - 1, text='+',
            font=('Helvetica', 13, 'bold'),
            fill='#555555', anchor='center'
        )

    # ---- Public API --------------------------------------------------------

    def add_tab(self, title='Untitled', file_path=None, content='', file_type='md'):
        """Add a new tab and return its tab_id.

        file_type: 'md' for markdown (shows preview), 'txt' for plain text (editor only).
        """
        tab_id = self._tab_id_counter
        self._tab_id_counter += 1

        # Main tab frame
        tab_frame = ttk.Frame(self.content_frame)

        # Toolbar above the editor/preview split
        tab_toolbar = ttk.Frame(tab_frame)
        tab_toolbar.pack(fill=tk.X, side=tk.TOP)

        preview_btn = ttk.Button(
            tab_toolbar, text='👁', width=3,
            command=lambda tid=tab_id: self.toggle_tab_preview(tid)
        )
        preview_btn.pack(side=tk.RIGHT, padx=(0, 2))

        # PanedWindow for editor (left) + preview (right)
        tab_paned = ttk.PanedWindow(tab_frame, orient=tk.HORIZONTAL)
        tab_paned.pack(fill=tk.BOTH, expand=True)

        # -- Editor pane --
        editor_frame = ttk.Frame(tab_paned)
        editor = scrolledtext.ScrolledText(
            editor_frame, wrap=tk.WORD,
            font=('Monaco', 12), tabstyle='wordprocessor',
            insertbackground='black',
            undo=True, autoseparators=True, maxundo=-1
        )
        editor.pack(fill=tk.BOTH, expand=True)
        tab_paned.add(editor_frame, weight=1)

        # -- Preview pane --
        preview_frame = ttk.Frame(tab_paned)
        preview = HTMLLabel(preview_frame)
        preview.pack(fill=tk.BOTH, expand=True)
        preview.set_html('')
        tab_paned.add(preview_frame, weight=1)

        if content:
            editor.insert(tk.END, content)
            editor.edit_modified(False)  # reset after initial load

        # For plain text files: hide the preview button and pane
        if file_type == 'txt':
            preview_btn.pack_forget()
            tab_paned.forget(preview_frame)

        # Track modified state via <<Modified>> virtual event
        editor.bind('<<Modified>>',
            lambda e, tid=tab_id: self._on_editor_modified(tid, e))

        tab = {
            'id': tab_id,
            'title': title,
            'file_path': file_path,
            'file_type': file_type,
            'editor': editor,
            'frame': tab_frame,
            'preview': preview,
            'preview_frame': preview_frame,
            'preview_btn': preview_btn,
            'tab_paned': tab_paned,
            'preview_visible': file_type != 'txt',
            'modified': False,
        }
        self._tabs.append(tab)

        # Notify so caller can bind keyboard shortcuts
        if self._on_tab_created:
            self._on_tab_created(tab_id, editor)

        # Switch to the new tab
        self.switch_to_tab(len(self._tabs) - 1)
        return tab_id

    def close_tab(self, index):
        """Remove a tab by index. Returns True if closed, False if not found."""
        if index < 0 or index >= len(self._tabs):
            return False

        was_active = (index == self._active_index)
        tab = self._tabs[index]
        tab['frame'].destroy()
        self._tabs.pop(index)

        if self._tabs:
            if was_active:
                # Active tab was closed — pick the nearest remaining tab
                new_idx = max(0, min(index, len(self._tabs) - 1))
            else:
                # Non-active tab closed — keep the active tab active
                if index < self._active_index:
                    self._active_index -= 1  # shift down
                new_idx = self._active_index

            # Invalidate so switch_to_tab won't skip the re-pack
            self._active_index = -1
            self.switch_to_tab(new_idx)
        else:
            self._active_index = -1
            self._redraw()
            if self._on_tab_switch:
                self._on_tab_switch(None)
        return True

    def switch_to_tab(self, index):
        """Activate the tab at the given index."""
        if index < 0 or index >= len(self._tabs):
            return

        if self._active_index == index:
            return

        # Hide current tab
        if 0 <= self._active_index < len(self._tabs):
            self._tabs[self._active_index]['frame'].pack_forget()

        # Show new tab
        self._active_index = index
        tab = self._tabs[index]
        tab['frame'].pack(fill=tk.BOTH, expand=True)
        tab['editor'].focus_set()
        self._redraw()

        if self._on_tab_switch:
            self._on_tab_switch(tab['id'])

    def find_tab_by_path(self, file_path):
        """Return tab index for the given file path, or -1 if not found."""
        if file_path is None:
            return -1
        for i, tab in enumerate(self._tabs):
            if tab['file_path'] == file_path:
                return i
        return -1

    def get_tab_index(self, tab_id):
        """Return tab index for the given tab_id, or -1 if not found."""
        for i, tab in enumerate(self._tabs):
            if tab['id'] == tab_id:
                return i
        return -1

    def get_active_editor(self):
        """Return the ScrolledText widget of the active tab, or None."""
        if 0 <= self._active_index < len(self._tabs):
            return self._tabs[self._active_index]['editor']
        return None

    def get_active_tab(self):
        """Return the active tab dict, or None."""
        if 0 <= self._active_index < len(self._tabs):
            return self._tabs[self._active_index]
        return None

    def get_tab_by_id(self, tab_id):
        """Return the tab dict for tab_id, or None."""
        for tab in self._tabs:
            if tab['id'] == tab_id:
                return tab
        return None

    def set_tab_path(self, tab_id, file_path):
        """Update a tab's file path and title."""
        tab = self.get_tab_by_id(tab_id)
        if tab:
            tab['file_path'] = file_path
            tab['title'] = os.path.basename(file_path)
            self._redraw()

    def set_tab_title(self, tab_id, title):
        """Update a tab's display title."""
        tab = self.get_tab_by_id(tab_id)
        if tab:
            tab['title'] = title
            self._redraw()

    def set_tab_modified(self, tab_id, modified):
        """Set or clear the modified flag for a tab."""
        tab = self.get_tab_by_id(tab_id)
        if tab and tab['modified'] != modified:
            tab['modified'] = modified
            self._redraw()

    def get_tab_count(self):
        """Return the number of open tabs."""
        return len(self._tabs)

    def get_active_index(self):
        """Return the index of the currently active tab."""
        return self._active_index

    def toggle_tab_preview(self, tab_id):
        """Toggle the preview pane for a specific tab. Returns new state."""
        tab = self.get_tab_by_id(tab_id)
        if not tab:
            return True
        # Plain text tabs have no preview
        if tab.get('file_type') == 'txt':
            return False
        visible = not tab.get('preview_visible', True)
        tab['preview_visible'] = visible
        if visible:
            tab['tab_paned'].add(tab['preview_frame'], weight=1)
        else:
            tab['tab_paned'].forget(tab['preview_frame'])
        return visible

    def toggle_preview(self):
        """Toggle the preview pane for the active tab. Returns new state."""
        tab = self.get_active_tab()
        if not tab:
            return True
        return self.toggle_tab_preview(tab['id'])

    def update_active_preview(self, html_content):
        """Update the preview content for the active tab (no-op for txt files)."""
        tab = self.get_active_tab()
        if tab and tab.get('file_type') != 'txt':
            tab['preview'].set_html(html_content)

    def get_preview_visible(self):
        """Return whether the active tab's preview is visible."""
        tab = self.get_active_tab()
        if tab:
            return tab.get('preview_visible', True)
        return True

    def set_tab_file_type(self, tab_id, file_type):
        """Update a tab's file type, showing/hiding preview accordingly."""
        tab = self.get_tab_by_id(tab_id)
        if not tab:
            return
        old_type = tab.get('file_type', 'md')
        if old_type == file_type:
            return
        tab['file_type'] = file_type
        if file_type == 'txt':
            # Hide preview
            tab['preview_btn'].pack_forget()
            tab['tab_paned'].forget(tab['preview_frame'])
            tab['preview_visible'] = False
        else:
            # Show preview
            tab['preview_btn'].pack(side=tk.RIGHT, padx=(0, 2))
            tab['tab_paned'].add(tab['preview_frame'], weight=1)
            tab['preview_visible'] = True

    # ---- Internal helpers --------------------------------------------------

    def _on_editor_modified(self, tab_id, event):
        """Fired when a tab's editor content changes."""
        editor = event.widget
        if editor.edit_modified():
            self.set_tab_modified(tab_id, True)
            editor.edit_modified(False)  # reset so it fires again

    def _redraw(self):
        """Redraw all tab headers on the canvas."""
        self.tab_canvas.delete('all')
        self._tab_rects = []
        self._close_rects = []
        self._plus_rect = None

        # Use a reasonable default width if canvas not yet realized
        canvas_w = self.tab_canvas.winfo_width()
        if canvas_w < 10:
            canvas_w = 800

        x, y, h = 4, 1, self.TAB_HEIGHT

        for i, tab in enumerate(self._tabs):
            # Build display text
            display = '• ' + tab['title'] if tab['modified'] else tab['title']

            # Estimate text width (approx pixels at 11pt Helvetica)
            text_px = len(display) * 8
            close_w = self.CLOSE_SIZE + 6
            tab_w = max(self.MIN_TAB_WIDTH,
                        min(self.MAX_TAB_WIDTH, text_px + close_w + self.TAB_PAD_X * 2))

            is_active = (i == self._active_index)

            if is_active:
                # Active tab: lighter, slightly taller, bridges to content
                bg = '#f0f0f0'
                self.tab_canvas.create_rectangle(
                    x, y - 1, x + tab_w, y + h + 2,
                    fill=bg, outline='#b0b0b0', width=1
                )
            else:
                bg = '#d8d8d8'
                self.tab_canvas.create_rectangle(
                    x, y, x + tab_w, y + h,
                    fill=bg, outline='#b0b0b0', width=1
                )

            # Tab title text
            text_x = x + self.TAB_PAD_X
            text_y = y + h // 2 + 1
            self.tab_canvas.create_text(
                text_x, text_y, text=display, anchor='w',
                font=('Helvetica', 11),
                fill='#222222' if is_active else '#444444'
            )

            # Store tab hit rect (with tab_id, not index)
            self._tab_rects.append((x, y, x + tab_w, y + h, tab['id']))

            # Close button (×)
            cx = x + tab_w - self.CLOSE_SIZE // 2 - 4
            cy = y + h // 2 + 1
            self.tab_canvas.create_text(
                cx, cy, text='×',
                font=('Helvetica', 13),
                fill='#666666', anchor='center'
            )
            self._close_rects.append((
                cx - self.CLOSE_SIZE // 2 - 1, y + 2,
                cx + self.CLOSE_SIZE // 2 + 1, y + h - 2, tab['id']
            ))

            x += tab_w + 2

        # Store plus button rect (for completeness)
        self._plus_rect = (x, y, x + 26, y + h)

        # Update scroll region
        total_w = x + 30
        self.tab_canvas.configure(scrollregion=(0, 0, total_w, h + 4))

        # Show/hide scrollbar
        if total_w > canvas_w + 4:
            self.tab_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        else:
            self.tab_scrollbar.pack_forget()

    def _on_canvas_click(self, event):
        """Handle mouse clicks on the tab canvas."""
        x = self.tab_canvas.canvasx(event.x)
        y = event.y

        # Check close buttons first (higher priority)
        for rx1, ry1, rx2, ry2, tab_id in reversed(self._close_rects):
            if rx1 <= x <= rx2 and ry1 <= y <= ry2:
                if self._on_close_request:
                    self._on_close_request(tab_id)
                return

        # Check tab rectangles for selection
        for rx1, ry1, rx2, ry2, tab_id in reversed(self._tab_rects):
            if rx1 <= x <= rx2 and ry1 <= y <= ry2:
                tab_idx = self.get_tab_index(tab_id)
                if tab_idx >= 0 and tab_idx != self._active_index:
                    self.switch_to_tab(tab_idx)
                return

    def _on_mousewheel(self, event):
        """Horizontal scrolling via mousewheel / trackpad (macOS)."""
        self.tab_canvas.xview_scroll(int(-event.delta / 30), 'units')


class MarkdownEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("Markdown Editor")
        self.root.geometry("1200x700")

        self.current_folder = None
        self.setup_ui()

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

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Toggle Preview", command=self.toggle_preview, accelerator="Cmd+\\")
        view_menu.add_separator()
        view_menu.add_command(label="Preview in Browser", command=self.preview_in_browser, accelerator="Cmd+P")
        view_menu.add_command(label="Refresh Preview", command=self.update_preview, accelerator="Cmd+R")

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
        editor_frame = ttk.Frame(paned)

        paned.add(sidebar_frame, weight=0)
        paned.add(editor_frame, weight=1)

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

        self.git_branch_label = ttk.Label(
            self.git_panel_frame, text="", font=("Helvetica", 10)
        )
        self.git_branch_label.pack(anchor="w", padx=10, pady=(5, 0))

        git_status_frame = ttk.Frame(self.git_panel_frame)
        git_status_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        git_status_scroll = ttk.Scrollbar(git_status_frame)
        git_status_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.git_status_text = tk.Text(
            git_status_frame, height=10, font=("Monaco", 10),
            yscrollcommand=git_status_scroll.set, state=tk.DISABLED,
            cursor='hand2'
        )
        self.git_status_text.pack(fill=tk.BOTH, expand=True)
        self.git_status_text.bind('<Double-Button-1>', self._on_git_status_double_click)
        self.git_status_text.bind('<Button-2>', self._on_git_status_right_click)
        self.git_status_text.bind('<Button-3>', self._on_git_status_right_click)
        self.git_status_text.bind('<Control-Button-1>', self._on_git_status_right_click)
        git_status_scroll.config(command=self.git_status_text.yview)

        # Git status context menu
        self._git_context_menu = tk.Menu(self.root, tearoff=0)
        self._git_context_menu.add_command(label="Rollback", command=self._git_rollback)
        self._git_context_menu.add_command(label="Open File", command=self._git_open_selected)
        self._git_context_menu.add_command(label="Show Diff", command=self._git_show_diff)
        self._git_right_clicked_file = None

        git_btn_frame = ttk.Frame(self.git_panel_frame)
        git_btn_frame.pack(fill=tk.X, padx=5, pady=5)
        # Store button references for show/hide control
        self._git_btns = {}
        # Row 0: repository setup
        self._git_btns['clone'] = ttk.Button(git_btn_frame, text="Clone", command=self.git_clone)
        self._git_btns['clone'].grid(row=0, column=0, padx=1, pady=1, sticky='ew')
        self._git_btns['init'] = ttk.Button(git_btn_frame, text="Init", command=self.git_init)
        self._git_btns['init'].grid(row=0, column=1, padx=1, pady=1, sticky='ew')
        # Row 1: remote + commit
        self._git_btns['remote'] = ttk.Button(git_btn_frame, text="Remote", command=self.git_set_remote)
        self._git_btns['remote'].grid(row=1, column=0, padx=1, pady=1, sticky='ew')
        self._git_btns['commit'] = ttk.Button(git_btn_frame, text="Commit", command=self.git_commit)
        self._git_btns['commit'].grid(row=1, column=1, padx=1, pady=1, sticky='ew')
        # Row 2: push + pull
        self._git_btns['push'] = ttk.Button(git_btn_frame, text="Push", command=self.git_push)
        self._git_btns['push'].grid(row=2, column=0, padx=1, pady=1, sticky='ew')
        self._git_btns['pull'] = ttk.Button(git_btn_frame, text="Pull", command=self.git_pull)
        self._git_btns['pull'].grid(row=2, column=1, padx=1, pady=1, sticky='ew')
        # Row 3: log + refresh
        self._git_btns['log'] = ttk.Button(git_btn_frame, text="Log", command=self.show_git_log)
        self._git_btns['log'].grid(row=3, column=0, padx=1, pady=1, sticky='ew')
        self._git_btns['refresh'] = ttk.Button(git_btn_frame, text="Refresh", command=self._refresh_git_panel)
        self._git_btns['refresh'].grid(row=3, column=1, padx=1, pady=1, sticky='ew')
        git_btn_frame.columnconfigure(0, weight=1)
        git_btn_frame.columnconfigure(1, weight=1)
        self._update_git_buttons()

        # Show file tree by default
        self.file_tree_frame.pack(fill=tk.BOTH, expand=True)

        # -- Tabbed editor --
        self.tabbed_editor = TabbedEditor(
            editor_frame,
            on_tab_created=self._bind_editor_shortcuts,
            on_tab_switch=self._on_tab_switch,
            on_close_request=self._on_close_request,
            on_new_tab_request=self.new_file,
        )
        self.tabbed_editor.pack(fill=tk.BOTH, expand=True)

        # -- Status bar --
        self.status_bar = ttk.Label(self.root, text="Ready", relief=tk.SUNKEN, anchor="w")
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=8)

    # ---- Shortcut binding for new editors ----------------------------------

    def _bind_editor_shortcuts(self, tab_id, editor):
        """Bind all keyboard shortcuts + events to a newly created editor."""
        editor.bind("<KeyRelease>", self.on_text_change)
        editor.bind("<Command-n>", lambda _: self.new_file())
        editor.bind("<Command-o>", lambda _: self.open_file())
        editor.bind("<Command-s>", lambda _: self.save_file())
        editor.bind("<Command-Shift-S>", lambda _: self.save_file_as())
        editor.bind("<Command-b>", lambda _: self.insert_format("**", "**"))
        editor.bind("<Command-i>", lambda _: self.insert_format("*", "*"))
        editor.bind("<Command-h>", lambda _: self.insert_heading())
        editor.bind("<Command-k>", lambda _: self.insert_link())
        editor.bind("<Command-Shift-c>", lambda _: self.insert_format("`", "`"))
        editor.bind("<Command-p>", lambda _: self.preview_in_browser())
        editor.bind("<Command-r>", lambda _: self.update_preview())
        editor.bind("<Command-backslash>", lambda _: self.toggle_preview())
        editor.bind("<Command-f>", lambda _: self.show_find_dialog())
        editor.bind("<Command-Shift-f>", lambda _: self.show_search_dialog())
        editor.bind("<Command-a>", lambda _: self.select_all())
        editor.bind("<Command-z>", lambda _: self.undo())
        editor.bind("<Command-Shift-Z>", lambda _: self.redo())
        editor.bind("<Button-2>", self.show_editor_context_menu)
        editor.bind("<Button-3>", self.show_editor_context_menu)
        editor.bind("<Control-Button-1>", self.show_editor_context_menu)

    # ---- Tab event callbacks -----------------------------------------------

    def _on_tab_switch(self, tab_id):
        """Called when the active tab changes."""
        self.update_preview()
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

        if tab['modified']:
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
                        content = tab['editor'].get(1.0, tk.END)
                        with open(tab['file_path'], 'w', encoding='utf-8') as f:
                            f.write(content)
                        self.tabbed_editor.set_tab_modified(tab_id, False)
                        tab['editor'].edit_modified(False)
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
        """Update the git panel with current branch and status."""
        if not self.current_folder:
            self.git_branch_label.config(text="No folder opened")
            self.git_status_text.config(state=tk.NORMAL)
            self.git_status_text.delete(1.0, tk.END)
            self.git_status_text.insert(tk.END, "Open a folder to see git status.")
            self.git_status_text.config(state=tk.DISABLED)
            self._update_git_buttons()
            return

        _, branch, _ = self.run_git_command(['branch', '--show-current'])
        branch = branch.strip() if branch else 'unknown'
        self.git_branch_label.config(text=f"Branch: {branch}")

        _, status, _ = self.run_git_command(['status', '--short'])
        self.git_status_text.config(state=tk.NORMAL)
        self.git_status_text.delete(1.0, tk.END)
        if status.strip():
            self.git_status_text.insert(tk.END, status)
        else:
            self.git_status_text.insert(tk.END, "Working tree clean")
        self.git_status_text.config(state=tk.DISABLED)
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
            rc, _, stderr = self.run_git_command(['checkout', '--', rel_path])
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
                        # Unbind the <<Modified>> handler so the
                        # programmatic delete/insert don't flag the
                        # tab as modified.  Also disable undo so the
                        # replacement isn't recorded in the undo stack.
                        editor.unbind('<<Modified>>')
                        editor.configure(undo=False)
                        editor.delete(1.0, tk.END)
                        editor.insert(tk.END, new_content)
                        editor.edit_reset()
                        editor.configure(undo=True)
                        # Re-bind the handler that was set in add_tab()
                        editor.bind('<<Modified>>',
                            lambda e, tid=tab['id']: self.tabbed_editor._on_editor_modified(tid, e))
                        self.tabbed_editor.set_tab_modified(tab['id'], False)
                        if tab_idx == self.tabbed_editor.get_active_index():
                            self.update_preview()
                    except Exception as e:
                        self.status_bar.config(text=f"Error reloading file: {e}")
                # Close diff tab for this file
                diff_title = f'Diff: {name}'
                for i, tab in enumerate(self.tabbed_editor._tabs):
                    if tab['title'] == diff_title:
                        tab['modified'] = False
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
        _, diff, _ = self.run_git_command(['diff', '--', rel_path])
        if not diff.strip():
            _, diff, _ = self.run_git_command(['diff', '--cached', '--', rel_path])

        title = f'Diff: {os.path.basename(rel_path)}'
        content = diff if diff.strip() else '(no changes)'
        # Check if a diff tab already exists for this file
        for i, tab in enumerate(self.tabbed_editor._tabs):
            if tab['title'] == title:
                self.tabbed_editor.switch_to_tab(i)
                # Update content
                tab['editor'].delete(1.0, tk.END)
                tab['editor'].insert(tk.END, content)
                tab['editor'].edit_modified(False)
                return
        self.tabbed_editor.add_tab(title=title, content=content, file_type='txt')

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

        # All other buttons: visible only when folder is open
        for name in ('remote', 'commit', 'push', 'pull', 'log', 'refresh'):
            if folder_open:
                self._git_btns[name].grid()
            else:
                self._git_btns[name].grid_remove()

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

        # Detect file type from extension
        file_type = 'txt' if file_path.lower().endswith('.txt') else 'md'

        self.tabbed_editor.add_tab(
            title=os.path.basename(file_path),
            file_path=file_path,
            content=content,
            file_type=file_type
        )
        self.status_bar.config(text=f"Opened: {os.path.basename(file_path)}")

    # ---- Save helpers ------------------------------------------------------

    def _save_to_tab(self, tab):
        """Write a tab's editor content to its file_path."""
        try:
            content = tab['editor'].get(1.0, tk.END)
            with open(tab['file_path'], 'w', encoding='utf-8') as f:
                f.write(content)
            self.tabbed_editor.set_tab_modified(tab['id'], False)
            tab['editor'].edit_modified(False)
            self.status_bar.config(text=f"Saved: {os.path.basename(tab['file_path'])}")
            return True
        except Exception as e:
            self.status_bar.config(text=f"Error saving file: {e}")
            return False

    # ---- File operations ---------------------------------------------------

    def new_file(self):
        """Create a new tab, asking for a filename to determine type."""
        filename = simpledialog.askstring(
            "New File", "Enter file name (e.g. notes.md, readme.txt):",
            parent=self.root
        )
        if filename:
            file_type = 'txt' if filename.lower().endswith('.txt') else 'md'
        else:
            filename = 'Untitled'
            file_type = 'md'
        self.tabbed_editor.add_tab(title=filename, content='', file_type=file_type)
        self.update_preview()
        self.status_bar.config(text="New file")

    def open_file(self):
        file_path = filedialog.askopenfilename(
            title="Open Markdown File",
            filetypes=[("Markdown files", "*.md *.markdown"), ("Text files", "*.txt"), ("All files", "*.*")]
        )
        if file_path:
            self._open_file_in_tab(file_path)

    def open_folder(self):
        folder_path = filedialog.askdirectory(title="Open Folder", initialdir=os.path.expanduser('~'))
        if folder_path:
            self.current_folder = folder_path
            self.populate_file_tree(folder_path)
            self.status_bar.config(text=f"Opened folder: {os.path.basename(folder_path)}")
            self._update_git_buttons()

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
                    elif item.endswith(('.md', '.markdown')):
                        self.file_tree.insert(parent_id, "end", text=f"📝 {item}", values=(full_path,), tags=("file",))
                    elif item.endswith('.txt'):
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
                if os.path.isfile(file_path) and file_path.endswith(('.md', '.markdown', '.txt')):
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
            defaultextension=".md",
            filetypes=[("Markdown files", "*.md"), ("Text files", "*.txt"), ("All files", "*.*")]
        )
        if file_path:
            self.tabbed_editor.set_tab_path(tab['id'], file_path)
            # Update file type based on new extension
            new_type = 'txt' if file_path.lower().endswith('.txt') else 'md'
            self.tabbed_editor.set_tab_file_type(tab['id'], new_type)
            self.update_preview()
            return self._save_to_tab(tab)
        return False

    # ---- Preview -----------------------------------------------------------

    def on_text_change(self, event=None):
        self.update_preview()

    def update_preview(self):
        editor = self.editor
        if editor is None:
            return
        content = editor.get(1.0, tk.END)
        html = markdown.markdown(
            content,
            extensions=['tables', 'fenced_code', 'nl2br', 'sane_lists']
        )

        styled_html = f'''<div style="font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;padding:20px;line-height:1.6;color:#24292f;">
            {self._apply_inline_styles(html)}
        </div>'''
        self.tabbed_editor.update_active_preview(styled_html)

    def _apply_inline_styles(self, html):
        html = re.sub(r'<h1>(.*?)</h1>', r'<h1 style="font-size:2em;border-bottom:1px solid #d0d7de;padding-bottom:0.3em;margin-top:1.5em;margin-bottom:0.5em;font-weight:600;">\1</h1>', html)
        html = re.sub(r'<h2>(.*?)</h2>', r'<h2 style="font-size:1.5em;border-bottom:1px solid #d0d7de;padding-bottom:0.3em;margin-top:1.5em;margin-bottom:0.5em;font-weight:600;">\1</h2>', html)
        html = re.sub(r'<h3>(.*?)</h3>', r'<h3 style="font-size:1.25em;margin-top:1.5em;margin-bottom:0.5em;font-weight:600;">\1</h3>', html)
        html = re.sub(r'<h4>(.*?)</h4>', r'<h4 style="font-size:1em;margin-top:1.5em;margin-bottom:0.5em;font-weight:600;">\1</h4>', html)
        html = re.sub(r'<code>(.*?)</code>', r'<code style="background-color:#f6f8fa;padding:2px 6px;border-radius:3px;font-family:"Monaco","Courier New",monospace;font-size:0.9em;">\1</code>', html)
        html = re.sub(r'<pre>(.*?)</pre>', r'<pre style="background-color:#f6f8fa;padding:16px;border-radius:6px;overflow-x:auto;">\1</pre>', html)
        html = re.sub(r'<blockquote>(.*?)</blockquote>', r'<blockquote style="border-left:4px solid #d0d7de;padding-left:16px;color:#57606a;margin:0;">\1</blockquote>', html)
        html = re.sub(r'<a href="(.*?)">(.*?)</a>', r'<a href="\1" style="color:#0969da;text-decoration:none;">\2</a>', html)
        html = re.sub(r'<ul>(.*?)</ul>', r'<ul style="padding-left:2em;">\1</ul>', html)
        html = re.sub(r'<ol>(.*?)</ol>', r'<ol style="padding-left:2em;">\1</ol>', html)
        html = re.sub(r'<hr ?/?>', r'<hr style="border:none;border-top:1px solid #d0d7de;margin:2em 0;">', html)
        html = re.sub(r'<p>(.*?)</p>', r'<p style="margin:0.5em 0;">\1</p>', html)
        return html

    def toggle_preview(self):
        visible = self.tabbed_editor.toggle_preview()
        if visible:
            self.status_bar.config(text="Preview shown")
        else:
            self.status_bar.config(text="Preview hidden")
        self.update_preview()

    def preview_in_browser(self):
        editor = self.editor
        if editor is None:
            return
        content = editor.get(1.0, tk.END)
        html = markdown.markdown(
            content,
            extensions=['tables', 'fenced_code', 'nl2br', 'sane_lists']
        )

        styled_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Markdown Preview</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; padding: 40px; max-width: 800px; margin: 0 auto; line-height: 1.6; color: #24292f; }}
                h1, h2, h3, h4, h5, h6 {{ margin-top: 1.5em; margin-bottom: 0.5em; font-weight: 600; }}
                h1 {{ font-size: 2em; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }}
                h2 {{ font-size: 1.5em; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }}
                code {{ background-color: #f6f8fa; padding: 2px 6px; border-radius: 3px; font-family: 'Monaco', 'Courier New', monospace; font-size: 0.9em; }}
                pre {{ background-color: #f6f8fa; padding: 16px; border-radius: 6px; overflow-x: auto; }}
                pre code {{ background-color: transparent; padding: 0; }}
                blockquote {{ border-left: 4px solid #d0d7de; padding-left: 16px; color: #57606a; margin: 0; }}
                table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
                th, td {{ border: 1px solid #d0d7de; padding: 8px 12px; text-align: left; }}
                th {{ background-color: #f6f8fa; font-weight: 600; }}
                a {{ color: #0969da; text-decoration: none; }}
                a:hover {{ text-decoration: underline; }}
                img {{ max-width: 100%; }}
                hr {{ border: none; border-top: 1px solid #d0d7de; margin: 2em 0; }}
                ul, ol {{ padding-left: 2em; }}
            </style>
        </head>
        <body>
            {html}
        </body>
        </html>
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write(styled_html)
            temp_path = f.name

        webbrowser.open('file://' + temp_path)
        self.status_bar.config(text="Opened preview in browser")

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
        try:
            editor.edit_undo()
        except tk.TclError:
            pass

    def redo(self):
        editor = self.editor
        if editor is None:
            return
        try:
            editor.edit_redo()
        except tk.TclError:
            pass

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
                "New File", "Enter file name (e.g. notes.md, readme.txt):",
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
                    self.tabbed_editor._tabs[existing]['modified'] = False
                    self.tabbed_editor.close_tab(existing)
                    if self.tabbed_editor.get_tab_count() == 0:
                        self.tabbed_editor.add_tab(content='')
                        self.update_preview()
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
                    elif item.endswith(('.md', '.markdown', '.txt')):
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

    def run_git_command(self, args, cwd=None):
        if not cwd:
            cwd = self.current_folder
        try:
            result = subprocess.run(
                ['git', '-c', 'core.quotePath=false'] + args,
                cwd=cwd,
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=30
            )
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError:
            return 1, "", "Git not found. Please install git."
        except subprocess.TimeoutExpired:
            return 1, "", "Command timed out"
        except Exception as e:
            return 1, "", str(e)

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
            returncode, stdout, stderr = self.run_git_command(['init'])
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

        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, _ = self.run_git_command(['remote', '-v'])
        current_remote = ""
        if returncode == 0 and stdout.strip():
            current_remote = f"\nCurrent remotes:\n{stdout}"

        remote_url = simpledialog.askstring(
            "Set Git Remote",
            f"Enter remote URL:{current_remote}\n\nExamples:\nhttps://github.com/username/repo.git\ngit@github.com:username/repo.git",
            parent=self.root
        )
        if remote_url:
            returncode, _, _ = self.run_git_command(['remote', 'get-url', 'origin'])
            if returncode == 0:
                returncode, stdout, stderr = self.run_git_command(['remote', 'set-url', 'origin', remote_url])
            else:
                returncode, stdout, stderr = self.run_git_command(['remote', 'add', 'origin', remote_url])

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

        returncode, _, stderr = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, _ = self.run_git_command(['status', '--short'])
        status_text = stdout if stdout.strip() else "No changes to commit"

        commit_msg = simpledialog.askstring(
            "Git Commit",
            f"Git Status:\n{status_text}\n\nEnter commit message:",
            parent=self.root
        )
        if commit_msg:
            returncode, stdout, stderr = self.run_git_command(['add', '.'])
            if returncode != 0:
                messagebox.showerror("Error", f"Failed to stage files:\n{stderr}", parent=self.root)
                return

            returncode, stdout, stderr = self.run_git_command(['commit', '-m', commit_msg])
            if returncode == 0:
                self.status_bar.config(text=f"Committed: {commit_msg}")
                messagebox.showinfo("Success", f"Changes committed:\n{commit_msg}", parent=self.root)
            else:
                self.status_bar.config(text=f"Commit failed: {stderr}")
                messagebox.showerror("Error", f"Failed to commit:\n{stderr}", parent=self.root)

    def git_pull(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        confirm = messagebox.askyesno("Git Pull", "Pull changes from remote?", parent=self.root)
        if confirm:
            returncode, stdout, stderr = self.run_git_command(['pull'])
            if returncode == 0:
                self.status_bar.config(text="Pull successful")
                messagebox.showinfo("Success", f"Pull successful:\n{stdout}", parent=self.root)
                self.populate_file_tree(self.current_folder)
            else:
                self.status_bar.config(text=f"Pull failed: {stderr}")
                messagebox.showerror("Error", f"Failed to pull:\n{stderr}", parent=self.root)

    def git_push(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, _ = self.run_git_command(['remote', 'get-url', 'origin'])
        if returncode != 0:
            messagebox.showwarning("No Remote", "Please set remote URL first", parent=self.root)
            return

        returncode, branch_stdout, _ = self.run_git_command(['branch', '--show-current'])
        current_branch = branch_stdout.strip() if returncode == 0 else 'main'

        returncode, _, _ = self.run_git_command(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])

        confirm = messagebox.askyesno("Git Push", "Push changes to remote?", parent=self.root)
        if confirm:
            if returncode != 0:
                returncode, stdout, stderr = self.run_git_command(['push', '--set-upstream', 'origin', current_branch])
            else:
                returncode, stdout, stderr = self.run_git_command(['push'])

            if returncode == 0:
                self.status_bar.config(text="Push successful")
                messagebox.showinfo("Success", f"Push successful:\n{stdout}", parent=self.root)
            else:
                self.status_bar.config(text=f"Push failed: {stderr}")
                messagebox.showerror("Error", f"Failed to push:\n{stderr}", parent=self.root)

    def show_git_log(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        returncode, stdout, stderr = self.run_git_command(['log', '--oneline', '--graph', '--all', '-20'])
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
        returncode, stdout, stderr = self.run_git_command(['log', '--oneline', '--graph', '--all', '-20'])
        log_text.config(state=tk.NORMAL)
        log_text.delete(1.0, tk.END)
        if returncode != 0:
            log_text.insert(tk.END, f"Error: {stderr}")
        elif not stdout.strip():
            log_text.insert(tk.END, "No commits yet")
        else:
            log_text.insert(tk.END, stdout)
        log_text.config(state=tk.DISABLED)


if __name__ == "__main__":
    root = tk.Tk()
    app = MarkdownEditor(root)
    root.mainloop()
