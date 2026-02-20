"""Path resolution for portable (source) vs installed (Program Files) mode."""

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True if running as a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def is_installed_mode() -> bool:
    """True if running from an installed location (has .installed marker or in Program Files)."""
    if is_frozen():
        app_dir = Path(sys.executable).parent
    else:
        app_dir = Path(__file__).resolve().parent

    if (app_dir / ".installed").exists():
        return True

    # Check if path contains Program Files
    app_str = str(app_dir).lower()
    return "program files" in app_str


def _get_documents_dir() -> Path:
    """Get the real Documents folder using the Windows Shell API."""
    try:
        import ctypes
        import ctypes.wintypes
        buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
        # CSIDL_PERSONAL = 0x0005 = Documents folder
        result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x0005, None, 0, buf)
        if result == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    # Fallback
    return Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"


class AppPaths:
    """Resolved application paths based on running mode."""

    def __init__(self) -> None:
        if is_frozen():
            self.app_dir = Path(sys.executable).parent
        else:
            self.app_dir = Path(__file__).resolve().parent

        if is_installed_mode():
            # Installed mode: data goes to %APPDATA%/FSE Processor
            appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            self._data_dir = appdata / "FSE Processor"
            # Downloads go to user's Documents (resolved via Shell API)
            self.default_download_dir = _get_documents_dir() / "FSE Downloads"
        else:
            # Portable mode: everything relative to app directory
            self._data_dir = self.app_dir
            self.default_download_dir = self.app_dir / "downloads"

        self.settings_file = self._data_dir / "settings.env"
        self.log_dir = self._data_dir / "logs"
        self.browser_data_dir = self._data_dir / "browser_data"

    def ensure_dirs(self) -> None:
        """Create data directories if they don't exist."""
        for d in (self._data_dir, self.log_dir, self.browser_data_dir):
            d.mkdir(parents=True, exist_ok=True)
        # Download dir is separate — don't crash if Documents is inaccessible
        try:
            self.default_download_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Fall back to Downloads folder inside data dir
            self.default_download_dir = self._data_dir / "downloads"
            self.default_download_dir.mkdir(parents=True, exist_ok=True)


# Singleton instance
paths = AppPaths()
