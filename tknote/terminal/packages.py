"""PackageManager — list, install, uninstall, and update Python packages.

Displays installed packages for the currently selected Python environment
(venv or system Python) and provides buttons to manage them.
"""

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk


class PackageManager(ttk.Frame):
    """A panel that lists installed Python packages and allows managing them."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._close_callback = None
        self._venv_path: str | None = None
        self._packages: list[dict] = []  # [{name, version}]
        self._busy = False
        self._build_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Toolbar ──
        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky='ew', padx=4, pady=(4, 2))

        self._install_btn = ttk.Button(
            toolbar, text='➕ Install', command=self._install_package)
        self._install_btn.pack(side=tk.LEFT, padx=(0, 2))

        self._uninstall_btn = ttk.Button(
            toolbar, text='✖ Uninstall', command=self._uninstall_package)
        self._uninstall_btn.pack(side=tk.LEFT, padx=2)

        self._update_btn = ttk.Button(
            toolbar, text='⬆ Update', command=self._update_package)
        self._update_btn.pack(side=tk.LEFT, padx=2)

        self._update_all_btn = ttk.Button(
            toolbar, text='⬆ Update All', command=self._update_all)
        self._update_all_btn.pack(side=tk.LEFT, padx=2)

        self._refresh_btn = ttk.Button(
            toolbar, text='⟳ Refresh', command=self._refresh)
        self._refresh_btn.pack(side=tk.LEFT, padx=2)

        # ── Search / filter ──
        ttk.Label(toolbar, text='  Filter:').pack(side=tk.LEFT, padx=(12, 2))
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add('write', lambda *_: self._apply_filter())
        filter_entry = ttk.Entry(toolbar, textvariable=self._filter_var, width=18)
        filter_entry.pack(side=tk.LEFT, padx=2)

        # ── Package count ──
        self._count_label = ttk.Label(toolbar, text='')
        self._count_label.pack(side=tk.RIGHT, padx=(8, 4))

        # Close button
        close_btn = ttk.Button(
            toolbar, text='×', width=1, command=self._on_close)
        close_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # ── Treeview ──
        tree_frame = ttk.Frame(self)
        tree_frame.grid(row=2, column=0, sticky='nsew', padx=4, pady=2)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        columns = ('name', 'version')
        self._tree = ttk.Treeview(
            tree_frame, columns=columns, show='headings',
            selectmode='extended')
        self._tree.heading('name', text='Package', anchor='w')
        self._tree.heading('version', text='Version', anchor='w')
        self._tree.column('name', width=200, minwidth=100)
        self._tree.column('version', width=100, minwidth=60)

        tree_scroll = ttk.Scrollbar(
            tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)

        self._tree.grid(row=0, column=0, sticky='nsew')
        tree_scroll.grid(row=0, column=1, sticky='ns')

        # ── Context menu ──
        self._context_menu = tk.Menu(self, tearoff=0)
        self._context_menu.add_command(
            label='Uninstall', command=self._uninstall_package)
        self._context_menu.add_command(
            label='Update', command=self._update_package)
        self._context_menu.add_separator()
        self._context_menu.add_command(
            label='Copy Name', command=self._copy_package_name)

        self._tree.bind('<Button-2>', self._on_right_click)
        self._tree.bind('<Button-3>', self._on_right_click)
        self._tree.bind('<Control-Button-1>', self._on_right_click)

        # ── Output area (collapsed by default) ──
        self._output_frame = ttk.Frame(self)
        # Hidden until needed
        self._output_text = tk.Text(
            self._output_frame, height=4, font=('Monaco', 10),
            bg='#f5f5f5', fg='#1e1e1e', wrap=tk.WORD, state=tk.DISABLED)
        out_scroll = ttk.Scrollbar(
            self._output_frame, command=self._output_text.yview)
        self._output_text.configure(yscrollcommand=out_scroll.set)

        self._output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        out_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ── Status bar ──
        self._status = ttk.Label(self, text='Select a Python environment to view packages',
                                 anchor='w', padding=(4, 2))
        self._status.grid(row=3, column=0, sticky='ew', padx=4, pady=(0, 4))

        # Initial empty state
        self._update_button_states()

    # ── Close button ─────────────────────────────────────────────────────────

    def set_close_callback(self, callback):
        """Called when the close (×) button is clicked."""
        self._close_callback = callback

    def _on_close(self):
        if self._close_callback:
            self._close_callback()

    # ── Venv integration ─────────────────────────────────────────────────────

    def set_venv(self, venv_path: str | None):
        """Set the active venv and refresh the package list."""
        self._venv_path = venv_path
        self._refresh()

    def _get_python_exe(self) -> str:
        """Return the python executable path for the current environment."""
        if self._venv_path:
            for name in ('python3', 'python'):
                exe = os.path.join(self._venv_path, 'bin', name)
                if os.path.isfile(exe):
                    return exe
        return sys.executable

    # ── pip command execution ────────────────────────────────────────────────

    def _run_pip(self, args: list[str], callback=None):
        """Run a pip command in a background thread.

        Args:
            args: pip arguments (without 'pip' itself), e.g. ['list', '--format=json'].
            callback: Optional callback(result) on success; result is the CompletedProcess.
        """
        if self._busy:
            self._show_output('[busy] Another operation is in progress.\n', 'stderr')
            return

        python = self._get_python_exe()
        cmd = [python, '-m', 'pip'] + args
        self._busy = True
        self._update_button_states()
        self._status.config(text=f'Running: pip {" ".join(args)}')
        self._show_output(f'$ pip {" ".join(args)}\n', 'stdout')

        def target():
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=120)
                self.after(0, lambda: self._on_pip_done(result, callback))
            except subprocess.TimeoutExpired:
                self.after(0, lambda: self._on_pip_error(
                    'Operation timed out (120s).'))
            except Exception as e:
                self.after(0, lambda: self._on_pip_error(str(e)))

        t = threading.Thread(target=target, daemon=True)
        t.start()

    def _on_pip_done(self, result, callback):
        """Handle completion of a pip subprocess."""
        self._busy = False
        self._update_button_states()

        if result.returncode == 0:
            self._show_output(result.stdout, 'stdout')
            if result.stderr:
                self._show_output(result.stderr, 'stderr')
            if callback:
                callback(result)
        else:
            self._show_output(result.stderr or result.stdout, 'stderr')
            self._status.config(text='Command failed — see output below')

    def _on_pip_error(self, error_msg):
        """Handle an exception from the pip thread."""
        self._busy = False
        self._update_button_states()
        self._show_output(f'Error: {error_msg}\n', 'stderr')
        self._status.config(text=f'Error: {error_msg}')

    # ── Package operations ───────────────────────────────────────────────────

    def _refresh(self):
        """Refresh the package list from pip."""
        self._tree.delete(*self._tree.get_children())
        self._packages = []
        self._count_label.config(text='')

        env_label = (os.path.basename(self._venv_path) if self._venv_path
                     else f'Python {sys.version_info.major}.{sys.version_info.minor}')
        self._status.config(text=f'Loading packages from {env_label}...')
        self._update_button_states()

        def on_result(result):
            try:
                packages = json.loads(result.stdout)
            except json.JSONDecodeError:
                self._status.config(text='Failed to parse package list')
                return

            self._packages = packages
            self._populate_tree(packages)
            count = len(packages)
            self._count_label.config(
                text=f'{count} package{"s" if count != 1 else ""}')
            self._status.config(text=f'{env_label} — {count} packages')
            self._hide_output()

        self._run_pip(['list', '--format=json'], callback=on_result)

    def _populate_tree(self, packages: list[dict]):
        """Fill the treeview with package entries, respecting the filter."""
        self._tree.delete(*self._tree.get_children())
        search = self._filter_var.get().strip().lower()

        for pkg in packages:
            name = pkg.get('name', '')
            version = pkg.get('version', '')
            if search and search not in name.lower():
                continue
            self._tree.insert('', tk.END, values=(name, version))

    def _apply_filter(self):
        """Re-apply the current filter to the package list."""
        self._populate_tree(self._packages)

    def _install_package(self):
        """Prompt for a package name and install it."""
        if self._busy:
            return

        name = simpledialog.askstring(
            'Install Package',
            'Enter package name (e.g. requests):',
            parent=self)
        if not name or not name.strip():
            return

        name = name.strip()
        self._status.config(text=f'Installing {name}...')

        def on_done(_result):
            self._status.config(text=f'Installed: {name}')
            self._refresh()

        self._run_pip(['install', name], callback=on_done)

    def _uninstall_package(self):
        """Uninstall the selected package(s)."""
        if self._busy:
            return

        selected = self._get_selected_names()
        if not selected:
            messagebox.showinfo(
                'Uninstall', 'Select one or more packages to uninstall.',
                parent=self)
            return

        names = ', '.join(selected)
        confirm = messagebox.askyesno(
            'Uninstall Package',
            f'Uninstall the following package(s)?\n\n{names}',
            parent=self)
        if not confirm:
            return

        self._status.config(text=f'Uninstalling {names}...')

        def on_done(_result):
            self._status.config(text=f'Uninstalled: {names}')
            self._refresh()

        self._run_pip(['uninstall', '-y'] + selected, callback=on_done)

    def _update_package(self):
        """Update the selected package(s)."""
        if self._busy:
            return

        selected = self._get_selected_names()
        if not selected:
            messagebox.showinfo(
                'Update', 'Select one or more packages to update.',
                parent=self)
            return

        names = ', '.join(selected)
        self._status.config(text=f'Updating {names}...')

        def on_done(_result):
            self._status.config(text=f'Updated: {names}')
            self._refresh()

        self._run_pip(
            ['install', '--upgrade'] + selected, callback=on_done)

    def _update_all(self):
        """Update all installed packages."""
        if self._busy:
            return

        if not self._packages:
            return

        confirm = messagebox.askyesno(
            'Update All Packages',
            f'Update all {len(self._packages)} packages to their latest versions?',
            parent=self)
        if not confirm:
            return

        self._status.config(text='Updating all packages...')

        def on_done(_result):
            self._status.config(text='All packages updated')
            self._refresh()

        # pip install --upgrade with a list of all package names
        # (excluding pip itself to avoid issues)
        names = [p['name'] for p in self._packages if p['name'] != 'pip']
        self._run_pip(['install', '--upgrade'] + names, callback=on_done)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _get_selected_names(self) -> list[str]:
        """Return the list of selected package names."""
        names = []
        for item in self._tree.selection():
            values = self._tree.item(item, 'values')
            if values:
                names.append(values[0])
        return names

    def _copy_package_name(self):
        """Copy selected package name(s) to clipboard."""
        names = self._get_selected_names()
        if names:
            self.clipboard_clear()
            self.clipboard_append('\n'.join(names))

    def _on_right_click(self, event):
        """Show context menu on right-click."""
        item = self._tree.identify_row(event.y)
        if item:
            if item not in self._tree.selection():
                self._tree.selection_set(item)
        else:
            self._tree.selection_remove(*self._tree.selection())

        has_selection = bool(self._tree.selection())
        self._context_menu.entryconfigure(
            0, state=tk.NORMAL if has_selection else tk.DISABLED)
        self._context_menu.entryconfigure(
            1, state=tk.NORMAL if has_selection else tk.DISABLED)
        self._context_menu.entryconfigure(
            3, state=tk.NORMAL if has_selection else tk.DISABLED)

        try:
            self._context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._context_menu.grab_release()

    def _update_button_states(self):
        """Enable/disable buttons based on busy state."""
        state = tk.DISABLED if self._busy else tk.NORMAL
        self._install_btn.config(state=state)
        self._uninstall_btn.config(state=state)
        self._update_btn.config(state=state)
        self._update_all_btn.config(state=state)
        self._refresh_btn.config(state=state)

    # ── Output area ──────────────────────────────────────────────────────────

    def _show_output(self, text: str, tag: str = ''):
        """Append text to the output area and show it."""
        # Show the output frame if hidden
        if not self._output_frame.winfo_ismapped():
            self._output_frame.grid(row=1, column=0, sticky='ew', padx=4, pady=2)

        self._output_text.config(state=tk.NORMAL)
        self._output_text.insert(tk.END, text)
        if tag:
            self._output_text.tag_add(tag, 'end-1c linestart', 'end-1c')
        self._output_text.see(tk.END)
        self._output_text.config(state=tk.DISABLED)

        # Configure tags if not already done
        try:
            self._output_text.tag_configure('stdout', foreground='#1e1e1e')
            self._output_text.tag_configure('stderr', foreground='#d32f2f')
        except tk.TclError:
            pass

    def _hide_output(self):
        """Hide the output area."""
        self._output_frame.grid_forget()

    def focus_input(self):
        """No-op for tab consistency — focus the tree."""
        self._tree.focus_set()
