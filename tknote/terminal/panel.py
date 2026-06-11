"""Terminal panel — Python Shell and system Terminal in a bottom panel.

The Python Shell uses idlelib's iomark pattern: a single Text widget with
separate read-only output and editable input regions.
"""

import fcntl
import os
import pty
import re
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

# ── ANSI escape sequence stripping ────────────────────────────────────────────

_ANSI_RE = re.compile(
    r'\x1b\[[0-9;<=>?]*[ -/]*[@-~]' # CSI: cursor/color/mode (incl. ?2004h etc.)
    r'|\x1b\][^\x07\x1b]*\x07'      # OSC: window title, etc.
    r'|\x1b\][^\x07\x1b]*\x1b\\'    # OSC (ST terminator)
    r'|\x1b[PX^_].*?\x1b\\'         # DCS/SOS/PM/APC strings
    r'|\x1b[^\x1b]'                 # any other single-char escape
)

# \x08 (backspace) intentionally kept — _append_shell_output interprets it
_CTRL_RE = re.compile(r'[\x00-\x07\x0b\x0c\x0e-\x1f]')


def _clean_ansi(text):
    """Strip ANSI escape sequences and stray control chars from terminal output."""
    text = _ANSI_RE.sub('', text)
    # Collapse repeated carriage returns / newlines
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = _CTRL_RE.sub('', text)
    return text


# ── Key → PTY byte mapping for terminal input ────────────────────────────────

_KEY_TO_PTY = {
    'Return':    b'\r',
    'BackSpace': b'\x7f',
    'Tab':       b'\t',
    'Escape':    b'\x1b',
    'Up':        b'\x1b[A',
    'Down':      b'\x1b[B',
    'Right':     b'\x1b[C',
    'Left':      b'\x1b[D',
    'Home':      b'\x1b[H',
    'End':       b'\x1b[F',
    'Delete':    b'\x1b[3~',
    'Prior':     b'\x1b[5~',   # Page Up
    'Next':      b'\x1b[6~',   # Page Down
}


