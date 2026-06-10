"""
py2app setup script for tknote
"""
from setuptools import setup

APP = ['tknote/__main__.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'packages': [
        'tkinter',
    ],
    'includes': [
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.simpledialog',
        'tkinter.messagebox',
        'tkinter.font',
        'builtins',
        'keyword',
        'code',
        'pty',
        'fcntl',
        'termios',
        'select',
        'signal',
        'threading',
    ],
    'excludes': [
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'wx', 'matplotlib', 'numpy', 'scipy',
        'pandas', 'jedi', 'IPython',
        'tkinter.scrolledtext',
    ],
    'plist': {
        'CFBundleName': 'tknote',
        'CFBundleDisplayName': 'tknote',
        'CFBundleIdentifier': 'com.tknote.editor',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13',
    },
}

setup(
    name='tknote',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
