"""
py2app setup script for Markdown Editor
"""
from setuptools import setup

APP = ['__main__.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'packages': [
        'tkinter',
        'tkhtmlview',
        'markdown',
        'PIL',
        'requests',
        'certifi',
        'charset_normalizer',
        'idna',
        'urllib3',
    ],
    'includes': [
        'tkinter',
        'tkinter.ttk',
        'tkinter.scrolledtext',
        'tkinter.filedialog',
        'tkinter.simpledialog',
        'tkinter.messagebox',
    ],
    'excludes': [
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'wx', 'matplotlib', 'numpy', 'scipy',
        'pandas', 'jedi', 'IPython',
    ],
    'plist': {
        'CFBundleName': 'Markdown Editor',
        'CFBundleDisplayName': 'Markdown Editor',
        'CFBundleIdentifier': 'com.tknote.markdown-editor',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13',
    },
}

setup(
    name='Markdown Editor',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
