"""VenvManager — detect, create, activate Python virtual environments.

Persists the active venv path to .idlerc/settings.json in the project root
so the choice survives across sessions.
"""

import json
import os
import subprocess
import sys


class VenvManager:
    """Manages Python virtual environment selection for a project folder."""

    # Common venv directory names to auto-detect
    _VENV_NAMES = {'.venv', 'venv', 'env', '.env'}

    def __init__(self):
        self._venv_path: str | None = None
        self._current_folder: str | None = None

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def venv_path(self) -> str | None:
        """Absolute path to the active venv, or None if system Python is used."""
        return self._venv_path

    @property
    def is_active(self) -> bool:
        """True when a virtual environment is selected."""
        return self._venv_path is not None

    # ── Folder lifecycle ────────────────────────────────────────────────────

    def set_folder(self, folder_path: str | None):
        """Called when a project folder is opened or closed.

        On open: loads venv path from .idlerc/settings.json.
        On close (None): clears all state.
        """
        self._current_folder = folder_path
        if folder_path:
            self._load_settings()
        else:
            self._venv_path = None

    # ── Settings persistence ────────────────────────────────────────────────

    def _settings_file(self) -> str | None:
        """Return the path to .idlerc/settings.json, or None if no folder is open."""
        if not self._current_folder:
            return None
        return os.path.join(self._current_folder, '.idlerc', 'settings.json')

    def _load_settings(self):
        """Read .idlerc/settings.json and activate the stored venv if it exists."""
        path = self._settings_file()
        if not path or not os.path.isfile(path):
            self._venv_path = None
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._venv_path = None
            return

        venv_path = data.get('pythonEnvPath')
        if venv_path and os.path.isdir(venv_path) and self._is_valid_venv(venv_path):
            self._venv_path = venv_path
        else:
            # Stored path is stale — discard it
            self._venv_path = None

    def save_settings(self):
        """Persist the current venv path to .idlerc/settings.json.

        Creates the .idlerc directory if it doesn't exist.
        Preserves other keys already in the settings file.
        """
        path = self._settings_file()
        if not path:
            return

        # Load existing data (if any) to preserve other keys
        data: dict = {}
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = {}

        # Update or remove the pythonEnvPath key
        if self._venv_path:
            data['pythonEnvPath'] = self._venv_path
        else:
            data.pop('pythonEnvPath', None)

        # Ensure the .idlerc directory exists
        note_dir = os.path.dirname(path)
        os.makedirs(note_dir, exist_ok=True)

        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write('\n')
        except OSError:
            pass  # permission error, disk full, etc.

    # ── Venv detection ──────────────────────────────────────────────────────

    def detect_venvs(self) -> list[str]:
        """Scan the project folder for existing virtual environments.

        Returns a list of absolute paths to valid venv directories.
        """
        if not self._current_folder:
            return []

        found: list[str] = []
        try:
            for entry in os.listdir(self._current_folder):
                full = os.path.join(self._current_folder, entry)
                if entry in self._VENV_NAMES or (
                    os.path.isdir(full) and self._is_valid_venv(full)
                ):
                    if full not in found:
                        found.append(full)
        except PermissionError:
            pass

        # Sort: active one first, then by name
        found.sort(key=lambda p: (p != self._venv_path, os.path.basename(p).lower()))
        return found

    @staticmethod
    def _is_valid_venv(path: str) -> bool:
        """Check whether a directory looks like a Python virtual environment."""
        bin_dir = os.path.join(path, 'bin')
        if not os.path.isdir(bin_dir):
            return False
        # Must contain at least 'python' or 'python3'
        return (
            os.path.isfile(os.path.join(bin_dir, 'python')) or
            os.path.isfile(os.path.join(bin_dir, 'python3'))
        )

    # ── Activation / deactivation ───────────────────────────────────────────

    def activate(self, venv_path: str):
        """Activate a virtual environment.

        Raises ValueError if the path is not a valid venv.
        """
        if not self._is_valid_venv(venv_path):
            raise ValueError(f"Not a valid Python virtual environment: {venv_path}")
        self._venv_path = venv_path
        self.save_settings()

    def deactivate(self):
        """Revert to system Python."""
        self._venv_path = None
        self.save_settings()

    # ── Creation ────────────────────────────────────────────────────────────

    def create_venv(self, name: str) -> str:
        """Create a new virtual environment in the project folder and activate it.

        Args:
            name: Directory name for the venv (e.g. '.venv').

        Returns:
            The absolute path to the created venv.

        Raises:
            ValueError: if no project folder is open.
            subprocess.CalledProcessError: if venv creation fails.
        """
        if not self._current_folder:
            raise ValueError("No project folder is open")

        venv_path = os.path.join(self._current_folder, name)

        # Use sys.executable to ensure matching Python version
        subprocess.run(
            [sys.executable, '-m', 'venv', venv_path],
            check=True, capture_output=True, text=True)

        self._venv_path = venv_path
        self.save_settings()
        return venv_path

    # ── Path helpers ────────────────────────────────────────────────────────

    def get_site_packages(self) -> str | None:
        """Return the site-packages directory for the active venv, or None."""
        if not self._venv_path:
            return None

        # The venv lib directory contains e.g. 'python3.11' — find it
        lib_dir = os.path.join(self._venv_path, 'lib')
        if not os.path.isdir(lib_dir):
            return None

        for entry in sorted(os.listdir(lib_dir), reverse=True):
            if entry.startswith('python'):
                sp = os.path.join(lib_dir, entry, 'site-packages')
                if os.path.isdir(sp):
                    return sp

        return None

    def get_shell_env(self) -> dict[str, str]:
        """Return environment variable overrides for the system terminal.

        When a venv is active, sets VIRTUAL_ENV and prepends the venv's bin
        directory to PATH so that 'python', 'pip', etc. resolve to the venv.
        Returns an empty dict when no venv is active.
        """
        if not self._venv_path:
            return {}

        bin_dir = os.path.join(self._venv_path, 'bin')
        if not os.path.isdir(bin_dir):
            return {}

        return {
            'VIRTUAL_ENV': self._venv_path,
            'PATH': f"{bin_dir}:{os.environ.get('PATH', '')}",
        }

    def get_display_name(self) -> str:
        """Return a short label for the status bar (e.g. '.venv')."""
        if not self._venv_path:
            return 'Python'
        if self._current_folder:
            try:
                rel = os.path.relpath(self._venv_path, self._current_folder)
                if not rel.startswith('..'):
                    return rel
            except ValueError:
                pass
        return os.path.basename(self._venv_path)

    def get_python_exe(self) -> str:
        """Return the path to the active Python executable.

        Uses the venv's python3 if active, otherwise sys.executable.
        """
        if self._venv_path:
            venv_python = os.path.join(self._venv_path, 'bin', 'python3')
            if os.path.isfile(venv_python):
                return venv_python
        return sys.executable
