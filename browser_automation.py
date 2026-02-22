import os
import re
import socket
import subprocess
import threading
import time
import urllib.request
import winreg
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright

from config import Config
from logger_module import ProcessingLogger

FSE_BASE_URL = "https://operatorisiss.servizirl.it/opefseie/"

DEFAULT_ALLOWED_TYPES = {"REFERTO", "LETTERA DIMISSIONE", "VERBALE PRONTO SOCCORSO"}

# ProgId → (channel for Playwright, process name for tasklist)
PROGID_TO_BROWSER: dict[str, tuple[str, str]] = {
    "MSEdgeHTM":      ("msedge",  "msedge.exe"),
    "ChromeHTML":     ("chrome",  "chrome.exe"),
    "BraveHTML":      (None,      "brave.exe"),    # channel=None → use exe path
    "FirefoxURL-308046B0AF4A39CB": ("firefox", "firefox.exe"),
    "FirefoxHTML-308046B0AF4A39CB": ("firefox", "firefox.exe"),
}

CDP_CONNECT_TIMEOUT = 15  # seconds to wait for CDP port to become available
CDP_CONNECT_POLL = 0.5    # poll interval in seconds


def _is_tipologia_valida(tipologia: str, allowed_types: set[str] | None = None) -> bool:
    types = allowed_types if allowed_types is not None else DEFAULT_ALLOWED_TYPES
    upper = tipologia.strip().upper()
    for t in types:
        if t == "REFERTO":
            if upper.startswith("REFERTO"):
                return True
        elif upper == t:
            return True
    return False


def _parse_table_date(date_text: str) -> date | None:
    """Parse a date string from the FSE table into a date object."""
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def detect_default_browser() -> dict | None:
    """Detect the system default browser from Windows registry.

    Returns a dict with keys: progid, channel, process_name, exe_path
    or None if detection fails.
    """
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        ) as key:
            progid, _ = winreg.QueryValueEx(key, "ProgId")
    except OSError:
        return None

    if not progid:
        return None

    # Try known ProgId mapping first
    for known_progid, (channel, process_name) in PROGID_TO_BROWSER.items():
        if progid.startswith(known_progid.split("-")[0]) or progid == known_progid:
            exe_path = _resolve_progid_exe_path(progid)
            return {
                "progid": progid,
                "channel": channel,
                "process_name": process_name,
                "exe_path": exe_path,
            }

    # Unknown ProgId — try to resolve the exe anyway
    exe_path = _resolve_progid_exe_path(progid)
    if exe_path:
        process_name = Path(exe_path).name.lower()
        return {
            "progid": progid,
            "channel": None,
            "process_name": process_name,
            "exe_path": exe_path,
        }

    return None


def _resolve_progid_exe_path(progid: str) -> str | None:
    """Resolve a ProgId to an executable path via the registry."""
    search_paths = [
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Classes\{progid}\shell\open\command"),
        (winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Classes\{progid}\shell\open\command"),
    ]
    for hive, subkey in search_paths:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                cmd, _ = winreg.QueryValueEx(key, "")
                if isinstance(cmd, str):
                    exe = _exe_from_command(cmd)
                    if exe and Path(exe).exists():
                        return str(Path(exe))
        except OSError:
            pass
    return None


def _exe_from_command(cmd: str) -> str | None:
    """Extract the exe path from a shell open command string (without checking existence)."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            return cmd[1:end]
    else:
        # Take everything up to the first space or argument
        parts = cmd.split()
        if parts:
            return parts[0]
    return None


def _read_original_open_command(progid: str) -> str | None:
    """Read the original shell\\open\\command from HKCR (machine-level default)."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            rf"{progid}\shell\open\command",
        ) as key:
            cmd, _ = winreg.QueryValueEx(key, "")
            if isinstance(cmd, str):
                return cmd
    except OSError:
        pass
    return None


