"""GUI PySide6 per FSE Processor."""

import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import os
import platform
import re
import ssl
import subprocess
import sys
import threading
import traceback
import urllib.request
import webbrowser
import winreg
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QPalette, QTextCursor
import qtawesome as qta
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
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_paths import paths
from credential_manager import encrypt_password, decrypt_password, is_encrypted, verify_password
from version import __version__
from browser_automation import (
    FSEBrowser,
    FSE_BASE_URL,
    BrowserCDPNotActive,
    PatientDocumentInfo,
    _is_tipologia_valida,
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
    ("IMAP_FOLDER", "Cartelle IMAP", "INBOX", "text"),
    ("DOWNLOAD_DIR", "Directory download", str(paths.default_download_dir), "dir"),
    ("BROWSER_CHANNEL", "Browser", "msedge", "browser_selector"),
    ("PDF_READER", "Lettore PDF", "default", "pdf_reader"),
    ("USE_EXISTING_BROWSER", "Usa browser esistente (CDP)", "true", "bool"),
    ("OPEN_AFTER_DOWNLOAD", "Apri al termine", "true", "bool"),
    ("CDP_PORT", "Porta CDP", "9222", "int"),
    ("HEADLESS", "Headless browser", "false", "bool"),
    ("DOWNLOAD_TIMEOUT", "Download timeout (sec)", "120", "int"),
    ("PAGE_TIMEOUT", "Page timeout (sec)", "60", "int"),
    ("CONSOLE_FONT_SIZE", "Dim. carattere console", "8", "int"),
    ("DEBUG_LOGGING", "Abilita info debug", "false", "bool"),
    ("MARK_AS_READ", "Marca come letto dopo elaborazione", "true", "bool"),
    ("DELETE_AFTER_PROCESSING", "Elimina email dopo elaborazione", "false", "bool"),
    ("MAX_EMAILS", "Max email da processare (0=tutte)", "3", "int"),
    ("MOVE_DIR", "Sposta referti in", "", "dir"),
    ("PROCESS_TEXT", "Processa il testo del referto", "false", "bool"),
    ("TEXT_DIR", "Salva i testi in", "", "dir"),
    ("PROCESSING_MODE", "Modalita' processazione", "local", "text"),
    ("LLM_PROVIDER", "Provider AI", "", "text"),
    ("LLM_API_KEY", "API Key AI", "", "password"),
    ("LLM_MODEL", "Modello AI", "", "text"),
    ("LLM_TIMEOUT", "Timeout AI (sec)", "120", "int"),
    ("LLM_BASE_URL", "URL endpoint AI", "", "text"),
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

PATIENT_DOC_TYPES_MAIN = [
    ("LETTERA DIMISSIONE", "Lettera Dimissione"),
    ("VERBALE PRONTO SOCCORSO", "Verbale Pronto Soccorso"),
]

REFERTO_SUBTYPE_ITEMS = [
    ("Tutti", "REFERTO"),
    ("Referto non specificato", "REFERTO SPECIALISTICO"),
    ("Radiologia", "REFERTO SPECIALISTICO RADIOLOGIA"),
    ("Laboratorio", "REFERTO SPECIALISTICO LABORATORIO"),
    ("Anatomia patologica", "REFERTO ANATOMIA PATOLOGICA"),
]

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
QCheckBox:disabled {
    color: #a0a0a0;
}
QCheckBox::indicator:checked:disabled {
    background-color: #b0c4de;
    border-color: #c0c0c0;
}
QCheckBox::indicator:unchecked:disabled {
    background-color: #e8e8e8;
    border-color: #c0c0c0;
}

/* ---------- QRadioButton ---------- */
QRadioButton:disabled {
    color: #a0a0a0;
}

/* ---------- QListWidget ---------- */
QListWidget:disabled {
    background-color: #f0f0f0;
    color: #a0a0a0;
}

/* ---------- Utility ---------- */
.subtle-label {
    color: #6b7b8d;
}
"""


def _validate_provider_model(provider: str, model: str) -> str:
    """Check that a model name is plausible for the given provider.

    Returns an error message string, or empty string if valid.
    """
    provider_prefixes: dict[str, tuple[str, ...]] = {
        "claude_api": ("claude-",),
        "claude_cli": ("claude-",),
        "openai_api": ("gpt-", "o1", "o3", "o4", "chatgpt-"),
        "gemini_api": ("gemini-",),
        "mistral_api": ("mistral-",),
    }
    prefixes = provider_prefixes.get(provider)
    if prefixes and not model.lower().startswith(prefixes):
        from text_processing.llm_analyzer import PROVIDER_LABELS
        label = PROVIDER_LABELS.get(provider, provider)
        expected = ", ".join(p + "..." for p in prefixes)
        return (
            f"Il modello '{model}' non sembra compatibile con {label}.\n"
            f"I modelli per questo provider iniziano con: {expected}\n\n"
            f"Correggi il modello o cambia provider."
        )
    return ""


def _version_tuple(v: str) -> tuple[int, ...]:
    """Convert a version string like '2.3.5' to a comparable tuple (2, 3, 5)."""
    return tuple(int(x) for x in v.split("."))


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


# ---- Setup Wizard ----

class SetupWizard(QDialog):
    """Guided setup wizard shown on first run or from Help menu."""

    # Signals for thread-safe GUI updates from worker threads
    _sig_info = Signal(str, str)
    _sig_error = Signal(str, str)
    _sig_call = Signal(object)

    _STEP_TITLES = [
        "Benvenuto",
        "Account Email",
        "Server di Posta (IMAP)",
        "Cartelle",
        "Browser e PDF",
        "Elaborazione Testo",
        "Parametri",
        "Riepilogo",
    ]

    def __init__(
        self,
        parent: "FSEApp",
        browsers: list[tuple[str, str]],
        pdf_readers: list[tuple[str, str]],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configurazione guidata")
        self.setMinimumSize(560, 420)
        self._browsers = browsers
        self._pdf_readers = pdf_readers
        self._current = 0

        # Connect signals for thread-safe GUI updates
        self._sig_info.connect(lambda t, m: QMessageBox.information(self, t, m))
        self._sig_error.connect(lambda t, m: QMessageBox.critical(self, t, m))
        self._sig_call.connect(lambda fn: fn())

        # Pre-load existing values (for re-launch on existing config)
        self._env = _load_env_values()

        self._build_ui()

    # ---- helpers ----

    def _env_val(self, key: str, default: str = "") -> str:
        val = self._env.get(key, default)
        if key == "EMAIL_PASS" and val:
            val = decrypt_password(val)
        return val

    # ---- UI construction ----

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # Title label
        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #1a4a8a;")
        outer.addWidget(self._title_label)

        # Stacked pages
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        self._build_step_welcome()
        self._build_step_email()
        self._build_step_imap()
        self._build_step_folders()
        self._build_step_browser()
        self._build_step_text_processing()
        self._build_step_params()
        self._build_step_summary()

        # Navigation bar
        nav = QHBoxLayout()
        self._btn_back = QPushButton("Indietro")
        self._btn_back.setIcon(qta.icon("fa5s.arrow-left", color="white"))
        self._btn_back.clicked.connect(self._go_back)
        nav.addWidget(self._btn_back)

        nav.addStretch()

        self._btn_next = QPushButton("Avanti")
        self._btn_next.setIcon(qta.icon("fa5s.arrow-right", color="white"))
        self._btn_next.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._btn_next.clicked.connect(self._go_next)
        nav.addWidget(self._btn_next)

        self._btn_cancel = QPushButton("Annulla")
        self._btn_cancel.setIcon(qta.icon("fa5s.times", color="#333"))
        self._btn_cancel.setStyleSheet(
            "background-color: #e0e0e0; color: #333; border-color: #c0c0c0;"
        )
        self._btn_cancel.clicked.connect(self._on_cancel)
        nav.addWidget(self._btn_cancel)

        outer.addLayout(nav)
        self._update_nav()

    # ---- Step builders ----

    def _build_step_welcome(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addStretch()
        welcome = QLabel(
            "<h2>Benvenuto in FSE Processor</h2>"
            "<p>Questa procedura guidata ti aiutera' a configurare "
            "l'applicazione in pochi semplici passaggi:</p>"
            "<ol>"
            "<li>Account email</li>"
            "<li>Server IMAP</li>"
            "<li>Cartelle di lavoro</li>"
            "<li>Browser e lettore PDF</li>"
            "<li>Parametri avanzati</li>"
            "</ol>"
            "<p>Puoi modificare queste impostazioni in qualsiasi momento "
            "dal tab <b>Impostazioni</b>.</p>"
        )
        welcome.setWordWrap(True)
        lay.addWidget(welcome)
        lay.addStretch()
        self._stack.addWidget(page)

    def _build_step_email(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        group = QGroupBox("Credenziali email")
        grid = QGridLayout(group)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel("Email utente:"), 0, 0)
        self._wiz_email = QLineEdit(self._env_val("EMAIL_USER"))
        self._wiz_email.setPlaceholderText("nome@esempio.it")
        grid.addWidget(self._wiz_email, 0, 1)

        grid.addWidget(QLabel("Password:"), 1, 0)
        self._wiz_pass = QLineEdit(self._env_val("EMAIL_PASS"))
        self._wiz_pass.setEchoMode(QLineEdit.EchoMode.Password)
        grid.addWidget(self._wiz_pass, 1, 1)

        note = QLabel("La password verra' crittografata al salvataggio.")
        note.setStyleSheet("color: #6b7b8d; font-size: 11px;")
        note.setWordWrap(True)
        grid.addWidget(note, 2, 0, 1, 2)

        lay.addWidget(group)
        lay.addStretch()
        self._stack.addWidget(page)

    def _build_step_imap(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        group = QGroupBox("Server di Posta (IMAP)")
        grid = QGridLayout(group)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel("Host:"), 0, 0)
        self._wiz_imap_host = QLineEdit(
            self._env_val("IMAP_HOST", "mail.fastweb360.it")
        )
        grid.addWidget(self._wiz_imap_host, 0, 1)

        grid.addWidget(QLabel("Porta:"), 1, 0)
        self._wiz_imap_port = QLineEdit(self._env_val("IMAP_PORT", "993"))
        self._wiz_imap_port.setFixedWidth(80)
        grid.addWidget(self._wiz_imap_port, 1, 1)

        grid.addWidget(QLabel("Cartelle IMAP:"), 2, 0)
        folder_row = QHBoxLayout()
        self._wiz_imap_folder = QLineEdit(
            self._env_val("IMAP_FOLDER", "INBOX")
        )
        folder_row.addWidget(self._wiz_imap_folder)
        self._wiz_btn_browse = QPushButton("Sfoglia...")
        self._wiz_btn_browse.setIcon(qta.icon("fa5s.folder-open", color="white"))
        self._wiz_btn_browse.clicked.connect(self._wiz_browse_imap_folders)
        folder_row.addWidget(self._wiz_btn_browse)
        grid.addLayout(folder_row, 2, 1)

        # Test connection button
        btn_row = QHBoxLayout()
        self._wiz_btn_test = QPushButton("Test connessione")
        self._wiz_btn_test.setIcon(qta.icon("fa5s.plug", color="white"))
        self._wiz_btn_test.clicked.connect(self._wiz_test_imap)
        btn_row.addWidget(self._wiz_btn_test)
        btn_row.addStretch()
        grid.addLayout(btn_row, 3, 0, 1, 2)

        lay.addWidget(group)

        note = QLabel(
            "<b>Nota per chi usa POP3:</b> questa app si collega alla casella di posta "
            "tramite il protocollo IMAP. Se il tuo client di posta (es. Outlook, Thunderbird) "
            "e' configurato in modalita' POP3, assicurati che l'opzione "
            "\"<i>Lascia una copia dei messaggi sul server</i>\" sia attiva, altrimenti "
            "le email scaricate dal client verranno cancellate dal server e l'app "
            "non potra' trovarle."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            "background-color: #fff8e1; border: 1px solid #ffe082; "
            "border-radius: 4px; padding: 8px; color: #5d4037; font-size: 11px;"
        )
        lay.addWidget(note)

        lay.addStretch()
        self._stack.addWidget(page)

    def _build_step_folders(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        group = QGroupBox("Cartelle di lavoro")
        grid = QGridLayout(group)
        grid.setColumnStretch(1, 1)

        # Download dir
        grid.addWidget(QLabel("Salva referti in:"), 0, 0)
        dl_row = QHBoxLayout()
        self._wiz_download_dir = QLineEdit(
            self._env_val("DOWNLOAD_DIR", str(paths.default_download_dir))
        )
        dl_row.addWidget(self._wiz_download_dir, 1)
        btn_dl = QPushButton("...")
        btn_dl.setFixedWidth(30)
        btn_dl.setObjectName("browseBtn")
        btn_dl.clicked.connect(
            lambda: self._wiz_browse_dir(self._wiz_download_dir, "Seleziona directory download")
        )
        dl_row.addWidget(btn_dl)
        grid.addLayout(dl_row, 0, 1)

        # Move dir
        grid.addWidget(QLabel("Sposta referti in:"), 1, 0)
        mv_row = QHBoxLayout()
        self._wiz_move_dir = QLineEdit(self._env_val("MOVE_DIR"))
        self._wiz_move_dir.setPlaceholderText("(opzionale)")
        mv_row.addWidget(self._wiz_move_dir, 1)
        btn_mv = QPushButton("...")
        btn_mv.setFixedWidth(30)
        btn_mv.setObjectName("browseBtn")
        btn_mv.clicked.connect(
            lambda: self._wiz_browse_dir(self._wiz_move_dir, "Seleziona directory destinazione")
        )
        mv_row.addWidget(btn_mv)
        grid.addLayout(mv_row, 1, 1)

        lay.addWidget(group)
        lay.addStretch()
        self._stack.addWidget(page)

    def _build_step_browser(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        group = QGroupBox("Browser e PDF")
        grid = QGridLayout(group)
        grid.setColumnStretch(1, 1)

        # Browser combo
        grid.addWidget(QLabel("Browser:"), 0, 0)
        self._wiz_browser_combo = QComboBox()
        self._wiz_browser_combo.setToolTip(
            "Il browser usato dall'app per accedere al portale FSE e scaricare i referti.\n"
            "Se hai gia' effettuato il login SSO con un browser specifico, selezionalo qui\n"
            "per riutilizzare la sessione esistente."
        )
        browser_map: dict[str, str] = {}
        for channel_or_path, display_name in self._browsers:
            browser_map[display_name] = channel_or_path
        browser_map[BROWSER_CHROMIUM_LABEL] = BROWSER_CHROMIUM
        self._wiz_browser_map = browser_map

        self._wiz_browser_combo.addItems(list(browser_map.keys()))
        # Pre-select saved value
        saved_browser = self._env_val("BROWSER_CHANNEL", "msedge")
        for label, val in browser_map.items():
            if val == saved_browser:
                self._wiz_browser_combo.setCurrentText(label)
                break
        grid.addWidget(self._wiz_browser_combo, 0, 1)

        # PDF reader combo
        grid.addWidget(QLabel("Lettore PDF:"), 1, 0)
        self._wiz_pdf_combo = QComboBox()
        self._wiz_pdf_combo.setToolTip(
            "Il programma usato per aprire i referti PDF scaricati.\n"
            "\"Predefinito di sistema\" usa il lettore PDF configurato in Windows."
        )
        pdf_map: dict[str, str] = {}
        pdf_map[PDF_READER_DEFAULT_LABEL] = PDF_READER_DEFAULT
        for exe_path, display_name in self._pdf_readers:
            pdf_map[display_name] = exe_path
        self._wiz_pdf_map = pdf_map

        self._wiz_pdf_combo.addItems(list(pdf_map.keys()))
        saved_pdf = self._env_val("PDF_READER", "default")
        for label, val in pdf_map.items():
            if val == saved_pdf or _norm(val) == _norm(saved_pdf):
                self._wiz_pdf_combo.setCurrentText(label)
                break
        grid.addWidget(self._wiz_pdf_combo, 1, 1)

        # Checkboxes
        r = 2
        self._wiz_cdp_cb = QCheckBox("Usa browser CDP")
        self._wiz_cdp_cb.setChecked(
            self._env_val("USE_EXISTING_BROWSER", "true").lower() == "true"
        )
        self._wiz_cdp_cb.setToolTip(
            "Modalita' CDP (Chrome DevTools Protocol): l'app si collega a un browser\n"
            "gia' in esecuzione invece di aprirne uno nuovo. Consigliato: permette\n"
            "di riusare la sessione SSO gia' attiva e lavora in background."
        )
        grid.addWidget(self._wiz_cdp_cb, r, 0, 1, 2)

        r += 1
        self._wiz_cdp_registry_cb = QCheckBox("Abilita CDP nel registro")
        # Read actual registry state from the parent app
        cdp_reg_checked = True
        parent_app = self.parent()
        if hasattr(parent_app, "_default_browser_info") and parent_app._default_browser_info:
            progid = parent_app._default_browser_info["progid"]
            port = int(self._env.get("CDP_PORT", "9222") or "9222")
            cdp_reg_checked = get_cdp_registry_status(progid, port)
            is_firefox = parent_app._default_browser_info.get("channel") == "firefox"
            self._wiz_cdp_registry_cb.setEnabled(not is_firefox)
        else:
            self._wiz_cdp_registry_cb.setEnabled(False)
            cdp_reg_checked = False
        self._wiz_cdp_registry_cb.setChecked(cdp_reg_checked)
        self._wiz_cdp_registry_cb.setToolTip(
            "Modifica il registro di Windows affinche' il browser predefinito\n"
            "si avvii automaticamente con il supporto CDP attivo.\n"
            "Necessario per la modalita' \"Usa browser CDP\".\n"
            "Senza questa opzione dovrai avviare il browser manualmente\n"
            "con il flag --remote-debugging-port."
        )
        grid.addWidget(self._wiz_cdp_registry_cb, r, 0, 1, 2)

        r += 1
        self._wiz_open_after_cb = QCheckBox("Apri referto al termine")
        self._wiz_open_after_cb.setChecked(
            self._env_val("OPEN_AFTER_DOWNLOAD", "true").lower() == "true"
        )
        self._wiz_open_after_cb.setToolTip(
            "Apre automaticamente ogni referto PDF nel lettore\n"
            "subito dopo il download. Utile per verificare al volo\n"
            "i documenti scaricati."
        )
        grid.addWidget(self._wiz_open_after_cb, r, 0, 1, 2)

        r += 1
        self._wiz_headless_cb = QCheckBox("Headless browser")
        self._wiz_headless_cb.setChecked(
            self._env_val("HEADLESS", "false").lower() == "true"
        )
        self._wiz_headless_cb.setToolTip(
            "Esegue il browser in modo completamente invisibile (senza finestra).\n"
            "Utile per esecuzioni automatiche non presidiate.\n"
            "Attenzione: in modalita' headless non potrai effettuare il login SSO\n"
            "manuale, perche' la finestra del browser non sara' visibile."
        )
        grid.addWidget(self._wiz_headless_cb, r, 0, 1, 2)

        lay.addWidget(group)
        lay.addStretch()
        self._stack.addWidget(page)

    def _build_step_text_processing(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)

        intro = QLabel(
            "Il testo viene estratto dai PDF scaricati, anonimizzato "
            "(dati paziente rimossi) e salvato in file .txt. Puoi scegliere se "
            "processare il testo solo in locale oppure con l'aiuto di un servizio AI, "
            "oppure disabilitare completamente l'analisi."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #6b7b8d; font-size: 11px; margin-bottom: 6px;")
        lay.addWidget(intro)

        # ── Mode selection (3 radios) ──
        mode_group = QGroupBox("Modalita' di analisi")
        mode_layout = QVBoxLayout(mode_group)

        saved_mode = self._env_val("PROCESSING_MODE", "local")
        self._wiz_mode_none = QRadioButton("Nessuna — non analizzare i referti")
        self._wiz_mode_local = QRadioButton("Solo estrazione — estrai e anonimizza in locale")
        self._wiz_mode_ai = QRadioButton("Estrazione e analisi con A.I. — richiede account LLM")
        if saved_mode == "ai":
            self._wiz_mode_ai.setChecked(True)
        elif saved_mode == "none":
            self._wiz_mode_none.setChecked(True)
        else:
            self._wiz_mode_local.setChecked(True)
        mode_layout.addWidget(self._wiz_mode_none)
        mode_layout.addWidget(self._wiz_mode_local)
        mode_layout.addWidget(self._wiz_mode_ai)

        lay.addWidget(mode_group)

        # ── AI settings ──
        self._wiz_ai_group = QGroupBox("Impostazioni AI")
        ai_layout = QGridLayout(self._wiz_ai_group)
        ai_layout.setColumnStretch(1, 1)

        from text_processing.llm_analyzer import PROVIDER_LABELS, LABEL_TO_PROVIDER, DEFAULT_MODELS
        self._wiz_provider_labels = PROVIDER_LABELS
        self._wiz_label_to_provider = LABEL_TO_PROVIDER
        self._wiz_default_models = DEFAULT_MODELS

        ar = 0
        ai_layout.addWidget(QLabel("Provider:"), ar, 0)
        self._wiz_llm_provider = QComboBox()
        self._wiz_llm_provider.addItems(list(PROVIDER_LABELS.values()))
        saved_provider = self._env_val("LLM_PROVIDER")
        if saved_provider in PROVIDER_LABELS:
            self._wiz_llm_provider.setCurrentText(PROVIDER_LABELS[saved_provider])
        self._wiz_llm_provider.currentTextChanged.connect(self._wiz_on_provider_changed)
        ai_layout.addWidget(self._wiz_llm_provider, ar, 1)

        ar += 1
        ai_layout.addWidget(QLabel("API Key:"), ar, 0)
        api_key_row = QHBoxLayout()
        self._wiz_llm_api_key = QLineEdit(decrypt_password(self._env_val("LLM_API_KEY")))
        self._wiz_llm_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._wiz_llm_api_key.setPlaceholderText("Inserisci la tua API key")
        api_key_row.addWidget(self._wiz_llm_api_key, 1)
        wiz_show_key = QPushButton("Mostra")
        wiz_show_key.setIcon(qta.icon("fa5s.eye", color="white"))
        wiz_show_key.setFixedWidth(80)
        wiz_show_key.setCheckable(True)
        wiz_show_key.toggled.connect(
            lambda checked: self._wiz_llm_api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        api_key_row.addWidget(wiz_show_key)
        ai_layout.addLayout(api_key_row, ar, 1)

        ar += 1
        ai_layout.addWidget(QLabel("Modello:"), ar, 0)
        self._wiz_llm_model = QComboBox()
        self._wiz_llm_model.setEditable(True)
        self._wiz_llm_model.setToolTip("Seleziona un modello predefinito o inseriscine uno personalizzato")
        self._wiz_update_model_items(saved_provider)
        saved_model = self._env_val("LLM_MODEL")
        if saved_model:
            self._wiz_llm_model.setCurrentText(saved_model)
        ai_layout.addWidget(self._wiz_llm_model, ar, 1)

        ar += 1
        self._wiz_base_url_label = QLabel("URL endpoint:")
        ai_layout.addWidget(self._wiz_base_url_label, ar, 0)
        self._wiz_llm_base_url = QLineEdit(self._env_val("LLM_BASE_URL"))
        self._wiz_llm_base_url.setPlaceholderText("https://your-server.com")
        ai_layout.addWidget(self._wiz_llm_base_url, ar, 1)

        ar += 1
        self._wiz_test_btn = QPushButton("Testa connessione")
        self._wiz_test_btn.setIcon(qta.icon("fa5s.plug", color="white"))
        self._wiz_test_btn.clicked.connect(self._wiz_test_llm)
        ai_layout.addWidget(self._wiz_test_btn, ar, 0, 1, 2)

        ar += 1
        anon_note = QLabel(
            "Il testo viene ANONIMIZZATO (nome paziente, codice fiscale e tutti "
            "i dati identificativi vengono rimossi) prima dell'invio al servizio AI."
        )
        anon_note.setWordWrap(True)
        anon_note.setStyleSheet(
            "background-color: #fff8e1; border: 1px solid #ffe082; "
            "border-radius: 4px; padding: 8px; color: #5d4037; font-size: 11px;"
        )
        ai_layout.addWidget(anon_note, ar, 0, 1, 2)

        lay.addWidget(self._wiz_ai_group)

        # ── Output directory ──
        dir_group = QGroupBox("Output")
        dir_layout = QGridLayout(dir_group)
        dir_layout.setColumnStretch(1, 1)
        dir_layout.addWidget(QLabel("Salva i testi in:"), 0, 0)
        txt_row = QHBoxLayout()
        self._wiz_text_dir = QLineEdit(self._env_val("TEXT_DIR"))
        self._wiz_text_dir.setPlaceholderText("(opzionale)")
        txt_row.addWidget(self._wiz_text_dir, 1)
        btn_txt = QPushButton("...")
        btn_txt.setFixedWidth(30)
        btn_txt.setObjectName("browseBtn")
        btn_txt.clicked.connect(
            lambda: self._wiz_browse_dir(self._wiz_text_dir, "Seleziona directory testi")
        )
        txt_row.addWidget(btn_txt)
        dir_layout.addLayout(txt_row, 0, 1)
        lay.addWidget(dir_group)

        # ── Visibility logic ──
        def _update():
            is_none = self._wiz_mode_none.isChecked()
            is_ai = self._wiz_mode_ai.isChecked()
            self._wiz_ai_group.setVisible(is_ai)
            dir_group.setEnabled(not is_none)
            # Auto-populate text dir when enabling processing
            if not is_none and not self._wiz_text_dir.text().strip():
                dl = self._wiz_download_dir.text().strip()
                if dl:
                    self._wiz_text_dir.setText(str(Path(dl) / "testi"))
                else:
                    self._wiz_text_dir.setText(str(paths.default_download_dir / "testi"))

        self._wiz_mode_none.toggled.connect(lambda: _update())
        self._wiz_mode_local.toggled.connect(lambda: _update())
        self._wiz_mode_ai.toggled.connect(lambda: _update())
        # Initial provider state
        self._wiz_on_provider_changed(self._wiz_llm_provider.currentText())
        _update()

        lay.addStretch()
        self._stack.addWidget(page)

    def _wiz_on_provider_changed(self, label: str) -> None:
        """Update wizard AI fields when provider changes."""
        provider = self._wiz_label_to_provider.get(label, "")
        # Show/hide base URL
        is_custom = provider == "custom_url"
        self._wiz_base_url_label.setVisible(is_custom)
        self._wiz_llm_base_url.setVisible(is_custom)
        # Reset API key — each provider needs its own key
        self._wiz_llm_api_key.clear()
        needs_key = provider != "claude_cli"
        self._wiz_llm_api_key.setEnabled(needs_key)
        self._wiz_llm_api_key.setReadOnly(False)  # ensure always writable
        if not needs_key:
            self._wiz_llm_api_key.setPlaceholderText("(non necessaria)")
        else:
            self._wiz_llm_api_key.setPlaceholderText("Inserisci la tua API key")
        # Update model suggestions
        self._wiz_update_model_items(provider)

    def _wiz_update_model_items(self, provider: str) -> None:
        """Populate wizard model combo with suggestions for the provider."""
        model_suggestions = {
            "claude_api": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"],
            "openai_api": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
            "gemini_api": ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
            "mistral_api": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
            "claude_cli": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"],
            "custom_url": [],
        }
        self._wiz_llm_model.blockSignals(True)
        self._wiz_llm_model.clear()
        self._wiz_llm_model.clearEditText()
        self._wiz_llm_model.setEditable(True)  # re-assert after clear
        suggestions = model_suggestions.get(provider, [])
        if suggestions:
            self._wiz_llm_model.addItems(suggestions)
            default = self._wiz_default_models.get(provider, "")
            if default in suggestions:
                self._wiz_llm_model.setCurrentText(default)
        self._wiz_llm_model.blockSignals(False)

    def _wiz_test_llm(self) -> None:
        """Test LLM connection from the wizard."""
        provider_label = self._wiz_llm_provider.currentText()
        provider = self._wiz_label_to_provider.get(provider_label, "")
        model = self._wiz_llm_model.currentText().strip()

        if not provider:
            QMessageBox.warning(self, "Test AI", "Seleziona un provider prima di testare.")
            return
        if not model:
            QMessageBox.warning(self, "Test AI", "Inserisci un modello prima di testare.")
            return

        error = _validate_provider_model(provider, model)
        if error:
            QMessageBox.warning(self, "Test AI", error)
            return

        self._wiz_test_btn.setEnabled(False)
        self._wiz_test_btn.setText("Test in corso...")
        threading.Thread(target=self._wiz_test_llm_worker, daemon=True).start()

    def _wiz_test_llm_worker(self) -> None:
        try:
            from text_processing.llm_analyzer import LLMAnalyzer, LLMConfig

            provider_label = self._wiz_llm_provider.currentText()
            provider = self._wiz_label_to_provider.get(provider_label, "")
            api_key_raw = self._wiz_llm_api_key.text()
            api_key = decrypt_password(api_key_raw) if api_key_raw.startswith("ENC:") else api_key_raw

            config = LLMConfig(
                provider=provider,
                api_key=api_key,
                model=self._wiz_llm_model.currentText(),
                timeout=15,
                base_url=self._wiz_llm_base_url.text().strip(),
            )
            analyzer = LLMAnalyzer(config)
            ok = analyzer.is_available()
            if ok:
                self._sig_info.emit("Test AI", f"Connessione a {provider_label} riuscita!")
            else:
                self._sig_error.emit("Test AI", f"Connessione a {provider_label} fallita.\nVerifica API key e connessione internet.")
        except Exception as e:
            self._sig_error.emit("Test AI", f"Errore: {e}")
        finally:
            self._sig_call.emit(
                lambda: (
                    self._wiz_test_btn.setEnabled(True),
                    self._wiz_test_btn.setText("Testa connessione"),
                )
            )

    def _build_step_params(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        group = QGroupBox("Parametri")
        grid = QGridLayout(group)
        grid.setColumnStretch(1, 1)

        r = 0
        grid.addWidget(QLabel("Download timeout (sec):"), r, 0)
        self._wiz_dl_timeout = QLineEdit(self._env_val("DOWNLOAD_TIMEOUT", "120"))
        self._wiz_dl_timeout.setFixedWidth(80)
        self._wiz_dl_timeout.setToolTip(
            "Tempo massimo di attesa, in secondi, per il download di un singolo referto.\n"
            "Se il download non si completa entro questo tempo, viene considerato fallito\n"
            "e l'app passa al referto successivo. Aumenta il valore se hai una connessione lenta."
        )
        grid.addWidget(self._wiz_dl_timeout, r, 1)

        r += 1
        grid.addWidget(QLabel("Page timeout (sec):"), r, 0)
        self._wiz_pg_timeout = QLineEdit(self._env_val("PAGE_TIMEOUT", "60"))
        self._wiz_pg_timeout.setFixedWidth(80)
        self._wiz_pg_timeout.setToolTip(
            "Tempo massimo di attesa, in secondi, per il caricamento di ogni pagina\n"
            "del portale FSE. Se una pagina non risponde entro questo tempo, l'operazione\n"
            "viene interrotta. Aumenta il valore se il portale e' particolarmente lento."
        )
        grid.addWidget(self._wiz_pg_timeout, r, 1)

        r += 1
        grid.addWidget(QLabel("Dimensione carattere console:"), r, 0)
        self._wiz_font_size = QLineEdit(self._env_val("CONSOLE_FONT_SIZE", "8"))
        self._wiz_font_size.setFixedWidth(80)
        self._wiz_font_size.setToolTip(
            "Dimensione del testo nella console di log dell'applicazione (in punti).\n"
            "Valori consigliati: 8-12. Aumenta se hai difficolta' a leggere i messaggi."
        )
        grid.addWidget(self._wiz_font_size, r, 1)

        lay.addWidget(group)

        note = QLabel(
            "<i>Nella maggior parte dei casi i valori predefiniti sono adeguati. "
            "Modifica solo se riscontri problemi di timeout o leggibilita'.</i>"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7b8d; font-size: 11px; margin-top: 8px;")
        lay.addWidget(note)
        lay.addStretch()
        self._stack.addWidget(page)

    def _build_step_summary(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel("Riepilogo delle impostazioni configurate:"))

        self._summary_text = QTextEdit()
        self._summary_text.setReadOnly(True)
        lay.addWidget(self._summary_text)

        note = QLabel(
            "Premi <b>Fine</b> per salvare le impostazioni e chiudere il wizard."
        )
        note.setWordWrap(True)
        lay.addWidget(note)
        self._stack.addWidget(page)

    # ---- Navigation ----

    def _update_nav(self) -> None:
        self._title_label.setText(
            f"Passo {self._current + 1} di {len(self._STEP_TITLES)} — "
            f"{self._STEP_TITLES[self._current]}"
        )
        self._btn_back.setVisible(self._current > 0)
        last = self._current == len(self._STEP_TITLES) - 1
        self._btn_next.setText("Fine" if last else "Avanti")
        self._stack.setCurrentIndex(self._current)

        # Refresh summary when entering the last step
        if last:
            self._refresh_summary()

    def _go_back(self) -> None:
        if self._current > 0:
            self._current -= 1
            self._update_nav()

    def _go_next(self) -> None:
        if self._current == len(self._STEP_TITLES) - 1:
            self._finish()
        else:
            self._current += 1
            self._update_nav()

    def _on_cancel(self) -> None:
        reply = QMessageBox.question(
            self,
            "Annulla configurazione",
            "Vuoi annullare la configurazione?\nLe impostazioni non verranno salvate.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.reject()

    # ---- Collect values ----

    def _collect_values(self) -> dict[str, str]:
        """Collect all wizard field values into a dict ready for saving."""
        vals: dict[str, str] = {}
        vals["EMAIL_USER"] = self._wiz_email.text().strip()

        raw_pass = self._wiz_pass.text()
        if raw_pass and not is_encrypted(raw_pass):
            vals["EMAIL_PASS"] = encrypt_password(raw_pass)
        else:
            vals["EMAIL_PASS"] = raw_pass

        vals["IMAP_HOST"] = self._wiz_imap_host.text().strip() or "mail.fastweb360.it"
        vals["IMAP_PORT"] = self._wiz_imap_port.text().strip() or "993"
        vals["IMAP_FOLDER"] = self._wiz_imap_folder.text().strip() or "INBOX"
        vals["DOWNLOAD_DIR"] = self._wiz_download_dir.text().strip() or str(paths.default_download_dir)
        vals["MOVE_DIR"] = self._wiz_move_dir.text().strip()
        if self._wiz_mode_none.isChecked():
            vals["PROCESS_TEXT"] = "false"
            vals["PROCESSING_MODE"] = "none"
        elif self._wiz_mode_ai.isChecked():
            vals["PROCESS_TEXT"] = "true"
            vals["PROCESSING_MODE"] = "ai"
        else:
            vals["PROCESS_TEXT"] = "true"
            vals["PROCESSING_MODE"] = "local"
        vals["TEXT_DIR"] = self._wiz_text_dir.text().strip()
        provider_label = self._wiz_llm_provider.currentText()
        vals["LLM_PROVIDER"] = self._wiz_label_to_provider.get(provider_label, "")
        raw_key = self._wiz_llm_api_key.text().strip()
        if raw_key and not is_encrypted(raw_key):
            vals["LLM_API_KEY"] = encrypt_password(raw_key)
        else:
            vals["LLM_API_KEY"] = raw_key
        vals["LLM_MODEL"] = self._wiz_llm_model.currentText().strip()
        vals["LLM_BASE_URL"] = self._wiz_llm_base_url.text().strip()

        selected_browser_label = self._wiz_browser_combo.currentText()
        vals["BROWSER_CHANNEL"] = self._wiz_browser_map.get(selected_browser_label, "msedge")

        selected_pdf_label = self._wiz_pdf_combo.currentText()
        vals["PDF_READER"] = self._wiz_pdf_map.get(selected_pdf_label, PDF_READER_DEFAULT)

        vals["USE_EXISTING_BROWSER"] = "true" if self._wiz_cdp_cb.isChecked() else "false"
        vals["OPEN_AFTER_DOWNLOAD"] = "true" if self._wiz_open_after_cb.isChecked() else "false"
        vals["HEADLESS"] = "true" if self._wiz_headless_cb.isChecked() else "false"

        vals["DOWNLOAD_TIMEOUT"] = self._wiz_dl_timeout.text().strip() or "120"
        vals["PAGE_TIMEOUT"] = self._wiz_pg_timeout.text().strip() or "60"
        vals["CONSOLE_FONT_SIZE"] = self._wiz_font_size.text().strip() or "8"

        # Preserve existing values for settings not in the wizard
        for key, _, default, _ in SETTINGS_SPEC:
            if key not in vals:
                vals[key] = self._env.get(key, default)

        return vals

    # ---- Summary ----

    def _refresh_summary(self) -> None:
        vals = self._collect_values()
        lines = []
        display_map = {
            "EMAIL_USER": "Email utente",
            "EMAIL_PASS": "Password",
            "IMAP_HOST": "IMAP Host",
            "IMAP_PORT": "IMAP Porta",
            "IMAP_FOLDER": "Cartelle IMAP",
            "DOWNLOAD_DIR": "Directory download",
            "MOVE_DIR": "Sposta referti in",
            "PROCESS_TEXT": "Processa testo",
            "TEXT_DIR": "Directory testi",
            "PROCESSING_MODE": "Modalita' processazione",
            "LLM_PROVIDER": "Provider AI",
            "LLM_MODEL": "Modello AI",
            "BROWSER_CHANNEL": "Browser",
            "PDF_READER": "Lettore PDF",
            "USE_EXISTING_BROWSER": "Usa browser CDP",
            "OPEN_AFTER_DOWNLOAD": "Apri al termine",
            "HEADLESS": "Headless browser",
            "DOWNLOAD_TIMEOUT": "Download timeout (sec)",
            "PAGE_TIMEOUT": "Page timeout (sec)",
            "CONSOLE_FONT_SIZE": "Dim. carattere console",
        }
        bool_keys = {"PROCESS_TEXT", "USE_EXISTING_BROWSER", "OPEN_AFTER_DOWNLOAD", "HEADLESS"}
        for key, label in display_map.items():
            val = vals.get(key, "")
            if key == "EMAIL_PASS":
                val = "****" if val else "(vuota)"
            elif key == "LLM_API_KEY":
                continue  # Don't show API key in summary
            elif key == "BROWSER_CHANNEL":
                val = self._wiz_browser_combo.currentText()
            elif key == "PDF_READER":
                val = self._wiz_pdf_combo.currentText()
            elif key == "PROCESSING_MODE":
                val = {"ai": "Con AI", "none": "Nessuna", "local": "Solo estrazione (locale)"}.get(val, val)
            elif key == "LLM_PROVIDER":
                val = self._wiz_provider_labels.get(val, val) if val else "(nessuno)"
            elif key in bool_keys:
                val = "Si'" if val.lower() == "true" else "No"
            elif not val:
                val = "(vuoto)"
            lines.append(f"<b>{label}:</b> {val}")
        self._summary_text.setHtml("<br>".join(lines))

    # ---- Finish / Save ----

    def _finish(self) -> None:
        vals = self._collect_values()
        try:
            _save_env_values(vals)
            # Apply CDP registry preference
            self._apply_cdp_registry()
            self.accept()
        except Exception as e:
            QMessageBox.critical(
                self, "Errore", f"Impossibile salvare le impostazioni:\n{e}"
            )

    def _apply_cdp_registry(self) -> None:
        """Apply the CDP registry checkbox state to the Windows registry."""
        parent_app = self.parent()
        if not hasattr(parent_app, "_default_browser_info") or not parent_app._default_browser_info:
            return
        progid = parent_app._default_browser_info["progid"]
        port = int(self._env.get("CDP_PORT", "9222") or "9222")
        try:
            if self._wiz_cdp_registry_cb.isChecked():
                enable_cdp_in_registry(progid, port)
            else:
                disable_cdp_in_registry(progid)
        except Exception:
            pass  # Non-critical, will be handled by main app

    # ---- IMAP test (wizard-local) ----

    def _wiz_test_imap(self) -> None:
        self._wiz_btn_test.setEnabled(False)
        self._wiz_btn_test.setText("Test in corso...")
        threading.Thread(target=self._wiz_test_imap_worker, daemon=True).start()

    def _wiz_test_imap_worker(self) -> None:
        try:
            # Build a temporary env to test with current wizard values
            vals = self._collect_values()
            _save_env_values(vals)
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            client = EmailClient(config, logger)
            client.connect()
            client.disconnect()
            self._sig_info.emit(
                "Test connessione",
                f"Connessione riuscita!\n\n"
                f"Server: {config.imap_host}:{config.imap_port}\n"
                f"Utente: {config.email_user}",
            )
        except Exception as e:
            err_msg = str(e) if str(e) and str(e) != "None" else (
                f"{type(e).__name__}: {e.args}" if e.args else type(e).__name__
            )
            self._sig_error.emit("Test connessione", f"Connessione fallita:\n\n{err_msg}")
        finally:
            self._sig_call.emit(
                lambda: (
                    self._wiz_btn_test.setEnabled(True),
                    self._wiz_btn_test.setText("Test connessione"),
                )
            )

    # ---- IMAP folder browse (wizard-local) ----

    def _wiz_browse_imap_folders(self) -> None:
        self._wiz_btn_browse.setEnabled(False)
        self._wiz_btn_browse.setText("Caricamento...")
        threading.Thread(target=self._wiz_browse_imap_worker, daemon=True).start()

    def _wiz_browse_imap_worker(self) -> None:
        try:
            vals = self._collect_values()
            _save_env_values(vals)
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            client = EmailClient(config, logger)
            client.connect()
            folders = FSEApp._list_imap_folders(client)
            client.disconnect()
            current = self._wiz_imap_folder.text()
            self._sig_call.emit(
                lambda f=folders, c=current: self._wiz_show_folder_picker(f, c)
            )
        except Exception as e:
            err_msg = str(e) if str(e) and str(e) != "None" else (
                f"{type(e).__name__}: {e.args}" if e.args else type(e).__name__
            )
            self._sig_error.emit(
                "Cartelle IMAP",
                f"Impossibile recuperare le cartelle:\n\n{err_msg}",
            )
        finally:
            self._sig_call.emit(
                lambda: (
                    self._wiz_btn_browse.setEnabled(True),
                    self._wiz_btn_browse.setText("Sfoglia..."),
                )
            )

    def _wiz_show_folder_picker(self, folders: list[str], current_text: str) -> None:
        current_set = {f.strip() for f in current_text.split(",") if f.strip()}
        dlg = QDialog(self)
        dlg.setWindowTitle("Seleziona cartelle IMAP")
        dlg.setMinimumWidth(400)
        dlg.setMinimumHeight(350)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Seleziona le cartelle da monitorare:"))

        lst = QListWidget()
        for folder_name in folders:
            item = QListWidgetItem(folder_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if folder_name in current_set else Qt.CheckState.Unchecked
            )
            lst.addItem(item)
        layout.addWidget(lst)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected: list[str] = []
            for i in range(lst.count()):
                item = lst.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    selected.append(item.text())
            if selected:
                self._wiz_imap_folder.setText(", ".join(selected))

    # ---- Directory browse helper ----

    def _wiz_browse_dir(self, line_edit: QLineEdit, title: str) -> None:
        path = QFileDialog.getExistingDirectory(self, title, line_edit.text() or ".")
        if path:
            line_edit.setText(os.path.normpath(path))


# ---- Signal bridge for thread-safe GUI updates ----

class _SignalBridge(QObject):
    """Bridge for thread-safe communication from worker threads to GUI."""
    append_text = Signal(QTextEdit, str)
    inline_text = Signal(QTextEdit, str)
    show_info = Signal(str, str)
    show_error = Signal(str, str)
    show_warning = Signal(str, str)
    call_on_main = Signal(object)  # generic callable

    def __init__(self) -> None:
        super().__init__()
        self.append_text.connect(self._on_append_text)
        self.inline_text.connect(self._on_inline_text)
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
    def _on_inline_text(widget: QTextEdit, msg: str) -> None:
        """Append text to the current last line (no newline)."""
        widget.setReadOnly(False)
        cursor = widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(msg)
        widget.setTextCursor(cursor)
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
        if getattr(record, "inline", False):
            # Inline text: append to current line without newline
            self._bridge.inline_text.emit(self._widget, record.getMessage())
        else:
            msg = self.format(record)
            self._bridge.append_text.emit(self._widget, msg)


def _is_millewin_installed() -> bool:
    """Check if Millewin is installed on the system."""
    exe_exists = Path(r"C:\Program Files (x86)\Millewin\millewin.exe").exists()
    service_exists = False
    try:
        result = subprocess.run(
            ["sc", "query", "pgmille"],
            capture_output=True, text=True, timeout=5,
        )
        service_exists = result.returncode == 0
    except Exception:
        pass
    return exe_exists and service_exists


class DocumentListDialog(QDialog):
    """Modal dialog showing a list of patient documents with checkboxes."""

    def __init__(self, docs: list[PatientDocumentInfo],
                 allowed_types: set[str] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Seleziona referti da scaricare")
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        layout = QVBoxLayout(self)

        # Select all / Deselect all buttons
        btn_row = QHBoxLayout()
        btn_select_all = QPushButton("Seleziona tutti")
        btn_select_all.setIcon(qta.icon("fa5s.check-square", color="white"))
        btn_select_all.clicked.connect(self._select_all)
        btn_row.addWidget(btn_select_all)
        btn_deselect_all = QPushButton("Deseleziona tutti")
        btn_deselect_all.setIcon(qta.icon("fa5s.minus-square", color="white"))
        btn_deselect_all.clicked.connect(self._deselect_all)
        btn_row.addWidget(btn_deselect_all)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # List widget
        self._list = QListWidget()
        self._docs = docs
        for doc in docs:
            text = f"{doc.date_text}  |  {doc.tipo_text}  |  {doc.ente_text}"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # Pre-filter: uncheck documents not matching allowed types
            if allowed_types is not None and not _is_tipologia_valida(doc.tipo_text, allowed_types):
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        layout.addWidget(self._list)

        # OK / Cancel
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _select_all(self) -> None:
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self) -> None:
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def get_selected_row_indices(self) -> set[int]:
        result: set[int] = set()
        for i in range(self._list.count()):
            if self._list.item(i).checkState() == Qt.CheckState.Checked:
                result.add(self._docs[i].row_index)
        return result


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
        self._mw_stop_event = threading.Event()
        self._mw_worker: threading.Thread | None = None
        self._mw_browser: FSEBrowser | None = None
        self._mw_auto_timer: QTimer | None = None
        self._mw_last_cf: str | None = None
        self._btn_mw_start: QPushButton | None = None
        self._btn_mw_stop: QPushButton | None = None
        self._mw_auto_cb: QCheckBox | None = None
        self._selected_row_indices: set[int] | None = None
        self._list_docs_worker: threading.Thread | None = None
        self._fields: dict[str, str | bool] = {}  # key -> current value
        self._btn_analyze: QPushButton | None = None  # created in _build_ui

        # Signal bridge for thread-safe GUI updates
        self._bridge = _SignalBridge()

        self._build_ui()
        self._load_settings()

        # First-run detection: show wizard if no settings file or no email configured
        if not Path(ENV_FILE).exists() or not self._fields.get("EMAIL_USER"):
            QTimer.singleShot(100, self._show_setup_wizard)

        # Auto-check for updates after the window is shown
        QTimer.singleShot(2000, self._check_updates_startup)

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

    def _show_setup_wizard(self) -> None:
        """Launch the setup wizard dialog."""
        wiz = SetupWizard(self, self._browsers, self._pdf_readers)
        if wiz.exec() == QDialog.DialogCode.Accepted:
            self._load_settings()

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

        # Tools menu
        tools_menu = menu_bar.addMenu("Strumenti")

        act_interpret = QAction("Interpreta referto", self)
        act_interpret.triggered.connect(self._open_interpreter)
        tools_menu.addAction(act_interpret)

        act_compare = QAction("Visiona testi", self)
        act_compare.triggered.connect(self._open_text_comparison)
        tools_menu.addAction(act_compare)

        # Help menu
        help_menu = menu_bar.addMenu("Aiuto")

        act_guide = QAction("Guida", self)
        act_guide.triggered.connect(self._open_guide)
        help_menu.addAction(act_guide)

        act_wizard = QAction("Configurazione guidata", self)
        act_wizard.triggered.connect(self._show_setup_wizard)
        help_menu.addAction(act_wizard)

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

        # Tab 2: Referti singolo paziente
        patient_tab = QWidget()
        self._notebook.addTab(patient_tab, "Referti singolo paziente")

        # Tab 3: Impostazioni (built first so fields exist for SISS tab)
        settings_tab = QWidget()
        self._notebook.addTab(settings_tab, "Impostazioni")
        self._build_settings_tab(settings_tab)

        # Build SISS tab after settings so fields exist
        self._build_siss_tab(siss_tab)
        self._build_patient_tab(patient_tab)

        # Bottom bar: Reset console (left) — Analizza referti — Apri cartella download (right)
        bottom_row = QHBoxLayout()
        self._btn_reset_console = QPushButton("Reset console")
        self._btn_reset_console.setIcon(qta.icon("fa5s.redo", color="white"))
        self._btn_reset_console.clicked.connect(self._reset_active_console)
        bottom_row.addWidget(self._btn_reset_console)
        bottom_row.addStretch()
        self._btn_analyze = QPushButton("Analizza referti")
        self._btn_analyze.setIcon(qta.icon("fa5s.microscope", color="white"))
        self._btn_analyze.clicked.connect(self._start_analysis)
        self._btn_analyze.setVisible(False)  # hidden until settings loaded
        bottom_row.addWidget(self._btn_analyze)
        btn_open_dl = QPushButton("Apri cartella download")
        btn_open_dl.setIcon(qta.icon("fa5s.folder-open", color="white"))
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
        self._btn_check.setIcon(qta.icon("fa5s.envelope", color="white"))
        self._btn_check.clicked.connect(self._check_email)
        self._btn_check.setToolTip("Conta le email non lette con referti da scaricare, senza avviare il download")
        btn_row.addWidget(self._btn_check)

        self._btn_start = QPushButton("Avvia download")
        self._btn_start.setIcon(qta.icon("fa5s.download", color="white"))
        self._btn_start.clicked.connect(self._start_processing)
        btn_row.addWidget(self._btn_start)

        self._btn_stop = QPushButton("Interrompi")
        self._btn_stop.setIcon(qta.icon("fa5s.stop-circle", color="white"))
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

    def _get_patient_selected_types(self) -> set[str] | None:
        """Return the set of selected patient document type keys.

        Returns None if radio 'Tutti' is checked.
        Reads checked items from the referto QListWidget via UserRole data.
        """
        if self._patient_radio_tutti.isChecked():
            return None
        result: set[str] = set()
        # Main checkboxes (Lettera Dimissione, Verbale PS)
        for key, cb in self._patient_doc_cbs.items():
            if cb.isChecked():
                result.add(key)
        # Referto subtypes from the list widget
        if self._patient_referti_cb.isChecked():
            lst = self._patient_referto_list
            for i in range(lst.count()):
                item = lst.item(i)
                if item.checkState() == Qt.Checked:
                    filter_key = item.data(Qt.UserRole)
                    result.add(filter_key)
                    if filter_key == "REFERTO":
                        break  # wildcard — matches all referto subtypes
        return result if result else set()

    def _build_patient_tab(self, parent: QWidget) -> None:
        """Build the Referti singolo paziente tab with two-column top layout."""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)

        # Top frame: two columns
        top_layout = QHBoxLayout()

        # ── Left column: Paziente + Origine e data ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Paziente group
        paziente_group = QGroupBox("Paziente")
        paz_layout = QGridLayout(paziente_group)
        paz_layout.addWidget(QLabel("CF:"), 0, 0)
        self._cf_entry = QLineEdit()
        self._cf_entry.setFont(QFont("Consolas", 11))
        paz_layout.addWidget(self._cf_entry, 0, 1)
        paz_layout.setColumnStretch(1, 1)
        mw_installed = _is_millewin_installed()
        if mw_installed:
            mw_btn = QPushButton("MW")
            mw_btn.setToolTip("Copia codice fiscale del paziente attivo in Millewin")
            mw_btn.setStyleSheet("padding: 2px 4px;")
            mw_btn.setFixedWidth(38)
            mw_btn.clicked.connect(self._copy_cf_from_millewin)
            paz_layout.addWidget(mw_btn, 0, 2)
        if mw_installed:
            mw_row = QHBoxLayout()
            self._btn_mw_start = QPushButton("FSE del paziente")
            self._btn_mw_start.setIcon(qta.icon("fa5s.file-medical", color="white"))
            self._btn_mw_start.setToolTip("Apri la scheda di un paziente in Millewin, poi premi il pulsante")
            self._btn_mw_start.clicked.connect(lambda: self._start_mw_workflow())
            mw_row.addWidget(self._btn_mw_start)
            self._btn_mw_stop = QPushButton("Stop")
            self._btn_mw_stop.setIcon(qta.icon("fa5s.stop-circle", color="white"))
            self._btn_mw_stop.clicked.connect(self._stop_mw_workflow)
            self._btn_mw_stop.setEnabled(False)
            mw_row.addWidget(self._btn_mw_stop)
            self._mw_auto_cb = QCheckBox("Sincro automatico")
            self._mw_auto_cb.setToolTip("Monitora Millewin e naviga automaticamente al FSE quando cambi paziente")
            self._mw_auto_cb.toggled.connect(self._on_mw_auto_toggled)
            mw_row.addWidget(self._mw_auto_cb)
            mw_row.addStretch()
            paz_layout.addLayout(mw_row, 1, 0, 1, 3)
        left_layout.addWidget(paziente_group)

        # Origine e data group
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

        left_layout.addWidget(filter_group)
        left_layout.addStretch()
        top_layout.addWidget(left_widget, 1)

        # ── Right column: Tipologia documenti ──
        (self._patient_radio_tutti,
         self._patient_radio_seleziona,
         self._patient_referto_list,
         self._patient_doc_cbs) = self._build_patient_doc_type_checkboxes()
        top_layout.addWidget(self._patient_doc_group, 1)

        layout.addLayout(top_layout)

        # Button row (full width)
        ctrl_layout = QHBoxLayout()
        ctrl_layout.addStretch()
        self._btn_load_enti = QPushButton("Elenca strutture")
        self._btn_load_enti.setIcon(qta.icon("fa5s.hospital", color="white"))
        self._btn_load_enti.setToolTip("Apre il browser e carica le strutture disponibili per il paziente")
        self._btn_load_enti.clicked.connect(self._start_ente_scan)
        ctrl_layout.addWidget(self._btn_load_enti)

        self._btn_list_docs = QPushButton("Elenca Referti Selezionati")
        self._btn_list_docs.setIcon(qta.icon("fa5s.list-alt", color="white"))
        self._btn_list_docs.setToolTip("Apre il browser e mostra la lista dei referti per selezionarli manualmente")
        self._btn_list_docs.clicked.connect(self._start_list_documents)
        ctrl_layout.addWidget(self._btn_list_docs)

        self._btn_patient_start = QPushButton("Avvia download")
        self._btn_patient_start.setIcon(qta.icon("fa5s.download", color="white"))
        self._btn_patient_start.clicked.connect(self._start_patient_download)
        ctrl_layout.addWidget(self._btn_patient_start)

        self._btn_patient_stop = QPushButton("Interrompi")
        self._btn_patient_stop.setIcon(qta.icon("fa5s.stop-circle", color="white"))
        self._btn_patient_stop.clicked.connect(self._stop_patient_download)
        self._btn_patient_stop.setEnabled(False)
        ctrl_layout.addWidget(self._btn_patient_stop)
        ctrl_layout.addStretch()
        layout.addLayout(ctrl_layout)

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

    def _build_patient_doc_type_checkboxes(self) -> tuple[QRadioButton, QRadioButton, QListWidget, dict[str, QCheckBox]]:
        """Create Patient document type checkboxes with radio Tutti/Seleziona.

        Returns (radio_tutti, radio_seleziona, referto_list, doc_cbs).
        """
        self._patient_doc_group = QGroupBox("Tipologia documenti")
        group_layout = QVBoxLayout(self._patient_doc_group)

        doc_cbs: dict[str, QCheckBox] = {}

        # ── Radio buttons row ──
        radio_tutti = QRadioButton("Tutti")
        radio_seleziona = QRadioButton("Seleziona")
        radio_tutti.setChecked(True)

        radio_row = QHBoxLayout()
        radio_row.addWidget(radio_tutti)
        radio_row.addWidget(radio_seleziona)
        radio_row.addStretch()
        group_layout.addLayout(radio_row)

        # ── Selection container (hidden when radio "Tutti") ──
        selection_container = QWidget()
        sel_layout = QVBoxLayout(selection_container)
        sel_layout.setContentsMargins(0, 0, 0, 0)

        # Main doc type checkboxes (vertical, with stretch between)
        for type_key, label in PATIENT_DOC_TYPES_MAIN:
            sel_layout.addStretch(1)
            cb = QCheckBox(label)
            cb.setChecked(True)
            sel_layout.addWidget(cb)
            doc_cbs[type_key] = cb

        # Referti specialistici row (checkbox + list)
        sel_layout.addStretch(1)
        referto_row = QHBoxLayout()
        self._patient_referti_cb = QCheckBox("Referti specialistici")
        self._patient_referti_cb.setChecked(True)
        referto_row.addWidget(self._patient_referti_cb)

        referto_list = QListWidget()
        referto_list.setMaximumHeight(110)
        referto_list.setMaximumWidth(220)
        for display_label, filter_key in REFERTO_SUBTYPE_ITEMS:
            item = QListWidgetItem(display_label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if display_label == "Tutti" else Qt.Unchecked)
            item.setData(Qt.UserRole, filter_key)
            referto_list.addItem(item)

        referto_row.addWidget(referto_list)
        referto_row.addStretch()
        sel_layout.addLayout(referto_row)
        sel_layout.addStretch(1)

        group_layout.addWidget(selection_container)

        # ── Initial state: radio "Tutti" checked → disable selection area ──
        selection_container.setEnabled(False)
        referto_list.setEnabled(False)

        # ── Interactions ──

        def on_radio_toggled(tutti_checked):
            selection_container.setEnabled(not tutti_checked)
            if not tutti_checked:
                # Re-apply referti checkbox state after container enable
                referto_list.setEnabled(self._patient_referti_cb.isChecked())

        def on_referti_toggled(checked):
            referto_list.setEnabled(checked)

        def on_list_item_changed(item):
            """Mutual exclusion: 'Tutti' vs individual items."""
            referto_list.blockSignals(True)
            tutti_item = referto_list.item(0)
            if item is tutti_item:
                # "Tutti" toggled — clear or set all others
                new_state = tutti_item.checkState()
                for i in range(1, referto_list.count()):
                    referto_list.item(i).setCheckState(
                        Qt.Unchecked if new_state == Qt.Checked else Qt.Unchecked
                    )
            else:
                # An individual item toggled — uncheck "Tutti"
                if item.checkState() == Qt.Checked:
                    tutti_item.setCheckState(Qt.Unchecked)
                # If no individual is checked, re-check "Tutti"
                any_checked = any(
                    referto_list.item(i).checkState() == Qt.Checked
                    for i in range(1, referto_list.count())
                )
                if not any_checked:
                    tutti_item.setCheckState(Qt.Checked)
            referto_list.blockSignals(False)

        radio_tutti.toggled.connect(on_radio_toggled)
        self._patient_referti_cb.toggled.connect(on_referti_toggled)
        referto_list.itemChanged.connect(on_list_item_changed)

        return radio_tutti, radio_seleziona, referto_list, doc_cbs

    def _copy_cf_from_millewin(self) -> None:
        """Read CF from Millewin window title and paste it into the patient CF field."""
        cf = self._read_millewin_cf()
        if cf:
            self._cf_entry.setText(cf)
        else:
            QMessageBox.information(
                self, "Millewin",
                "Nessun paziente aperto in Millewin (finestra non trovata o CF assente nel titolo)."
            )

    def _on_mw_auto_toggled(self, checked: bool) -> None:
        """Toggle automatic polling of Millewin window."""
        if checked:
            self._mw_last_cf = None
            self._mw_auto_timer = QTimer(self)
            self._mw_auto_timer.timeout.connect(self._mw_auto_poll)
            self._mw_auto_timer.start(2000)
            self._btn_mw_start.setEnabled(False)
            self._mw_log("Auto-polling attivato (ogni 2s)")
        else:
            if self._mw_auto_timer is not None:
                self._mw_auto_timer.stop()
                self._mw_auto_timer = None
            # Re-enable manual button only if no worker is running
            if not (self._mw_worker and self._mw_worker.is_alive()):
                self._btn_mw_start.setEnabled(True)
            self._mw_log("Auto-polling disattivato")

    def _mw_auto_poll(self) -> None:
        """Periodically check Millewin window for patient changes."""
        # Skip if a workflow is already running
        if self._mw_worker and self._mw_worker.is_alive():
            return
        cf = self._read_millewin_cf()
        if cf is None:
            return
        if cf == self._mw_last_cf:
            return
        self._mw_last_cf = cf
        self._bridge.call_on_main.emit(lambda c=cf: self._cf_entry.setText(c))
        self._mw_log(f"Cambio paziente rilevato: {cf}")
        self._start_mw_workflow(cf_override=cf)

    def _start_mw_workflow(self, cf_override: str | None = None) -> None:
        """Start the Millewin → FSE workflow."""
        if self._mw_worker and self._mw_worker.is_alive():
            QMessageBox.warning(self, "Attenzione", "Workflow Millewin gia' in corso")
            return

        self._save_settings_quietly()
        self._mw_stop_event.clear()
        self._btn_mw_start.setEnabled(False)
        self._btn_mw_stop.setEnabled(True)
        if cf_override is None:
            self._cf_entry.clear()
        self._mw_log("--- Avvio workflow Millewin → FSE ---")

        self._mw_worker = threading.Thread(
            target=self._mw_workflow_worker, args=(cf_override,), daemon=True,
        )
        self._mw_worker.start()
        self._mw_poll_timer = QTimer(self)
        self._mw_poll_timer.timeout.connect(self._poll_mw_worker)
        self._mw_poll_timer.start(500)

    def _mw_workflow_worker(self, cf_override: str | None = None) -> None:
        """Worker thread for the Millewin → FSE workflow."""
        try:
            # Step 1: Read CF from Millewin (or use override from auto-poll)
            if cf_override is not None:
                cf = cf_override
            else:
                cf = self._read_millewin_cf()
                if cf is None:
                    self._mw_log("Nessun paziente aperto in Millewin (finestra non trovata o CF assente nel titolo)")
                    return

            self._mw_log(f"CF estratto: {cf}")
            self._bridge.call_on_main.emit(lambda c=cf: self._cf_entry.setText(c))

            # Step 2: Create/reuse FSEBrowser
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            handler = TextHandler(self._patient_console, self._bridge)
            handler.setLevel(logging.DEBUG if config.debug_logging else logging.INFO)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger._logger.addHandler(handler)

            reused = False
            if self._mw_browser is not None:
                try:
                    if self._mw_browser._is_alive():
                        reused = True
                        logger.info("Riutilizzo browser esistente")
                except Exception:
                    pass
                if not reused:
                    try:
                        self._mw_browser.stop()
                    except Exception:
                        pass
                    self._mw_browser = None

            if not reused:
                browser = FSEBrowser(config, logger)
                self._start_browser_safe(browser)
                self._mw_browser = browser

            # Step 3: Navigate to FSE documents page
            def _on_page_expired():
                self._bridge.show_warning.emit(
                    "Pagina di ricerca scaduta",
                    "La pagina di ricerca del SISS e' scaduta.\n"
                    "L'overlay 'Identificazione del cittadino in corso' non "
                    "e' comparso dopo aver cliccato 'Cerca'.\n\n"
                    "Aggiornamento automatico in corso...",
                )

            self._mw_browser.navigate_for_millewin(
                cf, self._mw_stop_event, on_page_expired=_on_page_expired,
            )

            self._mw_log("Navigazione completata, pagina documenti pronta")
        except InterruptedError:
            self._mw_log("Workflow interrotto dall'utente")
        except Exception as e:
            self._mw_log(f"Errore: {e}")

    def _start_browser_safe(self, browser: FSEBrowser) -> None:
        """Start browser with automatic BrowserCDPNotActive handling.

        If the browser is running without CDP, asks the user for consent
        to restart it. Raises the original exception if user declines.
        """
        try:
            browser.start()
        except BrowserCDPNotActive as e:
            if self._ask_restart_browser(e):
                browser.restart_browser_with_cdp(
                    e.process_name, e.exe_path, e.port
                )
            else:
                raise InterruptedError("Connessione annullata dall'utente")

    def _ask_restart_browser(self, exc: BrowserCDPNotActive) -> bool:
        """Ask the user (on main thread) whether to restart the browser with CDP.

        Returns True if user accepts, False otherwise. Thread-safe.
        """
        result = [False]
        event = threading.Event()

        def _show_dialog():
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "Riavvio browser necessario",
                "Il browser e' in esecuzione ma senza CDP attivo.\n"
                "Per connettersi al FSE, il browser deve essere riavviato "
                "con il supporto CDP.\n\n"
                "ATTENZIONE: le tab aperte nel browser verranno chiuse.\n\n"
                "Vuoi riavviare il browser ora?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            result[0] = (reply == QMessageBox.StandardButton.Yes)
            event.set()

        self._bridge.call_on_main.emit(_show_dialog)
        event.wait()
        return result[0]

    def _poll_mw_worker(self) -> None:
        """Poll the Millewin workflow worker thread."""
        if self._mw_worker and self._mw_worker.is_alive():
            return
        self._mw_poll_timer.stop()
        # If auto-polling is active, keep manual button disabled
        if self._mw_auto_cb is not None and not self._mw_auto_cb.isChecked():
            if self._btn_mw_start is not None:
                self._btn_mw_start.setEnabled(True)
        if self._btn_mw_stop is not None:
            self._btn_mw_stop.setEnabled(False)
        self._mw_log("--- Workflow terminato ---")

    def _stop_mw_workflow(self) -> None:
        """Request stop of the Millewin workflow."""
        self._mw_stop_event.set()
        self._mw_log("Richiesta interruzione inviata...")
        self._btn_mw_stop.setEnabled(False)

    def _mw_log(self, msg: str) -> None:
        """Append a message to the patient console (main-thread safe)."""
        self._bridge.append_text.emit(self._patient_console, msg)

    def _read_millewin_cf(self) -> str | None:
        """Read the codice fiscale from the Millewin window title."""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi

        CF_PATTERN = re.compile(r'[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]')

        result = None

        def enum_callback(hwnd, _):
            nonlocal result
            if not user32.IsWindowVisible(hwnd):
                return True
            # Check class name starts with "FNWND"
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if not class_name.value.startswith("FNWND"):
                return True
            # Check process is millewin.exe
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h_process = kernel32.OpenProcess(0x0410, False, pid.value)  # QUERY_INFO | VM_READ
            if h_process:
                exe_name = ctypes.create_unicode_buffer(260)
                psapi.GetModuleFileNameExW(h_process, None, exe_name, 260)
                kernel32.CloseHandle(h_process)
                if "millewin.exe" not in exe_name.value.lower():
                    return True
            # Read window title
            length = user32.GetWindowTextLengthW(hwnd) + 1
            title = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, title, length)
            match = CF_PATTERN.search(title.value.upper())
            if match:
                result = match.group(0)
                return False  # Stop enumeration
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return result

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
        """Build the Settings tab with nested sub-tabs: Parametri + Processazione Testi."""
        spec = {key: (label, default, kind) for key, label, default, kind in SETTINGS_SPEC}

        layout = QVBoxLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)

        # Nested sub-tabs within Impostazioni
        self._settings_tabs = QTabWidget()

        params_page = QWidget()
        self._build_settings_params(params_page, spec)
        self._settings_tabs.addTab(params_page, "Parametri")

        text_page = QWidget()
        self._build_settings_text_processing(text_page, spec)
        self._settings_tabs.addTab(text_page, "Analisi referti")

        layout.addWidget(self._settings_tabs)

        # Shared save button
        save_btn = QPushButton("Salva Impostazioni")
        save_btn.setIcon(qta.icon("fa5s.save", color="white"))
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn)

    def _build_settings_params(self, parent: QWidget, spec: dict) -> None:
        """Build the Parametri sub-tab: mail server, browser, general parameters."""
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
            "IMAP_FOLDER": "Cartelle IMAP da monitorare, separate da virgola\n(es. INBOX, INBOX/ASST/Referti)",
        }
        self._settings_entries: dict[str, QLineEdit] = {}
        for r, key in enumerate(["EMAIL_USER", "EMAIL_PASS", "IMAP_HOST", "IMAP_PORT", "IMAP_FOLDER"]):
            label_text, default, kind = spec[key]
            mail_layout.addWidget(QLabel(label_text), r, 0)
            entry = QLineEdit(default)
            if kind == "password":
                entry.setEchoMode(QLineEdit.EchoMode.Password)
                entry.setReadOnly(True)
                entry.setToolTip("Usa il pulsante 'Cambia...' per modificare la password")
                pass_row = QHBoxLayout()
                pass_row.addWidget(entry)
                btn_change_pass = QPushButton("Cambia...")
                btn_change_pass.setIcon(qta.icon("fa5s.key", color="white"))
                btn_change_pass.setToolTip("Cambia la password email")
                btn_change_pass.clicked.connect(self._show_change_password_dialog)
                pass_row.addWidget(btn_change_pass)
                mail_layout.addLayout(pass_row, r, 1)
            elif key == "IMAP_FOLDER":
                entry.setToolTip(mail_tooltips.get(key, ""))
                folder_row = QHBoxLayout()
                folder_row.addWidget(entry)
                self._btn_browse_folders = QPushButton("Sfoglia...")
                self._btn_browse_folders.setIcon(qta.icon("fa5s.folder-open", color="white"))
                self._btn_browse_folders.setToolTip("Seleziona cartelle IMAP dal server")
                self._btn_browse_folders.clicked.connect(self._browse_imap_folders)
                folder_row.addWidget(self._btn_browse_folders)
                mail_layout.addLayout(folder_row, r, 1)
            else:
                entry.setToolTip(mail_tooltips.get(key, ""))
                mail_layout.addWidget(entry, r, 1)
            self._settings_entries[key] = entry
            self._fields[key] = default

        mail_btn_row = QHBoxLayout()
        self._btn_test_imap = QPushButton("Test connessione")
        self._btn_test_imap.setIcon(qta.icon("fa5s.plug", color="white"))
        self._btn_test_imap.clicked.connect(self._test_imap_connection)
        self._btn_test_imap.setToolTip("Verifica connessione e login al server di posta")
        mail_btn_row.addWidget(self._btn_test_imap)
        btn_reset_imap = QPushButton("Ripristina default")
        btn_reset_imap.setIcon(qta.icon("fa5s.undo", color="white"))
        btn_reset_imap.setToolTip("Ripristina host e porta IMAP ai valori predefiniti")
        btn_reset_imap.clicked.connect(self._reset_imap_defaults)
        mail_btn_row.addWidget(btn_reset_imap)
        mail_layout.addLayout(mail_btn_row, len(["EMAIL_USER", "EMAIL_PASS", "IMAP_HOST", "IMAP_PORT", "IMAP_FOLDER"]), 0, 1, 2)

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
        if (
            self._cdp_registry_cb.isEnabled()
            and not self._cdp_registry_cb.isChecked()
        ):
            self._cdp_registry_cb.setChecked(True)

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

        # ── Parametri ──
        params_group = QGroupBox("Parametri")
        params_layout = QVBoxLayout(params_group)
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

        params_row1 = QHBoxLayout()
        self._debug_cb = QCheckBox("Abilita info debug")
        self._debug_cb.setChecked(spec["DEBUG_LOGGING"][1].lower() == "true")
        self._debug_cb.setToolTip(
            "Mostra messaggi di debug dettagliati nella console (utile per diagnostica)"
        )
        params_row1.addWidget(self._debug_cb)
        params_row1.addStretch()
        params_layout.addLayout(params_row1)
        self._fields["DEBUG_LOGGING"] = spec["DEBUG_LOGGING"][1]

        self._fields["MAX_EMAILS"] = spec["MAX_EMAILS"][1]
        self._fields["MARK_AS_READ"] = spec["MARK_AS_READ"][1]
        self._fields["DELETE_AFTER_PROCESSING"] = spec["DELETE_AFTER_PROCESSING"][1]

        layout.addWidget(params_group)
        layout.addStretch()

    def _build_settings_text_processing(self, parent: QWidget, spec: dict) -> None:
        """Build the Processazione Testi sub-tab: folders, processing mode, AI settings."""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Cartelle ──
        cartelle_group = QGroupBox("Cartelle")
        cg = QGridLayout(cartelle_group)
        cg.setColumnStretch(1, 1)

        cr = 0
        cg.addWidget(QLabel("salva in:"), cr, 0)
        dl_dir_row = QHBoxLayout()
        self._download_dir_entry = QLineEdit(spec["DOWNLOAD_DIR"][1])
        self._download_dir_entry.setToolTip(spec["DOWNLOAD_DIR"][1])
        self._download_dir_entry.textChanged.connect(
            lambda text: self._download_dir_entry.setToolTip(text)
        )
        dl_dir_row.addWidget(self._download_dir_entry, 1)
        browse_dl_btn = QPushButton("...")
        browse_dl_btn.setFixedWidth(30)
        browse_dl_btn.setObjectName("browseBtn")
        browse_dl_btn.clicked.connect(self._browse_download_dir)
        dl_dir_row.addWidget(browse_dl_btn)
        cg.addLayout(dl_dir_row, cr, 1, 1, 2)
        self._fields["DOWNLOAD_DIR"] = spec["DOWNLOAD_DIR"][1]

        cr += 1
        after_label = QLabel("dopo il download:")
        after_label.setStyleSheet("font-weight: bold;")
        cg.addWidget(after_label, cr, 0, 1, 3)

        cr += 1
        cg.addWidget(QLabel("sposta in:"), cr, 0)
        mv_dir_row = QHBoxLayout()
        self._move_dir_entry = QLineEdit(spec["MOVE_DIR"][1])
        self._move_dir_entry.setToolTip("Dopo il download, sposta i referti in questa cartella (lascia vuoto per non spostare)")
        self._move_dir_entry.textChanged.connect(
            lambda text: self._move_dir_entry.setToolTip(text or "Dopo il download, sposta i referti in questa cartella (lascia vuoto per non spostare)")
        )
        mv_dir_row.addWidget(self._move_dir_entry, 1)
        browse_mv_btn = QPushButton("...")
        browse_mv_btn.setFixedWidth(30)
        browse_mv_btn.setObjectName("browseBtn")
        browse_mv_btn.clicked.connect(self._browse_move_dir)
        mv_dir_row.addWidget(browse_mv_btn)
        cg.addLayout(mv_dir_row, cr, 1, 1, 2)
        self._fields["MOVE_DIR"] = spec["MOVE_DIR"][1]

        layout.addWidget(cartelle_group)

        # ── Processazione del testo ──
        processing_group = QGroupBox("Analisi referti")
        pg = QVBoxLayout(processing_group)

        # Mode radio buttons (in own QWidget for auto-exclusive grouping)
        mode_widget = QWidget()
        mode_row = QHBoxLayout(mode_widget)
        mode_row.setContentsMargins(0, 0, 0, 0)
        self._mode_radio_none = QRadioButton("Nessuna")
        self._mode_radio_local = QRadioButton("Solo estrazione")
        self._mode_radio_ai = QRadioButton("Estrazione e analisi con A.I.")
        current_mode = spec.get("PROCESSING_MODE", ("", "local"))[1]
        if current_mode == "ai":
            self._mode_radio_ai.setChecked(True)
        elif current_mode == "none":
            self._mode_radio_none.setChecked(True)
        else:
            self._mode_radio_local.setChecked(True)
        self._fields["PROCESSING_MODE"] = current_mode
        self._fields["PROCESS_TEXT"] = "false" if current_mode == "none" else "true"
        mode_row.addWidget(self._mode_radio_none)
        mode_row.addWidget(self._mode_radio_local)
        mode_row.addWidget(self._mode_radio_ai)
        mode_row.addStretch()
        pg.addWidget(mode_widget)

        # Scope radio buttons (in own QWidget for auto-exclusive grouping)
        scope_widget = QWidget()
        scope_row = QHBoxLayout(scope_widget)
        scope_row.setContentsMargins(0, 0, 0, 0)
        self._scope_radio_all = QRadioButton("Tutti i referti scaricati")
        self._scope_radio_choose = QRadioButton("Scegli referti")
        self._scope_radio_all.setChecked(True)
        scope_row.addWidget(self._scope_radio_all)
        scope_row.addWidget(self._scope_radio_choose)
        scope_row.addStretch()
        pg.addWidget(scope_widget)

        # ── Impostazioni A.I. sub-group ──
        self._ai_group = QGroupBox("Impostazioni A.I.")
        ai_layout = QGridLayout(self._ai_group)
        ai_layout.setColumnStretch(1, 1)

        from text_processing.llm_analyzer import PROVIDER_LABELS, LABEL_TO_PROVIDER, DEFAULT_MODELS
        self._llm_provider_labels = PROVIDER_LABELS
        self._llm_label_to_provider = LABEL_TO_PROVIDER
        self._llm_default_models = DEFAULT_MODELS

        ar = 0
        ai_layout.addWidget(QLabel("Provider:"), ar, 0)
        self._llm_provider_combo = QComboBox()
        self._llm_provider_combo.addItems(list(PROVIDER_LABELS.values()))
        saved_provider = spec.get("LLM_PROVIDER", ("", ""))[1]
        if saved_provider in PROVIDER_LABELS:
            self._llm_provider_combo.setCurrentText(PROVIDER_LABELS[saved_provider])
        self._llm_provider_combo.currentTextChanged.connect(self._on_llm_provider_changed)
        ai_layout.addWidget(self._llm_provider_combo, ar, 1)
        self._fields["LLM_PROVIDER"] = saved_provider

        ar += 1
        ai_layout.addWidget(QLabel("API Key:"), ar, 0)
        api_key_row = QHBoxLayout()
        self._llm_api_key_entry = QLineEdit(spec.get("LLM_API_KEY", ("", ""))[1])
        self._llm_api_key_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self._llm_api_key_entry.setPlaceholderText("Inserisci la tua API key")
        api_key_row.addWidget(self._llm_api_key_entry, 1)
        self._llm_show_key_btn = QPushButton("Mostra")
        self._llm_show_key_btn.setIcon(qta.icon("fa5s.eye", color="white"))
        self._llm_show_key_btn.setFixedWidth(80)
        self._llm_show_key_btn.setCheckable(True)
        self._llm_show_key_btn.toggled.connect(
            lambda checked: self._llm_api_key_entry.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        api_key_row.addWidget(self._llm_show_key_btn)
        ai_layout.addLayout(api_key_row, ar, 1)
        self._fields["LLM_API_KEY"] = spec.get("LLM_API_KEY", ("", ""))[1]

        ar += 1
        ai_layout.addWidget(QLabel("Modello:"), ar, 0)
        self._llm_model_combo = QComboBox()
        self._llm_model_combo.setEditable(True)
        self._llm_model_combo.setToolTip("Seleziona un modello predefinito o inserisci un nome personalizzato")
        self._update_model_combo_items(saved_provider)
        saved_model = spec.get("LLM_MODEL", ("", ""))[1]
        if saved_model:
            self._llm_model_combo.setCurrentText(saved_model)
        ai_layout.addWidget(self._llm_model_combo, ar, 1)
        self._fields["LLM_MODEL"] = saved_model

        ar += 1
        self._llm_base_url_label = QLabel("URL endpoint:")
        ai_layout.addWidget(self._llm_base_url_label, ar, 0)
        self._llm_base_url_entry = QLineEdit(spec.get("LLM_BASE_URL", ("", ""))[1])
        self._llm_base_url_entry.setPlaceholderText("https://your-server.com")
        self._llm_base_url_entry.setToolTip("URL base per endpoint OpenAI-compatibile (solo per 'Endpoint personalizzato')")
        ai_layout.addWidget(self._llm_base_url_entry, ar, 1)
        self._fields["LLM_BASE_URL"] = spec.get("LLM_BASE_URL", ("", ""))[1]

        ar += 1
        timeout_row = QHBoxLayout()
        timeout_row.addWidget(QLabel("Timeout (sec):"))
        self._llm_timeout_entry = QLineEdit(spec.get("LLM_TIMEOUT", ("", "120"))[1])
        self._llm_timeout_entry.setFixedWidth(60)
        timeout_row.addWidget(self._llm_timeout_entry)
        self._fields["LLM_TIMEOUT"] = spec.get("LLM_TIMEOUT", ("", "120"))[1]
        self._llm_test_btn = QPushButton("Testa connessione")
        self._llm_test_btn.setIcon(qta.icon("fa5s.plug", color="white"))
        self._llm_test_btn.clicked.connect(self._test_llm_connection)
        timeout_row.addWidget(self._llm_test_btn)
        timeout_row.addStretch()
        ai_layout.addLayout(timeout_row, ar, 0, 1, 2)

        pg.addWidget(self._ai_group)

        # Wire visibility: mode radios control scope, AI group, and analyze button
        self._mode_radio_none.toggled.connect(lambda: self._on_mode_changed())
        self._mode_radio_local.toggled.connect(lambda: self._on_mode_changed())
        self._mode_radio_ai.toggled.connect(lambda: self._on_mode_changed())
        self._on_llm_provider_changed(self._llm_provider_combo.currentText())
        self._on_mode_changed()

        # Save extracted texts directory
        txt_group = QGroupBox("Salvataggio testi estratti")
        tg = QGridLayout(txt_group)
        tg.setColumnStretch(1, 1)
        tg.addWidget(QLabel("salva testi in:"), 0, 0)
        txt_dir_row = QHBoxLayout()
        self._text_dir_entry = QLineEdit(spec["TEXT_DIR"][1])
        self._text_dir_entry.setToolTip("Cartella dove salvare i testi estratti dai referti (lascia vuoto per non salvare)")
        self._text_dir_entry.textChanged.connect(
            lambda text: self._text_dir_entry.setToolTip(text or "Cartella dove salvare i testi estratti dai referti (lascia vuoto per non salvare)")
        )
        txt_dir_row.addWidget(self._text_dir_entry, 1)
        browse_txt_btn = QPushButton("...")
        browse_txt_btn.setFixedWidth(30)
        browse_txt_btn.setObjectName("browseBtn")
        browse_txt_btn.clicked.connect(self._browse_text_dir)
        txt_dir_row.addWidget(browse_txt_btn)
        tg.addLayout(txt_dir_row, 0, 1, 1, 2)
        self._fields["TEXT_DIR"] = spec["TEXT_DIR"][1]

        layout.addWidget(processing_group)
        layout.addWidget(txt_group)
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
        browse_btn.setIcon(qta.icon("fa5s.folder-open", color="white"))
        browse_btn.clicked.connect(on_browse)
        btn_layout.addWidget(browse_btn)
        btn_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.setIcon(qta.icon("fa5s.check", color="white"))
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(on_ok)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Annulla")
        cancel_btn.setIcon(qta.icon("fa5s.times", color="#333"))
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

    def _build_llm_config_from_widgets(self):
        """Build an LLMConfig from current settings widgets."""
        from text_processing.llm_analyzer import LLMConfig

        provider_label = self._llm_provider_combo.currentText()
        provider = self._llm_label_to_provider.get(provider_label, "")
        api_key_raw = self._llm_api_key_entry.text()
        api_key = decrypt_password(api_key_raw) if api_key_raw.startswith("ENC:") else api_key_raw

        return LLMConfig(
            provider=provider,
            api_key=api_key,
            model=self._llm_model_combo.currentText().strip(),
            timeout=int(self._llm_timeout_entry.text() or "120"),
            base_url=self._llm_base_url_entry.text().strip(),
        )

    def _open_interpreter(self) -> None:
        """Open the report interpreter dialog with current LLM settings."""
        from report_interpreter import ReportInterpreterDialog

        dlg = ReportInterpreterDialog(self, llm_config=self._build_llm_config_from_widgets())
        dlg.exec()

    def _open_text_comparison(self) -> None:
        """Open the text comparison dialog for original vs anonymized view."""
        from text_comparison import TextComparisonDialog

        download_dir = self._download_dir_entry.text() or str(paths.default_download_dir)
        dlg = TextComparisonDialog(self, download_dir=download_dir)
        dlg.exec()

    def _start_analysis(self) -> None:
        """Start report analysis based on current mode and scope settings."""
        from text_processing import TextProcessor, ProcessingMode, LLMConfig

        is_ai = self._mode_radio_ai.isChecked()
        is_choose = self._scope_radio_choose.isChecked()

        download_dir = self._download_dir_entry.text() or str(paths.default_download_dir)
        text_dir = self._text_dir_entry.text()
        if not text_dir:
            QMessageBox.warning(
                self, "Directory mancante",
                "Configura la directory di salvataggio testi nelle impostazioni.",
            )
            return

        # ── Scope "Scegli referti": open picker dialog ──
        if is_choose:
            from report_interpreter import ReportPickerDialog

            llm_config = self._build_llm_config_from_widgets() if is_ai else None
            dlg = ReportPickerDialog(
                self,
                download_dir=download_dir,
                text_dir=text_dir,
                llm_config=llm_config,
            )
            dlg.exec()
            return

        # ── Scope "Tutti i referti scaricati" ──
        dl_path = Path(download_dir)
        pdfs = sorted(dl_path.glob("*.pdf"))
        if not pdfs:
            QMessageBox.warning(
                self, "Nessun referto",
                f"Nessun file PDF trovato in:\n{dl_path}",
            )
            return

        # AI mode warning
        if is_ai:
            reply = QMessageBox.warning(
                self, "Analisi con A.I.",
                f"L'analisi con AI di {len(pdfs)} referti potrebbe richiedere "
                "diversi minuti.\nContinuare?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Ok:
                return

        # Check for already-analyzed reports
        text_path = Path(text_dir)
        already_done = []
        new_pdfs = []
        for pdf in pdfs:
            txt_file = text_path / f"{pdf.stem}.txt"
            if txt_file.exists():
                already_done.append(pdf)
            else:
                new_pdfs.append(pdf)

        if already_done:
            reply = QMessageBox.question(
                self, "Referti già analizzati",
                f"{len(already_done)} referti sono già stati analizzati.\n"
                "Vuoi rianalizzarli?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            elif reply == QMessageBox.StandardButton.Yes:
                pdfs_to_process = pdfs  # all
            else:
                pdfs_to_process = new_pdfs  # only new
        else:
            pdfs_to_process = pdfs

        if not pdfs_to_process:
            QMessageBox.information(
                self, "Nulla da analizzare",
                "Tutti i referti sono già stati analizzati.",
            )
            return

        # Build processor
        if is_ai:
            llm_config = self._build_llm_config_from_widgets()
            processor = TextProcessor(ProcessingMode.AI_ASSISTED, llm_config=llm_config)
        else:
            processor = TextProcessor(ProcessingMode.LOCAL_ONLY)

        # Progress dialog
        progress = QProgressDialog(
            "Analisi referti in corso...", "Annulla", 0, len(pdfs_to_process), self
        )
        progress.setWindowTitle("Analisi referti")
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModality.WindowModal)

        successes = 0
        errors = 0
        out_dir = Path(text_dir)

        for i, pdf in enumerate(pdfs_to_process):
            if progress.wasCanceled():
                break
            progress.setLabelText(f"Analisi: {pdf.name}")
            progress.setValue(i)
            QApplication.processEvents()

            try:
                result = processor.process(pdf)
                if result.success:
                    saved = TextProcessor.save_result(result, out_dir, pdf.stem)
                    if saved:
                        successes += 1
                    else:
                        errors += 1
                else:
                    errors += 1
            except Exception:
                errors += 1

        progress.setValue(len(pdfs_to_process))

        QMessageBox.information(
            self, "Analisi completata",
            f"Analisi terminata.\n\n"
            f"Successi: {successes}\n"
            f"Errori: {errors}",
        )

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

    def _check_updates_startup(self) -> None:
        """Silent update check at startup – only notifies if a new version exists."""
        self._check_updates(silent=True)

    def _check_updates(self, silent: bool = False) -> None:
        """Check for updates by fetching version.json from GitHub.

        When *silent* is True (startup check), no feedback is shown if the app
        is already up-to-date or if the network request fails.
        """
        VERSION_URL = "https://raw.githubusercontent.com/zndr/FSEpub/main/version.json"

        def worker():
            try:
                req = urllib.request.Request(VERSION_URL, headers={"User-Agent": "FSE-Processor"})
                # Skip SSL verification for update check only (hardcoded GitHub URL).
                # PyInstaller frozen bundles lack cacert.pem for OpenSSL.
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                remote_version = data.get("Version", "")
                download_url = data.get("DownloadUrl", "")
                release_notes = data.get("ReleaseNotes", "")

                if remote_version and _version_tuple(remote_version) > _version_tuple(__version__):
                    msg = (
                        f"Nuova versione disponibile: v{remote_version}\n"
                        f"Versione attuale: v{__version__}\n\n"
                    )
                    if release_notes:
                        msg += f"{release_notes}\n\n"
                    if download_url:
                        msg += "Vuoi aprire la pagina di download?"
                    self._bridge.call_on_main.emit(lambda: self._prompt_update(msg, download_url))
                elif not silent:
                    self._bridge.show_info.emit(
                        "Aggiornamenti",
                        f"Nessun aggiornamento disponibile.\n\nVersione attuale: v{__version__}",
                    )
            except Exception as e:
                if not silent:
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
        dlg.resize(560, 580)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel("Descrizione del problema:"))
        problem_text = QTextEdit()
        problem_text.setFont(QFont("Segoe UI", 10))
        problem_text.setPlaceholderText("Descrivi qui il problema riscontrato...")
        problem_text.setMaximumHeight(100)
        layout.addWidget(problem_text)

        # Attachments
        attach_group = QGroupBox("Allegati (screenshot, immagini)")
        attach_layout = QVBoxLayout(attach_group)
        attach_list = QListWidget()
        attach_list.setMaximumHeight(80)
        attach_layout.addWidget(attach_list)
        attached_files: list[str] = []

        attach_btn_row = QHBoxLayout()
        btn_add_file = QPushButton("Aggiungi immagine...")
        btn_add_file.setIcon(qta.icon("fa5s.image", color="white"))
        btn_add_file.setToolTip("Allega uno screenshot o un'immagine al messaggio")

        def _add_attachments():
            files, _ = QFileDialog.getOpenFileNames(
                dlg,
                "Seleziona immagini",
                "",
                "Immagini (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;Tutti i file (*)",
            )
            for f in files:
                if f not in attached_files:
                    attached_files.append(f)
                    attach_list.addItem(Path(f).name)

        btn_add_file.clicked.connect(_add_attachments)
        attach_btn_row.addWidget(btn_add_file)

        btn_paste_clip = QPushButton("Incolla da clipboard")
        btn_paste_clip.setIcon(qta.icon("fa5s.paste", color="white"))
        btn_paste_clip.setToolTip("Incolla un'immagine copiata negli appunti (es. da Cattura schermo)")

        def _paste_clipboard_image():
            clipboard = QApplication.clipboard()
            image = clipboard.image()
            if image.isNull():
                QMessageBox.information(dlg, "Clipboard", "Nessuna immagine trovata negli appunti.")
                return
            tmp_dir = Path(paths.log_dir)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            idx = len(attached_files) + 1
            tmp_path = str(tmp_dir / f"clipboard_{idx}.png")
            image.save(tmp_path)
            attached_files.append(tmp_path)
            attach_list.addItem(f"clipboard_{idx}.png")

        btn_paste_clip.clicked.connect(_paste_clipboard_image)
        attach_btn_row.addWidget(btn_paste_clip)

        btn_remove = QPushButton("Rimuovi")
        btn_remove.setIcon(qta.icon("fa5s.trash", color="white"))
        btn_remove.setToolTip("Rimuovi l'allegato selezionato")

        def _remove_attachment():
            row = attach_list.currentRow()
            if row >= 0:
                attach_list.takeItem(row)
                attached_files.pop(row)

        btn_remove.clicked.connect(_remove_attachment)
        attach_btn_row.addWidget(btn_remove)

        attach_btn_row.addStretch()
        attach_layout.addLayout(attach_btn_row)
        layout.addWidget(attach_group)

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
        copy_btn.setIcon(qta.icon("fa5s.copy", color="white"))
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(_get_full_report()))
        btn_layout.addWidget(copy_btn)

        preview_btn = QPushButton("Anteprima")
        preview_btn.setIcon(qta.icon("fa5s.eye", color="white"))
        preview_btn.setToolTip("Visualizza il messaggio che verra' inviato al supporto (con CF oscurati)")
        preview_btn.clicked.connect(lambda: self._show_send_preview(self._sanitize_cf(_get_full_report())))
        btn_layout.addWidget(preview_btn)

        send_btn = QPushButton("Invia")
        send_btn.setIcon(qta.icon("fa5s.paper-plane", color="white"))
        send_btn.setToolTip("Invia le informazioni di debug via email a supporto@dottorgiorgio.it")
        send_btn.clicked.connect(lambda: self._send_debug_email(_get_full_report(), dlg, attached_files))
        btn_layout.addWidget(send_btn)

        btn_layout.addStretch()
        close_btn = QPushButton("Chiudi")
        close_btn.setIcon(qta.icon("fa5s.times-circle", color="white"))
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
        close_btn.setIcon(qta.icon("fa5s.times-circle", color="white"))
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

    def _send_debug_email(
        self, body: str, dlg: QDialog = None, attachments: list[str] | None = None,
    ) -> None:
        """Send debug info via SMTP using configured email credentials."""
        import email.message
        import mimetypes
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
        file_list = list(attachments or [])

        subject = f"FSE Processor v{__version__} - Debug Info"
        dest = "supporto@dottorgiorgio.it"

        def worker():
            try:
                msg = email.message.EmailMessage()
                msg["Subject"] = subject
                msg["From"] = user
                msg["To"] = dest
                msg.set_content(sanitized_body)

                for filepath in file_list:
                    p = Path(filepath)
                    if not p.is_file():
                        continue
                    mime, _ = mimetypes.guess_type(str(p))
                    if mime and mime.startswith("image/"):
                        maintype, subtype = mime.split("/", 1)
                    else:
                        maintype, subtype = "application", "octet-stream"
                    msg.add_attachment(
                        p.read_bytes(),
                        maintype=maintype,
                        subtype=subtype,
                        filename=p.name,
                    )

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
                    n_att = len(file_list)
                    att_note = f"\n\n{n_att} allegat{'o' if n_att == 1 else 'i'} inclus{'o' if n_att == 1 else 'i'}." if n_att else ""
                    self._bridge.show_info.emit(
                        "Supporto",
                        "Messaggio inviato.\n\n"
                        "Tutti i codici fiscali sono stati rimossi per tutelare "
                        "la privacy dei pazienti." + att_note,
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

    def _browse_move_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Seleziona directory destinazione", self._move_dir_entry.text() or ".")
        if path:
            self._move_dir_entry.setText(os.path.normpath(path))

    def _browse_text_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Seleziona directory testi", self._text_dir_entry.text() or ".")
        if path:
            self._text_dir_entry.setText(os.path.normpath(path))

    # ---- AI / LLM settings helpers ----

    def _on_mode_changed(self) -> None:
        """Update UI state when the processing mode radio changes."""
        is_none = self._mode_radio_none.isChecked()
        is_ai = self._mode_radio_ai.isChecked()

        # Scope radios: disabled when "Nessuna"
        self._scope_radio_all.setEnabled(not is_none)
        self._scope_radio_choose.setEnabled(not is_none)

        # AI group: visible only in AI mode
        self._ai_group.setVisible(is_ai)

        # Analyze button: visible when not "Nessuna"
        if self._btn_analyze is not None:
            self._btn_analyze.setVisible(not is_none)

        # Auto-populate TEXT_DIR when enabling text processing
        if not is_none and hasattr(self, '_text_dir_entry') and not self._text_dir_entry.text().strip():
            dl_dir = self._download_dir_entry.text().strip()
            if dl_dir:
                default_text_dir = str(Path(dl_dir) / "testi")
            else:
                default_text_dir = str(paths.default_download_dir / "testi")
            self._text_dir_entry.setText(default_text_dir)

    def _on_llm_provider_changed(self, label: str) -> None:
        """Update UI when the LLM provider selection changes."""
        provider = self._llm_label_to_provider.get(label, "")
        # Show/hide base URL (only for custom_url)
        is_custom = provider == "custom_url"
        self._llm_base_url_label.setVisible(is_custom)
        self._llm_base_url_entry.setVisible(is_custom)
        # Reset API key — each provider needs its own key
        self._llm_api_key_entry.clear()
        needs_key = provider != "claude_cli"
        self._llm_api_key_entry.setEnabled(needs_key)
        self._llm_api_key_entry.setReadOnly(False)  # ensure always writable
        self._llm_show_key_btn.setEnabled(needs_key)
        if not needs_key:
            self._llm_api_key_entry.setPlaceholderText("(non necessaria)")
        else:
            self._llm_api_key_entry.setPlaceholderText("Inserisci la tua API key")
        # Update model suggestions
        self._update_model_combo_items(provider)

    # Model suggestions per provider — also used for test validation
    _MODEL_SUGGESTIONS: dict[str, list[str]] = {
        "claude_api": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"],
        "openai_api": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
        "gemini_api": ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
        "mistral_api": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
        "claude_cli": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"],
        "custom_url": [],
    }

    def _update_model_combo_items(self, provider: str) -> None:
        """Populate model combo with suggestions for the given provider."""
        self._llm_model_combo.blockSignals(True)
        self._llm_model_combo.clear()
        self._llm_model_combo.clearEditText()
        self._llm_model_combo.setEditable(True)  # re-assert after clear
        suggestions = self._MODEL_SUGGESTIONS.get(provider, [])
        if suggestions:
            self._llm_model_combo.addItems(suggestions)
            default = self._llm_default_models.get(provider, "")
            if default in suggestions:
                self._llm_model_combo.setCurrentText(default)
        self._llm_model_combo.blockSignals(False)

    def _test_llm_connection(self) -> None:
        """Test the LLM connection in a background thread."""
        # Pre-flight validation on UI thread before spawning background work
        provider_label = self._llm_provider_combo.currentText()
        provider = self._llm_label_to_provider.get(provider_label, "")
        model = self._llm_model_combo.currentText().strip()

        if not provider:
            QMessageBox.warning(self, "Test AI", "Seleziona un provider prima di testare.")
            return
        if not model:
            QMessageBox.warning(self, "Test AI", "Inserisci un modello prima di testare.")
            return

        # Validate model is compatible with the selected provider
        error = _validate_provider_model(provider, model)
        if error:
            QMessageBox.warning(self, "Test AI", error)
            return

        self._llm_test_btn.setEnabled(False)
        self._llm_test_btn.setText("Test in corso...")
        threading.Thread(target=self._test_llm_worker, daemon=True).start()

    def _test_llm_worker(self) -> None:
        try:
            from text_processing.llm_analyzer import LLMAnalyzer, LLMConfig
            from credential_manager import decrypt_password

            provider_label = self._llm_provider_combo.currentText()
            provider = self._llm_label_to_provider.get(provider_label, "")
            api_key_raw = self._llm_api_key_entry.text()
            api_key = decrypt_password(api_key_raw) if api_key_raw.startswith("ENC:") else api_key_raw

            config = LLMConfig(
                provider=provider,
                api_key=api_key,
                model=self._llm_model_combo.currentText().strip(),
                timeout=15,
                base_url=self._llm_base_url_entry.text().strip(),
            )
            analyzer = LLMAnalyzer(config)
            ok = analyzer.is_available()
            if ok:
                self._bridge.show_info.emit("Test AI", f"Connessione a {provider_label} riuscita!")
            else:
                self._bridge.show_error.emit("Test AI", f"Connessione a {provider_label} fallita.\nVerifica API key e connessione internet.")
        except Exception as e:
            self._bridge.show_error.emit("Test AI", f"Errore: {e}")
        finally:
            self._bridge.call_on_main.emit(lambda: self._llm_test_btn.setEnabled(True))
            self._bridge.call_on_main.emit(lambda: self._llm_test_btn.setText("Testa connessione"))

    # ---- IMAP folder picker ----

    def _browse_imap_folders(self) -> None:
        self._btn_browse_folders.setEnabled(False)
        self._btn_browse_folders.setText("Caricamento...")
        threading.Thread(target=self._fetch_imap_folders_worker, daemon=True).start()

    def _fetch_imap_folders_worker(self) -> None:
        try:
            self._save_settings_quietly()
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            client = EmailClient(config, logger)
            client.connect()
            folders = self._list_imap_folders(client)
            client.disconnect()
            # Show picker on main thread
            current = self._settings_entries["IMAP_FOLDER"].text()
            self._bridge.call_on_main.emit(
                lambda f=folders, c=current: self._show_folder_picker(f, c)
            )
        except Exception as e:
            err_msg = str(e) if str(e) and str(e) != "None" else (
                f"{type(e).__name__}: {e.args}" if e.args else type(e).__name__
            )
            self._bridge.show_error.emit(
                "Cartelle IMAP",
                f"Impossibile recuperare le cartelle:\n\n{err_msg}",
            )
        finally:
            self._bridge.call_on_main.emit(
                lambda: (
                    self._btn_browse_folders.setEnabled(True),
                    self._btn_browse_folders.setText("Sfoglia..."),
                )
            )

    _IMAP_LIST_RE = re.compile(
        rb'\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)'
    )

    @staticmethod
    def _list_imap_folders(client: EmailClient) -> list[str]:
        """Retrieve the list of mailbox folder names from the IMAP server."""
        conn = client._connection
        status, raw = conn.list()
        if status != "OK":
            return ["INBOX"]
        folders: list[str] = []
        for item in raw:
            if item is None:
                continue
            raw_line = item if isinstance(item, bytes) else item.encode("utf-8")
            m = FSEApp._IMAP_LIST_RE.match(raw_line)
            if not m:
                continue
            flags = m.group("flags").decode("utf-8", errors="replace")
            if "\\Noselect" in flags or "\\NoSelect" in flags:
                continue
            name = m.group("name").decode("utf-8", errors="replace").strip().strip('"')
            if name:
                folders.append(name)
        return sorted(folders)

    def _show_folder_picker(self, folders: list[str], current_text: str) -> None:
        """Show a dialog to let the user pick IMAP folders."""
        current_set = {f.strip() for f in current_text.split(",") if f.strip()}

        dlg = QDialog(self)
        dlg.setWindowTitle("Seleziona cartelle IMAP")
        dlg.setMinimumWidth(400)
        dlg.setMinimumHeight(350)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel("Seleziona le cartelle da monitorare:"))

        lst = QListWidget()
        for folder_name in folders:
            item = QListWidgetItem(folder_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if folder_name in current_set else Qt.CheckState.Unchecked
            )
            lst.addItem(item)
        layout.addWidget(lst)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected: list[str] = []
            for i in range(lst.count()):
                item = lst.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    selected.append(item.text())
            if selected:
                self._settings_entries["IMAP_FOLDER"].setText(", ".join(selected))

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
        self._fields["DEBUG_LOGGING"] = "true" if self._debug_cb.isChecked() else "false"
        self._fields["MOVE_DIR"] = self._move_dir_entry.text()
        if self._mode_radio_none.isChecked():
            self._fields["PROCESS_TEXT"] = "false"
            self._fields["PROCESSING_MODE"] = "none"
        elif self._mode_radio_ai.isChecked():
            self._fields["PROCESS_TEXT"] = "true"
            self._fields["PROCESSING_MODE"] = "ai"
        else:
            self._fields["PROCESS_TEXT"] = "true"
            self._fields["PROCESSING_MODE"] = "local"
        self._fields["TEXT_DIR"] = self._text_dir_entry.text()
        provider_label = self._llm_provider_combo.currentText()
        self._fields["LLM_PROVIDER"] = self._llm_label_to_provider.get(provider_label, "")
        self._fields["LLM_API_KEY"] = self._llm_api_key_entry.text()
        self._fields["LLM_MODEL"] = self._llm_model_combo.currentText()
        self._fields["LLM_TIMEOUT"] = self._llm_timeout_entry.text()
        self._fields["LLM_BASE_URL"] = self._llm_base_url_entry.text()
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
        # Encrypt passwords before saving to disk
        raw_pass = values.get("EMAIL_PASS", "")
        if raw_pass and not is_encrypted(raw_pass):
            values["EMAIL_PASS"] = encrypt_password(raw_pass)
        raw_api_key = values.get("LLM_API_KEY", "")
        if raw_api_key and not is_encrypted(raw_api_key):
            values["LLM_API_KEY"] = encrypt_password(raw_api_key)
        return values

    # ---- Settings ----

    def _load_settings(self) -> None:
        env_vals = _load_env_values()
        need_migration = False
        for key, _, default, kind in SETTINGS_SPEC:
            val = env_vals.get(key, default)

            # Decrypt passwords for in-memory / widget use
            if key == "EMAIL_PASS":
                raw_val = val
                val = decrypt_password(val)
                if raw_val and not is_encrypted(raw_val):
                    need_migration = True
            elif key == "LLM_API_KEY":
                val = decrypt_password(val)

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
            elif key == "DEBUG_LOGGING":
                self._debug_cb.setChecked(val.lower() == "true")
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
            elif key == "MOVE_DIR":
                self._move_dir_entry.setText(os.path.normpath(val) if val else "")
            elif key == "PROCESS_TEXT":
                pass  # Derived from PROCESSING_MODE
            elif key == "TEXT_DIR":
                self._text_dir_entry.setText(os.path.normpath(val) if val else "")
            elif key == "PROCESSING_MODE":
                if val == "ai":
                    self._mode_radio_ai.setChecked(True)
                elif val == "none":
                    self._mode_radio_none.setChecked(True)
                else:
                    self._mode_radio_local.setChecked(True)
            elif key == "LLM_PROVIDER":
                if val in self._llm_provider_labels:
                    self._llm_provider_combo.setCurrentText(self._llm_provider_labels[val])
            elif key == "LLM_API_KEY":
                self._llm_api_key_entry.setText(val)
            elif key == "LLM_MODEL":
                if val:
                    self._llm_model_combo.setCurrentText(val)
            elif key == "LLM_TIMEOUT":
                self._llm_timeout_entry.setText(val)
            elif key == "LLM_BASE_URL":
                self._llm_base_url_entry.setText(val)

        # Apply initial states
        self._on_mode_changed()

        # Apply initial state for delete toggle
        self._on_delete_toggled(self._siss_delete_cb.isChecked())

        # Auto-migrate plain-text password to encrypted form
        if need_migration:
            self._save_settings_quietly()

    def _show_change_password_dialog(self) -> None:
        """Show a modal dialog to change the email password."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Cambia password email")
        dlg.setMinimumWidth(360)
        layout = QVBoxLayout(dlg)

        # Read current stored value from env file to verify old password
        env_vals = _load_env_values()
        stored_pass = env_vals.get("EMAIL_PASS", "")
        has_existing = bool(stored_pass)

        error_label = QLabel("")
        error_label.setStyleSheet("color: red;")
        error_label.setWordWrap(True)
        error_label.hide()
        layout.addWidget(error_label)

        # Current password (only if one is already saved)
        current_entry = None
        if has_existing:
            layout.addWidget(QLabel("Password attuale:"))
            current_entry = QLineEdit()
            current_entry.setEchoMode(QLineEdit.EchoMode.Password)
            layout.addWidget(current_entry)

        layout.addWidget(QLabel("Nuova password:"))
        new_entry = QLineEdit()
        new_entry.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(new_entry)

        layout.addWidget(QLabel("Conferma nuova password:"))
        confirm_entry = QLineEdit()
        confirm_entry.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(confirm_entry)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        def on_accept():
            error_label.hide()

            # Verify current password if one exists
            if has_existing and current_entry is not None:
                if not verify_password(current_entry.text(), stored_pass):
                    error_label.setText("La password attuale non e' corretta.")
                    error_label.show()
                    return

            new_pass = new_entry.text()
            confirm_pass = confirm_entry.text()

            if not new_pass:
                error_label.setText("La nuova password non puo' essere vuota.")
                error_label.show()
                return

            if new_pass != confirm_pass:
                error_label.setText("Le password non coincidono.")
                error_label.show()
                return

            # Update the field and save
            self._fields["EMAIL_PASS"] = new_pass
            self._settings_entries["EMAIL_PASS"].setText(new_pass)
            self._save_settings_quietly()
            dlg.accept()
            QMessageBox.information(self, "Password", "Password aggiornata correttamente.")

        buttons.accepted.connect(on_accept)
        buttons.rejected.connect(dlg.reject)
        dlg.exec()

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
                msg = "Nessuna email con referti non letti da scaricare"
            elif client.limit_reached:
                msg = (
                    f"Trovati {count} messaggi da scaricare "
                    f"(raggiunto limite specificato nelle impostazioni)"
                )
            else:
                msg = f"{count} email con referti non letti da scaricare"
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
            handler.setLevel(logging.DEBUG if config.debug_logging else logging.INFO)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger._logger.addHandler(handler)

            run_processing(
                config, logger, self._stop_event,
                allowed_types=allowed_types,
                on_cdp_restart_needed=self._ask_restart_browser,
            )
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
        self._btn_list_docs.setEnabled(False)
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
            handler.setLevel(logging.DEBUG if config.debug_logging else logging.INFO)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger._logger.addHandler(handler)

            browser = FSEBrowser(config, logger)
            self._start_browser_safe(browser)
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

            # Save the browser for reuse by download worker.
            # Stop any previous browser first to release the CDP connection.
            if self._patient_browser is not None and self._patient_browser is not browser:
                try:
                    self._patient_browser.stop()
                except Exception:
                    pass
            self._patient_browser = browser
        except Exception as e:
            self._bridge.append_text.emit(self._patient_console, f"Errore scansione strutture: {e}")

    def _poll_ente_scan(self) -> None:
        if self._ente_scan_worker and self._ente_scan_worker.is_alive():
            return
        self._ente_scan_poll_timer.stop()
        self._btn_load_enti.setEnabled(True)
        self._btn_patient_start.setEnabled(True)
        self._btn_list_docs.setEnabled(True)
        self._btn_patient_stop.setEnabled(False)
        self._patient_log("--- Scansione strutture terminata ---")

    # ---- List documents workflow ----

    def _start_list_documents(self) -> None:
        """Validate CF and start the list documents worker."""
        if self._list_docs_worker and self._list_docs_worker.is_alive():
            QMessageBox.warning(self, "Attenzione", "Elencazione referti gia' in corso")
            return
        if self._patient_worker and self._patient_worker.is_alive():
            QMessageBox.warning(self, "Attenzione", "Download paziente in corso, attendere il completamento")
            return

        cf = self._cf_entry.text().strip().upper()
        if not re.match(r"^[A-Z0-9]{16}$", cf):
            QMessageBox.warning(self, "Errore", "Il codice fiscale deve essere di 16 caratteri alfanumerici")
            return

        self._save_settings_quietly()
        self._patient_stop_event.clear()
        self._btn_list_docs.setEnabled(False)
        self._btn_patient_start.setEnabled(False)
        self._btn_load_enti.setEnabled(False)
        self._btn_patient_stop.setEnabled(True)
        self._patient_log("--- Elencazione referti in corso... ---")

        self._list_docs_worker = threading.Thread(
            target=self._list_docs_worker_fn, args=(cf,), daemon=True,
        )
        self._list_docs_worker.start()
        self._list_docs_poll_timer = QTimer(self)
        self._list_docs_poll_timer.timeout.connect(self._poll_list_docs)
        self._list_docs_poll_timer.start(500)

    def _list_docs_worker_fn(self, codice_fiscale: str) -> None:
        """Worker: open browser, list documents, then signal main thread to show dialog."""
        try:
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            handler = TextHandler(self._patient_console, self._bridge)
            handler.setLevel(logging.DEBUG if config.debug_logging else logging.INFO)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger._logger.addHandler(handler)

            # Reuse browser if available
            reused = False
            if self._patient_browser is not None:
                try:
                    if self._patient_browser._is_alive():
                        browser = self._patient_browser
                        reused = True
                        logger.info("Riutilizzo browser esistente")
                except Exception:
                    pass
                if not reused:
                    # Stop stale browser to release CDP connection before
                    # creating a new one (Chrome allows only one debugger).
                    try:
                        self._patient_browser.stop()
                    except Exception:
                        pass
                    self._patient_browser = None
            if not reused:
                browser = FSEBrowser(config, logger)
                self._start_browser_safe(browser)
                browser.wait_for_manual_login(stop_event=self._patient_stop_event)

            def on_enti_found(enti):
                self._bridge.call_on_main.emit(lambda e=enti: self._update_ente_combobox(e))

            docs = browser.list_patient_documents(
                codice_fiscale,
                stop_event=self._patient_stop_event,
                on_enti_found=on_enti_found,
            )

            # Save browser for reuse by download worker
            self._patient_browser = browser

            # Show dialog on main thread
            self._bridge.call_on_main.emit(lambda d=docs: self._show_document_list_dialog(d))
        except InterruptedError:
            self._patient_log("Elencazione interrotta dall'utente")
        except Exception as e:
            self._patient_log(f"Errore elencazione referti: {e}")

    def _show_document_list_dialog(self, docs: list[PatientDocumentInfo]) -> None:
        """Show the document selection dialog on the main thread."""
        if not docs:
            self._patient_log("Nessun referto trovato nella tabella")
            return

        allowed_types = self._get_patient_selected_types()
        dlg = DocumentListDialog(docs, allowed_types=allowed_types, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._selected_row_indices = dlg.get_selected_row_indices()
            count = len(self._selected_row_indices)
            self._patient_log(f"Selezionati {count} referti su {len(docs)} totali")
        else:
            self._selected_row_indices = None
            self._patient_log("Selezione annullata")

    def _poll_list_docs(self) -> None:
        """Poll the list documents worker thread."""
        if self._list_docs_worker and self._list_docs_worker.is_alive():
            return
        self._list_docs_poll_timer.stop()
        self._btn_list_docs.setEnabled(True)
        self._btn_patient_start.setEnabled(True)
        self._btn_load_enti.setEnabled(True)
        self._btn_patient_stop.setEnabled(False)
        self._patient_log("--- Elencazione referti terminata ---")

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

        selected = self._get_patient_selected_types()
        if selected is not None and not selected:
            QMessageBox.warning(self, "Errore", "Seleziona almeno una tipologia di documento")
            return

        ente_filter = self._ente_combo.currentText().strip()
        date_from = self._parse_user_date(self._date_from_entry.text())
        date_to = self._parse_user_date(self._date_to_entry.text())
        row_indices = self._selected_row_indices

        self._save_settings_quietly()
        self._patient_stop_event.clear()
        self._btn_patient_start.setEnabled(False)
        self._btn_patient_stop.setEnabled(True)
        self._btn_load_enti.setEnabled(False)
        self._btn_list_docs.setEnabled(False)
        self._patient_log(f"--- Avvio download per CF: {cf} ---")

        self._patient_worker = threading.Thread(
            target=self._patient_download_worker,
            args=(cf, selected, ente_filter, date_from, date_to, row_indices),
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
                                 date_to: date | None = None,
                                 selected_row_indices: set[int] | None = None) -> None:
        try:
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            handler = TextHandler(self._patient_console, self._bridge)
            handler.setLevel(logging.DEBUG if config.debug_logging else logging.INFO)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger._logger.addHandler(handler)

            logger.debug(f"[DIAG] process_text={config.process_text}, "
                         f"text_dir={config.text_dir}, "
                         f"processing_mode={config.processing_mode}")

            file_manager = FileManager(config, logger)

            # Reuse browser from ente scan or list docs if still alive
            reused = False
            if self._patient_browser is not None:
                try:
                    if self._patient_browser._is_alive():
                        browser = self._patient_browser
                        self._patient_browser = None
                        reused = True
                        logger.info("Riutilizzo browser esistente")
                except Exception:
                    pass
                if not reused:
                    # Stop stale browser to release CDP connection before
                    # creating a new one (Chrome allows only one debugger).
                    try:
                        self._patient_browser.stop()
                    except Exception:
                        pass
                    self._patient_browser = None
            if not reused:
                browser = FSEBrowser(config, logger)
                self._start_browser_safe(browser)
                browser.wait_for_manual_login(stop_event=self._patient_stop_event)

            try:
                def on_enti_found(enti):
                    self._bridge.call_on_main.emit(lambda e=enti: self._update_ente_combobox(e))

                doc_results = browser.process_patient_all_dates(
                    codice_fiscale, self._patient_stop_event, allowed_types,
                    ente_filter=ente_filter, date_from=date_from, date_to=date_to,
                    on_enti_found=on_enti_found,
                    selected_row_indices=selected_row_indices,
                )
                # Reset row selection after use
                self._selected_row_indices = None

                # Initialize text processor
                text_processor = None
                text_dir = config.text_dir
                if config.process_text:
                    if not text_dir:
                        text_dir = config.download_dir / "testi"
                        logger.info(f"TEXT_DIR non configurata, uso default: {text_dir}")
                    from text_processing import TextProcessor, ProcessingMode, LLMConfig
                    if config.processing_mode == "ai" and config.llm_provider:
                        mode = ProcessingMode.AI_ASSISTED
                        llm_cfg = LLMConfig(
                            provider=config.llm_provider,
                            api_key=config.llm_api_key,
                            model=config.llm_model,
                            timeout=config.llm_timeout,
                            base_url=config.llm_base_url,
                        )
                        text_processor = TextProcessor(mode, llm_config=llm_cfg)
                    else:
                        mode = ProcessingMode.LOCAL_ONLY
                        text_processor = TextProcessor(mode)
                    logger.info(f"Processazione testo attiva (modalita': {mode.value})")
                else:
                    logger.debug("[DIAG] process_text=False, text_processor non creato")

                logger.debug(f"[DIAG] text_processor={'creato' if text_processor else 'None'}, "
                             f"text_dir={text_dir}")

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
                    renamed = file_manager.rename_download(
                        download_path=result.download_path,
                        patient_name=codice_fiscale,
                        codice_fiscale=codice_fiscale,
                        disciplina=result.disciplina,
                        fse_link=f"{FSE_BASE_URL}#/?codiceFiscale={codice_fiscale}",
                    )

                    logger.debug(f"[DIAG] renamed={'None' if renamed is None else renamed.name}, "
                                 f"text_processor={'si' if text_processor else 'None'}")

                    # Text processing
                    if renamed and text_processor is not None:
                        try:
                            tp_result = text_processor.process(renamed)
                            if tp_result.success:
                                saved = TextProcessor.save_result(
                                    tp_result, text_dir, renamed.stem,
                                )
                                if saved:
                                    logger.info(f"Testo salvato: {saved.name}")
                            else:
                                logger.warning(
                                    f"Estrazione testo fallita per {renamed.name}: "
                                    f"{tp_result.error_message}"
                                )
                        except Exception as e:
                            logger.warning(f"Errore processazione testo {renamed.name}: {e}")

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
        self._btn_list_docs.setEnabled(True)
        self._patient_log("--- Download terminato ---")

    def _stop_patient_download(self) -> None:
        self._patient_stop_event.set()
        self._patient_log("Richiesta interruzione inviata...")
        self._btn_patient_stop.setEnabled(False)

    def closeEvent(self, event) -> None:
        if self._mw_auto_timer is not None:
            self._mw_auto_timer.stop()
            self._mw_auto_timer = None
        if self._patient_browser is not None:
            try:
                self._patient_browser.stop()
            except Exception:
                pass
            self._patient_browser = None
        if self._mw_browser is not None:
            try:
                self._mw_browser.stop()
            except Exception:
                pass
            self._mw_browser = None
        super().closeEvent(event)


def _force_light_palette(app: QApplication) -> None:
    """Override the system palette with a light theme to avoid dark-mode issues."""
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(20, 20, 20))
    palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(233, 236, 239))
    palette.setColor(QPalette.ColorRole.Text, QColor(20, 20, 20))
    palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(20, 20, 20))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 108, 176))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(20, 20, 20))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 120, 120))
    app.setPalette(palette)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    _force_light_palette(app)
    app.setStyleSheet(APP_STYLE)
    window = FSEApp()
    window.show()
    sys.exit(app.exec())
