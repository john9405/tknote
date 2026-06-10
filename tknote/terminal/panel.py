"""Terminal panel — Python Shell and system Terminal in a bottom panel.

The Python Shell uses idlelib's iomark pattern: a single Text widget with
separate read-only output and editable input regions.
"""

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import tkinter as tk
from tkinter import ttk

from .shell import PythonShell


class TerminalPanel(ttk.Frame):
    """Bottom panel with Python Shell and system Terminal tabs."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._active_tab = 'python'
        self._close_callback = None

        # System shell state
        self._shell_proc = None
        self._shell_master_fd = None
        self._reader_thread = None
        self._reader_stop = threading.Event()

        self._build_ui()
        self._start_shell()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        self.configure(height=200)

        # Tab bar
        self._tab_bar = ttk.Frame(self)
        self._tab_bar.pack(fill=tk.X, side=tk.TOP)

        self._python_btn = ttk.Button(
            self._tab_bar, text='Python', width=10,
            command=lambda: self._switch_tab('python'))
        self._python_btn.pack(side=tk.LEFT, padx=(4, 1), pady=(2, 0))

        self._shell_btn = ttk.Button(
            self._tab_bar, text='Terminal', width=10,
            command=lambda: self._switch_tab('shell'))
        self._shell_btn.pack(side=tk.LEFT, padx=1, pady=(2, 0))

        # Close button
        close_btn = ttk.Button(
            self._tab_bar, text='×', width=2,
            command=self._on_close)
        close_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=(2, 0))

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, side=tk.TOP)

        # Content area
        self._content = ttk.Frame(self)
        self._content.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        # ── Python Shell tab (new idlelib-style) ──
        self._python_shell = PythonShell(self._content)

        # ── System Shell tab (unchanged) ──
        self._shell_frame = ttk.Frame(self._content)
        self._shell_frame.grid_rowconfigure(0, weight=1)
        self._shell_frame.grid_columnconfigure(1, weight=1)

        self._shell_output = tk.Text(
            self._shell_frame, state=tk.DISABLED,
            bg='#1e1e1e', fg='#d4d4d4', insertbackground='white',
            font=('Monaco', 11), wrap=tk.WORD)
        sh_scroll = ttk.Scrollbar(self._shell_frame,
                                  command=self._shell_output.yview)
        self._shell_output.configure(yscrollcommand=sh_scroll.set)
        self._shell_output.grid(row=0, column=0, columnspan=2, sticky='nsew')
        sh_scroll.grid(row=0, column=2, sticky='ns')
        self._shell_output.bind('<Key>', lambda e: 'break')

        ttk.Label(self._shell_frame, text='$',
                  font=('Monaco', 11, 'bold'),
                  foreground='#569cd6').grid(
            row=1, column=0, sticky='w', padx=(4, 2), pady=(2, 4))
        self._shell_input = ttk.Entry(self._shell_frame, font=('Monaco', 11))
        self._shell_input.grid(row=1, column=1, sticky='ew', padx=(0, 4), pady=(2, 4))
        self._shell_input.bind('<Return>', lambda e: self._send_shell_input())

        # Show Python tab by default
        self._switch_tab('python')

    # ── Tab switching ─────────────────────────────────────────────────────

    def set_close_callback(self, callback):
        """Called when the close (×) button is clicked."""
        self._close_callback = callback

    def _on_close(self):
        if self._close_callback:
            self._close_callback()

    def _switch_tab(self, tab):
        self._active_tab = tab
        if tab == 'python':
            self._shell_frame.pack_forget()
            self._python_shell.pack(fill=tk.BOTH, expand=True)
            self._python_shell.focus_input()
        else:
            self._python_shell.pack_forget()
            self._shell_frame.pack(fill=tk.BOTH, expand=True)
            self._shell_input.focus_set()

    # ── System Shell / Terminal ───────────────────────────────────────────

    def _start_shell(self):
        shell = os.environ.get('SHELL', '/bin/zsh')
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError:
            self._append_shell_output('[Error: could not open PTY]\n')
            return

        # Set terminal size
        try:
            winsize = struct.pack('HHHH', 24, 80, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

        try:
            self._shell_proc = subprocess.Popen(
                [shell, '-i'],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid,
                env={**os.environ, 'TERM': 'xterm-256color'},
                close_fds=True,
            )
        except Exception as e:
            self._append_shell_output(f'[Error: {e}]\n')
            os.close(master_fd)
            os.close(slave_fd)
            return

        os.close(slave_fd)
        self._shell_master_fd = master_fd

        # Non-blocking reads
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self._reader_stop.clear()
        self._reader_thread = threading.Thread(
            target=self._read_pty_output, daemon=True)
        self._reader_thread.start()

    def _append_shell_output(self, text):
        try:
            self._shell_output.configure(state=tk.NORMAL)
            self._shell_output.insert(tk.END, text)
            self._shell_output.see(tk.END)
            self._shell_output.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _read_pty_output(self):
        fd = self._shell_master_fd
        while not self._reader_stop.is_set() and fd is not None:
            try:
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    data = os.read(fd, 4096)
                    if not data:
                        break
                    decoded = data.decode('utf-8', errors='replace')
                    self.after(0, lambda d=decoded: self._append_shell_output(d))
            except OSError:
                break
        # Subprocess exited
        if not self._reader_stop.is_set():
            self.after(0, self._handle_shell_exit)

    def _send_shell_input(self):
        text = self._shell_input.get()
        self._shell_input.delete(0, tk.END)
        fd = self._shell_master_fd
        if fd is not None and text:
            try:
                os.write(fd, (text + '\n').encode('utf-8'))
            except OSError:
                self._append_shell_output('[Error: cannot send input]\n')

    def _handle_shell_exit(self):
        self._append_shell_output('\n[Process exited — restarting...]\n')
        if self._shell_master_fd is not None:
            try:
                os.close(self._shell_master_fd)
            except OSError:
                pass
            self._shell_master_fd = None
        self._shell_proc = None
        # Auto-restart after a brief delay
        self.after(500, self._start_shell)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def focus_input(self):
        """Focus the input field of the active tab."""
        if self._active_tab == 'python':
            self._python_shell.focus_input()
        else:
            self._shell_input.focus_set()

    def cleanup(self):
        """Kill subprocess, stop reader thread, clean up shell."""
        self._reader_stop.set()
        # Wake up select() by closing the fd
        fd = self._shell_master_fd
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            self._shell_master_fd = None
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        if self._shell_proc and self._shell_proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._shell_proc.pid), signal.SIGTERM)
                self._shell_proc.wait(timeout=2)
            except Exception:
                try:
                    self._shell_proc.kill()
                except Exception:
                    pass
        # Clean up Python shell
        self._python_shell.cleanup()