def get_cdp_registry_status(progid: str, port: int) -> bool:
    """Check if the CDP --remote-debugging-port flag is present in the HKCU override."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            rf"SOFTWARE\Classes\{progid}\shell\open\command",
        ) as key:
            cmd, _ = winreg.QueryValueEx(key, "")
            if isinstance(cmd, str) and f"--remote-debugging-port={port}" in cmd:
                return True
    except OSError:
        pass
    return False


def enable_cdp_in_registry(progid: str, port: int) -> None:
    """Add --remote-debugging-port to the browser open command in HKCU.

    Reads the original command from HKCR, appends the CDP flag,
    and writes the result to HKCU\\SOFTWARE\\Classes\\{progid}\\shell\\open\\command.
    """
    # Read original command from HKCR
    original_cmd = _read_original_open_command(progid)
    if not original_cmd:
        raise RuntimeError(
            f"Impossibile leggere il comando originale per {progid} dal registro."
        )

    # Check if already has the flag
    cdp_flag = f"--remote-debugging-port={port}"
    if cdp_flag in original_cmd:
        return  # Already present

    # Remove any existing --remote-debugging-port with different port
    cleaned = re.sub(r"--remote-debugging-port=\d+\s*", "", original_cmd).strip()

    # Insert the flag after the exe path (before %1 or other args)
    # Pattern: "exe_path" args... → "exe_path" --remote-debugging-port=PORT args...
    if cleaned.startswith('"'):
        end_quote = cleaned.find('"', 1)
        if end_quote > 0:
            exe_part = cleaned[:end_quote + 1]
            rest = cleaned[end_quote + 1:].strip()
            new_cmd = f'{exe_part} {cdp_flag} {rest}'.strip()
        else:
            new_cmd = f'{cleaned} {cdp_flag}'
    else:
        parts = cleaned.split(None, 1)
        exe_part = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        new_cmd = f'{exe_part} {cdp_flag} {rest}'.strip()

    # Write to HKCU
    key_path = rf"SOFTWARE\Classes\{progid}\shell\open\command"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, new_cmd)


def disable_cdp_in_registry(progid: str) -> None:
    """Remove the HKCU override, restoring the original HKCR command."""
    try:
        # Delete the override key tree under HKCU
        _delete_registry_tree(
            winreg.HKEY_CURRENT_USER,
            rf"SOFTWARE\Classes\{progid}\shell\open\command",
        )
        # Also try to clean up parent keys if empty
        for suffix in [r"shell\open", r"shell", ""]:
            parent = rf"SOFTWARE\Classes\{progid}\{suffix}".rstrip("\\")
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, parent)
            except OSError:
                break  # Key not empty or doesn't exist
    except OSError:
        pass  # Key doesn't exist, nothing to do


def _delete_registry_tree(hive: int, subkey: str) -> None:
    """Delete a registry key and all its values (non-recursive, leaf key only)."""
    try:
        winreg.DeleteKey(hive, subkey)
    except OSError:
        pass


def _is_cdp_port_available(port: int) -> bool:
    """Check if a CDP endpoint is responding on localhost:port via HTTP /json/version."""
    try:
        req = urllib.request.Request(f"http://localhost:{port}/json/version")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        # Fallback to raw socket check (e.g. if /json/version is slow)
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except (ConnectionRefusedError, TimeoutError, OSError):
            return False


def _is_browser_process_running(process_name: str) -> bool:
    """Check if a browser process is currently running via tasklist."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return process_name.lower() in result.stdout.lower()
    except Exception:
        return False


def _kill_browser_processes(process_name: str, timeout: int = 10) -> None:
    """Kill all instances of a browser process and wait until they're gone."""
    for _ in range(3):
        subprocess.run(
            ["taskkill", "/IM", process_name, "/F", "/T"],
            capture_output=True, timeout=5,
        )
        time.sleep(1)

    # Wait for processes to actually terminate
    elapsed = 0
    while elapsed < timeout:
        if not _is_browser_process_running(process_name):
            return
        time.sleep(1)
        elapsed += 1


