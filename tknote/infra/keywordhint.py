"""KeywordHint — floating popup with Python keyword descriptions.

Shows a tooltip when the cursor lands on a Python keyword, and hides
after a short delay or when the cursor moves away.
"""

import keyword
import re
import tkinter as tk

# ── Keyword descriptions (Chinese) ────────────────────────────────────────────

_KEYWORD_HINTS = {
    'False':   '布尔值 False — 表示假。',
    'None':    '空值 None — 表示"没有值"或"空"。',
    'True':    '布尔值 True — 表示真。',
    'and':     '逻辑与运算符 — 左右两边都为 True 时结果才为 True。\n短路求值：左边为 False 时不再计算右边。',
    'as':      '别名关键字 — 用于 import ... as ... 或 with ... as ... 给对象起别名。',
    'assert':  '断言语句 — 调试用，条件为 False 时抛出 AssertionError。',
    'async':   '异步声明 — 定义 async def 异步函数，常与 await 搭配。',
    'await':   '等待异步结果 — 在 async 函数内暂停执行，等待可等待对象完成。',
    'break':   '跳出循环 — 立即终止当前 for / while 循环。',
    'class':   '类定义 — 定义一个类（面向对象编程的核心）。',
    'continue':'继续下一次循环 — 跳过本轮循环剩余代码，进入下一轮迭代。',
    'def':     '函数定义 — 定义一个函数（可重复调用的代码块）。',
    'del':     '删除 — 删除变量、列表元素、字典键或对象属性。',
    'elif':    '否则如果 — else + if，用于多分支条件判断。',
    'else':    '否则 — 用于 if 条件不满足时，或循环正常结束（未 break）时。',
    'except':  '捕获异常 — 与 try 搭配，处理指定类型的异常。',
    'finally': '最终执行 — 与 try 搭配，无论是否发生异常都会执行的代码块。',
    'for':     'for 循环 — 遍历可迭代对象（列表、字符串、range 等）中的每个元素。',
    'from':    '从…导入 — from module import name，从模块导入指定内容。',
    'global':  '全局变量声明 — 在函数内部声明使用模块级全局变量。',
    'if':      '条件判断 — if 条件: 满足时执行。常与 elif / else 搭配。',
    'import':  '导入模块 — import module，引入外部模块功能。',
    'in':      '成员判断 — 检查值是否存在于序列中（如 x in list）。\n也用于 for 循环：for x in iterable。',
    'is':      '身份判断 — 检查两个引用是否指向同一个对象（比较内存地址）。\n与 ==（值比较）不同。',
    'lambda':  '匿名函数 — 创建单表达式的小型函数。\n例：lambda x: x + 1',
    'nonlocal':'非局部变量 — 在嵌套函数中声明使用外层（非全局）函数的变量。',
    'not':     '逻辑非 — 取反操作。not True → False。',
    'or':      '逻辑或 — 左右两边任一为 True 则结果为 True。\n短路求值：左边为 True 时不再计算右边。',
    'pass':    '占位语句 — 什么都不做，用作语法上需要但暂无实现的占位符。',
    'raise':   '引发异常 — 主动抛出指定异常。raise ValueError("消息")。',
    'return':  '返回值 — 从函数中返回一个值，函数执行到此结束。',
    'try':     '尝试执行 — 与 except / finally 搭配，处理可能出错的代码块。',
    'while':   'while 循环 — 条件为 True 时重复执行。注意避免死循环！',
    'with':    '上下文管理器 — 自动管理资源（如文件打开/关闭）。\n例：with open("f.txt") as f: ...',
    'yield':   '生成器 — 在函数中使用 yield 代替 return，使其成为生成器函数。\n每次 yield 暂停并返回值，下次从暂停处继续。',
}


# ── KeywordHint ───────────────────────────────────────────────────────────────

