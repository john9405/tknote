"""
SystemTerminal — 简洁可靠的 PTY 系统终端模拟器

特性：
  - PTY 驱动的 shell 子进程 (zsh/bash/sh)
  - ANSI SGR 16 色彩支持 (通过 tkinter text tags)
  - 线程读取器异步输出到 UI
  - 动态终端尺寸调整 (TIOCSWINSZ)
  - 按键转发、粘贴、滚动缓冲
  - venv 集成
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import termios
import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# ANSI 16 色调色板 (VS Code 风格暗色主题)
# ═══════════════════════════════════════════════════════════════════════════════

_FG_COLORS = {
    30: '#808080', 31: '#cd3131', 32: '#0dbc79', 33: '#e5e510',
    34: '#2472c8', 35: '#bc3fbc', 36: '#11a8cd', 37: '#e5e5e5',
    90: '#767676', 91: '#f14c4c', 92: '#23d18b', 93: '#f5f543',
    94: '#3b8eea', 95: '#d670d6', 96: '#29b8db', 97: '#ffffff',
}

_BG_COLORS = {
    40: '#000000', 41: '#cd3131', 42: '#0dbc79', 43: '#e5e510',
    44: '#2472c8', 45: '#bc3fbc', 46: '#11a8cd', 47: '#e5e5e5',
    100: '#767676', 101: '#f14c4c', 102: '#23d18b', 103: '#f5f543',
    104: '#3b8eea', 105: '#d670d6', 106: '#29b8db', 107: '#ffffff',
}

# 匹配 ANSI SGR 序列 (仅 \x1b[...m)
_SGR_RE = re.compile(r'\x1b\[([\d;]*)m')

# 需要从输出中移除的 CSI / OSC 序列（光标移动、标题更新等）
_STRIP_RE = re.compile(
    r'\x1b\[[\d;]*[ABCDEFGHJKSTfhlnpsu]'   # 光标移动 / 擦除 / 模式
    r'|\x1b\]\d+;[^\x07\x1b]*(?:\x07|\x1b\\)'  # OSC 序列
    r'|\x1b[()*+-./][^\x1b]'               # 字符集选择
    r'|\x1b[PX^_].*?\x1b\\'                # 设备控制字符串
    r'|\x1b[78DKMNc]'                      # 其他 ESC 序列
)


def _fg_tag(code: int) -> str:
    return f'ansi_fg_{code}'


def _bg_tag(code: int) -> str:
    return f'ansi_bg_{code}'


# ═══════════════════════════════════════════════════════════════════════════════
# ANSI 解码器 —— 流式解析 ANSI 文本，输出 (text, tags) 段
# ═══════════════════════════════════════════════════════════════════════════════

class AnsiDecoder:
    """将 ANSI 转义文本转换为带 tag 的纯文本段序列。

    输出：[(plain_text, (tag1, tag2, ...)), ...]
    调用方负责处理 \\r（回车）和 \\b（退格）。
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False
        self.fg = 37
        self.bg = None

    def current_tags(self) -> tuple[str, ...]:
        tags = []
        if self.bold:
            tags.append('ansi_bold')
        if self.dim:
            tags.append('ansi_dim')
        if self.italic:
            tags.append('ansi_italic')
        if self.underline:
            tags.append('ansi_underline')
        if self.fg is not None:
            tags.append(_fg_tag(self.fg))
        if self.bg is not None:
            tags.append(_bg_tag(self.bg))
        return tuple(tags)

    def _apply(self, code: int):
        if code == 0:
            self.reset()
        elif code == 1:
            self.bold = True
        elif code == 2:
            self.dim = True
        elif code == 3:
            self.italic = True
        elif code == 4:
            self.underline = True
        elif code == 22:
            self.bold = self.dim = False
        elif code == 23:
            self.italic = False
        elif code == 24:
            self.underline = False
        elif (30 <= code <= 37) or (90 <= code <= 97):
            self.fg = code
        elif (40 <= code <= 47) or (100 <= code <= 107):
            self.bg = code
        elif code == 39:
            self.fg = 37
        elif code == 49:
            self.bg = None

    def decode(self, text: str) -> list[tuple[str, tuple[str, ...]]]:
        """解析 ANSI SGR 序列，返回 (纯文本, tag元组) 列表。"""
        segments: list[tuple[str, tuple[str, ...]]] = []
        pos = 0

        for m in _SGR_RE.finditer(text):
            start, end = m.start(), m.end()

            if start > pos:
                plain = text[pos:start]
                if plain:
                    segments.append((plain, self.current_tags()))

            params = m.group(1)
            if params:
                for p in params.split(';'):
                    try:
                        self._apply(int(p) if p else 0)
                    except ValueError:
                        pass
            else:
                self._apply(0)

            pos = end

        if pos < len(text):
            segments.append((text[pos:], self.current_tags()))

        return segments