class TerminalPanel(ttk.Frame):
    """Bottom panel with Python Shell and system Terminal tabs."""

    def __init__(self, parent, cwd=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._active_tab = 'python'
        self._close_callback = None
        self._cwd = cwd or os.getcwd()

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

        # ── System Shell tab (inline interactive terminal) ──
        self._shell_frame = ttk.Frame(self._content)
        self._shell_frame.grid_rowconfigure(0, weight=1)
        self._shell_frame.grid_columnconfigure(0, weight=1)

        self._shell_output = tk.Text(
            self._shell_frame,
            bg='#ffffff', fg='#1e1e1e', insertbackground='#1e1e1e',
            font=('Monaco', 11), wrap=tk.WORD)
        sh_scroll = ttk.Scrollbar(self._shell_frame,
                                  command=self._shell_output.yview)
        self._shell_output.configure(yscrollcommand=sh_scroll.set)
        self._shell_output.grid(row=0, column=0, sticky='nsew')
        sh_scroll.grid(row=0, column=1, sticky='ns')

        # Inline key handling — keystrokes go directly to the PTY
        self._shell_output.bind('<Key>', self._handle_shell_key)
        # Paste: send clipboard to PTY instead of inserting locally
        self._shell_output.bind('<Command-v>', self._handle_shell_paste)
        self._shell_output.bind('<Control-v>', self._handle_shell_paste)
        # Copy / Select All pass through to Tk defaults
        self._shell_output.bind('<Command-c>', lambda e: None)
        self._shell_output.bind('<Command-a>', lambda e: None)
        self._shell_output.bind('<Command-x>', lambda e: None)

        # Right-click context menu with Copy / Paste
        self._shell_context_menu = tk.Menu(self._shell_frame, tearoff=0)
        self._shell_context_menu.add_command(
            label='Copy', command=self._shell_copy,
            accelerator='Cmd+C')
        self._shell_context_menu.add_command(
            label='Paste', command=self._shell_paste_to_terminal,
            accelerator='Cmd+V')
        self._shell_output.bind('<Button-2>', self._shell_right_click)
        self._shell_output.bind('<Button-3>', self._shell_right_click)
        self._shell_output.bind('<Control-Button-1>', self._shell_right_click)

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
            self._shell_output.focus_set()

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
                cwd=self._cwd,
                preexec_fn=os.setsid,
                env={**os.environ, 'TERM': 'dumb'},
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
        """Append PTY output to the terminal widget, interpreting backspaces."""
        try:
            w = self._shell_output
            w.mark_set("insert", "end-1c")
            # Process \b (backspace): delete the character before the cursor.
            # This correctly handles the classic '^H ^H' erase sequence that
            # shells emit under TERM=dumb for backspace line-editing.
            if '\b' in text:
                start = 0
                for i, ch in enumerate(text):
                    if ch == '\b':
                        if start < i:
                            w.insert("insert", text[start:i])
                        try:
                            w.delete("insert-1c", "insert")
                        except tk.TclError:
                            pass
                        start = i + 1
                if start < len(text):
                    w.insert("insert", text[start:])
            else:
                w.insert("insert", text)
            w.see("end-1c")
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
                    cleaned = _clean_ansi(decoded)
                    if cleaned:
                        self.after(0, lambda d=cleaned: self._append_shell_output(d))
            except OSError:
                break
        # Subprocess exited
        if not self._reader_stop.is_set():
            self.after(0, self._handle_shell_exit)

    # ── Inline key handling ─────────────────────────────────────────────────

    def _write_pty(self, data):
        """Write raw bytes to the PTY master fd.  Silently no-op if fd is dead."""
        fd = self._shell_master_fd
        if fd is not None:
            try:
                os.write(fd, data)
            except OSError:
                pass

    def _handle_shell_key(self, event):
        """Handle keypress in the terminal Text widget — send to PTY."""
        fd = self._shell_master_fd
        if fd is None:
            return "break"

        # Always force cursor to the trailing edge (user can't edit history)
        try:
            self._shell_output.mark_set("insert", "end-1c")
            self._shell_output.see("insert")
        except tk.TclError:
            pass

        keysym = event.keysym
        state  = event.state

        # ── macOS Command-key shortcuts pass through ──
        if state & 0x10:  # Command modifier
            if keysym.lower() in ('c', 'a', 'x'):
                return None  # let Tk handle Copy / Select All / Cut
            if keysym.lower() == 'v':
                return self._handle_shell_paste(event)
            return "break"

        # ── Control-key combinations (Ctrl+A … Ctrl+Z → ASCII 0x01 … 0x1A) ──
        if state & 0x4 and len(keysym) == 1:
            c = keysym.lower()
            if c == 'c' and self._has_selection():
                # Ctrl+C with selection → copy (like modern terminal emulators)
                self._shell_copy()
                return "break"
            if 'a' <= c <= 'z':
                self._write_pty((ord(c) - ord('a') + 1).to_bytes(1, 'big'))
                return "break"

        # ── Known special keys (arrows, home, end, etc.) ──
        pty_bytes = _KEY_TO_PTY.get(keysym)
        if pty_bytes is not None:
            self._write_pty(pty_bytes)
            return "break"

        # ── Printable characters ──
        char = event.char
        if char and len(char) == 1 and ord(char) >= 0x20:
            self._write_pty(char.encode('utf-8'))
            return "break"

        return "break"

    def _handle_shell_paste(self, event=None):
        """Paste: send clipboard content directly to the PTY."""
        try:
            clip = self._shell_output.clipboard_get()
        except tk.TclError:
            return "break"
        if clip:
            self._write_pty(clip.encode('utf-8'))
        return "break"

    def _has_selection(self):
        """Return True if text is currently selected in the terminal."""
        try:
            return bool(self._shell_output.get(tk.SEL_FIRST, tk.SEL_LAST))
        except tk.TclError:
            return False

    def _shell_copy(self):
        """Copy selected text to clipboard (context menu / fallback)."""
        try:
            sel = self._shell_output.get(tk.SEL_FIRST, tk.SEL_LAST)
            if sel:
                self._shell_output.clipboard_clear()
                self._shell_output.clipboard_append(sel)
        except tk.TclError:
            pass

    def _shell_paste_to_terminal(self):
        """Paste clipboard through PTY (context menu entry)."""
        self._handle_shell_paste()

    def _shell_right_click(self, event):
        """Show context menu on right-click in the terminal."""
        # Don't disrupt existing selection
        self._shell_context_menu.entryconfigure(
            0, state=tk.NORMAL if self._has_selection() else tk.DISABLED)
        try:
            self._shell_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._shell_context_menu.grab_release()

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

    # ── Directory tracking ──────────────────────────────────────────────────

    def cd_to(self, path):
        """Change the terminal's working directory (updates cwd and sends cd)."""
        if not path or not os.path.isdir(path):
            return
        self._cwd = path
        fd = self._shell_master_fd
        if fd is not None:
            try:
                # Quote path for shell safety
                escaped = path.replace("'", "'\\''")
                os.write(fd, f"cd '{escaped}'\n".encode('utf-8'))
            except OSError:
                pass

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def focus_input(self):
        """Focus the input field of the active tab."""
        if self._active_tab == 'python':
            self._python_shell.focus_input()
        else:
            self._shell_output.focus_set()
            try:
                self._shell_output.mark_set("insert", "end-1c")
            except tk.TclError:
                pass

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
