"""SystemTerminal — standalone inline interactive system terminal."""

import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import threading
import tkinter as tk
from tkinter import ttk

# ── ANSI escape sequence stripping ────────────────────────────────────────────

_ANSI_RE = re.compile(
    r'\x1b\[[0-9;<=>?]*[ -/]*[@-~]'  # CSI: cursor/color/mode (incl. ?2004h etc.)
    r'|\x1b\][^\x07\x1b]*\x07'       # OSC: window title, etc.
    r'|\x1b\][^\x07\x1b]*\x1b\\'     # OSC (ST terminator)
    r'|\x1b[PX^_].*?\x1b\\'          # DCS/SOS/PM/APC strings
    r'|\x1b[^\x1b]'                  # any other single-char escape
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


class SystemTerminal(ttk.Frame):
    """Standalone inline interactive system terminal (PTY-based)."""

    def __init__(self, parent, cwd=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._close_callback = None
        self._cwd = cwd or os.getcwd()
        self._venv_path = None

        # Shell state
        self._shell_proc = None
        self._shell_master_fd = None
        self._reader_thread = None
        self._reader_stop = threading.Event()

        self._build_ui()
        self._start_shell()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.configure(height=200)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header bar
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky='ew')

        ttk.Label(header, text='Terminal', font=('Helvetica', 10, 'bold')).pack(
            side=tk.LEFT, padx=(4, 0), pady=(2, 0))

        close_btn = ttk.Button(
            header, text='×', width=2, command=self._on_close)
        close_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=(2, 0))

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).grid(
            row=1, column=0, sticky='ew')

        # Terminal text area
        term_frame = ttk.Frame(self)
        term_frame.grid(row=2, column=0, sticky='nsew')
        self.grid_rowconfigure(2, weight=1)
        term_frame.grid_rowconfigure(0, weight=1)
        term_frame.grid_columnconfigure(0, weight=1)

        self._shell_output = tk.Text(
            term_frame,
            bg='#ffffff', fg='#1e1e1e', insertbackground='#1e1e1e',
            font=('Monaco', 11), wrap=tk.WORD)
        sh_scroll = ttk.Scrollbar(
            term_frame, command=self._shell_output.yview)
        self._shell_output.configure(yscrollcommand=sh_scroll.set)
        self._shell_output.grid(row=0, column=0, sticky='nsew')
        sh_scroll.grid(row=0, column=1, sticky='ns')

        # Key bindings
        self._shell_output.bind('<Key>', self._handle_shell_key)
        self._shell_output.bind('<Command-v>', self._handle_shell_paste)
        self._shell_output.bind('<Control-v>', self._handle_shell_paste)
        self._shell_output.bind('<Command-c>', lambda e: None)
        self._shell_output.bind('<Command-a>', lambda e: None)
        self._shell_output.bind('<Command-x>', lambda e: None)

        # Right-click context menu
        self._shell_context_menu = tk.Menu(term_frame, tearoff=0)
        self._shell_context_menu.add_command(
            label='Copy', command=self._shell_copy, accelerator='Cmd+C')
        self._shell_context_menu.add_command(
            label='Paste', command=self._shell_paste_to_terminal,
            accelerator='Cmd+V')
        self._shell_output.bind('<Button-2>', self._shell_right_click)
        self._shell_output.bind('<Button-3>', self._shell_right_click)
        self._shell_output.bind('<Control-Button-1>', self._shell_right_click)

    # ── Close button ──────────────────────────────────────────────────────────

    def set_close_callback(self, callback):
        """Called when the close (×) button is clicked."""
        self._close_callback = callback

    def _on_close(self):
        if self._close_callback:
            self._close_callback()

    # ── PTY / Shell process ───────────────────────────────────────────────────

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
                env=self._build_shell_env(),
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

        # If a venv is active, source its activate script once the shell is ready
        if self._venv_path:
            activate_script = os.path.join(self._venv_path, 'bin', 'activate')
            if os.path.isfile(activate_script):
                self.after(400, lambda: self._write_pty(
                    f"source '{activate_script}'\n".encode('utf-8')))

    def _append_shell_output(self, text):
        """Append PTY output to the terminal widget, interpreting backspaces."""
        try:
            w = self._shell_output
            w.mark_set("insert", "end-1c")
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

    # ── Key handling ──────────────────────────────────────────────────────────

    def _write_pty(self, data):
        """Write raw bytes to the PTY master fd."""
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

        try:
            self._shell_output.mark_set("insert", "end-1c")
            self._shell_output.see("insert")
        except tk.TclError:
            pass

        keysym = event.keysym
        state = event.state

        # macOS Command-key shortcuts
        if state & 0x10:
            if keysym.lower() in ('c', 'a', 'x'):
                return None
            if keysym.lower() == 'v':
                return self._handle_shell_paste(event)
            return "break"

        # Control-key combinations
        if state & 0x4 and len(keysym) == 1:
            c = keysym.lower()
            if c == 'c' and self._has_selection():
                self._shell_copy()
                return "break"
            if 'a' <= c <= 'z':
                self._write_pty((ord(c) - ord('a') + 1).to_bytes(1, 'big'))
                return "break"

        # Known special keys
        pty_bytes = _KEY_TO_PTY.get(keysym)
        if pty_bytes is not None:
            self._write_pty(pty_bytes)
            return "break"

        # Printable characters
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
        """Copy selected text to clipboard."""
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
        """Show context menu on right-click."""
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
        self.after(500, self._start_shell)

    # ── Venv integration ──────────────────────────────────────────────────────

    def set_venv(self, venv_path: str | None):
        """Set the active venv and restart the shell if running."""
        self._venv_path = venv_path
        if self._shell_proc and self._shell_proc.poll() is None:
            self._stop_shell()
            self._start_shell()

    def _build_shell_env(self) -> dict:
        """Build the environment dict for the shell subprocess."""
        env = {**os.environ, 'TERM': 'dumb'}
        if self._venv_path:
            bin_dir = os.path.join(self._venv_path, 'bin')
            if os.path.isdir(bin_dir):
                env['VIRTUAL_ENV'] = self._venv_path
                env['PATH'] = f"{bin_dir}:{env.get('PATH', '')}"
        return env

    def _stop_shell(self):
        """Kill the shell subprocess and clean up PTY resources."""
        self._reader_stop.set()
        fd = self._shell_master_fd
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            self._shell_master_fd = None
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
        if self._shell_proc and self._shell_proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._shell_proc.pid), signal.SIGTERM)
                self._shell_proc.wait(timeout=1)
            except Exception:
                try:
                    self._shell_proc.kill()
                except Exception:
                    pass
        self._shell_proc = None
        self._reader_stop.clear()

    # ── Directory tracking ────────────────────────────────────────────────────

    def cd_to(self, path):
        """Change the terminal's working directory."""
        if not path or not os.path.isdir(path):
            return
        self._cwd = path
        fd = self._shell_master_fd
        if fd is not None:
            try:
                escaped = path.replace("'", "'\\''")
                os.write(fd, f"cd '{escaped}'\n".encode('utf-8'))
            except OSError:
                pass

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def focus_input(self):
        """Focus the terminal text widget."""
        self._shell_output.focus_set()
        try:
            self._shell_output.mark_set("insert", "end-1c")
        except tk.TclError:
            pass

    def cleanup(self):
        """Kill subprocess, stop reader thread, clean up."""
        self._stop_shell()
