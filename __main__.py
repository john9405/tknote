import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, simpledialog, messagebox
from tkhtmlview import HTMLLabel
import markdown
import tempfile
import os
import re
import subprocess
import webbrowser


class MarkdownEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("Markdown Editor")
        self.root.geometry("1200x700")

        self.current_file = None
        self.current_folder = None
        self.preview_visible = True
        self.setup_ui()

    def setup_ui(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="New", command=self.new_file, accelerator="Cmd+N")
        file_menu.add_command(label="Open", command=self.open_file, accelerator="Cmd+O")
        file_menu.add_command(label="Open Folder", command=self.open_folder, accelerator="Cmd+Shift+O")
        file_menu.add_command(label="Save", command=self.save_file, accelerator="Cmd+S")
        file_menu.add_command(label="Save As", command=self.save_file_as, accelerator="Cmd+Shift+S")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)

        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(label="Bold", command=lambda: self.insert_format("**", "**"), accelerator="Cmd+B")
        edit_menu.add_command(label="Italic", command=lambda: self.insert_format("*", "*"), accelerator="Cmd+I")
        edit_menu.add_command(label="Heading", command=self.insert_heading, accelerator="Cmd+H")
        edit_menu.add_command(label="Link", command=self.insert_link, accelerator="Cmd+K")
        edit_menu.add_command(label="Code", command=lambda: self.insert_format("`", "`"), accelerator="Cmd+Shift+C")

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Toggle Preview", command=self.toggle_preview, accelerator="Cmd+\\")
        view_menu.add_separator()
        view_menu.add_command(label="Preview in Browser", command=self.preview_in_browser, accelerator="Cmd+P")
        view_menu.add_command(label="Refresh Preview", command=self.update_preview, accelerator="Cmd+R")

        search_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Search", menu=search_menu)
        search_menu.add_command(label="Find in File", command=self.show_find_dialog, accelerator="Cmd+F")
        search_menu.add_command(label="Search in Files", command=self.show_search_dialog, accelerator="Cmd+Shift+F")

        git_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Git", menu=git_menu)
        git_menu.add_command(label="Clone Repository", command=self.git_clone)
        git_menu.add_command(label="Init Repository", command=self.git_init)
        git_menu.add_command(label="Set Remote", command=self.git_set_remote)
        git_menu.add_separator()
        git_menu.add_command(label="Commit", command=self.git_commit, accelerator="Cmd+Shift+C")
        git_menu.add_command(label="Pull", command=self.git_pull)
        git_menu.add_command(label="Push", command=self.git_push)
        git_menu.add_separator()
        git_menu.add_command(label="Show Log", command=self.show_git_log)

        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        tree_frame = ttk.Frame(paned)
        left_frame = ttk.Frame(paned)
        right_frame = ttk.Frame(paned)

        paned.add(tree_frame, weight=0)
        paned.add(left_frame, weight=1)
        self.right_frame = right_frame
        paned.add(right_frame, weight=1)

        # File tree header with buttons
        tree_header = ttk.Frame(tree_frame)
        tree_header.pack(fill=tk.X)
        ttk.Label(tree_header, text="Files", font=("Helvetica", 10, "bold")).pack(side=tk.LEFT, padx=(0, 5))

        btn_frame = ttk.Frame(tree_header)
        btn_frame.pack(side=tk.RIGHT)

        new_btn = ttk.Button(btn_frame, text="+", width=1, command=self.show_new_menu)
        new_btn.pack(side=tk.LEFT, padx=1)

        refresh_btn = ttk.Button(btn_frame, text="⟳", width=1, command=self.refresh_file_tree)
        refresh_btn.pack(side=tk.LEFT, padx=1)

        tree_scroll = ttk.Scrollbar(tree_frame)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.file_tree = ttk.Treeview(tree_frame, yscrollcommand=tree_scroll.set, selectmode="browse")
        self.file_tree.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self.file_tree.yview)
        self.file_tree.bind("<Double-1>", self.on_tree_double_click)
        self.file_tree.bind("<Button-2>", self.show_context_menu)
        self.file_tree.bind("<Button-3>", self.show_context_menu)
        self.file_tree.bind("<Control-Button-1>", self.show_context_menu)

        # Context menu
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="New File", command=self.new_file_in_tree)
        self.context_menu.add_command(label="New Folder", command=self.new_folder_in_tree)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Rename", command=self.rename_selected)
        self.context_menu.add_command(label="Move", command=self.move_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Delete", command=self.delete_selected)

        ttk.Label(left_frame, text="Editor", font=("Helvetica", 10, "bold")).pack(anchor="w")

        self.editor = scrolledtext.ScrolledText(
            left_frame,
            wrap=tk.WORD,
            font=("Monaco", 12),
            tabstyle="wordprocessor",
            insertbackground="black"
        )
        self.editor.pack(fill=tk.BOTH, expand=True)

        # --- Syntax highlighting tags ---
        self.editor.tag_config("hl_header", foreground="#1a56db", font=("Monaco", 14, "bold"))
        self.editor.tag_config("hl_header2", foreground="#2563eb", font=("Monaco", 13, "bold"))
        self.editor.tag_config("hl_header3", foreground="#3b82f6", font=("Monaco", 12, "bold"))
        self.editor.tag_config("hl_bold", font=("Monaco", 12, "bold"))
        self.editor.tag_config("hl_italic", font=("Monaco", 12, "italic"))
        self.editor.tag_config("hl_code_inline", foreground="#b91c1c", background="#f1f5f9",
                              font=("Monaco", 11))
        self.editor.tag_config("hl_code_block", foreground="#374151", background="#f1f5f9",
                              font=("Monaco", 11), lmargin1=10, lmargin2=10)
        self.editor.tag_config("hl_link_text", foreground="#059669", underline=True)
        self.editor.tag_config("hl_link_url", foreground="#6b7280", font=("Monaco", 10))
        self.editor.tag_config("hl_image", foreground="#7c3aed")
        self.editor.tag_config("hl_blockquote", foreground="#78716c", font=("Monaco", 12, "italic"),
                              lmargin1=10, lmargin2=10)
        self.editor.tag_config("hl_hr", foreground="#d1d5db")
        self.editor.tag_config("hl_list", foreground="#0891b2", font=("Monaco", 12, "bold"))

        self.editor.bind("<KeyRelease>", self.on_text_change)
        self.editor.bind("<Command-n>", lambda _: self.new_file())
        self.editor.bind("<Command-o>", lambda _: self.open_file())
        self.editor.bind("<Command-s>", lambda _: self.save_file())
        self.editor.bind("<Command-Shift-S>", lambda _: self.save_file_as())
        self.editor.bind("<Command-b>", lambda _: self.insert_format("**", "**"))
        self.editor.bind("<Command-i>", lambda _: self.insert_format("*", "*"))
        self.editor.bind("<Command-h>", lambda _: self.insert_heading())
        self.editor.bind("<Command-k>", lambda _: self.insert_link())
        self.editor.bind("<Command-Shift-c>", lambda _: self.insert_format("`", "`"))
        self.editor.bind("<Command-p>", lambda _: self.preview_in_browser())
        self.editor.bind("<Command-r>", lambda _: self.update_preview())
        self.editor.bind("<Command-backslash>", lambda _: self.toggle_preview())
        self.editor.bind("<Command-f>", lambda _: self.show_find_dialog())
        self.editor.bind("<Command-Shift-f>", lambda _: self.show_search_dialog())

        ttk.Label(right_frame, text="Preview", font=("Helvetica", 10, "bold")).pack(anchor="w")

        self.preview = HTMLLabel(right_frame)
        self.preview.pack(fill=tk.BOTH, expand=True)

        self.preview.set_html('')

        self.status_bar = ttk.Label(main_frame, text="Ready", relief=tk.SUNKEN, anchor="w")
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def new_file(self):
        self.current_file = None
        self.editor.delete(1.0, tk.END)
        self.update_preview()
        self.status_bar.config(text="New file")

    def open_file(self):
        file_path = filedialog.askopenfilename(
            title="Open Markdown File",
            filetypes=[("Markdown files", "*.md *.markdown"), ("Text files", "*.txt"), ("All files", "*.*")]
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.editor.delete(1.0, tk.END)
                self.editor.insert(tk.END, content)
                self.current_file = file_path
                self._highlight_syntax()
                self.update_preview()
                self.status_bar.config(text=f"Opened: {os.path.basename(file_path)}")
            except Exception as e:
                self.status_bar.config(text=f"Error opening file: {e}")

    def open_folder(self):
        folder_path = filedialog.askdirectory(title="Open Folder", initialdir=os.path.expanduser('~'))
        if folder_path:
            self.current_folder = folder_path
            self.populate_file_tree(folder_path)
            self.status_bar.config(text=f"Opened folder: {os.path.basename(folder_path)}")

    def populate_file_tree(self, folder_path):
        self.file_tree.delete(*self.file_tree.get_children())

        def add_tree_items(path, parent_id):
            try:
                items = sorted(os.listdir(path), key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
                for item in items:
                    full_path = os.path.join(path, item)
                    if os.path.isdir(full_path):
                        # Skip hidden directories
                        if item.startswith('.'):
                            continue
                        node_id = self.file_tree.insert(parent_id, "end", text=f"📁 {item}", values=(full_path,), tags=("dir",), open=False)
                        add_tree_items(full_path, node_id)
                    elif item.endswith(('.md', '.markdown')):
                        self.file_tree.insert(parent_id, "end", text=f"📝 {item}", values=(full_path,), tags=("file",))
                    elif item.endswith('.txt'):
                        self.file_tree.insert(parent_id, "end", text=f"📄 {item}", values=(full_path,), tags=("file",))
            except PermissionError:
                pass

        add_tree_items(folder_path, "")

    def refresh_file_tree(self):
        if self.current_folder:
            self.populate_file_tree(self.current_folder)
            self.status_bar.config(text="File tree refreshed")
        else:
            self.status_bar.config(text="No folder opened")

    def on_tree_double_click(self, _):
        selection = self.file_tree.selection()
        if selection:
            item = selection[0]
            values = self.file_tree.item(item, "values")
            if values:
                file_path = values[0]
                if os.path.isfile(file_path) and file_path.endswith(('.md', '.markdown', '.txt')):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        self.editor.delete(1.0, tk.END)
                        self.editor.insert(tk.END, content)
                        self.current_file = file_path
                        self._highlight_syntax()
                        self.update_preview()
                        self.status_bar.config(text=f"Opened: {os.path.basename(file_path)}")
                    except Exception as e:
                        self.status_bar.config(text=f"Error opening file: {e}")

    def save_file(self):
        if self.current_file:
            self._save_to_file(self.current_file)
        else:
            self.save_file_as()

    def save_file_as(self):
        file_path = filedialog.asksaveasfilename(
            title="Save Markdown File",
            defaultextension=".md",
            filetypes=[("Markdown files", "*.md"), ("Text files", "*.txt"), ("All files", "*.*")]
        )
        if file_path:
            self.current_file = file_path
            self._save_to_file(file_path)

    def _save_to_file(self, file_path):
        try:
            content = self.editor.get(1.0, tk.END)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.status_bar.config(text=f"Saved: {os.path.basename(file_path)}")
        except Exception as e:
            self.status_bar.config(text=f"Error saving file: {e}")

    def _highlight_syntax(self):
        """Apply Markdown syntax highlighting to the editor content."""
        content = self.editor.get("1.0", tk.END)

        # Remove all previous highlight tags
        for tag in (
            "hl_header", "hl_header2", "hl_header3",
            "hl_bold", "hl_italic", "hl_code_inline", "hl_code_block",
            "hl_link_text", "hl_link_url", "hl_image",
            "hl_blockquote", "hl_hr", "hl_list",
        ):
            self.editor.tag_remove(tag, "1.0", tk.END)

        # --- 1. Code blocks (``` ... ```) --- must run first so we can exclude them
        code_block_ranges = []
        for m in re.finditer(r'```.*?\n(.*?)```', content, re.DOTALL):
            start_idx = f"1.0 + {m.start(1)} chars"
            end_idx = f"1.0 + {m.end(1)} chars"
            self.editor.tag_add("hl_code_block", start_idx, end_idx)
            code_block_ranges.append((m.start(), m.end()))

        def _inside_code_block(pos):
            for cs, ce in code_block_ranges:
                if cs <= pos < ce:
                    return True
            return False

        # --- 2. Block-level elements ---

        # Headers (line-start # ## ### etc.)
        for m in re.finditer(r'^(#{1,6})\s+(.+)$', content, re.MULTILINE):
            if _inside_code_block(m.start()):
                continue
            level = len(m.group(1))
            tag = {1: "hl_header", 2: "hl_header2", 3: "hl_header3"}.get(level, "hl_header3")
            line_start = f"1.0 + {m.start()} chars"
            line_end = f"1.0 + {m.end()} chars"
            self.editor.tag_add(tag, line_start, line_end)

        # Horizontal rules (---, ***, ___ alone on a line)
        for m in re.finditer(r'^(\-{3,}|\*{3,}|_{3,})\s*$', content, re.MULTILINE):
            if _inside_code_block(m.start()):
                continue
            line_start = f"1.0 + {m.start()} chars"
            line_end = f"1.0 + {m.end()} chars"
            self.editor.tag_add("hl_hr", line_start, line_end)

        # Blockquotes (> at line start)
        for m in re.finditer(r'^>\s?(.*)$', content, re.MULTILINE):
            if _inside_code_block(m.start()):
                continue
            line_start = f"1.0 + {m.start()} chars"
            line_end = f"1.0 + {m.end()} chars"
            self.editor.tag_add("hl_blockquote", line_start, line_end)

        # List markers (- or * or + at line start, followed by space)
        for m in re.finditer(r'^(\s*)([\-\*\+])\s(?=.+)', content, re.MULTILINE):
            if _inside_code_block(m.start()):
                continue
            marker_start = f"1.0 + {m.start(2)} chars"
            marker_end = f"1.0 + {m.end(2)} chars"
            self.editor.tag_add("hl_list", marker_start, marker_end)

        # Numbered lists
        for m in re.finditer(r'^(\s*)(\d+\.)\s(?=.+)', content, re.MULTILINE):
            if _inside_code_block(m.start()):
                continue
            marker_start = f"1.0 + {m.start(2)} chars"
            marker_end = f"1.0 + {m.end(2)} chars"
            self.editor.tag_add("hl_list", marker_start, marker_end)

        # --- 3. Inline elements ---

        # Images ![alt](url) — match before links
        for m in re.finditer(r'!\[([^\]]*)\]\(([^\)]+)\)', content):
            if _inside_code_block(m.start()):
                continue
            start_idx = f"1.0 + {m.start()} chars"
            end_idx = f"1.0 + {m.end()} chars"
            self.editor.tag_add("hl_image", start_idx, end_idx)

        # Links [text](url)
        for m in re.finditer(r'(?<!!)\[([^\]]+)\]\(([^\)]+)\)', content):
            if _inside_code_block(m.start()):
                continue
            # Link text part
            text_start = f"1.0 + {m.start(1)} chars"
            text_end = f"1.0 + {m.end(1)} chars"
            self.editor.tag_add("hl_link_text", f"1.0 + {m.start()} chars", f"1.0 + {m.start(1) - 1} chars")
            self.editor.tag_add("hl_link_text", f"1.0 + {m.end(1) + 1} chars", f"1.0 + {m.end(1) + 1 + len(m.group(2)) + 1} chars")
            self.editor.tag_add("hl_link_text", text_start, text_end)
            # URL part
            url_start = f"1.0 + {m.start(2)} chars"
            url_end = f"1.0 + {m.end(2)} chars"
            self.editor.tag_add("hl_link_url", url_start, url_end)

        # Bold **text** or __text__
        for m in re.finditer(r'\*\*(.+?)\*\*|__(.+?)__', content):
            if _inside_code_block(m.start()):
                continue
            start_idx = f"1.0 + {m.start()} chars"
            end_idx = f"1.0 + {m.end()} chars"
            self.editor.tag_add("hl_bold", start_idx, end_idx)

        # Italic *text* or _text_ (but not ** or __)
        for m in re.finditer(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', content):
            if _inside_code_block(m.start()):
                continue
            start_idx = f"1.0 + {m.start()} chars"
            end_idx = f"1.0 + {m.end()} chars"
            self.editor.tag_add("hl_italic", start_idx, end_idx)

        # Inline code `text`
        for m in re.finditer(r'`([^`]+)`', content):
            if _inside_code_block(m.start()):
                continue
            start_idx = f"1.0 + {m.start()} chars"
            end_idx = f"1.0 + {m.end()} chars"
            self.editor.tag_add("hl_code_inline", start_idx, end_idx)

    def on_text_change(self, event=None):
        self._highlight_syntax()
        self.update_preview()

    def update_preview(self):
        content = self.editor.get(1.0, tk.END)
        html = markdown.markdown(
            content,
            extensions=['tables', 'fenced_code', 'nl2br', 'sane_lists']
        )

        styled_html = f'''<div style="font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;padding:20px;line-height:1.6;color:#24292f;">
            {self._apply_inline_styles(html)}
        </div>'''
        self.preview.set_html(styled_html)

    def _apply_inline_styles(self, html):
        import re

        html = re.sub(r'<h1>(.*?)</h1>', r'<h1 style="font-size:2em;border-bottom:1px solid #d0d7de;padding-bottom:0.3em;margin-top:1.5em;margin-bottom:0.5em;font-weight:600;">\1</h1>', html)
        html = re.sub(r'<h2>(.*?)</h2>', r'<h2 style="font-size:1.5em;border-bottom:1px solid #d0d7de;padding-bottom:0.3em;margin-top:1.5em;margin-bottom:0.5em;font-weight:600;">\1</h2>', html)
        html = re.sub(r'<h3>(.*?)</h3>', r'<h3 style="font-size:1.25em;margin-top:1.5em;margin-bottom:0.5em;font-weight:600;">\1</h3>', html)
        html = re.sub(r'<h4>(.*?)</h4>', r'<h4 style="font-size:1em;margin-top:1.5em;margin-bottom:0.5em;font-weight:600;">\1</h4>', html)
        html = re.sub(r'<code>(.*?)</code>', r'<code style="background-color:#f6f8fa;padding:2px 6px;border-radius:3px;font-family:"Monaco","Courier New",monospace;font-size:0.9em;">\1</code>', html)
        html = re.sub(r'<pre>(.*?)</pre>', r'<pre style="background-color:#f6f8fa;padding:16px;border-radius:6px;overflow-x:auto;">\1</pre>', html)
        html = re.sub(r'<blockquote>(.*?)</blockquote>', r'<blockquote style="border-left:4px solid #d0d7de;padding-left:16px;color:#57606a;margin:0;">\1</blockquote>', html)
        html = re.sub(r'<a href="(.*?)">(.*?)</a>', r'<a href="\1" style="color:#0969da;text-decoration:none;">\2</a>', html)
        html = re.sub(r'<ul>(.*?)</ul>', r'<ul style="padding-left:2em;">\1</ul>', html)
        html = re.sub(r'<ol>(.*?)</ol>', r'<ol style="padding-left:2em;">\1</ol>', html)
        html = re.sub(r'<hr ?/?>', r'<hr style="border:none;border-top:1px solid #d0d7de;margin:2em 0;">', html)
        html = re.sub(r'<p>(.*?)</p>', r'<p style="margin:0.5em 0;">\1</p>', html)

        return html

    def toggle_preview(self):
        self.preview_visible = not self.preview_visible
        if self.preview_visible:
            paned = self.right_frame.master
            paned.add(self.right_frame, weight=1)
            self.status_bar.config(text="Preview shown")
        else:
            paned = self.right_frame.master
            paned.forget(self.right_frame)
            self.status_bar.config(text="Preview hidden")

    def preview_in_browser(self):
        content = self.editor.get(1.0, tk.END)
        html = markdown.markdown(
            content,
            extensions=['tables', 'fenced_code', 'nl2br', 'sane_lists']
        )

        styled_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Markdown Preview</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; padding: 40px; max-width: 800px; margin: 0 auto; line-height: 1.6; color: #24292f; }}
                h1, h2, h3, h4, h5, h6 {{ margin-top: 1.5em; margin-bottom: 0.5em; font-weight: 600; }}
                h1 {{ font-size: 2em; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }}
                h2 {{ font-size: 1.5em; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }}
                code {{ background-color: #f6f8fa; padding: 2px 6px; border-radius: 3px; font-family: 'Monaco', 'Courier New', monospace; font-size: 0.9em; }}
                pre {{ background-color: #f6f8fa; padding: 16px; border-radius: 6px; overflow-x: auto; }}
                pre code {{ background-color: transparent; padding: 0; }}
                blockquote {{ border-left: 4px solid #d0d7de; padding-left: 16px; color: #57606a; margin: 0; }}
                table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
                th, td {{ border: 1px solid #d0d7de; padding: 8px 12px; text-align: left; }}
                th {{ background-color: #f6f8fa; font-weight: 600; }}
                a {{ color: #0969da; text-decoration: none; }}
                a:hover {{ text-decoration: underline; }}
                img {{ max-width: 100%; }}
                hr {{ border: none; border-top: 1px solid #d0d7de; margin: 2em 0; }}
                ul, ol {{ padding-left: 2em; }}
            </style>
        </head>
        <body>
            {html}
        </body>
        </html>
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write(styled_html)
            temp_path = f.name

        webbrowser.open('file://' + temp_path)
        self.status_bar.config(text="Opened preview in browser")

    def insert_format(self, before, after):
        try:
            sel_start = self.editor.index(tk.SEL_FIRST)
            sel_end = self.editor.index(tk.SEL_LAST)
            selected = self.editor.get(sel_start, sel_end)
            self.editor.delete(sel_start, sel_end)
            self.editor.insert(sel_start, f"{before}{selected}{after}")
        except:
            self.editor.insert(tk.INSERT, f"{before}{after}")

    def insert_heading(self):
        line_start = self.editor.index("insert linestart")
        line = self.editor.get(line_start, line_start + " lineend")
        if line.startswith("# "):
            self.editor.delete(line_start, line_start + "+2 chars")
            self.editor.insert(line_start, "## ")
        elif line.startswith("## "):
            self.editor.delete(line_start, line_start + "+3 chars")
            self.editor.insert(line_start, "### ")
        elif line.startswith("### "):
            self.editor.delete(line_start, line_start + "+4 chars")
            self.editor.insert(line_start, "#### ")
        else:
            self.editor.insert(line_start, "# ")

    def insert_link(self):
        try:
            sel_start = self.editor.index(tk.SEL_FIRST)
            sel_end = self.editor.index(tk.SEL_LAST)
            selected = self.editor.get(sel_start, sel_end)
            self.editor.delete(sel_start, sel_end)
            self.editor.insert(sel_start, f"[{selected}](url)")
        except:
            self.editor.insert(tk.INSERT, "[text](url)")

    def get_selected_path(self):
        selection = self.file_tree.selection()
        if selection:
            item = selection[0]
            values = self.file_tree.item(item, "values")
            if values:
                return values[0]
        return self.current_folder

    def show_new_menu(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="New File", command=self.new_file_in_tree)
        menu.add_command(label="New Folder", command=self.new_folder_in_tree)
        menu.post(self.file_tree.winfo_rootx() + 50, self.file_tree.winfo_rooty() + 10)

    def show_context_menu(self, event):
        if not self.current_folder:
            return
        # Select the item under cursor
        item = self.file_tree.identify_row(event.y)
        if item:
            self.file_tree.selection_set(item)
        self.context_menu.post(event.x_root, event.y_root)

    def new_file_in_tree(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        parent_path = self.get_selected_path()
        if parent_path and os.path.isfile(parent_path):
            parent_path = os.path.dirname(parent_path)

        if parent_path:
            from tkinter import simpledialog
            filename = simpledialog.askstring("New File", "Enter file name:", parent=self.root)
            if filename:
                if not filename.endswith('.md'):
                    filename += '.md'
                new_file_path = os.path.join(parent_path, filename)
                try:
                    with open(new_file_path, 'w', encoding='utf-8') as f:
                        f.write('')
                    self.populate_file_tree(self.current_folder)
                    self.status_bar.config(text=f"Created: {filename}")
                except Exception as e:
                    self.status_bar.config(text=f"Error creating file: {e}")

    def new_folder_in_tree(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        parent_path = self.get_selected_path()
        if parent_path and os.path.isfile(parent_path):
            parent_path = os.path.dirname(parent_path)

        if parent_path:
            from tkinter import simpledialog
            foldername = simpledialog.askstring("New Folder", "Enter folder name:", parent=self.root)
            if foldername:
                new_folder_path = os.path.join(parent_path, foldername)
                try:
                    os.makedirs(new_folder_path, exist_ok=False)
                    self.populate_file_tree(self.current_folder)
                    self.status_bar.config(text=f"Created folder: {foldername}")
                except FileExistsError:
                    self.status_bar.config(text=f"Folder already exists: {foldername}")
                except Exception as e:
                    self.status_bar.config(text=f"Error creating folder: {e}")

    def delete_selected(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        selection = self.file_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.file_tree.item(item, "values")
        if not values:
            return

        path = values[0]
        item_text = self.file_tree.item(item, "text")
        name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        from tkinter import messagebox
        item_type = "folder" if os.path.isdir(path) else "file"
        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete the {item_type} '{name}'?",
            parent=self.root
        )

        if confirm:
            try:
                if os.path.isdir(path):
                    import shutil
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                self.populate_file_tree(self.current_folder)
                self.status_bar.config(text=f"Deleted: {name}")
                if self.current_file == path:
                    self.current_file = None
                    self.editor.delete(1.0, tk.END)
                    self.update_preview()
            except Exception as e:
                self.status_bar.config(text=f"Error deleting: {e}")

    def rename_selected(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        selection = self.file_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.file_tree.item(item, "values")
        if not values:
            return

        path = values[0]
        item_text = self.file_tree.item(item, "text")
        old_name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        from tkinter import simpledialog
        new_name = simpledialog.askstring("Rename", "Enter new name:", initialvalue=old_name, parent=self.root)
        if new_name and new_name != old_name:
            parent_dir = os.path.dirname(path)
            new_path = os.path.join(parent_dir, new_name)
            try:
                import shutil
                shutil.move(path, new_path)
                self.populate_file_tree(self.current_folder)
                if self.current_file == path:
                    self.current_file = new_path
                self.status_bar.config(text=f"Renamed to: {new_name}")
            except Exception as e:
                self.status_bar.config(text=f"Error renaming: {e}")

    def move_selected(self):
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        selection = self.file_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.file_tree.item(item, "values")
        if not values:
            return

        source_path = values[0]
        item_text = self.file_tree.item(item, "text")
        name = item_text.split(' ', 1)[1] if ' ' in item_text else item_text

        # Ask for destination directory
        dest_dir = filedialog.askdirectory(title="Select destination folder", initialdir=self.current_folder)
        if dest_dir:
            dest_path = os.path.join(dest_dir, name)
            try:
                import shutil
                # Check if destination already exists
                if os.path.exists(dest_path):
                    from tkinter import messagebox
                    overwrite = messagebox.askyesno(
                        "File Exists",
                        f"'{name}' already exists in the destination. Overwrite?",
                        parent=self.root
                    )
                    if not overwrite:
                        return
                    if os.path.isdir(dest_path):
                        shutil.rmtree(dest_path)
                    else:
                        os.remove(dest_path)
                shutil.move(source_path, dest_path)
                self.populate_file_tree(self.current_folder)
                if self.current_file == source_path:
                    self.current_file = dest_path
                self.status_bar.config(text=f"Moved to: {dest_dir}")
            except Exception as e:
                self.status_bar.config(text=f"Error moving: {e}")

    def show_find_dialog(self):
        """Find text in current file"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Find in File")
        dialog.geometry("400x120")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Find:").pack(anchor="w", padx=10, pady=(10, 5))

        search_var = tk.StringVar()
        search_entry = ttk.Entry(dialog, textvariable=search_var)
        search_entry.pack(fill=tk.X, padx=10, pady=5)
        search_entry.focus()

        case_var = tk.BooleanVar()

        options_frame = ttk.Frame(dialog)
        options_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Checkbutton(options_frame, text="Case sensitive", variable=case_var).pack(side=tk.LEFT)

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        def do_find():
            query = search_var.get()
            if not query:
                return
            self.find_in_editor(query, case_var.get())

        ttk.Button(button_frame, text="Find", command=do_find).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

        search_entry.bind("<Return>", lambda _: do_find())

    def find_in_editor(self, query, case_sensitive=False):
        """Find and highlight text in editor"""
        content = self.editor.get("1.0", tk.END)
        flags = 0 if case_sensitive else re.IGNORECASE

        # Remove old tags
        self.editor.tag_remove("found", "1.0", tk.END)

        try:
            for match in re.finditer(re.escape(query), content, flags):
                start_idx = f"1.0 + {match.start()} chars"
                end_idx = f"1.0 + {match.end()} chars"
                self.editor.tag_add("found", start_idx, end_idx)
            self.editor.tag_config("found", background="yellow", foreground="black")

            # Find first match
            first_match = self.editor.search(f"\\m{re.escape(query)}\\M", "1.0", forwards=True,
                                             regexp=1, nocase=not case_sensitive)
            if first_match:
                self.editor.mark_set("insert", first_match)
                self.editor.see(first_match)
                self.status_bar.config(text=f"Found matches")
            else:
                self.status_bar.config(text="No matches found")
        except re.error:
            self.status_bar.config(text="Invalid search pattern")

    def show_search_dialog(self):
        """Search across all files in folder"""
        if not self.current_folder:
            self.status_bar.config(text="No folder opened")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Search in Files")
        dialog.geometry("500x350")
        dialog.transient(self.root)
        dialog.grab_set()

        # Search input
        input_frame = ttk.Frame(dialog)
        input_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Label(input_frame, text="Search:").pack(side=tk.LEFT)

        search_var = tk.StringVar()
        search_entry = ttk.Entry(input_frame, textvariable=search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        search_entry.focus()

        case_var = tk.BooleanVar()
        ttk.Checkbutton(input_frame, text="Case sensitive", variable=case_var).pack(side=tk.LEFT)

        # Results list
        ttk.Label(dialog, text="Results:").pack(anchor="w", padx=10, pady=(10, 5))

        result_frame = ttk.Frame(dialog)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        result_scroll = ttk.Scrollbar(result_frame)
        result_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        result_list = tk.Listbox(result_frame, yscrollcommand=result_scroll.set)
        result_list.pack(fill=tk.BOTH, expand=True)
        result_scroll.config(command=result_list.yview)

        def do_search():
            query = search_var.get()
            if not query or not self.current_folder:
                return
            result_list.delete(0, tk.END)
            results = self.search_in_files(self.current_folder, query, case_var.get())
            for file_path, matches in results:
                rel_path = os.path.relpath(file_path, self.current_folder)
                result_list.insert(tk.END, f"{rel_path} ({matches} matches)")

        def open_selected(_):
            selection = result_list.curselection()
            if selection:
                item = result_list.get(selection[0])
                file_name = item.split(' (')[0]
                file_path = os.path.join(self.current_folder, file_name)
                query = search_var.get()
                if os.path.isfile(file_path):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        self.editor.delete(1.0, tk.END)
                        self.editor.insert(tk.END, content)
                        self.current_file = file_path
                        self._highlight_syntax()
                        self.update_preview()
                        self.status_bar.config(text=f"Opened: {file_name}")
                        # Highlight and jump to first match
                        if query:
                            self.find_in_editor(query, case_var.get())
                    except Exception as e:
                        self.status_bar.config(text=f"Error opening file: {e}")

        result_list.bind("<Double-Button-1>", open_selected)

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        ttk.Button(button_frame, text="Search", command=do_search).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

        search_entry.bind("<Return>", lambda _: do_search())

    def search_in_files(self, folder, query, case_sensitive=False):
        """Search for text in all markdown files"""
        import re
        results = []
        flags = 0 if case_sensitive else re.IGNORECASE

        def search_dir(path):
            try:
                for item in os.listdir(path):
                    full_path = os.path.join(path, item)
                    if os.path.isdir(full_path):
                        if not item.startswith('.'):
                            search_dir(full_path)
                    elif item.endswith(('.md', '.markdown', '.txt')):
                        try:
                            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                                matches = len(list(re.finditer(re.escape(query), content, flags)))
                                if matches > 0:
                                    results.append((full_path, matches))
                        except Exception:
                            pass
            except PermissionError:
                pass

        search_dir(folder)
        return sorted(results, key=lambda x: x[0])

    def run_git_command(self, args, cwd=None):
        """Run a git command and return the result"""
        if not cwd:
            cwd = self.current_folder
        try:
            result = subprocess.run(
                ['git'] + args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError:
            return 1, "", "Git not found. Please install git."
        except subprocess.TimeoutExpired:
            return 1, "", "Command timed out"
        except Exception as e:
            return 1, "", str(e)

    def git_clone(self):
        """Clone a git repository"""
        # Create clone dialog
        clone_dialog = tk.Toplevel(self.root)
        clone_dialog.title("Clone Repository")
        clone_dialog.geometry("500x200")
        clone_dialog.transient(self.root)
        clone_dialog.grab_set()

        ttk.Label(clone_dialog, text="Repository URL:").pack(anchor="w", padx=10, pady=(10, 5))

        url_var = tk.StringVar()
        url_entry = ttk.Entry(clone_dialog, textvariable=url_var)
        url_entry.pack(fill=tk.X, padx=10, pady=5)
        url_entry.focus()

        ttk.Label(clone_dialog, text="Destination Directory:").pack(anchor="w", padx=10, pady=(10, 5))

        dest_frame = ttk.Frame(clone_dialog)
        dest_frame.pack(fill=tk.X, padx=10, pady=5)

        dest_var = tk.StringVar(value=os.path.expanduser('~'))
        dest_entry = ttk.Entry(dest_frame, textvariable=dest_var)
        dest_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def browse_dest():
            dest_dir = filedialog.askdirectory(title="Select Destination", initialdir=dest_var.get())
            if dest_dir:
                dest_var.set(dest_dir)

        ttk.Button(dest_frame, text="Browse...", command=browse_dest).pack(side=tk.LEFT, padx=5)

        def do_clone():
            url = url_var.get().strip()
            dest = dest_var.get().strip()

            if not url:
                messagebox.showwarning("Missing URL", "Please enter repository URL", parent=clone_dialog)
                return

            if not dest:
                messagebox.showwarning("Missing Destination", "Please select destination directory", parent=clone_dialog)
                return

            # Clone repository
            try:
                result = subprocess.run(
                    ['git', 'clone', url, dest],
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if result.returncode == 0:
                    self.status_bar.config(text="Repository cloned successfully")
                    clone_dialog.destroy()
                    messagebox.showinfo("Success", "Repository cloned successfully", parent=self.root)
                    # Open the cloned repository
                    self.current_folder = dest
                    self.populate_file_tree(dest)
                    self.status_bar.config(text=f"Opened: {os.path.basename(dest)}")
                else:
                    error_msg = result.stderr if result.stderr else result.stdout
                    messagebox.showerror("Error", f"Failed to clone repository:\n{error_msg}", parent=clone_dialog)
            except FileNotFoundError:
                messagebox.showerror("Error", "Git not found. Please install git.", parent=clone_dialog)
            except subprocess.TimeoutExpired:
                messagebox.showerror("Error", "Clone operation timed out", parent=clone_dialog)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to clone:\n{str(e)}", parent=clone_dialog)

        button_frame = ttk.Frame(clone_dialog)
        button_frame.pack(fill=tk.X, padx=10, pady=(10, 10))

        ttk.Button(button_frame, text="Clone", command=do_clone).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Cancel", command=clone_dialog.destroy).pack(side=tk.RIGHT, padx=5)

        url_entry.bind("<Return>", lambda _: do_clone())

    def git_init(self):
        """Initialize git repository"""
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        confirm = messagebox.askyesno(
            "Init Git Repository",
            f"Initialize git repository in:\n{self.current_folder}?",
            parent=self.root
        )
        if confirm:
            returncode, stdout, stderr = self.run_git_command(['init'])
            if returncode == 0:
                self.status_bar.config(text="Git repository initialized")
                messagebox.showinfo("Success", "Git repository initialized successfully", parent=self.root)
            else:
                self.status_bar.config(text=f"Git init failed: {stderr}")
                messagebox.showerror("Error", f"Failed to initialize git:\n{stderr}", parent=self.root)

    def git_set_remote(self):
        """Set git remote URL"""
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        # Check if git is initialized
        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        # Show current remotes
        returncode, stdout, _ = self.run_git_command(['remote', '-v'])
        current_remote = ""
        if returncode == 0 and stdout.strip():
            current_remote = f"\nCurrent remotes:\n{stdout}"

        remote_url = simpledialog.askstring(
            "Set Git Remote",
            f"Enter remote URL:{current_remote}\n\nExamples:\nhttps://github.com/username/repo.git\ngit@github.com:username/repo.git",
            parent=self.root
        )
        if remote_url:
            # Check if origin exists
            returncode, _, _ = self.run_git_command(['remote', 'get-url', 'origin'])
            if returncode == 0:
                # Update existing origin
                returncode, stdout, stderr = self.run_git_command(['remote', 'set-url', 'origin', remote_url])
            else:
                # Add new origin
                returncode, stdout, stderr = self.run_git_command(['remote', 'add', 'origin', remote_url])

            if returncode == 0:
                self.status_bar.config(text="Remote set successfully")
                messagebox.showinfo("Success", "Remote URL set successfully", parent=self.root)
            else:
                self.status_bar.config(text=f"Failed to set remote: {stderr}")
                messagebox.showerror("Error", f"Failed to set remote:\n{stderr}", parent=self.root)

    def git_commit(self):
        """Commit changes with a message"""
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        # Check if git is initialized
        returncode, _, stderr = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        # Show status and get commit message
        returncode, stdout, _ = self.run_git_command(['status', '--short'])
        status_text = stdout if stdout.strip() else "No changes to commit"

        commit_msg = simpledialog.askstring(
            "Git Commit",
            f"Git Status:\n{status_text}\n\nEnter commit message:",
            parent=self.root
        )
        if commit_msg:
            # Add all changes
            returncode, stdout, stderr = self.run_git_command(['add', '.'])
            if returncode != 0:
                messagebox.showerror("Error", f"Failed to stage files:\n{stderr}", parent=self.root)
                return

            # Commit
            returncode, stdout, stderr = self.run_git_command(['commit', '-m', commit_msg])
            if returncode == 0:
                self.status_bar.config(text=f"Committed: {commit_msg}")
                messagebox.showinfo("Success", f"Changes committed:\n{commit_msg}", parent=self.root)
            else:
                self.status_bar.config(text=f"Commit failed: {stderr}")
                messagebox.showerror("Error", f"Failed to commit:\n{stderr}", parent=self.root)

    def git_pull(self):
        """Pull changes from remote"""
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        # Check if git is initialized
        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        confirm = messagebox.askyesno("Git Pull", "Pull changes from remote?", parent=self.root)
        if confirm:
            returncode, stdout, stderr = self.run_git_command(['pull'])
            if returncode == 0:
                self.status_bar.config(text="Pull successful")
                messagebox.showinfo("Success", f"Pull successful:\n{stdout}", parent=self.root)
                self.populate_file_tree(self.current_folder)
            else:
                self.status_bar.config(text=f"Pull failed: {stderr}")
                messagebox.showerror("Error", f"Failed to pull:\n{stderr}", parent=self.root)

    def git_push(self):
        """Push changes to remote"""
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        # Check if git is initialized
        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        # Check if remote is set
        returncode, stdout, _ = self.run_git_command(['remote', 'get-url', 'origin'])
        if returncode != 0:
            messagebox.showwarning("No Remote", "Please set remote URL first", parent=self.root)
            return

        # Get current branch name
        returncode, branch_stdout, _ = self.run_git_command(['branch', '--show-current'])
        current_branch = branch_stdout.strip() if returncode == 0 else 'main'

        # Check if upstream is set
        returncode, _, _ = self.run_git_command(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])

        confirm = messagebox.askyesno("Git Push", "Push changes to remote?", parent=self.root)
        if confirm:
            if returncode != 0:
                # No upstream set, use --set-upstream
                returncode, stdout, stderr = self.run_git_command(['push', '--set-upstream', 'origin', current_branch])
            else:
                # Upstream exists, normal push
                returncode, stdout, stderr = self.run_git_command(['push'])

            if returncode == 0:
                self.status_bar.config(text="Push successful")
                messagebox.showinfo("Success", f"Push successful:\n{stdout}", parent=self.root)
            else:
                self.status_bar.config(text=f"Push failed: {stderr}")
                messagebox.showerror("Error", f"Failed to push:\n{stderr}", parent=self.root)

    def show_git_log(self):
        """Show git commit log in a new window"""
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please open a folder first", parent=self.root)
            return

        # Check if git is initialized
        returncode, _, _ = self.run_git_command(['status'])
        if returncode != 0:
            messagebox.showwarning("Not a Git Repository", "Please initialize git first", parent=self.root)
            return

        # Get git log
        returncode, stdout, stderr = self.run_git_command(['log', '--oneline', '--graph', '--all', '-20'])
        if returncode != 0:
            messagebox.showerror("Error", f"Failed to get log:\n{stderr}", parent=self.root)
            return

        # Create log window
        log_window = tk.Toplevel(self.root)
        log_window.title("Git Log")
        log_window.geometry("600x400")
        log_window.transient(self.root)

        # Add refresh button
        btn_frame = ttk.Frame(log_window)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(btn_frame, text="Refresh", command=lambda: self.refresh_git_log(log_text)).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="Close", command=log_window.destroy).pack(side=tk.RIGHT, padx=5)

        # Log display
        log_scroll = ttk.Scrollbar(log_window)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        log_text = tk.Text(log_window, yscrollcommand=log_scroll.set, font=("Monaco", 11))
        log_text.pack(fill=tk.BOTH, expand=True, padx=(10, 0), pady=(0, 10))
        log_scroll.config(command=log_text.yview)

        if not stdout.strip():
            log_text.insert(tk.END, "No commits yet")
        else:
            log_text.insert(tk.END, stdout)

        log_text.config(state=tk.DISABLED)

    def refresh_git_log(self, log_text):
        """Refresh the git log display"""
        returncode, stdout, stderr = self.run_git_command(['log', '--oneline', '--graph', '--all', '-20'])
        log_text.config(state=tk.NORMAL)
        log_text.delete(1.0, tk.END)
        if returncode != 0:
            log_text.insert(tk.END, f"Error: {stderr}")
        elif not stdout.strip():
            log_text.insert(tk.END, "No commits yet")
        else:
            log_text.insert(tk.END, stdout)
        log_text.config(state=tk.DISABLED)


if __name__ == "__main__":
    root = tk.Tk()
    app = MarkdownEditor(root)
    root.mainloop()
