"""TabbedEditor — multi-tab editor using Canvas for tab headers."""

import os
import tkinter as tk
from tkinter import ttk

from .widget import EditorWidget


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

    # ---- Public API --------------------------------------------------------

    def add_tab(self, title='Untitled', file_path=None, content=''):
        """Add a new tab and return its tab_id."""
        tab_id = self._tab_id_counter
        self._tab_id_counter += 1

        # Main tab frame
        tab_frame = ttk.Frame(self.content_frame)

        # Editor widget (idlelib-style: line numbers + text + syntax highlighting)
        editor = EditorWidget(tab_frame, file_path=file_path)
        editor.pack(fill=tk.BOTH, expand=True)

        if content:
            editor.set_text(content)
            editor.set_saved()

        tab = {
            'id': tab_id,
            'title': title,
            'file_path': file_path,
            'editor': editor,
            'frame': tab_frame,
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
        """Return the EditorWidget of the active tab, or None."""
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
            editor = tab['editor']
            editor._file_path = file_path
            bookmarks = getattr(editor, '_bookmarks', None)
            if bookmarks is not None:
                bookmarks.load()
            self._redraw()

    def set_tab_title(self, tab_id, title):
        """Update a tab's display title."""
        tab = self.get_tab_by_id(tab_id)
        if tab:
            tab['title'] = title
            self._redraw()

    def set_tab_modified(self, tab_id=None, modified=None):
        """No-op — EditorWidget tracks its own modified state internally."""
        self._redraw()

    def get_tab_count(self):
        """Return the number of open tabs."""
        return len(self._tabs)

    def get_active_index(self):
        """Return the index of the currently active tab."""
        return self._active_index

    # ---- Internal helpers --------------------------------------------------

    def _redraw(self):
        """Redraw all tab headers on the canvas."""
        self.tab_canvas.delete('all')
        self._tab_rects = []
        self._close_rects = []

        # Use a reasonable default width if canvas not yet realized
        canvas_w = self.tab_canvas.winfo_width()
        if canvas_w < 10:
            canvas_w = 800

        x, y, h = 4, 1, self.TAB_HEIGHT

        for i, tab in enumerate(self._tabs):
            # Build display text
            modified = tab['editor'].is_modified()
            display = '• ' + tab['title'] if modified else tab['title']

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

        # Update scroll region
        total_w = x + 4
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
