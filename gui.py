"""GUI tkinter per FSE Processor."""

import ctypes
import logging
import os
import re
import threading
import tkinter as tk
import traceback
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

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
    ("IMAP_HOST", "IMAP Host", "mail-crs-lombardia.fastweb360.it", "text"),
    ("IMAP_PORT", "IMAP Port", "993", "int"),
    ("DOWNLOAD_DIR", "Directory download", str(paths.default_download_dir), "dir"),
    ("BROWSER_CHANNEL", "Browser", "msedge", "browser_selector"),
    ("PDF_READER", "Lettore PDF", "default", "pdf_reader"),
    ("USE_EXISTING_BROWSER", "Usa browser esistente (CDP)", "false", "bool"),
    ("CDP_PORT", "Porta CDP", "9222", "int"),
    ("HEADLESS", "Headless browser", "false", "bool"),
    ("DOWNLOAD_TIMEOUT", "Download timeout (sec)", "60", "int"),
    ("PAGE_TIMEOUT", "Page timeout (sec)", "30", "int"),
    ("DELETE_AFTER_PROCESSING", "Elimina email dopo elaborazione", "false", "bool"),
    ("MAX_EMAILS", "Max email da processare (0=tutte)", "3", "int"),
]

# Sentinel values for PDF reader selection
PDF_READER_DEFAULT = "default"
PDF_READER_CUSTOM = "__custom__"
PDF_READER_DEFAULT_LABEL = "Predefinito di sistema"
PDF_READER_CUSTOM_LABEL = "Personalizzato..."

DOCUMENT_TYPES = [
    ("REFERTO", "Referto", True),
    ("LETTERA DIMISSIONE", "Lettera Dimissione", True),
    ("VERBALE PRONTO SOCCORSO", "Verbale Pronto Soccorso", True),
]


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
            # Find the first distinguishing parent folder for each
            all_parts = [Path(raw_paths[nk]).parts for nk in nkeys]
            for nk, parts in zip(nkeys, all_parts):
                # Walk up from parent to find a unique folder
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

    # Return sorted by display name
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
        "brave.exe": (None, "Brave"),  # channel=None means use exe path
    }

    browsers: list[tuple[str, str]] = []
    seen: set[str] = set()

    for exe_name, (channel, display) in KNOWN_BROWSERS.items():
        # Try App Paths registry
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

        # Fallback: well-known install paths
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
        # Check SupportedTypes for .pdf
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
        # val is like "SOFTWARE\\Clients\\...\\Capabilities"
        cap_path = val.replace("/", "\\")
        # Check FileAssociations for .pdf
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
    # Try HKCR\Applications
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
    # Try App Paths
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

        # Get translation table (language + codepage pairs)
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

        # Try FileDescription first, then ProductName
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
    # 1. Try FriendlyAppName from Applications registry
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

    # 2. Try FileDescription / ProductName from exe version info
    ver_name = _get_exe_version_field(exe_path)
    if ver_name:
        return ver_name

    # 3. Fallback: cleaned exe stem
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

    # Append any new keys not already in file
    for key, val in values.items():
        if key not in written_keys:
            lines.append(f"{key}={val}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TextHandler(logging.Handler):
    """Logging handler that writes to a ScrolledText widget (thread-safe)."""

    def __init__(self, text_widget: ScrolledText) -> None:
        super().__init__()
        self._widget = text_widget

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record) + "\n"
        self._widget.after(0, self._append, msg)

    def _append(self, msg: str) -> None:
        self._widget.configure(state=tk.NORMAL)
        self._widget.insert(tk.END, msg)
        self._widget.see(tk.END)
        self._widget.configure(state=tk.DISABLED)


class FSEApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"FSE Processor v{__version__}")
        self.geometry("720x520")
        self.resizable(True, True)

        # Ensure data directories exist (for installed mode)
        paths.ensure_dirs()

        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._patient_stop_event = threading.Event()
        self._patient_worker: threading.Thread | None = None
        self._fields: dict[str, tk.Variable] = {}

        self._build_ui()
        self._load_settings()

    # ---- UI construction ----

    def _build_ui(self) -> None:
        # Detect PDF readers and browsers once at startup
        self._pdf_readers = _detect_pdf_readers()  # list of (exe_path, display_name)
        self._browsers = _detect_browsers()  # list of (channel_or_path, display_name)

        # --- Tabbed notebook ---
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 10))

        # Tab 1: Integrazione SISS
        siss_tab = tk.Frame(self._notebook, padx=8, pady=8)
        self._notebook.add(siss_tab, text="Integrazione SISS")

        # Tab 2: Download Paziente
        patient_tab = tk.Frame(self._notebook, padx=8, pady=8)
        self._notebook.add(patient_tab, text="Download Paziente")

        # Tab 3: Impostazioni (built first so fields exist for SISS tab)
        settings_tab = tk.Frame(self._notebook, padx=8, pady=8)
        self._notebook.add(settings_tab, text="Impostazioni")
        self._build_settings_tab(settings_tab)

        # Build SISS tab after settings so _fields["BROWSER_CHANNEL"] exists
        self._build_siss_tab(siss_tab)
        self._build_patient_tab(patient_tab)

    def _build_siss_tab(self, parent: tk.Frame) -> None:
        """Build the SISS Integration tab content."""
        # Detect default browser
        self._default_browser_info = detect_default_browser()
        default_name = "Non rilevato"
        if self._default_browser_info:
            progid = self._default_browser_info["progid"]
            friendly_names = {
                "MSEdgeHTM": "Microsoft Edge",
                "ChromeHTML": "Google Chrome",
                "BraveHTML": "Brave",
                "FirefoxURL": "Mozilla Firefox",
                "FirefoxHTML": "Mozilla Firefox",
            }
            default_name = next(
                (v for k, v in friendly_names.items() if progid.startswith(k)),
                progid,
            )

        # Browser info frame
        browser_frame = tk.LabelFrame(parent, text="Browser", padx=8, pady=6)
        browser_frame.pack(fill=tk.X)

        tk.Label(browser_frame, text=f"Browser predefinito: {default_name}").pack(anchor="w")

        self._mismatch_label = tk.Label(
            browser_frame, text="", fg="orange", wraplength=600, anchor="w", justify=tk.LEFT,
        )
        self._mismatch_label.pack(anchor="w", fill=tk.X)

        self._cdp_registry_var = tk.BooleanVar(value=False)
        is_firefox = (
            self._default_browser_info
            and self._default_browser_info.get("channel") == "firefox"
        )
        self._cdp_registry_cb = tk.Checkbutton(
            browser_frame,
            text="Abilita CDP nel registro (per Millewin/Medico2000)",
            variable=self._cdp_registry_var,
            command=self._on_cdp_registry_toggled,
            state=tk.DISABLED if is_firefox or not self._default_browser_info else tk.NORMAL,
        )
        self._cdp_registry_cb.pack(anchor="w")
        if is_firefox:
            tk.Label(
                browser_frame, text="(Firefox non supporta CDP)", fg="gray",
            ).pack(anchor="w")

        self._sync_cdp_registry_checkbox()
        self._update_browser_mismatch_warning()

        # Controls
        ctrl_frame = tk.LabelFrame(parent, text="Controlli", padx=8, pady=6)
        ctrl_frame.pack(fill=tk.X, pady=(8, 0))

        self._btn_check = tk.Button(ctrl_frame, text="Controlla Email", command=self._check_email)
        self._btn_check.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_start = tk.Button(ctrl_frame, text="Avvia", command=self._start_processing)
        self._btn_start.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_stop = tk.Button(ctrl_frame, text="Interrompi", command=self._stop_processing, state=tk.DISABLED)
        self._btn_stop.pack(side=tk.LEFT)

        tk.Button(ctrl_frame, text="Esci", command=self.destroy).pack(side=tk.RIGHT)

        # Document type checkboxes
        self._siss_doc_vars = self._build_doc_type_checkboxes(parent)

        # Console
        console_frame = tk.LabelFrame(parent, text="Console", padx=4, pady=4)
        console_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self._console = ScrolledText(console_frame, state=tk.DISABLED, wrap=tk.WORD, height=10)
        self._console.pack(fill=tk.BOTH, expand=True)

    @staticmethod
    def _build_doc_type_checkboxes(parent: tk.Widget) -> dict[str, tk.BooleanVar]:
        """Create a LabelFrame with document type checkboxes. Returns dict[type_key, BooleanVar]."""
        frame = tk.LabelFrame(parent, text="Tipologie documento", padx=8, pady=4)
        frame.pack(fill=tk.X, pady=(8, 0))
        doc_vars: dict[str, tk.BooleanVar] = {}
        for type_key, label, default_on in DOCUMENT_TYPES:
            var = tk.BooleanVar(value=default_on)
            tk.Checkbutton(frame, text=label, variable=var).pack(side=tk.LEFT, padx=(0, 16))
            doc_vars[type_key] = var
        return doc_vars

    @staticmethod
    def _get_selected_types(doc_vars: dict[str, tk.BooleanVar]) -> set[str]:
        """Return the set of selected document type keys."""
        return {key for key, var in doc_vars.items() if var.get()}

    def _build_patient_tab(self, parent: tk.Frame) -> None:
        """Build the Download Paziente tab content."""
        # CF input
        input_frame = tk.LabelFrame(parent, text="Codice Fiscale", padx=8, pady=6)
        input_frame.pack(fill=tk.X)
        input_frame.columnconfigure(1, weight=1)

        tk.Label(input_frame, text="CF:", anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        self._cf_var = tk.StringVar()
        self._cf_entry = tk.Entry(input_frame, textvariable=self._cf_var, font=("Consolas", 11))
        self._cf_entry.grid(row=0, column=1, sticky="ew", pady=2)

        # Document type checkboxes (independent from SISS tab)
        self._patient_doc_vars = self._build_doc_type_checkboxes(parent)

        # Controls
        ctrl_frame = tk.Frame(parent)
        ctrl_frame.pack(fill=tk.X, pady=(8, 0))

        self._btn_patient_start = tk.Button(
            ctrl_frame, text="Avvia Download", command=self._start_patient_download,
        )
        self._btn_patient_start.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_patient_stop = tk.Button(
            ctrl_frame, text="Interrompi", command=self._stop_patient_download, state=tk.DISABLED,
        )
        self._btn_patient_stop.pack(side=tk.LEFT)

        # Console
        console_frame = tk.LabelFrame(parent, text="Console", padx=4, pady=4)
        console_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self._patient_console = ScrolledText(console_frame, state=tk.DISABLED, wrap=tk.WORD, height=10)
        self._patient_console.pack(fill=tk.BOTH, expand=True)

    def _build_settings_tab(self, parent: tk.Frame) -> None:
        """Build the Settings tab content with grouped LabelFrames."""
        # Look-up dict from SETTINGS_SPEC for defaults
        spec = {key: (label, default, kind) for key, label, default, kind in SETTINGS_SPEC}

        # Top row: two columns side by side
        top_frame = tk.Frame(parent)
        top_frame.pack(fill=tk.X)
        top_frame.columnconfigure(0, weight=1, uniform="top")
        top_frame.columnconfigure(1, weight=1, uniform="top")

        # ── Left column: Server Posta ──
        mail_frame = tk.LabelFrame(top_frame, text="Server Posta", padx=8, pady=6)
        mail_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 8))
        mail_frame.columnconfigure(1, weight=1)

        for r, key in enumerate(["EMAIL_USER", "EMAIL_PASS", "IMAP_HOST", "IMAP_PORT"]):
            label, default, kind = spec[key]
            tk.Label(mail_frame, text=label, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(0, 8), pady=2,
            )
            var = tk.StringVar(value=default)
            show = "*" if kind == "password" else ""
            tk.Entry(mail_frame, textvariable=var, show=show).grid(
                row=r, column=1, sticky="ew", pady=2,
            )
            self._fields[key] = var

        # ── Right column: Browser e Download ──
        br_frame = tk.LabelFrame(top_frame, text="Browser e Download", padx=8, pady=6)
        br_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(0, 8))
        br_frame.columnconfigure(1, weight=1)

        r = 0
        tk.Label(br_frame, text="Browser", anchor="w").grid(
            row=r, column=0, sticky="w", padx=(0, 8), pady=2,
        )
        self._build_browser_selector_row(br_frame, r, "BROWSER_CHANNEL", spec["BROWSER_CHANNEL"][1])

        r += 1
        tk.Label(br_frame, text="Lettore PDF", anchor="w").grid(
            row=r, column=0, sticky="w", padx=(0, 8), pady=2,
        )
        self._build_pdf_reader_row(br_frame, r, "PDF_READER", spec["PDF_READER"][1])

        r += 1
        tk.Label(br_frame, text="Dir. download", anchor="w").grid(
            row=r, column=0, sticky="w", padx=(0, 8), pady=2,
        )
        var = tk.StringVar(value=spec["DOWNLOAD_DIR"][1])
        tk.Entry(br_frame, textvariable=var).grid(row=r, column=1, sticky="ew", pady=2)
        tk.Button(
            br_frame, text="...",
            command=lambda v=var: self._browse_dir(v),
        ).grid(row=r, column=2, padx=(4, 0), pady=2)
        self._fields["DOWNLOAD_DIR"] = var

        r += 1
        var = tk.BooleanVar(value=spec["USE_EXISTING_BROWSER"][1].lower() == "true")
        tk.Checkbutton(br_frame, text="Usa browser CDP", variable=var).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=2,
        )
        self._fields["USE_EXISTING_BROWSER"] = var

        r += 1
        tk.Label(br_frame, text="Porta CDP", anchor="w").grid(
            row=r, column=0, sticky="w", padx=(0, 8), pady=2,
        )
        var = tk.StringVar(value=spec["CDP_PORT"][1])
        tk.Entry(br_frame, textvariable=var, width=10).grid(
            row=r, column=1, sticky="w", pady=2,
        )
        self._fields["CDP_PORT"] = var

        # ── Bottom (full-width): Parametri ──
        params_frame = tk.LabelFrame(parent, text="Parametri", padx=8, pady=6)
        params_frame.pack(fill=tk.X)
        params_frame.columnconfigure(1, weight=1)
        params_frame.columnconfigure(3, weight=1)

        # Row 0: timeouts side by side
        tk.Label(params_frame, text="Download timeout (sec)", anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=2,
        )
        var = tk.StringVar(value=spec["DOWNLOAD_TIMEOUT"][1])
        tk.Entry(params_frame, textvariable=var, width=8).grid(
            row=0, column=1, sticky="w", pady=2,
        )
        self._fields["DOWNLOAD_TIMEOUT"] = var

        tk.Label(params_frame, text="Page timeout (sec)", anchor="w").grid(
            row=0, column=2, sticky="w", padx=(16, 8), pady=2,
        )
        var = tk.StringVar(value=spec["PAGE_TIMEOUT"][1])
        tk.Entry(params_frame, textvariable=var, width=8).grid(
            row=0, column=3, sticky="w", pady=2,
        )
        self._fields["PAGE_TIMEOUT"] = var

        # Row 1: max emails
        tk.Label(params_frame, text="Max email (0=tutte)", anchor="w").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=2,
        )
        var = tk.StringVar(value=spec["MAX_EMAILS"][1])
        tk.Entry(params_frame, textvariable=var, width=8).grid(
            row=1, column=1, sticky="w", pady=2,
        )
        self._fields["MAX_EMAILS"] = var

        # Row 2: checkboxes side by side
        var = tk.BooleanVar(value=spec["HEADLESS"][1].lower() == "true")
        tk.Checkbutton(params_frame, text="Headless browser", variable=var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=2,
        )
        self._fields["HEADLESS"] = var

        var = tk.BooleanVar(value=spec["DELETE_AFTER_PROCESSING"][1].lower() == "true")
        tk.Checkbutton(
            params_frame, text="Elimina email dopo elaborazione", variable=var,
        ).grid(row=2, column=2, columnspan=2, sticky="w", pady=2)
        self._fields["DELETE_AFTER_PROCESSING"] = var

        # Save button centered
        tk.Button(
            params_frame, text="Salva Impostazioni", command=self._save_settings,
        ).grid(row=3, column=0, columnspan=4, pady=(8, 0))

    def _build_pdf_reader_row(self, parent: tk.Widget, row: int, key: str, default: str) -> None:
        """Build the PDF reader selection row with combobox."""
        var = tk.StringVar(value=default)
        self._fields[key] = var

        # Build combo values and bidirectional maps (no duplicates)
        self._pdf_reader_map: dict[str, str] = {}     # display_label -> exe_path
        self._pdf_reader_revmap: dict[str, str] = {}   # norm(exe_path) -> display_label
        self._rebuild_pdf_combo_values()

        frame = tk.Frame(parent)
        frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
        frame.columnconfigure(0, weight=1)

        self._pdf_combo = ttk.Combobox(
            frame, values=list(self._pdf_reader_map.keys()), state="readonly",
        )
        self._pdf_combo.grid(row=0, column=0, sticky="ew")
        self._set_pdf_combo_from_value(default)
        self._pdf_combo.bind("<<ComboboxSelected>>", self._on_pdf_reader_changed)

    def _build_browser_selector_row(self, parent: tk.Widget, row: int, key: str, default: str) -> None:
        """Build the browser selection row with combobox."""
        var = tk.StringVar(value=default)
        self._fields[key] = var

        # Build maps: display_label -> channel_or_path
        self._browser_map: dict[str, str] = {}
        self._browser_revmap: dict[str, str] = {}  # channel_or_path -> display_label

        for channel_or_path, display_name in self._browsers:
            self._browser_map[display_name] = channel_or_path
            self._browser_revmap[channel_or_path] = display_name

        # Always add Chromium integrato as last option
        self._browser_map[BROWSER_CHROMIUM_LABEL] = BROWSER_CHROMIUM
        self._browser_revmap[BROWSER_CHROMIUM] = BROWSER_CHROMIUM_LABEL

        frame = tk.Frame(parent)
        frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
        frame.columnconfigure(0, weight=1)

        self._browser_combo = ttk.Combobox(
            frame, values=list(self._browser_map.keys()), state="readonly",
        )
        self._browser_combo.grid(row=0, column=0, sticky="ew")

        # Set initial value from stored channel
        label = self._browser_revmap.get(default)
        if label:
            self._browser_combo.set(label)
        elif self._browser_map:
            self._browser_combo.set(list(self._browser_map.keys())[0])

        self._browser_combo.bind("<<ComboboxSelected>>", self._on_browser_changed)

    def _on_browser_changed(self, _event: tk.Event) -> None:
        """Handle browser combobox selection change."""
        selected_label = self._browser_combo.get()
        channel = self._browser_map.get(selected_label, "msedge")
        self._fields["BROWSER_CHANNEL"].set(channel)
        self._update_browser_mismatch_warning()

    def _update_browser_mismatch_warning(self) -> None:
        """Show/hide a warning if the selected browser differs from the system default."""
        if not self._default_browser_info:
            self._mismatch_label.configure(text="")
            return

        default_channel = self._default_browser_info.get("channel")
        selected_channel = self._fields["BROWSER_CHANNEL"].get()

        # Normalize: both None or both equal means match
        if default_channel == selected_channel:
            self._mismatch_label.configure(text="")
        else:
            self._mismatch_label.configure(
                text=(
                    "Attenzione: il browser selezionato e' diverso dal browser "
                    "predefinito di sistema. Per usare la sessione SISS di "
                    "Millewin/Medico2000, seleziona lo stesso browser."
                ),
            )

    def _sync_cdp_registry_checkbox(self) -> None:
        """Read the current CDP registry status and update the checkbox."""
        if not self._default_browser_info:
            self._cdp_registry_var.set(False)
            return
        progid = self._default_browser_info["progid"]
        port = int(self._fields.get("CDP_PORT", tk.StringVar(value="9222")).get() or "9222")
        enabled = get_cdp_registry_status(progid, port)
        self._cdp_registry_var.set(enabled)

    def _on_cdp_registry_toggled(self) -> None:
        """Handle CDP registry checkbox toggle."""
        if not self._default_browser_info:
            return
        progid = self._default_browser_info["progid"]
        port = int(self._fields.get("CDP_PORT", tk.StringVar(value="9222")).get() or "9222")

        try:
            if self._cdp_registry_var.get():
                enable_cdp_in_registry(progid, port)
                self._log(f"CDP abilitato nel registro per {progid} (porta {port})")
            else:
                disable_cdp_in_registry(progid)
                self._log(f"CDP disabilitato nel registro per {progid}")
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile modificare il registro:\n{e}")
            # Revert checkbox
            self._sync_cdp_registry_checkbox()

    def _rebuild_pdf_combo_values(self, extra_exe: str | None = None) -> None:
        """Rebuild the combobox value maps from scratch (prevents duplicates)."""
        self._pdf_reader_map.clear()
        self._pdf_reader_revmap.clear()

        # 1. Default
        self._pdf_reader_map[PDF_READER_DEFAULT_LABEL] = PDF_READER_DEFAULT
        self._pdf_reader_revmap[_norm(PDF_READER_DEFAULT)] = PDF_READER_DEFAULT_LABEL

        # 2. Detected readers
        for exe_path, display_name in self._pdf_readers:
            nk = _norm(exe_path)
            if nk not in self._pdf_reader_revmap:
                self._pdf_reader_map[display_name] = exe_path
                self._pdf_reader_revmap[nk] = display_name

        # 3. Extra custom exe (from saved settings, not in detected list)
        if extra_exe and extra_exe != PDF_READER_DEFAULT:
            nk = _norm(extra_exe)
            if nk not in self._pdf_reader_revmap and Path(extra_exe).exists():
                display = _get_app_display_name(extra_exe, "")
                self._pdf_reader_map[display] = extra_exe
                self._pdf_reader_revmap[nk] = display

        # 4. Custom option (always last)
        self._pdf_reader_map[PDF_READER_CUSTOM_LABEL] = PDF_READER_CUSTOM

        # Update combobox if it exists
        if hasattr(self, "_pdf_combo"):
            self._pdf_combo["values"] = list(self._pdf_reader_map.keys())

    def _set_pdf_combo_from_value(self, value: str) -> None:
        """Set the combobox selection from a stored value (exe path or 'default')."""
        if not value or value == PDF_READER_DEFAULT:
            self._pdf_combo.set(PDF_READER_DEFAULT_LABEL)
            return
        nk = _norm(value)
        if nk in self._pdf_reader_revmap:
            self._pdf_combo.set(self._pdf_reader_revmap[nk])
            return
        # Unknown path - rebuild with extra value
        self._rebuild_pdf_combo_values(extra_exe=value)
        if nk in self._pdf_reader_revmap:
            self._pdf_combo.set(self._pdf_reader_revmap[nk])
        else:
            self._pdf_combo.set(PDF_READER_DEFAULT_LABEL)

    def _on_pdf_reader_changed(self, _event: tk.Event) -> None:
        """Handle combobox selection change."""
        selected_label = self._pdf_combo.get()
        exe_path = self._pdf_reader_map.get(selected_label, PDF_READER_DEFAULT)

        if exe_path == PDF_READER_CUSTOM:
            self._show_pdf_picker_dialog()
        else:
            self._fields["PDF_READER"].set(exe_path)

    def _show_pdf_picker_dialog(self) -> None:
        """Show a dialog listing all detected PDF readers, like Windows 'Open with'."""
        dlg = tk.Toplevel(self)
        dlg.title("Scegli lettore PDF")
        dlg.geometry("480x350")
        dlg.resizable(True, True)
        dlg.transient(self)
        dlg.grab_set()

        tk.Label(
            dlg, text="Seleziona un'applicazione per aprire i file PDF:",
            anchor="w", padx=8, pady=8,
        ).pack(fill=tk.X)

        # Listbox with all detected readers
        list_frame = tk.Frame(dlg)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8)

        scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
        listbox = tk.Listbox(
            list_frame, yscrollcommand=scrollbar.set,
            font=("Segoe UI", 10), activestyle="dotbox",
        )
        scrollbar.config(command=listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Populate with all detected readers
        items: list[tuple[str, str]] = []  # (display, exe_path)
        for exe_path, display_name in self._pdf_readers:
            items.append((display_name, exe_path))
            listbox.insert(tk.END, display_name)

        # Pre-select current value if it's in the list
        current = self._fields["PDF_READER"].get()
        if current and current != PDF_READER_DEFAULT:
            for idx, (_, exe) in enumerate(items):
                if _norm(exe) == _norm(current):
                    listbox.selection_set(idx)
                    listbox.see(idx)
                    break

        result = {"exe": None}

        def on_ok() -> None:
            sel = listbox.curselection()
            if sel:
                result["exe"] = items[sel[0]][1]
            dlg.destroy()

        def on_browse() -> None:
            path = filedialog.askopenfilename(
                parent=dlg,
                title="Seleziona lettore PDF",
                filetypes=[("Eseguibili", "*.exe"), ("Tutti i file", "*.*")],
            )
            if path:
                result["exe"] = path
                dlg.destroy()

        # Buttons
        btn_frame = tk.Frame(dlg, pady=8)
        btn_frame.pack(fill=tk.X, padx=8)

        tk.Button(btn_frame, text="Sfoglia...", command=on_browse).pack(side=tk.LEFT)
        tk.Button(btn_frame, text="Annulla", command=dlg.destroy).pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(btn_frame, text="OK", command=on_ok, width=10).pack(side=tk.RIGHT)

        # Double-click to select
        listbox.bind("<Double-Button-1>", lambda _e: on_ok())

        dlg.wait_window()

        if result["exe"]:
            chosen = result["exe"]
            self._fields["PDF_READER"].set(chosen)
            self._rebuild_pdf_combo_values(extra_exe=chosen)
            self._set_pdf_combo_from_value(chosen)
        else:
            # Cancelled - revert combo to current stored value
            self._set_pdf_combo_from_value(self._fields["PDF_READER"].get())

    # ---- Helpers ----

    def _browse_dir(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(initialdir=var.get() or ".")
        if path:
            var.set(path)

    def _browse_exe(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            initialdir=str(Path(var.get()).parent) if var.get() else ".",
            filetypes=[("Eseguibili", "*.exe"), ("Tutti i file", "*.*")],
        )
        if path:
            var.set(path)

    def _log(self, msg: str) -> None:
        """Append a message to the console (main-thread safe)."""
        self._console.configure(state=tk.NORMAL)
        self._console.insert(tk.END, msg + "\n")
        self._console.see(tk.END)
        self._console.configure(state=tk.DISABLED)

    def _get_field_values(self) -> dict[str, str]:
        """Collect current field values as strings for env file."""
        values: dict[str, str] = {}
        for key, _, _, kind in SETTINGS_SPEC:
            var = self._fields[key]
            if kind == "bool":
                values[key] = "true" if var.get() else "false"
            else:
                values[key] = var.get()
        return values

    # ---- Settings ----

    def _load_settings(self) -> None:
        env_vals = _load_env_values()
        for key, _, default, kind in SETTINGS_SPEC:
            val = env_vals.get(key, default)
            var = self._fields[key]
            if kind == "bool":
                var.set(val.lower() == "true")
            elif kind == "pdf_reader":
                var.set(val)
                self._set_pdf_combo_from_value(val)
            elif kind == "browser_selector":
                var.set(val)
                label = self._browser_revmap.get(val)
                if label:
                    self._browser_combo.set(label)
            else:
                var.set(val)

    def _save_settings(self) -> None:
        values = self._get_field_values()
        try:
            _save_env_values(values)
            self._log("Impostazioni salvate in " + ENV_FILE)
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile salvare: {e}")

    # ---- Check email ----

    def _check_email(self) -> None:
        self._btn_check.configure(state=tk.DISABLED)
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
            self.after(0, self._log, msg)
            self.after(0, lambda: messagebox.showinfo("Conteggio Email", msg))
        except Exception as e:
            err_msg = str(e) if str(e) and str(e) != "None" else (
                f"{type(e).__name__}: {e.args}" if e.args else type(e).__name__
            )
            tb = traceback.format_exc()
            self.after(0, self._log, f"Errore: {err_msg}\n{tb}")
            self.after(0, lambda m=err_msg: messagebox.showerror("Errore", m))
        finally:
            self.after(0, lambda: self._btn_check.configure(state=tk.NORMAL))

    def _save_settings_quietly(self) -> None:
        """Save current settings without user feedback."""
        values = self._get_field_values()
        _save_env_values(values)

    # ---- Processing ----

    def _start_processing(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("Attenzione", "Processamento gia' in corso")
            return
        if self._patient_worker and self._patient_worker.is_alive():
            messagebox.showwarning("Attenzione", "Download paziente in corso, attendere il completamento")
            return

        self._save_settings_quietly()
        self._stop_event.clear()
        self._btn_start.configure(state=tk.DISABLED)
        self._btn_stop.configure(state=tk.NORMAL)
        self._log("--- Avvio processamento ---")

        self._worker = threading.Thread(target=self._processing_worker, daemon=True)
        self._worker.start()
        self._poll_worker()

    def _processing_worker(self) -> None:
        try:
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            # Attach GUI handler to the underlying logger
            handler = TextHandler(self._console)
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger._logger.addHandler(handler)

            selected = self._get_selected_types(self._siss_doc_vars)
            run_processing(config, logger, self._stop_event, allowed_types=selected or None)
        except Exception as e:
            self.after(0, self._log, f"Errore fatale: {e}")

    def _poll_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            self.after(500, self._poll_worker)
        else:
            self._btn_start.configure(state=tk.NORMAL)
            self._btn_stop.configure(state=tk.DISABLED)
            self._log("--- Processamento terminato ---")

    def _stop_processing(self) -> None:
        self._stop_event.set()
        self._log("Richiesta interruzione inviata...")
        self._btn_stop.configure(state=tk.DISABLED)

    # ---- Patient download ----

    def _patient_log(self, msg: str) -> None:
        """Append a message to the patient console (main-thread safe)."""
        self._patient_console.configure(state=tk.NORMAL)
        self._patient_console.insert(tk.END, msg + "\n")
        self._patient_console.see(tk.END)
        self._patient_console.configure(state=tk.DISABLED)

    def _start_patient_download(self) -> None:
        if self._patient_worker and self._patient_worker.is_alive():
            messagebox.showwarning("Attenzione", "Download paziente gia' in corso")
            return
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("Attenzione", "Processamento SISS in corso, attendere il completamento")
            return

        cf = self._cf_var.get().strip().upper()
        if not re.match(r"^[A-Z0-9]{16}$", cf):
            messagebox.showwarning("Errore", "Il codice fiscale deve essere di 16 caratteri alfanumerici")
            return

        selected = self._get_selected_types(self._patient_doc_vars)
        if not selected:
            messagebox.showwarning("Errore", "Seleziona almeno una tipologia di documento")
            return

        self._save_settings_quietly()
        self._patient_stop_event.clear()
        self._btn_patient_start.configure(state=tk.DISABLED)
        self._btn_patient_stop.configure(state=tk.NORMAL)
        self._patient_log(f"--- Avvio download per CF: {cf} ---")

        self._patient_worker = threading.Thread(
            target=self._patient_download_worker, args=(cf, selected), daemon=True,
        )
        self._patient_worker.start()
        self._poll_patient_worker()

    def _patient_download_worker(self, codice_fiscale: str, allowed_types: set[str]) -> None:
        try:
            config = Config.load(ENV_FILE)
            logger = ProcessingLogger(config.log_dir)
            handler = TextHandler(self._patient_console)
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger._logger.addHandler(handler)

            file_manager = FileManager(config, logger)
            browser = FSEBrowser(config, logger)
            try:
                browser.start()
                browser.wait_for_manual_login()

                doc_results = browser.process_patient_all_dates(
                    codice_fiscale, self._patient_stop_event, allowed_types,
                )

                downloaded = 0
                for result in doc_results:
                    if result.skipped or result.error or not result.download_path:
                        continue
                    downloaded += 1
                    file_manager.rename_download(
                        download_path=result.download_path,
                        patient_name=codice_fiscale,
                        codice_fiscale=codice_fiscale,
                        disciplina=result.disciplina,
                        fse_link=f"{FSE_BASE_URL}#/?codiceFiscale={codice_fiscale}",
                    )

                logger.info(f"Download completato: {downloaded} documenti scaricati")
                file_manager.save_mappings()
            finally:
                browser.stop()
        except Exception as e:
            self.after(0, self._patient_log, f"Errore fatale: {e}")

    def _poll_patient_worker(self) -> None:
        if self._patient_worker and self._patient_worker.is_alive():
            self.after(500, self._poll_patient_worker)
        else:
            self._btn_patient_start.configure(state=tk.NORMAL)
            self._btn_patient_stop.configure(state=tk.DISABLED)
            self._patient_log("--- Download terminato ---")

    def _stop_patient_download(self) -> None:
        self._patient_stop_event.set()
        self._patient_log("Richiesta interruzione inviata...")
        self._btn_patient_stop.configure(state=tk.DISABLED)


if __name__ == "__main__":
    app = FSEApp()
    app.mainloop()
