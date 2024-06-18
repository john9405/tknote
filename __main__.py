import tkinter as tk
from tkinter import messagebox, simpledialog
import dbm

# 创建或打开数据库
db = dbm.open('notes', 'c')

# 创建主窗口
root = tk.Tk()
root.title("简单笔记软件")

# 创建左右布局
frame = tk.Frame(root)
frame.pack(expand=True, fill=tk.BOTH)

# 创建左侧笔记列表框
listbox = tk.Listbox(frame)
listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

# 创建右侧文本框，用于显示和编辑笔记
text = tk.Text(frame, wrap=tk.WORD)
text.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

# 显示所有笔记标题
def show_notes():
    listbox.delete(0, tk.END)
    for key in db.keys():
        listbox.insert(tk.END, key.decode())

# 显示选中的笔记内容
def show_note_content(event):
    selected_note = listbox.get(tk.ACTIVE)
    if selected_note:
        text.delete(1.0, tk.END)
        text.insert(tk.END, db[selected_note].decode())

# 保存笔记
def save_note():
    note_title = simpledialog.askstring("输入笔记标题", "标题:")
    if note_title:
        note_content = text.get("1.0", tk.END).strip()
        db[note_title] = note_content
        show_notes()

# 删除笔记
def delete_note():
    note_title = simpledialog.askstring("删除笔记", "输入要删除的笔记标题:")
    if note_title and note_title in db:
        del db[note_title]
        show_notes()
    else:
        messagebox.showerror("错误", "笔记标题不存在")

# 创建菜单
menu = tk.Menu(root)
root.config(menu=menu)

# 创建文件菜单
file_menu = tk.Menu(menu)
menu.add_cascade(label="文件", menu=file_menu)
file_menu.add_command(label="显示所有笔记", command=show_notes)
file_menu.add_command(label="保存笔记", command=save_note)
file_menu.add_command(label="删除笔记", command=delete_note)
file_menu.add_separator()
file_menu.add_command(label="退出", command=root.quit)

# 绑定列表框选择事件
listbox.bind('<<ListboxSelect>>', show_note_content)

# 运行主循环
show_notes()
root.mainloop()

# 关闭数据库
db.close()
