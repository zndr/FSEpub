"""GUI PySide6 per FSE Processor."""

import ctypes
import json
import logging
import os
import platform
import re
import sys
import threading
import traceback
import urllib.request
import webbrowser
import winreg
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_paths import paths
from version import __version__
from browser_automation import (
    FSEBrowser,
    FSE_BASE_URL,
    detect_default_browser,
    get_cdp_registry_status,
    enable_cdp_in_registry,
    disable_cdp_in_registry,
)
from config import Config
from email_client import EmailClient
from file_manager import FileManager
from logger_module import ProcessingLogger
from main import run_processing

ENV_FILE = str(paths.settings_file)

# Ordered list of settings: (env_key, label, default, kind)
# kind: "text", "password", "dir", "exe", "bool", "int", "pdf_reader"
SETTINGS_SPEC = [
    ("EMAIL_USER", "Email utente", "", "text"),
    ("EMAIL_PASS", "Email password", "", "password"),
    ("IMAP_HOST", "IMAP Host", "mail.fastweb360.it", "text"),
    ("IMAP_PORT", "IMAP Port", "993", "int"),
    ("DOWNLOAD_DIR", "Directory download", str(paths.default_download_dir), "dir"),
    ("BROWSER_CHANNEL", "Browser", "msedge", "browser_selector"),
    ("PDF_READER", "Lettore PDF", "default", "pdf_reader"),
    ("USE_EXISTING_BROWSER", "Usa browser esistente (CDP)", "false", "bool"),
    ("OPEN_AFTER_DOWNLOAD", "Apri al termine", "true", "bool"),
    ("CDP_PORT", "Porta CDP", "9222", "int"),
    ("HEADLESS", "Headless browser", "false", "bool"),
    ("DOWNLOAD_TIMEOUT", "Download timeout (sec)", "60", "int"),
    ("PAGE_TIMEOUT", "Page timeout (sec)", "30", "int"),
    ("CONSOLE_FONT_SIZE", "Dim. carattere console", "8", "int"),
    ("MARK_AS_READ", "Marca come letto dopo elaborazione", "true", "bool"),
    ("DELETE_AFTER_PROCESSING", "Elimina email dopo elaborazione", "false", "bool"),
    ("MAX_EMAILS", "Max email da processare (0=tutte)", "3", "int"),
]

# Sentinel values for PDF reader selection
PDF_READER_DEFAULT = "default"
PDF_READER_CUSTOM = "__custom__"
PDF_READER_DEFAULT_LABEL = "Predefinito di sistema"
PDF_READER_CUSTOM_LABEL = "Personalizzato..."

SISS_DOCUMENT_TYPES = [
    ("REFERTO", "Referto", True),
    ("LETTERA DIMISSIONE", "Lettera Dimissione", True),
    ("VERBALE PRONTO SOCCORSO", "Verbale Pronto Soccorso", True),
]

PATIENT_DOCUMENT_TYPES = [
    ("REFERTO", "Tutti i referti specialistici", True),
    ("REFERTO SPECIALISTICO", "non def.", False),
    ("REFERTO SPECIALISTICO LABORATORIO", "Lab", False),
    ("REFERTO SPECIALISTICO RADIOLOGIA", "Imaging", False),
    ("REFERTO ANATOMIA PATOLOGICA", "Anat. pat.", False),
    ("LETTERA DIMISSIONE", "Lettera Dimissione", True),
    ("VERBALE PRONTO SOCCORSO", "Verbale PS", True),
]

REFERTO_SUBTYPES = {
    "REFERTO SPECIALISTICO", "REFERTO SPECIALISTICO LABORATORIO",
    "REFERTO SPECIALISTICO RADIOLOGIA", "REFERTO ANATOMIA PATOLOGICA",
}

DATE_PRESETS = ["Tutte", "Ultima settimana", "Ultimo mese", "Ultimo anno", "Personalizzato"]
DATE_PRESET_DAYS = {"Ultima settimana": 7, "Ultimo mese": 30, "Ultimo anno": 365}

APP_STYLE = """
/* ---------- QGroupBox ---------- */
QGroupBox {
    border: 1px solid #3b7dd8;
    border-radius: 4px;
    margin-top: 14px;
    padding-top: 14px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 2px 8px;
    color: #1a4a8a;
}

/* ---------- QPushButton ---------- */
QPushButton {
    background-color: #2b6cb0;
    color: white;
    border: 1px solid #1e5a9a;
    border-radius: 4px;
    padding: 5px 14px;
    min-height: 20px;
}
QPushButton:hover {
    background-color: #1e5a9a;
}
QPushButton:pressed {
    background-color: #174a7a;
}
QPushButton:disabled {
    background-color: #e0e0e0;
    color: #a0a0a0;
    border-color: #c0c0c0;
}
QPushButton#browseBtn {
    background-color: #e8eef4;
    color: #333333;
    border: 1px solid #b0c4de;
    font-weight: bold;
    padding: 2px;
}
QPushButton#browseBtn:hover {
    background-color: #d0dcea;
}

/* ---------- QTabWidget / QTabBar ---------- */
QTabWidget::pane {
    border: 1px solid #3b7dd8;
    border-top: 2px solid #2b6cb0;
}
QTabBar::tab {
    background-color: #e8eef4;
    color: #333333;
    border: 1px solid #b0c4de;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 6px 16px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #2b6cb0;
    color: white;
    border-color: #2b6cb0;
}
QTabBar::tab:hover:!selected {
    background-color: #d0dcea;
}

/* ---------- QTextEdit (console) ---------- */
QTextEdit {
    background-color: #f0f4f8;
    border: 1px solid #c0d0e0;
    border-radius: 3px;
}

/* ---------- QLineEdit ---------- */
QLineEdit {
    border: 1px solid #b0c4de;
    border-radius: 3px;
    padding: 3px 6px;
}
QLineEdit:focus {
    border: 1px solid #2b6cb0;
}

/* ---------- QComboBox ---------- */
QComboBox {
    border: 1px solid #b0c4de;
    border-radius: 3px;
    padding: 3px 6px;
}
QComboBox:focus {
    border: 1px solid #2b6cb0;
}
/* ---------- QCheckBox ---------- */
QCheckBox::indicator:checked {
    background-color: #2b6cb0;
    border: 1px solid #1e5a9a;
    border-radius: 2px;
}
QCheckBox::indicator:unchecked {
    border: 1px solid #b0c4de;
    border-radius: 2px;
    background-color: white;
}

/* ---------- Utility ---------- */
.subtle-label {
    color: #6b7b8d;
}
"""


def _norm(path: str) -> str:
    """Normalize an exe path for deduplication."""
    return os.path.normcase(os.path.normpath(path))


def _detect_pdf_readers() -> list[tuple[str, str]]:
    """Detect PDF readers registered in Windows.

    Returns a list of (exe_path, display_name) tuples, deduplicated and sorted.
    """
    readers: dict[str, str] = {}  # normalized_exe_path -> (exe_path, display_name)
    raw_paths: dict[str, str] = {}  # normalized -> original path

    def _add(exe: str, name: str) -> None:
        key = _norm(exe)
        if key not in readers:
            readers[key] = name
            raw_paths[key] = exe

    # 1. OpenWithProgids for .pdf
    for hive, subkey in [
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\OpenWithProgids"),
        (winreg.HKEY_CLASSES_ROOT, r".pdf\OpenWithProgids"),
    ]:
        for progid in _enum_value_names(hive, subkey):
            exe = _resolve_progid_to_exe(progid)
            if exe:
                _add(exe, _get_app_display_name(exe, progid))

    # 2. OpenWithList for .pdf
    for _, val in _enum_values(
        winreg.HKEY_CURRENT_USER,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\OpenWithList",
    ):
        if isinstance(val, str) and val.lower().endswith(".exe"):
            exe = _resolve_app_exe(val)
            if exe:
                _add(exe, _get_app_display_name(exe, ""))

    # 3. HKCR\Applications\*.exe with SupportedTypes containing .pdf
    _collect_applications_with_pdf_support(readers, raw_paths, _add)

    # 4. RegisteredApplications -> Capabilities\FileAssociations\.pdf
    _collect_registered_applications(readers, raw_paths, _add)

    # Disambiguate duplicate display names
    name_counts: dict[str, list[str]] = {}
    for nk, name in readers.items():
        name_counts.setdefault(name, []).append(nk)
    for name, nkeys in name_counts.items():
        if len(nkeys) > 1:
            all_parts = [Path(raw_paths[nk]).parts for nk in nkeys]
            for nk, parts in zip(nkeys, all_parts):
                suffix = Path(raw_paths[nk]).parent.name
                for p in reversed(parts[:-1]):
                    candidate = p
                    if any(
                        candidate not in Path(raw_paths[other]).parts
                        for other in nkeys if other != nk
                    ):
                        suffix = candidate
                        break
                readers[nk] = f"{name} ({suffix})"

    return sorted(
        [(raw_paths[k], v) for k, v in readers.items()],
        key=lambda item: item[1].lower(),
    )


