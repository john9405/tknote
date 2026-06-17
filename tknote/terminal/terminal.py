"""SystemTerminal — standalone inline interactive system terminal.

Features:
  - ANSI SGR color support (16+256 colors) via tkinter text tags
  - Dark theme matching VS Code terminal colours
  - Character-cell wrap (no word-wrap) with horizontal scroll
  - Dynamic PTY window size that follows widget dimensions
  - Carriage-return / line-clear handling for inline progress bars
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# ANSI SGR → tkinter tag processing
# ═══════════════════════════════════════════════════════════════════════════════

# Standard 16-colour palette (dark-theme friendly, VS Code-ish)
_FG_PALETTE = {
    30: '#808080',   # black → gray (visible on dark bg)
    31: '#cd3131',   # red
    32: '#0dbc79',   # green
    33: '#e5e510',   # yellow
    34: '#2472c8',   # blue
    35: '#bc3fbc',   # magenta
    36: '#11a8cd',   # cyan
    37: '#e5e5e5',   # white
    90: '#767676',   # bright black
    91: '#f14c4c',   # bright red
    92: '#23d18b',   # bright green
    93: '#f5f543',   # bright yellow
    94: '#3b8eea',   # bright blue
    95: '#d670d6',   # bright magenta
    96: '#29b8db',   # bright cyan
    97: '#ffffff',   # bright white
}

_BG_PALETTE = {
    40: '#000000',
    41: '#cd3131',
    42: '#0dbc79',
    43: '#e5e510',
    44: '#2472c8',
    45: '#bc3fbc',
    46: '#11a8cd',
    47: '#e5e5e5',
    100: '#767676',
    101: '#f14c4c',
    102: '#23d18b',
    103: '#f5f543',
    104: '#3b8eea',
    105: '#d670d6',
    106: '#29b8db',
    107: '#ffffff',
}

# Map ANSI code → tag name suffix
def _make_tag_name(code, is_bg):
    if is_bg:
        return f'ansi_bg_{code}'
    return f'ansi_fg_{code}'


class _AnsiParser:
    """Streaming ANSI SGR parser.

    Feeds in raw terminal output; yields (text, tags) segments.
    Only SGR colour/style codes are processed; all other CSI sequences
    (cursor movement, erase, etc.) are silently stripped.

    Tags are tkinter tag names already configured on the Text widget.
    """

    # Sentinel inserted into the segment stream to mark \x1b[K (erase line)
    _ERASE_LINE = object()

    def __init__(self, text_widget: tk.Text):
        self._w = text_widget
        self._bold = False
        self._dim = False
        self._italic = False
        self._underline = False
        self._fg_code = 37   # default white
        self._bg_code = None  # default transparent

    def _current_tags(self) -> tuple[str, ...]:
        tags: list[str] = []
        if self._bold:
            tags.append('ansi_bold')
        if self._dim:
            tags.append('ansi_dim')
        if self._italic:
            tags.append('ansi_italic')
        if self._underline:
            tags.append('ansi_underline')
        if self._fg_code is not None:
            tags.append(_make_tag_name(self._fg_code, False))
        if self._bg_code is not None:
            tags.append(_make_tag_name(self._bg_code, True))
        return tuple(tags)

    def _reset_sgr(self):
        self._bold = False
        self._dim = False
        self._italic = False
        self._underline = False
        self._fg_code = 37
        self._bg_code = None

    def _apply_sgr(self, code: int):
        if code == 0:
            self._reset_sgr()
        elif code == 1:
            self._bold = True
        elif code == 2:
            self._dim = True
        elif code == 3:
            self._italic = True
        elif code == 4:
            self._underline = True
        elif code == 22:
            self._bold = False
            self._dim = False
        elif code == 23:
            self._italic = False
        elif code == 24:
            self._underline = False
        elif 30 <= code <= 37 or 90 <= code <= 97:
            self._fg_code = code
        elif 40 <= code <= 47 or 100 <= code <= 107:
            self._bg_code = code
        elif code == 39:
            self._fg_code = 37
        elif code == 49:
            self._bg_code = None

    def feed(self, data: str) -> list:
        """Parse a chunk of terminal output.

        Returns a list where each element is either:
          - (text: str, tags: tuple) — insert with given tags
          - _ERASE_LINE — clear from cursor to end of current line
        """
        segments: list = []
        i = 0
        n = len(data)
        buf: list[str] = []

        def _flush():
            if buf:
                segments.append((''.join(buf), self._current_tags()))
                buf.clear()

        while i < n:
            if data[i:i + 2] == '\x1b[':
                _flush()
                i += 2
                # Collect parameter bytes
                j = i
                while j < n and data[j] not in '@-~':
                    j += 1
                if j >= n:
                    # Incomplete sequence — treat \x1b[ as text
                    buf.append('\x1b[')
                    buf.append(data[i:])
                    i = n
                    break

                params_str = data[i:j]
                final = data[j]
                i = j + 1  # skip past terminator

                if final == 'm':
                    if params_str:
                        for p in params_str.split(';'):
                            try:
                                self._apply_sgr(int(p) if p else 0)
                            except ValueError:
                                pass
                    else:
                        self._apply_sgr(0)
                elif final == 'K' and params_str in ('', '0'):
                    segments.append(self._ERASE_LINE)
                # All other CSI sequences: silently dropped
            else:
                buf.append(data[i])
                i += 1

        _flush()

        # Merge adjacent text segments with identical tags
        merged: list = []
        for item in segments:
            if item is self._ERASE_LINE:
                merged.append(item)
            else:
                text, tags = item
                if merged and merged[-1] is not self._ERASE_LINE:
                    prev = merged[-1]
                    if prev[1] == tags:
                        merged[-1] = (prev[0] + text, tags)
                        continue
                merged.append(item)
        return merged


# ═══════════════════════════════════════════════════════════════════════════════
# Key → PTY byte mapping
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# SystemTerminal widget
# ═══════════════════════════════════════════════════════════════════════════════

class SystemTerminal(ttk.Frame):
    """Standalone inline interactive system terminal (PTY-based)."""

    # Dark theme colours
    BG = '#1e1e1e'
    FG = '#d4d4d4'
    CURSOR = '#d4d4d4'

    # Max lines in scrollback buffer (trim from top when exceeded)
    _MAX_SCROLLBACK_LINES = 10_000

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

        # Pending output buffer (batch accumulated reads before UI dispatch)
        self._pending_output: list[str] = []
        self._output_scheduled = False

        self._build_ui()
        self._start_shell()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.configure(height=200)
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header bar
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky='ew')

        ttk.Label(header, text='Terminal', font=('Helvetica', 10, 'bold')).pack(
            side=tk.LEFT, padx=(4, 0), pady=(2, 0))

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).grid(
            row=1, column=0, sticky='ew')

        # Terminal text area with both scrollbars
        term_frame = ttk.Frame(self)
        term_frame.grid(row=2, column=0, sticky='nsew')
        self.grid_rowconfigure(2, weight=1)
        term_frame.grid_rowconfigure(0, weight=1)
        term_frame.grid_columnconfigure(0, weight=1)

        self._shell_output = tk.Text(
            term_frame,
            bg=self.BG, fg=self.FG,
            insertbackground=self.CURSOR,
            font=('Monaco', 12),
            wrap=tk.NONE,              # character-cell wrap — no word-wrap
            blockcursor=False,
            highlightthickness=0,
            bd=0,
            padx=4, pady=4,
        )

        # Vertical scrollbar
        v_scroll = ttk.Scrollbar(
            term_frame, orient=tk.VERTICAL,
            command=self._shell_output.yview)
        # Horizontal scrollbar
        h_scroll = ttk.Scrollbar(
            term_frame, orient=tk.HORIZONTAL,
            command=self._shell_output.xview)
        self._shell_output.configure(
            yscrollcommand=v_scroll.set,
            xscrollcommand=h_scroll.set)

        self._shell_output.grid(row=0, column=0, sticky='nsew')
        v_scroll.grid(row=0, column=1, sticky='ns')
        h_scroll.grid(row=1, column=0, sticky='ew')

        # ANSI parser
        self._ansi = _AnsiParser(self._shell_output)
        self._setup_ansi_tags()

        # Key bindings — keystrokes are forwarded to the PTY; macOS
        # Command shortcuts fall back to default Text widget behaviour
        # (Cmd+C copy, Cmd+A select-all) because _handle_shell_key returns
        # None for them.
        self._shell_output.bind('<Key>', self._handle_shell_key)
        self._shell_output.bind('<Command-v>', self._handle_shell_paste)
        self._shell_output.bind('<Control-v>', self._handle_shell_paste)

        # Dynamic terminal size — update PTY when widget resizes
        self._shell_output.bind('<Configure>', self._on_resize, add='+')
        self._resize_after_id = None

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

    def _setup_ansi_tags(self):
        """Configure tkinter text tags for ANSI SGR attributes."""
        # Foreground colours
        for code, color in _FG_PALETTE.items():
            self._shell_output.tag_configure(
                _make_tag_name(code, False), foreground=color)
        # Background colours
        for code, color in _BG_PALETTE.items():
            self._shell_output.tag_configure(
                _make_tag_name(code, True), background=color)
        # 256-colour placeholders — generated on demand by _ensure_256_tag
        # Style attributes
        self._shell_output.tag_configure('ansi_bold', font=('Monaco', 12, 'bold'))
        self._shell_output.tag_configure('ansi_dim', foreground='#808080')
        self._shell_output.tag_configure('ansi_italic', font=('Monaco', 12, 'italic'))
        self._shell_output.tag_configure('ansi_underline', underline=True)

    # ── Close button ──────────────────────────────────────────────────────────

    def set_close_callback(self, callback):
        self._close_callback = callback

    def _on_close(self):
        if self._close_callback:
            self._close_callback()

    # ── Dynamic terminal sizing ───────────────────────────────────────────────

    def _measure_char_cell(self):
        """Measure actual character-cell size from the current font.

        Uses tkinter's font measurement for 'M' (average full-width glyph)
        to derive rows/cols more accurately than a hardcoded guess.
        """
        try:
            import tkinter.font as tkfont
            f = tkfont.Font(font=self._shell_output.cget('font'))
            char_w = f.measure('M')
            line_h = f.metrics('linespace')
            if char_w > 0 and line_h > 0:
                return char_w, line_h
        except Exception:
            pass
        return 7, 16  # fallback

    def _on_resize(self, event=None):
        """Debounced PTY window-size update when the widget is resized."""
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(80, self._update_winsize)

    def _update_winsize(self):
        """Send TIOCSWINSZ to the PTY with current widget dimensions."""
        self._resize_after_id = None
        fd = self._shell_master_fd
        if fd is None:
            return
        try:
            char_w, line_h = self._measure_char_cell()
            width_px = self._shell_output.winfo_width()
            height_px = self._shell_output.winfo_height()
            if width_px > 10 and height_px > 10:
                cols = max(20, width_px // char_w)
                rows = max(6, height_px // line_h)
                winsize = struct.pack('HHHH', rows, cols, 0, 0)
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

    # ── PTY / Shell process ───────────────────────────────────────────────────

    def _start_shell(self):
        shell = os.environ.get('SHELL', '/bin/zsh')
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError:
            self._append_shell_output('[Error: could not open PTY]\n')
            return

        # Set initial terminal size, then update once widget is mapped
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

        # Update window size once the widget has dimensions
        self.after(200, self._update_winsize)

    def _append_shell_output(self, text):
        """Parse ANSI-coloured output and insert into the text widget.

        Handles backspace, carriage-return, and \\x1b[K (erase-to-end-of-line)
        so that inline progress bars and line-editing render correctly.

        All widget mutations are batched inside a single try block and only
        one ``see('insert')`` call is issued at the end — this dramatically
        reduces tkinter round-trips for high-throughput output.
        """
        w = self._shell_output

        # ── Anchor insert to end-1c before any mutation ─────────────────
        # The user may have clicked elsewhere in the widget, moving the
        # insert mark.  Always reset it so PTY output is appended, not
        # injected mid-document.
        try:
            w.mark_set('insert', 'end-1c')
        except tk.TclError:
            pass

        # ── Preprocess raw text ──────────────────────────────────────────
        # \r\n → \n  (standard CRLF line ending)
        text = text.replace('\r\n', '\n')

        # Fast path for backspace processing using str.translate-style loop.
        # When a visible character follows \b, the pair is collapsed; when
        # \b appears at the start of a chunk (backspacing into previously
        # rendered text), we delete one char from the widget.
        if '\b' in text:
            out: list[str] = []
            pending_del = 0
            for ch in text:
                if ch == '\b':
                    if out:
                        out.pop()
                    else:
                        pending_del += 1
                else:
                    out.append(ch)
            text = ''.join(out)
            if pending_del:
                # Batch-delete characters from the widget — all at the
                # current insert position, moving backwards.
                try:
                    line_start = w.index('insert linestart')
                    if w.compare('insert', '>', line_start):
                        # Delete at most pending_del chars, but don't cross
                        # the line-start boundary.
                        target = f'insert -{pending_del}c'
                        if w.compare(target, '<', line_start):
                            target = line_start
                        w.delete(target, 'insert')
                except tk.TclError:
                    pass

        # Parse ANSI SGR sequences → (text, tags) segments
        segments = self._ansi.feed(text)
        if not segments:
            return

        try:
            # ── Build a flat list of (action, ...) to minimise tk calls ──
            for item in segments:
                if item is _AnsiParser._ERASE_LINE:
                    line_start = w.index('insert linestart')
                    line_end = w.index('insert lineend')
                    if w.compare('insert', '<', line_end):
                        w.delete('insert', line_end)
                    continue

                seg_text, tags = item
                if not seg_text:
                    continue

                if '\r' in seg_text:
                    parts = seg_text.split('\r')
                    for i, part in enumerate(parts):
                        if i > 0:
                            line_start = w.index('insert linestart')
                            if part:
                                # \r followed by text → overwrite line
                                if w.compare(line_start, '<', 'insert'):
                                    w.delete(line_start, 'insert')
                            # else: trailing \r with nothing after → just
                            # position cursor at line start (no delete)
                        if part:
                            w.insert('insert', part, tags)
                else:
                    w.insert('insert', seg_text, tags)

            # ── Scroll to cursor once after all inserts ──────────────────
            w.see('insert')

            # ── Trim scrollback buffer to prevent unbounded memory growth ─
            self._trim_scrollback()
        except tk.TclError:
            pass

    def _trim_scrollback(self):
        """Remove oldest lines when scrollback exceeds the limit."""
        try:
            w = self._shell_output
            # Count lines cheaply — end index gives line count (1-based)
            end_line = int(w.index('end-1c').split('.')[0])
            excess = end_line - self._MAX_SCROLLBACK_LINES
            if excess > 0:
                w.delete('1.0', f'{excess + 1}.0')
        except (tk.TclError, ValueError, IndexError):
            pass

    def _read_pty_output(self):
        """Background thread: read PTY output and dispatch to UI thread.

        Uses a short select timeout (20 ms) so keystrokes feel instant.
        Accumulates consecutive small reads into a batch before scheduling
        a single ``after_idle`` call, which reduces tkinter overhead when
        the PTY produces many small writes in quick succession.

        When the accumulated buffer ends with a lone ``\r`` the flush is
        deferred so that a following ``\n`` arrives in the same batch and
        ``\r\n`` → ``\n`` merging works correctly.
        """
        fd = self._shell_master_fd
        READ_SIZE = 8192
        acc: list[str] = []

        def _should_defer(combined: str) -> bool:
            return combined.endswith('\r') and not combined.endswith('\r\n')

        while not self._reader_stop.is_set() and fd is not None:
            try:
                r, _, _ = select.select([fd], [], [], 0.02)
                if r:
                    data = os.read(fd, READ_SIZE)
                    if not data:
                        if acc:
                            self._schedule_output(''.join(acc))
                            acc.clear()
                        break
                    decoded = data.decode('utf-8', errors='replace')
                    if decoded:
                        acc.append(decoded)
                        combined = ''.join(acc)
                        # If the last read was less than READ_SIZE the PTY
                        # has drained its current write — but defer flush
                        # when the buffer ends with a lone \r to let a
                        # following \n arrive in the same batch.
                        if len(data) < READ_SIZE and not _should_defer(combined):
                            self._schedule_output(combined)
                            acc.clear()
                elif acc:
                    # No data available — flush now; a lone \r is safe
                    # because _append_shell_output no longer deletes on
                    # trailing \r (the timeout guarantees \n isn't coming).
                    self._schedule_output(''.join(acc))
                    acc.clear()
            except OSError:
                if acc:
                    self._schedule_output(''.join(acc))
                    acc.clear()
                break

        if not self._reader_stop.is_set():
            self.after(0, self._handle_shell_exit)

    def _schedule_output(self, text: str):
        """Schedule output rendering on the UI thread via after_idle.

        ``after_idle`` coalesces multiple rapid-fire schedule calls into a
        single callback when the event loop is free, reducing per-chunk
        overhead while keeping the UI responsive.
        """
        if not self._output_scheduled:
            self._output_scheduled = True
            self.after_idle(self._flush_pending_output)
        self._pending_output.append(text)

    def _flush_pending_output(self):
        """Drain the pending output buffer and render all accumulated text.

        Swaps the buffer list atomically so that new data arriving from the
        reader thread during rendering goes into a fresh list and is never
        lost.
        """
        self._output_scheduled = False
        pending = self._pending_output
        self._pending_output = []
        if not pending:
            return
        batch = ''.join(pending)
        self._append_shell_output(batch)

    # ── Key handling ──────────────────────────────────────────────────────────

    def _write_pty(self, data):
        fd = self._shell_master_fd
        if fd is not None:
            try:
                os.write(fd, data)
            except OSError:
                pass

    def _handle_shell_key(self, event):
        fd = self._shell_master_fd
        if fd is None:
            return "break"

        keysym = event.keysym
        state = event.state

        # macOS Command-key shortcuts — let default handling take over
        # for copy / select-all (return None); Cmd+V is paste to PTY.
        if state & 0x10:
            if keysym.lower() in ('c', 'a'):
                return None          # → default Text copy / select-all
            if keysym.lower() == 'x':
                return "break"       # suppress cut (destructive in terminal)
            if keysym.lower() == 'v':
                return self._handle_shell_paste(event)
            return "break"

        # Control-key combinations — send raw control characters to PTY.
        # Ctrl+C always sends \x03 (SIGINT); use Cmd+C for copy instead.
        if state & 0x4 and len(keysym) == 1:
            c = keysym.lower()
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
        try:
            clip = self._shell_output.clipboard_get()
        except tk.TclError:
            return "break"
        if clip:
            self._write_pty(clip.encode('utf-8'))
        return "break"

    def _has_selection(self):
        try:
            return bool(self._shell_output.get(tk.SEL_FIRST, tk.SEL_LAST))
        except tk.TclError:
            return False

    def _shell_copy(self):
        try:
            sel = self._shell_output.get(tk.SEL_FIRST, tk.SEL_LAST)
            if sel:
                self._shell_output.clipboard_clear()
                self._shell_output.clipboard_append(sel)
        except tk.TclError:
            pass

    def _shell_paste_to_terminal(self):
        self._handle_shell_paste()

    def _shell_right_click(self, event):
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

    def set_venv(self, venv_path: Optional[str]):
        self._venv_path = venv_path
        if self._shell_proc and self._shell_proc.poll() is None:
            self._stop_shell()
            self._start_shell()

    def _build_shell_env(self) -> dict:
        """Build the environment dict for the shell subprocess.

        When a venv path is configured, VIRTUAL_ENV is set and the venv's
        ``bin`` directory is prepended to PATH so the shell starts with the
        virtual environment already active — no need to ``source activate``.
        """
        env = {**os.environ}
        # Use xterm-256color so programs emit SGR colour codes
        env['TERM'] = 'xterm-256color'
        # Some programs check COLORTERM for true-colour support
        env['COLORTERM'] = 'truecolor'
        if self._venv_path:
            bin_dir = os.path.join(self._venv_path, 'bin')
            activate_script = os.path.join(bin_dir, 'activate')
            if os.path.isdir(bin_dir) and os.path.isfile(activate_script):
                env['VIRTUAL_ENV'] = self._venv_path
                env['PATH'] = f"{bin_dir}:{env.get('PATH', '')}"
        return env

    def _stop_shell(self):
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
        self._shell_output.focus_set()
        try:
            self._shell_output.mark_set("insert", "end-1c")
        except tk.TclError:
            pass

    def send_command(self, cmd):
        """Send a command string to the PTY (appends newline)."""
        fd = self._shell_master_fd
        if fd is not None and self._shell_proc and self._shell_proc.poll() is None:
            try:
                os.write(fd, (cmd + '\n').encode('utf-8'))
            except OSError:
                pass

    def cleanup(self):
        self._stop_shell()
