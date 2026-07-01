"""
SystemTerminal — split-panel command console.

Layout:
  ┌─────────────────────────────┐
  │ Terminal                    │  header
  ├─────────────────────────────┤
  │                             │
  │  ~/project $ ls -la         │  output area (read-only)
  │  ...command output...       │
  │                             │
  ├─────────────────────────────┤
  │ $ [________________] [📋] [▶] │  input bar
  └─────────────────────────────┘

Features:
  - Subprocess-based command execution (no persistent PTY shell)
  - Read-only scrollable output area
  - Command input with Enter-to-run
  - History: Up/Down keys + popup button
  - ANSI SGR stripping for captured output
  - venv integration via environment variables
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

# ── ANSI SGR stripping (kept for tools that force colour) ──────────────────

_SGR_RE = re.compile(r'\x1b\[[\d;]*m')


# ═══════════════════════════════════════════════════════════════════════════════
# SystemTerminal
# ═══════════════════════════════════════════════════════════════════════════════

class SystemTerminal(ttk.Frame):
    """Split-panel terminal: output display + command input with history."""

    BG = '#ffffff'
    FG = '#1a1a1a'
    FONT = ('Monaco', 12)
    MAX_SCROLLBACK = 10_000

    # Tag colour scheme (light theme)
    TAG_STYLES = {
        'prompt': {'foreground': '#0451a5', 'font': (FONT[0], FONT[1], 'bold')},
        'stdout': {'foreground': '#1a1a1a'},
        'stderr': {'foreground': '#d32f2f'},
        'error':  {'foreground': '#d32f2f'},
    }

    def __init__(self, parent, cwd: str = None, **kwargs):
        super().__init__(parent, **kwargs)
        self._close_cb = None
        self._cwd = cwd or os.path.expanduser('~')
        self._venv_path: Optional[str] = None

        # History
        self._history: list[str] = []
        self._history_index: int = -1
        self._saved_input: str = ''  # saved current entry text during Up/Down

        # Running state
        self._running = False

        self._build_ui()
        self._welcome()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        self.configure(height=200)
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header bar
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky='ew')
        ttk.Label(header, text='Terminal', font=('Helvetica', 10, 'bold')).pack(
            side=tk.LEFT, padx=(4, 0), pady=(2, 0))

        ttk.Separator(self, orient=tk.HORIZONTAL).grid(
            row=1, column=0, sticky='ew')

        # ── Output area (read-only, scrollable) ──
        out_frame = ttk.Frame(self)
        out_frame.grid(row=2, column=0, sticky='nsew')
        out_frame.grid_rowconfigure(0, weight=1)
        out_frame.grid_columnconfigure(0, weight=1)

        self._output = tk.Text(
            out_frame,
            bg=self.BG, fg=self.FG,
            font=self.FONT,
            wrap=tk.WORD,
            highlightthickness=0,
            bd=0,
            padx=4, pady=4,
            state=tk.DISABLED,
            takefocus=0,
        )

        vs = ttk.Scrollbar(out_frame, orient=tk.VERTICAL,
                           command=self._output.yview)
        self._output.configure(yscrollcommand=vs.set)

        self._output.grid(row=0, column=0, sticky='nsew')
        vs.grid(row=0, column=1, sticky='ns')

        self._setup_tags()

        # ── Input bar ──
        ttk.Separator(self, orient=tk.HORIZONTAL).grid(
            row=3, column=0, sticky='ew')

        input_bar = ttk.Frame(self)
        input_bar.grid(row=4, column=0, sticky='ew', padx=4, pady=(2, 4))
        input_bar.grid_columnconfigure(1, weight=1)

        # Prompt label
        self._prompt_label = ttk.Label(input_bar, text='$', font=self.FONT)
        self._prompt_label.grid(row=0, column=0, padx=(0, 4))

        # Command entry
        self._entry = ttk.Entry(input_bar, font=self.FONT)
        self._entry.grid(row=0, column=1, sticky='ew')
        self._entry.bind('<Return>', self._on_enter)
        self._entry.bind('<Up>', self._on_up)
        self._entry.bind('<Down>', self._on_down)

        # History button
        self._hist_btn = ttk.Button(input_bar, text='⏰', width=3,
                                    command=self._show_history_popup)
        self._hist_btn.grid(row=0, column=2, padx=(4, 2))

        # Run button
        self._run_btn = ttk.Button(input_bar, text='▶', width=3,
                                   command=self._on_enter)
        self._run_btn.grid(row=0, column=3)

        # Right-click context menu on output
        self._ctx_menu = tk.Menu(self, tearoff=0)
        self._ctx_menu.add_command(label='Copy', command=self._copy_output,
                                   accelerator='Cmd+C')
        self._output.bind('<Button-2>', self._on_right_click)
        self._output.bind('<Button-3>', self._on_right_click)
        self._output.bind('<Control-Button-1>', self._on_right_click)
        self._output.bind('<Command-c>', lambda e: self._copy_output())

    def _setup_tags(self):
        for tag_name, style in self.TAG_STYLES.items():
            self._output.tag_configure(tag_name, **style)

    # ── Welcome ────────────────────────────────────────────────────────────

    def _welcome(self):
        self._append_output(f'  CWD: {self._cwd}\n', 'prompt')
        self._append_output('  Type a shell command and press Enter.\n\n', 'stdout')

    # ── Output helpers ─────────────────────────────────────────────────────

    def _append_output(self, text: str, *tags: str):
        """Append *text* to the output area (strips ANSI SGR, thread-safe)."""
        clean = _SGR_RE.sub('', text)
        if not clean:
            return

        def _do():
            try:
                w = self._output
                w.configure(state=tk.NORMAL)
                if tags:
                    w.insert('end', clean, tags)
                else:
                    w.insert('end', clean)
                w.see('end')
                self._trim_scrollback()
            except tk.TclError:
                pass
            finally:
                try:
                    w.configure(state=tk.DISABLED)
                except tk.TclError:
                    pass

        # Always schedule on the main thread
        self.after(0, _do)

    def _trim_scrollback(self):
        try:
            end = int(self._output.index('end-1c').split('.')[0])
            excess = end - self.MAX_SCROLLBACK
            if excess > 0:
                self._output.delete('1.0', f'{excess + 1}.0')
        except (tk.TclError, ValueError):
            pass

    def _echo_command(self, cmd: str):
        """Print the command as it would appear in a shell."""
        cwd_display = self._cwd.replace(os.path.expanduser('~'), '~')
        self._append_output(f'\n{cwd_display} $ {cmd}\n', 'prompt')

    def _echo_prompt(self):
        """Print a fresh prompt line after command output."""
        cwd_display = self._cwd.replace(os.path.expanduser('~'), '~')
        self._append_output(f'{cwd_display} $ ', 'prompt')

    # ── Command execution ──────────────────────────────────────────────────

    def _on_enter(self, event=None):
        """Execute the command in the entry field."""
        cmd = self._entry.get().strip()
        if not cmd:
            return

        # Save to history
        if not self._history or self._history[-1] != cmd:
            self._history.append(cmd)
        self._history_index = len(self._history)
        self._saved_input = ''

        # Clear entry and echo command
        self._entry.delete(0, tk.END)
        self._echo_command(cmd)

        # Run in background thread
        self._running = True
        self._set_input_state(tk.DISABLED)
        t = threading.Thread(target=self._run_command, args=(cmd,), daemon=True)
        t.start()

    def _run_command(self, cmd: str):
        """Execute *cmd* via subprocess (runs in background thread)."""
        try:
            result = subprocess.run(
                cmd, shell=True,
                capture_output=True, text=True,
                cwd=self._cwd,
                env=self._build_env(),
            )
        except Exception as e:
            self._append_output(f'[Error: {e}]\n', 'error')
            self._echo_prompt()
            self._running = False
            self.after(0, lambda: self._set_input_state(tk.NORMAL))
            return

        if result.stdout:
            self._append_output(result.stdout, 'stdout')
        if result.stderr:
            self._append_output(result.stderr, 'stderr')

        self._echo_prompt()
        self._running = False
        self.after(0, lambda: self._set_input_state(tk.NORMAL))

    def _set_input_state(self, state: str):
        """Enable or disable the entry and buttons."""
        try:
            self._entry.configure(state=state)
            if state == tk.NORMAL:
                self._run_btn.configure(state=tk.NORMAL)
                self._hist_btn.configure(state=tk.NORMAL)
            else:
                self._run_btn.configure(state=tk.DISABLED)
                self._hist_btn.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    # ── Environment ────────────────────────────────────────────────────────

    def _build_env(self) -> dict:
        """Build the environment dict for subprocess execution."""
        env = {**os.environ}
        if self._venv_path:
            bin_dir = os.path.join(self._venv_path, 'bin')
            if os.path.isdir(bin_dir):
                env['VIRTUAL_ENV'] = self._venv_path
                env['PATH'] = f"{bin_dir}:{env.get('PATH', '')}"
        return env

    # ── History ────────────────────────────────────────────────────────────

    def _on_up(self, event=None):
        """Navigate to the previous history entry."""
        if not self._history:
            return

        # Save current entry text on first Up press
        if self._history_index == len(self._history):
            self._saved_input = self._entry.get()

        if self._history_index > 0:
            self._history_index -= 1
            self._set_entry_text(self._history[self._history_index])

        return 'break'

    def _on_down(self, event=None):
        """Navigate to the next history entry."""
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._set_entry_text(self._history[self._history_index])
        elif self._history_index == len(self._history) - 1:
            # At the last history entry → restore saved input
            self._history_index = len(self._history)
            self._set_entry_text(self._saved_input)
            self._saved_input = ''

        return 'break'

    def _set_entry_text(self, text: str):
        """Set the entry widget text and move cursor to end."""
        self._entry.delete(0, tk.END)
        self._entry.insert(0, text)
        self._entry.xview_moveto(1.0)  # scroll to end for long commands

    def _show_history_popup(self):
        """Show a popup menu with command history."""
        if not self._history:
            return

        menu = tk.Menu(self, tearoff=0)
        for i, cmd in enumerate(reversed(self._history[-20:])):
            # Truncate long commands for display
            label = cmd if len(cmd) <= 60 else cmd[:57] + '...'
            menu.add_command(
                label=label,
                command=lambda c=cmd: self._fill_from_history(c),
            )

        try:
            x = self._hist_btn.winfo_rootx()
            y = self._hist_btn.winfo_rooty() - menu.winfo_reqheight()
            menu.post(x, y)
        finally:
            menu.grab_release()

    def _fill_from_history(self, cmd: str):
        """Fill the entry with a command from history."""
        self._entry.delete(0, tk.END)
        self._entry.insert(0, cmd)
        self._entry.xview_moveto(1.0)
        self._history_index = len(self._history)
        self._saved_input = cmd
        self.focus_input()

    def _copy_output(self):
        """Copy selected text from the output area."""
        try:
            sel = self._output.get(tk.SEL_FIRST, tk.SEL_LAST)
            if sel:
                self.clipboard_clear()
                self.clipboard_append(sel)
        except tk.TclError:
            pass

    def _on_right_click(self, event):
        try:
            has_sel = bool(self._output.get(tk.SEL_FIRST, tk.SEL_LAST))
        except tk.TclError:
            has_sel = False
        self._ctx_menu.entryconfigure(0, state=tk.NORMAL if has_sel else tk.DISABLED)
        try:
            self._ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._ctx_menu.grab_release()

    # ── Public API (kept compatible with app.py) ───────────────────────────

    def set_close_callback(self, cb):
        self._close_cb = cb

    def cd_to(self, path: str):
        """Change the working directory for subsequent commands."""
        if path and os.path.isdir(path):
            self._cwd = path
            cwd_display = path.replace(os.path.expanduser('~'), '~')
            self._append_output(f'[CWD: {cwd_display}]\n', 'prompt')

    def send_command(self, cmd: str):
        """Execute *cmd* programmatically (used by Run in Terminal)."""
        # Echo and execute
        self._echo_command(cmd)
        self._running = True
        self._set_input_state(tk.DISABLED)
        t = threading.Thread(target=self._run_command, args=(cmd,), daemon=True)
        t.start()

    def set_venv(self, venv_path: Optional[str]):
        """Set the venv path for subsequent command environments."""
        self._venv_path = venv_path
        if venv_path:
            name = os.path.basename(venv_path)
            self._append_output(f'[Venv: {name}]\n', 'prompt')
        else:
            self._append_output('[Venv: system Python]\n', 'prompt')

    def focus_input(self):
        """Focus the command entry field."""
        self._entry.focus_set()

    def cleanup(self):
        """Clean up resources (no persistent process — no-op)."""
        pass

    @property
    def text_widget(self):
        """Return the output Text widget (for compatibility)."""
        return self._output