def _detect_browsers() -> list[tuple[str, str]]:
    """Detect browsers installed on Windows.

    Returns a list of (channel_or_path, display_name) tuples.
    Known channels are returned as channel names; unknown browsers as exe paths.
    """
    KNOWN_BROWSERS = {
        "msedge.exe": ("msedge", "Microsoft Edge"),
        "chrome.exe": ("chrome", "Google Chrome"),
        "firefox.exe": ("firefox", "Mozilla Firefox"),
        "brave.exe": (None, "Brave"),
    }

    browsers: list[tuple[str, str]] = []
    seen: set[str] = set()

    for exe_name, (channel, display) in KNOWN_BROWSERS.items():
        exe_path = None
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                    hive,
                    rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
                ) as key:
                    val, _ = winreg.QueryValueEx(key, "")
                    if val and Path(val).exists():
                        exe_path = str(Path(val))
                        break
            except OSError:
                pass

        if not exe_path:
            fallback_dirs = [
                Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
                Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
                Path(os.environ.get("LOCALAPPDATA", "")),
            ]
            subpaths = {
                "msedge.exe": [r"Microsoft\Edge\Application\msedge.exe"],
                "chrome.exe": [r"Google\Chrome\Application\chrome.exe"],
                "firefox.exe": [r"Mozilla Firefox\firefox.exe"],
                "brave.exe": [r"BraveSoftware\Brave-Browser\Application\brave.exe"],
            }
            for base in fallback_dirs:
                if not base or not base.exists():
                    continue
                for sub in subpaths.get(exe_name, []):
                    candidate = base / sub
                    if candidate.exists():
                        exe_path = str(candidate)
                        break
                if exe_path:
                    break

        if exe_path and _norm(exe_path) not in seen:
            seen.add(_norm(exe_path))
            value = channel if channel else exe_path
            browsers.append((value, display))

    return browsers


# Sentinel values for browser selection
BROWSER_CHROMIUM = "chromium"
BROWSER_CHROMIUM_LABEL = "Chromium integrato (Playwright)"


def _enum_value_names(hive: int, subkey: str) -> list[str]:
    """Enumerate value names from a registry key."""
    names = []
    try:
        with winreg.OpenKey(hive, subkey) as key:
            i = 0
            while True:
                try:
                    name, _, _ = winreg.EnumValue(key, i)
                    names.append(name)
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return names


def _enum_values(hive: int, subkey: str) -> list[tuple[str, object]]:
    """Enumerate (name, value) pairs from a registry key."""
    results = []
    try:
        with winreg.OpenKey(hive, subkey) as key:
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(key, i)
                    results.append((name, val))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return results


def _enum_subkeys(hive: int, subkey: str) -> list[str]:
    """Enumerate subkey names from a registry key."""
    names = []
    try:
        with winreg.OpenKey(hive, subkey) as key:
            i = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return names


def _collect_applications_with_pdf_support(
    readers: dict, raw_paths: dict, add_fn: callable,
) -> None:
    """Scan HKCR\\Applications for apps declaring .pdf in SupportedTypes."""
    for app_name in _enum_subkeys(winreg.HKEY_CLASSES_ROOT, "Applications"):
        if not app_name.lower().endswith(".exe"):
            continue
        supported = _enum_value_names(
            winreg.HKEY_CLASSES_ROOT,
            rf"Applications\{app_name}\SupportedTypes",
        )
        if not any(s.lower() == ".pdf" for s in supported):
            continue
        exe = _resolve_app_exe(app_name)
        if exe:
            add_fn(exe, _get_app_display_name(exe, ""))


def _collect_registered_applications(
    readers: dict, raw_paths: dict, add_fn: callable,
) -> None:
    """Scan RegisteredApplications for apps with .pdf FileAssociations."""
    for name, val in _enum_values(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\RegisteredApplications",
    ):
        if not isinstance(val, str):
            continue
        cap_path = val.replace("/", "\\")
        assocs = _enum_values(winreg.HKEY_LOCAL_MACHINE, rf"{cap_path}\FileAssociations")
        pdf_progid = None
        for assoc_name, assoc_val in assocs:
            if assoc_name.lower() == ".pdf" and isinstance(assoc_val, str):
                pdf_progid = assoc_val
                break
        if not pdf_progid:
            continue
        exe = _resolve_progid_to_exe(pdf_progid)
        if exe:
            add_fn(exe, _get_app_display_name(exe, pdf_progid))


