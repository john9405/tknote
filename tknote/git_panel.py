"""Git / Source Control panel — status, commit, push/pull, branch management."""

import os
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .git_ops import run_git_command


class GitPanel(ttk.Frame):
    """A sidebar panel for git source-control operations.

    Parameters
    ----------
    parent : tk.Widget
        Parent widget.
    callbacks : dict
        Dictionary of callback functions:
        - ``get_current_folder``: () -> str | None
        - ``set_status``: (str) -> None
        - ``open_file_in_tab``: (str) -> None
        - ``refresh_file_tree``: () -> None
        - ``update_sidebar_toggle``: (bool) -> None
    """

    def __init__(self, parent, callbacks=None):
        super().__init__(parent)
        self._callbacks = callbacks or {}
        self._right_clicked_file = None  # (rel_path, abs_path)

        self._build_header()
        self._build_buttons()
        self._build_changed_files()
        self._build_commit_log()

    # ---- callbacks --------------------------------------------------------

    def _cb(self, name, *args):
        fn = self._callbacks.get(name)
        if fn:
            return fn(*args)
        return None

    @property
    def current_folder(self):
        return self._cb('get_current_folder')

    # ---- internal git helper -----------------------------------------------

    def _run_git(self, args):
        return run_git_command(args, cwd=self.current_folder)

    # ---- UI: header --------------------------------------------------------

    def _build_header(self):
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(header, text="Source Control",
                  font=("Helvetica", 10, "bold")).pack(side=tk.LEFT)

        actions = ttk.Frame(header)
        actions.pack(side=tk.RIGHT)

        self._branch_label = ttk.Label(
            actions, text="⎇ no repo", font=("Helvetica", 10), cursor='hand2')
        self._branch_label.pack(side=tk.LEFT, padx=(0, 6))
        self._branch_label.bind('<Button-1>', self._show_branch_popup)

        self._push_btn = ttk.Button(
            actions, text="↑", command=self.push, width=1, state=tk.DISABLED)
        self._push_btn.pack(side=tk.LEFT, padx=1)

        self._pull_btn = ttk.Button(
            actions, text="↓", command=self.pull, width=1, state=tk.DISABLED)
        self._pull_btn.pack(side=tk.LEFT, padx=1)

    # ---- UI: buttons -------------------------------------------------------

    def _build_buttons(self):
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        self._btns = {}

        # Row 0: repository setup
        self._btns['clone'] = ttk.Button(
            btn_frame, text="Clone", command=self.clone)
        self._btns['clone'].grid(row=0, column=0, columnspan=2, padx=1, pady=1, sticky='ew')
        self._btns['init'] = ttk.Button(
            btn_frame, text="Init", command=self.init_repo)
        self._btns['init'].grid(row=0, column=1, columnspan=2, padx=1, pady=1, sticky='ew')

        # Row 1: remote setup
        self._btns['remote'] = ttk.Button(
            btn_frame, text="Remote", command=self.set_remote)
        self._btns['remote'].grid(row=1, column=0, columnspan=2, padx=1, pady=1, sticky='ew')

        # Row 2: commit message
        self._commit_msg_var = tk.StringVar()
        self._commit_msg_entry = ttk.Entry(
            btn_frame, textvariable=self._commit_msg_var)
        self._commit_msg_entry.grid(
            row=2, column=0, columnspan=2, padx=1, pady=(4, 1), sticky='ew')
        self._commit_msg_entry.insert(0, "Enter commit message...")
        self._commit_msg_entry.bind('<FocusIn>', self._on_commit_focus_in)
        self._commit_msg_entry.bind('<FocusOut>', self._on_commit_focus_out)

        # Row 3: commit
        self._btns['commit'] = ttk.Button(
            btn_frame, text="Commit", command=self.commit)
        self._btns['commit'].grid(row=3, column=0, columnspan=2, padx=1, pady=1, sticky='ew')

        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

    # ---- UI: changed-files list --------------------------------------------

    def _build_changed_files(self):
        ttk.Label(self, text="Changed Files",
                  font=("Helvetica", 10, "bold")).pack(anchor="w", padx=5, pady=(5, 0))

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        scroll = ttk.Scrollbar(frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._status_text = tk.Text(
            frame, width=36, height=8, font=("Monaco", 10),
            yscrollcommand=scroll.set, state=tk.DISABLED, cursor='hand2')
        self._status_text.pack(fill=tk.BOTH, expand=True)
        scroll.config(command=self._status_text.yview)

        self._status_text.bind('<Double-Button-1>', self._on_status_double_click)
        self._status_text.bind('<Button-2>', self._on_status_right_click)
        self._status_text.bind('<Button-3>', self._on_status_right_click)
        self._status_text.bind('<Control-Button-1>', self._on_status_right_click)

        # Context menu
        self._ctx_menu = tk.Menu(self, tearoff=0)
        self._ctx_menu.add_command(label="Rollback", command=self._rollback)
        self._ctx_menu.add_command(label="Open File", command=self._open_selected)
        self._ctx_menu.add_command(label="Show Diff", command=self._show_diff_selected)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Refresh", command=self.refresh)

    # ---- UI: commit log ----------------------------------------------------

    def _build_commit_log(self):
        ttk.Label(self, text="Commit Log",
                  font=("Helvetica", 10, "bold")).pack(anchor="w", padx=5, pady=(5, 0))

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        scroll = ttk.Scrollbar(frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._log_text = tk.Text(
            frame, width=36, height=6, font=("Monaco", 10),
            yscrollcommand=scroll.set, state=tk.DISABLED)
        self._log_text.pack(fill=tk.BOTH, expand=True)
        scroll.config(command=self._log_text.yview)

    # ---- Public API --------------------------------------------------------

    def refresh(self):
        """Full refresh: branch, status, log, and button visibility."""
        folder = self.current_folder
        if not folder:
            self._branch_label.config(text="⎇ no repo", cursor='')
            self._push_btn.config(state=tk.DISABLED)
            self._pull_btn.config(state=tk.DISABLED)
            self._set_text(self._status_text, "Open a folder to see git status.")
            self._set_text(self._log_text, "")
            self._update_buttons()
            return

        has_git = os.path.isdir(os.path.join(folder, '.git'))
        if not has_git:
            self._branch_label.config(text="⎇ no repo", cursor='')
            self._push_btn.config(state=tk.DISABLED)
            self._pull_btn.config(state=tk.DISABLED)
        else:
            _, branch, _ = self._run_git(['branch', '--show-current'])
            branch = branch.strip() if branch else 'unknown'
            self._branch_label.config(text=f"⎇ {branch}", cursor='hand2')
            self._push_btn.config(state=tk.NORMAL)
            self._pull_btn.config(state=tk.NORMAL)

        # Changed files
        _, status, _ = self._run_git(['status', '--short'])
        self._set_text(self._status_text,
                       status if status.strip() else "Working tree clean")

        # Commit log
        self._refresh_commit_log()
        self._update_buttons()

    def _set_text(self, widget, content):
        widget.config(state=tk.NORMAL)
        widget.delete(1.0, tk.END)
        if content:
            widget.insert(tk.END, content)
        widget.config(state=tk.DISABLED)

    def _refresh_commit_log(self):
        if not self.current_folder:
            return
        rc, stdout, stderr = self._run_git(
            ['log', '--oneline', '--graph', '--all', '-20'])
        self._set_text(self._log_text,
                       f"Error: {stderr}" if rc != 0 else
                       stdout if stdout.strip() else "No commits yet")

    # ---- Button visibility -------------------------------------------------

    def _update_buttons(self):
        folder = self.current_folder
        has_git = (folder and os.path.isdir(os.path.join(folder, '.git')))

        # Clone: only when no folder
        if folder:
            self._btns['clone'].grid_remove()
        else:
            self._btns['clone'].grid()

        # Init: folder open but no .git
        if folder and not has_git:
            self._btns['init'].grid()
        else:
            self._btns['init'].grid_remove()

        # Remote
        if folder:
            self._btns['remote'].grid()
        else:
            self._btns['remote'].grid_remove()

        # Commit msg + button
        if folder and has_git:
            self._commit_msg_entry.grid()
            self._btns['commit'].grid()
        else:
            self._commit_msg_entry.grid_remove()
            self._btns['commit'].grid_remove()

    # ---- Commit message placeholder ----------------------------------------

    def _on_commit_focus_in(self, _event):
        if self._commit_msg_var.get() == "Enter commit message...":
            self._commit_msg_var.set("")

    def _on_commit_focus_out(self, _event):
        if not self._commit_msg_var.get().strip():
            self._commit_msg_var.set("Enter commit message...")

    # ---- Status text interaction -------------------------------------------

    def _parse_status_line(self, event):
        if not self.current_folder:
            return None, None
        idx = self._status_text.index(f'@{event.x},{event.y}')
        line = self._status_text.get(f'{idx} linestart', f'{idx} lineend')
        if not line.strip() or len(line) <= 3 or '->' in line:
            return None, None
        rel = line[3:].strip()
        return rel, os.path.join(self.current_folder, rel)

    def _on_status_double_click(self, event):
        rel, _ = self._parse_status_line(event)
        if rel:
            self._show_diff_for(rel)

    def _on_status_right_click(self, event):
        info = self._parse_status_line(event)
        self._right_clicked_file = info
        try:
            self._ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._ctx_menu.grab_release()

    # ---- Context menu actions ----------------------------------------------

    def _rollback(self):
        info = self._right_clicked_file
        if not info or not info[0] or not info[1]:
            return
        rel, abs_path = info
        name = os.path.basename(rel)
        if not messagebox.askyesno(
            "Rollback Changes",
            f"Discard all changes to '{name}'?\n\nThis cannot be undone.",
            parent=self,
        ):
            return
        rc, _, stderr = self._run_git(['checkout', '--', rel])
        if rc == 0:
            self._cb('set_status', f"Rolled back: {name}")
            self.refresh()
            # Reload file in open tab
            self._cb('reload_file', abs_path)
            # Close any diff tab
            self._cb('close_diff_tab', name)
        else:
            self._cb('set_status', f"Rollback failed: {stderr.strip()}")

    def _open_selected(self):
        info = self._right_clicked_file
        if info and info[0] and os.path.isfile(info[1]):
            self._cb('open_file_in_tab', info[1])

    def _show_diff_selected(self):
        info = self._right_clicked_file
        if info and info[0]:
            self._show_diff_for(info[0])

    def _show_diff_for(self, rel_path):
        _, diff, _ = self._run_git(['diff', '--', rel_path])
        if not diff.strip():
            _, diff, _ = self._run_git(['diff', '--cached', '--', rel_path])
        title = f'Diff: {os.path.basename(rel_path)}'
        content = diff if diff.strip() else '(no changes)'
        self._cb('open_diff_tab', title, content)

    # ---- Git operations ----------------------------------------------------

    def clone(self):
        dlg = tk.Toplevel(self)
        dlg.title("Clone Repository")
        dlg.geometry("500x200")
        dlg.transient(self)
        dlg.grab_set()

        ttk.Label(dlg, text="Repository URL:").pack(
            anchor="w", padx=10, pady=(10, 5))
        url_var = tk.StringVar()
        url_entry = ttk.Entry(dlg, textvariable=url_var)
        url_entry.pack(fill=tk.X, padx=10, pady=5)
        url_entry.focus()

        ttk.Label(dlg, text="Destination Directory:").pack(
            anchor="w", padx=10, pady=(10, 5))
        dest_frame = ttk.Frame(dlg)
        dest_frame.pack(fill=tk.X, padx=10, pady=5)
        dest_var = tk.StringVar(value=os.path.expanduser('~'))
        dest_entry = ttk.Entry(dest_frame, textvariable=dest_var)
        dest_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dest_frame, text="Browse...",
                   command=lambda: self._browse_dest(dest_var)).pack(
            side=tk.LEFT, padx=5)

        def _do():
            url = url_var.get().strip()
            dest = dest_var.get().strip()
            if not url:
                messagebox.showwarning("Missing URL", "Please enter repository URL", parent=dlg)
                return
            if not dest:
                messagebox.showwarning("Missing Destination",
                                       "Please select destination directory", parent=dlg)
                return
            try:
                result = subprocess.run(
                    ['git', '-c', 'core.quotePath=false', 'clone', url, dest],
                    capture_output=True, encoding='utf-8', errors='replace',
                    timeout=120)
                if result.returncode == 0:
                    self._cb('set_status', "Repository cloned successfully")
                    dlg.destroy()
                    messagebox.showinfo("Success", "Repository cloned successfully", parent=self)
                    self._cb('folder_opened', dest)
                    self.refresh()
                else:
                    err = result.stderr or result.stdout
                    messagebox.showerror("Error", f"Failed to clone repository:\n{err}", parent=dlg)
            except FileNotFoundError:
                messagebox.showerror("Error", "Git not found. Please install git.", parent=dlg)
            except subprocess.TimeoutExpired:
                messagebox.showerror("Error", "Clone operation timed out", parent=dlg)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to clone:\n{str(e)}", parent=dlg)

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill=tk.X, padx=10, pady=(10, 10))
        ttk.Button(btn_frame, text="Clone", command=_do).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(
            side=tk.RIGHT, padx=5)
        url_entry.bind("<Return>", lambda _: _do())

    @staticmethod
    def _browse_dest(var):
        d = filedialog.askdirectory(title="Select Destination", initialdir=var.get())
        if d:
            var.set(d)

    def init_repo(self):
        folder = self.current_folder
        if not folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self)
            return
        if not messagebox.askyesno(
            "Init Git Repository", f"Initialize git repository in:\n{folder}?", parent=self):
            return
        rc, _, stderr = self._run_git(['init'])
        if rc == 0:
            self._cb('set_status', "Git repository initialized")
            messagebox.showinfo("Success", "Git repository initialized successfully", parent=self)
            self._update_buttons()
            self.refresh()
        else:
            self._cb('set_status', f"Git init failed: {stderr}")
            messagebox.showerror("Error", f"Failed to initialize git:\n{stderr}", parent=self)

    def set_remote(self):
        folder = self.current_folder
        if not folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self)
            return
        rc, _, _ = self._run_git(['status'])
        if rc != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self)
            return
        rc, stdout, _ = self._run_git(['remote', '-v'])
        current = f"\nCurrent remotes:\n{stdout}" if rc == 0 and stdout.strip() else ""
        url = simpledialog.askstring(
            "Set Git Remote",
            f"Enter remote URL:{current}\n\nExamples:\nhttps://github.com/username/repo.git\ngit@github.com:username/repo.git",
            parent=self)
        if not url:
            return
        rc, _, _ = self._run_git(['remote', 'get-url', 'origin'])
        if rc == 0:
            rc, _, stderr = self._run_git(['remote', 'set-url', 'origin', url])
        else:
            rc, _, stderr = self._run_git(['remote', 'add', 'origin', url])
        if rc == 0:
            self._cb('set_status', "Remote set successfully")
            messagebox.showinfo("Success", "Remote URL set successfully", parent=self)
        else:
            self._cb('set_status', f"Failed to set remote: {stderr}")
            messagebox.showerror("Error", f"Failed to set remote:\n{stderr}", parent=self)

    def commit(self):
        folder = self.current_folder
        if not folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self)
            return
        rc, _, stderr = self._run_git(['status'])
        if rc != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self)
            return
        msg = self._commit_msg_var.get().strip()
        if not msg or msg == "Enter commit message...":
            msg = simpledialog.askstring("Git Commit", "Enter commit message:", parent=self)
        if not msg:
            return
        rc, _, stderr = self._run_git(['add', '.'])
        if rc != 0:
            messagebox.showerror("Error", f"Failed to stage files:\n{stderr}", parent=self)
            return
        rc, _, stderr = self._run_git(['commit', '-m', msg])
        if rc == 0:
            self._cb('set_status', f"Committed: {msg}")
            self._commit_msg_var.set("")
            self._commit_msg_entry.insert(0, "Enter commit message...")
            messagebox.showinfo("Success", f"Changes committed:\n{msg}", parent=self)
            self.refresh()
        else:
            self._cb('set_status', f"Commit failed: {stderr}")
            messagebox.showerror("Error", f"Failed to commit:\n{stderr}", parent=self)

    def pull(self):
        folder = self.current_folder
        if not folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self)
            return
        rc, _, _ = self._run_git(['status'])
        if rc != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self)
            return
        if not messagebox.askyesno("Git Pull", "Pull changes from remote?", parent=self):
            return
        rc, stdout, stderr = self._run_git(['pull'])
        if rc == 0:
            self._cb('set_status', "Pull successful")
            messagebox.showinfo("Success", f"Pull successful:\n{stdout}", parent=self)
            self._cb('refresh_file_tree')
            self.refresh()
        else:
            self._cb('set_status', f"Pull failed: {stderr}")
            messagebox.showerror("Error", f"Failed to pull:\n{stderr}", parent=self)

    def push(self):
        folder = self.current_folder
        if not folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self)
            return
        rc, _, _ = self._run_git(['status'])
        if rc != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self)
            return
        rc, _, _ = self._run_git(['remote', 'get-url', 'origin'])
        if rc != 0:
            messagebox.showwarning("No Remote", "Please set remote URL first", parent=self)
            return
        rc, branch_out, _ = self._run_git(['branch', '--show-current'])
        current_branch = branch_out.strip() if rc == 0 else 'main'
        rc, _, _ = self._run_git(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])
        if not messagebox.askyesno("Git Push", "Push changes to remote?", parent=self):
            return
        if rc != 0:
            rc, stdout, stderr = self._run_git(
                ['push', '--set-upstream', 'origin', current_branch])
        else:
            rc, stdout, stderr = self._run_git(['push'])
        if rc == 0:
            self._cb('set_status', "Push successful")
            messagebox.showinfo("Success", f"Push successful:\n{stdout}", parent=self)
        else:
            self._cb('set_status', f"Push failed: {stderr}")
            messagebox.showerror("Error", f"Failed to push:\n{stderr}", parent=self)

    # ---- Branch management -------------------------------------------------

    def _show_branch_popup(self, _event=None):
        folder = self.current_folder
        if not folder or not os.path.isdir(os.path.join(folder, '.git')):
            return

        _, current, _ = self._run_git(['branch', '--show-current'])
        current = current.strip()
        _, output, _ = self._run_git(['branch'])
        branches = [b.lstrip('* ').strip() for b in output.splitlines() if b.strip()]

        popup = tk.Toplevel(self)
        popup.title("Branches")
        popup.geometry("300x350")
        popup.transient(self)
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

        for i, b in enumerate(branches):
            branch_list.insert(tk.END, b)
            if b == current:
                branch_list.selection_set(i)
                branch_list.see(i)
                branch_list.itemconfig(i, {'bg': '#d0e0f0'})

        def _switch():
            sel = branch_list.curselection()
            if not sel:
                return
            target = branch_list.get(sel[0])
            if target == current:
                return
            rc, _, stderr = self._run_git(['checkout', target])
            if rc != 0:
                messagebox.showerror(
                    "Switch Branch Failed",
                    f"Could not switch to '{target}':\n{stderr}", parent=self)
                return
            self._cb('set_status', f"Switched to branch: {target}")
            popup.destroy()
            self.refresh()
            self._cb('refresh_file_tree')

        def _create():
            new_name = simpledialog.askstring(
                "New Branch", "Enter new branch name:", parent=popup)
            if not new_name:
                return
            rc, _, stderr = self._run_git(['checkout', '-b', new_name])
            if rc != 0:
                messagebox.showerror(
                    "Create Branch Failed",
                    f"Could not create branch '{new_name}':\n{stderr}", parent=self)
                return
            self._cb('set_status', f"Created and switched to branch: {new_name}")
            popup.destroy()
            self.refresh()
            self._cb('refresh_file_tree')

        branch_list.bind('<Double-Button-1>', lambda e: _switch())

        btn_frame = ttk.Frame(popup)
        btn_frame.pack(fill=tk.X, padx=10, pady=(5, 10))
        ttk.Button(btn_frame, text="Switch", command=_switch).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="New", command=_create).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Close", command=popup.destroy).pack(side=tk.RIGHT)
