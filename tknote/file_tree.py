"""File tree panel — folder/file browser with context menu operations."""

import os
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


class FileTreePanel(ttk.Frame):
    """A sidebar panel that displays a folder tree and supports file operations.

    Parameters
    ----------
    parent : tk.Widget
        Parent widget.
    callbacks : dict
        Dictionary of callback functions:
        - ``get_current_folder``: () -> str | None
        - ``set_current_folder``: (str | None) -> None
        - ``open_file_in_tab``: (str) -> None
        - ``set_status``: (str) -> None
        - ``on_folder_changed``: () -> None  — called after open/close folder
    """

    def __init__(self, parent, callbacks=None):
        super().__init__(parent)
        self._callbacks = callbacks or {}

        self._build_ui()
        self._bind_events()

    # ---- callbacks --------------------------------------------------------

    def _cb(self, name, *args):
        """Safely call a registered callback."""
        fn = self._callbacks.get(name)
        if fn:
            return fn(*args)
        return None

    # ---- UI ----------------------------------------------------------------

    def _build_ui(self):
        # Header
        tree_header = ttk.Frame(self)
        tree_header.pack(fill=tk.X)
        ttk.Label(tree_header, text="Files", font=("Helvetica", 10, "bold")).pack(
            side=tk.LEFT, padx=(0, 5))

        btn_frame = ttk.Frame(tree_header)
        btn_frame.pack(side=tk.RIGHT)

        new_btn = ttk.Button(btn_frame, text="+", width=1, command=self._show_new_menu)
        new_btn.pack(side=tk.LEFT, padx=1)

        refresh_btn = ttk.Button(btn_frame, text="⟳", width=1, command=self.refresh)
        refresh_btn.pack(side=tk.LEFT, padx=1)

        # Treeview + scrollbar
        tree_scroll = ttk.Scrollbar(self)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree = ttk.Treeview(
            self, yscrollcommand=tree_scroll.set, selectmode="browse",
            show="tree headings")
        self._tree.heading("#0", text="Files")
        self._tree.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self._tree.yview)

        # Context menu
        self._context_menu = tk.Menu(self, tearoff=0)
        self._context_menu.add_command(label="New File", command=self.new_file)
        self._context_menu.add_command(label="New Folder", command=self.new_folder)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="Rename", command=self.rename_selected)
        self._context_menu.add_command(label="Move", command=self.move_selected)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="Delete", command=self.delete_selected)

    def _bind_events(self):
        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Button-2>", self._show_context_menu)
        self._tree.bind("<Button-3>", self._show_context_menu)
        self._tree.bind("<Control-Button-1>", self._show_context_menu)

    # ---- Public API --------------------------------------------------------

    @property
    def current_folder(self):
        return self._cb('get_current_folder')

    @current_folder.setter
    def current_folder(self, value):
        self._cb('set_current_folder', value)

    def populate(self, folder_path):
        """Rebuild the tree from *folder_path*."""
        if folder_path and os.path.isdir(folder_path):
            self._tree.heading("#0", text=f"📂 {os.path.basename(folder_path)}")
        else:
            self._tree.heading("#0", text="Files")
        self._tree.delete(*self._tree.get_children())

        def _add_items(path, parent_id):
            try:
                items = sorted(
                    os.listdir(path),
                    key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
                for item in items:
                    full = os.path.join(path, item)
                    if os.path.isdir(full):
                        nid = self._tree.insert(
                            parent_id, "end", text=f"📁 {item}",
                            values=(full,), tags=("dir",), open=False)
                        _add_items(full, nid)
                    else:
                        self._tree.insert(
                            parent_id, "end", text=f"📄 {item}",
                            values=(full,), tags=("file",))
            except PermissionError:
                pass

        _add_items(folder_path, "")

    def refresh(self):
        """Refresh the tree for the currently opened folder."""
        folder = self.current_folder
        if folder:
            self.populate(folder)
            self._cb('set_status', "File tree refreshed")
        else:
            self._cb('set_status', "No folder opened")

    # ---- Event handlers ----------------------------------------------------

    def _on_double_click(self, _event):
        sel = self._tree.selection()
        if sel:
            values = self._tree.item(sel[0], "values")
            if values and os.path.isfile(values[0]):
                self._cb('open_file_in_tab', values[0])

    def _show_context_menu(self, event):
        if not self.current_folder:
            return
        item = self._tree.identify_row(event.y)
        if item:
            self._tree.selection_set(item)
        self._context_menu.post(event.x_root, event.y_root)

    def _show_new_menu(self):
        if not self.current_folder:
            self._cb('new_file_requested')  # fallback — delegates to main app
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="New File", command=self.new_file)
        menu.add_command(label="New Folder", command=self.new_folder)
        menu.post(self._tree.winfo_rootx() + 50, self._tree.winfo_rooty() + 10)

    # ---- Helpers -----------------------------------------------------------

    def _get_selected_path(self):
        sel = self._tree.selection()
        if sel:
            values = self._tree.item(sel[0], "values")
            if values:
                return values[0]
        return self.current_folder

    # ---- File operations ---------------------------------------------------

    def new_file(self, filename=None):
        """Create a new file. If *filename* not given, prompt the user."""
        folder = self.current_folder
        if not folder:
            self._cb('set_status', "No folder opened")
            return

        parent_path = self._get_selected_path()
        if parent_path and os.path.isfile(parent_path):
            parent_path = os.path.dirname(parent_path)
        if parent_path is None:
            parent_path = folder

        if filename is None:
            filename = simpledialog.askstring(
                "New File", "Enter file name:", parent=self)
        if filename:
            new_path = os.path.join(parent_path, filename)
            try:
                with open(new_path, 'w', encoding='utf-8') as f:
                    f.write('')
                self.populate(folder)
                self._cb('set_status', f"Created: {filename}")
            except Exception as e:
                self._cb('set_status', f"Error creating file: {e}")

    def new_folder(self, foldername=None):
        """Create a new folder. If *foldername* not given, prompt."""
        folder = self.current_folder
        if not folder:
            self._cb('set_status', "No folder opened")
            return

        parent_path = self._get_selected_path()
        if parent_path and os.path.isfile(parent_path):
            parent_path = os.path.dirname(parent_path)
        if parent_path is None:
            parent_path = folder

        if foldername is None:
            foldername = simpledialog.askstring(
                "New Folder", "Enter folder name:", parent=self)
        if foldername:
            new_path = os.path.join(parent_path, foldername)
            try:
                os.makedirs(new_path, exist_ok=False)
                self.populate(folder)
                self._cb('set_status', f"Created folder: {foldername}")
            except FileExistsError:
                self._cb('set_status', f"Folder already exists: {foldername}")
            except Exception as e:
                self._cb('set_status', f"Error creating folder: {e}")

    def delete_selected(self):
        folder = self.current_folder
        if not folder:
            self._cb('set_status', "No folder opened")
            return

        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        if not values:
            return

        path = values[0]
        item_text = self._tree.item(sel[0], "text")
        name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text
        item_type = "folder" if os.path.isdir(path) else "file"

        if not messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete the {item_type} '{name}'?",
            parent=self,
        ):
            return

        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            self.populate(folder)
            self._cb('set_status', f"Deleted: {name}")
            # Notify app to close open tabs for this file
            self._cb('file_deleted', path)
        except Exception as e:
            self._cb('set_status', f"Error deleting: {e}")

    def rename_selected(self):
        folder = self.current_folder
        if not folder:
            self._cb('set_status', "No folder opened")
            return

        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        if not values:
            return

        path = values[0]
        item_text = self._tree.item(sel[0], "text")
        old_name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        new_name = simpledialog.askstring(
            "Rename", "Enter new name:", initialvalue=old_name, parent=self)
        if new_name and new_name != old_name:
            parent_dir = os.path.dirname(path)
            new_path = os.path.join(parent_dir, new_name)
            try:
                shutil.move(path, new_path)
                self.populate(folder)
                self._cb('set_status', f"Renamed to: {new_name}")
                self._cb('file_path_changed', path, new_path)
            except Exception as e:
                self._cb('set_status', f"Error renaming: {e}")

    def move_selected(self):
        folder = self.current_folder
        if not folder:
            self._cb('set_status', "No folder opened")
            return

        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        if not values:
            return

        source_path = values[0]
        item_text = self._tree.item(sel[0], "text")
        name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        dest_dir = filedialog.askdirectory(
            title="Select destination folder", initialdir=folder)
        if not dest_dir:
            return

        dest_path = os.path.join(dest_dir, name)
        try:
            if os.path.exists(dest_path):
                if not messagebox.askyesno(
                    "File Exists",
                    f"'{name}' already exists in the destination. Overwrite?",
                    parent=self,
                ):
                    return
                if os.path.isdir(dest_path):
                    shutil.rmtree(dest_path)
                else:
                    os.remove(dest_path)
            shutil.move(source_path, dest_path)
            self.populate(folder)
            self._cb('set_status', f"Moved to: {dest_dir}")
            self._cb('file_path_changed', source_path, dest_path)
        except Exception as e:
            self._cb('set_status', f"Error moving: {e}")