def _launch_browser_with_cdp(exe_path: str, port: int) -> None:
    """Launch a browser with --remote-debugging-port as a detached process."""
    subprocess.Popen(
        [exe_path, f"--remote-debugging-port={port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


@dataclass
class DocumentResult:
    disciplina: str
    skipped: bool
    download_path: Path | None
    error: str | None


class FSEBrowser:
    def __init__(self, config: Config, logger: ProcessingLogger) -> None:
        self._config = config
        self._logger = logger
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None  # Only used in CDP mode
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._attached = False  # True when connected to existing browser via CDP

    def start(self) -> None:
        self._playwright = sync_playwright().start()

        # --- CDP mode: connect to an already-running browser ---
        if self._config.use_existing_browser:
            self._start_cdp()
            return

        # --- Standard mode: launch a new browser instance ---
        channel = self._config.browser_channel

        if channel == "firefox":
            self._context = self._playwright.firefox.launch_persistent_context(
                user_data_dir=str(self._config.browser_data_dir),
                headless=self._config.headless,
                accept_downloads=True,
            )
        elif channel == "chromium":
            # Bundled Chromium (no channel) - auto-download if needed
            self._context = self._launch_bundled_chromium()
        elif channel in ("chrome", "msedge"):
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self._config.browser_data_dir),
                    headless=self._config.headless,
                    accept_downloads=True,
                    channel=channel,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as e:
                if "Target page, context or browser has been closed" in str(e):
                    self._logger.warning(
                        f"Lancio {channel} fallito (versione incompatibile con Playwright). "
                        f"Fallback a Chromium integrato..."
                    )
                    self._context = self._launch_bundled_chromium()
                else:
                    raise
        else:
            # Custom executable path (e.g. Brave)
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._config.browser_data_dir),
                headless=self._config.headless,
                accept_downloads=True,
                executable_path=channel,
                args=["--disable-blink-features=AutomationControlled"],
            )

        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(self._config.page_timeout)
        self._logger.info(f"Browser avviato (channel={channel}, headless={self._config.headless})")

    def _start_cdp(self) -> None:
        """Smart CDP connection: attach to existing browser, or launch one if needed."""
        port = self._config.cdp_port
        endpoint = f"http://localhost:{port}"
        channel = self._config.browser_channel

        # 1. Detect browser info (needed for recovery)
        browser_info = detect_default_browser()
        process_name = None
        exe_path = None

        if browser_info:
            process_name = browser_info["process_name"]
            exe_path = browser_info["exe_path"]

        if not exe_path:
            exe_path = self._resolve_exe_from_channel(channel)
        if not process_name and channel in ("msedge", "chrome"):
            process_name = "msedge.exe" if channel == "msedge" else "chrome.exe"

        # 2. CDP port already available → connect directly
        if _is_cdp_port_available(port):
            self._logger.info(f"Porta CDP {port} disponibile, connessione diretta...")
            try:
                self._connect_cdp(endpoint, port)
                return
            except ConnectionError:
                # CDP port responds but handshake failed (stale session, browser busy).
                # Try to kill and relaunch if we know the browser.
                if process_name and exe_path and Path(exe_path).exists():
                    self._logger.warning(
                        "Connessione CDP fallita nonostante la porta sia aperta. "
                        "Rilancio del browser..."
                    )
                    _kill_browser_processes(process_name)
                    _launch_browser_with_cdp(exe_path, port)

                    elapsed = 0.0
                    while elapsed < CDP_CONNECT_TIMEOUT:
                        if _is_cdp_port_available(port):
                            self._logger.info(
                                f"Porta CDP {port} disponibile dopo rilancio ({elapsed:.1f}s)"
                            )
                            self._connect_cdp(endpoint, port)
                            return
                        time.sleep(CDP_CONNECT_POLL)
                        elapsed += CDP_CONNECT_POLL

                raise  # Re-raise if recovery not possible or also failed

        # 3. Browser running without CDP
        if process_name and _is_browser_process_running(process_name):
            # If CDP is enabled in registry, kill and relaunch
            progid = browser_info["progid"] if browser_info else None
            if progid and get_cdp_registry_status(progid, port) and exe_path:
                self._logger.info(
                    f"Browser in esecuzione senza CDP ma CDP abilitato nel registro. "
                    f"Chiusura e rilancio..."
                )
                _kill_browser_processes(process_name)
                _launch_browser_with_cdp(exe_path, port)

                elapsed = 0.0
                while elapsed < CDP_CONNECT_TIMEOUT:
                    if _is_cdp_port_available(port):
                        self._logger.info(f"Porta CDP {port} disponibile dopo rilancio ({elapsed:.1f}s)")
                        self._connect_cdp(endpoint, port)
                        return
                    time.sleep(CDP_CONNECT_POLL)
                    elapsed += CDP_CONNECT_POLL

                raise ConnectionError(
                    f"Browser rilanciato ma la porta CDP {port} non ha risposto "
                    f"entro {CDP_CONNECT_TIMEOUT} secondi."
                )

            browser_display = browser_info["progid"] if browser_info else channel
            raise ConnectionError(
                f"Il browser ({browser_display}) e' in esecuzione ma la porta CDP {port} non risponde.\n"
                f"Opzioni:\n"
                f"  1. Chiudi tutte le finestre del browser e riprova (l'app lo lancera' con CDP)\n"
                f"  2. Abilita 'CDP nel registro' nelle impostazioni, poi riavvia il browser\n"
                f"  3. Avvia manualmente il browser con: --remote-debugging-port={port}"
            )

        # 4. Browser not running → launch it with CDP
        if exe_path and Path(exe_path).exists():
            self._logger.info(f"Browser non in esecuzione, lancio con CDP: {exe_path}")
            _launch_browser_with_cdp(exe_path, port)

            # Wait for CDP port to become available
            elapsed = 0.0
            while elapsed < CDP_CONNECT_TIMEOUT:
                if _is_cdp_port_available(port):
                    self._logger.info(f"Porta CDP {port} disponibile dopo {elapsed:.1f}s")
                    self._connect_cdp(endpoint, port)
                    return
                time.sleep(CDP_CONNECT_POLL)
                elapsed += CDP_CONNECT_POLL

            raise ConnectionError(
                f"Browser lanciato ma la porta CDP {port} non ha risposto "
                f"entro {CDP_CONNECT_TIMEOUT} secondi."
            )

        # 5. Cannot determine browser to launch
        raise ConnectionError(
            f"Impossibile connettersi via CDP alla porta {port}.\n"
            f"Nessun browser rilevato. Avvia manualmente il browser con:\n"
            f"  --remote-debugging-port={port}"
        )

    def _connect_cdp(self, endpoint: str, port: int) -> None:
        """Connect to a browser via CDP and set up context/page."""
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(
                endpoint, timeout=60000
            )
        except Exception as e:
            raise ConnectionError(
                f"Impossibile connettersi al browser su {endpoint}. "
                f"Assicurati che il browser sia avviato con --remote-debugging-port={port}\n"
                f"Errore: {e}"
            )

        # Use the default context (carries existing cookies/session)
        contexts = self._browser.contexts
        if not contexts:
            raise ConnectionError(
                "Browser connesso via CDP ma nessun contesto trovato. "
                "Apri almeno una finestra nel browser."
            )
        self._context = contexts[0]
        self._attached = True

        # Open a new tab for our automation
        self._page = self._context.new_page()
        self._page.set_default_timeout(self._config.page_timeout)
        self._logger.info(
            f"Connesso a browser esistente (CDP porta {port}, "
            f"{len(self._context.pages)} tab totali)"
        )

    def _launch_bundled_chromium(self) -> BrowserContext:
        """Launch Playwright's bundled Chromium as fallback when system browser is incompatible."""
        try:
            return self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._config.browser_data_dir),
                headless=self._config.headless,
                accept_downloads=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as e:
            if "Executable doesn't exist" in str(e):
                self._logger.info("Chromium non trovato, avvio download automatico...")
                self._install_chromium()
                return self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self._config.browser_data_dir),
                    headless=self._config.headless,
                    accept_downloads=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            raise

    def _install_chromium(self) -> None:
        """Install Chromium using the bundled Playwright driver (frozen) or Python (dev)."""
        import sys
        if getattr(sys, "frozen", False):
            # Frozen mode: use bundled node.exe + Playwright CLI
            # PyInstaller 6.x onedir puts data files in _internal/ (sys._MEIPASS)
            bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
            node_exe = os.path.join(bundle_dir, "playwright", "driver", "node.exe")
            cli_js = os.path.join(bundle_dir, "playwright", "driver", "package", "cli.js")
            if os.path.exists(node_exe) and os.path.exists(cli_js):
                subprocess.run([node_exe, cli_js, "install", "chromium"], check=True)
            else:
                raise RuntimeError(
                    f"Driver Playwright non trovato nel bundle. "
                    f"Cercato node.exe in: {node_exe}"
                )
        else:
            # Dev mode: use Python
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)

    def _resolve_exe_from_channel(self, channel: str) -> str | None:
        """Resolve a Playwright channel name to an exe path via App Paths registry."""
        channel_to_exe = {
            "msedge": "msedge.exe",
            "chrome": "chrome.exe",
        }
        exe_name = channel_to_exe.get(channel)
        if not exe_name:
            return None

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                    hive,
                    rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
                ) as key:
                    val, _ = winreg.QueryValueEx(key, "")
                    if val and Path(val).exists():
                        return str(Path(val))
            except OSError:
                pass
        return None

    def _restart(self) -> None:
        """Restart browser after a crash, preserving the persistent session."""
        self._logger.info("Riavvio browser...")
        self.stop()
        self.start()

    def _is_alive(self) -> bool:
        """Check if browser/page is still usable."""
        try:
            self._page.title()
            return True
        except Exception:
            return False

    def stop(self) -> None:
        if self._attached:
            # CDP mode: close only our tab, leave the browser running
            if self._page:
                try:
                    self._page.close()
                except Exception:
                    pass
            if self._browser:
                try:
                    self._browser.close()  # Disconnects CDP, does NOT close the browser
                except Exception:
                    pass
        else:
            # Standard mode: close context (closes the browser we launched)
            if self._context:
                try:
                    self._context.close()
                except Exception:
                    pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self._playwright = None
        self._page = None
        self._attached = False
        self._logger.info("Browser chiuso")

    def wait_for_manual_login(self) -> None:
        """Navigate to the FSE portal and wait for the user to complete SSO login."""
        import time

        self._logger.info("Navigazione al portale FSE per login manuale...")
        self._page.goto(FSE_BASE_URL, wait_until="networkidle")

        # Try clicking "Accedi" to trigger SSO redirect
        try:
            accedi = self._page.get_by_role("button", name="Accedi")
            if accedi.is_visible(timeout=5000):
                accedi.click()
                self._page.wait_for_load_state("networkidle")
        except Exception:
            pass  # Button might not be there, that's ok

        # If we're on the SSO page, wait for the user to complete login
        self._logger.info(
            "LOGIN MANUALE RICHIESTO - Completa l'accesso nel browser. "
            "L'app proseguira' automaticamente dopo il login."
        )

        # Poll until we're back on the FSE portal (not on the SSO/login page)
        max_wait = 300  # 5 minutes max
        elapsed = 0
        while elapsed < max_wait:
            try:
                # Check all pages using JS to get the real URL (page.url can be stale)
                for page in self._context.pages:
                    try:
                        real_url = page.evaluate("window.location.href")
                    except Exception:
                        real_url = page.url
                    if "operatorisiss" in real_url and "idpcrlmain" not in real_url and "ssoauth" not in real_url:
                        self._page = page
                        self._page.set_default_timeout(self._config.page_timeout)
                        self._logger.info(f"Login rilevato su: {real_url}")
                        break
                else:
                    # Not found yet - every 30s try navigating to FSE as fallback
                    if elapsed > 0 and elapsed % 30 == 0:
                        self._logger.info(f"Poll login [{elapsed}s] - tentativo navigazione diretta FSE...")
                        try:
                            self._page.goto(FSE_BASE_URL, wait_until="networkidle", timeout=10000)
                            real_url = self._page.evaluate("window.location.href")
                            if "operatorisiss" in real_url and "idpcrlmain" not in real_url:
                                self._logger.info(f"Login rilevato dopo navigazione diretta: {real_url}")
                                break
                        except Exception:
                            pass
                    time.sleep(2)
                    elapsed += 2
                    continue
                break  # Found it
            except Exception as e:
                self._logger.info(f"Poll login [{elapsed}s] - errore: {e}")
                time.sleep(2)
                elapsed += 2

        if elapsed >= max_wait:
            raise RuntimeError("Timeout attesa login manuale (5 minuti)")

        self._page.wait_for_load_state("networkidle")
        self._logger.info("Login manuale completato, proseguo con l'automazione")

    def _wait_for_spinner(self, timeout: int = 15000) -> None:
        """Wait for the ngx-spinner overlay to disappear before interacting with the page."""
        spinner = self._page.locator("ngx-spinner .overlay")
        try:
            spinner.wait_for(state="hidden", timeout=timeout)
        except Exception:
            pass  # Spinner might not exist on this page, that's fine

    def _navigate_to_referti(self, codice_fiscale: str) -> None:
        """Navigate to the Referti tab, adapting to the current page state.

        The Angular SPA can land on different pages depending on state:
        - Search page (has #inputcf) → fill CF, click Cerca, Accedi, Referti
        - Patient fascicolo (has Referti tab) → click Referti directly
        """
        self._wait_for_spinner()

        # Step 1: Fill CF and click Cerca (only if search form is visible)
        cf_input = self._page.locator("#inputcf")
        if cf_input.is_visible(timeout=3000):
            cf_input.fill(codice_fiscale)
            self._logger.info(f"Codice fiscale inserito: {codice_fiscale}")
            self._wait_for_spinner()
            cerca = self._page.get_by_role("button", name="Cerca")
            if cerca.is_visible(timeout=3000):
                cerca.click()
                self._page.wait_for_load_state("networkidle")
                self._wait_for_spinner()
        else:
            self._logger.info("Form ricerca non presente, pagina gia' nel fascicolo")

        # Step 2: Click "Accedi" to enter patient FSE (only if visible)
        accedi_btn = self._page.locator("button.btn-xlarge")
        if accedi_btn.is_visible(timeout=3000):
            accedi_btn.click()
            self._page.wait_for_load_state("networkidle")
            self._wait_for_spinner()

        # Step 3: Click "Referti" tab
        self._logger.info("Ricerca tab 'Referti' nella pagina...")
        referti = self._page.get_by_text("Referti", exact=True).first
        if referti.is_visible(timeout=5000):
            self._logger.info("Tab 'Referti' trovato, click...")
            referti.click()
        else:
            # Broader fallback
            self._logger.warning("Tab 'Referti' non trovato con exact match, tentativo parziale...")
            self._page.get_by_text("Referti").first.click()
        self._page.wait_for_load_state("networkidle")
        self._wait_for_spinner()
        self._logger.info(f"Pagina referti caricata: {self._page.url}")

    def process_patient(self, fse_link: str, patient_name: str, codice_fiscale: str,
                        stop_event: threading.Event | None = None,
                        allowed_types: set[str] | None = None) -> list[DocumentResult]:
        # Check stop request
        if stop_event is not None and stop_event.is_set():
            self._logger.info(f"Processamento interrotto prima di {patient_name}")
            return []

        # Restart browser if it crashed
        if not self._is_alive():
            self._restart()

        self._logger.info(f"Navigazione FSE per {patient_name}: {fse_link}")
        results: list[DocumentResult] = []

        try:
            self._page.goto(fse_link, wait_until="networkidle")
            self._wait_for_spinner()

            # Check if we need to click "Accedi" (session might already be active)
            accedi = self._page.get_by_role("button", name="Accedi")
            if accedi.is_visible(timeout=3000):
                accedi.click()
                self._page.wait_for_load_state("networkidle")
                self._wait_for_spinner()

            # Check if we got redirected to SSO (session expired)
            if "idpcrlmain" in self._page.url or "ssoauth" in self._page.url:
                import time
                self._logger.warning(
                    "Sessione SSO scaduta - completa il login nel browser. "
                    "L'app proseguira' automaticamente."
                )
                max_wait = 60
                elapsed = 0
                while elapsed < max_wait:
                    try:
                        current_url = self._page.url
                        if "operatorisiss" in current_url and "idpcrlmain" not in current_url:
                            break
                    except Exception:
                        pass
                    time.sleep(2)
                    elapsed += 2
                if elapsed >= max_wait:
                    raise RuntimeError("Timeout attesa re-login (1 minuto)")

                self._page.wait_for_load_state("networkidle")
                # After re-login, navigate again to the patient
                self._page.goto(fse_link, wait_until="networkidle")
                accedi = self._page.get_by_role("button", name="Accedi")
                if accedi.is_visible(timeout=3000):
                    accedi.click()
                    self._page.wait_for_load_state("networkidle")

            # Navigate to Referti tab (adapts to page state)
            self._navigate_to_referti(codice_fiscale)

            # Wait for table to appear
            self._page.wait_for_selector("table tbody tr", state="attached")

        except Exception as e:
            self._logger.error(f"Errore navigazione FSE per {patient_name}: {e}")
            self._take_debug_screenshot(patient_name)
            return [DocumentResult(disciplina="N/A", skipped=False, download_path=None, error=str(e))]

        # Find the referti table (the one containing "Tipologia documento" header)
        try:
            referti_table = self._page.locator("table:has(th:has-text('Tipologia documento'))")
            referti_table.wait_for(state="attached", timeout=10000)

            headers = referti_table.locator("thead th")
            header_count = headers.count()
            header_texts = [headers.nth(j).inner_text().strip() for j in range(header_count)]
            self._logger.info(f"Intestazioni tabella referti: {header_texts}")

            date_col = tipo_col = visualizza_col = None
            for idx, h in enumerate(header_texts):
                h_upper = h.upper()
                if "DATA" in h_upper and date_col is None:
                    date_col = idx
                elif "TIPOLOGIA" in h_upper:
                    tipo_col = idx
                elif "VISUALIZZA" in h_upper:
                    visualizza_col = idx

            if tipo_col is None:
                raise RuntimeError(f"Colonna 'Tipologia' non trovata nelle intestazioni: {header_texts}")

            # Select only data rows from the referti table
            data_rows = referti_table.locator("tbody tr:has(td)")
            row_count = data_rows.count()
            self._logger.info(f"Trovate {row_count} righe dati nella tabella referti")

            if row_count == 0:
                return results

            # Debug: log first data row
            first_row_html = data_rows.nth(0).inner_html()
            self._logger.info(f"HTML prima riga dati: {first_row_html[:500]}")

            # Extract data from data rows
            row_data: list[tuple[int, str, str]] = []
            for i in range(row_count):
                cells = data_rows.nth(i).locator("td")
                cell_count = cells.count()
                if cell_count <= max(date_col or 0, tipo_col or 0):
                    continue  # Row doesn't have enough cells
                date_text = cells.nth(date_col).inner_text().strip() if date_col is not None else ""
                tipo_text = cells.nth(tipo_col).inner_text().strip()
                row_data.append((i, date_text, tipo_text))

            if not row_data:
                self._logger.warning("Nessuna riga dati valida trovata")
                return results

            # Log first few rows for debugging
            for row_idx, date_text, tipo_text in row_data[:5]:
                self._logger.info(f"Riga {row_idx + 1}: data='{date_text}', tipologia='{tipo_text}'")

            # Most recent date = first row (table is sorted newest first)
            most_recent_date = row_data[0][1]
            self._logger.info(f"Data piu' recente: {most_recent_date}")

            # Filter: same date + valid tipologia
            for row_idx, date_text, tipo_text in row_data:
                if date_text != most_recent_date:
                    self._logger.info(f"Riga {row_idx + 1}: data '{date_text}' diversa, stop scansione")
                    break

                if not _is_tipologia_valida(tipo_text, allowed_types):
                    self._logger.info(f"Riga {row_idx + 1}: tipologia '{tipo_text}' non di interesse, saltata")
                    results.append(DocumentResult(
                        disciplina=tipo_text, skipped=True, download_path=None, error=None
                    ))
                    continue

                # Download document
                result = self._download_document(row_idx, tipo_text, patient_name, visualizza_col, data_rows)
                results.append(result)

        except Exception as e:
            self._logger.error(f"Errore lettura tabella per {patient_name}: {e}")
            self._take_debug_screenshot(patient_name)
            return [DocumentResult(disciplina="N/A", skipped=False, download_path=None, error=str(e))]

        return results

    def _navigate_and_login(self, fse_link: str, codice_fiscale: str) -> None:
        """Navigate to an FSE link and handle SSO login if needed."""
        self._page.goto(fse_link, wait_until="networkidle")
        self._wait_for_spinner()

        # Check if we need to click "Accedi" (session might already be active)
        accedi = self._page.get_by_role("button", name="Accedi")
        if accedi.is_visible(timeout=3000):
            accedi.click()
            self._page.wait_for_load_state("networkidle")
            self._wait_for_spinner()

        # Check if we got redirected to SSO (session expired)
        if "idpcrlmain" in self._page.url or "ssoauth" in self._page.url:
            import time
            self._logger.warning(
                "Sessione SSO scaduta - completa il login nel browser. "
                "L'app proseguira' automaticamente."
            )
            max_wait = 60
            elapsed = 0
            while elapsed < max_wait:
                try:
                    current_url = self._page.url
                    if "operatorisiss" in current_url and "idpcrlmain" not in current_url:
                        break
                except Exception:
                    pass
                time.sleep(2)
                elapsed += 2
            if elapsed >= max_wait:
                raise RuntimeError("Timeout attesa re-login (1 minuto)")

            self._page.wait_for_load_state("networkidle")
            # After re-login, navigate again to the patient
            self._page.goto(fse_link, wait_until="networkidle")
            accedi = self._page.get_by_role("button", name="Accedi")
            if accedi.is_visible(timeout=3000):
                accedi.click()
                self._page.wait_for_load_state("networkidle")

        # Navigate to Referti tab (adapts to page state)
        self._navigate_to_referti(codice_fiscale)

        # Wait for table to appear
        self._page.wait_for_selector("table tbody tr", state="attached")

    def process_patient_all_dates(self, codice_fiscale: str,
                                  stop_event: threading.Event | None = None,
                                  allowed_types: set[str] | None = None,
                                  ente_filter: str = "",
                                  date_from: date | None = None,
                                  date_to: date | None = None,
                                  on_enti_found: callable | None = None) -> list[DocumentResult]:
        """Download ALL documents (all dates) for a patient, filtered by allowed types, ente, and date range."""
        patient_name = codice_fiscale  # Use CF as label since we don't have name

        if stop_event is not None and stop_event.is_set():
            self._logger.info(f"Download interrotto prima di {codice_fiscale}")
            return []

        if not self._is_alive():
            self._restart()

        fse_link = f"{FSE_BASE_URL}#/?codiceFiscale={codice_fiscale}"
        self._logger.info(f"Navigazione FSE per {codice_fiscale}: {fse_link}")
        results: list[DocumentResult] = []

        try:
            self._navigate_and_login(fse_link, codice_fiscale)
        except Exception as e:
            self._logger.error(f"Errore navigazione FSE per {codice_fiscale}: {e}")
            self._take_debug_screenshot(codice_fiscale)
            return [DocumentResult(disciplina="N/A", skipped=False, download_path=None, error=str(e))]

        try:
            referti_table = self._page.locator("table:has(th:has-text('Tipologia documento'))")
            referti_table.wait_for(state="attached", timeout=10000)

            headers = referti_table.locator("thead th")
            header_count = headers.count()
            header_texts = [headers.nth(j).inner_text().strip() for j in range(header_count)]
            self._logger.info(f"Intestazioni tabella referti: {header_texts}")

            date_col = tipo_col = ente_col = visualizza_col = None
            for idx, h in enumerate(header_texts):
                h_upper = h.upper()
                if "DATA" in h_upper and date_col is None:
                    date_col = idx
                elif "TIPOLOGIA" in h_upper:
                    tipo_col = idx
                elif "ENTE" in h_upper or "STRUTTURA" in h_upper:
                    ente_col = idx
                elif "VISUALIZZA" in h_upper:
                    visualizza_col = idx

            if tipo_col is None:
                raise RuntimeError(f"Colonna 'Tipologia' non trovata nelle intestazioni: {header_texts}")

            data_rows = referti_table.locator("tbody tr:has(td)")
            row_count = data_rows.count()
            self._logger.info(f"Trovate {row_count} righe dati nella tabella referti")

            if row_count == 0:
                return results

            # Phase 1: Scan all rows to extract data and collect unique enti
            row_info: list[tuple[int, str, str, str]] = []  # (index, date_text, tipo_text, ente_text)
            ente_set: set[str] = set()
            for i in range(row_count):
                cells = data_rows.nth(i).locator("td")
                cell_count = cells.count()
                if cell_count <= (tipo_col or 0):
                    continue
                date_text = cells.nth(date_col).inner_text().strip() if date_col is not None else ""
                tipo_text = cells.nth(tipo_col).inner_text().strip()
                ente_text = cells.nth(ente_col).inner_text().strip() if ente_col is not None else ""
                row_info.append((i, date_text, tipo_text, ente_text))
                if ente_text:
                    ente_set.add(ente_text)

            # Notify callback with unique enti
            if on_enti_found and ente_set:
                on_enti_found(sorted(ente_set))

            # Phase 2: Filter and download
            ente_filter_upper = ente_filter.strip().upper()

            # Pre-filter to identify matching rows and count
            rows_to_download: list[tuple[int, str, str, str]] = []
            for i, date_text, tipo_text, ente_text in row_info:
                if not _is_tipologia_valida(tipo_text, allowed_types):
                    results.append(DocumentResult(
                        disciplina=tipo_text, skipped=True, download_path=None, error=None
                    ))
                    continue
                if ente_filter_upper and ente_filter_upper not in ente_text.upper():
                    results.append(DocumentResult(
                        disciplina=tipo_text, skipped=True, download_path=None, error=None
                    ))
                    continue
                if date_from or date_to:
                    parsed = _parse_table_date(date_text)
                    if parsed:
                        if date_from and parsed < date_from:
                            results.append(DocumentResult(
                                disciplina=tipo_text, skipped=True, download_path=None, error=None
                            ))
                            continue
                        if date_to and parsed > date_to:
                            results.append(DocumentResult(
                                disciplina=tipo_text, skipped=True, download_path=None, error=None
                            ))
                            continue
                rows_to_download.append((i, date_text, tipo_text, ente_text))

            total_match = len(rows_to_download)
            total_rows = len(row_info)
            self._logger.info(f"Trovati {total_match} documenti corrispondenti ai filtri (su {total_rows} totali)")

            for dl_idx, (i, date_text, tipo_text, ente_text) in enumerate(rows_to_download, 1):
                if stop_event is not None and stop_event.is_set():
                    self._logger.info("Download interrotto dall'utente")
                    break

                self._logger.info(f"Download {dl_idx}/{total_match}: {tipo_text}")
                result = self._download_document(i, tipo_text, patient_name, visualizza_col, data_rows)
                results.append(result)

        except Exception as e:
            self._logger.error(f"Errore lettura tabella per {codice_fiscale}: {e}")
            self._take_debug_screenshot(codice_fiscale)
            return [DocumentResult(disciplina="N/A", skipped=False, download_path=None, error=str(e))]

        return results

    def _download_document(self, row_index: int, tipologia: str, patient_name: str,
                           visualizza_col: int | None, data_rows=None) -> DocumentResult:
        for attempt in range(2):  # max 1 retry
            try:
                if data_rows is not None:
                    row = data_rows.nth(row_index)
                else:
                    row = self._page.locator("table tbody tr:has(td)").nth(row_index)

                # Click the icon/link in the Visualizza column
                if visualizza_col is not None:
                    visualizza_cell = row.locator("td").nth(visualizza_col)
                else:
                    visualizza_cell = row.locator("td").last

                # Debug: log what's in the cell
                cell_html = visualizza_cell.inner_html()
                self._logger.info(f"HTML cella Visualizza: {cell_html[:200]}")

                # Click the first clickable element in the cell
                clickable = visualizza_cell.locator("a, button, img, svg, i, span[class*='icon'], span[class*='glyph']").first
                if clickable.count() == 0:
                    # Fallback: click the cell itself
                    clickable = visualizza_cell

                with self._page.expect_download(timeout=self._config.download_timeout) as download_info:
                    clickable.click()
                    # Wait for Accetta button and click it if it appears
                    accetta = self._page.get_by_role("button", name="Accetta")
                    if accetta.is_visible(timeout=5000):
                        accetta.click()

                download = download_info.value
                # Generate unique filename to avoid overwrites
                ext = Path(download.suggested_filename).suffix or ".pdf"
                unique_name = f"{patient_name}_{row_index}{ext}".replace(" ", "_")
                save_path = self._config.download_dir / unique_name
                download.save_as(str(save_path))

                self._logger.info(
                    f"Download completato: {save_path.name} (tipologia: {tipologia})"
                )
                return DocumentResult(
                    disciplina=tipologia, skipped=False, download_path=save_path, error=None
                )

            except Exception as e:
                if attempt == 0:
                    self._logger.warning(
                        f"Download fallito per riga {row_index + 1} ({tipologia}), "
                        f"tentativo {attempt + 1}/2: {e}"
                    )
                    try:
                        self._page.reload(wait_until="networkidle")
                        self._page.wait_for_selector("table tbody tr", state="visible")
                    except Exception:
                        pass
                else:
                    self._logger.error(
                        f"Download fallito definitivamente per riga {row_index + 1} ({tipologia}): {e}"
                    )
                    self._take_debug_screenshot(f"{patient_name}_row{row_index + 1}")

        return DocumentResult(
            disciplina=tipologia, skipped=False, download_path=None, error="Download fallito dopo retry"
        )

    def _take_debug_screenshot(self, label: str) -> None:
        try:
            screenshot_path = self._config.log_dir / f"debug_{label}.png"
            self._page.screenshot(path=str(screenshot_path))
            self._logger.debug(f"Screenshot debug salvato: {screenshot_path}")
        except Exception:
            pass