# ═══════════════════════════════════════════════════════════════════════════════
# 按键 → PTY 字节映射
# ═══════════════════════════════════════════════════════════════════════════════

_KEY_MAP = {
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
    'Prior':     b'\x1b[5~',
    'Next':      b'\x1b[6~',
    'Insert':    b'\x1b[2~',
    'F1':        b'\x1bOP',
    'F2':        b'\x1bOQ',
    'F3':        b'\x1bOR',
    'F4':        b'\x1bOS',
    'F5':        b'\x1b[15~',
    'F6':        b'\x1b[17~',
    'F7':        b'\x1b[18~',
    'F8':        b'\x1b[19~',
    'F9':        b'\x1b[20~',
    'F10':       b'\x1b[21~',
    'F11':       b'\x1b[23~',
    'F12':       b'\x1b[24~',
}


# ═══════════════════════════════════════════════════════════════════════════════
# SystemTerminal 主控件
# ═══════════════════════════════════════════════════════════════════════════════

class SystemTerminal(ttk.Frame):
    """可嵌入的系统终端控件（PTY 驱动）。"""

    BG = '#1e1e1e'
    FG = '#d4d4d4'
    CURSOR_COLOR = '#d4d4d4'
    FONT = ('Monaco', 12)
    MAX_SCROLLBACK = 10_000

    def __init__(self, parent, cwd: str = None, **kwargs):
        super().__init__(parent, **kwargs)
        self._close_cb = None
        self._cwd = cwd or os.path.expanduser('~')
        self._venv_path: Optional[str] = None

        # PTY 状态
        self._proc: Optional[subprocess.Popen] = None
        self._master_fd: Optional[int] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_reader = threading.Event()

        self._build_ui()
        self._start_shell()

    # ── UI 构建 ──────────────────────────────────────────────────────────

    def _build_ui(self):
        self.configure(height=200)
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 标题栏
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky='ew')
        ttk.Label(header, text='Terminal', font=('Helvetica', 10, 'bold')).pack(
            side=tk.LEFT, padx=(4, 0), pady=(2, 0))

        ttk.Separator(self, orient=tk.HORIZONTAL).grid(
            row=1, column=0, sticky='ew')

        # 终端文本区域
        tframe = ttk.Frame(self)
        tframe.grid(row=2, column=0, sticky='nsew')
        tframe.grid_rowconfigure(0, weight=1)
        tframe.grid_columnconfigure(0, weight=1)

        self._text = tk.Text(
            tframe,
            bg=self.BG, fg=self.FG,
            insertbackground=self.CURSOR_COLOR,
            font=self.FONT,
            wrap=tk.NONE,
            blockcursor=False,
            highlightthickness=0,
            bd=0,
            padx=4, pady=4,
            state=tk.DISABLED,
        )

        vs = ttk.Scrollbar(tframe, orient=tk.VERTICAL,
                           command=self._text.yview)
        hs = ttk.Scrollbar(tframe, orient=tk.HORIZONTAL,
                           command=self._text.xview)
        self._text.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)

        self._text.grid(row=0, column=0, sticky='nsew')
        vs.grid(row=0, column=1, sticky='ns')
        hs.grid(row=1, column=0, sticky='ew')

        self._setup_tags()

        # 按键绑定
        self._text.bind('<Key>', self._on_key)
        self._text.bind('<Command-v>', self._on_paste)
        self._text.bind('<Control-v>', self._on_paste)
        self._text.bind('<Configure>', self._on_resize, add='+')

        # 右键菜单
        self._ctx_menu = tk.Menu(self, tearoff=0)
        self._ctx_menu.add_command(label='Copy', command=self._copy,
                                   accelerator='Cmd+C')
        self._ctx_menu.add_command(label='Paste', command=self._paste,
                                   accelerator='Cmd+V')
        self._text.bind('<Button-2>', self._on_right_click)
        self._text.bind('<Button-3>', self._on_right_click)
        self._text.bind('<Control-Button-1>', self._on_right_click)

        self._resize_after = None
        self._ansi = AnsiDecoder()

    def _setup_tags(self):
        """配置所有 ANSI 颜色和样式标签。"""
        for code, color in _FG_COLORS.items():
            self._text.tag_configure(_fg_tag(code), foreground=color)
        for code, color in _BG_COLORS.items():
            self._text.tag_configure(_bg_tag(code), background=color)
        self._text.tag_configure('ansi_bold',
                                 font=(self.FONT[0], self.FONT[1], 'bold'))
        self._text.tag_configure('ansi_dim', foreground='#808080')
        self._text.tag_configure('ansi_italic',
                                 font=(self.FONT[0], self.FONT[1], 'italic'))
        self._text.tag_configure('ansi_underline', underline=True)

    # ── 关闭回调 ─────────────────────────────────────────────────────────

    def set_close_callback(self, cb):
        self._close_cb = cb

    # ── Shell 启动 ───────────────────────────────────────────────────────

    def _find_shell(self) -> str:
        for candidate in (
            os.environ.get('SHELL'),
            '/bin/zsh', shutil.which('zsh'),
            '/bin/bash', shutil.which('bash'),
            '/bin/sh',
        ):
            if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return '/bin/sh'

    def _shell_cmd(self, shell: str):
        name = os.path.basename(shell)
        if name == 'zsh':
            return [f'-{name}', '-i'], shell
        return [shell, '-i'], None

    def _build_env(self) -> dict:
        env = {**os.environ}
        env['TERM'] = 'xterm-256color'
        env['COLORTERM'] = 'truecolor'
        env['TERM_PROGRAM'] = 'tknote'

        if self._venv_path:
            bin_dir = os.path.join(self._venv_path, 'bin')
            if os.path.isdir(bin_dir) and os.path.isfile(
                    os.path.join(bin_dir, 'activate')):
                env['VIRTUAL_ENV'] = self._venv_path
                env['PATH'] = f"{bin_dir}:{env.get('PATH', '')}"
        return env

    def _start_shell(self):
        shell = self._find_shell()
        argv, executable = self._shell_cmd(shell)
        env = self._build_env()

        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as e:
            self._show_error(f'Cannot open PTY: {e}')
            return

        try:
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ,
                        struct.pack('HHHH', 24, 80, 0, 0))
        except OSError:
            pass

        try:
            kwargs = {
                'stdin': slave_fd, 'stdout': slave_fd, 'stderr': slave_fd,
                'cwd': self._cwd, 'preexec_fn': os.setsid,
                'env': env, 'close_fds': True,
            }
            if executable is not None:
                kwargs['executable'] = executable
            self._proc = subprocess.Popen(argv, **kwargs)
        except Exception as e:
            self._show_error(f'Cannot start shell: {e}')
            os.close(master_fd)
            os.close(slave_fd)
            return

        os.close(slave_fd)
        self._master_fd = master_fd

        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self._stop_reader.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        self.after(200, self._update_winsize)
        self._text.configure(state=tk.NORMAL)

    def _show_error(self, msg: str):
        try:
            self._text.configure(state=tk.NORMAL)
            self._text.insert(tk.END, f'\n[{msg}]\n', (_fg_tag(31),))
            self._text.see(tk.END)
            self._text.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    # ── 输出读取线程 ─────────────────────────────────────────────────────

    def _reader_loop(self):
        fd = self._master_fd
        while not self._stop_reader.is_set() and fd is not None:
            try:
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    data = os.read(fd, 4096)
                    if not data:
                        break
                    text = data.decode('utf-8', errors='replace')
                    if text:
                        self.after(0, self._handle_output, text)
            except (OSError, ValueError):
                break

        if not self._stop_reader.is_set():
            self.after(0, self._on_shell_exit)

    def _handle_output(self, raw: str):
        try:
            self._render(raw)
        except tk.TclError:
            pass

    # ── 输出渲染 ─────────────────────────────────────────────────────────

    def _render(self, raw: str):
        """解析并渲染原始 PTY 输出。"""
        w = self._text

        # 1. 剥离无法渲染的控制序列
        clean = _STRIP_RE.sub('', raw)
        if not clean:
            return

        # 2. 处理退格
        clean = self._handle_backspace(clean)

        # 3. \r\n → \n
        clean = clean.replace('\r\n', '\n')

        # 4. 按 \r 分段，模拟回车覆盖
        parts = clean.split('\r')

        w.configure(state=tk.NORMAL)
        try:
            for i, part in enumerate(parts):
                if i > 0:
                    # 回到行首，清除该行现有内容
                    ls = w.index('insert linestart')
                    if w.compare(ls, '<', 'insert'):
                        w.delete(ls, 'insert')

                if not part:
                    continue

                for seg_text, tags in self._ansi.decode(part):
                    if seg_text:
                        w.insert('insert', seg_text, tags)

            w.see('insert')
            self._trim_scrollback()
        finally:
            w.configure(state=tk.DISABLED)

    def _handle_backspace(self, text: str) -> str:
        """处理退格字符：X\\b → 删除 X；\\b 在开头 → 删除已渲染字符。"""
        if '\b' not in text:
            return text

        result: list[str] = []
        pending = 0

        for ch in text:
            if ch == '\b':
                pending += 1
            else:
                for _ in range(pending):
                    if result:
                        result.pop()
                pending = 0
                result.append(ch)

        # 末尾退格 → 从 widget 中删除已渲染字符
        if pending > 0:
            try:
                w = self._text
                w.configure(state=tk.NORMAL)
                ls = w.index('insert linestart')
                target = f'insert -{pending}c'
                if w.compare(target, '<', ls):
                    target = ls
                if w.compare(target, '<', 'insert'):
                    w.delete(target, 'insert')
                w.configure(state=tk.DISABLED)
            except tk.TclError:
                pass

        return ''.join(result)

    def _trim_scrollback(self):
        try:
            end = int(self._text.index('end-1c').split('.')[0])
            excess = end - self.MAX_SCROLLBACK
            if excess > 0:
                self._text.delete('1.0', f'{excess + 1}.0')
        except (tk.TclError, ValueError):
            pass

    # ── 尺寸管理 ─────────────────────────────────────────────────────────

    def _on_resize(self, event=None):
        if self._resize_after is not None:
            self.after_cancel(self._resize_after)
        self._resize_after = self.after(100, self._update_winsize)

    def _update_winsize(self):
        self._resize_after = None
        fd = self._master_fd
        if fd is None:
            return
        try:
            import tkinter.font as tkfont
            f = tkfont.Font(font=self._text.cget('font'))
            cw = f.measure('M')
            lh = f.metrics('linespace')
            if cw <= 0 or lh <= 0:
                cw, lh = 7, 16

            wp = self._text.winfo_width()
            hp = self._text.winfo_height()
            if wp > 10 and hp > 10:
                cols = max(20, wp // cw)
                rows = max(6, hp // lh)
                fcntl.ioctl(fd, termios.TIOCSWINSZ,
                            struct.pack('HHHH', rows, cols, 0, 0))
        except Exception:
            pass

    # ── 按键处理 ─────────────────────────────────────────────────────────

    def _write(self, data: bytes):
        fd = self._master_fd
        if fd is not None and self._proc and self._proc.poll() is None:
            try:
                os.write(fd, data)
            except OSError:
                pass

    def _on_key(self, event):
        if self._master_fd is None:
            return 'break'

        state = event.state
        keysym = event.keysym

        # Command (macOS): 保留 Cmd+C / Cmd+A
        if state & 0x10:
            if keysym.lower() in ('c', 'a'):
                return None
            if keysym.lower() == 'v':
                return self._on_paste(event)
            return 'break'

        # Control + 字母 → 控制字符
        if state & 0x4 and len(keysym) == 1:
            c = keysym.lower()
            if 'a' <= c <= 'z':
                self._write((ord(c) - ord('a') + 1).to_bytes(1, 'big'))
                return 'break'

        # 特殊按键
        if keysym in _KEY_MAP:
            self._write(_KEY_MAP[keysym])
            return 'break'

        # 可打印字符
        char = event.char
        if char and len(char) == 1 and ord(char) >= 0x20:
            self._write(char.encode('utf-8'))
            return 'break'

        return 'break'

    def _on_paste(self, event=None):
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            return 'break'
        if clip:
            self._write(clip.replace('\r\n', '\n').encode('utf-8'))
        return 'break'

    # ── 右键菜单 ─────────────────────────────────────────────────────────

    def _has_sel(self) -> bool:
        try:
            return bool(self._text.get(tk.SEL_FIRST, tk.SEL_LAST))
        except tk.TclError:
            return False

    def _copy(self):
        try:
            sel = self._text.get(tk.SEL_FIRST, tk.SEL_LAST)
            if sel:
                self.clipboard_clear()
                self.clipboard_append(sel)
        except tk.TclError:
            pass

    def _paste(self):
        self._on_paste()

    def _on_right_click(self, event):
        self._ctx_menu.entryconfigure(
            0, state=tk.NORMAL if self._has_sel() else tk.DISABLED)
        try:
            self._ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._ctx_menu.grab_release()

    # ── Shell 退出 ───────────────────────────────────────────────────────

    def _on_shell_exit(self):
        self._show_error('Process exited — restarting...')
        self._close_fd()
        self._proc = None
        self.after(500, self._start_shell)

    # ── Venv ─────────────────────────────────────────────────────────────

    def set_venv(self, venv_path: Optional[str]):
        self._venv_path = venv_path
        if self._proc and self._proc.poll() is None:
            self._restart()

    # ── 目录 / 命令 ──────────────────────────────────────────────────────

    def cd_to(self, path: str):
        if path and os.path.isdir(path):
            self._cwd = path
            self._write(f'cd {shlex.quote(path)}\n'.encode())

    def send_command(self, cmd: str):
        if self._proc and self._proc.poll() is None:
            self._write((cmd + '\n').encode())

    # ── 焦点 / 清理 ──────────────────────────────────────────────────────

    def focus_input(self):
        self._text.focus_set()
        try:
            self._text.mark_set('insert', 'end-1c')
            self._text.see('insert')
        except tk.TclError:
            pass

    def cleanup(self):
        self._stop_reader.set()
        self._close_fd()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                self._proc.wait(timeout=1)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def _close_fd(self):
        fd = self._master_fd
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            self._master_fd = None

    def _restart(self):
        self._stop_reader.set()
        self._close_fd()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                self._proc.wait(timeout=1)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        self._start_shell()

