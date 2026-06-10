"""Entry point for tknote."""

import tkinter as tk

from tknote.app import MarkdownEditor


def main():
    root = tk.Tk()
    app = MarkdownEditor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
