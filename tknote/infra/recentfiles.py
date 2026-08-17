"""RecentFiles — persistent list of recently opened files.

One path per line in ~/.tknote_recent, most recent first, deduped,
missing files filtered out on load.
"""

import os

RECENT_FILE = os.path.expanduser('~/.tknote_recent')
MAX_ENTRIES = 15


class RecentFiles:
    """Recently-opened-file store (persistent, most-recent-first)."""

    def __init__(self, store_path=RECENT_FILE, max_entries=MAX_ENTRIES):
        self.store_path = store_path
        self.max_entries = max_entries
        self._paths = []
        self._load()

    def _load(self):
        try:
            with open(self.store_path, encoding='utf-8') as f:
                paths = [line.strip() for line in f if line.strip()]
        except OSError:
            paths = []
        self._paths = [p for p in paths
                       if os.path.isfile(p)][:self.max_entries]

    def _save(self):
        try:
            with open(self.store_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self._paths) + '\n')
        except OSError:
            pass

    def add(self, path):
        """Record a file as the most recently opened."""
        path = os.path.abspath(path)
        if path in self._paths:
            self._paths.remove(path)
        self._paths.insert(0, path)
        del self._paths[self.max_entries:]
        self._save()

    def get_paths(self):
        """Return the recent paths, most recent first."""
        return list(self._paths)