class KeywordHint:
    """Floating tooltip that shows Python keyword descriptions.

    Bind to an EditorWidget / Text widget.  The hint appears when the
    insertion cursor lands on a Python keyword and disappears when it moves
    off or after a timeout.
    """

    HIDE_DELAY = 1200       # ms — auto-hide after this long
    SHOW_DELAY  = 400       # ms — show after cursor stays on keyword this long

    def __init__(self, text_widget):
        self.text = text_widget
        self._tipwindow = None
        self._label = None
        self._current_word = None
        self._show_after_id = None
        self._hide_after_id = None

    # ── Public API ────────────────────────────────────────────────────────

    def attach(self):
        """Bind events that trigger the keyword hint."""
        self.text.bind('<KeyRelease>', self._schedule_check, add=True)
        self.text.bind('<ButtonRelease-1>', self._schedule_check, add=True)
        # Also check on focus-in
        self.text.bind('<FocusIn>', self._schedule_check, add=True)

    def detach(self):
        """Unbind and clean up."""
        self._cancel_scheduled()
        self.hidetip()
        # Best-effort unbind — safe to call even if not bound
        try:
            self.text.unbind('<KeyRelease>', self._schedule_check)
            self.text.unbind('<ButtonRelease-1>', self._schedule_check)
            self.text.unbind('<FocusIn>', self._schedule_check)
        except tk.TclError:
            pass

    def force_show(self, event=None):
        """Immediately show hint for the word at the cursor (keyboard shortcut)."""
        self._cancel_scheduled()
        word = self._word_at_insert()
        if word:
            self._show(word)
        return "break"

    def hidetip(self):
        """Hide and destroy the tooltip window."""
        self._cancel_scheduled()
        if self._tipwindow:
            try:
                self._tipwindow.destroy()
            except tk.TclError:
                pass
            self._tipwindow = None
            self._label = None
            self._current_word = None

    # ── Internal ───────────────────────────────────────────────────────────

    def _cancel_scheduled(self):
        for attr in ('_show_after_id', '_hide_after_id'):
            aid = getattr(self, attr, None)
            if aid is not None:
                try:
                    self.text.after_cancel(aid)
                except (ValueError, tk.TclError):
                    pass
                setattr(self, attr, None)

    def _schedule_check(self, event=None):
        """Schedule a check after a short delay (debounce)."""
        self._cancel_scheduled()
        self._show_after_id = self.text.after(
            self.SHOW_DELAY, self._check_and_show)

    def _check_and_show(self):
        """Check the word at cursor and show/hide hint accordingly."""
        self._show_after_id = None
        word = self._word_at_insert()

        # Same word → keep showing, just reset hide timer
        if word and word == self._current_word:
            self._reset_hide_timer()
            return

        # Different word → hide old, maybe show new
        self.hidetip()
        if word:
            self._show(word)

    def _show(self, word):
        """Create and show the tooltip for a keyword."""
        desc = _KEYWORD_HINTS.get(word)
        if not desc:
            return

        self._current_word = word

        # Create toplevel window
        parent = self.text.winfo_toplevel()
        tw = tk.Toplevel(parent)
        tw.withdraw()
        tw.wm_overrideredirect(True)  # no title bar
        self._apply_platform_style(tw)
        try:
            tw.transient(parent)
        except tk.TclError:
            pass
        try:
            tw.wm_attributes('-topmost', True)
        except tk.TclError:
            pass
        self._tipwindow = tw

        # Label with keyword name + description
        text = f"🔑 {word}\n{desc}"
        self._label = tk.Label(
            tw, text=text, justify=tk.LEFT,
            background='#ffffd0', foreground='#333333',
            relief=tk.SOLID, borderwidth=1,
            font=self.text['font'] or ('Monaco', 11),
            wraplength=420,
        )
        self._label.pack()

        if not self._position():
            self.hidetip()
            return
        self._show_window()
        self._reset_hide_timer()

    def _position(self):
        """Position the tooltip near the cursor."""
        try:
            bbox = self.text.bbox(tk.INSERT)
        except tk.TclError:
            bbox = None
        if not bbox:
            return False

        x, y, w, h = bbox
        # Convert text-relative to screen-relative
        x_root = self.text.winfo_rootx() + x + 8
        y_root = self.text.winfo_rooty() + y + h + 4

        # Keep on screen
        sw = self.text.winfo_screenwidth()
        sh = self.text.winfo_screenheight()
        try:
            self._tipwindow.update_idletasks()
            tw_w = self._tipwindow.winfo_reqwidth()
            tw_h = self._tipwindow.winfo_reqheight()
        except tk.TclError:
            return False

        if x_root + tw_w > sw - 20:
            x_root = sw - tw_w - 20
        if y_root + tw_h > sh - 20:
            y_root = y_root - h - tw_h - 8  # flip above

        try:
            self._tipwindow.wm_geometry(f'+{int(x_root)}+{int(y_root)}')
        except tk.TclError:
            return False
        return True

    def _show_window(self):
        """Map the tooltip and keep it above the editor on macOS/Tk Aqua."""
        try:
            self._tipwindow.deiconify()
            self._tipwindow.lift(self.text.winfo_toplevel())
        except tk.TclError:
            return

        try:
            windowing_system = self.text.tk.call('tk', 'windowingsystem')
        except tk.TclError:
            windowing_system = ''

        if windowing_system == 'aqua':
            self.text.after_idle(self._raise_tipwindow)

    def _apply_platform_style(self, window):
        """Apply native tooltip-like behavior where Tk supports it."""
        try:
            windowing_system = self.text.tk.call('tk', 'windowingsystem')
        except tk.TclError:
            return

        if windowing_system != 'aqua':
            return

        try:
            self.text.tk.call(
                '::tk::unsupported::MacWindowStyle',
                'style',
                window._w,
                'help',
                'noActivates',
            )
        except tk.TclError:
            pass

    def _raise_tipwindow(self):
        if not self._tipwindow:
            return
        try:
            self._tipwindow.lift()
            self._tipwindow.wm_attributes('-topmost', True)
        except tk.TclError:
            pass

    def _reset_hide_timer(self):
        """Reset the auto-hide countdown."""
        if self._hide_after_id is not None:
            try:
                self.text.after_cancel(self._hide_after_id)
            except (ValueError, tk.TclError):
                pass
        self._hide_after_id = self.text.after(
            self.HIDE_DELAY, self.hidetip)

    def _word_at_insert(self):
        """Return the Python keyword at the insert cursor, or None."""
        try:
            index = self.text.index(tk.INSERT)
        except tk.TclError:
            return None

        try:
            line = self.text.get(f'{index} linestart', f'{index} lineend')
        except tk.TclError:
            return None

        try:
            column = int(index.split('.')[1])
        except (IndexError, ValueError):
            return None

        for match in re.finditer(r'[A-Za-z_][A-Za-z0-9_]*', line):
            # Treat the insertion point as being on the word while the cursor is
            # inside it or just after the last typed character.
            if match.start() <= column <= match.end():
                word = match.group(0)
                if keyword.iskeyword(word):
                    return word
                return None
        return None