def _resolve_progid_to_exe(progid: str) -> str | None:
    """Resolve a ProgID to an executable path."""
    search_paths = [
        (winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command"),
    ]
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        search_paths.append((hive, rf"SOFTWARE\Classes\{progid}\shell\open\command"))

    for hive, subkey in search_paths:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                cmd, _ = winreg.QueryValueEx(key, "")
                if isinstance(cmd, str):
                    result = _extract_exe_from_command(cmd)
                    if result:
                        return result
        except OSError:
            pass
    return None


def _resolve_app_exe(exe_name: str) -> str | None:
    """Resolve an exe name from OpenWithList/Applications to a full path."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            rf"Applications\{exe_name}\shell\open\command",
        ) as key:
            cmd, _ = winreg.QueryValueEx(key, "")
            if isinstance(cmd, str):
                result = _extract_exe_from_command(cmd)
                if result:
                    return result
    except OSError:
        pass
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
        ) as key:
            path, _ = winreg.QueryValueEx(key, "")
            if path:
                return _extract_exe_from_command(path)
    except OSError:
        pass
    return None


def _extract_exe_from_command(cmd: str) -> str | None:
    """Extract exe path from a registry command string."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            path = cmd[1:end]
        else:
            return None
    else:
        path = cmd.split()[0] if cmd else ""
    if path and Path(path).suffix.lower() == ".exe" and Path(path).exists():
        return str(Path(path))
    return None


def _get_exe_version_field(exe_path: str) -> str | None:
    """Read FileDescription or ProductName from the exe version resource via ctypes."""
    try:
        _GetFileVersionInfoSizeW = ctypes.windll.version.GetFileVersionInfoSizeW
        _GetFileVersionInfoW = ctypes.windll.version.GetFileVersionInfoW
        _VerQueryValueW = ctypes.windll.version.VerQueryValueW

        size = _GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return None

        buf = ctypes.create_string_buffer(size)
        if not _GetFileVersionInfoW(exe_path, 0, size, buf):
            return None

        p_val = ctypes.c_void_p()
        val_size = ctypes.c_uint()
        if not _VerQueryValueW(
            buf, r"\VarFileInfo\Translation",
            ctypes.byref(p_val), ctypes.byref(val_size),
        ):
            return None
        if val_size.value < 4:
            return None

        lang = ctypes.cast(p_val, ctypes.POINTER(ctypes.c_ushort))[0]
        cp = ctypes.cast(p_val, ctypes.POINTER(ctypes.c_ushort))[1]

        for field in ("FileDescription", "ProductName"):
            query = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\{field}"
            p_str = ctypes.c_void_p()
            str_size = ctypes.c_uint()
            if _VerQueryValueW(buf, query, ctypes.byref(p_str), ctypes.byref(str_size)):
                if str_size.value > 1:
                    result = ctypes.wstring_at(p_str, str_size.value - 1).strip()
                    if result:
                        return result
    except Exception:
        pass
    return None


def _get_app_display_name(exe_path: str, progid: str) -> str:
    """Get a user-friendly display name for an application."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            rf"Applications\{Path(exe_path).name}",
        ) as key:
            name, _ = winreg.QueryValueEx(key, "FriendlyAppName")
            if name and isinstance(name, str):
                return name
    except OSError:
        pass

    ver_name = _get_exe_version_field(exe_path)
    if ver_name:
        return ver_name

    return Path(exe_path).stem.replace("_", " ").replace("-", " ").title()


def _load_env_values(path: str = ENV_FILE) -> dict[str, str]:
    """Read key=value pairs from an env file."""
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    return values


def _save_env_values(values: dict[str, str], path: str = ENV_FILE) -> None:
    """Write key=value pairs to an env file, preserving comments."""
    env_path = Path(path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    written_keys: set[str] = set()

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in values:
                    lines.append(f"{key}={values[key]}")
                    written_keys.add(key)
                else:
                    lines.append(line)
            else:
                lines.append(line)

    for key, val in values.items():
        if key not in written_keys:
            lines.append(f"{key}={val}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---- Signal bridge for thread-safe GUI updates ----

class _SignalBridge(QObject):
    """Bridge for thread-safe communication from worker threads to GUI."""
    append_text = Signal(QTextEdit, str)
    show_info = Signal(str, str)
    show_error = Signal(str, str)
    show_warning = Signal(str, str)
    call_on_main = Signal(object)  # generic callable

    def __init__(self) -> None:
        super().__init__()
        self.append_text.connect(self._on_append_text)
        self.show_info.connect(self._on_show_info)
        self.show_error.connect(self._on_show_error)
        self.show_warning.connect(self._on_show_warning)
        self.call_on_main.connect(self._on_call)

    @staticmethod
    def _on_append_text(widget: QTextEdit, msg: str) -> None:
        widget.setReadOnly(False)
        widget.append(msg)
        widget.setReadOnly(True)
        widget.verticalScrollBar().setValue(widget.verticalScrollBar().maximum())

    @staticmethod
    def _on_show_info(title: str, msg: str) -> None:
        QMessageBox.information(None, title, msg)

    @staticmethod
    def _on_show_error(title: str, msg: str) -> None:
        QMessageBox.critical(None, title, msg)

    @staticmethod
    def _on_show_warning(title: str, msg: str) -> None:
        QMessageBox.warning(None, title, msg)

    @staticmethod
    def _on_call(fn: object) -> None:
        fn()


class TextHandler(logging.Handler):
    """Logging handler that writes to a QTextEdit widget (thread-safe via signals)."""

    def __init__(self, text_widget: QTextEdit, bridge: _SignalBridge) -> None:
        super().__init__()
        self._widget = text_widget
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self._bridge.append_text.emit(self._widget, msg)


class FSEApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"FSE Processor v{__version__}")
        self.resize(750, 560)

        # Ensure data directories exist (for installed mode)
        paths.ensure_dirs()

        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._patient_stop_event = threading.Event()
        self._patient_worker: threading.Thread | None = None
        self._patient_browser: FSEBrowser | None = None
        self._ente_scan_worker: threading.Thread | None = None
        self._fields: dict[str, str | bool] = {}  # key -> current value

        # Signal bridge for thread-safe GUI updates
        self._bridge = _SignalBridge()

        self._build_ui()
        self._load_settings()

    # ---- helpers for field values ----

    def _get_field(self, key: str) -> str:
        """Get a field value as string."""
        val = self._fields.get(key, "")
        if isinstance(val, bool):
            return "true" if val else "false"
        return str(val)

    def _set_field(self, key: str, value) -> None:
        """Set a field value."""
        self._fields[key] = value

    # ---- UI construction ----

    def _build_ui(self) -> None:
        # Detect PDF readers, browsers, and default browser once at startup
        self._pdf_readers = _detect_pdf_readers()
        self._browsers = _detect_browsers()
        self._default_browser_info = detect_default_browser()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Menu bar
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")

        act_settings = QAction("Impostazioni", self)
        act_settings.triggered.connect(lambda: self._notebook.setCurrentIndex(2))
        file_menu.addAction(act_settings)

        file_menu.addSeparator()

        act_exit = QAction("Esci", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # Help menu
        help_menu = menu_bar.addMenu("Aiuto")

        act_guide = QAction("Guida", self)
        act_guide.triggered.connect(self._open_guide)
        help_menu.addAction(act_guide)

        help_menu.addSeparator()

        act_updates = QAction("Controlla aggiornamenti", self)
        act_updates.triggered.connect(self._check_updates)
        help_menu.addAction(act_updates)

        act_debug = QAction("Debug", self)
        act_debug.triggered.connect(self._show_debug_info)
        help_menu.addAction(act_debug)

        help_menu.addSeparator()

        act_about = QAction("About", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

        # Tabbed notebook
        self._notebook = QTabWidget()
        main_layout.addWidget(self._notebook)

        # Tab 1: Scarica referti non letti
        siss_tab = QWidget()
        self._notebook.addTab(siss_tab, "Scarica referti non letti")

        # Tab 2: Scarica referti singolo paziente
        patient_tab = QWidget()
        self._notebook.addTab(patient_tab, "Scarica referti singolo paziente")

        # Tab 3: Impostazioni (built first so fields exist for SISS tab)
        settings_tab = QWidget()
        self._notebook.addTab(settings_tab, "Impostazioni")
        self._build_settings_tab(settings_tab)

        # Build SISS tab after settings so fields exist
        self._build_siss_tab(siss_tab)
        self._build_patient_tab(patient_tab)

        # Bottom bar: Reset console (left) — Apri cartella download (right)
        bottom_row = QHBoxLayout()
        self._btn_reset_console = QPushButton("Reset console")
        self._btn_reset_console.clicked.connect(self._reset_active_console)
        bottom_row.addWidget(self._btn_reset_console)
        bottom_row.addStretch()
        btn_open_dl = QPushButton("Apri cartella download")
        btn_open_dl.clicked.connect(self._open_download_dir)
        bottom_row.addWidget(btn_open_dl)
        main_layout.addLayout(bottom_row)

    def _reset_active_console(self) -> None:
        """Clear the console of the currently active tab."""
        idx = self._notebook.currentIndex()
        if idx == 0:
            self._console.clear()
        elif idx == 1:
            self._patient_console.clear()

    def _open_download_dir(self) -> None:
        """Open the configured download directory in the file explorer."""
        dl_dir = self._download_dir_entry.text() or str(paths.default_download_dir)
        p = Path(dl_dir)
        if p.is_dir():
            os.startfile(p)
        else:
            QMessageBox.warning(self, "Cartella non trovata", f"La cartella non esiste:\n{dl_dir}")

    def _build_siss_tab(self, parent: QWidget) -> None:
        """Build the SISS Integration tab content."""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)

        # Browser info line
        self._browser_info_label = QLabel("")
        self._browser_info_label.setProperty("class", "subtle-label")
        layout.addWidget(self._browser_info_label)
        self._update_browser_info_label()

        # Controls
        ctrl_group = QGroupBox("Controlli")
        ctrl_layout = QVBoxLayout(ctrl_group)

        btn_row = QHBoxLayout()
        self._btn_check = QPushButton("Controlla Email")
        self._btn_check.clicked.connect(self._check_email)
        self._btn_check.setToolTip("Conta le email con referti da scaricare, senza avviare il download")
        btn_row.addWidget(self._btn_check)

        self._btn_start = QPushButton("Avvia download")
        self._btn_start.clicked.connect(self._start_processing)
        btn_row.addWidget(self._btn_start)

        self._btn_stop = QPushButton("Interrompi")
        self._btn_stop.clicked.connect(self._stop_processing)
        self._btn_stop.setEnabled(False)
        btn_row.addWidget(self._btn_stop)

        btn_row.addStretch()
        ctrl_layout.addLayout(btn_row)

        # Max email row
        max_row = QHBoxLayout()
        max_row.addWidget(QLabel("Num. max email da scaricare (0=tutte):"))
        self._max_email_entry = QLineEdit()
        self._max_email_entry.setFixedWidth(60)
        self._max_email_entry.setText(self._get_field("MAX_EMAILS") or "3")
        self._max_email_entry.setToolTip("Numero massimo di email da elaborare per sessione (0 = tutte)")
        max_row.addWidget(self._max_email_entry)
        max_row.addStretch()
        ctrl_layout.addLayout(max_row)

        layout.addWidget(ctrl_group)

        # "Dopo il download" options
        post_group = QGroupBox("Dopo il download")
        post_layout = QVBoxLayout(post_group)

        self._siss_mark_cb = QCheckBox('Marca come "gia\' letto" i messaggi scaricati')
        post_layout.addWidget(self._siss_mark_cb)

        self._siss_delete_cb = QCheckBox("Elimina i messaggi scaricati dal server")
        self._siss_delete_cb.setToolTip("I messaggi verranno eliminati definitivamente dal server dopo il download")
        self._siss_delete_cb.toggled.connect(self._on_delete_toggled)
        post_layout.addWidget(self._siss_delete_cb)

        layout.addWidget(post_group)

        # Document type checkboxes with "Tutti"
        self._siss_tutti_cb, self._siss_doc_cbs = self._build_siss_doc_type_checkboxes(layout)

        # Console
        console_group = QGroupBox("Console")
        console_layout = QVBoxLayout(console_group)
        font_size = int(self._get_field("CONSOLE_FONT_SIZE") or "8")
        self._console = QTextEdit()
        self._console.setReadOnly(True)
        self._console.setFont(QFont("Consolas", font_size))
        self._console.setMinimumHeight(200)
        console_layout.addWidget(self._console)
        layout.addWidget(console_group, 1)  # stretch factor

    def _build_siss_doc_type_checkboxes(self, parent_layout: QVBoxLayout) -> tuple[QCheckBox, dict[str, QCheckBox]]:
        """Create SISS document type checkboxes with 'Tutti' toggle."""
        group = QGroupBox("Tipologie documento")
        row_layout = QHBoxLayout(group)

        tutti_cb = QCheckBox("Tutti")
        doc_cbs: dict[str, QCheckBox] = {}

        def on_tutti_changed(checked):
            for cb in doc_cbs.values():
                cb.setEnabled(not checked)

        tutti_cb.toggled.connect(on_tutti_changed)
        row_layout.addWidget(tutti_cb)
        row_layout.addStretch()

        for type_key, label, default_on in SISS_DOCUMENT_TYPES:
            cb = QCheckBox(label)
            cb.setChecked(default_on)
            row_layout.addWidget(cb)
            row_layout.addStretch()
            doc_cbs[type_key] = cb
        parent_layout.addWidget(group)
        return tutti_cb, doc_cbs

    def _on_delete_toggled(self, checked: bool) -> None:
        """When 'Elimina' is checked, force 'Marca come letto' on and disable it."""
        if checked:
            self._siss_mark_cb.setChecked(True)
            self._siss_mark_cb.setEnabled(False)
        else:
            self._siss_mark_cb.setEnabled(True)

    @staticmethod
    def _get_selected_types(tutti_cb: QCheckBox, doc_cbs: dict[str, QCheckBox]) -> set[str] | None:
        """Return the set of selected document type keys, or None if 'Tutti' is checked."""
        if tutti_cb.isChecked():
            return None
        return {key for key, cb in doc_cbs.items() if cb.isChecked()}

    def _build_patient_tab(self, parent: QWidget) -> None:
        """Build the Download Paziente tab content with two-column layout."""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)

        # Top frame: two columns
        top_layout = QHBoxLayout()

        # ── Left column: Tipologia documenti ──
        self._patient_tutti_cb, self._patient_doc_cbs = self._build_patient_doc_type_checkboxes()
        top_layout.addWidget(self._patient_doc_group, 1)

        # ── Right column: CF + Origine e data + bottoni ──
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # CF input
        cf_group = QGroupBox("Codice Fiscale")
        cf_layout = QGridLayout(cf_group)
        cf_layout.addWidget(QLabel("CF:"), 0, 0)
        self._cf_entry = QLineEdit()
        self._cf_entry.setFont(QFont("Consolas", 11))
        cf_layout.addWidget(self._cf_entry, 0, 1)
        cf_layout.setColumnStretch(1, 1)
        right_layout.addWidget(cf_group)

        # Filters: Ente/Struttura and Date
        filter_group = QGroupBox("Origine e data")
        filter_layout = QGridLayout(filter_group)
        filter_layout.setColumnStretch(1, 1)

        filter_layout.addWidget(QLabel("Ente/Struttura:"), 0, 0)
        self._ente_combo = QComboBox()
        self._ente_combo.setEditable(True)
        self._ente_combo.addItem("")
        self._ente_combo.setToolTip("Filtra i documenti per ente o struttura sanitaria di provenienza")
        small_font = self._ente_combo.font()
        small_font.setPointSize(small_font.pointSize() - 1)
        self._ente_combo.setFont(small_font)
        filter_layout.addWidget(self._ente_combo, 0, 1, 1, 3)

        filter_layout.addWidget(QLabel("Periodo:"), 1, 0)
        self._date_preset_combo = QComboBox()
        self._date_preset_combo.addItems(DATE_PRESETS)
        self._date_preset_combo.setCurrentText("Tutte")
        self._date_preset_combo.currentTextChanged.connect(self._on_date_preset_changed)
        filter_layout.addWidget(self._date_preset_combo, 1, 1, 1, 3)

        filter_layout.addWidget(QLabel("Dal:"), 2, 0)
        self._date_from_entry = QLineEdit()
        self._date_from_entry.setFixedWidth(100)
        self._date_from_entry.setEnabled(False)
        filter_layout.addWidget(self._date_from_entry, 2, 1)

        filter_layout.addWidget(QLabel("Al:"), 2, 2)
        self._date_to_entry = QLineEdit()
        self._date_to_entry.setFixedWidth(100)
        self._date_to_entry.setEnabled(False)
        filter_layout.addWidget(self._date_to_entry, 2, 3)

        right_layout.addWidget(filter_group)

        # Controls
        ctrl_layout = QHBoxLayout()
        ctrl_layout.addStretch()
        self._btn_load_enti = QPushButton("Carica strutture")
        self._btn_load_enti.setToolTip("Apre il browser e carica le strutture disponibili per il paziente")
        self._btn_load_enti.clicked.connect(self._start_ente_scan)
        ctrl_layout.addWidget(self._btn_load_enti)

        self._btn_patient_start = QPushButton("Avvia Download")
        self._btn_patient_start.clicked.connect(self._start_patient_download)
        ctrl_layout.addWidget(self._btn_patient_start)

        self._btn_patient_stop = QPushButton("Interrompi")
        self._btn_patient_stop.clicked.connect(self._stop_patient_download)
        self._btn_patient_stop.setEnabled(False)
        ctrl_layout.addWidget(self._btn_patient_stop)
        ctrl_layout.addStretch()
        right_layout.addLayout(ctrl_layout)

        right_layout.addStretch()
        top_layout.addWidget(right_widget, 1)

        layout.addLayout(top_layout)

        # Console
        console_group = QGroupBox("Console")
        console_layout = QVBoxLayout(console_group)
        font_size = int(self._get_field("CONSOLE_FONT_SIZE") or "8")
        self._patient_console = QTextEdit()
        self._patient_console.setReadOnly(True)
        self._patient_console.setFont(QFont("Consolas", font_size))
        self._patient_console.setMinimumHeight(120)
        console_layout.addWidget(self._patient_console)
        layout.addWidget(console_group, 1)

    def _build_patient_doc_type_checkboxes(self) -> tuple[QCheckBox, dict[str, QCheckBox]]:
        """Create Patient document type checkboxes with hierarchy."""
        self._patient_doc_group = QGroupBox("Tipologia documenti")
        group_layout = QVBoxLayout(self._patient_doc_group)
        group_layout.setSpacing(2)

        tutti_cb = QCheckBox("Tutti")
        doc_cbs: dict[str, QCheckBox] = {}
        all_cbs: list[QCheckBox] = []
        referto_parent_cb: QCheckBox | None = None
        referto_sub_cbs: list[QCheckBox] = []

        def on_tutti_changed(checked):
            for cb in all_cbs:
                cb.setEnabled(not checked)
            if not checked and referto_parent_cb and referto_parent_cb.isChecked():
                for cb in referto_sub_cbs:
                    cb.setEnabled(False)

        def on_referto_parent_changed(checked):
            if tutti_cb.isChecked():
                return
            for cb in referto_sub_cbs:
                cb.setEnabled(not checked)

        tutti_cb.toggled.connect(on_tutti_changed)
        group_layout.addWidget(tutti_cb)

        # Referto parent
        for type_key, label, default_on in PATIENT_DOCUMENT_TYPES:
            if type_key == "REFERTO":
                cb = QCheckBox(label)
                cb.setChecked(default_on)
                referto_parent_cb = cb
                cb.toggled.connect(on_referto_parent_changed)
                group_layout.addWidget(cb)
                doc_cbs[type_key] = cb
                all_cbs.append(cb)
                break

        # Referto sub-types (single indented row, compact)
        sub_items = [
            (tkey, dlabel, dfl) for tkey, dlabel, dfl in PATIENT_DOCUMENT_TYPES
            if tkey in REFERTO_SUBTYPES
        ]
        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(24, 0, 0, 0)
        for type_key, label, default_on in sub_items:
            cb = QCheckBox(label)
            cb.setToolTip(type_key.title())
            cb.setChecked(default_on)
            if referto_parent_cb and referto_parent_cb.isChecked():
                cb.setEnabled(False)
            sub_row.addWidget(cb)
            sub_row.addStretch()
            doc_cbs[type_key] = cb
            all_cbs.append(cb)
            referto_sub_cbs.append(cb)
        group_layout.addLayout(sub_row)

        # Other types
        other_row = QHBoxLayout()
        for type_key, label, default_on in PATIENT_DOCUMENT_TYPES:
            if type_key not in REFERTO_SUBTYPES and type_key != "REFERTO":
                cb = QCheckBox(label)
                cb.setChecked(default_on)
                other_row.addWidget(cb)
                doc_cbs[type_key] = cb
                all_cbs.append(cb)
        other_row.addStretch()
        group_layout.addLayout(other_row)

        return tutti_cb, doc_cbs

    def _on_date_preset_changed(self, preset: str) -> None:
        """Handle date preset combobox selection change."""
        if preset == "Tutte":
            self._date_from_entry.clear()
            self._date_to_entry.clear()
            self._date_from_entry.setEnabled(False)
            self._date_to_entry.setEnabled(False)
        elif preset == "Personalizzato":
            self._date_from_entry.setEnabled(True)
            self._date_to_entry.setEnabled(True)
        elif preset in DATE_PRESET_DAYS:
            days = DATE_PRESET_DAYS[preset]
            today = date.today()
            from_date = today - timedelta(days=days)
            self._date_from_entry.setText(from_date.strftime("%d/%m/%Y"))
            self._date_to_entry.setText(today.strftime("%d/%m/%Y"))
            self._date_from_entry.setEnabled(False)
            self._date_to_entry.setEnabled(False)

    def _update_ente_combobox(self, enti: list[str]) -> None:
        """Update the Ente/Struttura combobox with values from the table."""
        current = self._ente_combo.currentText()
        self._ente_combo.clear()
        self._ente_combo.addItem("")
        self._ente_combo.addItems(enti)
        idx = self._ente_combo.findText(current)
        if idx >= 0:
            self._ente_combo.setCurrentIndex(idx)

    def _build_settings_tab(self, parent: QWidget) -> None:
        """Build the Settings tab content with grouped QGroupBoxes."""
        spec = {key: (label, default, kind) for key, label, default, kind in SETTINGS_SPEC}

        layout = QVBoxLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)

        # Top row: two columns
        top_layout = QHBoxLayout()

        # ── Left column: Server Posta ──
        mail_group = QGroupBox("Server Posta")
        mail_layout = QGridLayout(mail_group)
        mail_layout.setColumnStretch(1, 1)

        mail_tooltips = {
            "IMAP_HOST": "Indirizzo del server di posta in arrivo (IMAP)",
            "IMAP_PORT": "Porta del server IMAP (993 per connessioni SSL)",
        }
        self._settings_entries: dict[str, QLineEdit] = {}
        for r, key in enumerate(["EMAIL_USER", "EMAIL_PASS", "IMAP_HOST", "IMAP_PORT"]):
            label_text, default, kind = spec[key]
            mail_layout.addWidget(QLabel(label_text), r, 0)
            entry = QLineEdit(default)
            if kind == "password":
                entry.setEchoMode(QLineEdit.EchoMode.Password)
            entry.setToolTip(mail_tooltips.get(key, ""))
            mail_layout.addWidget(entry, r, 1)
            self._settings_entries[key] = entry
            self._fields[key] = default

        # Test connection + Reset default buttons
        mail_btn_row = QHBoxLayout()
        self._btn_test_imap = QPushButton("Test connessione")
        self._btn_test_imap.clicked.connect(self._test_imap_connection)
        self._btn_test_imap.setToolTip("Verifica connessione e login al server di posta")
        mail_btn_row.addWidget(self._btn_test_imap)

        btn_reset_imap = QPushButton("Ripristina default")
        btn_reset_imap.setToolTip("Ripristina host e porta IMAP ai valori predefiniti")
        btn_reset_imap.clicked.connect(self._reset_imap_defaults)
        mail_btn_row.addWidget(btn_reset_imap)

        mail_layout.addLayout(mail_btn_row, len(["EMAIL_USER", "EMAIL_PASS", "IMAP_HOST", "IMAP_PORT"]), 0, 1, 2)

        top_layout.addWidget(mail_group)

        # ── Right column: Browser e Download ──
        br_group = QGroupBox("Browser e Download")
        br_layout = QGridLayout(br_group)
        br_layout.setColumnStretch(1, 1)

        r = 0
        br_layout.addWidget(QLabel("Browser"), r, 0)
        self._build_browser_selector_row(br_layout, r, "BROWSER_CHANNEL", spec["BROWSER_CHANNEL"][1])

        r += 1
        br_layout.addWidget(QLabel("Lettore PDF"), r, 0)
        self._build_pdf_reader_row(br_layout, r, "PDF_READER", spec["PDF_READER"][1])

        r += 1
        br_layout.addWidget(QLabel("salva in"), r, 0)
        dl_row = QHBoxLayout()
        self._download_dir_entry = QLineEdit(spec["DOWNLOAD_DIR"][1])
        self._download_dir_entry.setToolTip(spec["DOWNLOAD_DIR"][1])
        self._download_dir_entry.textChanged.connect(
            lambda text: self._download_dir_entry.setToolTip(text)
        )
        dl_row.addWidget(self._download_dir_entry, 1)
        browse_btn = QPushButton("...")
        browse_btn.setFixedWidth(30)
        browse_btn.setObjectName("browseBtn")
        browse_btn.clicked.connect(self._browse_download_dir)
        dl_row.addWidget(browse_btn)
        br_layout.addLayout(dl_row, r, 1, 1, 2)
        self._fields["DOWNLOAD_DIR"] = spec["DOWNLOAD_DIR"][1]

        r += 1
        self._cdp_cb = QCheckBox("Usa browser CDP")
        self._cdp_cb.setChecked(spec["USE_EXISTING_BROWSER"][1].lower() == "true")
        self._cdp_cb.setToolTip(
            "Connettiti a un browser gia' aperto invece di avviarne uno nuovo.\n"
            "In modalita' CDP il browser lavora in background: le azioni sono visibili\n"
            "solo nella console dell'app. Disattiva questa opzione per vedere il browser\n"
            "in azione durante l'automazione."
        )
        br_layout.addWidget(self._cdp_cb, r, 0, 1, 2)
        self._fields["USE_EXISTING_BROWSER"] = spec["USE_EXISTING_BROWSER"][1]

        self._open_after_cb = QCheckBox("Apri al termine")
        self._open_after_cb.setChecked(True)
        self._open_after_cb.setToolTip("Apri automaticamente i PDF scaricati al termine del download")
        br_layout.addWidget(self._open_after_cb, r, 2)
        self._fields["OPEN_AFTER_DOWNLOAD"] = "true"

        # CDP port: hidden field for code compatibility
        self._fields["CDP_PORT"] = spec["CDP_PORT"][1]

        r += 1
        self._cdp_registry_cb = QCheckBox("Abilita CDP nel registro")
        is_firefox = (
            self._default_browser_info
            and self._default_browser_info.get("channel") == "firefox"
        )
        self._cdp_registry_cb.setEnabled(not is_firefox and bool(self._default_browser_info))
        self._cdp_registry_cb.setToolTip("Modifica il registro di Windows per avviare il browser predefinito con il supporto CDP attivo")
        self._cdp_registry_cb.toggled.connect(self._on_cdp_registry_toggled)
        br_layout.addWidget(self._cdp_registry_cb, r, 0, 1, 2)
        self._sync_cdp_registry_checkbox()

        self._headless_cb = QCheckBox("Headless browser")
        self._headless_cb.setChecked(spec["HEADLESS"][1].lower() == "true")
        self._headless_cb.setToolTip(
            "Esegui il browser in background, senza finestra visibile.\n"
            "Utile per esecuzioni non presidiate, ma impedisce il login manuale SSO.\n"
            "Disattiva questa opzione per vedere il browser pilotato dall'app."
        )
        br_layout.addWidget(self._headless_cb, r, 2)
        self._fields["HEADLESS"] = spec["HEADLESS"][1]

        top_layout.addWidget(br_group)
        layout.addLayout(top_layout)

        # ── Bottom (full-width): Parametri ──
        params_group = QGroupBox("Parametri")
        params_layout = QVBoxLayout(params_group)

        # Row 0: timeouts
        params_row0 = QHBoxLayout()

        params_row0.addWidget(QLabel("Download timeout (sec)"))
        self._dl_timeout_entry = QLineEdit(spec["DOWNLOAD_TIMEOUT"][1])
        self._dl_timeout_entry.setFixedWidth(60)
        self._dl_timeout_entry.setToolTip("Tempo massimo di attesa (in secondi) per il download di un documento")
        params_row0.addWidget(self._dl_timeout_entry)
        self._fields["DOWNLOAD_TIMEOUT"] = spec["DOWNLOAD_TIMEOUT"][1]

        params_row0.addStretch()

        params_row0.addWidget(QLabel("Page timeout (sec)"))
        self._pg_timeout_entry = QLineEdit(spec["PAGE_TIMEOUT"][1])
        self._pg_timeout_entry.setFixedWidth(60)
        self._pg_timeout_entry.setToolTip("Tempo massimo di attesa (in secondi) per il caricamento di una pagina")
        params_row0.addWidget(self._pg_timeout_entry)
        self._fields["PAGE_TIMEOUT"] = spec["PAGE_TIMEOUT"][1]

        params_row0.addStretch()

        params_row0.addWidget(QLabel("Dim. carattere console"))
        self._font_size_entry = QLineEdit(spec["CONSOLE_FONT_SIZE"][1])
        self._font_size_entry.setFixedWidth(60)
        params_row0.addWidget(self._font_size_entry)
        self._fields["CONSOLE_FONT_SIZE"] = spec["CONSOLE_FONT_SIZE"][1]

        params_layout.addLayout(params_row0)

        # Fields for settings persistence displayed in SISS tab
        self._fields["MAX_EMAILS"] = spec["MAX_EMAILS"][1]
        self._fields["MARK_AS_READ"] = spec["MARK_AS_READ"][1]
        self._fields["DELETE_AFTER_PROCESSING"] = spec["DELETE_AFTER_PROCESSING"][1]

        # Save button
        save_btn = QPushButton("Salva Impostazioni")
        save_btn.clicked.connect(self._save_settings)
        params_layout.addWidget(save_btn)

        layout.addWidget(params_group)
        layout.addStretch()

    def _build_pdf_reader_row(self, parent_layout: QGridLayout, row: int, key: str, default: str) -> None:
        """Build the PDF reader selection row with combobox."""
        self._fields[key] = default

        # Build combo values and bidirectional maps
        self._pdf_reader_map: dict[str, str] = {}     # display_label -> exe_path
        self._pdf_reader_revmap: dict[str, str] = {}   # norm(exe_path) -> display_label
        self._rebuild_pdf_combo_values()

        self._pdf_combo = QComboBox()
        self._pdf_combo.addItems(list(self._pdf_reader_map.keys()))
        self._set_pdf_combo_from_value(default)
        self._pdf_combo.currentTextChanged.connect(self._on_pdf_reader_changed)
        parent_layout.addWidget(self._pdf_combo, row, 1, 1, 2)

    def _build_browser_selector_row(self, parent_layout: QGridLayout, row: int, key: str, default: str) -> None:
        """Build the browser selection row with combobox."""
        self._fields[key] = default

        self._browser_map: dict[str, str] = {}
        self._browser_revmap: dict[str, str] = {}

        for channel_or_path, display_name in self._browsers:
            self._browser_map[display_name] = channel_or_path
            self._browser_revmap[channel_or_path] = display_name

        self._browser_map[BROWSER_CHROMIUM_LABEL] = BROWSER_CHROMIUM
        self._browser_revmap[BROWSER_CHROMIUM] = BROWSER_CHROMIUM_LABEL

        self._browser_combo = QComboBox()
        self._browser_combo.addItems(list(self._browser_map.keys()))

        label = self._browser_revmap.get(default)
        if label:
            self._browser_combo.setCurrentText(label)
        elif self._browser_map:
            self._browser_combo.setCurrentIndex(0)

        self._browser_combo.currentTextChanged.connect(self._on_browser_changed)
        parent_layout.addWidget(self._browser_combo, row, 1, 1, 2)

    def _on_browser_changed(self, selected_label: str) -> None:
        """Handle browser combobox selection change."""
        channel = self._browser_map.get(selected_label, "msedge")
        self._fields["BROWSER_CHANNEL"] = channel
        self._update_browser_info_label()

    def _update_browser_info_label(self) -> None:
        """Update the compact browser info text in the SISS tab."""
        friendly_names = {
            "MSEdgeHTM": "Microsoft Edge",
            "ChromeHTML": "Google Chrome",
            "BraveHTML": "Brave",
            "FirefoxURL": "Mozilla Firefox",
            "FirefoxHTML": "Mozilla Firefox",
        }
        default_name = "Non rilevato"
        if self._default_browser_info:
            progid = self._default_browser_info["progid"]
            default_name = next(
                (v for k, v in friendly_names.items() if progid.startswith(k)),
                progid,
            )

        text = f"Browser predefinito: {default_name}"

        default_channel = self._default_browser_info.get("channel") if self._default_browser_info else None
        selected_channel = self._fields.get("BROWSER_CHANNEL", "")
        if default_channel != selected_channel:
            selected_label = self._browser_revmap.get(selected_channel, selected_channel)
            text += f"  |  Browser selezionato: {selected_label}"

        if hasattr(self, "_browser_info_label"):
            self._browser_info_label.setText(text)

    def _sync_cdp_registry_checkbox(self) -> None:
        """Read the current CDP registry status and update the checkbox."""
        if not self._default_browser_info:
            self._cdp_registry_cb.setChecked(False)
            return
        progid = self._default_browser_info["progid"]
        port = int(self._fields.get("CDP_PORT", "9222") or "9222")
        enabled = get_cdp_registry_status(progid, port)
        self._cdp_registry_cb.blockSignals(True)
        self._cdp_registry_cb.setChecked(enabled)
        self._cdp_registry_cb.blockSignals(False)

    def _on_cdp_registry_toggled(self, checked: bool) -> None:
        """Handle CDP registry checkbox toggle."""
        if not self._default_browser_info:
            return
        progid = self._default_browser_info["progid"]
        port = int(self._fields.get("CDP_PORT", "9222") or "9222")

        try:
            if checked:
                enable_cdp_in_registry(progid, port)
                self._log(f"CDP abilitato nel registro per {progid} (porta {port})")
            else:
                disable_cdp_in_registry(progid)
                self._log(f"CDP disabilitato nel registro per {progid}")
        except Exception as e:
            QMessageBox.critical(self, "Errore", f"Impossibile modificare il registro:\n{e}")
            self._sync_cdp_registry_checkbox()

    def _rebuild_pdf_combo_values(self, extra_exe: str | None = None) -> None:
        """Rebuild the combobox value maps from scratch."""
        self._pdf_reader_map.clear()
        self._pdf_reader_revmap.clear()

        self._pdf_reader_map[PDF_READER_DEFAULT_LABEL] = PDF_READER_DEFAULT
        self._pdf_reader_revmap[_norm(PDF_READER_DEFAULT)] = PDF_READER_DEFAULT_LABEL

        for exe_path, display_name in self._pdf_readers:
            nk = _norm(exe_path)
            if nk not in self._pdf_reader_revmap:
                self._pdf_reader_map[display_name] = exe_path
                self._pdf_reader_revmap[nk] = display_name

        if extra_exe and extra_exe != PDF_READER_DEFAULT:
            nk = _norm(extra_exe)
            if nk not in self._pdf_reader_revmap and Path(extra_exe).exists():
                display = _get_app_display_name(extra_exe, "")
                self._pdf_reader_map[display] = extra_exe
                self._pdf_reader_revmap[nk] = display

        self._pdf_reader_map[PDF_READER_CUSTOM_LABEL] = PDF_READER_CUSTOM

        if hasattr(self, "_pdf_combo"):
            self._pdf_combo.blockSignals(True)
            self._pdf_combo.clear()
            self._pdf_combo.addItems(list(self._pdf_reader_map.keys()))
            self._pdf_combo.blockSignals(False)

    def _set_pdf_combo_from_value(self, value: str) -> None:
        """Set the combobox selection from a stored value."""
        if not value or value == PDF_READER_DEFAULT:
            self._pdf_combo.setCurrentText(PDF_READER_DEFAULT_LABEL)
            return
        nk = _norm(value)
        if nk in self._pdf_reader_revmap:
            self._pdf_combo.setCurrentText(self._pdf_reader_revmap[nk])
            return
        self._rebuild_pdf_combo_values(extra_exe=value)
        if nk in self._pdf_reader_revmap:
            self._pdf_combo.setCurrentText(self._pdf_reader_revmap[nk])
        else:
            self._pdf_combo.setCurrentText(PDF_READER_DEFAULT_LABEL)

    def _on_pdf_reader_changed(self, selected_label: str) -> None:
        """Handle combobox selection change."""
        exe_path = self._pdf_reader_map.get(selected_label, PDF_READER_DEFAULT)

        if exe_path == PDF_READER_CUSTOM:
            self._show_pdf_picker_dialog()
        else:
            self._fields["PDF_READER"] = exe_path

    def _show_pdf_picker_dialog(self) -> None:
        """Show a dialog listing all detected PDF readers."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Scegli lettore PDF")
        dlg.resize(480, 350)
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel("Seleziona un'applicazione per aprire i file PDF:"))

        listbox = QListWidget()
        listbox.setFont(QFont("Segoe UI", 10))
        items: list[tuple[str, str]] = []
        for exe_path, display_name in self._pdf_readers:
            items.append((display_name, exe_path))
            listbox.addItem(display_name)

        # Pre-select current value
        current = self._fields.get("PDF_READER", PDF_READER_DEFAULT)
        if current and current != PDF_READER_DEFAULT:
            for idx, (_, exe) in enumerate(items):
                if _norm(exe) == _norm(current):
                    listbox.setCurrentRow(idx)
                    break

        layout.addWidget(listbox)

        result = {"exe": None}

        def on_ok():
            sel = listbox.currentRow()
            if sel >= 0:
                result["exe"] = items[sel][1]
            dlg.accept()

        def on_browse():
            path, _ = QFileDialog.getOpenFileName(
                dlg, "Seleziona lettore PDF", "",
                "Eseguibili (*.exe);;Tutti i file (*.*)",
            )
            if path:
                result["exe"] = path
                dlg.accept()

        btn_layout = QHBoxLayout()
        browse_btn = QPushButton("Sfoglia...")
        browse_btn.clicked.connect(on_browse)
        btn_layout.addWidget(browse_btn)
        btn_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(on_ok)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Annulla")
        cancel_btn.clicked.connect(dlg.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

        listbox.itemDoubleClicked.connect(lambda: on_ok())

        accepted = dlg.exec() == QDialog.DialogCode.Accepted

        if accepted and result["exe"]:
            chosen = result["exe"]
            self._fields["PDF_READER"] = chosen
            self._rebuild_pdf_combo_values(extra_exe=chosen)
            self._set_pdf_combo_from_value(chosen)
        else:
            # Cancelled - revert combo to current stored value
            self._set_pdf_combo_from_value(self._fields.get("PDF_READER", PDF_READER_DEFAULT))

    # ---- Helpers ----

    def _open_guide(self) -> None:
        """Open the user guide HTML file in the default browser."""
        guide_name = "guida_utente.html"
        candidates = [paths.app_dir / guide_name]
        if getattr(sys, "frozen", False):
            candidates.insert(0, Path(sys._MEIPASS) / guide_name)
        for guide in candidates:
            if guide.exists():
                webbrowser.open(guide.as_uri())
                return
        QMessageBox.warning(self, "Guida non trovata", f"Il file guida non è stato trovato:\n{candidates[0]}")

    def _check_updates(self) -> None:
        """Check for updates by fetching version.json from GitHub."""
        VERSION_URL = "https://raw.githubusercontent.com/zndr/FSEpub/main/version.json"

        def worker():
            try:
                req = urllib.request.Request(VERSION_URL, headers={"User-Agent": "FSE-Processor"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                remote_version = data.get("Version", "")
                download_url = data.get("DownloadUrl", "")
                release_notes = data.get("ReleaseNotes", "")

                if remote_version and remote_version != __version__:
                    msg = (
                        f"Nuova versione disponibile: v{remote_version}\n"
                        f"Versione attuale: v{__version__}\n\n"
                    )
                    if release_notes:
                        msg += f"{release_notes}\n\n"
                    if download_url:
                        msg += "Vuoi aprire la pagina di download?"
                    self._bridge.call_on_main.emit(lambda: self._prompt_update(msg, download_url))
                else:
                    self._bridge.show_info.emit(
                        "Aggiornamenti",
                        f"Nessun aggiornamento disponibile.\n\nVersione attuale: v{__version__}",
                    )
            except Exception as e:
                self._bridge.show_error.emit(
                    "Aggiornamenti",
                    f"Impossibile verificare gli aggiornamenti:\n{e}",
                )

        threading.Thread(target=worker, daemon=True).start()

    def _prompt_update(self, msg: str, download_url: str) -> None:
        """Show update dialog and optionally open download URL."""
        if download_url:
            reply = QMessageBox.question(self, "Aggiornamento disponibile", msg)
            if reply == QMessageBox.StandardButton.Yes:
                webbrowser.open(download_url)
        else:
            QMessageBox.information(self, "Aggiornamento disponibile", msg)

    def _show_debug_info(self) -> None:
        """Show debug information dialog."""
        import PySide6

        browser_channel = self._fields.get("BROWSER_CHANNEL", "N/A")
        browser_label = self._browser_revmap.get(browser_channel, browser_channel)
        cdp_enabled = self._fields.get("USE_EXISTING_BROWSER", "false")
        headless = self._fields.get("HEADLESS", "false")

        info = (
            f"FSE Processor v{__version__}\n"
            f"{'=' * 40}\n\n"
            f"Sistema operativo: {platform.platform()}\n"
            f"Python: {sys.version}\n"
            f"PySide6: {PySide6.__version__}\n\n"
            f"Directory app: {paths.app_dir}\n"
            f"Directory browser: {paths.browser_data_dir}\n"
            f"Directory download: {self._download_dir_entry.text()}\n"
            f"File impostazioni: {ENV_FILE}\n"
            f"Directory log: {paths.log_dir}\n\n"
            f"Browser selezionato: {browser_label}\n"
            f"CDP abilitato: {cdp_enabled}\n"
            f"Headless: {headless}\n"
            f"Frozen (PyInstaller): {getattr(sys, 'frozen', False)}\n"
        )

        if getattr(sys, "frozen", False):
            info += f"MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}\n"

        # Append console content if available
        siss_log = self._console.toPlainText().strip()
        patient_log = self._patient_console.toPlainText().strip()
        if siss_log:
            info += f"\n{'=' * 40}\nConsole SISS:\n{'=' * 40}\n{siss_log}\n"
        if patient_log:
            info += f"\n{'=' * 40}\nConsole Paziente:\n{'=' * 40}\n{patient_log}\n"

        dlg = QDialog(self)
        dlg.setWindowTitle("Debug Info")
        dlg.resize(520, 480)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel("Descrizione del problema:"))
        problem_text = QTextEdit()
        problem_text.setFont(QFont("Segoe UI", 10))
        problem_text.setPlaceholderText("Descrivi qui il problema riscontrato...")
        problem_text.setMaximumHeight(100)
        layout.addWidget(problem_text)

        layout.addWidget(QLabel("Informazioni di debug:"))
        debug_text = QTextEdit()
        debug_text.setReadOnly(True)
        debug_text.setFont(QFont("Consolas", 9))
        debug_text.setPlainText(info)
        layout.addWidget(debug_text)

        def _get_full_report() -> str:
            desc = problem_text.toPlainText().strip()
            report = ""
            if desc:
                report += f"Descrizione problema:\n{desc}\n\n"
            report += info
            return report

        btn_layout = QHBoxLayout()
        copy_btn = QPushButton("Copia")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(_get_full_report()))
        btn_layout.addWidget(copy_btn)

        preview_btn = QPushButton("Anteprima")
        preview_btn.setToolTip("Visualizza il messaggio che verra' inviato al supporto (con CF oscurati)")
        preview_btn.clicked.connect(lambda: self._show_send_preview(self._sanitize_cf(_get_full_report())))
        btn_layout.addWidget(preview_btn)

        send_btn = QPushButton("Invia")
        send_btn.setToolTip("Invia le informazioni di debug via email a supporto@dottorgiorgio.it")
        send_btn.clicked.connect(lambda: self._send_debug_email(_get_full_report(), dlg))
        btn_layout.addWidget(send_btn)

        btn_layout.addStretch()
        close_btn = QPushButton("Chiudi")
        close_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dlg.exec()

    def _show_send_preview(self, sanitized_body: str) -> None:
        """Show a preview of the sanitized message that will be sent."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Anteprima messaggio")
        dlg.resize(520, 400)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel("Questo e' il messaggio che verra' inviato al supporto:"))
        text = QTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Consolas", 9))
        text.setPlainText(sanitized_body)
        layout.addWidget(text)

        close_btn = QPushButton("Chiudi")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)

        dlg.exec()

    @staticmethod
    def _sanitize_cf(text: str) -> str:
        """Replace any Italian codice fiscale in text with a placeholder."""
        return re.sub(
            r'[A-Z]{6}\d{2}[A-EHLMPRST]\d{2}[A-Z]\d{3}[A-Z]',
            'XXXYYY11Z22H123T',
            text,
            flags=re.IGNORECASE,
        )

    def _send_debug_email(self, body: str, dlg: QDialog = None) -> None:
        """Send debug info via SMTP using configured email credentials."""
        import email.message
        import smtplib
        import ssl

        self._sync_fields_from_widgets()
        user = self._fields.get("EMAIL_USER", "").strip()
        password = self._fields.get("EMAIL_PASS", "").strip()
        imap_host = self._fields.get("IMAP_HOST", "").strip()

        if not user or not password or not imap_host:
            QMessageBox.warning(
                self, "Errore",
                "Configura le credenziali email nelle Impostazioni prima di inviare.",
            )
            return

        sanitized_body = self._sanitize_cf(body)

        subject = f"FSE Processor v{__version__} - Debug Info"
        dest = "supporto@dottorgiorgio.it"

        def worker():
            try:
                msg = email.message.EmailMessage()
                msg["Subject"] = subject
                msg["From"] = user
                msg["To"] = dest
                msg.set_content(sanitized_body)

                ctx = ssl.create_default_context()
                # Try SMTP STARTTLS on port 587, fallback to SSL on port 465
                sent = False
                for port, use_ssl in [(587, False), (465, True)]:
                    try:
                        if use_ssl:
                            server = smtplib.SMTP_SSL(imap_host, port, context=ctx, timeout=15)
                        else:
                            server = smtplib.SMTP(imap_host, port, timeout=15)
                            server.starttls(context=ctx)
                        server.login(user, password)
                        server.send_message(msg)
                        server.quit()
                        sent = True
                        break
                    except Exception:
                        continue

                if sent:
                    self._bridge.show_info.emit(
                        "Supporto",
                        "Messaggio inviato.\n\n"
                        "Tutti i codici fiscali sono stati rimossi per tutelare la privacy dei pazienti.",
                    )
                    if dlg:
                        self._bridge.call_on_main.emit(dlg.accept)
                else:
                    self._bridge.show_error.emit(
                        "Errore",
                        "Impossibile inviare il messaggio.\n"
                        "Verifica le credenziali email nelle Impostazioni.",
                    )
            except Exception as e:
                self._bridge.show_error.emit("Errore", f"Invio fallito:\n{e}")

        threading.Thread(target=worker, daemon=True).start()

    def _show_about(self) -> None:
        """Show the About dialog."""
        QMessageBox.about(
            self,
            "About FSE Processor",
            f"<h3>FSE Processor v{__version__}</h3>"
            f"<p>Strumento per il download automatico dei referti "
            f"dal Fascicolo Sanitario Elettronico (FSE) della Regione Lombardia.</p>"
            f"<p>Interfaccia grafica basata su Qt6 (PySide6).</p>"
            f"<hr>"
            f"<p style='color: gray; font-size: small;'>"
            f"Python {platform.python_version()} | "
            f"{platform.system()} {platform.release()}</p>",
        )

    def _browse_download_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Seleziona directory", self._download_dir_entry.text() or ".")
        if path:
            self._download_dir_entry.setText(os.path.normpath(path))

    def _log(self, msg: str) -> None:
        """Append a message to the console (main-thread safe)."""
        self._bridge.append_text.emit(self._console, msg)

    def _patient_log(self, msg: str) -> None:
        """Append a message to the patient console (main-thread safe)."""
        self._bridge.append_text.emit(self._patient_console, msg)

    def _sync_fields_from_widgets(self) -> None:
        """Read current widget values into _fields dict."""
        for key, entry in self._settings_entries.items():
            self._fields[key] = entry.text()
        self._fields["DOWNLOAD_DIR"] = self._download_dir_entry.text()
        self._fields["DOWNLOAD_TIMEOUT"] = self._dl_timeout_entry.text()
        self._fields["PAGE_TIMEOUT"] = self._pg_timeout_entry.text()
        self._fields["CONSOLE_FONT_SIZE"] = self._font_size_entry.text()
        self._fields["HEADLESS"] = "true" if self._headless_cb.isChecked() else "false"
        self._fields["USE_EXISTING_BROWSER"] = "true" if self._cdp_cb.isChecked() else "false"
        self._fields["OPEN_AFTER_DOWNLOAD"] = "true" if self._open_after_cb.isChecked() else "false"
        # BROWSER_CHANNEL and PDF_READER are already updated via combobox handlers
        # Sync fields from SISS tab widgets
        self._fields["MAX_EMAILS"] = self._max_email_entry.text()
        self._fields["MARK_AS_READ"] = "true" if self._siss_mark_cb.isChecked() else "false"
        self._fields["DELETE_AFTER_PROCESSING"] = "true" if self._siss_delete_cb.isChecked() else "false"

    def _get_field_values(self) -> dict[str, str]:
        """Collect current field values as strings for env file."""
        self._sync_fields_from_widgets()
        values: dict[str, str] = {}
        for key, _, _, kind in SETTINGS_SPEC:
            val = self._fields.get(key, "")
            if isinstance(val, bool):
                values[key] = "true" if val else "false"
            else:
                values[key] = str(val)
        return values

    # ---- Settings ----

    def _load_settings(self) -> None:
        env_vals = _load_env_values()
        for key, _, default, kind in SETTINGS_SPEC:
            val = env_vals.get(key, default)
            self._fields[key] = val

            # Update widgets
            if key in self._settings_entries:
                self._settings_entries[key].setText(val)
            elif key == "DOWNLOAD_DIR":
                self._download_dir_entry.setText(os.path.normpath(val))
            elif key == "DOWNLOAD_TIMEOUT":
                self._dl_timeout_entry.setText(val)
            elif key == "PAGE_TIMEOUT":
                self._pg_timeout_entry.setText(val)
            elif key == "CONSOLE_FONT_SIZE":
                self._font_size_entry.setText(val)
            elif key == "HEADLESS":
                self._headless_cb.setChecked(val.lower() == "true")
            elif key == "USE_EXISTING_BROWSER":
                self._cdp_cb.setChecked(val.lower() == "true")
            elif key == "OPEN_AFTER_DOWNLOAD":
                self._open_after_cb.setChecked(val.lower() == "true")
            elif key == "BROWSER_CHANNEL":
                label = self._browser_revmap.get(val)
                if label:
                    self._browser_combo.setCurrentText(label)
            elif key == "PDF_READER":
                self._set_pdf_combo_from_value(val)
            elif key == "MAX_EMAILS":
                self._max_email_entry.setText(val)
            elif key == "MARK_AS_READ":
                self._siss_mark_cb.setChecked(val.lower() == "true")
            elif key == "DELETE_AFTER_PROCESSING":
                self._siss_delete_cb.setChecked(val.lower() == "true")

        # Apply initial state for delete toggle
        self._on_delete_toggled(self._siss_delete_cb.isChecked())

    def _save_settings(self) -> None:
        values = self._get_field_values()
        try:
            _save_env_values(values)
            QMessageBox.information(self, "Impostazioni", "Impostazioni salvate correttamente.")
        except Exception as e:
            QMessageBox.critical(self, "Errore", f"Impossibile salvare: {e}")

    # ---- Test IMAP connection ----

    def _reset_imap_defaults(self) -> None:
        """Reset IMAP host and port to default values."""
        self._settings_entries["IMAP_HOST"].setText("mail.fastweb360.it")
        self._settings_entries["IMAP_PORT"].setText("993")

    def _test_imap_connection(self) -> None:
        self._btn_test_imap.setEnabled(False)
        self._btn_test_imap.setText("Test in corso...")
        threading.Thread(target=self._test_imap_worker, daemon=True).start()

    def _test_imap_worker(self) -> None:
        try:
            self._save_settings_quietly()
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            client = EmailClient(config, logger)
            client.connect()
            client.disconnect()
            self._bridge.show_info.emit(
                "Test connessione",
                f"Connessione riuscita!\n\n"
                f"Server: {config.imap_host}:{config.imap_port}\n"
                f"Utente: {config.email_user}",
            )
        except Exception as e:
            err_msg = str(e) if str(e) and str(e) != "None" else (
                f"{type(e).__name__}: {e.args}" if e.args else type(e).__name__
            )
            self._bridge.show_error.emit("Test connessione", f"Connessione fallita:\n\n{err_msg}")
        finally:
            self._bridge.call_on_main.emit(
                lambda: (self._btn_test_imap.setEnabled(True), self._btn_test_imap.setText("Test connessione"))
            )

    # ---- Check email ----

    def _check_email(self) -> None:
        self._btn_check.setEnabled(False)
        self._log("Connessione IMAP per conteggio email...")
        threading.Thread(target=self._check_email_worker, daemon=True).start()

    def _check_email_worker(self) -> None:
        try:
            self._save_settings_quietly()
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            client = EmailClient(config, logger)
            client.connect()
            emails = client.fetch_unread_emails()
            client.disconnect()
            count = len(emails)
            if count == 0:
                msg = "Nessuna email con referti da scaricare"
            elif client.limit_reached:
                msg = (
                    f"Trovati {count} messaggi da scaricare "
                    f"(raggiunto limite specificato nelle impostazioni)"
                )
            else:
                msg = f"{count} email con referti da scaricare"
            self._bridge.append_text.emit(self._console, msg)
            self._bridge.show_info.emit("Conteggio Email", msg)
        except Exception as e:
            err_msg = str(e) if str(e) and str(e) != "None" else (
                f"{type(e).__name__}: {e.args}" if e.args else type(e).__name__
            )
            tb = traceback.format_exc()
            self._bridge.append_text.emit(self._console, f"Errore: {err_msg}\n{tb}")
            self._bridge.show_error.emit("Errore", err_msg)
        finally:
            self._bridge.call_on_main.emit(lambda: self._btn_check.setEnabled(True))

    def _save_settings_quietly(self) -> None:
        """Save current settings without user feedback."""
        values = self._get_field_values()
        _save_env_values(values)

    # ---- Processing ----

    def _start_processing(self) -> None:
        if self._worker and self._worker.is_alive():
            QMessageBox.warning(self, "Attenzione", "Processamento gia' in corso")
            return
        if self._patient_worker and self._patient_worker.is_alive():
            QMessageBox.warning(self, "Attenzione", "Download paziente in corso, attendere il completamento")
            return

        selected = self._get_selected_types(self._siss_tutti_cb, self._siss_doc_cbs)
        if selected is not None and not selected:
            QMessageBox.warning(self, "Errore", "Seleziona almeno una tipologia di documento")
            return

        self._save_settings_quietly()
        self._stop_event.clear()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._log("--- Avvio processamento ---")

        self._worker = threading.Thread(target=self._processing_worker, args=(selected,), daemon=True)
        self._worker.start()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_worker)
        self._poll_timer.start(500)

    def _processing_worker(self, allowed_types: set[str] | None) -> None:
        try:
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            handler = TextHandler(self._console, self._bridge)
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger._logger.addHandler(handler)

            run_processing(config, logger, self._stop_event, allowed_types=allowed_types)
        except Exception as e:
            self._bridge.append_text.emit(self._console, f"Errore fatale: {e}")

    def _poll_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return  # Timer continues
        self._poll_timer.stop()
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._log("--- Processamento terminato ---")

    def _stop_processing(self) -> None:
        self._stop_event.set()
        self._log("Richiesta interruzione inviata...")
        self._btn_stop.setEnabled(False)

    # ---- Patient ente scan ----

    def _start_ente_scan(self) -> None:
        if self._ente_scan_worker and self._ente_scan_worker.is_alive():
            QMessageBox.warning(self, "Attenzione", "Scansione strutture gia' in corso")
            return
        if self._patient_worker and self._patient_worker.is_alive():
            QMessageBox.warning(self, "Attenzione", "Download paziente in corso, attendere il completamento")
            return

        cf = self._cf_entry.text().strip().upper()
        if not re.match(r"^[A-Z0-9]{16}$", cf):
            QMessageBox.warning(self, "Errore", "Il codice fiscale deve essere di 16 caratteri alfanumerici")
            return

        self._btn_load_enti.setEnabled(False)
        self._btn_patient_start.setEnabled(False)
        self._btn_patient_stop.setEnabled(True)
        self._patient_stop_event.clear()
        self._patient_log("--- Scansione strutture in corso... ---")

        self._ente_scan_worker = threading.Thread(
            target=self._ente_scan_worker_fn,
            args=(cf,),
            daemon=True,
        )
        self._ente_scan_worker.start()
        self._ente_scan_poll_timer = QTimer(self)
        self._ente_scan_poll_timer.timeout.connect(self._poll_ente_scan)
        self._ente_scan_poll_timer.start(500)

    def _ente_scan_worker_fn(self, codice_fiscale: str) -> None:
        try:
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            handler = TextHandler(self._patient_console, self._bridge)
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger._logger.addHandler(handler)

            browser = FSEBrowser(config, logger)
            browser.start()
            browser.wait_for_manual_login(stop_event=self._patient_stop_event)

            enti = browser.scan_patient_enti(codice_fiscale)
            if enti:
                self._bridge.call_on_main.emit(lambda e=enti: self._update_ente_combobox(e))
                self._bridge.append_text.emit(
                    self._patient_console, f"Trovate {len(enti)} strutture"
                )
            else:
                self._bridge.append_text.emit(
                    self._patient_console, "Nessuna struttura trovata"
                )

            # Save the browser for reuse by download worker
            self._patient_browser = browser
        except Exception as e:
            self._bridge.append_text.emit(self._patient_console, f"Errore scansione strutture: {e}")

    def _poll_ente_scan(self) -> None:
        if self._ente_scan_worker and self._ente_scan_worker.is_alive():
            return
        self._ente_scan_poll_timer.stop()
        self._btn_load_enti.setEnabled(True)
        self._btn_patient_start.setEnabled(True)
        self._btn_patient_stop.setEnabled(False)
        self._patient_log("--- Scansione strutture terminata ---")

    # ---- Patient download ----

    def _start_patient_download(self) -> None:
        if self._patient_worker and self._patient_worker.is_alive():
            QMessageBox.warning(self, "Attenzione", "Download paziente gia' in corso")
            return
        if self._worker and self._worker.is_alive():
            QMessageBox.warning(self, "Attenzione", "Processamento SISS in corso, attendere il completamento")
            return

        cf = self._cf_entry.text().strip().upper()
        if not re.match(r"^[A-Z0-9]{16}$", cf):
            QMessageBox.warning(self, "Errore", "Il codice fiscale deve essere di 16 caratteri alfanumerici")
            return

        selected = self._get_selected_types(self._patient_tutti_cb, self._patient_doc_cbs)
        if selected is not None and not selected:
            QMessageBox.warning(self, "Errore", "Seleziona almeno una tipologia di documento")
            return

        ente_filter = self._ente_combo.currentText().strip()
        date_from = self._parse_user_date(self._date_from_entry.text())
        date_to = self._parse_user_date(self._date_to_entry.text())

        self._save_settings_quietly()
        self._patient_stop_event.clear()
        self._btn_patient_start.setEnabled(False)
        self._btn_patient_stop.setEnabled(True)
        self._btn_load_enti.setEnabled(False)
        self._patient_log(f"--- Avvio download per CF: {cf} ---")

        self._patient_worker = threading.Thread(
            target=self._patient_download_worker,
            args=(cf, selected, ente_filter, date_from, date_to),
            daemon=True,
        )
        self._patient_worker.start()
        self._patient_poll_timer = QTimer(self)
        self._patient_poll_timer.timeout.connect(self._poll_patient_worker)
        self._patient_poll_timer.start(500)

    @staticmethod
    def _parse_user_date(text: str) -> date | None:
        """Parse a dd/mm/yyyy string into a date object."""
        text = text.strip()
        if not text:
            return None
        from datetime import datetime
        try:
            return datetime.strptime(text, "%d/%m/%Y").date()
        except ValueError:
            return None

    def _patient_download_worker(self, codice_fiscale: str,
                                 allowed_types: set[str] | None,
                                 ente_filter: str = "",
                                 date_from: date | None = None,
                                 date_to: date | None = None) -> None:
        try:
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            handler = TextHandler(self._patient_console, self._bridge)
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger._logger.addHandler(handler)

            file_manager = FileManager(config, logger)

            # Reuse browser from ente scan if still alive
            reused = False
            if self._patient_browser is not None:
                try:
                    if self._patient_browser._is_alive():
                        browser = self._patient_browser
                        self._patient_browser = None
                        reused = True
                        logger.info("Riutilizzo browser dalla scansione strutture")
                except Exception:
                    pass
            if not reused:
                browser = FSEBrowser(config, logger)
                browser.start()
                browser.wait_for_manual_login(stop_event=self._patient_stop_event)

            try:
                def on_enti_found(enti):
                    self._bridge.call_on_main.emit(lambda e=enti: self._update_ente_combobox(e))

                doc_results = browser.process_patient_all_dates(
                    codice_fiscale, self._patient_stop_event, allowed_types,
                    ente_filter=ente_filter, date_from=date_from, date_to=date_to,
                    on_enti_found=on_enti_found,
                )

                downloaded = 0
                skipped = 0
                errors = 0
                for result in doc_results:
                    if result.skipped:
                        skipped += 1
                        continue
                    if result.error or not result.download_path:
                        errors += 1
                        continue
                    downloaded += 1
                    file_manager.rename_download(
                        download_path=result.download_path,
                        patient_name=codice_fiscale,
                        codice_fiscale=codice_fiscale,
                        disciplina=result.disciplina,
                        fse_link=f"{FSE_BASE_URL}#/?codiceFiscale={codice_fiscale}",
                    )

                logger.info("--- Riepilogo ---")
                logger.info(f"Scaricati: {downloaded}")
                logger.info(f"Saltati (filtro): {skipped}")
                logger.info(f"Errori: {errors}")
                file_manager.save_mappings()
            finally:
                browser.stop()
        except Exception as e:
            self._bridge.append_text.emit(self._patient_console, f"Errore fatale: {e}")

    def _poll_patient_worker(self) -> None:
        if self._patient_worker and self._patient_worker.is_alive():
            return
        self._patient_poll_timer.stop()
        self._btn_patient_start.setEnabled(True)
        self._btn_patient_stop.setEnabled(False)
        self._btn_load_enti.setEnabled(True)
        self._patient_log("--- Download terminato ---")

    def _stop_patient_download(self) -> None:
        self._patient_stop_event.set()
        self._patient_log("Richiesta interruzione inviata...")
        self._btn_patient_stop.setEnabled(False)

    def closeEvent(self, event) -> None:
        if self._patient_browser is not None:
            try:
                self._patient_browser.stop()
            except Exception:
                pass
            self._patient_browser = None
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLE)
    window = FSEApp()
    window.show()
    sys.exit(app.exec())
